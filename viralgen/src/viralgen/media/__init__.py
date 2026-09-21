"""Modulo 3: generacion de medios visuales.

Produce, para un guion (modulo 1) y su voz medida (modulo 2), una imagen por
escena, un clip para las escenas cuyo `visual.asset_type` sea `video`, y un
manifiesto lateral `media.json`.

Termina en assets locales. El montaje final, los movimientos de camara sobre
imagenes fijas, los subtitulos, la mezcla y la publicacion son de los modulos
4 y 5.

`script.json` y `voice.json` se conservan intactos: se vinculan por `job_id`,
`voice_run_id` y SHA-256 de sus bytes exactos. Los hashes comprueban
CORRESPONDENCIA, no autenticidad.
"""

#: Version del contrato del manifiesto de medios.
MEDIA_SCHEMA_VERSION = "1.0"

#: Version del procesamiento local (geometria, medicion, cobertura). Entra en
#: el fingerprint de idempotencia: cambiarla invalida los resultados guardados.
MEDIA_PROCESSING_VERSION = "v1"

__all__ = ["MEDIA_SCHEMA_VERSION", "MEDIA_PROCESSING_VERSION"]
