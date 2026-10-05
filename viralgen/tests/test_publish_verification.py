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
    """Cerrar la condicion es exactamente lo que levanta el bloqueo."""
    for entrada in v.CHECKS:
        if entrada.verified:
            assert entrada.blocking is False, entrada.check_id
        # Pendiente <-> sin evidencia. Lo garantiza el propio dataclass.
        assert (entrada.status is v.CheckStatus.PENDING) == (not entrada.evidence)


def test_una_entrada_parcial_sigue_bloqueando() -> None:
    """Tener algo de evidencia no es tenerla cerrada.

    `ig_container_fields` tiene confirmados host, ruta y campos, pero la
    coleccion usa una variable de version; `s3_presign_expiry` vale para AWS y
    no para el proveedor que se elija. Las dos siguen bloqueando.
    """
    for identificador in ("ig_container_fields", "s3_presign_expiry"):
        entrada = v.find(identificador)
        assert entrada.status is v.CheckStatus.PARTIAL
        assert entrada.evidence, "una parcial tiene evidencia"
        assert entrada.verified is False
        assert entrada.blocking is True, identificador


def test_un_estado_sin_respaldo_no_se_puede_construir() -> None:
    """El dataclass impide declarar verificado lo que no tiene evidencia."""
    comun = dict(
        check_id="prueba",
        target="youtube",
        source_tag="S1",
        source_url="https://example.invalid/",
        what="algo",
        assumption="algo",
        verified_when="algo",
        blocks_real=True,
    )
    with pytest.raises(ValueError, match="necesita"):
        v.PendingCheck(**comun, status=v.CheckStatus.VERIFIED)
    with pytest.raises(ValueError, match="no puede ser"):
        v.PendingCheck(
            **comun,
            evidence=(
                v.SourceEvidence(
                    confirmed_by=v.REVIEWER,
                    confirmed_on="2026-10-05",
                    states="algo",
                    scope="algo",
                ),
            ),
        )


def test_la_evidencia_dice_quien_la_aporto() -> None:
    """No se afirma un acceso que este entorno no tuvo."""
    verificadas = [entrada for entrada in v.CHECKS if entrada.verified]
    assert verificadas, "el registro deberia tener al menos una entrada verificada"
    for entrada in verificadas:
        assert entrada.evidence
        for prueba in entrada.evidence:
            # Este entorno no alcanza la documentacion: toda la evidencia de
            # hoy es del revisor, y asi consta.
            assert prueba.confirmed_by == v.REVIEWER
            assert prueba.confirmed_on
            assert prueba.states.strip()
            # El alcance acota hasta donde llega esa evidencia.
            assert prueba.scope.strip()
            assert prueba.sources, entrada.check_id


def test_la_propiedad_de_contenido_sintetico_esta_verificada_por_el_revisor() -> None:
    entrada = v.find("yt_synthetic_media_property")
    assert entrada.verified
    assert "containsSyntheticMedia" in entrada.assumption
    assert entrada.blocking is False
    # Dos consultas: el registro guarda historial, no solo la ultima.
    assert [prueba.confirmed_on for prueba in entrada.evidence] == [
        "2026-09-26",
        "2026-10-05",
    ]
    assert all(prueba.confirmed_by == v.REVIEWER for prueba in entrada.evidence)


def test_el_410_de_sesion_caducada_se_declara_como_decision_propia() -> None:
    """La guia documenta 404 y no menciona 410. Eso se dice, no se disimula."""
    entrada = v.find("yt_resumable_protocol")
    assert entrada.verified
    assert "404" in entrada.assumption and "410" in entrada.assumption
    assert "DEFENSIVA PROPIA" in entrada.assumption
    alcance = entrada.evidence[-1].scope
    assert "410" in alcance
    assert "NO" in alcance or "no esta respaldado" in alcance
    # Que el adaptador trate 404 y 410 igual se prueba aparte, sobre el
    # transporte, en test_publish_youtube.py.


def test_los_bloqueantes_son_los_cinco_esperados() -> None:
    """Si esta lista cambia, tiene que ser una decision consciente.

    Tras la revision documental del 2026-10-05 ya no queda ningun bloqueo de
    YouTube: los cinco que quedan son de Instagram y de su almacenamiento
    temporal.
    """
    assert sorted(e.check_id for e in v.CHECKS if e.blocking) == sorted(
        [
            "ig_graph_version",
            "ig_container_fields",
            "ig_status_values",
            "ig_permissions",
            "s3_presign_expiry",
        ]
    )


def test_youtube_ya_no_tiene_bloqueos_documentales() -> None:
    """Los tres bloqueos de YouTube quedaron cerrados con evidencia del revisor."""
    assert v.blocking_for("youtube") == []
    for identificador in (
        "yt_insert_part_and_fields",
        "yt_resumable_protocol",
        "yt_oauth_installed_app",
    ):
        entrada = v.find(identificador)
        assert entrada.verified, identificador
        assert entrada.evidence[-1].confirmed_on == "2026-10-05"
    # Lo que no cierra la evidencia documental sigue siendo de la ejecucion:
    # que el usuario concedio permisos y que el token es del canal esperado.
    alcance = v.find("yt_oauth_installed_app").evidence[-1].scope
    assert "channels.list" in alcance


def test_instagram_y_staging_conservan_sus_bloqueos() -> None:
    assert {e.check_id for e in v.blocking_for("instagram")} == {
        "ig_graph_version",
        "ig_container_fields",
        "ig_status_values",
        "ig_permissions",
    }
    assert [e.check_id for e in v.blocking_for("staging")] == ["s3_presign_expiry"]


def test_las_consultas_que_no_confirmaron_nada_quedan_anotadas() -> None:
    """Un intento fallido consta como nota, no como evidencia."""
    entrada = v.find("ig_status_values")
    assert entrada.status is v.CheckStatus.PENDING
    assert entrada.evidence == ()
    assert any("2026-10-05" in nota for nota in entrada.notes)


def test_los_limites_de_texto_documentados_no_se_confunden_con_los_propios() -> None:
    """La diferencia de unidades esta escrita: caracteres frente a bytes."""
    entrada = v.find("yt_text_limits")
    assert entrada.status is v.CheckStatus.PARTIAL
    assert "BYTES" in entrada.assumption
    assert "5.000 bytes" in entrada.evidence[-1].states
    assert "no garantizan" in entrada.evidence[-1].scope


def test_los_recuentos_cuadran() -> None:
    totales = v.describe_all()["totals"]
    assert totales["entries"] == len(v.CHECKS)
    assert (
        totales["pending"] + totales["partial"] + totales["verified"]
        == totales["entries"]
    )
    # Solo bloquea lo que no esta verificado.
    assert totales["blocking"] <= totales["pending"] + totales["partial"]


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
    """Este entorno no alcanza la documentacion y el registro lo dice."""
    assert "403" in v.UNREACHABLE_REASON
    assert "Ninguna evidencia de este registro la aporto el desarrollo" in (
        v.UNREACHABLE_REASON
    )
    assert "2026-10-05" in v.UNREACHABLE_REASON


# ---------------------------------------------------------------------------
# El comando que publica este registro
# ---------------------------------------------------------------------------


def test_el_comando_de_verificacion_suma_los_tres_estados(tmp_path, capsys) -> None:
    """Los recuentos del informe tienen que cuadrar con las entradas.

    Regresion de un defecto real: el comando contaba `pending` y `verified` y
    se dejaba `partial` fuera, asi que 4+5 no sumaban 13 y el informe mentia por
    omision.
    """
    import json

    from viralgen.cli import main

    assert main(["--data-dir", str(tmp_path), "publish", "verification"]) == 0
    datos = json.loads(capsys.readouterr().out)
    totales = datos["totals"]
    assert (
        totales["verified"] + totales["partial"] + totales["pending"]
        == totales["entries"]
        == len(datos["checks"])
    )
    assert totales["blocking"] == len(datos["blocking"])


def test_el_comando_filtra_por_destino(tmp_path, capsys) -> None:
    import json

    from viralgen.cli import main

    assert (
        main(
            ["--data-dir", str(tmp_path), "publish", "verification", "--target", "youtube"]
        )
        == 0
    )
    datos = json.loads(capsys.readouterr().out)
    assert {c["target"] for c in datos["checks"]} == {"youtube"}
    assert datos["blocking"] == []
    assert datos["totals"]["entries"] == len(v.checks_for("youtube"))


def test_el_comando_puede_ocultar_las_verificadas(tmp_path, capsys) -> None:
    import json

    from viralgen.cli import main

    assert (
        main(
            [
                "--data-dir", str(tmp_path), "publish", "verification",
                "--target", "youtube", "--pending-only",
            ]
        )
        == 0
    )
    datos = json.loads(capsys.readouterr().out)
    assert datos["totals"]["verified"] == 0
    assert {c["status"] for c in datos["checks"]} == {"partial"}


def test_el_comando_declara_la_procedencia_de_cada_evidencia(tmp_path, capsys) -> None:
    import json

    from viralgen.cli import main

    assert main(["--data-dir", str(tmp_path), "publish", "verification"]) == 0
    datos = json.loads(capsys.readouterr().out)
    for entrada in datos["checks"]:
        for prueba in entrada["evidence"]:
            assert prueba["confirmed_by"] == "revisor"
            assert prueba["sources"]
    assert "Ninguna evidencia" in datos["reason"]
