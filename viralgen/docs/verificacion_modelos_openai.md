# Verificación de los modelos de OpenAI: qué se sabe y quién lo confirmó

Actualizado: **2026-11-19**. Rama `claude/brave-sagan-bewv55`.

Este documento existe por una razón concreta: **este entorno no alcanza
`developers.openai.com`**. La comprobación del 2026-11-17 devolvió código `000`
(conexión no establecida) para las referencias oficiales, con
`https://pypi.org/simple/openai/` respondiendo `200` en la misma tanda como
control. **No he leído ninguna de esas URLs**, y nada de lo que sigue afirma lo
contrario.

## Atribución de la evidencia

| Quién | Qué aportó | Fecha |
|---|---|---|
| Revisor | Declara haber **consultado** las cuatro referencias iniciales | 2026-11-17 |
| Revisor | El **contenido** de los extractos, en el documento «Evidencia para el piloto de Nubaria Cuentos», elaborado consultando ChatGPT y declarando haber revisado las fuentes oficiales enlazadas | 2026-11-18 |
| Desarrollo (este entorno) | La **superficie de parámetros del SDK instalado** (`openai==3.16.2`), leída de las firmas de `Images.generate/edit` y `Responses.parse` | 2026-11-17 |
| Desarrollo | Cualquier lectura directa de la documentación del proveedor | — *imposible aquí* |
| Nadie todavía | La **disponibilidad de los modelos en la cuenta «Viralgen VPS»** | — *solo la demuestra una llamada real* |

El revisor advierte además que no ejecutó las pruebas ni hizo llamadas en la
cuenta. Esa ruta completa está registrada en
`model_checks.EVIDENCE_ROUTE_2026_11_18` y viaja con **cada** evidencia suya,
no solo en este documento.

Tres distinciones que el registro protege en código, con pruebas que fallan si
alguien las borra:

1. Que el **SDK** acepte un parámetro no significa que el **modelo** lo admita.
   `Images.edit` acepta `input_fidelity`; la guía de 2.6 dice omitirlo.
2. Que la **documentación** lo describa no significa que **nuestra clave**
   tenga acceso.
3. Una **consulta sin extracto** no verifica nada: queda como nota fechada.

## Estado actual del registro

```
total: 18   verificadas: 11   parciales: 3   pendientes: 4
bloqueantes por falta de verificación: 0
```

```bash
python -m viralgen media models
python -c "import json; from viralgen.media import model_checks; \
print(json.dumps(model_checks.describe_all(), indent=2, ensure_ascii=False))"
```

### Qué cerró la evidencia del 2026-11-18

| Entrada | Antes | Ahora | Lo que dice la fuente |
|---|---|---|---|
| `gpt_image_1_retired` | *(no existía)* | **verificada** | «was shut down on October 23, 2026 and is no longer available» |
| `sunburst_endpoint` | pendiente, bloqueante | **verificada** | Generación y edición, con el ID exacto; «Specify a model explicitly.» |
| `sunburst_sizes` | pendiente, bloqueante | **verificada** | Lados múltiplos de 16, proporción 1:3–3:1, lado máx. 3840 px, 655 360–8 294 400 px |
| `sunburst_qualities` | pendiente, bloqueante | **parcial** | Consta `medium`; la lista completa no |
| `sunburst_edit_references` | pendiente, bloqueante | **parcial** | «you can provide up to 16 images» por petición; el límite de bytes no es atribuible a 2.6 |
| `sunburst_input_fidelity` | pendiente | **verificada** | «Omit `input_fidelity` on image edits.» |
| `sunburst_response_shape` | pendiente, bloqueante | **verificada** | La imagen en `data[].b64_json`; `response_format` es heredado y no admitido |
| `sunburst_usage_schema` | *(no existía)* | **verificada** | `usage` opcional, con `input_tokens_details.{text,image}_tokens` |
| `sunburst_token_consumption` | pendiente | **verificada** | 5 / 8 / 30 USD por millón de tokens (texto entrada / imagen entrada / imagen salida) |
| `terra_structured_outputs` | pendiente, bloqueante | **verificada** | La ficha incluye Responses y Structured Outputs |
| `terra_reasoning_and_output_budget` | pendiente | **parcial** | `max_output_tokens` incluye los tokens de razonamiento |
| `deprecations_current_models` | pendiente | **verificada** | Vía la ficha de gpt-image-1 |

### Qué sigue sin cerrar

| Entrada | Estado | Qué falta exactamente |
|---|---|---|
| `sunburst_qualities` | parcial | La lista completa de valores de `quality`. No molesta: el proyecto solo declara y envía `medium`. |
| `sunburst_edit_references` | parcial | El límite de **bytes** por imagen de referencia **para 2.6**. La referencia Python lo enuncia para modelos anteriores; atribuirlo por analogía sería inventarlo. |
| `terra_reasoning_and_output_budget` | parcial | Si **6000** tokens de salida bastan para un cuento de ~50 s. Solo lo dice una ejecución real. |
| `terra_temperature` | pendiente | Si acepta `temperature`. La evidencia del 18 no lo menciona. Mientras no conste, **no se envía**. |
| `terra_pricing` | pendiente | Sus tarifas por millón de tokens. La evidencia aporta las de Sunburst, no las de Terra. |
| `sunburst_account_availability` | pendiente | Que el modelo esté disponible para nuestra clave. |
| `terra_account_availability` | pendiente | Lo mismo para el modelo de guion. |

### Por qué la disponibilidad en la cuenta ya no bloquea

Hasta el 2026-11-18 esas dos entradas estaban marcadas como bloqueantes. Era un
bucle: la única prueba posible es una llamada real, y un bloqueo que impide esa
llamada no se levanta nunca. Ahora quedan como **pendientes declaradas** con
`blocks_real=False`, y lo que gobierna la primera llamada real es el
**presupuesto acordado del piloto** —una generación y una edición de prueba
antes de las diez imágenes del cuento—, no una puerta de código que se
contradice a sí misma.

Lo que sí bloquea es lo que se puede comprobar sin gastar nada: un modelo
**retirado** y un protocolo **sin verificar**.

## Consecuencias en el código

| Decisión | Dónde vive |
|---|---|
| `gpt-image-1` se sigue resolviendo, pero no acepta peticiones nuevas | `capabilities.assert_verified_for_real`, código `modelo_de_imagen_retirado` |
| La geometría se valida contra la **regla** documentada, no contra una lista inventada | `capabilities.SizeRules`, `GPT_IMAGE_SIZE_RULES` |
| `720x1280` es 9:16 **nativo** (45×16, 80×16, 921 600 px) y es el tamaño del piloto | primera entrada de `sizes` en el catálogo |
| `input_fidelity` se **omite**; configurarlo es un error | `forbids_input_fidelity`, código `input_fidelity_prohibida` |
| `response_format` no se envía nunca | `openai_images._call` |
| El consumo se guarda por componentes, y ausente no es cero | `openai_images._read`, `pipeline._accumulate_usage` |
| Factura **por tokens**: una tabla por (tamaño, calidad) no aplica | `bills_by_tokens`, `budget.TokenRates` |
| El límite local de referencias (4) es más estricto que el publicado (16) | `max_reference_images` vs `documented_max_reference_images` |

## Cómo se incorpora un extracto nuevo

```python
PendingCheck(
    check_id="terra_pricing",
    ...
    status=CheckStatus.VERIFIED,          # o PARTIAL si no cierra la condicion
    evidence=(
        _revisor(
            states="La ficha da X USD de entrada y Y de salida por millon.",
            scope="Cubre las tarifas; no cubre el acceso de nuestra clave.",
            tags=("O3",),
        ),
    ),
)
```

Si un extracto **contradice** un supuesto, el valor del catálogo se corrige y se
registra igual. Ya pasó una vez: se había supuesto que `input_fidelity` ayudaría
con la consistencia de personajes, y la guía dice omitirlo. Un supuesto
desmentido no se borra en silencio.

## Fuera de OpenAI: la voz

No es un modelo de OpenAI y por eso no está en este registro, pero condiciona el
piloto. Según la evidencia del revisor (2026-11-18):

- Las condiciones del EEE limitan el plan **Free** de ElevenLabs a usos **no
  comerciales**.
- El contenido generado **fuera** de una suscripción de pago **no adquiere**
  licencia comercial al contratarla después.

Consecuencia práctica: si el vídeo se va a reutilizar comercialmente, su voz
tiene que generarse **durante** un plan que lo permita. Una prueba puramente
técnica puede seguir con voz simulada.
