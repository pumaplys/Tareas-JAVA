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

DECLARADO NO ES VERIFICADO
--------------------------

Una entrada del catalogo puede estar DECLARADA con valores que todavia son un
SUPUESTO. `gpt-image-2.6-sunburst` es ese caso: este entorno no alcanza la
documentacion del proveedor (ver `model_checks.UNREACHABLE_REASON`), asi que
sus tamanos, calidades y limite de referencias se declaran por continuidad con
`gpt-image-1` y quedan marcados en `assumed_params`.

Un modelo asi se puede planificar y simular, pero NO despachar en real:
`assert_verified_for_real` lo bloquea citando las entradas pendientes del
registro. No hay interruptor para eludirlo; se levanta aportando los extractos
al registro, que es donde consta quien los leyo y cuando.
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
    #: Valores de `input_fidelity` que el proyecto admite enviar en una
    #: edicion. Vacio = el proyecto NO envia el parametro para este modelo.
    input_fidelities: tuple[str, ...] = ()
    #: False cuando los valores de arriba son un supuesto sin confirmar. Un
    #: modelo sin verificar se planifica y se simula, pero no se despacha.
    verified: bool = True
    #: Nombres de los campos cuyos valores son un SUPUESTO, para que el
    #: informe no los presente como documentados.
    assumed_params: tuple[str, ...] = ()
    #: Entradas de `model_checks` que hay que cerrar para verificarlo.
    pending_check_ids: tuple[str, ...] = ()

    @property
    def assumption_only(self) -> bool:
        return bool(self.assumed_params)

    def describe(self) -> dict:
        """Vista para informes. Dice que esta verificado y que es supuesto."""
        return {
            "model_id": self.model_id,
            "sizes": list(self.sizes),
            "qualities": list(self.qualities),
            "output_formats": list(self.output_formats),
            "supports_edit": self.supports_edit,
            "max_reference_images": self.max_reference_images,
            "input_fidelities": list(self.input_fidelities),
            "verified": self.verified,
            "assumed_params": list(self.assumed_params),
            "pending_check_ids": list(self.pending_check_ids),
        }

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
    # DECLARADO, NO VERIFICADO. Los tamanos, calidades y el limite de
    # referencias son un SUPUESTO por continuidad con gpt-image-1: este
    # entorno no alcanzo la ficha del modelo. El envio real esta bloqueado
    # hasta cerrar las entradas de `model_checks`.
    "gpt-image-2.6-sunburst": ImageModelCapabilities(
        model_id="gpt-image-2.6-sunburst",
        sizes=("1024x1024", "1024x1536", "1536x1024"),
        qualities=("low", "medium", "high", "auto"),
        output_formats=("png", "jpeg", "webp"),
        supports_edit=True,
        max_reference_images=4,
        input_fidelities=("low", "high"),
        verified=False,
        assumed_params=(
            "sizes",
            "qualities",
            "output_formats",
            "max_reference_images",
            "input_fidelities",
        ),
        pending_check_ids=(
            "sunburst_endpoint",
            "sunburst_sizes",
            "sunburst_qualities",
            "sunburst_edit_references",
            "sunburst_input_fidelity",
            "sunburst_response_shape",
            "sunburst_token_consumption",
            "sunburst_account_availability",
        ),
        notes=(
            "Soporte explicito preparado: generacion sin referencias por "
            "images.generate y edicion con referencias de personaje por "
            "images.edit, igual que gpt-image-1, mas `input_fidelity` opcional. "
            "Los valores admitidos son un SUPUESTO sin confirmar y el envio real "
            "esta bloqueado: ver model_checks.blocking_for('gpt-image-2.6-sunburst')."
        ),
    ),
    "mock-image-1": ImageModelCapabilities(
        model_id="mock-image-1",
        sizes=("1024x1536", "720x1280"),
        qualities=("medium",),
        output_formats=("jpeg", "png"),
        supports_edit=True,
        max_reference_images=4,
        input_fidelities=("low", "high"),
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
    input_fidelity: str | None = None,
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
    if input_fidelity is not None:
        if not capacidades.input_fidelities:
            problemas.append(
                f"el modelo no declara `input_fidelity`; quita "
                f"VIRALGEN_MEDIA_INPUT_FIDELITY o declaralo en el catalogo"
            )
        elif input_fidelity not in capacidades.input_fidelities:
            problemas.append(
                f"input_fidelity {input_fidelity!r} no declarada "
                f"({', '.join(capacidades.input_fidelities)})"
            )
        elif not edit:
            # El SDK solo lo acepta en `images.edit`. Enviarlo en una
            # generacion sin referencias seria un parametro inexistente.
            problemas.append(
                "`input_fidelity` solo existe en la edicion con referencias; "
                "esta operacion no lleva ninguna"
            )
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


def assert_verified_for_real(capacidades: ImageModelCapabilities) -> None:
    """Impide el despacho REAL de un modelo cuyos valores son supuestos.

    No es un limite del proveedor: es la consecuencia de no haber podido leer
    su documentacion. Se levanta aportando los extractos al registro de
    `model_checks`, no con un interruptor.
    """
    if capacidades.verified:
        return
    from .model_checks import blocking_for

    bloqueantes = blocking_for(capacidades.model_id)
    detalle = "; ".join(f"{c.check_id}: {c.what}" for c in bloqueantes)
    raise ConfigError(
        f"El modelo de imagen {capacidades.model_id!r} esta DECLARADO pero no "
        f"VERIFICADO: sus parametros admitidos ({', '.join(capacidades.assumed_params)}) "
        "son un supuesto, porque este entorno no alcanzo la documentacion oficial. "
        "Se puede planificar y simular, no despachar en real. Pendiente: " + detalle,
        details={
            "model": capacidades.model_id,
            "assumed_params": list(capacidades.assumed_params),
            "blocking_checks": [c.check_id for c in bloqueantes],
        },
    )
