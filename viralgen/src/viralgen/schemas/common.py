"""Enumeraciones compartidas por los modelos de proveedor y de documento."""

from __future__ import annotations

from enum import StrEnum


class Channel(StrEnum):
    """Canal editorial. Determina la direccion creativa y las validaciones."""

    INFANTIL = "infantil"
    CURIOSIDADES = "curiosidades"


class Platform(StrEnum):
    """Plataformas de destino previstas para el modulo 5."""

    YOUTUBE_SHORTS = "youtube_shorts"
    INSTAGRAM_REELS = "instagram_reels"
    TIKTOK = "tiktok"


class Beat(StrEnum):
    """Funcion narrativa de cada escena."""

    HOOK = "hook"
    CONTEXT = "context"
    DEVELOPMENT = "development"
    RESOLUTION = "resolution"
    CLOSE = "close"


class AssetType(StrEnum):
    """Tipo de medio que el modulo 3 debera producir para la escena."""

    IMAGE = "image"
    VIDEO = "video"


class SfxCue(StrEnum):
    """Momento aproximado del efecto de sonido dentro de la escena.

    Los tiempos definitivos dependen del audio real (modulo 2).
    """

    SCENE_START = "scene_start"
    SCENE_END = "scene_end"


class ProductionStatus(StrEnum):
    """Estado del documento exportado.

    ``ready_for_production`` significa unicamente que el plan puede pasar a
    producir medios. No autoriza publicaciones ni certifica monetizacion.
    """

    READY_FOR_PRODUCTION = "ready_for_production"
    NEEDS_REVIEW = "needs_review"


class ReviewStatus(StrEnum):
    """Estado de revision de un hecho en el catalogo importado."""

    APPROVED = "approved"
    PENDING = "pending"
    REJECTED = "rejected"


class VerificationLevel(StrEnum):
    """Alcance real de la verificacion del guion.

    * ``none``              - ficcion pura, sin afirmaciones factuales.
    * ``source_pack_only``  - toda afirmacion enlaza con el catalogo aportado.
      El sistema restringe la generacion a esas fuentes; NO verifica de forma
      independiente que la fuente respalde semanticamente la afirmacion.
    * ``needs_review``      - hay ambiguedad detectada; requiere revision humana.
    """

    NONE = "none"
    SOURCE_PACK_ONLY = "source_pack_only"
    NEEDS_REVIEW = "needs_review"


class CaptionStyle(StrEnum):
    """Estilo de subtitulado solicitado al modulo 4."""

    DYNAMIC_EMPHASIS = "dynamic_emphasis"
    CALM_READABLE = "calm_readable"


class JobStatus(StrEnum):
    """Estado del trabajo en la base de datos (no del documento)."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    """Solo para el comando `ideas`: propuestas generadas, sin guion."""

    READY_FOR_PRODUCTION = "ready_for_production"
    NEEDS_REVIEW = "needs_review"
    NEEDS_RESEARCH = "needs_research"
    FAILED = "failed"
