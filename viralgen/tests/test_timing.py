"""Duraciones: formula, cadena de tiempos y presupuesto de palabras."""

from __future__ import annotations

import pytest

from viralgen.timing import (
    build_timeline,
    scene_duration,
    speech_seconds,
    total_duration,
    within_tolerance,
    words_budget,
)


def test_formula_de_escena() -> None:
    # 125 palabras a 125 wpm = 60 s, mas 0,5 s de pausa final.
    assert scene_duration(125, 125, 0.5) == pytest.approx(60.5)
    assert speech_seconds(0, 155) == 0.0


def test_wpm_invalido() -> None:
    with pytest.raises(ValueError):
        speech_seconds(10, 0)


def test_cadena_sin_huecos_ni_solapamientos() -> None:
    timeline = build_timeline([10, 20, 30], [0.3, 0.3, 0.5], 125)
    assert timeline[0].start_s == 0.0
    for anterior, siguiente in zip(timeline, timeline[1:], strict=False):
        assert siguiente.start_s == anterior.end_s
    assert total_duration(timeline) == timeline[-1].end_s


def test_longitudes_incoherentes() -> None:
    with pytest.raises(ValueError):
        build_timeline([10, 20], [0.3], 125)


def test_presupuesto_de_palabras_descuenta_pausas() -> None:
    # 50 s objetivo, 2,3 s de pausas, 125 wpm -> 47,7 s de locucion.
    assert words_budget(50, 125, 2.3) == round(47.7 * 125 / 60)
    assert words_budget(1, 125, 10) == 1  # nunca baja de una palabra


def test_tolerancia() -> None:
    assert within_tolerance(45.0, 50.0)
    assert not within_tolerance(44.0, 50.0)
