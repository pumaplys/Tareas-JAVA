"""Modulo 2: voz, alineacion temporal y preparacion de sonido.

Produce un manifiesto lateral ``voice.json`` junto al ``script.json`` del
modulo 1, sin tocar ni un byte de ese guion.

Decision de contrato: ``script.json`` se queda en el esquema 1.0 y
``video.actual_duration_s`` sigue siendo ``null`` (campo reservado). Los
tiempos MEDIDOS viven en ``voice.json``, que se versiona por su cuenta y se
vincula al guion por ``job_id`` + SHA-256 de sus bytes exactos.

Un hash solo demuestra correspondencia entre archivos: no autentica al autor
del guion. El consumidor debe revalidar guion y medios ademas de comparar
hashes.
"""

#: Version del contrato del manifiesto de voz.
VOICE_SCHEMA_VERSION = "1.0"

#: Version del procesamiento local (medicion, pausas, agrupacion en palabras).
#: Entra en el fingerprint de idempotencia: cambiarla invalida resultados.
VOICE_PROCESSING_VERSION = "v1"

__all__ = ["VOICE_SCHEMA_VERSION", "VOICE_PROCESSING_VERSION"]
