"""Conjunto persistente de referencias de personaje por serie y version.

Ideas centrales:

* El conjunto se FIJA al iniciar el trabajo: resolver despues los archivos
  pendientes no cambia retroactivamente la identidad de la solicitud.
* La version deriva del hash de la biblia visual, salvo que se fije otra por
  configuracion. Una biblia nueva o una seleccion explicita producen OTRA
  version; cambiar el modelo de las escenas NO redibuja los personajes.
* Una referencia importada registra su tipo de origen y la declaracion de
  derechos APORTADA. El modulo no verifica esa declaracion y no finge hacerlo.
* Una referencia generada conserva proveedor, modelo, parametros, hash y modo
  real/simulado.

Las referencias mejoran el control de continuidad, pero NO garantizan
identidad perfecta entre generaciones.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ..errors import ConfigError
from ..textutil import sha256_json

Identifier = Annotated[
    str, StringConstraints(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_\-]*$")
]
Text = Annotated[str, StringConstraints(min_length=1, max_length=600)]


class ImportedReference(BaseModel):
    """Referencia aportada por quien opera el sistema."""

    model_config = ConfigDict(extra="forbid")

    character_id: Identifier
    path: Annotated[str, StringConstraints(min_length=1, max_length=400)]
    rights_declaration: Text = Field(
        description="Declaracion APORTADA. El modulo no la verifica."
    )
    source_kind: Annotated[str, StringConstraints(min_length=1, max_length=64)] = "imported"
    note: Annotated[str, StringConstraints(max_length=600)] = ""


class ReferencePack(BaseModel):
    """Archivo de referencias importadas."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    series_bible_id: Identifier
    description: Annotated[str, StringConstraints(max_length=600)] = ""
    references: list[ImportedReference] = Field(default_factory=list, max_length=40)

    def by_character(self) -> dict[str, ImportedReference]:
        return {item.character_id: item for item in self.references}


@dataclass(frozen=True)
class ReferenceSelection:
    """Seleccion FIJADA al iniciar el trabajo."""

    set_id: str
    version: str
    series_bible_id: str
    bible_sha256: str
    character_ids: tuple[str, ...]
    imported: dict[str, ImportedReference] = field(default_factory=dict)

    def selection_payload(self) -> dict:
        """Parte de la identidad de la EJECUCION.

        Incluye que personajes se han seleccionado y de donde salen, pero no
        los hashes de archivos todavia sin resolver.
        """
        return {
            "set_id": self.set_id,
            "version": self.version,
            "series_bible_id": self.series_bible_id,
            "bible_sha256": self.bible_sha256,
            "character_ids": list(self.character_ids),
            "imported": {
                cid: {"path": ref.path, "source_kind": ref.source_kind}
                for cid, ref in sorted(self.imported.items())
            },
        }


def bible_digest(bible) -> str:
    """Hash de la biblia visual tal y como la fijo el modulo 1."""
    return sha256_json(bible.model_dump(mode="json"))


def load_reference_pack(path: Path | None) -> ReferencePack | None:
    if path is None:
        return None
    try:
        crudo = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"No existe el paquete de referencias: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"JSON invalido en {path}: {exc}") from exc
    try:
        return ReferencePack.model_validate(crudo)
    except Exception as exc:
        raise ConfigError(f"Paquete de referencias invalido ({path}): {exc}") from exc


def build_selection(
    *,
    bible,
    used_character_ids: set[str],
    settings,
    pack: ReferencePack | None,
) -> ReferenceSelection:
    """Fija la seleccion de referencias del trabajo.

    Solo entran los personajes PRESENTES en las escenas: los ausentes quedan
    fuera y no se adjuntan a ninguna peticion.
    """
    disponibles = {personaje.character_id for personaje in bible.characters}
    desconocidos = sorted(used_character_ids - disponibles)
    if desconocidos:
        raise ConfigError(
            "El guion referencia personajes que no estan en la biblia visual: "
            + ", ".join(desconocidos)
        )
    digest = bible_digest(bible)
    version = settings.media_reference_set_version or f"v1-{digest[:12]}"
    importadas: dict[str, ImportedReference] = {}
    if pack is not None:
        if pack.series_bible_id != bible.series_id:
            raise ConfigError(
                f"El paquete de referencias es de la serie {pack.series_bible_id!r} y la "
                f"biblia del guion es {bible.series_id!r}."
            )
        importadas = {
            cid: ref
            for cid, ref in pack.by_character().items()
            if cid in used_character_ids
        }
        for cid, ref in importadas.items():
            if not Path(ref.path).is_file():
                raise ConfigError(
                    f"La referencia importada de {cid} no existe en {ref.path}."
                )
    return ReferenceSelection(
        set_id=f"{bible.series_id}-{version}",
        version=version,
        series_bible_id=bible.series_id,
        bible_sha256=digest,
        character_ids=tuple(sorted(used_character_ids)),
        imported=importadas,
    )


def reference_prompt(bible, character_id: str) -> str:
    """Prompt para generar la referencia de un personaje.

    Usa exclusivamente la descripcion de la biblia: aspecto y ropa tal y como
    los fijo el modulo 1. No anade protagonistas ni cambia nada del guion.
    """
    personaje = next(
        (item for item in bible.characters if item.character_id == character_id), None
    )
    if personaje is None:
        raise ConfigError(f"Personaje desconocido en la biblia: {character_id}")
    return (
        f"Character reference sheet, single character, neutral pose, plain background. "
        f"{personaje.name}: {personaje.description} Wardrobe: {personaje.wardrobe} "
        f"Style: {bible.style_prompt} "
        f"No text, no letters, no captions, no watermark, no logo in the image."
    )
