"""Contrato del manifiesto lateral `media.json`.

Esquema INDEPENDIENTE (1.0). `script.json` y `voice.json` se quedan en los
suyos y no se tocan.

Reglas del contrato:

* `extra="forbid"`, enums para lo cerrado y colecciones acotadas.
* Todas las rutas son RELATIVAS a la carpeta del manifiesto y sin escapes.
* Nunca se guarda base64, credenciales ni URLs firmadas.
* Cada magnitud dice DE DONDE sale: medida en local, informada por el
  proveedor, o decision de montaje.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator

from ..schemas.document import (
    HexHash,
    Identifier,
    MediumText,
    ShortText,
    StrictModel,
    UtcDatetime,
)

MEDIA_SCHEMA_VERSION = "1.0"


def _no_path_escape(value: str) -> str:
    candidato = value.replace("\\", "/")
    partes = candidato.split("/")
    if candidato.startswith("/") or ".." in partes or ":" in candidato:
        raise ValueError("la ruta debe ser relativa al manifiesto y sin escapes")
    return candidato


RelativePath = Annotated[
    str, StringConstraints(min_length=1, max_length=400, strip_whitespace=True)
]
PromptText = Annotated[str, StringConstraints(min_length=1, max_length=8000)]


class MediaStatus(StrEnum):
    READY = "ready"
    NEEDS_REVIEW = "needs_review"


class IssueSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class VisualReview(StrEnum):
    """Revision artistica humana. `ready` es preparacion TECNICA, no aprobacion."""

    NOT_PERFORMED = "not_performed"
    APPROVED = "approved"
    REJECTED = "rejected"


class AssetKind(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    CONTACT_SHEET = "contact_sheet"


class AssetRole(StrEnum):
    SCENE_IMAGE = "scene_image"
    VIDEO_SEED = "video_seed"
    SCENE_CLIP = "scene_clip"
    CHARACTER_REFERENCE = "character_reference"
    INSPECTION = "inspection"


class MeasurementSource(StrEnum):
    """De donde sale un dato numerico."""

    LOCAL_MEASUREMENT = "local_measurement"
    PROVIDER_REPORTED = "provider_reported"
    MONTAGE_DECISION = "montage_decision"


class ReferenceProvenanceKind(StrEnum):
    IMPORTED = "imported"
    GENERATED = "generated"


class GeometryPolicy(StrEnum):
    CONTAIN = "contain"
    CROP = "crop"


# ---------------------------------------------------------------------------
# Fuentes y control
# ---------------------------------------------------------------------------


class MediaSource(StrictModel):
    """Vinculo con guion y voz. Los hashes comprueban correspondencia."""

    script_schema_version: ShortText
    script_sha256: HexHash
    script_simulation: bool
    voice_schema_version: ShortText
    voice_sha256: HexHash
    voice_simulation: bool
    profile_id: Identifier
    channel: Identifier


class MediaIssue(StrictModel):
    code: Identifier
    message: ShortText
    severity: IssueSeverity
    blocking: bool
    scene_id: Identifier | None = None


class MediaControl(StrictModel):
    media_status: MediaStatus
    issues: list[MediaIssue] = Field(default_factory=list, max_length=200)
    visual_review: VisualReview = VisualReview.NOT_PERFORMED
    admissible_for_assembly: bool = Field(
        description=(
            "Resultado calculado. Un consumidor NO debe fiarse de este booleano: "
            "debe repetir la comprobacion sobre los archivos reales."
        )
    )

    @field_validator("admissible_for_assembly")
    @classmethod
    def _coherent(cls, value: bool, info) -> bool:
        if value and info.data.get("media_status") is not MediaStatus.READY:
            raise ValueError("admissible_for_assembly exige media_status=ready")
        return value


class MediaTimeline(StrictModel):
    """Reloj copiado del maestro de voz, mas el objetivo de entrega."""

    sample_rate_hz: int = Field(ge=8_000, le=48_000)
    total_samples: int = Field(ge=1)
    total_duration_s: float = Field(gt=0)
    source: Literal["voice_manifest"] = "voice_manifest"
    target_width: int = Field(ge=240, le=4320)
    target_height: int = Field(ge=240, le=7680)
    target_fps: int = Field(ge=1, le=120)
    target_source: Literal["montage_decision"] = "montage_decision"


# ---------------------------------------------------------------------------
# Referencias
# ---------------------------------------------------------------------------


class ReferenceProvenance(StrictModel):
    """De donde sale una referencia. No se inventa ninguna verificacion."""

    kind: ReferenceProvenanceKind
    rights_declaration: MediumText = Field(
        description=(
            "Declaracion de derechos APORTADA por quien importa el archivo. "
            "El modulo no la verifica ni la puede verificar."
        )
    )
    provider: Identifier | None = None
    model: ShortText | None = None
    parameters_hash: HexHash | None = None
    simulation: bool = False
    imported_from: ShortText | None = None


class ReferenceEntry(StrictModel):
    character_id: Identifier
    path: RelativePath
    sha256: HexHash
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    mime: ShortText
    provenance: ReferenceProvenance

    @field_validator("path")
    @classmethod
    def _relative(cls, value: str) -> str:
        return _no_path_escape(value)


class ReferenceSet(StrictModel):
    """Conjunto fijado por serie y version.

    Las referencias mejoran el control, pero NO garantizan identidad perfecta.
    Este modulo no certifica continuidad semantica con hashes ni con busquedas
    de palabras.
    """

    set_id: Identifier
    version: Identifier
    series_bible_id: Identifier
    bible_sha256: HexHash
    entries: list[ReferenceEntry] = Field(default_factory=list, max_length=20)


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------


class VideoDetails(StrictModel):
    """Datos propios de un clip. Separa lo pedido de lo medido."""

    task_id: ShortText | None = Field(
        default=None, description="Identificador REAL de la tarea remota, si existio."
    )
    init_image_sha256: HexHash
    requested_duration_s: float = Field(gt=0, le=600)
    requested_duration_source: Literal["montage_decision"] = "montage_decision"
    measured_duration_s: float = Field(gt=0, le=600)
    measured_duration_source: MeasurementSource = MeasurementSource.LOCAL_MEASUREMENT
    fps_rational: ShortText
    fps: float = Field(gt=0, le=1000)
    fps_source: MeasurementSource = MeasurementSource.LOCAL_MEASUREMENT
    codec: ShortText
    container: ShortText
    has_audio: bool
    use_source_audio: Literal[False] = False
    ratio: ShortText


class AssetTransformation(StrictModel):
    """Transformacion YA aplicada a un derivado. El modulo 4 no la repite."""

    policy: GeometryPolicy
    source_width: int = Field(ge=1)
    source_height: int = Field(ge=1)
    target_width: int = Field(ge=1)
    target_height: int = Field(ge=1)
    scaled_width: int = Field(ge=1)
    scaled_height: int = Field(ge=1)
    offset_x: int
    offset_y: int
    pad_color: ShortText
    crop_left: int | None = None
    crop_top: int | None = None
    crop_right: int | None = None
    crop_bottom: int | None = None


class MediaAsset(StrictModel):
    asset_id: Identifier
    kind: AssetKind
    role: AssetRole
    path: RelativePath
    size_bytes: int = Field(ge=1)
    sha256: HexHash
    mime: ShortText
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    dimensions_source: MeasurementSource = MeasurementSource.LOCAL_MEASUREMENT
    intrinsic_duration_s: float | None = Field(
        default=None, description="null en imagenes: una imagen no tiene duracion propia."
    )
    fps: float | None = Field(default=None, description="null en imagenes.")
    provider: Identifier
    model: ShortText
    simulation: bool
    effective_prompt: PromptText | None = None
    prompt_version: Identifier
    parameters: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    reference_asset_ids: list[Identifier] = Field(default_factory=list, max_length=20)
    derived_from: Identifier | None = None
    transformation: AssetTransformation | None = None
    video: VideoDetails | None = None
    cache_hit: bool = False

    @field_validator("path")
    @classmethod
    def _relative(cls, value: str) -> str:
        return _no_path_escape(value)


# ---------------------------------------------------------------------------
# Escenas
# ---------------------------------------------------------------------------


class PresentationPlan(StrictModel):
    """Instruccion PENDIENTE para el modulo 4, no aplicada al asset."""

    policy: GeometryPolicy
    target_width: int = Field(ge=1)
    target_height: int = Field(ge=1)
    pad_color: ShortText
    applied: Literal[False] = False
    decided_by: Literal["module_3_plan"] = "module_3_plan"


class ClipTrim(StrictModel):
    """Tramo del clip que el montaje debe reproducir. El sobrante se recorta."""

    play_from_s: float = Field(ge=0)
    play_to_s: float = Field(gt=0)
    source: Literal["module_3_plan"] = "module_3_plan"


class MediaSceneEntry(StrictModel):
    scene_id: Identifier
    order: int = Field(ge=1, le=40)
    requested_type: Literal["image", "video"]
    resolved_type: Literal["image", "video"]
    primary_asset_id: Identifier
    start_sample: int = Field(ge=0)
    end_sample: int = Field(ge=1)
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)
    duration_s: float = Field(gt=0)
    duration_source: Literal["voice_manifest"] = "voice_manifest"
    character_ids: list[Identifier] = Field(default_factory=list, max_length=8)
    presentation: PresentationPlan
    clip_trim: ClipTrim | None = None


# ---------------------------------------------------------------------------
# Uso e inspeccion
# ---------------------------------------------------------------------------


class MediaUsage(StrictModel):
    """Uso contado LOCALMENTE, salvo lo marcado como informado por el proveedor."""

    generation_attempts_total: int = Field(ge=0)
    generation_attempts_this_run: int = Field(ge=0)
    video_seconds_reserved_total: float = Field(ge=0)
    video_seconds_reserved_this_run: float = Field(ge=0)
    status_requests_total: int = Field(ge=0)
    status_requests_this_run: int = Field(ge=0)
    download_attempts_total: int = Field(ge=0)
    download_attempts_this_run: int = Field(ge=0)
    cache_hits: int = Field(ge=0)
    provider_reported: dict[str, int] = Field(default_factory=dict)
    estimated_cost_usd: float | None = Field(
        default=None, ge=0, description="null sin tarifa configurada o con consumo desconocido."
    )


class MediaInspection(StrictModel):
    contact_sheet_path: RelativePath | None = None
    note: MediumText = (
        "Recurso auxiliar para inspeccion humana. No forma parte del montaje y "
        "no acredita calidad visual."
    )

    @field_validator("contact_sheet_path")
    @classmethod
    def _relative(cls, value: str | None) -> str | None:
        return _no_path_escape(value) if value else value


class MediaProviders(StrictModel):
    """Configuracion efectiva, sin secretos."""

    image_provider: Identifier
    image_model: ShortText
    video_provider: Identifier | None = None
    video_model: ShortText | None = None
    video_api_version: ShortText | None = None
    geometry_policy: GeometryPolicy
    processing_version: Identifier
    settings_hash: HexHash


class MediaManifest(StrictModel):
    """Manifiesto lateral de medios visuales. Unica salida estructurada."""

    document_type: Literal["media_manifest"] = "media_manifest"
    schema_version: Literal["1.0"] = MEDIA_SCHEMA_VERSION
    media_run_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f\-]{36}$")]
    job_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f\-]{36}$")]
    voice_run_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f\-]{36}$")]
    created_at: UtcDatetime
    simulation: bool

    source: MediaSource
    providers: MediaProviders
    control: MediaControl
    timeline: MediaTimeline
    references: ReferenceSet
    assets: list[MediaAsset] = Field(min_length=1, max_length=200)
    scenes: list[MediaSceneEntry] = Field(min_length=1, max_length=40)
    usage: MediaUsage
    inspection: MediaInspection
