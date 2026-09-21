"""Orquestacion del modulo 3: guion + voz admitidos -> assets locales + media.json.

Un unico trabajador secuencial y una sola generacion remota en curso.

Orden por escena, completando cada una antes de empezar la siguiente:
referencias necesarias -> imagen -> clip si corresponde -> descarga ->
validacion -> checkpoint.

Un bloqueo sin resolver DETIENE todas las solicitudes nuevas, incluidas las de
otras escenas y las de referencias. Lo ya obtenido se conserva. Un trabajo que
espera una tarea remota puede seguir consultando ESA tarea dentro de sus
limites, pero no empieza otras generaciones.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import Settings
from ..diskutil import (
    atomic_write_json,
    directory_size_mb,
    ensure_free_space,
    ensure_within,
    sha256_file,
)
from ..errors import ConfigError, DiskSpaceError, ExitCode, IdempotencyConflictError, ViralgenError
from ..logging_setup import get_logger
from ..profiles import get_series_bible
from ..schemas.document import ScriptDocument
from ..storage import ProcessLock, Storage, iso, utcnow
from ..textutil import sha256_json, sha256_text
from ..voice.admission import check_voice_admission
from ..voice.schemas import VoiceManifest
from . import MEDIA_PROCESSING_VERSION, MEDIA_SCHEMA_VERSION
from .capabilities import parse_ratio, parse_size
from .imaging import (
    apply_geometry,
    build_contact_sheet,
    encode_jpeg_under_limit,
    inspect_image,
    pad_color_from_palette,
    plan_geometry,
    write_bytes_as_image,
)
from .planner import (
    MediaPlan,
    build_plan,
    image_cache_key,
    reference_cache_key,
    video_cache_key,
)
from .prompts import MEDIA_PROMPT_VERSION
from .providers import (
    ImageRequest,
    MediaBudget,
    MediaBudgetExceededError,
    MediaOutcomeUnknownError,
    MediaProviderError,
    VideoRequest,
    build_image_provider,
    build_video_provider,
)
from .references import build_selection, load_reference_pack, reference_prompt
from .schemas import (
    AssetKind,
    AssetRole,
    AssetTransformation,
    ClipTrim,
    GeometryPolicy,
    IssueSeverity,
    MediaAsset,
    MediaControl,
    MediaInspection,
    MediaIssue,
    MediaManifest,
    MediaProviders,
    MediaSceneEntry,
    MediaSource,
    MediaStatus,
    MediaTimeline,
    MediaUsage,
    PresentationPlan,
    ReferenceEntry,
    ReferenceProvenance,
    ReferenceSet,
    VideoDetails,
    VisualReview,
)
from .storage import MediaStorage
from .timeline import build_visual_timeline
from .videoprobe import decode_integrity_check, probe_video, require_video_tools

logger = get_logger("media.pipeline")


class MediaInputError(ViralgenError):
    """Las entradas no estan admitidas: no se emite ninguna peticion."""

    exit_code = ExitCode.VALIDATION
    code = "media_input_not_admitted"


class MediaPlanBlocked(ViralgenError):
    """El preflight detecto un problema antes de comprar nada."""

    exit_code = ExitCode.VALIDATION
    code = "media_plan_blocked"


@dataclass
class MediaJobRequest:
    script_path: Path
    voice_path: Path
    media_key: str | None = None
    simulation: bool = False
    seed: int | None = None


@dataclass
class MediaOutcome:
    media_run_id: str
    job_id: str
    voice_run_id: str
    status: str
    exit_code: ExitCode
    simulation: bool
    manifest_path: str | None = None
    contact_sheet_path: str | None = None
    available_paths: list[str] = field(default_factory=list)
    pending_scenes: list[str] = field(default_factory=list)
    remote_tasks: list[dict] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    plan: dict[str, Any] = field(default_factory=dict)
    reused: bool = False
    partial: bool = False
    admissible_for_assembly: bool = False
    admission_reasons: list[str] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None

    def summary(self) -> dict:
        datos = {
            "media_run_id": self.media_run_id,
            "job_id": self.job_id,
            "voice_run_id": self.voice_run_id,
            "media_status": self.status,
            "simulation": self.simulation,
            "manifest_path": self.manifest_path,
            "contact_sheet_path": self.contact_sheet_path,
            "available_paths": self.available_paths,
            "pending_scenes": self.pending_scenes,
            "remote_tasks": self.remote_tasks,
            "issues": self.issues,
            "usage": self.usage,
            "reused": self.reused,
            "partial": self.partial,
            "admissible_for_assembly": self.admissible_for_assembly,
            "admission_reasons": self.admission_reasons,
            "exit_code": int(self.exit_code),
        }
        if self.plan:
            datos["plan"] = self.plan
        if self.error_code:
            datos["error_code"] = self.error_code
            datos["error_message"] = self.error_message
        return datos


class MediaPipeline:
    """Genera los medios visuales de un guion con voz ya medida."""

    def __init__(
        self,
        settings: Settings,
        request: MediaJobRequest,
        *,
        image_provider: Any | None = None,
        video_provider: Any | None = None,
        now: datetime | None = None,
    ) -> None:
        self.settings = settings
        self.request = request
        self._image_override = image_provider
        self._video_override = video_provider
        self.now = now or utcnow()
        self.data_dir = settings.effective_data_dir(simulation=request.simulation)
        self.issues: list[MediaIssue] = []
        self.media_run_id = ""
        self._rng = random.Random(request.seed or 0)

    # -- Entrada principal -------------------------------------------------

    def run(self) -> MediaOutcome:
        document, script_sha, manifest, voice_sha = self._load_inputs()
        self.job_id = document.job_id
        self.voice_run_id = manifest.voice_run_id

        try:
            self._gate(document, manifest)
        except MediaInputError as exc:
            return self._blocked_outcome(exc)

        bible = get_series_bible(
            self._profile(document).series_bible_id, self.settings.series_bible_path
        )
        usados = {
            character_id
            for escena in document.scenes
            for character_id in escena.character_ids
        }
        pack = load_reference_pack(self.settings.media_reference_pack_path)
        selection = build_selection(
            bible=bible, used_character_ids=usados, settings=self.settings, pack=pack
        )
        timeline = build_visual_timeline(manifest, document)

        ensure_free_space(self.data_dir, self.settings.min_free_disk_mb, stage="medios:inicio")
        lock_path = self.data_dir / f"media-{self.job_id}.lock"
        with ProcessLock(lock_path), Storage(self.data_dir) as storage:
            self.storage = storage
            self.media_storage = MediaStorage(storage)
            self.media_storage.migrate()

            plan = build_plan(
                settings=self.settings,
                document=document,
                timeline=timeline,
                bible=bible,
                selection=selection,
                simulation=self.request.simulation,
                media_storage=self.media_storage,
                data_dir=self.data_dir,
            )
            self.plan = plan
            if plan.blocked:
                return self._plan_blocked_outcome(plan)
            if not self.request.simulation and (plan.missing_credentials or plan.missing_tools):
                return self._plan_blocked_outcome(
                    plan,
                    code="media_missing_requirements",
                    message=(
                        "Faltan requisitos para ejecutar: "
                        + ", ".join(plan.missing_credentials + plan.missing_tools)
                    ),
                )

            fingerprint = self._fingerprint(script_sha, voice_sha, selection, plan)
            fila, reutilizado = self._get_or_create_run(fingerprint, script_sha, voice_sha)
            self.media_run_id = fila["media_run_id"]
            if reutilizado is not None:
                return reutilizado

            try:
                return self._generate(
                    document, manifest, bible, selection, timeline, plan,
                    script_sha, voice_sha,
                )
            except ViralgenError as exc:
                self.media_storage.update_run(
                    self.media_run_id,
                    status="failed",
                    error_code=exc.code,
                    error_message=exc.message[:2000],
                )
                logger.error("medios %s fallidos (%s): %s", self.media_run_id, exc.code, exc.message)
                return MediaOutcome(
                    media_run_id=self.media_run_id,
                    job_id=self.job_id,
                    voice_run_id=self.voice_run_id,
                    status="failed",
                    exit_code=exc.exit_code,
                    simulation=self.request.simulation,
                    error_code=exc.code,
                    error_message=exc.message,
                    issues=[issue.model_dump(mode="json") for issue in self.issues],
                    usage=self._usage_snapshot(),
                )

    def preflight(self) -> dict:
        """`media plan`: valida y enumera SIN red y SIN generar nada.

        Puede ejecutarse sin claves: informa aparte de las credenciales que
        faltarian para ejecutar de verdad.
        """
        document, script_sha, manifest, voice_sha = self._load_inputs()
        self.job_id = document.job_id
        self.voice_run_id = manifest.voice_run_id

        admision: dict = {}
        try:
            self._gate(document, manifest)
            admision = {"input_admission": "ok"}
        except MediaInputError as exc:
            admision = {
                "input_admission": "blocked",
                "error_code": exc.code,
                "error_message": exc.message,
                "reasons": list(exc.details.get("reasons", [])) or [exc.message],
            }

        bible = get_series_bible(
            self._profile(document).series_bible_id, self.settings.series_bible_path
        )
        usados = {
            character_id
            for escena in document.scenes
            for character_id in escena.character_ids
        }
        pack = load_reference_pack(self.settings.media_reference_pack_path)
        selection = build_selection(
            bible=bible, used_character_ids=usados, settings=self.settings, pack=pack
        )
        timeline = build_visual_timeline(manifest, document)

        with Storage(self.data_dir) as storage:
            media_storage = MediaStorage(storage)
            media_storage.migrate()
            plan = build_plan(
                settings=self.settings,
                document=document,
                timeline=timeline,
                bible=bible,
                selection=selection,
                simulation=self.request.simulation,
                media_storage=media_storage,
                data_dir=self.data_dir,
            )

        datos = plan.to_dict()
        datos.update(
            {
                "job_id": self.job_id,
                "voice_run_id": self.voice_run_id,
                "simulation": self.request.simulation,
                "script_sha256": script_sha,
                "voice_sha256": voice_sha,
                "timeline": {
                    "sample_rate_hz": timeline.sample_rate_hz,
                    "total_samples": timeline.total_samples,
                    "total_duration_s": round(timeline.total_duration_s, 6),
                },
                "admission": admision,
            }
        )
        if admision.get("input_admission") == "blocked":
            datos["blocked"] = True
            datos["can_run"] = False
        datos["exit_code"] = _preflight_exit_code(datos)
        return datos

    # -- Entradas y admision ----------------------------------------------

    def _load_inputs(self):
        try:
            crudo_guion = self.request.script_path.read_bytes()
            crudo_voz = self.request.voice_path.read_bytes()
        except FileNotFoundError as exc:
            raise ConfigError(f"No existe un archivo de entrada: {exc}") from exc
        except OSError as exc:
            raise ConfigError(f"No se pudieron leer las entradas: {exc}") from exc
        try:
            document = ScriptDocument.model_validate(json.loads(crudo_guion))
        except Exception as exc:
            raise MediaInputError(
                f"El guion no cumple el contrato 1.0: {str(exc)[:300]}"
            ) from exc
        try:
            manifest = VoiceManifest.model_validate(json.loads(crudo_voz))
        except Exception as exc:
            raise MediaInputError(
                f"El manifiesto de voz no cumple su contrato: {str(exc)[:300]}"
            ) from exc
        return (
            document,
            sha256_file(self.request.script_path),
            manifest,
            sha256_file(self.request.voice_path),
        )

    def _profile(self, document):
        from ..profiles import get_profile

        return get_profile(document.profile_id, self.settings.profiles_path)

    def _gate(self, document: ScriptDocument, manifest: VoiceManifest) -> None:
        """Vuelve a ejecutar las admisiones de guion y voz sobre los archivos.

        No basta con leer un booleano almacenado. En `--mock` se permiten
        UNICAMENTE las excepciones estructuradas de origen simulado de la ruta
        preview: `needs_review`, hashes incorrectos, archivos ausentes y
        alineacion inutilizable siguen bloqueando.
        """
        informe = check_voice_admission(
            script_path=self.request.script_path,
            manifest_path=self.request.voice_path,
            settings=self.settings,
        )
        if manifest.job_id != document.job_id:
            raise MediaInputError(
                "El manifiesto de voz no corresponde al guion indicado (job_id distinto).",
                details={"reasons": ["job_id distinto entre guion y voz"]},
            )
        if not informe.contract_valid:
            raise MediaInputError(
                "Las entradas no cumplen su contrato: " + "; ".join(informe.reasons),
                details={"checks": informe.checks, "reasons": informe.reasons},
            )
        if self.request.simulation:
            if informe.admissible_for_preview:
                return
            raise MediaInputError(
                "Las entradas no pasan la ruta preview: " + "; ".join(informe.preview_reasons),
                details={"checks": informe.checks, "reasons": informe.preview_reasons},
            )
        if not informe.admissible_for_assembly:
            raise MediaInputError(
                "Las entradas no estan admitidas para produccion, asi que no se emite "
                "ninguna peticion: " + "; ".join(informe.reasons),
                details={
                    "checks": informe.checks,
                    "reasons": informe.reasons,
                    "hint": "usa --mock para un recorrido de pruebas",
                },
            )

    # -- Idempotencia ------------------------------------------------------

    def _fingerprint(self, script_sha: str, voice_sha: str, selection, plan: MediaPlan) -> str:
        return sha256_json(
            {
                "script_sha256": script_sha,
                "voice_sha256": voice_sha,
                "reference_selection": selection.selection_payload(),
                "image_model": plan.image_model,
                "video_model": plan.video_model,
                "geometry_policy": self.settings.media_geometry_policy,
                "image_quality": self.settings.media_image_quality,
                "image_format": self.settings.media_image_format,
                "prompt_version": MEDIA_PROMPT_VERSION,
                "processing_version": MEDIA_PROCESSING_VERSION,
                "simulation": self.request.simulation,
                "media_config": self.settings.media_hashable_view(),
            }
        )

    def _get_or_create_run(self, fingerprint: str, script_sha: str, voice_sha: str):
        media_key = self.request.media_key or f"auto-{uuid.uuid4()}"
        existente = self.media_storage.get_run_by_key(media_key)
        if existente is None:
            fila = self.media_storage.create_run(
                {
                    "media_run_id": str(uuid.uuid4()),
                    "job_id": self.job_id,
                    "voice_run_id": self.voice_run_id,
                    "media_key": media_key,
                    "fingerprint": fingerprint,
                    "simulation": int(self.request.simulation),
                    "status": "running",
                    "script_sha256": script_sha,
                    "voice_sha256": voice_sha,
                }
            )
            return fila, None

        if existente["fingerprint"] != fingerprint:
            raise IdempotencyConflictError(
                f"La clave --media-key {media_key!r} ya existe con otra solicitud "
                f"(ejecucion {existente['media_run_id']}).",
                details={"media_run_id": existente["media_run_id"], "media_key": media_key},
            )
        if existente["status"] in {"ready", "needs_review"}:
            datos = self.media_storage.load_manifest(existente["media_run_id"])
            if datos is not None:
                self.media_run_id = existente["media_run_id"]
                salida = self._reexport(datos)
                if salida is not None:
                    return existente, salida
        self.media_storage.update_run(existente["media_run_id"], status="running")
        return existente, None

    def _reexport(self, manifest_data: dict) -> MediaOutcome | None:
        """Reutiliza una salida completa y valida, sin ningun HTTP.

        Revalida los archivos antes de reutilizarlos: si falta alguno, se
        regenera.
        """
        manifest = MediaManifest.model_validate(manifest_data)
        run_dir = self._run_dir()
        for asset in manifest.assets:
            ruta = run_dir / asset.path
            if not ruta.is_file() or sha256_file(ruta) != asset.sha256:
                logger.warning("falta o cambio %s: se regenera", asset.path)
                return None
        ruta_manifiesto = self._export_manifest(manifest)
        return MediaOutcome(
            media_run_id=self.media_run_id,
            job_id=self.job_id,
            voice_run_id=self.voice_run_id,
            status=manifest.control.media_status.value,
            exit_code=(
                ExitCode.OK
                if manifest.control.media_status is MediaStatus.READY
                else ExitCode.NEEDS_REVIEW
            ),
            simulation=manifest.simulation,
            manifest_path=str(ruta_manifiesto),
            contact_sheet_path=(
                str(run_dir / manifest.inspection.contact_sheet_path)
                if manifest.inspection.contact_sheet_path
                else None
            ),
            available_paths=[str(run_dir / asset.path) for asset in manifest.assets],
            issues=[issue.model_dump(mode="json") for issue in manifest.control.issues],
            usage=self._usage_snapshot(),
            reused=True,
            admissible_for_assembly=manifest.control.admissible_for_assembly,
        )

    # -- Resultados sin manifiesto ----------------------------------------

    def _blocked_outcome(self, exc: MediaInputError) -> MediaOutcome:
        logger.error("entradas bloqueadas (%s): %s", exc.code, exc.message)
        return MediaOutcome(
            media_run_id="",
            job_id=getattr(self, "job_id", ""),
            voice_run_id=getattr(self, "voice_run_id", ""),
            status="blocked",
            exit_code=exc.exit_code,
            simulation=self.request.simulation,
            error_code=exc.code,
            error_message=exc.message,
            admission_reasons=list(exc.details.get("reasons", [])) or [exc.message],
            # Bloqueo antes de tocar SQLite: cero peticiones, explicitamente.
            usage={"used_total": {}, "used_this_run": {}, "cache_hits": 0},
        )

    def _plan_blocked_outcome(
        self, plan: MediaPlan, *, code: str = "media_plan_blocked", message: str | None = None
    ) -> MediaOutcome:
        motivos = [issue.message for issue in plan.issues if issue.blocking]
        if message:
            motivos.append(message)
        logger.error("plan bloqueado: %s", "; ".join(motivos) or code)
        return MediaOutcome(
            media_run_id="",
            job_id=self.job_id,
            voice_run_id=self.voice_run_id,
            status="blocked",
            exit_code=ExitCode.VALIDATION,
            simulation=self.request.simulation,
            error_code=code,
            error_message="; ".join(motivos)[:2000] or code,
            issues=[
                MediaIssue(
                    code=issue.code,
                    message=issue.message,
                    severity=IssueSeverity(issue.severity),
                    blocking=issue.blocking,
                    scene_id=issue.scene_id,
                ).model_dump(mode="json")
                for issue in plan.issues
            ],
            plan=plan.to_dict(),
            admission_reasons=motivos,
            usage={"used_total": {}, "used_this_run": {}, "cache_hits": 0},
        )

    # -- Rutas -------------------------------------------------------------

    def _run_dir(self) -> Path:
        base = self.data_dir / "jobs" / self.job_id / "media" / self.media_run_id
        return ensure_within(self.data_dir, base)

    def _cache_dir(self) -> Path:
        sufijo = "sim" if self.request.simulation else "real"
        return self.data_dir / "media-cache" / sufijo

    def _cache_path(self, cache_key: str, extension: str) -> Path:
        return self._cache_dir() / cache_key[:2] / f"{cache_key}.{extension}"

    def _link_into_run(self, origen: Path, destino: Path) -> None:
        """Enlace fisico seguro para no duplicar megabytes.

        Nunca se escribe a traves del enlace: los archivos se crean primero en
        la cache y solo despues se enlazan.
        """
        destino.parent.mkdir(parents=True, exist_ok=True)
        if destino.exists():
            return
        try:
            os.link(origen, destino)
        except OSError:
            shutil.copyfile(origen, destino)

    def _usage_snapshot(self) -> dict:
        presupuesto = getattr(self, "budget", None)
        if presupuesto is None:
            historico = (
                self.media_storage.usage(self.job_id)
                if hasattr(self, "media_storage")
                else {}
            )
            return {"used_total": historico, "used_this_run": {}, "cache_hits": 0}
        datos = presupuesto.snapshot()
        datos["cache_hits"] = getattr(self, "cache_hits", 0)
        return datos

    # -- Senales internas de control ---------------------------------------

    class _Blocked(Exception):
        """Bloqueo sin resolver: se detienen TODAS las solicitudes nuevas."""

    class _WaitingRemote(Exception):
        """Se agoto la espera local. La tarea remota sigue viva."""

        def __init__(self, task: dict) -> None:
            super().__init__(task.get("task_id", ""))
            self.task = task

    # -- Generacion ---------------------------------------------------------

    def _build_budget(self) -> MediaBudget:
        historico = self.media_storage.usage(self.job_id)
        return MediaBudget(
            limits={
                "generation_attempts": float(self.settings.media_max_generation_attempts),
                "status_requests": float(self.settings.media_max_status_requests),
                "download_attempts": float(
                    self.settings.media_max_download_attempts * max(1, len(self.plan.scenes))
                ),
                "video_seconds": float(self.settings.media_max_video_seconds),
            },
            used=historico,
            reserve=lambda kind, scene_id, video_seconds: self.media_storage.reserve_request(
                media_run_id=self.media_run_id,
                job_id=self.job_id,
                kind=kind,
                scene_id=scene_id,
                video_seconds=video_seconds,
            ),
            settle=self.media_storage.settle_request,
        )

    def _issue(
        self,
        code: str,
        message: str,
        severity: IssueSeverity,
        *,
        blocking: bool,
        scene_id: str | None = None,
    ) -> None:
        self.issues.append(
            MediaIssue(
                code=code,
                message=message[:200],
                severity=severity,
                blocking=blocking,
                scene_id=scene_id,
            )
        )

    def _generate(
        self, document, manifest, bible, selection, timeline, plan, script_sha, voice_sha
    ) -> MediaOutcome:
        run_dir = self._run_dir()
        run_dir.mkdir(parents=True, exist_ok=True)
        self.cache_hits = 0
        self.plan_sample_rate = timeline.sample_rate_hz
        self.budget = self._build_budget()
        self.image_provider = self._image_override or build_image_provider(
            simulation=self.request.simulation, settings=self.settings, seed=self.request.seed
        )
        necesita_video = any(escena.needs_video for escena in plan.scenes)
        self.video_provider = None
        if necesita_video:
            # Se exige FFmpeg/ffprobe ANTES de generar ningun asset de video.
            require_video_tools(self.settings.ffmpeg_path, self.settings.ffprobe_path)
            self.video_provider = self._video_override or build_video_provider(
                simulation=self.request.simulation,
                settings=self.settings,
                seed=self.request.seed,
            )

        assets: list[MediaAsset] = []
        entradas: list[MediaSceneEntry] = []
        pendientes: list[str] = []
        paleta = pad_color_from_palette(list(bible.color_palette))

        try:
            referencias = self._resolve_references(selection, bible, plan, run_dir, assets)
        except MediaPipeline._Blocked:
            return self._partial_outcome(assets, [escena.scene_id for escena in plan.scenes])
        except MediaPipeline._WaitingRemote as espera:  # pragma: no cover - refs son imagenes
            return self._waiting_outcome(assets, [espera.task])

        for indice, escena_plan in enumerate(plan.scenes):
            try:
                entrada, nuevos = self._process_scene(
                    escena_plan, document, bible, referencias, run_dir, paleta
                )
            except MediaPipeline._Blocked:
                pendientes = [item.scene_id for item in plan.scenes[indice:]]
                logger.error(
                    "bloqueo en %s: se detienen las solicitudes de las escenas siguientes",
                    escena_plan.scene_id,
                )
                break
            except MediaPipeline._WaitingRemote as espera:
                return self._waiting_outcome(assets, [espera.task], plan.scenes[indice:])
            assets.extend(nuevos)
            entradas.append(entrada)
            self.media_storage.update_run(self.media_run_id, status="running")

        if pendientes:
            return self._partial_outcome(assets, pendientes)

        return self._finish(
            document, manifest, bible, selection, timeline, plan,
            script_sha, voice_sha, assets, entradas, referencias, run_dir,
        )

    # -- Referencias --------------------------------------------------------

    def _resolve_references(
        self, selection, bible, plan: MediaPlan, run_dir: Path, assets: list[MediaAsset]
    ) -> dict[str, dict]:
        """Resuelve las referencias de los personajes PRESENTES.

        Cada personaje usado debe tener su referencia resuelta antes de generar
        su escena. Los ausentes no se adjuntan a ninguna peticion.
        """
        resueltas: dict[str, dict] = {}
        for character_id in selection.character_ids:
            guardada = self.media_storage.get_reference(
                selection.set_id, character_id, self.request.simulation
            )
            if guardada and Path(guardada["path"]).is_file():
                # Se reutiliza el conjunto fijado entre episodios: cambiar el
                # modelo de las escenas no redibuja a los personajes.
                resueltas[character_id] = {
                    "path": Path(guardada["path"]),
                    "sha256": guardada["sha256"],
                    "width": guardada["width"],
                    "height": guardada["height"],
                    "provenance": guardada["provenance"],
                }
                self.cache_hits += 1
            elif character_id in selection.imported:
                importada = selection.imported[character_id]
                origen = Path(importada.path)
                destino = self._cache_path(
                    sha256_text(f"imported|{selection.set_id}|{character_id}|{origen}"), "jpg"
                )
                destino.parent.mkdir(parents=True, exist_ok=True)
                if not destino.exists():
                    shutil.copyfile(origen, destino)
                info = inspect_image(destino, max_pixels=self.settings.media_max_image_pixels)
                procedencia = {
                    "kind": "imported",
                    "rights_declaration": importada.rights_declaration,
                    "imported_from": importada.source_kind,
                    "simulation": self.request.simulation,
                }
                self.media_storage.put_reference(
                    {
                        "set_id": selection.set_id,
                        "character_id": character_id,
                        "simulation": self.request.simulation,
                        "path": str(destino),
                        "sha256": sha256_file(destino),
                        "width": info.width,
                        "height": info.height,
                        "provenance": procedencia,
                    }
                )
                resueltas[character_id] = {
                    "path": destino,
                    "sha256": sha256_file(destino),
                    "width": info.width,
                    "height": info.height,
                    "provenance": procedencia,
                }
            else:
                resueltas[character_id] = self._generate_reference(
                    selection, bible, character_id, plan
                )

            entrada = resueltas[character_id]
            relativa = f"references/{character_id}.jpg"
            self._link_into_run(entrada["path"], run_dir / relativa)
            assets.append(
                MediaAsset(
                    asset_id=f"ref_{character_id}",
                    kind=AssetKind.IMAGE,
                    role=AssetRole.CHARACTER_REFERENCE,
                    path=relativa,
                    size_bytes=(run_dir / relativa).stat().st_size,
                    sha256=entrada["sha256"],
                    mime="image/jpeg",
                    width=entrada["width"],
                    height=entrada["height"],
                    provider=str(entrada["provenance"].get("provider") or "local"),
                    model=str(entrada["provenance"].get("model") or "imported"),
                    simulation=bool(entrada["provenance"].get("simulation", False)),
                    prompt_version=MEDIA_PROMPT_VERSION,
                    parameters={"set_id": selection.set_id, "version": selection.version},
                )
            )
            entrada["relative_path"] = relativa
        return resueltas

    def _generate_reference(self, selection, bible, character_id: str, plan: MediaPlan) -> dict:
        prompt = reference_prompt(bible, character_id)
        tamano = plan.scenes[0].image_size if plan.scenes else "1024x1536"
        clave = reference_cache_key(
            prompt=prompt,
            model=self.image_provider.model,
            size=tamano,
            simulation=self.request.simulation,
        )
        ruta, info, golpe = self._obtain_image(
            cache_key=clave,
            scene_id=None,
            operation_id=f"ref_{character_id}",
            prompt=prompt,
            size=tamano,
            reference_paths=[],
        )
        if golpe:
            self.cache_hits += 1
        procedencia = {
            "kind": "generated",
            "rights_declaration": (
                "Referencia generada por este proyecto a partir de la biblia visual."
            ),
            "provider": self.image_provider.name,
            "model": self.image_provider.model,
            "parameters_hash": sha256_json(
                {"prompt": prompt, "size": tamano, "model": self.image_provider.model}
            ),
            "simulation": self.request.simulation,
        }
        self.media_storage.put_reference(
            {
                "set_id": selection.set_id,
                "character_id": character_id,
                "simulation": self.request.simulation,
                "path": str(ruta),
                "sha256": sha256_file(ruta),
                "width": info.width,
                "height": info.height,
                "provenance": procedencia,
            }
        )
        return {
            "path": ruta,
            "sha256": sha256_file(ruta),
            "width": info.width,
            "height": info.height,
            "provenance": procedencia,
        }

    # -- Obtencion de imagenes ---------------------------------------------

    def _obtain_image(
        self,
        *,
        cache_key: str,
        scene_id: str | None,
        operation_id: str,
        prompt: str,
        size: str,
        reference_paths: list[Path],
    ) -> tuple[Path, Any, bool]:
        """Devuelve (ruta, medidas, cache_hit) generando solo si hace falta."""
        extension = self.settings.media_image_format
        destino = self._cache_path(cache_key, extension)

        fila = self.media_storage.get_cached_asset(cache_key)
        if fila and Path(fila["path"]).is_file():
            ruta = Path(fila["path"])
            if sha256_file(ruta) == fila["sha256"]:
                return ruta, inspect_image(
                    ruta, max_pixels=self.settings.media_max_image_pixels
                ), True

        if destino.is_file():
            # Recuperacion tras una caida entre escribir el archivo y anotar el
            # checkpoint: se valida y se adopta SIN volver a generar.
            try:
                info = inspect_image(destino, max_pixels=self.settings.media_max_image_pixels)
            except ViralgenError:
                destino.unlink(missing_ok=True)
            else:
                self._register_cached(cache_key, destino, info, kind="image")
                logger.info("checkpoint recuperado del disco para %s", operation_id)
                return destino, info, True

        bloqueo = self.media_storage.get_unknown_outcome(cache_key)
        if bloqueo:
            self._issue(
                "outcome_unknown",
                f"{operation_id}: una peticion anterior quedo con resultado incierto "
                f"({bloqueo['reason'][:80]}); repetirla automaticamente podria pagarla dos "
                "veces. Hace falta una reconciliacion explicita.",
                IssueSeverity.ERROR,
                blocking=True,
                scene_id=scene_id,
            )
            raise MediaPipeline._Blocked()

        peticion = ImageRequest(
            operation_id=operation_id,
            prompt=prompt,
            model=self.image_provider.model,
            size=size,
            quality=self.settings.media_image_quality,
            output_format=self.settings.media_image_format,
            reference_paths=list(reference_paths),
            scene_id=scene_id,
        )
        try:
            resultado = self.image_provider.create_image(peticion, self.budget)
        except MediaOutcomeUnknownError as exc:
            self.media_storage.mark_unknown_outcome(
                operation_key=cache_key,
                job_id=self.job_id,
                scene_id=scene_id,
                reason=exc.message,
            )
            self._issue(
                "outcome_unknown", exc.message, IssueSeverity.ERROR,
                blocking=True, scene_id=scene_id,
            )
            raise MediaPipeline._Blocked() from exc
        except (MediaBudgetExceededError, MediaProviderError) as exc:
            self._issue(
                exc.code, f"{operation_id}: {exc.message}", IssueSeverity.ERROR,
                blocking=True, scene_id=scene_id,
            )
            raise MediaPipeline._Blocked() from exc

        temporal = destino.with_suffix(destino.suffix + ".tmp")
        temporal.parent.mkdir(parents=True, exist_ok=True)
        info = write_bytes_as_image(
            resultado.image_bytes,
            temporal,
            max_pixels=self.settings.media_max_image_pixels,
        )
        os.replace(temporal, destino)
        info = inspect_image(destino, max_pixels=self.settings.media_max_image_pixels)
        self._register_cached(
            cache_key, destino, info, kind="image",
            meta={"request_id": resultado.request_id, "usage": resultado.provider_usage},
        )
        return destino, info, False

    def _register_cached(self, cache_key: str, path: Path, info, *, kind: str, meta: dict | None = None) -> None:
        self.media_storage.put_cached_asset(
            cache_key,
            {
                "kind": kind,
                "simulation": self.request.simulation,
                "path": str(path),
                "sha256": sha256_file(path),
                "width": getattr(info, "width", 0),
                "height": getattr(info, "height", 0),
                "meta": meta or {},
            },
        )

    # -- Escenas -----------------------------------------------------------

    def _process_scene(
        self, escena_plan, document, bible, referencias: dict, run_dir: Path, paleta: str
    ) -> tuple[MediaSceneEntry, list[MediaAsset]]:
        """Completa una escena antes de empezar la siguiente."""
        nuevos: list[MediaAsset] = []
        presentes = [
            character_id
            for character_id in escena_plan.character_ids
            if character_id in referencias
        ]
        rutas_referencia = [referencias[cid]["path"] for cid in presentes]
        hashes_referencia = [referencias[cid]["sha256"] for cid in presentes]

        clave_imagen = image_cache_key(
            plan=escena_plan,
            model=self.image_provider.model,
            reference_hashes=hashes_referencia,
            simulation=self.request.simulation,
        )
        ruta_imagen, info_imagen, golpe = self._obtain_image(
            cache_key=clave_imagen,
            scene_id=escena_plan.scene_id,
            operation_id=f"img_{escena_plan.scene_id}",
            prompt=escena_plan.effective_prompt,
            size=escena_plan.image_size,
            reference_paths=rutas_referencia,
        )
        if golpe:
            self.cache_hits += 1

        relativa = f"images/{escena_plan.scene_id}.{self.settings.media_image_format}"
        self._link_into_run(ruta_imagen, run_dir / relativa)
        asset_imagen = MediaAsset(
            asset_id=f"img_{escena_plan.scene_id}",
            kind=AssetKind.IMAGE,
            role=AssetRole.SCENE_IMAGE,
            path=relativa,
            size_bytes=(run_dir / relativa).stat().st_size,
            sha256=sha256_file(ruta_imagen),
            mime=info_imagen.mime,
            width=info_imagen.width,
            height=info_imagen.height,
            intrinsic_duration_s=None,  # una imagen no tiene duracion propia
            fps=None,
            provider=self.image_provider.name,
            model=self.image_provider.model,
            simulation=self.request.simulation,
            effective_prompt=escena_plan.effective_prompt,
            prompt_version=MEDIA_PROMPT_VERSION,
            parameters={
                "size": escena_plan.image_size,
                "quality": escena_plan.image_quality,
                "output_format": escena_plan.image_format,
                "operation": "image_edit" if presentes else "image_generate",
            },
            reference_asset_ids=[f"ref_{cid}" for cid in presentes],
            cache_hit=golpe,
        )
        nuevos.append(asset_imagen)

        presentacion = PresentationPlan(
            policy=GeometryPolicy(self.settings.media_geometry_policy),
            target_width=self.plan.target_width,
            target_height=self.plan.target_height,
            pad_color=paleta,
        )

        if not escena_plan.needs_video:
            entrada = MediaSceneEntry(
                scene_id=escena_plan.scene_id,
                order=escena_plan.order,
                requested_type="image",
                resolved_type="image",
                primary_asset_id=asset_imagen.asset_id,
                start_sample=escena_plan.start_sample,
                end_sample=escena_plan.end_sample,
                start_s=round(escena_plan.start_sample / self.plan_sample_rate, 6),
                end_s=round(escena_plan.end_sample / self.plan_sample_rate, 6),
                duration_s=round(escena_plan.duration_s, 6),
                character_ids=presentes,
                presentation=presentacion,
            )
            return entrada, nuevos

        # --- Escena de video ------------------------------------------------
        semilla_asset, ruta_semilla = self._prepare_seed(
            escena_plan, ruta_imagen, info_imagen, run_dir, paleta
        )
        nuevos.append(semilla_asset)

        clip_asset = self._obtain_clip(escena_plan, ruta_semilla, semilla_asset, run_dir)
        nuevos.append(clip_asset)

        entrada = MediaSceneEntry(
            scene_id=escena_plan.scene_id,
            order=escena_plan.order,
            requested_type="video",
            resolved_type="video",
            primary_asset_id=clip_asset.asset_id,
            start_sample=escena_plan.start_sample,
            end_sample=escena_plan.end_sample,
            start_s=round(escena_plan.start_sample / self.plan_sample_rate, 6),
            end_s=round(escena_plan.end_sample / self.plan_sample_rate, 6),
            duration_s=round(escena_plan.duration_s, 6),
            character_ids=presentes,
            presentation=presentacion,
            clip_trim=ClipTrim(
                play_from_s=0.0, play_to_s=round(escena_plan.duration_s, 6)
            ),
        )
        return entrada, nuevos

    def _prepare_seed(
        self, escena_plan, ruta_imagen: Path, info_imagen, run_dir: Path, paleta: str
    ) -> tuple[MediaAsset, Path]:
        """Imagen inicial UNICA en la proporcion que se pedira a Runway."""
        ancho, alto = parse_ratio(escena_plan.video_ratio)
        plan_geometria = plan_geometry(
            source_width=info_imagen.width,
            source_height=info_imagen.height,
            target_width=ancho,
            target_height=alto,
            policy=self.settings.media_geometry_policy,
            pad_color=paleta,
            applied=True,
        )
        clave = sha256_json(
            {
                "operation": "video_seed",
                "source_sha256": sha256_file(ruta_imagen),
                "geometry": plan_geometria.to_dict(),
                "simulation": self.request.simulation,
            }
        )
        destino = self._cache_path(clave, "jpg")
        if not destino.is_file():
            temporal = destino.with_suffix(".tmp.jpg")
            apply_geometry(
                ruta_imagen, temporal, plan_geometria,
                max_pixels=self.settings.media_max_image_pixels,
            )
            os.replace(temporal, destino)
        info = inspect_image(destino, max_pixels=self.settings.media_max_image_pixels)

        # El limite de Runway se aplica a la data URI YA CODIFICADA.
        limite_binario = int(self.settings.media_data_uri_max_bytes * 3 / 4) - 64
        politica: dict = {}
        if destino.stat().st_size > limite_binario:
            comprimido = self._cache_path(clave + "c", "jpg")
            _, politica = encode_jpeg_under_limit(
                destino, comprimido, max_bytes=limite_binario,
                max_pixels=self.settings.media_max_image_pixels,
            )
            destino = comprimido
            info = inspect_image(destino, max_pixels=self.settings.media_max_image_pixels)

        self._register_cached(clave, destino, info, kind="image", meta={"compression": politica})
        relativa = f"images/{escena_plan.scene_id}_seed.jpg"
        self._link_into_run(destino, run_dir / relativa)
        asset = MediaAsset(
            asset_id=f"seed_{escena_plan.scene_id}",
            kind=AssetKind.IMAGE,
            role=AssetRole.VIDEO_SEED,
            path=relativa,
            size_bytes=(run_dir / relativa).stat().st_size,
            sha256=sha256_file(destino),
            mime=info.mime,
            width=info.width,
            height=info.height,
            provider="local",
            model="pillow",
            simulation=self.request.simulation,
            prompt_version=MEDIA_PROMPT_VERSION,
            parameters={"ratio": escena_plan.video_ratio},
            derived_from=f"img_{escena_plan.scene_id}",
            transformation=AssetTransformation(
                policy=GeometryPolicy(plan_geometria.policy),
                source_width=plan_geometria.source_width,
                source_height=plan_geometria.source_height,
                target_width=plan_geometria.target_width,
                target_height=plan_geometria.target_height,
                scaled_width=plan_geometria.scaled_width,
                scaled_height=plan_geometria.scaled_height,
                offset_x=plan_geometria.offset_x,
                offset_y=plan_geometria.offset_y,
                pad_color=plan_geometria.pad_color,
                crop_left=plan_geometria.crop_box[0] if plan_geometria.crop_box else None,
                crop_top=plan_geometria.crop_box[1] if plan_geometria.crop_box else None,
                crop_right=plan_geometria.crop_box[2] if plan_geometria.crop_box else None,
                crop_bottom=plan_geometria.crop_box[3] if plan_geometria.crop_box else None,
            ),
        )
        return asset, destino

    def _obtain_clip(
        self, escena_plan, ruta_semilla: Path, semilla_asset: MediaAsset, run_dir: Path
    ) -> MediaAsset:
        """Crea o reanuda la tarea remota, descarga y mide el clip."""
        semilla_sha = semilla_asset.sha256
        clave = video_cache_key(
            init_image_sha256=semilla_sha,
            motion_prompt=escena_plan.motion_prompt or "",
            model=self.video_provider.model,
            ratio=escena_plan.video_ratio,
            duration_s=escena_plan.video_duration_s,
            simulation=self.request.simulation,
        )
        destino = self._cache_dir() / clave[:2] / f"{clave}.mp4"

        fila = self.media_storage.get_cached_asset(clave)
        if fila and Path(fila["path"]).is_file() and sha256_file(Path(fila["path"])) == fila["sha256"]:
            self.cache_hits += 1
            return self._clip_asset(
                escena_plan, Path(fila["path"]), semilla_asset, run_dir,
                task_id=fila["meta"].get("task_id"), cache_hit=True,
            )
        if destino.is_file():
            try:
                info = probe_video(
                    destino, ffprobe_path=self.settings.ffprobe_path,
                    timeout_s=self.settings.media_http_timeout_s,
                )
            except ViralgenError:
                destino.unlink(missing_ok=True)
            else:
                self._register_cached(clave, destino, info, kind="video")
                logger.info("checkpoint de clip recuperado para %s", escena_plan.scene_id)
                return self._clip_asset(
                    escena_plan, destino, semilla_asset, run_dir, task_id=None, cache_hit=True
                )

        bloqueo = self.media_storage.get_unknown_outcome(clave)
        if bloqueo:
            self._issue(
                "outcome_unknown",
                f"{escena_plan.scene_id}: una creacion anterior quedo con resultado incierto "
                f"({bloqueo['reason'][:80]}). Hace falta reconciliacion explicita.",
                IssueSeverity.ERROR,
                blocking=True,
                scene_id=escena_plan.scene_id,
            )
            raise MediaPipeline._Blocked()

        peticion = VideoRequest(
            operation_id=clave,
            scene_id=escena_plan.scene_id,
            init_image_path=ruta_semilla,
            init_image_sha256=semilla_sha,
            prompt_text=escena_plan.motion_prompt or "",
            model=self.video_provider.model,
            ratio=escena_plan.video_ratio,
            duration_s=escena_plan.video_duration_s,
        )

        guardada = self.media_storage.get_task(clave)
        if guardada and guardada["state"] not in {"failed", "cancelled"}:
            # Una operacion identica con tarea en curso reutiliza su task_id:
            # no se emite otro POST.
            task_id = guardada["task_id"]
            logger.info("se reanuda la tarea %s de %s", task_id, escena_plan.scene_id)
        else:
            try:
                tarea = self.video_provider.create_task(peticion, self.budget)
            except MediaOutcomeUnknownError as exc:
                self.media_storage.mark_unknown_outcome(
                    operation_key=clave, job_id=self.job_id,
                    scene_id=escena_plan.scene_id, reason=exc.message,
                )
                self._issue(
                    "outcome_unknown", exc.message, IssueSeverity.ERROR,
                    blocking=True, scene_id=escena_plan.scene_id,
                )
                raise MediaPipeline._Blocked() from exc
            except (MediaBudgetExceededError, MediaProviderError) as exc:
                self._issue(
                    exc.code, f"{escena_plan.scene_id}: {exc.message}", IssueSeverity.ERROR,
                    blocking=True, scene_id=escena_plan.scene_id,
                )
                raise MediaPipeline._Blocked() from exc
            task_id = tarea.task_id
            # El task_id se persiste EN CUANTO llega.
            self.media_storage.put_task(
                operation_key=clave, job_id=self.job_id, media_run_id=self.media_run_id,
                scene_id=escena_plan.scene_id, task_id=task_id, state="pending",
                simulation=self.request.simulation,
            )

        estado = self._await_task(clave, task_id, escena_plan)
        if estado.state != "succeeded":
            self.media_storage.update_task_state(clave, estado.state)
            self._issue(
                "clip_fallido",
                f"{escena_plan.scene_id}: la tarea {task_id} termino en {estado.state}"
                + (f" ({estado.failure})" if estado.failure else ""),
                IssueSeverity.ERROR,
                blocking=True,
                scene_id=escena_plan.scene_id,
            )
            raise MediaPipeline._Blocked()

        ruta = self._download_clip(clave, estado, escena_plan, destino)
        info = probe_video(
            ruta, ffprobe_path=self.settings.ffprobe_path,
            timeout_s=self.settings.media_http_timeout_s,
        )
        decode_integrity_check(
            ruta, ffmpeg_path=self.settings.ffmpeg_path,
            timeout_s=self.settings.media_http_timeout_s, max_seconds=2.0,
        )
        if info.duration_s + 1e-3 < escena_plan.duration_s:
            self._issue(
                "clip_demasiado_corto",
                f"{escena_plan.scene_id}: el clip mide {info.duration_s:.2f} s y la escena "
                f"necesita {escena_plan.duration_s:.2f} s. Una salida mas corta bloquea.",
                IssueSeverity.ERROR,
                blocking=True,
                scene_id=escena_plan.scene_id,
            )
            raise MediaPipeline._Blocked()

        self._register_cached(clave, ruta, info, kind="video", meta={"task_id": task_id})
        self.media_storage.update_task_state(clave, "succeeded")
        return self._clip_asset(
            escena_plan, ruta, semilla_asset, run_dir, task_id=task_id, cache_hit=False
        )

    def _await_task(self, clave: str, task_id: str, escena_plan):
        """Consulta la tarea con intervalo minimo, jitter y plazo local."""
        limite = time.monotonic() + self.settings.media_poll_wait_s
        primera = True
        while True:
            if not self.budget.can_spend("status_requests"):
                # Agotar el presupuesto de consultas NO cancela la tarea ni
                # autoriza otra creacion: se conserva el task_id.
                self._issue(
                    "presupuesto_de_consultas_agotado",
                    f"{escena_plan.scene_id}: se agotaron las consultas de estado. La tarea "
                    f"{task_id} sigue viva; hace falta una resolucion explicita.",
                    IssueSeverity.ERROR,
                    blocking=True,
                    scene_id=escena_plan.scene_id,
                )
                raise MediaPipeline._Blocked()
            if not primera:
                espera = self.settings.media_poll_interval_s + self._rng.uniform(0.0, 1.5)
                if time.monotonic() + espera > limite:
                    self.media_storage.update_task_state(clave, "running")
                    raise MediaPipeline._WaitingRemote(
                        {
                            "scene_id": escena_plan.scene_id,
                            "task_id": task_id,
                            "state": "running",
                            "operation_key": clave,
                        }
                    )
                time.sleep(espera)
            primera = False
            try:
                estado = self.video_provider.poll_task(task_id, self.budget)
            except (MediaBudgetExceededError, MediaProviderError) as exc:
                self._issue(
                    exc.code, f"{escena_plan.scene_id}: {exc.message}", IssueSeverity.ERROR,
                    blocking=True, scene_id=escena_plan.scene_id,
                )
                raise MediaPipeline._Blocked() from exc
            self.media_storage.update_task_state(clave, estado.state)
            if estado.terminal:
                return estado
            if time.monotonic() >= limite:
                raise MediaPipeline._WaitingRemote(
                    {
                        "scene_id": escena_plan.scene_id,
                        "task_id": task_id,
                        "state": estado.state,
                        "operation_key": clave,
                    }
                )

    def _download_clip(self, clave: str, estado, escena_plan, destino: Path) -> Path:
        """Descarga con reintentos de DESCARGA, nunca creando otro video."""
        limite = self.settings.media_max_video_file_mib * 1024 * 1024
        intentos = self.settings.media_max_download_attempts
        ultimo: Exception | None = None
        for intento in range(intentos):
            temporal = destino.with_suffix(f".part{intento}")
            try:
                self.video_provider.download_output(
                    estado, temporal, self.budget, max_bytes=limite
                )
            except MediaBudgetExceededError as exc:
                self._issue(
                    exc.code, f"{escena_plan.scene_id}: {exc.message}", IssueSeverity.ERROR,
                    blocking=True, scene_id=escena_plan.scene_id,
                )
                raise MediaPipeline._Blocked() from exc
            except MediaProviderError as exc:
                ultimo = exc
                temporal.unlink(missing_ok=True)
                logger.warning(
                    "descarga fallida de %s (%d/%d): %s",
                    escena_plan.scene_id, intento + 1, intentos, exc.message,
                )
                continue
            destino.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temporal, destino)
            return destino

        # El task_id se conserva: la tarea remota sigue existiendo.
        self._issue(
            "descarga_fallida",
            f"{escena_plan.scene_id}: no se pudo descargar la salida de la tarea "
            f"{estado.task_id} tras {intentos} intentos. El task_id se conserva; no se "
            "crea otro video.",
            IssueSeverity.ERROR,
            blocking=True,
            scene_id=escena_plan.scene_id,
        )
        raise MediaPipeline._Blocked() from ultimo

    def _clip_asset(
        self, escena_plan, ruta: Path, semilla_asset: MediaAsset, run_dir: Path,
        *, task_id: str | None, cache_hit: bool,
    ) -> MediaAsset:
        info = probe_video(
            ruta, ffprobe_path=self.settings.ffprobe_path,
            timeout_s=self.settings.media_http_timeout_s,
        )
        relativa = f"video/{escena_plan.scene_id}.mp4"
        self._link_into_run(ruta, run_dir / relativa)
        return MediaAsset(
            asset_id=f"clip_{escena_plan.scene_id}",
            kind=AssetKind.VIDEO,
            role=AssetRole.SCENE_CLIP,
            path=relativa,
            size_bytes=(run_dir / relativa).stat().st_size,
            sha256=sha256_file(ruta),
            mime="video/mp4",
            width=info.width,
            height=info.height,
            intrinsic_duration_s=round(info.duration_s, 6),
            fps=round(info.fps, 6),
            provider=self.video_provider.name,
            model=self.video_provider.model,
            simulation=self.request.simulation,
            effective_prompt=escena_plan.motion_prompt,
            prompt_version=MEDIA_PROMPT_VERSION,
            parameters={
                "ratio": escena_plan.video_ratio,
                "duration_s": escena_plan.video_duration_s,
            },
            derived_from=semilla_asset.asset_id,
            cache_hit=cache_hit,
            video=VideoDetails(
                task_id=task_id,
                init_image_sha256=semilla_asset.sha256,
                requested_duration_s=float(escena_plan.video_duration_s),
                measured_duration_s=round(info.duration_s, 6),
                fps_rational=f"{info.fps_num}/{info.fps_den}",
                fps=round(info.fps, 6),
                codec=info.codec,
                container=info.container,
                has_audio=info.has_audio,
                ratio=escena_plan.video_ratio,
            ),
        )

    # -- Resultados ---------------------------------------------------------

    def _partial_outcome(self, assets: list[MediaAsset], pendientes: list[str]) -> MediaOutcome:
        """Resultado PARCIAL: no se exporta un media.json que parezca completo."""
        bloqueantes = [issue for issue in self.issues if issue.blocking]
        motivo = bloqueantes[-1].message if bloqueantes else "bloqueo sin detalle"
        self.media_storage.update_run(
            self.media_run_id,
            status="needs_review",
            media_status="needs_review",
            error_code="media_blocked_partial",
            error_message=(
                f"{motivo}. Se detuvieron las solicitudes: quedan {len(pendientes)} escenas "
                f"sin resolver ({', '.join(pendientes)})."
            )[:2000],
        )
        run_dir = self._run_dir()
        logger.warning(
            "trabajo parcial %s: %d assets, %d escenas pendientes",
            self.media_run_id, len(assets), len(pendientes),
        )
        return MediaOutcome(
            media_run_id=self.media_run_id,
            job_id=self.job_id,
            voice_run_id=self.voice_run_id,
            status="needs_review",
            exit_code=ExitCode.NEEDS_REVIEW,
            simulation=self.request.simulation,
            manifest_path=None,
            available_paths=[
                str(run_dir / asset.path)
                for asset in assets
                if (run_dir / asset.path).is_file()
            ],
            pending_scenes=list(pendientes),
            issues=[issue.model_dump(mode="json") for issue in self.issues],
            usage=self._usage_snapshot(),
            partial=True,
            admission_reasons=[motivo],
        )

    def _waiting_outcome(
        self, assets: list[MediaAsset], tareas: list[dict], restantes=None
    ) -> MediaOutcome:
        """Espera local agotada. Ni exito ni fallo definitivo."""
        self.media_storage.update_run(
            self.media_run_id,
            status="waiting_remote",
            error_code=None,
            error_message=None,
        )
        run_dir = self._run_dir()
        pendientes = [item.scene_id for item in (restantes or [])]
        logger.info(
            "espera local agotada: %d tarea(s) remota(s) siguen vivas", len(tareas)
        )
        return MediaOutcome(
            media_run_id=self.media_run_id,
            job_id=self.job_id,
            voice_run_id=self.voice_run_id,
            status="waiting_remote",
            exit_code=ExitCode.WAITING_REMOTE,
            simulation=self.request.simulation,
            manifest_path=None,
            available_paths=[
                str(run_dir / asset.path)
                for asset in assets
                if (run_dir / asset.path).is_file()
            ],
            pending_scenes=pendientes,
            remote_tasks=tareas,
            issues=[issue.model_dump(mode="json") for issue in self.issues],
            usage=self._usage_snapshot(),
            partial=True,
            admission_reasons=[
                "hay una tarea remota en curso; vuelve a invocar el mismo comando para "
                "seguir consultando ESE task_id"
            ],
        )

    def _finish(
        self, document, manifest, bible, selection, timeline, plan,
        script_sha, voice_sha, assets, entradas, referencias, run_dir,
    ) -> MediaOutcome:
        # --- Hoja de contacto ----------------------------------------------
        hoja_relativa = None
        imagenes = [
            (f"{asset.asset_id}", run_dir / asset.path)
            for asset in assets
            if asset.role in (AssetRole.SCENE_IMAGE, AssetRole.CHARACTER_REFERENCE)
        ]
        if imagenes:
            hoja_relativa = "contact_sheet.jpg"
            info_hoja = build_contact_sheet(
                imagenes, run_dir / hoja_relativa,
                simulation=self.request.simulation,
                max_pixels=self.settings.media_max_image_pixels,
            )
            assets.append(
                MediaAsset(
                    asset_id="contact_sheet",
                    kind=AssetKind.CONTACT_SHEET,
                    role=AssetRole.INSPECTION,
                    path=hoja_relativa,
                    size_bytes=(run_dir / hoja_relativa).stat().st_size,
                    sha256=sha256_file(run_dir / hoja_relativa),
                    mime="image/jpeg",
                    width=info_hoja.width,
                    height=info_hoja.height,
                    provider="local",
                    model="pillow",
                    simulation=self.request.simulation,
                    prompt_version=MEDIA_PROMPT_VERSION,
                )
            )

        # --- Limites de disco ----------------------------------------------
        ocupado = directory_size_mb(run_dir)
        if ocupado > self.settings.media_max_job_mib:
            raise DiskSpaceError(
                f"El trabajo de medios ocupa {ocupado:.0f} MiB y el limite es "
                f"{self.settings.media_max_job_mib} MiB.",
                details={"run_dir": str(run_dir)},
            )
        ensure_free_space(
            self.data_dir, self.settings.min_free_disk_mb, stage="medios:exportacion"
        )

        bloqueado = any(issue.blocking for issue in self.issues)
        estado = MediaStatus.NEEDS_REVIEW if bloqueado else MediaStatus.READY
        admisible = (
            estado is MediaStatus.READY
            and not self.request.simulation
            and document.simulation is False
            and manifest.simulation is False
        )

        historico = self.media_storage.usage(self.job_id)
        instantanea = self.budget.snapshot()
        uso = MediaUsage(
            generation_attempts_total=int(historico["generation_attempts"]),
            generation_attempts_this_run=int(
                instantanea["used_this_run"].get("generation_attempts", 0)
            ),
            video_seconds_reserved_total=float(historico["video_seconds"]),
            video_seconds_reserved_this_run=float(
                instantanea["used_this_run"].get("video_seconds", 0)
            ),
            status_requests_total=int(historico["status_requests"]),
            status_requests_this_run=int(
                instantanea["used_this_run"].get("status_requests", 0)
            ),
            download_attempts_total=int(historico["download_attempts"]),
            download_attempts_this_run=int(
                instantanea["used_this_run"].get("download_attempts", 0)
            ),
            cache_hits=self.cache_hits,
            provider_reported={},
            estimated_cost_usd=self._estimated_cost(assets),
        )

        entradas_ref = [
            ReferenceEntry(
                character_id=character_id,
                path=datos["relative_path"],
                sha256=datos["sha256"],
                width=datos["width"],
                height=datos["height"],
                mime="image/jpeg",
                provenance=ReferenceProvenance(**datos["provenance"]),
            )
            for character_id, datos in sorted(referencias.items())
        ]

        documento = MediaManifest(
            media_run_id=self.media_run_id,
            job_id=self.job_id,
            voice_run_id=self.voice_run_id,
            created_at=self.now,
            simulation=self.request.simulation,
            source=MediaSource(
                script_schema_version=document.schema_version,
                script_sha256=script_sha,
                script_simulation=document.simulation,
                voice_schema_version=manifest.schema_version,
                voice_sha256=voice_sha,
                voice_simulation=manifest.simulation,
                profile_id=document.profile_id,
                channel=document.channel.value,
            ),
            providers=MediaProviders(
                image_provider=self.image_provider.name,
                image_model=self.image_provider.model,
                video_provider=self.video_provider.name if self.video_provider else None,
                video_model=self.video_provider.model if self.video_provider else None,
                video_api_version=(
                    getattr(self.video_provider, "api_version", None)
                    if self.video_provider
                    else None
                ),
                geometry_policy=GeometryPolicy(self.settings.media_geometry_policy),
                processing_version=MEDIA_PROCESSING_VERSION,
                settings_hash=sha256_json(self.settings.media_hashable_view()),
            ),
            control=MediaControl(
                media_status=estado,
                issues=list(self.issues),
                visual_review=VisualReview.NOT_PERFORMED,
                admissible_for_assembly=admisible,
            ),
            timeline=MediaTimeline(
                sample_rate_hz=timeline.sample_rate_hz,
                total_samples=timeline.total_samples,
                total_duration_s=round(timeline.total_duration_s, 6),
                target_width=plan.target_width,
                target_height=plan.target_height,
                target_fps=plan.target_fps,
            ),
            references=ReferenceSet(
                set_id=selection.set_id.replace(".", "_"),
                version=selection.version.replace(".", "_"),
                series_bible_id=selection.series_bible_id,
                bible_sha256=selection.bible_sha256,
                entries=entradas_ref,
            ),
            assets=assets,
            scenes=entradas,
            usage=uso,
            inspection=MediaInspection(contact_sheet_path=hoja_relativa),
        )

        self.media_storage.save_manifest(
            self.media_run_id, documento.model_dump(mode="json"), estado.value
        )
        self.media_storage.update_run(
            self.media_run_id, status=estado.value, error_code=None, error_message=None
        )
        ruta = self._export_manifest(documento)

        return MediaOutcome(
            media_run_id=self.media_run_id,
            job_id=self.job_id,
            voice_run_id=self.voice_run_id,
            status=estado.value,
            exit_code=ExitCode.OK if estado is MediaStatus.READY else ExitCode.NEEDS_REVIEW,
            simulation=self.request.simulation,
            manifest_path=str(ruta),
            contact_sheet_path=str(run_dir / hoja_relativa) if hoja_relativa else None,
            available_paths=[str(run_dir / asset.path) for asset in assets],
            issues=[issue.model_dump(mode="json") for issue in self.issues],
            usage=self._usage_snapshot(),
            plan=plan.to_dict(),
            admissible_for_assembly=admisible,
            admission_reasons=(
                []
                if admisible
                else _admission_reasons(self.issues, self.request.simulation, document, manifest)
            ),
        )

    def _estimated_cost(self, assets: list[MediaAsset]) -> float | None:
        """Coste estimado local. Sin tarifa configurada, None."""
        tarifas = self.settings.media_pricing()
        if tarifas["image_per_unit_usd"] is None and tarifas["video_per_second_usd"] is None:
            return None
        total = 0.0
        for asset in assets:
            if asset.cache_hit or asset.provider in {"local"}:
                continue
            if asset.kind is AssetKind.IMAGE and tarifas["image_per_unit_usd"] is not None:
                total += tarifas["image_per_unit_usd"]
            elif asset.kind is AssetKind.VIDEO and tarifas["video_per_second_usd"] is not None:
                total += tarifas["video_per_second_usd"] * float(
                    asset.video.requested_duration_s if asset.video else 0
                )
        return round(total, 6)

    def _export_manifest(self, documento: MediaManifest) -> Path:
        ruta = self._run_dir() / "media.json"
        datos = documento.model_dump(mode="json")
        try:
            atomic_write_json(ruta, datos)
        except OSError as exc:
            self.media_storage.record_export(
                self.media_run_id, path=ruta, sha256="", status="failed"
            )
            raise ViralgenError(
                f"No se pudo escribir {ruta}: {exc}. El manifiesto sigue en SQLite: repite "
                "el comando con la misma --media-key para rehacerlo sin regenerar nada.",
                details={"path": str(ruta)},
            ) from exc
        self.media_storage.record_export(
            self.media_run_id,
            path=ruta,
            sha256=sha256_text(json.dumps(datos, ensure_ascii=False, sort_keys=True)),
            status="ok",
        )
        return ruta


def _admission_reasons(issues, simulation: bool, document, manifest) -> list[str]:
    motivos = [issue.message for issue in issues if issue.blocking]
    if simulation:
        motivos.append("simulation=true: los medios simulados nunca alimentan el montaje")
    if document.simulation:
        motivos.append("el guion de origen es simulado")
    if manifest.simulation:
        motivos.append("la voz de origen es simulada")
    return motivos


def _preflight_exit_code(plan_dict: dict) -> int:
    """`plan` no genera nada: su codigo distingue bloqueo de falta de requisitos."""
    if plan_dict.get("blocked"):
        return int(ExitCode.VALIDATION)
    if plan_dict.get("missing_credentials") or plan_dict.get("missing_tools"):
        return int(ExitCode.NEEDS_REVIEW)
    return int(ExitCode.OK)
