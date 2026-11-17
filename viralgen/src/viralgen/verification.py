"""Primitivas del registro de verificacion documental.

El modulo 5 (`publish/verification.py`) introdujo este patron para un problema
concreto: los adaptadores hablan con APIs de terceros cuyos parametros exactos
-nombres de campo, versiones, tamanos, tarifas- cambian, y el encargo prohibe
*sustituir una referencia inaccesible por una afirmacion de haberla leido*.

El modulo 3 (medios visuales) tiene el mismo problema con el catalogo de
modelos de OpenAI, asi que las primitivas viven aqui y las usan los dos
registros. Lo que NO se comparte son las entradas: cada registro declara las
suyas, con sus fuentes y sus fechas.

Reglas que esto impone, y que son el motivo de que sea codigo y no un README:

* Un estado distinto de `pending` EXIGE evidencia que lo respalde.
* Un `pending` con evidencia es una contradiccion y falla al construirse.
* `partial` no levanta un bloqueo: deja escrito lo que ya se sabe y lo que
  falta.
* Un intento de consulta que no confirmo nada se guarda como `note`, nunca
  como evidencia.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

#: Quien aporta una evidencia.
REVIEWER = "revisor"
DEVELOPER = "desarrollo"


class CheckStatus(StrEnum):
    """Estado de la verificacion de un parametro."""

    PENDING = "pending"
    """Sin evidencia."""

    PARTIAL = "partial"
    """Hay evidencia, pero no cubre la condicion completa. Sigue bloqueando."""

    VERIFIED = "verified"
    """La condicion se cumple con la evidencia aportada."""


@dataclass(frozen=True)
class SourceEvidence:
    """Constancia de que alguien leyo la fuente, y de que confirmo.

    No afirma nada sobre quien NO la leyo: si `confirmed_by` es el revisor, es
    porque este entorno no pudo abrir la URL. El `scope` acota el alcance:
    confirmar un campo no verifica la lista entera de la que forma parte.
    """

    confirmed_by: str
    confirmed_on: str
    states: str
    scope: str
    sources: tuple[str, ...] = ()

    def describe(self) -> dict:
        return {
            "confirmed_by": self.confirmed_by,
            "confirmed_on": self.confirmed_on,
            "states": self.states,
            "scope": self.scope,
            "sources": list(self.sources),
        }


@dataclass(frozen=True)
class PendingCheck:
    """Un parametro de protocolo, con el supuesto que se usa y su estado."""

    check_id: str
    #: Destino o ambito afectado. El registro que lo contiene fija el
    #: vocabulario: en el modulo 5 son plataformas ("youtube", "instagram"),
    #: en el modulo 3 son modelos ("gpt-image-2.6-sunburst").
    target: str
    source_tag: str
    source_url: str
    what: str
    #: Lo que el codigo hace hoy con este parametro.
    assumption: str
    #: Condicion exacta para considerarlo verificado.
    verified_when: str
    #: Si impediria el envio real mientras no este verificado.
    blocks_real: bool
    status: CheckStatus = CheckStatus.PENDING
    #: Historial de consultas, de la mas antigua a la mas reciente.
    evidence: tuple[SourceEvidence, ...] = field(default_factory=tuple)
    #: Observaciones fechadas que NO confirman nada, como un intento de
    #: consulta que devolvio un error de acceso. Se guardan para que conste que
    #: alguien lo intento, sin que eso cuente como evidencia.
    notes: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.status is not CheckStatus.PENDING and not self.evidence:
            raise ValueError(
                f"{self.check_id}: un estado distinto de `pending` necesita "
                "evidencia que lo respalde"
            )
        if self.status is CheckStatus.PENDING and self.evidence:
            raise ValueError(
                f"{self.check_id}: hay evidencia, asi que el estado no puede ser "
                "`pending`; usa `partial` si no cierra la condicion"
            )

    @property
    def verified(self) -> bool:
        return self.status is CheckStatus.VERIFIED

    @property
    def blocking(self) -> bool:
        """Bloquea mientras no este VERIFICADO. Un `partial` sigue bloqueando."""
        return self.blocks_real and not self.verified

    @property
    def latest_evidence(self) -> SourceEvidence | None:
        return self.evidence[-1] if self.evidence else None

    def describe(self) -> dict:
        return {
            "check_id": self.check_id,
            "target": self.target,
            "source": self.source_tag,
            "source_url": self.source_url,
            "what": self.what,
            "assumption": self.assumption,
            "verified_when": self.verified_when,
            "status": self.status.value,
            "blocks_real_dispatch": self.blocking,
            "evidence": [entrada.describe() for entrada in self.evidence],
            "notes": list(self.notes),
        }


def totals_for(checks: tuple[PendingCheck, ...]) -> dict[str, int]:
    """Recuentos de un conjunto de entradas.

    `pending + partial + verified` SUMA el total: un informe que solo contara
    dos de los tres estados mentiria por omision, y ya lo hizo una vez.
    """
    return {
        "total": len(checks),
        "pending": sum(1 for c in checks if c.status is CheckStatus.PENDING),
        "partial": sum(1 for c in checks if c.status is CheckStatus.PARTIAL),
        "verified": sum(1 for c in checks if c.verified),
        "blocking": sum(1 for c in checks if c.blocking),
    }


__all__ = [
    "REVIEWER",
    "DEVELOPER",
    "CheckStatus",
    "SourceEvidence",
    "PendingCheck",
    "totals_for",
]
