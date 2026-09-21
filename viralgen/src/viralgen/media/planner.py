"""Preflight del modulo 3: `media plan`.

No usa red ni genera nada. Valida entradas y capacidades locales, enumera lo
que haria y detecta los problemas de TODAS las escenas antes de comprar la
primera imagen: modelos incompatibles, duraciones imposibles, exceso de
escenas de video, prompts demasiado largos y ejecutables ausentes.

`generate` repite este mismo preflight antes de emitir nada.

Los prompts NO se truncan en silencio: si uno se pasa del limite, bloquea.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

from ..diskutil import free_mb
from ..errors import ConfigError
from ..textutil import sha256_json
from .capabilities import (
    image_capabilities,
    parse_ratio,
    parse_size,
    validate_image_request,
    video_capabilities,
)
from .imaging import pad_color_from_palette
from .prompts import MEDIA_PROMPT_VERSION, build_effective_prompt, build_motion_prompt
from .providers.mock import MOCK_IMAGE_MODEL, MOCK_VIDEO_MODEL
from .references import ReferenceSelection
from .timeline import VisualTimeline
from .videoprobe import tool_available


@dataclass
class PlanIssue:
    code: str
    message: str
    severity: str
    blocking: bool
    scene_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "blocking": self.blocking,
            "scene_id": self.scene_id,
        }


@dataclass
class ScenePlan:
    scene_id: str
    order: int
    requested_type: str
    start_sample: int
    end_sample: int
    duration_s: float
    character_ids: list[str]
    effective_prompt: str
    prompt_chars: int
    image_size: str
    image_quality: str
    image_format: str
    needs_video: bool
    video_duration_s: int | None = None
    video_ratio: str | None = None
    motion_prompt: str | None = None
    image_cache_key: str = ""
    image_cache_hit: bool = False
    video_cache_key: str | None = None
    video_cache_hit: bool = False
    seed_size: str | None = None

    def to_dict(self) -> dict:
        return {
            "scene_id": self.scene_id,
            "order": self.order,
            "requested_type": self.requested_type,
            "start_sample": self.start_sample,
            "end_sample": self.end_sample,
            "duration_s": round(self.duration_s, 6),
            "character_ids": list(self.character_ids),
            "prompt_chars": self.prompt_chars,
            "image_size": self.image_size,
            "image_cache_hit": self.image_cache_hit,
            "needs_video": self.needs_video,
            "video_duration_s": self.video_duration_s,
            "video_ratio": self.video_ratio,
            "video_cache_hit": self.video_cache_hit,
        }


@dataclass
class MediaPlan:
    scenes: list[ScenePlan]
    issues: list[PlanIssue] = field(default_factory=list)
    reference_plan: dict[str, str] = field(default_factory=dict)
    missing_credentials: list[str] = field(default_factory=list)
    missing_tools: list[str] = field(default_factory=list)
    image_model: str = ""
    video_model: str | None = None
    target_width: int = 0
    target_height: int = 0
    target_fps: int = 0
    free_disk_mb: float = 0.0
    budget: dict[str, Any] = field(default_factory=dict)
    prompt_version: str = MEDIA_PROMPT_VERSION

    @property
    def blocked(self) -> bool:
        return any(issue.blocking for issue in self.issues)

    @property
    def video_scene_count(self) -> int:
        return sum(1 for escena in self.scenes if escena.needs_video)

    @property
    def total_video_seconds(self) -> float:
        return float(
            sum(escena.video_duration_s or 0 for escena in self.scenes if escena.needs_video)
        )

    def can_run(self) -> bool:
        """Si ademas de no estar bloqueado tiene con que ejecutarse."""
        return not self.blocked and not self.missing_credentials and not self.missing_tools

    def to_dict(self) -> dict:
        return {
            "prompt_version": self.prompt_version,
            "image_model": self.image_model,
            "video_model": self.video_model,
            "target": {
                "width": self.target_width,
                "height": self.target_height,
                "fps": self.target_fps,
            },
            "scenes": [escena.to_dict() for escena in self.scenes],
            "images_to_generate": sum(
                1 for escena in self.scenes if not escena.image_cache_hit
            ),
            "clips_to_generate": sum(
                1 for escena in self.scenes if escena.needs_video and not escena.video_cache_hit
            ),
            "video_scene_count": self.video_scene_count,
            "total_video_seconds": self.total_video_seconds,
            "cache_hits": sum(
                int(escena.image_cache_hit) + int(escena.video_cache_hit)
                for escena in self.scenes
            ),
            "references": dict(self.reference_plan),
            "missing_credentials": list(self.missing_credentials),
            "missing_tools": list(self.missing_tools),
            "free_disk_mb": round(self.free_disk_mb, 1),
            "budget": dict(self.budget),
            "issues": [issue.to_dict() for issue in self.issues],
            "blocked": self.blocked,
            "can_run": self.can_run(),
        }


def build_plan(
    *,
    settings: Any,
    document: Any,
    timeline: VisualTimeline,
    bible: Any,
    selection: ReferenceSelection,
    simulation: bool,
    media_storage: Any | None,
    data_dir: Path,
) -> MediaPlan:
    """Construye el plan completo sin emitir nada."""
    issues: list[PlanIssue] = []

    def problema(code: str, message: str, *, blocking: bool, scene_id: str | None = None,
                 severity: str = "error") -> None:
        issues.append(PlanIssue(code, message[:200], severity, blocking, scene_id))

    # --- Modelos y capacidades ---------------------------------------------
    if simulation:
        image_model = MOCK_IMAGE_MODEL
        video_model = MOCK_VIDEO_MODEL
    else:
        image_model = settings.openai_image_model or ""
        video_model = settings.runway_model or None

    capacidades_imagen = None
    if image_model:
        try:
            capacidades_imagen = image_capabilities(image_model)
        except ConfigError as exc:
            problema("modelo_de_imagen_desconocido", exc.message, blocking=True)
    else:
        problema(
            "modelo_de_imagen_no_declarado",
            "Falta OPENAI_IMAGE_MODEL: el modelo de imagenes se declara explicitamente.",
            blocking=True,
        )

    escenas_video = [escena for escena in timeline.scenes if escena.asset_type == "video"]
    capacidades_video = None
    if escenas_video:
        if not video_model:
            problema(
                "modelo_de_video_no_declarado",
                "El guion pide clips y falta RUNWAY_MODEL. Nunca se sustituye una escena "
                "de video por una imagen.",
                blocking=True,
            )
        else:
            try:
                capacidades_video = video_capabilities(video_model)
            except ConfigError as exc:
                problema("modelo_de_video_desconocido", exc.message, blocking=True)

    # --- Geometria objetivo -------------------------------------------------
    objetivo_ancho = document.video.width
    objetivo_alto = document.video.height
    objetivo_fps = document.video.fps
    aspecto = Fraction(objetivo_ancho, objetivo_alto)

    tamano_imagen = ""
    if capacidades_imagen is not None:
        tamano_imagen = capacidades_imagen.best_vertical(aspecto)
        ancho_gen, alto_gen = parse_size(tamano_imagen)
        if Fraction(ancho_gen, alto_gen) != aspecto:
            problema(
                "proporcion_no_exacta",
                f"El modelo no ofrece {aspecto.numerator}:{aspecto.denominator}; se usara "
                f"{tamano_imagen} y la diferencia se resuelve con un plan de adaptacion "
                f"explicito ({settings.media_geometry_policy}).",
                blocking=False,
                severity="info",
            )

    # --- Escenas ------------------------------------------------------------
    escenas_guion = {escena.scene_id: escena for escena in document.scenes}
    planes: list[ScenePlan] = []
    for visual in timeline.scenes:
        escena = escenas_guion[visual.scene_id]
        prompt = build_effective_prompt(
            scene=escena, bible=bible, channel=document.channel.value
        )
        if len(prompt) > settings.media_max_prompt_chars:
            problema(
                "prompt_demasiado_largo",
                f"{visual.scene_id}: el prompt efectivo tiene {len(prompt)} caracteres y el "
                f"limite es {settings.media_max_prompt_chars}. No se trunca en silencio.",
                blocking=True,
                scene_id=visual.scene_id,
            )

        personajes = list(escena.character_ids)
        if capacidades_imagen is not None:
            referencias_previstas = len([cid for cid in personajes if cid in selection.character_ids])
            try:
                validate_image_request(
                    capacidades_imagen,
                    size=tamano_imagen,
                    quality=settings.media_image_quality,
                    output_format=settings.media_image_format,
                    edit=bool(referencias_previstas),
                    reference_count=referencias_previstas,
                )
            except ConfigError as exc:
                problema(
                    "combinacion_no_soportada", exc.message, blocking=True,
                    scene_id=visual.scene_id,
                )

        necesita_video = visual.asset_type == "video"
        duracion_video: int | None = None
        ratio: str | None = None
        motion: str | None = None
        seed_size: str | None = None
        if necesita_video and capacidades_video is not None:
            ratio = capacidades_video.default_ratio
            duracion_video = capacidades_video.shortest_duration_covering(visual.duration_s)
            if duracion_video is None:
                problema(
                    "duration_not_supported",
                    f"{visual.scene_id} dura {visual.duration_s:.2f} s y {video_model!r} solo "
                    f"admite {capacidades_video.durations_s}. No se parten escenas, ni se "
                    "repiten imagenes, ni se congelan cuadros, ni se cambia la velocidad.",
                    blocking=True,
                    scene_id=visual.scene_id,
                )
            motion = build_motion_prompt(scene=escena)
            ancho_ratio, alto_ratio = parse_ratio(ratio)
            seed_size = f"{ancho_ratio}x{alto_ratio}"

        plan_escena = ScenePlan(
            scene_id=visual.scene_id,
            order=visual.order,
            requested_type=visual.asset_type,
            start_sample=visual.start_sample,
            end_sample=visual.end_sample,
            duration_s=visual.duration_s,
            character_ids=personajes,
            effective_prompt=prompt,
            prompt_chars=len(prompt),
            image_size=tamano_imagen,
            image_quality=settings.media_image_quality,
            image_format=settings.media_image_format,
            needs_video=necesita_video,
            video_duration_s=duracion_video,
            video_ratio=ratio,
            motion_prompt=motion,
            seed_size=seed_size,
        )
        planes.append(plan_escena)

    # --- Presupuestos -------------------------------------------------------
    if len(escenas_video) > settings.media_max_video_scenes:
        problema(
            "exceso_de_escenas_video",
            f"El guion pide {len(escenas_video)} escenas de video y el limite del trabajo es "
            f"{settings.media_max_video_scenes}.",
            blocking=True,
        )
    segundos = sum(plan.video_duration_s or 0 for plan in planes if plan.needs_video)
    if segundos > settings.media_max_video_seconds:
        problema(
            "exceso_de_segundos_video",
            f"Los clips sumarian {segundos} s reservados y el limite es "
            f"{settings.media_max_video_seconds} s.",
            blocking=True,
        )

    # --- Herramientas y credenciales ---------------------------------------
    faltan_herramientas: list[str] = []
    if escenas_video:
        for nombre, ruta in (("ffmpeg", settings.ffmpeg_path), ("ffprobe", settings.ffprobe_path)):
            if not tool_available(ruta):
                faltan_herramientas.append(nombre)
        if faltan_herramientas:
            problema(
                "ejecutables_ausentes",
                "El plan contiene video y faltan: " + ", ".join(faltan_herramientas)
                + ". Un trabajo solo de imagenes no los necesita.",
                blocking=True,
            )

    faltan_credenciales: list[str] = []
    if not simulation:
        if settings.openai_api_key is None or not settings.openai_api_key.get_secret_value().strip():
            faltan_credenciales.append("OPENAI_API_KEY")
        if not settings.openai_image_model:
            faltan_credenciales.append("OPENAI_IMAGE_MODEL")
        if escenas_video:
            if (
                settings.runwayml_api_secret is None
                or not settings.runwayml_api_secret.get_secret_value().strip()
            ):
                faltan_credenciales.append("RUNWAYML_API_SECRET")
            if not settings.runway_model:
                faltan_credenciales.append("RUNWAY_MODEL")

    # --- Referencias --------------------------------------------------------
    plan_referencias: dict[str, str] = {}
    for character_id in selection.character_ids:
        if character_id in selection.imported:
            plan_referencias[character_id] = "imported"
            continue
        existente = (
            media_storage.get_reference(selection.set_id, character_id, simulation)
            if media_storage is not None
            else None
        )
        plan_referencias[character_id] = "reusable" if existente else "to_generate"

    # --- Cache --------------------------------------------------------------
    if media_storage is not None:
        referencias_hash = _reference_hashes(selection, media_storage, simulation)
        for plan in planes:
            plan.image_cache_key = image_cache_key(
                plan=plan,
                model=image_model,
                reference_hashes=[
                    referencias_hash[cid]
                    for cid in plan.character_ids
                    if cid in referencias_hash
                ],
                simulation=simulation,
            )
            plan.image_cache_hit = media_storage.get_cached_asset(plan.image_cache_key) is not None

    # --- Disco --------------------------------------------------------------
    libres = free_mb(data_dir)
    if libres < settings.min_free_disk_mb:
        problema(
            "disco_insuficiente",
            f"Quedan {libres:.0f} MB libres y se exigen {settings.min_free_disk_mb} MB.",
            blocking=True,
        )

    return MediaPlan(
        scenes=planes,
        issues=issues,
        reference_plan=plan_referencias,
        missing_credentials=faltan_credenciales,
        missing_tools=faltan_herramientas,
        image_model=image_model,
        video_model=video_model if escenas_video else None,
        target_width=objetivo_ancho,
        target_height=objetivo_alto,
        target_fps=objetivo_fps,
        free_disk_mb=libres,
        budget={
            "max_generation_attempts": settings.media_max_generation_attempts,
            "max_video_scenes": settings.media_max_video_scenes,
            "max_video_seconds": settings.media_max_video_seconds,
            "max_status_requests": settings.media_max_status_requests,
            "max_download_attempts": settings.media_max_download_attempts,
            "max_job_mib": settings.media_max_job_mib,
        },
    )


def _reference_hashes(selection, media_storage, simulation: bool) -> dict[str, str]:
    resultado: dict[str, str] = {}
    for character_id in selection.character_ids:
        fila = media_storage.get_reference(selection.set_id, character_id, simulation)
        if fila:
            resultado[character_id] = fila["sha256"]
    return resultado


def image_cache_key(*, plan: ScenePlan, model: str, reference_hashes: list[str], simulation: bool) -> str:
    """Identidad del ASSET de imagen. No depende de limites administrativos."""
    return sha256_json(
        {
            "operation": "image_edit" if reference_hashes else "image_generate",
            "prompt": plan.effective_prompt,
            "prompt_version": MEDIA_PROMPT_VERSION,
            "model": model,
            "size": plan.image_size,
            "quality": plan.image_quality,
            "output_format": plan.image_format,
            "references": sorted(reference_hashes),
            "simulation": simulation,
        }
    )


def video_cache_key(
    *, init_image_sha256: str, motion_prompt: str, model: str, ratio: str,
    duration_s: int, simulation: bool,
) -> str:
    """Identidad del ASSET de video: imagen inicial, movimiento, duracion y proporcion."""
    return sha256_json(
        {
            "operation": "image_to_video",
            "init_image_sha256": init_image_sha256,
            "prompt_text": motion_prompt,
            "model": model,
            "ratio": ratio,
            "duration_s": duration_s,
            "simulation": simulation,
        }
    )


def reference_cache_key(*, prompt: str, model: str, size: str, simulation: bool) -> str:
    return sha256_json(
        {
            "operation": "character_reference",
            "prompt": prompt,
            "prompt_version": MEDIA_PROMPT_VERSION,
            "model": model,
            "size": size,
            "simulation": simulation,
        }
    )
