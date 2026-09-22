"""Reloj global y cuantizacion a fotogramas.

El reloj principal es `voice.json`: `sample_rate_hz = S` y `sample_count = N`
del maestro. La duracion de narracion es `T = N / S`. Las pausas del guion YA
estan incorporadas al maestro y a los limites de escena: no se vuelven a sumar.

La regla que sostiene todo el modulo: se convierten los LIMITES ACUMULADOS,
nunca cada duracion por separado.

    start_frame = ceil(start_sample * F / S)
    end_frame   = ceil(end_sample   * F / S)      # extremo exclusivo
    scene_frames = end_frame - start_frame
    total_frames = ceil(N * F / S)

Redondear cada duracion aisladamente acumula error. Con fronteras en
0 / 1,01 / 2,02 / 3,03 s a 30 fps el resultado correcto es 0 / 31 / 61 / 91,
es decir segmentos de 31, 30 y 30 fotogramas; redondear por separado daria 93.

El `ceil` se calcula con enteros (`-(-a // b)`), sin coma flotante: a 48 kHz y
varios minutos, `math.ceil(a / b)` puede caer del lado equivocado de un entero
exacto por error de representacion.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..errors import DocumentValidationError as ValidationError


def ceil_div(numerator: int, denominator: int) -> int:
    """`ceil(numerator / denominator)` con enteros exactos."""
    if denominator <= 0:
        raise ValueError("el denominador debe ser positivo")
    return -(-numerator // denominator)


def samples_to_frame(sample: int, *, fps: int, sample_rate_hz: int) -> int:
    """Frontera de fotograma de una posicion en muestras."""
    if sample < 0:
        raise ValueError("una posicion en muestras no puede ser negativa")
    return ceil_div(sample * fps, sample_rate_hz)


@dataclass(frozen=True)
class FrameScene:
    """Escena ya cuantizada. Conserva su origen en muestras."""

    scene_id: str
    order: int
    start_sample: int
    end_sample: int
    start_frame: int
    end_frame: int
    sample_rate_hz: int
    fps: int

    @property
    def frames(self) -> int:
        return self.end_frame - self.start_frame

    @property
    def start_s(self) -> float:
        """Inicio segun el RELOJ DE VOZ (muestras), no segun el fotograma."""
        return self.start_sample / self.sample_rate_hz

    @property
    def end_s(self) -> float:
        return self.end_sample / self.sample_rate_hz

    @property
    def frame_start_s(self) -> float:
        """Inicio segun el reloj de VIDEO, ya cuantizado."""
        return self.start_frame / self.fps

    @property
    def frame_duration_s(self) -> float:
        return self.frames / self.fps

    @property
    def start_offset_s(self) -> float:
        """Desplazamiento de la frontera de entrada respecto del reloj de voz.

        Positivo: el fotograma empieza DESPUES que la muestra. Con `ceil` el
        desplazamiento siempre esta en [0, 1/F).
        """
        return self.frame_start_s - self.start_s

    @property
    def end_offset_s(self) -> float:
        return self.end_frame / self.fps - self.end_s

    def describe(self) -> dict:
        return {
            "scene_id": self.scene_id,
            "order": self.order,
            "start_sample": self.start_sample,
            "end_sample": self.end_sample,
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
            "frames": self.frames,
            "voice_start_s": round(self.start_s, 6),
            "voice_end_s": round(self.end_s, 6),
            "frame_start_s": round(self.frame_start_s, 6),
            "frame_duration_s": round(self.frame_duration_s, 6),
            "start_offset_s": round(self.start_offset_s, 6),
            "end_offset_s": round(self.end_offset_s, 6),
        }


@dataclass
class RenderTimeline:
    """Particion exacta de [0, total_frames) en escenas."""

    scenes: list[FrameScene]
    fps: int
    sample_rate_hz: int
    sample_count: int
    total_frames: int
    warnings: list[str] = field(default_factory=list)

    @property
    def narration_duration_s(self) -> float:
        """T = N / S. Es la duracion de la VOZ, no la del video."""
        return self.sample_count / self.sample_rate_hz

    @property
    def visual_duration_s(self) -> float:
        """total_frames / F. Siempre >= narration_duration_s."""
        return self.total_frames / self.fps

    @property
    def quantization_excess_s(self) -> float:
        """Exceso visual final. Por construccion esta en [0, 1/F)."""
        return self.visual_duration_s - self.narration_duration_s

    def describe(self) -> dict:
        return {
            "sample_rate_hz": self.sample_rate_hz,
            "sample_count": self.sample_count,
            "narration_duration_s": round(self.narration_duration_s, 6),
            "fps": self.fps,
            "total_frames": self.total_frames,
            "visual_duration_s": round(self.visual_duration_s, 6),
            "quantization_excess_s": round(self.quantization_excess_s, 6),
            "max_frame_hold_s": round(1.0 / self.fps, 6),
            "scenes": [escena.describe() for escena in self.scenes],
        }


def build_render_timeline(
    *,
    scene_bounds: list[tuple[str, int, int, int]],
    sample_rate_hz: int,
    sample_count: int,
    fps: int,
) -> RenderTimeline:
    """Cuantiza las fronteras acumuladas de escena a fotogramas.

    `scene_bounds` son tuplas `(scene_id, order, start_sample, end_sample)` con
    `end_sample` EXCLUSIVO, tal como las publica `voice.json`.

    Se exige que las escenas de entrada ya formen una particion contigua de
    [0, sample_count): esa comprobacion es del validador de medios, pero aqui
    se repite porque un hueco produciria un video con un salto invisible.
    """
    if fps <= 0:
        raise ValidationError("fps debe ser positivo", details={"fps": fps})
    if sample_rate_hz <= 0 or sample_count <= 0:
        raise ValidationError(
            "el reloj de voz no es utilizable",
            details={"sample_rate_hz": sample_rate_hz, "sample_count": sample_count},
        )
    if not scene_bounds:
        raise ValidationError("no hay escenas que montar")

    ordenadas = sorted(scene_bounds, key=lambda item: item[1])
    total_frames = ceil_div(sample_count * fps, sample_rate_hz)

    escenas: list[FrameScene] = []
    cursor_muestras = 0
    for scene_id, order, start_sample, end_sample in ordenadas:
        if start_sample != cursor_muestras:
            raise ValidationError(
                f"hueco o solapamiento en el reloj de voz antes de {scene_id}: "
                f"empieza en {start_sample} y se esperaba {cursor_muestras}",
                details={"scene_id": scene_id},
            )
        if end_sample <= start_sample:
            raise ValidationError(
                f"{scene_id} no tiene duracion en el reloj de voz",
                details={"scene_id": scene_id},
            )
        escenas.append(
            FrameScene(
                scene_id=scene_id,
                order=order,
                start_sample=start_sample,
                end_sample=end_sample,
                start_frame=samples_to_frame(
                    start_sample, fps=fps, sample_rate_hz=sample_rate_hz
                ),
                end_frame=samples_to_frame(
                    end_sample, fps=fps, sample_rate_hz=sample_rate_hz
                ),
                sample_rate_hz=sample_rate_hz,
                fps=fps,
            )
        )
        cursor_muestras = end_sample

    if cursor_muestras != sample_count:
        raise ValidationError(
            f"las escenas cubren {cursor_muestras} muestras y el maestro declara "
            f"{sample_count}",
            details={"covered": cursor_muestras, "declared": sample_count},
        )

    _check_partition(escenas, total_frames)
    return RenderTimeline(
        scenes=escenas,
        fps=fps,
        sample_rate_hz=sample_rate_hz,
        sample_count=sample_count,
        total_frames=total_frames,
    )


def _check_partition(escenas: list[FrameScene], total_frames: int) -> None:
    """Las escenas deben particionar [0, total_frames) exactamente."""
    if escenas[0].start_frame != 0:
        raise ValidationError(
            f"la primera escena empieza en el fotograma {escenas[0].start_frame} "
            "y deberia empezar en 0"
        )
    cursor = 0
    for escena in escenas:
        if escena.start_frame != cursor:
            raise ValidationError(
                f"hueco o solapamiento de fotogramas antes de {escena.scene_id}: "
                f"empieza en {escena.start_frame} y se esperaba {cursor}",
                details={"scene_id": escena.scene_id},
            )
        if escena.frames <= 0:
            # Una escena de cero fotogramas desapareceria del video sin que
            # nada lo dijera: es un fallo, no un caso a tolerar.
            raise ValidationError(
                f"{escena.scene_id} se queda en cero fotogramas al cuantizar "
                f"({escena.end_s - escena.start_s:.6f} s a {escena.fps} fps). "
                "Una escena mas corta que un fotograma no se puede montar.",
                details={"scene_id": escena.scene_id, "frames": escena.frames},
            )
        cursor = escena.end_frame
    if cursor != total_frames:
        raise ValidationError(
            f"las escenas suman {cursor} fotogramas y el total es {total_frames}",
            details={"covered": cursor, "total_frames": total_frames},
        )
