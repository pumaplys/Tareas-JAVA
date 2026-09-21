"""Interfaces de proveedor de medios y presupuesto del trabajo.

Tres adaptadores y solo tres: imagenes (OpenAI Images), imagen-a-video
(Runway) y un simulado determinista. Nunca se sustituye una escena `video` por
una imagen ante errores, falta de credenciales o limites de presupuesto.

El presupuesto RESERVA y PERSISTE cada intento remoto antes de enviarlo, de
modo que reiniciar el proceso no lo restablezca. Se cuentan por separado los
intentos de generacion (referencias, imagenes de escena y creacion de clips),
las consultas de estado y las descargas.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ...errors import ExitCode, ViralgenError


class MediaProviderError(ViralgenError):
    exit_code = ExitCode.PROVIDER
    code = "media_provider_error"


class MediaTransientError(MediaProviderError):
    """Fallo clasificado como SEGURO de reintentar: conexion, 429 temporal, 5xx."""

    code = "media_transient_error"


class MediaPermanentError(MediaProviderError):
    """Autenticacion, parametros, cuota agotada o rechazo. No se reintenta."""

    code = "media_permanent_error"


class MediaBudgetExceededError(MediaProviderError):
    """Se alcanzo un limite del trabajo; no se emite nada mas."""

    code = "media_budget_exceeded"


class MediaOutcomeUnknownError(MediaProviderError):
    """Una creacion pudo aceptarse sin que sepamos su resultado.

    Un timeout o un corte tras enviar la creacion puede haber gastado credito.
    Sin `task_id` y sin idempotencia remota documentada no se puede saber si
    se acepto, asi que la operacion queda bloqueada: repetirla automaticamente
    podria pagarla dos veces.
    """

    code = "media_outcome_unknown"


@dataclass
class ImageRequest:
    """Peticion de una imagen. Una imagen por operacion."""

    operation_id: str
    prompt: str
    model: str
    size: str
    quality: str
    output_format: str
    reference_paths: list[Path] = field(default_factory=list)
    scene_id: str | None = None
    seed_text: str = ""

    @property
    def uses_edit(self) -> bool:
        """Con referencias se usa el flujo de EDICION; sin ellas, generacion."""
        return bool(self.reference_paths)

    def identity_payload(self, reference_hashes: list[str], simulation: bool) -> dict:
        """Identidad del ASSET (no de la ejecucion).

        Depende del prompt efectivo, las referencias por hash, la operacion, el
        proveedor, el modelo, los parametros y el modo. Los limites
        administrativos quedan fuera a proposito.
        """
        return {
            "operation": "image_edit" if self.uses_edit else "image_generate",
            "prompt": self.prompt,
            "model": self.model,
            "size": self.size,
            "quality": self.quality,
            "output_format": self.output_format,
            "references": sorted(reference_hashes),
            "simulation": simulation,
        }


@dataclass
class ImageResult:
    image_bytes: bytes
    request_id: str | None = None
    provider_usage: dict[str, int] = field(default_factory=dict)
    revised_prompt: str | None = None
    latency_ms: int = 0


@dataclass
class VideoRequest:
    """Peticion de imagen-a-video."""

    operation_id: str
    scene_id: str
    init_image_path: Path
    init_image_sha256: str
    prompt_text: str
    model: str
    ratio: str
    duration_s: int

    def identity_payload(self, simulation: bool) -> dict:
        return {
            "operation": "image_to_video",
            "init_image_sha256": self.init_image_sha256,
            "prompt_text": self.prompt_text,
            "model": self.model,
            "ratio": self.ratio,
            "duration_s": self.duration_s,
            "simulation": simulation,
        }


@dataclass
class VideoTask:
    task_id: str
    request_id: str | None = None
    latency_ms: int = 0


@dataclass
class VideoStatus:
    """Estado de una tarea remota."""

    task_id: str
    state: str
    """`pending` | `running` | `succeeded` | `failed` | `cancelled`."""

    output_url: str | None = None
    failure: str | None = None

    @property
    def terminal(self) -> bool:
        return self.state in {"succeeded", "failed", "cancelled"}


class MediaBudget:
    """Contadores duros del TRABAJO, persistidos antes de cada envio."""

    def __init__(
        self,
        *,
        limits: dict[str, float],
        used: dict[str, float],
        reserve: Callable[[str, str | None, float], int],
        settle: Callable[..., None],
    ) -> None:
        self.limits = dict(limits)
        self.used_total = dict(used)
        self.used_this_run: dict[str, float] = {clave: 0.0 for clave in used}
        self._reserve = reserve
        self._settle = settle

    def _check(self, clave: str, cantidad: float, detalle: dict) -> None:
        limite = self.limits.get(clave)
        if limite is None:
            return
        if self.used_total.get(clave, 0) + cantidad > limite + 1e-9:
            raise MediaBudgetExceededError(
                f"Se alcanzo el limite {clave}={limite:g} de este trabajo; no se emite "
                "ninguna peticion mas.",
                details={
                    "limit_name": clave,
                    "limit": limite,
                    "used_total": self.used_total.get(clave, 0),
                    **detalle,
                },
            )

    def can_spend(self, clave: str, cantidad: float = 1) -> bool:
        limite = self.limits.get(clave)
        if limite is None:
            return True
        return self.used_total.get(clave, 0) + cantidad <= limite + 1e-9

    def reserve(
        self, kind: str, scene_id: str | None = None, *, video_seconds: float = 0.0
    ) -> int:
        """Reserva y persiste un intento remoto ANTES de enviarlo."""
        contador = {
            "generation": "generation_attempts",
            "status": "status_requests",
            "download": "download_attempts",
        }[kind]
        detalle = {"kind": kind, "scene_id": scene_id}
        self._check(contador, 1, detalle)
        if video_seconds:
            self._check("video_seconds", video_seconds, detalle)
        token = self._reserve(kind, scene_id, video_seconds)
        self.used_total[contador] = self.used_total.get(contador, 0) + 1
        self.used_this_run[contador] = self.used_this_run.get(contador, 0) + 1
        if video_seconds:
            self.used_total["video_seconds"] = (
                self.used_total.get("video_seconds", 0) + video_seconds
            )
            self.used_this_run["video_seconds"] = (
                self.used_this_run.get("video_seconds", 0) + video_seconds
            )
        return token

    def settle(self, token: int, **fields: Any) -> None:
        self._settle(token, **fields)

    def snapshot(self) -> dict[str, Any]:
        return {
            "limits": dict(self.limits),
            "used_total": dict(self.used_total),
            "used_this_run": dict(self.used_this_run),
        }


class ImageProvider(ABC):
    """Proveedor de imagenes."""

    name: str = "abstract"
    model: str = "unknown"

    @abstractmethod
    def create_image(self, request: ImageRequest, budget: MediaBudget) -> ImageResult:
        """Devuelve los bytes de UNA imagen."""


class VideoProvider(ABC):
    """Proveedor de imagen-a-video por tareas."""

    name: str = "abstract"
    model: str = "unknown"
    api_version: str | None = None

    @abstractmethod
    def create_task(self, request: VideoRequest, budget: MediaBudget) -> VideoTask:
        """Crea la tarea y devuelve su identificador REAL."""

    @abstractmethod
    def poll_task(self, task_id: str, budget: MediaBudget) -> VideoStatus:
        """Consulta el estado de una tarea ya creada."""

    @abstractmethod
    def download_output(
        self, status: VideoStatus, destination: Path, budget: MediaBudget, *, max_bytes: int
    ) -> Path:
        """Descarga el resultado a un archivo local, con limite de tamano."""
