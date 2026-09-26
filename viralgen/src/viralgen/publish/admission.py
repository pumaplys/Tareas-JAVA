"""Admision del modulo 5: tres veredictos que no dependen del modo.

`check_publication_admission` no recibe el modo a proposito. Los tres
veredictos salen de los archivos y de los destinos pedidos, nunca de la
bandera con la que se invoco el comando:

* ``contract_valid``               - los cuatro documentos cumplen su contrato,
  sus vinculos y hashes cuadran y el MP4 existe, decodifica y coincide con lo
  declarado.
* ``admissible_for_simulation``    - ademas, todas las invariantes tecnicas del
  montaje. Un preview legitimo llega hasta aqui: se puede simular su
  publicacion.
* ``admissible_for_real_dispatch`` - ademas, origen de produccion, sin
  simulacion, destinos soportados y ningun parametro de protocolo pendiente de
  contrastar con su fuente.

Elegir `--mode real` no mueve ninguno de los tres. Lo unico que hace el modo es
decidir QUE veredicto se exige (`require_mode`), y por eso un preview no se
vuelve enviable por pedirlo con mas enfasis.

Nada de esto toca los documentos de origen: se leen, se rehashan y se miden a
salida nula. `simulation`, `render_mode` y los estados de produccion se
conservan tal cual, incluido `control.visual_review`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from ..diskutil import sha256_file
from ..media.schemas import MediaManifest
from ..render.admission import ORIGIN_CHECKS, check_render_admission, resolve_inside
from ..render.schemas import RenderManifest
from ..schemas.common import Platform
from ..schemas.document import ScriptDocument
from ..voice.schemas import VoiceManifest
from . import verification
from .errors import ModeViolationError, PublishAdmissionError
from .schemas import (
    AdmissionSummary,
    DocumentRef,
    PublishMode,
    SourceBundle,
    VerificationNotice,
    VerificationSummary,
    VideoRef,
)

#: Destino de verificacion asociado a cada plataforma soportada.
VERIFICATION_TARGET: dict[Platform, str] = {
    Platform.YOUTUBE_SHORTS: "youtube",
    Platform.INSTAGRAM_REELS: "instagram",
    Platform.TIKTOK: "tiktok",
}

#: Plataformas que este modulo entrega por transferencia remota. TikTok no
#: esta: su canal es la exportacion manual, por decision de alcance.
REMOTE_PLATFORMS: frozenset[Platform] = frozenset(
    {Platform.YOUTUBE_SHORTS, Platform.INSTAGRAM_REELS}
)

#: Plataformas que necesitan que el archivo sea descargable por la plataforma.
STAGING_PLATFORMS: frozenset[Platform] = frozenset({Platform.INSTAGRAM_REELS})

#: Comprobaciones que SOLO exige el envio real. Una simulacion las ignora, y
#: por eso un preview se puede simular pero nunca enviar.
REAL_ONLY_CHECKS: frozenset[str] = frozenset(
    {
        "origen_de_produccion",
        "destinos_con_transporte_real",
        "verificacion_de_protocolo",
    }
)


@dataclass
class PublishAdmissionReport:
    """Resultado de la admision. Los tres veredictos van siempre juntos."""

    checks: dict[str, bool] = field(default_factory=dict)
    failures: list[tuple[str, str]] = field(default_factory=list)
    contract_valid: bool = False
    unverified: list[str] = field(default_factory=list)
    measured: dict[str, Any] = field(default_factory=dict)
    targets: tuple[Platform, ...] = ()
    render_checks: dict[str, bool] = field(default_factory=dict)
    sources: SourceBundle | None = None
    video_path: Path | None = None

    @property
    def admissible_for_simulation(self) -> bool:
        return self.contract_valid and all(
            ok for nombre, ok in self.checks.items() if nombre not in REAL_ONLY_CHECKS
        )

    @property
    def admissible_for_real_dispatch(self) -> bool:
        return self.contract_valid and all(self.checks.values())

    @property
    def reasons(self) -> list[str]:
        return [motivo for _n, motivo in self.failures]

    @property
    def simulation_reasons(self) -> list[str]:
        return [
            motivo for nombre, motivo in self.failures if nombre not in REAL_ONLY_CHECKS
        ]

    def blocking_verification(self) -> list[verification.PendingCheck]:
        """Pendientes que impiden el envio real a los destinos pedidos."""
        pendientes: dict[str, verification.PendingCheck] = {}
        for plataforma in self.targets:
            destino = VERIFICATION_TARGET[plataforma]
            for pendiente in verification.blocking_for(destino):
                pendientes[pendiente.check_id] = pendiente
            if plataforma in STAGING_PLATFORMS:
                for pendiente in verification.blocking_for("staging"):
                    pendientes[pendiente.check_id] = pendiente
        return sorted(pendientes.values(), key=lambda p: p.check_id)

    def verification_summary(self) -> VerificationSummary:
        datos = verification.describe_all()
        return VerificationSummary(
            reason=datos["reason"],
            checks=[VerificationNotice(**entrada) for entrada in datos["checks"]],
            totals=dict(datos["totals"]),
            blocked_targets=list(datos["blocked_targets"]),
            note=datos["note"],
        )

    def to_summary(self) -> AdmissionSummary:
        return AdmissionSummary(
            contract_valid=self.contract_valid,
            admissible_for_simulation=self.admissible_for_simulation,
            admissible_for_real_dispatch=self.admissible_for_real_dispatch,
            checks=dict(self.checks),
            real_only_checks=sorted(REAL_ONLY_CHECKS),
            reasons=[motivo[:600] for motivo in self.reasons],
            simulation_reasons=[motivo[:600] for motivo in self.simulation_reasons],
            unverified=[texto[:600] for texto in self.unverified],
        )

    def to_dict(self) -> dict:
        return {
            "contract_valid": self.contract_valid,
            "admissible_for_simulation": self.admissible_for_simulation,
            "admissible_for_real_dispatch": self.admissible_for_real_dispatch,
            "checks": dict(self.checks),
            "real_only_checks": sorted(REAL_ONLY_CHECKS),
            "reasons": self.reasons,
            "simulation_reasons": self.simulation_reasons,
            "unverified_checks": list(self.unverified),
            "render_checks": dict(self.render_checks),
            "targets": [plataforma.value for plataforma in self.targets],
            "pending_verification": [
                pendiente.describe() for pendiente in self.blocking_verification()
            ],
            "measured": dict(self.measured),
            "note": (
                "Estos veredictos no dependen del modo elegido. Un plan "
                "localmente correcto puede tener comprobaciones remotas "
                "pendientes: pendiente no es aprobado."
            ),
        }


def _leer(modelo, ruta: Path):
    return modelo.model_validate(json.loads(ruta.read_text(encoding="utf-8")))


def check_publication_admission(
    *,
    script_path: Path,
    voice_path: Path,
    media_path: Path,
    manifest_path: Path,
    settings: Any,
    targets: Sequence[Platform],
) -> PublishAdmissionReport:
    """Revalida la cadena entera y decide los tres veredictos."""
    informe = PublishAdmissionReport(targets=tuple(dict.fromkeys(targets)))

    def registrar(nombre: str, ok: bool, motivo: str = "") -> bool:
        informe.checks[nombre] = ok
        if not ok:
            informe.failures.append((nombre, motivo))
        return ok

    if not informe.targets:
        registrar("destinos_indicados", False, "no se indico ningun destino")
        return informe
    registrar("destinos_indicados", True)

    # --- Lo que no depende de los archivos, primero -------------------------
    # Asi un informe que se corte antes de medir el MP4 sigue diciendo si los
    # destinos tienen transporte y que quedo sin verificar.
    _registrar_destinos(informe, registrar)
    bloqueantes = informe.blocking_verification()
    registrar(
        "verificacion_de_protocolo",
        not bloqueantes,
        "hay parametros de protocolo sin contrastar con su fuente: "
        + ", ".join(pendiente.check_id for pendiente in bloqueantes)
        + ". " + verification.UNREACHABLE_REASON,
    )

    # --- La cadena de los modulos 1-4, revalidada desde los archivos --------
    # No se lee ningun booleano guardado: se recalcula todo, incluidos los
    # hashes y la medicion del MP4.
    render_informe = check_render_admission(
        script_path=script_path,
        voice_path=voice_path,
        media_path=media_path,
        manifest_path=manifest_path,
        settings=settings,
    )
    informe.render_checks = dict(render_informe.checks)
    informe.unverified.extend(render_informe.unverified)
    informe.measured["render"] = render_informe.measured

    registrar(
        "cadena_contractualmente_valida",
        render_informe.contract_valid,
        "la cadena guion/voz/medios/render no es contractualmente valida: "
        + "; ".join(render_informe.reasons)[:400],
    )
    registrar(
        "montaje_tecnicamente_admisible",
        render_informe.admissible_for_preview,
        "el montaje no supera sus invariantes tecnicas: "
        + "; ".join(render_informe.preview_reasons)[:400],
    )
    fallos_de_origen = [
        motivo for nombre, motivo in render_informe.failures if nombre in ORIGIN_CHECKS
    ]
    registrar(
        "origen_de_produccion",
        render_informe.contract_valid and not fallos_de_origen,
        "el paquete no es de produccion: " + "; ".join(fallos_de_origen)[:400],
    )

    if not render_informe.contract_valid:
        # Sin contrato valido no hay nada que resolver: el MP4 podria no
        # existir o no corresponder a lo declarado.
        informe.contract_valid = False
        return informe

    # --- Documentos, ya validados por la admision anterior ------------------
    try:
        guion = _leer(ScriptDocument, script_path)
        voz = _leer(VoiceManifest, voice_path)
        medios = _leer(MediaManifest, media_path)
        render = _leer(RenderManifest, manifest_path)
    except Exception as exc:  # pragma: no cover - la admision anterior ya filtra
        registrar("documentos_legibles", False, f"no se pueden releer: {str(exc)[:200]}")
        informe.contract_valid = False
        return informe
    registrar("documentos_legibles", True)

    # --- El MP4: se resuelve con las reglas de rutas del modulo 4 -----------
    ruta_video = resolve_inside(manifest_path.parent, render.output.path)
    if ruta_video is None or not ruta_video.is_file():
        registrar(
            "video_disponible",
            False,
            f"el MP4 {render.output.path} no existe o queda fuera del paquete",
        )
        informe.contract_valid = False
        return informe

    tamano = ruta_video.stat().st_size
    hash_video = sha256_file(ruta_video)
    informe.video_path = ruta_video
    informe.measured["video"] = {
        "path": str(ruta_video),
        "sha256": hash_video,
        "size_bytes": tamano,
    }
    registrar(
        "video_disponible",
        hash_video == render.output.sha256 and tamano == render.output.size_bytes,
        "el MP4 no coincide con lo declarado en el manifiesto (hash o tamano)",
    )

    limite = int(settings.publish_max_video_mib) * 1024 * 1024
    registrar(
        "video_dentro_del_limite",
        tamano <= limite,
        f"el MP4 ocupa {tamano} B y el limite propio del publicador es {limite} B",
    )

    # --- Lo que DECIA el manifiesto frente a lo que se acaba de comprobar ---
    declarado = render.control.admissible_for_publisher
    recalculado = render_informe.admissible_for_publisher
    informe.measured["admissible_for_publisher"] = {
        "declared": declarado,
        "recomputed": recalculado,
        "note": (
            "El booleano guardado es informativo. El que decide es el "
            "recalculado sobre los archivos reales."
        ),
    }

    informe.contract_valid = all(
        informe.checks.get(nombre, False)
        for nombre in (
            "cadena_contractualmente_valida",
            "documentos_legibles",
            "video_disponible",
        )
    )

    informe.sources = SourceBundle(
        job_id=render.job_id,
        render_run_id=render.render_run_id,
        voice_run_id=render.voice_run_id,
        media_run_id=render.media_run_id,
        channel=guion.channel.value,
        profile_id=guion.profile_id,
        language=guion.language,
        render_mode=render.render_mode.value,
        render_simulation=render.simulation,
        script=DocumentRef(
            path=str(script_path),
            sha256=render.sources.script_sha256,
            size_bytes=script_path.stat().st_size,
            schema_version=guion.schema_version,
            simulation=guion.simulation,
        ),
        voice=DocumentRef(
            path=str(voice_path),
            sha256=render.sources.voice_sha256,
            size_bytes=voice_path.stat().st_size,
            schema_version=voz.schema_version,
            simulation=voz.simulation,
        ),
        media=DocumentRef(
            path=str(media_path),
            sha256=render.sources.media_sha256,
            size_bytes=media_path.stat().st_size,
            schema_version=medios.schema_version,
            simulation=medios.simulation,
        ),
        render=DocumentRef(
            path=str(manifest_path),
            sha256=sha256_file(manifest_path),
            size_bytes=manifest_path.stat().st_size,
            schema_version=render.schema_version,
            simulation=render.simulation,
        ),
        video=VideoRef(
            path=str(ruta_video),
            sha256=hash_video,
            size_bytes=tamano,
            container_duration_s=render.output.container_duration_s,
            width=render.output.video.width,
            height=render.output.video.height,
            fps=render.output.video.fps,
            verified_at=datetime.now(timezone.utc),
        ),
        admissible_for_publisher_declared=declarado,
        admissible_for_publisher_recomputed=recalculado,
    )
    return informe


def _registrar_destinos(informe: PublishAdmissionReport, registrar) -> None:
    """Comprueba que los destinos pedidos tengan transporte en esta entrega."""
    desconocidos = [
        plataforma.value
        for plataforma in informe.targets
        if plataforma not in VERIFICATION_TARGET
    ]
    registrar(
        "destinos_soportados",
        not desconocidos,
        "destinos sin adaptador: " + ", ".join(desconocidos),
    )
    solo_manual = [
        plataforma.value
        for plataforma in informe.targets
        if plataforma not in REMOTE_PLATFORMS
    ]
    # TikTok no descalifica el envio real del resto: descalifica el suyo. Se
    # registra como comprobacion propia para que el motivo quede escrito.
    registrar(
        "destinos_con_transporte_real",
        not solo_manual or len(solo_manual) < len(informe.targets),
        "los unicos destinos pedidos se entregan a mano en este alcance ("
        + ", ".join(solo_manual)
        + "): no hay envio automatico que autorizar",
    )


def require_mode(informe: PublishAdmissionReport, mode: PublishMode) -> None:
    """Exige el veredicto que ese modo necesita. No cambia ninguno.

    * ``plan`` no exige nada: su trabajo es precisamente describir lo que falta.
    * ``mock`` exige admisibilidad para simulacion.
    * ``real`` exige admisibilidad para envio real.
    """
    if mode is PublishMode.PLAN:
        return
    if mode is PublishMode.MOCK:
        if not informe.admissible_for_simulation:
            raise PublishAdmissionError(
                "el paquete no es admisible ni para simular: "
                + "; ".join(informe.simulation_reasons)[:500],
                details={"admission": informe.to_dict()},
            )
        return
    if not informe.admissible_for_real_dispatch:
        faltan_solo_reales = informe.admissible_for_simulation
        mensaje = (
            "el paquete se puede simular, pero NO es admisible para envio real: "
            if faltan_solo_reales
            else "el paquete no es admisible para envio real: "
        )
        raise PublishAdmissionError(
            mensaje + "; ".join(informe.reasons)[:500],
            details={"admission": informe.to_dict()},
        )


def require_real_transport(mode: PublishMode, *, operation: str) -> None:
    """Cortafuegos para el codigo de transporte.

    Lo llama cualquier adaptador antes de tocar la red. Si el modo no es real,
    el intento es un error de programacion y se detiene ahi: el modo simulado
    no alcanza la red aunque el entorno tenga credenciales.
    """
    if mode is not PublishMode.REAL:
        raise ModeViolationError(
            f"{operation} necesita modo real; el modo {mode.value} no usa "
            "clientes de red, aunque haya credenciales configuradas",
            details={"mode": mode.value, "operation": operation},
        )
