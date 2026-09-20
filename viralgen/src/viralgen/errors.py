"""Jerarquia de errores y codigos de salida del proceso.

Los codigos de salida separan las familias de fallo que el README documenta:
configuracion, proveedor, validacion, investigacion pendiente y disco.
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    """Codigos de salida del ejecutable ``viralgen``."""

    OK = 0
    """Ejecucion correcta. En ``generate`` implica ready_for_production."""

    UNEXPECTED = 1
    """Error no previsto (bug). Siempre se registra la traza en el log."""

    USAGE = 2
    """Uso incorrecto de la CLI (lo emite argparse)."""

    CONFIG = 3
    """Configuracion, perfil, biblia visual o catalogo de hechos invalidos."""

    PROVIDER = 4
    """Fallo del proveedor: transporte agotado, rechazo, respuesta incompleta."""

    VALIDATION = 5
    """El documento no supera las validaciones de integridad; no se exporta."""

    NEEDS_RESEARCH = 6
    """Faltan fuentes aprobadas: se corta antes de hacer llamadas de pago."""

    DISK = 7
    """Espacio libre por debajo de MIN_FREE_DISK_MB."""

    CONFLICT = 8
    """Misma --job-key con parametros distintos."""

    LOCKED = 9
    """Ya hay otra ejecucion del worker en curso."""

    NEEDS_REVIEW = 10
    """Documento exportado y valido, pero con avisos: requiere revision humana."""


class ViralgenError(Exception):
    """Base de todos los errores controlados del modulo."""

    exit_code: ExitCode = ExitCode.UNEXPECTED
    code: str = "unexpected_error"

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict:
        return {
            "error_code": self.code,
            "error_message": self.message,
            "details": self.details,
        }


class ConfigError(ViralgenError):
    exit_code = ExitCode.CONFIG
    code = "config_error"


class ProfileError(ConfigError):
    code = "profile_error"


class SourcePackError(ConfigError):
    code = "source_pack_error"


class ProviderError(ViralgenError):
    exit_code = ExitCode.PROVIDER
    code = "provider_error"


class ProviderTransientError(ProviderError):
    """Fallo reintentable: conexion, timeout, 429 temporal o 5xx."""

    code = "provider_transient_error"


class ProviderPermanentError(ProviderError):
    """Fallo NO reintentable: clave, permisos, cuota, modelo incompatible."""

    code = "provider_permanent_error"


class ProviderRefusalError(ProviderError):
    """El modelo devolvio un rechazo explicito en lugar de contenido."""

    code = "provider_refusal"


class ProviderIncompleteError(ProviderError):
    """La respuesta quedo incompleta (p. ej. max_output_tokens)."""

    code = "provider_incomplete"


class CallBudgetExceededError(ProviderError):
    """Se alcanzo MAX_CALLS_PER_JOB; no se emite ninguna peticion mas."""

    code = "call_budget_exceeded"


class DocumentValidationError(ViralgenError):
    exit_code = ExitCode.VALIDATION
    code = "validation_error"


class NeedsResearchError(ViralgenError):
    exit_code = ExitCode.NEEDS_RESEARCH
    code = "needs_research"


class DiskSpaceError(ViralgenError):
    exit_code = ExitCode.DISK
    code = "insufficient_disk_space"


class IdempotencyConflictError(ViralgenError):
    exit_code = ExitCode.CONFLICT
    code = "job_key_conflict"


class WorkerLockedError(ViralgenError):
    exit_code = ExitCode.LOCKED
    code = "worker_locked"
