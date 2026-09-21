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

## 4. Lo que el módulo 2 debe devolver

| Campo | Estado hoy | Qué hacer |
| --- | --- | --- |
| `video.actual_duration_s` | **siempre `null` en el módulo 1** | Es el hueco reservado para la duración real medida. |

**Carencia conocida del contrato 1.0**: no hay campos para las duraciones
reales *por escena* ni para las alineaciones palabra‑a‑palabra que el módulo 4
necesitará para los subtítulos. El módulo 1 tampoco genera timestamps ni
archivos SRT/ASS a propósito.

Al conectar voz hay que elegir **una** de estas dos vías y dejarla escrita:

1. **Extender el contrato a `1.1`** añadiendo, por ejemplo,
   `scenes[].actual_duration_s` y un bloque de alineaciones; habría que
   actualizar `SUPPORTED_SCHEMA_VERSIONS` en los consumidores.
2. **Archivo lateral** (`voice.json`) junto al `script.json`, referenciado por
   `job_id` y `scene_id`, dejando el contrato 1.0 intacto.

La opción 2 no toca el módulo 1 y es la de menor riesgo para empezar.

---

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
