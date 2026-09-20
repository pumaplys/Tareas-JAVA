"""Modelos Pydantic del modulo.

Se separan a proposito dos familias:

* ``provider``  - lo que el proveedor DEVUELVE. Su JSON Schema se envia a la
  API, asi que solo usa el subconjunto admitido por Structured Outputs
  (enums, ``minItems``/``maxItems``, limites numericos, ``additionalProperties:
  false``). No lleva limites de longitud de cadena.
* ``document``  - el contrato final ``script.json`` que consumen los modulos
  2-5. Aqui si hay limites de longitud, colecciones acotadas y campos
  calculados por la aplicacion (duraciones, hashes, uso de tokens...).
"""

from .common import (
    AssetType,
    Beat,
    CaptionStyle,
    Channel,
    JobStatus,
    Platform,
    ProductionStatus,
    ReviewStatus,
    SfxCue,
    VerificationLevel,
)
from .document import (
    Claim,
    Control,
    Evidence,
    FactRecord,
    HookVariant,
    Idea,
    Loop,
    Narration,
    Provenance,
    PublishingPlan,
    ScoreComponents,
    Scene,
    SceneAudio,
    SceneCaptions,
    SceneVisual,
    ScriptDocument,
    VisualBible,
    VisualCharacter,
    Video,
)
from .provider import (
    ProviderIdea,
    ProviderIdeaBatch,
    ProviderRatings,
    ProviderScript,
)

__all__ = [
    "AssetType",
    "Beat",
    "CaptionStyle",
    "Channel",
    "Claim",
    "Control",
    "Evidence",
    "FactRecord",
    "HookVariant",
    "Idea",
    "JobStatus",
    "Loop",
    "Narration",
    "Platform",
    "ProductionStatus",
    "Provenance",
    "ProviderIdea",
    "ProviderIdeaBatch",
    "ProviderRatings",
    "ProviderScript",
    "PublishingPlan",
    "ReviewStatus",
    "Scene",
    "SceneAudio",
    "SceneCaptions",
    "SceneVisual",
    "ScoreComponents",
    "ScriptDocument",
    "SfxCue",
    "VerificationLevel",
    "Video",
    "VisualBible",
    "VisualCharacter",
]
