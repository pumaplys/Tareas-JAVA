"""Contrato del manifiesto lateral ``voice.json``.

Se versiona por separado del guion: ``script.json`` sigue en 1.0 y no se
toca. Este manifiesto es la fuente de TIEMPOS MEDIDOS para los modulos 3 y 4.

Reglas del contrato:

* ``extra="forbid"`` en todos los modelos, enums para los valores cerrados y
  colecciones acotadas, igual que en el modulo 1.
* Las rutas de medios son RELATIVAS al directorio del manifiesto y no admiten
  escapes (`..`, rutas absolutas). El archivo de entrada se le pasa a la
  validacion: nunca se confia en una ruta guardada dentro del manifiesto.
* Ningun campo guarda base64 ni secretos.
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

VOICE_SCHEMA_VERSION = "1.0"


def _no_path_escape(value: str) -> str:
    candidate = value.replace("\\", "/")
    if candidate.startswith("/") or ".." in candidate.split("/") or ":" in candidate:
        raise ValueError("la ruta debe ser relativa al manifiesto y sin escapes")
    return candidate


RelativePath = Annotated[
    str,
    StringConstraints(min_length=1, max_length=400, strip_whitespace=True),
]

#: Texto mostrado de una palabra. Mas holgado que `Word` del modulo 1 porque
#: conserva la puntuacion adherida ("compartir," o "«ahora»").
WordText = Annotated[str, StringConstraints(min_length=1, max_length=120)]


class VoiceStatus(StrEnum):
    READY = "ready"
    NEEDS_REVIEW = "needs_review"


class IssueSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class AlignmentMethod(StrEnum):
    """De donde salieron los tiempos por palabra."""

    PROVIDER_ALIGNMENT = "provider_alignment"
    PROVIDER_NORMALIZED_ALIGNMENT = "provider_normalized_alignment"
    FORCED_ALIGNMENT = "forced_alignment"
    NONE = "none"


class AlignmentStatus(StrEnum):
    OK = "ok"
    MISSING = "missing"
    REJECTED = "rejected"


class CueType(StrEnum):
    SFX = "sfx"
    MUSIC = "music"


# ---------------------------------------------------------------------------
# Fuente y proveedor
# ---------------------------------------------------------------------------


class VoiceSource(StrictModel):
    """Vinculo con el guion de entrada.

    El hash comprueba la CORRESPONDENCIA entre archivos; no autentica a nadie.
    El consumidor debe revalidar el guion, no limitarse a comparar hashes.
    """

    source_script_schema_version: ShortText
    source_script_sha256: HexHash = Field(
        description="SHA-256 de los bytes exactos del script.json leido."
    )
    source_profile_id: Identifier
    source_channel: Identifier
    source_simulation: bool
    source_production_status: Identifier


class VoiceProviderInfo(StrictModel):
    name: Identifier
    model_id: ShortText
    voice_id: ShortText
    output_format: ShortText
    internal_format: ShortText = Field(
        description="Formato interno al que se decodifica todo (decision del proyecto)."
    )
    effective_settings: dict[str, float | int | bool | str | None] = Field(
        default_factory=dict,
        description="Parametros realmente enviados. Nunca contiene secretos.",
    )
    settings_hash: HexHash
    processing_version: Identifier


# ---------------------------------------------------------------------------
# Control
# ---------------------------------------------------------------------------


class VoiceIssue(StrictModel):
    code: Identifier
    message: ShortText
    severity: IssueSeverity
    blocking: bool
    scene_id: Identifier | None = None


class VoiceControl(StrictModel):
    """Estado del manifiesto.

    Un manifiesto con CUALQUIER incidencia `blocking=True` nunca es `ready`.
    La ausencia de un efecto opcional es informativa (`blocking=False`); los
    defectos de duracion o de alineacion son bloqueantes.
    """

    voice_status: VoiceStatus
    issues: list[VoiceIssue] = Field(default_factory=list, max_length=200)
    admissible_for_assembly: bool = Field(
        description=(
            "Resultado calculado. Un consumidor NO debe fiarse de este booleano: "
            "debe repetir la comprobacion sobre los archivos reales."
        )
    )

    @field_validator("admissible_for_assembly")
    @classmethod
    def _coherent(cls, value: bool, info) -> bool:
        estado = info.data.get("voice_status")
        if value and estado is not VoiceStatus.READY:
            raise ValueError("admissible_for_assembly exige voice_status=ready")
        return value


# ---------------------------------------------------------------------------
# Maestro y escenas
# ---------------------------------------------------------------------------


class VoiceMaster(StrictModel):
    """Pista completa de narracion, ya con las pausas del guion incluidas."""

    path: RelativePath
    sha256: HexHash
    codec: ShortText
    sample_rate_hz: int = Field(ge=8_000, le=48_000)
    channels: int = Field(ge=1, le=2)
    sample_count: int = Field(ge=1)
    actual_duration_s: float = Field(gt=0)

    @field_validator("path")
    @classmethod
    def _relative(cls, value: str) -> str:
        return _no_path_escape(value)


class VoiceScene(StrictModel):
    """Tiempos medidos de una escena, en muestras y en segundos derivados.

    `end_sample` es extremo EXCLUSIVO. `clip_end_s` marca el final del habla,
    antes del silencio anadido; `end_s` incluye ese silencio.
    """

    scene_id: Identifier
    order: int = Field(ge=1, le=40)
    source_text: MediumText
    source_text_sha256: HexHash
    clip_path: RelativePath
    clip_sha256: HexHash
    clip_samples: int = Field(ge=1)
    pause_samples: int = Field(ge=0)
    start_sample: int = Field(ge=0)
    end_sample: int = Field(ge=1)
    start_s: float = Field(ge=0)
    clip_end_s: float = Field(gt=0)
    end_s: float = Field(gt=0)
    actual_duration_s: float = Field(gt=0)
    alignment_method: AlignmentMethod
    alignment_status: AlignmentStatus
    provider_normalized_text: MediumText | None = Field(
        default=None,
        description="Texto normalizado del proveedor, si lo envio. NO sustituye al original.",
    )
    request_id: ShortText | None = None
    characters_sent: int = Field(ge=0)

    @field_validator("clip_path")
    @classmethod
    def _relative(cls, value: str) -> str:
        return _no_path_escape(value)


class VoiceWord(StrictModel):
    """Palabra narrada con tiempos GLOBALES respecto de narration.wav.

    `char_start` es inclusivo y `char_end` exclusivo, en puntos de codigo de
    Python sobre el `narration_text` original de la escena (no en bytes UTF-8).
    """

    scene_id: Identifier
    word_index: int = Field(ge=0, description="Indice desde cero dentro de la escena.")
    text: WordText
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=1)
    start_s: float = Field(ge=0)
    end_s: float = Field(ge=0)
    emphasis: bool


# ---------------------------------------------------------------------------
# Sonido opcional
# ---------------------------------------------------------------------------


class SoundAssetRef(StrictModel):
    asset_id: Identifier
    path: RelativePath
    sha256: HexHash
    license_note: MediumText

    @field_validator("path")
    @classmethod
    def _relative(cls, value: str) -> str:
        return _no_path_escape(value)


class SoundCue(StrictModel):
    """Posicion propuesta de un efecto o de la musica. La mezcla es del modulo 4."""

    cue_type: CueType
    scene_id: Identifier | None = None
    asset_id: Identifier
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)
    gain_db: float = Field(ge=-60.0, le=0.0)


class VoiceSound(StrictModel):
    cues: list[SoundCue] = Field(default_factory=list, max_length=60)
    assets: list[SoundAssetRef] = Field(
        default_factory=list,
        max_length=60,
        description="Solo los assets realmente usados, copiados junto al manifiesto.",
    )


# ---------------------------------------------------------------------------
# Uso
# ---------------------------------------------------------------------------


class VoiceUsage(StrictModel):
    """Uso contado LOCALMENTE. No es facturacion observada.

    `requests_total` cubre todo el historial del trabajo; `requests_this_run`
    solo esta invocacion, de modo que reutilizar un resultado muestre cero
    solicitudes nuevas sin perder el total.
    """

    requests_total: int = Field(ge=0)
    requests_this_run: int = Field(ge=0)
    characters_sent_total: int = Field(ge=0)
    characters_sent_this_run: int = Field(ge=0)
    request_ids: list[ShortText] = Field(default_factory=list, max_length=100)
    latency_ms_total: int = Field(ge=0)
    estimated_cost_usd: float | None = Field(
        default=None,
        ge=0,
        description="null si no hay tarifa configurada o si hubo consumo desconocido.",
    )
    billing_observed: bool = Field(
        default=False,
        description="False: el proveedor no ha confirmado facturacion por esta via.",
    )


# ---------------------------------------------------------------------------
# Manifiesto
# ---------------------------------------------------------------------------


class VoiceManifest(StrictModel):
    """Manifiesto lateral de voz. Unica salida estructurada del modulo 2."""

    document_type: Literal["voice_manifest"] = "voice_manifest"
    schema_version: Literal["1.0"] = VOICE_SCHEMA_VERSION
    voice_run_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f\-]{36}$")]
    job_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f\-]{36}$")]
    created_at: UtcDatetime
    simulation: bool

    source: VoiceSource
    provider: VoiceProviderInfo
    control: VoiceControl
    master: VoiceMaster
    scenes: list[VoiceScene] = Field(min_length=1, max_length=40)
    words: list[VoiceWord] = Field(default_factory=list, max_length=4000)
    sound: VoiceSound
    usage: VoiceUsage
