"""Cola, concesiones, identidad y autorizacion persistida.

Aqui se prueban los comportamientos que pierden trazabilidad o publican de
mas: reclamar dos veces la misma tarea, reiniciar y creerse con presupuesto
nuevo, colar el mismo video con otra clave, o poner en cola algo que nadie
autorizo.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from viralgen.config import Settings
from viralgen.errors import IdempotencyConflictError
from viralgen.publish.authorize import (
    build_authorization,
    find_authorization,
    require_authorization,
    store_authorization,
)
from viralgen.publish.clock import ManualClock
from viralgen.publish.errors import (
    AuthorizationRequiredError,
    DuplicateDeliveryError,
    ModeViolationError,
)
from viralgen.publish.queue import (
    enqueue_plan,
    mark_expired,
    request_cancel,
    start_verdict,
)
from viralgen.publish.receipt import build_receipt, load_receipt, write_receipt
from viralgen.publish.schemas import (
    DestinationState,
    EvidenceRecord,
    EvidenceSource,
    OverallOutcome,
    PendingRequirement,
    TransferPhase,
    Visibility,
)
from viralgen.publish.storage import PublishStorage
from viralgen.storage import Storage

from conftest import publish_destination, publish_plan

UTC = timezone.utc
AHORA = datetime(2026, 9, 24, 16, 0, tzinfo=UTC)
PUB_ID = "00000000-0000-4000-8000-0000000000aa"


@pytest.fixture
def almacen(tmp_path: Path) -> PublishStorage:
    almacenamiento = Storage(tmp_path / "datos")
    publicacion = PublishStorage(almacenamiento)
    publicacion.migrate()
    return publicacion


def _autorizar(almacen: PublishStorage, plan, *, publication_id: str = PUB_ID):
    registro = build_authorization(
        plan,
        plan_sha256="e" * 64,
        operator_identity="operador_local",
        clock=ManualClock(AHORA - timedelta(hours=1)),
    )
    return store_authorization(
        almacen, publication_id=publication_id, registro=registro
    )


def _encolar(almacen: PublishStorage, plan, settings: Settings, **kwargs):
    return enqueue_plan(
        almacen,
        plan=plan,
        plan_sha256="e" * 64,
        publication_id=kwargs.pop("publication_id", PUB_ID),
        settings=settings,
        clock=ManualClock(AHORA),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Autorizacion
# ---------------------------------------------------------------------------


def test_sin_autorizacion_no_se_encola_nada(almacen: PublishStorage, settings: Settings) -> None:
    plan = publish_plan()
    with pytest.raises(AuthorizationRequiredError):
        _encolar(almacen, plan, settings)
    # Y el trabajo queda sin destinos: no se aparca trabajo sin permiso.
    assert almacen.list_destinations(PUB_ID) == []


def test_la_autorizacion_sobrevive_al_reinicio(tmp_path: Path, settings: Settings) -> None:
    """Se guarda en SQLite, no en memoria: reabrir el proceso no la pierde."""
    plan = publish_plan()
    primera = PublishStorage(Storage(tmp_path / "datos"))
    primera.migrate()
    _autorizar(primera, plan)
    primera.storage.close()

    segunda = PublishStorage(Storage(tmp_path / "datos"))
    recuperada = find_authorization(segunda, publication_id=PUB_ID)
    assert recuperada is not None
    assert recuperada.covers(plan)


def test_cambiar_la_intencion_exige_reautorizar(almacen: PublishStorage) -> None:
    plan = publish_plan()
    _autorizar(almacen, plan)
    otro = publish_plan(
        destinations=[publish_destination(requested_visibility=Visibility.PUBLIC)]
    )
    with pytest.raises(AuthorizationRequiredError, match="no cubre esta revision"):
        require_authorization(almacen, publication_id=PUB_ID, plan=otro)


def test_reautorizar_retira_la_anterior(almacen: PublishStorage) -> None:
    plan = publish_plan()
    primera = _autorizar(almacen, plan)
    otro = publish_plan(
        destinations=[publish_destination(requested_visibility=Visibility.PUBLIC)]
    )
    segunda = build_authorization(
        otro, plan_sha256="f" * 64, operator_identity="operador_local",
        clock=ManualClock(AHORA),
    )
    store_authorization(almacen, publication_id=PUB_ID, registro=segunda)

    viva = find_authorization(almacen, publication_id=PUB_ID)
    assert viva is not None
    assert viva.authorization_id == segunda.authorization_id
    assert viva.authorization_id != primera.authorization_id
    assert viva.covers(otro) and not viva.covers(plan)


def test_un_destino_bloqueado_no_entra_en_la_cola(
    almacen: PublishStorage, settings: Settings
) -> None:
    destino = publish_destination(
        state=DestinationState.BLOCKED,
        pending_requirements=[
            PendingRequirement(
                code="sin_titulo",
                message="falta el titulo",
                blocks="all",
                resolution="escribelo en el plan",
            )
        ],
    )
    plan = publish_plan(destinations=[destino])
    _autorizar(almacen, plan)
    with pytest.raises(ModeViolationError, match="requisitos sin resolver"):
        _encolar(almacen, plan, settings)


# ---------------------------------------------------------------------------
# Identidad y duplicados
# ---------------------------------------------------------------------------


def test_la_misma_clave_recupera_la_misma_tarea(
    almacen: PublishStorage, settings: Settings
) -> None:
    plan = publish_plan()
    _autorizar(almacen, plan)
    primero = _encolar(almacen, plan, settings)
    segundo = _encolar(almacen, plan, settings)
    assert primero.publication["publication_id"] == segundo.publication["publication_id"]
    assert len(almacen.list_destinations(PUB_ID)) == 1


def test_la_misma_clave_con_otra_intencion_es_conflicto(
    almacen: PublishStorage, settings: Settings
) -> None:
    plan = publish_plan()
    _autorizar(almacen, plan)
    _encolar(almacen, plan, settings)

    otro = publish_plan(
        destinations=[publish_destination(requested_visibility=Visibility.PUBLIC)]
    )
    _autorizar(almacen, otro)
    with pytest.raises(IdempotencyConflictError, match="otra intencion"):
        _encolar(almacen, otro, settings)


def test_el_mismo_video_con_otra_clave_es_duplicado(
    almacen: PublishStorage, settings: Settings
) -> None:
    """La identidad no es el titulo: es el video, la plataforma y la cuenta."""
    plan = publish_plan()
    _autorizar(almacen, plan)
    _encolar(almacen, plan, settings)

    otra_clave = publish_plan(publish_key="demo-002")
    otro_id = "00000000-0000-4000-8000-0000000000bb"
    _autorizar(almacen, otra_clave, publication_id=otro_id)
    with pytest.raises(DuplicateDeliveryError, match="ya esta reservado"):
        _encolar(almacen, otra_clave, settings, publication_id=otro_id)


def test_los_espacios_de_identidad_no_se_mezclan(
    almacen: PublishStorage, settings: Settings
) -> None:
    """La misma clave simulada y real son dos trabajos independientes."""
    from viralgen.publish.schemas import PublishMode

    simulado = publish_plan(mode=PublishMode.MOCK)
    _autorizar(almacen, simulado)
    _encolar(almacen, simulado, settings)

    real = publish_plan(mode=PublishMode.REAL)
    otro_id = "00000000-0000-4000-8000-0000000000cc"
    _autorizar(almacen, real, publication_id=otro_id)
    resultado = _encolar(almacen, real, settings, publication_id=otro_id)

    assert resultado.publication["identity_space"] == "real"
    assert almacen.find_publication("demo-001", identity_space="mock") is not None
    assert almacen.find_publication("demo-001", identity_space="real") is not None


# ---------------------------------------------------------------------------
# Concesion del trabajador
# ---------------------------------------------------------------------------


def _preparar(almacen: PublishStorage, settings: Settings):
    plan = publish_plan()
    _autorizar(almacen, plan)
    _encolar(almacen, plan, settings)
    return plan


def test_dos_invocaciones_no_reclaman_el_mismo_destino(
    almacen: PublishStorage, settings: Settings
) -> None:
    _preparar(almacen, settings)
    primera = almacen.claim_destination(
        PUB_ID, "yt_principal", owner="worker:1", now=AHORA, lease_seconds=300
    )
    segunda = almacen.claim_destination(
        PUB_ID, "yt_principal", owner="worker:2", now=AHORA, lease_seconds=300
    )
    assert primera is not None
    assert segunda is None


def test_una_concesion_vencida_se_recupera_tras_una_caida(
    almacen: PublishStorage, settings: Settings
) -> None:
    """Si el proceso muere, la tarea no queda bloqueada para siempre."""
    _preparar(almacen, settings)
    almacen.claim_destination(
        PUB_ID, "yt_principal", owner="worker:muerto", now=AHORA, lease_seconds=300
    )
    despues = AHORA + timedelta(seconds=301)
    recuperada = almacen.claim_destination(
        PUB_ID, "yt_principal", owner="worker:vivo", now=despues, lease_seconds=300
    )
    assert recuperada is not None
    assert recuperada["lease_owner"] == "worker:vivo"


def test_la_concesion_se_renueva_durante_una_transferencia_larga(
    almacen: PublishStorage, settings: Settings
) -> None:
    _preparar(almacen, settings)
    almacen.claim_destination(
        PUB_ID, "yt_principal", owner="worker:1", now=AHORA, lease_seconds=300
    )
    assert almacen.renew_lease(
        PUB_ID, "yt_principal", owner="worker:1", now=AHORA + timedelta(seconds=200),
        lease_seconds=300,
    )
    assert not almacen.renew_lease(
        PUB_ID, "yt_principal", owner="worker:otro", now=AHORA, lease_seconds=300
    )


# ---------------------------------------------------------------------------
# Reloj de la cola
# ---------------------------------------------------------------------------


def test_una_tarea_futura_no_esta_lista(almacen: PublishStorage, settings: Settings) -> None:
    _preparar(almacen, settings)
    fila = almacen.get_destination(PUB_ID, "yt_principal")
    veredicto = start_verdict(fila, now=AHORA)  # programada a las 16:30Z
    assert not veredicto.due and not veredicto.expired
    assert almacen.due_destinations(now=AHORA) == []


def test_una_tarea_debida_aparece_en_la_cola(
    almacen: PublishStorage, settings: Settings
) -> None:
    _preparar(almacen, settings)
    debidas = almacen.due_destinations(now=AHORA + timedelta(minutes=35))
    assert [fila["destination_id"] for fila in debidas] == ["yt_principal"]
    assert start_verdict(debidas[0], now=AHORA + timedelta(minutes=35)).due


def test_una_ventana_vencida_manda_a_revision_en_vez_de_publicar(
    almacen: PublishStorage, settings: Settings
) -> None:
    """Tras una caida larga no sale todo lo acumulado de golpe."""
    _preparar(almacen, settings)
    tarde = AHORA + timedelta(hours=12)
    fila = almacen.get_destination(PUB_ID, "yt_principal")
    veredicto = start_verdict(fila, now=tarde)
    assert veredicto.expired and not veredicto.due

    actualizado = mark_expired(almacen, fila, now=tarde, verdict=veredicto)
    assert actualizado["state"] == DestinationState.NEEDS_REVIEW.value
    tipos = [evento["kind"] for evento in almacen.events(PUB_ID)]
    assert "late_start_expired" in tipos


# ---------------------------------------------------------------------------
# Estados, gasto y cancelacion
# ---------------------------------------------------------------------------


def test_una_transicion_no_documentada_se_rechaza(
    almacen: PublishStorage, settings: Settings
) -> None:
    _preparar(almacen, settings)
    with pytest.raises(ValueError, match="transicion no documentada"):
        almacen.update_destination(
            PUB_ID, "yt_principal", state=DestinationState.DELIVERED
        )


def test_el_presupuesto_persiste_tras_reiniciar(tmp_path: Path, settings: Settings) -> None:
    """Abrir otro proceso no regala solicitudes ni bytes."""
    plan = publish_plan()
    primera = PublishStorage(Storage(tmp_path / "datos"))
    primera.migrate()
    _autorizar(primera, plan)
    enqueue_plan(
        primera, plan=plan, plan_sha256="e" * 64, publication_id=PUB_ID,
        settings=settings, clock=ManualClock(AHORA),
    )
    primera.update_destination(
        PUB_ID, "yt_principal", requests_delta=7, bytes_delta=1_048_576, attempts_delta=1
    )
    primera.storage.close()

    segunda = PublishStorage(Storage(tmp_path / "datos"))
    fila = segunda.get_destination(PUB_ID, "yt_principal")
    assert (fila["requests_used"], fila["bytes_transferred"], fila["attempts"]) == (
        7,
        1_048_576,
        1,
    )
    segunda.update_destination(PUB_ID, "yt_principal", requests_delta=3)
    assert segunda.get_destination(PUB_ID, "yt_principal")["requests_used"] == 10


def test_cancelar_antes_de_empezar_detiene_la_tarea(
    almacen: PublishStorage, settings: Settings
) -> None:
    _preparar(almacen, settings)
    fila = request_cancel(
        almacen, publication_id=PUB_ID, destination_id="yt_principal", now=AHORA
    )
    assert fila["state"] == DestinationState.CANCELLED.value


def test_cancelar_despues_de_enviar_registra_y_explica(
    almacen: PublishStorage, settings: Settings
) -> None:
    """No se declara una cancelacion remota ni se borra una publicacion."""
    _preparar(almacen, settings)
    almacen.update_destination(
        PUB_ID, "yt_principal", state=DestinationState.DISPATCHING,
        phase=TransferPhase.UPLOADING.value,
    )
    fila = request_cancel(
        almacen, publication_id=PUB_ID, destination_id="yt_principal", now=AHORA
    )
    assert fila["state"] == DestinationState.DISPATCHING.value
    assert fila["cancel_requested_at"] is not None
    assert "no se retira desde aqui" in fila["cancel_note"]
    evento = almacen.events(PUB_ID)[0]
    assert evento["kind"] == "cancel_requested"


# ---------------------------------------------------------------------------
# Recibo
# ---------------------------------------------------------------------------


def test_el_recibo_sale_de_la_base_de_datos(
    almacen: PublishStorage, settings: Settings, tmp_path: Path
) -> None:
    _preparar(almacen, settings)
    recibo = build_receipt(
        almacen, publication_id=PUB_ID, settings=settings, clock=ManualClock(AHORA)
    )
    assert recibo.summary.overall is OverallOutcome.SCHEDULED
    assert recibo.destinations[0].state is DestinationState.SCHEDULED_LOCAL
    assert recibo.authorization is not None
    assert recibo.simulation is True

    destino = tmp_path / "publication.json"
    write_receipt(destino, recibo)
    releido = load_receipt(destino)
    assert releido.intent_fingerprint == recibo.intent_fingerprint


def test_un_recibo_editado_no_cambia_la_base_de_datos(
    almacen: PublishStorage, settings: Settings, tmp_path: Path
) -> None:
    """Editar el archivo no concede autorizacion ni demuestra exito remoto."""
    import json

    _preparar(almacen, settings)
    recibo = build_receipt(
        almacen, publication_id=PUB_ID, settings=settings, clock=ManualClock(AHORA)
    )
    destino = tmp_path / "publication.json"
    write_receipt(destino, recibo)

    datos = json.loads(destino.read_text(encoding="utf-8"))
    datos["destinations"][0]["state"] = "delivered"
    datos["destinations"][0]["real_remote_id"] = "dQw4w9WgXcQ"
    destino.write_text(json.dumps(datos), encoding="utf-8")

    # El archivo ni siquiera es un recibo valido: una simulacion no tiene ID
    # remoto real, y una entrega exige evidencia.
    with pytest.raises(Exception):
        load_receipt(destino)

    # Y la base de datos sigue diciendo la verdad.
    fila = almacen.get_destination(PUB_ID, "yt_principal")
    assert fila["state"] == DestinationState.SCHEDULED_LOCAL.value
    assert fila["real_remote_id"] is None


def test_el_resumen_del_recibo_distingue_lo_entregado_de_lo_visible(
    almacen: PublishStorage, settings: Settings
) -> None:
    _preparar(almacen, settings)
    almacen.update_destination(
        PUB_ID, "yt_principal", state=DestinationState.DISPATCHING
    )
    almacen.update_destination(
        PUB_ID,
        "yt_principal",
        state=DestinationState.DELIVERED,
        phase=TransferPhase.VERIFIED.value,
        mock_remote_id="mock-yt-1",
        observed_visibility=Visibility.PRIVATE.value,
        publicly_visible=0,
        last_evidence_json=EvidenceRecord(
            source=EvidenceSource.SIMULATED,
            checked_at=AHORA,
            summary="publicador simulado: recurso consultado",
        ).model_dump_json(),
    )
    recibo = build_receipt(
        almacen, publication_id=PUB_ID, settings=settings, clock=ManualClock(AHORA)
    )
    assert recibo.summary.overall is OverallOutcome.DELIVERED
    assert recibo.summary.delivered == 1
    assert recibo.summary.publicly_visible == 0
    assert recibo.destinations[0].publicly_visible is False


def test_un_uuid_de_recibo_es_estable(almacen: PublishStorage, settings: Settings) -> None:
    _preparar(almacen, settings)
    uno = build_receipt(almacen, publication_id=PUB_ID, settings=settings)
    otro = build_receipt(almacen, publication_id=PUB_ID, settings=settings)
    assert uno.receipt_id == otro.receipt_id
    assert uuid.UUID(uno.receipt_id)
