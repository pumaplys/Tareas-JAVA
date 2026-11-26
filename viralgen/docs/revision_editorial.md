# Revisión editorial local de un `script.json`

Un guion puede quedar en `needs_review` por un aviso editorial y necesitar tres
cambios de texto. Volver a llamar al proveedor para eso cuesta dinero y
devuelve **otro** guion, no el mismo con una frase arreglada. `viralgen revise`
aplica los cambios que decide una persona, **sin red y sin proveedores**, y
recalcula con las funciones del proyecto lo que depende de ellos.

## Qué recalcula, y con qué

Nada se copia a mano:

| Qué | Con qué |
|---|---|
| `word_count` de cada escena | `textutil.count_words` |
| `estimated_start_s`, `estimated_end_s`, `estimated_duration_s` de **todas** las escenas | `timing.build_timeline` |
| `narration.full_text` y `narration.word_count` | concatenación en orden + `count_words` |
| `video.estimated_duration_s` | final de la línea temporal |
| `control.warnings` y `control.production_status` | `validation.validate_document` + `assembly.apply_validation_result` |

Se recalcula la línea temporal **entera**, no sólo las escenas editadas:
acortar la escena 2 adelanta el inicio de las cinco siguientes.

El último punto es el que importa: **el estado no se escribe a mano**. Si tras
la revisión siguen quedando avisos, el documento sigue en `needs_review`, y eso
es información. Si los avisos desaparecen, es porque el contenido dejó de
incumplir.

## Qué no puede tocar

Sólo se admiten campos **editoriales**:

```
narration_text   captions.emphasis_words   captions.overlay_text
```

Cualquier otro se rechaza antes de abrir nada, y los que alguien podría querer
tocar para forzar una admisión se rechazan **con su motivo**:

| Campo | Por qué no |
|---|---|
| `control.production_status` | el estado se deriva de la validación, no se escribe |
| `control.warnings` | los calcula la validación; borrarlos no resuelve lo que avisan |
| `simulation` | el modo de origen de un documento no se reescribe |
| `provenance` | describe la generación inicial y una revisión no la altera |
| `narration.word_count`, `video.estimated_duration_s` | se recalculan |

## El original se conserva

| Qué | Dónde queda |
|---|---|
| `script.json` original | **intacto**, byte a byte. Se relee después de escribir la revisión y se compara el SHA-256 |
| Fila de `scripts` en SQLite | **intacta**: sigue siendo el original |
| Fila de `exports` | **intacta** |
| La revisión | archivo nuevo `script.revision-N.json`, **al lado** del original |
| El registro de la revisión | tabla propia `script_revisions`, con su ruta y su SHA-256 |

El documento revisado lleva un bloque `editorial_revision` que lo identifica
como tal y lo enlaza al original:

```json
"editorial_revision": {
  "revision_no": 1,
  "revision_of_sha256": "<SHA-256 de los BYTES del script.json original>",
  "revision_of_content_sha256": "<el que guarda la tabla exports>",
  "revised_at": "...",
  "revised_by": "operador_local",
  "tool": "viralgen revise",
  "provider_calls": 0,
  "edits_sha256": "<identidad del conjunto de ediciones>",
  "changes": [{"scene_id": "...", "field": "...", "before_sha256": "...", "after_sha256": "..."}]
}
```

`provider_calls` es `0` **por contrato** (`Literal[0]`): un documento revisado
no puede presentar una llamada nueva como parte de la revisión. Y `provenance`
sigue describiendo la generación inicial, sin tocar: lo que escribió el
proveedor y lo que cambió una persona quedan separados.

El campo es **opcional**: un `script.json` generado antes de este cambio sigue
validando, y `schema_version` sigue siendo `1.0`.

## Repetir no crea otra revisión

La identidad de una revisión es **el conjunto de ediciones más el original
sobre el que se aplican** (`UNIQUE(job_id, edits_sha256, revision_of_sha256)`).
Repetir la misma orden responde `reused: true`, no crea una segunda fila y no
reescribe el archivo.

## Dónde se detiene

- El `--job-key` no existe en ese `DATA_DIR`.
- El trabajo no tiene guion en SQLite o no tiene exportación completa.
- **El archivo no coincide con el hash registrado en SQLite**: no se revisa un
  original dudoso.
- No hay idea **seleccionada**, hay más de una, o la seleccionada no tiene
  promesa guardada: la coherencia editorial se valida contra la promesa real,
  no contra una cadena vacía.
- Una edición no cambia nada (el valor ya es el pedido).
- El documento de `scripts` ya es una revisión.

## El archivo de ediciones

Son **datos**, no código, para que la operación sea auditable y repetible:

```json
{
  "note": "Revision editorial del operador: tres cambios de texto.",
  "edits": [
    {"scene_id": "escena_2", "field": "narration_text", "value": "..."},
    {"scene_id": "escena_7", "field": "narration_text", "value": "..."},
    {"scene_id": "escena_7", "field": "captions.emphasis_words", "value": ["mochila", "maraca"]}
  ]
}
```

El del caso del puente está en
[`examples/revision_nubaria_001.json`](../examples/revision_nubaria_001.json).

## Órdenes exactas

### 1. Previsualizar — no escribe nada

```bash
cd /opt/viralgen-repo/viralgen && . .venv/bin/activate
VIRALGEN_DATA_DIR=/root/viralgen-datos \
VIRALGEN_PROFILES_PATH=examples/perfiles_solo_imagenes.json \
  python -m viralgen revise \
  --job-key nubaria-001-puente-con-hipo \
  --edits examples/revision_nubaria_001.json
```

Imprime el antes y el después, los tres cambios, las incidencias que quedarían
y la admisión para voz **proyectada** (`admission_for_voice_if_exported`: se
llama así porque en previsualización no hay archivo todavía, y no se afirma que
esté exportado lo que no se ha escrito). No toca el disco ni SQLite.

Para el caso del puente debe imprimir:

```
original: 109 palabras, 56.12 s, needs_review
revisado: 101 palabras, 52.28 s, ready_for_production
delta:    -8 palabras, -3.84 s
escenas:  7 de imagen, 0 de vídeo
incidencias: 0          llamadas al proveedor: 0
```

### 2. Aplicar — escribe la revisión

```bash
cd /opt/viralgen-repo/viralgen && . .venv/bin/activate
VIRALGEN_DATA_DIR=/root/viralgen-datos \
VIRALGEN_PROFILES_PATH=examples/perfiles_solo_imagenes.json \
  python -m viralgen revise \
  --job-key nubaria-001-puente-con-hipo \
  --edits examples/revision_nubaria_001.json \
  --apply
```

Escribe `script.revision-1.json` junto al original, lo relee para no dar por
exportado lo que no se puede volver a leer, y guarda la fila en
`script_revisions`. El resumen incluye `original_untouched.unchanged`, que se
comprueba releyendo el original después de escribir.

### Si el trabajo no aparece

`revise` usa el espacio de datos de **producción**. Si el guion se generó con
`--mock`, vive en el subdirectorio `simulation/`, así que hay que apuntar ahí:

```bash
VIRALGEN_DATA_DIR=/root/viralgen-datos/simulation ...
```

## Códigos de salida

| Código | Significado |
|---|---|
| `0` | el documento revisado no tiene incidencias |
| `10` | la revisión se calculó (o se aplicó) pero **siguen quedando avisos**: no se fuerza un cero |
| `3` | error de configuración: campo no editable, trabajo inexistente, original que no coincide con SQLite… |

## Lo que esto no hace

No genera voz, ni imágenes, ni publicaciones, y no autoriza nada. Que un
documento quede `ready_for_production` significa que **puede** pasar a
producir medios, no que deba. La admisión para voz se informa con el criterio
real del proyecto (`validation.check_admission`, cinco condiciones a la vez),
sin sintetizar audio.
