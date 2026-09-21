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


# ---------------------------------------------------------------------------
# Modulo 2: voz
# ---------------------------------------------------------------------------


@pytest.fixture
def voice_settings(tmp_path: Path) -> Settings:
    """Ajustes con limites holgados y sin credenciales."""
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        min_free_disk_mb=0,
        log_level="ERROR",
    )


@pytest.fixture
def script_path(voice_settings) -> Path:
    """Genera un guion simulado con el modulo 1 y devuelve su ruta."""
    from viralgen.pipeline import JobRequest, Pipeline

    peticion = JobRequest(
        command="generate",
        profile_id="infantil_cuentos",
        topic="aprender a compartir",
        simulation=True,
        seed=5,
        job_key="voz-base",
    )
    resultado = Pipeline(voice_settings, peticion).run()
    assert resultado.script_path is not None
    return Path(resultado.script_path)


@pytest.fixture
def run_voice(voice_settings):
    """Ejecuta el pipeline de voz simulado."""
    from viralgen.voice.pipeline import VoiceJobRequest, VoicePipeline

    def _run(script: Path, **kwargs):
        peticion = VoiceJobRequest(
            script_path=script,
            voice_key=kwargs.pop("voice_key", "voz-001"),
            simulation=kwargs.pop("simulation", True),
            seed=kwargs.pop("seed", 5),
        )
        return VoicePipeline(voice_settings, peticion, **kwargs).run()

    return _run


# ---------------------------------------------------------------------------
# Modulo 3: medios visuales
# ---------------------------------------------------------------------------


@pytest.fixture
def image_only_profiles(tmp_path: Path) -> Path:
    """Perfiles con `video_scene_budget: 0`.

    Es configuracion LEGITIMA del modulo 1: NO se modifica ningun guion ya
    vinculado a un voice.json para convertir sus escenas de video en imagen.
    """
    from viralgen.profiles import load_profiles

    datos = load_profiles().model_dump(mode="json")
    for perfil in datos["profiles"]:
        perfil["video_scene_budget"] = 0
    destino = tmp_path / "perfiles_solo_imagenes.json"
    destino.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    return destino


@pytest.fixture
def media_settings(tmp_path: Path, image_only_profiles: Path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        min_free_disk_mb=0,
        log_level="ERROR",
        profiles_path=image_only_profiles,
    )


@pytest.fixture
def media_inputs(media_settings) -> tuple[Path, Path]:
    """Genera guion y voz simulados coherentes y devuelve sus rutas."""
    from viralgen.pipeline import JobRequest, Pipeline
    from viralgen.voice.pipeline import VoiceJobRequest, VoicePipeline

    guion = Pipeline(
        media_settings,
        JobRequest(
            command="generate",
            profile_id="infantil_cuentos",
            topic="aprender a compartir",
            simulation=True,
            seed=5,
            job_key="medios-base",
        ),
    ).run()
    assert guion.script_path is not None
    voz = VoicePipeline(
        media_settings,
        VoiceJobRequest(
            script_path=Path(guion.script_path),
            voice_key="medios-voz",
            simulation=True,
            seed=5,
        ),
    ).run()
    assert voz.manifest_path is not None
    return Path(guion.script_path), Path(voz.manifest_path)


@pytest.fixture
def run_media(media_settings):
    """Ejecuta el pipeline de medios simulado."""
    from viralgen.media.pipeline import MediaJobRequest, MediaPipeline

    def _run(script: Path, voice: Path, **kwargs):
        peticion = MediaJobRequest(
            script_path=script,
            voice_path=voice,
            media_key=kwargs.pop("media_key", "visual-001"),
            simulation=kwargs.pop("simulation", True),
            seed=kwargs.pop("seed", 5),
        )
        return MediaPipeline(media_settings, peticion, **kwargs).run()

    return _run
