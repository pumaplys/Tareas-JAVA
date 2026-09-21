"""Adaptador real de imagenes: OpenAI Images API.

Dos flujos, tal y como documenta la guia oficial:

* SIN referencias -> `client.images.generate(...)`, que el SDK serializa como
  `POST /v1/images/generations` con cuerpo JSON.
* CON referencias -> `client.images.edit(image=[...], ...)`, que el SDK
  serializa como `POST /v1/images/edits` en multipart, con un campo `image[]`
  por referencia.

Ambos payloads se comprobaron contra el SDK instalado con un transporte
simulado (ver `tests/test_media_providers.py`). NO se han validado contra la
API en vivo: este entorno no tiene credenciales.

La guia reconoce ademas limites de consistencia entre generaciones: las
referencias mejoran el control, pero no garantizan identidad perfecta.

Referencia: https://developers.openai.com/api/docs/guides/image-generation
"""

from __future__ import annotations

import base64
import binascii
import random
import time
from pathlib import Path
from typing import Any

from ...logging_setup import get_logger
from ..capabilities import image_capabilities, validate_image_request
from .base import (
    ImageProvider,
    ImageRequest,
    ImageResult,
    MediaBudget,
    MediaOutcomeUnknownError,
    MediaPermanentError,
    MediaTransientError,
)

logger = get_logger("media.openai_images")

BACKOFF_BASE_S = 1.0
BACKOFF_MAX_S = 30.0

#: Codigos que no se reintentan aunque lleguen con 429.
NON_RETRYABLE_CODES = {
    "insufficient_quota",
    "billing_hard_limit_reached",
    "invalid_api_key",
    "model_not_found",
    "unsupported_parameter",
    "unsupported_value",
    "moderation_blocked",
    "content_policy_violation",
}

MIME_BY_FORMAT = {"jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


class OpenAIImageProvider(ImageProvider):
    name = "openai"

    def __init__(self, *, settings: Any, client: Any = None, sleep=time.sleep) -> None:
        self.model = settings.require_image_settings()
        self.settings = settings
        self.capabilities = image_capabilities(self.model)
        self._sleep = sleep
        self._rng = random.Random(0)
        if client is not None:
            self._client = client
        else:
            from openai import OpenAI

            kwargs: dict[str, Any] = {
                "api_key": settings.openai_api_key.get_secret_value(),
                "timeout": float(settings.media_image_timeout_s),
                # El presupuesto lo lleva la aplicacion: el SDK no reintenta.
                "max_retries": 0,
            }
            if settings.openai_base_url:
                kwargs["base_url"] = settings.openai_base_url
            self._client = OpenAI(**kwargs)

    # -- API ---------------------------------------------------------------

    def create_image(self, request: ImageRequest, budget: MediaBudget) -> ImageResult:
        # Validacion de capacidades ANTES de cualquier HTTP.
        validate_image_request(
            self.capabilities,
            size=request.size,
            quality=request.quality,
            output_format=request.output_format,
            edit=request.uses_edit,
            reference_count=len(request.reference_paths),
        )
        if len(request.prompt) > self.settings.media_max_prompt_chars:
            raise MediaPermanentError(
                f"El prompt efectivo de {request.scene_id or request.operation_id} tiene "
                f"{len(request.prompt)} caracteres y el limite configurado es "
                f"{self.settings.media_max_prompt_chars}. No se recorta en silencio.",
                details={"scene_id": request.scene_id, "chars": len(request.prompt)},
            )

        intentos = self.settings.media_max_safe_retries + 1
        ultimo: MediaTransientError | None = None
        for intento in range(intentos):
            token = budget.reserve("generation", request.scene_id)
            comenzado = time.monotonic()
            try:
                respuesta = self._call(request)
            except MediaTransientError as exc:
                latencia = int((time.monotonic() - comenzado) * 1000)
                budget.settle(
                    token, status="error", error_code=exc.code,
                    error_message=exc.message[:500], latency_ms=latencia,
                )
                ultimo = exc
                logger.warning(
                    "imagen intento=%d/%d fallo seguro (%s)", intento + 1, intentos, exc.code
                )
                if intento == intentos - 1 or not budget.can_spend("generation_attempts"):
                    break
                self._sleep(self._backoff(intento, exc.details.get("retry_after")))
                continue
            except MediaOutcomeUnknownError as exc:
                budget.settle(
                    token, status="outcome_unknown", error_code=exc.code,
                    error_message=exc.message[:500],
                )
                raise
            except MediaPermanentError as exc:
                budget.settle(
                    token, status="error", error_code=exc.code,
                    error_message=exc.message[:500],
                )
                raise

            latencia = int((time.monotonic() - comenzado) * 1000)
            resultado = self._read(request, respuesta, latencia)
            budget.settle(
                token, status="ok", request_id=resultado.request_id, latency_ms=latencia
            )
            return resultado

        assert ultimo is not None
        raise MediaTransientError(
            f"Se agotaron los reintentos seguros generando la imagen de "
            f"{request.scene_id or request.operation_id}: {ultimo.message}",
            details={**ultimo.details, "attempts": intentos},
        )

    # -- Interno -----------------------------------------------------------

    def _call(self, request: ImageRequest) -> Any:
        comun: dict[str, Any] = {
            "model": self.model,
            "prompt": request.prompt,
            "n": 1,
            "size": request.size,
            "quality": request.quality,
            "output_format": request.output_format,
        }
        try:
            if request.uses_edit:
                # Cada referencia va como un `image[]` del multipart.
                adjuntos = [
                    (
                        ruta.name,
                        ruta.read_bytes(),
                        MIME_BY_FORMAT.get(ruta.suffix.lstrip(".").lower(), "image/jpeg"),
                    )
                    for ruta in request.reference_paths
                ]
                return self._client.images.edit(image=adjuntos, **comun)
            return self._client.images.generate(**comun)
        except Exception as exc:
            raise self._classify(exc, request) from exc

    def _classify(self, exc: Exception, request: ImageRequest):
        import openai

        if isinstance(exc, openai.APITimeoutError):
            # Un timeout despues de enviar puede haber gastado credito y la
            # API de imagenes no devuelve un identificador de tarea que
            # consultar: no se repite automaticamente.
            return MediaOutcomeUnknownError(
                f"Tiempo de espera agotado generando la imagen de "
                f"{request.scene_id or request.operation_id}. La peticion pudo aceptarse: "
                "se bloquea la repeticion automatica de esta operacion.",
                details={"scene_id": request.scene_id, "operation_id": request.operation_id},
            )
        if isinstance(exc, openai.APIConnectionError):
            return MediaOutcomeUnknownError(
                f"Conexion cortada generando la imagen de "
                f"{request.scene_id or request.operation_id}. No se puede saber si la "
                "peticion se acepto; se bloquea la repeticion automatica.",
                details={"scene_id": request.scene_id, "operation_id": request.operation_id},
            )
        if isinstance(exc, openai.RateLimitError):
            codigo = _error_code(exc)
            if codigo in NON_RETRYABLE_CODES:
                return MediaPermanentError(
                    f"Cuota o limite de facturacion agotados ({codigo}); no se reintenta."
                )
            return MediaTransientError(
                f"Limite de peticiones temporal: {exc}",
                details={"retry_after": _retry_after(exc)},
            )
        if isinstance(exc, openai.InternalServerError):
            return MediaTransientError(
                f"Error 5xx del proveedor: {exc}", details={"retry_after": _retry_after(exc)}
            )
        if isinstance(exc, openai.AuthenticationError):
            return MediaPermanentError("Clave de API invalida o revocada; no se reintenta.")
        if isinstance(exc, openai.PermissionDeniedError):
            return MediaPermanentError("Permisos insuficientes para el modelo de imagen.")
        if isinstance(exc, openai.NotFoundError):
            return MediaPermanentError(
                f"Modelo o recurso no encontrado ({self.model}); revisa OPENAI_IMAGE_MODEL."
            )
        if isinstance(exc, openai.BadRequestError):
            # Incluye rechazos de moderacion. El prompt NO se reescribe solo.
            return MediaPermanentError(
                f"Peticion rechazada por el proveedor: {exc}. El prompt no se modifica "
                "automaticamente."
            )
        if isinstance(exc, openai.APIStatusError):
            estado = getattr(exc, "status_code", None)
            if estado is not None and 500 <= int(estado) < 600:
                return MediaTransientError(f"Error {estado} del proveedor: {exc}")
            return MediaPermanentError(f"Error {estado} del proveedor: {exc}")
        if isinstance(exc, openai.OpenAIError):
            return MediaPermanentError(f"Error del SDK de OpenAI: {exc}")
        return MediaPermanentError(f"Error inesperado llamando al proveedor: {exc}")

    def _read(self, request: ImageRequest, respuesta: Any, latencia: int) -> ImageResult:
        datos = getattr(respuesta, "data", None) or []
        if not datos:
            raise MediaPermanentError(
                f"El proveedor no devolvio ninguna imagen para "
                f"{request.scene_id or request.operation_id}."
            )
        primera = datos[0]
        b64 = getattr(primera, "b64_json", None)
        if not b64:
            raise MediaPermanentError(
                "La respuesta no trae b64_json; este MVP no descarga imagenes por URL."
            )
        # Limite ANTES de decodificar: 4 bytes de base64 son 3 de imagen.
        limite = self.settings.media_max_image_response_mib * 1024 * 1024
        if len(b64) > limite * 4 // 3 + 4:
            raise MediaPermanentError(
                f"La respuesta de imagen supera el limite de "
                f"{self.settings.media_max_image_response_mib} MiB."
            )
        try:
            contenido = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise MediaPermanentError(f"base64 invalido en la respuesta: {exc}") from exc
        if len(contenido) > limite:
            raise MediaPermanentError(
                f"La imagen decodificada supera el limite de "
                f"{self.settings.media_max_image_response_mib} MiB."
            )

        uso = getattr(respuesta, "usage", None)
        informado: dict[str, int] = {}
        for campo in ("input_tokens", "output_tokens", "total_tokens"):
            valor = getattr(uso, campo, None) if uso is not None else None
            if isinstance(valor, int):
                informado[campo] = valor

        return ImageResult(
            image_bytes=contenido,
            request_id=_request_id(respuesta),
            provider_usage=informado,
            revised_prompt=getattr(primera, "revised_prompt", None),
            latency_ms=latencia,
        )

    def _backoff(self, intento: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return min(float(retry_after), BACKOFF_MAX_S)
        base = min(BACKOFF_BASE_S * (2**intento), BACKOFF_MAX_S)
        return base + self._rng.uniform(0.0, base * 0.25)


def _error_code(exc: Exception) -> str | None:
    codigo = getattr(exc, "code", None)
    if codigo:
        return str(codigo)
    cuerpo = getattr(exc, "body", None)
    if isinstance(cuerpo, dict):
        error = cuerpo.get("error")
        if isinstance(error, dict) and error.get("code"):
            return str(error["code"])
    return None


def _retry_after(exc: Exception) -> float | None:
    respuesta = getattr(exc, "response", None)
    cabeceras = getattr(respuesta, "headers", None)
    if not cabeceras or not hasattr(cabeceras, "get"):
        return None
    valor = cabeceras.get("retry-after")
    try:
        return float(valor) if valor is not None else None
    except (TypeError, ValueError):
        return None


def _request_id(respuesta: Any) -> str | None:
    valor = getattr(respuesta, "_request_id", None) or getattr(respuesta, "id", None)
    return str(valor)[:200] if valor else None
