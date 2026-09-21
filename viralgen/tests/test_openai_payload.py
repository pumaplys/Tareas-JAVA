"""Payload que serializa realmente el SDK instalado.

Se usa el cliente de `openai` de verdad con un transporte HTTP simulado
(`httpx2.MockTransport`): no hay red ni claves reales, pero la solicitud pasa
por el mismo camino de serializacion que en produccion.

LIMITE DE ESTA PRUEBA: comprueba lo que el SDK ENVIA. No demuestra que el
servidor de OpenAI acepte el esquema; eso solo lo confirma una llamada real.
"""

from __future__ import annotations

import json

import httpx2
import pytest
from openai import OpenAI

from viralgen.schemas.provider import PROVIDER_MODELS, ProviderScript


def _capturar(modelo, **extra) -> dict:
    """Emite una peticion contra un transporte falso y devuelve el cuerpo."""
    capturado: dict = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        capturado["url"] = str(request.url)
        capturado["body"] = json.loads(request.content)
        return httpx2.Response(
            200,
            json={
                "id": "resp_de_prueba",
                "object": "response",
                "created_at": 0,
                "model": "modelo-de-prueba",
                "status": "completed",
                "output": [],
                "parallel_tool_calls": False,
                "tool_choice": "auto",
                "tools": [],
            },
        )

    cliente = OpenAI(
        api_key="sk-de-prueba-no-real",
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )
    # La respuesta simulada viene vacia a proposito: lo que se inspecciona es
    # la SOLICITUD, no lo que devuelve el servidor.
    cliente.responses.parse(
        model="modelo-de-prueba",
        instructions="instrucciones",
        input="datos",
        text_format=modelo,
        max_output_tokens=100,
        store=False,
        **extra,
    )
    return capturado


def _objetos(schema: dict):
    """Recorre el esquema y devuelve todos los subesquemas de tipo objeto."""
    pendientes = [schema]
    while pendientes:
        actual = pendientes.pop()
        if isinstance(actual, dict):
            if actual.get("type") == "object":
                yield actual
            pendientes.extend(actual.values())
        elif isinstance(actual, list):
            pendientes.extend(actual)


@pytest.mark.parametrize("modelo", PROVIDER_MODELS, ids=lambda m: m.__name__)
def test_formato_estructurado_enviado(modelo) -> None:
    capturado = _capturar(modelo)
    assert capturado["url"].endswith("/v1/responses")
    cuerpo = capturado["body"]

    formato = cuerpo["text"]["format"]
    assert formato["type"] == "json_schema"
    assert formato["strict"] is True
    assert formato["name"] == modelo.__name__
    assert "schema" in formato


@pytest.mark.parametrize("modelo", PROVIDER_MODELS, ids=lambda m: m.__name__)
def test_required_y_additional_properties_en_todo_el_esquema(modelo) -> None:
    """El SDK exige additionalProperties=false y TODAS las claves en required."""
    schema = _capturar(modelo)["body"]["text"]["format"]["schema"]
    objetos = list(_objetos(schema))
    assert objetos, "el esquema deberia contener objetos"
    for objeto in objetos:
        assert objeto.get("additionalProperties") is False
        propiedades = set(objeto.get("properties", {}))
        if propiedades:
            assert set(objeto.get("required", [])) == propiedades


def test_campos_nullable_van_en_required_como_union() -> None:
    """Un campo opcional no se omite: se declara union con null y sigue en required."""
    schema = _capturar(ProviderScript)["body"]["text"]["format"]["schema"]
    captions = schema["$defs"]["ProviderSceneCaptions"]
    assert "overlay_text" in captions["required"]
    tipos = {rama["type"] for rama in captions["properties"]["overlay_text"]["anyOf"]}
    assert tipos == {"string", "null"}

    assert "educational_goal" in schema["required"]


def test_el_esquema_enviado_no_lleva_default() -> None:
    """El SDK elimina los `default`; comprobamos que no quedan restos."""
    schema = _capturar(ProviderScript)["body"]["text"]["format"]["schema"]
    texto = json.dumps(schema)
    assert '"default"' not in texto


def test_sin_temperature_y_con_limite_de_salida() -> None:
    cuerpo = _capturar(ProviderScript)["body"]
    assert "temperature" not in cuerpo
    assert cuerpo["max_output_tokens"] == 100
    assert cuerpo["store"] is False


def test_el_esquema_enviado_se_mantiene_conservador() -> None:
    """Decision del proyecto: no enviamos restricciones de cadena.

    `pattern` y ciertos valores de `format` si estan admitidos por la API, pero
    este proyecto los deja fuera a proposito y valida esos limites en local
    (`schemas.document`). La prueba fija esa decision para que un cambio sea
    deliberado y no accidental.
    """
    schema = _capturar(ProviderScript)["body"]["text"]["format"]["schema"]
    texto = json.dumps(schema)
    for palabra in ("minLength", "maxLength", "format", "pattern"):
        assert f'"{palabra}"' not in texto
