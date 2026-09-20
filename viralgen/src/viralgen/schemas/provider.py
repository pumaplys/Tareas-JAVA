"""Modelos que el PROVEEDOR debe devolver (Structured Outputs).

Su JSON Schema viaja a la API, por lo que solo se usan construcciones del
subconjunto admitido por Structured Outputs:

* objetos con ``additionalProperties: false`` y todas las propiedades en
  ``required`` (el SDK aplica esa transformacion estricta),
* enums (``Literal``),
* ``minItems`` / ``maxItems`` en listas,
* ``minimum`` / ``maximum`` en numeros.

Queda deliberadamente FUERA: ``minLength``/``maxLength`` en cadenas, ``format``,
valores por defecto y cualquier validador que solo exista en Python. Esos
limites se aplican despues, en local, al construir el documento final
(``schemas.document``). Lo que el modelo no decide (duraciones, conteos de
palabras, hashes, procedencia de las fuentes) tampoco aparece aqui.

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
# Comprobacion del subconjunto admitido
# ---------------------------------------------------------------------------

#: Palabras clave de JSON Schema que NO enviamos al proveedor.
UNSUPPORTED_KEYWORDS: frozenset[str] = frozenset(
    {"minLength", "maxLength", "format", "default", "pattern", "contentEncoding"}
)


def find_unsupported_keywords(schema: Any, path: str = "$") -> list[str]:
    """Devuelve las rutas del esquema que usan palabras clave no admitidas.

    Se usa en los tests para garantizar que los esquemas enviados al proveedor
    siguen dentro del subconjunto documentado de Structured Outputs.
    """
    found: list[str] = []
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key in UNSUPPORTED_KEYWORDS:
                found.append(f"{path}.{key}")
            found.extend(find_unsupported_keywords(value, f"{path}.{key}"))
    elif isinstance(schema, list):
        for index, value in enumerate(schema):
            found.extend(find_unsupported_keywords(value, f"{path}[{index}]"))
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
    "UNSUPPORTED_KEYWORDS",
    "find_unsupported_keywords",
]
