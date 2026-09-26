# viralgen — Módulos 1‑4: guion, voz, medios visuales y montaje

**Módulo 1** produce un documento JSON validado (`script.json`) que describe el
plan completo de un vídeo vertical 9:16 —idea, guion, escenas, prompts de imagen
y movimiento, indicaciones de voz, subtítulos, audio, loop, evidencia y borrador
de publicación— listo para que lo consuman los módulos 2‑5 **sin tener que
interpretar texto libre**.

**Módulo 2** toma ese guion y produce la narración: `narration.wav` más un
manifiesto lateral `voice.json` con los **tiempos medidos** (duración real por
escena y alineación por palabra). El guion **no se modifica ni un byte**: ver
§15 y [`docs/contrato_modulo_2_voz.md`](docs/contrato_modulo_2_voz.md).

**Módulo 3** toma ese guion y esa voz y produce los **medios visuales**: una
imagen por escena, un clip por cada escena `asset_type: video`, un conjunto
versionado de referencias de personaje y un manifiesto lateral `media.json`.
Ni el guion ni `voice.json` se modifican: ver §16 y
[`docs/contrato_modulo_3_visuales.md`](docs/contrato_modulo_3_visuales.md).
**No monta ni publica**: eso es del módulo 4.

**Módulo 4** toma los tres y produce el **vídeo**: `video.mp4` vertical con la
narración, los subtítulos integrados, los sonidos opcionales que ya existan y
un manifiesto lateral `render.json`. Usa **FFmpeg directamente desde Python**:
Python orquesta, calcula el plan y escribe contratos; no carga el vídeo en
memoria ni construye sus fotogramas. Ver §17 y
[`docs/contrato_modulo_4_montaje.md`](docs/contrato_modulo_4_montaje.md).
**No publica**: eso sería del módulo 5.

## Qué hace y qué no hace

Hace:

- Propone ideas, las filtra, detecta duplicados y puntúa candidatas.
- Desarrolla el guion completo de la idea elegida.
- Calcula localmente palabras, duraciones y tiempos acumulados.
- Valida integridad y criterios editoriales.
- Persiste todo en SQLite y exporta un `script.json` atómico.

**No** hace (el módulo 1): no genera audio ni imágenes, no ejecuta FFmpeg, no
monta vídeo, no publica nada, no descarga analíticas y no consulta buscadores.
Tampoco instala ni despliega nada en una VPS.

Lo que **ningún** módulo de esta entrega hace: publicar en plataformas,
programar publicaciones, consultar analíticas ni desplegar en una VPS.

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
son texto: un trabajo completo ocupa del orden de 30‑60 KB. Los **medios** no:
ver §16 para los presupuestos de disco del módulo 3.

FFmpeg es **obligatorio para el módulo 4** (monta el vídeo) y opcional para los
anteriores: el 2 lo usa para decodificar el MP3 del proveedor y el 3 para medir
clips. Se necesita con **libx264, AAC y libass**, más una fuente tipográfica:

```bash
sudo apt install -y ffmpeg fonts-dejavu-core
```

Versión **realmente probada** en esta entrega: `ffmpeg 6.1.1-3ubuntu5` en Ubuntu
24.04, con `--enable-libx264 --enable-libass`. Las capacidades se leen de los
ejecutables instalados, **no de la documentación en línea**: si al paquete de tu
distribución le falta un codificador o un filtro, `viralgen render plan` lo dice
por nombre antes de intentar nada.

`fonts-dejavu-core` aporta **DejaVu Sans Bold**, la fuente por defecto de los
subtítulos: licencia redistribuible (Bitstream Vera / Arev) y cobertura completa
del español. El módulo fija el **archivo** de la fuente, no su nombre de
familia, y registra su SHA-256: así no hay sustitución silenciosa.

### Versiones fijadas y cómo se verificaron

`pyproject.toml` fija rangos compatibles (`pydantic>=2.13,<3`,
`pydantic-settings>=2.15,<3`, `openai>=3.16,<4`, `httpx2>=2.13,<3`,
`Pillow>=12.3,<13`, `pytest>=9.1,<10`) y `requirements.lock.txt` recoge el
conjunto exacto resuelto.

`Pillow` la añade el **módulo 3**: se usa para abrir cada imagen, verificarla y
medirla, y para calcular la geometría de encuadre y la hoja de contacto. El
módulo no se fía de la extensión del archivo ni de lo que declare el proveedor.

El **módulo 4 no añade ninguna dependencia de Python**. La cobertura de glifos y
las anchuras de la fuente se leen con un lector mínimo de TrueType propio
(`render/sfnt.py`, ~200 líneas sobre `struct`), en vez de arrastrar una
biblioteca tipográfica completa para tres tablas. FFmpeg es una dependencia del
**sistema**, no del paquete.

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
| 11 | espera remota (solo módulo 3; el montaje local nunca inventa una espera remota) | Módulo 3: hay una tarea de vídeo viva en el proveedor y la espera local se agotó. **Ni éxito ni fallo**: vuelve a invocar el mismo `--media-key` para seguir consultando ese mismo `task_id`. |

---

## 11. Estructura del proyecto

```
viralgen/
├── pyproject.toml              # dependencias fijadas y entry point `viralgen`
├── requirements.lock.txt       # conjunto exacto verificado
├── .env.example  .gitignore
├── schema/script.schema.json   # contrato del modulo 1
├── schema/voice.schema.json    # contrato del manifiesto de voz
├── schema/media.schema.json    # contrato del manifiesto de medios
├── schema/render.schema.json   # contrato del manifiesto de montaje
├── schema/publication_plan.schema.json     # contrato del plan de publicacion
├── schema/publication_receipt.schema.json  # contrato del recibo de publicacion
├── docs/contrato_modulo_2_voz.md       # contrato modulo 1 -> modulo 2 (voz)
├── docs/contrato_modulo_3_visuales.md  # contrato modulo 3 -> modulo 4 (montaje)
├── docs/contrato_modulo_4_montaje.md   # contrato modulo 4 -> modulo 5 (publicacion)
├── docs/modulo_5_publicacion.md        # modulo 5: modos, estados, secretos, pendientes
├── deploy/systemd/                     # unidades PREPARADAS, no instaladas
├── tools/demo_publicacion.sh           # recorrido completo simulado del modulo 5
├── .github/workflows/viralgen-ffmpeg.yml  # CI con FFmpeg real (en la raiz del repo)
├── examples/                   # salidas simuladas completas y un preview.mp4 real
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
│   ├── data/                   # profiles.json, series_bible.json, facts_demo.json
│   ├── voice/                  # MODULO 2
│   │   ├── audio.py timing.py  # PCM, pausas, medicion en muestras
│   │   ├── alignment.py        # caracteres -> palabras, validacion
│   │   ├── schemas.py          # contrato de voice.json
│   │   ├── providers/          # base, elevenlabs, mock
│   │   ├── profiles.py sound.py # voces por perfil, catalogo de sonido
│   │   ├── storage.py          # migracion idempotente de tablas de voz
│   │   ├── admission.py        # puerta de entrada del modulo 4 (voz)
│   │   ├── pipeline.py
│   │   └── data/voice_profiles.json
│   ├── media/                  # MODULO 3
│   │   ├── capabilities.py     # tamanos, calidades y duraciones ADMITIDAS
│   │   ├── imaging.py          # Pillow: decodificar, medir, geometria, hoja
│   │   ├── videoprobe.py       # ffprobe: medir el STREAM de video
│   │   ├── timeline.py         # reloj tomado de voice.json
│   │   ├── prompts.py          # prompt efectivo versionado (v1)
│   │   ├── references.py       # conjunto de referencias versionado
│   │   ├── schemas.py          # contrato de media.json
│   │   ├── providers/          # base, openai_images, runway, mock
│   │   ├── planner.py          # preflight `media plan` e identidad de cache
│   │   ├── storage.py          # tablas de medios, cache y tareas remotas
│   │   ├── admission.py        # puerta de entrada del modulo 4 (medios)
│   │   └── pipeline.py
│   └── render/                 # MODULO 4
│       ├── timeline.py         # cuantizacion de muestras a fotogramas
│       ├── ffmpeg.py           # capacidades reales y subprocesos acotados
│       ├── sfnt.py fonts.py    # lector TrueType propio y eleccion de fuente
│       ├── captions.py         # ASS: agrupacion, resaltado y escapado
│       ├── audio.py            # mezcla, ducking y sonoridad EBU R128
│       ├── video.py            # geometria, segmentos, concat y mux
│       ├── probe.py            # medicion del archivo terminado con ffprobe
│       ├── schemas.py          # contrato de render.json
│       ├── planner.py          # preflight `render plan`
│       ├── storage.py          # etapas, checkpoints e intentos persistidos
│       ├── admission.py        # puerta de entrada del modulo 5
│       └── pipeline.py
│   └── publish/                # MODULO 5
│       ├── verification.py     # lo que NO se pudo contrastar con su fuente
│       ├── schemas.py          # contratos del plan y del recibo
│       ├── clock.py            # reloj inyectable, zonas IANA y cambios de hora
│       ├── admission.py        # tres veredictos, independientes del modo
│       ├── accounts.py         # catalogo alias -> ID exacto de cuenta
│       ├── plan.py             # borrador editorial (sin generar texto)
│       ├── authorize.py        # autorizacion atada a la intencion
│       ├── storage.py          # cola, concesiones, gasto y antiduplicados
│       ├── queue.py            # encolado, ventana de inicio y cancelacion
│       ├── secrets.py          # archivos 0600 en directorio 0700
│       ├── transport.py        # HTTP clasificado, sin reintentos ciegos
│       ├── staging.py          # S3 compatible: un MP4, URL firmada, limpieza
│       ├── worker.py           # un paso por destino y por invocacion
│       ├── receipt.py cli.py
│       └── providers/          # base, google_oauth, youtube, instagram,
│                               # tiktok (manual) y mock
└── tests/
```

---

## 12. Pruebas

```bash
source .venv/bin/activate
pytest -q
```

Resultado de la ejecución en este entorno: **669 pruebas correctas y ninguna
saltada** (Python 3.12, sin red y sin claves). Con FFmpeg instalado se ejecutan
también las cinco que antes se saltaban en los módulos 2 y 3, y las **63 de
integración local** de los módulos 4 y 5 (marca `ffmpeg`).

Las pruebas se dividen en **cuatro** categorías que este README no mezcla:

| Categoría | Qué ejercita | Estado |
| --- | --- | --- |
| **Local** | Lógica, esquemas, aritmética, validadores, proveedor simulado, Pillow. | Ejecutadas. |
| **Transporte simulado** | El cliente HTTP real (SDK de `openai`, `httpx2` con `MockTransport`) contra un transporte de prueba: se captura la solicitud y se comprueban ruta, cabeceras y cuerpo. **No hay red.** | Ejecutadas. |
| **Integración local** (marca `ffmpeg`) | FFmpeg y `ffprobe` **reales**: se codifican MP4 de verdad y se miden fotograma a fotograma. Sin red y sin credenciales. | **Ejecutadas**: 63 pruebas. |
| **Integración externa** | Las APIs reales de OpenAI y ElevenLabs, Runway **solo si el guion pide clips**, y las de publicación (YouTube, Meta y el almacenamiento temporal). | **Pendiente**: sin credenciales ni red en este entorno. Ver §13 (texto y voz), §16.1 (imagen y vídeo) y §18 (publicación). |

Una prueba de transporte simulado **no es** integración externa, y una
integración **local** con FFmpeg tampoco: este proyecto no las presenta como
tales.

### La integración local es obligatoria, no opcional

```bash
pytest -m ffmpeg -rs          #  63: solo la integracion local real
pytest -m "not ffmpeg"        # 606: solo lo que no necesita FFmpeg
```

Las dos selecciones son una **partición exacta** de las 669: entre ambas se
ejecuta todo una sola vez, sin solape ni huecos. Es como las ejecuta la CI.

Última ejecución de aceptación, sobre el árbol `e2271fa`:

```
pytest -m ffmpeg        -> 63 passed, 606 deselected en 948 s   (codigo 0)
tools/verificar_integracion.py --minimo 40
                        -> 63 recogidas, 63 ejecutadas, 0 saltadas, 0 fallidas
pytest -m "not ffmpeg"  -> 606 passed, 63 deselected en 154 s   (codigo 0)
```

`.github/workflows/viralgen-ffmpeg.yml` instala FFmpeg y la fuente, ejecuta la
selección obligatoria y pasa su informe **JUnit XML** por
`tools/verificar_integracion.py`, que rechaza tres cosas: una selección vacía,
una selección saltada y una selección marcada `xfail`.

**Por qué sobre XML y no sobre el texto del resumen.** El código de salida 5
solo cubre la colección vacía, no los saltos
([exit codes](https://docs.pytest.org/en/stable/reference/exit-codes.html),
[skipping](https://docs.pytest.org/en/stable/how-to/skipping.html)). Y un
`grep SKIPPED` tiene un hueco comprobado: una selección **entera en `xfail`**
sale con código 0 y `-rs` **no imprime** esa palabra, así que la aprobaría. En
el XML sí consta, porque pytest registra el `xfail` como
`<skipped type="pytest.xfail">`.

Las tres formas de no ejecutar están cubiertas por pruebas
(`tests/test_verificar_integracion.py`), sobre informes generados por pytest de
verdad, no escritos a mano. El `--minimo` es un **suelo** contra un derrumbe
silencioso de la marca, no el recuento exacto.

Una suite verde porque todo se saltó no acredita ningún render.

**Qué está comprobado y qué no**, que no es lo mismo:

| | Estado |
| --- | --- |
| El guard rechaza una selección vacía, saltada o en `xfail` | **Comprobado localmente**, con informes de pytest reales (`tests/test_verificar_integracion.py`) |
| El guard rechaza las pruebas del repo cuando FFmpeg no está | **Comprobado localmente**: con FFmpeg oculto del `PATH`, pytest sale con 0 y 51 saltadas, y el guard responde `ejecutadas=0` y falla |
| El workflow completo en GitHub Actions | **Preparado, no ejecutado**: este entorno no lanza CI. Lo que está verificado es el guard y su integración, no el runner |

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

Del **módulo 3** (`tests/test_media_*.py`), además:

- **Capacidades**: un tamaño, una calidad, un formato o un número de referencias
  que el modelo no admite se rechazan **antes** de enviar nada; una duración que
  ninguna opción cubre da `duration_not_supported` en vez de un clip corto.
- **Imagen**: decodificación y verificación reales con Pillow, geometría
  `contain`/`crop` sin deformar, compresión que respeta un límite de bytes, hoja
  de contacto y placeholders deterministas por semilla.
- **Payload de los adaptadores reales sobre transporte simulado**: ruta, método
  y cuerpo de `/v1/images/generations`; el multipart de `/v1/images/edits` con
  un campo `image[]` por referencia; el cuerpo de `/v1/image_to_video` con su
  data URI; las cabeceras `Authorization` y `X-Runway-Version`; el sondeo de
  `GET /v1/tasks/{id}`. **Sin red.**
- **Preflight**: `media plan` no llama a nadie, detecta credenciales y
  herramientas que faltan y anticipa los aciertos de caché.
- **Un clip nunca se sustituye por una imagen**: ante error, falta de
  credenciales o presupuesto agotado el trabajo queda parcial y **no** se
  publica `media.json`.
- **Presupuesto por trabajo**: reanudar no reinicia el contador; `outcome_unknown`
  bloquea la repetición automática.
- **Tareas remotas**: recorrido completo hasta `waiting_remote` con **código
  11** y sin manifiesto; el `task_id` del resumen es el real; y una segunda
  invocación que **retoma esa misma tarea** con un solo POST en total.
- **Caché por identidad**: mismo contenido, mismo asset; cambiar un límite
  administrativo no lo invalida; un archivo ya validado se adopta tras una caída.
- **Admisión del trío**: cobertura completa, hashes, rutas fuera del paquete
  (incluidos enlaces simbólicos), archivos que no decodifican, reloj que no
  coincide con la voz y los tres veredictos separados.
- **Medición de vídeo con `ffprobe`** (`tests/test_media_video.py`): se mide el
  **stream de vídeo**, no la pista de audio ni el contenedor, y se comprueba la
  integridad decodificando.

Del **módulo 4** (`tests/test_render_*.py`), además:

- **Cuantización acumulada**: el ejemplo de 91 fotogramas, fronteras
  fraccionarias, pausas ya incluidas, primera y última escena, cero huecos, y
  el rechazo de una escena que colapsaría a cero fotogramas.
- **Admisión**: producción rechaza fuentes simuladas **antes de codificar**
  (`renders_new = 0`); preview acepta su origen pero **no** relaja hashes rotos,
  cuantización falseada ni archivos ausentes; elegir el modo del informe no
  cambia ningún `check`; un manifiesto manipulado no se cree.
- **Render real de un fixture breve**: cuenta exacta de fotogramas, cambios de
  escena donde dice el reloj, PTS monótonos y CFR, `+faststart`, y ausencia de
  desplazamiento acumulado.
- **Subtítulos realmente visibles**: se extraen fotogramas y se cuentan píxeles
  claros dentro de la región de texto; el resaltado cambia entre dos eventos del
  mismo grupo, comparando **regiones con tolerancia** (exigir imágenes idénticas
  entre compilaciones de libass sería frágil).
- **Sin inyección ASS**: `{`/`}` del guion se escapan; una barra invertida
  bloquea con motivo en vez de convertirse en `\N`.
- **Geometría**: `contain` no deforma (se verifica con una cuadrícula de prueba),
  `crop` recorta, una transformación **ya aplicada** no se repite, y una política
  o un color desconocidos se rechazan en vez de interpretarse como filtro.
- **24 → 30 fps**: 120 fotogramas pasan a 150 y la **duración no cambia**.
- **Audio**: la narración entra una sola vez, la conversión 24→48 kHz es exacta
  (2×), la sonoridad medida cae en −16 ±1 LUFS con pico bajo −1 dBTP, un audio
  silencioso **no inventa una medida**, los cues se colocan en su tiempo global
  y un asset corto **no se repite** automáticamente.
- **Reutilización y reanudación**: repetir la clave da `renders_new = 0`; cambiar
  la configuración es conflicto; un fallo de segmento corta las etapas
  siguientes sin publicar manifiesto; un fallo de muxing **no obliga a
  recodificar** los segmentos; los intentos por etapa están acotados y persisten.
- **Procesos**: los argumentos van en lista sin shell, `stdin` cerrado, un
  timeout mata el **grupo** de procesos (se comprueba que no queda ningún hijo
  vivo), una salida que crece de más se corta y el log está acotado.
- **Limpieza**: borra los intermedios y **no invalida el paquete**; `validate` y
  la reutilización siguen funcionando después.
- **Un archivo que no es lo que dice**: un MP4 truncado supera `ffprobe` y falla
  al decodificar entero; una imagen renombrada a `.mp4` no pasa por vídeo.

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

### Voz real (también pendiente)

**Estado: NO ejecutada.** No hay `ELEVENLABS_API_KEY`, ni `ELEVENLABS_MODEL_ID`,
ni `voice_id` de cuenta, ni FFmpeg en este entorno. Todo el audio entregado es
del proveedor simulado y está marcado como tal.

El recorrido real, cuando haya credenciales, es: **guion real admitido → voz
real → validación → repetir la misma `--voice-key` sin llamadas nuevas.** No
puede empezar antes que la prueba real de OpenAI: el módulo 2 se niega a
sintetizar un guion con `simulation=true`, y reclasificar un guion demo para
desbloquearlo sería falsear el origen.

```bash
sudo apt install -y ffmpeg      # el adaptador real decodifica con FFmpeg

read -rsp "ELEVENLABS_API_KEY: " ELEVENLABS_API_KEY && export ELEVENLABS_API_KEY
export ELEVENLABS_MODEL_ID="eleven_multilingual_v2"   # o el que uses
export ELEVENLABS_VOICE_ID="<voice_id de TU cuenta>"  # o rellénalo por perfil

# Límites explícitos (son los valores por defecto; se fijan para dejar constancia).
export VIRALGEN_VOICE_MAX_REQUESTS_PER_JOB=24
export VIRALGEN_VOICE_MAX_REQUEST_CHARS=2500
export VIRALGEN_VOICE_REQUEST_TIMEOUT_SECONDS=60
export VIRALGEN_DATA_DIR="$PWD/.viralgen"

# 1) Voz sobre un script.json REAL ya admitido (el del paso 1 de §13).
viralgen voice generate \
  --script .viralgen/jobs/<job_id>/script.json \
  --voice-key real-voz-001

# 2) Auditoría de la pareja.
viralgen voice validate \
  --script .viralgen/jobs/<job_id>/script.json \
  --manifest .viralgen/jobs/<job_id>/voice/<voice_run_id>/voice.json

# 3) Idempotencia: mismo comando -> "reused": true y "requests_new": 0.
viralgen voice generate \
  --script .viralgen/jobs/<job_id>/script.json \
  --voice-key real-voz-001
```

Qué habrá que entregar: el `voice.json` completo, el `narration.wav`, el modelo
y la voz usados, los `request_ids` que devuelva el servidor, las solicitudes
nuevas y totales, los caracteres enviados, la duración medida y el resultado de
la validación.

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
10. **Resuelto:** el contrato 1.0 no tenía sitio para las medidas del módulo 2.
    Se optó por el manifiesto lateral `voice.json` en vez de migrar el guion a
    1.1 (§15). `video.actual_duration_s` queda como campo reservado.
11. **La ruta real de voz nunca se ha ejecutado contra ElevenLabs.** Está
    cubierta con transporte HTTP simulado (payload, errores, reintentos,
    `Retry-After`, límites), pero eso solo verifica lo que sale de esta máquina.
    Ver §13.
12. **Resuelto:** la decodificación MP3 ya se prueba, con FFmpeg instalado.
    El recorrido `--mock` sigue sin necesitarlo porque genera PCM directamente.
13. **El audio simulado no es voz.** Son señales de prueba generadas con la
    biblioteca estándar, y sus tiempos por carácter son sintéticos: no miden la
    precisión de ningún proveedor real.
14. **La alineación reversible admitida es estrecha a propósito**: igualdad
    exacta o normalización Unicode/de espacios que conserve la longitud. Una
    normalización que expanda números o abreviaturas exige otra alineación o
    revisión humana.
15. **No se promete facturación exactamente una vez** en voz: un timeout puede
    haber consumido crédito, así que la reserva se cuenta igual.
16. **La ruta real de imagen y vídeo nunca se ha ejecutado** contra OpenAI
    Images ni Runway. Está cubierta con transporte HTTP simulado (rutas,
    cabeceras, multipart, cuerpos, errores, sondeo de tareas), que verifica
    **lo que sale de esta máquina** y nada más. Ver §16.1.
17. **Resuelto:** las pruebas que necesitaban FFmpeg ya se ejecutan. Se
    instaló `ffmpeg 6.1.1-3ubuntu5` en el entorno de desarrollo y las cinco
    que antes se saltaban en los módulos 2 y 3 **pasan**.
18. **Las imágenes simuladas no son imágenes generadas.** Son placeholders
    dibujados con Pillow, rotulados `SIMULACION - NO ES UNA IMAGEN REAL`. Sirven
    para ejercitar el recorrido, los hashes y la aritmética; no dicen nada sobre
    la calidad de ningún modelo.
19. **Nada mide la calidad visual ni la continuidad.** Se persiguen con
    referencias versionadas y prompts, pero no se verifican. `visual_review`
    empieza en `not_performed` y solo lo cambia una persona.
20. **Las duraciones de vídeo del proveedor son discretas.** Se pide la más
    corta que cubre la escena y sobran fotogramas por diseño; el recorte es del
    módulo 4. Si ninguna duración admitida cubre la escena, el trabajo lo dice
    en vez de entregar un clip corto.
21. **No se promete facturación exactamente una vez** en medios. Una tarea de
    vídeo puede consumir crédito aunque la respuesta se pierda: el presupuesto
    se reserva **antes** de enviar y un `outcome_unknown` **bloquea** la
    repetición automática en lugar de arriesgar un cobro doble.
22. **El montaje de producción nunca se ha ejecutado**, porque no hay fuentes
    reales que montar. El montaje en sí **no llama a ningún proveedor**: las
    credenciales hacen falta antes, para producir el paquete. Para un caso
    solo de imágenes bastan OpenAI y ElevenLabs; Runway solo si el guion pide
    clips. El
    recorrido **preview** sí se ha ejecutado entero y produce un MP4 auténtico
    (§17.1).
23. **Un MP4 real con contenido simulado sigue siendo simulado.** FFmpeg
    renderiza de verdad, pero las imágenes son placeholders y el audio son
    tonos: el ejemplo acredita transporte temporal, mezcla y aritmética, no
    inteligibilidad ni calidad visual.
24. **Nada mide la calidad del vídeo.** No hay medida de ritmo, de legibilidad
    ni de acierto editorial. `visual_review` empieza en `not_performed` y solo
    lo cambia una persona.
25. **La región de subtítulos no es una zona segura certificada.** Es una
    decisión de diseño de este proyecto; cada plataforma recorta y superpone su
    propia interfaz, y eso no se puede comprobar desde aquí.
26. **Un único objetivo de entrega**: 1080×1920 a 30 fps. Otros formatos no
    están implementados, y el objetivo declarado en `media.json` se **comprueba**
    en vez de sobrescribirse.
27. **No hay medida de memoria.** `peak_memory_mib` es `null` porque no se
    instrumentó; no se estima un número que nadie midió.
28. **El montaje es secuencial por diseño**: un trabajador y un proceso pesado a
    la vez. No hay cola distribuida ni paralelismo entre escenas.

---

## 15. Módulo 2: voz, alineación temporal y preparación de sonido

Toma un `script.json` **admitido** y produce la narración medida. Contrato
completo para quien monte: [`docs/contrato_modulo_2_voz.md`](docs/contrato_modulo_2_voz.md).

### Decisión de contrato

`script.json` se queda en el esquema **1.0 y con sus bytes intactos**.
`video.actual_duration_s` sigue siendo `null` y queda como **campo reservado**.
Los tiempos medidos viven en un manifiesto lateral `voice.json`
(`document_type="voice_manifest"`, `schema_version="1.0"`), versionado por su
cuenta y vinculado a la entrada por `job_id`, versión de esquema del guion y
**SHA-256 de los bytes exactos** del `script.json`.

Esto sustituye a la alternativa de migrar el guion a 1.1: los consumidores del
contrato 1.0 siguen funcionando y los nuevos consultan `voice.json`.

> Un hash comprueba la correspondencia entre archivos, **no autentica al autor**
> de un guion. El consumidor debe revalidar guion y medios, además de comparar
> hashes.

### Uso

```bash
# Simulación: sin claves, sin red y sin FFmpeg.
viralgen voice generate --script ruta/script.json --voice-key demo-voz-001 --mock

# Real (requiere credenciales de VOZ; no las de OpenAI).
viralgen voice generate --script ruta/script.json --voice-key voz-001

# Auditoría de la pareja guion + manifiesto. No genera audio ni llama a nadie.
viralgen voice validate --script ruta/script.json --manifest ruta/voice.json

# Contrato exportable.
viralgen voice schema --output ruta/voice.schema.json
```

Salidas en `DATA_DIR[/simulation]/jobs/<job_id>/voice/<voice_run_id>/`:
`voice.json`, `audio/narration.wav` y `audio/scenes/<scene_id>.wav`.

### Admisión

Antes de **cualquier petición de pago**, el modo real exige el criterio completo
del módulo 1 (§7): documento válido, versión compatible, exportación completa,
`ready_for_production` y `simulation=false`. Una entrada `needs_review`,
simulada, manipulada o de versión desconocida queda bloqueada con motivo
explícito y **cero peticiones emitidas**.

`--mock` es una ruta de pruebas explícita: reutiliza exactamente la misma
validación y relaja **solo** la condición de origen simulado, leyendo las claves
estructuradas del informe (nunca el texto de sus mensajes). Un borrador con
avisos sigue bloqueado también en `--mock`.

Toda salida del proveedor simulado lleva `simulation=true` y
`admissible_for_assembly=false`, **aunque la entrada fuera real**. Los datos de
simulación viven en su propio espacio (`DATA_DIR/simulation/`).

### Proveedor de voz

`POST /v1/text-to-speech/{voice_id}/with-timestamps`, autenticado con la
cabecera `xi-api-key`; `output_format` va como parámetro de consulta y `text` +
`model_id` en el cuerpo JSON, junto con `voice_settings`, `previous_text` y
`next_text`. El contexto sirve para la continuidad: **no se pronuncia ni se
concatena** al texto. La respuesta trae `audio_base64` y **puede** traer
`alignment` y `normalized_alignment`: no se presupone que existan. Los
`request_id` se guardan solo si el servidor los envía.

`ELEVENLABS_MODEL_ID` no tiene valor por defecto. `eleven_multilingual_v2` es un
punto de partida compatible con la referencia consultada, pero hay que
declararlo: el modelo no se cambia en silencio. Los `voice_id` los aporta tu
cuenta —vienen vacíos en `voice_profiles.json` y **no se inventan**—; sin uno,
el modo real se detiene con un error de configuración.

`voice_direction` y `pronunciation_notes` del guion son **indicaciones
editoriales** para quien revise: no se envían dentro del texto que se pronuncia.
Lo único que viaja como parámetro es el bloque `settings` del perfil de voz
(`stability`, `similarity_boost`, `style`, `use_speaker_boost`), que debe ser
admitido por el modelo configurado.

Dirección creativa: voz cálida y pausada para infantil, apertura clara y
expresiva para curiosidades. **La voz empieza con el gancho ya escrito**: no se
antepone ninguna presentación ni se retoca el guion.

### Formato interno y medición

Se pide `mp3_44100_128` y se decodifica cada clip a **WAV PCM 16 bits, mono,
24.000 Hz** con FFmpeg (lista de argumentos, `shell=False`, timeout, archivos
locales controlados). FFmpeg se comprueba **antes** de las peticiones reales;
`--mock` no lo necesita. El formato interno es una decisión del proyecto y queda
registrado en el manifiesto.

La duración real se mide **contando muestras** del PCM decodificado. Nunca a
partir de palabras por minuto, del tamaño del MP3 ni de lo que declare el
modelo. Las fórmulas exactas (`clip_samples`, `pause_samples`, `start_sample`,
`end_sample` exclusivo, y los segundos derivados) están en
`src/viralgen/voice/timing.py` y en el contrato.

La pausa del guion se añade como silencio **exactamente una vez**, también tras
la última escena si figura, y es adicional a las respiraciones del clip. Las
escenas cubren el maestro desde la muestra 0 sin huecos ni solapamientos, y su
suma es exactamente el número de muestras del maestro. No hay crossfades,
recortes de silencio ni cambios de velocidad: cualquier transformación futura
que desplace tiempos obligaría a recalcular la alineación.

### Alineación por palabra

Se prioriza `alignment` cuando su texto concatenado corresponde al
`narration_text` original. `normalized_alignment` solo puede sustituirla si se
relaciona **sin ambigüedad** con el texto fuente: en el MVP, igualdad exacta o
una normalización Unicode/de espacios que conserve la longitud. **No se asume**
que «12 km» y «doce kilómetros» se correspondan; eso exige otra alineación o
revisión.

Tolerancia explícita de redondeo: **20 ms** configurable. Los ajustes permitidos
quedan registrados como incidencias informativas. Desviaciones mayores, palabras
omitidas, duraciones inválidas o correspondencias ambiguas **bloquean** el uso
para montaje. **No se fabrican timestamps** repartiendo la duración entre
palabras.

Si falta una alineación utilizable, **se conserva el audio**. Con
`VOICE_ALLOW_FORCED_ALIGNMENT=true` se puede pedir `POST /v1/forced-alignment`
sobre ese WAV y el texto original: cuenta contra el mismo presupuesto y **no
regenera la voz**. El resultado se vuelve a validar igual que cualquier otro —
una puntuación del proveedor no es una probabilidad de exactitud—.

### Un bloqueo detiene TODAS las solicitudes nuevas

La secuencia por escena es: sintetizar → guardar su audio → comprobar su
alineación → ejecutar la recuperación configurada **para esa escena**. Si la
recuperación está desactivada o termina sin resolver el problema, se persiste
`needs_review` con el motivo y **se termina sin pedir las escenas siguientes**:
ni síntesis ni alineaciones.

Los reintentos transitorios acotados de la operación **en curso** conservan su
política; no son una forma de seguir procesando otras escenas.

**Un trabajo parcial es un resultado válido.** Se conservan los clips, sus
hashes, el estado y los contadores en SQLite. No se construye un maestro
incompleto y **no se publica un `voice.json` que aparente contener todas las
escenas** cuando faltan medios. El resumen de la CLI lo dice con campos
estructurados:

```json
{
  "voice_status": "needs_review",
  "partial": true,
  "manifest_path": null,
  "master_path": null,
  "pending_scenes": ["sc_04", "sc_05", "sc_06", "sc_07"],
  "available_paths": ["…/audio/scenes/sc_01.wav", "…/audio/scenes/sc_02.wav", "…"],
  "requests_new": 3,
  "exit_code": 10
}
```

Un bloqueo en la **última** escena no deja nada pendiente, así que sí produce un
manifiesto completo con `needs_review`: el esquema 1.0 solo conoce `ready` y
`needs_review` para manifiestos completos, y un corte parcial no inventa un
tercer estado.

La ausencia de un efecto opcional **no** detiene nada: es una incidencia con
`blocking=false` y `severity=info`. Lo que bloquea lleva `blocking=true` y
`severity=error`. La distinción está en esos campos, no en el texto del mensaje.

**Repetir sin resolver el bloqueo no gasta solicitudes nuevas.** Al reanudar se
reutilizan los clips guardados (identidad + hash), la alineación se reevalúa en
local y el corte vuelve a producirse con `requests_new: 0`. Una recuperación
que ya se intentó y falló queda marcada junto al clip y **no se repite**.

**Cómo resolver ese estado** (hace falta una acción explícita; ninguna es
automática):

1. Activar `VOICE_ALLOW_FORCED_ALIGNMENT=true`, o
2. corregir el guion y volver a exportarlo con el módulo 1, o
3. cambiar de voz, modelo o parámetros.

Las tres cambian el *fingerprint* de la ejecución, así que **hay que usar una
`--voice-key` nueva**: repetir la misma clave con otra solicitud produce
conflicto a propósito. Los clips ya pagados siguen en SQLite bajo su ejecución
anterior.

### Sonido opcional

Catálogo local con `asset_id`, ruta, hash y nota de licencia. **Nada se
descarga** y las descripciones del guion no se interpretan como rutas ni URLs:
solo se usan los assets declarados y el mapeo explícito. Los efectos y la música
están **desactivados por defecto**; una indicación sin asset produce un aviso
informativo (`blocking=false`) y el montaje puede seguir solo con narración.
`scene_start` es el comienzo real de la escena y `scene_end`, el final del clip
hablado antes de su pausa. La mezcla definitiva es del módulo 4.

### Presupuestos, reanudación y disco

| Ajuste | Por defecto | Unidad |
| --- | --- | --- |
| `VOICE_MAX_REQUESTS_PER_JOB` | 24 | peticiones **por trabajo**, no por proceso |
| `VOICE_MAX_TRANSPORT_RETRIES` | 2 | reintentos además del intento inicial |
| `VOICE_REQUEST_TIMEOUT_SECONDS` | 60 | segundos |
| `VOICE_MAX_REQUEST_CHARS` | 2500 | caracteres por petición |
| `VOICE_MAX_RESPONSE_BYTES` | 10 MiB | bytes por respuesta HTTP |
| `VOICE_MAX_JOB_STORAGE_MB` | 250 | MiB por trabajo de voz |
| `VOICE_ALIGNMENT_TOLERANCE_MS` | 20 | milisegundos |

Cada petición se **reserva en SQLite antes de enviarse**, así que reiniciar el
proceso no restablece el presupuesto; los reintentos y las alineaciones forzadas
cuentan igual. Se distinguen `requests_total` (histórico del trabajo) y
`requests_this_run`, de modo que reutilizar un resultado muestre **cero
solicitudes nuevas** sin perder el total.

Solo se reintentan fallos transitorios (conexión, timeout, 429 temporal, 5xx),
con retroceso y `Retry-After` acotados. Clave, permisos, parámetros, modelo o
crédito agotado **se detienen**. El cliente HTTP se construye con `retries=0`
para no duplicar reintentos.

> **Un timeout puede haber consumido crédito.** Por eso la reserva se persiste
> antes de enviar y se cuenta igual. Este proyecto **no promete facturación
> exactamente una vez**.

`--voice-key` da idempotencia: su *fingerprint* incluye el hash del guion, voz,
modelo, parámetros, formato, versión del procesamiento y modo simulado/real.
Misma clave y misma solicitud devuelven el resultado disponible sin peticiones;
un cambio con la misma clave produce conflicto. Los clips se persisten con su
identidad de síntesis (texto + contexto + parámetros) y solo se recuperan si
hash e identidad coinciden: **un fallo de exportación o de alineación no provoca
otra síntesis de una voz ya guardada**.

Se respetan `MIN_FREE_DISK_MB` y los límites de log del módulo 1. El base64 se
decodifica y se descarta enseguida —nunca entra en el JSON ni en los logs—, se
procesa un clip cada vez y el WAV maestro se construye por bloques. Los
temporales regenerables se borran tras consolidar; maestro, manifiesto y clips
se conservan.

### Admisión para el módulo 4: tres veredictos separados

`viralgen voice validate` revalida **desde los archivos reales**: el guion y su
propia admisión, el SHA-256 de sus bytes, el manifiesto, los hashes y el formato
de todos los WAV, la cobertura de escenas sin huecos, que las palabras cubran la
narración y caigan dentro del clip de su escena, el origen de ambos artefactos y
la duración medida frente al rango del perfil y al ±10 %.
**No se fía del booleano guardado en `voice.json`.** Es de **solo lectura**: no
modifica ningún archivo ni cambia el origen declarado.

El informe lleva **siempre los tres**, nunca uno solo:

| Campo | Qué significa |
| --- | --- |
| `contract_valid` | Los archivos cumplen su contrato y se pueden leer. Es lo mínimo; **no autoriza nada**. |
| `admissible_for_preview` | Además, todo cuadra: hashes, medios, cobertura, palabras y duración. Sirve para **CI y revisión de recorridos de prueba**. Ignora únicamente el origen (`origin_checks`: `voz_real`, `guion_real`). |
| `admissible_for_assembly` | Lo anterior **y** ambos artefactos son reales. **El único que autoriza producir medios.** |

`--allow-simulation` cambia **solo cuál de ellos decide el código de salida**,
para que CI pueda terminar en 0 con archivos de prueba. El resumen sigue
llevando `admissible_for_assembly: false` y `simulation: true`, y `checks` es
idéntico en los dos modos. Los módulos posteriores deben leer
`admissible_for_assembly`; **no pueden tomar el código de salida de una
validación de pruebas como autorización para producir**.

Ejemplo real sobre el ejemplo simulado incluido: `contract_valid: true`,
`admissible_for_preview: true`, `admissible_for_assembly: false`,
`preview_reasons: []` y `reasons` con los dos motivos de origen.

Un manifiesto `needs_review` se rechaza **también** en pruebas (`voz_lista` y
`sin_bloqueos` no están entre los `origin_checks`), y un hash o una estructura
incorrectos se rechazan en ambos modos. Los incumplimientos quedan
`needs_review`; no se alarga con silencio, bucles ni cambios de velocidad para
satisfacer una duración.

### Ejemplo simulado incluido

`examples/voz_simulada/` contiene un recorrido completo producido por el
**proveedor simulado** (`--mock --seed 2026`) sobre
`examples/ejemplo_cuento_infantil.json`: `voice.json`, `audio/narration.wav` y
los siete clips por escena (~4,6 MB). Se valida solo:

```bash
viralgen voice validate \
  --script examples/ejemplo_cuento_infantil.json \
  --manifest examples/voz_simulada/voice.json --allow-simulation
```

El PCM son **señales de prueba** (un tono con envolvente) generadas con la
biblioteca estándar, **no voz hablada**, y sus tiempos por carácter son
sintéticos: sirven para ejercitar el recorrido y las pruebas, no son evidencia
de la precisión del proveedor real ni de su sincronización.

Ese comando termina en 0 porque `admissible_for_preview` es `true`; el mismo
resumen sigue diciendo `admissible_for_assembly: false`. Sin
`--allow-simulation` el código de salida es 10, que es lo correcto.

El ejemplo sirve para comprobar aritmética, hashes y recuperación:
1 211 462 muestras / 24 000 Hz = 50,477583 s, y esa cifra coincide con la suma
de `clip_samples + pause_samples` de las siete escenas. **Eso verifica la
aritmética, no la calidad de una voz hablada.**

### Reproducir por perfil

```bash
PACK=src/viralgen/data/facts_demo.json

# 1) Infantil (ficción: sin catálogo de hechos).
viralgen generate --profile infantil_cuentos --topic "aprender a compartir" \
  --job-key voz-infantil --mock --seed 5
# 2) Curiosidades corto y largo (exigen catálogo).
viralgen generate --profile curiosidades_corto --topic "pieza que reparte la fuerza" \
  --source-pack "$PACK" --job-key voz-corto --mock --seed 5
viralgen generate --profile curiosidades_largo --topic "pieza que reparte la fuerza" \
  --source-pack "$PACK" --job-key voz-largo --mock --seed 5

# Cada comando imprime su script_path en el resumen JSON. Con él:
viralgen voice generate --script <ruta/script.json> --voice-key v-001 --mock --seed 5

# Repetir EXACTAMENTE el mismo comando devuelve "reused": true y "requests_new": 0.
viralgen voice generate --script <ruta/script.json> --voice-key v-001 --mock --seed 5
```

---

## 16. Módulo 3: medios visuales

Toma un `script.json` **admitido** y su `voice.json` **admitido** y produce los
medios: una imagen por escena, un clip por cada escena `asset_type: video`, un
conjunto versionado de referencias de personaje, una hoja de contacto y el
manifiesto `media.json`. Contrato completo para quien monte:
[`docs/contrato_modulo_3_visuales.md`](docs/contrato_modulo_3_visuales.md).

**No monta, no mezcla audio, no pone subtítulos y no publica.** Termina en
archivos locales más un manifiesto.

### Decisión de contrato

Igual que en el módulo 2: ni `script.json` ni `voice.json` se tocan. Los medios
viven en un manifiesto lateral `media.json` (`document_type="media_manifest"`,
`schema_version="1.0"`), vinculado por `job_id`, `voice_run_id` y el **SHA-256
de los bytes exactos** de los dos archivos de entrada.

`voice.json` es la **única fuente de tiempos medidos**. El módulo 3 no mide
audio y no calcula duraciones propias: copia el reloj (`sample_rate_hz`,
`total_samples`) y los límites por escena en **muestras**, y deriva los segundos
al exportar.

### Uso

```bash
# Preflight: qué se va a pedir, a quién y cuánto cuesta. NO llama a nadie.
viralgen media plan --script <script.json> --voice <voice.json> --mock

# Simulación: sin claves, sin red y sin FFmpeg (solo imágenes).
viralgen media generate --script <script.json> --voice <voice.json> \
  --media-key demo-001 --mock --seed 2026

# Real (requiere OPENAI_API_KEY + OPENAI_IMAGE_MODEL; Runway solo si hay clips).
viralgen media generate --script <script.json> --voice <voice.json> \
  --media-key real-001

# Auditoría del trío guion + voz + medios. No genera nada ni llama a nadie.
viralgen media validate --script <script.json> --voice <voice.json> \
  --manifest <media.json>

# Contrato exportable.
viralgen media schema --output schema/media.schema.json
```

`media plan` es el preflight y contesta antes de gastar: modelo de imagen y de
vídeo, tamaño por escena, duración solicitada por clip, caracteres de cada
prompt, qué hay ya en caché (`image_cache_hit`, `video_cache_hit`), qué
credenciales faltan (`missing_credentials`), qué herramientas faltan
(`missing_tools`), disco libre, presupuestos e incidencias. `can_run` dice si
merece la pena invocar `generate`.

### Proveedores implementados

Tres adaptadores, ni uno más:

| Proveedor | Para qué | Endpoints usados |
| --- | --- | --- |
| **OpenAI Images** | Imágenes de escena y referencias. | `POST /v1/images/generations` (JSON, sin referencias) y `POST /v1/images/edits` (multipart, un campo `image[]` por referencia). |
| **Runway** | Clips a partir de una imagen inicial. | `POST /v1/image_to_video` (crea la tarea) y `GET /v1/tasks/{id}` (consulta esa tarea). Cabeceras `Authorization: Bearer …` y `X-Runway-Version`. |
| **Simulado** | Recorrido completo sin red, sin claves y sin coste. | — |

Modelos: los identificadores **los aporta la configuración**, no el código.
`OPENAI_IMAGE_MODEL` es independiente de `OPENAI_MODEL` (el del guion): un
modelo de texto no sirve para imágenes y el proyecto **no lo sustituye en
silencio**. Los puntos de partida compatibles con las referencias consultadas
son `gpt-image-1` y `gen4_turbo`; se declaran en `.env.example` **sin valor por
defecto** para que nadie los cambie sin darse cuenta.

`src/viralgen/media/capabilities.py` recoge lo que cada modelo admite (tamaños,
calidades, formatos, número de referencias, duraciones y relaciones de aspecto)
y **rechaza una petición imposible antes de enviarla**: no se gasta una llamada
para que el servidor diga que no.

### Un clip nunca se sustituye por una imagen

Si una escena pide `asset_type: video` y el clip no se puede producir —error,
falta de credenciales, presupuesto agotado, duración no admitida— el trabajo
queda **parcial** y **no se publica `media.json`**. Nunca se entrega una imagen
en su lugar, porque el manifiesto aparentaría cubrir una escena que no está
cubierta. El resumen lo dice con `partial: true`, `manifest_path: null`,
`pending_scenes` y `available_paths`; el trabajo sigue en SQLite y se retoma con
el mismo `--media-key`.

Tampoco se resuelve al revés: **no se reescribe un guion ya vinculado a un
`voice.json` para convertir sus escenas `video` en `image`**. Eso rompería el
hash y falsearía lo que se pidió.

### Tareas asíncronas y facturación

Los clips son tareas remotas. El adaptador crea la tarea, guarda su `task_id`
**real** y consulta el estado con un intervalo mínimo. Si la espera local se
agota, el comando termina con `media_status: waiting_remote` y **código 11**:
ni éxito ni fallo. Volver a invocar el mismo `--media-key` **retoma esa misma
tarea**; no se crea otra y **no se inventa ningún `task_id`**.

Ante un resultado desconocido (`outcome_unknown`: un timeout después de enviar,
una conexión cortada) la repetición automática queda **bloqueada**: puede haber
consumido crédito. El presupuesto se reserva y se persiste **antes** de enviar,
y se cuenta **por trabajo, no por proceso**: reiniciar no reinicia el contador.
**No se promete facturación exactamente una vez.**

### Qué se mide y qué se declara

Todo número del manifiesto dice de dónde sale, con un campo `*_source`:

| Valor | Significado |
| --- | --- |
| `local_measurement` | Medido aquí abriendo el archivo: Pillow para imágenes, `ffprobe` para clips. |
| `provider_reported` | Declarado por el proveedor. Informativo, no comprobado. |
| `montage_decision` | Decisión del proyecto, no un hecho del archivo. |

Las imágenes se **decodifican y verifican**: el tipo sale del contenido, no de
la extensión ni de lo que diga el proveedor. Los clips se miden con `ffprobe`
sobre el **stream de vídeo** —nunca sobre la pista de audio ni el campo de
duración del contenedor— y se comprueba su integridad decodificándolos. Una
imagen **nunca** se renombra a `.mp4` y no se inventa ningún resultado de
`ffprobe`: si `ffprobe` no está, las pruebas que lo necesitan **se saltan** y se
informa del salto.

### Geometría: contener o recortar, nunca deformar

El material principal se entrega **como lo devolvió el proveedor**, medido, más
un **plan** de presentación por escena (`policy`, `target_*`, `pad_color`,
`applied: false`). El reencuadre es del módulo 4, que tiene el contexto del
montaje. Las políticas son `contain` (relleno lateral) y `crop` (recorte):
**no existe la opción de estirar**. La única geometría ya aplicada está en
`assets[].transformation` de los derivados, por ejemplo la semilla que se envía
a Runway, y ahí sí está hecha y no debe repetirse.

### Referencias de personaje versionadas

El conjunto de referencias es **persistente y versionado**, y se **fija al
empezar** el trabajo: todas las escenas reciben las mismas, para que la
continuidad no dependa del orden de generación. Su versión se deriva del hash de
la biblia de serie: si cambia la descripción de un personaje, **nace una versión
nueva** en vez de reutilizar una referencia que ya no corresponde.

Cada entrada lleva procedencia declarada: `generated` (creada aquí) o `imported`
(aportada como archivo local, con `rights_declaration`). El proyecto **no
descarga imágenes de terceros**.

Esto persigue la continuidad; **no la verifica**. Nada en el manifiesto mide si
el personaje es reconocible entre escenas.

### Identidad, caché e idempotencia

`--media-key` es la clave de idempotencia del **trabajo**: repetir el mismo
comando devuelve `reused: true` sin gastar llamadas. La **caché de assets** es
distinta: un asset se identifica por su contenido —prompt efectivo, referencias
por hash, operación, proveedor, modelo, parámetros y modo—, no por la ejecución
que lo pidió. Los límites administrativos (presupuestos, tamaños) quedan fuera
de esa identidad a propósito: cambiarlos no debe invalidar un asset.

Los archivos entran en el paquete por **enlace duro** desde la caché, de forma
que borrar el índice de la caché no deja el paquete sin medios. Y como la ruta
del archivo en caché se deriva de su identidad, una caída entre escribir el
archivo y anotar el punto de control **adopta el archivo ya validado** en vez de
regenerarlo.

### Admisión para el módulo 4: tres veredictos separados

Igual que en voz, y por la misma razón:

| Campo | Qué autoriza |
| --- | --- |
| `contract_valid` | Nada. Los tres archivos se leen, cumplen su esquema, sus vínculos cuadran y cada archivo referenciado **decodifica**. |
| `admissible_for_preview` | Revisión y CI de recorridos de **prueba**. Ignora **únicamente** `origin_checks` (`guion_real`, `voz_real`, `medios_reales`). |
| `admissible_for_assembly` | **Montar.** Es el que debe leer el módulo 4. |

`--allow-simulation` elige solo el modo y su código de salida: **nunca cambia
`checks`** ni convierte `admissible_for_assembly` en `true`. El validador
revalida desde los bytes reales y **no se fía** del booleano guardado en el
manifiesto. Comprueba además que ninguna ruta se salga del paquete, incluidos
los escapes por enlace simbólico.

`media_status: ready` es preparación **técnica**. La revisión artística es otra
cosa y vive en `visual_review`, que empieza en `not_performed`: **nadie ha
mirado las imágenes**. La hoja de contacto existe para esa mirada humana y **no
forma parte del montaje**.

### Ejemplo simulado incluido

`examples/visuales_simulados/` contiene un recorrido completo producido por el
**proveedor simulado** (`--mock --seed 2026`): `script.json`, `voz/voice.json`
con su audio y `medios/media.json` con las cinco imágenes de escena, la
referencia de personaje y la hoja de contacto (~2,7 MB en total, de los cuales
2,4 MB son el WAV de la narración). Se valida solo:

```bash
viralgen media validate \
  --script examples/visuales_simulados/script.json \
  --voice examples/visuales_simulados/voz/voice.json \
  --manifest examples/visuales_simulados/medios/media.json \
  --allow-simulation
```

Resultado real: `contract_valid: true`, `admissible_for_preview: true`,
`admissible_for_assembly: false`, `preview_reasons: []` y `reasons` con los tres
motivos de origen. Duración medida: 611 272 muestras / 24 000 Hz = 25,469667 s,
que coincide con la suma de las cinco escenas. **Eso verifica la aritmética, los
hashes y la cobertura, no la calidad de ninguna imagen.**

**Por qué un paquete nuevo y no el ejemplo que ya había**:
`examples/ejemplo_cuento_infantil.json` tiene escenas `asset_type: video`, y un
clip **no se sustituye por una imagen** (ver arriba). Generarlo aquí exigiría
`ffprobe`, que no está instalado, así que el trabajo habría quedado parcial y
sin manifiesto —que es el comportamiento correcto, pero no sirve de ejemplo de
`media.json`—. Se generó por eso un guion corto de 25 s **solo de imágenes**,
con el catálogo de perfiles `examples/perfiles_solo_imagenes.json`
(`video_scene_budget: 0`, una decisión de configuración legítima, tomada
**antes** de generar el guion y no un guion existente reescrito).

Las imágenes son **placeholders dibujados con Pillow**, rotulados
`SIMULACION - NO ES UNA IMAGEN REAL`. No son salidas de ningún modelo
generativo.

### Reproducir ese ejemplo

```bash
export VIRALGEN_DATA_DIR=/tmp/viralgen-ejemplo
export VIRALGEN_PROFILES_PATH="$PWD/examples/perfiles_solo_imagenes.json"

viralgen generate --profile curiosidades_corto \
  --topic "por que la cremallera no se suelta" --duration 25 \
  --source-pack src/viralgen/data/facts_demo.json \
  --job-key ejemplo-visual --mock --seed 2026
# El resumen imprime script_path; con él:
viralgen voice generate --script <script.json> \
  --voice-key ejemplo-visual-voz --mock --seed 2026
# El resumen imprime manifest_path; con él:
viralgen media generate --script <script.json> --voice <voice.json> \
  --media-key ejemplo-visual-001 --mock --seed 2026
```

### 16.1 Prueba con los proveedores reales de imagen y vídeo (pendiente)

**No se ha ejecutado.** No hay credenciales de OpenAI Images ni de Runway en
este entorno, `ffprobe` no está instalado y la red está cerrada. Los adaptadores
están implementados y cubiertos con **transporte HTTP simulado** —que verifica
ruta, cabeceras, multipart, cuerpo y sondeo de tareas, es decir **lo que sale de
esta máquina**—, y eso **no demuestra** que los servidores acepten nada.

Recorrido preparado, para ejecutarlo tal cual cuando haya credenciales, red y
FFmpeg:

```bash
sudo apt install -y ffmpeg      # aporta ffprobe, necesario para medir clips

# Las claves se introducen fuera del historial del shell.
read -rs -p "OPENAI_API_KEY: "    OPENAI_API_KEY    && export OPENAI_API_KEY
read -rs -p "RUNWAYML_API_SECRET: " RUNWAYML_API_SECRET && export RUNWAYML_API_SECRET

# Identificadores EXACTOS de la cuenta real. No hay valores por defecto.
export OPENAI_IMAGE_MODEL=<modelo de imagenes de tu cuenta>
export RUNWAY_MODEL=<modelo de video de tu cuenta>
export RUNWAY_API_VERSION=2024-11-06

# Límites explícitos (son los valores por defecto; se fijan para dejar constancia).
export VIRALGEN_MEDIA_MAX_GENERATION_ATTEMPTS=24
export VIRALGEN_MEDIA_MAX_VIDEO_SCENES=2
export VIRALGEN_MEDIA_MAX_VIDEO_SECONDS=20
export VIRALGEN_MEDIA_MAX_STATUS_REQUESTS=120
export VIRALGEN_DATA_DIR="$PWD/.viralgen"

# 0) Preflight: qué se va a pedir y cuánto. NO llama a nadie.
viralgen media plan \
  --script .viralgen/jobs/<job_id>/script.json \
  --voice  .viralgen/jobs/<job_id>/voice/<voice_run_id>/voice.json

# 1) Medios sobre un guion y una voz REALES ya admitidos (§13).
viralgen media generate \
  --script .viralgen/jobs/<job_id>/script.json \
  --voice  .viralgen/jobs/<job_id>/voice/<voice_run_id>/voice.json \
  --media-key real-medios-001

# 2) Si sale "waiting_remote" (código 11): repetir el MISMO comando. Retoma la
#    misma tarea remota por su task_id; no crea otra.

# 3) Auditoría del trío. Aquí es donde debe salir admissible_for_assembly: true.
viralgen media validate \
  --script   .viralgen/jobs/<job_id>/script.json \
  --voice    .viralgen/jobs/<job_id>/voice/<voice_run_id>/voice.json \
  --manifest .viralgen/jobs/<job_id>/media/<media_run_id>/media.json

# 4) Idempotencia: mismo comando -> "reused": true y 0 intentos nuevos.
viralgen media generate \
  --script .viralgen/jobs/<job_id>/script.json \
  --voice  .viralgen/jobs/<job_id>/voice/<voice_run_id>/voice.json \
  --media-key real-medios-001
```

Qué habrá que entregar de esa ejecución: el `media.json` completo, las imágenes
y los clips, los modelos usados, los `task_id` reales que devuelva Runway, los
intentos nuevos y totales, los segundos de vídeo reservados, las duraciones
medidas con `ffprobe` frente a las solicitadas y el resultado de la validación.
**Hasta entonces, nada de este recorrido se presenta como ejecutado.**

---

## 17. Módulo 4: montaje local, subtítulos y exportación

Toma un `script.json`, un `voice.json` y un `media.json` **admitidos** y produce
el vídeo. Contrato completo para quien publique:
[`docs/contrato_modulo_4_montaje.md`](docs/contrato_modulo_4_montaje.md).

**No publica, no programa y no consulta analíticas.** Termina en un MP4, un
`captions.ass`, unos fotogramas de inspección y `render.json`.

### Decisión de contrato

Igual que en los módulos anteriores: ninguna entrada se toca. `script.json`
sigue en el esquema 1.0 y su `video.actual_duration_s` **sigue siendo `null`**;
`render.json` describe el **archivo exportado**, que es otra cosa.

### Uso

```bash
# Preflight: valida, calcula fotogramas y subtítulos, estima espacio. NO codifica.
viralgen render plan --script <script.json> --voice <voice.json> --media <media.json>

# Montaje de producción (exige admisión completa de toda la cadena).
viralgen render generate --script <script.json> --voice <voice.json> \
  --media <media.json> --render-key real-001

# Montaje preview (admite fuentes simuladas; marca el vídeo y nunca publica).
viralgen render generate --script <script.json> --voice <voice.json> \
  --media <media.json> --render-key demo-001 --preview

# Auditoría del cuarteto. Solo lectura: mide y decodifica, no escribe.
viralgen render validate --script <script.json> --voice <voice.json> \
  --media <media.json> --manifest <render.json>

# Contrato exportable.
viralgen render schema --output schema/render.schema.json
```

**Ningún comando necesita credenciales.** Se montan archivos que ya existen;
una configuración de proveedores ausente no impide montar.

`render plan` contesta antes de gastar CPU: escenas, fotogramas por escena,
grupos y eventos de subtítulo, fuente elegida con su hash, capacidades de FFmpeg
que faltan (`missing_tools`), disco libre, presupuesto estimado e incidencias.
`can_render` dice si merece la pena invocar `generate`.

> Un plan con `can_render: false` por falta de un ejecutable **sigue siendo un
> plan aritmético válido**, pero las comprobaciones que necesitaban ese
> ejecutable quedan **no verificadas**, nunca aprobadas. Eso es distinto de una
> fuente comprobada como inadmisible, y el plan las distingue
> (`unverified_checks` frente a `issues`).

### El reloj: dos relojes y un exceso menor que un fotograma

`voice.json` manda. Se convierten los **límites acumulados**, nunca cada
duración por separado:

```
start_frame  = ceil(start_sample × F / S)
end_frame    = ceil(end_sample   × F / S)      # exclusivo
total_frames = ceil(N × F / S)
```

Con fronteras en 0 / 1,01 / 2,02 / 3,03 s a 30 fps salen fronteras
**0 / 31 / 61 / 91** y segmentos de **31, 30 y 30** fotogramas. Redondear cada
duración por separado daría 93, y eso es exactamente el error que se evita. El
`ceil` se calcula con enteros: a 48 kHz y varios minutos, `math.ceil(a/b)` puede
caer del lado equivocado de un entero exacto.

Las escenas **particionan** [0, `total_frames`) sin huecos ni solapes, y ninguna
puede quedarse en cero fotogramas. El exceso visual final está siempre en
[0, 1/F) y se registra como `quantization_excess_s`.

**La narración no se acelera, no se recorta y no se alarga** para cuadrar. La
pequeña diferencia entre el final del audio y el del vídeo es esa cuantización,
documentada. No se usa `-shortest`: taparía una pista truncada en vez de
delatarla.

### Montaje visual: un segmento por escena

Cada escena se procesa y **se valida antes de empezar la siguiente**: si un
segmento no tiene los fotogramas exactos o el tamaño objetivo, el trabajo se
detiene ahí.

Geometría: `contain` (relleno lateral) o `crop` (recorte). **Nunca deformación.**
Una transformación que el módulo 3 ya incorporó a un derivado **no se repite**.
Una política desconocida se rechaza: el guion no puede colar una expresión de
FFmpeg por esta vía.

Movimiento de cámara determinista y **opcional**, sin alterar la duración:
`static` por defecto, y `zoom_in` de hasta 1,04 solo cuando el estilo es
dinámico **y** hay un recorte que lo permite. Las escenas de vídeo conservan su
movimiento original y no reciben otro. Un `contain` nunca se recorta para
simular movimiento.

Cortes directos, sin intros, outros, transiciones ni fundidos que cambien el
reloj. El loop narrativo se conserva por la secuencia existente: **no se añade
una repetición al final y no se promete continuidad visual perfecta entre
extremos**.

Los segmentos se codifican con ajustes **homogéneos** (mismo codec, resolución,
formato de píxel, fps, base temporal, GOP cerrado de 2 s y `scenecut`
desactivado), que es lo que permite concatenarlos con `concat` **copiando el
stream** en vez de recodificar. La lista de `concat` se escribe con nombres
relativos y FFmpeg se ejecuta con ese directorio como `cwd`: así no puede
referirse a rutas de fuera ni a una URL.

Formato de salida: **MP4 no fragmentado, H.264 (`libx264`), `yuv420p`, SAR 1:1,
CFR 30/1, AAC-LC a 48 000 Hz**, orientación neutra y `+faststart`. Se empieza en
`veryfast` y CRF 21, configurables. **El tamaño final se mide**; CRF no lo
promete.

### Subtítulos

`captions.ass` se genera desde la **alineación que ya existe** en `voice.json`:
no se transcribe, no se inventan timestamps y no se vuelve a pedir voz.

Grupos de 2 a 5 palabras, máximo dos líneas, cortando también por las pausas que
el módulo 2 midió. Los subtítulos completos cubren **todas** las palabras.
`PlayResX=1080`, `PlayResY=1920`, y una región de texto en x 96..984, y
1080..1530 — una **decisión de diseño, no una zona segura** garantizada en
ninguna plataforma. La marca de preview vive arriba, fuera de esa región.

| Perfil | Estilo |
| --- | --- |
| Curiosidades (`dynamic_emphasis`) | Letra gruesa de 76, texto claro con borde oscuro, **palabra activa resaltada** y una entrada breve del grupo a escala 106 % durante 120 ms. |
| Infantil (`calm_readable`) | Tamaño 72, contraste alto, **grupos estables** y sin resaltado por palabra: nada de sacudidas ni saltos. |

Son **valores iniciales para ajustar mirando el render**, no un algoritmo de
viralidad.

Mientras cambia la palabra activa **solo cambia el color**: la caja y los saltos
de línea se quedan quietos, para que la frase no salte de lado en cada palabra.
En los huecos el grupo sigue visible sin palabra activa, y el resaltado no se
queda pegado durante una pausa larga ni se adelanta al grupo siguiente.

Una palabra legítima muy larga (`extraordinariamente` mide 889 px a tamaño 76 y
la región tiene 888) **encoge de forma acotada** hasta el 80 % antes de darse
por vencida: bloquear un render entero por una palabra del español sería peor.
Si ni al mínimo cabe, se bloquea; **nunca se recorta el texto ni se elimina una
palabra**.

Tres unidades que no se mezclan: eventos ASS en **centésimas**,
transformaciones `\t(...)` en **milisegundos relativos al evento**, alineación
original en **segundos globales**. Política única de redondeo: se cuantiza cada
**frontera** con `floor(t*100 + 0.5)`, nunca la duración; como dos eventos
contiguos comparten el valor de origen, no hay solapes ni deriva acumulada. Una
palabra cuyo resaltado colapsa conserva su texto y se anota en
`collapsed_highlights`: no se fabrica duración de voz.

**El texto del guion es dato.** Las etiquetas salen solo de plantillas de este
módulo; `{` y `}` se escapan, y una barra invertida **bloquea con motivo** en
vez de convertirse en una directiva `\N` de libass.

La fuente se fija por **archivo** (no por nombre de familia), se registra su
SHA-256 y se comprueba que cubre los caracteres realmente usados más los del
español (`áéíóúüñ¿¡«»—…`). Un glifo ausente saldría como cuadro vacío, así que
bloquea antes de renderizar.

### Audio

`narration.wav` se usa **una sola vez**: los clips por escena ya están dentro
del maestro, y volver a concatenarlos duplicaría la voz y sus pausas. El audio
de los clips de vídeo **nunca** entra en la mezcla (`use_source_audio=false`).

Solo se consumen `sound_cues` **ya resueltos y verificables** del manifiesto de
voz, y se comprueba su hash antes de mezclar. Un asset obligatorio que falta es
un **error**, no una excusa; una indicación que el módulo 2 dejó sin resolver
conserva su aviso y **no dispara ninguna descarga**. Un asset más corto que su
intervalo **no se repite** automáticamente.

La mezcla de trabajo va a 48 000 Hz. Con narración a 24 000 Hz la conversión es
exactamente **2×**, así que hay `2N` muestras por canal sin redondeo; para otras
frecuencias se aplica una conversión racional y se declara el factor. **La
narración no se ajusta a la duración redondeada del vídeo.**

Normalización EBU R128 en dos pasadas, activada por defecto: **−16 LUFS** y
techo de **−1,5 dBTP**, con la frecuencia de salida fijada explícitamente
(`loudnorm` remuestrea a 192 kHz por dentro). Son objetivos **propios del
proyecto**, no requisitos de ninguna plataforma. Se comprueban sobre el audio
**final decodificado** con tolerancia de ±1 LU y pico máximo −1 dBTP; un
incumplimiento deja el candidato en `needs_review` y **no abre un bucle de
recodificación**. Si la señal es silenciosa o el análisis no sirve, se dice y
**no se inventa una medida**.

### Validación del archivo terminado

Que FFmpeg termine con código 0 no basta. Sobre el archivo se comprueba:

- codec, dimensiones, SAR/DAR, fps racional, orientación y formato de píxel;
- la cuenta de fotogramas **contando, no leyendo la cabecera**, y exigiendo
  exactamente `total_frames`;
- PTS de **presentación** consistentes con CFR (con B-frames el orden de
  decodificación no es el de presentación: se ordena por PTS);
- **decodificación completa de ambas pistas** a salida nula — lo único que
  demuestra que el archivo no está truncado;
- que el audio contenga la narración entera. La cuenta de muestras sale de
  **decodificar el PCM**, no de la duración que declara el contenedor: en un
  AAC real difieren (en el ejemplo, 1 222 512 declaradas frente a 1 222 656
  decodificadas). Se admite un margen técnico de **un frame AAC (1024
  muestras)** para el relleno del codec; ese margen **no tapa voz truncada ni
  convierte una medición equivocada en correcta**. El signo importa: déficit
  negativo = falta narración (defecto); positivo = sobra relleno (normal);
- que todo evento de subtítulo venga de palabras de la voz, quepa en el reloj y
  use un estilo declarado;
- fotogramas de muestra en gancho, frontera de escena (y el anterior), medio y
  cierre, como **evidencia** de texto integrado y de la marca de preview.

> Un detector de píxeles no certifica legibilidad ni corrección semántica.
> `inspection.checks_performed` dice exactamente qué se comprobó.

Si falta un segmento, falla la decodificación o hay discrepancia de reloj, **no
se publica un `render.json` ready**. Un candidato completo con incumplimientos
medidos se conserva como `needs_review`, y eso lo rechaza también para preview.

### Admisión para el módulo 5: tres veredictos separados

| Campo | Qué autoriza |
| --- | --- |
| `contract_valid` | Nada. Los cuatro archivos se leen, sus vínculos y hashes cuadran y el MP4 decodifica entero. |
| `admissible_for_preview` | Revisión y CI de recorridos de **prueba**. Ignora **únicamente** `origin_checks`. |
| `admissible_for_publisher` | **El módulo 5 puede recibir el archivo.** Exige `render_mode=production`, `simulation=false` y origen real. |

`origin_checks = {guion_real, voz_real, medios_reales, render_real,
modo_produccion}`.

`admissible_for_publisher` significa que el módulo 5 **puede recibir** el
archivo. **No certifica monetización, calidad editorial ni permiso para
publicarlo.**

`--allow-simulation` elige solo el modo del informe y su código de salida:
**nunca cambia `checks`** ni habilita al publicador.

**Una salida preview no llega nunca al publicador**, aunque todas sus fuentes
fueran reales: lleva una marca `PREVIEW` incrustada en los píxeles, y eso no se
quita del archivo. Preview usa **la misma resolución y las mismas
comprobaciones técnicas** que producción, para que el ejemplo ejercite el
recorrido final de verdad.

En preview el archivo se llama **`preview.mp4`** y en producción `video.mp4`: el
nombre dice lo que es, y así nadie sube un preview por descuido.

### Reanudación, recursos y limpieza

`--render-key` es la clave de idempotencia. Su *fingerprint* incluye los hashes
de las tres fuentes, el modo, el objetivo, los codecs, los estilos, la fuente
tipográfica, la mezcla y las versiones del pipeline y del motor. Misma clave y
misma configuración **recuperan el resultado** (`renders_new: 0`); un cambio da
**conflicto**. Comprobar el archivo con `ffprobe` **no cuenta** como
codificación nueva.

La identidad de **etapa** es independiente de la de la ejecución, así que un
fallo de muxing no obliga a recodificar las escenas. Un segmento preview nunca
vale para una salida de producción: el modo entra en su identidad.

Tras consolidar y validar, se limpian los intermedios regenerables **de este
render**, y se retiran a la vez sus filas de caché. **La limpieza no puede
invalidar el paquete**: el MP4, el `captions.ass`, los fotogramas y el
manifiesto viven fuera del área de trabajo, y `validate` y la reutilización
siguen funcionando después. Los hashes de lo borrado se conservan en
`render.json` como dato histórico, **no como archivos obligatorios**. Ante un
fallo no se limpia nada: las etapas validadas se conservan para reanudar.

Todos los subprocesos se lanzan con **lista de argumentos, `shell=False` y
`stdin` cerrado**, con timeout por etapa, logs acotados y terminación del
**grupo** de procesos —matar solo al padre dejaría hijos codificando—. El
progreso legible por máquina va a su propio archivo, nunca al `stdout` de la
CLI, que lleva el resumen JSON.

| Parámetro | Valor inicial |
| --- | --- |
| `RENDER_WORKERS` | 1 |
| `RENDER_FFMPEG_THREADS` | 2 (los hilos de filtros se limitan aparte) |
| `RENDER_MAX_DURATION_S` | 120, además de las reglas del perfil |
| `RENDER_STAGE_TIMEOUT_S` | 600 |
| `RENDER_JOB_TIMEOUT_S` | 1800 por invocación |
| `RENDER_MAX_ATTEMPTS_PER_STAGE` | 2, persistidos |
| `RENDER_MAX_WORK_MIB` | 1536 (pico simultáneo: segmentos, mezcla, candidato y temporales) |
| `RENDER_MAX_OUTPUT_MIB` | 200 |
| `RENDER_LOG_MAX_MIB` | 5 por ejecución |

`MIN_FREE_DISK_MB` se respeta **antes y durante** la ejecución. El crecimiento
del archivo de salida se vigila mientras se escribe: CRF no impone tamaño, así
que si se pasa del presupuesto el proceso se detiene y **el candidato queda
inválido a propósito**. Un archivo truncado por un límite no se presenta como
exportación correcta.

### Ejemplo real incluido

`examples/montaje_preview/` es un **MP4 auténtico**, producido por FFmpeg sobre
el paquete `examples/visuales_simulados/` (~2,0 MB):

```
examples/montaje_preview/
├── preview.mp4        # 1,6 MB · 1080x1920 · 30 fps · 765 fotogramas
├── render.json        # el manifiesto de este render
├── captions.ass       # artefacto de diagnostico
└── frames/            # 5 fotogramas de inspeccion (gancho, frontera, medio, cierre)
```

Se valida solo, contra sus entradas:

```bash
viralgen render validate \
  --script examples/visuales_simulados/script.json \
  --voice  examples/visuales_simulados/voz/voice.json \
  --media  examples/visuales_simulados/medios/media.json \
  --manifest examples/montaje_preview/render.json --allow-simulation
```

Resultado real: `contract_valid: true`, `admissible_for_preview: true`,
`admissible_for_publisher: false`, `preview_reasons: []`. Sin
`--allow-simulation` el código de salida es **10**, que es lo correcto.

Cifras medidas de ese archivo: **765 fotogramas** (= `ceil(611272 × 30 / 24000)`),
25,5 s de vídeo frente a **25,469667 s** de narración —un exceso de cuantización
de 0,030333 s, por debajo de 1/30 = 0,033333—, **−16,01 LUFS** integrados y
**−12,83 dBTP** de pico.

> **El MP4 es real; su contenido sigue siendo simulado.** Las imágenes son
> placeholders de Pillow rotulados `SIMULACION - NO ES UNA IMAGEN REAL` y el
> audio son tonos de prueba, no voz hablada. Que FFmpeg lo renderice
> correctamente acredita **transporte temporal, mezcla y aritmética**, no
> inteligibilidad ni calidad visual. Por eso lleva la marca
> `PREVIEW · SIMULACIÓN` incrustada.

### Reproducir ese recorrido

```bash
export VIRALGEN_DATA_DIR=/tmp/viralgen-montaje
E=examples/visuales_simulados

# 1) Preflight local, sin codificar.
viralgen render plan --script $E/script.json --voice $E/voz/voice.json \
  --media $E/medios/media.json --preview

# 2) Render preview real.
viralgen render generate --script $E/script.json --voice $E/voz/voice.json \
  --media $E/medios/media.json --render-key ejemplo-preview-001 --preview

# 3) Admisible para preview (exit 0). El resumen imprime manifest_path.
viralgen render validate --script $E/script.json --voice $E/voz/voice.json \
  --media $E/medios/media.json --manifest <render.json> --allow-simulation

# 4) Rechazado para publicacion (exit 10), que es lo correcto.
viralgen render validate --script $E/script.json --voice $E/voz/voice.json \
  --media $E/medios/media.json --manifest <render.json>

# 5) Repetir el paso 2: "reused": true y "renders_new": 0.
```

### 17.1 Montaje de producción (pendiente de fuentes reales)

El recorrido de **producción** no se ha ejecutado, y no por falta de FFmpeg:
FFmpeg está y funciona. Falta lo anterior en la cadena. `render generate` sin
`--preview` exige `admissible_for_assembly` en guion, voz y medios, y eso
requiere las integraciones externas de §13 y §16.1, que siguen pendientes por
falta de credenciales y de red.

**El montaje no llama a ningún proveedor.** Una vez existe un paquete real y
admisible, renderiza en local: no vuelve a pedir guion, ni voz, ni imágenes.
Las credenciales hacen falta **antes**, para producir ese paquete.

Qué proveedor hace falta depende del guion:

| Para producir… | Hace falta |
| --- | --- |
| Guion e imágenes de escena | OpenAI (`OPENAI_API_KEY` + `OPENAI_MODEL` y `OPENAI_IMAGE_MODEL`) |
| Narración | ElevenLabs (`ELEVENLABS_API_KEY` + `ELEVENLABS_MODEL_ID` + un `voice_id` de tu cuenta) |
| Clips de vídeo | Runway — **solo si el guion pide escenas `asset_type: video`** |

Un primer caso real de **cuentos compuesto solo por imágenes** no necesita
Runway en absoluto: basta OpenAI para guion e imágenes y ElevenLabs para la
voz. Es exactamente la forma del catálogo
`examples/perfiles_solo_imagenes.json`, con `video_scene_budget: 0`. Runway
entra únicamente cuando el guion solicita clips que ese adaptador deba
generar.

Cuando existan esas fuentes reales, el recorrido es el mismo cambiando dos
cosas: sin `--preview` y con otra `--render-key`.

```bash
viralgen render plan --script <real/script.json> --voice <real/voice.json> \
  --media <real/media.json>
viralgen render generate --script <real/script.json> --voice <real/voice.json> \
  --media <real/media.json> --render-key produccion-001
viralgen render validate --script <real/script.json> --voice <real/voice.json> \
  --media <real/media.json> --manifest <real/render.json>
```

Solo entonces `admissible_for_publisher` puede ser `true`. Hasta ese momento,
**ningún archivo de este repositorio es publicable**, y el módulo 4 lo dice en
cada resumen.

---

## 18. Módulo 5: publicación y programación

Toma un paquete de montaje **ya admitido** y lo entrega a los destinos que el
operador **haya autorizado**. Documentación completa del módulo:
[`docs/modulo_5_publicacion.md`](docs/modulo_5_publicacion.md).

**No genera contenido, no monta y no edita los documentos de origen.** Y no
publica nada sin una autorización explícita atada a esa intención exacta.

### Tres permisos distintos

| Concepto | Quién decide | Qué significa |
| --- | --- | --- |
| Admisión técnica | el código, sobre los archivos | el paquete cumple sus contratos |
| Autorización del operador | una persona de esta instalación | esto, a esta cuenta, con este texto, a esta hora |
| Estado remoto | la plataforma | qué ha pasado de verdad |

Un `ready` del módulo 4 no da permiso para subir nada. Un `exit 0` de `plan`, de
la simulación o de la exportación **no significa que se haya publicado**.

### Tres modos y tres veredictos

| Modo | Red | Envíos | Exige |
| --- | --- | --- | --- |
| `plan` | no | no | nada: su trabajo es decir qué falta |
| `mock` | **no** | simulados | `admissible_for_simulation` |
| `real` | sí | reales | `admissible_for_real_dispatch` |

Elegir un modo **no cambia** lo que es admisible: la admisión no recibe el modo.
El modo solo decide **qué veredicto se exige**, y por eso un preview se puede
simular y nunca enviar.

### Capacidades por plataforma

| | YouTube Shorts | Instagram Reels | TikTok |
| --- | --- | --- | --- |
| Transporte | Data API v3, subida reanudable de 8 MiB | Graph con Facebook Login, `video_url` | exportación manual |
| Almacenamiento temporal | no | **sí** (bucket privado, URL firmada 2 h) | no |
| Programación remota | no (`publishAt` no se envía) | no | no |
| Verificación | `videos.list` | consulta del medio | **ninguna** |
| Estado típico | `delivered` | `delivered` | `awaiting_manual` → `manually_reported` |

TikTok no tiene integración remota porque sus directrices de Direct Post
**excluyen** las utilidades privadas para cuentas propias o del equipo. No hay
interruptor que lo eluda, y las capacidades del adaptador lo declaran.

### Uso

```bash
# 1) Borrador editorial. Sin red, sin OAuth, sin staging, sin envíos.
viralgen publish plan \
  --script <script.json> --voice <voice.json> \
  --media <media.json> --render <render.json> \
  --accounts <accounts.json> --mode mock --publish-key demo-001 \
  --destination "id=yt,platform=youtube_shorts,account=canal_demo,at=2026-09-24T18:30:00,tz=Europe/Madrid,visibility=private,notify=false"

# 2) El operador edita el borrador si falta algo (el plan dice exactamente qué).

# 3) Autorización de ESA revisión. Cubre también subir el MP4 al staging.
viralgen publish approve --plan <publication_plan.json> --operator <identidad>

# 4) Cola con el horario autorizado.
viralgen publish enqueue --plan <publication_plan.json>

# 5) Trabajador: procesa lo vencido y termina (suficiente para un timer).
viralgen publish worker --once --publish-key demo-001

# 6) Estado local; --refresh consulta en remoto de forma explícita.
viralgen publish status --publish-key demo-001 --out publication.json

# TikTok: exportar NO es publicar.
viralgen publish export-manual --publish-key demo-001 --destination tk
viralgen publish record-manual --publish-key demo-001 --destination tk --url <url>

# Contratos, comprobación de cuenta y limpieza.
viralgen publish validate --plan <publication_plan.json>
viralgen publish schema --out schema/
viralgen publish accounts check --plan <publication_plan.json>
viralgen publish gc            # enumera; con --apply borra lo propio
```

Ni `plan` ni `mock` necesitan credenciales. Para el modo real:
`viralgen auth youtube` (loopback + PKCE, desde un equipo con navegador) y
`viralgen auth instagram --from-file <token.json>` (ningún token se acepta como
argumento: quedaría en el historial).

### Demostración completa, entera en simulación

```bash
tools/demo_publicacion.sh              # crea su propio directorio temporal
tools/demo_publicacion.sh /ruta/datos  # o uno concreto
```

Recorre plan → edición del operador → autorización → cola con reloj de ensayo →
envío simulado de YouTube e Instagram → espera remota y continuación → estado
final → repetición sin duplicar → exportación de TikTok en `awaiting_manual` →
registro manual, y termina comprobando que **el mismo paquete en modo real se
rechaza antes de cualquier llamada**, staging incluido.

Resultado de la demostración (cifras reales de esta entrega):

| Destino | Estado | Identificador | Visible públicamente |
| --- | --- | --- | --- |
| `yt` (privado) | `delivered` | `mock_…` | `false` |
| `ig` (público) | `delivered` | `mock_…` | `true` |
| `tk` | `manually_reported` | el que aportó el operador | — |

`real_remote_id` es `null` en los tres: **nada de esto existe en ninguna
plataforma**, y el recibo no finge lo contrario.

### Lo que impide publicar de más

* La autorización queda atada a la **intención**, al MP4 y a los IDs de cuenta.
  Cambiar cuenta, vídeo, texto, privacidad, horario o destino crea una revisión
  nueva. Renovar un token de la misma cuenta o reemitir una URL temporal del
  mismo objeto **no** la invalida.
* Antes del primer byte se **rehashea el MP4**: los bytes, no un booleano.
* **Ventana de inicio de 15 minutos**: tras una caída larga, lo vencido pasa a
  `needs_review` en vez de publicarse de golpe.
* El mismo MP4 no se cuela en la misma plataforma y cuenta con otra clave. La
  identidad **no es el título**.
* Una operación mutante **no se reintenta a ciegas**: un timeout tras enviar
  bytes o pedir la publicación se resuelve **consultando** la sesión o el
  contenedor. Si no se puede resolver, `needs_reconciliation` y se dejan de crear
  cosas para ese destino.
* Las URLs firmadas, las URIs de sesión y los tokens viven en un directorio
  privado (0700, archivos 0600) **fuera del repositorio**, y los contratos
  rechazan en validación cualquier texto que las contenga.

### Límites locales ≠ cuotas de las plataformas

Un trabajador, 1 entrega real nueva por cuenta y día, 200 solicitudes por destino
(persistidas, incluyendo sondeos), 3 intentos por operación, sondeo cada 30 s con
backoff hasta 300 s, ventana remota de 3600 s, MP4 de hasta 200 MiB. **Son
decisiones del producto**; las cuotas reales de cada plataforma no están aquí y
no se deducen de memoria.

### Pendiente antes de operar en una VPS

El modo real está **bloqueado a propósito**: ninguna de las referencias de
protocolo citadas era alcanzable desde este entorno (403 del proxy de egreso), y
esa limitación vive en el código (`viralgen.publish.verification`), aparece en
`publish plan` y bloquea el destino afectado. Levantar un bloqueo exige
comprobar el parámetro contra su fuente, no que "parezca correcto".

Faltan además cuentas, permisos, credenciales y un paquete de producción; los
mocks **no los sustituyen**. Las unidades de systemd están **preparadas y no
instaladas** en [`deploy/systemd/`](deploy/systemd/), y el procedimiento para
probar después YouTube en privado e Instagram (que **tiene efecto real**) está en
[`docs/modulo_5_publicacion.md`](docs/modulo_5_publicacion.md).
