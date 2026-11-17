"""Configuracion del piloto: gpt-6.1-terra para guion, sunburst declarado.

Que demuestran estas pruebas y que NO:

* SI: que nuestro codigo envia exactamente los parametros que dice enviar, con
  la cadena de modelo que se configure, y que el SDK instalado los serializa.
  Se usa el cliente real de `openai` contra `httpx2.MockTransport`.
* NO: que el servidor de OpenAI acepte esos parametros para esos modelos. Eso
  solo lo confirma una llamada real con credenciales, y esta registrado como
  pendiente en `media/model_checks.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx2
import pytest
from openai import OpenAI
from pydantic import SecretStr

from viralgen.config import Settings
from viralgen.errors import ConfigError, ProviderError
from viralgen.media import model_checks
from viralgen.media.planner import ScenePlan, image_cache_key
from viralgen.media.providers.base import ImageRequest
from viralgen.media.providers.openai_images import OpenAIImageProvider
from viralgen.providers.base import CallBudget, ProviderRequest
from viralgen.providers.openai_provider import OpenAIProvider
from viralgen.schemas.provider import ProviderScript

TERRA = "gpt-6.1-terra"
SUNBURST = "gpt-image-2.6-sunburst"


# ---------------------------------------------------------------------------
# 1. Guion con gpt-6.1-terra: Responses + Structured Outputs
# ---------------------------------------------------------------------------


def _cliente_que_captura(capturado: dict, *, respuesta: dict | None = None) -> OpenAI:
    def handler(request: httpx2.Request) -> httpx2.Response:
        capturado["url"] = str(request.url)
        capturado["body"] = json.loads(request.content)
        return httpx2.Response(200, json=respuesta or _respuesta_vacia())

    return OpenAI(
        api_key="sk-de-prueba-no-real",
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )


def _respuesta_vacia() -> dict:
    return {
        "id": "resp_de_prueba",
        "object": "response",
        "created_at": 0,
        "model": TERRA,
        "status": "completed",
        "output": [],
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
    }


def _peticion() -> ProviderRequest:
    return ProviderRequest(
        stage="script",
        instructions="instrucciones",
        input_text="datos",
        schema_model=ProviderScript,
    )


def _ajustes_terra(tmp_path: Path, **extra) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        min_free_disk_mb=0,
        log_level="ERROR",
        openai_api_key=SecretStr("sk-de-prueba-no-real"),
        openai_model=TERRA,
        **extra,
    )


def test_el_guion_viaja_por_responses_con_el_modelo_configurado(tmp_path) -> None:
    capturado: dict = {}
    ajustes = _ajustes_terra(tmp_path)
    proveedor = OpenAIProvider(settings=ajustes, client=_cliente_que_captura(capturado))

    # La respuesta simulada viene vacia a proposito: lo que se inspecciona es
    # la SOLICITUD. El adaptador lo detecta y lo dice, en vez de devolver algo
    # a medias.
    with pytest.raises(ProviderError, match="no devolvio contenido estructurado"):
        proveedor.generate_structured(_peticion(), CallBudget(max_calls=3))

    assert capturado["url"].endswith("/v1/responses")
    cuerpo = capturado["body"]
    # La cadena de modelo viaja tal cual: no se sustituye ni se normaliza.
    assert cuerpo["model"] == TERRA
    # Structured Outputs estricto, que es el unico modo en que pedimos guiones.
    assert cuerpo["text"]["format"]["type"] == "json_schema"
    assert cuerpo["text"]["format"]["strict"] is True
    assert cuerpo["store"] is False
    assert cuerpo["max_output_tokens"] == ajustes.max_output_tokens


def test_a_terra_no_se_le_envia_temperature_por_defecto(tmp_path) -> None:
    """Hay modelos que la rechazan con unsupported_parameter; no se arriesga."""
    capturado: dict = {}
    ajustes = _ajustes_terra(tmp_path)
    assert ajustes.send_temperature is False

    proveedor = OpenAIProvider(settings=ajustes, client=_cliente_que_captura(capturado))
    with pytest.raises(ProviderError):
        proveedor.generate_structured(_peticion(), CallBudget(max_calls=3))

    assert "temperature" not in capturado["body"]
    # Y el registro deja constancia de por que sigue sin enviarse.
    entrada = model_checks.find("terra_temperature")
    assert entrada.target == TERRA
    assert "no lo envia" in entrada.assumption.lower()
    assert "unsupported_parameter" in entrada.assumption


def test_la_temperature_solo_viaja_si_se_habilita_explicitamente(tmp_path) -> None:
    capturado: dict = {}
    ajustes = _ajustes_terra(tmp_path, send_temperature=True, temperature=0.4)
    proveedor = OpenAIProvider(settings=ajustes, client=_cliente_que_captura(capturado))
    with pytest.raises(ProviderError):
        proveedor.generate_structured(_peticion(), CallBudget(max_calls=3))

    assert capturado["body"]["temperature"] == pytest.approx(0.4)


def test_el_modelo_de_guion_sigue_siendo_configurable(tmp_path) -> None:
    """Terra no se cablea: OPENAI_MODEL manda, y no hay lista blanca de texto."""
    for modelo in (TERRA, "gpt-image-1-no-sirve-pero-se-envia", "otro-modelo"):
        capturado: dict = {}
        ajustes = _ajustes_terra(tmp_path)
        ajustes = ajustes.model_copy(update={"openai_model": modelo})
        proveedor = OpenAIProvider(
            settings=ajustes, client=_cliente_que_captura(capturado)
        )
        with pytest.raises(ProviderError):
            proveedor.generate_structured(_peticion(), CallBudget(max_calls=3))
        assert capturado["body"]["model"] == modelo


def test_el_modelo_entra_en_la_identidad_del_trabajo_de_guion(tmp_path) -> None:
    """Cambiar de modelo no reutiliza en silencio un guion de otro modelo."""
    from viralgen.pipeline import Pipeline

    # El fingerprint incluye `provider.model`, asi que dos modelos distintos
    # con el mismo job_key dan huellas distintas y NO se reutilizan: el
    # pipeline lanza IdempotencyConflictError en vez de servir el guion viejo.
    assert hasattr(Pipeline, "_fingerprint")
    fuente = Path("src/viralgen/pipeline.py").read_text(encoding="utf-8")
    bloque = fuente.split("def _fingerprint")[1].split("def ")[0]
    assert '"model": provider.model' in bloque
    assert '"config_hash": self.config_hash' in bloque


# ---------------------------------------------------------------------------
# 2. Imagen con sunburst: declarado, bloqueado para real
# ---------------------------------------------------------------------------


def test_construir_el_proveedor_real_con_sunburst_falla_antes_de_la_red(tmp_path) -> None:
    """El bloqueo ocurre al construir: no hay cliente, no hay HTTP, no hay gasto."""
    ajustes = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        min_free_disk_mb=0,
        log_level="ERROR",
        openai_api_key=SecretStr("sk-de-prueba-no-real"),
        openai_image_model=SUNBURST,
    )
    with pytest.raises(ConfigError) as excinfo:
        OpenAIImageProvider(settings=ajustes)

    assert "VERIFICADO" in excinfo.value.message
    assert excinfo.value.details["model"] == SUNBURST
    assert "sunburst_endpoint" in excinfo.value.details["blocking_checks"]


def test_con_gpt_image_1_el_proveedor_real_si_se_construye(tmp_path) -> None:
    ajustes = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        min_free_disk_mb=0,
        log_level="ERROR",
        openai_api_key=SecretStr("sk-de-prueba-no-real"),
        openai_image_model="gpt-image-1",
    )
    proveedor = OpenAIImageProvider(settings=ajustes, client=object())
    assert proveedor.model == "gpt-image-1"
    assert proveedor.capabilities.verified is True


def test_la_edicion_envia_input_fidelity_solo_si_esta_configurada() -> None:
    """Y solo en la edicion: en generacion el parametro no existe."""
    base = {
        "operation_id": "op1",
        "prompt": "prompt",
        "model": SUNBURST,
        "size": "1024x1536",
        "quality": "medium",
        "output_format": "jpeg",
    }
    sin_referencias = ImageRequest(**base, input_fidelity="high")
    assert sin_referencias.uses_edit is False
    assert sin_referencias.effective_input_fidelity is None

    con_referencias = ImageRequest(
        **base, reference_paths=[Path("lumi.jpeg")], input_fidelity="high"
    )
    assert con_referencias.uses_edit is True
    assert con_referencias.effective_input_fidelity == "high"

    sin_configurar = ImageRequest(**base, reference_paths=[Path("lumi.jpeg")])
    assert sin_configurar.effective_input_fidelity is None


# ---------------------------------------------------------------------------
# 3. Identidad de cache: modelo y parametros efectivos
# ---------------------------------------------------------------------------


def _escena() -> ScenePlan:
    return ScenePlan(
        scene_id="s1",
        order=0,
        requested_type="image",
        start_sample=0,
        end_sample=24_000,
        duration_s=1.0,
        character_ids=["lumi"],
        effective_prompt="prompt",
        prompt_chars=6,
        image_size="1024x1536",
        image_quality="medium",
        image_format="jpeg",
        needs_video=False,
    )


def test_el_modelo_forma_parte_de_la_identidad_del_asset() -> None:
    escena = _escena()
    comun = {"reference_hashes": ["aa"], "simulation": False}
    clave_1 = image_cache_key(plan=escena, model="gpt-image-1", **comun)
    clave_2 = image_cache_key(plan=escena, model=SUNBURST, **comun)
    assert clave_1 != clave_2


def test_la_fidelidad_forma_parte_de_la_identidad_del_asset() -> None:
    escena = _escena()
    comun = {"model": SUNBURST, "reference_hashes": ["aa"], "simulation": False}
    sin = image_cache_key(plan=escena, **comun)
    baja = image_cache_key(plan=escena, input_fidelity="low", **comun)
    alta = image_cache_key(plan=escena, input_fidelity="high", **comun)
    assert len({sin, baja, alta}) == 3


def test_los_parametros_efectivos_forman_parte_de_la_identidad() -> None:
    """Tamano, calidad y formato: cambiar cualquiera produce otro asset."""
    base = image_cache_key(
        plan=_escena(), model="gpt-image-1", reference_hashes=[], simulation=False
    )
    variantes = set()
    for campo, valor in (
        ("image_size", "1024x1024"),
        ("image_quality", "high"),
        ("image_format", "png"),
        ("effective_prompt", "otro prompt"),
    ):
        escena = _escena()
        setattr(escena, campo, valor)
        variantes.add(
            image_cache_key(
                plan=escena, model="gpt-image-1", reference_hashes=[], simulation=False
            )
        )
    assert base not in variantes
    assert len(variantes) == 4


def test_la_simulacion_y_la_produccion_no_comparten_identidad() -> None:
    """Separacion simulacion/produccion: ni por accidente comparten asset."""
    escena = _escena()
    comun = {"model": "gpt-image-1", "reference_hashes": ["aa"]}
    simulado = image_cache_key(plan=escena, simulation=True, **comun)
    real = image_cache_key(plan=escena, simulation=False, **comun)
    assert simulado != real


def test_la_fidelidad_no_cambia_la_identidad_de_una_generacion_sin_referencias() -> None:
    """Coherencia con effective_input_fidelity: si no se envia, no es identidad."""
    escena = _escena()
    comun = {"model": SUNBURST, "reference_hashes": [], "simulation": False}
    # Asi es como lo llama el planificador cuando la escena no tiene
    # referencias: input_fidelity=None aunque este configurada.
    sin_referencias_a = image_cache_key(plan=escena, input_fidelity=None, **comun)
    sin_referencias_b = image_cache_key(plan=escena, **comun)
    assert sin_referencias_a == sin_referencias_b


def test_sin_fidelidad_la_clave_es_la_de_antes_de_que_existiera() -> None:
    """Anadir el parametro NO invalida los assets ya cacheados.

    Si la clave `input_fidelity` apareciera en el payload como `None`, el hash
    cambiaria para todos los assets generados antes y habria que regenerarlos
    -y pagarlos- sin que nada del resultado haya cambiado. Esta prueba fija que
    la clave solo se anade cuando el parametro viaja de verdad.
    """
    from viralgen.media.prompts import MEDIA_PROMPT_VERSION
    from viralgen.textutil import sha256_json

    escena = _escena()
    esperado = sha256_json(
        {
            "operation": "image_edit",
            "prompt": escena.effective_prompt,
            "prompt_version": MEDIA_PROMPT_VERSION,
            "model": "gpt-image-1",
            "size": escena.image_size,
            "quality": escena.image_quality,
            "output_format": escena.image_format,
            "references": ["aa"],
            "simulation": False,
        }
    )
    sin_fidelidad = image_cache_key(
        plan=escena, model="gpt-image-1", reference_hashes=["aa"], simulation=False
    )
    assert sin_fidelidad == esperado

    # Y con fidelidad si cambia, que es el objetivo.
    con_fidelidad = image_cache_key(
        plan=escena,
        model="gpt-image-1",
        reference_hashes=["aa"],
        simulation=False,
        input_fidelity="high",
    )
    assert con_fidelidad != esperado


def test_el_payload_del_proveedor_coincide_con_el_del_planificador() -> None:
    """Si divergen, el plan predice una clave distinta de la que se usa."""
    from viralgen.textutil import sha256_json

    peticion = ImageRequest(
        operation_id="op1",
        prompt="prompt",
        model="gpt-image-1",
        size="1024x1536",
        quality="medium",
        output_format="jpeg",
        reference_paths=[Path("lumi.jpeg")],
    )
    payload = peticion.identity_payload(["aa"], False)
    assert "input_fidelity" not in payload

    con = ImageRequest(
        operation_id="op1",
        prompt="prompt",
        model="gpt-image-1",
        size="1024x1536",
        quality="medium",
        output_format="jpeg",
        reference_paths=[Path("lumi.jpeg")],
        input_fidelity="high",
    ).identity_payload(["aa"], False)
    assert con["input_fidelity"] == "high"
    assert sha256_json(payload) != sha256_json(con)
