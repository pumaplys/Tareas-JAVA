"""Proveedores de texto: real (OpenAI) y simulado (determinista)."""

from .base import (
    CallBudget,
    ProviderRequest,
    ProviderResult,
    ProviderUsage,
    TextProvider,
    UsageTotals,
)

__all__ = [
    "CallBudget",
    "ProviderRequest",
    "ProviderResult",
    "ProviderUsage",
    "TextProvider",
    "UsageTotals",
    "build_provider",
]


def build_provider(*, simulation: bool, settings, seed: int | None = None) -> TextProvider:
    """Crea el proveedor adecuado.

    La importacion es perezosa para que el modo simulado no necesite tener
    instalado ni configurado el SDK de OpenAI.
    """
    if simulation:
        from .mock_provider import MockProvider

        return MockProvider(seed=seed)

    from .openai_provider import OpenAIProvider

    return OpenAIProvider(settings=settings)
