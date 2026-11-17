"""Catalogo de modelos: lo que este proyecto se compromete a enviar."""

from __future__ import annotations

from fractions import Fraction

import pytest

from viralgen.errors import ConfigError
from viralgen.media import model_checks
from viralgen.media.capabilities import (
    IMAGE_MODELS,
    VIDEO_MODELS,
    assert_verified_for_real,
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
    # El catalogo es una decision: ampliarlo rompe esta prueba a proposito.
    assert set(IMAGE_MODELS) == {
        "gpt-image-1",
        "gpt-image-2.6-sunburst",
        "mock-image-1",
    }
    assert set(VIDEO_MODELS) == {"gen4_turbo", "mock-video-1"}
    for capacidades in IMAGE_MODELS.values():
        assert capacidades.notes


def test_sunburst_esta_declarado_pero_no_verificado() -> None:
    """Declarado para poder planificar; sin verificar para no despachar."""
    capacidades = image_capabilities("gpt-image-2.6-sunburst")
    assert capacidades.verified is False
    assert capacidades.assumption_only is True
    # Los valores que son supuesto se nombran, para que ningun informe los
    # presente como documentados.
    assert "sizes" in capacidades.assumed_params
    assert "qualities" in capacidades.assumed_params
    # Y cada supuesto apunta a la entrada del registro que lo cerraria.
    assert capacidades.pending_check_ids
    for check_id in capacidades.pending_check_ids:
        assert model_checks.find(check_id).target == "gpt-image-2.6-sunburst"


def test_gpt_image_1_sigue_siendo_el_modelo_verificado() -> None:
    capacidades = image_capabilities("gpt-image-1")
    assert capacidades.verified is True
    assert capacidades.assumed_params == ()
    # No lanza: es el modelo que el piloto puede usar de verdad.
    assert_verified_for_real(capacidades) is None


def test_un_modelo_sin_verificar_no_se_despacha_en_real() -> None:
    capacidades = image_capabilities("gpt-image-2.6-sunburst")
    with pytest.raises(ConfigError) as excinfo:
        assert_verified_for_real(capacidades)
    # El mensaje dice por que, no solo que no.
    assert "no" in excinfo.value.message.lower()
    assert "VERIFICADO" in excinfo.value.message
    assert excinfo.value.details["blocking_checks"]
    # Y no hay interruptor: el bloqueo depende del registro, no de un ajuste.
    assert "model_checks" not in excinfo.value.details


def test_input_fidelity_solo_existe_en_la_edicion() -> None:
    capacidades = image_capabilities("gpt-image-1")
    # gpt-image-1 no la declara en este catalogo: enviarla es un error.
    with pytest.raises(ConfigError, match="no declara"):
        validate_image_request(
            capacidades,
            size="1024x1536",
            quality="medium",
            output_format="jpeg",
            edit=True,
            reference_count=1,
            input_fidelity="high",
        )

    sunburst = image_capabilities("gpt-image-2.6-sunburst")
    # Declarada y en una edicion: pasa.
    validate_image_request(
        sunburst,
        size="1024x1536",
        quality="medium",
        output_format="jpeg",
        edit=True,
        reference_count=2,
        input_fidelity="high",
    )
    # Declarada pero SIN referencias: el parametro no existe en generacion.
    with pytest.raises(ConfigError, match="solo existe en la edicion"):
        validate_image_request(
            sunburst,
            size="1024x1536",
            quality="medium",
            output_format="jpeg",
            edit=False,
            reference_count=0,
            input_fidelity="high",
        )
    # Un valor fuera de los declarados tambien falla antes de cualquier HTTP.
    with pytest.raises(ConfigError, match="no declarada"):
        validate_image_request(
            sunburst,
            size="1024x1536",
            quality="medium",
            output_format="jpeg",
            edit=True,
            reference_count=1,
            input_fidelity="ultra",
        )
