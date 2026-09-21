"""Tiempos medidos del modulo 2, en muestras enteras.

Regla central: el maestro se mide en MUESTRAS y los segundos se derivan al
exportar. No se acumulan segundos redondeados escena a escena, porque el error
se arrastraria hasta el final de la pieza.

Con `sample_rate = 24000`, para cada escena:

* ``clip_samples``  — muestras del clip decodificado, incluidos los silencios
  naturales de la grabacion.
* ``pause_samples`` — redondeo de ``pause_after_s x sample_rate``.
* ``start_sample``  — suma de ``clip_samples + pause_samples`` de las escenas
  anteriores.
* ``end_sample``    — ``start_sample + clip_samples + pause_samples`` (extremo
  EXCLUSIVO).
* ``start_s = start_sample / sample_rate``
* ``clip_end_s = (start_sample + clip_samples) / sample_rate``
* ``end_s = end_sample / sample_rate``
* ``actual_duration_s = (clip_samples + pause_samples) / sample_rate``

La pausa explicita del guion se anade como silencio EXACTAMENTE UNA VEZ,
tambien despues de la ultima escena si el guion la declara. Es adicional a las
respiraciones naturales del clip y no se vuelve a sumar en el montaje.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Misma tolerancia que el modulo 1: +-10 % sobre la duracion objetivo.
DURATION_TOLERANCE = 0.10


@dataclass(frozen=True)
class MeasuredScene:
    scene_id: str
    order: int
    clip_samples: int
    pause_samples: int
    start_sample: int

    @property
    def end_sample(self) -> int:
        return self.start_sample + self.clip_samples + self.pause_samples

    @property
    def total_samples(self) -> int:
        return self.clip_samples + self.pause_samples

    def start_s(self, sample_rate_hz: int) -> float:
        return self.start_sample / sample_rate_hz

    def clip_end_s(self, sample_rate_hz: int) -> float:
        return (self.start_sample + self.clip_samples) / sample_rate_hz

    def end_s(self, sample_rate_hz: int) -> float:
        return self.end_sample / sample_rate_hz

    def actual_duration_s(self, sample_rate_hz: int) -> float:
        return self.total_samples / sample_rate_hz


def build_measured_timeline(
    scenes: list[tuple[str, int, int, int]],
) -> list[MeasuredScene]:
    """Encadena escenas desde la muestra 0, sin huecos ni solapamientos.

    `scenes` es una lista de (scene_id, order, clip_samples, pause_samples).
    """
    resultado: list[MeasuredScene] = []
    cursor = 0
    for scene_id, order, clip_samples, pause_samples in scenes:
        if clip_samples <= 0:
            raise ValueError(f"la escena {scene_id} no tiene muestras de audio")
        if pause_samples < 0:
            raise ValueError(f"la escena {scene_id} tiene una pausa negativa")
        medida = MeasuredScene(
            scene_id=scene_id,
            order=order,
            clip_samples=clip_samples,
            pause_samples=pause_samples,
            start_sample=cursor,
        )
        resultado.append(medida)
        cursor = medida.end_sample
    return resultado


def total_samples(timeline: list[MeasuredScene]) -> int:
    return timeline[-1].end_sample if timeline else 0
