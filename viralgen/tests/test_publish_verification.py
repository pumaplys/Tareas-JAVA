"""El registro de verificacion documental.

Este registro es la pieza que impide que "no lo pude comprobar" se convierta en
"seguro que esta bien". Las pruebas fijan sus invariantes: cada entrada dice que
supuesto usa el codigo y cuando se da por verificada, una entrada verificada
declara QUIEN aporto la evidencia, y solo las pendientes bloquean.
"""

from __future__ import annotations

import pytest

from viralgen.publish import verification as v
from viralgen.publish.admission import VERIFICATION_TARGET
from viralgen.schemas.common import Platform


def test_cada_entrada_declara_supuesto_y_condicion() -> None:
    """Sin las dos cosas, la entrada no sirve para nada."""
    for entrada in v.CHECKS:
        assert entrada.assumption.strip(), entrada.check_id
        assert entrada.verified_when.strip(), entrada.check_id
        assert entrada.source_url.startswith("https://"), entrada.check_id
        assert entrada.target in {"youtube", "instagram", "staging", "tiktok", "*"}


def test_los_identificadores_no_se_repiten() -> None:
    ids = [entrada.check_id for entrada in v.CHECKS]
    assert len(set(ids)) == len(ids)


def test_una_entrada_verificada_no_bloquea() -> None:
    """Aportar evidencia es exactamente lo que levanta el bloqueo."""
    for entrada in v.CHECKS:
        if entrada.verified:
            assert entrada.blocking is False, entrada.check_id
        assert entrada.verified == (entrada.evidence is not None)


def test_la_evidencia_dice_quien_la_aporto() -> None:
    """No se afirma un acceso que este entorno no tuvo."""
    verificadas = [entrada for entrada in v.CHECKS if entrada.verified]
    assert verificadas, "el registro deberia tener al menos una entrada verificada"
    for entrada in verificadas:
        assert entrada.evidence is not None
        assert entrada.evidence.confirmed_by in (v.REVIEWER, v.DEVELOPER)
        assert entrada.evidence.confirmed_on
        assert entrada.evidence.states.strip()
        # El alcance acota hasta donde llega esa evidencia.
        assert entrada.evidence.scope.strip()


def test_la_propiedad_de_contenido_sintetico_esta_verificada_por_el_revisor() -> None:
    entrada = v.find("yt_synthetic_media_property")
    assert entrada.verified
    assert entrada.evidence.confirmed_by == v.REVIEWER
    assert "containsSyntheticMedia" in entrada.assumption
    assert entrada.blocking is False
    # Y no arrastra a la lista completa de campos, que sigue pendiente.
    campos = v.find("yt_insert_part_and_fields")
    assert campos.verified is False and campos.blocking is True
    assert "yt_synthetic_media_property" in campos.verified_when


def test_los_bloqueantes_son_los_ocho_esperados() -> None:
    """Si esta lista cambia, tiene que ser una decision consciente."""
    assert sorted(e.check_id for e in v.CHECKS if e.blocking) == sorted(
        [
            "yt_insert_part_and_fields",
            "yt_resumable_protocol",
            "yt_oauth_installed_app",
            "ig_graph_version",
            "ig_container_fields",
            "ig_status_values",
            "ig_permissions",
            "s3_presign_expiry",
        ]
    )


def test_los_recuentos_cuadran() -> None:
    totales = v.describe_all()["totals"]
    assert totales["entries"] == len(v.CHECKS)
    assert totales["pending"] + totales["verified"] == totales["entries"]
    assert totales["blocking"] <= totales["pending"]


@pytest.mark.parametrize("plataforma", list(Platform))
def test_cada_plataforma_tiene_su_destino_de_verificacion(plataforma) -> None:
    destino = VERIFICATION_TARGET[plataforma]
    assert v.checks_for(destino), destino


def test_pendientes_y_verificadas_particionan_las_entradas_del_destino() -> None:
    for destino in ("youtube", "instagram", "staging", "tiktok"):
        todas = v.checks_for(destino)
        pendientes = v.pending_for(destino)
        verificadas = v.verified_for(destino)
        assert len(pendientes) + len(verificadas) == len(todas)
        assert set(v.blocking_for(destino)) <= set(pendientes)


def test_un_identificador_inexistente_falla_pronto() -> None:
    with pytest.raises(KeyError):
        v.find("no_existe")


def test_el_motivo_no_afirma_haber_leido_nada() -> None:
    assert "403" in v.UNREACHABLE_REASON
    assert "declaran quien la aporto" in v.UNREACHABLE_REASON
