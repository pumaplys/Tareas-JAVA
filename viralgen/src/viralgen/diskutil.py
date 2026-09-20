"""Comprobacion de espacio libre y escritura atomica de JSON."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .errors import DiskSpaceError, ViralgenError


def free_mb(path: Path) -> float:
    """Megabytes libres en el sistema de archivos que contiene `path`.

    Sube por los padres hasta encontrar un directorio existente, de modo que
    funcione tambien antes de crear DATA_DIR.
    """
    probe = path.expanduser().resolve()
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    usage = shutil.disk_usage(probe)
    return usage.free / (1024 * 1024)


def ensure_free_space(path: Path, min_free_mb: int, *, stage: str) -> float:
    """Lanza DiskSpaceError si el espacio libre esta por debajo del umbral."""
    available = free_mb(path)
    if available < min_free_mb:
        raise DiskSpaceError(
            f"Espacio libre insuficiente en {path}: {available:.0f} MB disponibles, "
            f"se exigen al menos {min_free_mb} MB (MIN_FREE_DISK_MB). "
            "El trabajo se detiene antes de escribir para no agotar el disco.",
            details={"stage": stage, "free_mb": round(available, 1), "min_free_mb": min_free_mb},
        )
    return available


def ensure_within(base: Path, candidate: Path) -> Path:
    """Valida que `candidate` cae dentro de `base` y devuelve la ruta resuelta.

    Impide que una ruta propuesta por el modelo o por el usuario escriba
    fuera de DATA_DIR.
    """
    base_resolved = base.expanduser().resolve()
    target = candidate.expanduser()
    if not target.is_absolute():
        target = base_resolved / target
    target = target.resolve()
    if base_resolved != target and base_resolved not in target.parents:
        raise ViralgenError(
            f"Ruta fuera del directorio permitido: {target} no esta dentro de {base_resolved}",
            details={"base": str(base_resolved), "path": str(target)},
        )
    return target


def atomic_write_text(path: Path, text: str) -> None:
    """Escribe UTF-8 mediante archivo temporal + reemplazo atomico."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, data: Any) -> None:
    """Serializa `data` como JSON UTF-8 legible y lo escribe atomicamente."""
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    atomic_write_text(path, text)
