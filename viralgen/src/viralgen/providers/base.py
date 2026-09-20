"""Interfaz minima de proveedor de texto estructurado.

Hay dos implementaciones y solo dos: ``openai_provider`` (real) y
``mock_provider`` (simulado y determinista). No se anaden mas proveedores en
este MVP.

El contrato es deliberadamente estrecho: se pide un objeto que valide contra
un modelo Pydantic concreto. Quien llama nunca recibe texto libre ni tiene que
quitar bloques Markdown para encontrar el JSON.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from ..errors import CallBudgetExceededError


@dataclass
class ProviderRequest:
    """Peticion a un proveedor de texto.

    `payload` es la vista estructurada de los mismos datos que ya van en
    `input_text`. El proveedor real solo usa `instructions` + `input_text`;
    el simulado usa `payload` para construir una respuesta determinista sin
    tener que interpretar lenguaje natural.
    """

    stage: str
    instructions: str
    input_text: str
    schema_model: type[BaseModel]
    payload: dict[str, Any] = field(default_factory=dict)
    max_output_tokens: int | None = None


@dataclass
class ProviderUsage:
    """Uso declarado por el proveedor para una peticion."""

    input_tokens: int = 0
    output_tokens: int = 0
    request_id: str | None = None
    latency_ms: int = 0
    model: str = ""
    known: bool = True
    """False cuando el consumo real es desconocido (p. ej. fallo sin cuerpo)."""


@dataclass
class ProviderResult:
    parsed: BaseModel
    usage: ProviderUsage
    raw_status: str = "completed"


class CallBudget:
    """Contador duro de peticiones reales por trabajo.

    Cuenta TODAS las peticiones emitidas: primera llamada, reintentos de
    transporte, segunda tanda de ideas y reparacion. Nunca se sobrepasa
    `max_calls`: al llegar al limite se lanza `CallBudgetExceededError` antes
    de emitir nada.
    """

    def __init__(self, max_calls: int) -> None:
        self.max_calls = max_calls
        self.used = 0
        self.by_stage: dict[str, int] = {}

    @property
    def remaining(self) -> int:
        return max(0, self.max_calls - self.used)

    def can_spend(self, count: int = 1) -> bool:
        return self.used + count <= self.max_calls

    def spend(self, stage: str) -> int:
        if not self.can_spend():
            raise CallBudgetExceededError(
                f"Se alcanzo MAX_CALLS_PER_JOB={self.max_calls}; no se emiten mas peticiones.",
                details={"stage": stage, "used": self.used, "max_calls": self.max_calls},
            )
        self.used += 1
        self.by_stage[stage] = self.by_stage.get(stage, 0) + 1
        return self.used

    def snapshot(self) -> dict[str, Any]:
        return {"used": self.used, "max_calls": self.max_calls, "by_stage": dict(self.by_stage)}


class TextProvider(ABC):
    """Proveedor de texto estructurado."""

    #: Identificador corto que viaja en `provenance.provider`.
    name: str = "abstract"

    #: Modelo efectivo; en simulacion es un nombre sintetico.
    model: str = "unknown"

    @abstractmethod
    def generate_structured(self, request: ProviderRequest, budget: CallBudget) -> ProviderResult:
        """Devuelve una instancia validada de `request.schema_model`."""


@dataclass
class UsageTotals:
    """Acumulado de uso de todo el trabajo."""

    input_tokens: int = 0
    output_tokens: int = 0
    request_ids: list[str] = field(default_factory=list)
    unknown_usage_calls: int = 0

    def add(self, usage: ProviderUsage) -> None:
        if usage.known:
            self.input_tokens += usage.input_tokens
            self.output_tokens += usage.output_tokens
        else:
            self.unknown_usage_calls += 1
        if usage.request_id and usage.request_id not in self.request_ids:
            self.request_ids.append(usage.request_id)

    def estimated_cost_usd(self, pricing: tuple[float, float] | None) -> float | None:
        """Coste estimado o None.

        Devuelve None si no hay tarifas configuradas o si alguna peticion
        consumio una cantidad desconocida de tokens: atribuir coste cero a una
        peticion fallida seria enganoso.
        """
        if pricing is None or self.unknown_usage_calls:
            return None
        price_in, price_out = pricing
        cost = (self.input_tokens * price_in + self.output_tokens * price_out) / 1_000_000
        return round(cost, 6)
