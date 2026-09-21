"""Preflight: detecta los problemas de TODAS las escenas antes de comprar nada."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import SecretStr

from viralgen.media.planner import build_plan
from viralgen.media.timeline import MediaTimelineError, VisualScene, VisualTimeline, build_visual_timeline
from viralgen.profiles import get_series_bible
from viralgen.media.references import build_selection
from viralgen.schemas.document import ScriptDocument
from viralgen.voice.schemas import VoiceManifest


def _cargar(media_inputs):
    guion, voz = media_inputs
    document = ScriptDocument.model_validate(json.loads(guion.read_text(encoding="utf-8")))
    manifest = VoiceManifest.model_validate(json.loads(voz.read_text(encoding="utf-8")))
    return document, manifest


def _contexto(media_settings, document):
    bible = get_series_bible("bosque_lumina", media_settings.series_bible_path)
    usados = {cid for escena in document.scenes for cid in escena.character_ids}
    seleccion = build_selection(
        bible=bible, used_character_ids=usados, settings=media_settings, pack=None
    )
    return bible, seleccion


def _plan(media_settings, document, timeline, bible, seleccion, **kwargs):
    return build_plan(
        settings=media_settings,
        document=document,
        timeline=timeline,
        bible=bible,
        selection=seleccion,
        simulation=kwargs.pop("simulation", True),
        media_storage=kwargs.pop("media_storage", None),
        data_dir=media_settings.effective_data_dir(simulation=True),
    )


def test_plan_de_solo_imagenes_no_necesita_video(media_settings, media_inputs) -> None:
    document, manifest = _cargar(media_inputs)
    bible, seleccion = _contexto(media_settings, document)
    timeline = build_visual_timeline(manifest, document)
    plan = _plan(media_settings, document, timeline, bible, seleccion)

    assert not plan.blocked
    assert plan.video_scene_count == 0
    assert plan.video_model is None
    assert plan.missing_tools == []  # sin video no hacen falta FFmpeg/ffprobe
    datos = plan.to_dict()
    assert datos["images_to_generate"] == len(document.scenes)
    assert datos["clips_to_generate"] == 0
    assert all(escena["prompt_chars"] > 0 for escena in datos["scenes"])


def test_una_escena_demasiado_larga_bloquea(media_settings, media_inputs) -> None:
    """Ninguna duracion admitida la cubre: `duration_not_supported`."""
    document, manifest = _cargar(media_inputs)
    bible, seleccion = _contexto(media_settings, document)
    escenas = [
        VisualScene(
            scene_id=escena.scene_id,
            order=escena.order,
            start_sample=escena.start_sample,
            end_sample=escena.end_sample,
            sample_rate_hz=manifest.master.sample_rate_hz,
            # La primera escena pasa a ser de video y dura mas de 10 s.
            asset_type="video" if escena.order == 1 else "image",
        )
        for escena in sorted(manifest.scenes, key=lambda item: item.order)
    ]
    largo = escenas[0]
    escenas[0] = VisualScene(
        scene_id=largo.scene_id, order=1, start_sample=0,
        end_sample=11 * manifest.master.sample_rate_hz,
        sample_rate_hz=manifest.master.sample_rate_hz, asset_type="video",
    )
    timeline = VisualTimeline(
        scenes=escenas,
        sample_rate_hz=manifest.master.sample_rate_hz,
        total_samples=manifest.master.sample_count,
    )
    plan = _plan(media_settings, document, timeline, bible, seleccion)
    codigos = {issue.code for issue in plan.issues}
    assert "duration_not_supported" in codigos
    assert plan.blocked


def test_exceso_de_escenas_de_video(media_settings, media_inputs) -> None:
    document, manifest = _cargar(media_inputs)
    bible, seleccion = _contexto(media_settings, document)
    escenas = [
        VisualScene(
            scene_id=escena.scene_id, order=escena.order,
            start_sample=escena.start_sample, end_sample=escena.end_sample,
            sample_rate_hz=manifest.master.sample_rate_hz, asset_type="video",
        )
        for escena in sorted(manifest.scenes, key=lambda item: item.order)
    ]
    timeline = VisualTimeline(
        scenes=escenas, sample_rate_hz=manifest.master.sample_rate_hz,
        total_samples=manifest.master.sample_count,
    )
    plan = _plan(media_settings, document, timeline, bible, seleccion)
    codigos = {issue.code for issue in plan.issues}
    assert "exceso_de_escenas_video" in codigos
    # Tambien avisa de los ejecutables si el plan contiene video.
    assert "ejecutables_ausentes" in codigos or plan.missing_tools == []
    assert plan.blocked


def test_prompt_demasiado_largo_bloquea(media_settings, media_inputs) -> None:
    document, manifest = _cargar(media_inputs)
    bible, seleccion = _contexto(media_settings, document)
    media_settings.media_max_prompt_chars = 100
    timeline = build_visual_timeline(manifest, document)
    plan = _plan(media_settings, document, timeline, bible, seleccion)
    assert "prompt_demasiado_largo" in {issue.code for issue in plan.issues}
    assert plan.blocked


def test_modelo_de_imagen_desconocido(media_settings, media_inputs) -> None:
    document, manifest = _cargar(media_inputs)
    bible, seleccion = _contexto(media_settings, document)
    media_settings.openai_image_model = "modelo-que-no-existe"
    timeline = build_visual_timeline(manifest, document)
    plan = _plan(media_settings, document, timeline, bible, seleccion, simulation=False)
    assert "modelo_de_imagen_desconocido" in {issue.code for issue in plan.issues}
    assert plan.blocked


def test_las_credenciales_se_informan_aparte(media_settings, media_inputs) -> None:
    """`plan` puede ejecutarse sin claves; lo dice en un campo propio."""
    document, manifest = _cargar(media_inputs)
    bible, seleccion = _contexto(media_settings, document)
    media_settings.openai_image_model = "gpt-image-1"
    timeline = build_visual_timeline(manifest, document)
    plan = _plan(media_settings, document, timeline, bible, seleccion, simulation=False)
    assert not plan.blocked
    assert "OPENAI_API_KEY" in plan.missing_credentials
    assert plan.can_run() is False


def test_proporcion_no_exacta_es_informativa(media_settings, media_inputs) -> None:
    """1024x1536 no es 9:16 y se dice, sin bloquear."""
    document, manifest = _cargar(media_inputs)
    bible, seleccion = _contexto(media_settings, document)
    media_settings.openai_image_model = "gpt-image-1"
    media_settings.openai_api_key = SecretStr("sk-de-prueba-no-real")
    timeline = build_visual_timeline(manifest, document)
    plan = _plan(media_settings, document, timeline, bible, seleccion, simulation=False)
    informativos = [issue for issue in plan.issues if issue.code == "proporcion_no_exacta"]
    assert informativos and not informativos[0].blocking
    assert plan.scenes[0].image_size == "1024x1536"


def test_disco_insuficiente_bloquea(media_settings, media_inputs) -> None:
    document, manifest = _cargar(media_inputs)
    bible, seleccion = _contexto(media_settings, document)
    media_settings.min_free_disk_mb = 10**9
    timeline = build_visual_timeline(manifest, document)
    plan = _plan(media_settings, document, timeline, bible, seleccion)
    assert "disco_insuficiente" in {issue.code for issue in plan.issues}
    assert plan.blocked


def test_la_cobertura_visual_sale_de_la_voz(media_settings, media_inputs) -> None:
    document, manifest = _cargar(media_inputs)
    timeline = build_visual_timeline(manifest, document)
    assert timeline.total_samples == manifest.master.sample_count
    cursor = 0
    for escena in timeline.scenes:
        assert escena.start_sample == cursor
        cursor = escena.end_sample
    assert cursor == manifest.master.sample_count
    # La pausa ya esta dentro del intervalo: no se vuelve a sumar.
    voz = {escena.scene_id: escena for escena in manifest.scenes}
    for escena in timeline.scenes:
        fuente = voz[escena.scene_id]
        assert escena.samples == fuente.clip_samples + fuente.pause_samples


def test_un_hueco_en_la_voz_se_detecta(media_settings, media_inputs) -> None:
    document, manifest = _cargar(media_inputs)
    manifest.scenes[1].start_sample += 10
    with pytest.raises(MediaTimelineError, match="Hueco"):
        build_visual_timeline(manifest, document)
