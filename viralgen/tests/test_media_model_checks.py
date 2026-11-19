"""Registro de verificacion de modelos: que se sabe y quien lo confirmo.

Estas pruebas protegen distinciones, no valores. Tres clases de hecho que no
se mezclan: lo que acepta el SDK (lo comprobo el desarrollo), lo que dice la
documentacion (lo aporto el revisor) y lo que solo demuestra una llamada real.
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


def test_el_sdk_lo_comprobo_el_desarrollo_y_la_doc_el_revisor() -> None:
    """Cada evidencia dice quien la aporto, y no se intercambian."""
    por_autor: dict[str, set[str]] = {}
    for entrada in mc.CHECKS:
        for evidencia in entrada.evidence:
            por_autor.setdefault(evidencia.confirmed_by, set()).add(entrada.check_id)

    # Lo del SDK, el desarrollo. Y solo eso.
    assert por_autor[DEVELOPER] == {
        "sdk_image_param_surface",
        "sdk_responses_param_surface",
    }
    # Todo lo documental, el revisor.
    assert "gpt_image_1_retired" in por_autor[REVIEWER]
    assert por_autor[DEVELOPER].isdisjoint(por_autor[REVIEWER])


def test_el_desarrollo_no_firma_ninguna_lectura_de_la_documentacion() -> None:
    """Este entorno no alcanza developers.openai.com: no puede firmarla."""
    for entrada in mc.CHECKS:
        for evidencia in entrada.evidence:
            if evidencia.confirmed_by != DEVELOPER:
                continue
            assert all(
                "developers.openai.com" not in fuente for fuente in evidencia.sources
            ), entrada.check_id


def test_la_evidencia_del_revisor_declara_su_ruta() -> None:
    """No se blanquea como lectura directa: consta como llego."""
    assert "ChatGPT" in mc.EVIDENCE_ROUTE_2026_11_18
    assert "revisado las fuentes oficiales" in mc.EVIDENCE_ROUTE_2026_11_18
    assert "El desarrollo no ha leido" in mc.EVIDENCE_ROUTE_2026_11_18

    del_revisor = [
        e for c in mc.CHECKS for e in c.evidence if e.confirmed_by == REVIEWER
    ]
    assert del_revisor
    for evidencia in del_revisor:
        assert evidencia.confirmed_on == mc.REVIEW_2026_11_18
        # Cada una arrastra la ruta, no solo la URL.
        assert mc.EVIDENCE_ROUTE_2026_11_18 in evidencia.sources


def test_cada_evidencia_acota_lo_que_no_cubre() -> None:
    """Cada evidencia declara su limite, no solo lo que confirma.

    La comprobacion es un INDICIO, no una prueba: busca una negacion en el
    texto del alcance. Un alcance que dijera "cubre todo" la pasaria. Lo que
    de verdad impide el exceso es que el alcance exista y se lea en el informe.
    """
    import re

    negacion = re.compile(r"\bno\b", re.IGNORECASE)
    for entrada in mc.CHECKS:
        for evidencia in entrada.evidence:
            assert evidencia.scope.strip(), entrada.check_id
            assert negacion.search(evidencia.scope), entrada.check_id


# ---------------------------------------------------------------------------
# Lo que la evidencia del 2026-11-18 cerro, y lo que no
# ---------------------------------------------------------------------------


def test_la_retirada_de_gpt_image_1_esta_verificada() -> None:
    entrada = mc.find("gpt_image_1_retired")
    assert entrada.status is CheckStatus.VERIFIED
    assert entrada.target == mc.IMAGE_MODEL_RETIRED
    assert "October 23, 2026" in entrada.latest_evidence.states
    # Y su alcance dice que NO implica borrarlo del catalogo.
    assert "cache" in entrada.latest_evidence.scope


@pytest.mark.parametrize(
    "check_id",
    [
        "sunburst_endpoint",
        "sunburst_sizes",
        "sunburst_input_fidelity",
        "sunburst_response_shape",
        "sunburst_usage_schema",
        "sunburst_token_consumption",
        "terra_structured_outputs",
    ],
)
def test_el_protocolo_cerrado_tiene_evidencia_del_revisor(check_id) -> None:
    entrada = mc.find(check_id)
    assert entrada.status is CheckStatus.VERIFIED, check_id
    assert entrada.latest_evidence is not None
    assert entrada.latest_evidence.confirmed_by == REVIEWER
    # Una entrada cerrada ya no arrastra la nota de "consulte pero no llego".
    assert mc.NOTA_CONSULTA_SIN_EXTRACTO not in entrada.notes


@pytest.mark.parametrize(
    "check_id, lo_que_falta",
    [
        ("sunburst_qualities", "la lista entera"),
        ("sunburst_edit_references", "bytes"),
        ("terra_reasoning_and_output_budget", "6000"),
    ],
)
def test_lo_parcial_dice_que_parte_falta(check_id, lo_que_falta) -> None:
    """Un parcial tiene evidencia y, aun asi, deja escrito el hueco."""
    entrada = mc.find(check_id)
    assert entrada.status is CheckStatus.PARTIAL, check_id
    assert entrada.evidence
    assert lo_que_falta in entrada.verified_when or lo_que_falta in entrada.assumption


def test_lo_que_la_evidencia_no_menciono_sigue_pendiente() -> None:
    """La tanda del 18 no cubrio temperature ni la tarifa de Terra."""
    for check_id in ("terra_temperature", "terra_pricing"):
        entrada = mc.find(check_id)
        assert entrada.status is CheckStatus.PENDING, check_id
        assert not entrada.evidence, check_id
        assert mc.NOTA_CONSULTA_SIN_EXTRACTO in entrada.notes, check_id


def test_input_fidelity_se_cerro_al_contrario_de_lo_supuesto() -> None:
    """Se suponia util para la consistencia; la guia dice omitirlo."""
    entrada = mc.find("sunburst_input_fidelity")
    assert entrada.status is CheckStatus.VERIFIED
    assert "Omit `input_fidelity`" in entrada.latest_evidence.states
    assert "OMITE" in entrada.assumption
    assert "en sentido contrario" in entrada.verified_when


def test_la_tarifa_de_salida_no_es_el_precio_de_una_imagen() -> None:
    entrada = mc.find("sunburst_token_consumption")
    assert "POR TOKENS" in entrada.assumption
    assert "30 USD NO" in entrada.assumption
    # Y el alcance deja claro que los recuentos no constan.
    assert "NO" in entrada.latest_evidence.scope


# ---------------------------------------------------------------------------
# La disponibilidad en la cuenta
# ---------------------------------------------------------------------------


def test_la_disponibilidad_en_la_cuenta_no_bloquea_por_construccion() -> None:
    """Un bloqueo que impide la unica prueba posible no se levanta nunca."""
    for check_id in ("sunburst_account_availability", "terra_account_availability"):
        entrada = mc.find(check_id)
        assert entrada.status is CheckStatus.PENDING, check_id
        assert entrada.blocks_real is False, check_id
        assert "NINGUNA" in entrada.assumption
        assert "llamada real" in entrada.verified_when
    # Y el motivo esta escrito en el modulo, no solo aqui.
    assert "bucle" in mc.__doc__


def test_una_prueba_de_transporte_simulado_no_cierra_la_cuenta() -> None:
    entrada = mc.find("sunburst_account_availability")
    assert "transporte simulado tampoco" in entrada.verified_when


# ---------------------------------------------------------------------------
# El informe
# ---------------------------------------------------------------------------


def test_el_informe_nombra_lo_que_de_verdad_esta_rechazado() -> None:
    """`blocked_models` vacio no significa "cualquier modelo sirve"."""
    informe = mc.describe_all()
    # Hoy no hay bloqueos por falta de verificacion.
    assert informe["blocked_models"] == []
    # Pero gpt-image-1 SI esta rechazado para una peticion nueva, y se dice.
    rechazados = informe["refused_for_new_real_requests"]
    assert "gpt-image-1" in rechazados
    assert "RETIRADO" in rechazados["gpt-image-1"]
    assert "gpt-image-2.6-sunburst" not in rechazados


def test_el_informe_declara_las_dos_fechas_y_el_control_de_red() -> None:
    informe = mc.describe_all()
    assert informe["reviewer_consulted_on"] == ["2026-11-17", "2026-11-18"]
    assert "developers.openai.com" in informe["reason"]
    assert "200" in informe["reason"]  # el control que si respondio
    assert "ChatGPT" in informe["evidence_route"]


def test_las_fuentes_citadas_son_del_proveedor() -> None:
    for tag, url in mc.SOURCES.items():
        assert url.startswith("https://developers.openai.com/"), tag


def test_cada_entrada_dice_la_condicion_exacta_que_la_cerraria() -> None:
    for entrada in mc.CHECKS:
        assert entrada.what.strip()
        assert entrada.assumption.strip()
        assert entrada.verified_when.strip()


def test_find_falla_con_un_id_inexistente() -> None:
    with pytest.raises(KeyError):
        mc.find("no_existe")
