"""Adaptadores de medios: OpenAI Images, Runway y simulado determinista."""

from .base import (
    ImageProvider,
    ImageRequest,
    ImageResult,
    MediaBudget,
    MediaBudgetExceededError,
    MediaOutcomeUnknownError,
    MediaPermanentError,
    MediaProviderError,
    MediaTransientError,
    VideoProvider,
    VideoRequest,
    VideoStatus,
    VideoTask,
)

__all__ = [
    "ImageProvider",
    "ImageRequest",
    "ImageResult",
    "MediaBudget",
    "MediaBudgetExceededError",
    "MediaOutcomeUnknownError",
    "MediaPermanentError",
    "MediaProviderError",
    "MediaTransientError",
    "VideoProvider",
    "VideoRequest",
    "VideoStatus",
    "VideoTask",
    "build_image_provider",
    "build_video_provider",
]


def build_image_provider(*, simulation: bool, settings, seed: int | None = None):
    """Importacion perezosa: el mock no necesita el SDK configurado."""
    if simulation:
        from .mock import MockImageProvider

        return MockImageProvider(settings=settings, seed=seed)

    from .openai_images import OpenAIImageProvider

    return OpenAIImageProvider(settings=settings)


def build_video_provider(*, simulation: bool, settings, seed: int | None = None):
    if simulation:
        from .mock import MockVideoProvider

        return MockVideoProvider(settings=settings, seed=seed)

    from .runway import RunwayVideoProvider

    return RunwayVideoProvider(settings=settings)
