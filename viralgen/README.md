# viralgen — Módulo 1: generador de ideas, guiones y prompts de medios

Este módulo produce **un único artefacto**: un documento JSON validado
(`script.json`) que describe el plan completo de un vídeo vertical 9:16 —idea,
guion, escenas, prompts de imagen y movimiento, indicaciones de voz,
subtítulos, audio, loop, evidencia y borrador de publicación— listo para que lo
consuman los módulos 2‑5 **sin tener que interpretar texto libre**.

## Qué hace y qué no hace

Hace:

- Propone ideas, las filtra, detecta duplicados y puntúa candidatas.
- Desarrolla el guion completo de la idea elegida.
- Calcula localmente palabras, duraciones y tiempos acumulados.
- Valida integridad y criterios editoriales.
- Persiste todo en SQLite y exporta un `script.json` atómico.

**No** hace: no genera audio ni imágenes, no ejecuta FFmpeg, no monta vídeo, no
publica nada, no descarga analíticas y no consulta buscadores. Tampoco instala
ni despliega nada en una VPS.

`production_status=ready_for_production` significa **solo** que el plan puede
pasar a producir medios. No autoriza publicaciones, no certifica originalidad y
no dice nada sobre elegibilidad para programas de monetización.

---

## 1. Instalación en Ubuntu

Requiere Python 3.12 o 3.13.

```bash
sudo apt update && sudo apt install -y python3.12 python3.12-venv
cd viralgen
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"          # o: pip install -r requirements.lock.txt && pip install -e .
viralgen --version
```

El paquete ocupa unos pocos MB; las dependencias instaladas rondan los 60 MB, de
sobra dentro de los 10 GB de la VPS. Los datos generados (SQLite + JSON + logs)
son texto: un trabajo completo ocupa del orden de 30‑60 KB.

### Versiones fijadas y cómo se verificaron

`pyproject.toml` fija rangos compatibles (`pydantic>=2.13,<3`,
`pydantic-settings>=2.15,<3`, `openai>=3.16,<4`, `pytest>=9.1,<10`) y
`requirements.lock.txt` recoge el conjunto exacto resuelto.

Verificación realizada (no son versiones inventadas): se creó un entorno virtual
limpio de Python 3.12, se instalaron las dependencias sin fijar versión, se
anotó lo que resolvió `pip` (`openai 3.16.2`, `pydantic 2.13.5`,
`pydantic-settings 2.15.0`, `pytest 9.1.1`), se comprobó por introspección que
`openai.resources.responses.Responses` expone `parse(...)` con el parámetro
`text_format` —que es el método de Structured Outputs compatible con esa versión
del SDK— y se ejecutó la batería de pruebas completa contra ese entorno.

---

## 2. Configuración

Copia `.env.example` a `.env` y ajusta lo que necesites. Todas las variables
llevan prefijo `VIRALGEN_` salvo las del proveedor.

| Variable | Por defecto | Para qué sirve |
| --- | --- | --- |
| `OPENAI_API_KEY` | *(vacío)* | Clave real. **Solo obligatoria sin `--mock`.** |
| `OPENAI_MODEL` | *(vacío)* | Identificador exacto del modelo. **Solo obligatorio sin `--mock`.** |
| `VIRALGEN_DATA_DIR` | `./.viralgen` | Raíz de SQLite, exportaciones y logs. |
| `VIRALGEN_LOG_LEVEL` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR`. |
| `VIRALGEN_REQUEST_TIMEOUT_SECONDS` | `60` | Tiempo máximo por petición. |
| `VIRALGEN_MAX_CALLS_PER_JOB` | `6` | Tope duro de peticiones reales por trabajo. |
| `VIRALGEN_MAX_TRANSPORT_RETRIES` | `2` | Reintentos de transporte (además del intento inicial). |
| `VIRALGEN_MAX_OUTPUT_TOKENS` | `6000` | Límite de salida por petición. |
| `VIRALGEN_MIN_FREE_DISK_MB` | `1500` | Umbral de disco antes de empezar y antes de exportar. |
| `VIRALGEN_DUPLICATE_SIMILARITY_THRESHOLD` | `0.80` | Umbral de Jaccard para marcar duplicado. |
| `VIRALGEN_HISTORY_LOOKBACK_DAYS` | `90` | Ventana local de búsqueda de duplicados. |
| `VIRALGEN_HISTORY_MAX_ITEMS` | `30` | Resúmenes de historial enviados al proveedor. |
| `VIRALGEN_IDEAS_PER_BATCH` | `5` | Ideas por tanda. |
| `VIRALGEN_SEND_TEMPERATURE` | `false` | Si es `false`, **nunca** se envía `temperature`. |
| `VIRALGEN_PRICE_INPUT_PER_1M_USD` / `..._OUTPUT_...` | *(vacío)* | Tarifas explícitas. Sin ellas, `estimated_cost_usd` es `null`. |
| `VIRALGEN_PROFILES_PATH` / `VIRALGEN_SERIES_BIBLE_PATH` | *(vacío)* | Perfiles y biblia visual propios. |

**No hay modelo por defecto a propósito.** El proyecto no inventa nombres de
modelo ni cambia uno por otro en silencio: si falta `OPENAI_MODEL` en modo real,
el comando termina con un error de configuración que lo explica.

Tampoco se registran nunca claves, cabeceras de autorización ni volcados
completos del entorno: el logger filtra esos patrones antes de escribir.

---

## 3. Uso

### Simulación (sin claves y sin red)

```bash
viralgen generate --profile infantil_cuentos --topic "aprender a compartir" --mock --job-key demo-infantil-001
viralgen ideas    --profile infantil_cuentos --topic "aprender a compartir" --mock
viralgen generate --profile curiosidades_largo --source-pack facts.json --mock --job-key demo-curi-001
```

La simulación escribe en un **espacio de datos separado**
(`DATA_DIR/simulation/`) para no contaminar el historial real, marca
`simulation=true` en el documento y solo acepta catálogos con `demo_only=true`.
Sus resultados **no** son una integración real ni miden la calidad del modelo.

### Ejecución real

```bash
export OPENAI_API_KEY="..."
export OPENAI_MODEL="<modelo que admita Structured Outputs>"
viralgen generate --profile curiosidades_largo --source-pack facts.json --job-key curiosidad-001
```

### Otros comandos

```bash
viralgen validate --input ruta/script.json      # revalida un documento existente
viralgen schema   --output ruta/script.schema.json
viralgen profiles                                # lista los perfiles disponibles
```

`--duration` acepta cualquier valor dentro del rango del perfil. `--seed` hace
determinista el proveedor simulado; **no promete determinismo con el proveedor
real**.

### Salidas

- **stdout**: exclusivamente un resumen JSON (`job_id`, `status`,
  `production_status`, rutas, avisos, llamadas usadas, `exit_code`). Es lo que
  deben leer los scripts.
- **stderr**: mensajes para personas.
- **archivos**: `DATA_DIR[/simulation]/jobs/<job_id>/script.json` (o
  `ideas.json`), escritos con archivo temporal y reemplazo atómico. Nunca se
  aceptan rutas propuestas por el modelo.
- **logs**: `DATA_DIR[/simulation]/logs/viralgen.log`, rotativo a 5 MB con tres
  copias.

---

### El esquema que se envía realmente al proveedor

El proveedor real usa `client.responses.parse(text_format=<modelo Pydantic>)`.
El SDK deriva de ese modelo un `json_schema` estricto: pone
`additionalProperties: false` en cada objeto, mete **todas** las propiedades en
`required` (los campos opcionales viajan como unión con `null`, no se omiten) y
elimina los `default`.

**El esquema que enviamos es deliberadamente conservador**: no lleva
restricciones de cadena (`minLength`, `maxLength`, `pattern`, `format`) ni
valores por defecto. Esto **no** quiere decir que la API las prohíba todas: la
[documentación oficial](https://developers.openai.com/api/docs/guides/structured-outputs)
admite `pattern` y un conjunto concreto de valores de `format`, y describe
restricciones adicionales para modelos *fine-tuned*. Se omiten por decisión
propia, por dos motivos:

1. el conjunto admitido depende del modelo y de la versión de la API, y un
   esquema mínimo reduce el riesgo de rechazo;
2. esos límites se aplican igualmente **en local** al construir el documento
   final (`schemas/document.py`), que es donde importan para el contrato.

La decisión está fijada por pruebas (`tests/test_schemas.py` y
`tests/test_openai_payload.py`), de modo que ampliar el esquema sea deliberado.

`tests/test_openai_payload.py` instancia el **SDK real** con un transporte HTTP
simulado (`httpx2.MockTransport`) y captura la solicitud: comprueba el endpoint,
`text.format.type=json_schema`, `strict=true`, `required`,
`additionalProperties`, el tratamiento de los campos *nullable*, la ausencia de
`default` y que no se envía `temperature`. **Esa prueba no demuestra que el
servidor acepte el esquema**: solo verifica lo que sale de esta máquina. La
confirmación definitiva requiere una llamada real (ver §14).

## 4. Perfiles editoriales

Se definen en `src/viralgen/data/profiles.json` y se pueden editar sin tocar
código (o sustituir con `VIRALGEN_PROFILES_PATH`).

| Perfil | Canal y audiencia | Duración objetivo | Ritmo | Escenas |
| --- | --- | --- | --- | --- |
| `infantil_cuentos` | YouTube Shorts; niños de 5‑8 años | 50 s (40‑60) | 125 ppm | 6‑9 |
| `curiosidades_corto` | Reels y TikTok; público general | 35 s (25‑45) | 155 ppm | 5‑8 |
| `curiosidades_largo` | Reels y TikTok; público general | 70 s (65‑90) | 155 ppm | 9‑12 |

Los tres usan composición vertical 9:16, 1080×1920 y 30 fps, narración y
metadatos en español y prompts visuales en inglés (configurable). Cada perfil
tiene además un **presupuesto de escenas de vídeo** (`video_scene_budget`) para
acotar el coste futuro del módulo 3; el validador comprueba que el plan lo
respeta.

Estos rangos son decisiones **de este proyecto**, no límites de las
plataformas. El perfil largo permite planificar contenido de más de un minuto,
pero **no certifica monetización**: Creator Rewards exige contenido original y
otros requisitos de cuenta y región
(<https://newsroom.tiktok.com/en-us/introducing-the-new-creator-rewards-program>).
Por eso no existe ningún campo `monetizable`.

La dirección creativa infantil sigue las orientaciones de calidad para público
infantil de YouTube
(<https://support.google.com/youtube/answer/10774223?hl=es>): historia con
desenlace, sin sustos intensos ni conductas peligrosas imitables, sin
personajes de franquicias y sin llamadas a comentar o comprar.

### Biblia visual

`src/viralgen/data/series_bible.json` define el estilo, la paleta, el
`negative_prompt` y los personajes estables de cada serie. **La aplicación
inserta esa biblia en el documento**: el modelo no puede reinventar al
protagonista entre episodios. Los prompts por escena piden explícitamente
ausencia de texto y marcas de agua; los rótulos se añaden en montaje (módulo 4).

**Continuidad autocontenida en cada `image_prompt`.** Insertar la biblia solo
en `visual_bible` no garantiza que el generador de imágenes la use: el módulo 3
trabaja escena a escena. Por eso:

1. la biblia relevante (estilo, paleta, `negative_prompt` y fichas de
   personaje) viaja al proveedor en el bloque de datos de la etapa de guion
   (`Pipeline._request_script`, claves `series_bible` y `characters`);
2. al ensamblar, la aplicación **copia el aspecto y la ropa** de cada personaje
   referenciado en `character_ids` al final del `image_prompt` de esa escena
   (`assembly.compose_image_prompt`), sin duplicar lo que el modelo ya escribió;
3. el validador `prompt_sin_continuidad` (fatal) comprueba que cada personaje
   referenciado está efectivamente descrito en el prompt de su escena.

Consecuencia práctica: `description` y `wardrobe` alimentan directamente los
prompts, así que conviene redactarlos en el idioma de `visual_prompt_language`
(en los datos empaquetados, inglés). El resto de la biblia y toda la narración
siguen en español.

### Cambiar de perfil o de modelo

Cambiar `--profile`, `OPENAI_MODEL`, los perfiles o la biblia cambia el
`config_hash` y el *fingerprint* de idempotencia, de modo que un trabajo nuevo
no se confunde con uno anterior. El documento guarda `prompt_version`,
`config_hash`, `source_pack_hash`, `provider`, `model` y un `experiment_tag`
para poder comparar tandas más adelante.

---

## 5. Evidencia para las curiosidades

El catálogo de hechos revisados se aporta con `--source-pack`. No hay buscador
ni scraper en esta versión; el archivo se reutiliza y se amplía a mano.

```json
{
  "schema_version": "1.0",
  "pack_id": "mi_catalogo",
  "facts": [
    {
      "fact_id": "cremallera_dientes",
      "claim_text": "Afirmación exacta que respalda la fuente.",
      "source_url": "https://…",
      "source_title": "Título de la fuente",
      "evidence_excerpt": "Fragmento literal que sostiene la afirmación.",
      "checked_at": "2026-01-15T09:00:00Z",
      "review_status": "approved",
      "demo_only": false
    }
  ]
}
```

Se valida que los `fact_id` sean únicos, que las fechas lleven zona horaria UTC
y que las URLs sean http/https. Se guarda además un `source_pack_hash`
(SHA‑256 del contenido canónico, ordenado por `fact_id`) en el documento.

Reglas:

- En modo real solo se usan hechos con `review_status=approved` **y**
  `demo_only=false`. Un catálogo de prueba (`demo_only=true`) solo se acepta en
  simulación.
- La aprobación viene del catálogo importado, **nunca** de una decisión del LLM.
- Los campos de procedencia (`source_url`, `checked_at`, …) se **copian** del
  catálogo; al modelo solo se le envían `fact_id`, `claim_text`,
  `evidence_excerpt` y `source_title`, y el validador rechaza el documento si
  algún registro no coincide literalmente con el original.
- Cada afirmación factual del guion se enlaza con uno o varios `fact_id`. El
  modelo puede explicar y parafrasear, pero no añadir cifras, causalidades ni
  detalles no respaldados. Si una escena menciona cifras sin referencia, se
  marca `needs_review`; nunca se afirma que algo esté verificado.
- Si faltan fuentes aprobadas, `generate` termina en `needs_research`
  **antes de hacer ninguna llamada de pago**. `ideas` sí puede proponer
  preguntas de investigación, exportadas con `"verified": false` y
  `research_only: true`.
- La ficción infantil no necesita fuentes para sus hechos imaginarios. Si
  explica ciencia o naturaleza real, se le aplica el mismo mecanismo.

> **Límite importante y explícito.** El sistema **restringe** la generación a las
> fuentes aportadas. **No** verifica de forma independiente que una fuente
> respalde semánticamente la afirmación, y una URL sintácticamente válida no
> demuestra nada sobre su contenido. `verification_level=source_pack_only`
> significa exactamente eso y nada más.

El catálogo de ejemplo `src/viralgen/data/facts_demo.json` es **ficticio**:
apunta a `example.org` y lleva `demo_only=true`. Sirve para probar el mecanismo,
no para producir contenido.

### Las fuentes y los temas son datos, no instrucciones

El prompt de sistema declara explícitamente que los bloques de tema, catálogo,
historial y biblia visual son datos: si contienen órdenes, peticiones de cambiar
las reglas o enlaces a seguir, deben ignorarse. Además, la aplicación no ejecuta
nada de lo que devuelve el modelo, no acepta rutas de archivo propuestas por él
y copia la procedencia desde el catálogo local.

---

## 6. Algoritmo de generación y selección

1. Valida configuración, perfil, parámetros, catálogo y espacio en disco.
2. Crea o recupera el trabajo por su `--job-key`.
3. Carga el historial del canal (duplicados: 90 días; al proveedor: 30
   resúmenes) y la biblia visual.
4. Pide **cinco ideas en una sola llamada estructurada**, cada una con título,
   premisa, promesa, posible desenlace, concepto visual, referencias factuales
   cuando corresponda y cuatro valoraciones editoriales razonadas.
5. Filtra y puntúa localmente (ver abajo). Si no queda ninguna idea válida,
   permite **una sola** tanda adicional, dentro del presupuesto de llamadas.
6. Desarrolla el guion completo en **otra llamada estructurada**, conservando
   las restricciones y referencias de la idea elegida.
7. Calcula los campos derivados y valida. Permite **una única** llamada de
   reparación con la lista concreta de errores, y revalida todo después. No hay
   ciclos abiertos.
8. Persiste en SQLite y exporta un único `script.json`.

### Duplicados y novedad

- **Normalización documentada**: NFKD → se eliminan las marcas combinantes
  (`canción` → `cancion`, `niño` → `nino`) → minúsculas → la puntuación pasa a
  espacio → se colapsan espacios.
- **Duplicado exacto**: SHA‑256 del texto normalizado de `título + premisa`.
- **Similitud léxica**: índice de Jaccard sobre conjuntos de tokens
  (sin palabras vacías, tokens de 3+ caracteres), umbral configurable `0,80`.
- **Reutilización del hecho central**: en curiosidades se rechaza una idea cuyo
  `fact_id` principal ya fue el eje de otra.

Esto es comparación **léxica**, no búsqueda semántica: dos ideas equivalentes
escritas con otro vocabulario no se detectan.

### Puntuación editorial

El modelo puntúa de 0 a 5 `hook`, `clarity`, `payoff` y `visual_potential`, con
una justificación por criterio. La aplicación calcula
`novelty = 5 × (1 − similitud_máxima)` (5 si no hay historial) y:

```
score = 20 × (0,25·hook + 0,20·clarity + 0,20·payoff + 0,15·visual_potential + 0,20·novelty)
```

Se guardan los cinco componentes y una justificación breve. **No es una
probabilidad de viralidad**: es una heurística editorial para ordenar
candidatas. Gana la idea válida con mayor puntuación; los empates se resuelven
de forma determinista (gancho, novedad, título normalizado, `idea_ref`).

### Conteo de palabras y duraciones

`count_words` está documentada y probada para español: normaliza a NFC; una
palabra es una secuencia alfanumérica Unicode; el apóstrofo y el guion interior
no la parten (`anti-gravedad` = 1); un número con separadores interiores cuenta
como una (`2.500` = 1); la puntuación no cuenta.

Por escena, con la pausa **al final**:

```
estimated_duration_s = 60 × word_count / target_wpm + pause_after_s
```

Los tiempos acumulados se construyen localmente desde 0, sin huecos ni
solapamientos. **Los cálculos del LLM no son autoridad**: si no coinciden con el
cálculo local, el documento se rechaza.

### Validaciones

Se distinguen dos niveles:

- **`fatal`** — integridad rota: referencias que no resuelven, hechos no
  aprobados o alterados, número de escenas fuera del perfil, presupuesto de
  vídeo superado, tiempos o conteos manipulados, metadatos que no corresponden
  al canal, `made_for_kids` incorrecto, loop incoherente, narración que no
  coincide con las escenas. El trabajo termina en `failed` y **no se exporta
  nada**.
- **`warning`** — el plan es íntegro pero requiere criterio humano: duración
  fuera de ±10 % del objetivo o del rango del perfil, escena de duración
  implausible, gancho de más de ~3 s o que no aparece al principio de la primera
  escena, beats mal repartidos, cierre que no parece responder a la promesa,
  palabras destacadas ausentes, cifras sin respaldo. Se exporta con
  `production_status=needs_review` y el motivo en `control.warnings`.

Las comprobaciones deterministas están separadas de las valoraciones
editoriales, que son heurísticas de apoyo y **no garantías automáticas**.

Ante exceso o defecto de duración se pide **ajustar el texto** en la única
reparación disponible; nunca se acelera la voz.

---

## 7. Contrato JSON para los módulos 2‑5

`schema/script.schema.json` contiene el JSON Schema exportable
(`viralgen schema --output …`). Los modelos Pydantic prohíben campos
adicionales, usan enums, límites de longitud y colecciones acotadas.

Se separan explícitamente **lo que responde el proveedor** (textos) de **lo que
calcula o incorpora la aplicación** (identidad, biblia visual, duraciones,
conteos, hashes, procedencia de las fuentes, metadatos obligatorios de
publicación y uso de tokens).

Grupos del documento:

| Grupo | Contenido |
| --- | --- |
| Identidad | `schema_version="1.0"`, `job_id` (UUID), `created_at` (UTC), `channel`, `profile_id`, `language`, `target_platforms`, `simulation`. |
| Control | `production_status`, `warnings`, `prompt_version`, `config_hash`, `source_pack_hash` (nullable). |
| Idea | `title`, `premise`, `topic`, `selected_idea_id`, `editorial_score` y componentes, `hook_variants` (tres, identificadas), `selected_hook_id`, `educational_goal` (nullable), `experiment_tag`. |
| Vídeo | `width`, `height`, `fps`, `aspect_ratio`, `target_duration_s`, `estimated_duration_s`, `actual_duration_s=null`. |
| Narración | `full_text` (concatenación en orden de las escenas, la construye la app), `word_count`, `target_wpm`, `voice_direction`, `pronunciation_notes`. |
| Biblia visual | `style_prompt`, `color_palette`, `negative_prompt`, `characters` con `character_id`, aspecto y ropa estables. |
| Escenas | `scene_id`, `order`, `beat`, `narration_text`, `pause_after_s`, `word_count`, `estimated_start_s/end_s/duration_s`, `character_ids`, `visual`, `captions`, `audio`, `claim_refs`. |
| `visual` | `asset_type` (`image`/`video`), `image_prompt`, `motion_prompt`, `continuity_notes`. |
| `captions` | `emphasis_words`, `overlay_text` opcional. Sin timestamps de palabra ni SRT/ASS. |
| `audio` | `sfx_description` opcional, `sfx_cue` (`scene_start`/`scene_end`), `music_mood` opcional. |
| Loop | `enabled`, `opening_scene_id`, `closing_scene_id`, `connection_explanation` (null cuando no aplica). |
| Evidencia | `claims` (con `claim_id`, `claim_text`, `scene_ids`, `fact_ids`), `facts` realmente usados, `verification_level`. |
| Publicación futura | Un elemento por plataforma: `platform`, `title`, `caption`, `hashtags`, `made_for_kids` (nullable; `true` en infantil/YouTube), `ai_disclosure_review_required=true`. |
| Procedencia técnica | `provider`, `model`, `request_ids`, `input_tokens`, `output_tokens`, `estimated_cost_usd` (nullable). |

Notas para quien consuma el contrato:

- `actual_duration_s` lo rellenará el **módulo 2** tras medir la voz real.
  El **módulo 4** ajustará escenas y subtítulos a esas alineaciones.
- Los subtítulos completos salen de `narration_text`, sin resumirla ni
  cambiarla. Este módulo no genera timestamps de palabra.
- Los tiempos finales de los efectos dependen del audio real.
- El **módulo 5** decidirá los campos definitivos de publicación a partir de los
  medios terminados y los requisitos vigentes. Aquí no se fijan horarios, ni
  privacidad de TikTok, ni promesas de ingresos.
- Si el perfil largo queda por debajo de su mínimo tras generar audio, los
  módulos posteriores deben corregirlo o devolver el trabajo; la duración
  estimada no lo etiqueta como elegible para nada.
`needs_research` y `failed` son estados **del trabajo**, no del documento: no se
fabrica un `script.json` incompleto para representarlos.

### Criterio de admisión para consumidores reales

Un documento con avisos **se conserva** como `needs_review` —es material
recuperable, no basura— pero **nunca queda habilitado para generar medios de
forma automática**. Para que un consumidor real (módulo 2 en adelante) pueda
tomar un `script.json`, deben cumplirse **las cinco condiciones a la vez**:

| # | Condición | Cómo se comprueba |
| --- | --- | --- |
| 1 | Documento válido | valida contra `ScriptDocument` y no queda ningún problema `fatal` |
| 2 | Versión de esquema compatible | `schema_version` ∈ `SUPPORTED_SCHEMA_VERSIONS` (hoy: `1.0`) |
| 3 | Exportación completa | el archivo se escribió entero (temporal + reemplazo atómico) y se relee sin error |
| 4 | `production_status == ready_for_production` | — |
| 5 | `simulation == false` | una salida simulada jamás alimenta producción |

El criterio está **escrito una sola vez y es ejecutable**:
`viralgen.validation.check_admission(...)`. Se expone en dos sitios:

- el resumen JSON de `generate` lleva `admissible_for_media` y
  `admission_reasons`;
- `viralgen validate --input …` añade `admissible_for_media`, `checks` y
  `reasons`.

Esto **no** implementa el módulo 2: solo deja el criterio en un único lugar para
que quien conecte voz lo consulte en vez de reinventarlo.

Basta que falle una condición para rechazar el documento. En particular, **todo
lo generado con `--mock` es inadmisible por definición** (condición 5), incluso
cuando sale `ready_for_production`.

---

## 8. Persistencia, reanudación y disco

SQLite (`DATA_DIR[/simulation]/viralgen.sqlite3`) es la fuente de verdad:
trabajos, candidatos, guiones, historial de ideas, uso del proveedor y
exportaciones. El documento validado completo se guarda en la base de datos,
así que una exportación se puede rehacer **sin volver a llamar al proveedor**.

- **Idempotencia**: `--job-key`. Misma clave y misma solicitud → se devuelve el
  resultado existente (`"reused": true`, cero llamadas). Misma clave con
  parámetros distintos → conflicto (código 8). El *fingerprint* se calcula a
  partir de la solicitud y de las versiones de configuración, prompts, modelo y
  catálogo.
- **Reanudación**: las etapas completadas (`ideas_done`, `script_done`,
  `repair_done`, `exported`) quedan registradas; un trabajo interrumpido no
  repite lo ya persistido. También se persiste `calls_used`: **`MAX_CALLS_PER_JOB`
  es un tope por trabajo, no por proceso**, así que al reanudar se recupera lo ya
  gastado y la suma de todos los intentos nunca lo sobrepasa. Del mismo modo,
  `repair_done` sobrevive a la reanudación: hay **una reparación por trabajo**,
  no una por ejecución.
- **Ni reutilizar ni reexportar promocionan nada**: al recuperar un trabajo
  terminado se relee el documento de SQLite, se revalida en local y se
  reexporta con **los mismos avisos y el mismo `production_status`**. Un
  borrador sigue siendo borrador; pasar a `ready_for_production` exige una
  generación nueva que supere la validación. Los fallos estructurales
  permanecen como `failed` y no producen archivo.
- **Recuperación de exportación**: si borras el `script.json`, repetir el
  comando con la misma `--job-key` lo reescribe desde SQLite sin gastar
  llamadas.
- **Bloqueo de proceso**: un `flock` sobre `DATA_DIR/worker.lock` impide dos
  ejecuciones simultáneas (código 9). Las transacciones son cortas y **nunca**
  se mantiene una transacción de escritura abierta durante una llamada HTTP.
- **Disco**: se comprueba el espacio libre antes de empezar y antes de exportar,
  con umbral `MIN_FREE_DISK_MB=1500`. No se borran trabajos ni resultados en
  silencio para recuperar espacio; la limpieza audiovisual global pertenece al
  módulo 4. No se guarda base64 ni medios.

> La idempotencia local **no garantiza una sola facturación**: si una llamada
> remota termina de forma incierta (corte de red tras enviarse la petición), el
> proveedor puede haberla contabilizado aunque aquí conste como fallida.

---

## 9. Errores, límites y costes

- Tiempo de espera configurable, retroceso exponencial con *jitter* y respeto de
  `Retry-After`.
- Se reintentan **solo** fallos transitorios: conexión, tiempo de espera,
  límites temporales y errores 5xx.
- **No** se reintentan: clave incorrecta, permisos, cuota de facturación
  agotada, modelo no compatible, respuestas incompletas ni rechazos del
  proveedor.
- Los reintentos internos del SDK están desactivados (`max_retries=0`): los
  gestiona el proyecto.
- `MAX_CALLS_PER_JOB` cuenta **todas** las peticiones reales, incluidos
  reintentos, tanda adicional de ideas y reparación. Nunca se sobrepasa.
- Se limita la salida, el tamaño de la entrada y el tamaño del catálogo enviado
  al modelo: para catálogos grandes se filtra localmente por similitud con el
  tema y se registra exactamente qué registros se seleccionaron.
- Se registran uso, latencia, etapa y códigos de error en `usage_events`.
- `estimated_cost_usd` solo se calcula si hay tarifas explícitas configuradas.
  Si alguna petición consumió una cantidad desconocida de tokens, el coste es
  `null`: atribuir coste cero a una petición fallida sería engañoso.

---

## 10. Estados y códigos de salida

Estado del **trabajo** (SQLite): `pending`, `running`, `completed` (solo
`ideas`), `ready_for_production`, `needs_review`, `needs_research`, `failed`.

Estado del **documento**: `ready_for_production` o `needs_review`.

| Código | Familia | Significado |
| --- | --- | --- |
| 0 | — | Correcto. En `generate`, documento `ready_for_production`. |
| 1 | — | Error inesperado (bug); la traza queda en el log. |
| 2 | uso | Argumentos de CLI incorrectos. |
| 3 | configuración | Ajustes, perfil, biblia o catálogo inválidos; falta clave o modelo en modo real. |
| 4 | proveedor | Transporte agotado, rechazo, respuesta incompleta o presupuesto de llamadas superado. |
| 5 | validación | El documento no supera la integridad tras la reparación; **no se exporta**. |
| 6 | investigación pendiente | Faltan fuentes aprobadas; se corta antes de llamadas de pago. |
| 7 | disco | Espacio libre por debajo del umbral. |
| 8 | idempotencia | Misma `--job-key` con parámetros distintos. |
| 9 | concurrencia | Ya hay otra ejecución en curso. |
| 10 | revisión | Documento exportado y válido, pero con avisos: revisión humana. |

---

## 11. Estructura del proyecto

```
viralgen/
├── pyproject.toml              # dependencias fijadas y entry point `viralgen`
├── requirements.lock.txt       # conjunto exacto verificado
├── .env.example  .gitignore
├── schema/script.schema.json   # contrato exportado
├── docs/contrato_modulo_2_voz.md  # campos que necesita el modulo de voz
├── examples/                   # dos salidas simuladas completas
├── src/viralgen/
│   ├── config.py               # ajustes centralizados
│   ├── errors.py               # jerarquía de errores y códigos de salida
│   ├── textutil.py timing.py   # conteo de palabras, normalización, duraciones
│   ├── profiles.py evidence.py # perfiles, biblia visual, catálogo de hechos
│   ├── scoring.py              # duplicados y puntuación editorial
│   ├── schemas/                # provider.py (respuesta) y document.py (contrato)
│   ├── providers/              # base.py, openai_provider.py, mock_provider.py
│   ├── prompts/v1/             # plantillas versionadas
│   ├── assembly.py validation.py
│   ├── storage.py diskutil.py  # SQLite, bloqueo, escritura atómica
│   ├── pipeline.py cli.py
│   └── data/                   # profiles.json, series_bible.json, facts_demo.json
└── tests/
```

---

## 12. Pruebas

```bash
source .venv/bin/activate
pytest -q
```

Resultado de la ejecución en este entorno: **160 pruebas, todas correctas**
(Python 3.12, sin red y sin claves).

Qué se cubre, además de las unidades sueltas:

- JSON inválido y documentos que no cumplen el esquema.
- Referencias inexistentes (personajes, `claim_refs`, escenas, hechos).
- Hechos no aprobados, `demo_only` en modo real y procedencia alterada.
- Rechazo explícito y respuesta incompleta del proveedor.
- Reintentos acotados, errores no reintentables, `Retry-After` y tope de
  llamadas por trabajo.
- Duración fuera de rango y avisos que llevan a `needs_review`.
- Duplicados exactos, léxicos y dentro de la misma tanda; desempate
  determinista.
- Reutilización de `--job-key`, conflicto de parámetros y recuperación de una
  exportación borrada sin gastar llamadas.
- Espacio insuficiente y bloqueo de proceso.
- Una sola reparación, una sola tanda adicional de ideas y ausencia de
  exportación cuando la reparación no arregla el problema.
- Un recorrido completo simulado **por cada perfil**, que exporta el JSON y lo
  vuelve a validar.
- Que el log no filtra secretos.
- **Payload real del SDK** (`tests/test_openai_payload.py`): con el cliente de
  `openai` y un transporte HTTP simulado, se captura la solicitud y se
  comprueban `text.format.type=json_schema`, `strict`, `required`,
  `additionalProperties`, los campos *nullable*, la ausencia de `default` y que
  no se envía `temperature`.
- **Continuidad de personajes**: cada `image_prompt` describe a los personajes
  de su escena (y solo a esos); un prompt sin ficha se marca `fatal`.
- **Criterio de admisión**: las cinco condiciones, incluida la negativa a
  admitir una salida simulada o un borrador con avisos.
- **Los estados no se promocionan solos**: un borrador `needs_review` reutilizado
  conserva sus avisos, su estado y su inadmisibilidad, sin gastar llamadas.
- **El presupuesto es por trabajo**: tras un fallo, reanudar no reinicia el
  contador y la suma respeta `MAX_CALLS_PER_JOB`.
- Que `experiment_tag` y la biblia visual los aporta la aplicación (no están
  siquiera en el esquema de respuesta del proveedor) y que `actual_duration_s`
  sigue siendo `null`.

### Ejemplos de salida

`examples/ejemplo_cuento_infantil.json` y
`examples/ejemplo_curiosidad_corta.json` proceden del **proveedor simulado**
(`--mock --seed 2026`), cumplen el esquema y llevan `simulation: true`. Por esa
misma razón `admissible_for_media` es `false` en los dos: son válidos y
revalidables, pero **no** material de producción. La
curiosidad usa el catálogo **ficticio** `facts_demo.json`: sus fuentes no están
verificadas y no deben presentarse como tales.

Reproducirlos:

```bash
viralgen generate --profile infantil_cuentos  --topic "aprender a compartir la merienda" \
  --job-key ejemplo-cuento --mock --seed 2026
viralgen generate --profile curiosidades_corto --topic "por que la cremallera no se suelta" \
  --source-pack src/viralgen/data/facts_demo.json --job-key ejemplo-curiosidad --mock --seed 2026
```

---

## 13. Prueba con el proveedor real (pendiente)

**Estado: NO ejecutada.** Este entorno no tiene `OPENAI_API_KEY` ni
`OPENAI_MODEL` configurados, así que no hay ninguna salida del proveedor real.
Todo lo entregado como ejemplo procede del proveedor simulado y está marcado
como tal. Una simulación **no** sustituye a esta prueba.

Comando preparado, para ejecutar en un entorno que sí tenga credenciales. La
clave se pasa por variable de entorno y **nunca** aparece en el comando, en los
logs, en los commits ni en el informe:

```bash
# La clave se introduce fuera del historial del shell.
read -rsp "OPENAI_API_KEY: " OPENAI_API_KEY && export OPENAI_API_KEY
export OPENAI_MODEL="<identificador exacto del modelo con Structured Outputs>"

# Límites explícitos (son los valores por defecto; se fijan para dejar constancia).
export VIRALGEN_MAX_CALLS_PER_JOB=6
export VIRALGEN_MAX_OUTPUT_TOKENS=6000
export VIRALGEN_REQUEST_TIMEOUT_SECONDS=60
export VIRALGEN_DATA_DIR="$PWD/.viralgen"

# 1) Cuento infantil de ficción: no necesita catálogo de hechos.
viralgen generate \
  --profile infantil_cuentos \
  --topic "aprender a compartir la merienda" \
  --job-key real-infantil-001

# 2) Revalidación del documento exportado.
viralgen validate --input .viralgen/jobs/<job_id>/script.json

# 3) Idempotencia: misma job-key y misma solicitud -> "reused": true y 0 llamadas.
viralgen generate \
  --profile infantil_cuentos \
  --topic "aprender a compartir la merienda" \
  --job-key real-infantil-001
```

Qué habrá que entregar tras ejecutarlo: el `script.json` completo, el modelo
usado, los `provenance.request_ids`, el número de llamadas (`calls`) y el
consumo observado (`input_tokens` / `output_tokens`), el resultado de la
revalidación y la confirmación de que la segunda ejecución no gasta llamadas.

**La prueba real de curiosidades queda aparte y también pendiente**: exige un
catálogo de hechos revisados de verdad. El catálogo `facts_demo.json` es
exclusivamente de prueba (`demo_only: true`, URLs en `example.org`) y el modo
real lo rechaza por diseño. Cambiar ese campo a `false` para desbloquearlo
sería falsear la procedencia, así que no se hace.

---

## 14. Limitaciones conocidas

1. **La verificación es de restricción, no semántica.** El sistema obliga a que
   toda afirmación provenga del catálogo aportado, pero no comprueba que la
   fuente realmente sostenga lo que se dice. Eso sigue siendo trabajo humano.
2. **Los duplicados se detectan por léxico.** Jaccard y SHA‑256 no reconocen la
   misma idea escrita con otras palabras.
3. **La puntuación editorial es heurística.** Ordena candidatas; no predice
   rendimiento.
4. **El proveedor simulado no imita la calidad de un modelo real.** Compone
   texto a partir de bancos de frases para ejercitar el recorrido completo; sus
   salidas sirven para pruebas y demostraciones, no para publicar.
5. **La duración es una estimación.** La real la medirá el módulo 2 y puede
   desviarse; los módulos posteriores deben corregir o devolver el trabajo.
6. **La idempotencia es local.** No garantiza una única facturación si una
   llamada remota termina de forma incierta.
7. **Solo hay dos proveedores** (OpenAI y simulado), sin cola de trabajos ni
   servidor web: un proceso, un trabajo a la vez, por diseño.
8. **No se ha instalado ni probado en la VPS**, porque no se facilitó acceso.
   Las instrucciones de Ubuntu están escritas para ejecutarse allí tal cual.
9. **La ruta al proveedor real nunca se ha ejecutado contra la API.** Está
   cubierta con un cliente falso (errores, reintentos, rechazos) y con una
   prueba del payload real del SDK sobre transporte simulado, pero eso solo
   verifica lo que sale de esta máquina: **no demuestra que el servidor acepte
   el esquema**. Ver §13.
10. **El contrato 1.0 no tiene sitio para las medidas del módulo 2** más allá de
    `video.actual_duration_s`: faltan duraciones reales por escena y
    alineaciones palabra‑a‑palabra. Hay dos vías propuestas en
    `docs/contrato_modulo_2_voz.md`; decidirlas es el primer paso al conectar voz.
