"""Exportacion del estado persistido a `publication.json`.

El recibo se CONSTRUYE desde SQLite cada vez. No se guarda un JSON y se va
parcheando: eso permitiria que un archivo editado a mano pareciera la verdad.
Aqui el archivo es una foto; la verdad esta en la base de datos, y los
permisos, en la tabla de autorizaciones.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path

from ..diskutil import sha256_file
from ..schemas.common import Platform
from .authorize import from_row as authorization_from_row
from .clock import Clock, SystemClock
from .schemas import (
    BudgetUsage,
    DestinationState,
    EvidenceRecord,
    ManualExportRef,
    ManualReport,
    PublicationPlan,
    PublicationReceipt,
    PublishMode,
    ReceiptDestination,
    StagingRef,
    StructuredError,
    TransferPhase,
    Visibility,
    summarize,
)
from .storage import PublishStorage


def _modelo(texto: str | None, modelo):
    return modelo.model_validate(json.loads(texto)) if texto else None


def _visibilidad(valor: str | None) -> Visibility | None:
    return Visibility(valor) if valor else None


def destination_from_row(fila: dict, *, request_limit: int, attempt_limit: int) -> ReceiptDestination:
    """Traduce una fila a su vista de contrato, sin inventar nada."""
    return ReceiptDestination(
        destination_id=fila["destination_id"],
        platform=Platform(fila["platform"]),
        account_alias=fila["account_alias"],
        expected_account_id=fila["expected_account_id"],
        observed_account_id=fila["observed_account_id"],
        state=DestinationState(fila["state"]),
        phase=TransferPhase(fila["phase"]),
        simulation=bool(fila["simulation"]),
        requested_visibility=Visibility(fila["requested_visibility"]),
        observed_visibility=_visibilidad(fila["observed_visibility"]),
        publicly_visible=(
            None if fila["publicly_visible"] is None else bool(fila["publicly_visible"])
        ),
        real_remote_id=fila["real_remote_id"],
        mock_remote_id=fila["mock_remote_id"],
        remote_refs=json.loads(fila["remote_refs_json"] or "{}"),
        permalink=fila["permalink"],
        scheduled_at_utc=fila["scheduled_at"],
        timezone=fila["timezone"],
        dispatch_started_at=fila["dispatch_started_at"],
        delivered_at=fila["delivered_at"],
        next_attempt_at=fila["next_attempt_at"],
        next_poll_at=fila["next_poll_at"],
        budget=BudgetUsage(
            requests_used=int(fila["requests_used"]),
            request_limit=request_limit,
            bytes_transferred=int(fila["bytes_transferred"]),
            attempts=int(fila["attempts"]),
            operation_attempts=int(fila["operation_attempts"]),
            attempt_limit=attempt_limit,
        ),
        last_error=_modelo(fila["last_error_json"], StructuredError),
        last_evidence=_modelo(fila["last_evidence_json"], EvidenceRecord),
        staging=_modelo(fila["staging_json"], StagingRef),
        manual_export=_modelo(fila["manual_export_json"], ManualExportRef),
        manual_report=_modelo(fila["manual_report_json"], ManualReport),
        cancel_requested_at=fila["cancel_requested_at"],
        cancel_note=fila["cancel_note"],
    )


def build_receipt(
    storage: PublishStorage,
    *,
    publication_id: str,
    settings,
    clock: Clock | None = None,
) -> PublicationReceipt:
    """Arma el recibo desde la base de datos."""
    reloj = clock or SystemClock()
    trabajo = storage.get_publication(publication_id)
    if trabajo is None:
        raise KeyError(publication_id)
    plan = PublicationPlan.model_validate_json(trabajo["plan_json"])
    filas = storage.list_destinations(publication_id)
    if not filas:
        raise ValueError(
            f"el trabajo {publication_id} no tiene destinos persistidos: "
            "ponlo en cola antes de pedir su recibo"
        )
    destinos = [
        destination_from_row(
            fila,
            request_limit=int(settings.publish_max_requests_per_destination),
            attempt_limit=int(settings.publish_max_attempts_per_operation),
        )
        for fila in filas
    ]
    autorizacion_fila = storage.active_authorization(publication_id)
    return PublicationReceipt(
        receipt_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"receipt:{publication_id}")),
        publish_key=trabajo["publish_key"],
        intent_fingerprint=trabajo["intent_fingerprint"],
        plan_revision=int(trabajo["plan_revision"]),
        plan_sha256=trabajo["plan_sha256"],
        mode=PublishMode(trabajo["mode"]),
        simulation=bool(trabajo["simulation"]),
        created_at=trabajo["created_at"],
        updated_at=reloj.now(),
        sources=plan.sources,
        authorization=(
            authorization_from_row(autorizacion_fila) if autorizacion_fila else None
        ),
        destinations=destinos,
        summary=summarize(destinos),
    )


def receipt_to_json(recibo: PublicationReceipt) -> str:
    return json.dumps(recibo.model_dump(mode="json"), ensure_ascii=False, indent=2)


def write_receipt(path: Path, recibo: PublicationReceipt) -> str:
    """Escribe el recibo de forma atomica y devuelve su SHA-256."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporal = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as manejador:
            manejador.write(receipt_to_json(recibo) + "\n")
            manejador.flush()
            os.fsync(manejador.fileno())
        os.replace(temporal, path)
    except BaseException:
        Path(temporal).unlink(missing_ok=True)
        raise
    return sha256_file(path)


def load_receipt(path: Path) -> PublicationReceipt:
    """Lee un recibo. Sirve para inspeccionarlo, NO para conceder permisos.

    Un recibo editado sigue siendo un archivo: no cambia la base de datos, no
    autoriza nada y no demuestra que una plataforma haya recibido nada.
    """
    return PublicationReceipt.model_validate_json(path.read_text(encoding="utf-8"))


