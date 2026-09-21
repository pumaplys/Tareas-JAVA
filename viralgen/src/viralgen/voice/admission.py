"""Admision de voz: la puerta de entrada del modulo 4.

NO se confia en el booleano guardado en `voice.json`. Esta comprobacion
revalida desde los archivos reales: el guion y su hash, el manifiesto, los
hashes y el formato de los WAV, la cobertura de escenas, las alineaciones y el
origen (real o simulado) de AMBOS artefactos.

Un hash demuestra correspondencia entre archivos, no autoria. Por eso ademas
de comparar hashes se vuelve a validar el guion con las reglas del modulo 1.

TRES RESULTADOS INDEPENDIENTES, nunca uno solo:

* ``contract_valid``          - los archivos cumplen su contrato y se pueden
  leer. Es lo minimo; no autoriza nada.
* ``admissible_for_preview``  - ademas, todo cuadra: hashes, medios, cobertura,
  palabras y duracion. Sirve para CI y para revisar un recorrido de PRUEBAS.
  Ignora UNICAMENTE el origen (`ORIGIN_CHECKS`).
* ``admissible_for_assembly`` - lo anterior Y ademas ambos artefactos son
  reales. Es el unico que autoriza producir medios.

Una salida simulada conserva siempre `simulation=true` y
`admissible_for_assembly=false`: que su recorrido de pruebas sea correcto no la
convierte en material de produccion. Los modulos posteriores deben leer
`admissible_for_assembly`, nunca el codigo de salida de una validacion de
pruebas.

Esta comprobacion es de SOLO LECTURA: no modifica `script.json` ni
`voice.json`, no cambia el origen declarado y no puede hacer que un consumidor
real acepte senales de prueba.
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


#: Comprobaciones que solo miran el ORIGEN de los artefactos. Son las unicas
#: que la admision de pruebas ignora; cualquier otra falla en ambos modos.
ORIGIN_CHECKS: frozenset[str] = frozenset({"voz_real", "guion_real"})


@dataclass
class VoiceAdmissionReport:
    """Resultado de la auditoria, con los tres veredictos separados."""

    checks: dict[str, bool] = field(default_factory=dict)
    failures: list[tuple[str, str]] = field(default_factory=list)
    contract_valid: bool = False
    measured_duration_s: float | None = None

    @property
    def admissible_for_assembly(self) -> bool:
        """Unico veredicto que autoriza producir medios."""
        return self.contract_valid and all(self.checks.values())

    @property
    def admissible_for_preview(self) -> bool:
        """Veredicto para CI y revision de recorridos de prueba."""
        return self.contract_valid and all(
            ok for nombre, ok in self.checks.items() if nombre not in ORIGIN_CHECKS
        )

    @property
    def reasons(self) -> list[str]:
        """Motivos que impiden la admision para montaje."""
        return [motivo for _nombre, motivo in self.failures]

    @property
    def preview_reasons(self) -> list[str]:
        """Motivos que impiden incluso la admision de pruebas."""
        return [
            motivo for nombre, motivo in self.failures if nombre not in ORIGIN_CHECKS
        ]

    def to_dict(self) -> dict:
        return {
            "contract_valid": self.contract_valid,
            "admissible_for_preview": self.admissible_for_preview,
            "admissible_for_assembly": self.admissible_for_assembly,
            "checks": dict(self.checks),
            "origin_checks": sorted(ORIGIN_CHECKS),
            "reasons": self.reasons,
            "preview_reasons": self.preview_reasons,
            "measured_duration_s": self.measured_duration_s,
        }


def check_voice_admission(
    *,
    script_path: Path,
    manifest_path: Path,
    settings: Any,
) -> VoiceAdmissionReport:
    """Audita la pareja (guion, manifiesto) desde los archivos reales.

    Siempre calcula lo mismo y devuelve los tres veredictos por separado: es
    quien llama (la CLI, el modulo 4) el que decide cual mirar. No hay modo
    "relajado" que devuelva un unico booleano ambiguo.
    """
    informe = VoiceAdmissionReport()

    def registrar(nombre: str, ok: bool, motivo: str = "") -> bool:
        informe.checks[nombre] = ok
        if not ok:
            informe.failures.append((nombre, motivo))
        return ok

    # --- Guion ------------------------------------------------------------
    try:
        script_path.read_bytes()
    except OSError as exc:
        registrar("guion_legible", False, f"no se pudo leer el guion: {exc}")
        return informe
    registrar("guion_legible", True)
    # SHA-256 de los BYTES exactos del archivo, no de su texto reserializado.
    script_sha = sha256_file(script_path)

    try:
        document = ScriptDocument.model_validate(
            json.loads(script_path.read_text(encoding="utf-8"))
        )
    except Exception as exc:
        registrar("guion_valido", False, f"el guion no cumple el esquema: {str(exc)[:300]}")
        return informe
    registrar("guion_valido", True)

    profile = get_profile(document.profile_id, settings.profiles_path)
    informe_guion = validate_document(document, profile=profile, allowed_facts=None, promise="")
    admision_guion = check_admission(document, export_complete=True, report=informe_guion)

    # Las comprobaciones estructurales y editoriales del modulo 1 valen en los
    # dos modos; el origen simulado se separa aparte, como `guion_real`.
    sin_origen = {
        nombre: ok
        for nombre, ok in admision_guion.checks.items()
        if nombre != "no_es_simulacion"
    }
    registrar(
        "guion_admitido",
        all(sin_origen.values()),
        "guion: no supera sus propias comprobaciones estructurales o editoriales ("
        + ", ".join(sorted(nombre for nombre, ok in sin_origen.items() if not ok))
        + ")",
    )

    # --- Manifiesto -------------------------------------------------------
    try:
        manifest = VoiceManifest.model_validate(
            json.loads(manifest_path.read_text(encoding="utf-8"))
        )
    except Exception as exc:
        registrar(
            "manifiesto_valido",
            False,
            f"el manifiesto no cumple el contrato: {str(exc)[:300]}",
        )
        return informe
    registrar("manifiesto_valido", True)
    informe.contract_valid = True
    base = manifest_path.parent

    registrar(
        "mismo_job",
        manifest.job_id == document.job_id,
        "el manifiesto apunta a otro job_id",
    )
    registrar(
        "hash_del_guion",
        manifest.source.source_script_sha256 == script_sha,
        "el guion ha cambiado desde que se genero la voz: el SHA-256 no coincide",
    )

    # --- Maestro ----------------------------------------------------------
    fmt = PcmFormat(
        sample_rate_hz=settings.voice_sample_rate_hz,
        channels=settings.voice_channels,
        sample_width_bytes=settings.voice_sample_width_bytes,
    )
    master_path = base / manifest.master.path
    if registrar(
        "maestro_presente",
        master_path.is_file(),
        f"falta la narracion completa: {manifest.master.path}",
    ):
        registrar(
            "maestro_integro",
            sha256_file(master_path) == manifest.master.sha256,
            "el hash de narration.wav no coincide con el manifiesto",
        )
        info = read_wav_info(master_path)
        registrar(
            "maestro_formato",
            info.matches(fmt) and manifest.master.sample_rate_hz == fmt.sample_rate_hz,
            "narration.wav no esta en el formato interno declarado",
        )
        registrar(
            "maestro_muestras",
            info.sample_count == manifest.master.sample_count,
            f"narration.wav tiene {info.sample_count} muestras y el manifiesto declara "
            f"{manifest.master.sample_count}",
        )

    # --- Escenas ----------------------------------------------------------
    escenas_guion = {escena.scene_id: escena for escena in document.scenes}
    registrar(
        "escenas_completas",
        {e.scene_id for e in manifest.scenes} == set(escenas_guion),
        "el manifiesto no cubre exactamente las escenas del guion",
    )

    cursor = 0
    clips_rotos: list[str] = []
    cobertura_rota: list[str] = []
    for escena in sorted(manifest.scenes, key=lambda item: item.order):
        clip_path = base / escena.clip_path
        if not clip_path.is_file():
            clips_rotos.append(f"falta el clip de {escena.scene_id}")
            continue
        if sha256_file(clip_path) != escena.clip_sha256:
            clips_rotos.append(f"el hash del clip de {escena.scene_id} no coincide")
            continue
        info_clip = read_wav_info(clip_path)
        if not info_clip.matches(fmt) or info_clip.sample_count != escena.clip_samples:
            clips_rotos.append(
                f"el clip de {escena.scene_id} no coincide en formato o numero de muestras"
            )
            continue
        if escena.start_sample != cursor:
            cobertura_rota.append(
                f"hay un hueco o solapamiento antes de {escena.scene_id}: empieza en "
                f"{escena.start_sample} y se esperaba {cursor}"
            )
        if escena.end_sample != escena.start_sample + escena.clip_samples + escena.pause_samples:
            cobertura_rota.append(f"los limites de {escena.scene_id} no cuadran con sus muestras")
        fuente = escenas_guion.get(escena.scene_id)
        if fuente is not None and escena.source_text_sha256 != sha256_text(fuente.narration_text):
            cobertura_rota.append(f"el texto de {escena.scene_id} no es el del guion")
        cursor = escena.end_sample

    registrar("clips_integros", not clips_rotos, "; ".join(clips_rotos))
    if not cobertura_rota and cursor != manifest.master.sample_count:
        cobertura_rota.append(
            f"las escenas suman {cursor} muestras y el maestro declara "
            f"{manifest.master.sample_count}"
        )
    registrar("cobertura_sin_huecos", not cobertura_rota, "; ".join(cobertura_rota))

    # --- Palabras ---------------------------------------------------------
    if manifest.control.voice_status is VoiceStatus.READY:
        palabras_rotas: list[str] = []
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
                palabras_rotas.append(
                    f"{escena.scene_id}: {len(obtenidas)} palabras alineadas frente a "
                    f"{esperadas} narradas"
                )
                continue
            for palabra in obtenidas:
                if palabra.start_s < escena.start_s - TIME_EPSILON_S:
                    palabras_rotas.append(
                        f"{escena.scene_id}: una palabra empieza antes de la escena"
                    )
                    break
                if palabra.end_s > escena.clip_end_s + TIME_EPSILON_S:
                    palabras_rotas.append(
                        f"{escena.scene_id}: una palabra invade el silencio anadido"
                    )
                    break
        registrar("palabras_cubren_la_narracion", not palabras_rotas, "; ".join(palabras_rotas))
    else:
        registrar(
            "palabras_cubren_la_narracion",
            False,
            "voice_status=needs_review: no hay alineacion utilizable garantizada",
        )

    # --- Estado -----------------------------------------------------------
    registrar(
        "voz_lista",
        manifest.control.voice_status is VoiceStatus.READY,
        "voice_status no es ready",
    )
    registrar(
        "sin_bloqueos",
        not any(issue.blocking for issue in manifest.control.issues),
        "el manifiesto conserva incidencias bloqueantes",
    )

    # --- Origen (lo unico que la admision de pruebas ignora) --------------
    registrar(
        "voz_real",
        manifest.simulation is False,
        "simulation=true en la voz: una simulacion nunca alimenta montaje",
    )
    registrar(
        "guion_real",
        manifest.source.source_simulation is False and document.simulation is False,
        "el guion de origen es simulado",
    )

    # --- Duracion ---------------------------------------------------------
    medida = manifest.master.actual_duration_s
    objetivo = document.video.target_duration_s
    informe.measured_duration_s = medida
    registrar(
        "duracion_en_rango",
        profile.min_duration_s <= medida <= profile.max_duration_s,
        f"la duracion medida ({medida:.2f} s) esta fuera del rango del perfil "
        f"({profile.min_duration_s:g}-{profile.max_duration_s:g} s)",
    )
    registrar(
        "duracion_en_tolerancia",
        abs(medida - objetivo) <= objetivo * DURATION_TOLERANCE,
        f"la duracion medida ({medida:.2f} s) se aleja mas de un "
        f"{DURATION_TOLERANCE:.0%} del objetivo ({objetivo:.2f} s)",
    )

    return informe
