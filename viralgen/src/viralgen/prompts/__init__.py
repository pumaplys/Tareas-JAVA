"""Plantillas de prompt versionadas.

Las plantillas viven en `prompts/<version>/*.md` y se empaquetan con la
libreria. `PROMPT_VERSION` viaja en el documento exportado y en el fingerprint
de idempotencia, de modo que se puedan comparar resultados entre versiones.

La sustitucion usa marcadores ``{{clave}}`` en lugar de `str.format` para que
las llaves del texto (JSON de ejemplo, por ejemplo) no rompan el renderizado.
"""

from __future__ import annotations

import re
from functools import lru_cache
from importlib import resources

from .. import PROMPT_VERSION
from ..textutil import sha256_json

_PLACEHOLDER_RE = re.compile(r"\{\{([a-z0-9_]+)\}\}")

TEMPLATE_NAMES = (
    "system_common",
    "ideas_infantil",
    "ideas_curiosidades",
    "script_infantil",
    "script_curiosidades",
    "repair",
)


@lru_cache(maxsize=32)
def load_template(name: str, version: str = PROMPT_VERSION) -> str:
    package = f"viralgen.prompts.{version}"
    return resources.files(package).joinpath(f"{name}.md").read_text(encoding="utf-8")


def render(name: str, context: dict[str, str], version: str = PROMPT_VERSION) -> str:
    """Renderiza una plantilla sustituyendo los marcadores ``{{clave}}``."""
    template = load_template(name, version)
    missing: list[str] = []

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in context:
            missing.append(key)
            return match.group(0)
        return str(context[key])

    rendered = _PLACEHOLDER_RE.sub(replace, template)
    if missing:
        raise KeyError(f"Faltan variables en la plantilla {name}: {sorted(set(missing))}")
    return rendered


@lru_cache(maxsize=4)
def templates_digest(version: str = PROMPT_VERSION) -> str:
    """Huella del conjunto de plantillas de una version."""
    return sha256_json(
        {name: load_template(name, version) for name in TEMPLATE_NAMES}
    )


__all__ = ["PROMPT_VERSION", "TEMPLATE_NAMES", "load_template", "render", "templates_digest"]
