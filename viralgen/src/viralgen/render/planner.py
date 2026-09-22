"""Preflight del modulo 4: `render plan`. Estrictamente local, sin codificar.

Contesta, antes de gastar un segundo de CPU: que escenas hay, cuantos
fotogramas, cuantos grupos de subtitulo, cuanto espacio hace falta, que
herramientas o archivos faltan y si se puede renderizar.

Distincion que este modulo cuida:

* una fuente COMPROBADA COMO INADMISIBLE es un veredicto -> `blocked`;
* una comprobacion que NECESITABA un ejecutable ausente queda NO VERIFICADA
  -> `can_render=false`, pero el plan aritmetico sigue siendo valido y no
  acredita la integridad pendiente de sus entradas.

Nunca se confunden las dos cosas.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..diskutil import free_mb
from ..media.schemas import MediaManifest
from ..profiles import get_profile
from ..schemas.document import ScriptDocument
from ..voice.schemas import VoiceManifest
from .audio import WORK_SAMPLE_RATE_HZ, plan_resample
from .captions import CAPTION_STYLES, CaptionWord, build_events, group_words
from .fonts import FontAsset, load_font, require_glyphs
from .ffmpeg import ToolCapabilities, probe_capabilities
from .timeline import RenderTimeline, build_render_timeline
from .video import EncodeSettings, choose_camera_move

#: Version del formato del plan. Entra en su hash.
PLAN_VERSION = "v1"


@dataclass
class PlanIssue:
    code: str
    message: str
    severity: str = "error"
    blocking: bool = True
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
    asset_type: str
    source_path: Path
    frames: int
    start_frame: int
    end_frame: int
    geometry_policy: str
    geometry_applied: bool
    pad_color: str
    camera_move: str
    clip_from_s: float | None
    clip_to_s: float | None
    source_width: int
    source_height: int

    def to_dict(self) -> dict:
        return {
            "scene_id": self.scene_id,
            "order": self.order,
            "asset_type": self.asset_type,
            "frames": self.frames,
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
            "geometry_policy": self.geometry_policy,
            "geometry_already_applied": self.geometry_applied,
            "camera_move": self.camera_move,
            "clip_from_s": self.clip_from_s,
            "clip_to_s": self.clip_to_s,
            "source": f"{self.source_width}x{self.source_height}",
        }


@dataclass
class RenderPlan:
    """Plan completo. `can_render` es lo que decide si merece la pena seguir."""

    render_mode: str
    simulation: bool
    job_id: str
    voice_run_id: str
    media_run_id: str
    script_sha256: str
    voice_sha256: str
    media_sha256: str
    profile_id: str
    caption_style: str
    timeline: RenderTimeline
    scenes: list[ScenePlan]
    font: FontAsset | None
    group_count: int
    event_count: int
    word_count: int
    collapsed_highlights: list[str]
    capabilities: ToolCapabilities
    encode: EncodeSettings
    estimated_work_mib: float
    free_disk_mb: float
    issues: list[PlanIssue] = field(default_factory=list)
    missing_tools: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)
    admission: dict[str, Any] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        """Hay un veredicto de inadmisibilidad o un defecto comprobado."""
        return any(issue.blocking for issue in self.issues)

    @property
    def can_render(self) -> bool:
        return not self.blocked and not self.missing_tools

    def fingerprint_payload(self) -> dict:
        """Identidad de la SOLICITUD. Cambiarla provoca conflicto de clave."""
        return {
            "plan_version": PLAN_VERSION,
            "render_mode": self.render_mode,
            "script_sha256": self.script_sha256,
            "voice_sha256": self.voice_sha256,
            "media_sha256": self.media_sha256,
            "target": {
                "width": self.encode.width,
                "height": self.encode.height,
                "fps": self.encode.fps,
            },
            "encode": self.encode.describe(),
            "caption_style": self.caption_style,
            "font_sha256": self.font.sha256 if self.font else None,
            "scenes": [escena.to_dict() for escena in self.scenes],
        }

    def plan_sha256(self) -> str:
        crudo = json.dumps(
            self.fingerprint_payload(), sort_keys=True, ensure_ascii=False
        )
        return hashlib.sha256(crudo.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        return {
            "plan_version": PLAN_VERSION,
            "render_mode": self.render_mode,
            "simulation": self.simulation,
            "job_id": self.job_id,
            "voice_run_id": self.voice_run_id,
            "media_run_id": self.media_run_id,
            "script_sha256": self.script_sha256,
            "voice_sha256": self.voice_sha256,
            "media_sha256": self.media_sha256,
            "profile_id": self.profile_id,
            "caption_style": self.caption_style,
            "target": {
                "width": self.encode.width,
                "height": self.encode.height,
                "fps": self.encode.fps,
            },
            "timeline": self.timeline.describe(),
            "scenes": [escena.to_dict() for escena in self.scenes],
            "captions": {
                "word_count": self.word_count,
                "group_count": self.group_count,
                "event_count": self.event_count,
                "collapsed_highlights": list(self.collapsed_highlights),
                "font": self.font.describe() if self.font else None,
            },
            "audio": {
                "work_sample_rate_hz": WORK_SAMPLE_RATE_HZ,
                "resample": plan_resample(
                    source_rate_hz=self.timeline.sample_rate_hz,
                    source_samples=self.timeline.sample_count,
                ).describe(),
            },
            "tools": self.capabilities.describe(),
            "encode": self.encode.describe(),
            "estimated_work_mib": round(self.estimated_work_mib, 2),
            "free_disk_mb": round(self.free_disk_mb, 1),
            "issues": [issue.to_dict() for issue in self.issues],
            "missing_tools": list(self.missing_tools),
            "unverified_checks": list(self.unverified),
            "admission": dict(self.admission),
            "blocked": self.blocked,
            "can_render": self.can_render,
            "plan_sha256": self.plan_sha256(),
        }


def build_plan(
    *,
    document: ScriptDocument,
    voice: VoiceManifest,
    media: MediaManifest,
    media_dir: Path,
    script_sha256: str,
    voice_sha256: str,
    media_sha256: str,
    settings: Any,
    render_mode: str,
    admission: dict[str, Any],
    data_dir: Path,
) -> RenderPlan:
    """Construye el plan. NO ejecuta FFmpeg y NO escribe nada."""
    incidencias: list[PlanIssue] = []
    no_verificado: list[str] = []

    perfil = get_profile(document.profile_id, settings.profiles_path)
    estilo_id = settings.render_caption_style_override or perfil.caption_style.value
    if estilo_id not in CAPTION_STYLES:
        incidencias.append(
            PlanIssue(
                "estilo_de_subtitulo_desconocido",
                f"el perfil pide el estilo {estilo_id!r} y no esta definido",
            )
        )
        estilo_id = "calm_readable"
    estilo = CAPTION_STYLES[estilo_id]

    ajustes = EncodeSettings(
        preset=settings.render_preset,
        crf=settings.render_crf,
        fps=settings.render_fps,
        width=settings.render_width,
        height=settings.render_height,
        audio_bitrate=settings.render_audio_bitrate,
    )

    # El objetivo del contrato de medios manda: no se sobrescribe en silencio.
    objetivo_medios = (
        media.timeline.target_width,
        media.timeline.target_height,
        media.timeline.target_fps,
    )
    if objetivo_medios != (ajustes.width, ajustes.height, ajustes.fps):
        incidencias.append(
            PlanIssue(
                "objetivo_incompatible",
                f"media.json declara {objetivo_medios[0]}x{objetivo_medios[1]} a "
                f"{objetivo_medios[2]} fps y el montaje esta configurado a "
                f"{ajustes.width}x{ajustes.height} a {ajustes.fps} fps. No se "
                "sobrescribe un objetivo distinto en silencio.",
            )
        )

    # --- Reloj --------------------------------------------------------------
    try:
        linea = build_render_timeline(
            scene_bounds=[
                (escena.scene_id, escena.order, escena.start_sample, escena.end_sample)
                for escena in voice.scenes
            ],
            sample_rate_hz=voice.master.sample_rate_hz,
            sample_count=voice.master.sample_count,
            fps=ajustes.fps,
        )
    except Exception as exc:
        incidencias.append(PlanIssue("reloj_invalido", str(exc)[:300]))
        linea = _empty_timeline(voice, ajustes.fps)

    if linea.narration_duration_s > settings.render_max_duration_s:
        incidencias.append(
            PlanIssue(
                "duracion_excesiva",
                f"la narracion dura {linea.narration_duration_s:.1f} s y el limite "
                f"del montaje es {settings.render_max_duration_s} s",
            )
        )
    if not (perfil.min_duration_s <= linea.narration_duration_s <= perfil.max_duration_s):
        incidencias.append(
            PlanIssue(
                "duracion_fuera_del_perfil",
                f"la narracion dura {linea.narration_duration_s:.2f} s y el perfil "
                f"{perfil.profile_id} admite {perfil.min_duration_s}-"
                f"{perfil.max_duration_s} s",
                severity="warning",
                blocking=False,
            )
        )

    # --- Escenas ------------------------------------------------------------
    por_id = {asset.asset_id: asset for asset in media.assets}
    entradas = {entrada.scene_id: entrada for entrada in media.scenes}
    escenas: list[ScenePlan] = []
    for cuadro in linea.scenes:
        entrada = entradas.get(cuadro.scene_id)
        if entrada is None:
            incidencias.append(
                PlanIssue(
                    "escena_sin_medios",
                    f"{cuadro.scene_id} no aparece en media.json",
                    scene_id=cuadro.scene_id,
                )
            )
            continue
        asset = por_id.get(entrada.primary_asset_id)
        if asset is None:
            incidencias.append(
                PlanIssue(
                    "asset_inexistente",
                    f"{cuadro.scene_id}: primary_asset_id {entrada.primary_asset_id} "
                    "no esta en la lista de assets",
                    scene_id=cuadro.scene_id,
                )
            )
            continue
        ruta = (media_dir / asset.path).resolve()
        if not _inside(media_dir, ruta) or not ruta.is_file():
            incidencias.append(
                PlanIssue(
                    "asset_ausente",
                    f"{cuadro.scene_id}: el archivo {asset.path} no existe o queda "
                    "fuera del paquete",
                    scene_id=cuadro.scene_id,
                )
            )
            continue

        politica = entrada.presentation.policy.value
        ya_aplicada = bool(asset.transformation is not None)
        movimiento = choose_camera_move(
            caption_style=estilo_id,
            policy=politica,
            asset_type=entrada.resolved_type,
        )
        escenas.append(
            ScenePlan(
                scene_id=cuadro.scene_id,
                order=cuadro.order,
                asset_type=entrada.resolved_type,
                source_path=ruta,
                frames=cuadro.frames,
                start_frame=cuadro.start_frame,
                end_frame=cuadro.end_frame,
                geometry_policy=politica,
                geometry_applied=ya_aplicada,
                pad_color=entrada.presentation.pad_color,
                camera_move=movimiento,
                clip_from_s=entrada.clip_trim.play_from_s if entrada.clip_trim else None,
                clip_to_s=entrada.clip_trim.play_to_s if entrada.clip_trim else None,
                source_width=asset.width,
                source_height=asset.height,
            )
        )
        # Un clip debe cubrir su escena ya desde el modulo 3. Aqui solo se
        # comprueba de nuevo: el cierre de fotograma (<1/F) no rellena nada.
        if entrada.resolved_type == "video" and asset.video is not None:
            disponible = asset.video.measured_duration_s
            if entrada.clip_trim is not None:
                disponible = entrada.clip_trim.play_to_s - entrada.clip_trim.play_from_s
            if disponible + 1e-6 < cuadro.frames / ajustes.fps:
                incidencias.append(
                    PlanIssue(
                        "clip_demasiado_corto",
                        f"{cuadro.scene_id}: el clip aporta {disponible:.3f} s y el "
                        f"segmento necesita {cuadro.frames / ajustes.fps:.3f} s. El "
                        "cierre de fotograma no sirve para rellenar un clip corto.",
                        scene_id=cuadro.scene_id,
                    )
                )

    # --- Subtitulos ---------------------------------------------------------
    fuente: FontAsset | None = None
    grupos = eventos = 0
    colapsadas: list[str] = []
    palabras = [
        CaptionWord(
            scene_id=palabra.scene_id,
            word_index=palabra.word_index,
            text=palabra.text,
            start_s=palabra.start_s,
            end_s=palabra.end_s,
            emphasis=palabra.emphasis,
        )
        for palabra in voice.words
    ]
    try:
        fuente = load_font(settings.render_font_path)
        require_glyphs(fuente, [palabra.text for palabra in palabras])
        conjunto = group_words(palabras, font=fuente, style=estilo)
        lista_eventos = build_events(
            conjunto, style=estilo, total_duration_s=linea.narration_duration_s
        )
        grupos, eventos = len(conjunto), len(lista_eventos)
        colapsadas = [
            f"{grupo.scene_id}#{grupo.words[indice].word_index}"
            for grupo in conjunto
            for indice in grupo.collapsed
        ]
    except Exception as exc:
        incidencias.append(PlanIssue("subtitulos_invalidos", str(exc)[:300]))

    if not palabras:
        incidencias.append(
            PlanIssue(
                "sin_alineacion",
                "voice.json no trae palabras alineadas: no hay subtitulos que poner",
                severity="warning",
                blocking=False,
            )
        )

    # --- Herramientas -------------------------------------------------------
    capacidades = probe_capabilities(settings.ffmpeg_path, settings.ffprobe_path)
    faltan = list(capacidades.missing)
    if faltan:
        no_verificado.append(
            "integridad del MP4 de salida (hace falta ffmpeg/ffprobe)"
        )
        for nota in admission.get("unverified", []):
            no_verificado.append(str(nota))

    # --- Espacio ------------------------------------------------------------
    estimado = _estimate_work_mib(linea, ajustes)
    libre = free_mb(data_dir)
    if libre < settings.min_free_disk_mb:
        incidencias.append(
            PlanIssue(
                "disco_insuficiente",
                f"quedan {libre:.0f} MB libres y el minimo es "
                f"{settings.min_free_disk_mb} MB",
            )
        )
    if estimado > settings.render_max_work_mib:
        incidencias.append(
            PlanIssue(
                "presupuesto_de_trabajo_excedido",
                f"el trabajo necesitaria unos {estimado:.0f} MiB y el limite es "
                f"{settings.render_max_work_mib} MiB",
            )
        )

    return RenderPlan(
        render_mode=render_mode,
        simulation=bool(
            document.simulation or voice.simulation or media.simulation
        ),
        job_id=document.job_id,
        voice_run_id=voice.voice_run_id,
        media_run_id=media.media_run_id,
        script_sha256=script_sha256,
        voice_sha256=voice_sha256,
        media_sha256=media_sha256,
        profile_id=perfil.profile_id,
        caption_style=estilo_id,
        timeline=linea,
        scenes=escenas,
        font=fuente,
        group_count=grupos,
        event_count=eventos,
        word_count=len(palabras),
        collapsed_highlights=colapsadas,
        capabilities=capacidades,
        encode=ajustes,
        estimated_work_mib=estimado,
        free_disk_mb=libre,
        issues=incidencias,
        missing_tools=faltan,
        unverified=no_verificado,
        admission=admission,
    )


def _estimate_work_mib(timeline: RenderTimeline, encode: EncodeSettings) -> float:
    """Pico SIMULTANEO estimado: segmentos + mezcla + candidato + temporales.

    Es una estimacion para presupuestar, no una promesa: CRF no fija tamano.
    El crecimiento real se vigila durante la ejecucion.
    """
    segundos = timeline.visual_duration_s
    # Tasa observada de libx264 a CRF 21 en 1080x1920: se toma un techo
    # holgado para presupuestar, no para prometer.
    video_mib = segundos * (6.0 / 8)          # ~6 Mb/s -> MiB/s
    mezcla_mib = segundos * WORK_SAMPLE_RATE_HZ * 2 * 4 / (1024 * 1024)  # f32 estereo
    # Los segmentos y el archivo final coexisten hasta la limpieza.
    return video_mib * 2 + mezcla_mib + 32


def _empty_timeline(voice: VoiceManifest, fps: int) -> RenderTimeline:
    """Reloj degradado cuando la cuantizacion falla: permite seguir el plan."""
    return RenderTimeline(
        scenes=[],
        fps=fps,
        sample_rate_hz=voice.master.sample_rate_hz,
        sample_count=voice.master.sample_count,
        total_frames=max(
            1, -(-voice.master.sample_count * fps // voice.master.sample_rate_hz)
        ),
    )


def _inside(base: Path, candidate: Path) -> bool:
    base_resuelta = base.resolve()
    return base_resuelta == candidate or base_resuelta in candidate.parents
