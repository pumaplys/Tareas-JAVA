"""Catalogo local de modelos y capacidades verificadas.

QUE ES ESTO Y QUE NO ES. Es una declaracion CONSERVADORA del proyecto, no una
copia de la documentacion del proveedor ni una promesa de compatibilidad
universal. Se comprobo contra la superficie de parametros del SDK instalado
(`openai.resources.images.Images.generate/edit`) y contra la documentacion
oficial citada en el README. NO se ha validado contra la API en vivo: este
entorno no tiene credenciales.

Consecuencias practicas:

* Una cadena de modelo desconocida produce error ANTES de cualquier HTTP.
* Una combinacion no soportada (tamano, calidad, formato, edicion) tambien.
* Nunca se sustituye un modelo por otro en silencio.
* Ampliar el catalogo es una decision explicita, no un efecto secundario.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction

from ..errors import ConfigError


@dataclass(frozen=True)
class ImageModelCapabilities:
    """Lo que este proyecto se compromete a enviarle a un modelo de imagen."""

    model_id: str
    sizes: tuple[str, ...]
    qualities: tuple[str, ...]
    output_formats: tuple[str, ...]
    supports_edit: bool
    max_reference_images: int
    notes: str

    def vertical_sizes(self) -> list[str]:
        """Tamanos mas altos que anchos, del mas vertical al menos."""
        verticales = [size for size in self.sizes if _aspect(size) < 1]
        return sorted(verticales, key=_aspect)

    def best_vertical(self, target_aspect: Fraction) -> str:
        """Tamano vertical soportado mas cercano a la proporcion objetivo.

        Si existe uno EXACTO se usa ese. Si no, se elige el mas proximo y la
        diferencia se resuelve con un plan de adaptacion explicito: 1024x1536
        es 2:3, NO es 9:16, y no se presenta como tal.
        """
        verticales = self.vertical_sizes()
        if not verticales:
            raise ConfigError(
                f"El modelo de imagen {self.model_id!r} no declara ningun tamano vertical."
            )
        objetivo = float(target_aspect)
        exactos = [size for size in verticales if abs(_aspect(size) - objetivo) < 1e-9]
        if exactos:
            return exactos[0]
        return min(verticales, key=lambda size: abs(_aspect(size) - objetivo))


@dataclass(frozen=True)
class VideoModelCapabilities:
    """Duraciones y proporciones admitidas para imagen-a-video."""

    model_id: str
    durations_s: tuple[int, ...]
    ratios: tuple[str, ...]
    default_ratio: str
    notes: str

    def shortest_duration_covering(self, needed_s: float) -> int | None:
        """Duracion admitida mas corta que cubre el intervalo completo.

        Devuelve None si ninguna llega: entonces el plan bloquea con
        `duration_not_supported`. No se parten escenas, ni se repiten
        imagenes, ni se congelan cuadros, ni se cambia la velocidad.
        """
        for duracion in sorted(self.durations_s):
            if duracion + 1e-9 >= needed_s:
                return duracion
        return None


def _aspect(size: str) -> float:
    ancho, _, alto = size.partition("x")
    return int(ancho) / int(alto)


def parse_size(size: str) -> tuple[int, int]:
    ancho, _, alto = size.partition("x")
    return int(ancho), int(alto)


def parse_ratio(ratio: str) -> tuple[int, int]:
    """Convierte '720:1280' en (720, 1280)."""
    ancho, _, alto = ratio.partition(":")
    return int(ancho), int(alto)


# ---------------------------------------------------------------------------
# Catalogo
# ---------------------------------------------------------------------------

#: Modelos de imagen declarados. `gpt-image-1` es el unico modelo real del MVP.
IMAGE_MODELS: dict[str, ImageModelCapabilities] = {
    "gpt-image-1": ImageModelCapabilities(
        model_id="gpt-image-1",
        # 1024x1536 es 2:3. NO es 9:16 y no se presenta como tal.
        sizes=("1024x1024", "1024x1536", "1536x1024"),
        qualities=("low", "medium", "high", "auto"),
        output_formats=("png", "jpeg", "webp"),
        supports_edit=True,
        max_reference_images=4,
        notes=(
            "Parametros comprobados contra el SDK instalado (size, quality, "
            "output_format, n, edit con image[]). El tamano vertical 1024x1536 "
            "es 2:3: la entrega 9:16 la resuelve el modulo 4 con el plan de "
            "presentacion registrado."
        ),
    ),
    "mock-image-1": ImageModelCapabilities(
        model_id="mock-image-1",
        sizes=("1024x1536", "720x1280"),
        qualities=("medium",),
        output_formats=("jpeg", "png"),
        supports_edit=True,
        max_reference_images=4,
        notes="Modelo del proveedor simulado. No llama a ninguna API.",
    ),
}

#: Modelos de video declarados.
VIDEO_MODELS: dict[str, VideoModelCapabilities] = {
    "gen4_turbo": VideoModelCapabilities(
        model_id="gen4_turbo",
        durations_s=(5, 10),
        ratios=("720:1280", "1280:720"),
        default_ratio="720:1280",
        notes=(
            "Subconjunto conservador segun la documentacion citada. No se "
            "presenta como el modelo mas reciente ni como una comparacion de "
            "costes; valida contra la referencia vigente antes de ampliarlo."
        ),
    ),
    "mock-video-1": VideoModelCapabilities(
        model_id="mock-video-1",
        durations_s=(5, 10),
        ratios=("720:1280",),
        default_ratio="720:1280",
        notes="Modelo del proveedor simulado. Produce MP4 con FFmpeg local.",
    ),
}


def image_capabilities(model_id: str) -> ImageModelCapabilities:
    capacidades = IMAGE_MODELS.get(model_id)
    if capacidades is None:
        raise ConfigError(
            f"Modelo de imagen desconocido para este proyecto: {model_id!r}. "
            f"Declarados: {', '.join(sorted(IMAGE_MODELS))}. Ampliar el catalogo es "
            "una decision explicita; no se asume compatibilidad universal.",
            details={"known": sorted(IMAGE_MODELS)},
        )
    return capacidades


def video_capabilities(model_id: str) -> VideoModelCapabilities:
    capacidades = VIDEO_MODELS.get(model_id)
    if capacidades is None:
        raise ConfigError(
            f"Modelo de video desconocido para este proyecto: {model_id!r}. "
            f"Declarados: {', '.join(sorted(VIDEO_MODELS))}.",
            details={"known": sorted(VIDEO_MODELS)},
        )
    return capacidades


def validate_image_request(
    capacidades: ImageModelCapabilities,
    *,
    size: str,
    quality: str,
    output_format: str,
    edit: bool,
    reference_count: int,
) -> None:
    """Valida la combinacion ANTES de emitir nada."""
    problemas: list[str] = []
    if size not in capacidades.sizes:
        problemas.append(f"tamano {size!r} no declarado ({', '.join(capacidades.sizes)})")
    if quality not in capacidades.qualities:
        problemas.append(
            f"calidad {quality!r} no declarada ({', '.join(capacidades.qualities)})"
        )
    if output_format not in capacidades.output_formats:
        problemas.append(
            f"formato {output_format!r} no declarado ({', '.join(capacidades.output_formats)})"
        )
    if edit and not capacidades.supports_edit:
        problemas.append("el modelo no declara soporte de edicion con referencias")
    if reference_count > capacidades.max_reference_images:
        problemas.append(
            f"{reference_count} referencias superan el maximo declarado "
            f"({capacidades.max_reference_images}); omitir personajes no es una opcion"
        )
    if problemas:
        raise ConfigError(
            f"Combinacion no soportada por {capacidades.model_id!r}: " + "; ".join(problemas),
            details={"model": capacidades.model_id, "problems": problemas},
        )
