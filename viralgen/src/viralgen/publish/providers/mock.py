"""Publicador simulado: recorrido completo sin red ni credenciales.

Para que existe: el modo `mock` tiene que ensayar el flujo entero -cuenta,
envio, espera remota, verificacion- sin tocar ninguna plataforma. Eso permite
probar la cola, los estados y la reanudacion sin abrir una cuenta y sin pagar
nada.

Reglas que lo mantienen honesto:

* **No hay cliente de red.** Este adaptador no tiene ninguno, asi que no puede
  alcanzar la red aunque el entorno tenga credenciales. La imposibilidad es
  estructural, no una promesa.
* **Espacio de identidad separado.** Lo que devuelve va a `mock_remote_id`;
  `real_remote_id` se queda en null y no se fabrica ninguna URL que funcione.
* **La evidencia dice que es simulada.** Un destino entregado en simulacion
  lleva `EvidenceSource.SIMULATED`, y el contrato rechaza declarar una entrega
  simulada como si fuese una consulta real.
* **Los tiempos se respetan.** Hay una fase de procesamiento, igual que en la
  realidad, para que la cola tenga que esperar de verdad.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from ...schemas.common import Platform
from ..schemas import (
    DestinationState,
    ErrorClass,
    EvidenceRecord,
    EvidenceSource,
    StructuredError,
    TransferPhase,
    Visibility,
)
from .base import AccountCheck, DispatchContext, PublisherAdapter, StepResult

#: Segundos simulados de procesamiento remoto antes de dar por verificado.
PROCESSING_SECONDS = 60


def mock_remote_id(ctx: DispatchContext) -> str:
    """Identificador determinista del espacio SIMULADO.

    Lleva prefijo a proposito: quien lo vea en un recibo no puede confundirlo
    con un identificador de plataforma.
    """
    semilla = f"{ctx.publication_id}:{ctx.destination_id}:{ctx.video_sha256}"
    return "mock_" + hashlib.sha256(semilla.encode("utf-8")).hexdigest()[:16]


@dataclass
class MockPublisherAdapter(PublisherAdapter):
    """Simulador del recorrido de un destino remoto."""

    platform: Platform
    settings: Any
    name: str = "mock_publisher"
    remote: bool = False
    #: Permite ensayar un fallo parcial multicanal sin tocar nada real.
    fail_on_start: bool = False
    #: Cuantas consultas tarda en "terminar" de procesar.
    polls_until_ready: int = 1

    def check_account(self, ctx: DispatchContext) -> AccountCheck:
        return AccountCheck(
            ok=True,
            observed_account_id=ctx.expected_account_id,
            detail=(
                "comprobacion SIMULADA: no se ha consultado ninguna plataforma"
            ),
        )

    def start(self, ctx: DispatchContext) -> StepResult:
        if self.fail_on_start:
            return StepResult(
                state=DestinationState.FAILED,
                phase=TransferPhase.UPLOADING,
                error=StructuredError(
                    code="mock_failure",
                    error_class=ErrorClass.INVALID_PAYLOAD,
                    message="fallo simulado para ensayar un resultado parcial",
                    retryable=False,
                    occurred_at=ctx.now,
                ),
                evidence=EvidenceRecord(
                    source=EvidenceSource.SIMULATED,
                    checked_at=ctx.now,
                    summary="publicador simulado: fallo provocado",
                ),
            )
        return StepResult(
            state=DestinationState.WAITING_REMOTE,
            phase=TransferPhase.BYTES_ACCEPTED,
            remote_id=mock_remote_id(ctx),
            evidence=EvidenceRecord(
                source=EvidenceSource.SIMULATED,
                checked_at=ctx.now,
                summary=(
                    "publicador simulado: transferencia completada, procesamiento "
                    "en curso"
                ),
                remote_status="processing",
            ),
            bytes_sent=ctx.video_size,
            next_poll_in_s=float(self.settings.publish_poll_interval_s),
        )

    def poll(self, ctx: DispatchContext) -> StepResult:
        consultas = int(ctx.row.get("attempts") or 0)
        identificador = ctx.row.get("mock_remote_id") or mock_remote_id(ctx)
        if consultas < self.polls_until_ready:
            return StepResult(
                state=DestinationState.WAITING_REMOTE,
                phase=TransferPhase.REMOTE_PROCESSING,
                remote_id=identificador,
                evidence=EvidenceRecord(
                    source=EvidenceSource.SIMULATED,
                    checked_at=ctx.now,
                    summary="publicador simulado: sigue procesando",
                    remote_status="processing",
                ),
                next_poll_in_s=float(self.settings.publish_poll_interval_s),
            )
        visible = ctx.requested_visibility is Visibility.PUBLIC
        return StepResult(
            state=DestinationState.DELIVERED,
            phase=TransferPhase.VERIFIED,
            remote_id=identificador,
            observed_account_id=ctx.expected_account_id,
            observed_visibility=ctx.requested_visibility,
            publicly_visible=visible,
            # Sin permalink: no se fabrica una URL que parezca funcionar.
            permalink=None,
            evidence=EvidenceRecord(
                source=EvidenceSource.SIMULATED,
                checked_at=ctx.now,
                summary=(
                    "publicador simulado: recurso verificado con la visibilidad "
                    "solicitada. No existe en ninguna plataforma."
                ),
                remote_status="processed",
            ),
        )

    def capabilities(self) -> dict[str, Any]:
        return {
            **super().capabilities(),
            "simulates": self.platform.value,
            "network": False,
            "identity_space": "mock",
            "note": (
                "Un recorrido simulado no acredita integracion con la "
                "plataforma: solo ejercita la cola, los estados y los contratos."
            ),
        }
