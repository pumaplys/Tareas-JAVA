"""Proveedores simulados y deterministas.

QUE SON Y QUE NO SON. Las imagenes son formas geometricas derivadas de un
hash y rotuladas SIMULACION: no son ilustraciones de IA ni evidencia de
calidad visual. Los clips son MP4/H.264 reales creados con FFmpeg a partir de
`testsrc`: son senales de prueba. NUNCA se renombra una imagen a `.mp4` ni se
inventan resultados de ffprobe.

La ruta SOLO DE IMAGENES funciona sin claves, sin modelos reales
configurados, sin red y sin FFmpeg. La ruta de video si necesita FFmpeg, igual
que la real.

Toda salida del mock lleva `simulation=true` y jamas es admisible para el
montaje de produccion.
"""

from __future__ import annotations

import hashlib
import shutil
import time
from pathlib import Path
from typing import Any

from ...errors import ConfigError
from ..capabilities import parse_ratio, parse_size, video_capabilities
from ..imaging import render_placeholder_image
from ..videoprobe import make_test_video, tool_available
from .base import (
    ImageProvider,
    ImageRequest,
    ImageResult,
    MediaBudget,
    MediaPermanentError,
    VideoProvider,
    VideoRequest,
    VideoStatus,
    VideoTask,
)

MOCK_IMAGE_MODEL = "mock-image-1"
MOCK_VIDEO_MODEL = "mock-video-1"


class MockImageProvider(ImageProvider):
    name = "mock"

    def __init__(self, *, settings: Any, seed: int | None = None) -> None:
        self.settings = settings
        self.seed = seed
        self.model = MOCK_IMAGE_MODEL

    def create_image(self, request: ImageRequest, budget: MediaBudget) -> ImageResult:
        from ..capabilities import image_capabilities, validate_image_request

        capacidades = image_capabilities(self.model)
        validate_image_request(
            capacidades,
            size=request.size,
            quality=request.quality,
            output_format=request.output_format,
            edit=request.uses_edit,
            reference_count=len(request.reference_paths),
        )
        if len(request.prompt) > self.settings.media_max_prompt_chars:
            raise MediaPermanentError(
                f"El prompt efectivo tiene {len(request.prompt)} caracteres y el limite "
                f"configurado es {self.settings.media_max_prompt_chars}."
            )

        token = budget.reserve("generation", request.scene_id)
        comenzado = time.monotonic()
        try:
            ancho, alto = parse_size(request.size)
            semilla = self._digest(request)
            temporal = Path(self.settings.data_dir).expanduser() / ".mock-media"
            temporal.mkdir(parents=True, exist_ok=True)
            destino = temporal / f"{semilla[:16]}.{request.output_format}"
            render_placeholder_image(
                destino,
                width=ancho,
                height=alto,
                seed_text=semilla,
                label_lines=[
                    f"escena {request.scene_id or request.operation_id}",
                    f"refs: {len(request.reference_paths)}",
                    f"{ancho}x{alto} {request.quality}",
                ],
                palette=["#2E6F9E", "#D9622B", "#8C8F94", "#F2F0EB"],
                image_format=request.output_format,
                max_pixels=self.settings.media_max_image_pixels,
            )
            contenido = destino.read_bytes()
            destino.unlink(missing_ok=True)
        except Exception as exc:
            budget.settle(token, status="error", error_code="mock_error",
                          error_message=str(exc)[:300])
            raise
        latencia = int((time.monotonic() - comenzado) * 1000)
        budget.settle(token, status="ok", request_id=f"mock-img-{semilla[:16]}",
                      latency_ms=latencia)
        return ImageResult(
            image_bytes=contenido,
            request_id=f"mock-img-{semilla[:16]}",
            provider_usage={},
            latency_ms=latencia,
        )

    def _digest(self, request: ImageRequest) -> str:
        material = "|".join(
            [
                str(self.seed),
                request.operation_id,
                request.prompt,
                request.size,
                request.quality,
                request.output_format,
                str(len(request.reference_paths)),
            ]
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


class MockVideoProvider(VideoProvider):
    """Crea MP4 reales con FFmpeg. Sin FFmpeg, bloquea igual que el real."""

    name = "mock"

    def __init__(self, *, settings: Any, seed: int | None = None, succeeds_after: int = 1) -> None:
        self.settings = settings
        self.seed = seed
        self.model = MOCK_VIDEO_MODEL
        self.api_version = None
        self.capabilities = video_capabilities(MOCK_VIDEO_MODEL)
        self.succeeds_after = succeeds_after
        self._tasks: dict[str, dict] = {}
        self._polls: dict[str, int] = {}

    def create_task(self, request: VideoRequest, budget: MediaBudget) -> VideoTask:
        if request.duration_s not in self.capabilities.durations_s:
            raise MediaPermanentError(
                f"Duracion {request.duration_s}s no declarada para {self.model!r}."
            )
        if not tool_available(self.settings.ffmpeg_path):
            raise ConfigError(
                "El proveedor simulado de video necesita FFmpeg para crear un MP4 real. "
                "Nunca se renombra una imagen a .mp4."
            )
        token = budget.reserve("generation", request.scene_id, video_seconds=request.duration_s)
        material = "|".join(
            [str(self.seed), request.operation_id, request.init_image_sha256,
             request.prompt_text, str(request.duration_s), request.ratio]
        )
        task_id = "mock-task-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]
        self._tasks[task_id] = {"request": request}
        budget.settle(token, status="ok", request_id=task_id, task_id=task_id)
        return VideoTask(task_id=task_id, request_id=task_id, latency_ms=1)

    def poll_task(self, task_id: str, budget: MediaBudget) -> VideoStatus:
        token = budget.reserve("status", None)
        self._polls[task_id] = self._polls.get(task_id, 0) + 1
        budget.settle(token, status="ok", task_id=task_id)
        if self._polls[task_id] < self.succeeds_after:
            return VideoStatus(task_id=task_id, state="running")
        return VideoStatus(
            task_id=task_id, state="succeeded", output_url=f"mock://task/{task_id}"
        )

    def download_output(
        self, status: VideoStatus, destination: Path, budget: MediaBudget, *, max_bytes: int
    ) -> Path:
        token = budget.reserve("download", None)
        datos = self._tasks.get(status.task_id)
        if datos is None:
            budget.settle(token, status="error", error_code="mock_task_desconocida",
                          task_id=status.task_id)
            raise MediaPermanentError(f"Tarea simulada desconocida: {status.task_id}")
        peticion: VideoRequest = datos["request"]
        ancho, alto = parse_ratio(peticion.ratio)
        try:
            make_test_video(
                destination,
                seconds=peticion.duration_s,
                width=ancho,
                height=alto,
                ffmpeg_path=self.settings.ffmpeg_path,
                timeout_s=self.settings.media_http_timeout_s,
            )
        except Exception as exc:
            budget.settle(token, status="error", error_code="mock_error",
                          error_message=str(exc)[:300], task_id=status.task_id)
            raise
        if destination.stat().st_size > max_bytes:
            destination.unlink(missing_ok=True)
            budget.settle(token, status="error", error_code="media_permanent_error",
                          task_id=status.task_id)
            raise MediaPermanentError(
                f"El clip simulado supera el limite de {max_bytes} bytes."
            )
        budget.settle(token, status="ok", task_id=status.task_id)
        return destination


def mock_video_available(settings: Any) -> bool:
    return shutil.which(settings.ffmpeg_path) is not None
