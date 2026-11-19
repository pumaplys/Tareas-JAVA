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

DECLARADO NO ES VERIFICADO, Y DECLARADO NO ES VIGENTE
-----------------------------------------------------

Una entrada del catalogo puede estar en tres situaciones, y `describe()` las
distingue:

* **Vigente y verificada.** `gpt-image-2.6-sunburst`: sus parametros los
  aporto el revisor el 2026-11-18 con las fuentes oficiales. Se despacha.
* **Retirada.** `gpt-image-1` se apago el 2026-10-23. Se sigue RESOLVIENDO
  -los manifiestos, ejemplos y claves de cache ya generados lo citan, y
  borrarlo convertiria ese historial en "modelo desconocido"- pero
  `assert_verified_for_real` rechaza cualquier solicitud nueva.
* **Declarada con supuestos.** Ninguna hoy. Si volviera a haberla, se
  planifica y se simula pero no se despacha, y el bloqueo se levanta
  aportando extractos al registro, no con un interruptor.

Lo que este modulo NO decide es si el modelo esta disponible en la cuenta:
eso no se demuestra leyendo nada, solo con una llamada real, y por eso no
bloquea aqui (seria un bucle). Vive en `model_checks` como pendiente
declarada.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction

from ..errors import ConfigError


@dataclass(frozen=True)
class SizeRules:
    """Limites de geometria documentados por el proveedor, como REGLA.

    La ficha de 2.6 no enumera tamanos: publica condiciones. Implementarlas
    como regla es mas fiel que inventar una lista, y permite comprobar un
    tamano concreto -720x1280- contra lo que dice la fuente en vez de contra
    una suposicion.
    """

    side_multiple: int
    min_ratio: Fraction
    max_ratio: Fraction
    max_side: int
    min_pixels: int
    max_pixels: int
    source_note: str

    def problems(self, size: str) -> list[str]:
        """Que incumple ese tamano. Lista vacia = lo admite la regla."""
        ancho, alto = parse_size(size)
        fallos: list[str] = []
        if ancho % self.side_multiple or alto % self.side_multiple:
            fallos.append(
                f"{size}: los dos lados deben ser multiplos de {self.side_multiple}"
            )
        if max(ancho, alto) > self.max_side:
            fallos.append(f"{size}: el lado mayor supera {self.max_side} px")
        pixeles = ancho * alto
        if pixeles < self.min_pixels:
            fallos.append(f"{size}: {pixeles} px por debajo del minimo {self.min_pixels}")
        if pixeles > self.max_pixels:
            fallos.append(f"{size}: {pixeles} px por encima del maximo {self.max_pixels}")
        proporcion = Fraction(ancho, alto)
        if not (self.min_ratio <= proporcion <= self.max_ratio):
            fallos.append(
                f"{size}: proporcion {proporcion} fuera del rango "
                f"{self.min_ratio}..{self.max_ratio}"
            )
        return fallos

    def describe(self) -> dict:
        return {
            "side_multiple": self.side_multiple,
            "ratio_range": [str(self.min_ratio), str(self.max_ratio)],
            "max_side": self.max_side,
            "pixel_range": [self.min_pixels, self.max_pixels],
            "source": self.source_note,
        }


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
    #: Limites de geometria documentados. Cuando existen, un tamano tiene que
    #: estar en `sizes` (seleccion del proyecto) Y cumplir la regla.
    size_rules: SizeRules | None = None
    #: La documentacion del modelo dice OMITIR `input_fidelity`. Entonces
    #: configurarlo es un error, no una opcion.
    forbids_input_fidelity: bool = False
    #: Maximo de referencias por peticion segun la documentacion. El proyecto
    #: puede mantener un limite local mas estricto en `max_reference_images`.
    documented_max_reference_images: int | None = None
    #: True si el proveedor cobra por TOKENS y no por imagen. Entonces una
    #: tabla de tarifas por (tamano, calidad) no aplica, y el coste a priori es
    #: desconocido aunque las tarifas consten: faltan los recuentos.
    bills_by_tokens: bool = False
    #: Fecha de retirada anunciada. Un modelo retirado se sigue RESOLVIENDO
    #: -los manifiestos, ejemplos y cachés historicos lo citan- pero no acepta
    #: solicitudes reales nuevas.
    retired_on: str | None = None

    @property
    def retired(self) -> bool:
        return self.retired_on is not None

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
            "size_rules": self.size_rules.describe() if self.size_rules else None,
            "forbids_input_fidelity": self.forbids_input_fidelity,
            "documented_max_reference_images": self.documented_max_reference_images,
            "retired_on": self.retired_on,
            "bills_by_tokens": self.bills_by_tokens,
            "usable_for_new_real_requests": not self.retired and self.verified,
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

#: Reglas de geometria de la familia GPT Image segun la guia de generacion de
#: imagenes, aportadas por el revisor el 2026-11-18 (ver `model_checks`).
GPT_IMAGE_SIZE_RULES = SizeRules(
    side_multiple=16,
    min_ratio=Fraction(1, 3),
    max_ratio=Fraction(3, 1),
    max_side=3840,
    min_pixels=655_360,
    max_pixels=8_294_400,
    source_note=(
        "Guia de generacion de imagenes, seccion Size and quality options; "
        "evidencia aportada por el revisor el 2026-11-18."
    ),
)

#: Modelos de imagen declarados. `gpt-image-2.6-sunburst` es el modelo real
#: vigente; `gpt-image-1` sigue resolviendose solo para leer lo ya generado.
IMAGE_MODELS: dict[str, ImageModelCapabilities] = {
    # RETIRADO el 2026-10-23 segun la ficha del modelo (evidencia del revisor,
    # 2026-11-18). Se conserva en el catalogo A PROPOSITO: los manifiestos, los
    # ejemplos y las claves de cache ya generadas lo citan, y borrarlo
    # convertiria ese historial en "modelo desconocido". Lo que no acepta son
    # solicitudes reales NUEVAS.
    "gpt-image-1": ImageModelCapabilities(
        model_id="gpt-image-1",
        # 1024x1536 es 2:3. NO es 9:16 y no se presenta como tal.
        sizes=("1024x1024", "1024x1536", "1536x1024"),
        qualities=("low", "medium", "high", "auto"),
        output_formats=("png", "jpeg", "webp"),
        supports_edit=True,
        max_reference_images=4,
        retired_on="2026-10-23",
        pending_check_ids=("gpt_image_1_retired",),
        notes=(
            "RETIRADO el 2026-10-23: no acepta solicitudes reales nuevas. Se "
            "mantiene declarado para poder leer manifiestos, ejemplos y assets "
            "en cache generados antes de esa fecha, que lo citan por su "
            "identificador. El tamano vertical 1024x1536 es 2:3."
        ),
    ),
    # Parametros documentados por la evidencia del revisor (2026-11-18). Lo
    # que sigue sin demostrar NO es el protocolo: es la disponibilidad en
    # nuestra cuenta, y eso solo lo prueba una llamada real.
    "gpt-image-2.6-sunburst": ImageModelCapabilities(
        model_id="gpt-image-2.6-sunburst",
        # 720x1280 es 9:16 NATIVO y cumple la regla documentada: 45x16 y
        # 80x16, proporcion dentro de 1:3..3:1, 921 600 px dentro del rango.
        # Con el, la entrega vertical ya no necesita adaptacion del modulo 4.
        sizes=("720x1280", "1024x1024", "1024x1536", "1536x1024"),
        # La guia de 2.6 documenta `medium`. El resto de valores no consta, y
        # el proyecto solo envia el que declara aqui.
        qualities=("medium",),
        output_formats=("png", "jpeg", "webp"),
        supports_edit=True,
        # Limite LOCAL, mas estricto que el publicado: ninguna escena de la
        # biblia usa mas de tres personajes.
        max_reference_images=4,
        documented_max_reference_images=16,
        size_rules=GPT_IMAGE_SIZE_RULES,
        bills_by_tokens=True,
        # "Omit `input_fidelity` on image edits": configurarlo es un error.
        input_fidelities=(),
        forbids_input_fidelity=True,
        verified=True,
        pending_check_ids=("sunburst_account_availability",),
        notes=(
            "Generacion por images.generate (JSON) y edicion con referencias de "
            "personaje por images.edit (multipart, un image[] por referencia), "
            "n=1 y modelo explicito. `input_fidelity` se OMITE: la guia de 2.6 "
            "lo indica expresamente. La respuesta se lee de data[0].b64_json; "
            "`response_format` no se envia. Factura por tokens, no por imagen. "
            "Parametros aportados por el revisor el 2026-11-18; la "
            "disponibilidad en la cuenta sigue sin demostrar."
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
    if capacidades.size_rules is not None:
        # La regla documentada manda sobre la seleccion: un tamano declarado
        # por error que incumple los limites del proveedor se rechaza aqui, no
        # en un 400 pagado.
        problemas.extend(capacidades.size_rules.problems(size))
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
        if capacidades.forbids_input_fidelity:
            problemas.append(
                f"la documentacion de {capacidades.model_id!r} indica OMITIR "
                "`input_fidelity` en las ediciones; quita VIRALGEN_MEDIA_INPUT_FIDELITY "
                "(dejarlo vacio no envia el parametro; no se envia `null`)"
            )
        elif not capacidades.input_fidelities:
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
    """Impide el despacho REAL de un modelo que no puede atenderlo.

    Dos motivos distintos, con mensajes distintos:

    * **Retirado.** El proveedor lo apago. Ninguna peticion nueva va a
      funcionar, asi que no se emite. Seguir resolviendolo en el catalogo es
      otra cosa: hace falta para leer lo ya generado.
    * **Sin verificar.** Sus parametros son un supuesto porque no se pudo
      leer su documentacion. Se puede planificar y simular, no despachar.

    Lo que esta puerta NO comprueba es la disponibilidad en la cuenta: eso no
    se puede demostrar sin hacer la primera llamada real, y bloquear por ello
    seria un bucle. Esa condicion vive en `model_checks` como pendiente
    declarada, y la gobierna el presupuesto acordado del piloto.
    """
    if capacidades.retired:
        raise ConfigError(
            f"El modelo de imagen {capacidades.model_id!r} fue RETIRADO el "
            f"{capacidades.retired_on} y no acepta solicitudes nuevas. Sigue "
            "declarado en el catalogo para poder leer manifiestos, ejemplos y "
            "assets en cache que lo citan, pero no se puede generar con el. "
            "Declara otro modelo en OPENAI_IMAGE_MODEL.",
            details={
                "model": capacidades.model_id,
                "retired_on": capacidades.retired_on,
                "readable_for_history": True,
            },
        )
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
