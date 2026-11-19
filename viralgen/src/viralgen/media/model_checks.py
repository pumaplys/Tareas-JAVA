"""Estado de verificacion de los modelos de OpenAI que usa el proyecto.

Mismo patron que `publish/verification.py` y por el mismo motivo: este entorno
NO alcanza la documentacion del proveedor, y el encargo prohibe sustituir una
referencia inaccesible por una afirmacion de haberla leido.

QUE SE SABE Y COMO SE SABE
--------------------------

Tres clases de hecho, y no se mezclan:

1. **Lo que acepta el SDK instalado.** Comprobado aqui, leyendo las firmas de
   `openai.resources.images.Images.generate/edit` y de
   `openai.resources.responses.Responses.parse` con `openai==3.16.2`. Evidencia
   propia del desarrollo.
2. **Lo que dice la documentacion del proveedor.** Aportada por el REVISOR el
   2026-11-18. Este entorno no la ha leido: el proxy de egreso sigue sin
   alcanzar `developers.openai.com`.
3. **Lo que solo demuestra una llamada real.** La disponibilidad del modelo en
   la cuenta del operador. No hay documento que la cierre.

Que el SDK acepte un parametro no significa que el modelo lo admita, y que la
documentacion lo describa no significa que nuestra clave tenga acceso. Las tres
cosas se registran por separado.

POR QUE LA DISPONIBILIDAD EN LA CUENTA NO BLOQUEA
-------------------------------------------------

Hasta el 2026-11-18 las dos entradas `*_account_availability` estaban marcadas
como bloqueantes. Era un bucle: la unica prueba posible es una llamada real, y
un bloqueo que impide esa llamada no se puede levantar nunca. Ahora quedan como
PENDIENTES declaradas con `blocks_real=False`, y lo que gobierna la primera
llamada real es el presupuesto acordado del piloto -una generacion y una
edicion de prueba antes de las diez imagenes del cuento-, no una puerta de
codigo que se contradice a si misma.

Lo que si bloquea es el protocolo sin verificar y el modelo retirado, que son
condiciones comprobables sin gastar nada.
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
IMAGE_MODEL_RETIRED = "gpt-image-1"
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

#: Fechas de las dos aportaciones del revisor.
REVIEW_2026_11_17 = "2026-11-17"
REVIEW_2026_11_18 = "2026-11-18"

#: Como llego la evidencia del 2026-11-18. Se registra la RUTA, no solo el
#: resultado: el revisor declara haber revisado las fuentes oficiales
#: enlazadas, y el documento que las resume se produjo consultando ChatGPT ese
#: mismo dia. Son dos afirmaciones distintas y ninguna es una lectura directa
#: de este entorno.
EVIDENCE_ROUTE_2026_11_18 = (
    "Documento «Evidencia para el piloto de Nubaria Cuentos», aportado por el "
    "revisor sobre la base del commit 5557aa1. El revisor declara haber "
    "revisado las fuentes oficiales enlazadas; el documento se elaboro "
    "consultando ChatGPT el 2026-11-18. El revisor advierte ademas que no "
    "ejecuto las pruebas ni hizo llamadas en la cuenta «Viralgen VPS». El "
    "desarrollo no ha leido ninguna de esas URLs."
)

#: Referencias oficiales citadas. Las cuatro primeras son las del 2026-11-17;
#: el resto las anadio la evidencia del 2026-11-18.
SOURCES: dict[str, str] = {
    "O1": "https://developers.openai.com/api/docs/models/gpt-image-2.6-sunburst",
    "O2": "https://developers.openai.com/api/docs/guides/image-generation",
    "O3": "https://developers.openai.com/api/docs/models/gpt-6.1-terra",
    "O4": "https://developers.openai.com/api/docs/deprecations",
    "O5": "https://developers.openai.com/api/docs/models/gpt-image-1",
    "O6": "https://developers.openai.com/api/reference/resources/images/methods/generate",
    "O7": "https://developers.openai.com/api/reference/resources/images/methods/edit",
    "O8": "https://developers.openai.com/api/docs/guides/image-prompting?model=gpt-image-2.6",
    "O9": "https://developers.openai.com/api/reference/python/resources/images/methods/edit",
    "O10": "https://developers.openai.com/api/docs/guides/reasoning",
}

#: Nota de la primera tanda: se consulto la fuente pero el extracto no llego.
#: Se conserva en las entradas que SIGUEN sin extracto, para que conste el
#: intento sin contarlo como evidencia.
NOTA_CONSULTA_SIN_EXTRACTO = (
    f"{REVIEW_2026_11_17}: el revisor declara haber consultado la referencia "
    "oficial. El extracto concreto no se ha trasladado a este entorno, asi que "
    "no confirma ningun valor: la entrada sigue sin verificar."
)


def _revisor(*, states: str, scope: str, tags: tuple[str, ...]) -> SourceEvidence:
    """Evidencia del revisor del 2026-11-18, con su ruta y sus fuentes."""
    return SourceEvidence(
        confirmed_by=REVIEWER,
        confirmed_on=REVIEW_2026_11_18,
        states=states,
        scope=scope,
        sources=tuple(SOURCES[tag] for tag in tags) + (EVIDENCE_ROUTE_2026_11_18,),
    )


# ---------------------------------------------------------------------------
# Evidencia propia del desarrollo: la firma del SDK instalado, leida aqui.
# ---------------------------------------------------------------------------

EVIDENCIA_SDK_IMAGENES = SourceEvidence(
    confirmed_by=DEVELOPER,
    confirmed_on=REVIEW_2026_11_17,
    states=(
        "Con openai==3.16.2, `Images.generate` acepta prompt, background, model, "
        "moderation, n, output_compression, output_format, partial_images, "
        "quality, response_format, size, stream, style y user; `Images.edit` "
        "acepta ademas image, input_fidelity y mask. Los seis parametros que "
        "este proyecto envia (model, prompt, n, size, quality, output_format) "
        "existen en esa superficie."
    ),
    scope=(
        "Cubre UNICAMENTE que el SDK serializa esos parametros. No dice nada "
        "sobre que valores admite cada modelo ni sobre como los cobra. En "
        "particular, que `Images.edit` acepte `input_fidelity` NO significa que "
        "gpt-image-2.6-sunburst lo admita: su guia dice omitirlo."
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
        "Los parametros que este proyecto envia (model, instructions, input, "
        "text_format, max_output_tokens, store) y el opcional (temperature) "
        "existen en esa superficie."
    ),
    scope=(
        "Cubre UNICAMENTE la superficie del SDK y el metodo `parse`, que deriva "
        "el json_schema estricto del modelo Pydantic. No confirma por si solo "
        "que gpt-6.1-terra acepte cada parametro."
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
            "`images.edit(image=[...], **lo mismo)`."
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
    # --- gpt-image-1: retirado -----------------------------------------------
    PendingCheck(
        check_id="gpt_image_1_retired",
        target=IMAGE_MODEL_RETIRED,
        source_tag="O5",
        source_url=SOURCES["O5"],
        what="Si gpt-image-1 sigue atendiendo solicitudes.",
        assumption=(
            "Esta RETIRADO desde el 2026-10-23: el catalogo lo sigue resolviendo "
            "para leer manifiestos, ejemplos y claves de cache historicos, pero "
            "`assert_verified_for_real` rechaza toda solicitud nueva antes de "
            "abrir HTTP."
        ),
        verified_when=(
            "La ficha del modelo declara la fecha de apagado. CERRADA: el "
            "revisor cita «was shut down on October 23, 2026 and is no longer "
            "available»."
        ),
        blocks_real=True,
        status=CheckStatus.VERIFIED,
        evidence=(
            _revisor(
                states=(
                    "La ficha de GPT Image 1 indica: «was shut down on October 23, "
                    "2026 and is no longer available»."
                ),
                scope=(
                    "Cierra que el identificador no sirve para peticiones nuevas. "
                    "NO implica borrarlo del catalogo: los documentos y las claves "
                    "de cache ya generados lo citan y tienen que seguir "
                    "resolviendose."
                ),
                tags=("O5",),
            ),
        ),
    ),
    # --- gpt-image-2.6-sunburst: protocolo -----------------------------------
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
            "Responde en los dos endpoints de imagenes: generacion por JSON y "
            "edicion por multipart. Se pide `n=1` y el modelo se nombra "
            "explicitamente; no se cambia de modelo de forma implicita."
        ),
        verified_when=(
            "La ficha del modelo y las referencias de los metodos listan ambos "
            "endpoints con ese identificador. CERRADA."
        ),
        blocks_real=True,
        status=CheckStatus.VERIFIED,
        evidence=(
            _revisor(
                states=(
                    "La ficha de Sunburst describe generacion y edicion; las "
                    "referencias de los metodos generate y edit enumeran el ID "
                    "exacto, y la de generacion indica «Specify a model "
                    "explicitly.»"
                ),
                scope=(
                    "Cubre las rutas y el identificador. No dice nada sobre el "
                    "acceso de nuestra clave."
                ),
                tags=("O1", "O6", "O7"),
            ),
        ),
    ),
    PendingCheck(
        check_id="sunburst_sizes",
        target=IMAGE_MODEL_SUNBURST,
        source_tag="O2",
        source_url=SOURCES["O2"],
        what="Valores admitidos de `size`, y si existe alguno 9:16 nativo.",
        assumption=(
            "La geometria se valida contra la REGLA documentada, no contra una "
            "lista inventada: lados multiplos de 16, proporcion entre 1:3 y 3:1, "
            "lado maximo 3840 px y entre 655 360 y 8 294 400 px. `720x1280` la "
            "cumple y es 9:16 NATIVO, asi que la entrega vertical ya no necesita "
            "adaptacion del modulo 4."
        ),
        verified_when=(
            "La guia publica los limites de tamano. CERRADA. Que 720x1280 los "
            "cumpla es una DEDUCCION aritmetica de esos limites (45x16, 80x16, "
            "9:16, 921 600 px), no una imagen generada."
        ),
        blocks_real=True,
        status=CheckStatus.VERIFIED,
        evidence=(
            _revisor(
                states=(
                    "Size and quality options: ambos lados multiplos de 16, "
                    "proporcion entre 1:3 y 3:1, lado maximo 3840 px, y entre "
                    "655 360 y 8 294 400 pixeles."
                ),
                scope=(
                    "Cubre los limites como regla. NO es una enumeracion de "
                    "tamanos ni una imagen observada: que 720x1280 sea admisible "
                    "se deduce de la regla."
                ),
                tags=("O2",),
            ),
        ),
    ),
    PendingCheck(
        check_id="sunburst_qualities",
        target=IMAGE_MODEL_SUNBURST,
        source_tag="O8",
        source_url=SOURCES["O8"],
        what="Valores admitidos de `quality`.",
        assumption=(
            "Solo se declara y se envia `medium`, que es el que consta. El resto "
            "de valores no se supone: un `low` o `high` no declarado se rechaza "
            "antes de cualquier HTTP."
        ),
        verified_when=(
            "La guia enumera la lista completa de valores de calidad. PARCIAL: "
            "consta `medium`, que es el unico que este proyecto usa; la lista "
            "entera sigue sin constar."
        ),
        blocks_real=False,
        status=CheckStatus.PARTIAL,
        evidence=(
            _revisor(
                states="La guia de 2.6, en Model parameters, incluye `medium`.",
                scope=(
                    "Cubre que `medium` es valido. No cubre que existan o no "
                    "otros valores, asi que el catalogo no declara ninguno mas."
                ),
                tags=("O8",),
            ),
        ),
    ),
    PendingCheck(
        check_id="sunburst_edit_references",
        target=IMAGE_MODEL_SUNBURST,
        source_tag="O7",
        source_url=SOURCES["O7"],
        what=(
            "Cuantas imagenes de referencia admite una edicion, y que limite de "
            "bytes o de pixeles tiene cada una."
        ),
        assumption=(
            "El maximo publicado es 16 por peticion; el proyecto mantiene su "
            "limite local mas estricto de 4, porque ninguna escena de la biblia "
            "usa mas de tres personajes. Las 8 referencias que el cuento envia "
            "en total NO son 8 llamadas: son las adjuntas de cada edicion. El "
            "limite de BYTES por imagen sigue sin constar para 2.6."
        ),
        verified_when=(
            "La referencia de edicion indica el maximo de imagenes y el limite "
            "por imagen para este modelo. PARCIAL: consta el numero (16 por "
            "peticion); el limite de bytes no se puede atribuir a 2.6 porque la "
            "referencia Python lo da enumerando modelos anteriores."
        ),
        blocks_real=False,
        status=CheckStatus.PARTIAL,
        evidence=(
            _revisor(
                states=(
                    "La referencia de edicion muestra archivos multipart con el "
                    "campo repetido `image[]` y, para el array de referencias de "
                    "GPT Image, indica «you can provide up to 16 images»."
                ),
                scope=(
                    "Cubre el numero de imagenes por peticion y la forma del "
                    "multipart. NO cubre el limite de bytes por imagen: la "
                    "referencia Python lo enuncia para modelos anteriores y "
                    "atribuirlo a 2.6 seria una analogia, no una lectura."
                ),
                tags=("O7", "O9"),
            ),
        ),
    ),
    PendingCheck(
        check_id="sunburst_input_fidelity",
        target=IMAGE_MODEL_SUNBURST,
        source_tag="O8",
        source_url=SOURCES["O8"],
        what=(
            "Si el modelo admite `input_fidelity` y con que valores; era el "
            "parametro candidato para la consistencia de los personajes."
        ),
        assumption=(
            "Se OMITE. La guia de 2.6 lo indica expresamente, asi que el "
            "catalogo lo marca `forbids_input_fidelity` y configurar "
            "VIRALGEN_MEDIA_INPUT_FIDELITY para este modelo es un ERROR que se "
            "rechaza antes de la peticion. Tampoco se envia `null`. La "
            "continuidad de personajes se sostiene con las referencias y la "
            "revision visual."
        ),
        verified_when=(
            "La guia dice si el parametro se usa con este modelo. CERRADA, y en "
            "sentido contrario al que se habia supuesto."
        ),
        blocks_real=False,
        status=CheckStatus.VERIFIED,
        evidence=(
            _revisor(
                states=(
                    "Image prompting, pestana GPT-Image-2.6, al final de Model "
                    "parameters: «Omit `input_fidelity` on image edits.»"
                ),
                scope=(
                    "Cubre que no debe enviarse a este modelo. No dice que el "
                    "parametro no exista en el SDK -existe- ni como se comporta "
                    "en otros modelos."
                ),
                tags=("O8",),
            ),
        ),
    ),
    PendingCheck(
        check_id="sunburst_response_shape",
        target=IMAGE_MODEL_SUNBURST,
        source_tag="O6",
        source_url=SOURCES["O6"],
        what=(
            "Si la respuesta trae siempre `b64_json` y si `response_format` sigue "
            "existiendo."
        ),
        assumption=(
            "Se lee `data[0].b64_json`, se decodifica y se comprueba que es una "
            "imagen valida con las dimensiones esperadas. `response_format` NO se "
            "envia y una URL no se acepta como sustituto. `output_format` elige "
            "el formato de archivo."
        ),
        verified_when=(
            "Las secciones Returns de generacion y edicion describen la forma de "
            "la respuesta. CERRADA."
        ),
        blocks_real=True,
        status=CheckStatus.VERIFIED,
        evidence=(
            _revisor(
                states=(
                    "En Create image → Returns y Create image edit → Returns la "
                    "imagen llega en `data[].b64_json`. La referencia de "
                    "generacion identifica `response_format` como parametro "
                    "heredado no admitido por GPT Image."
                ),
                scope=(
                    "Cubre la forma de la respuesta y que `response_format` no "
                    "aplica. No cubre el contenido de la imagen."
                ),
                tags=("O6", "O7"),
            ),
        ),
    ),
    PendingCheck(
        check_id="sunburst_usage_schema",
        target=IMAGE_MODEL_SUNBURST,
        source_tag="O6",
        source_url=SOURCES["O6"],
        what="Que campos de consumo devuelve la respuesta, si los devuelve.",
        assumption=(
            "`usage` es OPCIONAL. Si llega, se guardan por separado "
            "`input_tokens`, `input_tokens_details.text_tokens`, "
            "`input_tokens_details.image_tokens`, `output_tokens` y "
            "`total_tokens`. Un campo ausente NO vale cero, y el total no se "
            "suma a sus componentes."
        ),
        verified_when=(
            "Las secciones Returns → usage describen el esquema. CERRADA."
        ),
        blocks_real=False,
        status=CheckStatus.VERIFIED,
        evidence=(
            _revisor(
                states=(
                    "`usage` es opcional; si llega, el esquema contempla "
                    "input_tokens, input_tokens_details.text_tokens, "
                    "input_tokens_details.image_tokens, output_tokens, "
                    "total_tokens y, opcionalmente, output_tokens_details."
                ),
                scope=(
                    "Cubre el esquema de los campos. No dice cuantos tokens "
                    "consume una imagen concreta."
                ),
                tags=("O6", "O7"),
            ),
        ),
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
            "Factura POR TOKENS, no por imagen: 5 USD por millon de tokens de "
            "texto de entrada, 8 USD por millon de imagen de entrada y 30 USD por "
            "millon de imagen de salida (tarifa estandar, sin descuentos). Las "
            "referencias adjuntas se cobran como imagen de ENTRADA. Los 30 USD NO "
            "son el precio de una imagen. Los tokens que consume una geometria "
            "dada no constan, asi que el coste del cuento sigue siendo "
            "desconocido a priori."
        ),
        verified_when=(
            "La ficha publica las tarifas y la guia dice si las entradas se "
            "cobran aparte. CERRADA en cuanto a TARIFAS; los RECUENTOS de tokens "
            "por imagen solo los da la calculadora o una llamada real."
        ),
        blocks_real=False,
        status=CheckStatus.VERIFIED,
        evidence=(
            _revisor(
                states=(
                    "Sunburst → Pricing, tarifa estandar sin descuento por millon "
                    "de tokens: 5 USD texto de entrada, 8 USD imagen de entrada, "
                    "30 USD imagen de salida. La calculadora de generacion estima "
                    "la salida a partir de modelo, calidad y dimensiones."
                ),
                scope=(
                    "Cubre las tarifas por token y el modelo de facturacion. NO "
                    "da los tokens de una imagen concreta, ni el coste total del "
                    "cuento, y la calculadora no incluye las entradas."
                ),
                tags=("O1", "O2"),
            ),
        ),
    ),
    PendingCheck(
        check_id="sunburst_account_availability",
        target=IMAGE_MODEL_SUNBURST,
        source_tag="cuenta",
        source_url="proyecto «Viralgen VPS» en la organizacion del operador",
        what="Si el modelo esta disponible para nuestra clave y proyecto.",
        assumption=(
            "NINGUNA. Un modelo documentado no implica acceso: el proveedor puede "
            "responder model_not_found o 403, y ambos se tratan como permanentes "
            "sin reintento. No bloquea por construccion -ver la cabecera del "
            "modulo-: la gobierna el presupuesto acordado del piloto, que exige "
            "una generacion y una edicion de prueba ANTES de las diez imagenes "
            "del cuento."
        ),
        verified_when=(
            "Una llamada real con la clave del proyecto «Viralgen VPS» devuelve "
            "una imagen. No hay documento que lo cierre, y una prueba de "
            "transporte simulado tampoco."
        ),
        blocks_real=False,
    ),
    # --- gpt-6.1-terra -------------------------------------------------------
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
            "Los admite. Si no fuera asi, `responses.parse` devolveria un 400 que "
            "el adaptador clasifica como permanente y NO reintenta."
        ),
        verified_when="La ficha del modelo declara Responses y Structured Outputs. CERRADA.",
        blocks_real=True,
        status=CheckStatus.VERIFIED,
        evidence=(
            _revisor(
                states="La ficha de Terra incluye Responses y Structured Outputs.",
                scope=(
                    "Cubre el soporte del modo. No cubre el acceso de nuestra "
                    "clave ni el tamano de salida suficiente para este cuento."
                ),
                tags=("O3",),
            ),
        ),
    ),
    PendingCheck(
        check_id="terra_reasoning_and_output_budget",
        target=TEXT_MODEL_TERRA,
        source_tag="O10",
        source_url=SOURCES["O10"],
        what=(
            "Si el modelo razona antes de responder y si esos tokens cuentan "
            "contra `max_output_tokens`."
        ),
        assumption=(
            "CUENTAN. Se mantiene MAX_OUTPUT_TOKENS=6000 y una respuesta cortada "
            "se trata como `incomplete` SIN reintento automatico y SIN subir el "
            "limite por cuenta propia: el mensaje pide subirlo a quien decide."
        ),
        verified_when=(
            "La guia de razonamiento dice si `max_output_tokens` incluye los "
            "tokens de razonamiento. CERRADA. Sigue PENDIENTE si 6000 bastan "
            "para este cuento, que solo lo dice una ejecucion real."
        ),
        blocks_real=False,
        status=CheckStatus.PARTIAL,
        evidence=(
            _revisor(
                states=(
                    "La guia de razonamiento, en Handling incomplete responses, "
                    "confirma que `max_output_tokens` incluye tambien los tokens "
                    "de razonamiento."
                ),
                scope=(
                    "Cubre que el presupuesto de salida es compartido. NO dice si "
                    "6000 bastan para un cuento de ~50 s: eso se sabra al "
                    "ejecutarlo."
                ),
                tags=("O10",),
            ),
        ),
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
            "La ficha del modelo dice si el parametro se admite o se ignora. La "
            "evidencia del 2026-11-18 no lo menciona, asi que sigue pendiente y "
            "el parametro se queda sin enviar."
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
        verified_when=(
            "La ficha del modelo da las dos tarifas. La evidencia del 2026-11-18 "
            "aporta las de Sunburst, no las de Terra."
        ),
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
            "un model_not_found es permanente y se reporta tal cual. No bloquea "
            "por construccion, igual que la de Sunburst."
        ),
        verified_when=(
            "Una llamada real con la clave del proyecto «Viralgen VPS» devuelve un "
            "guion valido."
        ),
        blocks_real=False,
    ),
    # --- Deprecaciones -------------------------------------------------------
    PendingCheck(
        check_id="deprecations_current_models",
        target="*",
        source_tag="O4",
        source_url=SOURCES["O4"],
        what=(
            "Si los identificadores en uso tienen fecha de retirada anunciada."
        ),
        assumption=(
            "gpt-image-1 esta retirado desde el 2026-10-23 y el catalogo lo "
            "refleja. El modelo de imagen vigente es gpt-image-2.6-sunburst. No "
            "consta retirada de Terra."
        ),
        verified_when=(
            "La pagina de deprecaciones, o la ficha del modelo, dan la fecha. "
            "CERRADA para gpt-image-1 por su propia ficha."
        ),
        blocks_real=False,
        status=CheckStatus.VERIFIED,
        evidence=(
            _revisor(
                states=(
                    "La ficha de GPT Image 1 fecha el apagado el 2026-10-23. La "
                    "evidencia no menciona retirada de gpt-6.1-terra ni de "
                    "gpt-image-2.6-sunburst."
                ),
                scope=(
                    "Cubre gpt-image-1. El silencio sobre los otros dos no es una "
                    "garantia de permanencia."
                ),
                tags=("O4", "O5"),
            ),
        ),
    ),
)


def checks_for(target: str) -> list[PendingCheck]:
    """Entradas que afectan a un modelo, mas las generales."""
    return [entrada for entrada in CHECKS if entrada.target in (target, "*")]


def blocking_for(target: str) -> list[PendingCheck]:
    """Entradas que impiden usar ese modelo en modo real."""
    return [entrada for entrada in checks_for(target) if entrada.blocking]


def pending_for(target: str) -> list[PendingCheck]:
    """Las que no estan verificadas para ese modelo, incluidas las parciales."""
    return [entrada for entrada in checks_for(target) if not entrada.verified]


def find(check_id: str) -> PendingCheck:
    for entrada in CHECKS:
        if entrada.check_id == check_id:
            return entrada
    raise KeyError(f"no hay ninguna verificacion de modelo con id {check_id!r}")


def totals() -> dict[str, int]:
    return totals_for(CHECKS)


def _refused_for_new_real_requests() -> dict[str, str]:
    """Modelos del catalogo que NO atienden una solicitud real nueva, y por que.

    Se calcula preguntandole a la propia puerta (`assert_verified_for_real`),
    no repitiendo su criterio aqui: si manana cambia, este informe cambia con
    ella en vez de quedarse desfasado.
    """
    from ..errors import ConfigError
    from .capabilities import IMAGE_MODELS, assert_verified_for_real

    refusados: dict[str, str] = {}
    for nombre, capacidades in sorted(IMAGE_MODELS.items()):
        try:
            assert_verified_for_real(capacidades)
        except ConfigError as exc:
            refusados[nombre] = exc.message
    return refusados


def describe_all() -> dict:
    return {
        "reason": UNREACHABLE_REASON,
        "reviewer_consulted_on": [REVIEW_2026_11_17, REVIEW_2026_11_18],
        "evidence_route": EVIDENCE_ROUTE_2026_11_18,
        "sources": dict(SOURCES),
        "checks": [entrada.describe() for entrada in CHECKS],
        "totals": totals(),
        # `blocking` significa "bloquea PORQUE sigue sin verificarse". Hoy no
        # hay ninguna, y decir solo eso induciria a creer que cualquier modelo
        # del catalogo sirve. Lo que de verdad importa es quien NO acepta una
        # solicitud nueva, y eso incluye el modelo retirado, cuya retirada esta
        # verificada precisamente.
        "blocked_models": sorted(
            {e.target for e in CHECKS if e.blocking and e.target != "*"}
        ),
        "refused_for_new_real_requests": _refused_for_new_real_requests(),
        "note": (
            "Tres clases de hecho, sin mezclar: lo que acepta el SDK (lo "
            "comprobo el desarrollo leyendo su firma), lo que dice la "
            "documentacion (lo aporto el revisor el 2026-11-18; el desarrollo no "
            "ha leido esas URLs) y lo que solo demuestra una llamada real (la "
            "disponibilidad en la cuenta, que no bloquea por construccion)."
        ),
    }


__all__ = [
    "IMAGE_MODEL_SUNBURST",
    "IMAGE_MODEL_RETIRED",
    "TEXT_MODEL_TERRA",
    "UNREACHABLE_REASON",
    "EVIDENCE_ROUTE_2026_11_18",
    "SOURCES",
    "REVIEW_2026_11_17",
    "REVIEW_2026_11_18",
    "CHECKS",
    "checks_for",
    "blocking_for",
    "pending_for",
    "find",
    "totals",
    "describe_all",
]
