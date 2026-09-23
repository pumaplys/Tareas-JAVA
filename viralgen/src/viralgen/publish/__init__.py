"""Modulo 5: publicacion y programacion.

Toma un paquete de montaje YA ADMITIDO y lo entrega a los destinos que el
operador haya autorizado. No genera contenido, no monta y no edita los
documentos de origen: los lee, los revalida y los transfiere.

Tres modos que no se mezclan:

* ``plan``  - comprobacion local y borrador editorial. Sin red, sin OAuth, sin
  staging y sin envios.
* ``mock``  - publicador simulado completo, con su propio espacio de identidad.
  NUNCA usa un cliente de red real, aunque haya credenciales en el entorno.
* ``real``  - solo fuentes admitidas para produccion y destinos autorizados.

Tres conceptos separados que este modulo nunca confunde: la admision tecnica,
la autorizacion del operador y el estado remoto. Un `ready` del modulo 4 no da
permiso para subir nada, y un `exit 0` de `plan` no significa que se haya
publicado.
"""

from __future__ import annotations

#: Contrato del borrador editorial.
PLAN_SCHEMA_VERSION = "1.0"

#: Contrato del recibo de estado.
RECEIPT_SCHEMA_VERSION = "1.0"

#: Version del publicador. Entra en el fingerprint de la intencion.
PUBLISHER_VERSION = "v1"

__all__ = [
    "PLAN_SCHEMA_VERSION",
    "RECEIPT_SCHEMA_VERSION",
    "PUBLISHER_VERSION",
]
