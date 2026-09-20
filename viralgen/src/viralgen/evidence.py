"""Catalogo local de hechos revisados (``--source-pack``).

Esta primera version no lleva buscador ni scraper: el catalogo se aporta como
archivo JSON, se valida, se le calcula un hash y se filtra localmente.

Limites explicitos de este mecanismo (documentados tambien en el README):

* El sistema RESTRINGE la generacion a las fuentes aportadas. No verifica de
  forma independiente que la fuente respalde semanticamente la afirmacion.
* Una URL sintacticamente valida no demuestra nada sobre su contenido.
* La aprobacion procede del catalogo importado, nunca de una decision del LLM.
* Los campos de procedencia se copian del catalogo y no se confian al modelo.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .errors import SourcePackError
from .schemas.common import ReviewStatus
from .schemas.document import FactRecord
from .textutil import jaccard_similarity, sha256_json


class SourcePack(BaseModel):
    """Archivo de catalogo importado con ``--source-pack``."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    pack_id: Annotated[
        str, StringConstraints(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_\-]*$")
    ]
    description: Annotated[str, StringConstraints(max_length=600)] = ""
    facts: list[FactRecord] = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _validate_facts(self) -> "SourcePack":
        seen: set[str] = set()
        limit = datetime.now(timezone.utc) + timedelta(days=1)
        for fact in self.facts:
            if fact.fact_id in seen:
                raise ValueError(f"fact_id duplicado: {fact.fact_id}")
            seen.add(fact.fact_id)
            if fact.checked_at > limit:
                raise ValueError(f"checked_at en el futuro para {fact.fact_id}")
        return self

    # -- Derivados ---------------------------------------------------------

    def content_hash(self) -> str:
        """SHA-256 del contenido canonico del catalogo.

        Se calcula sobre ``{"pack_id": ..., "facts": [...]}`` con los hechos
        ordenados por ``fact_id`` y serializados en modo JSON, con claves
        ordenadas y sin espacios. Reordenar el archivo no cambia el hash;
        cambiar un solo caracter de cualquier campo, si.
        """
        payload = {
            "pack_id": self.pack_id,
            "facts": [
                fact.model_dump(mode="json")
                for fact in sorted(self.facts, key=lambda item: item.fact_id)
            ],
        }
        return sha256_json(payload)

    def by_id(self) -> dict[str, FactRecord]:
        return {fact.fact_id: fact for fact in self.facts}


@dataclass(frozen=True)
class FactSelection:
    """Resultado de filtrar el catalogo antes de enviarlo al modelo."""

    usable: list[FactRecord]
    selected: list[FactRecord]
    rejected: dict[str, str]
    truncated: bool

    def selected_ids(self) -> list[str]:
        return [fact.fact_id for fact in self.selected]

    def report(self) -> dict:
        return {
            "usable_count": len(self.usable),
            "selected_count": len(self.selected),
            "selected_fact_ids": self.selected_ids(),
            "rejected": self.rejected,
            "truncated": self.truncated,
        }


def load_source_pack(path: Path) -> SourcePack:
    """Lee y valida un catalogo de hechos."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SourcePackError(f"No existe el catalogo de hechos: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SourcePackError(f"JSON invalido en el catalogo {path}: {exc}") from exc
    except OSError as exc:
        raise SourcePackError(f"No se pudo leer el catalogo {path}: {exc}") from exc
    try:
        return SourcePack.model_validate(raw)
    except Exception as exc:
        raise SourcePackError(f"Catalogo de hechos invalido ({path}): {exc}") from exc


def usable_facts(pack: SourcePack, *, simulation: bool) -> tuple[list[FactRecord], dict[str, str]]:
    """Separa los hechos utilizables de los descartados, con su motivo.

    Reglas:

    * Solo ``review_status == approved``.
    * ``demo_only=True`` se acepta UNICAMENTE en simulacion. En modo real un
      catalogo de prueba nunca alimenta un guion.
    """
    usable: list[FactRecord] = []
    rejected: dict[str, str] = {}
    for fact in pack.facts:
        if fact.review_status is not ReviewStatus.APPROVED:
            rejected[fact.fact_id] = f"review_status={fact.review_status.value}"
            continue
        if fact.demo_only and not simulation:
            rejected[fact.fact_id] = "demo_only=true no permitido en modo real"
            continue
        usable.append(fact)
    return usable, rejected


def select_facts_for_model(
    pack: SourcePack,
    *,
    simulation: bool,
    topic: str | None,
    limit: int,
) -> FactSelection:
    """Filtra localmente y recorta el catalogo que se envia al modelo.

    Para catalogos grandes no se manda todo: se ordenan los hechos utilizables
    por similitud lexica con el tema pedido (Jaccard sobre texto normalizado) y
    se toman los `limit` primeros. El informe indica exactamente que registros
    se seleccionaron, para que quede trazado.
    """
    usable, rejected = usable_facts(pack, simulation=simulation)
    if topic:
        ranked = sorted(
            usable,
            key=lambda fact: (
                -jaccard_similarity(topic, f"{fact.claim_text} {fact.source_title}"),
                fact.fact_id,
            ),
        )
    else:
        ranked = sorted(usable, key=lambda fact: fact.fact_id)
    selected = ranked[:limit]
    return FactSelection(
        usable=usable,
        selected=selected,
        rejected=rejected,
        truncated=len(ranked) > len(selected),
    )


def facts_for_prompt(facts: list[FactRecord]) -> list[dict]:
    """Vista reducida del catalogo para el prompt.

    Se envia lo justo para redactar (id, afirmacion, extracto y titulo). La
    URL y la fecha no se envian: se copian despues desde el catalogo, de modo
    que el modelo no pueda alterarlas ni inventarlas.
    """
    return [
        {
            "fact_id": fact.fact_id,
            "claim_text": fact.claim_text,
            "evidence_excerpt": fact.evidence_excerpt,
            "source_title": fact.source_title,
        }
        for fact in facts
    ]
