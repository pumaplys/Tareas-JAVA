"""Reloj del proyecto para el modulo 3.

`voice.json` es la UNICA fuente de tiempos medidos. Para cada escena se copian
`start_sample` y `end_sample` (extremo final EXCLUSIVO) y se usa el
`sample_rate_hz` del maestro:

    duracion_visual_s = (end_sample - start_sample) / sample_rate_hz

Ese intervalo YA incluye la pausa explicita que contabilizo la voz: no se
vuelve a sumar. No se recalcula nada con palabras por minuto ni con las
estimaciones del guion.

Los tiempos se conservan como enteros de muestras y los segundos se derivan,
para no acumular redondeos. La cuantizacion a fotogramas es del modulo 4.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import ViralgenError


class MediaTimelineError(ViralgenError):
    exit_code = 5
    code = "media_timeline_error"


@dataclass(frozen=True)
class VisualScene:
    """Intervalo visual de una escena, en muestras del maestro de voz."""

    scene_id: str
    order: int
    start_sample: int
    end_sample: int
    sample_rate_hz: int
    asset_type: str

    @property
    def samples(self) -> int:
        return self.end_sample - self.start_sample

    @property
    def duration_s(self) -> float:
        return self.samples / self.sample_rate_hz

    @property
    def start_s(self) -> float:
        return self.start_sample / self.sample_rate_hz

    @property
    def end_s(self) -> float:
        return self.end_sample / self.sample_rate_hz


@dataclass(frozen=True)
class VisualTimeline:
    scenes: list[VisualScene]
    sample_rate_hz: int
    total_samples: int

    @property
    def total_duration_s(self) -> float:
        return self.total_samples / self.sample_rate_hz

    def by_id(self) -> dict[str, VisualScene]:
        return {escena.scene_id: escena for escena in self.scenes}


def build_visual_timeline(manifest, document) -> VisualTimeline:
    """Construye la cobertura visual a partir del manifiesto de voz.

    Exige una entrada por escena, en el mismo orden que el guion, encadenadas
    desde la muestra cero hasta `master.sample_count`, sin huecos ni
    solapamientos.
    """
    sample_rate = manifest.master.sample_rate_hz
    total = manifest.master.sample_count
    if sample_rate <= 0 or total <= 0:
        raise MediaTimelineError("El maestro de voz declara un reloj invalido.")

    escenas_guion = {escena.scene_id: escena for escena in document.scenes}
    escenas_voz = sorted(manifest.scenes, key=lambda item: item.order)
    if [escena.scene_id for escena in escenas_voz] != [
        escena.scene_id for escena in sorted(document.scenes, key=lambda item: item.order)
    ]:
        raise MediaTimelineError(
            "Las escenas de voice.json no coinciden en identidad u orden con las del guion."
        )

    resultado: list[VisualScene] = []
    cursor = 0
    for escena in escenas_voz:
        if escena.start_sample != cursor:
            raise MediaTimelineError(
                f"Hueco o solapamiento antes de {escena.scene_id}: empieza en "
                f"{escena.start_sample} y se esperaba {cursor}."
            )
        if escena.end_sample <= escena.start_sample:
            raise MediaTimelineError(f"{escena.scene_id} tiene un intervalo vacio.")
        fuente = escenas_guion[escena.scene_id]
        resultado.append(
            VisualScene(
                scene_id=escena.scene_id,
                order=escena.order,
                start_sample=escena.start_sample,
                end_sample=escena.end_sample,
                sample_rate_hz=sample_rate,
                asset_type=fuente.visual.asset_type.value,
            )
        )
        cursor = escena.end_sample

    if cursor != total:
        raise MediaTimelineError(
            f"Las escenas cubren {cursor} muestras y el maestro declara {total}."
        )
    return VisualTimeline(scenes=resultado, sample_rate_hz=sample_rate, total_samples=total)
