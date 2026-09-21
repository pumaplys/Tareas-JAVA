"""Catalogo de modelos: lo que este proyecto se compromete a enviar."""

from __future__ import annotations

from fractions import Fraction

import pytest

from viralgen.errors import ConfigError
from viralgen.media.capabilities import (
    IMAGE_MODELS,
    VIDEO_MODELS,
    image_capabilities,
    parse_ratio,
    parse_size,
    validate_image_request,
    video_capabilities,
)


def test_modelo_desconocido_falla_antes_de_http() -> None:
    with pytest.raises(ConfigError, match="desconocido"):
        image_capabilities("gpt-image-999")
    with pytest.raises(ConfigError, match="desconocido"):
        video_capabilities("gen9_ultra")


def test_1024x1536_no_es_9_16() -> None:
    """El catalogo no confunde 2:3 con 9:16."""
    ancho, alto = parse_size("1024x1536")
    assert Fraction(ancho, alto) == Fraction(2, 3)
    assert Fraction(ancho, alto) != Fraction(9, 16)

    capacidades = image_capabilities("gpt-image-1")
    elegido = capacidades.best_vertical(Fraction(9, 16))
    assert elegido == "1024x1536"  # el mas proximo, no uno exacto
    ancho, alto = parse_size(elegido)
    assert Fraction(ancho, alto) != Fraction(9, 16)


def test_el_mock_si_ofrece_un_tamano_9_16() -> None:
    capacidades = image_capabilities("mock-image-1")
    elegido = capacidades.best_vertical(Fraction(9, 16))
    ancho, alto = parse_size(elegido)
    assert Fraction(ancho, alto) == Fraction(9, 16)


@pytest.mark.parametrize(
    ("kwargs", "fragmento"),
    [
        ({"size": "1080x1920"}, "tamano"),
        ({"quality": "ultra"}, "calidad"),
        ({"output_format": "tiff"}, "formato"),
        ({"reference_count": 9}, "referencias"),
    ],
)
def test_combinaciones_no_soportadas(kwargs, fragmento) -> None:
    base = {
        "size": "1024x1536",
        "quality": "medium",
        "output_format": "jpeg",
        "edit": True,
        "reference_count": 1,
    }
    base.update(kwargs)
    with pytest.raises(ConfigError, match=fragmento):
        validate_image_request(image_capabilities("gpt-image-1"), **base)


def test_duracion_admitida_mas_corta_que_cubre() -> None:
    capacidades = video_capabilities("gen4_turbo")
    assert capacidades.shortest_duration_covering(4.2) == 5
    assert capacidades.shortest_duration_covering(5.0) == 5
    assert capacidades.shortest_duration_covering(5.01) == 10
    assert capacidades.shortest_duration_covering(10.0) == 10
    # Ninguna duracion cubre: bloquea, no se parte la escena.
    assert capacidades.shortest_duration_covering(10.5) is None


def test_proporcion_vertical_declarada() -> None:
    capacidades = video_capabilities("gen4_turbo")
    assert capacidades.default_ratio == "720:1280"
    ancho, alto = parse_ratio(capacidades.default_ratio)
    assert Fraction(ancho, alto) == Fraction(9, 16)


def test_el_catalogo_es_pequeno_y_explicito() -> None:
    assert set(IMAGE_MODELS) == {"gpt-image-1", "mock-image-1"}
    assert set(VIDEO_MODELS) == {"gen4_turbo", "mock-video-1"}
    for capacidades in IMAGE_MODELS.values():
        assert capacidades.notes
