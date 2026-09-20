"""Fixtures comunes. Ninguna prueba usa red ni claves."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from viralgen.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Aisla las pruebas de la configuracion de la maquina."""
    for name in list(os.environ):
        if name.startswith("VIRALGEN_") or name.startswith("OPENAI_"):
            monkeypatch.delenv(name, raising=False)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        min_free_disk_mb=0,
        log_level="ERROR",
    )


@pytest.fixture
def demo_pack() -> Path:
    return FIXTURES / "facts_demo_test.json"


@pytest.fixture
def real_pack() -> Path:
    return FIXTURES / "facts_real_test.json"


@pytest.fixture
def pending_pack() -> Path:
    return FIXTURES / "facts_pending_test.json"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def run_pipeline(settings):
    """Ejecuta el pipeline simulado y devuelve (resultado, ajustes)."""
    from viralgen.pipeline import JobRequest, Pipeline

    def _run(**kwargs):
        request = JobRequest(
            command=kwargs.pop("command", "generate"),
            profile_id=kwargs.pop("profile_id", "infantil_cuentos"),
            topic=kwargs.pop("topic", "aprender a compartir"),
            duration_s=kwargs.pop("duration_s", None),
            source_pack=kwargs.pop("source_pack", None),
            simulation=kwargs.pop("simulation", True),
            seed=kwargs.pop("seed", 42),
            job_key=kwargs.pop("job_key", "prueba-001"),
            idea_count=kwargs.pop("idea_count", None),
        )
        return Pipeline(settings, request, **kwargs).run()

    return _run


@pytest.fixture
def documento_infantil(run_pipeline):
    from viralgen.schemas.document import ScriptDocument

    outcome = run_pipeline()
    return ScriptDocument.model_validate(read_json(Path(outcome.script_path)))


@pytest.fixture
def documento_curiosidades(run_pipeline, demo_pack):
    from viralgen.schemas.document import ScriptDocument

    outcome = run_pipeline(
        profile_id="curiosidades_corto",
        topic="pieza que reparte la fuerza",
        source_pack=demo_pack,
        job_key="prueba-cur",
    )
    return ScriptDocument.model_validate(read_json(Path(outcome.script_path)))
