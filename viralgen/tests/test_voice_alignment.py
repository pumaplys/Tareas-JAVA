"""Alineacion por palabra: correspondencia, agrupacion y limites."""

from __future__ import annotations

import pytest

from viralgen.voice.alignment import (
    CharAlignment,
    build_alignment,
    emphasis_set,
    is_emphasis,
    text_matches,
    validate_char_alignment,
    word_spans_of,
)
from viralgen.voice.schemas import AlignmentMethod, AlignmentStatus

METODO = AlignmentMethod.PROVIDER_ALIGNMENT


def _uniforme(texto: str, paso: float = 0.1) -> CharAlignment:
    """Alineacion sintetica SOLO para las pruebas de agrupacion."""
    n = len(texto)
    return CharAlignment(
        list(texto), [i * paso for i in range(n)], [(i + 1) * paso for i in range(n)]
    )


# ---------------------------------------------------------------------------
# Correspondencia con el texto fuente
# ---------------------------------------------------------------------------


def test_igualdad_exacta() -> None:
    assert text_matches("Hola mundo", "Hola mundo") == (True, "exacta")


def test_normalizacion_unicode_reversible() -> None:
    combinado = "canción"
    precompuesto = "canción"
    ok, detalle = text_matches(combinado, precompuesto)
    assert ok and detalle == "nfc"


def test_espacios_equivalentes() -> None:
    ok, _ = text_matches("Hola mundo", "Hola mundo")
    assert ok


def test_normalizacion_no_equivalente_se_rechaza() -> None:
    """No se asume que '12 km' y 'doce kilometros' se correspondan."""
    ok, motivo = text_matches("doce kilometros", "12 km")
    assert not ok and "longitud" in motivo

    ok, motivo = text_matches("Hola mundo", "Hola mundi")
    assert not ok and "no son espacios" in motivo


def test_una_normalizacion_no_equivalente_bloquea() -> None:
    texto = "Son 12 km de camino."
    alineacion = _uniforme("Son doce kilometros de camino.")
    resultado = build_alignment(
        alineacion, texto, clip_duration_s=10.0, tolerance_s=0.02, method=METODO
    )
    assert resultado.status is AlignmentStatus.REJECTED
    assert not resultado.usable
    assert "no corresponde" in resultado.issues[0]


# ---------------------------------------------------------------------------
# Validacion estructural
# ---------------------------------------------------------------------------


def test_arrays_de_distinto_tamano() -> None:
    mala = CharAlignment(["a", "b"], [0.0], [0.1, 0.2])
    problemas = validate_char_alignment(mala, clip_duration_s=1.0, tolerance_s=0.02)
    assert problemas and "no miden lo mismo" in problemas[0]


def test_valores_no_finitos() -> None:
    mala = CharAlignment(["a"], [float("nan")], [0.1])
    problemas = validate_char_alignment(mala, clip_duration_s=1.0, tolerance_s=0.02)
    assert any("no finito" in problema for problema in problemas)


def test_tiempos_que_retroceden() -> None:
    mala = CharAlignment(["a", "b"], [0.5, 0.1], [0.6, 0.2])
    problemas = validate_char_alignment(mala, clip_duration_s=1.0, tolerance_s=0.02)
    assert any("retroceden" in problema for problema in problemas)


def test_timestamps_fuera_de_los_limites_del_wav() -> None:
    texto = "Hola mundo"
    alineacion = _uniforme(texto)  # termina en 1,0 s
    resultado = build_alignment(
        alineacion, texto, clip_duration_s=0.5, tolerance_s=0.02, method=METODO
    )
    assert resultado.status is AlignmentStatus.REJECTED
    assert any("termina en" in problema for problema in resultado.issues)


def test_desviacion_dentro_de_tolerancia_se_ajusta_y_se_registra() -> None:
    texto = "Hola"
    alineacion = _uniforme(texto)  # termina en 0,4 s
    resultado = build_alignment(
        alineacion, texto, clip_duration_s=0.39, tolerance_s=0.02, method=METODO
    )
    assert resultado.usable
    assert resultado.words[-1].end_s == pytest.approx(0.39)
    assert any("ajustado" in ajuste for ajuste in resultado.adjustments)


# ---------------------------------------------------------------------------
# Agrupacion en palabras
# ---------------------------------------------------------------------------


def test_la_puntuacion_se_conserva_en_el_texto_mostrado() -> None:
    texto = "Hola, mundo bonito."
    resultado = build_alignment(
        _uniforme(texto), texto, clip_duration_s=10.0, tolerance_s=0.02, method=METODO
    )
    assert [palabra.text for palabra in resultado.words] == ["Hola,", "mundo", "bonito."]


def test_los_espacios_no_tienen_duracion_propia() -> None:
    texto = "uno dos"
    resultado = build_alignment(
        _uniforme(texto), texto, clip_duration_s=10.0, tolerance_s=0.02, method=METODO
    )
    primera, segunda = resultado.words
    # El hueco entre palabras es el espacio: no pertenece a ninguna.
    assert primera.end_s < segunda.start_s


def test_un_signo_suelto_se_une_a_la_palabra_anterior() -> None:
    texto = "mundo — bonito"
    assert [texto[a:b] for a, b in word_spans_of(texto)] == ["mundo —", "bonito"]


def test_un_signo_suelto_inicial_se_une_a_la_siguiente() -> None:
    texto = "— mundo bonito"
    assert [texto[a:b] for a, b in word_spans_of(texto)] == ["— mundo", "bonito"]


def test_indices_en_puntos_de_codigo_no_en_bytes() -> None:
    texto = "añañá camión"
    resultado = build_alignment(
        _uniforme(texto), texto, clip_duration_s=10.0, tolerance_s=0.02, method=METODO
    )
    segunda = resultado.words[1]
    assert texto[segunda.char_start:segunda.char_end] == "camión"
    # "añañá " son 6 puntos de codigo pero 9 bytes en UTF-8.
    assert segunda.char_start == 6
    assert len(texto[: segunda.char_start].encode("utf-8")) == 9


# ---------------------------------------------------------------------------
# Enfasis
# ---------------------------------------------------------------------------


def test_el_enfasis_usa_la_normalizacion_documentada() -> None:
    destacadas = emphasis_set(["Compartir", "camión"])
    assert is_emphasis("compartir,", destacadas)
    assert is_emphasis("¡CAMION!", destacadas)
    assert not is_emphasis("compartimos", destacadas)


def test_alineacion_vacia() -> None:
    problemas = validate_char_alignment(
        CharAlignment([], [], []), clip_duration_s=1.0, tolerance_s=0.02
    )
    assert problemas == ["la alineacion viene vacia"]
