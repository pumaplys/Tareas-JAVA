"""Inspeccion de video con ffprobe/ffmpeg.

Reglas del MVP:

* La duracion VISUAL sale del stream de video. La duracion de una pista de
  audio no es la duracion visual y no se usa como tal.
* Se acepta MP4/H.264 SDR. Otros contenedores o codecs bloquean.
* La comprobacion de integridad decodifica a salida nula (`-f null -`): no
  escribe cuadros en disco y esta acotada por tiempo.
* No se transcodifica nada por adelantado. La conversion a 1080x1920 y 30 fps
  es del modulo 4.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from ..errors import ConfigError, ViralgenError

#: Codecs de video aceptados en este MVP.
ACCEPTED_VIDEO_CODECS = {"h264"}

#: Contenedores aceptados (tal y como los nombra ffprobe).
ACCEPTED_CONTAINERS = {"mov,mp4,m4a,3gp,3g2,mj2", "mp4"}


class MediaVideoError(ViralgenError):
    exit_code = 5
    code = "media_video_error"


@dataclass(frozen=True)
class VideoInfo:
    """Medidas REALES del archivo, leidas con ffprobe."""

    path: Path
    width: int
    height: int
    duration_s: float
    fps_num: int
    fps_den: int
    codec: str
    container: str
    has_audio: bool
    size_bytes: int

    @property
    def fps(self) -> float:
        return self.fps_num / self.fps_den if self.fps_den else 0.0

    def describe(self) -> dict:
        return {
            "width": self.width,
            "height": self.height,
            "measured_duration_s": round(self.duration_s, 6),
            "fps_rational": f"{self.fps_num}/{self.fps_den}",
            "fps": round(self.fps, 6),
            "codec": self.codec,
            "container": self.container,
            "has_audio": self.has_audio,
            "size_bytes": self.size_bytes,
        }


def tool_available(path: str) -> bool:
    return shutil.which(path) is not None


def require_video_tools(ffmpeg_path: str, ffprobe_path: str) -> tuple[str, str]:
    """Exige FFmpeg y ffprobe ANTES de generar ningun asset de video."""
    faltan = [
        nombre
        for nombre, ruta in (("ffmpeg", ffmpeg_path), ("ffprobe", ffprobe_path))
        if shutil.which(ruta) is None
    ]
    if faltan:
        raise ConfigError(
            f"Faltan ejecutables para trabajar con video: {', '.join(faltan)}. "
            "Instalalos (apt install ffmpeg) o ajusta VIRALGEN_FFMPEG_PATH / "
            "VIRALGEN_FFPROBE_PATH. Un trabajo solo de imagenes no los necesita.",
            details={"missing": faltan},
        )
    return shutil.which(ffmpeg_path), shutil.which(ffprobe_path)  # type: ignore[return-value]


def _run(args: list[str], timeout_s: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(  # noqa: S603 - lista de argumentos, shell=False
            args, shell=False, capture_output=True, timeout=timeout_s, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaVideoError(
            f"La herramienta {Path(args[0]).name} supero el tiempo limite de {timeout_s} s."
        ) from exc


def probe_video(path: Path, *, ffprobe_path: str = "ffprobe", timeout_s: int = 60) -> VideoInfo:
    """Lee dimensiones, duracion, fps y codec del STREAM DE VIDEO."""
    binario = shutil.which(ffprobe_path)
    if binario is None:
        raise ConfigError(f"No se encontro ffprobe ({ffprobe_path!r}).")
    resultado = _run(
        [
            binario, "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ],
        timeout_s,
    )
    if resultado.returncode != 0:
        detalle = resultado.stderr.decode("utf-8", "replace").strip()[:300]
        raise MediaVideoError(f"ffprobe fallo con {path.name}: {detalle}")
    try:
        datos = json.loads(resultado.stdout or b"{}")
    except ValueError as exc:
        raise MediaVideoError(f"ffprobe devolvio un JSON invalido para {path.name}") from exc

    streams = datos.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise MediaVideoError(f"{path.name} no contiene ningun stream de video.")
    audio = any(s.get("codec_type") == "audio" for s in streams)

    codec = str(video.get("codec_name") or "").lower()
    if codec not in ACCEPTED_VIDEO_CODECS:
        raise MediaVideoError(
            f"Codec {codec or 'desconocido'} no aceptado en este MVP "
            f"({', '.join(sorted(ACCEPTED_VIDEO_CODECS))})."
        )
    contenedor = str((datos.get("format") or {}).get("format_name") or "")
    if contenedor not in ACCEPTED_CONTAINERS:
        raise MediaVideoError(
            f"Contenedor {contenedor or 'desconocido'} no aceptado en este MVP (MP4)."
        )

    # La duracion visual sale del stream de video. Solo si ese stream no la
    # declara se recurre a la del contenedor, y queda dicho en el error.
    duracion_bruta = video.get("duration")
    if duracion_bruta is None:
        duracion_bruta = (datos.get("format") or {}).get("duration")
    try:
        duracion = float(duracion_bruta)
    except (TypeError, ValueError) as exc:
        raise MediaVideoError(
            f"{path.name} no declara una duracion de video utilizable."
        ) from exc
    if duracion <= 0:
        raise MediaVideoError(f"{path.name} declara una duracion de video no positiva.")

    fraccion = Fraction(str(video.get("avg_frame_rate") or "0/1"))
    if fraccion == 0:
        fraccion = Fraction(str(video.get("r_frame_rate") or "0/1"))
    if fraccion == 0:
        raise MediaVideoError(f"{path.name} no declara fotogramas por segundo.")

    return VideoInfo(
        path=path,
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        duration_s=duracion,
        fps_num=fraccion.numerator,
        fps_den=fraccion.denominator,
        codec=codec,
        container=contenedor,
        has_audio=audio,
        size_bytes=path.stat().st_size,
    )


def decode_integrity_check(
    path: Path, *, ffmpeg_path: str = "ffmpeg", timeout_s: int = 120, max_seconds: float | None = None
) -> None:
    """Decodifica a salida nula para detectar archivos corruptos.

    No crea cuadros en disco y esta acotada por `timeout_s` y, opcionalmente,
    por los primeros `max_seconds` del clip.
    """
    binario = shutil.which(ffmpeg_path)
    if binario is None:
        raise ConfigError(f"No se encontro FFmpeg ({ffmpeg_path!r}).")
    args = [binario, "-nostdin", "-v", "error", "-xerror"]
    if max_seconds:
        args += ["-t", f"{max_seconds:g}"]
    args += ["-i", str(path), "-map", "0:v:0", "-f", "null", "-"]
    resultado = _run(args, timeout_s)
    if resultado.returncode != 0:
        detalle = resultado.stderr.decode("utf-8", "replace").strip()[:300]
        raise MediaVideoError(
            f"La decodificacion de integridad fallo en {path.name}: {detalle}"
        )


def make_test_video(
    destination: Path,
    *,
    seconds: int,
    width: int,
    height: int,
    fps: int = 24,
    ffmpeg_path: str = "ffmpeg",
    timeout_s: int = 120,
    label: str = "SIMULACION",
) -> Path:
    """Crea un MP4/H.264 real y valido con FFmpeg, para el proveedor simulado.

    Es una senal de prueba: nunca se renombra una imagen a .mp4 ni se inventan
    resultados de ffprobe.
    """
    binario = shutil.which(ffmpeg_path)
    if binario is None:
        raise ConfigError(f"No se encontro FFmpeg ({ffmpeg_path!r}).")
    destination.parent.mkdir(parents=True, exist_ok=True)
    args = [
        binario, "-nostdin", "-v", "error", "-y",
        "-f", "lavfi",
        "-i", f"testsrc=size={width}x{height}:rate={fps}:duration={seconds}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
        "-t", str(seconds), "-an", str(destination),
    ]
    resultado = _run(args, timeout_s)
    if resultado.returncode != 0:
        destination.unlink(missing_ok=True)
        detalle = resultado.stderr.decode("utf-8", "replace").strip()[:300]
        raise MediaVideoError(f"No se pudo crear el MP4 de prueba: {detalle}")
    return destination
