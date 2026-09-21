"""Audio PCM con la biblioteca estandar y decodificacion via FFmpeg.

Formato interno del proyecto (decision fija, registrada en el manifiesto):
WAV PCM de 16 bits, mono, 24.000 Hz. Todo lo que entra se decodifica a ese
formato antes de medirse.

La duracion real SIEMPRE se mide contando muestras del PCM decodificado
(`wave.Wave_read.getnframes()`), nunca a partir de palabras por minuto, del
tamano del MP3 ni de lo que declare el proveedor.
"""

from __future__ import annotations

import math
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from ..errors import ConfigError, ViralgenError

#: Tamano de bloque para leer y escribir PCM sin cargarlo entero en memoria.
BLOCK_FRAMES = 65_536


class AudioError(ViralgenError):
    exit_code = ConfigError.exit_code
    code = "audio_error"


@dataclass(frozen=True)
class PcmFormat:
    """Formato PCM esperado."""

    sample_rate_hz: int
    channels: int
    sample_width_bytes: int

    @property
    def codec(self) -> str:
        if self.sample_width_bytes == 2:
            return "pcm_s16le"
        return f"pcm_s{self.sample_width_bytes * 8}le"

    @property
    def bytes_per_frame(self) -> int:
        return self.channels * self.sample_width_bytes

    def describe(self) -> dict:
        return {
            "codec": self.codec,
            "sample_rate_hz": self.sample_rate_hz,
            "channels": self.channels,
            "sample_width_bytes": self.sample_width_bytes,
        }


@dataclass(frozen=True)
class WavInfo:
    """Parametros medidos de un WAV existente."""

    path: Path
    sample_rate_hz: int
    channels: int
    sample_width_bytes: int
    sample_count: int

    @property
    def duration_s(self) -> float:
        return self.sample_count / self.sample_rate_hz

    def matches(self, fmt: PcmFormat) -> bool:
        return (
            self.sample_rate_hz == fmt.sample_rate_hz
            and self.channels == fmt.channels
            and self.sample_width_bytes == fmt.sample_width_bytes
        )


def read_wav_info(path: Path) -> WavInfo:
    """Lee los parametros reales del WAV contando sus frames."""
    try:
        with wave.open(str(path), "rb") as handle:
            return WavInfo(
                path=path,
                sample_rate_hz=handle.getframerate(),
                channels=handle.getnchannels(),
                sample_width_bytes=handle.getsampwidth(),
                sample_count=handle.getnframes(),
            )
    except FileNotFoundError as exc:
        raise AudioError(f"No existe el WAV: {path}") from exc
    except wave.Error as exc:
        raise AudioError(f"WAV invalido ({path}): {exc}") from exc


def require_format(info: WavInfo, fmt: PcmFormat) -> None:
    if not info.matches(fmt):
        raise AudioError(
            f"El WAV {info.path.name} no esta en el formato interno del proyecto "
            f"({fmt.codec}, {fmt.sample_rate_hz} Hz, {fmt.channels} canal/es): "
            f"encontrado {info.sample_width_bytes * 8} bits, {info.sample_rate_hz} Hz, "
            f"{info.channels} canal/es.",
            details={"path": str(info.path), "expected": fmt.describe()},
        )


def pause_samples(pause_after_s: float, sample_rate_hz: int) -> int:
    """Muestras de silencio de una pausa.

    Redondeo documentado: media hacia arriba (`floor(x + 0.5)`), no el
    redondeo bancario de `round()`. Con las pausas del modulo 1 (multiplos de
    0,05 s) el resultado es exacto: 0,3 s x 24000 = 7200 muestras.
    """
    if pause_after_s < 0:
        raise ValueError("pause_after_s no puede ser negativa")
    return int(math.floor(pause_after_s * sample_rate_hz + 0.5))


def silence_bytes(n_samples: int, fmt: PcmFormat) -> bytes:
    """Silencio PCM firmado: ceros."""
    if n_samples < 0:
        raise ValueError("n_samples no puede ser negativo")
    return b"\x00" * (n_samples * fmt.bytes_per_frame)


def iter_wav_frames(path: Path, block_frames: int = BLOCK_FRAMES) -> Iterator[bytes]:
    """Lee un WAV por bloques, sin cargarlo entero en memoria."""
    with wave.open(str(path), "rb") as handle:
        while True:
            chunk = handle.readframes(block_frames)
            if not chunk:
                return
            yield chunk


def write_wav(path: Path, chunks: Iterable[bytes], fmt: PcmFormat) -> int:
    """Escribe un WAV a partir de bloques PCM. Devuelve las muestras escritas."""
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(fmt.channels)
        handle.setsampwidth(fmt.sample_width_bytes)
        handle.setframerate(fmt.sample_rate_hz)
        for chunk in chunks:
            if not chunk:
                continue
            if len(chunk) % fmt.bytes_per_frame:
                raise AudioError("Bloque PCM con un numero de bytes no alineado a frames")
            handle.writeframes(chunk)
            written += len(chunk) // fmt.bytes_per_frame
    return written


def ffmpeg_available(ffmpeg_path: str) -> bool:
    return shutil.which(ffmpeg_path) is not None


def require_ffmpeg(ffmpeg_path: str) -> str:
    """Comprueba FFmpeg ANTES de gastar peticiones de pago."""
    resolved = shutil.which(ffmpeg_path)
    if resolved is None:
        raise ConfigError(
            f"No se encontro FFmpeg ({ffmpeg_path!r}). Hace falta para decodificar el "
            "audio del proveedor a WAV PCM. Instalalo (apt install ffmpeg) o ajusta "
            "VIRALGEN_FFMPEG_PATH. El modo --mock no lo necesita.",
            details={"ffmpeg_path": ffmpeg_path},
        )
    return resolved


def decode_to_wav(
    source: Path,
    destination: Path,
    *,
    fmt: PcmFormat,
    ffmpeg_path: str = "ffmpeg",
    timeout_s: int = 120,
) -> WavInfo:
    """Decodifica un archivo de audio local a WAV PCM con FFmpeg.

    Se invoca con lista de argumentos y `shell=False`, con timeout y sobre
    archivos locales controlados por la aplicacion: nunca sobre rutas ni URLs
    propuestas por un proveedor o por el modelo.
    """
    binary = require_ffmpeg(ffmpeg_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    args = [
        binary,
        "-nostdin",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-i", str(source),
        "-vn",
        "-ac", str(fmt.channels),
        "-ar", str(fmt.sample_rate_hz),
        "-acodec", fmt.codec,
        "-f", "wav",
        str(destination),
    ]
    try:
        completed = subprocess.run(  # noqa: S603 - lista de argumentos, shell=False
            args,
            shell=False,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        destination.unlink(missing_ok=True)
        raise AudioError(
            f"FFmpeg supero el tiempo limite de {timeout_s} s decodificando {source.name}"
        ) from exc
    if completed.returncode != 0:
        destination.unlink(missing_ok=True)
        detalle = completed.stderr.decode("utf-8", "replace").strip()[:500]
        raise AudioError(
            f"FFmpeg fallo decodificando {source.name} (codigo {completed.returncode}): {detalle}"
        )
    info = read_wav_info(destination)
    require_format(info, fmt)
    return info
