"""Catalogo de cuentas de destino.

Una cuenta aqui es un ALIAS local mas el identificador exacto que debe
responder la plataforma. El identificador no es un secreto -no autoriza nada
por si solo- pero es imprescindible: publicar en la cuenta equivocada no se
deshace, asi que el envio real exige que el ID observado coincida con el
esperado.

El catalogo se lee de un archivo JSON indicado en la configuracion. Si no hay
archivo, se construye uno minimo con las variables de entorno del canal de
YouTube y del usuario de Instagram. Nunca se inventa un ID.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import ConfigError
from ..schemas.common import Platform

#: Que clase de identificador espera cada plataforma.
ACCOUNT_ID_KIND: dict[Platform, str] = {
    Platform.YOUTUBE_SHORTS: "youtube_channel_id",
    Platform.INSTAGRAM_REELS: "instagram_user_id",
    Platform.TIKTOK: "manual_handle",
}


@dataclass(frozen=True)
class Account:
    """Una cuenta configurada. Sin tokens ni credenciales: solo identidad."""

    alias: str
    platform: Platform
    account_id: str | None
    label: str | None = None
    #: Instagram: la Pagina vinculada, cuando el operador la declara.
    page_id: str | None = None

    @property
    def account_id_kind(self) -> str:
        return ACCOUNT_ID_KIND[self.platform]

    def describe(self) -> dict[str, Any]:
        return {
            "alias": self.alias,
            "platform": self.platform.value,
            "account_id": self.account_id,
            "account_id_kind": self.account_id_kind,
            "label": self.label,
            "page_id": self.page_id,
        }


def _desde_archivo(path: Path) -> dict[str, Account]:
    try:
        datos = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConfigError(
            f"El catalogo de cuentas {path} no se puede leer: {exc}",
            details={"path": str(path)},
        ) from exc
    entradas = datos.get("accounts")
    if not isinstance(entradas, list) or not entradas:
        raise ConfigError(
            f"El catalogo de cuentas {path} no trae una lista 'accounts'.",
            details={"path": str(path)},
        )
    cuentas: dict[str, Account] = {}
    for entrada in entradas:
        if not isinstance(entrada, dict):
            raise ConfigError("Cada cuenta del catalogo debe ser un objeto JSON.")
        alias = str(entrada.get("alias", "")).strip()
        plataforma = str(entrada.get("platform", "")).strip()
        if not alias:
            raise ConfigError("Hay una cuenta sin 'alias' en el catalogo.")
        try:
            destino = Platform(plataforma)
        except ValueError as exc:
            raise ConfigError(
                f"La cuenta {alias!r} declara una plataforma desconocida: {plataforma!r}",
                details={"alias": alias, "platform": plataforma},
            ) from exc
        if alias in cuentas:
            raise ConfigError(f"El alias {alias!r} esta repetido en el catalogo.")
        identificador = entrada.get("account_id")
        cuentas[alias] = Account(
            alias=alias,
            platform=destino,
            account_id=str(identificador).strip() if identificador else None,
            label=(str(entrada["label"]) if entrada.get("label") else None),
            page_id=(str(entrada["page_id"]) if entrada.get("page_id") else None),
        )
    return cuentas


def _desde_entorno(settings: Any) -> dict[str, Account]:
    """Catalogo minimo cuando no hay archivo. Solo con lo que este definido."""
    cuentas: dict[str, Account] = {}
    if getattr(settings, "youtube_channel_id", None):
        cuentas["youtube"] = Account(
            alias="youtube",
            platform=Platform.YOUTUBE_SHORTS,
            account_id=str(settings.youtube_channel_id),
        )
    if getattr(settings, "instagram_user_id", None):
        cuentas["instagram"] = Account(
            alias="instagram",
            platform=Platform.INSTAGRAM_REELS,
            account_id=str(settings.instagram_user_id),
            page_id=(
                str(settings.instagram_page_id)
                if getattr(settings, "instagram_page_id", None)
                else None
            ),
        )
    return cuentas


def load_accounts(settings: Any, *, path: Path | None = None) -> dict[str, Account]:
    """Devuelve el catalogo por alias. Un catalogo vacio es valido."""
    elegido = path or getattr(settings, "publish_accounts_path", None)
    if elegido:
        return _desde_archivo(Path(elegido))
    return _desde_entorno(settings)


def resolve_account(
    cuentas: dict[str, Account], alias: str, *, platform: Platform
) -> Account:
    """Resuelve un alias exigiendo que sea de la plataforma pedida."""
    cuenta = cuentas.get(alias)
    if cuenta is None:
        disponibles = ", ".join(sorted(cuentas)) or "(catalogo vacio)"
        raise ConfigError(
            f"No hay ninguna cuenta con alias {alias!r}. Disponibles: {disponibles}. "
            "Declara el catalogo en VIRALGEN_PUBLISH_ACCOUNTS_PATH.",
            details={"alias": alias, "available": sorted(cuentas)},
        )
    if cuenta.platform is not platform:
        raise ConfigError(
            f"La cuenta {alias!r} es de {cuenta.platform.value}, no de {platform.value}.",
            details={"alias": alias},
        )
    return cuenta
