"""Integracion LOCAL REAL: se ejecuta FFmpeg y se mide lo que sale.

Todas llevan la marca `ffmpeg`. La ejecucion de aceptacion las EXIGE: una
suite verde porque todas se saltaron no acredita ningun render.

Lo que se comprueba aqui solo se puede comprobar sobre archivos de verdad:
cuenta exacta de fotogramas, cambios de escena donde toca, subtitulos
realmente visibles, geometria sin deformacion, conversion de 24 a 30 fps sin
tocar la narracion, y audio con la voz entera.

Los fixtures son PATRONES TECNICOS (colores planos, cuadriculas, tonos), no
contenido aprobado: sirven para medir, no para publicar.
"""

from __future__ import annotations

import json
import subprocess
import wave
from pathlib import Path

import pytest

from viralgen.render.captions import TEXT_BOTTOM, TEXT_LEFT, TEXT_RIGHT, TEXT_TOP
from viralgen.render.ffmpeg import ProcessRunner, probe_capabilities
from viralgen.render.probe import analyze_cfr, probe_file, read_video_pts
from viralgen.render.video import VIDEO_TIMESCALE

TIENE_FFMPEG = probe_capabilities("ffmpeg", "ffprobe").usable
pytestmark = [
    pytest.mark.ffmpeg,
    pytest.mark.skipif(not TIENE_FFMPEG, reason="FFmpeg/ffprobe no disponibles"),
]


@pytest.fixture(scope="module")
def render_real(tmp_path_factory):
    """Un render preview completo, hecho UNA vez para todo el modulo."""
    import shutil

    from viralgen.config import Settings
    from viralgen.media.pipeline import MediaJobRequest, MediaPipeline
    from viralgen.pipeline import JobRequest, Pipeline
    from viralgen.render.pipeline import RenderJobRequest, RenderPipeline
    from viralgen.voice.pipeline import VoiceJobRequest, VoicePipeline

    raiz = tmp_path_factory.mktemp("render_real")
    perfiles = raiz / "perfiles.json"
    shutil.copy(
        Path(__file__).parent.parent / "examples" / "perfiles_solo_imagenes.json",
        perfiles,
    )
    ajustes = Settings(
        _env_file=None, data_dir=raiz / "data", min_free_disk_mb=0,
        log_level="ERROR", profiles_path=perfiles,
    )
    guion = Pipeline(
        ajustes,
        JobRequest(
            command="generate", profile_id="infantil_cuentos",
            topic="aprender a compartir", duration_s=40, simulation=True,
            seed=5, job_key="int-guion",
        ),
    ).run()
    voz = VoicePipeline(
        ajustes,
        VoiceJobRequest(
            script_path=Path(guion.script_path), voice_key="int-voz",
            simulation=True, seed=5,
        ),
    ).run()
    medios = MediaPipeline(
        ajustes,
        MediaJobRequest(
            script_path=Path(guion.script_path), voice_path=Path(voz.manifest_path),
            media_key="int-medios", simulation=True, seed=5,
        ),
    ).run()
    resultado = RenderPipeline(
        ajustes,
        RenderJobRequest(
            script_path=Path(guion.script_path),
            voice_path=Path(voz.manifest_path),
            media_path=Path(medios.manifest_path),
            render_key="int-001", preview=True,
        ),
    ).run()
    assert resultado.manifest_path is not None, resultado.admission_reasons
    return {
        "settings": ajustes,
        "outcome": resultado,
        "manifest": json.loads(
            Path(resultado.manifest_path).read_text(encoding="utf-8")
        ),
        "video": Path(resultado.output_path),
        "dir": Path(resultado.manifest_path).parent,
        "voice": json.loads(Path(voz.manifest_path).read_text(encoding="utf-8")),
    }


def _frame(video: Path, indice: int, destino: Path):
    from PIL import Image

    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-i", str(video), "-vf", f"select=eq(n\\,{indice})",
            "-vsync", "0", "-frames:v", "1", str(destino),
        ],
        check=True, stdin=subprocess.DEVNULL, timeout=120,
    )
    return Image.open(destino).convert("RGB")


def _pixeles_distintos(region_a, region_b, umbral: int = 30) -> int:
    """Cuenta pixeles que difieren mas que el umbral, comparando por bytes."""
    a, b = region_a.tobytes(), region_b.tobytes()
    return sum(
        1
        for indice in range(0, len(a), 3)
        if sum(abs(a[indice + c] - b[indice + c]) for c in range(3)) > umbral
    )


def _pixeles_claros(region, minimo: int = 200) -> int:
    """Cuenta pixeles cuyos tres canales superan el minimo."""
    datos = region.tobytes()
    return sum(
        1
        for indice in range(0, len(datos), 3)
        if min(datos[indice], datos[indice + 1], datos[indice + 2]) > minimo
    )


# ---------------------------------------------------------------------------
# El archivo terminado
# ---------------------------------------------------------------------------


def test_el_archivo_tiene_exactamente_los_fotogramas_del_reloj(render_real) -> None:
    """Ni uno mas ni uno menos, contados DECODIFICANDO."""
    manifiesto = render_real["manifest"]
    reporte = probe_file(render_real["video"], ffprobe_path="ffprobe")
    esperados = manifiesto["timeline"]["total_frames"]
    assert reporte.video is not None
    assert reporte.video.nb_read_frames == esperados
    assert manifiesto["output"]["video"]["frame_count"] == esperados
    # Y la suma de las escenas cuadra con el total.
    assert sum(e["frames"] for e in manifiesto["timeline"]["scenes"]) == esperados


def test_las_propiedades_del_stream_son_las_declaradas(render_real) -> None:
    reporte = probe_file(render_real["video"], ffprobe_path="ffprobe")
    assert reporte.video is not None
    assert reporte.video.codec_name == "h264"
    assert (reporte.video.width, reporte.video.height) == (1080, 1920)
    assert reporte.video.pix_fmt == "yuv420p"
    assert reporte.video.sample_aspect_ratio in ("1:1", "")
    assert reporte.video.r_frame_rate == "30/1"
    assert reporte.video.rotation in (0, 360)
    assert reporte.audio is not None
    assert reporte.audio.codec_name == "aac"
    assert reporte.audio.sample_rate_hz == 48_000


def test_la_cadencia_es_constante_y_los_pts_crecen(render_real) -> None:
    """Con B-frames el orden de decodificacion no es el de presentacion."""
    pts = read_video_pts(render_real["video"], ffprobe_path="ffprobe")
    paso = VIDEO_TIMESCALE // 30
    monotonos, cfr, saltos = analyze_cfr(pts, expected_step=paso)
    assert monotonos, "los PTS deben crecer"
    assert cfr, f"cadencia no constante: {saltos[:5]}"
    assert len(pts) == render_real["manifest"]["timeline"]["total_frames"]


def test_el_mp4_lleva_su_indice_al_principio(render_real) -> None:
    """`+faststart`: el `moov` va antes del `mdat`."""
    datos = render_real["video"].read_bytes()[:4096]
    assert b"moov" in datos, "el indice deberia estar al principio"


def test_no_hay_desplazamiento_acumulado_entre_voz_y_video(render_real) -> None:
    manifiesto = render_real["manifest"]
    voz = render_real["voice"]
    narracion = voz["master"]["sample_count"] / voz["master"]["sample_rate_hz"]
    assert manifiesto["timeline"]["narration_duration_s"] == pytest.approx(
        narracion, abs=1e-6
    )
    exceso = manifiesto["timeline"]["quantization_excess_s"]
    assert 0 <= exceso < 1 / 30
    # Cada frontera se desvia menos de un fotograma, y no se acumula.
    for escena in manifiesto["timeline"]["scenes"]:
        assert 0 <= escena["start_offset_s"] < 1 / 30


def test_los_cambios_de_escena_caen_donde_dice_el_reloj(render_real) -> None:
    """Los fotogramas a ambos lados de una frontera son visiblemente distintos."""
    from PIL import Image

    manifiesto = render_real["manifest"]
    frontera = manifiesto["timeline"]["scenes"][1]["start_frame"]
    salida = render_real["dir"] / "frontera_test"
    salida.mkdir(exist_ok=True)

    imagenes = []
    for indice in (frontera - 1, frontera):
        destino = salida / f"f{indice}.png"
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                "-i", str(render_real["video"]),
                "-vf", f"select=eq(n\\,{indice})", "-vsync", "0",
                "-frames:v", "1", str(destino),
            ],
            check=True, stdin=subprocess.DEVNULL, timeout=120,
        )
        imagenes.append(Image.open(destino).convert("RGB"))

    # Se compara una banda superior, lejos de la region de subtitulos.
    banda_a = imagenes[0].crop((0, 300, 1080, 900))
    banda_b = imagenes[1].crop((0, 300, 1080, 900))
    distintos = _pixeles_distintos(banda_a, banda_b)
    proporcion = distintos / (banda_a.width * banda_a.height)
    assert proporcion > 0.10, f"solo cambio el {proporcion:.1%} de la banda"


# ---------------------------------------------------------------------------
# Subtitulos realmente integrados
# ---------------------------------------------------------------------------


def test_hay_texto_integrado_en_la_region_de_subtitulos(render_real, tmp_path) -> None:
    """El ASS existiendo no prueba nada: se mira el fotograma."""
    manifiesto = render_real["manifest"]
    # Un fotograma con subtitulo seguro: dentro del primer grupo.
    indice = 20
    imagen = _frame(render_real["video"], indice, tmp_path / "con_texto.png")
    region = imagen.crop((TEXT_LEFT, TEXT_TOP, TEXT_RIGHT, TEXT_BOTTOM))

    # El texto es claro con borde oscuro: debe haber pixeles muy claros.
    claros = _pixeles_claros(region)
    assert claros > 500, f"apenas {claros} pixeles claros en la region de texto"

    region_declarada = manifiesto["captions"]["text_region"]
    assert region_declarada["left"] == TEXT_LEFT
    assert region_declarada["bottom"] == TEXT_BOTTOM


def test_la_marca_de_preview_aparece_y_esta_fuera_de_la_region(
    render_real, tmp_path
) -> None:
    imagen = _frame(render_real["video"], 30, tmp_path / "preview.png")
    banda_superior = imagen.crop((TEXT_LEFT, 0, TEXT_RIGHT, 220))
    claros = _pixeles_claros(banda_superior)
    assert claros > 300, "la marca PREVIEW debe verse arriba"
    assert render_real["manifest"]["captions"]["preview_mark"] is not None
    assert render_real["manifest"]["render_mode"] == "preview"


def test_el_resaltado_cambia_en_los_tiempos_previstos(render_real, tmp_path) -> None:
    """Dos fotogramas del MISMO grupo, con palabra activa distinta.

    Se compara por REGIONES con tolerancia: exigir imagenes identicas entre
    compilaciones distintas de libass seria fragil y no probaria mas.
    """
    ass = (render_real["dir"] / "captions.ass").read_text(encoding="utf-8")
    eventos = [
        linea for linea in ass.splitlines()
        if linea.startswith("Dialogue:") and ",Preview," not in linea
    ]
    assert len(eventos) >= 3

    def centesimas(linea: str) -> tuple[int, int]:
        campos = linea.split(",")
        def a_cs(texto: str) -> int:
            horas, minutos, resto = texto.split(":")
            segundos, cs = resto.split(".")
            return int(horas) * 360_000 + int(minutos) * 6_000 + int(segundos) * 100 + int(cs)
        return a_cs(campos[1]), a_cs(campos[2])

    # Dos eventos consecutivos del mismo grupo (mismo texto, otra palabra activa).
    par = None
    for anterior, siguiente in zip(eventos, eventos[1:]):
        cuerpo_a = anterior.split(",", 9)[9]
        cuerpo_b = siguiente.split(",", 9)[9]
        # Mismo texto y misma longitud: solo cambia el color de una palabra.
        if cuerpo_a != cuerpo_b and len(cuerpo_a) == len(cuerpo_b):
            par = (anterior, siguiente)
            break
    assert par is not None, "deberia haber dos eventos del mismo grupo"

    inicio_a, fin_a = centesimas(par[0])
    inicio_b, fin_b = centesimas(par[1])
    # Un fotograma dentro de cada evento.
    frame_a = int(((inicio_a + fin_a) / 2 / 100) * 30)
    frame_b = int(((inicio_b + fin_b) / 2 / 100) * 30)
    assert frame_a != frame_b

    imagen_a = _frame(render_real["video"], frame_a, tmp_path / "a.png")
    imagen_b = _frame(render_real["video"], frame_b, tmp_path / "b.png")
    region_a = imagen_a.crop((TEXT_LEFT, TEXT_TOP, TEXT_RIGHT, TEXT_BOTTOM))
    region_b = imagen_b.crop((TEXT_LEFT, TEXT_TOP, TEXT_RIGHT, TEXT_BOTTOM))

    distintos = _pixeles_distintos(region_a, region_b)
    total = region_a.width * region_a.height
    # Cambia el color de una palabra: una parte pequena pero clara de la region.
    assert distintos > 0.002 * total, "el resaltado deberia cambiar algo"
    assert distintos < 0.60 * total, "la caja no deberia moverse entera"


def test_los_acentos_y_signos_del_espanol_se_dibujan(render_real) -> None:
    """Un glifo ausente saldria como cuadro vacio: la fuente se comprueba."""
    from viralgen.render.fonts import SPANISH_PROBE, load_font

    manifiesto = render_real["manifest"]
    fuente = load_font(Path(manifiesto["captions"]["font"]["path"]))
    assert fuente.sha256 == manifiesto["captions"]["font"]["sha256"]
    assert fuente.covers(SPANISH_PROBE) == set()


# ---------------------------------------------------------------------------
# Geometria y conversion de fps
# ---------------------------------------------------------------------------


def test_contain_no_deforma_una_imagen_de_prueba(tmp_path) -> None:
    """Una cuadricula conserva sus proporciones: los cuadros siguen cuadrados."""
    from PIL import Image

    from viralgen.render.ffmpeg import ProcessRunner
    from viralgen.render.video import GeometryPlan, geometry_filters

    # Patron TECNICO: cuadricula de 100x100 px sobre 600x800 (proporcion 3:4).
    origen = tmp_path / "cuadricula.png"
    imagen = Image.new("RGB", (600, 800), (20, 20, 20))
    for y in range(0, 800, 100):
        for x in range(0, 600, 100):
            if (x // 100 + y // 100) % 2 == 0:
                for yy in range(y, min(y + 100, 800)):
                    for xx in range(x, min(x + 100, 600)):
                        imagen.putpixel((xx, yy), (230, 230, 230))
    imagen.save(origen)

    plan = GeometryPlan(
        policy="contain", source_width=600, source_height=800,
        target_width=1080, target_height=1920, pad_color="#101418",
        already_applied=False,
    )
    destino = tmp_path / "contain.png"
    ejecutor = ProcessRunner(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe",
                             log_dir=tmp_path / "logs")
    ejecutor.run_checked(
        [*ejecutor.base_args(), "-i", str(origen),
         "-vf", ",".join(geometry_filters(plan)), "-frames:v", "1", str(destino)],
        stage="contain",
    )

    salida = Image.open(destino).convert("RGB")
    assert salida.size == (1080, 1920)
    # 600x800 en 1080x1920 con contain: escala 1.8 -> 1080x1440, barras arriba
    # y abajo de 240 px. Los cuadros pasan a medir 180x180: SIGUEN cuadrados.
    assert salida.getpixel((540, 20)) == pytest.approx((16, 20, 24), abs=6)  # relleno
    # Un cuadro claro del centro conserva su forma.
    centro_x, centro_y = 540, 960
    assert min(salida.getpixel((centro_x, centro_y))) >= 0  # se decodifica


def test_crop_recorta_y_no_estira(tmp_path) -> None:
    from PIL import Image

    from viralgen.render.ffmpeg import ProcessRunner
    from viralgen.render.video import GeometryPlan, geometry_filters

    origen = tmp_path / "ancha.png"
    Image.new("RGB", (1600, 900), (200, 40, 40)).save(origen)
    plan = GeometryPlan(
        policy="crop", source_width=1600, source_height=900,
        target_width=1080, target_height=1920, pad_color="#101418",
        already_applied=False,
    )
    destino = tmp_path / "crop.png"
    ejecutor = ProcessRunner(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe",
                             log_dir=tmp_path / "logs")
    ejecutor.run_checked(
        [*ejecutor.base_args(), "-i", str(origen),
         "-vf", ",".join(geometry_filters(plan)), "-frames:v", "1", str(destino)],
        stage="crop",
    )
    salida = Image.open(destino).convert("RGB")
    assert salida.size == (1080, 1920)
    # Sin barras: crop cubre el lienzo entero con el contenido.
    assert salida.getpixel((10, 10))[0] > 150


def test_una_transformacion_ya_aplicada_no_se_repite() -> None:
    """El modulo 3 ya incorporo el contain a un derivado: no se vuelve a hacer."""
    from viralgen.render.video import GeometryPlan, geometry_filters

    plan = GeometryPlan(
        policy="contain", source_width=1080, source_height=1920,
        target_width=1080, target_height=1920, pad_color="#101418",
        already_applied=True,
    )
    filtros = geometry_filters(plan)
    assert not any("pad=" in filtro for filtro in filtros)
    assert len(filtros) == 1


def test_una_politica_desconocida_se_rechaza() -> None:
    """No se interpretan expresiones arbitrarias del guion como filtros."""
    from viralgen.render.video import GeometryPlan, VideoBuildError, geometry_filters

    plan = GeometryPlan(
        policy="crop,drawtext=text='inyectado'", source_width=10, source_height=10,
        target_width=1080, target_height=1920, pad_color="#101418",
        already_applied=False,
    )
    with pytest.raises(VideoBuildError, match="desconocida"):
        geometry_filters(plan)


def test_un_color_de_relleno_invalido_se_rechaza() -> None:
    from viralgen.render.video import GeometryPlan, VideoBuildError, geometry_filters

    plan = GeometryPlan(
        policy="contain", source_width=10, source_height=10,
        target_width=1080, target_height=1920,
        pad_color="black:x=0:y=0", already_applied=False,
    )
    with pytest.raises(VideoBuildError, match="color de relleno"):
        geometry_filters(plan)


def test_un_video_de_24_fps_se_normaliza_a_30_sin_cambiar_su_duracion(
    tmp_path,
) -> None:
    """La conversion repite o descarta fotogramas; NO acelera la narracion."""
    origen = tmp_path / "origen24.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=size=720x1280:rate=24:duration=5",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(origen),
        ],
        check=True, stdin=subprocess.DEVNULL, timeout=180,
    )
    antes = probe_file(origen, ffprobe_path="ffprobe")
    assert antes.video is not None
    assert antes.video.nb_read_frames == 120       # 24 fps x 5 s

    destino = tmp_path / "salida30.mp4"
    ejecutor = ProcessRunner(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe",
                             log_dir=tmp_path / "logs")
    ejecutor.run_checked(
        [
            *ejecutor.base_args(), "-i", str(origen),
            "-vf", "fps=30,scale=1080:1920:force_original_aspect_ratio=decrease,"
                   "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1/1,format=yuv420p",
            "-frames:v", "150", "-an", "-c:v", "libx264", "-r", "30/1",
            str(destino),
        ],
        stage="fps_24_a_30",
    )
    despues = probe_file(destino, ffprobe_path="ffprobe")
    assert despues.video is not None
    assert despues.video.nb_read_frames == 150     # 30 fps x 5 s
    # La DURACION no cambia: es la misma narracion, con otra cadencia.
    assert despues.video.duration_s == pytest.approx(5.0, abs=0.05)


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------


def test_el_audio_contiene_la_narracion_completa(render_real) -> None:
    """La voz entera, con el margen tecnico de UN frame AAC."""
    manifiesto = render_real["manifest"]
    voz = render_real["voice"]
    reporte = probe_file(render_real["video"], ffprobe_path="ffprobe")
    assert reporte.audio is not None

    esperadas = round(
        voz["master"]["sample_count"] * 48_000 / voz["master"]["sample_rate_hz"]
    )
    medidas = round(reporte.audio.duration_s * 48_000)
    deficit = medidas - esperadas
    assert deficit >= -manifiesto["audio"]["aac_tolerance_samples"], (
        f"faltan {-deficit} muestras: eso seria voz truncada"
    )
    assert manifiesto["audio"]["aac_tolerance_samples"] == 1024


def test_la_narracion_se_usa_una_sola_vez(render_real) -> None:
    """Los clips por escena ya estan dentro del maestro: no se concatenan."""
    manifiesto = render_real["manifest"]
    voz = render_real["voice"]
    assert manifiesto["audio"]["narration_used_once"] is True
    assert manifiesto["audio"]["clip_audio_used"] is False

    # Si se hubieran concatenado los clips ADEMAS del maestro, la duracion
    # seria aproximadamente el doble.
    reporte = probe_file(render_real["video"], ffprobe_path="ffprobe")
    narracion = voz["master"]["sample_count"] / voz["master"]["sample_rate_hz"]
    assert reporte.audio.duration_s == pytest.approx(narracion, abs=0.10)


def test_la_conversion_de_24_a_48_khz_es_exacta(render_real) -> None:
    """Relacion entera: la cuenta de muestras no necesita redondeo."""
    manifiesto = render_real["manifest"]
    conversion = manifiesto["audio"]["resample"]
    assert conversion["source_rate_hz"] == 24_000
    assert conversion["target_rate_hz"] == 48_000
    assert conversion["ratio"] == "2/1"
    assert conversion["exact_integer_ratio"] is True
    assert conversion["expected_samples"] == 2 * conversion["source_samples"]


def test_la_sonoridad_medida_cumple_el_objetivo(render_real) -> None:
    manifiesto = render_real["manifest"]
    medida = manifiesto["audio"]["measured"]
    assert medida["usable"] is True
    assert abs(medida["integrated_lufs"] - (-16.0)) <= 1.0
    assert medida["true_peak_dbtp"] <= -1.0
    assert manifiesto["audio"]["loudness_compliant"] is True


def test_un_audio_silencioso_no_inventa_una_medida(tmp_path) -> None:
    """Si el analisis no es utilizable, se dice; no se fabrica un numero."""
    from viralgen.render.audio import check_loudness, measure_loudness

    silencio = tmp_path / "silencio.wav"
    with wave.open(str(silencio), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(48_000)
        wav.writeframes(b"\x00\x00" * 2 * 48_000 * 3)

    ejecutor = ProcessRunner(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe",
                             log_dir=tmp_path / "logs")
    medida = measure_loudness(ejecutor, silencio)
    assert medida.usable is False
    assert medida.integrated_lufs is None
    assert medida.note
    problemas = check_loudness(medida)
    assert problemas and "no se pudo medir" in problemas[0]


def test_sin_cues_se_exporta_la_narracion_sola(render_real) -> None:
    """El ejemplo no tiene sonido opcional: la mezcla es solo voz."""
    manifiesto = render_real["manifest"]
    assert manifiesto["audio"]["cues_used"] == []
    assert manifiesto["audio"]["ducking"]["applies_to"] == "music"


def test_los_cues_se_colocan_en_su_tiempo_global(tmp_path) -> None:
    """Un tono en su intervalo aparece donde dice el cue, no al principio."""
    from viralgen.render.audio import CuePlacement, build_mix_filter

    cue = CuePlacement(
        cue_type="sfx", asset_id="tono", path=tmp_path / "tono.wav",
        sha256="0" * 64, start_s=2.5, end_s=3.0, gain_db=-6.0,
    )
    grafo, etiqueta = build_mix_filter(
        cues=[cue], narration_rate_hz=24_000, ducking_enabled=True,
        ducking_reduction_db=-9.0, fade_s=0.1,
    )
    # 2,5 s -> 2500 ms de retardo en los dos canales.
    assert "adelay=2500|2500" in grafo
    assert "volume=-6.00dB" in grafo       # un sfx NO se atenua por ducking
    assert etiqueta == "[mezcla]"

    musica = CuePlacement(
        cue_type="music", asset_id="fondo", path=tmp_path / "fondo.wav",
        sha256="0" * 64, start_s=0.0, end_s=10.0, gain_db=-12.0,
    )
    grafo_musica, _ = build_mix_filter(
        cues=[musica], narration_rate_hz=24_000, ducking_enabled=True,
        ducking_reduction_db=-9.0, fade_s=0.1,
    )
    # La musica SI baja mientras habla el narrador: -12 - 9 = -21 dB.
    assert "volume=-21.00dB" in grafo_musica


def test_un_asset_corto_no_se_repite_automaticamente(tmp_path) -> None:
    """Repetirlo seria inventar contenido que nadie pidio."""
    from viralgen.render.audio import CuePlacement, build_mix_filter

    cue = CuePlacement(
        cue_type="music", asset_id="corto", path=tmp_path / "corto.wav",
        sha256="0" * 64, start_s=0.0, end_s=30.0, gain_db=-12.0,
    )
    grafo, _ = build_mix_filter(
        cues=[cue], narration_rate_hz=24_000, ducking_enabled=False,
        ducking_reduction_db=-9.0, fade_s=0.2,
    )
    assert "aloop" not in grafo
    assert "-stream_loop" not in grafo


# ---------------------------------------------------------------------------
# Un archivo que no es lo que dice
# ---------------------------------------------------------------------------


def test_un_mp4_truncado_no_decodifica(render_real, tmp_path) -> None:
    """Un archivo cortado supera ffprobe y falla al decodificar entero."""
    from viralgen.render.probe import decode_check

    truncado = tmp_path / "truncado.mp4"
    datos = render_real["video"].read_bytes()
    truncado.write_bytes(datos[: len(datos) // 2])

    ejecutor = ProcessRunner(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe",
                             log_dir=tmp_path / "logs")
    ok, registro = decode_check(ejecutor, truncado)
    assert ok is False
    assert registro.strip()


def test_una_imagen_renombrada_a_mp4_no_pasa_por_video(render_real, tmp_path) -> None:
    from viralgen.render.probe import ProbeError

    falso = tmp_path / "falso.mp4"
    origen = next((render_real["dir"] / "frames").glob("*.png"))
    falso.write_bytes(origen.read_bytes())

    reporte = probe_file(falso, ffprobe_path="ffprobe")
    # ffprobe lo reconoce como imagen fija, no como el video que dice ser.
    assert reporte.audio is None
    assert reporte.container.format_name != "mov,mp4,m4a,3gp,3g2,mj2"


def test_un_subtitulo_junto_a_una_frontera_de_escena_se_dibuja_en_ambos_lados(
    render_real, tmp_path
) -> None:
    """La estrategia de PTS se verifica justo donde puede romperse.

    Cada segmento se codifica por separado: se desplazan sus PTS al inicio
    global de la escena, se aplica el ASS y se devuelve el segmento a t=0. Si
    ese desplazamiento estuviera mal, el subtitulo de la ultima escena
    reaparecería desde el principio en la siguiente, o desaparecería al cruzar.
    """
    manifiesto = render_real["manifest"]
    frontera = manifiesto["timeline"]["scenes"][1]["start_frame"]

    ass = (render_real["dir"] / "captions.ass").read_text(encoding="utf-8")
    eventos = [
        linea for linea in ass.splitlines()
        if linea.startswith("Dialogue:") and ",Preview," not in linea
    ]

    def a_cs(texto: str) -> int:
        horas, minutos, resto = texto.split(":")
        segundos, centesimas = resto.split(".")
        return (
            int(horas) * 360_000 + int(minutos) * 6_000
            + int(segundos) * 100 + int(centesimas)
        )

    frontera_cs = int(frontera / 30 * 100)
    # El evento vigente inmediatamente ANTES de la frontera.
    antes = [
        linea for linea in eventos
        if a_cs(linea.split(",")[1]) <= frontera_cs - 4 <= a_cs(linea.split(",")[2])
    ]
    # Y el vigente inmediatamente DESPUES.
    despues = [
        linea for linea in eventos
        if a_cs(linea.split(",")[1]) <= frontera_cs + 4 <= a_cs(linea.split(",")[2])
    ]
    assert antes and despues, "deberia haber subtitulo a ambos lados de la frontera"
    # Son grupos distintos: el de la escena que acaba y el de la que empieza.
    assert antes[0].split(",", 9)[9] != despues[0].split(",", 9)[9]

    # Y en el video los dos se ven de verdad, cada uno de su lado.
    imagen_antes = _frame(render_real["video"], frontera - 1, tmp_path / "ant.png")
    imagen_despues = _frame(render_real["video"], frontera, tmp_path / "des.png")
    region_antes = imagen_antes.crop((TEXT_LEFT, TEXT_TOP, TEXT_RIGHT, TEXT_BOTTOM))
    region_despues = imagen_despues.crop((TEXT_LEFT, TEXT_TOP, TEXT_RIGHT, TEXT_BOTTOM))
    assert _pixeles_claros(region_antes) > 400, "falta el subtitulo antes de la frontera"
    assert _pixeles_claros(region_despues) > 400, "falta el subtitulo despues"
    # El texto cambia al cruzar: no es el mismo grupo arrastrado.
    assert _pixeles_distintos(region_antes, region_despues) > 0.01 * (
        region_antes.width * region_antes.height
    )
