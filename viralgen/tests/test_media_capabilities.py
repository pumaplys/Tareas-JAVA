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


def test_sunburst_esta_verificado_por_la_evidencia_del_revisor() -> None:
    """Protocolo cerrado el 2026-11-18; lo que falta es otra cosa."""
    capacidades = image_capabilities("gpt-image-2.6-sunburst")
    assert capacidades.verified is True
    assert capacidades.assumed_params == ()
    assert capacidades.retired is False
    # Factura por tokens: una tabla por (tamano, calidad) no le aplica.
    assert capacidades.bills_by_tokens is True
    # Solo consta `medium`, asi que solo se declara `medium`.
    assert capacidades.qualities == ("medium",)
    # 720x1280 es 9:16 NATIVO y es el primero de la seleccion.
    assert capacidades.sizes[0] == "720x1280"
    assert Fraction(*parse_size("720x1280")) == Fraction(9, 16)
    # Lo unico que sigue pendiente es la disponibilidad en la cuenta, y no
    # bloquea: la unica prueba posible es una llamada real.
    assert capacidades.pending_check_ids == ("sunburst_account_availability",)
    assert model_checks.find("sunburst_account_availability").blocks_real is False
    assert_verified_for_real(capacidades) is None


def test_la_regla_de_geometria_documentada_se_aplica() -> None:
    """La guia publica limites, no una lista. Se implementan como regla."""
    reglas = image_capabilities("gpt-image-2.6-sunburst").size_rules
    assert reglas is not None
    # 720x1280: 45x16 y 80x16, 9:16, 921 600 px. Lo admite.
    assert reglas.problems("720x1280") == []
    # Y lo que la regla prohibe, lo dice y por que.
    assert "multiplos de 16" in " ".join(reglas.problems("1000x1280"))
    assert "lado mayor supera" in " ".join(reglas.problems("4096x1024"))
    assert "por debajo del minimo" in " ".join(reglas.problems("512x512"))
    assert "proporcion" in " ".join(reglas.problems("720x3008"))


def test_gpt_image_1_esta_retirado_pero_sigue_resolviendose() -> None:
    """Se apago el 2026-10-23. Borrarlo rompería la lectura del historial."""
    capacidades = image_capabilities("gpt-image-1")
    assert capacidades.retired is True
    assert capacidades.retired_on == "2026-10-23"
    # Sigue en el catalogo: los manifiestos y las claves de cache lo citan.
    assert capacidades.sizes
    assert capacidades.describe()["usable_for_new_real_requests"] is False

    with pytest.raises(ConfigError) as excinfo:
        assert_verified_for_real(capacidades)
    assert "RETIRADO" in excinfo.value.message
    assert excinfo.value.details["retired_on"] == "2026-10-23"
    # El mensaje dice que el historial se sigue pudiendo leer.
    assert excinfo.value.details["readable_for_history"] is True


def test_un_modelo_sin_verificar_no_se_despacha_en_real() -> None:
    """Hoy no hay ninguno, pero la puerta sigue cubierta.

    Se construye uno sintetico: si manana se declara un modelo con parametros
    supuestos, tiene que seguir bloqueandose.
    """
    from viralgen.media.capabilities import ImageModelCapabilities

    supuesto = ImageModelCapabilities(
        model_id="gpt-image-2.6-sunburst",  # reutiliza sus entradas del registro
        sizes=("720x1280",),
        qualities=("medium",),
        output_formats=("jpeg",),
        supports_edit=True,
        max_reference_images=4,
        notes="sintetico para la prueba",
        verified=False,
        assumed_params=("sizes",),
    )
    with pytest.raises(ConfigError) as excinfo:
        assert_verified_for_real(supuesto)
    assert "VERIFICADO" in excinfo.value.message
    assert excinfo.value.details["assumed_params"] == ["sizes"]


def test_input_fidelity_esta_prohibida_en_sunburst() -> None:
    """La guia de 2.6 dice: «Omit `input_fidelity` on image edits.»"""
    sunburst = image_capabilities("gpt-image-2.6-sunburst")
    assert sunburst.forbids_input_fidelity is True
    assert sunburst.input_fidelities == ()

    base = dict(
        size="720x1280", quality="medium", output_format="jpeg",
        edit=True, reference_count=2,
    )
    # Sin el parametro: pasa.
    validate_image_request(sunburst, **base)
    # Con el parametro, con cualquier valor: error, y el mensaje dice que la
    # correccion es QUITARLO.
    for valor in ("low", "high", "ultra"):
        with pytest.raises(ConfigError) as excinfo:
            validate_image_request(sunburst, **base, input_fidelity=valor)
        assert "OMITIR" in excinfo.value.message
        assert "VIRALGEN_MEDIA_INPUT_FIDELITY" in excinfo.value.message


def test_el_limite_local_de_referencias_es_mas_estricto_que_el_publicado() -> None:
    """16 por peticion es lo documentado; el proyecto se queda en 4."""
    sunburst = image_capabilities("gpt-image-2.6-sunburst")
    assert sunburst.documented_max_reference_images == 16
    assert sunburst.max_reference_images == 4
    # Y el que se aplica es el local.
    with pytest.raises(ConfigError, match="superan el maximo declarado"):
        validate_image_request(
            sunburst, size="720x1280", quality="medium", output_format="jpeg",
            edit=True, reference_count=5,
        )
