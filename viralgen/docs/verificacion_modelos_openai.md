# Verificación de los modelos de OpenAI: qué falta y quién puede aportarlo

Fecha de este documento: **2026-11-17**. Estado del código: rama
`claude/brave-sagan-bewv55`.

Este documento existe por una razón concreta: **este entorno no alcanza
`developers.openai.com`**. La comprobación del 2026-11-17 devolvió código `000`
(conexión no establecida) para las cuatro referencias oficiales, con
`https://pypi.org/simple/openai/` respondiendo `200` en la misma tanda como
control. Por tanto no he leído ninguna de las cuatro fuentes, y nada de lo que
sigue afirma lo contrario.

## Atribución de la evidencia

| Quién | Qué aportó | Fecha |
|---|---|---|
| Revisor | Declara haber **consultado** las cuatro referencias oficiales | 2026-11-17 |
| Revisor | El **contenido** de esos extractos | — *no trasladado todavía* |
| Desarrollo (este entorno) | La **superficie de parámetros del SDK instalado** (`openai==3.16.2`), leída de las firmas de `Images.generate/edit` y `Responses.parse` | 2026-11-17 |
| Desarrollo | Cualquier afirmación sobre lo que admite el modelo al otro lado | — *imposible aquí* |

Esa distinción está codificada, no sólo escrita: en
`src/viralgen/media/model_checks.py` la consulta del revisor consta como `note`
fechada —prueba de que alguien abrió la fuente— y **no** como `SourceEvidence`.
`tests/test_media_model_checks.py::test_una_consulta_sin_extracto_no_se_cuenta_como_evidencia`
falla si alguien intenta ascenderla.

Que el SDK acepte un parámetro **no** significa que el modelo lo admita: el SDK
es genérico para toda la familia de modelos de imagen. Confundir las dos cosas
es el error que este registro existe para evitar.

## Estado actual del registro

```
total: 16   verificadas: 2   parciales: 0   pendientes: 14   bloqueantes: 8
```

Las dos verificadas son las del SDK, aportadas por el desarrollo. Las catorce
pendientes dependen de la documentación oficial o de una llamada real. Las ocho
bloqueantes son:

```
sunburst_endpoint          sunburst_sizes          sunburst_qualities
sunburst_edit_references   sunburst_response_shape sunburst_account_availability
terra_structured_outputs   terra_account_availability
```

Mientras siga cualquiera de ellas, `gpt-image-2.6-sunburst` no se despacha en
modo real. Las de `terra` no bloquean el envío por sí mismas —el modelo de
guion no tiene lista blanca, a propósito— pero dejan constancia de que su
compatibilidad no está confirmada.

Se consulta con:

```bash
python -c "import json; from viralgen.media import model_checks; \
print(json.dumps(model_checks.describe_all(), indent=2, ensure_ascii=False))"
```

## Extractos que necesito

Para cada punto, lo útil es **el fragmento literal** de la página (o una captura
de la tabla), no un resumen. Si un dato no aparece en la fuente, decir «no
aparece» también cierra la entrada: es información.

### A. `gpt-image-2.6-sunburst` — ficha del modelo y guía de imágenes

Fuentes: `developers.openai.com/api/docs/models/gpt-image-2.6-sunburst` y
`developers.openai.com/api/docs/guides/image-generation`.

| # | Entrada del registro | Extracto que hace falta |
|---|---|---|
| A1 | `sunburst_endpoint` | Los **endpoints** que lista la ficha. Concretamente: ¿responde en `POST /v1/images/generations` y `POST /v1/images/edits`, o sólo como herramienta de imagen dentro de `/v1/responses`? **Si fuera sólo lo segundo, el adaptador actual no sirve** y no es un cambio de cadena de modelo: es un flujo nuevo. |
| A2 | `sunburst_sizes` | La **lista de valores de `size`**. Interesa especialmente si hay un vertical 9:16 nativo (p. ej. `1080x1920`): hoy el único vertical es 2:3 y el módulo 4 lo adapta. |
| A3 | `sunburst_qualities` | La **lista de valores de `quality`**. |
| A4 | `sunburst_response_shape` | Si la respuesta trae **siempre `b64_json`**, si `response_format` sigue existiendo, y si incluye `usage` (con qué campos). |
| A5 | `sunburst_edit_references` | **Cuántas imágenes** admite `image[]` en una edición, y el **límite por imagen** (bytes y/o píxeles). |
| A6 | `sunburst_input_fidelity` | Si el modelo admite **`input_fidelity`**, con qué valores, y si cambia el consumo de tokens de entrada. Es el parámetro que más afecta a la consistencia de los personajes. |
| A7 | `sunburst_token_consumption` | La **tarifa**: por imagen según tamaño y calidad, o por tokens. Y si las **imágenes de referencia de una edición se cobran aparte**. |

### B. `gpt-6.1-terra` — ficha del modelo

Fuente: `developers.openai.com/api/docs/models/gpt-6.1-terra`.

| # | Entrada del registro | Extracto que hace falta |
|---|---|---|
| B1 | `terra_structured_outputs` | Si declara soporte de **Structured Outputs / `json_schema` estricto** en `/v1/responses`. Es el único modo en que este proyecto pide guiones. |
| B2 | `terra_temperature` | Si **acepta `temperature`**, la ignora o la rechaza. Hoy no se envía. |
| B3 | `terra_reasoning_and_output_budget` | Si es un **modelo de razonamiento**, su ventana de contexto, su máximo de tokens de salida, y **si los tokens de razonamiento cuentan contra `max_output_tokens`**. Si cuentan, los 6000 actuales pueden quedarse cortos para un guion largo. |
| B4 | `terra_pricing` | **Tarifa** por millón de tokens de entrada y de salida (y de entrada en caché, si la hay). |

### C. Deprecaciones

Fuente: `developers.openai.com/api/docs/deprecations`.

| # | Entrada del registro | Extracto que hace falta |
|---|---|---|
| C1 | `deprecations_current_models` | Si **`gpt-image-1`** aparece con fecha de retirada. Es el modelo verificado que el piloto puede usar hoy; si está anunciado, el piloto depende de cerrar sunburst antes de esa fecha. |

### D. Lo que ningún documento resuelve

Dos entradas (`sunburst_account_availability`, `terra_account_availability`) no
se cierran leyendo nada: **sólo una llamada real** con la clave del proyecto
«Viralgen VPS» demuestra que esos modelos están disponibles para nuestra cuenta.
Un modelo declarado en el catálogo no da acceso a él; el proveedor puede
responder `model_not_found` o `403`, y ambos se tratan como permanentes sin
reintento.

## Cómo se incorpora un extracto cuando llegue

En `src/viralgen/media/model_checks.py`, a la entrada correspondiente:

```python
PendingCheck(
    check_id="sunburst_sizes",
    ...
    status=CheckStatus.VERIFIED,          # o PARTIAL si no cierra la condicion
    evidence=(
        SourceEvidence(
            confirmed_by=REVIEWER,         # quien lo leyo de verdad
            confirmed_on="2026-11-__",
            states="La ficha enumera: 1024x1024, 1024x1536, ...",
            scope="Cubre los tamanos; no dice nada de las calidades.",
            sources=(SOURCES["O1"],),
        ),
    ),
)
```

Y, si el extracto confirma los valores, quitar ese campo de `assumed_params` en
`capabilities.py`. Cuando `assumed_params` queda vacío se pone `verified=True` y
el bloqueo del envío real desaparece **por sí solo**: no hay interruptor que lo
eluda, y `tests/test_media_capabilities.py` lo comprueba.

Si un extracto **contradice** un supuesto, el valor del catálogo se corrige y se
registra igual. Un supuesto desmentido no se borra en silencio.
