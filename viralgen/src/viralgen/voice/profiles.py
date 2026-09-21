"""Configuracion de voz por perfil y catalogo local de sonido.

Los `voice_id` vienen VACIOS a proposito: son identificadores de la cuenta
real de ElevenLabs y no se inventan. En modo real hay que rellenarlos en el
archivo (o definir ELEVENLABS_VOICE_ID como valor global) o el trabajo se
detiene con un error de configuracion.

`voice_direction` y `pronunciation_notes` del guion son INDICACIONES
EDITORIALES para quien revise: no se envian dentro del texto que se pronuncia
ni se traducen automaticamente a parametros del proveedor. Lo unico que viaja
como parametro es el bloque `settings` de este archivo.
"""

from __future__ import annotations

import json
from enum import StrEnum
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from ..errors import ConfigError
from ..textutil import sha256_json

Identifier = Annotated[
    str, StringConstraints(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_\-]*$")
]
Text = Annotated[str, StringConstraints(min_length=1, max_length=600)]

#: Parametros que se aceptan en `settings`. Se envian como `voice_settings`.
#: Deben ser admitidos por el modelo configurado; no se anaden otros a ciegas.
ALLOWED_VOICE_SETTINGS = {"stability", "similarity_boost", "style", "use_speaker_boost"}


class SoundKind(StrEnum):
    SFX = "sfx"
    MUSIC = "music"


class VoiceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: Identifier
    voice_id: str | None = Field(
        default=None,
        max_length=120,
        description="Identificador de voz de la cuenta real. Nunca inventado.",
    )
    voice_label: Text
    direction: Text
    settings: dict[str, float | bool] = Field(default_factory=dict)
    sfx_asset_id: Identifier | None = None
    music_asset_id: Identifier | None = None

    @model_validator(mode="after")
    def _known_settings(self) -> "VoiceProfile":
        desconocidos = sorted(set(self.settings) - ALLOWED_VOICE_SETTINGS)
        if desconocidos:
            raise ValueError(
                "parametros de voz no admitidos por esta configuracion: "
                + ", ".join(desconocidos)
            )
        return self

    def effective_settings(self) -> dict[str, float | bool]:
        return {clave: self.settings[clave] for clave in sorted(self.settings)}

    def settings_hash(self) -> str:
        return sha256_json(
            {
                "profile_id": self.profile_id,
                "settings": self.effective_settings(),
            }
        )


class VoiceProfilesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    profiles: list[VoiceProfile] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def _unique(self) -> "VoiceProfilesFile":
        ids = [perfil.profile_id for perfil in self.profiles]
        if len(ids) != len(set(ids)):
            raise ValueError("profile_id duplicado en la configuracion de voz")
        return self


class SoundAsset(BaseModel):
    """Archivo local de sonido. Nunca se descarga nada."""

    model_config = ConfigDict(extra="forbid")

    asset_id: Identifier
    kind: SoundKind
    path: Annotated[str, StringConstraints(min_length=1, max_length=400)]
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    license_note: Text


class SceneCueConfig(BaseModel):
    """Mapeo explicito de una indicacion del guion a un asset concreto.

    Las descripciones de efectos del guion son texto libre: NO se interpretan
    como rutas ni como URLs. Solo se usa este mapeo explicito.
    """

    model_config = ConfigDict(extra="forbid")

    scene_id: Identifier
    cue_type: SoundKind
    asset_id: Identifier
    gain_db: float = Field(default=-14.0, ge=-60.0, le=0.0)


class SoundAssetsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    description: Annotated[str, StringConstraints(max_length=600)] = ""
    assets: list[SoundAsset] = Field(default_factory=list, max_length=200)
    scene_cues: list[SceneCueConfig] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def _coherent(self) -> "SoundAssetsFile":
        ids = [asset.asset_id for asset in self.assets]
        if len(ids) != len(set(ids)):
            raise ValueError("asset_id duplicado")
        conocidos = set(ids)
        for cue in self.scene_cues:
            if cue.asset_id not in conocidos:
                raise ValueError(f"scene_cues referencia un asset inexistente: {cue.asset_id}")
        return self

    def by_id(self) -> dict[str, SoundAsset]:
        return {asset.asset_id: asset for asset in self.assets}


# ---------------------------------------------------------------------------
# Carga
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"No existe el archivo: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"JSON invalido en {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"No se pudo leer {path}: {exc}") from exc


@lru_cache(maxsize=8)
def _load_voice_profiles(path_str: str | None) -> VoiceProfilesFile:
    if path_str:
        raw = _read_json(Path(path_str))
    else:
        raw = json.loads(
            resources.files("viralgen.voice.data")
            .joinpath("voice_profiles.json")
            .read_text(encoding="utf-8")
        )
    try:
        return VoiceProfilesFile.model_validate(raw)
    except Exception as exc:
        raise ConfigError(
            f"Configuracion de voz invalida ({path_str or 'empaquetada'}): {exc}"
        ) from exc


def load_voice_profiles(path: Path | None = None) -> VoiceProfilesFile:
    return _load_voice_profiles(str(path) if path else None)


def get_voice_profile(profile_id: str, path: Path | None = None) -> VoiceProfile:
    archivo = load_voice_profiles(path)
    for perfil in archivo.profiles:
        if perfil.profile_id == profile_id:
            return perfil
    disponibles = ", ".join(sorted(p.profile_id for p in archivo.profiles))
    raise ConfigError(
        f"No hay configuracion de voz para el perfil {profile_id!r}. Disponibles: {disponibles}",
        details={"available": disponibles.split(", ")},
    )


def resolve_voice_id(profile: VoiceProfile, settings, *, simulation: bool) -> str:
    """Determina el voice_id efectivo.

    Orden: el del archivo de perfiles, y si no, ELEVENLABS_VOICE_ID. En
    simulacion se usa un identificador sintetico. En real, si no hay ninguno,
    se detiene: los identificadores de voz los aporta la cuenta.
    """
    if simulation:
        return f"mock-voice-{profile.profile_id}"
    elegido = profile.voice_id or settings.elevenlabs_voice_id
    if not elegido:
        raise ConfigError(
            f"Falta el voice_id para el perfil {profile.profile_id!r}. Rellena "
            "`voice_id` en la configuracion de voz o define ELEVENLABS_VOICE_ID. "
            "Los identificadores de voz los aporta tu cuenta de ElevenLabs: este "
            "proyecto no inventa ninguno.",
            details={"profile_id": profile.profile_id},
        )
    return elegido


def load_sound_assets(path: Path | None) -> SoundAssetsFile:
    """Catalogo local de sonido. Sin archivo, catalogo vacio."""
    if path is None:
        return SoundAssetsFile(schema_version="1.0")
    raw = _read_json(Path(path))
    try:
        return SoundAssetsFile.model_validate(raw)
    except Exception as exc:
        raise ConfigError(f"Catalogo de sonido invalido ({path}): {exc}") from exc
