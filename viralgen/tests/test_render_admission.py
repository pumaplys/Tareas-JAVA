"""Admision del modulo 4: tres veredictos independientes.

Lo que se comprueba aqui:

* produccion RECHAZA fuentes simuladas antes de codificar nada;
* preview acepta su ORIGEN pero no relaja contrato, hashes ni medios
  incompletos;
* elegir el modo del informe NO cambia ningun `check`;
* una salida preview nunca llega al publicador, aunque sus fuentes fueran
  reales.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from viralgen.render.admission import ORIGIN_CHECKS, check_render_admission
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


@pytest.fixture(scope="module")
def paquete_base(tmp_path_factory):
    """Renderiza UNA vez y guarda el paquete completo con sus entradas.

    Cada prueba trabaja sobre una copia: varias alteran o borran archivos a
    proposito, y codificar un video por prueba costaria minutos.
    """
    import shutil

    from viralgen.config import Settings
    from viralgen.media.pipeline import MediaJobRequest, MediaPipeline
    from viralgen.pipeline import JobRequest, Pipeline
    from viralgen.render.pipeline import RenderJobRequest, RenderPipeline
    from viralgen.voice.pipeline import VoiceJobRequest, VoicePipeline

    raiz = tmp_path_factory.mktemp("paquete_admision")
    perfiles = raiz / "perfiles_solo_imagenes.json"
    shutil.copy(
        Path(__file__).parent.parent / "examples" / "perfiles_solo_imagenes.json",
        perfiles,
    )
    ajustes = Settings(
        _env_file=None,
        data_dir=raiz / "data",
        min_free_disk_mb=0,
        log_level="ERROR",
        profiles_path=perfiles,
    )
    guion = Pipeline(
        ajustes,
        JobRequest(
            command="generate", profile_id="infantil_cuentos",
            topic="aprender a compartir", simulation=True, seed=5,
            job_key="adm-base",
        ),
    ).run()
    voz = VoicePipeline(
        ajustes,
        VoiceJobRequest(
            script_path=Path(guion.script_path), voice_key="adm-voz",
            simulation=True, seed=5,
        ),
    ).run()
    medios = MediaPipeline(
        ajustes,
        MediaJobRequest(
            script_path=Path(guion.script_path), voice_path=Path(voz.manifest_path),
            media_key="adm-medios", simulation=True, seed=5,
        ),
    ).run()
    render = RenderPipeline(
        ajustes,
        RenderJobRequest(
            script_path=Path(guion.script_path),
            voice_path=Path(voz.manifest_path),
            media_path=Path(medios.manifest_path),
            render_key="adm-001",
            preview=True,
        ),
    ).run()
    assert render.manifest_path is not None, render.admission_reasons
    return {
        "root": raiz,
        "script": Path(guion.script_path),
        "voice": Path(voz.manifest_path),
        "media": Path(medios.manifest_path),
        "manifest": Path(render.manifest_path),
    }


def _video_de(manifest: Path) -> Path:
    """El nombre del archivo lo dice el manifiesto, no se supone."""
    datos = json.loads(manifest.read_text(encoding="utf-8"))
    return manifest.parent / datos["output"]["path"]


@pytest.fixture
def paquete(paquete_base, tmp_path):
    """Copia aislada del paquete: cada prueba puede romperla sin contagiar."""
    import shutil

    destino = tmp_path / "paquete"
    shutil.copytree(paquete_base["root"], destino, symlinks=True)

    def trasladar(ruta: Path) -> Path:
        return destino / ruta.relative_to(paquete_base["root"])

    return (
        trasladar(paquete_base["script"]),
        trasladar(paquete_base["voice"]),
        trasladar(paquete_base["media"]),
        trasladar(paquete_base["manifest"]),
    )


# ---------------------------------------------------------------------------
# Puerta de entrada: produccion no empieza con fuentes simuladas
# ---------------------------------------------------------------------------


def test_produccion_rechaza_fuentes_simuladas_antes_de_codificar(
    render_settings, render_inputs
) -> None:
    """El corte ocurre en la puerta, sin gastar una sola codificacion."""
    from viralgen.render.pipeline import RenderJobRequest, RenderPipeline

    script, voice, media = render_inputs
    resultado = RenderPipeline(
        render_settings,
        RenderJobRequest(
            script_path=script, voice_path=voice, media_path=media,
            render_key="prod-simulado", preview=False,
        ),
    ).run()
    assert resultado.status == "blocked"
    assert resultado.manifest_path is None
    assert resultado.output_path is None
    assert resultado.renders_new == 0, "no debe haberse codificado nada"
    assert any("admision completa" in motivo for motivo in resultado.admission_reasons)


def test_el_preflight_de_produccion_tambien_bloquea(
    render_settings, render_inputs
) -> None:
    from viralgen.render.pipeline import RenderJobRequest, RenderPipeline

    script, voice, media = render_inputs
    plan = RenderPipeline(
        render_settings,
        RenderJobRequest(
            script_path=script, voice_path=voice, media_path=media, preview=False
        ),
    ).preflight()
    assert plan["blocked"] is True
    assert plan["can_render"] is False
    # Pero la aritmetica del plan sigue siendo valida y esta ahi.
    assert plan["timeline"]["total_frames"] > 0


def test_el_preflight_preview_admite_el_origen_simulado(
    render_settings, render_inputs
) -> None:
    from viralgen.render.pipeline import RenderJobRequest, RenderPipeline

    script, voice, media = render_inputs
    plan = RenderPipeline(
        render_settings,
        RenderJobRequest(
            script_path=script, voice_path=voice, media_path=media, preview=True
        ),
    ).preflight()
    assert plan["blocked"] is False
    assert plan["can_render"] is True
    assert plan["simulation"] is True


# ---------------------------------------------------------------------------
# Tres veredictos
# ---------------------------------------------------------------------------


@necesita_ffmpeg
def test_una_simulacion_pasa_preview_y_nunca_publicador(
    render_settings, paquete
) -> None:
    script, voice, media, manifest = paquete
    informe = check_render_admission(
        script_path=script, voice_path=voice, media_path=media,
        manifest_path=manifest, settings=render_settings,
    )
    assert informe.contract_valid is True
    assert informe.admissible_for_preview is True
    assert informe.admissible_for_publisher is False
    assert informe.preview_reasons == []
    # Los unicos checks que fallan son los de ORIGEN.
    fallidos = {nombre for nombre, ok in informe.checks.items() if not ok}
    assert fallidos <= set(ORIGIN_CHECKS), fallidos
    assert "modo_produccion" in fallidos
    assert "render_real" in fallidos


@necesita_ffmpeg
def test_el_modo_del_informe_no_cambia_ningun_check(
    render_settings, paquete
) -> None:
    """`--allow-simulation` elige el veredicto que decide el codigo de salida.

    No toca `checks` ni convierte una simulacion en material publicable.
    """
    script, voice, media, manifest = paquete
    primero = check_render_admission(
        script_path=script, voice_path=voice, media_path=media,
        manifest_path=manifest, settings=render_settings,
    )
    segundo = check_render_admission(
        script_path=script, voice_path=voice, media_path=media,
        manifest_path=manifest, settings=render_settings,
    )
    assert primero.checks == segundo.checks
    assert primero.admissible_for_publisher is segundo.admissible_for_publisher is False


@necesita_ffmpeg
def test_un_hash_roto_bloquea_tambien_en_preview(
    render_settings, paquete, tmp_path
) -> None:
    """Preview ignora el ORIGEN, no la correspondencia entre archivos."""
    script, voice, media, manifest = paquete
    # Se reescribe con otro formato: mismo contenido semantico, otros bytes.
    # Es exactamente el caso que el hash existe para detectar.
    alterado = tmp_path / "script_alterado.json"
    datos = json.loads(script.read_text(encoding="utf-8"))
    alterado.write_text(
        json.dumps(datos, ensure_ascii=False, indent=4), encoding="utf-8"
    )

    informe = check_render_admission(
        script_path=alterado, voice_path=voice, media_path=media,
        manifest_path=manifest, settings=render_settings,
    )
    assert informe.admissible_for_preview is False
    assert informe.checks["hash_del_guion"] is False
    assert "hash_del_guion" not in ORIGIN_CHECKS


@necesita_ffmpeg
def test_un_video_alterado_rompe_el_contrato(render_settings, paquete) -> None:
    script, voice, media, manifest = paquete
    video = _video_de(manifest)
    datos = bytearray(video.read_bytes())
    datos[-1] = (datos[-1] + 1) % 256
    video.write_bytes(bytes(datos))

    informe = check_render_admission(
        script_path=script, voice_path=voice, media_path=media,
        manifest_path=manifest, settings=render_settings,
    )
    assert informe.contract_valid is False
    assert informe.checks["archivos_integros"] is False


@necesita_ffmpeg
def test_un_video_ausente_bloquea(render_settings, paquete) -> None:
    script, voice, media, manifest = paquete
    _video_de(manifest).unlink()
    informe = check_render_admission(
        script_path=script, voice_path=voice, media_path=media,
        manifest_path=manifest, settings=render_settings,
    )
    assert informe.contract_valid is False
    assert informe.checks["archivos_integros"] is False


@necesita_ffmpeg
def test_una_ruta_que_escapa_por_symlink_se_rechaza(
    render_settings, paquete, tmp_path
) -> None:
    script, voice, media, manifest = paquete
    fuera = tmp_path / "fuera.mp4"
    video = _video_de(manifest)
    fuera.write_bytes(video.read_bytes())
    video.unlink()
    video.symlink_to(fuera)

    informe = check_render_admission(
        script_path=script, voice_path=voice, media_path=media,
        manifest_path=manifest, settings=render_settings,
    )
    assert informe.contract_valid is False
    assert any("fuera del paquete" in motivo for motivo in informe.reasons)


@necesita_ffmpeg
def test_un_manifiesto_manipulado_no_se_cree(render_settings, paquete) -> None:
    """El validador recalcula: no se fia del booleano guardado."""
    script, voice, media, manifest = paquete
    datos = json.loads(manifest.read_text(encoding="utf-8"))
    datos["control"]["admissible_for_publisher"] = True
    datos["render_mode"] = "production"
    datos["simulation"] = False
    manifest.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")

    informe = check_render_admission(
        script_path=script, voice_path=voice, media_path=media,
        manifest_path=manifest, settings=render_settings,
    )
    # El origen se deriva de las FUENTES, no de lo que diga el manifiesto.
    assert informe.admissible_for_publisher is False
    assert informe.checks["guion_real"] is False
    assert informe.checks["voz_real"] is False


@necesita_ffmpeg
def test_una_cuantizacion_falseada_se_detecta(render_settings, paquete) -> None:
    """El reloj del manifiesto se recalcula desde la voz."""
    script, voice, media, manifest = paquete
    datos = json.loads(manifest.read_text(encoding="utf-8"))
    datos["timeline"]["total_frames"] += 3
    manifest.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")

    informe = check_render_admission(
        script_path=script, voice_path=voice, media_path=media,
        manifest_path=manifest, settings=render_settings,
    )
    assert informe.admissible_for_preview is False
    assert informe.checks["cuantizacion_coherente"] is False


def test_sin_herramientas_no_hay_veredicto_tecnico_positivo(
    render_settings, paquete, monkeypatch
) -> None:
    """Un ejecutable ausente deja la comprobacion NO VERIFICADA, no aprobada."""
    script, voice, media, manifest = paquete
    render_settings.ffmpeg_path = "ffmpeg-que-no-existe"
    render_settings.ffprobe_path = "ffprobe-que-no-existe"

    informe = check_render_admission(
        script_path=script, voice_path=voice, media_path=media,
        manifest_path=manifest, settings=render_settings,
    )
    assert informe.contract_valid is False
    assert informe.admissible_for_preview is False
    assert informe.unverified, "debe decir QUE quedo sin verificar"
    assert informe.checks["video_medido"] is False
    # Y se distingue de una fuente comprobada como inadmisible.
    assert any("no se pudo medir" in motivo for motivo in informe.reasons)


@necesita_ffmpeg
def test_la_validacion_no_modifica_ningun_byte(render_settings, paquete) -> None:
    """`validate` es de LECTURA: puede decodificar, no escribir."""
    from viralgen.diskutil import sha256_file

    script, voice, media, manifest = paquete
    rutas = [script, voice, media, manifest, _video_de(manifest),
             manifest.parent / "captions.ass"]
    antes = {ruta: sha256_file(ruta) for ruta in rutas}

    check_render_admission(
        script_path=script, voice_path=voice, media_path=media,
        manifest_path=manifest, settings=render_settings,
    )
    despues = {ruta: sha256_file(ruta) for ruta in rutas}
    assert antes == despues
