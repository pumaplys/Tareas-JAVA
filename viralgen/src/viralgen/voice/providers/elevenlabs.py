"""Adaptador real de ElevenLabs.

Endpoints usados:

* ``POST /v1/text-to-speech/{voice_id}/with-timestamps`` — sintesis con
  tiempos por caracter. ``output_format`` va como parametro de consulta;
  ``text`` y ``model_id`` en el cuerpo JSON.
  https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps
* ``POST /v1/forced-alignment`` — alineacion sobre un WAV ya sintetizado.
  Solo se usa con VOICE_ALLOW_FORCED_ALIGNMENT=true.
  https://elevenlabs.io/docs/api-reference/forced-alignment/create

Notas de implementacion:

* Autenticacion por cabecera ``xi-api-key``. La clave nunca se registra.
* La respuesta trae ``audio_base64`` y PUEDE traer ``alignment`` y
  ``normalized_alignment``: no se asume que existan.
* El base64 se decodifica y se descarta enseguida; no se guarda en el JSON ni
  en los logs.
* El cliente HTTP se construye con `retries=0` en el transporte: los reintentos
  los gestiona esta clase, sin duplicarlos.
* `request_id` solo se guarda si el servidor lo envia. No se inventa.
"""

from __future__ import annotations

import base64
import binascii
import random
import time
from pathlib import Path
from typing import Any

import httpx2

from ...logging_setup import get_logger
from ..alignment import CharAlignment
from ..audio import PcmFormat, decode_to_wav
from .base import (
    SynthesisRequest,
    SynthesisResult,
    VoiceBudget,
    VoicePermanentError,
    VoiceProvider,
    VoiceTransientError,
)

logger = get_logger("voice.elevenlabs")

BACKOFF_BASE_S = 1.0
BACKOFF_MAX_S = 30.0

#: Cabeceras de las que se acepta el identificador de solicitud del servidor.
REQUEST_ID_HEADERS = ("request-id", "x-request-id", "x-amzn-requestid")

#: Codigos del proveedor que no se reintentan aunque lleguen con 429.
NON_RETRYABLE_HINTS = (
    "quota_exceeded",
    "insufficient_credits",
    "payment_required",
    "subscription",
    "voice_limit_reached",
)


class ElevenLabsProvider(VoiceProvider):
    name = "elevenlabs"
    supports_forced_alignment = True

    def __init__(self, *, settings: Any, client: Any = None, sleep=time.sleep) -> None:
        api_key, model_id = settings.require_voice_settings()
        self.settings = settings
        self.model_id = model_id
        self._api_key = api_key
        self._sleep = sleep
        self._rng = random.Random(0)
        self.fmt = PcmFormat(
            sample_rate_hz=settings.voice_sample_rate_hz,
            channels=settings.voice_channels,
            sample_width_bytes=settings.voice_sample_width_bytes,
        )
        self._base_url = str(settings.elevenlabs_base_url).rstrip("/")
        self._client = client or httpx2.Client(
            timeout=float(settings.voice_request_timeout_seconds),
            # Sin reintentos en el transporte: los gestiona esta clase.
            transport=httpx2.HTTPTransport(retries=0),
        )

    # -- API ---------------------------------------------------------------

    def synthesize(self, request: SynthesisRequest, budget: VoiceBudget) -> SynthesisResult:
        if len(request.text) > self.settings.voice_max_request_chars:
            raise VoicePermanentError(
                f"La escena {request.scene_id} tiene {len(request.text)} caracteres y el "
                f"limite configurado es {self.settings.voice_max_request_chars}.",
                details={"scene_id": request.scene_id},
            )

        url = f"{self._base_url}/v1/text-to-speech/{request.voice_id}/with-timestamps"
        params = {"output_format": request.output_format}
        body: dict[str, Any] = {"text": request.text, "model_id": request.model_id}
        if request.settings:
            body["voice_settings"] = dict(request.settings)
        # Contexto para la continuidad: NO se pronuncia.
        if request.previous_text:
            body["previous_text"] = request.previous_text
        if request.next_text:
            body["next_text"] = request.next_text

        payload = self._request_with_retries(
            "POST", url, budget, kind="synthesis", scene_id=request.scene_id,
            params=params, json=body, characters=len(request.text),
        )
        data, request_id, latency_ms, status = payload

        audio_b64 = data.get("audio_base64")
        if not isinstance(audio_b64, str) or not audio_b64:
            raise VoicePermanentError(
                f"La respuesta de la escena {request.scene_id} no trae audio_base64.",
                details={"scene_id": request.scene_id, "request_id": request_id},
            )
        try:
            audio_bytes = base64.b64decode(audio_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise VoicePermanentError(
                f"audio_base64 invalido en la escena {request.scene_id}: {exc}"
            ) from exc
        finally:
            audio_b64 = ""  # se descarta enseguida
            data.pop("audio_base64", None)

        return SynthesisResult(
            audio_bytes=audio_bytes,
            audio_format=_container_of(request.output_format),
            alignment=_parse_alignment(data.get("alignment")),
            normalized_alignment=_parse_alignment(data.get("normalized_alignment")),
            request_id=request_id,
            latency_ms=latency_ms,
            http_status=status,
            characters_sent=len(request.text),
        )

    def force_align(
        self, audio_path: Path, text: str, budget: VoiceBudget
    ) -> CharAlignment | None:
        """Alineacion forzada sobre el WAV ya generado. No regenera la voz."""
        url = f"{self._base_url}/v1/forced-alignment"
        with audio_path.open("rb") as handle:
            files = {"file": (audio_path.name, handle.read(), "audio/wav")}
        data, _request_id, _latency, _status = self._request_with_retries(
            "POST", url, budget, kind="forced_alignment", scene_id=None,
            files=files, data={"text": text}, characters=len(text),
        )
        caracteres = data.get("characters")
        if not caracteres:
            logger.warning("la alineacion forzada no devolvio informacion por caracter")
            return None
        return _parse_forced_characters(caracteres)

    def decode_to_internal(
        self, result: SynthesisResult, source: Path, destination: Path
    ) -> None:
        """Decodifica a WAV PCM 16 bits mono 24 kHz con FFmpeg."""
        decode_to_wav(
            source,
            destination,
            fmt=self.fmt,
            ffmpeg_path=self.settings.ffmpeg_path,
            timeout_s=self.settings.ffmpeg_timeout_seconds,
        )

    # -- Transporte --------------------------------------------------------

    def _request_with_retries(
        self,
        method: str,
        url: str,
        budget: VoiceBudget,
        *,
        kind: str,
        scene_id: str | None,
        characters: int,
        **kwargs: Any,
    ) -> tuple[dict, str | None, int, int]:
        intentos = self.settings.voice_max_transport_retries + 1
        ultimo: VoiceTransientError | None = None

        for intento in range(intentos):
            # La reserva se persiste ANTES de enviar: un timeout puede haber
            # consumido credito aunque no lleguemos a ver la respuesta.
            token = budget.reserve(kind, scene_id)
            comenzado = time.monotonic()
            try:
                data, request_id, status = self._send(method, url, **kwargs)
            except VoiceTransientError as exc:
                latency_ms = int((time.monotonic() - comenzado) * 1000)
                budget.settle(
                    token, status="error", error_code=exc.code,
                    error_message=exc.message[:500], latency_ms=latency_ms,
                    characters=characters,
                    http_status=exc.details.get("http_status"),
                )
                ultimo = exc
                logger.warning(
                    "voz etapa=%s intento=%d/%d fallo transitorio (%s)",
                    kind, intento + 1, intentos, exc.code,
                )
                if intento == intentos - 1 or not budget.can_spend():
                    break
                self._sleep(self._backoff(intento, exc.details.get("retry_after")))
                continue
            except VoicePermanentError as exc:
                latency_ms = int((time.monotonic() - comenzado) * 1000)
                budget.settle(
                    token, status="error", error_code=exc.code,
                    error_message=exc.message[:500], latency_ms=latency_ms,
                    characters=characters,
                    http_status=exc.details.get("http_status"),
                )
                raise

            latency_ms = int((time.monotonic() - comenzado) * 1000)
            budget.settle(
                token, status="ok", request_id=request_id, characters=characters,
                latency_ms=latency_ms, http_status=status,
            )
            return data, request_id, latency_ms, status

        assert ultimo is not None
        raise VoiceTransientError(
            f"Se agotaron los reintentos de voz en la etapa '{kind}': {ultimo.message}",
            details={**ultimo.details, "attempts": intentos},
        )

    def _send(self, method: str, url: str, **kwargs: Any) -> tuple[dict, str | None, int]:
        headers = {"xi-api-key": self._api_key, "accept": "application/json"}
        limite = self.settings.voice_max_response_bytes
        try:
            with self._client.stream(method, url, headers=headers, **kwargs) as respuesta:
                declarado = respuesta.headers.get("content-length")
                if declarado and int(declarado) > limite:
                    raise VoicePermanentError(
                        f"La respuesta declara {declarado} bytes y el limite es {limite}."
                    )
                trozos: list[bytes] = []
                total = 0
                for trozo in respuesta.iter_bytes():
                    total += len(trozo)
                    if total > limite:
                        raise VoicePermanentError(
                            f"La respuesta supera el limite de {limite} bytes."
                        )
                    trozos.append(trozo)
                cuerpo = b"".join(trozos)
                estado = respuesta.status_code
                request_id = _request_id_of(respuesta.headers)
                retry_after = _retry_after(respuesta.headers)
        except httpx2.TimeoutException as exc:
            raise VoiceTransientError(f"Tiempo de espera agotado: {exc}") from exc
        except httpx2.TransportError as exc:
            raise VoiceTransientError(f"Fallo de transporte: {exc}") from exc

        if estado >= 400:
            raise self._classify(estado, cuerpo, request_id, retry_after)

        import json as _json

        try:
            data = _json.loads(cuerpo)
        except ValueError as exc:
            raise VoicePermanentError(f"El proveedor devolvio un JSON invalido: {exc}") from exc
        if not isinstance(data, dict):
            raise VoicePermanentError("El proveedor devolvio un JSON que no es un objeto.")
        return data, request_id, estado

    def _classify(
        self, status: int, body: bytes, request_id: str | None, retry_after: float | None
    ):
        detalle = body.decode("utf-8", "replace")[:400]
        pista = detalle.lower()
        comun = {"http_status": status, "request_id": request_id}

        if status == 401:
            return VoicePermanentError(
                "Clave de ElevenLabs invalida o revocada; no se reintenta.", details=comun
            )
        if status == 403:
            return VoicePermanentError(
                "Permisos insuficientes para esa voz o modelo; no se reintenta.", details=comun
            )
        if status == 404:
            return VoicePermanentError(
                "Voz o modelo no encontrados; revisa ELEVENLABS_VOICE_ID y "
                "ELEVENLABS_MODEL_ID.",
                details=comun,
            )
        if status in (400, 422):
            return VoicePermanentError(
                f"Peticion rechazada por parametros o modelo: {detalle}", details=comun
            )
        if status in (402, 413):
            return VoicePermanentError(
                f"Peticion rechazada (credito o tamano): {detalle}", details=comun
            )
        if status == 429:
            if any(pista.count(marca) for marca in NON_RETRYABLE_HINTS):
                return VoicePermanentError(
                    f"Cuota o credito agotados; no se reintenta: {detalle}", details=comun
                )
            return VoiceTransientError(
                f"Limite de peticiones temporal: {detalle}",
                details={**comun, "retry_after": retry_after},
            )
        if status >= 500:
            return VoiceTransientError(
                f"Error {status} del proveedor: {detalle}",
                details={**comun, "retry_after": retry_after},
            )
        return VoicePermanentError(f"Error {status} del proveedor: {detalle}", details=comun)

    def _backoff(self, intento: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return min(float(retry_after), BACKOFF_MAX_S)
        base = min(BACKOFF_BASE_S * (2**intento), BACKOFF_MAX_S)
        return base + self._rng.uniform(0.0, base * 0.25)


# ---------------------------------------------------------------------------
# Lectura de la respuesta
# ---------------------------------------------------------------------------


def _container_of(output_format: str) -> str:
    """Contenedor implicito del formato pedido (mp3_44100_128 -> mp3)."""
    return output_format.split("_", 1)[0].lower()


def _request_id_of(headers: Any) -> str | None:
    for nombre in REQUEST_ID_HEADERS:
        valor = headers.get(nombre)
        if valor:
            return str(valor)[:200]
    return None


def _retry_after(headers: Any) -> float | None:
    valor = headers.get("retry-after")
    if valor is None:
        return None
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _parse_alignment(raw: Any) -> CharAlignment | None:
    """Lee una alineacion del proveedor. Devuelve None si no viene o no sirve."""
    if not isinstance(raw, dict):
        return None
    caracteres = raw.get("characters")
    inicios = raw.get("character_start_times_seconds")
    finales = raw.get("character_end_times_seconds")
    if not isinstance(caracteres, list) or not isinstance(inicios, list):
        return None
    if not isinstance(finales, list):
        return None
    try:
        return CharAlignment(
            characters=[str(item) for item in caracteres],
            start_times_s=[float(item) for item in inicios],
            end_times_s=[float(item) for item in finales],
        )
    except (TypeError, ValueError):
        return None


def _parse_forced_characters(raw: Any) -> CharAlignment | None:
    """Convierte la salida por caracter de forced-alignment."""
    if not isinstance(raw, list) or not raw:
        return None
    caracteres: list[str] = []
    inicios: list[float] = []
    finales: list[float] = []
    for elemento in raw:
        if not isinstance(elemento, dict):
            return None
        texto = elemento.get("text")
        inicio = elemento.get("start")
        fin = elemento.get("end")
        if texto is None or inicio is None or fin is None:
            return None
        try:
            inicios.append(float(inicio))
            finales.append(float(fin))
        except (TypeError, ValueError):
            return None
        caracteres.append(str(texto))
    return CharAlignment(caracteres, inicios, finales)
