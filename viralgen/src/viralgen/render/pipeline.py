"""Pipeline del modulo 4: de guion+voz+medios a `video.mp4` y `render.json`.

Orden de trabajo, estrictamente secuencial:

1. Cargar las tres entradas y REVALIDAR la cadena anterior entera.
2. Preflight: reloj, escenas, subtitulos, herramientas, disco.
3. Subtitulos: `captions.ass`.
4. Un segmento visual por escena, validado antes de empezar el siguiente.
5. Mezcla de audio y normalizacion de sonoridad.
6. Concatenacion + mux + `+faststart`.
7. Validacion del archivo terminado: ffprobe, cuenta real de fotogramas,
   PTS, decodificacion completa y muestreo de fotogramas.
8. Consolidacion atomica, limpieza de intermedios y `render.json`.

Ninguna transaccion SQLite queda abierta mientras FFmpeg trabaja. Ante un
error conocido se cortan las etapas posteriores y se devuelve un resumen
estructurado con `manifest_path` y `output_path` en `null`: un manifiesto
incompleto no se exporta como si fuera un producto terminado.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..diskutil import (
    atomic_write_json,
    atomic_write_text,
    directory_size_mb,
    ensure_free_space,
    free_mb,
    sha256_file,
)
from ..errors import (
    ConfigError,
    DiskSpaceError,
    ExitCode,
    IdempotencyConflictError,
    ViralgenError,
)
from ..media.schemas import MediaManifest
from ..profiles import get_profile
from ..schemas.document import ScriptDocument
from ..storage import ProcessLock, Storage, utcnow
from ..voice.schemas import VoiceManifest
from .audio import (
    AAC_FRAME_SAMPLES,
    WORK_SAMPLE_RATE_HZ,
    CuePlacement,
    build_mix_filter,
    check_loudness,
    loudnorm_second_pass_filter,
    measure_loudness,
    plan_resample,
)
from .captions import (
    CAPTION_STYLES,
    PLAY_RES_X,
    PLAY_RES_Y,
    TEXT_BOTTOM,
    TEXT_LEFT,
    TEXT_RIGHT,
    TEXT_TOP,
    CaptionWord,
    build_ass_document,
    build_events,
    group_words,
)
from .ffmpeg import ProcessRunner, RenderStageError, require_tools
from .planner import PLAN_VERSION, RenderPlan, build_plan
from .probe import (
    analyze_cfr,
    decode_audio_pcm_samples,
    decode_check,
    probe_file,
    read_video_pts,
)
from .schemas import (
    AudioStreamInfo,
    CueUsage,
    FontInfo,
    FrameSample,
    LoudnessInfo,
    RenderAudio,
    RenderCaptions,
    RenderControl,
    RenderInspection,
    RenderIssue,
    RenderManifest,
    RenderOutput,
    RenderProcessing,
    RenderResources,
    RenderSources,
    RenderTimelineInfo,
    ResampleInfo,
    SceneBoundary,
    SegmentInfo,
    ToolInfo,
    VideoStreamInfo,
)
from .storage import RenderStorage
from .video import (
    VIDEO_TIMESCALE,
    GeometryPlan,
    SegmentSpec,
    mux_args,
    segment_args,
    write_concat_list,
)
from . import RENDER_PIPELINE_VERSION

logger = logging.getLogger(__name__)

#: Marca visible que lleva TODA salida preview.
PREVIEW_MARK = "PREVIEW"
PREVIEW_SIMULATION_MARK = "PREVIEW · SIMULACIÓN"

#: Version del estilo de subtitulos. Entra en el fingerprint.
CAPTION_STYLE_VERSION = "v1"

#: El nombre del archivo dice lo que es. Un `preview.mp4` no se sube por
#: descuido creyendo que es el video final, y el manifiesto queda coherente
#: sin tener que renombrar nada a mano.
OUTPUT_NAMES = {"production": "video.mp4", "preview": "preview.mp4"}


class RenderInputError(ViralgenError):
    """Las entradas no admiten montaje."""

    exit_code = ExitCode.VALIDATION
    code = "render_input_error"


@dataclass
class RenderJobRequest:
    script_path: Path
    voice_path: Path
    media_path: Path
    render_key: str | None = None
    preview: bool = False
    seed: int | None = None

    @property
    def render_mode(self) -> str:
        return "preview" if self.preview else "production"


@dataclass
class RenderOutcome:
    render_run_id: str
    job_id: str
    voice_run_id: str
    media_run_id: str
    status: str
    exit_code: ExitCode
    render_mode: str
    simulation: bool
    manifest_path: str | None = None
    output_path: str | None = None
    captions_path: str | None = None
    pending_stages: list[str] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    measured: dict[str, Any] = field(default_factory=dict)
    resources: dict[str, Any] = field(default_factory=dict)
    renders_new: int = 0
    renders_total: int = 0
    reused: bool = False
    partial: bool = False
    admissible_for_publisher: bool = False
    admission_reasons: list[str] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None

    def summary(self) -> dict:
        datos = {
            "render_run_id": self.render_run_id,
            "job_id": self.job_id,
            "voice_run_id": self.voice_run_id,
            "media_run_id": self.media_run_id,
            "render_status": self.status,
            "render_mode": self.render_mode,
            "simulation": self.simulation,
            "manifest_path": self.manifest_path,
            "output_path": self.output_path,
            "captions_path": self.captions_path,
            "partial": self.partial,
            "reused": self.reused,
            "pending_stages": list(self.pending_stages),
            "renders_new": self.renders_new,
            "renders_total": self.renders_total,
            "measured": dict(self.measured),
            "resources": dict(self.resources),
            "issues": list(self.issues),
            "admissible_for_publisher": self.admissible_for_publisher,
            "admission_reasons": list(self.admission_reasons),
            "exit_code": int(self.exit_code),
        }
        if self.error_code:
            datos["error_code"] = self.error_code
            datos["error_message"] = self.error_message
        return datos


class RenderPipeline:
    """Monta un video a partir de tres documentos ya admitidos."""

    def __init__(
        self,
        settings: Any,
        request: RenderJobRequest,
        *,
        now: datetime | None = None,
        runner: ProcessRunner | None = None,
    ) -> None:
        self.settings = settings
        self.request = request
        self.now = now or utcnow()
        self.issues: list[RenderIssue] = []
        self.render_run_id = ""
        self._runner_override = runner
        self._renders_new = 0
        self._cache_hits = 0

    # -- Entrada principal ---------------------------------------------------

    def preflight(self) -> dict:
        """`render plan`: local, sin codificar y sin escribir nada."""
        document, script_sha, voz, voice_sha, medios, media_sha = self._load_inputs()
        admision = self._chain_admission(document, voz, medios)
        plan = build_plan(
            document=document,
            voice=voz,
            media=medios,
            media_dir=self.request.media_path.parent,
            script_sha256=script_sha,
            voice_sha256=voice_sha,
            media_sha256=media_sha,
            settings=self.settings,
            render_mode=self.request.render_mode,
            admission=admision,
            data_dir=self.settings.effective_data_dir(
                simulation=self._simulation(document, voz, medios)
            ),
        )
        datos = plan.to_dict()
        gate = self._gate_reasons(admision, plan)
        datos["admission"] = admision
        datos["blocked"] = plan.blocked or bool(gate)
        datos["can_render"] = (
            not datos["blocked"] and not plan.missing_tools
        )
        if gate:
            datos["issues"] = datos["issues"] + [
                {
                    "code": "admision_insuficiente",
                    "message": motivo,
                    "severity": "error",
                    "blocking": True,
                    "scene_id": None,
                }
                for motivo in gate
            ]
        datos["exit_code"] = int(
            ExitCode.OK if datos["can_render"] else ExitCode.NEEDS_REVIEW
        )
        return datos

    def run(self) -> RenderOutcome:
        inicio = time.monotonic()
        document, script_sha, voz, voice_sha, medios, media_sha = self._load_inputs()
        self.job_id = document.job_id
        self.voice_run_id = voz.voice_run_id
        self.media_run_id = medios.media_run_id
        self.simulation = self._simulation(document, voz, medios)
        self.data_dir = self.settings.effective_data_dir(simulation=self.simulation)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        admision = self._chain_admission(document, voz, medios)
        motivos = self._gate_reasons(admision, None)
        if motivos:
            return self._blocked_outcome(motivos)

        plan = build_plan(
            document=document,
            voice=voz,
            media=medios,
            media_dir=self.request.media_path.parent,
            script_sha256=script_sha,
            voice_sha256=voice_sha,
            media_sha256=media_sha,
            settings=self.settings,
            render_mode=self.request.render_mode,
            admission=admision,
            data_dir=self.data_dir,
        )
        motivos_plan = self._gate_reasons(admision, plan)
        if motivos_plan:
            return self._blocked_outcome(motivos_plan)

        # Herramientas: se exigen ANTES de crear nada.
        capacidades = require_tools(self.settings.ffmpeg_path, self.settings.ffprobe_path)

        with Storage(self.data_dir) as almacen, ProcessLock(
            self.data_dir / "render.lock"
        ):
            self.storage = almacen
            self.render_storage = RenderStorage(almacen)
            self.render_storage.migrate()

            huella = self._fingerprint(plan)
            reutilizado = self._resume_or_create(plan, huella, script_sha, voice_sha, media_sha)
            if reutilizado is not None:
                return reutilizado

            libre_antes = free_mb(self.data_dir)
            try:
                return self._render(
                    document=document,
                    voz=voz,
                    medios=medios,
                    plan=plan,
                    capabilities=capacidades,
                    script_sha=script_sha,
                    voice_sha=voice_sha,
                    media_sha=media_sha,
                    started=inicio,
                    free_before=libre_antes,
                )
            except (RenderStageError, ConfigError, DiskSpaceError) as exc:
                self.render_storage.update_run(
                    self.render_run_id,
                    status="failed",
                    error_code=getattr(exc, "code", "render_error"),
                    error_message=str(exc)[:500],
                )
                self._issue(
                    getattr(exc, "code", "render_error"),
                    str(exc)[:300],
                    blocking=True,
                )
                return self._partial_outcome(exc)

    # -- Entradas y admision -------------------------------------------------

    def _load_inputs(self):
        def leer(ruta: Path, etiqueta: str) -> tuple[dict, str]:
            if not ruta.is_file():
                raise RenderInputError(
                    f"no existe el {etiqueta}: {ruta}", details={"path": str(ruta)}
                )
            crudo = ruta.read_text(encoding="utf-8")
            try:
                return json.loads(crudo), sha256_file(ruta)
            except json.JSONDecodeError as exc:
                raise RenderInputError(
                    f"el {etiqueta} no es JSON valido: {exc}", details={"path": str(ruta)}
                ) from exc

        datos_guion, script_sha = leer(self.request.script_path, "guion")
        datos_voz, voice_sha = leer(self.request.voice_path, "manifiesto de voz")
        datos_medios, media_sha = leer(self.request.media_path, "manifiesto de medios")
        try:
            document = ScriptDocument.model_validate(datos_guion)
            voz = VoiceManifest.model_validate(datos_voz)
            medios = MediaManifest.model_validate(datos_medios)
        except Exception as exc:
            raise RenderInputError(
                f"una de las entradas no cumple su contrato: {str(exc)[:300]}"
            ) from exc
        return document, script_sha, voz, voice_sha, medios, media_sha

    @staticmethod
    def _simulation(
        document: ScriptDocument, voz: VoiceManifest, medios: MediaManifest
    ) -> bool:
        """Se DERIVA de las fuentes. Ninguna bandera lo cambia."""
        return bool(document.simulation or voz.simulation or medios.simulation)

    def _chain_admission(
        self, document: ScriptDocument, voz: VoiceManifest, medios: MediaManifest
    ) -> dict:
        """Revalida guion+voz+medios con el validador REAL del modulo 3."""
        from ..media.admission import check_media_admission

        informe = check_media_admission(
            script_path=self.request.script_path,
            voice_path=self.request.voice_path,
            manifest_path=self.request.media_path,
            settings=self.settings,
        )
        datos = informe.to_dict()
        datos["unverified"] = [
            motivo
            for nombre, motivo in informe.failures
            if nombre == "medios_verificables"
        ]
        return datos

    def _gate_reasons(self, admision: dict, plan: RenderPlan | None) -> list[str]:
        """Que impide montar. `--preview` solo relaja el ORIGEN."""
        motivos: list[str] = []
        if not admision.get("contract_valid"):
            motivos.append(
                "la cadena guion/voz/medios no cumple su contrato: "
                + "; ".join(admision.get("reasons", []))[:300]
            )
            return motivos
        if not admision.get("admissible_for_preview"):
            # Estos NO son de origen: contrato, duraciones, alineacion,
            # archivos. `--preview` no los relaja.
            motivos.append(
                "la cadena anterior no supera las comprobaciones tecnicas: "
                + "; ".join(admision.get("preview_reasons", []))[:300]
            )
        if not self.request.preview and not admision.get("admissible_for_assembly"):
            motivos.append(
                "produccion exige admision completa de la cadena: "
                + "; ".join(admision.get("reasons", []))[:300]
            )
        if plan is not None and plan.blocked:
            motivos.extend(
                issue.message for issue in plan.issues if issue.blocking
            )
        return motivos

    # -- Identidad y reanudacion --------------------------------------------

    def _fingerprint(self, plan: RenderPlan) -> str:
        """Identidad de la SOLICITUD, no de sus etapas."""
        crudo = json.dumps(
            {
                **plan.fingerprint_payload(),
                "pipeline_version": RENDER_PIPELINE_VERSION,
                "caption_style_version": CAPTION_STYLE_VERSION,
                "ffmpeg_major": plan.capabilities.version_tuple[:2],
                "mix": {
                    "ducking": self.settings.render_enable_ducking,
                    "ducking_db": self.settings.render_ducking_reduction_db,
                    "loudnorm": self.settings.render_enable_loudnorm,
                    "lufs": self.settings.render_lufs_target,
                    "tp": self.settings.render_true_peak_dbtp,
                },
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(crudo.encode("utf-8")).hexdigest()

    def _resume_or_create(
        self, plan: RenderPlan, huella: str, script_sha: str, voice_sha: str, media_sha: str
    ) -> RenderOutcome | None:
        """Reutiliza un render completo o abre uno nuevo."""
        clave = self.request.render_key or f"auto-{huella[:16]}"
        existente = self.render_storage.get_run_by_key(clave)
        if existente is not None:
            if existente["fingerprint"] != huella:
                raise IdempotencyConflictError(
                    f"la clave {clave!r} ya existe con otra solicitud: cambiaron las "
                    "fuentes, el modo, el objetivo, los codecs o los estilos.",
                    details={"render_key": clave},
                )
            self.render_run_id = existente["render_run_id"]
            if existente["status"] == "completed" and existente["manifest_json"]:
                recuperado = self._reuse_completed(existente)
                if recuperado is not None:
                    return recuperado
            return None

        self.render_run_id = str(uuid.uuid4())
        self.render_storage.create_run(
            {
                "render_run_id": self.render_run_id,
                "job_id": self.job_id,
                "voice_run_id": self.voice_run_id,
                "media_run_id": self.media_run_id,
                "render_key": clave,
                "fingerprint": huella,
                "render_mode": self.request.render_mode,
                "simulation": int(self.simulation),
                "status": "running",
                "render_status": None,
                "script_sha256": script_sha,
                "voice_sha256": voice_sha,
                "media_sha256": media_sha,
                "manifest_json": None,
                "output_path": None,
                "error_code": None,
                "error_message": None,
            }
        )
        return None

    def _reuse_completed(self, fila: dict) -> RenderOutcome | None:
        """Devuelve el render ya hecho si sus archivos siguen intactos.

        Comprobarlo con ffprobe NO cuenta como codificacion nueva.
        """
        try:
            manifiesto = RenderManifest.model_validate(json.loads(fila["manifest_json"]))
        except Exception:
            return None
        directorio = self._run_dir()
        video = directorio / manifiesto.output.path
        if not video.is_file() or sha256_file(video) != manifiesto.output.sha256:
            logger.info("el video del render %s ya no esta: se rehace", fila["render_run_id"])
            return None
        manifiesto_path = directorio / "render.json"
        if not manifiesto_path.is_file():
            atomic_write_json(manifiesto_path, manifiesto.model_dump(mode="json"))
        return self._finished_outcome(
            manifiesto,
            manifest_path=manifiesto_path,
            output_path=video,
            reused=True,
            renders_new=0,
        )

    def _run_dir(self) -> Path:
        return self.data_dir / "jobs" / self.job_id / "render" / self.render_run_id

    # -- Render --------------------------------------------------------------

    def _render(
        self,
        *,
        document: ScriptDocument,
        voz: VoiceManifest,
        medios: MediaManifest,
        plan: RenderPlan,
        capabilities,
        script_sha: str,
        voice_sha: str,
        media_sha: str,
        started: float,
        free_before: float,
    ) -> RenderOutcome:
        directorio = self._run_dir()
        trabajo = directorio / "work"
        trabajo.mkdir(parents=True, exist_ok=True)
        ensure_free_space(self.data_dir, self.settings.min_free_disk_mb, stage="render")

        ejecutor = self._runner_override or ProcessRunner(
            ffmpeg_path=self.settings.ffmpeg_path,
            ffprobe_path=self.settings.ffprobe_path,
            stage_timeout_s=self.settings.render_stage_timeout_s,
            log_max_bytes=self.settings.render_log_max_mib * 1024 * 1024,
            threads=self.settings.render_ffmpeg_threads,
            filter_threads=self.settings.render_filter_threads,
            log_dir=trabajo / "logs",
        )

        # --- Subtitulos -----------------------------------------------------
        ass_path, datos_subtitulos = self._write_captions(voz, plan, directorio)

        # --- Segmentos ------------------------------------------------------
        segmentos, info_segmentos = self._build_segments(
            plan=plan, ejecutor=ejecutor, work=trabajo, captions=ass_path
        )

        # --- Audio ----------------------------------------------------------
        mezcla, datos_audio = self._build_audio(
            voz=voz, plan=plan, ejecutor=ejecutor, work=trabajo
        )

        # --- Mux ------------------------------------------------------------
        salida = self._mux(
            plan=plan, ejecutor=ejecutor, work=trabajo,
            segments=segmentos, audio=mezcla,
            destination=directorio / OUTPUT_NAMES[self.request.render_mode],
        )

        # --- Validacion del archivo terminado -------------------------------
        reporte, problemas, muestras, pcm_samples = self._validate_output(
            salida, plan=plan, voz=voz, ejecutor=ejecutor, directorio=directorio
        )
        for codigo, mensaje in problemas:
            self._issue(codigo, mensaje, blocking=True)

        cumple_sonoridad = self._check_final_loudness(
            ejecutor, salida, datos_audio
        )

        # --- Manifiesto -----------------------------------------------------
        pico_mib = directory_size_mb(directorio)
        manifiesto = self._build_manifest(
            document=document, voz=voz, medios=medios, plan=plan,
            capabilities=capabilities, script_sha=script_sha, voice_sha=voice_sha,
            media_sha=media_sha, output=salida, report=reporte,
            captions=datos_subtitulos, audio=datos_audio,
            loudness_ok=cumple_sonoridad, segments=info_segmentos,
            samples=muestras, pcm_samples=pcm_samples,
            wall_time=time.monotonic() - started,
            peak_mib=pico_mib, free_before=free_before,
        )

        manifiesto_path = directorio / "render.json"
        atomic_write_json(manifiesto_path, manifiesto.model_dump(mode="json"))
        self.render_storage.record_export(
            render_run_id=self.render_run_id,
            job_id=self.job_id,
            path=str(manifiesto_path),
            sha256=sha256_file(manifiesto_path),
            size_bytes=manifiesto_path.stat().st_size,
            status=manifiesto.control.render_status.value,
        )
        self.render_storage.update_run(
            self.render_run_id,
            status="completed",
            render_status=manifiesto.control.render_status.value,
            manifest_json=json.dumps(manifiesto.model_dump(mode="json"), ensure_ascii=False),
            output_path=str(salida),
        )

        # La limpieza NO puede invalidar el paquete: solo borra intermedios
        # regenerables, nunca el MP4, el ASS, los fotogramas ni el manifiesto.
        self._cleanup(trabajo)

        return self._finished_outcome(
            manifiesto,
            manifest_path=manifiesto_path,
            output_path=salida,
            reused=False,
            renders_new=self._renders_new,
        )

    # -- Etapas --------------------------------------------------------------

    def _write_captions(
        self, voz: VoiceManifest, plan: RenderPlan, directorio: Path
    ) -> tuple[Path, dict]:
        estilo = CAPTION_STYLES[plan.caption_style]
        assert plan.font is not None
        palabras = [
            CaptionWord(
                scene_id=palabra.scene_id,
                word_index=palabra.word_index,
                text=palabra.text,
                start_s=palabra.start_s,
                end_s=palabra.end_s,
                emphasis=palabra.emphasis,
            )
            for palabra in voz.words
        ]
        grupos = group_words(palabras, font=plan.font, style=estilo)
        eventos = build_events(
            grupos, style=estilo, total_duration_s=plan.timeline.narration_duration_s
        )
        marca = None
        if self.request.preview:
            marca = PREVIEW_SIMULATION_MARK if self.simulation else PREVIEW_MARK
        documento = build_ass_document(
            eventos,
            font=plan.font,
            style=estilo,
            preview_mark=marca,
            total_duration_s=plan.timeline.visual_duration_s,
        )
        ruta = directorio / "captions.ass"
        atomic_write_text(ruta, documento)
        colapsadas = [
            f"{grupo.scene_id}#{grupo.words[indice].word_index}"
            for grupo in grupos
            for indice in grupo.collapsed
        ]
        for etiqueta in colapsadas:
            self._issue(
                "resaltado_colapsado",
                f"{etiqueta}: la palabra dura menos de una centesima y su resaltado "
                "no se puede representar. Su texto sigue visible en el grupo.",
                severity="info",
                blocking=False,
            )
        return ruta, {
            "word_count": len(palabras),
            "group_count": len(grupos),
            "event_count": len(eventos),
            "collapsed": colapsadas,
            "preview_mark": marca,
            "style": estilo,
        }

    def _build_segments(
        self, *, plan: RenderPlan, ejecutor: ProcessRunner, work: Path, captions: Path
    ) -> tuple[list[Path], list[SegmentInfo]]:
        """Un segmento por escena, validado antes de empezar el siguiente."""
        segmentos_dir = work / "segments"
        segmentos_dir.mkdir(parents=True, exist_ok=True)
        fuentes_dir = plan.font.path.parent if plan.font else Path("/")

        rutas: list[Path] = []
        info: list[SegmentInfo] = []
        for escena in plan.scenes:
            destino = segmentos_dir / f"{escena.order:02d}_{escena.scene_id}.mp4"
            spec = SegmentSpec(
                scene_id=escena.scene_id,
                order=escena.order,
                source_path=escena.source_path,
                asset_type=escena.asset_type,
                frames=escena.frames,
                start_frame=escena.start_frame,
                geometry=GeometryPlan(
                    policy=escena.geometry_policy,
                    source_width=escena.source_width,
                    source_height=escena.source_height,
                    target_width=plan.encode.width,
                    target_height=plan.encode.height,
                    pad_color=escena.pad_color,
                    already_applied=escena.geometry_applied,
                ),
                camera_move=escena.camera_move,
                clip_from_s=escena.clip_from_s,
                clip_to_s=escena.clip_to_s,
                captions_path=captions,
                fonts_dir=fuentes_dir,
            )
            identidad = self._stage_identity("segment", spec.identity(), plan)
            reutilizado = self._reuse_artifact(identidad, destino)
            if reutilizado:
                self._cache_hits += 1
                info.append(
                    self._segment_info(escena, destino, reused=True)
                )
                rutas.append(destino)
                continue

            self._run_stage(
                ejecutor,
                segment_args(
                    spec, runner=ejecutor, settings=plan.encode, destination=destino
                ),
                stage=f"segment_{escena.scene_id}",
                watch_path=destino,
                watch_max_bytes=self.settings.render_max_output_mib * 1024 * 1024,
            )
            self._verify_segment(destino, escena.frames, plan)
            self._store_artifact(identidad, destino, kind="segment")
            info.append(self._segment_info(escena, destino, reused=False))
            rutas.append(destino)
            ensure_free_space(self.data_dir, self.settings.min_free_disk_mb, stage="segments")
            self._check_work_budget(work)
        return rutas, info

    def _verify_segment(self, path: Path, frames: int, plan: RenderPlan) -> None:
        """Valida un segmento ANTES de pasar al siguiente."""
        reporte = probe_file(path, ffprobe_path=self.settings.ffprobe_path)
        if reporte.video is None:
            raise RenderStageError(f"el segmento {path.name} no tiene video")
        if reporte.video.nb_read_frames != frames:
            raise RenderStageError(
                f"el segmento {path.name} tiene {reporte.video.nb_read_frames} "
                f"fotogramas y deberia tener {frames}",
                details={"segment": path.name, "expected": frames},
            )
        if (reporte.video.width, reporte.video.height) != (
            plan.encode.width, plan.encode.height
        ):
            raise RenderStageError(
                f"el segmento {path.name} no tiene el tamano objetivo"
            )

    def _segment_info(self, escena, path: Path, *, reused: bool) -> SegmentInfo:
        return SegmentInfo(
            scene_id=escena.scene_id,
            order=escena.order,
            frames=escena.frames,
            sha256=sha256_file(path),
            size_bytes=path.stat().st_size,
            retained=bool(self.settings.render_keep_segments),
            reused=reused,
        )

    def _build_audio(
        self, *, voz: VoiceManifest, plan: RenderPlan, ejecutor: ProcessRunner, work: Path
    ) -> tuple[Path, dict]:
        """Narracion + cues verificados, normalizado en dos pasadas."""
        base = self.request.voice_path.parent
        narracion = (base / voz.master.path).resolve()
        if not narracion.is_file():
            raise RenderInputError(
                f"no existe la narracion {voz.master.path} del manifiesto de voz"
            )
        if sha256_file(narracion) != voz.master.sha256:
            raise RenderInputError(
                "el hash de narration.wav no coincide con el manifiesto de voz"
            )

        cues = self._resolve_cues(voz, base)
        conversion = plan_resample(
            source_rate_hz=voz.master.sample_rate_hz,
            source_samples=voz.master.sample_count,
        )

        grafo, etiqueta = build_mix_filter(
            cues=cues,
            narration_rate_hz=voz.master.sample_rate_hz,
            ducking_enabled=self.settings.render_enable_ducking,
            ducking_reduction_db=self.settings.render_ducking_reduction_db,
            fade_s=self.settings.render_cue_fade_s,
        )
        mezcla = work / "mix.wav"
        args = [
            *ejecutor.base_args(),
            "-i", str(narracion),
            *[arg for cue in cues for arg in ("-i", str(cue.path))],
            "-filter_complex", grafo,
            "-map", etiqueta,
            # La narracion NO se acorta ni se alarga: la mezcla dura lo que la voz.
            "-c:a", "pcm_s16le",
            "-ar", str(WORK_SAMPLE_RATE_HZ),
            "-ac", "2",
            str(mezcla),
        ]
        self._run_stage(ejecutor, args, stage="audio_mix")

        normalizacion: dict[str, Any] = {"enabled": False}
        medida_previa = None
        if self.settings.render_enable_loudnorm:
            medida_previa = measure_loudness(ejecutor, mezcla)
            if medida_previa.usable:
                normalizado = work / "mix_norm.wav"
                self._run_stage(
                    ejecutor,
                    [
                        *ejecutor.base_args(),
                        "-i", str(mezcla),
                        "-af", loudnorm_second_pass_filter(medida_previa),
                        "-c:a", "pcm_s16le",
                        "-ar", str(WORK_SAMPLE_RATE_HZ),
                        "-ac", "2",
                        str(normalizado),
                    ],
                    stage="audio_loudnorm",
                )
                mezcla = normalizado
                normalizacion = {
                    "enabled": True,
                    "mode": "two_pass_linear",
                    "target_lufs": self.settings.render_lufs_target,
                    "true_peak_ceiling_dbtp": self.settings.render_true_peak_dbtp,
                    "measured_input_lufs": medida_previa.integrated_lufs,
                    "output_sample_rate_hz": WORK_SAMPLE_RATE_HZ,
                }
            else:
                normalizacion = {
                    "enabled": False,
                    "reason": medida_previa.note,
                }
                self._issue(
                    "sonoridad_no_medible",
                    f"no se normalizo: {medida_previa.note}",
                    severity="warning",
                    blocking=False,
                )

        reporte = probe_file(
            mezcla, ffprobe_path=self.settings.ffprobe_path, count_frames=False
        )
        muestras = 0
        if reporte.audio and reporte.audio.duration_s:
            muestras = round(reporte.audio.duration_s * WORK_SAMPLE_RATE_HZ)

        return mezcla, {
            "resample": conversion,
            "cues": cues,
            "normalization": normalizacion,
            "work_sample_count": muestras or conversion.expected_samples,
            "ducking": {
                "enabled": self.settings.render_enable_ducking,
                "reduction_db": self.settings.render_ducking_reduction_db,
                "applies_to": "music",
                "note": "ganancia fija y reproducible; los efectos puntuales no se atenuan",
            },
        }

    def _resolve_cues(self, voz: VoiceManifest, base: Path) -> list[CuePlacement]:
        """Solo cues YA RESUELTOS y verificables. Nada se descarga."""
        por_id = {asset.asset_id: asset for asset in voz.sound.assets}
        colocados: list[CuePlacement] = []
        for cue in voz.sound.cues:
            asset = por_id.get(cue.asset_id)
            if asset is None:
                # El modulo 2 dejo la indicacion sin resolver: conserva su
                # aviso y NO dispara ninguna descarga.
                self._issue(
                    "cue_sin_resolver",
                    f"el cue {cue.asset_id} no tiene asset en el manifiesto de voz: "
                    "se exporta sin el",
                    severity="warning",
                    blocking=False,
                    scene_id=cue.scene_id,
                )
                continue
            ruta = (base / asset.path).resolve()
            if not ruta.is_file():
                raise RenderInputError(
                    f"el asset de sonido {asset.path}, referenciado por el manifiesto "
                    "de voz, no existe. Un archivo obligatorio que falta es un error.",
                    details={"asset_id": asset.asset_id},
                )
            if sha256_file(ruta) != asset.sha256:
                raise RenderInputError(
                    f"el asset de sonido {asset.path} no coincide con su hash",
                    details={"asset_id": asset.asset_id},
                )
            colocados.append(
                CuePlacement(
                    cue_type=cue.cue_type.value,
                    asset_id=cue.asset_id,
                    path=ruta,
                    sha256=asset.sha256,
                    start_s=cue.start_s,
                    end_s=cue.end_s,
                    gain_db=cue.gain_db,
                )
            )
        return colocados

    def _mux(
        self,
        *,
        plan: RenderPlan,
        ejecutor: ProcessRunner,
        work: Path,
        segments: list[Path],
        audio: Path,
        destination: Path,
    ) -> Path:
        lista = work / "segments" / "concat.txt"
        write_concat_list(segments, lista)
        candidato = work / "candidate.mp4"
        self._run_stage(
            ejecutor,
            mux_args(
                runner=ejecutor,
                concat_list=lista,
                audio_path=audio,
                destination=candidato,
                settings=plan.encode,
                total_frames=plan.timeline.total_frames,
            ),
            stage="mux",
            cwd=lista.parent,
            watch_path=candidato,
            watch_max_bytes=self.settings.render_max_output_mib * 1024 * 1024,
        )
        # Consolidacion atomica: el resultado valido no se sobrescribe a medias.
        destination.parent.mkdir(parents=True, exist_ok=True)
        candidato.replace(destination)
        return destination

    def _validate_output(
        self,
        salida: Path,
        *,
        plan: RenderPlan,
        voz: VoiceManifest,
        ejecutor: ProcessRunner,
        directorio: Path,
    ):
        """Mide el archivo terminado. El exito de FFmpeg no basta."""
        problemas: list[tuple[str, str]] = []
        medidas: int | None = None
        reporte = probe_file(salida, ffprobe_path=self.settings.ffprobe_path)

        if reporte.video is None:
            problemas.append(("sin_video", "el archivo exportado no tiene video"))
            return reporte, problemas, [], None
        if reporte.video.nb_read_frames != plan.timeline.total_frames:
            problemas.append((
                "fotogramas_incorrectos",
                f"el archivo tiene {reporte.video.nb_read_frames} fotogramas y el "
                f"reloj exige {plan.timeline.total_frames}",
            ))
        if reporte.audio is None:
            problemas.append(("sin_audio", "el archivo exportado no tiene audio"))
        else:
            esperadas = round(
                voz.master.sample_count
                * reporte.audio.sample_rate_hz
                / voz.master.sample_rate_hz
            )
            # Se DECODIFICA para contar. La duracion declarada por el
            # contenedor no sirve: difiere del PCM en un AAC real.
            medidas = self._contar_pcm(salida, reporte, ejecutor)
            if medidas is None:
                problemas.append((
                    "audio_no_medido",
                    "no se pudo contar el PCM del audio: sin esa medida no se "
                    "puede afirmar que la narracion este completa",
                ))
            elif medidas - esperadas < -AAC_FRAME_SAMPLES:
                problemas.append((
                    "audio_truncado",
                    f"al audio le faltan {esperadas - medidas} muestras respecto "
                    "de la narracion",
                ))

        pts = read_video_pts(salida, ffprobe_path=self.settings.ffprobe_path)
        paso = VIDEO_TIMESCALE // plan.encode.fps
        monotonos, cfr, saltos = analyze_cfr(pts, expected_step=paso)
        reporte.pts_monotonic = monotonos
        reporte.cfr = cfr
        reporte.pts_gaps = saltos
        if not monotonos:
            problemas.append(("pts_no_monotonos", "los PTS no crecen de forma monotona"))
        if not cfr:
            problemas.append((
                "cadencia_no_constante",
                f"la cadencia no es constante: {len(saltos)} salto(s) de PTS",
            ))

        decodifica, registro = decode_check(ejecutor, salida)
        if not decodifica:
            problemas.append((
                "decodificacion_fallida",
                f"el archivo no decodifica entero: {registro[:200]}",
            ))
        reporte.notes.append(
            "ambas pistas decodificadas a salida nula"
            if decodifica else "la decodificacion completa fallo"
        )

        muestras = self._sample_frames(salida, plan=plan, directorio=directorio)
        return reporte, problemas, muestras, medidas

    def _contar_pcm(self, salida, reporte, ejecutor) -> int | None:
        """Muestras PCM por canal, DECODIFICANDO. None si no se pudo medir.

        Se lee por trozos: el audio no se carga entero en memoria. El limite
        de bytes sale de la duracion maxima admitida, con margen.
        """
        if reporte.audio is None or reporte.audio.channels < 1:
            return None
        limite_bytes = (
            self.settings.render_max_duration_s
            * max(reporte.audio.sample_rate_hz, 48_000)
            * reporte.audio.channels
            * 2      # s16le
            * 2      # margen
        )
        try:
            return decode_audio_pcm_samples(
                ejecutor,
                salida,
                channels=reporte.audio.channels,
                max_bytes=limite_bytes,
                timeout_s=self.settings.render_stage_timeout_s,
            )
        except Exception as exc:
            logger.warning("no se pudo contar el PCM del audio: %s", exc)
            return None

    def _sample_frames(
        self, salida: Path, *, plan: RenderPlan, directorio: Path
    ) -> list[FrameSample]:
        """Extrae fotogramas representativos como EVIDENCIA, no como prueba.

        Gancho, grupo largo, cambio de palabra, frontera de escena y cierre.
        """
        carpeta = directorio / "frames"
        carpeta.mkdir(parents=True, exist_ok=True)
        total = plan.timeline.total_frames
        puntos: list[tuple[str, int]] = [("gancho", 1)]
        if len(plan.timeline.scenes) > 1:
            frontera = plan.timeline.scenes[1].start_frame
            puntos.append(("frontera_de_escena", max(frontera, 0)))
            puntos.append(("antes_de_la_frontera", max(frontera - 1, 0)))
        puntos.append(("medio", total // 2))
        puntos.append(("cierre", max(total - 2, 0)))

        ejecutor = self._runner_override or ProcessRunner(
            ffmpeg_path=self.settings.ffmpeg_path,
            ffprobe_path=self.settings.ffprobe_path,
            stage_timeout_s=self.settings.render_stage_timeout_s,
            threads=self.settings.render_ffmpeg_threads,
            filter_threads=self.settings.render_filter_threads,
            log_dir=directorio / "work" / "logs",
        )
        muestras: list[FrameSample] = []
        vistos: set[int] = set()
        for etiqueta, indice in puntos:
            if indice in vistos or indice >= total:
                continue
            vistos.add(indice)
            # JPEG y no PNG: son evidencia para que una PERSONA mire si el
            # texto esta integrado y si la marca aparece, no artefactos que
            # nadie compare pixel a pixel. Pesan la mitad en el paquete.
            destino = carpeta / f"{etiqueta}_{indice:05d}.jpg"
            try:
                ejecutor.run_checked(
                    [
                        *ejecutor.base_args(),
                        "-i", str(salida),
                        "-vf", f"select=eq(n\\,{indice})",
                        "-vsync", "0",
                        "-frames:v", "1",
                        "-q:v", "3",
                        str(destino),
                    ],
                    stage=f"probe_frame_{etiqueta}",
                )
            except RenderStageError:
                continue
            if destino.is_file():
                muestras.append(
                    FrameSample(
                        label=etiqueta,
                        frame_index=indice,
                        time_s=round(indice / plan.encode.fps, 6),
                        path=str(destino.relative_to(directorio)),
                        sha256=sha256_file(destino),
                    )
                )
        return muestras

    def _check_final_loudness(
        self, ejecutor: ProcessRunner, salida: Path, datos_audio: dict
    ) -> bool:
        """Mide el audio FINAL ya codificado, no la mezcla de trabajo."""
        medida = measure_loudness(ejecutor, salida, stage="probe_loudness")
        datos_audio["measured"] = medida
        problemas = check_loudness(
            medida,
            target_lufs=self.settings.render_lufs_target,
            tolerance_lu=self.settings.render_lufs_tolerance_lu,
        )
        if not self.settings.render_enable_loudnorm and not medida.usable:
            return True
        for problema in problemas:
            # Un incumplimiento conocido deja el candidato en needs_review; no
            # se abre un bucle de recodificacion buscando el numero.
            self._issue("sonoridad_fuera_de_objetivo", problema, blocking=True)
        return not problemas

    # -- Utilidades ----------------------------------------------------------

    def _run_stage(self, ejecutor: ProcessRunner, args: list[str], *, stage: str, **kwargs):
        """Ejecuta una etapa contando intentos persistidos."""
        intentos = self.render_storage.attempts(self.render_run_id, stage)
        if intentos >= self.settings.render_max_attempts_per_stage:
            raise RenderStageError(
                f"la etapa {stage} agoto sus {intentos} intentos: hace falta una "
                "resolucion explicita, no otro reintento automatico.",
                details={"stage": stage, "attempts": intentos},
            )
        inicio = time.monotonic()
        try:
            resultado = ejecutor.run_checked(args, stage=stage, **kwargs)
        except RenderStageError as exc:
            self.render_storage.record_attempt(
                render_run_id=self.render_run_id, job_id=self.job_id, stage=stage,
                attempt=intentos + 1, status="failed",
                elapsed_s=time.monotonic() - inicio,
                error_code=getattr(exc, "code", "render_stage_error"),
                error_message=str(exc),
            )
            raise
        self.render_storage.record_attempt(
            render_run_id=self.render_run_id, job_id=self.job_id, stage=stage,
            attempt=intentos + 1, status="ok", elapsed_s=resultado.elapsed_s,
        )
        if not stage.startswith("probe"):
            self._renders_new += 1
        return resultado

    def _stage_identity(self, kind: str, payload: dict, plan: RenderPlan) -> str:
        """Identidad de una ETAPA, separada de la de la ejecucion.

        Asi un segmento identico se aprovecha desde otra `--render-key`. El
        modo entra en la identidad: un segmento preview lleva su marca y NUNCA
        vale para una salida de produccion.
        """
        crudo = json.dumps(
            {
                "kind": kind,
                "pipeline_version": RENDER_PIPELINE_VERSION,
                "plan_version": PLAN_VERSION,
                "render_mode": self.request.render_mode,
                "simulation": self.simulation,
                "encode": plan.encode.describe(),
                "caption_style": plan.caption_style,
                "caption_style_version": CAPTION_STYLE_VERSION,
                "font_sha256": plan.font.sha256 if plan.font else None,
                "voice_sha256": plan.voice_sha256,
                "media_sha256": plan.media_sha256,
                "payload": payload,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(crudo.encode("utf-8")).hexdigest()

    def _reuse_artifact(self, identidad: str, destino: Path) -> bool:
        """Adopta una etapa ya validada, aunque venga de otra render-key."""
        fila = self.render_storage.get_artifact(identidad)
        if fila is None:
            return False
        origen = Path(fila["path"])
        if not origen.is_file() or sha256_file(origen) != fila["sha256"]:
            self.render_storage.drop_artifact(identidad)
            return False
        if origen != destino:
            destino.parent.mkdir(parents=True, exist_ok=True)
            try:
                if destino.exists():
                    destino.unlink()
                destino.hardlink_to(origen)
            except OSError:
                shutil.copy2(origen, destino)
        self.render_storage.touch_artifact(identidad)
        return True

    def _store_artifact(self, identidad: str, path: Path, *, kind: str) -> None:
        self.render_storage.put_artifact(
            identity_key=identidad,
            kind=kind,
            simulation=self.simulation,
            render_mode=self.request.render_mode,
            path=str(path),
            sha256=sha256_file(path),
            size_bytes=path.stat().st_size,
        )
        self.render_storage.put_stage(
            render_run_id=self.render_run_id,
            stage=f"{kind}:{path.name}",
            state="done",
            identity_key=identidad,
            path=str(path),
            sha256=sha256_file(path),
            size_bytes=path.stat().st_size,
        )

    def _check_work_budget(self, work: Path) -> None:
        usado = directory_size_mb(work)
        if usado > self.settings.render_max_work_mib:
            raise DiskSpaceError(
                f"el area de trabajo ocupa {usado:.0f} MiB y el limite es "
                f"{self.settings.render_max_work_mib} MiB",
                details={"work_mib": round(usado, 1)},
            )

    def _cleanup(self, work: Path) -> None:
        """Borra intermedios regenerables de ESTE render, nada mas.

        Nunca toca fuentes, referencias compartidas, salidas finales ni
        trabajos ajenos, y no puede invalidar el paquete: el MP4, el ASS, los
        fotogramas y el manifiesto viven fuera de `work/`.

        Ante un FALLO no se llama: las etapas validadas se conservan para poder
        reanudar sin recodificarlas.
        """
        if self.settings.render_keep_segments or not work.is_dir():
            return
        # Las filas de cache que apuntan a los archivos que se van a borrar se
        # retiran a la vez: un indice que apunta a la nada no sirve de nada y
        # obligaria a descubrirlo archivo por archivo en cada ejecucion.
        for nombre, fila in self.render_storage.stages(self.render_run_id).items():
            ruta = fila.get("path")
            if ruta and Path(ruta).is_relative_to(work):
                self.render_storage.drop_artifact(fila["identity_key"])
                logger.debug("cache retirada con el intermedio %s", nombre)
        try:
            shutil.rmtree(work, ignore_errors=True)
        except OSError as exc:  # pragma: no cover - limpieza best effort
            logger.warning("no se pudo limpiar %s: %s", work, exc)

    def _issue(
        self,
        code: str,
        message: str,
        *,
        severity: str = "error",
        blocking: bool = True,
        scene_id: str | None = None,
    ) -> None:
        self.issues.append(
            RenderIssue(
                code=code,
                message=message[:500],
                severity=severity,
                blocking=blocking,
                scene_id=scene_id,
            )
        )

    # -- Manifiesto y resultados --------------------------------------------

    def _build_manifest(self, **kw) -> RenderManifest:
        plan: RenderPlan = kw["plan"]
        reporte = kw["report"]
        salida: Path = kw["output"]
        directorio = self._run_dir()
        subtitulos = kw["captions"]
        audio = kw["audio"]
        voz: VoiceManifest = kw["voz"]
        medios: MediaManifest = kw["medios"]
        document: ScriptDocument = kw["document"]

        bloqueantes = [issue for issue in self.issues if issue.blocking]
        estado = "needs_review" if bloqueantes else "ready"
        publicable = (
            estado == "ready"
            and self.request.render_mode == "production"
            and not self.simulation
        )

        perfil = get_profile(document.profile_id, self.settings.profiles_path)
        medida = audio.get("measured")
        esperadas_audio = round(
            voz.master.sample_count * WORK_SAMPLE_RATE_HZ / voz.master.sample_rate_hz
        )
        # La cuenta viene de _validate_output, que ya decodifico el PCM. No se
        # vuelve a decodificar solo para escribir el manifiesto.
        decodificadas = kw.get("pcm_samples")

        return RenderManifest(
            render_run_id=self.render_run_id,
            job_id=self.job_id,
            voice_run_id=self.voice_run_id,
            media_run_id=self.media_run_id,
            created_at=self.now,
            render_mode=self.request.render_mode,
            simulation=self.simulation,
            sources=RenderSources(
                script_schema_version=document.schema_version,
                script_sha256=kw["script_sha"],
                script_simulation=document.simulation,
                voice_schema_version=voz.schema_version,
                voice_sha256=kw["voice_sha"],
                voice_simulation=voz.simulation,
                media_schema_version=medios.schema_version,
                media_sha256=kw["media_sha"],
                media_simulation=medios.simulation,
                profile_id=perfil.profile_id,
                channel=perfil.channel.value,
                caption_style=plan.caption_style,
            ),
            control=RenderControl(
                render_status=estado,
                issues=self.issues,
                visual_review="not_performed",
                visual_review_method="frame_sampling" if kw["samples"] else "none",
                admissible_for_publisher=publicable,
            ),
            output=RenderOutput(
                path=str(salida.relative_to(directorio)),
                sha256=sha256_file(salida),
                size_bytes=salida.stat().st_size,
                container_format=reporte.container.format_name,
                container_duration_s=reporte.container.duration_s,
                faststart=True,
                video=VideoStreamInfo(
                    codec_name=reporte.video.codec_name,
                    width=reporte.video.width,
                    height=reporte.video.height,
                    pix_fmt=reporte.video.pix_fmt,
                    sample_aspect_ratio=reporte.video.sample_aspect_ratio or "1:1",
                    display_aspect_ratio=reporte.video.display_aspect_ratio or "9:16",
                    fps_rational=reporte.video.r_frame_rate,
                    fps=reporte.video.fps,
                    frame_count=reporte.video.nb_read_frames or 0,
                    stream_duration_s=reporte.video.duration_s,
                    rotation_degrees=reporte.video.rotation,
                    has_b_frames=reporte.video.has_b_frames,
                    constant_frame_rate=reporte.cfr,
                    pts_monotonic=reporte.pts_monotonic,
                ),
                audio=AudioStreamInfo(
                    codec_name=reporte.audio.codec_name if reporte.audio else "",
                    sample_rate_hz=reporte.audio.sample_rate_hz if reporte.audio else 0,
                    channels=reporte.audio.channels if reporte.audio else 0,
                    channel_layout=reporte.audio.channel_layout if reporte.audio else "",
                    stream_duration_s=reporte.audio.duration_s if reporte.audio else None,
                    stream_duration_ts=reporte.audio.duration_ts if reporte.audio else None,
                    decoded_samples=decodificadas,
                    decoded_samples_source=(
                        "pcm_decode" if decodificadas is not None else "not_measured"
                    ),
                    initial_padding_samples=(
                        reporte.audio.initial_padding if reporte.audio else None
                    ),
                ),
                decode_check_passed=not any(
                    issue.code == "decodificacion_fallida" for issue in self.issues
                ),
            ),
            timeline=RenderTimelineInfo(
                sample_rate_hz=plan.timeline.sample_rate_hz,
                sample_count=plan.timeline.sample_count,
                narration_duration_s=plan.timeline.narration_duration_s,
                fps=plan.encode.fps,
                total_frames=plan.timeline.total_frames,
                visual_duration_s=plan.timeline.visual_duration_s,
                quantization_excess_s=plan.timeline.quantization_excess_s,
                max_frame_hold_s=1.0 / plan.encode.fps,
                scenes=[
                    SceneBoundary(
                        **cuadro.describe(),
                        asset_type=escena.asset_type,
                        camera_move=escena.camera_move,
                        geometry_policy=escena.geometry_policy,
                        geometry_already_applied=escena.geometry_applied,
                        clip_from_s=escena.clip_from_s,
                        clip_to_s=escena.clip_to_s,
                    )
                    for cuadro, escena in zip(plan.timeline.scenes, plan.scenes)
                ],
            ),
            audio=RenderAudio(
                work_sample_rate_hz=WORK_SAMPLE_RATE_HZ,
                work_sample_count=audio["work_sample_count"],
                channels=2,
                resample=ResampleInfo(**audio["resample"].describe()),
                cues_used=[CueUsage(**cue.describe()) for cue in audio["cues"]],
                ducking=audio["ducking"],
                normalization=audio["normalization"],
                measured=LoudnessInfo(**medida.describe()),
                loudness_compliant=kw["loudness_ok"],
                aac_tolerance_samples=AAC_FRAME_SAMPLES,
                audio_sample_deficit=(
                    decodificadas - esperadas_audio if decodificadas is not None else 0
                ),
                stream_vs_decoded_samples=(
                    reporte.audio.duration_ts - decodificadas
                    if reporte.audio is not None
                    and reporte.audio.duration_ts is not None
                    and decodificadas is not None
                    else None
                ),
            ),
            captions=RenderCaptions(
                path="captions.ass",
                sha256=sha256_file(directorio / "captions.ass"),
                style_id=plan.caption_style,
                style_version=CAPTION_STYLE_VERSION,
                font=FontInfo(
                    path=str(plan.font.path),
                    sha256=plan.font.sha256,
                    family=plan.font.family,
                    units_per_em=plan.font.units_per_em,
                ),
                word_count=subtitulos["word_count"],
                group_count=subtitulos["group_count"],
                event_count=subtitulos["event_count"],
                play_res_x=PLAY_RES_X,
                play_res_y=PLAY_RES_Y,
                text_region={
                    "left": TEXT_LEFT, "right": TEXT_RIGHT,
                    "top": TEXT_TOP, "bottom": TEXT_BOTTOM,
                },
                rounding_policy=(
                    "Se cuantiza cada FRONTERA en segundos a centesimas con "
                    "floor(t*100+0.5); nunca la duracion. Dos eventos contiguos "
                    "comparten el valor de origen, asi que no hay solapes ni deriva."
                ),
                collapsed_highlights=subtitulos["collapsed"],
                preview_mark=subtitulos["preview_mark"],
            ),
            processing=RenderProcessing(
                pipeline_version=RENDER_PIPELINE_VERSION,
                tools=ToolInfo(
                    ffmpeg_version=kw["capabilities"].ffmpeg_version,
                    ffprobe_version=kw["capabilities"].ffprobe_version,
                    min_tested_version=kw["capabilities"].describe()["min_tested_version"],
                    meets_min_tested_version=kw["capabilities"].describe()[
                        "meets_min_tested_version"
                    ],
                    libass=kw["capabilities"].has_libass,
                    encoders_required=list(kw["capabilities"].describe()["encoders_required"]),
                    filters_required=list(kw["capabilities"].describe()["filters_required"]),
                ),
                encode_settings=plan.encode.describe(),
                plan_sha256=plan.plan_sha256(),
                fingerprint=self._fingerprint(plan),
                segments=kw["segments"],
                concat_mode="stream_copy",
            ),
            resources=RenderResources(
                wall_time_s=round(kw["wall_time"], 3),
                peak_work_dir_mib=round(kw["peak_mib"], 2),
                free_disk_mb_before=round(kw["free_before"], 1),
                free_disk_mb_after=round(free_mb(self.data_dir), 1),
                renders_new=self._renders_new,
                renders_total=self.render_storage.encodes_total(self.job_id),
                segment_cache_hits=self._cache_hits,
                peak_memory_mib=None,
            ),
            inspection=RenderInspection(
                frames=kw["samples"],
                checks_performed=[
                    "ffprobe_streams",
                    "frame_count_decoded",
                    "pts_cfr",
                    "full_decode_null",
                    "loudness_ebu_r128",
                    "frame_sampling",
                ],
            ),
        )

    def _finished_outcome(
        self,
        manifiesto: RenderManifest,
        *,
        manifest_path: Path,
        output_path: Path,
        reused: bool,
        renders_new: int,
    ) -> RenderOutcome:
        estado = manifiesto.control.render_status.value
        codigo = ExitCode.OK if estado == "ready" else ExitCode.NEEDS_REVIEW
        return RenderOutcome(
            render_run_id=manifiesto.render_run_id,
            job_id=manifiesto.job_id,
            voice_run_id=manifiesto.voice_run_id,
            media_run_id=manifiesto.media_run_id,
            status=estado,
            exit_code=codigo,
            render_mode=manifiesto.render_mode.value,
            simulation=manifiesto.simulation,
            manifest_path=str(manifest_path),
            output_path=str(output_path),
            captions_path=str(manifest_path.parent / manifiesto.captions.path),
            issues=[issue.model_dump(mode="json") for issue in manifiesto.control.issues],
            measured={
                "frame_count": manifiesto.output.video.frame_count,
                "total_frames": manifiesto.timeline.total_frames,
                "visual_duration_s": manifiesto.timeline.visual_duration_s,
                "narration_duration_s": manifiesto.timeline.narration_duration_s,
                "quantization_excess_s": manifiesto.timeline.quantization_excess_s,
                "size_bytes": manifiesto.output.size_bytes,
                "integrated_lufs": manifiesto.audio.measured.integrated_lufs,
                "true_peak_dbtp": manifiesto.audio.measured.true_peak_dbtp,
            },
            resources=manifiesto.resources.model_dump(mode="json"),
            renders_new=renders_new,
            renders_total=manifiesto.resources.renders_total,
            reused=reused,
            admissible_for_publisher=manifiesto.control.admissible_for_publisher,
            admission_reasons=(
                []
                if manifiesto.control.admissible_for_publisher
                else _publisher_reasons(manifiesto)
            ),
        )

    def _blocked_outcome(self, motivos: list[str]) -> RenderOutcome:
        return RenderOutcome(
            render_run_id=self.render_run_id or "",
            job_id=getattr(self, "job_id", ""),
            voice_run_id=getattr(self, "voice_run_id", ""),
            media_run_id=getattr(self, "media_run_id", ""),
            status="blocked",
            exit_code=ExitCode.NEEDS_REVIEW,
            render_mode=self.request.render_mode,
            simulation=getattr(self, "simulation", False),
            manifest_path=None,
            output_path=None,
            partial=True,
            admissible_for_publisher=False,
            admission_reasons=motivos,
            renders_new=0,
            renders_total=0,
        )

    def _partial_outcome(self, exc: Exception) -> RenderOutcome:
        """Trabajo parcial: sin manifiesto y sin archivo de salida."""
        etapas = [
            nombre
            for nombre, fila in self.render_storage.stages(self.render_run_id).items()
            if fila["state"] != "done"
        ]
        return RenderOutcome(
            render_run_id=self.render_run_id,
            job_id=self.job_id,
            voice_run_id=self.voice_run_id,
            media_run_id=self.media_run_id,
            status="failed",
            exit_code=getattr(exc, "exit_code", ExitCode.VALIDATION),
            render_mode=self.request.render_mode,
            simulation=self.simulation,
            manifest_path=None,
            output_path=None,
            pending_stages=etapas,
            issues=[issue.model_dump(mode="json") for issue in self.issues],
            partial=True,
            renders_new=self._renders_new,
            renders_total=self.render_storage.encodes_total(self.job_id),
            admissible_for_publisher=False,
            admission_reasons=["el render no llego a completarse"],
            error_code=getattr(exc, "code", "render_error"),
            error_message=str(exc)[:500],
        )


def _publisher_reasons(manifiesto: RenderManifest) -> list[str]:
    motivos: list[str] = []
    if manifiesto.render_mode.value != "production":
        motivos.append(
            "render_mode=preview: una salida preview nunca se entrega al publicador"
        )
    if manifiesto.simulation:
        motivos.append("simulation=true: las fuentes son simuladas")
    if manifiesto.control.render_status.value != "ready":
        motivos.append(f"render_status={manifiesto.control.render_status.value}")
    return motivos
