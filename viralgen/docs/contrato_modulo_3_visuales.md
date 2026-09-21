# Contrato del módulo 3 → módulo 4 (montaje)

Documento de **lectura** para quien conecte el montaje. El módulo 3 no monta
ni publica: deja escrito qué archivos entrega, qué números son **medidos**,
cuáles son **declarados por el proveedor**, cuáles son **decisiones del
montaje** y qué sigue faltando.

Versión del contrato descrita: `schema_version = "1.0"`.
Esquema completo: [`schema/media.schema.json`](../schema/media.schema.json).
Contrato previo: [`docs/contrato_modulo_2_voz.md`](contrato_modulo_2_voz.md).

---

## 0. Puerta de entrada: no montes si no admite

El módulo 4 recibe **tres archivos**, no uno: `script.json`, `voice.json` y
`media.json`. La validación es conjunta y **local, sin red**:

```bash
viralgen media validate \
  --script <script.json> --voice <voice.json> --manifest <media.json>
```

Implementada en `viralgen.media.admission.check_media_admission(...)`. Revalida
desde los bytes reales: no se fía de ningún booleano guardado, **tampoco de
`control.admissible_for_assembly` del propio manifiesto**.

Devuelve **tres veredictos independientes**, nunca uno solo:

| Campo | Qué autoriza |
| --- | --- |
| `contract_valid` | Nada. Solo dice que los tres archivos se leen, cumplen su esquema, sus vínculos cuadran, todas las rutas caen dentro del paquete y **cada archivo referenciado decodifica**. |
| `admissible_for_preview` | Revisión humana y CI de recorridos de **prueba**. Añade cobertura, tiempos, referencias y duración. Ignora **únicamente** `origin_checks`. |
| `admissible_for_assembly` | **Montar.** Es el que debe leer el módulo 4. Exige además origen real en guion, voz y medios. |

`origin_checks = {guion_real, voz_real, medios_reales}`. Son las **únicas**
comprobaciones que `preview` ignora.

`--allow-simulation` elige solo el modo y su código de salida: **nunca cambia
`checks`** ni convierte `admissible_for_assembly` en `true`. El origen se
deriva de las entradas y de las operaciones realizadas, no de una bandera de
quien invoca. Un paquete simulado conserva siempre `simulation = true`.

### `media_status` y `visual_review` son cosas distintas

| Campo | Significado |
| --- | --- |
| `control.media_status = "ready"` | Preparación **técnica** completa: todas las escenas cubiertas, todo medido, sin bloqueos. |
| `control.media_status = "needs_review"` | Hay incidencias. Material recuperable, **no** montable automáticamente; `control.issues` dice por qué. |
| `control.visual_review` | Revisión **artística humana**: `not_performed` \| `approved` \| `rejected`. `ready` **no** implica que nadie haya mirado las imágenes. |

`inspection.contact_sheet_path` existe para esa mirada humana. **No forma parte
del montaje y no acredita calidad visual.**

### Trabajos parciales: puede no haber manifiesto

Igual que en el módulo 2: si una escena queda con un bloqueo sin resolver, el
módulo 3 **detiene todas las solicitudes nuevas** y **no publica `media.json`**,
porque sería un manifiesto que aparenta cubrir todas las escenas cuando faltan
medios. El trabajo queda en SQLite con sus assets, hashes y contadores; el
resumen de la CLI lo indica con `partial: true`, `manifest_path: null`,
`pending_scenes` y `available_paths`.

**Si no hay `media.json`, no hay nada que montar.** El estado parcial no es un
tercer valor del esquema 1.0: es la ausencia del archivo.

Hay un segundo caso sin manifiesto: `media_status = "waiting_remote"` en el
resumen de la CLI (código de salida propio). Significa que una tarea de vídeo
sigue viva en el proveedor y la espera local de esta invocación se agotó.
`remote_tasks` lleva los `task_id` **reales**. Volver a invocar el mismo
`--media-key` retoma esas tareas; **nunca** se inventa un `task_id` ni se
vuelve a crear la tarea, porque eso facturaría dos veces.

---

## 1. Identidad y vínculo con guion y voz

| Campo | Qué es |
| --- | --- |
| `media_run_id` | Ejecución de medios. Cambia en cada trabajo nuevo. |
| `job_id` | El del guion. **Debe coincidir** con `script.json` y `voice.json`. |
| `voice_run_id` | La ejecución de voz exacta sobre la que se midió. |
| `source.script_sha256` | SHA-256 de los **bytes** de `script.json`. |
| `source.voice_sha256` | SHA-256 de los **bytes** de `voice.json`. |
| `source.script_simulation`, `source.voice_simulation` | Origen heredado. Una sola simulación contamina la cadena. |
| `source.profile_id`, `source.channel` | Perfil editorial y canal que guiaron el estilo. |

Si cualquiera de los dos hashes no cuadra, el guion o la voz **han cambiado
desde que se generaron los medios** y el paquete no admite. Regenera; no
parchees el manifiesto.

---

## 2. El reloj: `voice.json` es la única fuente de tiempos medidos

`timeline` copia el maestro de voz. **El módulo 3 no mide audio y no calcula
duraciones propias.**

| Campo | Origen | Unidad |
| --- | --- | --- |
| `timeline.sample_rate_hz` | copiado de `voice.master.sample_rate_hz` | Hz |
| `timeline.total_samples` | copiado de `voice.master.sample_count` | muestras |
| `timeline.total_duration_s` | derivado: `total_samples / sample_rate_hz` | segundos |
| `timeline.source` | `"voice_manifest"` | — |
| `timeline.target_width`, `target_height`, `target_fps` | **decisión de entrega**, no medición | píxeles, fps |
| `timeline.target_source` | `"montage_decision"` | — |

Por escena, en `scenes[]`:

| Campo | Origen |
| --- | --- |
| `start_sample`, `end_sample` | copiados de la escena de voz |
| `start_s`, `end_s`, `duration_s` | derivados de las muestras al exportar |
| `duration_source` | `"voice_manifest"` |

**Las muestras son la autoridad; los segundos son una vista derivada.** Suma
muestras, nunca segundos: sumar segundos redondeados acumula error. Las escenas
son contiguas y cubren `0 .. voice.master.sample_count` sin huecos ni solapes;
el validador lo comprueba.

---

## 3. Los tres orígenes de un número

Todo dato numérico del manifiesto declara de dónde sale. Esta es la distinción
que el módulo 4 debe respetar:

| Valor de `*_source` | Significado | Ejemplos |
| --- | --- | --- |
| `local_measurement` | **Medido aquí**, abriendo el archivo: Pillow para imágenes, `ffprobe` para clips. Es lo único que acredita el archivo. | `assets[].dimensions_source`, `video.measured_duration_source`, `video.fps_source` |
| `provider_reported` | **Declarado por el proveedor**, no comprobado. Informativo. | `usage.provider_reported` |
| `montage_decision` | **Decisión del proyecto**, no un hecho del archivo. | `timeline.target_source`, `video.requested_duration_source`, `scenes[].presentation.decided_by` |

Consecuencias prácticas:

* `assets[].width`/`height` de un clip salen del **stream de vídeo**, no del
  contenedor ni de lo que prometiera el proveedor.
* `video.measured_duration_s` sale del **stream de vídeo**, nunca de la pista
  de audio ni del campo de duración del contenedor.
* `video.requested_duration_s` es lo que se **pidió** (el modelo solo acepta
  duraciones discretas); `measured_duration_s` es lo que **hay**. Pueden
  diferir, y por eso son dos campos.
* Un clip siempre dura **al menos** lo que su escena (`duration_s`). El
  validador lo exige; el recorte lo decide el módulo 4 con `scenes[].clip_trim`.

---

## 4. Assets: qué hay en el paquete

`assets[]` es la lista completa de archivos, todos con ruta **relativa al
directorio del manifiesto**, `size_bytes` y `sha256`.

| `role` | Qué es |
| --- | --- |
| `scene_image` | Imagen de una escena. |
| `character_reference` | Referencia de personaje del conjunto versionado. |
| `video_seed` | Imagen inicial exacta que se envió para generar un clip. |
| `scene_clip` | Clip de vídeo de una escena `asset_type: video`. |
| `inspection` | Hoja de contacto. **No se monta.** |

Campos que el montaje necesita mirar:

* `kind` (`image` \| `video` \| `contact_sheet`) y `mime`: el tipo se determinó
  **decodificando el archivo**, no por su extensión.
* `simulation`: `true` marca contenido del proveedor simulado. Nunca se monta.
* `reference_asset_ids`: qué referencias se enviaron realmente.
* `derived_from` + `transformation`: un derivado dice de qué asset sale y **qué
  transformación ya está aplicada**. El módulo 4 **no la repite**.
* `cache_hit`: el archivo se reutilizó de la caché por identidad. No cambia el
  contenido ni el hash.
* `video`: presente solo en clips (§3).
* `effective_prompt` + `prompt_version`: trazabilidad de lo que se pidió.

Lo que **no** hay en el manifiesto ni en los registros, por diseño: base64,
credenciales, cabeceras de autorización y URLs firmadas del proveedor.

---

## 5. Presentación: planificada aquí, aplicada en el montaje

`scenes[].presentation` es un **plan**, no una operación hecha:

| Campo | Qué es |
| --- | --- |
| `policy` | `contain` (relleno lateral) o `crop` (recorte). **Nunca hay deformación**: no existe la opción de estirar. |
| `target_width`, `target_height` | Encuadre vertical de entrega. |
| `pad_color` | Color de relleno, derivado de la paleta del perfil. |
| `applied` | Siempre `false` en el esquema 1.0. |
| `decided_by` | `"module_3_plan"`. |

`applied: false` significa exactamente eso: **el módulo 3 no reencuadra el
material principal**. Entrega el original medido y el plan; el reencuadre es
del módulo 4, que tiene el contexto del montaje. La única geometría ya aplicada
aparece en `assets[].transformation` de los derivados (por ejemplo la semilla
de vídeo), y ahí sí está hecha.

---

## 6. Referencias de personaje

`references` describe el conjunto **versionado y persistente** usado en el
trabajo:

| Campo | Qué es |
| --- | --- |
| `set_id`, `version` | Identidad del conjunto. Se deriva de la biblia de serie salvo que se fije a mano. |
| `series_bible_id`, `bible_sha256` | De qué biblia sale. Si la biblia cambia, **nace una versión nueva**; no se reutiliza una referencia que ya no corresponde. |
| `entries[].provenance.kind` | `generated` (creada por este proyecto) o `imported` (aportada con derechos declarados). |
| `entries[].provenance.rights_declaration` | Declaración de derechos, obligatoria en ambos casos. |
| `entries[].provenance.simulation` | Si la referencia salió del proveedor simulado. |

El conjunto se **fija al empezar** el trabajo: todas las escenas del mismo
trabajo reciben las mismas referencias, para que la continuidad no dependa del
orden de generación. El proyecto no descarga imágenes de terceros: las
importadas se aportan como archivos locales con su procedencia.

---

## 7. Invariantes en los que puedes confiar

Comprobados por `viralgen media validate` sobre los archivos reales:

1. Los tres documentos validan contra su esquema y sus `schema_version` son
   compatibles.
2. `job_id` coincide en los tres; `voice_run_id` coincide entre medios y voz.
3. Los hashes de los bytes de `script.json` y `voice.json` cuadran.
4. El reloj (`sample_rate_hz`, `total_samples`) es idéntico al maestro de voz.
5. Todas las escenas del guion están cubiertas, en orden, contiguas, sin huecos
   ni solapes, hasta `total_samples`.
6. Toda ruta del manifiesto cae **dentro** del paquete; los escapes por
   `..` y por enlace simbólico se rechazan.
7. Cada archivo existe, su `sha256` y su `size_bytes` cuadran, y **decodifica**:
   las imágenes se abren y verifican, los clips se miden con `ffprobe`.
8. Cada escena `asset_type: video` del guion tiene un clip; **nunca** se
   sustituye por una imagen ante errores, falta de credenciales o límites de
   presupuesto. Si no hay clip, el trabajo queda parcial (§0).
9. Todo clip dura al menos lo que su escena.
10. Cada `character_id` de una escena está resuelto en el conjunto de
    referencias, y las referencias enviadas constan en `reference_asset_ids`.
11. Si algo de lo anterior falla, `contract_valid` o `admissible_for_preview`
    es `false` y `reasons` dice por qué.

---

## 8. Lo que el módulo 3 **no** hace

* No monta, no concatena, no aplica transiciones ni subtítulos.
* No mezcla audio. `video.use_source_audio` es `false` **por contrato**: el
  audio del clip nunca sustituye a la narración del módulo 2.
* No reencuadra el material principal (§5).
* No aprueba nada artísticamente: `visual_review` empieza en `not_performed`.
* No publica en ninguna plataforma.
* No promete facturación exactamente una vez. Reserva presupuesto **antes** de
  enviar y lo persiste; ante un resultado desconocido (`outcome_unknown`)
  bloquea la repetición automática en vez de arriesgar un cobro doble.

---

## 9. Instrucciones para el módulo 4 (montaje)

1. **Valida los tres archivos juntos** y lee `admissible_for_assembly`. No uses
   el código de salida de una validación con `--allow-simulation` como
   autorización, y no leas el booleano guardado en el manifiesto.
2. **Toma el reloj de `voice.json`**, a través de `timeline`. Suma muestras.
3. **Para cada escena**, usa `primary_asset_id`; el asset trae su ruta, su hash
   y sus dimensiones medidas.
4. **Aplica `presentation`** al colocar la imagen o el clip. `applied: false`
   quiere decir que te toca a ti; `assets[].transformation` quiere decir que ya
   está hecho y no debes repetirlo.
5. **Recorta los clips** a `duration_s` con `clip_trim`; sobran fotogramas por
   diseño, porque las duraciones del proveedor son discretas.
6. **Ignora el audio de los clips.** La narración es la del módulo 2.
7. **No montes la hoja de contacto.**
8. Si `visual_review` es `not_performed`, el material es técnicamente correcto
   pero **nadie lo ha mirado**. Decide si eso basta para tu caso.

---

## 10. Lo que falta en el contrato actual

Declarado para que nadie lo dé por hecho:

* **No hay integración externa ejecutada.** Los ejemplos incluidos son del
  proveedor simulado. Los adaptadores reales de OpenAI Images y Runway están
  implementados y cubiertos con **transporte simulado** (payloads y cabeceras
  verificados sin red); no se han ejercido contra las APIs reales.
* **No hay ninguna medida de calidad visual.** Nada en el manifiesto dice si
  una imagen es buena, si el personaje es reconocible entre escenas o si el
  movimiento del clip acompaña al texto. `visual_review` es el hueco donde
  ponerlo.
* **No hay métrica de continuidad.** La continuidad se persigue con referencias
  versionadas y prompts, pero **no se verifica**.
* **Los costes son `null` mientras no se declaren tarifas.** El proyecto no
  inventa precios.
* **Las duraciones de vídeo son discretas** y se elige la más corta que cubre
  la escena. Si ninguna la cubre, el trabajo lo dice (`duration_not_supported`)
  en vez de entregar un clip corto.
