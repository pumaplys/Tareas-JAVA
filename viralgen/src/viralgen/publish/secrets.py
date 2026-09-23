"""Almacen privado de material sensible.

Que guarda: tokens de OAuth, URIs de sesion de subida y URLs firmadas de
staging. Ninguna de esas cosas entra en el plan, en el recibo, en los logs ni
en los ejemplos: aqui quedan en archivos con permisos 0600 dentro de un
directorio 0700, fuera del repositorio.

Que NO hace: cifrar. Un cifrado cuya clave viva al lado del archivo no protege
de nada y da una sensacion de seguridad que no corresponde. Lo que protege
aqui son los permisos del sistema de archivos y el hecho de que el directorio
este fuera del arbol del proyecto. Si hace falta mas que eso, el sitio correcto
es un gestor de secretos del sistema, y se documenta como tal.

Las escrituras son atomicas: un token a medio escribir por una caida dejaria
la cuenta inutilizable hasta reautorizar.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from ..errors import ConfigError

#: Permisos exigidos. En Ubuntu son suficientes para un unico operador.
DIR_MODE = 0o700
FILE_MODE = 0o600


class SecretStore:
    """Acceso a los archivos privados del publicador."""

    def __init__(self, base: Path) -> None:
        self.base = Path(base).expanduser()

    # -- Ubicacion ---------------------------------------------------------

    def ensure(self) -> Path:
        """Crea el directorio con permisos correctos y comprueba los que hay."""
        repositorio = Path(__file__).resolve().parents[3]
        resuelto = self.base.expanduser().resolve()
        if resuelto == repositorio or repositorio in resuelto.parents:
            raise ConfigError(
                f"El directorio de secretos {resuelto} esta dentro del repositorio. "
                "Elige una ruta fuera del arbol del proyecto: un token no debe "
                "poder acabar en un commit ni en un paquete de ejemplo.",
                details={"path": str(resuelto)},
            )
        self.base.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
        actual = stat.S_IMODE(self.base.stat().st_mode)
        if actual != DIR_MODE:
            os.chmod(self.base, DIR_MODE)
        return self.base

    def path_for(self, name: str) -> Path:
        if "/" in name or name.startswith("."):
            raise ValueError(f"nombre de secreto invalido: {name!r}")
        return self.base / name

    # -- Lectura y escritura ----------------------------------------------

    def put(self, name: str, payload: dict[str, Any]) -> Path:
        """Escribe un secreto de forma atomica con permisos 0600."""
        self.ensure()
        destino = self.path_for(name)
        descriptor, temporal = tempfile.mkstemp(dir=str(self.base), suffix=".tmp")
        try:
            os.chmod(temporal, FILE_MODE)
            with os.fdopen(descriptor, "w", encoding="utf-8") as manejador:
                json.dump(payload, manejador, ensure_ascii=False)
                manejador.flush()
                os.fsync(manejador.fileno())
            os.replace(temporal, destino)
        except BaseException:
            Path(temporal).unlink(missing_ok=True)
            raise
        os.chmod(destino, FILE_MODE)
        return destino

    def get(self, name: str) -> dict[str, Any] | None:
        destino = self.path_for(name)
        if not destino.is_file():
            return None
        modo = stat.S_IMODE(destino.stat().st_mode)
        if modo & 0o077:
            raise ConfigError(
                f"El archivo de secreto {destino} tiene permisos {oct(modo)}: "
                "debe ser 0600. Corrigelo con `chmod 600` antes de continuar.",
                details={"path": str(destino), "mode": oct(modo)},
            )
        return json.loads(destino.read_text(encoding="utf-8"))

    def delete(self, name: str) -> None:
        self.path_for(name).unlink(missing_ok=True)

    def exists(self, name: str) -> bool:
        return self.path_for(name).is_file()


def require_secret_store(settings: Any) -> SecretStore:
    """El almacen privado en modo real. Sin el no se conecta nada."""
    configurado = getattr(settings, "publish_secrets_dir", None)
    if not configurado:
        raise ConfigError(
            "Falta VIRALGEN_PUBLISH_SECRETS_DIR. El modo real guarda tokens y "
            "URIs de sesion en un directorio privado (0700, archivos 0600) "
            "fuera del repositorio. Los modos plan y mock no lo necesitan.",
            details={"missing": ["VIRALGEN_PUBLISH_SECRETS_DIR"]},
        )
    almacen = SecretStore(Path(configurado))
    almacen.ensure()
    return almacen
