"""Calculo local de duraciones. El LLM no es autoridad sobre los tiempos.

Formula por escena, con la pausa SIEMPRE al final de la escena:

    estimated_duration_s = 60 * word_count / target_wpm + pause_after_s

Los tiempos acumulados empiezan en 0 y se encadenan sin huecos ni
solapamientos: ``estimated_start_s`` de la escena N es ``estimated_end_s`` de
la escena N-1.

Todos los valores se redondean a 3 decimales al almacenarse, pero la cadena
acumulada se construye con la suma exacta para que el ultimo ``estimated_end_s``
coincida con la duracion total estimada.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Duracion plausible de una escena suelta (heuristica de control de calidad).
MIN_SCENE_DURATION_S = 1.2
MAX_SCENE_DURATION_S = 15.0

#: El gancho debe durar aproximadamente tres segundos o menos.
MAX_HOOK_DURATION_S = 3.0

#: Tolerancia sobre la duracion objetivo del perfil.
DURATION_TOLERANCE = 0.10


@dataclass(frozen=True)
class SceneTiming:
    word_count: int
    pause_after_s: float
    start_s: float
    end_s: float
    duration_s: float


def speech_seconds(word_count: int, target_wpm: int) -> float:
    """Segundos de locucion de `word_count` palabras al ritmo indicado."""
    if target_wpm <= 0:
        raise ValueError("target_wpm debe ser mayor que cero")
    return 60.0 * word_count / target_wpm


def scene_duration(word_count: int, target_wpm: int, pause_after_s: float) -> float:
    return speech_seconds(word_count, target_wpm) + pause_after_s


def build_timeline(
    word_counts: list[int], pauses: list[float], target_wpm: int
) -> list[SceneTiming]:
    """Encadena las escenas desde el segundo 0, sin huecos ni solapamientos."""
    if len(word_counts) != len(pauses):
        raise ValueError("word_counts y pauses deben tener la misma longitud")
    timeline: list[SceneTiming] = []
    cursor = 0.0
    for count, pause in zip(word_counts, pauses, strict=True):
        duration = scene_duration(count, target_wpm, pause)
        start = cursor
        end = cursor + duration
        timeline.append(
            SceneTiming(
                word_count=count,
                pause_after_s=round(pause, 3),
                start_s=round(start, 3),
                end_s=round(end, 3),
                duration_s=round(duration, 3),
            )
        )
        cursor = end
    return timeline


def total_duration(timeline: list[SceneTiming]) -> float:
    if not timeline:
        return 0.0
    return round(timeline[-1].end_s, 3)


def words_budget(target_duration_s: float, target_wpm: int, total_pause_s: float) -> int:
    """Palabras que caben en la duracion objetivo descontando las pausas."""
    speech = max(target_duration_s - total_pause_s, 0.0)
    return max(1, round(speech * target_wpm / 60.0))


def within_tolerance(estimated_s: float, target_s: float, tolerance: float = DURATION_TOLERANCE) -> bool:
    return abs(estimated_s - target_s) <= target_s * tolerance
