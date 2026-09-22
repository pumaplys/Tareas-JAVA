"""Construccion de los segmentos visuales y de la exportacion final.

Un segmento por escena, procesado y validado antes de empezar el siguiente.
Todos los segmentos comparten codec, resolucion, formato de pixel, fps, base
temporal y ajustes de codificacion: es la condicion para que el demuxer
`concat` pueda unirlos copiando el stream en vez de recodificar.

Decisiones de codificacion, todas explicitas:

* H.264 por `libx264`, `yuv420p`, SAR 1:1, CFR 30/1, `-video_track_timescale`
  fijo para que la base temporal no cambie entre segmentos.
* GOP cerrado de una duracion fija y `scenecut` desactivado: cada segmento
  empieza en un IDR, que es lo que permite concatenar sin artefactos.
* AAC-LC a 48 000 Hz en el mux final.
* `+faststart` en el MP4 final para que su indice quede al principio.

El tamano final se MIDE; CRF no promete ninguno.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from ..errors import ViralgenError
from .audio import WORK_SAMPLE_RATE_HZ
from .ffmpeg import ProcessRunner

logger = logging.getLogger(__name__)

#: Objetivo del MVP. Se comprueba contra el objetivo del contrato de medios.
TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920
TARGET_FPS = 30

#: Base temporal del stream de video. Multiplo de 30 para que cada fotograma
#: caiga en un entero exacto de PTS: 15360/30 = 512 por fotograma.
VIDEO_TIMESCALE = 15360

DEFAULT_PRESET = "veryfast"
DEFAULT_CRF = 21

#: Movimiento de camara admitido. Cualquier otro valor se RECHAZA: el guion no
#: puede colar una expresion arbitraria de FFmpeg por esta via.
CAMERA_MOVES = ("static", "zoom_in", "zoom_out")

#: Zoom maximo del estilo dinamico. Un zoom sobre una imagen `contain` recortaria
#: el relleno, asi que ahi se queda en `static`.
MAX_ZOOM = 1.04


class VideoBuildError(ViralgenError):
    code = "video_build_error"


@dataclass(frozen=True)
class EncodeSettings:
    """Ajustes homogeneos para todos los segmentos y para el mux final."""

    preset: str = DEFAULT_PRESET
    crf: int = DEFAULT_CRF
    fps: int = TARGET_FPS
    width: int = TARGET_WIDTH
    height: int = TARGET_HEIGHT
    audio_bitrate: str = "192k"
    gop_seconds: int = 2

    @property
    def gop_frames(self) -> int:
        return self.fps * self.gop_seconds

    def describe(self) -> dict:
        return {
            "video_codec": "libx264",
            "preset": self.preset,
            "crf": self.crf,
            "pix_fmt": "yuv420p",
            "width": self.width,
            "height": self.height,
            "fps": f"{self.fps}/1",
            "sar": "1:1",
            "video_timescale": VIDEO_TIMESCALE,
            "gop_frames": self.gop_frames,
            "closed_gop": True,
            "scenecut": "disabled",
            "audio_codec": "aac",
            "audio_profile": "aac_low",
            "audio_sample_rate_hz": WORK_SAMPLE_RATE_HZ,
            "audio_bitrate": self.audio_bitrate,
            "faststart": True,
            "note": (
                "CRF no impone tamano: el tamano final se mide sobre el archivo, "
                "no se promete a partir del CRF."
            ),
        }

    def x264_args(self) -> list[str]:
        """Parametros homogeneos, necesarios para concatenar por copia."""
        return [
            "-c:v", "libx264",
            "-preset", self.preset,
            "-crf", str(self.crf),
            "-pix_fmt", "yuv420p",
            "-profile:v", "high",
            "-level", "4.0",
            # GOP cerrado y sin corte por escena: cada segmento empieza en IDR
            # y todos tienen la misma estructura.
            "-g", str(self.gop_frames),
            "-keyint_min", str(self.gop_frames),
            "-sc_threshold", "0",
            "-x264-params", f"keyint={self.gop_frames}:min-keyint={self.gop_frames}"
                            ":scenecut=0:open-gop=0",
            "-video_track_timescale", str(VIDEO_TIMESCALE),
            "-r", f"{self.fps}/1",
            "-vsync", "cfr",
        ]


# ---------------------------------------------------------------------------
# Geometria
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GeometryPlan:
    """Transformacion PENDIENTE que hay que aplicar a un asset."""

    policy: str
    source_width: int
    source_height: int
    target_width: int
    target_height: int
    pad_color: str
    already_applied: bool

    def describe(self) -> dict:
        return {
            "policy": self.policy,
            "source": f"{self.source_width}x{self.source_height}",
            "target": f"{self.target_width}x{self.target_height}",
            "pad_color": self.pad_color,
            "already_applied": self.already_applied,
        }


def geometry_filters(plan: GeometryPlan) -> list[str]:
    """Filtros de encuadre. Conserva la proporcion: NUNCA deforma.

    `contain` escala hasta caber entero y rellena los lados; `crop` escala
    hasta cubrir y recorta el sobrante centrado. No hay una tercera opcion que
    estire, porque estirar se nota y no se puede deshacer.
    """
    if plan.already_applied:
        # El modulo 3 ya incorporo esta transformacion a un derivado. Repetirla
        # volveria a escalar una imagen ya escalada.
        return [f"scale={plan.target_width}:{plan.target_height}:flags=lanczos"]

    ancho, alto = plan.target_width, plan.target_height
    if plan.policy == "contain":
        return [
            # `force_original_aspect_ratio=decrease` cabe entero; el `pad`
            # centrado completa hasta el lienzo.
            f"scale={ancho}:{alto}:force_original_aspect_ratio=decrease:flags=lanczos",
            f"pad={ancho}:{alto}:(ow-iw)/2:(oh-ih)/2:color={_ass_color_to_ffmpeg(plan.pad_color)}",
        ]
    if plan.policy == "crop":
        return [
            f"scale={ancho}:{alto}:force_original_aspect_ratio=increase:flags=lanczos",
            f"crop={ancho}:{alto}",
        ]
    raise VideoBuildError(
        f"politica de geometria desconocida: {plan.policy!r}. Solo se admiten "
        "contain y crop; no se interpretan expresiones arbitrarias.",
        details={"policy": plan.policy},
    )


def _ass_color_to_ffmpeg(color: str) -> str:
    """`#RRGGBB` -> `0xRRGGBB`. Rechaza cualquier otra cosa."""
    texto = color.strip()
    if texto.startswith("#"):
        texto = texto[1:]
    if len(texto) != 6 or any(c not in "0123456789abcdefABCDEF" for c in texto):
        raise VideoBuildError(
            f"color de relleno no valido: {color!r}. Se espera #RRGGBB.",
            details={"pad_color": color},
        )
    return f"0x{texto}"


def camera_filters(
    move: str, *, frames: int, fps: int, width: int, height: int
) -> list[str]:
    """Movimiento de camara determinista sobre una imagen fija.

    Nunca cambia la DURACION: el numero de fotogramas es el que exige el reloj.
    El zoom se hace escalando de mas y recortando la ventana con `crop`, con un
    desplazamiento lineal por fotograma: es reproducible y facil de comprobar.
    """
    if move not in CAMERA_MOVES:
        raise VideoBuildError(
            f"movimiento de camara desconocido: {move!r}. Admitidos: "
            + ", ".join(CAMERA_MOVES),
            details={"camera_move": move},
        )
    if move == "static" or frames <= 1:
        return []

    # `n` es el indice de fotograma dentro del segmento. El factor va de 1 a
    # MAX_ZOOM (o al reves) de forma lineal y acotada.
    ultimo = max(frames - 1, 1)
    if move == "zoom_in":
        progreso = f"(n/{ultimo})"
    else:
        progreso = f"(1-n/{ultimo})"
    delta = MAX_ZOOM - 1.0
    ancho_visible = f"({width}/(1+{delta:.4f}*{progreso}))"
    alto_visible = f"({height}/(1+{delta:.4f}*{progreso}))"
    return [
        # Se amplia una sola vez al maximo y se recorta una ventana movil: asi
        # el recorte nunca sale del lienzo y no hay reescalados encadenados.
        f"scale={int(width * MAX_ZOOM)}:{int(height * MAX_ZOOM)}:flags=lanczos",
        f"crop=w='{ancho_visible}*{MAX_ZOOM}':h='{alto_visible}*{MAX_ZOOM}'"
        f":x='(iw-ow)/2':y='(ih-oh)/2'",
        f"scale={width}:{height}:flags=lanczos",
    ]


def choose_camera_move(*, caption_style: str, policy: str, asset_type: str) -> str:
    """Valor inicial del movimiento, por estilo y por geometria.

    * Las escenas de video conservan SU movimiento original y no reciben otro.
    * `contain` se queda estatico: un zoom recortaria el relleno lateral.
    * El estilo dinamico puede permitirse un zoom leve cuando hay recorte.
    """
    if asset_type == "video":
        return "static"
    if policy != "crop":
        return "static"
    return "zoom_in" if caption_style == "dynamic_emphasis" else "static"


# ---------------------------------------------------------------------------
# Segmentos
# ---------------------------------------------------------------------------


@dataclass
class SegmentSpec:
    """Todo lo que define un segmento. Su hash entra en el checkpoint."""

    scene_id: str
    order: int
    source_path: Path
    asset_type: str
    frames: int
    start_frame: int
    geometry: GeometryPlan
    camera_move: str
    clip_from_s: float | None
    clip_to_s: float | None
    captions_path: Path
    fonts_dir: Path

    def identity(self) -> dict:
        return {
            "scene_id": self.scene_id,
            "asset_type": self.asset_type,
            "frames": self.frames,
            "start_frame": self.start_frame,
            "geometry": self.geometry.describe(),
            "camera_move": self.camera_move,
            "clip_from_s": self.clip_from_s,
            "clip_to_s": self.clip_to_s,
        }


def segment_args(
    spec: SegmentSpec,
    *,
    runner: ProcessRunner,
    settings: EncodeSettings,
    destination: Path,
) -> list[str]:
    """Argumentos de FFmpeg para UN segmento, sin audio.

    Los subtitulos se integran aqui. Para que los tiempos del ASS sean los
    GLOBALES y no los del segmento se desplazan los PTS al inicio global de la
    escena, se aplica `ass` y se devuelve el segmento a t=0. Asi un evento que
    cae justo en una frontera de escena se dibuja donde le toca, y ninguna
    animacion se reinicia por accidente.
    """
    desplazamiento = spec.start_frame / settings.fps
    entrada: list[str]
    cadena: list[str] = []

    if spec.asset_type == "image":
        entrada = [
            "-loop", "1",
            "-framerate", f"{settings.fps}/1",
            "-i", str(spec.source_path),
        ]
        cadena += geometry_filters(spec.geometry)
        cadena += camera_filters(
            spec.camera_move,
            frames=spec.frames,
            fps=settings.fps,
            width=settings.width,
            height=settings.height,
        )
    else:
        entrada = []
        if spec.clip_from_s:
            entrada += ["-ss", f"{spec.clip_from_s:.6f}"]
        entrada += ["-i", str(spec.source_path)]
        # La conversion de fps puede repetir o descartar fotogramas; eso NO
        # cambia la velocidad narrativa del clip.
        cadena.append(f"fps={settings.fps}")
        cadena += geometry_filters(spec.geometry)

    cadena += [
        "setsar=1/1",
        "format=yuv420p",
        # Reloj global -> ASS -> vuelta a t=0.
        f"setpts=PTS+{desplazamiento:.6f}/TB",
        f"ass=filename={_escape_filter_path(spec.captions_path)}"
        f":fontsdir={_escape_filter_path(spec.fonts_dir)}",
        f"setpts=PTS-{desplazamiento:.6f}/TB",
    ]

    return [
        *runner.base_args(),
        *entrada,
        "-vf", ",".join(cadena),
        # `-frames:v` fija la cuenta EXACTA: ni uno mas ni uno menos.
        "-frames:v", str(spec.frames),
        "-an",
        *settings.x264_args(),
        "-movflags", "+write_colr",
        str(destination),
    ]


def _escape_filter_path(path: Path) -> str:
    """Escapa una ruta para el interior de un filtro de FFmpeg.

    Dentro de un filtergraph, `:` separa opciones, `,` separa filtros y `'`
    delimita. Las rutas del paquete son nuestras y se validan antes, pero se
    escapan igualmente: el escape es la defensa, no la confianza.
    """
    texto = str(path)
    for caracter in ("\\", "'", ":", ",", "[", "]", ";"):
        texto = texto.replace(caracter, "\\" + caracter)
    return texto


# ---------------------------------------------------------------------------
# Concatenacion y mux
# ---------------------------------------------------------------------------


def write_concat_list(segments: list[Path], destination: Path) -> None:
    """Escribe la lista del demuxer `concat`.

    Se usan nombres RELATIVOS dentro del propio directorio y FFmpeg se ejecuta
    con ese `cwd`: asi la lista no puede referirse a rutas de fuera ni a una
    URL, y no hace falta `-safe 0`.
    """
    lineas = []
    for segmento in segments:
        nombre = segmento.name
        if "'" in nombre or "\n" in nombre or "/" in nombre:
            raise VideoBuildError(
                f"nombre de segmento no admitido en una lista concat: {nombre!r}",
                details={"name": nombre},
            )
        lineas.append(f"file '{nombre}'")
    destination.write_text("\n".join(lineas) + "\n", encoding="utf-8")


def mux_args(
    *,
    runner: ProcessRunner,
    concat_list: Path,
    audio_path: Path,
    destination: Path,
    settings: EncodeSettings,
    total_frames: int,
) -> list[str]:
    """Concatena por COPIA del stream visual y multiplexa el audio.

    Copiar exige que todos los segmentos compartan propiedades; como los
    produce este mismo modulo con los mismos ajustes, lo hacen. Recodificar
    aqui seria una segunda generacion de perdidas sin motivo.

    No se usa `-shortest`: la duracion sale del calculo de fotogramas, y
    `-shortest` taparia una pista truncada en vez de delatarla.
    """
    return [
        *runner.base_args(),
        "-f", "concat",
        # `-safe 1` (por defecto) exige nombres relativos simples: es la
        # defensa contra una lista que intente abrir otra cosa.
        "-safe", "1",
        "-protocol_whitelist", "file",
        "-i", concat_list.name,
        "-i", str(audio_path),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac",
        "-profile:a", "aac_low",
        "-b:a", settings.audio_bitrate,
        "-ar", str(WORK_SAMPLE_RATE_HZ),
        "-ac", "2",
        "-frames:v", str(total_frames),
        "-video_track_timescale", str(VIDEO_TIMESCALE),
        # Orientacion neutra: el video YA es vertical, no se rota al mostrarlo.
        "-metadata:s:v:0", "rotate=0",
        "-movflags", "+faststart",
        "-f", "mp4",
        str(destination),
    ]
