"""Interfaz comun de los adaptadores de publicacion.

Un adaptador hace tres cosas y ninguna mas: comprobar la cuenta, empezar la
entrega y consultar como va. No decide si algo se puede publicar -eso ya lo
decidieron la admision y la autorizacion- y no escribe en la base de datos:
devuelve un resultado y el trabajador lo persiste.

La separacion importa porque el estado remoto es lo unico que no controlamos.
Todo lo que un adaptador afirme sobre el debe venir de una consulta, con su
evidencia, y nunca de suponer que una operacion salio bien porque no se vio
fallar.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ...schemas.common import Platform
from ..schemas import (
    DestinationMetadata,
    DestinationOptions,
    DestinationState,
    EvidenceRecord,
    PublishMode,
    StagingRef,
    StructuredError,
    TransferPhase,
    Visibility,
)
from ..secrets import SecretStore


@dataclass
class DispatchContext:
    """Todo lo que un adaptador necesita para operar un destino."""

    publication_id: str
    destination_id: str
    platform: Platform
    account_alias: str
    expected_account_id: str | None
    metadata: DestinationMetadata
    requested_visibility: Visibility
    options: DestinationOptions
    video_path: Path
    video_sha256: str
    video_size: int
    mode: PublishMode
    row: dict
    now: datetime
    settings: Any
    secrets: SecretStore | None = None
    #: Persistencia del gasto. Se llama ANTES de cada peticion, no despues:
    #: una peticion perdida tambien se ha consumido.
    spend: Callable[[int, int], None] = lambda solicitudes, bytes_: None
    #: Renovacion de la concesion durante transferencias largas.
    heartbeat: Callable[[], None] = lambda: None

    @property
    def remote_refs(self) -> dict[str, str]:
        import json

        return json.loads(self.row.get("remote_refs_json") or "{}")

    def session_name(self, sufijo: str) -> str:
        """Nombre del archivo privado de esta tarea (sesiones, contenedores)."""
        return f"session_{self.publication_id}_{self.destination_id}_{sufijo}.json"


@dataclass
class AccountCheck:
    """Resultado de comprobar la cuenta. No publica nada."""

    ok: bool
    observed_account_id: str | None = None
    candidates: list[str] = field(default_factory=list)
    detail: str = ""
    error: StructuredError | None = None


@dataclass
class StepResult:
    """Lo que el adaptador sabe tras un paso, con su evidencia."""

    state: DestinationState
    phase: TransferPhase
    remote_id: str | None = None
    remote_refs: dict[str, str] = field(default_factory=dict)
    observed_account_id: str | None = None
    observed_visibility: Visibility | None = None
    publicly_visible: bool | None = None
    permalink: str | None = None
    evidence: EvidenceRecord | None = None
    error: StructuredError | None = None
    #: Referencia al objeto temporal, SIN la URL firmada. La persiste el
    #: trabajador; la URL vive solo en el almacen privado.
    staging: StagingRef | None = None
    next_poll_in_s: float | None = None
    bytes_sent: int = 0
    requests_used: int = 0
    note: str = ""


class PublisherAdapter(ABC):
    """Contrato de un destino publicable."""

    platform: Platform
    name: str
    #: True si el adaptador habla con una API remota real.
    remote: bool = True

    @abstractmethod
    def check_account(self, ctx: DispatchContext) -> AccountCheck:
        """Comprueba identidad y acceso. Nunca publica."""

    @abstractmethod
    def start(self, ctx: DispatchContext) -> StepResult:
        """Inicia la entrega. Persiste la intencion antes de cada mutacion."""

    @abstractmethod
    def poll(self, ctx: DispatchContext) -> StepResult:
        """Consulta el estado remoto de algo ya iniciado."""

    def capabilities(self) -> dict[str, Any]:
        """Que sabe hacer este adaptador en ESTA entrega."""
        return {
            "platform": self.platform.value,
            "adapter": self.name,
            "remote_api": self.remote,
        }
