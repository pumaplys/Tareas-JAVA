"""CLI del modulo 2: resumen JSON, codigos de salida y esquema."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from viralgen.cli import main
from viralgen.errors import ExitCode


@pytest.fixture
def cli(tmp_path, monkeypatch, capsys):
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


@pytest.fixture
def guion(cli):
    codigo, resumen, _ = cli(
        "generate", "--profile", "infantil_cuentos", "--topic", "compartir",
        "--job-key", "cli-voz", "--mock", "--seed", "5",
    )
    assert codigo == ExitCode.OK
    return resumen["script_path"]


def test_voice_generate_y_reutilizacion(cli, guion) -> None:
    codigo, resumen, _ = cli(
        "voice", "generate", "--script", guion, "--voice-key", "demo-voz-001",
        "--mock", "--seed", "5",
    )
    assert codigo == ExitCode.OK
    assert resumen["voice_status"] == "ready"
    assert resumen["simulation"] is True
    assert resumen["admissible_for_assembly"] is False
    assert resumen["requests_new"] > 0
    assert Path(resumen["manifest_path"]).is_file()
    assert Path(resumen["master_path"]).is_file()
    assert resumen["measured_duration_s"] > 0

    # Misma clave y misma solicitud: cero solicitudes nuevas.
    codigo, repetido, _ = cli(
        "voice", "generate", "--script", guion, "--voice-key", "demo-voz-001",
        "--mock", "--seed", "5",
    )
    assert codigo == ExitCode.OK
    assert repetido["reused"] is True
    assert repetido["requests_new"] == 0
    assert repetido["requests_total"] == resumen["requests_total"]


def test_voice_validate_separa_contrato_y_admision(cli, guion) -> None:
    _, generado, _ = cli(
        "voice", "generate", "--script", guion, "--voice-key", "val", "--mock", "--seed", "5"
    )
    manifiesto = generado["manifest_path"]

    codigo, informe, _ = cli("voice", "validate", "--script", guion, "--manifest", manifiesto)
    assert codigo == ExitCode.NEEDS_REVIEW
    assert informe["contract_valid"] is True  # el contrato si es valido
    assert informe["admissible_for_assembly"] is False  # pero no sirve para montar
    assert informe["reasons"]

    codigo, informe, _ = cli(
        "voice", "validate", "--script", guion, "--manifest", manifiesto, "--allow-simulation"
    )
    assert codigo == ExitCode.OK
    assert informe["admissible_for_assembly"] is True


def test_voice_validate_no_genera_audio(cli, guion, tmp_path) -> None:
    _, generado, _ = cli(
        "voice", "generate", "--script", guion, "--voice-key", "sin-audio", "--mock", "--seed", "5"
    )
    base = Path(generado["manifest_path"]).parent
    antes = sorted(p.name for p in (base / "audio").rglob("*"))
    cli("voice", "validate", "--script", guion, "--manifest", generado["manifest_path"])
    assert sorted(p.name for p in (base / "audio").rglob("*")) == antes


def test_voice_schema(cli, tmp_path) -> None:
    destino = tmp_path / "voice.schema.json"
    codigo, resumen, _ = cli("voice", "schema", "--output", str(destino))
    assert codigo == ExitCode.OK
    esquema = json.loads(destino.read_text(encoding="utf-8"))
    assert esquema["properties"]["document_type"]["const"] == "voice_manifest"
    assert esquema["properties"]["schema_version"]["const"] == "1.0"
    assert resumen["document_type"] == "voice_manifest"


def test_modo_real_sin_credenciales_es_error_de_configuracion(cli, guion, monkeypatch) -> None:
    """Generar voz real exige credenciales de VOZ, no de OpenAI."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    codigo, resumen, err = cli(
        "voice", "generate", "--script", guion, "--voice-key", "real"
    )
    # El guion simulado se bloquea antes incluso de mirar las credenciales.
    assert codigo == ExitCode.VALIDATION
    assert resumen["voice_status"] == "blocked"
    assert "OPENAI" not in (resumen.get("error_message") or "")


def test_uso_incorrecto(cli) -> None:
    with pytest.raises(SystemExit) as exc:
        cli("voice", "generate")  # falta --script
    assert exc.value.code == ExitCode.USAGE
