"""Medicion de PCM, pausas y construccion del maestro."""

from __future__ import annotations

import shutil
import wave
from pathlib import Path

import pytest

from viralgen.voice.audio import (
    AudioError,
    PcmFormat,
    decode_to_wav,
    ffmpeg_available,
    iter_wav_frames,
    pause_samples,
    read_wav_info,
    require_format,
    silence_bytes,
    write_wav,
)
from viralgen.voice.timing import build_measured_timeline, total_samples

FMT = PcmFormat(sample_rate_hz=24_000, channels=1, sample_width_bytes=2)


@pytest.mark.parametrize(
    ("segundos", "muestras"),
    [(0.0, 0), (0.25, 6_000), (0.3, 7_200), (0.5, 12_000), (1.5, 36_000)],
)
def test_pausa_en_muestras(segundos: float, muestras: int) -> None:
    """Redondeo documentado: media hacia arriba."""
    assert pause_samples(segundos, 24_000) == muestras


def test_pausa_negativa() -> None:
    with pytest.raises(ValueError):
        pause_samples(-0.1, 24_000)


def _tono(path: Path, muestras: int) -> None:
    write_wav(path, [b"\x01\x00" * muestras], FMT)


def test_la_duracion_se_mide_contando_muestras(tmp_path: Path) -> None:
    ruta = tmp_path / "clip.wav"
    _tono(ruta, 36_000)
    info = read_wav_info(ruta)
    assert info.sample_count == 36_000
    assert info.duration_s == pytest.approx(1.5)
    require_format(info, FMT)


def test_formato_distinto_se_rechaza(tmp_path: Path) -> None:
    ruta = tmp_path / "otro.wav"
    with wave.open(str(ruta), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(44_100)
        handle.writeframes(b"\x00\x00\x00\x00" * 100)
    with pytest.raises(AudioError, match="formato interno"):
        require_format(read_wav_info(ruta), FMT)


def test_wav_inexistente(tmp_path: Path) -> None:
    with pytest.raises(AudioError, match="No existe"):
        read_wav_info(tmp_path / "nada.wav")


def test_la_pausa_se_cuenta_exactamente_una_vez(tmp_path: Path) -> None:
    """El maestro = clips + pausas, cada pausa una sola vez, tambien la ultima."""
    clips = []
    for indice, muestras in enumerate((24_000, 12_000, 6_000)):
        ruta = tmp_path / f"c{indice}.wav"
        _tono(ruta, muestras)
        clips.append((ruta, muestras))

    pausas = [7_200, 7_200, 12_000]  # 0,3 s, 0,3 s y 0,5 s al final
    medidas = build_measured_timeline(
        [(f"sc_{i:02d}", i + 1, muestras, pausa)
         for i, ((_, muestras), pausa) in enumerate(zip(clips, pausas, strict=True))]
    )

    def bloques():
        for (ruta, _), medida in zip(clips, medidas, strict=True):
            yield from iter_wav_frames(ruta)
            yield silence_bytes(medida.pause_samples, FMT)

    maestro = tmp_path / "narration.wav"
    escritas = write_wav(maestro, bloques(), FMT)

    esperadas = sum(m for _, m in clips) + sum(pausas)
    assert escritas == esperadas == total_samples(medidas)
    assert read_wav_info(maestro).sample_count == esperadas

    # Las escenas cubren el maestro desde cero, sin huecos ni solapamientos.
    cursor = 0
    for medida in medidas:
        assert medida.start_sample == cursor
        cursor = medida.end_sample
    assert cursor == esperadas


def test_los_segundos_se_derivan_de_los_enteros() -> None:
    """No se acumulan segundos redondeados escena a escena."""
    medidas = build_measured_timeline([(f"sc_{i:02d}", i + 1, 17_777, 7_200) for i in range(9)])
    ultimo = medidas[-1]
    assert ultimo.end_sample == 9 * (17_777 + 7_200)
    assert ultimo.end_s(24_000) == pytest.approx(ultimo.end_sample / 24_000, abs=1e-9)


def test_escena_sin_audio() -> None:
    with pytest.raises(ValueError, match="no tiene muestras"):
        build_measured_timeline([("sc_01", 1, 0, 0)])


def test_bloque_no_alineado(tmp_path: Path) -> None:
    with pytest.raises(AudioError, match="no alineado"):
        write_wav(tmp_path / "malo.wav", [b"\x01"], FMT)


@pytest.mark.skipif(not ffmpeg_available("ffmpeg"), reason="FFmpeg no esta instalado")
def test_decodificacion_mp3_con_ffmpeg(tmp_path: Path) -> None:
    """Decodifica un MP3 real a WAV PCM 16 bits mono 24 kHz.

    Si FFmpeg no esta disponible la prueba se SALTA explicitamente: saltarla no
    equivale a haber probado el decodificador.
    """
    origen = tmp_path / "fuente.wav"
    _tono(origen, 24_000)
    mp3 = tmp_path / "fuente.mp3"
    import subprocess

    subprocess.run(
        [shutil.which("ffmpeg"), "-nostdin", "-y", "-loglevel", "error", "-i", str(origen),
         "-codec:a", "libmp3lame", "-b:a", "128k", str(mp3)],
        check=True, shell=False, timeout=60,
    )
    destino = tmp_path / "salida.wav"
    info = decode_to_wav(mp3, destino, fmt=FMT, timeout_s=60)
    assert info.matches(FMT)
    assert info.sample_count > 0
