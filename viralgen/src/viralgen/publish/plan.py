"""Construccion del borrador editorial (`publication_plan.json`).

El plan sale de tres sitios y de ninguno mas:

* del **guion**, que ya trae un plan de publicacion por plataforma con titulo,
  texto y etiquetas;
* de la **peticion del operador**, que elige cuenta, hora, zona y visibilidad;
* de la **admision**, que dice que falta para poder enviar de verdad.

Lo que este modulo NO hace: generar texto. No resume, no acorta, no anade
ganchos, cifras, llamadas a la accion ni hashtags. Si el guion no trae un dato
obligatorio, el plan lo dice y el operador lo completa editando el archivo.
Preferimos un plan incompleto y honesto a uno completo e inventado.

El plan tampoco tiene efectos externos: construirlo no abre conexiones, no
crea archivos remotos y no autoriza nada.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from ..diskutil import sha256_file
from ..errors import DocumentValidationError
from ..schemas.common import Channel, Platform
from ..schemas.document import PublishingPlan, ScriptDocument
from .accounts import Account, resolve_account
from .admission import PublishAdmissionReport, VERIFICATION_TARGET
from .clock import Clock, SystemClock, ensure_zone, local_to_utc, parse_wall
from .schemas import (
    AccountRef,
    AdmissionSummary,
    AudienceDecision,
    DestinationMetadata,
    DestinationOptions,
    DestinationState,
    LOCAL_TEXT_LIMITS,
    PendingRequirement,
    PlanDestination,
    PublicationPlan,
    PublishMode,
    ScheduleSpec,
    SyntheticDisclosure,
    TextSource,
    Visibility,
    compose_caption,
)


@dataclass(frozen=True)
class DestinationRequest:
    """Lo que pide el operador para un destino. Sin texto: ese sale del guion."""

    destination_id: str
    platform: Platform
    account_alias: str
    local_time: str
    timezone: str
    visibility: Visibility
    fold: int | None = None
    notify_subscribers: bool | None = None
    share_to_feed: bool | None = None


def _entrada_del_guion(
    guion: ScriptDocument, plataforma: Platform
) -> PublishingPlan | None:
    for entrada in guion.publishing:
        if entrada.platform is plataforma:
            return entrada
    return None


def _decidir_audiencia(
    guion: ScriptDocument, entrada: PublishingPlan | None
) -> AudienceDecision:
    """El canal infantil se declara infantil; el resto exige decision expresa.

    `made_for_kids=null` en el guion significa "sin decidir", no "no". No se
    convierte en `false` por comodidad.
    """
    if guion.channel is Channel.INFANTIL:
        return AudienceDecision.MADE_FOR_KIDS
    if entrada is not None and entrada.made_for_kids is not None:
        return (
            AudienceDecision.MADE_FOR_KIDS
            if entrada.made_for_kids
            else AudienceDecision.NOT_MADE_FOR_KIDS
        )
    return AudienceDecision.UNDECIDED


def _dentro_de_limites(
    titulo: str | None, descripcion: str | None, tags: list[str], hashtags: list[str]
) -> bool:
    return (
        len(titulo or "") <= LOCAL_TEXT_LIMITS["title_chars"]
        and len(descripcion or "") <= LOCAL_TEXT_LIMITS["description_chars"]
        and len(tags) <= LOCAL_TEXT_LIMITS["tags"]
        and len(hashtags) <= LOCAL_TEXT_LIMITS["hashtags"]
    )


def _requisitos(
    *,
    plataforma: Platform,
    cuenta: Account,
    metadata: DestinationMetadata,
    entrada: PublishingPlan | None,
    admision: PublishAdmissionReport,
) -> list[PendingRequirement]:
    """Todo lo que impide autorizar este destino, con su remedio."""
    requisitos: list[PendingRequirement] = []

    if entrada is None:
        requisitos.append(
            PendingRequirement(
                code="sin_plan_editorial",
                message=(
                    f"El guion no trae plan de publicacion para {plataforma.value}: "
                    "no hay titulo ni texto de origen."
                ),
                blocks="all",
                resolution=(
                    "Completa title y description en el plan antes de autorizar. "
                    "Este modulo no redacta el texto por ti."
                ),
            )
        )
    else:
        if not metadata.title:
            requisitos.append(
                PendingRequirement(
                    code="sin_titulo",
                    message="Falta el titulo final.",
                    blocks="all",
                    resolution="Escribe `metadata.title` en el plan.",
                )
            )
        if not metadata.description:
            requisitos.append(
                PendingRequirement(
                    code="sin_texto",
                    message="Falta el texto final (descripcion o caption).",
                    blocks="all",
                    resolution="Escribe `metadata.description` en el plan.",
                )
            )

    if metadata.audience is AudienceDecision.UNDECIDED:
        requisitos.append(
            PendingRequirement(
                code="audiencia_sin_decidir",
                message=(
                    "La audiencia infantil no esta decidida. `made_for_kids=null` "
                    "en el guion significa sin decidir, no 'no'."
                ),
                # YouTube exige la declaracion en el propio envio; sin ella no
                # hay payload completo ni siquiera para ensayarlo.
                blocks="all" if plataforma is Platform.YOUTUBE_SHORTS else "real",
                resolution=(
                    "Pon `metadata.audience` en made_for_kids o not_made_for_kids."
                ),
            )
        )

    if metadata.synthetic_disclosure is SyntheticDisclosure.NOT_REVIEWED:
        requisitos.append(
            PendingRequirement(
                code="divulgacion_sintetica_sin_revisar",
                message=(
                    "Falta decidir si el video contiene medios sinteticos realistas. "
                    "Es distinto de `simulation`: un video real de produccion puede "
                    "llevar medios generados con IA."
                ),
                blocks="real",
                resolution="Resuelve `metadata.synthetic_disclosure` en el plan.",
            )
        )

    if not metadata.within_local_limits:
        requisitos.append(
            PendingRequirement(
                code="texto_supera_limites_locales",
                message=(
                    "El texto supera los topes locales "
                    f"({LOCAL_TEXT_LIMITS}). Son decisiones del producto, no "
                    "limites verificados de la plataforma."
                ),
                blocks="real",
                resolution=(
                    "Acorta el texto en el plan. No se recorta solo: recortar en "
                    "silencio cambiaria lo que se publica."
                ),
            )
        )

    if not cuenta.account_id:
        requisitos.append(
            PendingRequirement(
                code="cuenta_sin_identificador",
                message=(
                    f"La cuenta {cuenta.alias!r} no declara el ID exacto que debe "
                    "responder la plataforma."
                ),
                blocks="real",
                resolution=(
                    "Anade `account_id` al catalogo de cuentas. Publicar en la "
                    "cuenta equivocada no se deshace."
                ),
            )
        )

    if not admision.checks.get("origen_de_produccion", False):
        requisitos.append(
            PendingRequirement(
                code="origen_no_es_de_produccion",
                message=(
                    "El paquete no es un render de produccion sin simulacion: "
                    + "; ".join(
                        motivo
                        for nombre, motivo in admision.failures
                        if nombre == "origen_de_produccion"
                    )[:300]
                ),
                blocks="real",
                resolution=(
                    "Publica un paquete de produccion. Reclasificar el manifiesto "
                    "no sirve: las fuentes tambien se leen."
                ),
            )
        )

    destino_verificacion = VERIFICATION_TARGET.get(plataforma)
    pendientes = [
        pendiente
        for pendiente in admision.blocking_verification()
        if pendiente.target in (destino_verificacion, "staging")
    ]
    if pendientes:
        requisitos.append(
            PendingRequirement(
                code="verificacion_de_protocolo_pendiente",
                message=(
                    "Parametros de protocolo sin contrastar con su fuente: "
                    + ", ".join(pendiente.check_id for pendiente in pendientes)
                ),
                blocks="real",
                resolution=(
                    "Comprueba cada parametro contra su documentacion, ajusta el "
                    "adaptador si difiere y marca la entrada como verificada."
                ),
            )
        )

    if plataforma is Platform.TIKTOK:
        requisitos.append(
            PendingRequirement(
                code="entrega_manual",
                message=(
                    "TikTok se entrega a mano en este alcance: el paquete se "
                    "exporta y lo publica una persona con las herramientas oficiales."
                ),
                blocks="real",
                resolution=(
                    "Usa `publish export-manual` y, cuando publiques, "
                    "`publish record-manual`. La hora del plan es una indicacion "
                    "para el operador, no una programacion confirmada por TikTok."
                ),
            )
        )

    return requisitos


def _destino(
    *,
    peticion: DestinationRequest,
    guion: ScriptDocument,
    cuenta: Account,
    admision: PublishAdmissionReport,
) -> PlanDestination:
    entrada = _entrada_del_guion(guion, peticion.platform)
    hashtags = list(entrada.hashtags) if entrada else []
    # El guion NO tiene campo de etiquetas de YouTube. No se fabrican a partir
    # de los hashtags: serian un dato inventado con aspecto de dato real.
    tags: list[str] = []
    titulo = entrada.title if entrada else None
    descripcion = entrada.caption if entrada else None

    metadata = DestinationMetadata(
        title=titulo,
        description=descripcion,
        tags=tags,
        hashtags=hashtags,
        language=guion.language,
        audience=_decidir_audiencia(guion, entrada),
        synthetic_disclosure=SyntheticDisclosure.NOT_REVIEWED,
        text_source=TextSource.SCRIPT_PUBLISHING_PLAN,
        within_local_limits=_dentro_de_limites(titulo, descripcion, tags, hashtags),
    )

    zona = ensure_zone(peticion.timezone)
    pared = parse_wall(peticion.local_time)
    instante = local_to_utc(pared, zona, fold=peticion.fold)
    horario = ScheduleSpec(
        scheduled_at_utc=instante,
        timezone=peticion.timezone,
        local_time=pared.strftime("%Y-%m-%dT%H:%M:%S"),
        fold=peticion.fold or 0,
        late_start_window_s=admision.measured.get("late_start_window_s", 900),
    )

    opciones = DestinationOptions(
        notify_subscribers=(
            peticion.notify_subscribers
            if peticion.platform is Platform.YOUTUBE_SHORTS
            else None
        ),
        share_to_feed=(
            peticion.share_to_feed
            if peticion.platform is Platform.INSTAGRAM_REELS
            else None
        ),
        requires_staging=peticion.platform is Platform.INSTAGRAM_REELS,
        manual_delivery=peticion.platform is Platform.TIKTOK,
    )

    requisitos = _requisitos(
        plataforma=peticion.platform,
        cuenta=cuenta,
        metadata=metadata,
        entrada=entrada,
        admision=admision,
    )
    bloqueado = any(requisito.blocks == "all" for requisito in requisitos)

    return PlanDestination(
        destination_id=peticion.destination_id,
        platform=peticion.platform,
        account=AccountRef(
            platform=peticion.platform,
            alias=cuenta.alias,
            expected_account_id=cuenta.account_id,
            account_id_kind=cuenta.account_id_kind,
        ),
        metadata=metadata,
        requested_visibility=peticion.visibility,
        schedule=horario,
        options=opciones,
        # `blocked` es "no se puede continuar"; `draft` es "borrador editable".
        # Que falte la autorizacion no es estar bloqueado: eso es lo normal.
        state=DestinationState.BLOCKED if bloqueado else DestinationState.DRAFT,
        pending_requirements=requisitos,
    )


def readable_lines(plan: PublicationPlan) -> list[str]:
    """Vista legible para la revision humana. Sin URLs firmadas ni tokens."""
    lineas = [
        f"Video: {plan.sources.video.path}",
        f"  SHA-256 {plan.sources.video.sha256} "
        f"({plan.sources.video.size_bytes} bytes, "
        f"{plan.sources.video.width}x{plan.sources.video.height})",
        f"Origen: render {plan.sources.render_mode}, "
        f"simulacion={'si' if plan.sources.render_simulation else 'no'}",
        f"Modo: {plan.mode.value} "
        f"({'simulado' if plan.mode.simulation else 'ENVIO REAL'})",
        "---",
    ]
    for destino in plan.destinations:
        lineas.append(f"[{destino.destination_id}] {destino.platform.value}")
        lineas.append(
            f"  Cuenta: {destino.account.alias} "
            f"({destino.account.account_id_kind}="
            f"{destino.account.expected_account_id or 'SIN DECLARAR'})"
        )
        lineas.append(f"  Titulo: {destino.metadata.title or '(pendiente)'}")
        texto = destino.metadata.description or "(pendiente)"
        lineas.append(f"  Texto: {texto[:160]}{'...' if len(texto) > 160 else ''}")
        if destino.metadata.hashtags:
            lineas.append(
                "  Hashtags: " + " ".join(f"#{h}" for h in destino.metadata.hashtags)
            )
            pie = compose_caption(destino.metadata)
            lineas.append(
                f"  Texto final publicado (descripcion + hashtags): "
                f"{pie[:200]}{'...' if len(pie) > 200 else ''}"
            )
        lineas.append(f"  Visibilidad solicitada: {destino.requested_visibility.value}")
        lineas.append(
            f"  Hora: {destino.schedule.local_time} ({destino.schedule.timezone}) "
            f"= {destino.schedule.scheduled_at_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        )
        lineas.append(f"  Audiencia: {destino.metadata.audience.value}")
        lineas.append(
            f"  Contenido sintetico: {destino.metadata.synthetic_disclosure.value}"
        )
        transferencias = []
        if destino.options.requires_staging:
            transferencias.append(
                "subida del MP4 a un almacenamiento temporal privado y URL "
                "firmada de corta duracion para que la plataforma lo descargue"
            )
        if destino.options.manual_delivery:
            transferencias.append("exportacion local para publicar a mano")
        if not transferencias:
            transferencias.append("subida directa del MP4 a la plataforma")
        lineas.append("  Transferencias previstas: " + "; ".join(transferencias))
        for requisito in destino.pending_requirements:
            lineas.append(
                f"  PENDIENTE [{requisito.code}] (bloquea {requisito.blocks}): "
                f"{requisito.message}"
            )
        lineas.append("---")
    return [linea for linea in lineas if linea.strip()]


def build_publication_plan(
    *,
    admission: PublishAdmissionReport,
    script_path: Path,
    requests: Sequence[DestinationRequest],
    accounts: dict[str, Account],
    mode: PublishMode,
    publish_key: str,
    settings: Any,
    clock: Clock | None = None,
    plan_id: str | None = None,
    revision: int = 1,
) -> PublicationPlan:
    """Arma el borrador. No escribe nada ni toca la red."""
    if admission.sources is None:
        raise DocumentValidationError(
            "no se puede planificar sobre un paquete que no supera el contrato: "
            + "; ".join(admission.reasons)[:400],
            details={"admission": admission.to_dict()},
        )
    if not requests:
        raise DocumentValidationError("hay que indicar al menos un destino")

    reloj = clock or SystemClock()
    guion = ScriptDocument.model_validate(
        json.loads(script_path.read_text(encoding="utf-8"))
    )
    admission.measured.setdefault(
        "late_start_window_s", int(settings.publish_late_start_window_s)
    )

    destinos = [
        _destino(
            peticion=peticion,
            guion=guion,
            cuenta=resolve_account(accounts, peticion.account_alias, platform=peticion.platform),
            admision=admission,
        )
        for peticion in requests
    ]

    plan = PublicationPlan(
        plan_id=plan_id or str(uuid.uuid4()),
        revision=revision,
        created_at=reloj.now(),
        mode=mode,
        publish_key=publish_key,
        intent_fingerprint="0" * 64,
        sources=admission.sources,
        destinations=destinos,
        admission=_resumen(admission),
        verification=admission.verification_summary(),
        notes=[
            "Un plan no tiene efectos externos: ni red, ni staging, ni envios.",
            "Autorizar cubre tambien subir ese MP4 al almacenamiento temporal "
            "cuando el destino lo necesite.",
            "Cambiar cuenta, video, texto, privacidad, horario o destino crea "
            "una revision nueva que hay que volver a autorizar.",
        ],
    )
    plan = plan.model_copy(update={"readable_view": readable_lines(plan)})
    return _revalidar(
        plan.model_copy(update={"intent_fingerprint": plan.compute_intent_fingerprint()})
    )


def _resumen(admision: PublishAdmissionReport) -> AdmissionSummary:
    return admision.to_summary()


def _revalidar(plan: PublicationPlan) -> PublicationPlan:
    """Vuelve a pasar el plan por su contrato.

    `model_copy(update=...)` no valida nada: si se escribiera su resultado sin
    revalidar, el archivo podria no ser legible por el propio contrato que
    dice cumplir.
    """
    return PublicationPlan.model_validate(plan.model_dump(mode="python"))


def normalize_plan(plan: PublicationPlan) -> tuple[PublicationPlan, bool]:
    """Recalcula lo derivado de un plan posiblemente editado a mano.

    Devuelve el plan con `intent_fingerprint` y vista legible al dia, y si la
    intencion habia cambiado respecto de lo que declaraba el archivo. Editar el
    borrador es parte del flujo; lo que no vale es que el fingerprint guardado
    decida por su cuenta.
    """
    actual = plan.compute_intent_fingerprint()
    cambio = actual != plan.intent_fingerprint
    actualizado = _revalidar(
        plan.model_copy(
            update={
                "intent_fingerprint": actual,
                "revision": plan.revision + 1 if cambio else plan.revision,
                "readable_view": readable_lines(plan),
            }
        )
    )
    return actualizado, cambio


def plan_to_json(plan: PublicationPlan) -> str:
    return json.dumps(
        plan.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=False
    )


def write_plan(path: Path, plan: PublicationPlan) -> str:
    """Escribe el plan de forma atomica y devuelve su SHA-256."""
    path.parent.mkdir(parents=True, exist_ok=True)
    contenido = plan_to_json(plan) + "\n"
    descriptor, temporal = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as manejador:
            manejador.write(contenido)
            manejador.flush()
            os.fsync(manejador.fileno())
        os.replace(temporal, path)
    except BaseException:
        Path(temporal).unlink(missing_ok=True)
        raise
    return sha256_file(path)


def load_plan(path: Path) -> tuple[PublicationPlan, str]:
    """Lee un plan del disco y devuelve (plan, sha256 del archivo)."""
    try:
        datos = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise DocumentValidationError(
            f"el plan {path} no se puede leer: {exc}", details={"path": str(path)}
        ) from exc
    try:
        plan = PublicationPlan.model_validate(datos)
    except Exception as exc:
        raise DocumentValidationError(
            f"el plan {path} no cumple su contrato: {str(exc)[:400]}",
            details={"path": str(path)},
        ) from exc
    return plan, sha256_file(path)
