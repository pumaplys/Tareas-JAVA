"""Modulo 4: montaje local, subtitulos y exportacion de video.

Toma un guion, una voz y unos medios YA ADMITIDOS y produce `video.mp4` mas
un manifiesto lateral `render.json`. No genera contenido nuevo: corta, encuadra,
subtitula, mezcla y codifica lo que ya existe.

Lo que este modulo NO hace: publicar, subir, analizar rendimiento, llamar a
ningun servicio remoto ni modificar un solo byte de sus entradas.
"""

from __future__ import annotations

#: Version del contrato de `render.json`.
RENDER_SCHEMA_VERSION = "1.0"

#: Version del pipeline de montaje. Entra en el fingerprint: cambiarla invalida
#: la reutilizacion de etapas anteriores, porque el resultado podria diferir.
RENDER_PIPELINE_VERSION = "v1"

__all__ = ["RENDER_SCHEMA_VERSION", "RENDER_PIPELINE_VERSION"]
