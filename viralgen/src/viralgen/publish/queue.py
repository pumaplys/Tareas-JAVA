"""Cola local: poner en cola, elegir lo que toca y cancelar.

La cola no publica nada. Decide que tarea esta lista para que el trabajador la
recoja, protege contra duplicados y guarda el horario autorizado. Las
operaciones externas viven en los adaptadores.

La programacion es LOCAL: a la hora autorizada empieza la entrega. No se
programa en la plataforma, de modo que cancelar antes de empezar tiene una
semantica unica y clara para todos los destinos.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..storage import iso, utcnow
from .authorize import identity_space, require_authorization
from .clock import Clock, DueVerdict, SystemClock, evaluate_due
from .errors import ModeViolationError
from .schemas import (
    DestinationState,
    PublicationPlan,
    PublishMode,
    TransferPhase,
)
from .storage import PublishStorage


def publication_id_for(publish_key: str, *, mode: PublishMode) -> str:
    """Identidad del trabajo, derivada de la clave y del espacio de identidad.

    Determinista a proposito: `approve` y `enqueue` se refieren al mismo
    trabajo sin pasarse un identificador a mano, y la misma clave en modo
    simulado y en real da trabajos DISTINTOS.
    """
    espacio = identity_space(mode)
    return str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"viralgen:publish:{espacio}:{publish_key}")
    )


def account_key(destino) -> str:
    """Clave de cuenta para la proteccion antiduplicados.

    Se prefiere el ID exacto; si no lo hay -caso que el envio real no
    permite- se cae al alias, que al menos separa cuentas distintas del
    catalogo.
    """
    return destino.account.expected_account_id or f"alias:{destino.account.alias}"


@dataclass
class EnqueueResult:
    publication: dict
    destinations: list[dict]
    created: bool


def enqueue_plan(
    storage: PublishStorage,
    *,
    plan: PublicationPlan,
    plan_sha256: str,
    publication_id: str,
    settings: Any,
    clock: Clock | None = None,
) -> EnqueueResult:
    """Persiste el horario y los destinos AUTORIZADOS.

    Exige autorizacion viva para esta intencion exacta. Sin ella no se crea
    ninguna tarea: la cola no es un sitio donde aparcar algo sin permiso.
    """
    reloj = clock or SystemClock()
    espacio = identity_space(plan.mode)

    bloqueados = [
        destino.destination_id
        for destino in plan.destinations
        if destino.state is DestinationState.BLOCKED
    ]
    if bloqueados:
        raise ModeViolationError(
            "Hay destinos con requisitos sin resolver: " + ", ".join(bloqueados) +
            ". Completa el plan antes de ponerlo en cola.",
            details={"blocked": bloqueados},
        )

    # Nada se escribe sin permiso: primero la autorizacion, despues la cola.
    require_authorization(storage, publication_id=publication_id, plan=plan)

    trabajo = storage.upsert_publication(
        {
            "publication_id": publication_id,
            "publish_key": plan.publish_key,
            "identity_space": espacio,
            "mode": plan.mode.value,
            "simulation": int(plan.mode.simulation),
            "job_id": plan.sources.job_id,
            "render_run_id": plan.sources.render_run_id,
            "intent_fingerprint": plan.compute_intent_fingerprint(),
            "plan_revision": plan.revision,
            "plan_sha256": plan_sha256,
            "plan_json": plan.model_dump_json(),
            "video_sha256": plan.sources.video.sha256,
            "video_path": plan.sources.video.path,
            "status": "scheduled",
        }
    )
    nuevo = trabajo["created_at"] == trabajo["updated_at"]

    destinos: list[dict] = []
    for destino in plan.destinations:
        storage.reserve_delivery(
            identity_space=espacio,
            platform=destino.platform.value,
            account_key=account_key(destino),
            video_sha256=plan.sources.video.sha256,
            publication_id=trabajo["publication_id"],
            destination_id=destino.destination_id,
        )
        estado = (
            DestinationState.DRAFT
            if destino.options.manual_delivery
            else DestinationState.SCHEDULED_LOCAL
        )
        fila = storage.create_destination(
            {
                "publication_id": trabajo["publication_id"],
                "destination_id": destino.destination_id,
                "platform": destino.platform.value,
                "account_alias": destino.account.alias,
                "expected_account_id": destino.account.expected_account_id,
                "state": estado.value,
                "phase": TransferPhase.NOT_STARTED.value,
                "simulation": int(plan.mode.simulation),
                "requested_visibility": destino.requested_visibility.value,
                "scheduled_at": iso(destino.schedule.scheduled_at_utc),
                "timezone": destino.schedule.timezone,
                "fold": destino.schedule.fold,
                "late_start_window_s": destino.schedule.late_start_window_s,
            }
        )
        destinos.append(fila)
        storage.record_event(
            publication_id=trabajo["publication_id"],
            destination_id=destino.destination_id,
            kind="enqueued",
            state=fila["state"],
            detail={
                "platform": destino.platform.value,
                "account_alias": destino.account.alias,
                "scheduled_at": iso(destino.schedule.scheduled_at_utc),
                "timezone": destino.schedule.timezone,
                "requested_visibility": destino.requested_visibility.value,
            },
        )

    _ = reloj  # el horario ya viene decidido en el plan
    return EnqueueResult(publication=trabajo, destinations=destinos, created=nuevo)


def start_verdict(fila: dict, *, now: datetime) -> DueVerdict:
    """Si un destino PROGRAMADO puede iniciar su envio ahora."""
    programado = datetime.fromisoformat(fila["scheduled_at"].replace("Z", "+00:00"))
    return evaluate_due(
        programado, now, late_window_s=int(fila["late_start_window_s"])
    )


def daily_budget_exhausted(
    storage: PublishStorage, fila: dict, *, now: datetime, settings: Any
) -> bool:
    """Tope propio de entregas nuevas por cuenta y dia. No es cuota de nadie."""
    limite = int(settings.publish_max_new_real_per_account_per_day)
    if limite <= 0:
        return True
    trabajo = storage.get_publication(fila["publication_id"])
    assert trabajo is not None
    usadas = storage.count_new_deliveries(
        identity_space=trabajo["identity_space"],
        platform=fila["platform"],
        account_key=fila["expected_account_id"] or f"alias:{fila['account_alias']}",
        day=iso(now)[:10],
    )
    return usadas > limite


def request_cancel(
    storage: PublishStorage,
    *,
    publication_id: str,
    destination_id: str,
    now: datetime,
    note: str = "",
) -> dict:
    """Cancela lo que aun no ha salido; lo demas se registra y se explica.

    Nunca declara una cancelacion remota ni borra una publicacion existente:
    gestionar una publicacion ya creada queda fuera de este MVP.
    """
    fila = storage.get_destination(publication_id, destination_id)
    if fila is None:
        raise KeyError(destination_id)
    estado = DestinationState(fila["state"])

    if estado in (
        DestinationState.DRAFT,
        DestinationState.BLOCKED,
        DestinationState.SCHEDULED_LOCAL,
        DestinationState.AWAITING_MANUAL,
    ):
        actualizado = storage.update_destination(
            publication_id,
            destination_id,
            state=DestinationState.CANCELLED,
            cancel_requested_at=iso(now),
            cancel_note=note or "cancelado antes de iniciar ninguna operacion externa",
        )
        storage.record_event(
            publication_id=publication_id,
            destination_id=destination_id,
            kind="cancelled",
            state=actualizado["state"],
            detail={"stopped": True, "previous_state": estado.value},
        )
        return actualizado

    explicacion = {
        DestinationState.DISPATCHING: (
            "ya hay una operacion externa iniciada: se registra la peticion y se "
            "deja de crear nada nuevo, pero lo ya enviado no se retira desde aqui"
        ),
        DestinationState.WAITING_REMOTE: (
            "hay un identificador remoto en curso: no se cancela en la plataforma; "
            "se sigue consultando su estado para poder informar"
        ),
        DestinationState.NEEDS_RECONCILIATION: (
            "el resultado es ambiguo: hasta resolverlo no se crea nada nuevo, y "
            "cancelar aqui no aclara que paso en la plataforma"
        ),
        DestinationState.DELIVERED: (
            "ya esta entregado: retirar una publicacion existente queda fuera de "
            "este MVP y se hace desde las herramientas de la plataforma"
        ),
        DestinationState.MANUALLY_REPORTED: (
            "lo publico una persona: retirarlo tambien le corresponde a ella"
        ),
    }.get(estado, f"el destino esta en {estado.value} y no hay nada local que detener")

    actualizado = storage.update_destination(
        publication_id,
        destination_id,
        cancel_requested_at=iso(now),
        cancel_note=(note + " | " if note else "") + explicacion,
    )
    storage.record_event(
        publication_id=publication_id,
        destination_id=destination_id,
        kind="cancel_requested",
        state=fila["state"],
        detail={"stopped": False, "explanation": explicacion},
    )
    return actualizado


def mark_expired(
    storage: PublishStorage, fila: dict, *, now: datetime, verdict: DueVerdict
) -> dict:
    """Una tarea que perdio su ventana pasa a revision, no a publicarse."""
    actualizado = storage.update_destination(
        fila["publication_id"],
        fila["destination_id"],
        state=DestinationState.NEEDS_REVIEW,
    )
    storage.record_event(
        publication_id=fila["publication_id"],
        destination_id=fila["destination_id"],
        kind="late_start_expired",
        state=actualizado["state"],
        detail={
            "seconds_late": round(verdict.seconds_late),
            "late_start_window_s": int(fila["late_start_window_s"]),
            "reason": verdict.reason,
        },
    )
    return actualizado


def worker_owner(prefix: str = "worker") -> str:
    """Identidad de esta invocacion del trabajador, para la concesion."""
    import os

    return f"{prefix}:{os.getpid()}:{iso(utcnow())}"


def identity_space_of(mode: PublishMode) -> str:
    return identity_space(mode)
