# Contrato del módulo 1 → módulo 2 (voz y sonido)

Documento de **lectura** para quien conecte la generación de voz. El módulo 1
no implementa nada de esto: solo deja escrito qué campos entrega, cuáles son
autoridad, cuáles son estimaciones y qué falta en el contrato actual.

Versión del contrato descrita: `schema_version = "1.0"`.
Esquema completo: [`schema/script.schema.json`](../schema/script.schema.json).

---

## 0. Puerta de entrada: no empieces si no admite

Antes de sintetizar nada, comprueba **las cinco condiciones a la vez**. Están
implementadas en `viralgen.validation.check_admission(...)` y expuestas en
`viralgen validate --input <script.json>` (campo `admissible_for_media`).

| # | Campo | Valor exigido |
| --- | --- | --- |
| 1 | — | el JSON valida contra `ScriptDocument` y no quedan problemas `fatal` |
| 2 | `schema_version` | `"1.0"` (o una versión que tu lector declare compatible) |
| 3 | — | exportación completa: el archivo se relee entero sin error |
| 4 | `control.production_status` | `"ready_for_production"` |
| 5 | `simulation` | `false` |

`production_status = "needs_review"` significa **borrador recuperable**, no
material de producción: consérvalo, muéstralo a una persona, pero no lo
sintetices automáticamente. `control.warnings` dice exactamente por qué.

`simulation = true` marca salidas del proveedor simulado. Nunca alimentan
producción, aunque salgan `ready_for_production`.

**Para el módulo 4 hay una segunda puerta**, sobre la pareja guion+manifiesto:
`viralgen.voice.admission.check_voice_admission(...)`, expuesta en
`viralgen voice validate --script … --manifest …`. Revalida el guion, compara
el hash de sus bytes, valida el manifiesto, comprueba los hashes y el formato
de todos los WAV, la cobertura de escenas sin huecos, que las palabras cubran
la narración y caigan dentro del clip de su escena, el origen real de ambos
artefactos y la duración medida frente al rango del perfil y al ±10 %.
**No se fía del booleano `admissible_for_assembly` guardado en el manifiesto.**

---

## 1. Identidad y trazabilidad

| Campo | Tipo | Para qué lo necesitas |
| --- | --- | --- |
| `job_id` | UUID | Clave para nombrar los audios y correlacionar trabajos. |
| `created_at` | fecha UTC | Orden y depuración. |
| `channel` | `infantil` \| `curiosidades` | Elegir voz y tono por canal. |
| `profile_id` | id | Perfil editorial de origen. |
| `language` | `es` | Idioma de la narración (los prompts visuales van aparte y **no** se locutan). |
| `control.prompt_version`, `control.config_hash` | texto | Comparar tandas y reproducir resultados. |
| `idea.experiment_tag` | id | Agrupar experimentos (versión de prompts + proveedor + modelo + perfil). |

---

## 2. Lo que hay que locutar

**Autoridad del texto: `scenes[].narration_text`.** `narration.full_text` es la
concatenación en orden de esos textos, separados por un espacio; el módulo 1 la
construye y la valida. Usa una u otra, pero no las mezcles.

| Campo | Tipo | Notas |
| --- | --- | --- |
| `scenes[].scene_id` | id (`sc_01`…) | Clave estable para nombrar cada pista. |
| `scenes[].order` | entero 1..N | Consecutivo y sin huecos. |
| `scenes[].narration_text` | texto | **Lo que se locuta.** No lo resumas ni lo reescribas. |
| `scenes[].pause_after_s` | 0–1,5 s | Silencio **al final** de la escena, no al principio. |
| `scenes[].beat` | `hook`/`context`/`development`/`resolution`/`close` | Permite modular intención por tramo. |
| `narration.full_text` | texto | Concatenación exacta, por si prefieres una sola pasada. |
| `narration.voice_direction` | texto | Intención de voz para toda la pieza. |
| `narration.pronunciation_notes` | lista de textos | Nombres propios y pausas concretas. |
| `narration.target_wpm` | entero | Ritmo **objetivo** usado para estimar, no una orden de velocidad. |

> No aceleres la voz para cuadrar la duración. Si el audio real se sale de
> rango, es el texto lo que debe ajustarse (o el trabajo debe devolverse).

---

## 3. Estimaciones contra las que comparar

Todo esto lo calcula el módulo 1 **en local** con
`60 × word_count / target_wpm + pause_after_s`. Son estimaciones, no medidas.

| Campo | Tipo | Notas |
| --- | --- | --- |
| `scenes[].word_count` | entero | Conteo local documentado (reglas en el README). |
| `scenes[].estimated_start_s` / `estimated_end_s` | segundos | Cadena continua desde 0, sin huecos ni solapes. |
| `scenes[].estimated_duration_s` | segundos | Incluye la pausa final. |
| `video.target_duration_s` | segundos | Objetivo del perfil (o `--duration`). |
| `video.estimated_duration_s` | segundos | Suma de las escenas. |
| `video.fps`, `video.width/height`, `video.aspect_ratio` | — | Informativos para módulos 3‑4. |

---

## 4. Lo que el módulo 2 devuelve: `voice.json`

**Decisión tomada e implementada.** `script.json` se queda en el esquema 1.0 y
**no se toca ni un byte**: `video.actual_duration_s` sigue siendo `null` y
permanece como **campo reservado**. Los tiempos MEDIDOS viven en un manifiesto
lateral `voice.json`, versionado por su cuenta
(`document_type="voice_manifest"`, `schema_version="1.0"`).

Esto descarta la alternativa de migrar ahora el guion a 1.1: los consumidores
del contrato 1.0 siguen funcionando sin cambios, y los nuevos leen `voice.json`.

Esquema completo: [`schema/voice.schema.json`](../schema/voice.schema.json).

### Dónde está

```
DATA_DIR[/simulation]/jobs/<job_id>/voice/<voice_run_id>/
├── voice.json
└── audio/
    ├── narration.wav          # maestro: clips + pausas del guion
    └── scenes/<scene_id>.wav  # un clip por escena, sin la pausa añadida
```

Las rutas del manifiesto son **relativas a su propio directorio** y no admiten
escapes. El archivo de entrada se le pasa a la validación: **nunca** se confía
en una ruta guardada dentro del manifiesto.

### Vínculo con el guion

| Campo | Para qué |
| --- | --- |
| `job_id` | El mismo del guion. |
| `source.source_script_schema_version` | Versión del contrato de entrada. |
| `source.source_script_sha256` | SHA-256 de los **bytes exactos** del `script.json` leído. |
| `source.source_profile_id`, `source_channel` | Perfil y canal de origen. |
| `source.source_simulation`, `source_production_status` | Origen del guion. |

> Un hash comprueba la **correspondencia entre archivos**, no autentica al autor
> de un guion. El consumidor debe **volver a validar** el guion y los medios,
> además de comparar hashes.

### Grupos del manifiesto

| Grupo | Contenido |
| --- | --- |
| Identidad | `document_type`, `schema_version`, `voice_run_id`, `job_id`, `created_at` (UTC), `simulation`. |
| Fuente | los campos `source.*` de arriba. |
| Proveedor | `name`, `model_id`, `voice_id`, `output_format`, `internal_format`, `effective_settings` (sin secretos), `settings_hash`, `processing_version`. |
| Control | `voice_status` (`ready`/`needs_review`), `issues[]` con `code`, `severity`, `blocking` y `scene_id`, y `admissible_for_assembly`. |
| Maestro | `path`, `sha256`, `codec`, `sample_rate_hz`, `channels`, `sample_count`, `actual_duration_s`. |
| Escenas | identidad y orden originales, `source_text` + su hash, clip y su hash, muestras y tiempos derivados, método y estado de alineación. |
| Palabras | `scene_id`, `word_index`, `text`, `char_start`, `char_end`, `start_s`, `end_s`, `emphasis`. |
| Sonido | `cues[]` y `assets[]` verificables; puede estar vacío. |
| Uso | `requests_total` (histórico del trabajo) frente a `requests_this_run`, caracteres, `request_ids`, latencia y `estimated_cost_usd` nullable. |

**Un manifiesto con cualquier incidencia `blocking=true` nunca es `ready`.** La
ausencia de un efecto opcional es informativa (`blocking=false`); los defectos
de duración o de alineación son bloqueantes.

### Tiempos: la regla exacta

Con `sample_rate = 24000`, para cada escena:

```
clip_samples   = muestras del WAV decodificado (incluye sus silencios naturales)
pause_samples  = floor(pause_after_s × sample_rate + 0.5)      # media hacia arriba
start_sample   = Σ (clip_samples + pause_samples) de las escenas anteriores
end_sample     = start_sample + clip_samples + pause_samples   # EXCLUSIVO
start_s        = start_sample / sample_rate
clip_end_s     = (start_sample + clip_samples) / sample_rate   # fin del habla
end_s          = end_sample / sample_rate                      # con el silencio
actual_duration_s = (clip_samples + pause_samples) / sample_rate
```

La pausa explícita del guion se añade como silencio **exactamente una vez**,
también después de la última escena si el guion la declara, y es **adicional**
a las respiraciones naturales del clip. Los segundos se derivan de los enteros
al exportar: no se acumulan segundos redondeados escena a escena.

### Palabras

`start_s` y `end_s` son **globales** respecto de `narration.wav` (ya llevan
sumado el inicio de la escena, que incluye las pausas anteriores).
`char_start`/`char_end` indexan el `narration_text` original en **puntos de
código de Python**, no en bytes UTF-8.

Política de agrupación, documentada y probada: una palabra es una secuencia
máxima de caracteres que no son espacio; la puntuación pegada se conserva en el
texto mostrado; los espacios no reciben duración propia; un grupo sin
alfanuméricos se une a la palabra anterior (o a la siguiente si no hay).

`emphasis` sale de `captions.emphasis_words` con la normalización del módulo 1
(sin tildes, minúsculas, sin puntuación). **No altera el texto narrado.**

### Lo que el módulo 2 NO hace

No rellena `video.actual_duration_s`, no modifica estimaciones, guion, avisos ni
`simulation` del módulo 1. No mezcla música, no genera subtítulos ASS y no
publica nada.

## 5. Campos relacionados con audio que ya vienen

| Campo | Tipo | Notas |
| --- | --- | --- |
| `scenes[].audio.sfx_description` | texto o `null` | Indicación en lenguaje natural, ligada a la acción. |
| `scenes[].audio.sfx_cue` | `scene_start` \| `scene_end` | Posición **aproximada**: el tiempo final depende del audio real. |
| `scenes[].audio.music_mood` | texto o `null` | Indicación de ambiente. Debe ser música de uso comercial permitido; el módulo 1 solo redacta la indicación, no selecciona pista. |
| `scenes[].captions.emphasis_words` | lista | Palabras a destacar; están garantizadas dentro del `narration_text` de esa escena. Las usará el módulo 4 con las alineaciones del módulo 2. |

---

## 6. Campos que el módulo de voz **no** necesita

- `visual_bible` y `scenes[].visual` (`image_prompt`, `motion_prompt`,
  `continuity_notes`): son para el módulo 3. Los `image_prompt` ya incluyen la
  ficha de continuidad de cada personaje, así que el módulo 3 no depende de
  leer `visual_bible` por separado.
- `publishing`: lo decidirá el módulo 5 a partir de los medios terminados.
  `ai_disclosure_review_required` es siempre `true` y `made_for_kids` es un
  borrador, no una decisión final.
- `evidence`: relevante para revisión editorial, no para locutar.
  `verification_level = source_pack_only` significa que el guion se restringió
  a las fuentes aportadas, **no** que alguien haya verificado que esas fuentes
  respalden cada afirmación.

---

## 7. Invariantes en los que puedes confiar

Los garantiza el módulo 1 y los comprueba su validador antes de exportar:

1. `narration.full_text` == `" ".join(scenes[].narration_text)` en orden.
2. `scenes[].order` es 1..N consecutivo y los `scene_id` son únicos.
3. La línea de tiempo empieza en `0.0` y encadena sin huecos ni solapes;
   `scenes[-1].estimated_end_s == video.estimated_duration_s`.
4. `word_count` coincide con el conteo local del texto de esa escena.
5. `0 ≤ pause_after_s ≤ 1,5`.
6. Toda referencia resuelve: `character_ids` → `visual_bible.characters`,
   `claim_refs` → `evidence.claims`, `claim.fact_ids` → `evidence.facts`.
7. `evidence.facts` solo contiene hechos realmente usados, copiados literalmente
   del catálogo importado.
8. `video.actual_duration_s` es `null`.
9. Si `production_status == ready_for_production`, `control.warnings` está vacío.

---

## 8. Instrucciones para el módulo 4 (montaje) y el 3 (imagen)

**Módulo 4 — montaje y subtítulos:**

1. **Usa `narration.wav` una sola vez.** Ya contiene los clips y las pausas del
   guion. **No vuelvas a sumar `pause_after_s`**: el silencio ya está dentro.
2. **Usa los tiempos reales de `voice.json`** para colocar escenas y
   subtítulos, no las estimaciones de `script.json`. Para una escena, el habla
   va de `start_s` a `clip_end_s`; `end_s` incluye su silencio.
3. **Conserva `script.json`** para las decisiones narrativas y visuales
   (`beat`, `loop`, `captions.overlay_text`, `publishing`, `evidence`).
4. Los subtítulos completos salen de `scenes[].narration_text` del guion; los
   tiempos por palabra, de `words[]` del manifiesto.
5. No alargues con silencio, bucles ni cambios automáticos de velocidad para
   cuadrar una duración. Si no cuadra, es `needs_review`.
6. Cualquier transformación futura que desplace tiempos (recortes de silencio,
   crossfades, cambio de velocidad) **obliga a recalcular la alineación**: este
   módulo no aplica ninguna a propósito.

**Módulo 3 — imagen y clips:** puede usar `scenes[].actual_duration_s` del
manifiesto (duración real, no estimada) al pedir los clips, y los
`image_prompt` del guion, que ya son autocontenidos.

**La elegibilidad para monetización sigue sin ser una conclusión de este
módulo**, igual que en el módulo 1.
