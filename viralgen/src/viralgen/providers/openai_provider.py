"""Proveedor real: OpenAI Responses API + Structured Outputs.

Puntos importantes:

* Se usa ``client.responses.parse(text_format=<modelo Pydantic>)``, que es el
  metodo compatible con el SDK instalado (openai >= 3.16) y deriva el
  ``json_schema`` estricto del modelo. NO se pide JSON "a mano" ni se quitan
  bloques Markdown para encontrarlo.
* Los reintentos internos del SDK se desactivan (``max_retries=0``) porque los
  gestionamos aqui, con retroceso exponencial, jitter y respeto de
  ``Retry-After``, contando cada intento contra ``MAX_CALLS_PER_JOB``.
* Las respuestas incompletas y los rechazos explicitos se tratan por separado
  y NO se reintentan.
* ``temperature`` solo se envia si esta explicitamente habilitado, porque hay
  modelos que no lo admiten.

Documentacion de referencia:
https://developers.openai.com/api/docs/guides/structured-outputs
"""

from __future__ import annotations

import random
import time
from typing import Any

from ..errors import (
    ConfigError,
    ProviderError,
    ProviderIncompleteError,
    ProviderPermanentError,
    ProviderRefusalError,
    ProviderTransientError,
)
from ..logging_setup import get_logger
from .base import CallBudget, ProviderRequest, ProviderResult, ProviderUsage, TextProvider

logger = get_logger("provider.openai")

#: Retroceso base en segundos; se multiplica por 2^intento y se le suma jitter.
BACKOFF_BASE_S = 1.0
BACKOFF_MAX_S = 30.0

#: Codigos de error del proveedor que NUNCA se reintentan aunque lleguen con 429.
NON_RETRYABLE_CODES = {
    "insufficient_quota",
    "billing_hard_limit_reached",
    "invalid_api_key",
    "model_not_found",
    "unsupported_parameter",
    "unsupported_value",
}


class OpenAIProvider(TextProvider):
    name = "openai"

    def __init__(self, *, settings: Any, client: Any = None, sleep=time.sleep) -> None:
        api_key, model = settings.require_provider_settings()
        self.settings = settings
        self.model = model
        self._sleep = sleep
        self._rng = random.Random(0)
        if client is not None:
            self._client = client
        else:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover - dependencia declarada
                raise ConfigError(
                    "El paquete 'openai' no esta instalado; instala las dependencias "
                    "o usa --mock."
                ) from exc
            kwargs: dict[str, Any] = {
                "api_key": api_key,
                "timeout": float(settings.request_timeout_seconds),
                # Gestionamos nosotros los reintentos: el SDK no debe duplicarlos.
                "max_retries": 0,
            }
            if settings.openai_base_url:
                kwargs["base_url"] = settings.openai_base_url
            self._client = OpenAI(**kwargs)

    # -- API publica -------------------------------------------------------

    def generate_structured(self, request: ProviderRequest, budget: CallBudget) -> ProviderResult:
        attempts = self.settings.max_transport_retries + 1
        last_error: ProviderError | None = None

        for attempt in range(attempts):
            budget.spend(request.stage)
            started = time.monotonic()
            try:
                response = self._call(request)
            except ProviderTransientError as exc:
                last_error = exc
                latency_ms = int((time.monotonic() - started) * 1000)
                logger.warning(
                    "etapa=%s intento=%d/%d fallo transitorio (%s) latencia=%dms",
                    request.stage,
                    attempt + 1,
                    attempts,
                    exc.code,
                    latency_ms,
                )
                if attempt == attempts - 1 or not budget.can_spend():
                    break
                self._sleep(self._backoff_seconds(attempt, exc.details.get("retry_after")))
                continue

            latency_ms = int((time.monotonic() - started) * 1000)
            return self._build_result(request, response, latency_ms)

        assert last_error is not None
        raise ProviderTransientError(
            f"Se agotaron los reintentos de transporte en la etapa '{request.stage}': "
            f"{last_error.message}",
            details={**last_error.details, "attempts": attempts},
        )

    # -- Interno -----------------------------------------------------------

    def _call(self, request: ProviderRequest) -> Any:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "instructions": request.instructions,
            "input": request.input_text,
            "text_format": request.schema_model,
            "max_output_tokens": request.max_output_tokens or self.settings.max_output_tokens,
            "store": False,
        }
        if self.settings.send_temperature and self.settings.temperature is not None:
            kwargs["temperature"] = self.settings.temperature
        try:
            return self._client.responses.parse(**kwargs)
        except Exception as exc:
            raise self._classify(exc) from exc

    def _classify(self, exc: Exception) -> ProviderError:
        """Traduce las excepciones del SDK a la jerarquia del proyecto."""
        import openai

        if isinstance(exc, openai.APITimeoutError):
            return ProviderTransientError(f"Tiempo de espera agotado: {exc}")
        if isinstance(exc, openai.APIConnectionError):
            return ProviderTransientError(f"Fallo de conexion: {exc}")
        if isinstance(exc, openai.RateLimitError):
            code = _error_code(exc)
            if code in NON_RETRYABLE_CODES:
                return ProviderPermanentError(
                    f"Limite de facturacion o cuota agotada ({code}); no se reintenta."
                )
            return ProviderTransientError(
                f"Limite de peticiones temporal: {exc}",
                details={"retry_after": _retry_after(exc)},
            )
        if isinstance(exc, openai.InternalServerError):
            return ProviderTransientError(
                f"Error 5xx del proveedor: {exc}", details={"retry_after": _retry_after(exc)}
            )
        if isinstance(exc, openai.AuthenticationError):
            return ProviderPermanentError("Clave de API invalida o revocada; no se reintenta.")
        if isinstance(exc, openai.PermissionDeniedError):
            return ProviderPermanentError(
                "Permisos insuficientes para el modelo o la organizacion; no se reintenta."
            )
        if isinstance(exc, openai.NotFoundError):
            return ProviderPermanentError(
                f"Modelo o recurso no encontrado ({self.model}); revisa OPENAI_MODEL."
            )
        if isinstance(exc, openai.BadRequestError):
            return ProviderPermanentError(
                f"Peticion rechazada por el proveedor (posible modelo sin soporte de "
                f"Structured Outputs o parametro no admitido): {exc}"
            )
        if isinstance(exc, openai.LengthFinishReasonError):
            return ProviderIncompleteError(
                "La respuesta se corto por longitud; sube MAX_OUTPUT_TOKENS o acorta la entrada."
            )
        if isinstance(exc, openai.ContentFilterFinishReasonError):
            return ProviderRefusalError("El proveedor filtro el contenido de la respuesta.")
        if isinstance(exc, openai.APIStatusError):
            status = getattr(exc, "status_code", None)
            if status is not None and 500 <= int(status) < 600:
                return ProviderTransientError(f"Error {status} del proveedor: {exc}")
            return ProviderPermanentError(f"Error {status} del proveedor: {exc}")
        if isinstance(exc, openai.OpenAIError):
            return ProviderPermanentError(f"Error del SDK de OpenAI: {exc}")
        return ProviderPermanentError(f"Error inesperado llamando al proveedor: {exc}")

    def _build_result(
        self, request: ProviderRequest, response: Any, latency_ms: int
    ) -> ProviderResult:
        status = getattr(response, "status", "completed") or "completed"
        usage = _usage_from(response, latency_ms, self.model)

        refusal = _first_refusal(response)
        if refusal:
            raise ProviderRefusalError(
                f"El modelo rechazo la peticion en la etapa '{request.stage}': {refusal}",
                details={"stage": request.stage, "request_id": usage.request_id},
            )

        if status == "incomplete":
            reason = getattr(getattr(response, "incomplete_details", None), "reason", "desconocida")
            raise ProviderIncompleteError(
                f"Respuesta incompleta en la etapa '{request.stage}' (motivo: {reason}). "
                "No se reintenta automaticamente: ajusta MAX_OUTPUT_TOKENS o el tamano de entrada.",
                details={"stage": request.stage, "reason": reason, "request_id": usage.request_id},
            )
        if status not in {"completed", None}:
            raise ProviderError(
                f"Estado inesperado de la respuesta: {status}",
                details={"stage": request.stage, "status": status},
            )

        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise ProviderError(
                f"El proveedor no devolvio contenido estructurado en la etapa '{request.stage}'.",
                details={"stage": request.stage, "request_id": usage.request_id},
            )
        if not isinstance(parsed, request.schema_model):
            parsed = request.schema_model.model_validate(
                parsed if isinstance(parsed, dict) else parsed.model_dump()
            )

        logger.info(
            "etapa=%s estado=%s latencia=%dms tokens_in=%d tokens_out=%d",
            request.stage,
            status,
            latency_ms,
            usage.input_tokens,
            usage.output_tokens,
        )
        return ProviderResult(parsed=parsed, usage=usage, raw_status=status)

    def _backoff_seconds(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return min(float(retry_after), BACKOFF_MAX_S)
        base = min(BACKOFF_BASE_S * (2**attempt), BACKOFF_MAX_S)
        return base + self._rng.uniform(0.0, base * 0.25)


# ---------------------------------------------------------------------------
# Ayudas de lectura de la respuesta
# ---------------------------------------------------------------------------


def _error_code(exc: Exception) -> str | None:
    code = getattr(exc, "code", None)
    if code:
        return str(code)
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("code"):
            return str(error["code"])
    return None


def _retry_after(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    for key in ("retry-after", "Retry-After", "retry-after-ms"):
        value = headers.get(key) if hasattr(headers, "get") else None
        if value is None:
            continue
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            continue
        return seconds / 1000.0 if key == "retry-after-ms" else seconds
    return None


def _usage_from(response: Any, latency_ms: int, model: str) -> ProviderUsage:
    usage = getattr(response, "usage", None)
    request_id = getattr(response, "id", None) or getattr(response, "_request_id", None)
    if usage is None:
        return ProviderUsage(
            request_id=str(request_id) if request_id else None,
            latency_ms=latency_ms,
            model=model,
            known=False,
        )
    return ProviderUsage(
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        request_id=str(request_id) if request_id else None,
        latency_ms=latency_ms,
        model=str(getattr(response, "model", model) or model),
        known=True,
    )


def _first_refusal(response: Any) -> str | None:
    """Busca un rechazo explicito en la salida del modelo."""
    for item in getattr(response, "output", None) or []:
        for content in getattr(item, "content", None) or []:
            refusal = getattr(content, "refusal", None)
            if refusal:
                return str(refusal)
            if getattr(content, "type", None) == "refusal":
                return str(getattr(content, "text", "rechazo sin detalle"))
    return None
