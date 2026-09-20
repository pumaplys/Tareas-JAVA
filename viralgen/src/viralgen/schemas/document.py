"""Contrato JSON final (``script.json``) que consumen los modulos 2-5.

Especificacion de campos, no ejemplo de valores. Reglas generales:

* ``extra="forbid"`` en todos los modelos: ningun campo adicional.
* Enums para todo valor cerrado, limites de longitud en las cadenas y
  colecciones acotadas.
* Los campos derivados (duraciones, ``word_count``, ``full_text``, hashes,
  uso de tokens) los calcula la APLICACION, nunca el modelo.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    StringConstraints,
)

from .common import (
    AssetType,
    Beat,
    Channel,
    Platform,
    ProductionStatus,
    ReviewStatus,
    SfxCue,
    VerificationLevel,
)

SCHEMA_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Tipos base reutilizables
# ---------------------------------------------------------------------------


def _ensure_utc(value: datetime) -> datetime:
    """Exige fecha con zona horaria y la normaliza a UTC."""
    if value.tzinfo is None:
        raise ValueError("la fecha debe llevar zona horaria explicita (UTC)")
    return value.astimezone(timezone.utc).replace(microsecond=0)


UtcDatetime = Annotated[
    datetime,
    AfterValidator(_ensure_utc),
    PlainSerializer(
        lambda dt: dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        return_type=str,
        when_used="json",
    ),
]


def _validate_http_url(value: str) -> str:
    """Valida esquema y host sin reescribir la URL original.

    Una URL sintacticamente valida NO demuestra que respalde el dato: solo
    comprobamos que sea http/https y tenga host.
    """
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("source_url debe usar http o https")
    if not parsed.netloc:
        raise ValueError("source_url debe incluir un host")
    return value


HttpUrlStr = Annotated[
    str,
    StringConstraints(min_length=8, max_length=2048, strip_whitespace=True),
    AfterValidator(_validate_http_url),
]

Identifier = Annotated[
    str,
    StringConstraints(
        min_length=1, max_length=64, strip_whitespace=True, pattern=r"^[a-z0-9][a-z0-9_\-]*$"
    ),
]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=200, strip_whitespace=True)]
MediumText = Annotated[str, StringConstraints(min_length=1, max_length=600, strip_whitespace=True)]
LongText = Annotated[str, StringConstraints(min_length=1, max_length=4000, strip_whitespace=True)]
Word = Annotated[str, StringConstraints(min_length=1, max_length=48, strip_whitespace=True)]
LanguageCode = Annotated[str, StringConstraints(pattern=r"^[a-z]{2}(-[A-Z]{2})?$")]
HexHash = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class StrictModel(BaseModel):
    """Base comun: prohibe campos adicionales y valida en la asignacion."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# ---------------------------------------------------------------------------
# Control
# ---------------------------------------------------------------------------


class Control(StrictModel):
    """Estado del documento y trazabilidad de la configuracion usada."""

    production_status: ProductionStatus = Field(
        description=(
            "ready_for_production solo significa que el plan puede pasar a "
            "producir medios: no autoriza publicaciones ni certifica monetizacion."
        )
    )
    warnings: list[ShortText] = Field(
        default_factory=list,
        max_length=50,
        description="Motivos legibles por los que el documento requiere revision.",
    )
    prompt_version: Identifier = Field(description="Version de las plantillas de prompt.")
    config_hash: HexHash = Field(description="SHA-256 de ajustes + perfil + biblia visual.")
    source_pack_hash: HexHash | None = Field(
        default=None, description="SHA-256 del catalogo de hechos usado; null si no aplica."
    )


# ---------------------------------------------------------------------------
# Idea
# ---------------------------------------------------------------------------


class ScoreComponents(StrictModel):
    """Componentes de la puntuacion editorial, todos entre 0 y 5.

    ``novelty`` la calcula la aplicacion: 5 x (1 - similitud maxima).
    Los otros cuatro los razona el modelo y se guardan tal cual.
    """

    hook: float = Field(ge=0, le=5)
    clarity: float = Field(ge=0, le=5)
    payoff: float = Field(ge=0, le=5)
    visual_potential: float = Field(ge=0, le=5)
    novelty: float = Field(ge=0, le=5)


class HookVariant(StrictModel):
    """Alternativa de gancho. Solo la seleccionada forma parte de la narracion."""

    hook_id: Identifier
    text: ShortText


class Idea(StrictModel):
    title: ShortText
    premise: MediumText
    topic: ShortText
    selected_idea_id: Identifier
    editorial_score: float = Field(
        ge=0,
        le=100,
        description=(
            "20 x (0,25*hook + 0,20*clarity + 0,20*payoff + 0,15*visual_potential "
            "+ 0,20*novelty). Es una heuristica editorial, no una probabilidad de viralidad."
        ),
    )
    score_components: ScoreComponents
    score_rationale: MediumText
    hook_variants: list[HookVariant] = Field(min_length=3, max_length=3)
    selected_hook_id: Identifier
    educational_goal: MediumText | None = None
    experiment_tag: Identifier = Field(
        description="Etiqueta para comparar tandas: prompt_version + modelo + perfil."
    )


# ---------------------------------------------------------------------------
# Video
# ---------------------------------------------------------------------------


class Video(StrictModel):
    width: int = Field(ge=240, le=4320)
    height: int = Field(ge=240, le=7680)
    fps: int = Field(ge=1, le=120)
    aspect_ratio: Literal["9:16", "1:1", "4:5", "16:9"]
    target_duration_s: float = Field(gt=0, le=600)
    estimated_duration_s: float = Field(
        gt=0, le=600, description="Calculado localmente a partir de palabras y pausas."
    )
    actual_duration_s: None = Field(
        default=None, description="Lo rellenara el modulo 2 tras medir la voz real."
    )


# ---------------------------------------------------------------------------
# Narracion
# ---------------------------------------------------------------------------


class Narration(StrictModel):
    full_text: LongText = Field(
        description="Concatenacion en orden de narration_text de las escenas (la construye la app)."
    )
    word_count: int = Field(ge=1, le=5000)
    target_wpm: int = Field(ge=60, le=260)
    voice_direction: MediumText
    pronunciation_notes: list[ShortText] = Field(default_factory=list, max_length=12)


# ---------------------------------------------------------------------------
# Biblia visual
# ---------------------------------------------------------------------------


class VisualCharacter(StrictModel):
    """Personaje estable de la serie. No se reinventa entre episodios."""

    character_id: Identifier
    name: ShortText
    description: MediumText = Field(description="Aspecto estable del personaje.")
    wardrobe: MediumText = Field(description="Ropa estable del personaje.")


class VisualBible(StrictModel):
    style_prompt: MediumText
    color_palette: list[Word] = Field(min_length=2, max_length=8)
    negative_prompt: MediumText = Field(
        description="Incluye evitar texto y marcas de agua: los rotulos se anaden en montaje."
    )
    characters: list[VisualCharacter] = Field(min_length=1, max_length=8)


# ---------------------------------------------------------------------------
# Escenas
# ---------------------------------------------------------------------------


class SceneVisual(StrictModel):
    asset_type: AssetType
    image_prompt: MediumText = Field(
        description="Prompt autocontenido de imagen: personajes, accion, encuadre, iluminacion."
    )
    motion_prompt: MediumText = Field(
        description="Movimiento solicitado, separado del prompt de imagen."
    )
    continuity_notes: MediumText


class SceneCaptions(StrictModel):
    """Los subtitulos completos provienen de narration_text, sin resumirla.

    Este modulo no genera timestamps de palabra ni archivos SRT/ASS.
    """

    emphasis_words: list[Word] = Field(default_factory=list, max_length=8)
    overlay_text: ShortText | None = None


class SceneAudio(StrictModel):
    """Los tiempos finales de los efectos dependeran del audio real (modulo 2)."""

    sfx_description: ShortText | None = None
    sfx_cue: SfxCue
    music_mood: ShortText | None = None


class Scene(StrictModel):
    scene_id: Identifier
    order: int = Field(ge=1, le=40)
    beat: Beat
    narration_text: MediumText
    pause_after_s: float = Field(
        ge=0.0, le=1.5, description="Pausa al FINAL de la escena, en segundos."
    )
    word_count: int = Field(ge=1, le=400)
    estimated_start_s: float = Field(ge=0, le=600)
    estimated_end_s: float = Field(ge=0, le=600)
    estimated_duration_s: float = Field(gt=0, le=120)
    character_ids: list[Identifier] = Field(default_factory=list, max_length=8)
    visual: SceneVisual
    captions: SceneCaptions
    audio: SceneAudio
    claim_refs: list[Identifier] = Field(default_factory=list, max_length=6)


# ---------------------------------------------------------------------------
# Loop
# ---------------------------------------------------------------------------


class Loop(StrictModel):
    """Conexion narrativa entre final e inicio. No es repetir material."""

    enabled: bool
    opening_scene_id: Identifier | None = None
    closing_scene_id: Identifier | None = None
    connection_explanation: MediumText | None = None


# ---------------------------------------------------------------------------
# Evidencia
# ---------------------------------------------------------------------------


class FactRecord(StrictModel):
    """Registro copiado literalmente del catalogo importado.

    Los campos de procedencia NO se confian al modelo: se copian del catalogo.
    """

    fact_id: Identifier
    claim_text: MediumText
    source_url: HttpUrlStr
    source_title: ShortText
    evidence_excerpt: MediumText
    checked_at: UtcDatetime
    review_status: ReviewStatus
    demo_only: bool


class Claim(StrictModel):
    """Afirmacion factual del guion ligada a uno o varios hechos del catalogo."""

    claim_id: Identifier
    claim_text: MediumText
    scene_ids: list[Identifier] = Field(min_length=1, max_length=20)
    fact_ids: list[Identifier] = Field(min_length=1, max_length=6)


class Evidence(StrictModel):
    claims: list[Claim] = Field(default_factory=list, max_length=20)
    facts: list[FactRecord] = Field(
        default_factory=list,
        max_length=30,
        description="Solo los registros del catalogo realmente utilizados.",
    )
    verification_level: VerificationLevel


# ---------------------------------------------------------------------------
# Publicacion futura (la decide el modulo 5)
# ---------------------------------------------------------------------------


class PublishingPlan(StrictModel):
    """Borrador por plataforma. Sin horarios, privacidad ni promesas de ingresos."""

    platform: Platform
    title: ShortText
    caption: MediumText
    hashtags: list[Word] = Field(default_factory=list, max_length=12)
    made_for_kids: bool | None = None
    ai_disclosure_review_required: Literal[True] = True


# ---------------------------------------------------------------------------
# Procedencia tecnica
# ---------------------------------------------------------------------------


class Provenance(StrictModel):
    """Datos de uso tomados de respuestas reales del proveedor."""

    provider: Identifier
    model: ShortText
    request_ids: list[ShortText] = Field(default_factory=list, max_length=20)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    estimated_cost_usd: float | None = Field(
        default=None,
        ge=0,
        description="null si no hay tarifas explicitas configuradas para el modelo.",
    )


# ---------------------------------------------------------------------------
# Documento
# ---------------------------------------------------------------------------


class ScriptDocument(StrictModel):
    """Plan completo de un video. Unica salida del modulo 1."""

    # --- Identidad (la asigna la aplicacion) ---
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    job_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f\-]{36}$")]
    created_at: UtcDatetime
    channel: Channel
    profile_id: Identifier
    language: LanguageCode
    target_platforms: list[Platform] = Field(min_length=1, max_length=3)
    simulation: bool

    # --- Grupos ---
    control: Control
    idea: Idea
    video: Video
    narration: Narration
    visual_bible: VisualBible
    scenes: list[Scene] = Field(min_length=3, max_length=20)
    loop: Loop
    evidence: Evidence
    publishing: list[PublishingPlan] = Field(min_length=1, max_length=3)
    provenance: Provenance
