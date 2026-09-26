"""La CLI de publicacion, incluido el recorrido completo en simulacion.

El recorrido largo esta marcado `ffmpeg` porque la admision mide el MP4 de
verdad: sin esa medicion no habria nada que admitir. El resto de casos no
necesita herramientas y comprueba los limites de cada comando.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from viralgen.cli import main
from viralgen.errors import ExitCode
from viralgen.render.ffmpeg import probe_capabilities

RAIZ = Path(__file__).parent.parent
EJEMPLOS = RAIZ / "examples"
CUENTAS = EJEMPLOS / "publicacion_simulada" / "accounts.json"
COMPLETAR = EJEMPLOS / "publicacion_simulada" / "completar_plan_demo.py"

TIENE_FFMPEG = probe_capabilities("ffmpeg", "ffprobe").usable


def necesita_ffmpeg(prueba):
    con_salto = pytest.mark.skipif(
        not TIENE_FFMPEG, reason="FFmpeg/ffprobe no disponibles"
    )(prueba)
    return pytest.mark.ffmpeg(con_salto)


def _salida(capsys) -> dict:
    capturado = capsys.readouterr()
    return json.loads(capturado.out)


def _destino(identificador: str, plataforma: str, cuenta: str, hora: str, **extra) -> str:
    partes = [
        f"id={identificador}",
        f"platform={plataforma}",
        f"account={cuenta}",
        f"at={hora}",
        "tz=Europe/Madrid",
    ]
    partes += [f"{clave}={valor}" for clave, valor in extra.items()]
    return ",".join(partes)


# ---------------------------------------------------------------------------
# Comandos que no necesitan herramientas
# ---------------------------------------------------------------------------


def test_schema_exporta_los_dos_contratos(tmp_path: Path, capsys) -> None:
    codigo = main(
        ["--data-dir", str(tmp_path), "publish", "schema", "--out", str(tmp_path / "s")]
    )
    assert codigo == int(ExitCode.OK)
    datos = _salida(capsys)
    assert len(datos["written"]) == 2
    for ruta in datos["written"]:
        contenido = json.loads(Path(ruta).read_text(encoding="utf-8"))
        assert "properties" in contenido


def test_un_destino_mal_escrito_se_explica(tmp_path: Path, capsys) -> None:
    codigo = main(
        [
            "--data-dir", str(tmp_path), "publish", "plan",
            "--script", str(EJEMPLOS / "visuales_simulados" / "script.json"),
            "--voice", str(EJEMPLOS / "visuales_simulados" / "voz" / "voice.json"),
            "--media", str(EJEMPLOS / "visuales_simulados" / "medios" / "media.json"),
            "--render", str(EJEMPLOS / "montaje_preview" / "render.json"),
            "--publish-key", "x", "--destination", "id=yt,platform=youtube_shorts",
        ]
    )
    assert codigo == int(ExitCode.CONFIG)
    assert "faltan claves" in _salida(capsys)["error_message"]


def test_una_plataforma_inexistente_se_rechaza(tmp_path: Path, capsys) -> None:
    codigo = main(
        [
            "--data-dir", str(tmp_path), "publish", "plan",
            "--script", str(EJEMPLOS / "visuales_simulados" / "script.json"),
            "--voice", str(EJEMPLOS / "visuales_simulados" / "voz" / "voice.json"),
            "--media", str(EJEMPLOS / "visuales_simulados" / "medios" / "media.json"),
            "--render", str(EJEMPLOS / "montaje_preview" / "render.json"),
            "--publish-key", "x",
            "--destination", _destino("x", "twitter", "canal_demo", "2026-09-24T18:30:00"),
        ]
    )
    assert codigo == int(ExitCode.CONFIG)
    assert "Plataforma desconocida" in _salida(capsys)["error_message"]


def test_status_de_una_clave_inexistente_lo_dice(tmp_path: Path, capsys) -> None:
    codigo = main(
        ["--data-dir", str(tmp_path), "publish", "status", "--publish-key", "no-existe"]
    )
    assert codigo == int(ExitCode.CONFIG)
    assert "no-existe" in _salida(capsys)["error_message"]


def test_record_manual_exige_algo_que_registrar(tmp_path: Path, capsys) -> None:
    codigo = main(
        [
            "--data-dir", str(tmp_path), "publish", "record-manual",
            "--publish-key", "demo", "--destination", "tk",
        ]
    )
    assert codigo == int(ExitCode.CONFIG)
    assert "--url" in _salida(capsys)["error_message"]


def test_el_reloj_de_ensayo_no_vale_para_destinos_reales(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    """`--now` es para ensayar, no para saltarse la ventana de algo real."""
    from viralgen.publish.storage import PublishStorage
    from viralgen.storage import Storage

    datos = tmp_path / "datos"
    almacenamiento = PublishStorage(Storage(datos))
    almacenamiento.migrate()
    almacenamiento.create_destination(
        {
            "publication_id": "pub-real",
            "destination_id": "yt",
            "platform": "youtube_shorts",
            "account_alias": "canal",
            "state": "scheduled_local",
            "simulation": 0,
            "requested_visibility": "private",
            "scheduled_at": "2026-09-24T16:30:00Z",
            "timezone": "Europe/Madrid",
        }
    )
    codigo = main(
        [
            "--data-dir", str(datos), "publish", "worker", "--once",
            "--mode", "real", "--now", "2026-09-24T16:35:00Z",
        ]
    )
    assert codigo == int(ExitCode.CONFIG)
    assert "entrega real" in _salida(capsys)["error_message"]


# ---------------------------------------------------------------------------
# Recorrido completo en simulacion
# ---------------------------------------------------------------------------


def _plan_args(tmp_path: Path, clave: str, modo: str, destinos: list[str]) -> list[str]:
    argumentos = [
        "--data-dir", str(tmp_path), "publish", "plan",
        "--script", str(EJEMPLOS / "visuales_simulados" / "script.json"),
        "--voice", str(EJEMPLOS / "visuales_simulados" / "voz" / "voice.json"),
        "--media", str(EJEMPLOS / "visuales_simulados" / "medios" / "media.json"),
        "--render", str(EJEMPLOS / "montaje_preview" / "render.json"),
        "--accounts", str(CUENTAS),
        "--mode", modo, "--publish-key", clave,
    ]
    for destino in destinos:
        argumentos += ["--destination", destino]
    return argumentos


@necesita_ffmpeg
def test_recorrido_completo_simulado(tmp_path: Path, capsys) -> None:
    """Plan, edicion, autorizacion, cola, envio simulado, estado y TikTok."""
    destinos = [
        _destino("yt", "youtube_shorts", "canal_demo", "2026-09-24T18:30:00",
                 visibility="private", notify="false"),
        _destino("ig", "instagram_reels", "reels_demo", "2026-09-24T18:45:00",
                 visibility="public", share="true"),
        _destino("tk", "tiktok", "tiktok_demo", "2026-09-24T19:00:00",
                 visibility="public"),
    ]

    # 1. El plan sale con requisitos pendientes y NO tiene efectos externos.
    codigo = main(_plan_args(tmp_path, "demo-001", "mock", destinos))
    plan_json = _salida(capsys)
    assert codigo == int(ExitCode.NEEDS_REVIEW)
    assert plan_json["external_effects"] is False
    assert plan_json["admission"]["admissible_for_simulation"] is True
    assert plan_json["admission"]["admissible_for_real_dispatch"] is False
    ruta_plan = Path(plan_json["plan_path"])

    # 2. El operador completa lo que el guion no traia.
    import runpy
    import sys

    sys.argv = ["completar", str(ruta_plan)]
    with pytest.raises(SystemExit) as salida:
        runpy.run_path(str(COMPLETAR), run_name="__main__")
    assert salida.value.code == 0
    capsys.readouterr()

    # 3. Autorizacion.
    assert main(["--data-dir", str(tmp_path), "publish", "approve",
                 "--plan", str(ruta_plan), "--operator", "operador_demo"]) == 0
    aprobacion = _salida(capsys)
    assert aprobacion["plan_revision"] == 2
    assert aprobacion["authorization"]["staging_authorized"] is True

    # 4. Cola.
    assert main(["--data-dir", str(tmp_path), "publish", "enqueue",
                 "--plan", str(ruta_plan)]) == 0
    cola = _salida(capsys)
    estados = {d["destination_id"]: d["state"] for d in cola["destinations"]}
    assert estados == {
        "yt": "scheduled_local", "ig": "scheduled_local", "tk": "draft"
    }

    # 5-7. El trabajador, con reloj de ensayo.
    for instante in (
        "2026-09-24T16:35:00Z", "2026-09-24T16:46:00Z", "2026-09-24T16:52:00Z"
    ):
        main(["--data-dir", str(tmp_path), "publish", "worker", "--once",
              "--publish-key", "demo-001", "--now", instante])
        capsys.readouterr()

    # 8. Repetir no envia nada nuevo.
    main(["--data-dir", str(tmp_path), "publish", "worker", "--once",
          "--publish-key", "demo-001", "--now", "2026-09-24T17:10:00Z"])
    repetido = _salida(capsys)
    assert repetido["processed"] == []

    # 9. TikTok: exportar no publica.
    assert main(["--data-dir", str(tmp_path), "publish", "export-manual",
                 "--publish-key", "demo-001", "--destination", "tk"]) == 0
    exportacion = _salida(capsys)
    assert exportacion["state"] == "awaiting_manual"
    assert exportacion["published"] is False
    assert exportacion["simulation"] is True
    paquete = Path(exportacion["package"]["package_path"])
    assert (paquete / "NO_PUBLICABLE.txt").is_file()

    # 10. Registro manual con origen humano.
    assert main([
        "--data-dir", str(tmp_path), "publish", "record-manual",
        "--publish-key", "demo-001", "--destination", "tk",
        "--url", "https://www.tiktok.com/@cuenta_demo/video/0000000000000000000",
        "--at", "2026-09-24T19:20:00+02:00",
    ]) == 0
    manual = _salida(capsys)
    assert manual["evidence_source"] == "operator_reported"
    assert manual["api_confirmed"] is False

    # 11. Estado final: entregado en simulacion, sin identificadores reales.
    recibo_path = tmp_path / "publication.json"
    main(["--data-dir", str(tmp_path), "publish", "status",
          "--publish-key", "demo-001", "--out", str(recibo_path)])
    estado = _salida(capsys)
    recibo = estado["receipt"]
    assert recibo["simulation"] is True
    assert recibo["summary"]["overall"] == "delivered"
    por_id = {d["destination_id"]: d for d in recibo["destinations"]}
    assert por_id["yt"]["publicly_visible"] is False  # se pidio privado
    assert por_id["ig"]["publicly_visible"] is True
    assert por_id["tk"]["state"] == "manually_reported"
    for destino in recibo["destinations"]:
        assert destino["real_remote_id"] is None
        assert destino["permalink"] in (None, ) or destino["state"] == "manually_reported"
    # Y el recibo escrito no lleva secretos.
    texto = recibo_path.read_text(encoding="utf-8")
    for marca in ("X-Amz-Signature", "access_token", "Authorization"):
        assert marca not in texto


@necesita_ffmpeg
def test_el_mismo_paquete_en_modo_real_se_rechaza_antes_de_llamar(
    tmp_path: Path, capsys
) -> None:
    """Ni OAuth, ni staging, ni una peticion: se corta en la admision."""
    destinos = [
        _destino("yt", "youtube_shorts", "canal_demo", "2026-09-24T18:30:00",
                 visibility="private"),
        _destino("ig", "instagram_reels", "reels_demo", "2026-09-24T18:45:00",
                 visibility="public"),
    ]
    codigo = main(_plan_args(tmp_path, "demo-real", "real", destinos))
    plan_json = _salida(capsys)
    assert codigo == int(ExitCode.NEEDS_REVIEW)
    assert plan_json["admission"]["admissible_for_real_dispatch"] is False
    motivos = " ".join(plan_json["admission"]["reasons"])
    assert "simulad" in motivos
    assert "sin contrastar" in motivos

    codigo = main([
        "--data-dir", str(tmp_path), "publish", "approve",
        "--plan", plan_json["plan_path"], "--operator", "operador_demo",
    ])
    assert codigo == int(ExitCode.VALIDATION)
    error = _salida(capsys)
    assert error["error_code"] == "publish_not_admissible"
    assert "NO es admisible para envio real" in error["error_message"]
