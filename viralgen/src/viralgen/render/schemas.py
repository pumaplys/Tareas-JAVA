"""Contrato del manifiesto lateral ``render.json``.

Se versiona por separado: `script.json` sigue en 1.0 y sin tocar, y su
`video.actual_duration_s` sigue siendo `null`. Este manifiesto describe el
ARCHIVO EXPORTADO, que es otra cosa: la duracion del MP4 no es la estimacion
del guion ni la de la voz.

Reglas del contrato, iguales que en los modulos anteriores:

* `extra="forbid"`, enums para los valores cerrados y colecciones acotadas.
* Las rutas son RELATIVAS al directorio del manifiesto, sin escapes.
* Se separa siempre lo MEDIDO de lo DECIDIDO: cada numero dice de donde sale.
* Ningun campo guarda secretos.
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

RENDER_SCHEMA_VERSION = "1.0"


def _no_path_escape(value: str) -> str:
    candidate = value.replace("\\", "/")
    if candidate.startswith("/") or ".." in candidate.split("/") or ":" in candidate:
        raise ValueError("la ruta debe ser relativa al manifiesto y sin escapes")
    return candidate


RelativePath = Annotated[
    str, StringConstraints(min_length=1, max_length=400, strip_whitespace=True)
]


class RenderStatus(StrEnum):
    READY = "ready"
    NEEDS_REVIEW = "needs_review"


class RenderMode(StrEnum):
    """Modo de la ejecucion. Nunca se deriva de una bandera del validador."""

    PRODUCTION = "production"
    PREVIEW = "preview"


class IssueSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class VisualReviewState(StrEnum):
    """Revision humana. `ready` es preparacion TECNICA, no aprobacion."""

    NOT_PERFORMED = "not_performed"
    APPROVED = "approved"
    REJECTED = "rejected"


class MeasurementSource(StrEnum):
    """De donde sale un numero. Se declara siempre."""

    LOCAL_MEASUREMENT = "local_measurement"
    MONTAGE_DECISION = "montage_decision"
    SOURCE_MANIFEST = "source_manifest"


class GeometryPolicy(StrEnum):
    CONTAIN = "contain"
    CROP = "crop"


class CameraMove(StrEnum):
    STATIC = "static"
    ZOOM_IN = "zoom_in"
    ZOOM_OUT = "zoom_out"


# ---------------------------------------------------------------------------
# Fuentes y control
# ---------------------------------------------------------------------------


class RenderSources(StrictModel):
    """Los tres documentos de entrada, por version y por hash de sus BYTES."""

    script_schema_version: ShortText
    script_sha256: HexHash
    script_simulation: bool
    voice_schema_version: ShortText
    voice_sha256: HexHash
    voice_simulation: bool
    media_schema_version: ShortText
    media_sha256: HexHash
    media_simulation: bool
    profile_id: Identifier
    channel: Identifier
    caption_style: Identifier


class RenderIssue(StrictModel):
    code: Identifier
    message: ShortText
    severity: IssueSeverity
    blocking: bool
    scene_id: Identifier | None = None


class RenderControl(StrictModel):
    render_status: RenderStatus
    issues: list[RenderIssue] = Field(default_factory=list, max_length=200)
    visual_review: VisualReviewState = VisualReviewState.NOT_PERFORMED
    visual_review_method: ShortText = Field(
        default="none",
        description="Como se reviso: 'none', 'frame_sampling', 'human'.",
    )
    admissible_for_publisher: bool = Field(
        description=(
            "Resultado calculado. Un consumidor NO debe fiarse de este booleano: "
            "debe repetir la comprobacion sobre los archivos reales."
        )
    )

    @field_validator("admissible_for_publisher")
    @classmethod
    def _coherent(cls, value: bool, info) -> bool:
        if value and info.data.get("render_status") is not RenderStatus.READY:
            raise ValueError("admissible_for_publisher exige render_status=ready")
        return value


# ---------------------------------------------------------------------------
# Salida
# ---------------------------------------------------------------------------


class VideoStreamInfo(StrictModel):
    """Propiedades MEDIDAS del stream de video del archivo exportado."""

    codec_name: ShortText
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    pix_fmt: ShortText
    sample_aspect_ratio: ShortText
    display_aspect_ratio: ShortText
    fps_rational: ShortText = Field(description="Ej. '30/1'. Racional, no decimal.")
    fps: float = Field(gt=0)
    frame_count: int = Field(ge=1, description="Contado decodificando, no leido de la cabecera.")
    stream_duration_s: float | None = None
    rotation_degrees: int = Field(default=0, ge=-360, le=360)
    has_b_frames: int = Field(default=0, ge=0)
    constant_frame_rate: bool
    pts_monotonic: bool
    source: Literal["local_measurement"] = "local_measurement"


class AudioStreamInfo(StrictModel):
    """Propiedades MEDIDAS del stream de audio."""

    codec_name: ShortText
    sample_rate_hz: int = Field(ge=8_000, le=192_000)
    channels: int = Field(ge=1, le=8)
    channel_layout: ShortText
    stream_duration_s: float | None = None
    decoded_samples: int | None = Field(
        default=None, description="Muestras por canal realmente decodificadas."
    )
    initial_padding_samples: int | None = Field(
        default=None,
        description="Priming que declara el decodificador AAC, si lo expone.",
    )
    source: Literal["local_measurement"] = "local_measurement"


class RenderOutput(StrictModel):
    """El archivo entregado. Todo aqui esta MEDIDO sobre el archivo."""

    path: RelativePath
    sha256: HexHash
    size_bytes: int = Field(ge=1)
    container_format: ShortText
    container_duration_s: float | None = None
    faststart: bool
    video: VideoStreamInfo
    audio: AudioStreamInfo
    decode_check_passed: bool = Field(
        description="Ambas pistas decodificadas enteras a salida nula, sin errores."
    )
    color_primaries: ShortText | None = None
    color_transfer: ShortText | None = None
    color_space: ShortText | None = None

    @field_validator("path")
    @classmethod
    def _relative(cls, value: str) -> str:
        return _no_path_escape(value)


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------


class SceneBoundary(StrictModel):
    """Frontera de una escena, en los DOS relojes, con su desplazamiento."""

    scene_id: Identifier
    order: int = Field(ge=1, le=40)
    start_sample: int = Field(ge=0)
    end_sample: int = Field(ge=1)
    start_frame: int = Field(ge=0)
    end_frame: int = Field(ge=1)
    frames: int = Field(ge=1, description="Nunca cero: una escena vacia es un fallo.")
    voice_start_s: float = Field(ge=0)
    voice_end_s: float = Field(gt=0)
    frame_start_s: float = Field(ge=0)
    frame_duration_s: float = Field(gt=0)
    start_offset_s: float = Field(
        description="frame_start_s - voice_start_s. En [0, 1/F) por construccion."
    )
    end_offset_s: float
    asset_type: Literal["image", "video"]
    camera_move: CameraMove
    geometry_policy: GeometryPolicy
    geometry_already_applied: bool
    clip_from_s: float | None = None
    clip_to_s: float | None = None


class RenderTimelineInfo(StrictModel):
    """Reloj de voz, reloj de video y el exceso de cuantizacion entre ambos."""

    sample_rate_hz: int = Field(ge=8_000, le=48_000)
    sample_count: int = Field(ge=1)
    narration_duration_s: float = Field(gt=0, description="N/S. Reloj de VOZ.")
    narration_source: Literal["source_manifest"] = "source_manifest"
    fps: int = Field(ge=1, le=120)
    total_frames: int = Field(ge=1)
    visual_duration_s: float = Field(gt=0, description="total_frames/F. Reloj de VIDEO.")
    quantization_excess_s: float = Field(
        ge=0,
        description="visual_duration_s - narration_duration_s. Siempre < 1/F.",
    )
    max_frame_hold_s: float = Field(
        gt=0,
        description=(
            "1/F. Es lo unico que se permite sostener de la ultima muestra visual "
            "al cerrar el fotograma final; nunca sirve para alargar un clip corto."
        ),
    )
    target_source: Literal["montage_decision"] = "montage_decision"
    scenes: list[SceneBoundary] = Field(min_length=1, max_length=40)


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------


class ResampleInfo(StrictModel):
    source_rate_hz: int = Field(ge=8_000, le=192_000)
    target_rate_hz: int = Field(ge=8_000, le=192_000)
    source_samples: int = Field(ge=1)
    expected_samples: int = Field(ge=1)
    ratio: ShortText
    exact_integer_ratio: bool
    note: MediumText


class CueUsage(StrictModel):
    cue_type: Identifier
    asset_id: Identifier
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)
    gain_db: float = Field(ge=-60.0, le=12.0)
    sha256: HexHash


class LoudnessInfo(StrictModel):
    integrated_lufs: float | None = None
    true_peak_dbtp: float | None = None
    loudness_range_lu: float | None = None
    threshold_lufs: float | None = None
    usable: bool
    note: MediumText


class RenderAudio(StrictModel):
    """Mezcla: que se uso, como se convirtio y que se midio al final."""

    narration_used_once: Literal[True] = Field(
        default=True,
        description="La narracion maestra entra UNA vez; los clips por escena no.",
    )
    clip_audio_used: Literal[False] = Field(
        default=False,
        description="use_source_audio=false: el audio de los clips nunca se mezcla.",
    )
    work_sample_rate_hz: int = Field(ge=8_000, le=192_000)
    work_sample_count: int = Field(ge=1)
    channels: int = Field(ge=1, le=2)
    resample: ResampleInfo
    cues_used: list[CueUsage] = Field(default_factory=list, max_length=60)
    ducking: dict[str, float | bool | str] = Field(default_factory=dict)
    normalization: dict[str, float | bool | str | None] = Field(default_factory=dict)
    measured: LoudnessInfo
    loudness_compliant: bool
    aac_tolerance_samples: int = Field(
        ge=0,
        description=(
            "Margen tecnico del codec (un frame AAC). No sirve para tapar voz "
            "truncada: solo cubre priming y padding."
        ),
    )
    audio_sample_deficit: int = Field(
        description=(
            "decoded_samples - esperadas. Negativo significa que FALTA audio; "
            "solo se admite dentro de aac_tolerance_samples."
        )
    )


# ---------------------------------------------------------------------------
# Subtitulos
# ---------------------------------------------------------------------------


class FontInfo(StrictModel):
    path: ShortText = Field(description="Ruta del SISTEMA, no del paquete.")
    sha256: HexHash
    family: ShortText
    units_per_em: int = Field(ge=1)


class RenderCaptions(StrictModel):
    path: RelativePath
    sha256: HexHash
    style_id: Identifier
    style_version: Identifier
    font: FontInfo
    word_count: int = Field(ge=0)
    group_count: int = Field(ge=0)
    event_count: int = Field(ge=0)
    play_res_x: int = Field(ge=1)
    play_res_y: int = Field(ge=1)
    text_region: dict[str, int] = Field(
        description="Region de composicion. DECISION de diseno, no zona segura."
    )
    rounding_policy: MediumText
    collapsed_highlights: list[ShortText] = Field(
        default_factory=list,
        max_length=200,
        description=(
            "Palabras cuyo resaltado colapso al cuantizar. Su texto sigue "
            "visible; no se fabrico duracion de voz."
        ),
    )
    preview_mark: ShortText | None = None
    rendered_by: Literal["libass"] = "libass"

    @field_validator("path")
    @classmethod
    def _relative(cls, value: str) -> str:
        return _no_path_escape(value)


# ---------------------------------------------------------------------------
# Procesamiento
# ---------------------------------------------------------------------------


class ToolInfo(StrictModel):
    ffmpeg_version: ShortText
    ffprobe_version: ShortText
    min_tested_version: ShortText
    meets_min_tested_version: bool
    libass: bool
    encoders_required: list[ShortText] = Field(default_factory=list, max_length=20)
    filters_required: list[ShortText] = Field(default_factory=list, max_length=40)


class SegmentInfo(StrictModel):
    """Un segmento visual ya validado. Su archivo puede haberse limpiado."""

    scene_id: Identifier
    order: int = Field(ge=1, le=40)
    frames: int = Field(ge=1)
    sha256: HexHash
    size_bytes: int = Field(ge=1)
    retained: bool = Field(
        description="False: se limpio tras consolidar. Su hash queda aqui."
    )
    reused: bool = Field(default=False, description="Se reutilizo un checkpoint validado.")


class RenderProcessing(StrictModel):
    pipeline_version: Identifier
    tools: ToolInfo
    encode_settings: dict[str, str | int | float | bool | None] = Field(
        default_factory=dict
    )
    plan_sha256: HexHash
    fingerprint: HexHash
    segments: list[SegmentInfo] = Field(default_factory=list, max_length=40)
    concat_mode: ShortText = Field(
        default="stream_copy",
        description="'stream_copy' o 'reencode'. Copiar exige segmentos compatibles.",
    )


# ---------------------------------------------------------------------------
# Recursos e inspeccion
# ---------------------------------------------------------------------------


class RenderResources(StrictModel):
    wall_time_s: float = Field(ge=0)
    peak_work_dir_mib: float = Field(ge=0)
    free_disk_mb_before: float = Field(ge=0)
    free_disk_mb_after: float = Field(ge=0)
    renders_new: int = Field(ge=0, description="Codificaciones nuevas de ESTA invocacion.")
    renders_total: int = Field(ge=0, description="Codificaciones de todo el trabajo.")
    segment_cache_hits: int = Field(ge=0)
    peak_memory_mib: float | None = Field(
        default=None, description="null si no se midio: no se estima."
    )


class FrameSample(StrictModel):
    label: ShortText
    frame_index: int = Field(ge=0)
    time_s: float = Field(ge=0)
    path: RelativePath
    sha256: HexHash

    @field_validator("path")
    @classmethod
    def _relative(cls, value: str) -> str:
        return _no_path_escape(value)


class RenderInspection(StrictModel):
    contact_sheet_path: RelativePath | None = None
    frames: list[FrameSample] = Field(default_factory=list, max_length=20)
    checks_performed: list[ShortText] = Field(default_factory=list, max_length=40)
    note: MediumText = Field(
        default=(
            "Los fotogramas son evidencia de que hay texto integrado y de que la "
            "marca de preview aparece. Un detector de pixeles no certifica "
            "legibilidad ni correccion semantica."
        )
    )

    @field_validator("contact_sheet_path")
    @classmethod
    def _relative(cls, value: str | None) -> str | None:
        return None if value is None else _no_path_escape(value)


# ---------------------------------------------------------------------------
# Manifiesto
# ---------------------------------------------------------------------------


class RenderManifest(StrictModel):
    """Manifiesto lateral del montaje. Unica salida estructurada del modulo 4."""

    document_type: Literal["render_manifest"] = "render_manifest"
    schema_version: Literal["1.0"] = RENDER_SCHEMA_VERSION
    render_run_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f\-]{36}$")]
    job_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f\-]{36}$")]
    voice_run_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f\-]{36}$")]
    media_run_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f\-]{36}$")]
    created_at: UtcDatetime
    render_mode: RenderMode
    simulation: bool = Field(
        description="Derivado de las FUENTES y de las operaciones, no de una bandera."
    )

    sources: RenderSources
    control: RenderControl
    output: RenderOutput
    timeline: RenderTimelineInfo
    audio: RenderAudio
    captions: RenderCaptions
    processing: RenderProcessing
    resources: RenderResources
    inspection: RenderInspection
