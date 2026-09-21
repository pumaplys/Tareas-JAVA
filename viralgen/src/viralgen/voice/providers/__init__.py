"""Proveedores de voz: real (ElevenLabs) y simulado determinista."""

from .base import (
    SynthesisRequest,
    SynthesisResult,
    VoiceBudget,
    VoiceBudgetExceededError,
    VoicePermanentError,
    VoiceProvider,
    VoiceProviderError,
    VoiceTransientError,
)

__all__ = [
    "SynthesisRequest",
    "SynthesisResult",
    "VoiceBudget",
    "VoiceBudgetExceededError",
    "VoicePermanentError",
    "VoiceProvider",
    "VoiceProviderError",
    "VoiceTransientError",
    "build_voice_provider",
]


def build_voice_provider(*, simulation: bool, settings, seed: int | None = None):
    """Crea el proveedor adecuado. Importacion perezosa."""
    if simulation:
        from .mock import MockVoiceProvider

        return MockVoiceProvider(settings=settings, seed=seed)

    from .elevenlabs import ElevenLabsProvider

    return ElevenLabsProvider(settings=settings)
