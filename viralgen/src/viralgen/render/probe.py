"""Inspeccion del archivo terminado con ffprobe, y decodificacion real.

Que FFmpeg termine con codigo 0 no significa que el archivo sirva. Aqui se
mide lo que hay dentro:

* propiedades de cada stream (codec, dimensiones, SAR/DAR, fps, canales);
* la cuenta REAL de fotogramas, contando paquetes/frames, no la cabecera;
* los PTS de presentacion, para comprobar CFR y continuidad (con B-frames el
  orden de decodificacion NO es el de presentacion: se ordena por PTS);
* una decodificacion completa de ambas pistas a salida nula, que es lo unico
  que demuestra que el archivo no esta truncado.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from ..errors import ViralgenError
from .ffmpeg import ProcessRunner, _capture


class ProbeError(ViralgenError):
    code = "probe_error"


@dataclass
class StreamInfo:
    index: int
    codec_name: str
    codec_type: str
    raw: dict = field(default_factory=dict)


@dataclass
class VideoInfo:
    codec_name: str
    width: int
    height: int
    pix_fmt: str
    sample_aspect_ratio: str
    display_aspect_ratio: str
    avg_frame_rate: str
    r_frame_rate: str
    nb_read_frames: int | None
    duration_s: float | None
    rotation: int
    has_b_frames: int

    @property
    def fps(self) -> float:
        try:
            return float(Fraction(self.r_frame_rate))
        except (ZeroDivisionError, ValueError):
            return 0.0

    def describe(self) -> dict:
        return {
            "codec_name": self.codec_name,
            "width": self.width,
            "height": self.height,
            "pix_fmt": self.pix_fmt,
            "sample_aspect_ratio": self.sample_aspect_ratio,
            "display_aspect_ratio": self.display_aspect_ratio,
            "r_frame_rate": self.r_frame_rate,
            "avg_frame_rate": self.avg_frame_rate,
            "fps": round(self.fps, 6),
            "frame_count": self.nb_read_frames,
            "stream_duration_s": self.duration_s,
            "rotation_degrees": self.rotation,
            "has_b_frames": self.has_b_frames,
        }


@dataclass
class AudioInfo:
    codec_name: str
    sample_rate_hz: int
    channels: int
    channel_layout: str
    duration_s: float | None
    nb_samples: int | None
    start_pts: int | None
    initial_padding: int | None

    def describe(self) -> dict:
        return {
            "codec_name": self.codec_name,
            "sample_rate_hz": self.sample_rate_hz,
            "channels": self.channels,
            "channel_layout": self.channel_layout,
            "stream_duration_s": self.duration_s,
            "decoded_samples": self.nb_samples,
            "start_pts": self.start_pts,
            "initial_padding_samples": self.initial_padding,
        }


@dataclass
class ContainerInfo:
    format_name: str
    duration_s: float | None
    size_bytes: int
    tags: dict = field(default_factory=dict)

    def describe(self) -> dict:
        return {
            "format_name": self.format_name,
            "container_duration_s": self.duration_s,
            "size_bytes": self.size_bytes,
        }


@dataclass
class MediaReport:
    """Todo lo medido sobre un archivo terminado."""

    container: ContainerInfo
    video: VideoInfo | None
    audio: AudioInfo | None
    pts_monotonic: bool = True
    pts_gaps: list[int] = field(default_factory=list)
    cfr: bool = True
    notes: list[str] = field(default_factory=list)

    def describe(self) -> dict:
        return {
            "container": self.container.describe(),
            "video": self.video.describe() if self.video else None,
            "audio": self.audio.describe() if self.audio else None,
            "pts_monotonic": self.pts_monotonic,
            "constant_frame_rate": self.cfr,
            "pts_gap_frames": self.pts_gaps[:20],
            "notes": list(self.notes),
        }


def _ffprobe_json(ffprobe_path: str, args: list[str], *, timeout_s: int = 120) -> dict:
    salida = _capture(
        [ffprobe_path, "-hide_banner", "-loglevel", "error", "-of", "json", *args],
        timeout_s=timeout_s,
    )
    inicio = salida.find("{")
    if inicio < 0:
        raise ProbeError(
            "ffprobe no devolvio JSON", details={"output": salida[:400]}
        )
    try:
        return json.loads(salida[inicio:])
    except json.JSONDecodeError as exc:
        raise ProbeError(
            f"la salida de ffprobe no es JSON valido: {exc}",
            details={"output": salida[:400]},
        ) from exc


def probe_file(path: Path, *, ffprobe_path: str, count_frames: bool = True) -> MediaReport:
    """Describe el archivo: contenedor, streams y cuenta real de fotogramas."""
    if not path.is_file():
        raise ProbeError(f"no existe el archivo {path}")

    args = ["-show_format", "-show_streams"]
    if count_frames:
        # `-count_frames` decodifica para contar de verdad, no lee la cabecera.
        args.append("-count_frames")
    datos = _ffprobe_json(ffprobe_path, [*args, str(path)])

    formato = datos.get("format", {})
    contenedor = ContainerInfo(
        format_name=str(formato.get("format_name", "")),
        duration_s=_float(formato.get("duration")),
        size_bytes=int(formato.get("size") or path.stat().st_size),
        tags=dict(formato.get("tags", {})),
    )

    video = None
    audio = None
    for stream in datos.get("streams", []):
        tipo = stream.get("codec_type")
        if tipo == "video" and video is None:
            video = _video_info(stream)
        elif tipo == "audio" and audio is None:
            audio = _audio_info(stream)

    return MediaReport(container=contenedor, video=video, audio=audio)


def _video_info(stream: dict) -> VideoInfo:
    return VideoInfo(
        codec_name=str(stream.get("codec_name", "")),
        width=int(stream.get("width") or 0),
        height=int(stream.get("height") or 0),
        pix_fmt=str(stream.get("pix_fmt", "")),
        sample_aspect_ratio=str(stream.get("sample_aspect_ratio", "")),
        display_aspect_ratio=str(stream.get("display_aspect_ratio", "")),
        avg_frame_rate=str(stream.get("avg_frame_rate", "0/0")),
        r_frame_rate=str(stream.get("r_frame_rate", "0/0")),
        nb_read_frames=_int(stream.get("nb_read_frames")),
        duration_s=_float(stream.get("duration")),
        rotation=_rotation(stream),
        has_b_frames=int(stream.get("has_b_frames") or 0),
    )


def _audio_info(stream: dict) -> AudioInfo:
    return AudioInfo(
        codec_name=str(stream.get("codec_name", "")),
        sample_rate_hz=int(stream.get("sample_rate") or 0),
        channels=int(stream.get("channels") or 0),
        channel_layout=str(stream.get("channel_layout", "")),
        duration_s=_float(stream.get("duration")),
        nb_samples=_int(stream.get("nb_read_samples")),
        start_pts=_int(stream.get("start_pts")),
        initial_padding=_int(stream.get("initial_padding")),
    )


def _rotation(stream: dict) -> int:
    """Rotacion declarada, sea por tag o por matriz de display."""
    tags = stream.get("tags", {})
    crudo = tags.get("rotate")
    if crudo is not None:
        try:
            return int(float(crudo)) % 360
        except (TypeError, ValueError):
            return 0
    for dato in stream.get("side_data_list", []) or []:
        if "rotation" in dato:
            try:
                return int(float(dato["rotation"])) % 360
            except (TypeError, ValueError):
                return 0
    return 0


def read_video_pts(path: Path, *, ffprobe_path: str) -> list[int]:
    """PTS de PRESENTACION de cada fotograma de video, ordenados.

    Con B-frames el orden de decodificacion no es el de presentacion, asi que
    la lista se ordena por PTS antes de analizar continuidad.
    """
    datos = _ffprobe_json(
        ffprobe_path,
        [
            "-select_streams", "v:0",
            "-show_entries", "frame=pts",
            "-fflags", "+genpts",
            str(path),
        ],
    )
    valores: list[int] = []
    for frame in datos.get("frames", []):
        pts = _int(frame.get("pts"))
        if pts is not None:
            valores.append(pts)
    return sorted(valores)


def analyze_cfr(pts: list[int], *, expected_step: int) -> tuple[bool, bool, list[int]]:
    """Comprueba PTS monotonos y paso constante.

    Devuelve `(monotonos, cfr, indices_con_salto)`.
    """
    if len(pts) < 2:
        return True, True, []
    monotonos = all(b > a for a, b in zip(pts, pts[1:]))
    saltos = [
        indice
        for indice, (a, b) in enumerate(zip(pts, pts[1:]))
        if b - a != expected_step
    ]
    return monotonos, not saltos, saltos


def decode_check(
    runner: ProcessRunner, path: Path, *, stage: str = "decode_check"
) -> tuple[bool, str]:
    """Decodifica AMBAS pistas enteras a salida nula.

    Es lo unico que demuestra que el archivo no esta truncado. Un MP4 cortado
    por un limite de tamano supera `ffprobe` y falla aqui.
    """
    args = [
        runner.ffmpeg_path, "-hide_banner", "-nostdin",
        "-loglevel", "error",
        "-xerror",
        "-i", str(path),
        "-map", "0",
        "-f", "null", "-",
    ]
    resultado = runner.run(args, stage=stage)
    return resultado.ok and not resultado.log_tail.strip(), resultado.log_tail


def _float(value) -> float | None:
    if value in (None, "", "N/A"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value) -> int | None:
    if value in (None, "", "N/A"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
