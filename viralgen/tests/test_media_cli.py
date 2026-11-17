"""CLI del modulo 3: resumen JSON, codigos de salida y esquema."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from viralgen.cli import main
from viralgen.errors import ExitCode


@pytest.fixture
def cli(tmp_path, monkeypatch, capsys, image_only_profiles):
    monkeypatch.setenv("VIRALGEN_DATA_DIR", str(tmp_path / "datos"))
    monkeypatch.setenv("VIRALGEN_MIN_FREE_DISK_MB", "0")
    monkeypatch.setenv("VIRALGEN_LOG_LEVEL", "ERROR")
    monkeypatch.setenv("VIRALGEN_PROFILES_PATH", str(image_only_profiles))

    def _run(*argv: str):
        codigo = main(list(argv))
        capturado = capsys.readouterr()
        try:
            resumen = json.loads(capturado.out)
        except json.JSONDecodeError:
            resumen = None
        return codigo, resumen, capturado.err

    return _run


@pytest.fixture
def entradas(cli):
    _, guion, _ = cli(
        "generate", "--profile", "infantil_cuentos", "--topic", "compartir",
        "--job-key", "cli-media", "--mock", "--seed", "5",
    )
    _, voz, _ = cli(
        "voice", "generate", "--script", guion["script_path"],
        "--voice-key", "cli-voz", "--mock", "--seed", "5",
    )
    return guion["script_path"], voz["manifest_path"]


def test_media_plan_sin_claves(cli, entradas) -> None:
    guion, voz = entradas
    codigo, resumen, _ = cli("media", "plan", "--script", guion, "--voice", voz, "--mock")
    assert codigo == ExitCode.OK
    assert resumen["blocked"] is False
    assert resumen["can_run"] is True
    assert resumen["images_to_generate"] > 0
    assert resumen["clips_to_generate"] == 0
    assert resumen["missing_credentials"] == []
    assert resumen["admission"]["input_admission"] == "ok"


def test_media_plan_real_informa_de_credenciales(cli, entradas) -> None:
    """Sin --mock el plan puede ejecutarse igual y decir que falta."""
    guion, voz = entradas
    codigo, resumen, _ = cli("media", "plan", "--script", guion, "--voice", voz)
    # La entrada simulada bloquea la ruta real; ademas faltan credenciales.
    assert codigo in (ExitCode.VALIDATION, ExitCode.NEEDS_REVIEW)
    assert resumen["admission"]["input_admission"] == "blocked"


def test_media_generate_y_reutilizacion(cli, entradas) -> None:
    guion, voz = entradas
    codigo, resumen, _ = cli(
        "media", "generate", "--script", guion, "--voice", voz,
        "--media-key", "demo-visual-001", "--mock", "--seed", "5",
    )
    assert codigo == ExitCode.OK
    assert resumen["media_status"] == "ready"
    assert resumen["simulation"] is True
    assert resumen["admissible_for_assembly"] is False
    assert Path(resumen["manifest_path"]).is_file()
    assert Path(resumen["contact_sheet_path"]).is_file()
    assert resumen["usage"]["used_this_run"]["generation_attempts"] > 0

    codigo, repetido, _ = cli(
        "media", "generate", "--script", guion, "--voice", voz,
        "--media-key", "demo-visual-001", "--mock", "--seed", "5",
    )
    assert codigo == ExitCode.OK
    assert repetido["reused"] is True
    assert repetido["usage"]["used_this_run"] == {}


def test_media_validate_separa_los_tres_veredictos(cli, entradas) -> None:
    guion, voz = entradas
    _, generado, _ = cli(
        "media", "generate", "--script", guion, "--voice", voz,
        "--media-key", "val", "--mock", "--seed", "5",
    )
    manifiesto = generado["manifest_path"]

    codigo, produccion, _ = cli(
        "media", "validate", "--script", guion, "--voice", voz, "--manifest", manifiesto
    )
    assert codigo == ExitCode.NEEDS_REVIEW
    assert produccion["mode"] == "production"
    assert produccion["contract_valid"] is True
    assert produccion["admissible_for_preview"] is True
    assert produccion["admissible_for_assembly"] is False

    codigo, pruebas, _ = cli(
        "media", "validate", "--script", guion, "--voice", voz,
        "--manifest", manifiesto, "--allow-simulation",
    )
    assert codigo == ExitCode.OK
    assert pruebas["mode"] == "preview"
    assert pruebas["admissible_for_assembly"] is False
    # Los checks son IDENTICOS en los dos modos.
    assert pruebas["checks"] == produccion["checks"]


def test_media_schema(cli, tmp_path) -> None:
    destino = tmp_path / "media.schema.json"
    codigo, resumen, _ = cli("media", "schema", "--output", str(destino))
    assert codigo == ExitCode.OK
    esquema = json.loads(destino.read_text(encoding="utf-8"))
    assert esquema["properties"]["document_type"]["const"] == "media_manifest"
    assert esquema["properties"]["schema_version"]["const"] == "1.0"
    assert resumen["document_type"] == "media_manifest"


def test_un_bloqueo_inicial_no_produce_manifiesto(cli, entradas) -> None:
    guion, voz = entradas
    codigo, resumen, _ = cli(
        "media", "generate", "--script", guion, "--voice", voz, "--media-key", "real-bloqueo",
    )
    assert codigo == ExitCode.VALIDATION
    assert resumen["media_status"] == "blocked"
    assert resumen["manifest_path"] is None
    assert resumen["usage"]["used_total"].get("generation_attempts", 0) == 0


def test_uso_incorrecto(cli) -> None:
    with pytest.raises(SystemExit) as exc:
        cli("media", "generate", "--script", "x.json")  # falta --voice
    assert exc.value.code == ExitCode.USAGE


def test_media_models_informa_del_catalogo_sin_llamar_a_nadie(cli) -> None:
    codigo, resumen, _ = cli("media", "models")

    assert codigo == int(ExitCode.OK)
    modelos = resumen["models"]
    assert set(modelos) == {"gpt-image-1", "gpt-image-2.6-sunburst", "mock-image-1"}
    # El informe distingue lo verificado de lo supuesto, modelo a modelo.
    assert modelos["gpt-image-1"]["verified"] is True
    assert modelos["gpt-image-1"]["assumed_params"] == []
    assert modelos["gpt-image-2.6-sunburst"]["verified"] is False
    assert modelos["gpt-image-2.6-sunburst"]["assumed_params"]
    assert modelos["gpt-image-2.6-sunburst"]["blocking_checks"]


def test_media_models_lleva_el_registro_y_sus_recuentos(cli) -> None:
    _, resumen, _ = cli("media", "models")
    verificacion = resumen["verification"]
    totales = verificacion["totals"]

    # Los recuentos suman: un informe que no sume miente por omision.
    assert (
        totales["pending"] + totales["partial"] + totales["verified"] == totales["total"]
    )
    # Y el informe dice por que no hay evidencia propia de la documentacion.
    assert "developers.openai.com" in verificacion["reason"]
    assert verificacion["reviewer_consulted_on"] == "2026-11-17"
    assert "gpt-image-2.6-sunburst" in verificacion["blocked_models"]


def test_media_models_filtra_por_modelo(cli) -> None:
    _, resumen, _ = cli("media", "models", "--model", "gpt-image-1")
    assert set(resumen["models"]) == {"gpt-image-1"}


def test_media_models_rechaza_un_modelo_inexistente(cli) -> None:
    codigo, resumen, _ = cli("media", "models", "--model", "gpt-image-999")
    assert codigo == int(ExitCode.CONFIG)
    assert resumen["error_code"] == "config_error"
    assert "desconocido" in resumen["error_message"]
    # El error enumera lo que si esta declarado, en vez de solo negar.
    assert resumen["details"]["known"] == [
        "gpt-image-1",
        "gpt-image-2.6-sunburst",
        "mock-image-1",
    ]
