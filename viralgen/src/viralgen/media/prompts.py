"""Construccion del prompt efectivo de cada escena.

El modulo 3 CONSUME los prompts ya preparados por el modulo 1: no hay una
llamada a un LLM para reescribirlos ni regeneraciones automaticas por criterio
estetico.

El prompt efectivo se compone de:

1. `visual.image_prompt` de la escena, que el modulo 1 ya dejo autocontenido
   (accion, encuadre, iluminacion y la ficha de continuidad de los personajes
   presentes);
2. el estilo y la paleta de la biblia visual;
3. las restricciones, expresadas DENTRO del prompt. La Images API no tiene un
   campo `negative_prompt`, asi que no se inventa uno;
4. una nota de composicion que reserva margen para los rotulos del modulo 4.

No se anaden protagonistas ausentes ni se cambia texto, hechos, desenlace ni
la escena seleccionada por el modulo 1.
"""

from __future__ import annotations

#: Version del compositor de prompts. Viaja en el manifiesto y en la identidad
#: de cada asset: cambiarla invalida la cache de imagenes.
MEDIA_PROMPT_VERSION = "v1"

#: Guia por canal. Es direccion visual, no contenido nuevo.
CHANNEL_GUIDANCE = {
    "infantil": (
        "Clear readable facial expressions, one main comprehensible action, "
        "legible framing, spatial continuity with the previous scene."
    ),
    "curiosidades": (
        "The hero object or contrast must be unmistakable and centered; the "
        "image explains the point being made, it does not decorate it."
    ),
}

#: Restricciones comunes. Una ilustracion generada NO es prueba documental de
#: ningun hecho, asi que jamas se piden cifras, carteles ni rotulos.
COMMON_CONSTRAINTS = (
    "No text, no letters, no numbers, no captions, no subtitles, no signs, "
    "no charts, no logos, no watermarks anywhere in the image."
)

COMPOSITION_NOTE = (
    "Vertical composition. Keep the top and bottom margins visually calm: "
    "on-screen captions are added later in editing, not drawn here."
)


def build_effective_prompt(*, scene, bible, channel: str) -> str:
    """Prompt efectivo y reproducible de una escena."""
    partes = [scene.visual.image_prompt.strip().rstrip(".")]
    guia = CHANNEL_GUIDANCE.get(channel)
    if guia:
        partes.append(guia)
    partes.append(f"Series style: {bible.style_prompt.strip().rstrip('.')}")
    if bible.color_palette:
        partes.append("Palette: " + ", ".join(bible.color_palette))
    if bible.negative_prompt:
        partes.append(f"Avoid: {bible.negative_prompt.strip().rstrip('.')}")
    partes.append(COMMON_CONSTRAINTS)
    partes.append(COMPOSITION_NOTE)
    return ". ".join(parte for parte in partes if parte) + "."


def build_motion_prompt(*, scene) -> str:
    """Texto de movimiento para imagen-a-video.

    Sale del `motion_prompt` del guion y de sus notas de continuidad: describe
    accion y movimiento, no vuelve a describir la imagen.
    """
    partes = [scene.visual.motion_prompt.strip().rstrip(".")]
    continuidad = (scene.visual.continuity_notes or "").strip().rstrip(".")
    if continuidad:
        partes.append(continuidad)
    partes.append("Keep the subject identity, wardrobe and palette unchanged")
    return ". ".join(parte for parte in partes if parte) + "."
