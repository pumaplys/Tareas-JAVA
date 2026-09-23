"""Contratos del modulo 5: plan, recibo, reloj, estados e identidad.

Lo que se prueba aqui no es que pydantic sepa validar, sino las reglas de
producto que impiden publicar de mas: que una autorizacion deje de valer en
cuanto cambia la intencion, que un recibo no pueda declarar una entrega sin
evidencia, que una hora inexistente no se desplace sola y que ninguna URL
firmada acabe dentro de un documento exportable.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from viralgen.publish import clock
from viralgen.publish.schemas import (
    ALLOWED_TRANSITIONS,
    AuthorizationRecord,
    BudgetUsage,
    DestinationState,
    ErrorClass,
    EvidenceRecord,
    EvidenceSource,
    ManualReport,
    OverallOutcome,
    PublishMode,
    ReceiptDestination,
    ScheduleSpec,
    StructuredError,
    TERMINAL_STATES,
    Visibility,
    assert_no_secret_material,
    summarize,
    transition_allowed,
)
from viralgen.schemas.common import Platform

from conftest import publish_destination, publish_plan, publish_sources

UTC = timezone.utc


def _destino_recibo(**cambios) -> ReceiptDestination:
    base = dict(
        destination_id="yt_principal",
        platform=Platform.YOUTUBE_SHORTS,
        account_alias="canal_curiosidades",
        expected_account_id="UC_canal_de_pruebas",
        state=DestinationState.SCHEDULED_LOCAL,
        simulation=True,
        requested_visibility=Visibility.PRIVATE,
        budget=BudgetUsage(request_limit=200, attempt_limit=3),
    )
    base.update(cambios)
    return ReceiptDestination(**base)


# ---------------------------------------------------------------------------
# Reloj y horarios
# ---------------------------------------------------------------------------


def test_una_hora_inexistente_no_se_desplaza_sola() -> None:
    """El salto de primavera se rechaza; corregirlo es del operador."""
    zona = clock.ensure_zone("Europe/Madrid")
    pared = clock.parse_wall("2026-03-29T02:30:00")
    assert clock.is_nonexistent(pared, zona)
    with pytest.raises(clock.ScheduleError, match="no existe"):
        clock.local_to_utc(pared, zona)


def test_una_hora_repetida_exige_elegir_cual() -> None:
    """Las 02:30 del cambio de otono ocurren dos veces: hay que decir cual."""
    zona = clock.ensure_zone("Europe/Madrid")
    pared = clock.parse_wall("2026-10-25T02:30:00")
    assert clock.is_ambiguous(pared, zona)
    with pytest.raises(clock.ScheduleError, match="fold"):
        clock.local_to_utc(pared, zona)

    primera = clock.local_to_utc(pared, zona, fold=0)
    segunda = clock.local_to_utc(pared, zona, fold=1)
    assert segunda - primera == timedelta(hours=1)


def test_una_zona_desconocida_no_se_convierte_en_utc() -> None:
    with pytest.raises(clock.ScheduleError, match="desconocida"):
        clock.ensure_zone("Europe/Madriz")


def test_el_horario_del_plan_rehace_la_conversion() -> None:
    """Un instante UTC que no corresponde a su hora local no pasa."""
    with pytest.raises(ValidationError, match="no corresponde"):
        ScheduleSpec(
            scheduled_at_utc=datetime(2026, 9, 24, 12, 0, tzinfo=UTC),
            timezone="Europe/Madrid",
            local_time="2026-09-24T18:30:00",
            late_start_window_s=900,
        )


def test_el_horario_conserva_la_zona_del_operador() -> None:
    horario = ScheduleSpec(
        scheduled_at_utc=datetime(2026, 9, 24, 16, 30, tzinfo=UTC),
        timezone="Europe/Madrid",
        local_time="2026-09-24T18:30:00",
        late_start_window_s=900,
    )
    assert horario.timezone == "Europe/Madrid"
    assert horario.scheduled_at_utc.hour == 16


def test_la_ventana_de_inicio_caduca_en_vez_de_publicar_lo_acumulado() -> None:
    """Tras una caida larga, lo vencido pide revision; no sale de golpe."""
    previsto = datetime(2026, 9, 24, 16, 30, tzinfo=UTC)
    temprano = clock.evaluate_due(previsto, previsto - timedelta(minutes=5), late_window_s=900)
    assert not temprano.due and not temprano.expired

    a_tiempo = clock.evaluate_due(previsto, previsto + timedelta(minutes=10), late_window_s=900)
    assert a_tiempo.due

    tarde = clock.evaluate_due(previsto, previsto + timedelta(hours=9), late_window_s=900)
    assert not tarde.due and tarde.expired
    assert "revision" in tarde.reason


def test_el_reloj_de_pruebas_solo_avanza_cuando_se_le_dice() -> None:
    reloj = clock.ManualClock(datetime(2026, 9, 24, 16, 0, tzinfo=UTC))
    assert reloj.now() == datetime(2026, 9, 24, 16, 0, tzinfo=UTC)
    reloj.advance(3600)
    assert reloj.now() == datetime(2026, 9, 24, 17, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Estados
# ---------------------------------------------------------------------------


def test_las_transiciones_estan_documentadas_y_son_cerradas() -> None:
    """Cada estado declara sus salidas; no hay saltos implicitos."""
    assert set(ALLOWED_TRANSITIONS) == set(DestinationState)
    for salidas in ALLOWED_TRANSITIONS.values():
        assert salidas <= set(DestinationState)


def test_no_se_llega_a_entregado_sin_pasar_por_el_envio() -> None:
    assert not transition_allowed(
        DestinationState.SCHEDULED_LOCAL, DestinationState.DELIVERED
    )
    assert transition_allowed(DestinationState.DISPATCHING, DestinationState.DELIVERED)


def test_lo_entregado_y_lo_cancelado_no_vuelven_a_salir() -> None:
    assert ALLOWED_TRANSITIONS[DestinationState.DELIVERED] == frozenset()
    assert ALLOWED_TRANSITIONS[DestinationState.CANCELLED] == frozenset()
    assert DestinationState.DELIVERED in TERMINAL_STATES
    # `needs_reconciliation` NO es terminal: espera al operador.
    assert DestinationState.NEEDS_RECONCILIATION not in TERMINAL_STATES


def test_awaiting_manual_no_es_publicado() -> None:
    """Exportar para TikTok no entrega nada: solo prepara el paquete."""
    assert DestinationState.AWAITING_MANUAL not in TERMINAL_STATES
    assert DestinationState.DELIVERED not in ALLOWED_TRANSITIONS[
        DestinationState.AWAITING_MANUAL
    ]


# ---------------------------------------------------------------------------
# Recibo: lo que no se puede declarar
# ---------------------------------------------------------------------------


def test_una_entrega_exige_evidencia_y_visibilidad_observada() -> None:
    with pytest.raises(ValidationError, match="evidencia"):
        _destino_recibo(state=DestinationState.DELIVERED)


def test_una_simulacion_no_tiene_identificador_remoto_real() -> None:
    with pytest.raises(ValidationError, match="ID remoto real"):
        _destino_recibo(simulation=True, real_remote_id="dQw4w9WgXcQ")


def test_un_envio_real_no_lleva_identificadores_simulados() -> None:
    with pytest.raises(ValidationError, match="simulados"):
        _destino_recibo(simulation=False, mock_remote_id="mock-1")


def test_una_entrega_simulada_no_se_acredita_como_consulta_real() -> None:
    with pytest.raises(ValidationError, match="evidencia simulada"):
        _destino_recibo(
            state=DestinationState.DELIVERED,
            simulation=True,
            mock_remote_id="mock-1",
            observed_visibility=Visibility.PRIVATE,
            publicly_visible=False,
            last_evidence=EvidenceRecord(
                source=EvidenceSource.API_QUERY,
                checked_at=datetime(2026, 9, 24, 17, 0, tzinfo=UTC),
                summary="consulta del recurso",
            ),
        )


def test_privado_entregado_no_es_publicamente_visible() -> None:
    """Una entrega autorizada como privada se entrega, pero no se publica."""
    destino = _destino_recibo(
        state=DestinationState.DELIVERED,
        simulation=True,
        mock_remote_id="mock-1",
        requested_visibility=Visibility.PRIVATE,
        observed_visibility=Visibility.PRIVATE,
        publicly_visible=False,
        last_evidence=EvidenceRecord(
            source=EvidenceSource.SIMULATED,
            checked_at=datetime(2026, 9, 24, 17, 0, tzinfo=UTC),
            summary="publicador simulado",
        ),
    )
    assert destino.state is DestinationState.DELIVERED
    assert destino.publicly_visible is False

    with pytest.raises(ValidationError, match="observed_visibility=public"):
        _destino_recibo(
            state=DestinationState.DELIVERED,
            simulation=True,
            observed_visibility=Visibility.PRIVATE,
            publicly_visible=True,
            last_evidence=EvidenceRecord(
                source=EvidenceSource.SIMULATED,
                checked_at=datetime(2026, 9, 24, 17, 0, tzinfo=UTC),
                summary="publicador simulado",
            ),
        )


def test_un_reporte_manual_no_adjudica_un_id_verificado() -> None:
    reporte = ManualReport(
        reported_url="https://www.tiktok.com/@cuenta/video/123",
        reported_at=datetime(2026, 9, 24, 19, 0, tzinfo=UTC),
        recorded_at=datetime(2026, 9, 24, 19, 5, tzinfo=UTC),
    )
    destino = _destino_recibo(
        state=DestinationState.MANUALLY_REPORTED,
        platform=Platform.TIKTOK,
        manual_report=reporte,
    )
    assert destino.manual_report.evidence_source == "operator_reported"
    assert destino.real_remote_id is None


def test_un_resultado_ambiguo_no_se_marca_reintentable() -> None:
    with pytest.raises(ValidationError, match="no es reintentable"):
        StructuredError(
            code="timeout_tras_publicar",
            error_class=ErrorClass.AMBIGUOUS,
            message="se perdio la respuesta tras solicitar la publicacion",
            retryable=True,
            occurred_at=datetime(2026, 9, 24, 17, 0, tzinfo=UTC),
        )


def test_el_resumen_global_sale_de_los_estados() -> None:
    """Un exito parcial se dice partial; no se redondea a exito ni a fallo."""
    entregado = _destino_recibo(
        destination_id="yt_principal",
        state=DestinationState.DELIVERED,
        simulation=True,
        mock_remote_id="mock-1",
        observed_visibility=Visibility.PRIVATE,
        publicly_visible=False,
        last_evidence=EvidenceRecord(
            source=EvidenceSource.SIMULATED,
            checked_at=datetime(2026, 9, 24, 17, 0, tzinfo=UTC),
            summary="publicador simulado",
        ),
    )
    fallido = _destino_recibo(
        destination_id="ig_principal",
        platform=Platform.INSTAGRAM_REELS,
        state=DestinationState.FAILED,
        simulation=True,
    )
    resumen = summarize([entregado, fallido])
    assert resumen.overall is OverallOutcome.PARTIAL
    assert (resumen.delivered, resumen.failed, resumen.publicly_visible) == (1, 1, 0)

    en_curso = _destino_recibo(
        destination_id="ig_principal",
        platform=Platform.INSTAGRAM_REELS,
        state=DestinationState.WAITING_REMOTE,
        simulation=True,
    )
    assert summarize([entregado, en_curso]).overall is OverallOutcome.IN_PROGRESS

    reconciliar = _destino_recibo(
        destination_id="ig_principal",
        platform=Platform.INSTAGRAM_REELS,
        state=DestinationState.NEEDS_RECONCILIATION,
        simulation=True,
    )
    assert summarize([entregado, reconciliar]).overall is OverallOutcome.NEEDS_ATTENTION


# ---------------------------------------------------------------------------
# Identidad de la intencion y autorizacion
# ---------------------------------------------------------------------------


def _autorizacion(plan, **cambios) -> AuthorizationRecord:
    base = dict(
        authorization_id="00000000-0000-4000-8000-000000000099",
        authorized_at=datetime(2026, 9, 21, 10, 0, tzinfo=UTC),
        operator_identity="operador_local",
        mode=plan.mode,
        intent_fingerprint=plan.compute_intent_fingerprint(),
        plan_sha256="1" * 64,
        plan_revision=plan.revision,
        video_sha256=plan.sources.video.sha256,
        account_ids={
            destino.destination_id: destino.account.expected_account_id or ""
            for destino in plan.destinations
        },
    )
    base.update(cambios)
    return AuthorizationRecord(**base)


def test_una_autorizacion_valida_cubre_el_mismo_plan() -> None:
    plan = publish_plan()
    assert _autorizacion(plan).covers(plan)


@pytest.mark.parametrize(
    "cambio",
    [
        pytest.param({"metadata_title": "Otro titulo"}, id="texto"),
        pytest.param({"visibility": Visibility.PUBLIC}, id="privacidad"),
        pytest.param({"account": "otra_cuenta"}, id="cuenta"),
        pytest.param({"hora": "2026-09-24T19:30:00"}, id="hora"),
        pytest.param({"video": "9" * 64}, id="video"),
    ],
)
def test_cambiar_la_intencion_invalida_la_autorizacion(cambio: dict) -> None:
    """Cuenta, video, texto, privacidad u hora: cualquiera exige reautorizar."""
    plan = publish_plan()
    autorizacion = _autorizacion(plan)

    if "metadata_title" in cambio:
        metadata = plan.destinations[0].metadata.model_copy(
            update={"title": cambio["metadata_title"]}
        )
        nuevo = publish_plan(destinations=[publish_destination(metadata=metadata)])
    elif "visibility" in cambio:
        nuevo = publish_plan(
            destinations=[publish_destination(requested_visibility=cambio["visibility"])]
        )
    elif "account" in cambio:
        cuenta = plan.destinations[0].account.model_copy(
            update={"expected_account_id": cambio["account"]}
        )
        nuevo = publish_plan(destinations=[publish_destination(account=cuenta)])
    elif "hora" in cambio:
        horario = ScheduleSpec(
            scheduled_at_utc=datetime(2026, 9, 24, 17, 30, tzinfo=UTC),
            timezone="Europe/Madrid",
            local_time=cambio["hora"],
            late_start_window_s=900,
        )
        nuevo = publish_plan(destinations=[publish_destination(schedule=horario)])
    else:
        video = plan.sources.video.model_copy(update={"sha256": cambio["video"]})
        nuevo = publish_plan(sources=publish_sources(video=video))

    assert not autorizacion.covers(nuevo)


def test_reexportar_el_mismo_plan_no_invalida_la_autorizacion() -> None:
    """Otra fecha de emision o una nota nueva no cambian la intencion.

    Es el mismo motivo por el que renovar un token de la misma cuenta tampoco
    la cambia: no toca ni el contenido ni el destino autorizados.
    """
    plan = publish_plan()
    autorizacion = _autorizacion(plan)
    reexportado = publish_plan(
        plan_id="00000000-0000-4000-8000-000000000077",
        created_at=datetime(2026, 9, 22, 8, 0, tzinfo=UTC),
        notes=["reexportado para revisar"],
    )
    assert reexportado.compute_intent_fingerprint() == plan.compute_intent_fingerprint()
    assert autorizacion.covers(reexportado)


def test_el_espacio_de_identidad_separa_simulacion_y_real() -> None:
    """La misma intencion en mock y en real NO comparte fingerprint."""
    simulado = publish_plan(mode=PublishMode.MOCK)
    real = publish_plan(mode=PublishMode.REAL)
    assert simulado.compute_intent_fingerprint() != real.compute_intent_fingerprint()
    assert not _autorizacion(simulado).covers(real)


def test_una_autorizacion_revocada_no_cubre_nada() -> None:
    plan = publish_plan()
    revocada = _autorizacion(plan, revoked_at=datetime(2026, 9, 22, 8, 0, tzinfo=UTC))
    assert not revocada.covers(plan)


def test_el_fingerprint_guardado_se_recalcula() -> None:
    plan = publish_plan(intent_fingerprint="0" * 64)
    assert not plan.fingerprint_matches()


# ---------------------------------------------------------------------------
# Higiene de secretos
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "texto",
    [
        "https://bucket.example.com/v.mp4?X-Amz-Signature=deadbeef",
        "Authorization: Bearer ya29.a0AfH6",
        "el contenedor fallo con access_token=EAAG123",
    ],
)
def test_ningun_documento_admite_material_sensible(texto: str) -> None:
    with pytest.raises(ValueError, match="material sensible"):
        assert_no_secret_material(texto, field_name="prueba")


def test_un_error_no_puede_llevar_la_url_firmada() -> None:
    with pytest.raises(ValidationError, match="material sensible"):
        StructuredError(
            code="staging_expirado",
            error_class=ErrorClass.TRANSIENT,
            message="fallo al descargar https://s3.example.com/a.mp4?X-Amz-Signature=abc",
            retryable=True,
            occurred_at=datetime(2026, 9, 24, 17, 0, tzinfo=UTC),
        )


def test_el_plan_no_admite_un_texto_con_credenciales() -> None:
    from viralgen.publish.schemas import DestinationMetadata

    with pytest.raises(ValidationError, match="material sensible"):
        DestinationMetadata(
            title="Mi video",
            description="descarga: https://x.example/a.mp4?X-Amz-Credential=AKIA",
            language="es-ES",
            audience=publish_destination().metadata.audience,
            synthetic_disclosure=publish_destination().metadata.synthetic_disclosure,
            text_source=publish_destination().metadata.text_source,
            within_local_limits=True,
        )


# ---------------------------------------------------------------------------
# Reglas del plan
# ---------------------------------------------------------------------------


def test_un_plan_no_programa_por_si_solo() -> None:
    with pytest.raises(ValidationError, match="draft"):
        publish_destination(state=DestinationState.SCHEDULED_LOCAL)


def test_tiktok_solo_existe_como_entrega_manual() -> None:
    from viralgen.publish.schemas import AccountRef, DestinationOptions

    cuenta = AccountRef(
        platform=Platform.TIKTOK,
        alias="cuenta_tiktok",
        expected_account_id="@cuenta",
        account_id_kind="manual_handle",
    )
    with pytest.raises(ValidationError, match="Direct Post"):
        publish_destination(
            destination_id="tk_principal",
            platform=Platform.TIKTOK,
            account=cuenta,
            options=DestinationOptions(manual_delivery=False),
        )


def test_no_se_duplica_la_misma_cuenta_en_el_mismo_plan() -> None:
    with pytest.raises(ValidationError, match="misma plataforma y cuenta"):
        publish_plan(
            destinations=[
                publish_destination(destination_id="yt_uno"),
                publish_destination(destination_id="yt_dos"),
            ]
        )
