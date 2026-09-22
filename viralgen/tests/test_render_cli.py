"""CLI del modulo 4: `viralgen render plan|generate|validate|schema`.

`stdout` lleva el resumen JSON y nada mas; los mensajes humanos y el progreso
van por otro lado. Ningun comando necesita credenciales: se montan archivos
que ya existen.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from viralgen.cli import main
from viralgen.errors import ExitCode
from viralgen.render.ffmpeg import probe_capabilities

TIENE_FFMPEG = probe_capabilities("ffmpeg", "ffprobe").usable


def necesita_ffmpeg(prueba):
    """Aplica DOS marcas: `ffmpeg` (seleccion) y `skipif` (salto sin las herramientas).

    OJO con la forma corta: `pytest.mark.ffmpeg(pytest.mark.skipif(...))` NO
    compone dos marcas. Convierte el `skipif` en un ARGUMENTO de la marca
    `ffmpeg` y el salto queda muerto, asi que sin FFmpeg la prueba intenta
    ejecutarse en vez de saltarse. Se componen apilandolas.
    """
    con_salto = pytest.mark.skipif(
        not TIENE_FFMPEG,
        reason="FFmpeg/ffprobe no disponibles",
    )(prueba)
    return pytest.mark.ffmpeg(con_salto)


def _ejecutar(argv: list[str], capsys, monkeypatch, render_settings) -> tuple[int, dict]:
    monkeypatch.setenv("VIRALGEN_DATA_DIR", str(render_settings.data_dir))
    monkeypatch.setenv("VIRALGEN_PROFILES_PATH", str(render_settings.profiles_path))
    monkeypatch.setenv("VIRALGEN_MIN_FREE_DISK_MB", "0")
    monkeypatch.setenv("VIRALGEN_LOG_LEVEL", "ERROR")
    codigo = main(argv)
    salida = capsys.readouterr().out
    return codigo, json.loads(salida)


def test_render_schema(tmp_path, capsys, monkeypatch, render_settings) -> None:
    destino = tmp_path / "render.schema.json"
    codigo, datos = _ejecutar(
        ["render", "schema", "--output", str(destino)],
        capsys, monkeypatch, render_settings,
    )
    assert codigo == int(ExitCode.OK)
    assert datos["document_type"] == "render_manifest"
    assert datos["schema_version"] == "1.0"

    esquema = json.loads(destino.read_text(encoding="utf-8"))
    assert esquema["title"] == "viralgen render manifest"
    for grupo in (
        "sources", "control", "output", "timeline", "audio", "captions",
        "processing", "resources", "inspection",
    ):
        assert grupo in esquema["properties"]


def test_render_plan_no_necesita_credenciales(
    render_inputs, capsys, monkeypatch, render_settings
) -> None:
    """Una configuracion de proveedores ausente no impide planificar."""
    script, voice, media = render_inputs
    for variable in ("OPENAI_API_KEY", "ELEVENLABS_API_KEY", "RUNWAYML_API_SECRET"):
        monkeypatch.delenv(variable, raising=False)

    codigo, datos = _ejecutar(
        [
            "render", "plan",
            "--script", str(script), "--voice", str(voice), "--media", str(media),
            "--preview",
        ],
        capsys, monkeypatch, render_settings,
    )
    assert codigo == int(ExitCode.OK)
    assert datos["can_render"] is True
    assert datos["timeline"]["total_frames"] > 0
    assert datos["plan_sha256"]
    # El plan enumera lo que haria, sin codificar.
    assert len(datos["scenes"]) >= 2
    assert datos["captions"]["group_count"] > 0
    assert datos["tools"]["libass"] is TIENE_FFMPEG


def test_render_plan_sin_preview_bloquea_una_simulacion(
    render_inputs, capsys, monkeypatch, render_settings
) -> None:
    script, voice, media = render_inputs
    codigo, datos = _ejecutar(
        [
            "render", "plan",
            "--script", str(script), "--voice", str(voice), "--media", str(media),
        ],
        capsys, monkeypatch, render_settings,
    )
    assert codigo == int(ExitCode.NEEDS_REVIEW)
    assert datos["can_render"] is False
    assert datos["blocked"] is True


@necesita_ffmpeg
def test_render_generate_y_reutilizacion(
    render_inputs, capsys, monkeypatch, render_settings
) -> None:
    script, voice, media = render_inputs
    argumentos = [
        "render", "generate",
        "--script", str(script), "--voice", str(voice), "--media", str(media),
        "--render-key", "cli-001", "--preview",
    ]
    codigo, datos = _ejecutar(argumentos, capsys, monkeypatch, render_settings)
    assert codigo == int(ExitCode.OK)
    assert datos["render_status"] == "ready"
    assert datos["render_mode"] == "preview"
    assert datos["simulation"] is True
    assert datos["admissible_for_publisher"] is False
    assert datos["renders_new"] > 0
    assert Path(datos["output_path"]).is_file()
    assert Path(datos["captions_path"]).is_file()

    codigo, repetido = _ejecutar(argumentos, capsys, monkeypatch, render_settings)
    assert codigo == int(ExitCode.OK)
    assert repetido["reused"] is True
    assert repetido["renders_new"] == 0


@necesita_ffmpeg
def test_render_validate_separa_los_tres_veredictos(
    render_inputs, capsys, monkeypatch, render_settings
) -> None:
    script, voice, media = render_inputs
    _codigo, generado = _ejecutar(
        [
            "render", "generate",
            "--script", str(script), "--voice", str(voice), "--media", str(media),
            "--render-key", "cli-val", "--preview",
        ],
        capsys, monkeypatch, render_settings,
    )
    manifiesto = generado["manifest_path"]

    base = [
        "render", "validate",
        "--script", str(script), "--voice", str(voice), "--media", str(media),
        "--manifest", manifiesto,
    ]
    codigo, preview = _ejecutar(
        [*base, "--allow-simulation"], capsys, monkeypatch, render_settings
    )
    assert codigo == int(ExitCode.OK)
    assert preview["mode"] == "preview"
    assert preview["contract_valid"] is True
    assert preview["admissible_for_preview"] is True
    assert preview["admissible_for_publisher"] is False
    assert preview["preview_reasons"] == []

    codigo, estricto = _ejecutar(base, capsys, monkeypatch, render_settings)
    assert codigo == int(ExitCode.NEEDS_REVIEW)
    assert estricto["mode"] == "publisher"
    # Elegir el modo del informe NO cambia ningun check.
    assert estricto["checks"] == preview["checks"]
    assert estricto["admissible_for_publisher"] is False
    assert "publicar" in estricto["note"]


def test_render_validate_no_se_fia_de_un_manifiesto_manipulado(
    render_inputs, capsys, monkeypatch, render_settings, tmp_path
) -> None:
    """Sin herramientas no hay veredicto tecnico positivo."""
    script, voice, media = render_inputs
    falso = tmp_path / "render.json"
    falso.write_text(
        json.dumps({"document_type": "render_manifest", "schema_version": "1.0"}),
        encoding="utf-8",
    )
    codigo, datos = _ejecutar(
        [
            "render", "validate",
            "--script", str(script), "--voice", str(voice), "--media", str(media),
            "--manifest", str(falso), "--allow-simulation",
        ],
        capsys, monkeypatch, render_settings,
    )
    assert codigo == int(ExitCode.NEEDS_REVIEW)
    assert datos["contract_valid"] is False
    assert datos["checks"]["manifiesto_valido"] is False


def test_uso_incorrecto(capsys, monkeypatch, render_settings) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["render", "generate", "--script", "falta-todo.json"])
    assert excinfo.value.code == int(ExitCode.USAGE)


def test_el_stdout_solo_lleva_json(
    render_inputs, capsys, monkeypatch, render_settings
) -> None:
    """El resumen se puede parsear sin filtrar ruido."""
    script, voice, media = render_inputs
    monkeypatch.setenv("VIRALGEN_DATA_DIR", str(render_settings.data_dir))
    monkeypatch.setenv("VIRALGEN_PROFILES_PATH", str(render_settings.profiles_path))
    monkeypatch.setenv("VIRALGEN_LOG_LEVEL", "ERROR")
    main(
        [
            "render", "plan",
            "--script", str(script), "--voice", str(voice), "--media", str(media),
            "--preview",
        ]
    )
    capturado = capsys.readouterr()
    json.loads(capturado.out)          # no levanta: es JSON puro
    assert capturado.out.lstrip().startswith("{")
