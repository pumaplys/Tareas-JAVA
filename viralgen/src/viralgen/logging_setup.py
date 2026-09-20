"""Registro: consola (stderr) + archivo rotativo, con redaccion de secretos.

stdout queda reservado para el resumen JSON de la CLI; todo mensaje humano
va a stderr o al archivo de log.
"""

from __future__ import annotations

import logging
import logging.handlers
import re
from pathlib import Path

LOGGER_NAME = "viralgen"

# Patrones que jamas deben aparecer en el log. No se registran claves,
# cabeceras de autorizacion ni volcados completos del entorno.
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)(authorization\s*[:=]\s*)(bearer\s+)?\S+"),
    re.compile(r"(?i)(api[_\-]?key\s*[:=]\s*)\S+"),
    re.compile(r"(?i)(OPENAI_API_KEY\s*=\s*)\S+"),
]


class RedactingFilter(logging.Filter):
    """Sustituye secretos por ``[REDACTED]`` en el mensaje ya formateado."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - mensajes mal formateados
            return True
        redacted = message
        for pattern in _SECRET_PATTERNS:
            redacted = pattern.sub(_replace, redacted)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def _replace(match: re.Match[str]) -> str:
    if match.lastindex:
        return f"{match.group(1)}[REDACTED]"
    return "[REDACTED]"


def configure_logging(log_dir: Path, level: str, *, max_bytes: int, backup_count: int) -> logging.Logger:
    """Configura el logger del paquete. Es idempotente dentro del proceso."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    redactor = RedactingFilter()

    console = logging.StreamHandler()  # stderr por defecto
    console.setLevel(level)
    console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    console.addFilter(redactor)
    logger.addHandler(console)

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / "viralgen.log",
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        file_handler.addFilter(redactor)
        logger.addHandler(file_handler)
    except OSError as exc:  # disco lleno o permisos: seguimos con consola
        logger.warning("No se pudo abrir el log de archivo (%s); solo consola.", exc)

    return logger


def get_logger(suffix: str | None = None) -> logging.Logger:
    return logging.getLogger(LOGGER_NAME if not suffix else f"{LOGGER_NAME}.{suffix}")
