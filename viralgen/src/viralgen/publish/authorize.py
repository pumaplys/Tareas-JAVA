"""Autorizacion del operador: el segundo de los tres permisos.

Los tres son distintos y ninguno implica otro:

1. **Admision tecnica** - el paquete cumple sus contratos (modulo 4 + admision
   de este).
2. **Autorizacion del operador** - una persona de esta instalacion ha dicho
   "esto, a esta cuenta, con este texto, a esta hora". Es lo que hay aqui.
3. **Estado remoto** - lo que la plataforma dice que ha pasado.

La autorizacion queda atada a la INTENCION, no al archivo: reexportar el mismo
plan no la invalida, y renovar un token de la misma cuenta o reemitir una URL
temporal del mismo objeto tampoco, porque ninguna de esas cosas cambia que se
publica ni donde. Cambiar cuenta, video, texto, privacidad, horario o destino
si: eso es una revision nueva y se vuelve a pedir.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from ..storage import iso
from .clock import Clock, SystemClock
from .errors import AuthorizationRequiredError
from .schemas import AuthorizationRecord, PublicationPlan, PublishMode
from .storage import PublishStorage


def identity_space(mode: PublishMode) -> str:
    """Espacio de identidad del modo. Simulado y real nunca se mezclan."""
    return "real" if mode is PublishMode.REAL else "mock"


def _account_ids(plan: PublicationPlan) -> dict[str, str]:
    return {
        destino.destination_id: (destino.account.expected_account_id or "")
        for destino in plan.destinations
    }


def build_authorization(
    plan: PublicationPlan,
    *,
    plan_sha256: str,
    operator_identity: str,
    clock: Clock | None = None,
    staging_authorized: bool | None = None,
) -> AuthorizationRecord:
    """Arma el registro. El fingerprint se RECALCULA del plan, no se copia."""
    reloj = clock or SystemClock()
    necesita_staging = any(
        destino.options.requires_staging for destino in plan.destinations
    )
    return AuthorizationRecord(
        authorization_id=str(uuid.uuid4()),
        authorized_at=reloj.now(),
        operator_identity=operator_identity,
        mode=plan.mode,
        intent_fingerprint=plan.compute_intent_fingerprint(),
        plan_sha256=plan_sha256,
        plan_revision=plan.revision,
        video_sha256=plan.sources.video.sha256,
        account_ids=_account_ids(plan),
        staging_authorized=(
            necesita_staging if staging_authorized is None else staging_authorized
        ),
    )


def to_row(registro: AuthorizationRecord, *, publication_id: str) -> dict:
    return {
        "authorization_id": registro.authorization_id,
        "publication_id": publication_id,
        "intent_fingerprint": registro.intent_fingerprint,
        "plan_sha256": registro.plan_sha256,
        "plan_revision": registro.plan_revision,
        "video_sha256": registro.video_sha256,
        "account_ids_json": json.dumps(registro.account_ids, ensure_ascii=False),
        "mode": registro.mode.value,
        "operator_identity": registro.operator_identity,
        "staging_authorized": int(registro.staging_authorized),
        "authorized_at": iso(registro.authorized_at),
        "revoked_at": iso(registro.revoked_at) if registro.revoked_at else None,
    }


def from_row(fila: dict) -> AuthorizationRecord:
    return AuthorizationRecord(
        authorization_id=fila["authorization_id"],
        authorized_at=fila["authorized_at"],
        operator_identity=fila["operator_identity"],
        mode=PublishMode(fila["mode"]),
        intent_fingerprint=fila["intent_fingerprint"],
        plan_sha256=fila["plan_sha256"],
        plan_revision=int(fila["plan_revision"]),
        video_sha256=fila["video_sha256"],
        account_ids=json.loads(fila["account_ids_json"]),
        staging_authorized=bool(fila["staging_authorized"]),
        revoked_at=fila["revoked_at"],
    )


def store_authorization(
    storage: PublishStorage,
    *,
    publication_id: str,
    registro: AuthorizationRecord,
    revoke_previous: bool = True,
    now: datetime | None = None,
) -> AuthorizationRecord:
    """Guarda la autorizacion y retira la anterior si la habia.

    Una autorizacion por intencion: dejar dos vivas invitaria a que la vieja
    cubriese algo que ya no describe.
    """
    if revoke_previous:
        storage.revoke_authorizations(
            publication_id, at=now or registro.authorized_at
        )
    storage.save_authorization(to_row(registro, publication_id=publication_id))
    storage.record_event(
        publication_id=publication_id,
        kind="authorized",
        detail={
            "authorization_id": registro.authorization_id,
            "operator_identity": registro.operator_identity,
            "intent_fingerprint": registro.intent_fingerprint,
            "plan_revision": registro.plan_revision,
            "staging_authorized": registro.staging_authorized,
        },
    )
    return registro


def find_authorization(
    storage: PublishStorage, *, publication_id: str
) -> AuthorizationRecord | None:
    fila = storage.active_authorization(publication_id)
    return from_row(fila) if fila else None


def require_authorization(
    storage: PublishStorage, *, publication_id: str, plan: PublicationPlan
) -> AuthorizationRecord:
    """Exige una autorizacion viva que cubra EXACTAMENTE esta intencion."""
    registro = find_authorization(storage, publication_id=publication_id)
    if registro is None:
        raise AuthorizationRequiredError(
            "No hay ninguna autorizacion viva para este trabajo. Revisa el plan "
            "y ejecuta `publish approve`.",
            details={"publication_id": publication_id},
        )
    if registro.covers(plan):
        return registro

    motivos = []
    if registro.mode is not plan.mode:
        motivos.append(
            f"la autorizacion es del modo {registro.mode.value} y el plan es "
            f"{plan.mode.value}"
        )
    if registro.video_sha256 != plan.sources.video.sha256:
        motivos.append("el video autorizado no es este")
    if registro.account_ids != _account_ids(plan):
        motivos.append("las cuentas de destino han cambiado")
    if registro.intent_fingerprint != plan.compute_intent_fingerprint():
        motivos.append(
            "la intencion ha cambiado (texto, privacidad, horario o destino)"
        )
    raise AuthorizationRequiredError(
        "La autorizacion existente no cubre esta revision: "
        + "; ".join(motivos)
        + ". Vuelve a autorizar el plan actual.",
        details={
            "publication_id": publication_id,
            "authorized_fingerprint": registro.intent_fingerprint,
            "current_fingerprint": plan.compute_intent_fingerprint(),
            "reasons": motivos,
        },
    )
