"""Adaptador real de imagen-a-video: Runway.

Endpoints usados:

* ``POST /v1/image_to_video`` — crea la tarea. Cuerpo con `model`,
  `promptImage`, `promptText`, `ratio` y `duration`. Devuelve un `id` de tarea.
* ``GET /v1/tasks/{id}`` — consulta el estado de esa tarea.

Autenticacion `Authorization: Bearer <RUNWAYML_API_SECRET>` y cabecera
`X-Runway-Version`, cuyo valor vive en configuracion y viaja a la procedencia
del manifiesto.

Decisiones del MVP:

* La imagen inicial se envia como data URI JPEG. El limite se aplica a la URI
  YA CODIFICADA, no al binario: por eso el tope propio es sobre la cadena
  final. No hace falta alojar imagenes publicamente.
* El `task_id` se persiste en cuanto llega. Reanudar consulta ESE mismo
  identificador; agotar la espera local no cancela nada ni implica fallo
  remoto.
* La salida se descarga enseguida a un temporal con limite de tamano, porque
  las URLs son temporales. El manifiesto apunta al archivo local. Una descarga
  fallida reintenta la DESCARGA o recupera la salida de la tarea; nunca crea
  otro video.

Referencias: https://docs.dev.runwayml.com/guides/using-the-api/ ,
https://docs.dev.runwayml.com/guides/models/ ,
https://docs.dev.runwayml.com/assets/inputs/ ,
https://docs.dev.runwayml.com/assets/outputs/
"""

from __future__ import annotations

import base64
import random
import time
from pathlib import Path
from typing import Any

import httpx2

from ...logging_setup import get_logger
from ..capabilities import video_capabilities
from .base import (
    MediaBudget,
    MediaOutcomeUnknownError,
    MediaPermanentError,
    MediaTransientError,
    VideoProvider,
    VideoRequest,
    VideoStatus,
    VideoTask,
)

logger = get_logger("media.runway")

BACKOFF_BASE_S = 1.0
BACKOFF_MAX_S = 30.0

#: Estados de tarea que este adaptador reconoce.
STATE_MAP = {
    "PENDING": "pending",
    "THROTTLED": "pending",
    "RUNNING": "running",
    "SUCCEEDED": "succeeded",
    "FAILED": "failed",
    "CANCELLED": "cancelled",
}

NON_RETRYABLE_HINTS = ("quota", "credit", "insufficient", "subscription", "payment")


class RunwayVideoProvider(VideoProvider):
    name = "runway"

    def __init__(self, *, settings: Any, client: Any = None, sleep=time.sleep) -> None:
        secreto, modelo = settings.require_video_settings()
        self.settings = settings
        self.model = modelo
        self.capabilities = video_capabilities(modelo)
        self.api_version = settings.runway_api_version
        self._secret = secreto
        self._sleep = sleep
        self._rng = random.Random(0)
        self._base_url = str(settings.runway_base_url).rstrip("/")
        self._client = client or httpx2.Client(
            timeout=float(settings.media_http_timeout_s),
            transport=httpx2.HTTPTransport(retries=0),
        )

    # -- API ---------------------------------------------------------------

    def create_task(self, request: VideoRequest, budget: MediaBudget) -> VideoTask:
        if request.duration_s not in self.capabilities.durations_s:
            raise MediaPermanentError(
                f"Duracion {request.duration_s}s no declarada para {self.model!r} "
                f"({', '.join(str(d) for d in self.capabilities.durations_s)})."
            )
        if request.ratio not in self.capabilities.ratios:
            raise MediaPermanentError(
                f"Proporcion {request.ratio!r} no declarada para {self.model!r}."
            )

        data_uri = self._data_uri(request.init_image_path)
        cuerpo = {
            "model": self.model,
            "promptImage": data_uri,
            "promptText": request.prompt_text,
            "ratio": request.ratio,
            "duration": request.duration_s,
        }
        # La reserva incluye los segundos de video: se cuentan aunque la tarea
        # acabe fallando, porque ya se pidieron.
        token = budget.reserve("generation", request.scene_id, video_seconds=request.duration_s)
        comenzado = time.monotonic()
        try:
            datos, request_id, _estado = self._send(
                "POST", f"{self._base_url}/v1/image_to_video", json=cuerpo,
                timeout=float(self.settings.media_http_timeout_s),
            )
        except MediaTransientError as exc:
            # Un corte DESPUES de enviar la creacion pudo gastar credito y no
            # tenemos task_id que consultar.
            budget.settle(
                token, status="outcome_unknown", error_code="media_outcome_unknown",
                error_message=exc.message[:500],
            )
            raise MediaOutcomeUnknownError(
                f"No se pudo confirmar la creacion del clip de {request.scene_id}: "
                f"{exc.message}. Sin task_id no se puede saber si se acepto, asi que "
                "se bloquea la repeticion automatica de esta operacion.",
                details={"scene_id": request.scene_id, "operation_id": request.operation_id},
            ) from exc
        except MediaPermanentError as exc:
            budget.settle(
                token, status="error", error_code=exc.code, error_message=exc.message[:500]
            )
            raise

        task_id = datos.get("id") or datos.get("taskId")
        if not task_id:
            budget.settle(
                token, status="outcome_unknown", error_code="media_outcome_unknown",
                error_message="la respuesta no trae identificador de tarea",
            )
            raise MediaOutcomeUnknownError(
                f"La creacion del clip de {request.scene_id} respondio sin identificador "
                "de tarea: no se puede consultar ni repetir con seguridad."
            )
        latencia = int((time.monotonic() - comenzado) * 1000)
        budget.settle(
            token, status="ok", request_id=request_id, task_id=str(task_id),
            latency_ms=latencia,
        )
        return VideoTask(task_id=str(task_id), request_id=request_id, latency_ms=latencia)

    def poll_task(self, task_id: str, budget: MediaBudget) -> VideoStatus:
        token = budget.reserve("status", None)
        try:
            datos, _request_id, _estado = self._send(
                "GET", f"{self._base_url}/v1/tasks/{task_id}",
                timeout=float(self.settings.media_status_timeout_s),
            )
        except MediaTransientError as exc:
            budget.settle(token, status="error", error_code=exc.code, task_id=task_id)
            raise
        except MediaPermanentError as exc:
            budget.settle(token, status="error", error_code=exc.code, task_id=task_id)
            raise
        budget.settle(token, status="ok", task_id=task_id)

        bruto = str(datos.get("status") or "").upper()
        estado = STATE_MAP.get(bruto, "running" if bruto else "pending")
        salidas = datos.get("output") or []
        url = None
        if isinstance(salidas, list) and salidas:
            primera = salidas[0]
            url = primera if isinstance(primera, str) else (primera or {}).get("url")
        fallo = datos.get("failure") or datos.get("failureCode")
        return VideoStatus(
            task_id=task_id,
            state=estado,
            output_url=str(url) if url else None,
            failure=str(fallo)[:300] if fallo else None,
        )

    def download_output(
        self, status: VideoStatus, destination: Path, budget: MediaBudget, *, max_bytes: int
    ) -> Path:
        if not status.output_url:
            raise MediaPermanentError(
                f"La tarea {status.task_id} no expone ninguna salida descargable."
            )
        token = budget.reserve("download", None)
        destination.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        try:
            with self._client.stream(
                "GET", status.output_url, timeout=float(self.settings.media_http_timeout_s)
            ) as respuesta:
                if respuesta.status_code >= 400:
                    raise MediaTransientError(
                        f"La descarga devolvio {respuesta.status_code}"
                    )
                declarado = respuesta.headers.get("content-length")
                if declarado and int(declarado) > max_bytes:
                    raise MediaPermanentError(
                        f"El clip declara {declarado} bytes y el limite es {max_bytes}."
                    )
                # Se controla el tamano aunque no venga Content-Length.
                with destination.open("wb") as salida:
                    for trozo in respuesta.iter_bytes():
                        total += len(trozo)
                        if total > max_bytes:
                            raise MediaPermanentError(
                                f"El clip supera el limite de {max_bytes} bytes."
                            )
                        salida.write(trozo)
        except (httpx2.TimeoutException, httpx2.TransportError) as exc:
            destination.unlink(missing_ok=True)
            budget.settle(token, status="error", error_code="media_transient_error",
                          task_id=status.task_id)
            raise MediaTransientError(f"Fallo de transporte descargando: {exc}") from exc
        except MediaPermanentError:
            destination.unlink(missing_ok=True)
            budget.settle(token, status="error", error_code="media_permanent_error",
                          task_id=status.task_id)
            raise
        except MediaTransientError:
            destination.unlink(missing_ok=True)
            budget.settle(token, status="error", error_code="media_transient_error",
                          task_id=status.task_id)
            raise
        budget.settle(token, status="ok", task_id=status.task_id)
        return destination

    # -- Interno -----------------------------------------------------------

    def _data_uri(self, path: Path) -> str:
        """Data URI JPEG. El limite se aplica a la cadena YA CODIFICADA."""
        contenido = path.read_bytes()
        uri = "data:image/jpeg;base64," + base64.b64encode(contenido).decode("ascii")
        limite = self.settings.media_data_uri_max_bytes
        if len(uri) > limite:
            raise MediaPermanentError(
                f"La data URI de {path.name} ocupa {len(uri)} bytes y el limite propio es "
                f"{limite}. Comprime la imagen inicial o bloquea: no se recorta la escena.",
                details={"uri_bytes": len(uri), "limit": limite},
            )
        return uri

    def _send(self, method: str, url: str, *, timeout: float, **kwargs: Any):
        cabeceras = {
            "Authorization": f"Bearer {self._secret}",
            "X-Runway-Version": self.api_version,
            "Accept": "application/json",
        }
        try:
            respuesta = self._client.request(
                method, url, headers=cabeceras, timeout=timeout, **kwargs
            )
        except httpx2.TimeoutException as exc:
            raise MediaTransientError(f"Tiempo de espera agotado: {exc}") from exc
        except httpx2.TransportError as exc:
            raise MediaTransientError(f"Fallo de transporte: {exc}") from exc

        request_id = respuesta.headers.get("x-request-id") or respuesta.headers.get("request-id")
        if respuesta.status_code >= 400:
            raise self._classify(respuesta)
        try:
            datos = respuesta.json()
        except ValueError as exc:
            raise MediaPermanentError(f"Runway devolvio un JSON invalido: {exc}") from exc
        if not isinstance(datos, dict):
            raise MediaPermanentError("Runway devolvio un JSON que no es un objeto.")
        return datos, (str(request_id) if request_id else None), respuesta.status_code

    def _classify(self, respuesta: Any):
        estado = respuesta.status_code
        detalle = respuesta.text[:300] if hasattr(respuesta, "text") else ""
        pista = detalle.lower()
        if estado in (401, 403):
            return MediaPermanentError(
                "Credenciales de Runway invalidas o sin permisos; no se reintenta."
            )
        if estado == 404:
            return MediaPermanentError(f"Recurso no encontrado en Runway: {detalle}")
        if estado in (400, 422):
            return MediaPermanentError(f"Peticion rechazada por parametros: {detalle}")
        if estado == 402 or any(marca in pista for marca in NON_RETRYABLE_HINTS):
            return MediaPermanentError(f"Credito o cuota agotados: {detalle}")
        if estado == 429:
            return MediaTransientError(
                f"Limite temporal de Runway: {detalle}",
                details={"retry_after": _retry_after(respuesta.headers)},
            )
        if estado >= 500:
            return MediaTransientError(
                f"Error {estado} de Runway: {detalle}",
                details={"retry_after": _retry_after(respuesta.headers)},
            )
        return MediaPermanentError(f"Error {estado} de Runway: {detalle}")


def _retry_after(headers: Any) -> float | None:
    valor = headers.get("retry-after") if hasattr(headers, "get") else None
    try:
        return float(valor) if valor is not None else None
    except (TypeError, ValueError):
        return None
