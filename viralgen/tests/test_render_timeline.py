"""Reloj global y cuantizacion a fotogramas.

La regla que se comprueba aqui: se convierten los LIMITES ACUMULADOS, nunca
cada duracion por separado. Redondear duraciones sueltas acumula error y
produce un video mas largo que la narracion.
"""

from __future__ import annotations

import pytest

from viralgen.errors import DocumentValidationError
from viralgen.render.timeline import (
    build_render_timeline,
    ceil_div,
    samples_to_frame,
)

S = 24_000
F = 30


def _bounds(*segundos: float) -> list[tuple[str, int, int, int]]:
    """Fronteras en segundos -> tuplas (scene_id, order, inicio, fin)."""
    muestras = [round(valor * S) for valor in segundos]
    return [
        (f"sc_{indice + 1:02d}", indice + 1, muestras[indice], muestras[indice + 1])
        for indice in range(len(muestras) - 1)
    ]


def test_ceil_entero_no_usa_coma_flotante() -> None:
    # A 48 kHz y varios minutos, math.ceil(a/b) puede caer del lado equivocado
    # de un entero exacto. Con enteros nunca pasa.
    assert ceil_div(48_000 * 30, 48_000) == 30
    assert ceil_div(1, 30) == 1
    assert ceil_div(0, 30) == 0
    assert ceil_div(-(-1), 30) == 1
    # Un valor que es exactamente entero NO se pasa al siguiente fotograma.
    assert samples_to_frame(24_000, fps=30, sample_rate_hz=24_000) == 30


def test_el_ejemplo_de_aceptacion_da_91_fotogramas() -> None:
    """Fronteras 0 / 1,01 / 2,02 / 3,03 s -> 0 / 31 / 61 / 91 a 30 fps."""
    linea = build_render_timeline(
        scene_bounds=_bounds(0, 1.01, 2.02, 3.03),
        sample_rate_hz=S,
        sample_count=round(3.03 * S),
        fps=F,
    )
    assert [escena.start_frame for escena in linea.scenes] == [0, 31, 61]
    assert linea.scenes[-1].end_frame == 91
    assert [escena.frames for escena in linea.scenes] == [31, 30, 30]
    assert linea.total_frames == 91

    # Redondear cada duracion por separado daria 93: es el error que se evita.
    por_separado = sum(round(1.01 * F) + 1 for _ in range(3))
    assert por_separado == 93 != linea.total_frames


def test_las_escenas_particionan_el_video_sin_huecos() -> None:
    linea = build_render_timeline(
        scene_bounds=_bounds(0, 1.01, 2.02, 3.03),
        sample_rate_hz=S,
        sample_count=round(3.03 * S),
        fps=F,
    )
    cursor = 0
    for escena in linea.scenes:
        assert escena.start_frame == cursor
        assert escena.frames >= 1
        cursor = escena.end_frame
    assert cursor == linea.total_frames


def test_el_exceso_visual_siempre_cabe_en_un_fotograma() -> None:
    """El sobrante final esta en [0, 1/F). Es la cuantizacion documentada."""
    for total_s in (3.03, 25.469667, 7.0, 12.3456, 0.5):
        muestras = round(total_s * S)
        linea = build_render_timeline(
            scene_bounds=_bounds(0, total_s),
            sample_rate_hz=S,
            sample_count=muestras,
            fps=F,
        )
        assert 0 <= linea.quantization_excess_s < 1 / F
        assert linea.visual_duration_s >= linea.narration_duration_s


def test_una_duracion_exacta_no_anade_fotograma() -> None:
    """Si la narracion cae justo en un fotograma, no sobra nada."""
    linea = build_render_timeline(
        scene_bounds=_bounds(0, 2.0),
        sample_rate_hz=S,
        sample_count=2 * S,
        fps=F,
    )
    assert linea.total_frames == 60
    assert linea.quantization_excess_s == 0.0


def test_la_primera_escena_empieza_en_cero_y_la_ultima_cierra_el_total() -> None:
    linea = build_render_timeline(
        scene_bounds=_bounds(0, 0.7, 1.9, 4.05),
        sample_rate_hz=S,
        sample_count=round(4.05 * S),
        fps=F,
    )
    assert linea.scenes[0].start_frame == 0
    assert linea.scenes[-1].end_frame == linea.total_frames


def test_los_desplazamientos_quedan_registrados_y_acotados() -> None:
    """Cada frontera dice cuanto se desplazo respecto del reloj de voz."""
    linea = build_render_timeline(
        scene_bounds=_bounds(0, 1.01, 2.02, 3.03),
        sample_rate_hz=S,
        sample_count=round(3.03 * S),
        fps=F,
    )
    for escena in linea.scenes:
        # Con `ceil` el fotograma nunca empieza ANTES que la muestra.
        assert 0 <= escena.start_offset_s < 1 / F
        assert "start_offset_s" in escena.describe()
    assert linea.scenes[1].start_offset_s == pytest.approx(31 / 30 - 1.01, abs=1e-9)


def test_las_pausas_ya_estan_en_los_limites_y_no_se_suman_otra_vez() -> None:
    """Los limites de voz incluyen la pausa: el video no anade otra."""
    # Escena de 1 s de habla + 0,25 s de pausa -> la frontera esta en 1,25 s.
    linea = build_render_timeline(
        scene_bounds=_bounds(0, 1.25, 2.5),
        sample_rate_hz=S,
        sample_count=round(2.5 * S),
        fps=F,
    )
    assert linea.scenes[0].frames == 38          # ceil(1,25*30) = 38
    assert linea.total_frames == 75              # 2,5 s exactos
    assert sum(escena.frames for escena in linea.scenes) == 75


def test_una_escena_mas_corta_que_un_fotograma_es_un_fallo() -> None:
    """Cero fotogramas significa una escena que desaparece sin avisar.

    Con `ceil` en las dos fronteras, una escena colapsa solo si AMBAS caen
    dentro del mismo intervalo de fotograma: por eso no basta con que sea
    corta, tiene que empezar justo despues de una frontera.
    """
    bordes = [
        ("sc_01", 1, 0, S + 1),          # termina 1 muestra pasado el fotograma 30
        ("sc_02", 2, S + 1, S + 10),     # 9 muestras dentro del mismo fotograma
        ("sc_03", 3, S + 10, 2 * S),
    ]
    with pytest.raises(DocumentValidationError, match="cero fotogramas"):
        build_render_timeline(
            scene_bounds=bordes, sample_rate_hz=S, sample_count=2 * S, fps=F
        )


def test_un_hueco_en_el_reloj_de_voz_se_rechaza() -> None:
    bordes = [("sc_01", 1, 0, S), ("sc_02", 2, S + 100, 2 * S)]
    with pytest.raises(DocumentValidationError, match="hueco o solapamiento"):
        build_render_timeline(
            scene_bounds=bordes, sample_rate_hz=S, sample_count=2 * S, fps=F
        )


def test_las_escenas_deben_cubrir_el_maestro_entero() -> None:
    bordes = [("sc_01", 1, 0, S)]
    with pytest.raises(DocumentValidationError, match="muestras"):
        build_render_timeline(
            scene_bounds=bordes, sample_rate_hz=S, sample_count=3 * S, fps=F
        )


def test_el_orden_manda_sobre_el_orden_de_la_lista() -> None:
    """Las escenas llegan desordenadas y se cuantizan igual."""
    bordes = _bounds(0, 1.01, 2.02, 3.03)
    linea = build_render_timeline(
        scene_bounds=list(reversed(bordes)),
        sample_rate_hz=S,
        sample_count=round(3.03 * S),
        fps=F,
    )
    assert [escena.scene_id for escena in linea.scenes] == ["sc_01", "sc_02", "sc_03"]
    assert [escena.frames for escena in linea.scenes] == [31, 30, 30]


def test_un_clip_justo_en_el_limite_temporal_cubre_su_escena() -> None:
    """Un clip que dura EXACTAMENTE lo que su escena sigue siendo valido.

    El cierre de fotograma (<1/F) no cuenta como duracion que el clip deba
    aportar: el ultimo fotograma sostiene la ultima muestra visual.
    """
    duracion_escena_s = 5.0
    linea = build_render_timeline(
        scene_bounds=_bounds(0, duracion_escena_s),
        sample_rate_hz=S,
        sample_count=round(duracion_escena_s * S),
        fps=F,
    )
    escena = linea.scenes[0]
    assert escena.frames == 150
    # El clip aporta justo 5,000 s y el segmento pide 150/30 = 5,000 s.
    assert escena.frames / F == pytest.approx(duracion_escena_s, abs=1e-9)
    assert escena.frames / F <= duracion_escena_s + 1e-9
