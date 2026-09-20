"""viralgen - Modulo 1: generador de ideas, guiones y prompts de medios.

Este paquete produce un unico artefacto consumible por los modulos 2-5:
un documento JSON validado (``script.json``) que describe un video vertical
9:16 completo a nivel de plan: idea, guion, escenas, prompts de imagen y
movimiento, indicaciones de voz, subtitulos, audio, loop y evidencia.

No genera medios, no ejecuta FFmpeg y no publica nada.
"""

__all__ = ["__version__", "SCHEMA_VERSION", "PROMPT_VERSION"]

__version__ = "0.1.0"

#: Version del contrato JSON exportado (campo ``schema_version``).
SCHEMA_VERSION = "1.0"

#: Version de las plantillas de prompt empaquetadas (carpeta ``prompts/v1``).
PROMPT_VERSION = "v1"
