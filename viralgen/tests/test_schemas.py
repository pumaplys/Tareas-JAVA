"""El contrato JSON y el subconjunto de esquema admitido por el proveedor."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from viralgen.schemas.document import ScriptDocument
from viralgen.schemas.provider import PROVIDER_MODELS, find_unsupported_keywords


@pytest.mark.parametrize("modelo", PROVIDER_MODELS, ids=lambda m: m.__name__)
def test_esquema_del_proveedor_usa_solo_el_subconjunto_admitido(modelo) -> None:
    """Structured Outputs no admite minLength/maxLength/format/default."""
    encontrados = find_unsupported_keywords(modelo.model_json_schema())
    assert encontrados == [], f"palabras clave no admitidas: {encontrados}"


@pytest.mark.parametrize("modelo", PROVIDER_MODELS, ids=lambda m: m.__name__)
def test_esquema_del_proveedor_prohibe_campos_extra(modelo) -> None:
    schema = modelo.model_json_schema()
    for nombre, definicion in schema.get("$defs", {}).items():
        if definicion.get("type") == "object":
            assert definicion.get("additionalProperties") is False, nombre
    assert schema.get("additionalProperties") is False


def test_documento_prohibe_campos_adicionales() -> None:
    with pytest.raises(ValidationError):
        ScriptDocument.model_validate({"campo_inventado": 1})


def test_json_schema_del_documento_es_exportable() -> None:
    schema = ScriptDocument.model_json_schema()
    assert schema["properties"]["schema_version"]["const"] == "1.0"
    assert "scenes" in schema["properties"]
    assert schema["additionalProperties"] is False
