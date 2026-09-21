"""Modelos que el PROVEEDOR debe devolver (Structured Outputs).

Su JSON Schema viaja a la API. Lo que si se usa:

* objetos con ``additionalProperties: false`` y todas las propiedades en
  ``required`` (esa transformacion estricta la aplica el SDK),
* enums (``Literal``),
* ``minItems`` / ``maxItems`` en listas,
* ``minimum`` / ``maximum`` en numeros.

**Decision del proyecto: el esquema enviado es deliberadamente conservador.**
Se dejan fuera las restricciones de cadena (``minLength``, ``maxLength``,
``pattern``, ``format``) y los valores por defecto. Esto NO significa que la
API las prohiba todas: la documentacion oficial admite ``pattern`` y un
conjunto concreto de valores de ``format``, y describe restricciones
adicionales para modelos *fine-tuned*. Se omiten aqui por dos razones:

1. el conjunto exacto admitido depende del modelo y de la version de la API,
   asi que un esquema minimo reduce el riesgo de rechazo por esquema;
2. esos limites se aplican igualmente en local al construir el documento final
   (``schemas.document``), que es donde importan para el contrato.

Lo que el modelo no decide (duraciones, conteos de palabras, hashes,
procedencia de las fuentes, biblia visual, ``experiment_tag``) tampoco aparece
aqui.

Referencia: https://developers.openai.com/api/docs/guides/structured-outputs
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .common import AssetType, Beat, Platform, SfxCue


class ProviderModel(BaseModel):
    """Base de los modelos de respuesta: sin campos extra, sin defaults."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Etapa 1: ideas
# ---------------------------------------------------------------------------


class ProviderRating(ProviderModel):
    """Valoracion editorial razonada, de 0 a 5."""

    score: int = Field(ge=0, le=5)
    rationale: str


class ProviderRatings(ProviderModel):
    """Las cuatro valoraciones editoriales que aporta el modelo.

    ``novelty`` NO esta aqui: la calcula la aplicacion contra el historial.
    """

    hook: ProviderRating
    clarity: ProviderRating
    payoff: ProviderRating
    visual_potential: ProviderRating


class ProviderIdea(ProviderModel):
    idea_ref: str
    """Identificador local de la idea dentro de la tanda, p. ej. 'idea_1'."""

    title: str
    premise: str
    topic: str
    promise: str
    possible_ending: str
    visual_concept: str
    educational_goal: str | None
    fact_ids: list[str] = Field(max_length=6)
    """fact_id del catalogo aportado. Vacio en ficcion infantil."""

    ratings: ProviderRatings


class ProviderIdeaBatch(ProviderModel):
    ideas: list[ProviderIdea] = Field(min_length=1, max_length=8)


# ---------------------------------------------------------------------------
# Etapa 2: guion completo
# ---------------------------------------------------------------------------


class ProviderHookVariant(ProviderModel):
    hook_id: str
    text: str


class ProviderSceneVisual(ProviderModel):
    asset_type: AssetType
    image_prompt: str
    motion_prompt: str
    continuity_notes: str


class ProviderSceneCaptions(ProviderModel):
    emphasis_words: list[str] = Field(max_length=8)
    overlay_text: str | None


class ProviderSceneAudio(ProviderModel):
    sfx_description: str | None
    sfx_cue: SfxCue
    music_mood: str | None


class ProviderScene(ProviderModel):
    scene_id: str
    order: int = Field(ge=1, le=40)
    beat: Beat
    narration_text: str
    pause_after_s: float = Field(ge=0.0, le=1.5)
    character_ids: list[str] = Field(max_length=8)
    visual: ProviderSceneVisual
    captions: ProviderSceneCaptions
    audio: ProviderSceneAudio
    claim_refs: list[str] = Field(max_length=6)


class ProviderClaim(ProviderModel):
    claim_id: str
    claim_text: str
    scene_ids: list[str] = Field(min_length=1, max_length=20)
    fact_ids: list[str] = Field(min_length=1, max_length=6)


class ProviderLoop(ProviderModel):
    enabled: bool
    opening_scene_id: str | None
    closing_scene_id: str | None
    connection_explanation: str | None


class ProviderPublishing(ProviderModel):
    platform: Platform
    title: str
    caption: str
    hashtags: list[str] = Field(max_length=12)


class ProviderScript(ProviderModel):
    title: str
    premise: str
    topic: str
    educational_goal: str | None
    hook_variants: list[ProviderHookVariant] = Field(min_length=3, max_length=3)
    selected_hook_id: str
    voice_direction: str
    pronunciation_notes: list[str] = Field(max_length=12)
    scenes: list[ProviderScene] = Field(min_length=3, max_length=20)
    loop: ProviderLoop
    claims: list[ProviderClaim] = Field(max_length=20)
    publishing: list[ProviderPublishing] = Field(min_length=1, max_length=3)


# ---------------------------------------------------------------------------
# Comprobacion del esquema conservador
# ---------------------------------------------------------------------------

#: Palabras clave de JSON Schema que este proyecto decide NO enviar.
#: Algunas si estan admitidas por la API (vease el docstring del modulo): la
#: lista fija una decision de diseno, no una prohibicion del proveedor.
OMITTED_KEYWORDS: frozenset[str] = frozenset(
    {"minLength", "maxLength", "format", "default", "pattern", "contentEncoding"}
)


def find_omitted_keywords(schema: Any, path: str = "$") -> list[str]:
    """Devuelve las rutas del esquema que usan palabras clave omitidas.

    Se usa en los tests para que el esquema enviado al proveedor siga siendo el
    conservador que documentamos, y que ampliarlo sea una decision deliberada.
    """
    found: list[str] = []
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key in OMITTED_KEYWORDS:
                found.append(f"{path}.{key}")
            found.extend(find_omitted_keywords(value, f"{path}.{key}"))
    elif isinstance(schema, list):
        for index, value in enumerate(schema):
            found.extend(find_omitted_keywords(value, f"{path}[{index}]"))
    return found


PROVIDER_MODELS: tuple[type[ProviderModel], ...] = (ProviderIdeaBatch, ProviderScript)


__all__ = [
    "PROVIDER_MODELS",
    "ProviderClaim",
    "ProviderHookVariant",
    "ProviderIdea",
    "ProviderIdeaBatch",
    "ProviderLoop",
    "ProviderModel",
    "ProviderPublishing",
    "ProviderRating",
    "ProviderRatings",
    "ProviderScene",
    "ProviderSceneAudio",
    "ProviderSceneCaptions",
    "ProviderSceneVisual",
    "ProviderScript",
    "OMITTED_KEYWORDS",
    "find_omitted_keywords",
]
