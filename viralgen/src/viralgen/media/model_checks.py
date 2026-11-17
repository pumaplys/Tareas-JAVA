"""Estado de verificacion de los modelos de OpenAI que usa el proyecto.

Mismo patron que `publish/verification.py` y por el mismo motivo: este entorno
NO alcanza la documentacion del proveedor, y el encargo prohibe sustituir una
referencia inaccesible por una afirmacion de haberla leido.

QUE SE SABE Y COMO SE SABE
--------------------------

Hay dos clases de hecho, y no se mezclan:

1. **Lo que acepta el SDK instalado.** Se comprueba aqui, en local, leyendo la
   firma de `openai.resources.images.Images.generate/edit` y de
   `openai.resources.responses.Responses.parse` con `openai==3.16.2`. Esto es
   evidencia propia del desarrollo y se registra como tal.
2. **Lo que acepta el MODELO al otro lado.** Los valores admitidos de `size`,
   `quality`, `input_fidelity`, el numero de imagenes de referencia, la forma
   de la respuesta, el consumo de tokens y las tarifas SOLO los dice la
   documentacion del proveedor. Nada de eso se verifica aqui.

Que el SDK acepte un parametro no significa que el modelo lo admita: el SDK es
generico para toda la familia de modelos de imagen. Confundir las dos cosas es
exactamente el error que este registro existe para evitar.

ESTADO ACTUAL
-------------

El revisor declara haber consultado las cuatro referencias oficiales el
2026-11-17, pero el CONTENIDO de esos extractos no se ha trasladado todavia a
este entorno. Por tanto:

* ninguna entrada sobre el comportamiento del proveedor esta verificada;
* la consulta del revisor consta como `note` fechada, que es lo que es: prueba
  de que alguien abrio la fuente, no del valor de un parametro;
* `gpt-image-2.6-sunburst` y `gpt-6.1-terra` quedan DECLARADOS en el catalogo
  y BLOQUEADOS para envio real hasta que los extractos lleguen.

`docs/verificacion_modelos_openai.md` lista, entrada por entrada, el extracto
exacto que hace falta.
"""

from __future__ import annotations

from ..verification import (
    DEVELOPER,
    REVIEWER,
    CheckStatus,
    PendingCheck,
    SourceEvidence,
    totals_for,
)

#: Modelos a los que se refiere este registro.
IMAGE_MODEL_SUNBURST = "gpt-image-2.6-sunburst"
TEXT_MODEL_TERRA = "gpt-6.1-terra"

#: Por que este entorno no aporta evidencia documental propia.
UNREACHABLE_REASON = (
    "El proxy de egreso de este entorno respondio 000 (conexion no establecida) "
    "a developers.openai.com el 2026-11-17, con PyPI respondiendo 200 como "
    "control en la misma comprobacion. Ninguna evidencia sobre el "
    "comportamiento del proveedor la aporto el desarrollo. Lo unico que este "
    "entorno puede comprobar, y comprueba, es la superficie de parametros del "
    "SDK instalado (openai==3.16.2)."
)

#: Referencias oficiales que el revisor declara haber consultado el 2026-11-17.
SOURCES: dict[str, str] = {
    "O1": "https://developers.openai.com/api/docs/models/gpt-image-2.6-sunburst",
    "O2": "https://developers.openai.com/api/docs/guides/image-generation",
    "O3": "https://developers.openai.com/api/docs/models/gpt-6.1-terra",
    "O4": "https://developers.openai.com/api/docs/deprecations",
}

#: Fecha en que el revisor declara haber consultado las fuentes.
REVIEW_2026_11_17 = "2026-11-17"

#: Nota que llevan las entradas cuya fuente consulto el revisor sin que el
#: extracto haya llegado. Deja constancia del intento sin contarlo como
#: evidencia, que es la distincion que este registro protege.
NOTA_CONSULTA_SIN_EXTRACTO = (
    f"{REVIEW_2026_11_17}: el revisor declara haber consultado la referencia "
    "oficial. El extracto concreto no se ha trasladado a este entorno, asi que "
    "no confirma ningun valor: la entrada sigue sin verificar."
)

#: Evidencia propia del desarrollo: la firma del SDK instalado, leida aqui.
EVIDENCIA_SDK_IMAGENES = SourceEvidence(
    confirmed_by=DEVELOPER,
    confirmed_on=REVIEW_2026_11_17,
    states=(
        "Con openai==3.16.2, `Images.generate` acepta prompt, background, model, "
        "moderation, n, output_compression, output_format, partial_images, "
        "quality, response_format, size, stream, style y user; `Images.edit` "
        "acepta ademas image, input_fidelity y mask. Los seis parametros que "
        "este proyecto envia (model, prompt, n, size, quality, output_format) y "
        "el septimo opcional (input_fidelity, solo en edicion) existen en esa "
        "superficie."
    ),
    scope=(
        "Cubre UNICAMENTE que el SDK serializa esos parametros. No dice nada "
        "sobre que valores admite cada modelo ni sobre como los cobra."
    ),
    sources=("firma local de openai.resources.images.Images",),
)

EVIDENCIA_SDK_RESPONSES = SourceEvidence(
    confirmed_by=DEVELOPER,
    confirmed_on=REVIEW_2026_11_17,
    states=(
        "Con openai==3.16.2, `Responses.parse` acepta text_format, input, "
        "instructions, max_output_tokens, model, store, temperature, reasoning, "
        "verbosity, service_tier, prompt_cache_key y truncation, entre otros. "
        "Los cinco parametros que este proyecto envia siempre (model, "
        "instructions, input, text_format, max_output_tokens, store) y el "
        "opcional (temperature) existen en esa superficie, y `reasoning` esta "
        "disponible si hiciera falta."
    ),
    scope=(
        "Cubre UNICAMENTE la superficie del SDK y el metodo `parse`, que deriva "
        "el json_schema estricto del modelo Pydantic. No confirma que "
        "gpt-6.1-terra admita Structured Outputs ni que acepte temperature."
    ),
    sources=("firma local de openai.resources.responses.Responses",),
)


CHECKS: tuple[PendingCheck, ...] = (
    # --- SDK: evidencia propia, cerrada --------------------------------------
    PendingCheck(
        check_id="sdk_image_param_surface",
        target="*",
        source_tag="SDK",
        source_url="local: openai==3.16.2",
        what="Que los parametros de imagen que enviamos existen en el SDK.",
        assumption=(
            "`images.generate(model, prompt, n, size, quality, output_format)` y "
            "`images.edit(image=[...], **lo mismo, input_fidelity opcional)`."
        ),
        verified_when="La firma del SDK instalado acepta esos nombres.",
        blocks_real=False,
        status=CheckStatus.VERIFIED,
        evidence=(EVIDENCIA_SDK_IMAGENES,),
    ),
    PendingCheck(
        check_id="sdk_responses_param_surface",
        target="*",
        source_tag="SDK",
        source_url="local: openai==3.16.2",
        what="Que los parametros de guion que enviamos existen en el SDK.",
        assumption=(
            "`responses.parse(model, instructions, input, text_format, "
            "max_output_tokens, store=False)` y `temperature` solo si "
            "VIRALGEN_SEND_TEMPERATURE lo habilita."
        ),
        verified_when="La firma del SDK instalado acepta esos nombres.",
        blocks_real=False,
        status=CheckStatus.VERIFIED,
        evidence=(EVIDENCIA_SDK_RESPONSES,),
    ),
    # --- gpt-image-2.6-sunburst ---------------------------------------------
    PendingCheck(
        check_id="sunburst_endpoint",
        target=IMAGE_MODEL_SUNBURST,
        source_tag="O1",
        source_url=SOURCES["O1"],
        what=(
            "Si el modelo se sirve por /v1/images/generations y /v1/images/edits, "
            "o solo como herramienta de imagen dentro de /v1/responses."
        ),
        assumption=(
            "Se asume que responde en los dos endpoints de imagenes, igual que "
            "gpt-image-1, porque es el unico flujo que este proyecto implementa."
        ),
        verified_when=(
            "La ficha del modelo lista ambos endpoints como compatibles. Si solo "
            "estuviera disponible via Responses, el adaptador actual NO sirve y "
            "haria falta un flujo nuevo, no un cambio de cadena de modelo."
        ),
        blocks_real=True,
        notes=(NOTA_CONSULTA_SIN_EXTRACTO,),
    ),
    PendingCheck(
        check_id="sunburst_sizes",
        target=IMAGE_MODEL_SUNBURST,
        source_tag="O1",
        source_url=SOURCES["O1"],
        what="Valores admitidos de `size`, y si existe alguno 9:16 nativo.",
        assumption=(
            "Se declara el mismo juego conservador que gpt-image-1 "
            "(1024x1024, 1024x1536, 1536x1024) marcado como SUPUESTO. El vertical "
            "1024x1536 es 2:3, no 9:16: la entrega vertical la resuelve el modulo "
            "4 con su plan de presentacion, igual que hoy."
        ),
        verified_when=(
            "La ficha del modelo enumera los tamanos. Si incluyera un vertical "
            "9:16 nativo (p. ej. 1080x1920), el plan de presentacion del modulo 4 "
            "dejaria de necesitar adaptacion y hay que declararlo."
        ),
        blocks_real=True,
        notes=(NOTA_CONSULTA_SIN_EXTRACTO,),
    ),
    PendingCheck(
        check_id="sunburst_qualities",
        target=IMAGE_MODEL_SUNBURST,
        source_tag="O1",
        source_url=SOURCES["O1"],
        what="Valores admitidos de `quality`.",
        assumption=(
            "Se declara low/medium/high/auto como SUPUESTO, por continuidad con "
            "gpt-image-1."
        ),
        verified_when="La ficha del modelo enumera los valores de calidad.",
        blocks_real=True,
        notes=(NOTA_CONSULTA_SIN_EXTRACTO,),
    ),
    PendingCheck(
        check_id="sunburst_edit_references",
        target=IMAGE_MODEL_SUNBURST,
        source_tag="O2",
        source_url=SOURCES["O2"],
        what=(
            "Cuantas imagenes de referencia admite una edicion, y que limite de "
            "bytes o de pixeles tiene cada una."
        ),
        assumption=(
            "Se declaran 4 referencias como SUPUESTO. El proyecto nunca omite un "
            "personaje para encajar en el limite: si no caben, bloquea."
        ),
        verified_when=(
            "La guia indica el maximo de imagenes por peticion de edicion y el "
            "limite por imagen para este modelo."
        ),
        blocks_real=True,
        notes=(NOTA_CONSULTA_SIN_EXTRACTO,),
    ),
    PendingCheck(
        check_id="sunburst_input_fidelity",
        target=IMAGE_MODEL_SUNBURST,
        source_tag="O2",
        source_url=SOURCES["O2"],
        what=(
            "Si el modelo admite `input_fidelity` y con que valores; es el "
            "parametro que mas afecta a la consistencia de los personajes."
        ),
        assumption=(
            "El proyecto NO lo envia por defecto. Si se configura "
            "VIRALGEN_MEDIA_INPUT_FIDELITY, se valida contra los valores "
            "declarados (low/high, SUPUESTOS) y entra en la identidad de cache."
        ),
        verified_when=(
            "La guia documenta el parametro para este modelo y sus valores "
            "admitidos, y si cambia el consumo de tokens de entrada."
        ),
        blocks_real=False,
        notes=(NOTA_CONSULTA_SIN_EXTRACTO,),
    ),
    PendingCheck(
        check_id="sunburst_response_shape",
        target=IMAGE_MODEL_SUNBURST,
        source_tag="O2",
        source_url=SOURCES["O2"],
        what=(
            "Si la respuesta trae siempre `b64_json`, y si incluye `usage` con "
            "input_tokens / output_tokens."
        ),
        assumption=(
            "El lector exige `b64_json` y falla con un mensaje explicito si no "
            "llega; este MVP no descarga imagenes por URL. `usage` se guarda solo "
            "si viene, campo a campo, sin inventar ceros."
        ),
        verified_when=(
            "La guia describe la forma de la respuesta de este modelo, incluido "
            "si `response_format` sigue existiendo o si b64 es el unico modo."
        ),
        blocks_real=True,
        notes=(NOTA_CONSULTA_SIN_EXTRACTO,),
    ),
    PendingCheck(
        check_id="sunburst_token_consumption",
        target=IMAGE_MODEL_SUNBURST,
        source_tag="O1",
        source_url=SOURCES["O1"],
        what=(
            "Como se factura: por imagen segun tamano y calidad, o por tokens; y "
            "como se cobran las imagenes de referencia de una edicion."
        ),
        assumption=(
            "NINGUNA. Sin tarifa configurada, el coste estimado es `null` y el "
            "informe lo dice. No se asume que una edicion con 3 referencias cueste "
            "lo mismo que una generacion sin referencias."
        ),
        verified_when=(
            "La ficha del modelo da la tarifa por tamano y calidad, y la guia dice "
            "si los tokens de las imagenes de entrada se cobran aparte."
        ),
        blocks_real=False,
        notes=(NOTA_CONSULTA_SIN_EXTRACTO,),
    ),
    PendingCheck(
        check_id="sunburst_account_availability",
        target=IMAGE_MODEL_SUNBURST,
        source_tag="cuenta",
        source_url="proyecto «Viralgen VPS» en la organizacion del operador",
        what="Si el modelo esta disponible para nuestra clave y proyecto.",
        assumption=(
            "NINGUNA. Un modelo declarado en el catalogo no implica acceso: el "
            "proveedor puede responder model_not_found o 403, y ambos se tratan "
            "como permanentes sin reintento."
        ),
        verified_when=(
            "Una llamada real con la clave del proyecto «Viralgen VPS» devuelve "
            "una imagen, o el panel de la organizacion lista el modelo. Esto NO "
            "se puede verificar sin credenciales ni presupuesto."
        ),
        blocks_real=True,
    ),
    # --- gpt-6.1-terra ------------------------------------------------------
    PendingCheck(
        check_id="terra_structured_outputs",
        target=TEXT_MODEL_TERRA,
        source_tag="O3",
        source_url=SOURCES["O3"],
        what=(
            "Si el modelo admite Structured Outputs estricto en /v1/responses, "
            "que es el unico modo en que este proyecto pide guiones."
        ),
        assumption=(
            "Se asume que si. Si no los admitiera, `responses.parse` devuelve un "
            "400 que el adaptador clasifica como permanente y NO reintenta, con un "
            "mensaje que ya menciona esa posibilidad."
        ),
        verified_when=(
            "La ficha del modelo declara soporte de structured outputs / "
            "json_schema estricto."
        ),
        blocks_real=True,
        notes=(NOTA_CONSULTA_SIN_EXTRACTO,),
    ),
    PendingCheck(
        check_id="terra_temperature",
        target=TEXT_MODEL_TERRA,
        source_tag="O3",
        source_url=SOURCES["O3"],
        what="Si el modelo acepta `temperature`.",
        assumption=(
            "El proyecto NO lo envia: VIRALGEN_SEND_TEMPERATURE es False por "
            "defecto, precisamente porque hay modelos que lo rechazan. Enviarlo a "
            "un modelo que no lo admite devuelve unsupported_parameter, que esta "
            "en la lista de codigos que no se reintentan."
        ),
        verified_when=(
            "La ficha del modelo dice si el parametro se admite o se ignora. "
            "Mientras no conste, se queda sin enviar."
        ),
        blocks_real=False,
        notes=(NOTA_CONSULTA_SIN_EXTRACTO,),
    ),
    PendingCheck(
        check_id="terra_reasoning_and_output_budget",
        target=TEXT_MODEL_TERRA,
        source_tag="O3",
        source_url=SOURCES["O3"],
        what=(
            "Si el modelo razona antes de responder y si esos tokens cuentan "
            "contra `max_output_tokens`."
        ),
        assumption=(
            "Se mantiene MAX_OUTPUT_TOKENS=6000 y una respuesta cortada se trata "
            "como `incomplete` SIN reintento automatico, con un mensaje que pide "
            "subir el limite. Si el modelo gasta tokens de razonamiento del mismo "
            "presupuesto, 6000 puede quedarse corto para un guion largo."
        ),
        verified_when=(
            "La ficha del modelo indica si es un modelo de razonamiento, su "
            "ventana de contexto y su maximo de tokens de salida."
        ),
        blocks_real=False,
        notes=(NOTA_CONSULTA_SIN_EXTRACTO,),
    ),
    PendingCheck(
        check_id="terra_pricing",
        target=TEXT_MODEL_TERRA,
        source_tag="O3",
        source_url=SOURCES["O3"],
        what="Tarifa por millon de tokens de entrada y de salida.",
        assumption=(
            "NINGUNA. Sin VIRALGEN_PRICE_INPUT_PER_1M_USD y "
            "VIRALGEN_PRICE_OUTPUT_PER_1M_USD, el coste estimado del guion es "
            "`null`; un coste desconocido sigue siendo desconocido."
        ),
        verified_when="La ficha del modelo da las dos tarifas.",
        blocks_real=False,
        notes=(NOTA_CONSULTA_SIN_EXTRACTO,),
    ),
    PendingCheck(
        check_id="terra_account_availability",
        target=TEXT_MODEL_TERRA,
        source_tag="cuenta",
        source_url="proyecto «Viralgen VPS» en la organizacion del operador",
        what="Si el modelo esta disponible para nuestra clave y proyecto.",
        assumption=(
            "NINGUNA. OPENAI_MODEL es configurable y no se sustituye en silencio: "
            "un model_not_found es permanente y se reporta tal cual."
        ),
        verified_when=(
            "Una llamada real con la clave del proyecto «Viralgen VPS» devuelve un "
            "guion valido. No se puede verificar sin credenciales."
        ),
        blocks_real=True,
    ),
    # --- Deprecaciones ------------------------------------------------------
    PendingCheck(
        check_id="deprecations_current_models",
        target="*",
        source_tag="O4",
        source_url=SOURCES["O4"],
        what=(
            "Si gpt-image-1 o el modelo de guion en uso tienen fecha de retirada "
            "anunciada."
        ),
        assumption=(
            "Se asume que gpt-image-1 sigue sirviendo: es el modelo verificado del "
            "catalogo y el que puede usar el piloto. Si estuviera anunciado para "
            "retirada, el piloto depende de cerrar sunburst antes de esa fecha."
        ),
        verified_when=(
            "La pagina de deprecaciones dice si esos identificadores aparecen y "
            "con que fecha de cierre."
        ),
        blocks_real=False,
        notes=(NOTA_CONSULTA_SIN_EXTRACTO,),
    ),
)


def checks_for(target: str) -> list[PendingCheck]:
    """Entradas que afectan a un modelo, mas las generales."""
    return [entrada for entrada in CHECKS if entrada.target in (target, "*")]


def blocking_for(target: str) -> list[PendingCheck]:
    """Entradas que impiden usar ese modelo en modo real."""
    return [entrada for entrada in checks_for(target) if entrada.blocking]


def find(check_id: str) -> PendingCheck:
    for entrada in CHECKS:
        if entrada.check_id == check_id:
            return entrada
    raise KeyError(f"no hay ninguna verificacion de modelo con id {check_id!r}")


def totals() -> dict[str, int]:
    return totals_for(CHECKS)


def describe_all() -> dict:
    return {
        "reason": UNREACHABLE_REASON,
        "reviewer_consulted_on": REVIEW_2026_11_17,
        "sources": dict(SOURCES),
        "checks": [entrada.describe() for entrada in CHECKS],
        "totals": totals(),
        "blocked_models": sorted({e.target for e in CHECKS if e.blocking and e.target != "*"}),
        "note": (
            "Las dos entradas verificadas las aporto el desarrollo leyendo la "
            "firma del SDK instalado; no dicen nada sobre el comportamiento del "
            "proveedor. Todo lo que depende de la documentacion oficial sigue sin "
            "verificar: el revisor declara haber consultado las fuentes el "
            f"{REVIEW_2026_11_17}, pero el extracto no ha llegado a este entorno."
        ),
    }


__all__ = [
    "IMAGE_MODEL_SUNBURST",
    "TEXT_MODEL_TERRA",
    "UNREACHABLE_REASON",
    "SOURCES",
    "REVIEW_2026_11_17",
    "CHECKS",
    "checks_for",
    "blocking_for",
    "find",
    "totals",
    "describe_all",
]
