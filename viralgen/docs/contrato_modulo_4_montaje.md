# Contrato del módulo 4 → módulo 5 (publicación)

Documento de **lectura** para quien conecte la publicación. El módulo 4 no
publica: deja escrito qué archivo entrega, qué números son **medidos**, cuáles
son **decisiones de montaje**, y qué sigue faltando.

Versión del contrato descrita: `schema_version = "1.0"`.
Esquema completo: [`schema/render.schema.json`](../schema/render.schema.json).
Contratos previos: [módulo 2](contrato_modulo_2_voz.md) ·
[módulo 3](contrato_modulo_3_visuales.md).

---

## 0. Puerta de entrada: no publiques si no admite

El módulo 5 recibe **cuatro archivos**: `script.json`, `voice.json`,
`media.json` y `render.json`. La validación es conjunta y **local, sin red**:

```bash
viralgen render validate \
  --script <script.json> --voice <voice.json> \
  --media <media.json> --manifest <render.json>
```

Implementada en `viralgen.render.admission.check_render_admission(...)`.
Revalida desde los bytes reales: mide el MP4 con `ffprobe`, cuenta los
fotogramas **decodificando**, comprueba los PTS y decodifica ambas pistas
enteras a salida nula. **No se fía de ningún booleano guardado**, tampoco de
`control.admissible_for_publisher` del propio manifiesto.

Devuelve **tres veredictos independientes**:

| Campo | Qué autoriza |
| --- | --- |
| `contract_valid` | Nada. Los cuatro archivos se leen, sus vínculos y hashes cuadran, las rutas caen dentro del paquete y el MP4 existe y decodifica entero. |
| `admissible_for_preview` | Revisión humana y CI de recorridos de **prueba**. Añade fotogramas exactos, reloj, audio completo y subtítulos coherentes. Ignora **únicamente** `origin_checks`. |
| `admissible_for_publisher` | **El módulo 5 puede recibir el archivo.** Exige además `render_mode=production`, `simulation=false` y origen real en guion, voz y medios. |

`origin_checks = {guion_real, voz_real, medios_reales, render_real,
modo_produccion}`. Son las **únicas** comprobaciones que `preview` ignora.

> `admissible_for_publisher` significa que el módulo 5 **puede recibir** el
> archivo. **No certifica monetización, calidad editorial ni permiso para
> publicarlo.**

`--allow-simulation` elige solo el modo del informe y su código de salida:
**nunca cambia `checks`** ni habilita al publicador.

### Una salida preview nunca llega al publicador

Aunque todas sus fuentes fueran reales. `render_mode=preview` es un
`origin_check` que falla siempre, porque una salida preview **lleva una marca
visible incrustada en el vídeo** (`PREVIEW`, o `PREVIEW · SIMULACIÓN` si
además hay simulación) y esa marca no se puede quitar de los píxeles.

Preview usa **la misma resolución y las mismas comprobaciones técnicas** que
producción: sirve para ejercitar el recorrido final, no para saltárselo.

El nombre del archivo también lo dice: **`preview.mp4`** en preview y
`video.mp4` en producción. Aun así, **no deduzcas el modo del nombre**: léelo
de `render_mode`, y toma la ruta de `output.path`.

### `render_status` y `visual_review` son cosas distintas

| Campo | Significado |
| --- | --- |
| `control.render_status = "ready"` | Preparación **técnica** completa: fotogramas exactos, audio íntegro, sonoridad en objetivo, sin bloqueos. |
| `control.render_status = "needs_review"` | Hay incumplimientos medidos. El candidato se conserva, pero **no es publicable** y tampoco admisible para preview. |
| `control.visual_review` | Revisión **humana**: `not_performed` \| `approved` \| `rejected`. `ready` **no** implica que nadie haya mirado el vídeo. |
| `control.visual_review_method` | Cómo se miró: `none`, `frame_sampling` (fotogramas extraídos) o `human`. |

### Trabajos parciales: puede no haber manifiesto

Si una etapa falla, el módulo 4 **corta las etapas posteriores** y **no publica
`render.json` ni el MP4**. El trabajo queda en SQLite con sus etapas
recuperables; el resumen de la CLI lo indica con `partial: true`,
`manifest_path: null`, `output_path: null` y `pending_stages`.

**Si no hay `render.json`, no hay nada que publicar.**

---

## 1. Identidad y vínculo con toda la cadena

| Campo | Qué es |
| --- | --- |
| `render_run_id` | Ejecución de montaje. |
| `job_id` | El del guion. **Debe coincidir** con los tres documentos anteriores. |
| `voice_run_id`, `media_run_id` | Las ejecuciones exactas de voz y medios usadas. |
| `sources.script_sha256` / `voice_sha256` / `media_sha256` | SHA-256 de los **bytes** de cada entrada. |
| `sources.*_simulation` | Origen heredado. Una sola simulación contamina la cadena. |
| `sources.profile_id`, `channel`, `caption_style` | Perfil editorial y estilo de subtítulo aplicados. |

Si cualquier hash no cuadra, esa entrada **ha cambiado desde que se montó** y
el paquete no admite. Regenera; no parchees el manifiesto.

---

## 2. El reloj: dos relojes y un exceso acotado

`voice.json` sigue siendo la **única fuente de tiempos medidos**. El módulo 4
lo cuantiza a fotogramas convirtiendo los **límites acumulados**, nunca cada
duración por separado.

```
start_frame  = ceil(start_sample × F / S)
end_frame    = ceil(end_sample   × F / S)      # exclusivo
total_frames = ceil(N × F / S)
```

| Campo | Origen | Unidad |
| --- | --- | --- |
| `timeline.sample_rate_hz`, `sample_count` | copiados de `voice.master` | Hz, muestras |
| `timeline.narration_duration_s` | `N/S`. Reloj de **voz** | segundos |
| `timeline.fps`, `total_frames` | reloj de **vídeo** | fps, fotogramas |
| `timeline.visual_duration_s` | `total_frames/F` | segundos |
| `timeline.quantization_excess_s` | diferencia entre ambos | segundos |
| `timeline.max_frame_hold_s` | `1/F` | segundos |

**`quantization_excess_s` siempre está en [0, 1/F).** Es el cierre del último
fotograma, documentado. La narración **no se acelera, no se recorta y no se
alarga** para cuadrar con él.

`max_frame_hold_s` es lo único que se permite sostener de la última muestra
visual. **Nunca sirve para aceptar un clip que ya era demasiado corto, rellenar
segundos ni repetir una escena.**

Por escena, en `timeline.scenes[]`:

| Campo | Qué es |
| --- | --- |
| `start_sample`, `end_sample` | los tiempos medidos de la voz |
| `start_frame`, `end_frame`, `frames` | su cuantización. **`frames` nunca es cero** |
| `start_offset_s`, `end_offset_s` | desplazamiento de cada frontera respecto del reloj de voz, en [0, 1/F) |
| `asset_type`, `camera_move`, `geometry_policy`, `geometry_already_applied` | qué se hizo con el material |
| `clip_from_s`, `clip_to_s` | recorte temporal aplicado a un clip |

Las escenas **particionan exactamente** [0, `total_frames`): sin huecos ni
solapes. El validador lo recalcula, no se lo cree.

> Ejemplo de aceptación: fronteras en 0 / 1,01 / 2,02 / 3,03 s a 30 fps dan
> fronteras 0 / 31 / 61 / 91 y segmentos de 31, 30 y 30 fotogramas. Redondear
> cada duración por separado daría 93, y eso sería un error.

---

## 3. La salida: todo medido sobre el archivo

`output` describe el MP4 entregado. **Todo aquí está medido**, no prometido.

| Campo | Qué es |
| --- | --- |
| `path`, `sha256`, `size_bytes` | el archivo (`video.mp4` o `preview.mp4`). El tamaño se **mide**: CRF no lo impone |
| `container_format`, `container_duration_s` | del contenedor |
| `faststart` | el índice (`moov`) va al principio |
| `video.frame_count` | **contado decodificando**, no leído de la cabecera |
| `video.fps_rational` | racional (`"30/1"`), no decimal |
| `video.sample_aspect_ratio`, `display_aspect_ratio` | SAR 1:1, DAR 9:16 |
| `video.rotation_degrees` | 0: orientación neutra, el vídeo ya es vertical |
| `video.constant_frame_rate`, `pts_monotonic` | analizados sobre los PTS de **presentación** |
| `audio.decoded_samples`, `initial_padding_samples` | muestras reales y priming que declare el decodificador |
| `decode_check_passed` | ambas pistas decodificadas enteras a salida nula |

**Tres duraciones separadas**, a propósito: la del stream de vídeo, la del
stream de audio y la del contenedor. No son la misma y confundirlas esconde
problemas.

Formato fijo del MVP: **MP4 no fragmentado, H.264 (`libx264`), `yuv420p`,
SAR 1:1, CFR 30/1, AAC-LC a 48 000 Hz**, con `+faststart`.

---

## 4. Audio: la voz entera, una sola vez

| Campo | Qué es |
| --- | --- |
| `audio.narration_used_once` | `true` por contrato: los clips por escena ya están dentro del maestro |
| `audio.clip_audio_used` | `false` por contrato: el audio de un clip de vídeo **nunca** entra en la mezcla |
| `audio.resample` | conversión de frecuencia, con su relación y si es exacta |
| `audio.cues_used` | solo cues **ya resueltos y verificados** del manifiesto de voz |
| `audio.ducking` | reducción de la música mientras habla el narrador |
| `audio.normalization` | parámetros efectivos de `loudnorm` |
| `audio.measured` | sonoridad medida sobre el audio **ya codificado** |
| `audio.loudness_compliant` | si cumple los objetivos declarados |
| `audio.aac_tolerance_samples` | margen técnico: **un frame AAC (1024 muestras)** |
| `audio.audio_sample_deficit` | `decoded_samples − esperadas`. **Negativo = falta** narración (defecto); **positivo = sobra**, que es el relleno del último frame AAC y es normal |
| `audio.stream_vs_decoded_samples` | `stream_duration_ts − decoded_samples`. Documenta la brecha entre lo declarado y lo decodificado; no es un defecto por sí sola |

### Cuatro magnitudes de audio que no son la misma

Confundirlas fue un defecto real de esta entrega: `decoded_samples` se
calculaba como `duración × frecuencia` y se etiquetaba como cuenta de PCM.

| Magnitud | Qué es | De dónde sale |
| --- | --- | --- |
| `audio.work_sample_count` | la mezcla PCM que produjo el módulo | calculada a 48 kHz desde la voz |
| `output.audio.stream_duration_ts` | duración **declarada** por el contenedor | `ffprobe`, en la base de tiempo del stream |
| `output.audio.decoded_samples` | muestras por canal que hay **al decodificar** | se decodifica el PCM y se cuentan bytes |
| `output.audio.initial_padding_samples` | priming que expone el decodificador | `ffprobe` |

En un AAC real las dos del medio **difieren**: el contenedor descuenta el
priming y el codificador rellena el último frame hasta 1024 muestras. En el
ejemplo de este repositorio son 1 222 512 frente a 1 222 656 — 144 muestras,
3 ms.

`decoded_samples_source` dice de dónde salió la cuenta: `pcm_decode` o
`not_measured`. **Nunca de la duración.** Si no se pudo medir, el campo queda
en `null` y el validador lo rechaza en vez de dar el audio por bueno.

FFmpeg ya aplica el *Skip Samples* del primer paquete al decodificar, así que
el priming **no se vuelve a descontar**: hacerlo lo contaría dos veces.

Con narración a 24 000 Hz la conversión a 48 000 Hz es exactamente **2×**, así
que la mezcla tiene `2N` muestras por canal y no hay redondeo que documentar.
Para otras frecuencias se aplica una conversión racional y se declara aquí.

**El margen AAC cubre priming y padding del codec, no voz truncada.** Un
déficit mayor que un frame AAC es un fallo, no una tolerancia.

Objetivos **propios del proyecto**, no requisitos de ninguna plataforma:
−16 LUFS integrados y −1,5 dBTP de techo, con tolerancia de ±1 LU y pico máximo
−1 dBTP al comprobar. Un incumplimiento conocido deja el candidato en
`needs_review`; **no se abre un bucle de recodificación** buscando el número.

Si la señal es silenciosa o el análisis no es utilizable, se **dice**
(`measured.usable = false`, con su motivo) y no se inventa una medida.

---

## 5. Subtítulos

| Campo | Qué es |
| --- | --- |
| `captions.path`, `sha256` | `captions.ass`, artefacto de **diagnóstico** |
| `captions.style_id`, `style_version` | estilo aplicado y su versión |
| `captions.font` | ruta **del sistema**, hash, familia y unidades por em |
| `captions.word_count`, `group_count`, `event_count` | cuántas palabras, grupos y eventos |
| `captions.play_res_x/y`, `text_region` | lienzo y región de composición |
| `captions.rounding_policy` | la política única de redondeo, escrita |
| `captions.collapsed_highlights` | palabras cuyo **resaltado** colapsó al cuantizar |
| `captions.preview_mark` | el texto de la marca, o `null` |

**Que `captions.ass` exista no prueba que el texto se vea en pantalla.** Eso se
comprueba sobre fotogramas del MP4; `inspection.frames` los lleva.

`text_region` (x 96..984, y 1080..1530) es una **decisión de diseño, no una
garantía de zona segura**: cada plataforma recorta y superpone su interfaz.

Unidades, que no se mezclan: eventos ASS en **centésimas**, transformaciones
`\t(...)` en **milisegundos relativos al evento**, alineación original en
**segundos globales**.

Una palabra cuyo resaltado colapsa al cuantizar **conserva su texto** en el
grupo y aparece en `collapsed_highlights`: no se fabrica duración de voz.

La fuente se fija por **archivo** y se registra su hash. Se comprueba que cubre
los caracteres realmente usados: un glifo ausente saldría como cuadro vacío, y
eso bloquea antes de renderizar.

---

## 6. Procesamiento y recursos

| Campo | Qué es |
| --- | --- |
| `processing.pipeline_version` | versión del montaje |
| `processing.tools` | versión **de los ejecutables instalados**, capacidades exigidas, libass |
| `processing.encode_settings` | configuración efectiva |
| `processing.plan_sha256`, `fingerprint` | identidad del plan y de la solicitud |
| `processing.segments[]` | un segmento por escena, con su hash y `retained` |
| `processing.concat_mode` | `stream_copy`: los segmentos se unen sin recodificar |
| `resources.renders_new` / `renders_total` | codificaciones de esta invocación y del trabajo |
| `resources.segment_cache_hits` | etapas reutilizadas |
| `resources.peak_memory_mib` | **`null` si no se midió**. No se estima |

`segments[].retained = false` significa que el archivo intermedio **se limpió
tras consolidar**: era regenerable. **Su hash queda aquí como dato histórico,
pero NO es un archivo obligatorio para validar el MP4.** `validate` y la
reutilización funcionan igual después de la limpieza.

---

## 7. Invariantes en los que puedes confiar

Comprobados por `viralgen render validate` sobre los archivos reales:

1. Los cuatro documentos validan y sus `schema_version` son compatibles.
2. `job_id` coincide en los cuatro; `voice_run_id` y `media_run_id` coinciden.
3. Los hashes de los bytes de las tres entradas cuadran.
4. El reloj es idéntico al maestro de voz, y la cuantización se **recalcula**.
5. Las escenas particionan [0, `total_frames`) sin huecos, solapes ni escenas
   de cero fotogramas.
6. El MP4 tiene **exactamente** `total_frames` fotogramas, contados
   decodificando.
7. Cadencia constante y PTS de presentación monótonos.
8. Ambas pistas **decodifican enteras** sin error.
9. El audio contiene la narración completa, dentro del margen de un frame AAC.
10. Todo evento de subtítulo viene de palabras de la voz, cabe en el reloj y
    usa un estilo declarado.
11. Toda ruta cae **dentro** del paquete; los escapes por `..` y por enlace
    simbólico se rechazan.
12. La sonoridad medida cumple los objetivos declarados.

---

## 8. Lo que el módulo 4 **no** hace

* No publica, no sube, no programa y no consulta analíticas.
* No añade intros, outros, transiciones ni fundidos que cambien el reloj.
* No repite el loop narrativo al final: la secuencia del guion ya lo lleva, y
  **no se promete continuidad visual perfecta entre extremos**.
* No usa `-shortest`: la duración sale del cálculo de fotogramas, y `-shortest`
  taparía una pista truncada en vez de delatarla.
* No aprueba nada artísticamente.
* No modifica ni un byte de `script.json`, `voice.json` ni `media.json`, ni de
  los medios que referencian. `video.actual_duration_s` del guion original
  **sigue siendo `null`**: `render.json` describe el archivo exportado, que es
  otra cosa.

---

## 9. Instrucciones para el módulo 5 (publicación)

1. **Valida los cuatro archivos juntos** y lee `admissible_for_publisher`. No
   uses el código de salida de una validación con `--allow-simulation` como
   autorización, y no leas el booleano guardado en el manifiesto.
2. **Nunca publiques una salida `preview`**, aunque sus fuentes sean reales:
   lleva una marca incrustada en los píxeles.
3. **Toma el archivo de `output.path`** y comprueba su `sha256` antes de subir.
4. **Usa las duraciones medidas**, no las estimadas del guion. Si necesitas una
   sola cifra, `timeline.visual_duration_s` es la del vídeo.
5. Si `visual_review` es `not_performed`, el material es técnicamente correcto
   pero **nadie lo ha mirado**. Decide si eso basta para tu caso.
6. El borrador de publicación (título, descripción, etiquetas) está en
   `script.json`, no aquí.

---

## 10. Lo que falta en el contrato actual

* **Ninguna medida de calidad visual o editorial.** Nada dice si el vídeo es
  bueno, si el ritmo funciona o si los subtítulos se leen cómodos.
  `visual_review` es el hueco donde ponerlo.
* **La región de texto no es una zona segura certificada.** Es una decisión de
  diseño de este proyecto.
* **No hay medida de memoria** salvo que alguien la instrumente:
  `peak_memory_mib` es `null`, no una estimación.
* **Los ejemplos incluidos son preview con fuentes simuladas.** FFmpeg
  renderiza el MP4 de verdad, pero los tonos y las imágenes siguen siendo
  simulados: sirven para medir transporte temporal y mezcla, **no** para juzgar
  inteligibilidad de una voz hablada ni calidad de imagen.
* **El montaje no llama a ningún proveedor.** Trabaja en local sobre un paquete
  ya admitido. Las credenciales hacen falta aguas arriba: OpenAI para guion e
  imágenes y ElevenLabs para la voz; **Runway solo si el guion pide escenas
  `asset_type: video`**. Un caso solo de imágenes no lo necesita.
* **Un único objetivo de entrega**: 1080×1920 a 30 fps. Otros formatos no están
  implementados y el objetivo del contrato de medios se comprueba, no se
  sobrescribe.
