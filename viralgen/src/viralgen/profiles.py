"""Perfiles editoriales y biblia visual de serie.

Ambos son datos: viven en archivos JSON validados y se pueden editar sin
tocar codigo. Los rangos de duracion y de escenas son decisiones DE ESTE
PROYECTO, no limites universales de las plataformas.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .errors import ProfileError
from .schemas.common import CaptionStyle, Channel, Platform
from .textutil import sha256_json

Identifier = Annotated[
    str, StringConstraints(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_\-]*$")
]
Text = Annotated[str, StringConstraints(min_length=1, max_length=600)]


class BibleCharacter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    character_id: Identifier
    name: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    description: Text
    wardrobe: Text


class SeriesBible(BaseModel):
    """Identidad visual estable de una serie. No se reinventa por episodio."""

    model_config = ConfigDict(extra="forbid")

    series_id: Identifier
    title: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    style_prompt: Text
    color_palette: list[Annotated[str, StringConstraints(min_length=1, max_length=48)]] = Field(
        min_length=2, max_length=8
    )
    negative_prompt: Text
    characters: list[BibleCharacter] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def _unique_characters(self) -> "SeriesBible":
        ids = [character.character_id for character in self.characters]
        if len(ids) != len(set(ids)):
            raise ValueError("character_id duplicado en la biblia visual")
        return self


class SeriesBibleFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    series: list[SeriesBible] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def _unique_series(self) -> "SeriesBibleFile":
        ids = [item.series_id for item in self.series]
        if len(ids) != len(set(ids)):
            raise ValueError("series_id duplicado")
        return self


class Profile(BaseModel):
    """Perfil editorial: canal, duracion objetivo, ritmo y limites de escenas."""

    model_config = ConfigDict(extra="forbid")

    profile_id: Identifier
    channel: Channel
    audience: Text
    target_platforms: list[Platform] = Field(min_length=1, max_length=3)

    default_duration_s: float = Field(gt=0, le=600)
    min_duration_s: float = Field(gt=0, le=600)
    max_duration_s: float = Field(gt=0, le=600)
    target_wpm: int = Field(ge=60, le=260)

    min_scenes: int = Field(ge=2, le=20)
    max_scenes: int = Field(ge=2, le=20)
    video_scene_budget: int = Field(
        ge=0, le=20, description="Maximo de escenas con asset_type=video (control de coste)."
    )

    width: int = Field(ge=240, le=4320)
    height: int = Field(ge=240, le=7680)
    fps: int = Field(ge=1, le=120)
    aspect_ratio: Literal["9:16", "1:1", "4:5", "16:9"]

    language: Annotated[str, StringConstraints(pattern=r"^[a-z]{2}(-[A-Z]{2})?$")] = "es"
    visual_prompt_language: Annotated[str, StringConstraints(pattern=r"^[a-z]{2}$")] = "en"
    caption_style: CaptionStyle

    requires_evidence: bool = Field(
        description="True cuando toda afirmacion factual debe enlazar con el catalogo."
    )
    made_for_kids: bool | None = Field(
        default=None, description="Valor para YouTube. True en el perfil infantil."
    )
    series_bible_id: Identifier

    niches: list[Text] = Field(min_length=1, max_length=12)
    editorial_guidance: Text
    avoid: list[Text] = Field(min_length=1, max_length=20)
    voice_direction_hint: Text
    music_mood_hint: Text

    @model_validator(mode="after")
    def _coherent_ranges(self) -> "Profile":
        if self.min_duration_s > self.max_duration_s:
            raise ValueError("min_duration_s no puede superar a max_duration_s")
        if not (self.min_duration_s <= self.default_duration_s <= self.max_duration_s):
            raise ValueError("default_duration_s debe estar dentro del rango del perfil")
        if self.min_scenes > self.max_scenes:
            raise ValueError("min_scenes no puede superar a max_scenes")
        return self

    # -- Ayudas -----------------------------------------------------------

    def resolve_duration(self, requested: float | None) -> float:
        """Duracion objetivo efectiva; valida el rango del perfil."""
        if requested is None:
            return float(self.default_duration_s)
        if not (self.min_duration_s <= requested <= self.max_duration_s):
            raise ProfileError(
                f"--duration {requested:g} s esta fuera del rango del perfil "
                f"{self.profile_id} ({self.min_duration_s:g}-{self.max_duration_s:g} s).",
                details={
                    "profile_id": self.profile_id,
                    "min_duration_s": self.min_duration_s,
                    "max_duration_s": self.max_duration_s,
                },
            )
        return float(requested)

    def hashable_view(self) -> dict:
        return self.model_dump(mode="json")


class ProfilesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    profiles: list[Profile] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def _unique_profiles(self) -> "ProfilesFile":
        ids = [profile.profile_id for profile in self.profiles]
        if len(ids) != len(set(ids)):
            raise ValueError("profile_id duplicado")
        return self


# ---------------------------------------------------------------------------
# Carga
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProfileError(f"No existe el archivo: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ProfileError(f"JSON invalido en {path}: {exc}") from exc
    except OSError as exc:
        raise ProfileError(f"No se pudo leer {path}: {exc}") from exc


def _packaged_json(name: str) -> dict:
    text = resources.files("viralgen.data").joinpath(name).read_text(encoding="utf-8")
    return json.loads(text)


@lru_cache(maxsize=8)
def _load_profiles_cached(path_str: str | None) -> ProfilesFile:
    raw = _read_json(Path(path_str)) if path_str else _packaged_json("profiles.json")
    try:
        return ProfilesFile.model_validate(raw)
    except Exception as exc:
        raise ProfileError(f"Perfiles invalidos ({path_str or 'empaquetados'}): {exc}") from exc


@lru_cache(maxsize=8)
def _load_bible_cached(path_str: str | None) -> SeriesBibleFile:
    raw = _read_json(Path(path_str)) if path_str else _packaged_json("series_bible.json")
    try:
        return SeriesBibleFile.model_validate(raw)
    except Exception as exc:
        raise ProfileError(f"Biblia visual invalida ({path_str or 'empaquetada'}): {exc}") from exc


def load_profiles(path: Path | None = None) -> ProfilesFile:
    return _load_profiles_cached(str(path) if path else None)


def load_series_bibles(path: Path | None = None) -> SeriesBibleFile:
    return _load_bible_cached(str(path) if path else None)


def get_profile(profile_id: str, path: Path | None = None) -> Profile:
    profiles = load_profiles(path)
    for profile in profiles.profiles:
        if profile.profile_id == profile_id:
            return profile
    available = ", ".join(sorted(p.profile_id for p in profiles.profiles))
    raise ProfileError(
        f"Perfil desconocido: {profile_id!r}. Disponibles: {available}",
        details={"available": available.split(", ")},
    )


def get_series_bible(series_id: str, path: Path | None = None) -> SeriesBible:
    bibles = load_series_bibles(path)
    for bible in bibles.series:
        if bible.series_id == series_id:
            return bible
    available = ", ".join(sorted(b.series_id for b in bibles.series))
    raise ProfileError(
        f"Serie desconocida: {series_id!r}. Disponibles: {available}",
        details={"available": available.split(", ")},
    )


def profile_config_hash_extra(profile: Profile, bible: SeriesBible) -> dict:
    """Parte de `config_hash` que depende del perfil y de la biblia visual."""
    return {
        "profile": profile.hashable_view(),
        "series_bible": bible.model_dump(mode="json"),
    }


def profiles_digest(profile: Profile, bible: SeriesBible) -> str:
    return sha256_json(profile_config_hash_extra(profile, bible))
