"""Presupuesto: coste estimado y limite de solicitudes son cosas distintas.

La prueba que de verdad importa aqui es la ultima: 24 intentos no son un
maximo en dolares, y el informe no los puede presentar como tal.
"""

from __future__ import annotations

import json

import pytest

from viralgen.errors import ConfigError
from viralgen.media.budget import (
    CONCEPT_REFERENCE_IMAGE,
    CONCEPT_REFERENCE_INPUT,
    CONCEPT_SCENE_IMAGE,
    ImageTariffs,
    estimate_from_plan,
    load_image_tariffs,
)
from viralgen.media.planner import MediaPlan, ScenePlan


def _escena(
    scene_id: str,
    *,
    size: str = "1024x1536",
    quality: str = "medium",
    characters: tuple[str, ...] = (),
    cache_hit: bool = False,
) -> ScenePlan:
    return ScenePlan(
        scene_id=scene_id,
        order=0,
        requested_type="image",
        start_sample=0,
        end_sample=24_000,
        duration_s=1.0,
        character_ids=list(characters),
        effective_prompt="prompt",
        prompt_chars=6,
        image_size=size,
        image_quality=quality,
        image_format="jpeg",
        needs_video=False,
        image_cache_hit=cache_hit,
    )


class _Ajustes:
    """Ajustes minimos: solo lo que el presupuesto consulta."""

    media_max_safe_retries = 2
    media_max_generation_attempts = 24
    media_max_video_scenes = 0
    media_max_video_seconds = 0
    media_max_download_attempts = 3
    media_image_quality = "medium"
    media_image_tariffs_path = None
    price_image_per_unit_usd = None
    price_image_reference_input_usd = None
    price_video_per_second_usd = None


# ---------------------------------------------------------------------------
# Tarifas
# ---------------------------------------------------------------------------


def test_sin_tarifas_el_coste_es_desconocido_no_cero() -> None:
    plan = MediaPlan(scenes=[_escena("s1"), _escena("s2")], image_model="gpt-image-1")
    estimacion = estimate_from_plan(plan, _Ajustes())

    assert estimacion.expected_usd is None
    assert estimacion.ceiling_usd is None
    assert CONCEPT_SCENE_IMAGE in estimacion.unknown_concepts
    # Pero las UNIDADES si se conocen: no saber el precio no impide contar.
    assert estimacion.planned_requests["creation_requests_nominal"] == 2


def test_la_tarifa_depende_del_tamano_y_de_la_calidad() -> None:
    """Dos escenas iguales en todo menos la calidad no cuestan lo mismo."""
    tarifas = ImageTariffs(
        per_size_quality={
            ("1024x1536", "medium"): 0.042,
            ("1024x1536", "high"): 0.167,
        }
    )
    plan = MediaPlan(
        scenes=[_escena("s1", quality="medium"), _escena("s2", quality="high")],
        image_model="gpt-image-1",
    )
    estimacion = estimate_from_plan(plan, _Ajustes(), tariffs=tarifas)

    assert estimacion.unknown_concepts == []
    assert estimacion.expected_usd == pytest.approx(0.042 + 0.167)
    # Y el desglose conserva las dimensiones, no solo el total.
    por_calidad = {
        (linea.size, linea.quality): linea.unit_price_usd
        for linea in estimacion.lines
        if linea.concept == CONCEPT_SCENE_IMAGE
    }
    assert por_calidad == {
        ("1024x1536", "medium"): 0.042,
        ("1024x1536", "high"): 0.167,
    }


def test_un_tamano_sin_tarifa_no_se_rellena_con_el_de_otro() -> None:
    tarifas = ImageTariffs(per_size_quality={("1024x1536", "medium"): 0.042})
    plan = MediaPlan(
        scenes=[_escena("s1"), _escena("s2", size="1536x1024")],
        image_model="gpt-image-1",
    )
    estimacion = estimate_from_plan(plan, _Ajustes(), tariffs=tarifas)

    assert estimacion.expected_usd is None
    assert CONCEPT_SCENE_IMAGE in estimacion.unknown_concepts


def test_la_tarifa_plana_sigue_valiendo_como_respaldo() -> None:
    ajustes = _Ajustes()
    ajustes.price_image_per_unit_usd = 0.05
    plan = MediaPlan(scenes=[_escena("s1")], image_model="gpt-image-1")
    estimacion = estimate_from_plan(plan, ajustes)

    assert estimacion.expected_usd == pytest.approx(0.05)
    assert estimacion.lines[0].price_source == "tarifa_plana"


def test_la_tabla_manda_sobre_la_tarifa_plana() -> None:
    ajustes = _Ajustes()
    ajustes.price_image_per_unit_usd = 0.05
    tarifas = ImageTariffs(
        per_size_quality={("1024x1536", "medium"): 0.042}, flat_per_unit=0.05
    )
    plan = MediaPlan(scenes=[_escena("s1")], image_model="gpt-image-1")
    estimacion = estimate_from_plan(plan, ajustes, tariffs=tarifas)

    assert estimacion.expected_usd == pytest.approx(0.042)
    assert estimacion.lines[0].price_source == "tabla"


# ---------------------------------------------------------------------------
# Lo que se cuenta
# ---------------------------------------------------------------------------


def test_los_aciertos_de_cache_no_se_vuelven_a_pagar() -> None:
    tarifas = ImageTariffs(per_size_quality={("1024x1536", "medium"): 0.1})
    plan = MediaPlan(
        scenes=[_escena("s1"), _escena("s2", cache_hit=True)],
        image_model="gpt-image-1",
    )
    estimacion = estimate_from_plan(plan, _Ajustes(), tariffs=tarifas)

    assert estimacion.expected_usd == pytest.approx(0.1)
    assert estimacion.planned_requests["creation_requests_nominal"] == 1


def test_las_referencias_a_generar_se_cuentan_y_se_reutilizan() -> None:
    tarifas = ImageTariffs(per_size_quality={("1024x1536", "medium"): 0.1})
    plan = MediaPlan(
        scenes=[_escena("s1", characters=("lumi",))],
        image_model="gpt-image-1",
        reference_plan={"lumi": "to_generate", "tobi": "reusable", "nube": "imported"},
    )
    estimacion = estimate_from_plan(plan, _Ajustes(), tariffs=tarifas)

    linea = next(l for l in estimacion.lines if l.concept == CONCEPT_REFERENCE_IMAGE)
    # Solo la que hay que generar: una reusable o importada no se paga.
    assert linea.units == 1
    assert "se reutilizan" in linea.note
    # Y se presupuesta con el tamano que de verdad va a pedir el trabajo.
    assert linea.size == "1024x1536"


def test_las_referencias_enviadas_en_la_edicion_no_se_suponen_gratis() -> None:
    """Si el proveedor cobra los tokens de entrada, esto no es cero."""
    tarifas = ImageTariffs(per_size_quality={("1024x1536", "medium"): 0.1})
    plan = MediaPlan(
        scenes=[_escena("s1", characters=("lumi", "tobi"))],
        image_model="gpt-image-1",
    )
    estimacion = estimate_from_plan(plan, _Ajustes(), tariffs=tarifas)

    linea = next(l for l in estimacion.lines if l.concept == CONCEPT_REFERENCE_INPUT)
    assert linea.units == 2
    assert linea.unit_price_usd is None
    assert linea.price_source == "desconocida"
    # Y por eso el total es desconocido, en vez de contar las entradas como 0.
    assert estimacion.expected_usd is None
    assert CONCEPT_REFERENCE_INPUT in estimacion.unknown_concepts

    # Declarada la tarifa, el total se cierra.
    tarifas_completas = ImageTariffs(
        per_size_quality={("1024x1536", "medium"): 0.1}, reference_input=0.01
    )
    cerrada = estimate_from_plan(plan, _Ajustes(), tariffs=tarifas_completas)
    assert cerrada.expected_usd == pytest.approx(0.1 + 0.02)


# ---------------------------------------------------------------------------
# Lo que el encargo pide distinguir
# ---------------------------------------------------------------------------


def test_los_reintentos_son_un_techo_no_lo_esperado() -> None:
    tarifas = ImageTariffs(per_size_quality={("1024x1536", "medium"): 0.1})
    plan = MediaPlan(scenes=[_escena("s1"), _escena("s2")], image_model="gpt-image-1")
    estimacion = estimate_from_plan(plan, _Ajustes(), tariffs=tarifas)

    # Nominal: una llamada por imagen.
    assert estimacion.expected_usd == pytest.approx(0.2)
    # Techo: cada imagen agota sus 2 reintentos seguros -> 3 llamadas.
    assert estimacion.ceiling_usd == pytest.approx(0.6)
    assert estimacion.planned_requests["creation_requests_if_all_retry"] == 6
    # Y el techo esta ETIQUETADO como techo, no como coste.
    informe = estimacion.describe()
    assert informe["cost_estimate_usd"]["expected"] == pytest.approx(0.2)
    assert informe["cost_estimate_usd"]["ceiling_with_retries"] == pytest.approx(0.6)


def test_el_limite_de_intentos_no_se_presenta_como_un_maximo_en_dolares() -> None:
    """24 intentos es un tope de SOLICITUDES. No es un tope de gasto."""
    plan = MediaPlan(scenes=[_escena("s1")], image_model="gpt-image-1")
    informe = estimate_from_plan(plan, _Ajustes()).describe()

    # El limite vive en su propia seccion, separado del coste.
    assert informe["request_limits"]["max_generation_attempts"] == 24
    assert "max_generation_attempts" not in informe["cost_estimate_usd"]
    # Y el informe lo dice con palabras, para que nadie lo lea como dolares.
    assert "no es un maximo de gasto" in informe["limits_are_not_money"]
    assert "24" in informe["limits_are_not_money"]
    # Con el limite conocido pero sin tarifas, el coste sigue siendo None: un
    # numero de intentos no se convierte en una cifra de dinero.
    assert informe["cost_estimate_usd"]["expected"] is None


def test_el_informe_dice_si_el_modelo_esta_verificado() -> None:
    plan = MediaPlan(scenes=[_escena("s1")], image_model="gpt-image-2.6-sunburst")
    informe = estimate_from_plan(plan, _Ajustes()).describe()
    assert informe["model"] == "gpt-image-2.6-sunburst"
    assert informe["model_verified"] is True

    # Un modelo que el catalogo no resuelve no se da por verificado.
    plan_desconocido = MediaPlan(scenes=[_escena("s1")], image_model="gpt-image-999")
    assert (
        estimate_from_plan(plan_desconocido, _Ajustes()).describe()["model_verified"]
        is False
    )


def test_con_facturacion_por_tokens_la_tabla_no_aplica() -> None:
    """Las unidades siguen contando solicitudes; su precio unitario no existe."""
    tarifas = ImageTariffs(per_size_quality={("1024x1536", "medium"): 0.1})
    plan = MediaPlan(
        scenes=[_escena("s1"), _escena("s2")], image_model="gpt-image-2.6-sunburst"
    )
    informe = estimate_from_plan(plan, _Ajustes(), tariffs=tarifas).describe()

    assert informe["billing"]["model"] == "tokens"
    assert all(l["price_source"] == "facturado_por_tokens" for l in informe["lines"])
    assert informe["cost_estimate_usd"]["expected"] is None
    # Y el motivo distingue "faltan recuentos" de "falta una tarifa".
    assert "TOKENS" in informe["cost_estimate_usd"]["unknown_reason"]
    assert "no se conocen" in informe["cost_estimate_usd"]["unknown_reason"]
    # Las unidades, en cambio, si se conocen.
    assert informe["planned_requests"]["creation_requests_nominal"] == 2


def test_las_tarifas_por_token_se_declaran_con_su_formula() -> None:
    ajustes = _Ajustes()
    ajustes.price_image_text_input_per_1m_usd = 5.0
    ajustes.price_image_input_per_1m_usd = 8.0
    ajustes.price_image_output_per_1m_usd = 30.0
    plan = MediaPlan(scenes=[_escena("s1")], image_model="gpt-image-2.6-sunburst")

    tarifas = estimate_from_plan(plan, ajustes).describe()["billing"]["token_rates"]
    assert tarifas["text_input_per_1m_usd"] == pytest.approx(5.0)
    assert tarifas["image_input_per_1m_usd"] == pytest.approx(8.0)
    assert tarifas["image_output_per_1m_usd"] == pytest.approx(30.0)
    # La formula dice explicitamente que la salida no es el precio por imagen.
    assert "NO es el precio de una imagen" in tarifas["formula"]


def test_el_consumo_declarado_se_valora_por_componentes() -> None:
    """Y nunca sumando el total a sus partes, que lo duplicaria."""
    from viralgen.media.budget import TokenRates

    tarifas = TokenRates(text_input_per_1m=5.0, image_input_per_1m=8.0, image_output_per_1m=30.0)
    consumo = {
        "input_tokens": 4600,          # el agregado, que NO se usa
        "input_tokens_text": 1200,
        "input_tokens_image": 3400,
        "output_tokens": 1580,
        "total_tokens": 6180,          # tampoco
    }
    esperado = (1200 * 5 + 3400 * 8 + 1580 * 30) / 1_000_000
    assert tarifas.price(consumo) == pytest.approx(esperado)
    # Nada que ver con cargar 30 USD por imagen.
    assert tarifas.price(consumo) < 0.1

    # Si falta un componente con tarifa, no hay cifra: ausente no es cero.
    assert tarifas.price({"input_tokens_text": 1200, "output_tokens": 1580}) is None
    # Y sin tarifas completas tampoco.
    assert TokenRates(text_input_per_1m=5.0).price(consumo) is None


# ---------------------------------------------------------------------------
# Carga de la tabla
# ---------------------------------------------------------------------------


def test_la_tabla_se_lee_del_archivo_configurado(tmp_path) -> None:
    ruta = tmp_path / "tarifas.json"
    ruta.write_text(
        json.dumps({"1024x1536": {"medium": 0.042, "high": 0.167}}), encoding="utf-8"
    )
    ajustes = _Ajustes()
    ajustes.media_image_tariffs_path = ruta

    tarifas = load_image_tariffs(ajustes)
    assert tarifas.unit_price("1024x1536", "high") == pytest.approx(0.167)
    assert tarifas.unit_price("1024x1024", "high") is None
    assert tarifas.source == str(ruta)


@pytest.mark.parametrize(
    "contenido, mensaje",
    [
        ("no es json", "no es JSON valido"),
        ('["lista"]', "debe ser un objeto"),
        ('{"1024x1536": 0.04}', "debe ser un objeto"),
        ('{"1024x1536": {"medium": "gratis"}}', "no es un numero"),
        ('{"1024x1536": {"medium": -1}}', "es negativa"),
    ],
)
def test_una_tabla_invalida_es_un_error_no_un_cero(tmp_path, contenido, mensaje) -> None:
    ruta = tmp_path / "tarifas.json"
    ruta.write_text(contenido, encoding="utf-8")
    ajustes = _Ajustes()
    ajustes.media_image_tariffs_path = ruta

    with pytest.raises(ConfigError, match=mensaje):
        load_image_tariffs(ajustes)


def test_avisa_cuando_el_peor_caso_no_cabe_en_el_limite() -> None:
    """7 escenas + 3 referencias con 2 reintentos son 30 contra un tope de 24."""
    escenas = [_escena(f"s{i}", characters=("lumi",)) for i in range(7)]
    plan = MediaPlan(
        scenes=escenas,
        image_model="gpt-image-1",
        reference_plan={"lumi": "to_generate", "tobi": "to_generate", "nube": "to_generate"},
    )
    estimacion = estimate_from_plan(plan, _Ajustes())

    assert estimacion.planned_requests["creation_requests_nominal"] == 10
    assert estimacion.planned_requests["creation_requests_if_all_retry"] == 30
    assert estimacion.retry_ceiling_exceeds_limit is True

    informe = estimacion.describe()
    assert informe["retry_ceiling_exceeds_limit"] is True
    assert "NO cabe" in informe["retry_ceiling_note"]
    assert "24" in informe["retry_ceiling_note"]
    assert "escenas sin imagen" in informe["retry_ceiling_note"]


def test_no_avisa_cuando_el_peor_caso_si_cabe() -> None:
    plan = MediaPlan(scenes=[_escena("s1"), _escena("s2")], image_model="gpt-image-1")
    estimacion = estimate_from_plan(plan, _Ajustes())

    # 2 imagenes x 3 intentos = 6, que si cabe en 24.
    assert estimacion.planned_requests["creation_requests_if_all_retry"] == 6
    assert estimacion.retry_ceiling_exceeds_limit is False
    assert "cabe en el tope" in estimacion.describe()["retry_ceiling_note"]
