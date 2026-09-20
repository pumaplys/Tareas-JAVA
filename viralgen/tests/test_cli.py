"""CLI: codigos de salida, resumen JSON en stdout y mensajes en stderr."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from viralgen.cli import main
from viralgen.errors import ExitCode

FIXTURES = Path(__file__).parent / "fixtures"
PACK_DEMO = FIXTURES / "facts_demo_test.json"


@pytest.fixture
def cli(tmp_path, monkeypatch, capsys):
    """Ejecuta la CLI y devuelve (codigo, resumen_json, stderr)."""
    monkeypatch.setenv("VIRALGEN_DATA_DIR", str(tmp_path / "datos"))
    monkeypatch.setenv("VIRALGEN_MIN_FREE_DISK_MB", "0")
    monkeypatch.setenv("VIRALGEN_LOG_LEVEL", "ERROR")

    def _run(*argv: str):
        codigo = main(list(argv))
        capturado = capsys.readouterr()
        try:
            resumen = json.loads(capturado.out)
        except json.JSONDecodeError:
            resumen = None
        return codigo, resumen, capturado.err

    return _run


def test_generate_simulado(cli) -> None:
    codigo, resumen, err = cli(
        "generate", "--profile", "infantil_cuentos", "--topic", "compartir",
        "--job-key", "cli-1", "--mock", "--seed", "4",
    )
    assert codigo == ExitCode.OK
    assert resumen["status"] == "ready_for_production"
    assert resumen["simulation"] is True
    assert Path(resumen["script_path"]).is_file()
    # stdout solo lleva el resumen JSON.
    assert set(resumen) >= {"job_id", "status", "script_path", "exit_code"}


def test_ideas_simulado(cli) -> None:
    codigo, resumen, _ = cli(
        "ideas", "--profile", "infantil_cuentos", "--topic", "compartir",
        "--job-key", "cli-ideas", "--mock",
    )
    assert codigo == ExitCode.OK
    assert resumen["script_path"] is None
    assert Path(resumen["ideas_path"]).is_file()


def test_generate_curiosidades_con_catalogo(cli) -> None:
    codigo, resumen, _ = cli(
        "generate", "--profile", "curiosidades_largo", "--source-pack", str(PACK_DEMO),
        "--job-key", "cli-cur", "--mock", "--seed", "4",
    )
    assert codigo == ExitCode.OK
    assert resumen["production_status"] == "ready_for_production"


def test_needs_research_tiene_codigo_propio(cli) -> None:
    codigo, resumen, _ = cli(
        "generate", "--profile", "curiosidades_corto", "--job-key", "cli-nr", "--mock"
    )
    assert codigo == ExitCode.NEEDS_RESEARCH
    assert resumen["error_code"] == "needs_research"


def test_perfil_desconocido_es_error_de_configuracion(cli) -> None:
    codigo, resumen, err = cli("generate", "--profile", "no_existe", "--mock")
    assert codigo == ExitCode.CONFIG
    assert resumen["error_code"] == "profile_error"
    assert "no_existe" in err


def test_duracion_fuera_de_rango(cli) -> None:
    codigo, resumen, _ = cli(
        "generate", "--profile", "infantil_cuentos", "--duration", "300", "--mock"
    )
    assert codigo == ExitCode.CONFIG


def test_conflicto_de_job_key(cli) -> None:
    cli("generate", "--profile", "infantil_cuentos", "--topic", "a", "--job-key", "k", "--mock")
    codigo, resumen, _ = cli(
        "generate", "--profile", "infantil_cuentos", "--topic", "b", "--job-key", "k", "--mock"
    )
    assert codigo == ExitCode.CONFLICT
    assert resumen["error_code"] == "job_key_conflict"


def test_schema_y_validate(cli, tmp_path) -> None:
    destino = tmp_path / "script.schema.json"
    codigo, resumen, _ = cli("schema", "--output", str(destino))
    assert codigo == ExitCode.OK
    esquema = json.loads(destino.read_text(encoding="utf-8"))
    assert esquema["properties"]["schema_version"]["const"] == "1.0"
    assert resumen["schema_version"] == "1.0"

    codigo, generado, _ = cli(
        "generate", "--profile", "infantil_cuentos", "--job-key", "cli-val", "--mock"
    )
    codigo, informe, _ = cli("validate", "--input", generado["script_path"])
    assert codigo == ExitCode.OK
    assert informe["schema_valid"] is True
    assert informe["issues"] == []


def test_validate_detecta_documento_roto(cli, tmp_path) -> None:
    _, generado, _ = cli(
        "generate", "--profile", "infantil_cuentos", "--job-key", "cli-roto", "--mock"
    )
    ruta = Path(generado["script_path"])
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    datos["scenes"][0]["character_ids"].append("fantasma")
    roto = tmp_path / "roto.json"
    roto.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")

    codigo, informe, _ = cli("validate", "--input", str(roto))
    assert codigo == ExitCode.VALIDATION
    assert any(issue["code"] == "personaje_inexistente" for issue in informe["issues"])


def test_validate_json_que_no_cumple_el_esquema(cli, tmp_path) -> None:
    malo = tmp_path / "malo.json"
    malo.write_text('{"schema_version": "1.0"}', encoding="utf-8")
    codigo, informe, _ = cli("validate", "--input", str(malo))
    assert codigo == ExitCode.VALIDATION
    assert informe["schema_valid"] is False


def test_validate_archivo_inexistente(cli, tmp_path) -> None:
    codigo, resumen, _ = cli("validate", "--input", str(tmp_path / "nada.json"))
    assert codigo == ExitCode.CONFIG


def test_profiles(cli) -> None:
    codigo, resumen, _ = cli("profiles")
    assert codigo == ExitCode.OK
    assert {p["profile_id"] for p in resumen["profiles"]} == {
        "infantil_cuentos",
        "curiosidades_corto",
        "curiosidades_largo",
    }


def test_uso_incorrecto(cli) -> None:
    with pytest.raises(SystemExit) as exc:
        cli("generate")  # falta --profile
    assert exc.value.code == ExitCode.USAGE


def test_el_log_no_filtra_secretos(tmp_path, monkeypatch) -> None:
    from viralgen.logging_setup import configure_logging, get_logger

    configure_logging(tmp_path / "logs", "INFO", max_bytes=10_000, backup_count=1)
    get_logger("prueba").info("clave sk-abcdef1234567890 y Authorization: Bearer xyz123")
    contenido = (tmp_path / "logs" / "viralgen.log").read_text(encoding="utf-8")
    assert "sk-abcdef1234567890" not in contenido
    assert "xyz123" not in contenido
    assert "[REDACTED]" in contenido
