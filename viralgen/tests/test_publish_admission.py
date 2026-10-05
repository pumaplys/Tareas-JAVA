"""Admision del modulo 5: tres veredictos que el modo no mueve.

El riesgo que cubren estas pruebas es concreto: que un paquete de preview, o
uno con el manifiesto retocado, acabe enviado a una cuenta real. Por eso se
comprueba tanto la logica de los veredictos como el recorrido completo sobre
el paquete real del repositorio.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from viralgen.config import Settings
from viralgen.publish import verification
from viralgen.publish.admission import (
    REAL_ONLY_CHECKS,
    PublishAdmissionReport,
    check_publication_admission,
    require_mode,
    require_real_transport,
)
from viralgen.publish.errors import ModeViolationError, PublishAdmissionError
from viralgen.publish.schemas import PublishMode
from viralgen.render.admission import ORIGIN_CHECKS, RenderAdmissionReport
from viralgen.render.ffmpeg import probe_capabilities
from viralgen.schemas.common import Platform

RAIZ = Path(__file__).parent.parent
PAQUETE = RAIZ / "examples" / "montaje_preview"
ENTRADAS = RAIZ / "examples" / "visuales_simulados"

TIENE_FFMPEG = probe_capabilities("ffmpeg", "ffprobe").usable


def necesita_ffmpeg(prueba):
    """Aplica DOS marcas: `ffmpeg` (seleccion) y `skipif` (salto sin herramientas)."""
    con_salto = pytest.mark.skipif(
        not TIENE_FFMPEG, reason="FFmpeg/ffprobe no disponibles"
    )(prueba)
    return pytest.mark.ffmpeg(con_salto)


TODOS = [Platform.YOUTUBE_SHORTS, Platform.INSTAGRAM_REELS, Platform.TIKTOK]


# ---------------------------------------------------------------------------
# Logica de los veredictos, sin tocar archivos
# ---------------------------------------------------------------------------


def _render_report(*, contrato: bool = True, origen_real: bool = True) -> RenderAdmissionReport:
    """Un informe del modulo 4 con la forma que devuelve el de verdad."""
    informe = RenderAdmissionReport()
    informe.checks = {
        "guion_valido": True,
        "video_medido": True,
        "audio_medido": True,
    }
    informe.contract_valid = contrato
    for nombre in sorted(ORIGIN_CHECKS):
        informe.checks[nombre] = origen_real
        if not origen_real:
            informe.failures.append((nombre, f"{nombre}: origen simulado"))
    return informe


@pytest.fixture
def paquete_falso(tmp_path: Path) -> dict[str, Path]:
    """Rutas que existen pero cuyo contenido no se llega a leer."""
    rutas = {}
    for nombre in ("script", "voice", "media", "render"):
        ruta = tmp_path / f"{nombre}.json"
        ruta.write_text("{}", encoding="utf-8")
        rutas[nombre] = ruta
    return rutas


def _admision(monkeypatch, paquete, settings, *, contrato=True, origen_real=True, targets=TODOS):
    """Ejecuta la admision con el informe del modulo 4 sustituido."""
    monkeypatch.setattr(
        "viralgen.publish.admission.check_render_admission",
        lambda **_: _render_report(contrato=contrato, origen_real=origen_real),
    )
    return check_publication_admission(
        script_path=paquete["script"],
        voice_path=paquete["voice"],
        media_path=paquete["media"],
        manifest_path=paquete["render"],
        settings=settings,
        targets=targets,
    )


def test_sin_contrato_valido_no_hay_ningun_veredicto_positivo(
    monkeypatch, paquete_falso, settings: Settings
) -> None:
    informe = _admision(monkeypatch, paquete_falso, settings, contrato=False)
    assert not informe.contract_valid
    assert not informe.admissible_for_simulation
    assert not informe.admissible_for_real_dispatch


def test_un_preview_se_simula_pero_no_se_envia(
    monkeypatch, paquete_falso, settings: Settings
) -> None:
    """El caso central: origen simulado bloquea el envio real, no la simulacion."""
    monkeypatch.setattr(verification, "PENDING_CHECKS", ())
    informe = _admision(monkeypatch, paquete_falso, settings, origen_real=False)
    # El resto de comprobaciones se resuelven leyendo el paquete real; aqui se
    # observa solo el efecto del origen.
    assert informe.checks["origen_de_produccion"] is False
    assert "origen_de_produccion" in REAL_ONLY_CHECKS
    assert not informe.admissible_for_real_dispatch
    assert any("simulado" in motivo for motivo in informe.reasons)


def test_elegir_el_modo_no_cambia_los_veredictos() -> None:
    """`require_mode` exige un veredicto; no lo concede."""
    informe = PublishAdmissionReport(targets=(Platform.YOUTUBE_SHORTS,))
    informe.contract_valid = True
    informe.checks = {"origen_de_produccion": False, "video_disponible": True}
    informe.failures = [("origen_de_produccion", "el paquete es un preview")]

    antes = (informe.admissible_for_simulation, informe.admissible_for_real_dispatch)
    require_mode(informe, PublishMode.PLAN)  # plan nunca exige nada
    require_mode(informe, PublishMode.MOCK)
    with pytest.raises(PublishAdmissionError, match="NO es admisible para envio real"):
        require_mode(informe, PublishMode.REAL)
    assert (informe.admissible_for_simulation, informe.admissible_for_real_dispatch) == antes


def test_lo_que_no_se_puede_simular_tampoco_se_puede_enviar() -> None:
    informe = PublishAdmissionReport(targets=(Platform.YOUTUBE_SHORTS,))
    informe.contract_valid = True
    informe.checks = {"video_disponible": False}
    informe.failures = [("video_disponible", "el MP4 no coincide con lo declarado")]
    with pytest.raises(PublishAdmissionError, match="ni para simular"):
        require_mode(informe, PublishMode.MOCK)


@pytest.mark.parametrize("modo", [PublishMode.PLAN, PublishMode.MOCK])
def test_el_transporte_real_se_niega_fuera_del_modo_real(modo: PublishMode) -> None:
    """Aunque el entorno tuviera credenciales: el modo simulado no sale a la red."""
    with pytest.raises(ModeViolationError, match="modo real"):
        require_real_transport(modo, operation="videos.insert")


def test_la_verificacion_pendiente_bloquea_el_envio_real(
    monkeypatch, paquete_falso, settings: Settings
) -> None:
    """Un parametro de protocolo sin contrastar no se presenta como aprobado.

    Tras la revision documental del 2026-10-05, los pendientes que bloquean son
    los de Instagram y los de su almacenamiento temporal.
    """
    informe = _admision(
        monkeypatch, paquete_falso, settings, targets=[Platform.INSTAGRAM_REELS]
    )
    assert informe.checks["verificacion_de_protocolo"] is False
    bloqueantes = {pendiente.check_id for pendiente in informe.blocking_verification()}
    assert bloqueantes == {
        "ig_graph_version",
        "ig_container_fields",
        "ig_status_values",
        "ig_permissions",
        "s3_presign_expiry",
    }
    # Y no contamina otros destinos: los de YouTube no aparecen aqui.
    assert not any(pendiente.startswith("yt_") for pendiente in bloqueantes)


def test_un_plan_solo_de_youtube_no_arrastra_bloqueos_de_otros_destinos(
    monkeypatch, paquete_falso, settings: Settings
) -> None:
    """La admision se delimita por destino, y eso es lo que abre el piloto.

    YouTube cerro sus tres bloqueos documentales. Un plan dirigido solo a
    YouTube no debe heredar los pendientes de Instagram ni los del
    almacenamiento temporal, que ese plan no usa.
    """
    informe = _admision(
        monkeypatch, paquete_falso, settings, targets=[Platform.YOUTUBE_SHORTS]
    )
    assert informe.blocking_verification() == []
    assert informe.checks["verificacion_de_protocolo"] is True
    # El motivo del rechazo, si queda alguno, ya no es la verificacion.
    assert not any(
        nombre == "verificacion_de_protocolo" for nombre, _m in informe.failures
    )
    assert not any("sin contrastar" in motivo for motivo in informe.reasons)


def test_anadir_instagram_a_un_plan_de_youtube_vuelve_a_bloquearlo(
    monkeypatch, paquete_falso, settings: Settings
) -> None:
    """Los bloqueos se suman por destino: basta un destino bloqueado."""
    solo_youtube = _admision(
        monkeypatch, paquete_falso, settings, targets=[Platform.YOUTUBE_SHORTS]
    )
    con_instagram = _admision(
        monkeypatch,
        paquete_falso,
        settings,
        targets=[Platform.YOUTUBE_SHORTS, Platform.INSTAGRAM_REELS],
    )
    assert solo_youtube.checks["verificacion_de_protocolo"] is True
    assert con_instagram.checks["verificacion_de_protocolo"] is False
    assert {p.check_id for p in con_instagram.blocking_verification()} == {
        "ig_graph_version",
        "ig_container_fields",
        "ig_status_values",
        "ig_permissions",
        "s3_presign_expiry",
    }


def test_los_tres_veredictos_de_un_plan_solo_de_youtube(
    monkeypatch, paquete_falso, settings: Settings
) -> None:
    """Con origen de produccion y sin bloqueos, el envio real es admisible.

    El informe del modulo 4 se sustituye por un DOBLE que declara origen de
    produccion: no se reclasifica ningun paquete simulado del repositorio para
    que pase por produccion, que es justo lo que no hay que hacer. Lo que se
    comprueba aqui es la logica de los tres veredictos, no la del modulo 4.
    """
    monkeypatch.setattr(
        "viralgen.publish.admission.check_render_admission",
        lambda **_: _render_report(contrato=True, origen_real=True),
    )
    informe = PublishAdmissionReport(targets=(Platform.YOUTUBE_SHORTS,))
    informe.contract_valid = True
    informe.checks = {
        "destinos_indicados": True,
        "destinos_soportados": True,
        "destinos_con_transporte_real": True,
        "verificacion_de_protocolo": not informe.blocking_verification(),
        "cadena_contractualmente_valida": True,
        "montaje_tecnicamente_admisible": True,
        "origen_de_produccion": True,
        "documentos_legibles": True,
        "video_disponible": True,
        "video_dentro_del_limite": True,
    }
    assert informe.admissible_for_simulation is True
    assert informe.admissible_for_real_dispatch is True
    require_mode(informe, PublishMode.REAL)  # no lanza

    # El mismo informe con Instagram entre los destinos vuelve a bloquear.
    con_instagram = PublishAdmissionReport(
        targets=(Platform.YOUTUBE_SHORTS, Platform.INSTAGRAM_REELS)
    )
    con_instagram.contract_valid = True
    con_instagram.checks = dict(informe.checks)
    con_instagram.checks["verificacion_de_protocolo"] = not (
        con_instagram.blocking_verification()
    )
    con_instagram.failures = [
        ("verificacion_de_protocolo", "hay parametros de protocolo sin contrastar")
    ]
    assert con_instagram.admissible_for_real_dispatch is False
    with pytest.raises(PublishAdmissionError):
        require_mode(con_instagram, PublishMode.REAL)


def test_instagram_arrastra_la_verificacion_del_staging(
    monkeypatch, paquete_falso, settings: Settings
) -> None:
    """Publicar un Reel implica subir el MP4 a un bucket: cuenta igual."""
    informe = _admision(
        monkeypatch, paquete_falso, settings, targets=[Platform.INSTAGRAM_REELS]
    )
    bloqueantes = {pendiente.check_id for pendiente in informe.blocking_verification()}
    assert "s3_presign_expiry" in bloqueantes


def test_tiktok_solo_no_ofrece_envio_automatico(
    monkeypatch, paquete_falso, settings: Settings
) -> None:
    informe = _admision(monkeypatch, paquete_falso, settings, targets=[Platform.TIKTOK])
    assert informe.checks["destinos_con_transporte_real"] is False
    assert any("a mano" in motivo for motivo in informe.reasons)


def test_sin_destinos_no_hay_nada_que_admitir(
    monkeypatch, paquete_falso, settings: Settings
) -> None:
    informe = _admision(monkeypatch, paquete_falso, settings, targets=[])
    assert not informe.contract_valid
    assert informe.checks == {"destinos_indicados": False}


def test_el_informe_publica_los_tres_veredictos_juntos() -> None:
    informe = PublishAdmissionReport(targets=(Platform.YOUTUBE_SHORTS,))
    datos = informe.to_dict()
    for clave in (
        "contract_valid",
        "admissible_for_simulation",
        "admissible_for_real_dispatch",
    ):
        assert clave in datos
    assert "pendiente no es aprobado" in datos["note"]


# ---------------------------------------------------------------------------
# Recorrido real sobre el paquete del repositorio
# ---------------------------------------------------------------------------


def _rutas(paquete: Path) -> dict:
    return dict(
        script_path=ENTRADAS / "script.json",
        voice_path=ENTRADAS / "voz" / "voice.json",
        media_path=ENTRADAS / "medios" / "media.json",
        manifest_path=paquete / "render.json",
    )


@necesita_ffmpeg
def test_el_preview_del_repositorio_se_simula_y_no_se_envia(settings: Settings) -> None:
    """Sobre archivos reales, no sobre dobles."""
    informe = check_publication_admission(
        **_rutas(PAQUETE), settings=settings, targets=TODOS
    )
    assert informe.contract_valid
    assert informe.admissible_for_simulation
    assert not informe.admissible_for_real_dispatch
    assert informe.sources is not None
    assert informe.sources.video.sha256 == informe.measured["video"]["sha256"]
    motivos = " ".join(informe.reasons)
    assert "simulad" in motivos


@necesita_ffmpeg
def test_marcar_admissible_for_publisher_a_mano_no_autoriza_nada(
    tmp_path: Path, settings: Settings
) -> None:
    """El booleano guardado es informativo; el que decide es el recalculado."""
    copia = tmp_path / "paquete"
    shutil.copytree(PAQUETE, copia)
    manifiesto = copia / "render.json"
    datos = json.loads(manifiesto.read_text(encoding="utf-8"))
    datos["control"]["render_status"] = "ready"
    datos["control"]["admissible_for_publisher"] = True
    manifiesto.write_text(json.dumps(datos), encoding="utf-8")

    informe = check_publication_admission(
        **_rutas(copia), settings=settings, targets=[Platform.YOUTUBE_SHORTS]
    )
    declarado = informe.measured["admissible_for_publisher"]
    assert declarado["declared"] is True
    assert declarado["recomputed"] is False
    assert not informe.admissible_for_real_dispatch


@necesita_ffmpeg
def test_reclasificar_el_manifiesto_no_convierte_un_preview_en_produccion(
    tmp_path: Path, settings: Settings
) -> None:
    """Falsificar render_mode y simulation no blanquea las fuentes.

    Las banderas del manifiesto se pueden reescribir; las de los documentos de
    origen tambien se leen, y esos no se tocan. El paquete original del
    repositorio queda intacto: esto ocurre sobre una copia.
    """
    copia = tmp_path / "paquete"
    shutil.copytree(PAQUETE, copia)
    manifiesto = copia / "render.json"
    datos = json.loads(manifiesto.read_text(encoding="utf-8"))
    datos["render_mode"] = "production"
    datos["simulation"] = False
    for clave in ("script_simulation", "voice_simulation", "media_simulation"):
        datos["sources"][clave] = False
    manifiesto.write_text(json.dumps(datos), encoding="utf-8")

    informe = check_publication_admission(
        **_rutas(copia), settings=settings, targets=[Platform.YOUTUBE_SHORTS]
    )
    assert not informe.admissible_for_real_dispatch
    assert informe.checks["origen_de_produccion"] is False

    # Y el ejemplo del repositorio sigue siendo un preview simulado.
    original = json.loads((PAQUETE / "render.json").read_text(encoding="utf-8"))
    assert original["render_mode"] == "preview"
    assert original["simulation"] is True
