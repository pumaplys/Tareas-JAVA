"""`media smoke`: una generacion y una edicion, aisladas y acotadas.

Lo que estas pruebas demuestran y lo que NO:

* SI: que el adaptador real serializa las dos peticiones correctas, que los
  bytes adjuntos en la edicion son los de la primera imagen, que el tope de dos
  peticiones y los cero reintentos se respetan, que repetir no reenvia y que un
  fallo o un resultado incierto detienen la prueba.
* NO: que el modelo responda en la cuenta, que la facturacion sea la esperada
  ni que los personajes se parezcan. Eso solo lo dice una ejecucion real.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json

import httpx2
import pytest
from openai import OpenAI
from PIL import Image
from pydantic import SecretStr

from viralgen.config import Settings
from viralgen.errors import ConfigError
from viralgen.media import smoke
from viralgen.media.providers.base import MediaOutcomeUnknownError, MediaPermanentError
from viralgen.media.providers.openai_images import OpenAIImageProvider

SUNBURST = "gpt-image-2.6-sunburst"


@pytest.fixture
def ajustes(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "datos",
        min_free_disk_mb=0,
        log_level="ERROR",
        openai_api_key=SecretStr("sk-de-prueba-no-real"),
        openai_image_model=SUNBURST,
        media_max_safe_retries=0,
    )


def _png(width: int = 720, height: int = 1280, color: str = "#336699") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _respuesta(png: bytes, *, usage: dict | None = None) -> httpx2.Response:
    cuerpo: dict = {"created": 0, "data": [{"b64_json": base64.b64encode(png).decode()}]}
    if usage is not None:
        cuerpo["usage"] = usage
    return httpx2.Response(200, json=cuerpo)


def _runner(ajustes, respuestas, *, run_id: str = "prueba"):
    """Un corredor con transporte controlado por la prueba."""
    capturadas: list[httpx2.Request] = []
    pendientes = list(respuestas)

    def handler(request: httpx2.Request) -> httpx2.Response:
        capturadas.append(request)
        siguiente = pendientes.pop(0)
        if isinstance(siguiente, Exception):
            raise siguiente
        return siguiente

    cliente = OpenAI(
        api_key="sk-de-prueba-no-real",
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )
    proveedor = OpenAIImageProvider(settings=ajustes, client=cliente, sleep=lambda _s: None)
    corredor = smoke._SmokeRunner(
        ajustes,
        provider=proveedor,
        simulation=True,
        character_id="lumi",
        run_id=run_id,
    )
    return corredor, capturadas


# ---------------------------------------------------------------------------
# Los prompts salen de la biblia
# ---------------------------------------------------------------------------


def test_los_prompts_usan_la_descripcion_y_el_vestuario_de_la_biblia(ajustes) -> None:
    bible = smoke._load_bible(ajustes)
    generacion, edicion = smoke.build_steps(bible, "lumi")

    # Aspecto y ropa, tal cual los fija la biblia. No los reescribe un LLM.
    assert "pale orange fur" in generacion.prompt
    assert "moss-green knitted scarf" in generacion.prompt
    assert "Full body" in generacion.prompt

    # La edicion mantiene al personaje y anade SOLO la escena nueva.
    assert "wooden footbridge" in edicion.prompt
    assert "Keep this exact character, wardrobe and palette unchanged" in edicion.prompt
    assert "pale orange fur" in edicion.prompt

    # Las dos piden que no haya texto en la imagen.
    for paso in (generacion, edicion):
        assert "No text" in paso.prompt


def test_un_personaje_inexistente_falla_con_las_opciones(ajustes) -> None:
    bible = smoke._load_bible(ajustes)
    with pytest.raises(ConfigError) as excinfo:
        smoke.build_steps(bible, "mortadelo")
    assert "lumi" in excinfo.value.message
    assert "nube" in excinfo.value.message


def test_la_identidad_distingue_los_dos_pasos_y_el_modo(ajustes) -> None:
    bible = smoke._load_bible(ajustes)
    generacion, edicion = smoke.build_steps(bible, "lumi")

    claves = {
        generacion.identity(model=SUNBURST, reference_sha256=None, simulation=False),
        edicion.identity(model=SUNBURST, reference_sha256="aa", simulation=False),
        generacion.identity(model=SUNBURST, reference_sha256=None, simulation=True),
        edicion.identity(model=SUNBURST, reference_sha256="bb", simulation=True),
    }
    # Cuatro identidades distintas: paso, referencia y modo cuentan.
    assert len(claves) == 4

    # Y la operacion es propia de la prueba, asi que no puede colisionar con un
    # asset del pipeline.
    assert generacion.operation == "smoke_generate"
    assert edicion.operation == "smoke_edit"


# ---------------------------------------------------------------------------
# Modo plan: sin red y sin clave
# ---------------------------------------------------------------------------


def test_el_plan_no_necesita_clave_ni_red(tmp_path) -> None:
    sin_clave = Settings(
        _env_file=None,
        data_dir=tmp_path / "datos",
        min_free_disk_mb=0,
        log_level="ERROR",
        openai_image_model=SUNBURST,
    )
    informe = smoke.plan(sin_clave)

    assert informe["mode"] == "plan"
    assert informe["model"] == SUNBURST
    assert informe["size"] == "720x1280"
    assert (informe["width"], informe["height"]) == (720, 1280)
    assert informe["quality"] == "medium"
    assert informe["output_format"] == "png"
    assert informe["images_per_request"] == 1
    # Los dos parametros que NO se envian, declarados como tal.
    assert informe["input_fidelity"] is None
    assert informe["response_format"] is None
    assert informe["capability_problems"] == []
    assert informe["budget"] == {"max_requests": 2, "safe_retries": 0, "sdk_max_retries": 0}
    assert [paso["endpoint"] for paso in informe["steps"]] == [
        "POST /v1/images/generations",
        "POST /v1/images/edits",
    ]


def test_el_plan_delata_un_modelo_que_no_admite_los_parametros(tmp_path) -> None:
    """gpt-image-1 no declara 720x1280: el plan lo dice sin tocar la red."""
    ajustes = Settings(
        _env_file=None,
        data_dir=tmp_path / "datos",
        min_free_disk_mb=0,
        log_level="ERROR",
        openai_image_model="gpt-image-1",
    )
    informe = smoke.plan(ajustes)
    assert informe["model_retired_on"] == "2026-10-23"
    assert any("720x1280" in problema for problema in informe["capability_problems"])


def test_el_modo_real_rechaza_un_modelo_retirado(tmp_path) -> None:
    ajustes = Settings(
        _env_file=None,
        data_dir=tmp_path / "datos",
        min_free_disk_mb=0,
        log_level="ERROR",
        openai_api_key=SecretStr("sk-de-prueba-no-real"),
        openai_image_model="gpt-image-1",
    )
    with pytest.raises(ConfigError) as excinfo:
        smoke.run(ajustes, mode="real")
    assert "RETIRADO" in excinfo.value.message


# ---------------------------------------------------------------------------
# La secuencia de las dos peticiones
# ---------------------------------------------------------------------------


def test_la_secuencia_es_generations_y_despues_edits(ajustes) -> None:
    primera, segunda = _png(color="#336699"), _png(color="#993366")
    corredor, capturadas = _runner(
        ajustes,
        [
            _respuesta(primera, usage={"input_tokens": 190, "output_tokens": 1056}),
            _respuesta(segunda),
        ],
    )
    resumen = corredor.execute()

    assert [r.url.path for r in capturadas] == [
        "/v1/images/generations",
        "/v1/images/edits",
    ]
    assert capturadas[0].headers["content-type"].startswith("application/json")
    assert capturadas[1].headers["content-type"].startswith("multipart/form-data")
    assert resumen["requests_sent"] == 2
    assert [paso["status"] for paso in resumen["steps"]] == ["ok", "ok"]


def test_la_edicion_adjunta_exactamente_los_bytes_de_la_primera_imagen(ajustes) -> None:
    """La comprobacion se hace sobre el cuerpo serializado, no sobre la variable."""
    primera, segunda = _png(color="#336699"), _png(color="#993366")
    corredor, capturadas = _runner(ajustes, [_respuesta(primera), _respuesta(segunda)])
    resumen = corredor.execute()

    adjunto = smoke.extract_multipart_reference(capturadas[1].content)
    assert adjunto is not None
    assert hashlib.sha256(adjunto).hexdigest() == resumen["steps"][0]["sha256"]
    # Y es la UNICA referencia.
    assert smoke._multipart_field_names(capturadas[1].content).count("image[]") == 1


def test_ni_input_fidelity_ni_response_format_viajan(ajustes) -> None:
    corredor, capturadas = _runner(ajustes, [_respuesta(_png()), _respuesta(_png())])
    corredor.execute()

    cuerpo_generacion = json.loads(capturadas[0].content)
    assert set(cuerpo_generacion) == {
        "model", "prompt", "n", "size", "quality", "output_format"
    }
    assert cuerpo_generacion["size"] == "720x1280"
    assert cuerpo_generacion["quality"] == "medium"
    assert cuerpo_generacion["output_format"] == "png"
    assert cuerpo_generacion["n"] == 1

    campos = set(smoke._multipart_field_names(capturadas[1].content))
    assert "input_fidelity" not in campos
    assert "response_format" not in campos


def test_las_dimensiones_se_comprueban_decodificando(ajustes) -> None:
    corredor, _ = _runner(ajustes, [_respuesta(_png()), _respuesta(_png())])
    resumen = corredor.execute()

    for paso in resumen["steps"]:
        assert (paso["width"], paso["height"]) == (720, 1280)
        assert paso["dimensions_match_request"] is True
        assert paso["mime"] == "image/png"


def test_una_imagen_con_otras_dimensiones_se_delata(ajustes) -> None:
    """No se da por bueno lo que llega: se mide."""
    corredor, _ = _runner(
        ajustes, [_respuesta(_png(1024, 1536)), _respuesta(_png(1024, 1536))]
    )
    resumen = corredor.execute()
    assert resumen["steps"][0]["dimensions_match_request"] is False
    assert (resumen["steps"][0]["width"], resumen["steps"][0]["height"]) == (1024, 1536)


def test_el_consumo_se_registra_solo_si_lo_devuelve_el_proveedor(ajustes) -> None:
    corredor, _ = _runner(
        ajustes,
        [
            _respuesta(
                _png(),
                usage={
                    "input_tokens": 190,
                    "input_tokens_details": {"text_tokens": 190, "image_tokens": 0},
                    "output_tokens": 1056,
                    "total_tokens": 1246,
                },
            ),
            _respuesta(_png()),  # sin `usage`
        ],
    )
    resumen = corredor.execute()

    generacion, edicion = resumen["steps"]
    assert generacion["usage"]["input_tokens_text"] == 190
    assert generacion["usage"]["output_tokens"] == 1056
    assert generacion["usage_note"] is None
    # El segundo no lo devolvio: queda en None, no en ceros.
    assert edicion["usage"] is None
    assert "ausente no es cero" in edicion["usage_note"]


# ---------------------------------------------------------------------------
# Control del gasto
# ---------------------------------------------------------------------------


def test_repetir_no_reenvia_nada(ajustes) -> None:
    corredor, capturadas = _runner(ajustes, [_respuesta(_png()), _respuesta(_png())])
    primero = corredor.execute()
    assert primero["requests_sent"] == 2
    assert len(capturadas) == 2

    # Segunda invocacion, con otro run_id: la identidad es del ASSET, no de la
    # ejecucion, asi que no se envia nada.
    corredor2, capturadas2 = _runner(ajustes, [], run_id="otro")
    segundo = corredor2.execute()
    assert segundo["requests_sent"] == 0
    assert capturadas2 == []
    assert all(paso["cache_hit"] for paso in segundo["steps"])
    # Y devuelve las mismas imagenes.
    assert [p["sha256"] for p in segundo["steps"]] == [
        p["sha256"] for p in primero["steps"]
    ]


def test_si_falla_la_primera_imagen_no_se_intenta_la_edicion(ajustes) -> None:
    """Editar sin referencia seria otra prueba, no esta."""
    corredor, capturadas = _runner(
        ajustes, [httpx2.Response(400, json={"error": {"message": "mal"}})]
    )
    resumen = corredor.execute()

    assert len(capturadas) == 1
    assert resumen["requests_sent"] == 1
    assert resumen["steps"][0]["status"] == "error"
    # Solo hay un paso en el informe: la edicion ni se planteo.
    assert len(resumen["steps"]) == 1


def test_un_resultado_incierto_detiene_y_bloquea_la_repeticion(ajustes) -> None:
    """Un corte despues de enviar pudo gastar credito: no se repite solo."""
    corredor, capturadas = _runner(
        ajustes, [httpx2.ConnectError("conexion cortada")]
    )
    resumen = corredor.execute()

    assert len(capturadas) == 1
    assert resumen["steps"][0]["status"] == "outcome_unknown"
    assert "credito" in resumen["steps"][0]["note"]

    # Repetir no reenvia: queda bloqueado hasta reconciliar a mano.
    corredor2, capturadas2 = _runner(ajustes, [], run_id="otro")
    segundo = corredor2.execute()
    assert capturadas2 == []
    assert segundo["steps"][0]["status"] == "outcome_unknown"
    assert "ejecucion anterior" in segundo["steps"][0]["reason"]


def test_un_error_transitorio_no_se_reintenta(ajustes) -> None:
    """Cero reintentos: un 500 gasta una peticion y se detiene."""
    corredor, capturadas = _runner(ajustes, [httpx2.Response(500, json={})])
    resumen = corredor.execute()

    assert len(capturadas) == 1, "con cero reintentos no se vuelve a enviar"
    assert resumen["steps"][0]["status"] == "error"
    assert resumen["requests_sent"] == 1


def test_el_tope_es_de_dos_peticiones(ajustes) -> None:
    corredor, _ = _runner(ajustes, [_respuesta(_png()), _respuesta(_png())])
    resumen = corredor.execute()
    assert resumen["budget"]["limits"]["generation_attempts"] == 2.0
    assert resumen["requests_sent"] == 2
    assert smoke.SMOKE_MAX_REQUESTS == 2


# ---------------------------------------------------------------------------
# Diagnostico, no produccion
# ---------------------------------------------------------------------------


def test_no_escribe_manifiesto_ni_entra_en_la_cola(ajustes) -> None:
    corredor, _ = _runner(ajustes, [_respuesta(_png()), _respuesta(_png())])
    resumen = corredor.execute()

    assert resumen["diagnostic_only"] is True
    assert resumen["admissible_for_assembly"] is False
    assert resumen["admission_reasons"]

    raiz = ajustes.data_dir
    assert list(raiz.rglob("media.json")) == []
    assert list(raiz.rglob("publication_plan.json")) == []
    # Lo que si escribe: las dos imagenes y su resumen, bajo smoke/.
    producidos = sorted(p.name for p in raiz.rglob("smoke/*/*"))
    assert producidos == ["1_generacion.png", "2_edicion.png", "smoke.json"]


def test_el_transporte_simulado_vive_en_el_espacio_de_simulacion(ajustes) -> None:
    """Separacion simulacion/produccion: no contamina el espacio real."""
    resumen = smoke.run(ajustes, mode="simulated_transport", run_id="sim")
    assert "/simulation/" in resumen["out_dir"]
    assert resumen["transport"] == "simulated"
    # Y dice, sin ambiguedad, que las imagenes no las hizo el proveedor.
    assert "las produjo ESTE codigo, no el proveedor" in resumen["transport_note"]
    assert "no acreditan nada visual" in resumen["transport_note"]


def test_el_resumen_se_exporta_a_disco(ajustes) -> None:
    corredor, _ = _runner(ajustes, [_respuesta(_png()), _respuesta(_png())])
    resumen = corredor.execute()
    from pathlib import Path

    guardado = json.loads(Path(resumen["summary_path"]).read_text(encoding="utf-8"))
    assert guardado["steps"][0]["sha256"] == resumen["steps"][0]["sha256"]
    assert guardado["diagnostic_only"] is True


# ---------------------------------------------------------------------------
# El modo que se puede pegar sin claves
# ---------------------------------------------------------------------------


def test_el_modo_simulado_completo_demuestra_la_secuencia(ajustes) -> None:
    """Es el que se documenta como orden pegable: sin clave y sin red."""
    resumen = smoke.run(ajustes, mode="simulated_transport", run_id="pegable")

    assert [llamada["path"] for llamada in resumen["http_calls"]] == [
        "/v1/images/generations",
        "/v1/images/edits",
    ]
    assert resumen["reference_sha256_matches"] is True
    assert resumen["requests_sent"] == 2
    assert [paso["status"] for paso in resumen["steps"]] == ["ok", "ok"]
    # Las dos imagenes son distintas: si fueran iguales, comparar no diria nada.
    assert resumen["steps"][0]["sha256"] != resumen["steps"][1]["sha256"]
    # El cuerpo de la generacion no lleva los dos parametros prohibidos.
    assert "response_format" not in resumen["http_calls"][0]["body_keys"]
    assert "input_fidelity" not in resumen["http_calls"][0]["body_keys"]
    assert "input_fidelity" not in resumen["http_calls"][1]["multipart_fields"]


def test_un_modo_desconocido_falla(ajustes) -> None:
    with pytest.raises(ConfigError, match="Modo de prueba desconocido"):
        smoke.run(ajustes, mode="produccion")


def test_la_nota_distingue_cache_de_consumo_no_devuelto(ajustes) -> None:
    """Sin consumo hay dos motivos, y confundirlos sugiere un cero falso."""
    corredor, _ = _runner(
        ajustes,
        [_respuesta(_png(), usage={"input_tokens": 190, "output_tokens": 1056}), _respuesta(_png())],
    )
    primero = corredor.execute()
    # Paso 1: el proveedor lo devolvio.
    assert primero["steps"][0]["usage"] is not None
    assert primero["steps"][0]["usage_note"] is None
    # Paso 2: no lo devolvio.
    assert primero["steps"][1]["usage"] is None
    assert "no devolvio" in primero["steps"][1]["usage_note"]

    # Repetido: no se pidio nada, que es otra cosa.
    corredor2, _ = _runner(ajustes, [], run_id="otro")
    segundo = corredor2.execute()
    for paso in segundo["steps"]:
        assert paso["cache_hit"] is True
        assert paso["usage"] is None
        assert "no se pidio nada" in paso["usage_note"]
        assert "No es que el proveedor devolviera cero" in paso["usage_note"]
