"""Interfaz de proveedor de voz y presupuesto de peticiones.

Dos implementaciones y solo dos: ElevenLabs (real) y un simulado determinista.

El presupuesto RESERVA cada peticion antes de enviarla y persiste esa reserva,
de modo que reiniciar el proceso no lo restablezca. Los reintentos y las
alineaciones forzadas cuentan igual.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ...errors import ExitCode, ViralgenError
from ..alignment import CharAlignment


class VoiceProviderError(ViralgenError):
    exit_code = ExitCode.PROVIDER
    code = "voice_provider_error"


class VoiceTransientError(VoiceProviderError):
    """Fallo reintentable: conexion, tiempo de espera, 429 temporal o 5xx."""

    code = "voice_transient_error"


class VoicePermanentError(VoiceProviderError):
    """Fallo NO reintentable: clave, permisos, parametros, modelo o credito."""

    code = "voice_permanent_error"


class VoiceBudgetExceededError(VoiceProviderError):
    """Se alcanzo VOICE_MAX_REQUESTS_PER_JOB; no se emite nada mas."""

    code = "voice_budget_exceeded"


@dataclass
class SynthesisRequest:
    """Peticion de sintesis de una escena."""

    scene_id: str
    text: str
    """Texto TAL CUAL aparece en el guion. Sin acotaciones ni notas de direccion."""

    previous_text: str | None
    next_text: str | None
    """Contexto para la continuidad. NO se pronuncia ni se concatena al texto."""

    voice_id: str
    model_id: str
    output_format: str
    settings: dict[str, Any] = field(default_factory=dict)
    hint_speech_duration_s: float | None = None
    """Estimacion del modulo 1. Solo la usa el simulado; el real la ignora."""

    def identity_payload(self) -> dict[str, Any]:
        """Identidad de la sintesis: si cambia, el clip guardado no sirve."""
        return {
            "text": self.text,
            "previous_text": self.previous_text,
            "next_text": self.next_text,
            "voice_id": self.voice_id,
            "model_id": self.model_id,
            "output_format": self.output_format,
            "settings": dict(sorted(self.settings.items())),
        }


@dataclass
class SynthesisResult:
    audio_bytes: bytes
    audio_format: str
    """'mp3' o 'wav': indica como decodificar, no es el formato interno."""

    alignment: CharAlignment | None = None
    normalized_alignment: CharAlignment | None = None
    request_id: str | None = None
    latency_ms: int = 0
    http_status: int | None = None
    characters_sent: int = 0


class VoiceBudget:
    """Contador duro de peticiones, con reserva persistida antes de enviar.

    `used_total` es el historial del TRABAJO (todas las invocaciones);
    `used_this_run` solo esta ejecucion. Reutilizar un resultado muestra cero
    nuevas sin perder el total.
    """

    def __init__(
        self,
        *,
        max_requests: int,
        used_total: int,
        reserve: Callable[[str, str | None], int],
        settle: Callable[..., None],
    ) -> None:
        self.max_requests = max_requests
        self.used_total = used_total
        self.used_this_run = 0
        self._reserve = reserve
        self._settle = settle

    @property
    def remaining(self) -> int:
        return max(0, self.max_requests - self.used_total)

    def can_spend(self, count: int = 1) -> bool:
        return self.used_total + count <= self.max_requests

    def reserve(self, kind: str, scene_id: str | None = None) -> int:
        """Reserva y persiste una peticion ANTES de enviarla."""
        if not self.can_spend():
            raise VoiceBudgetExceededError(
                f"Se alcanzo VOICE_MAX_REQUESTS_PER_JOB={self.max_requests} para este "
                "trabajo; no se emiten mas peticiones de voz.",
                details={
                    "kind": kind,
                    "scene_id": scene_id,
                    "used_total": self.used_total,
                    "max_requests": self.max_requests,
                },
            )
        token = self._reserve(kind, scene_id)
        self.used_total += 1
        self.used_this_run += 1
        return token

    def settle(self, token: int, **fields: Any) -> None:
        self._settle(token, **fields)

    def snapshot(self) -> dict[str, Any]:
        return {
            "used_total": self.used_total,
            "used_this_run": self.used_this_run,
            "max_requests": self.max_requests,
        }


class VoiceProvider(ABC):
    """Proveedor de voz con tiempos por caracter."""

    name: str = "abstract"
    model_id: str = "unknown"
    supports_forced_alignment: bool = False

    @abstractmethod
    def synthesize(self, request: SynthesisRequest, budget: VoiceBudget) -> SynthesisResult:
        """Sintetiza una escena y devuelve audio codificado y alineacion."""

    def force_align(
        self, audio_path: Path, text: str, budget: VoiceBudget
    ) -> CharAlignment | None:
        """Alineacion forzada sobre un WAV ya sintetizado. Opcional."""
        raise NotImplementedError

    def decode_to_internal(self, result: SynthesisResult, source: Path, destination: Path) -> None:
        """Convierte el audio recibido al formato interno del proyecto."""
        raise NotImplementedError
