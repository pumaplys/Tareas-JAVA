"""Admision de voz: la puerta de entrada del modulo 4.

NO se confia en el booleano guardado en `voice.json`. Esta comprobacion
revalida desde los archivos reales: el guion y su hash, el manifiesto, los
hashes y el formato de los WAV, la cobertura de escenas, las alineaciones y el
origen (real o simulado) de AMBOS artefactos.

Un hash demuestra correspondencia entre archivos, no autoria. Por eso ademas
de comparar hashes se vuelve a validar el guion con las reglas del modulo 1.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..diskutil import sha256_file
from ..profiles import get_profile
from ..schemas.document import ScriptDocument
from ..textutil import sha256_text
from ..validation import check_admission, validate_document
from .alignment import word_spans_of
from .audio import PcmFormat, read_wav_info
from .schemas import VoiceManifest, VoiceStatus
from .timing import DURATION_TOLERANCE

#: Margen al comparar segundos derivados de muestras.
TIME_EPSILON_S = 0.002


@dataclass
class VoiceAdmissionReport:
    """Resultado de la admision para montaje."""

    admissible: bool
    checks: dict[str, bool] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    contract_valid: bool = False
    measured_duration_s: float | None = None

    def to_dict(self) -> dict:
        return {
            "admissible_for_assembly": self.admissible,
            "contract_valid": self.contract_valid,
            "checks": dict(self.checks),
            "reasons": list(self.reasons),
            "measured_duration_s": self.measured_duration_s,
        }


def check_voice_admission(
    *,
    script_path: Path,
    manifest_path: Path,
    settings: Any,
    require_real: bool = True,
) -> VoiceAdmissionReport:
    """Comprueba que la pareja (guion, manifiesto) sirve para montar.

    `require_real=False` permite auditar un recorrido simulado sin exigir
    `simulation=false`; el resto de comprobaciones son las mismas.
    """
    checks: dict[str, bool] = {}
    reasons: list[str] = []

    def fallo(nombre: str, motivo: str) -> None:
        checks[nombre] = False
        reasons.append(motivo)

    # --- Guion ------------------------------------------------------------
    try:
        script_bytes = script_path.read_bytes()
    except OSError as exc:
        return VoiceAdmissionReport(
            admissible=False,
            checks={"guion_legible": False},
            reasons=[f"no se pudo leer el guion: {exc}"],
        )
    checks["guion_legible"] = True
    # SHA-256 de los BYTES exactos del archivo, no de su texto reserializado.
    script_sha = sha256_file(script_path)

    try:
        document = ScriptDocument.model_validate(json.loads(script_bytes))
    except Exception as exc:
        return VoiceAdmissionReport(
            admissible=False,
            checks={**checks, "guion_valido": False},
            reasons=[f"el guion no cumple el esquema: {str(exc)[:300]}"],
        )
    checks["guion_valido"] = True

    profile = get_profile(document.profile_id, settings.profiles_path)
    informe_guion = validate_document(document, profile=profile, allowed_facts=None, promise="")
    admision_guion = check_admission(document, export_complete=True, report=informe_guion)
    if require_real:
        checks["guion_admitido"] = admision_guion.admissible
        if not admision_guion.admissible:
            reasons.extend(f"guion: {motivo}" for motivo in admision_guion.reasons)
    else:
        sin_simulacion = {
            nombre: ok
            for nombre, ok in admision_guion.checks.items()
            if nombre != "no_es_simulacion"
        }
        checks["guion_admitido"] = all(sin_simulacion.values())
        if not checks["guion_admitido"]:
            reasons.append("guion: no supera sus propias comprobaciones editoriales")

    # --- Manifiesto -------------------------------------------------------
    try:
        manifest_raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = VoiceManifest.model_validate(manifest_raw)
    except Exception as exc:
        return VoiceAdmissionReport(
            admissible=False,
            checks={**checks, "manifiesto_valido": False},
            reasons=[*reasons, f"el manifiesto no cumple el contrato: {str(exc)[:300]}"],
        )
    checks["manifiesto_valido"] = True

    contract_valid = True
    base = manifest_path.parent

    checks["mismo_job"] = manifest.job_id == document.job_id
    if not checks["mismo_job"]:
        reasons.append("el manifiesto apunta a otro job_id")

    checks["hash_del_guion"] = manifest.source.source_script_sha256 == script_sha
    if not checks["hash_del_guion"]:
        reasons.append(
            "el guion ha cambiado desde que se genero la voz: el SHA-256 no coincide"
        )

    # --- Maestro ----------------------------------------------------------
    fmt = PcmFormat(
        sample_rate_hz=settings.voice_sample_rate_hz,
        channels=settings.voice_channels,
        sample_width_bytes=settings.voice_sample_width_bytes,
    )
    master_path = base / manifest.master.path
    if not master_path.is_file():
        fallo("maestro_presente", f"falta la narracion completa: {manifest.master.path}")
    else:
        checks["maestro_presente"] = True
        if sha256_file(master_path) != manifest.master.sha256:
            fallo("maestro_integro", "el hash de narration.wav no coincide con el manifiesto")
        else:
            checks["maestro_integro"] = True
        info = read_wav_info(master_path)
        formato_ok = info.matches(fmt) and manifest.master.sample_rate_hz == fmt.sample_rate_hz
        checks["maestro_formato"] = formato_ok
        if not formato_ok:
            reasons.append("narration.wav no esta en el formato interno declarado")
        muestras_ok = info.sample_count == manifest.master.sample_count
        checks["maestro_muestras"] = muestras_ok
        if not muestras_ok:
            reasons.append(
                f"narration.wav tiene {info.sample_count} muestras y el manifiesto declara "
                f"{manifest.master.sample_count}"
            )

    # --- Escenas ----------------------------------------------------------
    escenas_guion = {escena.scene_id: escena for escena in document.scenes}
    checks["escenas_completas"] = {e.scene_id for e in manifest.scenes} == set(escenas_guion)
    if not checks["escenas_completas"]:
        reasons.append("el manifiesto no cubre exactamente las escenas del guion")

    cursor = 0
    clips_ok = True
    cobertura_ok = True
    for escena in sorted(manifest.scenes, key=lambda item: item.order):
        clip_path = base / escena.clip_path
        if not clip_path.is_file():
            clips_ok = False
            reasons.append(f"falta el clip de {escena.scene_id}")
            continue
        if sha256_file(clip_path) != escena.clip_sha256:
            clips_ok = False
            reasons.append(f"el hash del clip de {escena.scene_id} no coincide")
            continue
        info_clip = read_wav_info(clip_path)
        if not info_clip.matches(fmt) or info_clip.sample_count != escena.clip_samples:
            clips_ok = False
            reasons.append(
                f"el clip de {escena.scene_id} no coincide en formato o numero de muestras"
            )
            continue
        if escena.start_sample != cursor:
            cobertura_ok = False
            reasons.append(
                f"hay un hueco o solapamiento antes de {escena.scene_id}: "
                f"empieza en {escena.start_sample} y se esperaba {cursor}"
            )
        if escena.end_sample != escena.start_sample + escena.clip_samples + escena.pause_samples:
            cobertura_ok = False
            reasons.append(f"los limites de {escena.scene_id} no cuadran con sus muestras")
        fuente = escenas_guion.get(escena.scene_id)
        if fuente is not None and escena.source_text_sha256 != sha256_text(fuente.narration_text):
            cobertura_ok = False
            reasons.append(f"el texto de {escena.scene_id} no es el del guion")
        cursor = escena.end_sample

    checks["clips_integros"] = clips_ok
    checks["cobertura_sin_huecos"] = cobertura_ok
    if cobertura_ok and cursor != manifest.master.sample_count:
        checks["cobertura_sin_huecos"] = False
        reasons.append(
            f"las escenas suman {cursor} muestras y el maestro declara "
            f"{manifest.master.sample_count}"
        )

    # --- Palabras ---------------------------------------------------------
    if manifest.control.voice_status is VoiceStatus.READY:
        palabras_ok = True
        por_escena: dict[str, list] = {}
        for palabra in manifest.words:
            por_escena.setdefault(palabra.scene_id, []).append(palabra)
        for escena in manifest.scenes:
            fuente = escenas_guion.get(escena.scene_id)
            if fuente is None:
                continue
            esperadas = len(word_spans_of(fuente.narration_text))
            obtenidas = por_escena.get(escena.scene_id, [])
            if len(obtenidas) != esperadas:
                palabras_ok = False
                reasons.append(
                    f"{escena.scene_id}: {len(obtenidas)} palabras alineadas frente a "
                    f"{esperadas} narradas"
                )
                continue
            for palabra in obtenidas:
                if palabra.start_s < escena.start_s - TIME_EPSILON_S:
                    palabras_ok = False
                    reasons.append(f"{escena.scene_id}: una palabra empieza antes de la escena")
                    break
                if palabra.end_s > escena.clip_end_s + TIME_EPSILON_S:
                    palabras_ok = False
                    reasons.append(
                        f"{escena.scene_id}: una palabra invade el silencio anadido"
                    )
                    break
        checks["palabras_cubren_la_narracion"] = palabras_ok
    else:
        checks["palabras_cubren_la_narracion"] = False
        reasons.append("voice_status=needs_review: no hay alineacion utilizable garantizada")

    # --- Estado y origen --------------------------------------------------
    checks["voz_lista"] = manifest.control.voice_status is VoiceStatus.READY
    if not checks["voz_lista"]:
        reasons.append("voice_status no es ready")

    sin_bloqueos = not any(issue.blocking for issue in manifest.control.issues)
    checks["sin_bloqueos"] = sin_bloqueos
    if not sin_bloqueos:
        reasons.append("el manifiesto conserva incidencias bloqueantes")

    if require_real:
        checks["voz_real"] = manifest.simulation is False
        if not checks["voz_real"]:
            reasons.append("simulation=true en la voz: una simulacion nunca alimenta montaje")
        checks["guion_real"] = manifest.source.source_simulation is False
        if not checks["guion_real"]:
            reasons.append("el guion de origen es simulado")

    # --- Duracion ---------------------------------------------------------
    medida = manifest.master.actual_duration_s
    objetivo = document.video.target_duration_s
    dentro_rango = profile.min_duration_s <= medida <= profile.max_duration_s
    dentro_tolerancia = abs(medida - objetivo) <= objetivo * DURATION_TOLERANCE
    checks["duracion_en_rango"] = dentro_rango
    checks["duracion_en_tolerancia"] = dentro_tolerancia
    if not dentro_rango:
        reasons.append(
            f"la duracion medida ({medida:.2f} s) esta fuera del rango del perfil "
            f"({profile.min_duration_s:g}-{profile.max_duration_s:g} s)"
        )
    if not dentro_tolerancia:
        reasons.append(
            f"la duracion medida ({medida:.2f} s) se aleja mas de un "
            f"{DURATION_TOLERANCE:.0%} del objetivo ({objetivo:.2f} s)"
        )

    return VoiceAdmissionReport(
        admissible=all(checks.values()),
        checks=checks,
        reasons=reasons,
        contract_valid=contract_valid,
        measured_duration_s=medida,
    )
