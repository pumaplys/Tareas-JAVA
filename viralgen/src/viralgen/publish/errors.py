"""Errores del modulo 5, sobre los codigos de salida que ya existen.

No se inventan familias nuevas: un problema de credenciales sigue siendo
configuracion o proveedor, un choque de identidad sigue siendo CONFLICT y una
espera remota sigue siendo WAITING_REMOTE. Lo que si aporta este modulo son
codigos legibles que distinguen *por que* no se publica.
"""

from __future__ import annotations

from ..errors import (
    DocumentValidationError,
    ExitCode,
    ProviderPermanentError,
    ViralgenError,
)


class PublishAdmissionError(DocumentValidationError):
    """El paquete no es admisible para lo que se pide (simular o enviar)."""

    code = "publish_not_admissible"


class AuthorizationRequiredError(ViralgenError):
    """Falta la autorizacion del operador, o la que habia ya no cubre esto."""

    exit_code = ExitCode.NEEDS_REVIEW
    code = "authorization_required"


class ModeViolationError(ViralgenError):
    """Se pidio algo que ese modo no permite.

    Ejemplos: enviar de verdad un paquete preview, o intentar que el modo
    simulado toque la red. No se resuelve reintentando.
    """

    exit_code = ExitCode.CONFIG
    code = "mode_violation"


class AuthRequiredError(ProviderPermanentError):
    """La plataforma exige un consentimiento nuevo: renovar no basta."""

    code = "auth_required"


class ReconciliationRequiredError(ViralgenError):
    """La operacion pudo completarse y no hay evidencia para decidirlo.

    Detiene nuevas creaciones para ese destino: repetir un POST de publicacion
    es exactamente lo que produce duplicados.
    """

    exit_code = ExitCode.NEEDS_REVIEW
    code = "needs_reconciliation"


class DuplicateDeliveryError(ViralgenError):
    """Ese mismo MP4 ya fue entregado a esa plataforma y cuenta."""

    exit_code = ExitCode.CONFLICT
    code = "duplicate_delivery"


class VerificationPendingError(ViralgenError):
    """Un parametro de protocolo sin contrastar bloquea el modo real."""

    exit_code = ExitCode.CONFIG
    code = "verification_pending"
