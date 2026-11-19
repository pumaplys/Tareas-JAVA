"""Presupuesto de un trabajo de medios: solicitudes y coste, por separado.

POR QUE ESTE MODULO EXISTE
--------------------------

Hasta ahora el coste de imagen se estimaba con una tarifa PLANA por unidad
(`price_image_per_unit_usd`). Eso solo seria correcto si el proveedor cobrara
igual un 1024x1024 en calidad `low` y un 1536x1024 en `high`, y para la familia
gpt-image no lo es. Una tarifa plana no es una estimacion conservadora: es una
cifra que parece exacta y no lo es.

Aqui se separan tres cosas que antes se confundian:

1. **Limite de solicitudes.** `media_max_generation_attempts` (24 por defecto)
   es un tope de INTENTOS DE CREACION del trabajo, decidido por el proyecto.
   No es un limite en dolares, no lo garantiza nadie y no se debe presentar
   como un maximo de gasto.
2. **Coste esperado.** Lo que costaria el camino nominal: una llamada por
   asset que no esta en cache, con su tamano y su calidad concretos.
3. **Techo de coste.** Lo que costaria si cada asset agotara sus reintentos
   seguros, acotado por el limite de solicitudes. Solo se calcula si TODAS las
   tarifas implicadas se conocen.

REGLA QUE NO SE NEGOCIA
-----------------------

Una tarifa desconocida NO vale cero. Si falta el precio de un concepto, ese
concepto aparece en `unknown_concepts` y los totales se devuelven como `None`.
Un coste desconocido sigue siendo desconocido.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ..errors import ConfigError

#: Conceptos del desglose.
CONCEPT_SCENE_IMAGE = "imagen_de_escena"
CONCEPT_REFERENCE_IMAGE = "imagen_de_referencia"
CONCEPT_REFERENCE_INPUT = "referencia_enviada_en_edicion"
CONCEPT_VIDEO_SECONDS = "segundos_de_video"


@dataclass(frozen=True)
class TokenRates:
    """Tarifas por millon de tokens del modelo de imagen.

    Es el modelo de facturacion de `gpt-image-2.6-sunburst`: cobra por tokens
    y separa texto de entrada, imagen de entrada e imagen de salida. Las
    referencias adjuntas a una edicion se cobran como imagen de ENTRADA.

    Importante: la tarifa de salida NO es el precio de una imagen. Con 30 USD
    por millon de tokens de salida, cargar 30 USD por imagen seria errar por
    varios ordenes de magnitud.
    """

    text_input_per_1m: float | None = None
    image_input_per_1m: float | None = None
    image_output_per_1m: float | None = None

    @property
    def configured(self) -> bool:
        return any(
            r is not None
            for r in (self.text_input_per_1m, self.image_input_per_1m, self.image_output_per_1m)
        )

    @property
    def complete(self) -> bool:
        return all(
            r is not None
            for r in (self.text_input_per_1m, self.image_input_per_1m, self.image_output_per_1m)
        )

    def price(self, tokens: dict[str, int]) -> float | None:
        """Valora un consumo YA DECLARADO por el proveedor.

        Usa los componentes (`input_tokens_text`, `input_tokens_image`,
        `output_tokens`), nunca `total_tokens`: sumar el total a sus partes
        duplicaria la cuenta. Si falta un componente con tarifa, devuelve None:
        un consumo a medias no se presenta como el consumo.
        """
        if not self.complete:
            return None
        piezas = (
            ("input_tokens_text", self.text_input_per_1m),
            ("input_tokens_image", self.image_input_per_1m),
            ("output_tokens", self.image_output_per_1m),
        )
        total = 0.0
        for clave, tarifa in piezas:
            valor = tokens.get(clave)
            if valor is None:
                return None
            assert tarifa is not None
            total += valor * tarifa / 1_000_000
        return round(total, 6)

    def describe(self) -> dict[str, Any]:
        return {
            "text_input_per_1m_usd": self.text_input_per_1m,
            "image_input_per_1m_usd": self.image_input_per_1m,
            "image_output_per_1m_usd": self.image_output_per_1m,
            "formula": (
                "(5 x tokens de texto de entrada + 8 x tokens de imagen de "
                "entrada + 30 x tokens de imagen de salida) / 1 000 000, con las "
                "tarifas configuradas. Las referencias adjuntas cuentan como "
                "imagen de entrada. La tarifa de salida NO es el precio de una "
                "imagen."
            ),
        }


def load_token_rates(settings: Any) -> TokenRates:
    return TokenRates(
        text_input_per_1m=getattr(settings, "price_image_text_input_per_1m_usd", None),
        image_input_per_1m=getattr(settings, "price_image_input_per_1m_usd", None),
        image_output_per_1m=getattr(settings, "price_image_output_per_1m_usd", None),
    )


@dataclass(frozen=True)
class ImageTariffs:
    """Tarifas de imagen declaradas por el operador. Nunca inventadas.

    `per_size_quality` manda sobre `flat_per_unit`: si la tabla cubre la
    combinacion, se usa su precio; si no, se cae a la tarifa plana, y si
    tampoco hay, el precio es desconocido.
    """

    per_size_quality: dict[tuple[str, str], float] = field(default_factory=dict)
    flat_per_unit: float | None = None
    reference_input: float | None = None
    source: str = "sin tarifas configuradas"

    def unit_price(self, size: str, quality: str) -> float | None:
        precio = self.per_size_quality.get((size, quality))
        if precio is not None:
            return precio
        return self.flat_per_unit

    def price_source(self, size: str, quality: str) -> str:
        if (size, quality) in self.per_size_quality:
            return "tabla"
        if self.flat_per_unit is not None:
            return "tarifa_plana"
        return "desconocida"

    @property
    def configured(self) -> bool:
        return bool(self.per_size_quality) or self.flat_per_unit is not None

    def describe(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "entries": {
                f"{size}/{quality}": precio
                for (size, quality), precio in sorted(self.per_size_quality.items())
            },
            "flat_per_unit_usd": self.flat_per_unit,
            "reference_input_usd": self.reference_input,
        }


def load_image_tariffs(settings: Any) -> ImageTariffs:
    """Lee la tabla de tarifas del archivo configurado, si lo hay.

    Formato: ``{"1024x1536": {"medium": 0.042, "high": 0.167}}``. Los valores
    son USD por imagen. Un archivo ilegible o con un numero negativo es un
    error de configuracion, no un cero silencioso.
    """
    ruta: Path | None = getattr(settings, "media_image_tariffs_path", None)
    plana = getattr(settings, "price_image_per_unit_usd", None)
    referencia = getattr(settings, "price_image_reference_input_usd", None)

    if ruta is None:
        return ImageTariffs(
            flat_per_unit=plana,
            reference_input=referencia,
            source=(
                "VIRALGEN_PRICE_IMAGE_PER_UNIT_USD (tarifa plana)"
                if plana is not None
                else "sin tarifas configuradas"
            ),
        )

    ruta = Path(ruta)
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(
            f"No se pudo leer la tabla de tarifas de imagen {ruta}: {exc}",
            details={"path": str(ruta)},
        ) from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"La tabla de tarifas de imagen {ruta} no es JSON valido: {exc}",
            details={"path": str(ruta)},
        ) from exc

    if not isinstance(datos, dict):
        raise ConfigError(
            f"La tabla de tarifas {ruta} debe ser un objeto "
            '{"<tamano>": {"<calidad>": <usd>}}.',
            details={"path": str(ruta)},
        )

    tabla: dict[tuple[str, str], float] = {}
    for size, por_calidad in datos.items():
        if not isinstance(por_calidad, dict):
            raise ConfigError(
                f"La entrada {size!r} de {ruta} debe ser un objeto "
                '{"<calidad>": <usd>}.',
                details={"path": str(ruta), "size": size},
            )
        for quality, precio in por_calidad.items():
            if not isinstance(precio, (int, float)) or isinstance(precio, bool):
                raise ConfigError(
                    f"La tarifa de {size}/{quality} en {ruta} no es un numero.",
                    details={"path": str(ruta), "size": size, "quality": quality},
                )
            if precio < 0:
                raise ConfigError(
                    f"La tarifa de {size}/{quality} en {ruta} es negativa.",
                    details={"path": str(ruta), "size": size, "quality": quality},
                )
            tabla[(str(size), str(quality))] = float(precio)

    return ImageTariffs(
        per_size_quality=tabla,
        flat_per_unit=plana,
        reference_input=referencia,
        source=str(ruta),
    )


@dataclass(frozen=True)
class CostLine:
    """Una linea del desglose, con sus dimensiones explicitas."""

    concept: str
    units: int
    unit_price_usd: float | None
    price_source: str
    size: str | None = None
    quality: str | None = None
    #: Unidades adicionales si cada unidad agotara sus reintentos seguros.
    retry_units: int = 0
    note: str = ""

    @property
    def known(self) -> bool:
        return self.unit_price_usd is not None

    @property
    def expected_usd(self) -> float | None:
        if self.unit_price_usd is None:
            return None
        return round(self.units * self.unit_price_usd, 6)

    @property
    def ceiling_usd(self) -> float | None:
        if self.unit_price_usd is None:
            return None
        return round((self.units + self.retry_units) * self.unit_price_usd, 6)

    def describe(self) -> dict[str, Any]:
        return {
            "concept": self.concept,
            "units": self.units,
            "retry_units": self.retry_units,
            "size": self.size,
            "quality": self.quality,
            "unit_price_usd": self.unit_price_usd,
            "price_source": self.price_source,
            "expected_usd": self.expected_usd,
            "ceiling_usd": self.ceiling_usd,
            "note": self.note,
        }


@dataclass(frozen=True)
class BudgetEstimate:
    """Resultado: desglose, totales y limites, sin mezclarlos."""

    lines: tuple[CostLine, ...]
    request_limits: dict[str, int]
    planned_requests: dict[str, int]
    tariffs: ImageTariffs
    token_rates: TokenRates
    model: str
    model_verified: bool
    #: True si el modelo cobra por tokens. Entonces el coste a priori es
    #: desconocido por falta de RECUENTOS, no por falta de tarifas.
    bills_by_tokens: bool = False

    @property
    def retry_ceiling_exceeds_limit(self) -> bool:
        """Si el peor caso NO cabe en el tope de intentos del trabajo.

        Pasa con facilidad: 7 escenas + 3 referencias son 10 creaciones, y con
        2 reintentos seguros cada una son 30 solicitudes contra un tope de 24.
        Entonces el trabajo se detendria por presupuesto agotado ANTES de
        llegar al techo, asi que la cifra en dolares del techo es una cota que
        el propio limite impide alcanzar. Decirlo evita leer las dos cifras
        como si fueran compatibles.
        """
        limite = self.request_limits.get("max_generation_attempts", 0)
        return bool(limite) and self.planned_requests.get(
            "creation_requests_if_all_retry", 0
        ) > limite

    @property
    def unknown_concepts(self) -> list[str]:
        return sorted({linea.concept for linea in self.lines if not linea.known and linea.units})

    @property
    def unknown_reason(self) -> str | None:
        """Por que no hay total, distinguiendo las dos causas posibles."""
        if not self.unknown_concepts:
            return None
        if self.bills_by_tokens:
            return (
                "El modelo factura por TOKENS. Las tarifas pueden constar, pero "
                "los tokens que consumira una imagen todavia no pedida no se "
                "conocen: el total a priori es desconocido. Se valora a "
                "posteriori con el consumo que declare cada respuesta."
            )
        return (
            "Falta la tarifa de al menos un concepto que si se va a generar. "
            "Sumar solo lo que tiene precio daria una cifra mas baja que la real."
        )

    @property
    def expected_usd(self) -> float | None:
        """Coste del camino nominal, o None si falta alguna tarifa."""
        if self.unknown_concepts:
            return None
        return round(sum(linea.expected_usd or 0.0 for linea in self.lines), 6)

    @property
    def ceiling_usd(self) -> float | None:
        """Techo con reintentos, o None si falta alguna tarifa."""
        if self.unknown_concepts:
            return None
        return round(sum(linea.ceiling_usd or 0.0 for linea in self.lines), 6)

    def describe(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "model_verified": self.model_verified,
            "lines": [linea.describe() for linea in self.lines],
            "billing": {
                "model": "tokens" if self.bills_by_tokens else "per_image",
                "token_rates": self.token_rates.describe(),
                "note": (
                    "Con facturacion por tokens, una tabla de tarifas por "
                    "(tamano, calidad) NO aplica: las unidades de abajo siguen "
                    "siendo utiles para contar solicitudes, pero su precio "
                    "unitario no existe como tal."
                    if self.bills_by_tokens
                    else "El proveedor cobra por imagen: la tabla por (tamano, calidad) aplica."
                ),
            },
            "cost_estimate_usd": {
                "expected": self.expected_usd,
                "ceiling_with_retries": self.ceiling_usd,
                "unknown_concepts": self.unknown_concepts,
                "unknown_reason": self.unknown_reason,
                "meaning": (
                    "`expected` es el camino nominal: una llamada por asset que "
                    "no esta en cache. `ceiling_with_retries` supone que cada "
                    "asset agota sus reintentos seguros. Ambos son None si falta "
                    "alguna tarifa: un coste desconocido sigue siendo desconocido."
                ),
            },
            "request_limits": dict(self.request_limits),
            "planned_requests": dict(self.planned_requests),
            "retry_ceiling_exceeds_limit": self.retry_ceiling_exceeds_limit,
            "retry_ceiling_note": (
                (
                    "El peor caso ("
                    f"{self.planned_requests.get('creation_requests_if_all_retry', 0)}"
                    " solicitudes) NO cabe en max_generation_attempts="
                    f"{self.request_limits.get('max_generation_attempts', 0)}: el "
                    "trabajo se detendria por presupuesto agotado antes de llegar "
                    "a el, con escenas sin imagen. `ceiling_with_retries` es una "
                    "cota que el propio limite impide alcanzar; subir el limite o "
                    "bajar los reintentos es una decision, no un detalle."
                )
                if self.retry_ceiling_exceeds_limit
                else (
                    "El peor caso cabe en el tope de intentos del trabajo."
                )
            ),
            "limits_are_not_money": (
                "Los limites son topes de SOLICITUDES decididos por el proyecto. "
                f"max_generation_attempts={self.request_limits.get('max_generation_attempts')} "
                "no es un maximo de gasto en dolares y no se debe presentar como tal: "
                "el gasto depende de la tarifa de cada tamano y calidad, que el "
                "proveedor fija."
            ),
            "tariffs": self.tariffs.describe(),
        }


def estimate_from_plan(
    plan: Any,
    settings: Any,
    *,
    tariffs: ImageTariffs | None = None,
    reference_size: str | None = None,
) -> BudgetEstimate:
    """Presupuesto de un `MediaPlan` ya calculado.

    Cuenta, con dimensiones y calidad explicitas:

    * las imagenes de escena que NO estan en cache, agrupadas por
      (tamano, calidad), porque el precio depende de las dos;
    * las referencias de personaje que hay que generar;
    * las referencias que viajan COMO ENTRADA en cada edicion, cuyo coste el
      proveedor puede cobrar aparte y que no se supone gratis;
    * los segundos de video, si el plan los pide.

    Los aciertos de cache no se cobran: ya estan en disco.
    """
    if tariffs is None:
        tariffs = load_image_tariffs(settings)

    reintentos = int(getattr(settings, "media_max_safe_retries", 0))

    # --- Imagenes de escena, agrupadas por (tamano, calidad) ---------------
    grupos: dict[tuple[str, str], int] = {}
    entradas_de_referencia = 0
    for escena in getattr(plan, "scenes", ()) or ():
        if getattr(escena, "image_cache_hit", False):
            continue
        clave = (escena.image_size, escena.image_quality)
        grupos[clave] = grupos.get(clave, 0) + 1
        entradas_de_referencia += len(getattr(escena, "character_ids", ()) or ())

    lineas: list[CostLine] = []
    for (size, quality), unidades in sorted(grupos.items()):
        lineas.append(
            CostLine(
                concept=CONCEPT_SCENE_IMAGE,
                units=unidades,
                size=size,
                quality=quality,
                unit_price_usd=tariffs.unit_price(size, quality),
                price_source=tariffs.price_source(size, quality),
                retry_units=unidades * reintentos,
                note="No incluye los aciertos de cache: esos no se vuelven a pagar.",
            )
        )

    # --- Referencias de personaje que hay que generar ----------------------
    por_generar = sum(
        1 for estado in (getattr(plan, "reference_plan", {}) or {}).values()
        if estado == "to_generate"
    )
    if por_generar:
        size_ref = reference_size or _reference_size(plan, settings)
        quality_ref = getattr(settings, "media_image_quality", "medium")
        lineas.append(
            CostLine(
                concept=CONCEPT_REFERENCE_IMAGE,
                units=por_generar,
                size=size_ref,
                quality=quality_ref,
                unit_price_usd=tariffs.unit_price(size_ref, quality_ref),
                price_source=tariffs.price_source(size_ref, quality_ref),
                retry_units=por_generar * reintentos,
                note=(
                    "Una vez generadas se reutilizan: en un trabajo posterior "
                    "esta linea vale 0."
                ),
            )
        )

    # --- Referencias enviadas como ENTRADA en cada edicion -----------------
    if entradas_de_referencia:
        lineas.append(
            CostLine(
                concept=CONCEPT_REFERENCE_INPUT,
                units=entradas_de_referencia,
                size=None,
                quality=None,
                unit_price_usd=tariffs.reference_input,
                price_source=(
                    "VIRALGEN_PRICE_IMAGE_REFERENCE_INPUT_USD"
                    if tariffs.reference_input is not None
                    else "desconocida"
                ),
                retry_units=entradas_de_referencia * reintentos,
                note=(
                    "Cada edicion envia las referencias de sus personajes como "
                    "imagenes de entrada. Si el proveedor las cobra como tokens "
                    "de entrada, esto NO es cero; mientras no conste la tarifa, "
                    "es desconocido (ver model_checks: sunburst_token_consumption)."
                ),
            )
        )

    # --- Video -------------------------------------------------------------
    segundos = 0
    for escena in getattr(plan, "scenes", ()) or ():
        duracion = getattr(escena, "video_duration_s", None)
        if duracion:
            segundos += int(duracion)
    if segundos:
        precio_video = getattr(settings, "price_video_per_second_usd", None)
        lineas.append(
            CostLine(
                concept=CONCEPT_VIDEO_SECONDS,
                units=segundos,
                size=getattr(plan, "video_model", None),
                quality=None,
                unit_price_usd=precio_video,
                price_source=(
                    "VIRALGEN_PRICE_VIDEO_PER_SECOND_USD"
                    if precio_video is not None
                    else "desconocida"
                ),
                retry_units=0,
                note="El video no se reintenta de forma automatica.",
            )
        )

    solicitudes_nominales = sum(linea.units for linea in lineas if linea.concept in {
        CONCEPT_SCENE_IMAGE, CONCEPT_REFERENCE_IMAGE
    })
    solicitudes_techo = sum(
        linea.units + linea.retry_units for linea in lineas if linea.concept in {
            CONCEPT_SCENE_IMAGE, CONCEPT_REFERENCE_IMAGE
        }
    )

    modelo = getattr(plan, "image_model", "") or ""
    verificado = True
    por_tokens = False
    if modelo:
        try:
            from .capabilities import image_capabilities

            capacidades = image_capabilities(modelo)
            verificado = capacidades.verified
            por_tokens = capacidades.bills_by_tokens
        except ConfigError:
            verificado = False

    if por_tokens:
        # Las unidades siguen contando solicitudes, pero su "precio unitario"
        # no existe: el proveedor cobra por tokens. Decir "precio desconocido"
        # sin mas haria pensar que falta configurar una tarifa.
        lineas = [
            replace(linea, unit_price_usd=None, price_source="facturado_por_tokens")
            for linea in lineas
        ]

    return BudgetEstimate(
        lines=tuple(lineas),
        request_limits={
            "max_generation_attempts": int(getattr(settings, "media_max_generation_attempts", 0)),
            "max_safe_retries": reintentos,
            "max_video_scenes": int(getattr(settings, "media_max_video_scenes", 0)),
            "max_video_seconds": int(getattr(settings, "media_max_video_seconds", 0)),
            "max_download_attempts": int(getattr(settings, "media_max_download_attempts", 0)),
        },
        planned_requests={
            "creation_requests_nominal": solicitudes_nominales,
            "creation_requests_if_all_retry": solicitudes_techo,
            "reference_inputs_sent": entradas_de_referencia,
        },
        tariffs=tariffs,
        token_rates=load_token_rates(settings),
        model=modelo,
        model_verified=verificado,
        bills_by_tokens=por_tokens,
    )


def _reference_size(plan: Any, settings: Any) -> str:
    """Tamano con el que se generan las referencias de personaje.

    Se toma del tamano que ya eligio el plan para las escenas, para no
    inventar una dimension distinta de la que de verdad se va a pedir.
    """
    for escena in getattr(plan, "scenes", ()) or ():
        if getattr(escena, "image_size", None):
            return str(escena.image_size)
    return "1024x1536"


__all__ = [
    "CONCEPT_SCENE_IMAGE",
    "CONCEPT_REFERENCE_IMAGE",
    "CONCEPT_REFERENCE_INPUT",
    "CONCEPT_VIDEO_SECONDS",
    "ImageTariffs",
    "load_image_tariffs",
    "CostLine",
    "BudgetEstimate",
    "estimate_from_plan",
]
