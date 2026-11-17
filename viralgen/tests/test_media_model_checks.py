"""Registro de verificacion de modelos: que se sabe y quien lo confirmo.

Estas pruebas protegen una distincion, no un valor: lo que comprueba el SDK
instalado NO es lo mismo que lo que confirma la documentacion del proveedor, y
una consulta sin extracto no verifica nada.
"""

from __future__ import annotations

import pytest

from viralgen.media import model_checks as mc
from viralgen.verification import CheckStatus, DEVELOPER, REVIEWER


def test_los_recuentos_suman_el_total() -> None:
    """Un informe que solo cuenta dos de los tres estados miente por omision."""
    totales = mc.totals()
    assert (
        totales["pending"] + totales["partial"] + totales["verified"] == totales["total"]
    )
    assert totales["total"] == len(mc.CHECKS)


def test_lo_unico_verificado_lo_aporto_el_desarrollo_y_es_el_sdk() -> None:
    """Las dos entradas cerradas son de la firma del SDK, no de la doc."""
    verificadas = [c for c in mc.CHECKS if c.verified]
    assert {c.check_id for c in verificadas} == {
        "sdk_image_param_surface",
        "sdk_responses_param_surface",
    }
    for entrada in verificadas:
        evidencia = entrada.latest_evidence
        assert evidencia is not None
        assert evidencia.confirmed_by == DEVELOPER
        assert entrada.source_tag == "SDK"
        # Y su alcance dice explicitamente lo que NO cubre.
        assert "No " in evidencia.scope or "no " in evidencia.scope


def test_ninguna_entrada_del_proveedor_esta_verificada() -> None:
    """El revisor consulto las fuentes; el extracto no ha llegado."""
    del_proveedor = [c for c in mc.CHECKS if c.source_tag.startswith("O")]
    assert del_proveedor, "deberia haber entradas que dependan de la doc oficial"
    for entrada in del_proveedor:
        assert entrada.status is CheckStatus.PENDING, entrada.check_id
        assert not entrada.evidence, entrada.check_id
        # La consulta consta como nota fechada, que no es evidencia.
        assert any(mc.REVIEW_2026_11_17 in nota for nota in entrada.notes), entrada.check_id


def test_una_consulta_sin_extracto_no_se_cuenta_como_evidencia() -> None:
    """La nota NO se puede colar como SourceEvidence del revisor."""
    aportadas_por_el_revisor = [
        c for c in mc.CHECKS
        if any(e.confirmed_by == REVIEWER for e in c.evidence)
    ]
    assert aportadas_por_el_revisor == []


def test_ambos_modelos_nuevos_bloquean_el_envio_real() -> None:
    for modelo in (mc.IMAGE_MODEL_SUNBURST, mc.TEXT_MODEL_TERRA):
        bloqueantes = mc.blocking_for(modelo)
        assert bloqueantes, f"{modelo} deberia bloquear mientras no se verifique"
        # Entre ellas, la disponibilidad en nuestra cuenta: declarar un modelo
        # en el catalogo no da acceso a el.
        ids = {c.check_id for c in bloqueantes}
        assert any(id_.endswith("account_availability") for id_ in ids), ids


def test_la_disponibilidad_en_la_cuenta_no_se_supone() -> None:
    entrada = mc.find("sunburst_account_availability")
    assert entrada.blocks_real is True
    assert entrada.status is CheckStatus.PENDING
    assert "NINGUNA" in entrada.assumption
    # No tiene nota de consulta: no es algo que resuelva la documentacion.
    assert entrada.notes == ()


def test_el_motivo_de_inalcanzabilidad_declara_el_control() -> None:
    """Decir 'no pude' obliga a decir como se comprobo que no."""
    assert "developers.openai.com" in mc.UNREACHABLE_REASON
    assert "200" in mc.UNREACHABLE_REASON  # el control que si respondio
    assert mc.REVIEW_2026_11_17 in mc.UNREACHABLE_REASON


def test_las_cuatro_fuentes_del_revisor_estan_registradas() -> None:
    urls = set(mc.SOURCES.values())
    assert urls == {
        "https://developers.openai.com/api/docs/models/gpt-image-2.6-sunburst",
        "https://developers.openai.com/api/docs/guides/image-generation",
        "https://developers.openai.com/api/docs/models/gpt-6.1-terra",
        "https://developers.openai.com/api/docs/deprecations",
    }


def test_cada_entrada_dice_la_condicion_exacta_que_la_cerraria() -> None:
    for entrada in mc.CHECKS:
        assert entrada.what.strip()
        assert entrada.assumption.strip()
        assert entrada.verified_when.strip()


def test_find_falla_con_un_id_inexistente() -> None:
    with pytest.raises(KeyError):
        mc.find("no_existe")


def test_describe_all_no_presenta_supuestos_como_documentados() -> None:
    informe = mc.describe_all()
    assert informe["totals"]["total"] == len(mc.CHECKS)
    assert mc.IMAGE_MODEL_SUNBURST in informe["blocked_models"]
    assert mc.TEXT_MODEL_TERRA in informe["blocked_models"]
    assert "el extracto no ha llegado" in informe["note"]
    assert informe["reviewer_consulted_on"] == mc.REVIEW_2026_11_17
