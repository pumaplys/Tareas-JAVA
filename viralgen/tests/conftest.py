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
    prefijos = (
        "VIRALGEN_",
        "OPENAI_",
        # Modulo 5: si la maquina tiene credenciales de publicacion, las
        # pruebas NO deben verlas. El modo simulado no las usa, y que una
        # prueba pase por tenerlas seria justo el fallo que se quiere evitar.
        "YOUTUBE_",
        "META_",
        "INSTAGRAM_",
        "PUBLISH_",
        "ELEVENLABS_",
        "RUNWAY",
    )
    for name in list(os.environ):
        if name.startswith(prefijos):
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


# ---------------------------------------------------------------------------
# Modulo 4: montaje
# ---------------------------------------------------------------------------


def _tools_available() -> bool:
    from viralgen.render.ffmpeg import probe_capabilities

    return probe_capabilities("ffmpeg", "ffprobe").usable


#: Marca de integracion local real. NO se salta en la ejecucion de aceptacion:
#: una suite verde porque todas se saltaron no acredita ningun render.
TIENE_FFMPEG = _tools_available()
necesita_ffmpeg = pytest.mark.skipif(
    not TIENE_FFMPEG,
    reason="FFmpeg/ffprobe (con libx264, aac y libass) no estan disponibles",
)


@pytest.fixture
def render_settings(tmp_path: Path, image_only_profiles: Path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        min_free_disk_mb=0,
        log_level="ERROR",
        profiles_path=image_only_profiles,
    )


@pytest.fixture
def render_short_inputs(render_settings):
    """Guion CORTO + voz, para que cada prueba codifique poco video.

    Las pruebas del modulo 4 codifican de verdad: un guion de 50 s multiplica
    por dos el tiempo de toda la suite sin comprobar nada adicional.
    """
    from viralgen.pipeline import JobRequest, Pipeline
    from viralgen.voice.pipeline import VoiceJobRequest, VoicePipeline

    guion = Pipeline(
        render_settings,
        JobRequest(
            command="generate",
            profile_id="infantil_cuentos",
            topic="aprender a compartir",
            duration_s=40,   # minimo del perfil: menos video que codificar por prueba
            simulation=True,
            seed=5,
            job_key="render-corto",
        ),
    ).run()
    assert guion.script_path is not None
    voz = VoicePipeline(
        render_settings,
        VoiceJobRequest(
            script_path=Path(guion.script_path),
            voice_key="render-corto-voz",
            simulation=True,
            seed=5,
        ),
    ).run()
    assert voz.manifest_path is not None
    return Path(guion.script_path), Path(voz.manifest_path)


@pytest.fixture
def render_inputs(render_settings, render_short_inputs):
    """Guion + voz + medios simulados, coherentes entre si."""
    from viralgen.media.pipeline import MediaJobRequest, MediaPipeline

    script, voice = render_short_inputs
    medios = MediaPipeline(
        render_settings,
        MediaJobRequest(
            script_path=script,
            voice_path=voice,
            media_key="render-base",
            simulation=True,
            seed=5,
        ),
    ).run()
    assert medios.manifest_path is not None, medios.admission_reasons
    return script, voice, Path(medios.manifest_path)


@pytest.fixture
def run_render(render_settings):
    """Ejecuta el pipeline de montaje."""
    from viralgen.render.pipeline import RenderJobRequest, RenderPipeline

    def _run(script: Path, voice: Path, media: Path, **kwargs):
        peticion = RenderJobRequest(
            script_path=script,
            voice_path=voice,
            media_path=media,
            render_key=kwargs.pop("render_key", "render-001"),
            preview=kwargs.pop("preview", True),
            seed=kwargs.pop("seed", 5),
        )
        return RenderPipeline(render_settings, peticion, **kwargs).run()

    return _run


# ---------------------------------------------------------------------------
# Modulo 5: fabricas de planes y recibos
# ---------------------------------------------------------------------------
#
# Son FIXTURES AISLADAS con forma de datos de produccion, para poder probar
# contratos e identidad sin montar un video ni tocar los ejemplos del
# repositorio. No son ejemplos publicables ni reclasifican nada: el preview de
# `examples/` sigue siendo un preview.

_HASH_A = "a" * 64
_HASH_B = "b" * 64
_UUID = "00000000-0000-4000-8000-00000000000"


def publish_sources(**cambios):
    """Un `SourceBundle` de prueba, de produccion salvo que se diga otra cosa."""
    from datetime import datetime, timezone

    from viralgen.publish.schemas import DocumentRef, SourceBundle, VideoRef

    def documento(letra: str, simulation: bool = False) -> DocumentRef:
        return DocumentRef(
            path=f"/paquete/{letra}.json",
            sha256=letra * 64,
            size_bytes=1024,
            schema_version="1.0",
            simulation=simulation,
        )

    base = dict(
        job_id=_UUID + "1",
        render_run_id=_UUID + "2",
        voice_run_id=_UUID + "3",
        media_run_id=_UUID + "4",
        channel="curiosidades",
        profile_id="curiosidades_es",
        language="es-ES",
        render_mode="production",
        render_simulation=False,
        script=documento("c"),
        voice=documento("d"),
        media=documento("e"),
        render=documento("f"),
        video=VideoRef(
            path="/paquete/video.mp4",
            sha256=_HASH_A,
            size_bytes=5_000_000,
            container_duration_s=25.5,
            width=1080,
            height=1920,
            fps=30.0,
            verified_at=datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc),
        ),
        admissible_for_publisher_declared=True,
        admissible_for_publisher_recomputed=True,
    )
    base.update(cambios)
    return SourceBundle(**base)


def publish_destination(**cambios):
    """Un destino de YouTube listo para autorizar."""
    from datetime import datetime, timezone

    from viralgen.publish.schemas import (
        AccountRef,
        AudienceDecision,
        DestinationMetadata,
        DestinationOptions,
        DestinationState,
        PlanDestination,
        ScheduleSpec,
        SyntheticDisclosure,
        TextSource,
        Visibility,
    )
    from viralgen.schemas.common import Platform

    metadata = cambios.pop(
        "metadata",
        DestinationMetadata(
            title="Por que el cielo cambia de color",
            description="Un repaso corto a la dispersion de la luz.",
            tags=["ciencia", "luz"],
            hashtags=["ciencia"],
            language="es-ES",
            audience=AudienceDecision.NOT_MADE_FOR_KIDS,
            synthetic_disclosure=SyntheticDisclosure.NO_REALISTIC_SYNTHETIC_MEDIA,
            text_source=TextSource.SCRIPT_PUBLISHING_PLAN,
            within_local_limits=True,
        ),
    )
    horario = cambios.pop(
        "schedule",
        ScheduleSpec(
            scheduled_at_utc=datetime(2026, 9, 24, 16, 30, tzinfo=timezone.utc),
            timezone="Europe/Madrid",
            local_time="2026-09-24T18:30:00",
            fold=0,
            late_start_window_s=900,
        ),
    )
    base = dict(
        destination_id="yt_principal",
        platform=Platform.YOUTUBE_SHORTS,
        account=AccountRef(
            platform=Platform.YOUTUBE_SHORTS,
            alias="canal_curiosidades",
            expected_account_id="UC_canal_de_pruebas",
            account_id_kind="youtube_channel_id",
        ),
        metadata=metadata,
        requested_visibility=Visibility.PRIVATE,
        schedule=horario,
        options=DestinationOptions(notify_subscribers=False),
        state=DestinationState.DRAFT,
    )
    base.update(cambios)
    return PlanDestination(**base)


def publish_plan(**cambios):
    """Un `PublicationPlan` coherente, con su fingerprint recalculado."""
    from datetime import datetime, timezone

    from viralgen.publish.schemas import (
        AdmissionSummary,
        PublicationPlan,
        PublishMode,
        VerificationSummary,
    )

    base = dict(
        plan_id=_UUID + "5",
        revision=1,
        created_at=datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc),
        mode=PublishMode.MOCK,
        publish_key="demo-001",
        intent_fingerprint=_HASH_B,
        sources=publish_sources(),
        destinations=[publish_destination()],
        admission=AdmissionSummary(
            contract_valid=True,
            admissible_for_simulation=True,
            admissible_for_real_dispatch=False,
        ),
        verification=VerificationSummary(
            reason="fixture de prueba",
            checks=[],
            blocked_targets=[],
            note="fixture",
        ),
    )
    base.update(cambios)
    plan = PublicationPlan(**base)
    if "intent_fingerprint" not in cambios:
        plan = plan.model_copy(
            update={"intent_fingerprint": plan.compute_intent_fingerprint()}
        )
    return plan
