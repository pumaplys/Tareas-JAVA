"""Reutilizacion, reanudacion, presupuestos y limpieza del modulo 4.

Aqui se comprueba lo que pasa ALREDEDOR de la codificacion: que repetir una
clave no vuelva a codificar, que un cambio de solicitud sea conflicto, que un
fallo de etapa corte las siguientes sin publicar un manifiesto a medias, y que
la limpieza no pueda invalidar el paquete entregado.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from viralgen.diskutil import sha256_file
from viralgen.errors import DiskSpaceError, IdempotencyConflictError
from viralgen.render.ffmpeg import ProcessRunner, RenderStageError, probe_capabilities

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


# ---------------------------------------------------------------------------
# Identidad y reutilizacion
# ---------------------------------------------------------------------------


@necesita_ffmpeg
def test_repetir_la_clave_no_vuelve_a_codificar(render_inputs, run_render) -> None:
    """`renders_new=0`. Comprobar con ffprobe NO cuenta como codificacion."""
    script, voice, media = render_inputs
    primero = run_render(script, voice, media, render_key="reuso-001")
    assert primero.manifest_path is not None
    assert primero.renders_new > 0

    segundo = run_render(script, voice, media, render_key="reuso-001")
    assert segundo.reused is True
    assert segundo.renders_new == 0
    assert segundo.output_path == primero.output_path
    assert segundo.render_run_id == primero.render_run_id
    # El archivo entregado es el mismo, byte a byte.
    assert sha256_file(Path(segundo.output_path)) == sha256_file(
        Path(primero.output_path)
    )


@necesita_ffmpeg
def test_cambiar_la_configuracion_es_conflicto_de_clave(
    render_settings, render_inputs, run_render
) -> None:
    """Misma clave y otra solicitud: conflicto, no una salida distinta callada."""
    script, voice, media = render_inputs
    run_render(script, voice, media, render_key="conf-001")
    # Cambiar el CRF cambia el archivo resultante: no puede compartir clave.
    render_settings.render_crf = 28
    with pytest.raises(IdempotencyConflictError, match="otra solicitud"):
        run_render(script, voice, media, render_key="conf-001")


def test_produccion_y_preview_tienen_identidades_distintas(
    render_settings, render_inputs
) -> None:
    """El modo entra en la identidad: una marca PREVIEW cambia el resultado.

    Con fuentes simuladas produccion ni siquiera arranca, asi que la garantia
    se comprueba sobre el plan, que es donde vive la identidad.
    """
    from viralgen.render.pipeline import RenderJobRequest, RenderPipeline

    script, voice, media = render_inputs
    planes = []
    for preview in (True, False):
        planes.append(
            RenderPipeline(
                render_settings,
                RenderJobRequest(
                    script_path=script, voice_path=voice, media_path=media,
                    preview=preview,
                ),
            ).preflight()["plan_sha256"]
        )
    assert planes[0] != planes[1]


@necesita_ffmpeg
def test_un_segmento_preview_no_sirve_para_produccion(
    render_settings, render_inputs, run_render
) -> None:
    """La identidad de etapa incluye el MODO: la marca los hace distintos."""
    from viralgen.render.pipeline import RenderJobRequest, RenderPipeline

    script, voice, media = render_inputs
    preview = RenderPipeline(
        render_settings,
        RenderJobRequest(
            script_path=script, voice_path=voice, media_path=media,
            render_key="ident-preview", preview=True,
        ),
    )
    produccion = RenderPipeline(
        render_settings,
        RenderJobRequest(
            script_path=script, voice_path=voice, media_path=media,
            render_key="ident-prod", preview=False,
        ),
    )
    resultado = preview.run()
    assert resultado.manifest_path is not None

    # Las identidades de etapa del mismo segmento difieren por el modo.
    plan_a = preview.preflight()
    plan_b = produccion.preflight()
    assert plan_a["plan_sha256"] != plan_b["plan_sha256"]


@necesita_ffmpeg
def test_tras_consolidar_los_segmentos_se_limpian_y_la_cache_se_retira(
    render_settings, render_inputs, run_render
) -> None:
    """Un render que TERMINA limpia sus segmentos: son regenerables.

    Y el indice de cache se retira con ellos: una fila que apunta a un archivo
    borrado no aprovecha nada y habria que descubrirla en cada ejecucion.
    """
    from viralgen.storage import Storage
    from viralgen.render.storage import RenderStorage

    script, voice, media = render_inputs
    resultado = run_render(script, voice, media, render_key="etapa-a")
    assert resultado.manifest_path is not None

    directorio = render_settings.effective_data_dir(simulation=True)
    with Storage(directorio) as almacen:
        render_storage = RenderStorage(almacen)
        render_storage.migrate()
        filas = almacen.connect().execute(
            "SELECT path FROM render_artifacts"
        ).fetchall()
    # Ninguna fila sobreviviente apunta a un archivo inexistente.
    for fila in filas:
        assert Path(fila["path"]).is_file()


# ---------------------------------------------------------------------------
# Fallos y trabajos parciales
# ---------------------------------------------------------------------------


class _EjecutorQueFalla(ProcessRunner):
    """Ejecutor que revienta en una etapa concreta."""

    def __init__(self, *args, etapa_rota: str, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.etapa_rota = etapa_rota
        self.etapas: list[str] = []

    def run_checked(self, args, *, stage: str, **kwargs):
        self.etapas.append(stage)
        if self.etapa_rota in stage:
            raise RenderStageError(
                f"fallo inyectado en {stage}", details={"stage": stage}
            )
        return super().run_checked(args, stage=stage, **kwargs)


def _ejecutor_roto(render_settings, tmp_path, etapa: str) -> _EjecutorQueFalla:
    return _EjecutorQueFalla(
        ffmpeg_path=render_settings.ffmpeg_path,
        ffprobe_path=render_settings.ffprobe_path,
        stage_timeout_s=render_settings.render_stage_timeout_s,
        threads=1,
        filter_threads=1,
        log_dir=tmp_path / "logs_rotos",
        etapa_rota=etapa,
    )


@necesita_ffmpeg
def test_un_fallo_de_segmento_corta_las_etapas_siguientes(
    render_settings, render_inputs, run_render, tmp_path
) -> None:
    """Sin manifiesto y sin archivo de salida: nada a medias."""
    script, voice, media = render_inputs
    ejecutor = _ejecutor_roto(render_settings, tmp_path, "segment_sc_02")
    resultado = run_render(
        script, voice, media, render_key="roto-seg", runner=ejecutor
    )
    assert resultado.status == "failed"
    assert resultado.partial is True
    assert resultado.manifest_path is None
    assert resultado.output_path is None
    assert resultado.error_code == "render_stage_error"
    # No se llego a mezclar ni a multiplexar.
    assert not any(etapa == "mux" for etapa in ejecutor.etapas)
    assert not any(etapa == "audio_mix" for etapa in ejecutor.etapas)


@necesita_ffmpeg
def test_un_fallo_de_muxing_no_obliga_a_recodificar_todo(
    render_settings, render_inputs, run_render, tmp_path
) -> None:
    """Los segmentos validados sobreviven al fallo y se reutilizan."""
    script, voice, media = render_inputs
    roto = _ejecutor_roto(render_settings, tmp_path, "mux")
    fallido = run_render(script, voice, media, render_key="roto-mux", runner=roto)
    assert fallido.status == "failed"
    assert fallido.manifest_path is None
    segmentos_codificados = sum(
        1 for etapa in roto.etapas if etapa.startswith("segment_")
    )
    assert segmentos_codificados > 0

    # Al reintentar con otra clave, los segmentos ya estan: no se recodifican.
    segundo = run_render(script, voice, media, render_key="tras-mux")
    assert segundo.manifest_path is not None
    assert segundo.resources["segment_cache_hits"] == segmentos_codificados


@necesita_ffmpeg
def test_los_intentos_por_etapa_estan_acotados_y_persisten(
    render_settings, render_inputs, tmp_path
) -> None:
    """Agotar los intentos exige resolucion explicita, no otro reintento."""
    from viralgen.render.pipeline import RenderJobRequest, RenderPipeline

    script, voice, media = render_inputs
    render_settings.render_max_attempts_per_stage = 1

    peticion = RenderJobRequest(
        script_path=script, voice_path=voice, media_path=media,
        render_key="intentos", preview=True,
    )
    primero = RenderPipeline(
        render_settings, peticion,
        runner=_ejecutor_roto(render_settings, tmp_path, "segment_sc_01"),
    ).run()
    assert primero.status == "failed"

    # Segunda invocacion: el intento ya gastado esta PERSISTIDO.
    segundo = RenderPipeline(
        render_settings, peticion,
        runner=_ejecutor_roto(render_settings, tmp_path, "nada_que_romper"),
    ).run()
    assert segundo.status == "failed"
    assert "agoto sus" in (segundo.error_message or "")


# ---------------------------------------------------------------------------
# Disco
# ---------------------------------------------------------------------------


def test_el_preflight_avisa_de_disco_insuficiente(
    render_settings, render_inputs
) -> None:
    from viralgen.render.pipeline import RenderJobRequest, RenderPipeline

    script, voice, media = render_inputs
    render_settings.min_free_disk_mb = 10_000_000  # imposible de satisfacer
    plan = RenderPipeline(
        render_settings,
        RenderJobRequest(
            script_path=script, voice_path=voice, media_path=media, preview=True
        ),
    ).preflight()
    assert plan["blocked"] is True
    assert any(
        issue["code"] == "disco_insuficiente" for issue in plan["issues"]
    )


def test_el_preflight_avisa_si_el_trabajo_no_cabe_en_su_presupuesto(
    render_settings, render_inputs
) -> None:
    from viralgen.render.pipeline import RenderJobRequest, RenderPipeline

    script, voice, media = render_inputs
    render_settings.render_max_work_mib = 16
    plan = RenderPipeline(
        render_settings,
        RenderJobRequest(
            script_path=script, voice_path=voice, media_path=media, preview=True
        ),
    ).preflight()
    assert plan["blocked"] is True
    assert any(
        issue["code"] == "presupuesto_de_trabajo_excedido" for issue in plan["issues"]
    )


def test_el_presupuesto_de_trabajo_se_vigila_durante_la_ejecucion(
    render_settings, render_inputs, tmp_path
) -> None:
    """Se comprueba tras cada segmento, no solo antes de empezar.

    El preflight estima por arriba; esta guardia mira lo que hay ESCRITO, que
    es lo unico que puede llenar el disco de verdad.
    """
    from viralgen.render.pipeline import RenderJobRequest, RenderPipeline

    script, voice, media = render_inputs
    pipeline = RenderPipeline(
        render_settings,
        RenderJobRequest(
            script_path=script, voice_path=voice, media_path=media, preview=True
        ),
    )
    trabajo = tmp_path / "work"
    trabajo.mkdir()
    (trabajo / "segmento.bin").write_bytes(b"x" * (3 * 1024 * 1024))

    render_settings.render_max_work_mib = 1
    with pytest.raises(DiskSpaceError, match="area de trabajo"):
        pipeline._check_work_budget(trabajo)

    render_settings.render_max_work_mib = 64
    pipeline._check_work_budget(trabajo)  # ahora cabe: no levanta nada


# ---------------------------------------------------------------------------
# Inmutabilidad y limpieza
# ---------------------------------------------------------------------------


@necesita_ffmpeg
def test_el_montaje_no_toca_ni_un_byte_de_sus_fuentes(
    render_inputs, run_render
) -> None:
    script, voice, media = render_inputs
    fuentes = [script, voice, media]
    # Tambien los archivos a los que apuntan.
    fuentes.append(voice.parent / "audio" / "narration.wav")
    medios = json.loads(media.read_text(encoding="utf-8"))
    for asset in medios["assets"]:
        fuentes.append(media.parent / asset["path"])

    antes = {ruta: sha256_file(ruta) for ruta in fuentes if ruta.is_file()}
    resultado = run_render(script, voice, media, render_key="inmutable")
    assert resultado.manifest_path is not None
    despues = {ruta: sha256_file(ruta) for ruta in fuentes if ruta.is_file()}
    assert antes == despues


@necesita_ffmpeg
def test_la_limpieza_no_invalida_el_paquete(render_inputs, run_render) -> None:
    """Los intermedios se borran; el MP4, el ASS y los fotogramas se quedan."""
    script, voice, media = render_inputs
    resultado = run_render(script, voice, media, render_key="limpieza")
    assert resultado.manifest_path is not None
    directorio = Path(resultado.manifest_path).parent

    # El area de trabajo regenerable ya no esta.
    assert not (directorio / "work").exists()
    # Y el paquete entregado si. En preview el archivo se llama preview.mp4:
    # el nombre dice lo que es y no se sube por descuido.
    assert (directorio / "preview.mp4").is_file()
    assert not (directorio / "video.mp4").exists()
    assert (directorio / "captions.ass").is_file()
    assert (directorio / "render.json").is_file()

    manifiesto = json.loads((directorio / "render.json").read_text(encoding="utf-8"))
    # Los fotogramas se comprueban por el MANIFIESTO, no por su extension: el
    # contrato dice donde estan, y suponer ".png" ataria la prueba a un
    # detalle de formato que el modulo puede cambiar.
    assert manifiesto["inspection"]["frames"]
    for muestra in manifiesto["inspection"]["frames"]:
        assert (directorio / muestra["path"]).is_file()
    # Los hashes de los intermedios borrados se conservan, pero NO se declaran
    # archivos obligatorios del paquete.
    assert manifiesto["processing"]["segments"]
    for segmento in manifiesto["processing"]["segments"]:
        assert segmento["sha256"]
        assert segmento["retained"] is False


@necesita_ffmpeg
def test_la_limpieza_no_toca_otros_trabajos(render_inputs, run_render) -> None:
    script, voice, media = render_inputs
    primero = run_render(script, voice, media, render_key="vecino-a")
    segundo = run_render(script, voice, media, render_key="vecino-b")
    assert primero.manifest_path and segundo.manifest_path
    # El primero sigue intacto tras montar el segundo.
    assert Path(primero.output_path).is_file()
    assert Path(primero.manifest_path).is_file()


@necesita_ffmpeg
def test_el_paquete_se_revalida_tras_la_limpieza(
    render_settings, render_inputs, run_render
) -> None:
    """`validate` y la reutilizacion funcionan con los intermedios borrados."""
    from viralgen.render.admission import check_render_admission

    script, voice, media = render_inputs
    resultado = run_render(script, voice, media, render_key="tras-limpieza")
    assert resultado.manifest_path is not None

    informe = check_render_admission(
        script_path=script, voice_path=voice, media_path=media,
        manifest_path=Path(resultado.manifest_path), settings=render_settings,
    )
    assert informe.contract_valid is True
    assert informe.admissible_for_preview is True

    repetido = run_render(script, voice, media, render_key="tras-limpieza")
    assert repetido.reused is True
    assert repetido.renders_new == 0


@necesita_ffmpeg
def test_un_video_borrado_se_rehace_en_vez_de_darse_por_bueno(
    render_inputs, run_render
) -> None:
    script, voice, media = render_inputs
    primero = run_render(script, voice, media, render_key="recupera")
    Path(primero.output_path).unlink()

    segundo = run_render(script, voice, media, render_key="recupera")
    assert segundo.reused is False
    assert Path(segundo.output_path).is_file()
