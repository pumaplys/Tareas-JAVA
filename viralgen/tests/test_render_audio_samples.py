"""La cuenta de muestras de audio sale de DECODIFICAR, no de la duracion.

Regresion de un defecto real: `decoded_samples` se calculaba como
`duration_s × sample_rate`, es decir la duracion DECLARADA por el contenedor
convertida a muestras, y se etiquetaba como si fuese una cuenta de PCM. En un
AAC real las dos magnitudes difieren, porque el contenedor descuenta el priming
del codificador y este rellena el ultimo frame hasta 1024 muestras.

En el `preview.mp4` del repositorio la diferencia era de 144 muestras (3 ms):
1 222 512 declaradas frente a 1 222 656 decodificadas.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from viralgen.render.ffmpeg import ProcessRunner, probe_capabilities
from viralgen.render.probe import (
    PCM_SAMPLE_WIDTH,
    ProbeError,
    decode_audio_pcm_samples,
    probe_file,
)

TIENE_FFMPEG = probe_capabilities("ffmpeg", "ffprobe").usable


def necesita_ffmpeg(prueba):
    """Aplica DOS marcas: `ffmpeg` (seleccion) y `skipif` (salto sin las herramientas)."""
    con_salto = pytest.mark.skipif(
        not TIENE_FFMPEG, reason="FFmpeg/ffprobe no disponibles"
    )(prueba)
    return pytest.mark.ffmpeg(con_salto)


SAMPLE_RATE = 48_000
CHANNELS = 2


def _runner(tmp_path: Path) -> ProcessRunner:
    return ProcessRunner(
        ffmpeg_path="ffmpeg", ffprobe_path="ffprobe", log_dir=tmp_path / "logs"
    )


def _aac_de_duracion(destino: Path, *, muestras: int) -> None:
    """Crea un MP4 con AAC real de una cantidad EXACTA de muestras de entrada.

    Se parte de PCM generado por `atrim` sobre un tono, para controlar cuantas
    muestras entran; lo que salga del codificador es justo lo que se quiere
    medir y no se presupone.
    """
    duracion = muestras / SAMPLE_RATE
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-f", "lavfi",
            "-i", f"sine=frequency=440:sample_rate={SAMPLE_RATE}:duration={duracion:.6f}",
            "-af", f"atrim=end_sample={muestras},aformat=channel_layouts=stereo",
            "-c:a", "aac", "-profile:a", "aac_low", "-b:a", "192k",
            "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS),
            str(destino),
        ],
        check=True, stdin=subprocess.DEVNULL, timeout=120,
    )


def _pcm_por_tuberia(path: Path) -> int:
    """Cuenta independiente: bytes del PCM extraido, sin usar el codigo propio."""
    proceso = subprocess.run(
        [
            "ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-xerror",
            "-i", str(path), "-map", "0:a:0",
            "-c:a", "pcm_s16le", "-f", "s16le", "-",
        ],
        check=True, stdin=subprocess.DEVNULL, capture_output=True, timeout=120,
    )
    return len(proceso.stdout) // (CHANNELS * PCM_SAMPLE_WIDTH)


@necesita_ffmpeg
def test_la_duracion_declarada_y_el_pcm_difieren_en_un_aac_real(tmp_path) -> None:
    """El supuesto que rompia la medicion anterior, comprobado."""
    # Una cuenta que NO es multiplo de 1024: obliga al codificador a rellenar.
    pedidas = 100_000
    archivo = tmp_path / "tono.m4a"
    _aac_de_duracion(archivo, muestras=pedidas)

    reporte = probe_file(archivo, ffprobe_path="ffprobe", count_frames=False)
    assert reporte.audio is not None
    declaradas = reporte.audio.duration_ts
    pcm = _pcm_por_tuberia(archivo)

    assert declaradas is not None
    assert pcm != declaradas, (
        "este fixture existe precisamente porque las dos magnitudes difieren; "
        f"declaradas={declaradas} pcm={pcm}"
    )
    # Y el PCM es multiplo de un frame AAC: el ultimo va relleno.
    assert pcm % 1024 == 0


@necesita_ffmpeg
def test_la_medicion_del_modulo_cuenta_el_pcm_no_la_duracion(tmp_path) -> None:
    """`decode_audio_pcm_samples` tiene que coincidir con la cuenta externa."""
    archivo = tmp_path / "tono.m4a"
    _aac_de_duracion(archivo, muestras=100_000)

    reporte = probe_file(archivo, ffprobe_path="ffprobe", count_frames=False)
    independiente = _pcm_por_tuberia(archivo)
    medido = decode_audio_pcm_samples(
        _runner(tmp_path), archivo, channels=CHANNELS, max_bytes=64 * 1024 * 1024
    )
    assert medido == independiente
    # Lo importante: NO coincide con la duracion declarada convertida.
    assert medido != reporte.audio.duration_ts


@necesita_ffmpeg
def test_el_priming_no_se_descuenta_dos_veces(tmp_path) -> None:
    """FFmpeg ya procesa el Skip Samples al decodificar.

    El contenedor lleva un paquete mas que los frames decodificados: esa
    diferencia es el priming, y ya esta aplicada en el PCM que se cuenta.
    Restarla otra vez dejaria la cuenta 1024 muestras corta.
    """
    archivo = tmp_path / "tono.m4a"
    _aac_de_duracion(archivo, muestras=100_000)

    reporte = probe_file(archivo, ffprobe_path="ffprobe", count_frames=True)
    medido = decode_audio_pcm_samples(
        _runner(tmp_path), archivo, channels=CHANNELS, max_bytes=64 * 1024 * 1024
    )
    assert reporte.audio is not None
    # La cuenta medida equivale a los frames DECODIFICADOS x 1024, no a los
    # paquetes del contenedor.
    frames_decodificados = medido // 1024
    assert frames_decodificados * 1024 == medido
    assert medido == _pcm_por_tuberia(archivo)


@necesita_ffmpeg
def test_el_signo_del_deficit_distingue_falta_de_sobra(tmp_path) -> None:
    """Negativo = falta narracion (defecto). Positivo = relleno del codec."""
    archivo = tmp_path / "tono.m4a"
    esperadas = 100_000
    _aac_de_duracion(archivo, muestras=esperadas)
    medido = decode_audio_pcm_samples(
        _runner(tmp_path), archivo, channels=CHANNELS, max_bytes=64 * 1024 * 1024
    )

    deficit = medido - esperadas
    # El codificador RELLENA: sobra audio, no falta.
    assert deficit > 0, f"se esperaba relleno, no recorte: {deficit}"
    assert deficit < 1024, "el relleno cabe en un frame AAC"

    # Y el caso contrario tiene el signo opuesto: una narracion mas larga que
    # el archivo da deficit negativo.
    deficit_truncado = medido - (esperadas + 5000)
    assert deficit_truncado < 0


@necesita_ffmpeg
def test_un_audio_que_no_decodifica_no_devuelve_una_cuenta(tmp_path) -> None:
    """Sin medida no se inventa un numero: se levanta el error."""
    roto = tmp_path / "roto.m4a"
    roto.write_bytes(b"esto no es un MP4" * 100)
    with pytest.raises(ProbeError):
        decode_audio_pcm_samples(
            _runner(tmp_path), roto, channels=CHANNELS, max_bytes=1024 * 1024
        )


@necesita_ffmpeg
def test_el_limite_de_bytes_corta_la_lectura(tmp_path) -> None:
    """El audio no se carga entero: el tope se aplica mientras se lee."""
    archivo = tmp_path / "tono.m4a"
    _aac_de_duracion(archivo, muestras=SAMPLE_RATE * 5)
    with pytest.raises(ProbeError, match="supera"):
        decode_audio_pcm_samples(
            _runner(tmp_path), archivo, channels=CHANNELS, max_bytes=64 * 1024
        )


@necesita_ffmpeg
def test_el_ejemplo_del_repositorio_declara_la_cuenta_medida(tmp_path) -> None:
    """El paquete entregado tiene que llevar la cuenta real, no la duracion."""
    import json

    raiz = Path(__file__).parent.parent
    manifiesto_path = raiz / "examples" / "montaje_preview" / "render.json"
    if not manifiesto_path.is_file():  # pragma: no cover - ejemplo ausente
        pytest.skip("el ejemplo no esta en el arbol")

    manifiesto = json.loads(manifiesto_path.read_text(encoding="utf-8"))
    video = manifiesto_path.parent / manifiesto["output"]["path"]
    audio = manifiesto["output"]["audio"]

    pcm = _pcm_por_tuberia(video)
    assert audio["decoded_samples"] == pcm, (
        "el manifiesto del ejemplo no declara la cuenta PCM medida"
    )
    assert audio["decoded_samples_source"] == "pcm_decode"
    # Y la duracion declarada se conserva como magnitud SEPARADA.
    assert audio["stream_duration_ts"] is not None
    assert audio["stream_duration_ts"] != pcm
