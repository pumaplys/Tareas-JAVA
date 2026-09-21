"""Orquestacion del modulo 2: guion admitido -> voz medida -> voice.json.

Flujo de `voice generate`:

1. Lee el guion y calcula el SHA-256 de sus BYTES exactos.
2. Puerta de admision: en modo real exige el criterio completo del modulo 1
   ANTES de cualquier peticion de pago. En `--mock` se relaja unicamente la
   condicion de origen simulado, usando las claves estructuradas del informe
   (nunca el texto humano de sus mensajes).
3. Comprueba disco, FFmpeg (solo en real) y bloquea el trabajo.
4. Recupera o crea la ejecucion por `--voice-key` (idempotencia).
5. Sintetiza escena a escena, reutilizando los clips ya guardados cuya
   identidad y hash coinciden.
6. Mide en muestras, construye narration.wav por bloques y alinea por palabra.
7. Planifica el sonido opcional, arma el manifiesto y lo exporta.

Nunca se mantiene una transaccion de escritura abierta durante una llamada
HTTP, y cada peticion se reserva en SQLite ANTES de enviarse.
"""

from __future__ import annotations

import json
import shutil
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
from ..profiles import get_profile
from ..schemas.document import ScriptDocument
from ..storage import ProcessLock, Storage, iso, utcnow
from ..textutil import sha256_json, sha256_text
from ..validation import check_admission, validate_document
from . import VOICE_PROCESSING_VERSION, VOICE_SCHEMA_VERSION
from .alignment import AlignmentResult, CharAlignment, build_alignment, emphasis_set, is_emphasis
from .audio import PcmFormat, iter_wav_frames, pause_samples, read_wav_info, require_ffmpeg, silence_bytes, write_wav
from .profiles import get_voice_profile, load_sound_assets, resolve_voice_id
from .providers import SynthesisRequest, VoiceBudget, build_voice_provider
from .providers.base import VoiceProvider
from .schemas import (
    AlignmentMethod,
    AlignmentStatus,
    IssueSeverity,
    SoundAssetRef,
    VoiceControl,
    VoiceIssue,
    VoiceManifest,
    VoiceMaster,
    VoiceProviderInfo,
    VoiceScene,
    VoiceSound,
    VoiceSource,
    VoiceStatus,
    VoiceUsage,
    VoiceWord,
)
from .sound import SceneTiming, plan_sound
from .timing import DURATION_TOLERANCE, build_measured_timeline, total_samples

logger = get_logger("voice.pipeline")


@dataclass
class VoiceJobRequest:
    script_path: Path
    voice_key: str | None = None
    simulation: bool = False
    seed: int | None = None


@dataclass
class VoiceOutcome:
    voice_run_id: str
    job_id: str
    status: str
    exit_code: ExitCode
    simulation: bool
    manifest_path: str | None = None
    master_path: str | None = None
    measured_duration_s: float | None = None
    admissible_for_assembly: bool = False
    admission_reasons: list[str] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    requests_new: int = 0
    requests_total: int = 0
    reused: bool = False
    error_code: str | None = None
    error_message: str | None = None

    def summary(self) -> dict:
        datos = {
            "voice_run_id": self.voice_run_id,
            "job_id": self.job_id,
            "voice_status": self.status,
            "simulation": self.simulation,
            "manifest_path": self.manifest_path,
            "master_path": self.master_path,
            "measured_duration_s": self.measured_duration_s,
            "admissible_for_assembly": self.admissible_for_assembly,
            "admission_reasons": self.admission_reasons,
            "issues": self.issues,
            "requests_new": self.requests_new,
            "requests_total": self.requests_total,
            "reused": self.reused,
            "exit_code": int(self.exit_code),
        }
        if self.error_code:
            datos["error_code"] = self.error_code
            datos["error_message"] = self.error_message
        return datos


class VoicePipeline:
    """Genera la voz de un guion ya exportado por el modulo 1."""

    def __init__(
        self,
        settings: Settings,
        request: VoiceJobRequest,
        *,
        provider: VoiceProvider | None = None,
        now: datetime | None = None,
    ) -> None:
        self.settings = settings
        self.request = request
        self._provider_override = provider
        self.now = now or utcnow()
        self.fmt = PcmFormat(
            sample_rate_hz=settings.voice_sample_rate_hz,
            channels=settings.voice_channels,
            sample_width_bytes=settings.voice_sample_width_bytes,
        )
        self.data_dir = settings.effective_data_dir(simulation=request.simulation)
        self.issues: list[VoiceIssue] = []
        self.blocking_known = False

    # -- Entrada principal -------------------------------------------------

    def run(self) -> VoiceOutcome:
        document, script_sha = self._load_script()
        self.job_id_cache = document.job_id
        profile = get_profile(document.profile_id, self.settings.profiles_path)
        voice_profile = get_voice_profile(document.profile_id, self.settings.voice_profiles_path)

        try:
            self._gate(document, profile)
        except VoiceInputError as exc:
            # Estado de dominio, no excepcion de configuracion: se devuelve un
            # resumen con el motivo explicito y sin haber emitido nada.
            logger.error("entrada bloqueada (%s): %s", exc.code, exc.message)
            return VoiceOutcome(
                voice_run_id="",
                job_id=document.job_id,
                status="blocked",
                exit_code=exc.exit_code,
                simulation=self.request.simulation,
                error_code=exc.code,
                error_message=exc.message,
                admission_reasons=list(exc.details.get("reasons", [])) or [exc.message],
            )

        voice_id = resolve_voice_id(
            voice_profile, self.settings, simulation=self.request.simulation
        )
        provider = self._provider_override or build_voice_provider(
            simulation=self.request.simulation, settings=self.settings, seed=self.request.seed
        )
        if not self.request.simulation and self._provider_override is None:
            # FFmpeg se comprueba ANTES de gastar ninguna peticion de pago.
            require_ffmpeg(self.settings.ffmpeg_path)

        ensure_free_space(self.data_dir, self.settings.min_free_disk_mb, stage="voz:inicio")

        fingerprint = self._fingerprint(script_sha, voice_id, provider, voice_profile)
        lock_path = self.data_dir / f"voice-{document.job_id}.lock"
        with ProcessLock(lock_path), Storage(self.data_dir) as storage:
            from .storage import VoiceStorage

            self.storage = storage
            self.voice_storage = VoiceStorage(storage)
            self.voice_storage.migrate()

            run_row, reuse = self._get_or_create_run(
                document, script_sha, fingerprint, provider, voice_id
            )
            self.voice_run_id = run_row["voice_run_id"]
            if reuse is not None:
                return reuse

            try:
                return self._generate(document, profile, voice_profile, provider, voice_id, script_sha)
            except ViralgenError as exc:
                self.voice_storage.update_run(
                    self.voice_run_id,
                    status="failed",
                    error_code=exc.code,
                    error_message=exc.message[:2000],
                )
                logger.error("voz %s fallida (%s): %s", self.voice_run_id, exc.code, exc.message)
                presupuesto = getattr(self, "budget", None)
                return VoiceOutcome(
                    voice_run_id=self.voice_run_id,
                    job_id=document.job_id,
                    status="failed",
                    exit_code=exc.exit_code,
                    simulation=self.request.simulation,
                    error_code=exc.code,
                    error_message=exc.message,
                    requests_new=presupuesto.used_this_run if presupuesto else 0,
                    requests_total=self.voice_storage.requests_used(document.job_id),
                )

    # -- Admision ----------------------------------------------------------

    def _load_script(self) -> tuple[ScriptDocument, str]:
        path = self.request.script_path
        try:
            raw = path.read_bytes()
        except FileNotFoundError as exc:
            raise ConfigError(f"No existe el guion: {path}") from exc
        except OSError as exc:
            raise ConfigError(f"No se pudo leer el guion {path}: {exc}") from exc
        try:
            document = ScriptDocument.model_validate(json.loads(raw))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"JSON invalido en {path}: {exc}") from exc
        except Exception as exc:
            raise VoiceInputError(
                f"El guion no cumple el contrato 1.0: {str(exc)[:400]}"
            ) from exc
        return document, sha256_file(path)

    def _gate(self, document: ScriptDocument, profile) -> None:
        """Puerta de admision previa a cualquier peticion.

        En real se exige el criterio completo del modulo 1. En simulacion se
        relaja SOLO `no_es_simulacion`, leyendo las claves del informe; no se
        interpretan los mensajes de texto.
        """
        informe = validate_document(document, profile=profile, allowed_facts=None, promise="")
        admision = check_admission(document, export_complete=True, report=informe)

        if self.request.simulation:
            requeridas = {
                nombre: ok
                for nombre, ok in admision.checks.items()
                if nombre != "no_es_simulacion"
            }
            if all(requeridas.values()):
                return
            fallidas = sorted(nombre for nombre, ok in requeridas.items() if not ok)
            raise VoiceInputError(
                "El guion no supera las comprobaciones estructurales y editoriales "
                f"necesarias ni siquiera en simulacion: {', '.join(fallidas)}.",
                details={"failed_checks": fallidas, "reasons": admision.reasons},
            )

        if not admision.admissible:
            raise VoiceInputError(
                "El guion no esta admitido para producir medios, asi que no se emite "
                "ninguna peticion de pago: " + "; ".join(admision.reasons),
                details={
                    "checks": admision.checks,
                    "reasons": admision.reasons,
                    "hint": "usa --mock para un recorrido de pruebas",
                },
            )

    # -- Idempotencia ------------------------------------------------------

    def _fingerprint(
        self, script_sha: str, voice_id: str, provider: VoiceProvider, voice_profile
    ) -> str:
        return sha256_json(
            {
                "script_sha256": script_sha,
                "provider": provider.name,
                "model_id": provider.model_id,
                "voice_id": voice_id,
                "settings": voice_profile.effective_settings(),
                "output_format": self.settings.voice_output_format,
                "internal_format": self.fmt.describe(),
                "processing_version": VOICE_PROCESSING_VERSION,
                "simulation": self.request.simulation,
                "voice_config": self.settings.voice_hashable_view(),
            }
        )

    def _get_or_create_run(
        self,
        document: ScriptDocument,
        script_sha: str,
        fingerprint: str,
        provider: VoiceProvider,
        voice_id: str,
    ) -> tuple[dict, VoiceOutcome | None]:
        voice_key = self.request.voice_key or f"auto-{uuid.uuid4()}"
        existente = self.voice_storage.get_run_by_key(voice_key)
        if existente is None:
            fila = self.voice_storage.create_run(
                {
                    "voice_run_id": str(uuid.uuid4()),
                    "job_id": document.job_id,
                    "voice_key": voice_key,
                    "fingerprint": fingerprint,
                    "simulation": int(self.request.simulation),
                    "status": "running",
                    "provider": provider.name,
                    "model_id": provider.model_id,
                    "voice_id": voice_id,
                    "script_sha256": script_sha,
                    "script_path": str(self.request.script_path),
                }
            )
            return fila, None

        if existente["fingerprint"] != fingerprint:
            raise IdempotencyConflictError(
                f"La clave --voice-key {voice_key!r} ya existe con otra solicitud "
                f"(ejecucion {existente['voice_run_id']}). Usa otra clave o repite la "
                "misma solicitud.",
                details={"voice_run_id": existente["voice_run_id"], "voice_key": voice_key},
            )

        if existente["status"] in {"ready", "needs_review"}:
            manifiesto = self.voice_storage.load_manifest(existente["voice_run_id"])
            if manifiesto is not None:
                self.voice_run_id = existente["voice_run_id"]
                salida = self._reexport(document, manifiesto)
                if salida is not None:
                    return existente, salida
        self.voice_storage.update_run(existente["voice_run_id"], status="running")
        return existente, None

    def _reexport(self, document: ScriptDocument, manifest_data: dict) -> VoiceOutcome | None:
        """Reexporta desde SQLite sin ninguna peticion nueva.

        Solo sirve si los medios siguen disponibles: un manifiesto sin sus WAV
        no se publica.
        """
        manifest = VoiceManifest.model_validate(manifest_data)
        run_dir = self._run_dir()
        if not (run_dir / manifest.master.path).is_file():
            logger.warning("faltan los medios de %s: se regenera", self.voice_run_id)
            return None
        for escena in manifest.scenes:
            if not (run_dir / escena.clip_path).is_file():
                logger.warning("falta el clip %s: se regenera", escena.scene_id)
                return None

        ruta = self._export_manifest(manifest)
        totales = self.voice_storage.request_totals(document.job_id)
        return VoiceOutcome(
            voice_run_id=self.voice_run_id,
            job_id=document.job_id,
            status=manifest.control.voice_status.value,
            exit_code=(
                ExitCode.OK
                if manifest.control.voice_status is VoiceStatus.READY
                else ExitCode.NEEDS_REVIEW
            ),
            simulation=manifest.simulation,
            manifest_path=str(ruta),
            master_path=str(run_dir / manifest.master.path),
            measured_duration_s=manifest.master.actual_duration_s,
            admissible_for_assembly=manifest.control.admissible_for_assembly,
            admission_reasons=[],
            issues=[issue.model_dump(mode="json") for issue in manifest.control.issues],
            requests_new=0,
            requests_total=int(totales["requests"]),
            reused=True,
        )

    # -- Generacion --------------------------------------------------------

    def _run_dir(self) -> Path:
        base = self.data_dir / "jobs" / self.job_id_cache / "voice" / self.voice_run_id
        return ensure_within(self.data_dir, base)

    def _generate(
        self,
        document: ScriptDocument,
        profile,
        voice_profile,
        provider: VoiceProvider,
        voice_id: str,
        script_sha: str,
    ) -> VoiceOutcome:
        run_dir = self._run_dir()
        clips_dir = run_dir / "audio" / "scenes"
        temp_dir = run_dir / "tmp"
        clips_dir.mkdir(parents=True, exist_ok=True)

        self.budget = VoiceBudget(
            max_requests=self.settings.voice_max_requests_per_job,
            used_total=self.voice_storage.requests_used(document.job_id),
            reserve=lambda kind, scene_id: self.voice_storage.reserve_request(
                voice_run_id=self.voice_run_id,
                job_id=document.job_id,
                kind=kind,
                scene_id=scene_id,
            ),
            settle=self.voice_storage.settle_request,
        )

        guardados = self.voice_storage.load_clips(self.voice_run_id)
        escenas = sorted(document.scenes, key=lambda item: item.order)
        clip_info: dict[str, dict] = {}

        for indice, escena in enumerate(escenas):
            peticion = SynthesisRequest(
                scene_id=escena.scene_id,
                text=escena.narration_text,
                previous_text=escenas[indice - 1].narration_text if indice > 0 else None,
                next_text=(
                    escenas[indice + 1].narration_text if indice + 1 < len(escenas) else None
                ),
                voice_id=voice_id,
                model_id=provider.model_id,
                output_format=self.settings.voice_output_format,
                settings=voice_profile.effective_settings(),
                hint_speech_duration_s=60.0 * escena.word_count / profile.target_wpm,
            )
            identidad = sha256_json(peticion.identity_payload())
            clip_rel = f"audio/scenes/{escena.scene_id}.wav"
            clip_path = run_dir / clip_rel

            reutilizado = guardados.get(escena.scene_id)
            if (
                reutilizado is not None
                and reutilizado["identity_sha256"] == identidad
                and clip_path.is_file()
                and sha256_file(clip_path) == reutilizado["clip_sha256"]
            ):
                logger.info("escena %s reutilizada del disco", escena.scene_id)
                clip_info[escena.scene_id] = {
                    "path": clip_rel,
                    "sha256": reutilizado["clip_sha256"],
                    "samples": int(reutilizado["clip_samples"]),
                    "alignment": reutilizado["alignment"],
                    "request_id": reutilizado["request_id"],
                    "characters_sent": int(reutilizado["characters_sent"]),
                }
                continue

            resultado = provider.synthesize(peticion, self.budget)
            temp_dir.mkdir(parents=True, exist_ok=True)
            bruto = temp_dir / f"{escena.scene_id}.{resultado.audio_format}"
            bruto.write_bytes(resultado.audio_bytes)
            resultado.audio_bytes = b""  # se descarta enseguida
            provider.decode_to_internal(resultado, bruto, clip_path)
            bruto.unlink(missing_ok=True)

            info = read_wav_info(clip_path)
            clip_sha = sha256_file(clip_path)
            alineacion = {
                "alignment": _alignment_to_dict(resultado.alignment),
                "normalized_alignment": _alignment_to_dict(resultado.normalized_alignment),
            }
            self.voice_storage.save_clip(
                self.voice_run_id,
                {
                    "scene_id": escena.scene_id,
                    "identity_sha256": identidad,
                    "clip_path": clip_rel,
                    "clip_sha256": clip_sha,
                    "clip_samples": info.sample_count,
                    "alignment": alineacion,
                    "request_id": resultado.request_id,
                    "characters_sent": resultado.characters_sent,
                },
            )
            clip_info[escena.scene_id] = {
                "path": clip_rel,
                "sha256": clip_sha,
                "samples": info.sample_count,
                "alignment": alineacion,
                "request_id": resultado.request_id,
                "characters_sent": resultado.characters_sent,
            }

        # --- Medicion y maestro ------------------------------------------
        medidas = build_measured_timeline(
            [
                (
                    escena.scene_id,
                    escena.order,
                    clip_info[escena.scene_id]["samples"],
                    pause_samples(escena.pause_after_s, self.fmt.sample_rate_hz),
                )
                for escena in escenas
            ]
        )
        master_rel = "audio/narration.wav"
        master_path = run_dir / master_rel
        escritas = write_wav(master_path, self._master_chunks(run_dir, escenas, medidas, clip_info), self.fmt)
        esperadas = total_samples(medidas)
        if escritas != esperadas:
            raise VoiceAudioMismatch(
                f"narration.wav tiene {escritas} muestras y se esperaban {esperadas}."
            )
        master_sha = sha256_file(master_path)
        duracion = esperadas / self.fmt.sample_rate_hz

        # --- Alineacion ---------------------------------------------------
        palabras, escenas_manifiesto = self._align(
            escenas, medidas, clip_info, run_dir, provider
        )

        # --- Duracion -----------------------------------------------------
        objetivo = document.video.target_duration_s
        if abs(duracion - objetivo) > objetivo * DURATION_TOLERANCE:
            self._issue(
                "duracion_fuera_de_tolerancia",
                f"la voz mide {duracion:.2f} s y el objetivo es {objetivo:.2f} s "
                f"(tolerancia {DURATION_TOLERANCE:.0%}); ajusta el texto, no la velocidad",
                IssueSeverity.ERROR,
                blocking=True,
            )
        if not (profile.min_duration_s <= duracion <= profile.max_duration_s):
            self._issue(
                "duracion_fuera_del_perfil",
                f"la voz mide {duracion:.2f} s, fuera del rango del perfil "
                f"({profile.min_duration_s:g}-{profile.max_duration_s:g} s)",
                IssueSeverity.ERROR,
                blocking=True,
            )

        # --- Sonido opcional ----------------------------------------------
        tiempos = {
            medida.scene_id: SceneTiming(
                scene_id=medida.scene_id,
                start_s=medida.start_s(self.fmt.sample_rate_hz),
                clip_end_s=medida.clip_end_s(self.fmt.sample_rate_hz),
                end_s=medida.end_s(self.fmt.sample_rate_hz),
            )
            for medida in medidas
        }
        plan = plan_sound(
            document,
            tiempos,
            catalog=load_sound_assets(self.settings.sound_assets_path),
            voice_profile=voice_profile,
            enable_sfx=self.settings.voice_enable_sfx,
            enable_music=self.settings.voice_enable_music,
            master_duration_s=duracion,
        )
        self.issues.extend(plan.issues)
        # Solo se copian los assets realmente usados, junto al manifiesto.
        for destino_relativo, origen in plan.files_to_copy:
            destino = run_dir / destino_relativo
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origen, destino)
        assets: list[SoundAssetRef] = list(plan.assets)

        # --- Limite de almacenamiento -------------------------------------
        ocupado = directory_size_mb(run_dir)
        if ocupado > self.settings.voice_max_job_storage_mb:
            raise DiskSpaceError(
                f"El trabajo de voz ocupa {ocupado:.0f} MiB y el limite es "
                f"{self.settings.voice_max_job_storage_mb} MiB.",
                details={"run_dir": str(run_dir)},
            )
        ensure_free_space(self.data_dir, self.settings.min_free_disk_mb, stage="voz:exportacion")

        # --- Manifiesto ----------------------------------------------------
        bloqueado = any(issue.blocking for issue in self.issues)
        estado = VoiceStatus.NEEDS_REVIEW if bloqueado else VoiceStatus.READY
        admisible = (
            estado is VoiceStatus.READY
            and not self.request.simulation
            and document.simulation is False
        )
        totales = self.voice_storage.request_totals(document.job_id)
        caracteres_run = sum(
            dato["characters_sent"] for dato in clip_info.values()
        )

        manifest = VoiceManifest(
            voice_run_id=self.voice_run_id,
            job_id=document.job_id,
            created_at=self.now,
            simulation=self.request.simulation,
            source=VoiceSource(
                source_script_schema_version=document.schema_version,
                source_script_sha256=script_sha,
                source_profile_id=document.profile_id,
                source_channel=document.channel.value,
                source_simulation=document.simulation,
                source_production_status=document.control.production_status.value,
            ),
            provider=VoiceProviderInfo(
                name=provider.name,
                model_id=provider.model_id,
                voice_id=voice_id,
                output_format=self.settings.voice_output_format,
                internal_format=(
                    f"{self.fmt.codec} {self.fmt.sample_rate_hz} Hz "
                    f"{self.fmt.channels}ch"
                ),
                effective_settings=dict(voice_profile.effective_settings()),
                settings_hash=voice_profile.settings_hash(),
                processing_version=VOICE_PROCESSING_VERSION,
            ),
            control=VoiceControl(
                voice_status=estado,
                issues=list(self.issues),
                admissible_for_assembly=admisible,
            ),
            master=VoiceMaster(
                path=master_rel,
                sha256=master_sha,
                codec=self.fmt.codec,
                sample_rate_hz=self.fmt.sample_rate_hz,
                channels=self.fmt.channels,
                sample_count=esperadas,
                actual_duration_s=round(duracion, 6),
            ),
            scenes=escenas_manifiesto,
            words=palabras,
            sound=VoiceSound(cues=plan.cues, assets=assets),
            usage=VoiceUsage(
                requests_total=int(totales["requests"]),
                requests_this_run=self.budget.used_this_run,
                characters_sent_total=int(totales["characters"]),
                characters_sent_this_run=caracteres_run,
                request_ids=self.voice_storage.request_ids(document.job_id)[:100],
                latency_ms_total=int(totales["latency_ms"]),
                estimated_cost_usd=self._cost(int(totales["characters"])),
                billing_observed=False,
            ),
        )

        self.voice_storage.save_manifest(
            self.voice_run_id, manifest.model_dump(mode="json"), estado.value
        )
        self.voice_storage.update_run(
            self.voice_run_id, status=estado.value, error_code=None, error_message=None
        )
        ruta = self._export_manifest(manifest)

        # Temporales regenerables fuera; maestro, clips y manifiesto se quedan.
        shutil.rmtree(temp_dir, ignore_errors=True)

        return VoiceOutcome(
            voice_run_id=self.voice_run_id,
            job_id=document.job_id,
            status=estado.value,
            exit_code=ExitCode.OK if estado is VoiceStatus.READY else ExitCode.NEEDS_REVIEW,
            simulation=self.request.simulation,
            manifest_path=str(ruta),
            master_path=str(master_path),
            measured_duration_s=round(duracion, 3),
            admissible_for_assembly=admisible,
            admission_reasons=[] if admisible else _reasons(self.issues, self.request.simulation),
            issues=[issue.model_dump(mode="json") for issue in self.issues],
            requests_new=self.budget.used_this_run,
            requests_total=int(totales["requests"]),
        )

    # -- Piezas ------------------------------------------------------------

    def _master_chunks(self, run_dir: Path, escenas, medidas, clip_info):
        """Concatena clips y pausas por bloques, sin cargar todo en memoria."""
        for escena, medida in zip(escenas, medidas, strict=True):
            yield from iter_wav_frames(run_dir / clip_info[escena.scene_id]["path"])
            if medida.pause_samples:
                yield silence_bytes(medida.pause_samples, self.fmt)

    def _align(self, escenas, medidas, clip_info, run_dir: Path, provider: VoiceProvider):
        palabras: list[VoiceWord] = []
        manifiesto: list[VoiceScene] = []
        tolerancia = self.settings.voice_alignment_tolerance_ms / 1000.0

        for escena, medida in zip(escenas, medidas, strict=True):
            datos = clip_info[escena.scene_id]
            duracion_clip = datos["samples"] / self.fmt.sample_rate_hz
            resultado = self._resolve_alignment(
                escena, datos, duracion_clip, tolerancia, run_dir, provider
            )

            if resultado.usable:
                destacadas = emphasis_set(list(escena.captions.emphasis_words))
                desplazamiento = medida.start_s(self.fmt.sample_rate_hz)
                for indice, palabra in enumerate(resultado.words):
                    palabras.append(
                        VoiceWord(
                            scene_id=escena.scene_id,
                            word_index=indice,
                            text=palabra.text,
                            char_start=palabra.char_start,
                            char_end=palabra.char_end,
                            start_s=round(desplazamiento + palabra.start_s, 6),
                            end_s=round(desplazamiento + palabra.end_s, 6),
                            emphasis=is_emphasis(palabra.text, destacadas),
                        )
                    )
            else:
                self.blocking_known = True
                self._issue(
                    "alineacion_no_utilizable",
                    f"{escena.scene_id}: " + ("; ".join(resultado.issues) or "sin alineacion"),
                    IssueSeverity.ERROR,
                    blocking=True,
                    scene_id=escena.scene_id,
                )
            for ajuste in resultado.adjustments:
                self._issue(
                    "ajuste_de_alineacion",
                    f"{escena.scene_id}: {ajuste}",
                    IssueSeverity.INFO,
                    blocking=False,
                    scene_id=escena.scene_id,
                )

            normalizado = None
            bruto = (datos.get("alignment") or {}).get("normalized_alignment")
            if bruto and bruto.get("characters"):
                normalizado = "".join(bruto["characters"])[:600]

            manifiesto.append(
                VoiceScene(
                    scene_id=escena.scene_id,
                    order=escena.order,
                    source_text=escena.narration_text,
                    source_text_sha256=sha256_text(escena.narration_text),
                    clip_path=datos["path"],
                    clip_sha256=datos["sha256"],
                    clip_samples=datos["samples"],
                    pause_samples=medida.pause_samples,
                    start_sample=medida.start_sample,
                    end_sample=medida.end_sample,
                    start_s=round(medida.start_s(self.fmt.sample_rate_hz), 6),
                    clip_end_s=round(medida.clip_end_s(self.fmt.sample_rate_hz), 6),
                    end_s=round(medida.end_s(self.fmt.sample_rate_hz), 6),
                    actual_duration_s=round(
                        medida.actual_duration_s(self.fmt.sample_rate_hz), 6
                    ),
                    alignment_method=resultado.method,
                    alignment_status=resultado.status,
                    provider_normalized_text=normalizado,
                    request_id=datos.get("request_id"),
                    characters_sent=int(datos.get("characters_sent", 0)),
                )
            )
        return palabras, manifiesto

    def _resolve_alignment(
        self, escena, datos, duracion_clip: float, tolerancia: float, run_dir: Path, provider
    ) -> AlignmentResult:
        """Elige la mejor alineacion disponible para una escena."""
        guardada = datos.get("alignment") or {}
        principal = _alignment_from_dict(guardada.get("alignment"))
        normalizada = _alignment_from_dict(guardada.get("normalized_alignment"))

        if principal is not None:
            resultado = build_alignment(
                principal,
                escena.narration_text,
                clip_duration_s=duracion_clip,
                tolerance_s=tolerancia,
                method=AlignmentMethod.PROVIDER_ALIGNMENT,
            )
            if resultado.usable:
                return resultado
            fallo = resultado
        else:
            fallo = AlignmentResult(
                method=AlignmentMethod.NONE,
                status=AlignmentStatus.MISSING,
                issues=["el proveedor no devolvio alineacion"],
            )

        if normalizada is not None:
            # Solo sustituye si se puede relacionar sin ambiguedad con el texto
            # fuente: nunca se asume que "12 km" y "doce kilometros" se
            # correspondan uno a uno.
            resultado = build_alignment(
                normalizada,
                escena.narration_text,
                clip_duration_s=duracion_clip,
                tolerance_s=tolerancia,
                method=AlignmentMethod.PROVIDER_NORMALIZED_ALIGNMENT,
            )
            if resultado.usable:
                return resultado

        if (
            self.settings.voice_allow_forced_alignment
            and provider.supports_forced_alignment
            and not self.blocking_known
            and self.budget.can_spend()
        ):
            try:
                forzada = provider.force_align(
                    run_dir / datos["path"], escena.narration_text, self.budget
                )
            except ViralgenError as exc:
                fallo.issues.append(f"la alineacion forzada fallo: {exc.message[:200]}")
                forzada = None
            if forzada is not None:
                resultado = build_alignment(
                    forzada,
                    escena.narration_text,
                    clip_duration_s=duracion_clip,
                    tolerance_s=tolerancia,
                    method=AlignmentMethod.FORCED_ALIGNMENT,
                )
                # Una puntuacion del proveedor NO es una probabilidad de acierto:
                # el resultado se vuelve a validar igual que cualquier otro.
                if resultado.usable:
                    return resultado
                fallo = resultado
        return fallo

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
            VoiceIssue(
                code=code,
                message=message[:200],
                severity=severity,
                blocking=blocking,
                scene_id=scene_id,
            )
        )

    def _cost(self, characters: int) -> float | None:
        """Coste estimado local. Sin tarifa configurada, None."""
        tarifa = self.settings.voice_pricing()
        if tarifa is None:
            return None
        return round(characters * tarifa / 1000.0, 6)

    def _export_manifest(self, manifest: VoiceManifest) -> Path:
        run_dir = self._run_dir()
        ruta = run_dir / "voice.json"
        datos = manifest.model_dump(mode="json")
        try:
            atomic_write_json(ruta, datos)
        except OSError as exc:
            self.voice_storage.record_export(
                self.voice_run_id, path=ruta, sha256="", status="failed"
            )
            raise ViralgenError(
                f"No se pudo escribir {ruta}: {exc}. El manifiesto sigue en SQLite: "
                "repite el comando con la misma --voice-key para rehacerlo sin "
                "sintetizar de nuevo.",
                details={"path": str(ruta)},
            ) from exc
        self.voice_storage.record_export(
            self.voice_run_id,
            path=ruta,
            sha256=sha256_text(json.dumps(datos, ensure_ascii=False, sort_keys=True)),
            status="ok",
        )
        return ruta


# ---------------------------------------------------------------------------
# Errores y ayudas
# ---------------------------------------------------------------------------


class VoiceInputError(ViralgenError):
    """El guion de entrada no sirve: no se emite ninguna peticion."""

    exit_code = ExitCode.VALIDATION
    code = "voice_input_not_admitted"


class VoiceAudioMismatch(ViralgenError):
    exit_code = ExitCode.VALIDATION
    code = "voice_audio_mismatch"


def _alignment_to_dict(alignment: CharAlignment | None) -> dict | None:
    if alignment is None:
        return None
    return {
        "characters": list(alignment.characters),
        "character_start_times_seconds": list(alignment.start_times_s),
        "character_end_times_seconds": list(alignment.end_times_s),
    }


def _alignment_from_dict(raw: Any) -> CharAlignment | None:
    if not isinstance(raw, dict):
        return None
    caracteres = raw.get("characters")
    inicios = raw.get("character_start_times_seconds")
    finales = raw.get("character_end_times_seconds")
    if not isinstance(caracteres, list) or not isinstance(inicios, list):
        return None
    if not isinstance(finales, list):
        return None
    return CharAlignment([str(c) for c in caracteres], [float(v) for v in inicios], [float(v) for v in finales])


def _reasons(issues: list[VoiceIssue], simulation: bool) -> list[str]:
    motivos = [issue.message for issue in issues if issue.blocking]
    if simulation:
        motivos.append(
            "simulation=true: una voz simulada nunca alimenta el montaje"
        )
    return motivos
