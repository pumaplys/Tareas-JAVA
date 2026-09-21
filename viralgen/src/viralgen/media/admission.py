"""Validacion y admision del modulo 3: la puerta de entrada del modulo 4.

Validador LOCAL sin red. Recibe explicitamente guion, voz y manifiesto y
revalida desde los archivos reales: esquemas compatibles, hashes, admisiones
de origen, archivos y formatos, referencias resueltas, cobertura de todas las
escenas, tiempos coincidentes con la voz y duracion suficiente de los clips.

Tres veredictos INDEPENDIENTES, nunca uno solo:

* ``contract_valid``          - definicion documentada: los tres documentos
  cumplen su esquema, se pueden leer, sus vinculos (job_id, voice_run_id,
  hashes) cuadran, todas las rutas quedan dentro del paquete y los archivos
  referenciados existen y DECODIFICAN. No incluye admision de origen.
* ``admissible_for_preview``  - ademas, todas las invariantes de cobertura,
  tiempos, referencias y duracion. Ignora UNICAMENTE el origen.
* ``admissible_for_assembly`` - lo anterior Y fuentes y medios reales.

`--allow-simulation` solo elige el modo y su codigo de salida: nunca cambia
`checks` ni convierte `admissible_for_assembly` en true. El origen se deriva
de las entradas y de las operaciones, no de una bandera del usuario.

Este validador NO modifica ningun byte ni el historial.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..diskutil import sha256_file
from ..profiles import get_profile
from ..schemas.document import ScriptDocument
from ..voice.admission import check_voice_admission
from ..voice.schemas import VoiceManifest
from .imaging import inspect_image
from .schemas import AssetKind, AssetRole, MediaManifest, MediaStatus
from .videoprobe import probe_video, tool_available

#: Comprobaciones que solo miran el ORIGEN. Son las unicas que preview ignora.
ORIGIN_CHECKS: frozenset[str] = frozenset({"medios_reales", "guion_real", "voz_real"})

#: Margen al comparar segundos derivados de muestras.
TIME_EPSILON_S = 0.002


@dataclass
class MediaAdmissionReport:
    checks: dict[str, bool] = field(default_factory=dict)
    failures: list[tuple[str, str]] = field(default_factory=list)
    contract_valid: bool = False
    measured_duration_s: float | None = None

    @property
    def admissible_for_assembly(self) -> bool:
        return self.contract_valid and all(self.checks.values())

    @property
    def admissible_for_preview(self) -> bool:
        return self.contract_valid and all(
            ok for nombre, ok in self.checks.items() if nombre not in ORIGIN_CHECKS
        )

    @property
    def reasons(self) -> list[str]:
        return [motivo for _n, motivo in self.failures]

    @property
    def preview_reasons(self) -> list[str]:
        return [motivo for nombre, motivo in self.failures if nombre not in ORIGIN_CHECKS]

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


def _inside(base: Path, relativa: str) -> Path | None:
    """Resuelve una ruta del paquete rechazando escapes, tambien por symlink."""
    candidato = (base / relativa)
    try:
        resuelto = candidato.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    base_resuelta = base.resolve()
    if base_resuelta != resuelto and base_resuelta not in resuelto.parents:
        return None
    return resuelto


def check_media_admission(
    *,
    script_path: Path,
    voice_path: Path,
    manifest_path: Path,
    settings: Any,
) -> MediaAdmissionReport:
    """Audita el trio (guion, voz, medios) desde los archivos reales."""
    informe = MediaAdmissionReport()

    def registrar(nombre: str, ok: bool, motivo: str = "") -> bool:
        informe.checks[nombre] = ok
        if not ok:
            informe.failures.append((nombre, motivo))
        return ok

    # --- Documentos ---------------------------------------------------------
    try:
        document = ScriptDocument.model_validate(
            json.loads(script_path.read_text(encoding="utf-8"))
        )
    except Exception as exc:
        registrar("guion_valido", False, f"el guion no se puede leer o validar: {str(exc)[:200]}")
        return informe
    registrar("guion_valido", True)

    try:
        voz = VoiceManifest.model_validate(json.loads(voice_path.read_text(encoding="utf-8")))
    except Exception as exc:
        registrar("voz_valida", False, f"la voz no se puede leer o validar: {str(exc)[:200]}")
        return informe
    registrar("voz_valida", True)

    try:
        medios = MediaManifest.model_validate(
            json.loads(manifest_path.read_text(encoding="utf-8"))
        )
    except Exception as exc:
        registrar(
            "manifiesto_valido", False,
            f"el manifiesto de medios no cumple su contrato: {str(exc)[:200]}",
        )
        return informe
    registrar("manifiesto_valido", True)

    # --- Admision de guion y voz (sin red) ---------------------------------
    voz_informe = check_voice_admission(
        script_path=script_path, manifest_path=voice_path, settings=settings
    )
    registrar(
        "guion_y_voz_consistentes",
        voz_informe.admissible_for_preview,
        "guion/voz: " + "; ".join(voz_informe.preview_reasons),
    )
    registrar(
        "guion_real",
        document.simulation is False and medios.source.script_simulation is False,
        "el guion de origen es simulado",
    )
    registrar(
        "voz_real",
        voz.simulation is False and medios.source.voice_simulation is False,
        "la voz de origen es simulada",
    )

    # --- Vinculos -----------------------------------------------------------
    registrar(
        "mismo_job",
        medios.job_id == document.job_id == voz.job_id,
        "el manifiesto de medios apunta a otro job_id",
    )
    registrar(
        "misma_voz",
        medios.voice_run_id == voz.voice_run_id,
        "el manifiesto de medios apunta a otra ejecucion de voz",
    )
    registrar(
        "hash_del_guion",
        medios.source.script_sha256 == sha256_file(script_path),
        "el guion ha cambiado desde que se generaron los medios",
    )
    registrar(
        "hash_de_la_voz",
        medios.source.voice_sha256 == sha256_file(voice_path),
        "la voz ha cambiado desde que se generaron los medios",
    )

    # --- Reloj --------------------------------------------------------------
    registrar(
        "reloj_coincide_con_la_voz",
        medios.timeline.sample_rate_hz == voz.master.sample_rate_hz
        and medios.timeline.total_samples == voz.master.sample_count,
        "el reloj del manifiesto no coincide con el maestro de voz",
    )
    informe.measured_duration_s = medios.timeline.total_duration_s

    escenas_voz = {escena.scene_id: escena for escena in voz.scenes}
    escenas_guion = {escena.scene_id: escena for escena in document.scenes}
    cobertura: list[str] = []
    cursor = 0
    for entrada in sorted(medios.scenes, key=lambda item: item.order):
        fuente = escenas_voz.get(entrada.scene_id)
        if fuente is None:
            cobertura.append(f"{entrada.scene_id} no existe en la voz")
            continue
        if entrada.start_sample != cursor:
            cobertura.append(
                f"hueco o solapamiento antes de {entrada.scene_id}: empieza en "
                f"{entrada.start_sample} y se esperaba {cursor}"
            )
        if (entrada.start_sample, entrada.end_sample) != (
            fuente.start_sample, fuente.end_sample
        ):
            cobertura.append(f"{entrada.scene_id} no usa los tiempos medidos de la voz")
        guion_escena = escenas_guion.get(entrada.scene_id)
        if guion_escena is not None and entrada.requested_type != guion_escena.visual.asset_type.value:
            cobertura.append(
                f"{entrada.scene_id} declara un tipo distinto al del guion"
            )
        if entrada.requested_type != entrada.resolved_type:
            cobertura.append(
                f"{entrada.scene_id}: se pidio {entrada.requested_type} y se resolvio "
                f"{entrada.resolved_type}"
            )
        cursor = entrada.end_sample
    if {entrada.scene_id for entrada in medios.scenes} != set(escenas_guion):
        cobertura.append("el manifiesto no cubre exactamente las escenas del guion")
    if not cobertura and cursor != medios.timeline.total_samples:
        cobertura.append(
            f"las escenas cubren {cursor} muestras y el maestro declara "
            f"{medios.timeline.total_samples}"
        )
    registrar("cobertura_completa", not cobertura, "; ".join(cobertura))

    # --- Archivos -----------------------------------------------------------
    base = manifest_path.parent
    por_id = {asset.asset_id: asset for asset in medios.assets}
    archivos_rotos: list[str] = []
    sin_verificar: list[str] = []
    for asset in medios.assets:
        ruta = _inside(base, asset.path)
        if ruta is None:
            archivos_rotos.append(f"{asset.asset_id}: ruta fuera del paquete o inexistente")
            continue
        if sha256_file(ruta) != asset.sha256:
            archivos_rotos.append(f"{asset.asset_id}: el hash no coincide")
            continue
        if asset.kind in (AssetKind.IMAGE, AssetKind.CONTACT_SHEET):
            try:
                info = inspect_image(ruta, max_pixels=settings.media_max_image_pixels)
            except Exception as exc:
                archivos_rotos.append(f"{asset.asset_id}: no decodifica ({str(exc)[:80]})")
                continue
            if (info.width, info.height) != (asset.width, asset.height):
                archivos_rotos.append(f"{asset.asset_id}: dimensiones distintas a las medidas")
        elif asset.kind is AssetKind.VIDEO:
            if not tool_available(settings.ffprobe_path):
                sin_verificar.append(
                    f"{asset.asset_id}: falta ffprobe para verificar el video"
                )
                continue
            try:
                info_video = probe_video(ruta, ffprobe_path=settings.ffprobe_path)
            except Exception as exc:
                archivos_rotos.append(f"{asset.asset_id}: no decodifica ({str(exc)[:80]})")
                continue
            if asset.video is None:
                archivos_rotos.append(f"{asset.asset_id}: falta el bloque de video")
                continue
            if abs(info_video.duration_s - asset.video.measured_duration_s) > 0.05:
                archivos_rotos.append(
                    f"{asset.asset_id}: la duracion medida no coincide con la declarada"
                )
    registrar("archivos_integros", not archivos_rotos, "; ".join(archivos_rotos))
    registrar(
        "medios_verificables",
        not sin_verificar,
        "; ".join(sin_verificar) or "",
    )

    # --- Referencias --------------------------------------------------------
    referencias = {entrada.character_id for entrada in medios.references.entries}
    usados = {
        character_id
        for escena in document.scenes
        for character_id in escena.character_ids
    }
    faltantes = sorted(usados - referencias)
    registrar(
        "referencias_resueltas",
        not faltantes,
        "faltan referencias de: " + ", ".join(faltantes),
    )

    # --- Duracion de los clips ---------------------------------------------
    clips_cortos: list[str] = []
    for entrada in medios.scenes:
        asset = por_id.get(entrada.primary_asset_id)
        if asset is None:
            clips_cortos.append(f"{entrada.scene_id}: primary_asset_id inexistente")
            continue
        if entrada.resolved_type == "video":
            if asset.video is None:
                clips_cortos.append(f"{entrada.scene_id}: el asset principal no es un clip")
                continue
            if asset.video.measured_duration_s + TIME_EPSILON_S < entrada.duration_s:
                clips_cortos.append(
                    f"{entrada.scene_id}: el clip mide "
                    f"{asset.video.measured_duration_s:.2f} s y la escena necesita "
                    f"{entrada.duration_s:.2f} s"
                )
        elif asset.role is not AssetRole.SCENE_IMAGE:
            clips_cortos.append(f"{entrada.scene_id}: el asset principal no es su imagen")
    registrar("duracion_suficiente", not clips_cortos, "; ".join(clips_cortos))

    # --- Estado -------------------------------------------------------------
    registrar(
        "medios_listos",
        medios.control.media_status is MediaStatus.READY,
        "media_status no es ready",
    )
    registrar(
        "sin_bloqueos",
        not any(issue.blocking for issue in medios.control.issues),
        "el manifiesto conserva incidencias bloqueantes",
    )
    registrar(
        "medios_reales",
        medios.simulation is False,
        "simulation=true en los medios: una simulacion nunca alimenta montaje",
    )

    # El contrato se da por valido si los documentos, vinculos y archivos
    # cuadran, aunque falte la admision de origen.
    informe.contract_valid = all(
        informe.checks.get(nombre, False)
        for nombre in (
            "guion_valido", "voz_valida", "manifiesto_valido", "mismo_job",
            "misma_voz", "hash_del_guion", "hash_de_la_voz", "archivos_integros",
        )
    )
    return informe
