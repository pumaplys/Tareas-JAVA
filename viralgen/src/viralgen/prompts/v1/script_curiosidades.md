TAREA: desarrolla el guion completo a partir de la idea seleccionada que
recibes como DATOS. Debe durar unos {{target_duration_s}} segundos leidos a
{{target_wpm}} palabras por minuto.

EXTENSION
- Escribe entre {{min_scenes}} y {{max_scenes}} escenas.
- Reparte en total unas {{total_words}} palabras de narracion entre todas las
  escenas (aproximadamente {{words_per_scene}} por escena).
- `pause_after_s` es la pausa AL FINAL de cada escena, entre 0 y 1,5 segundos.

BEATS
Primera escena `hook`, luego `context` y `development`, una `resolution` que
responde la pregunta y una ultima escena `close`.

GANCHO
- Escribe TRES alternativas de gancho, identificadas, y elige una en
  `selected_hook_id`.
- El gancho elegido debe aparecer literalmente al principio del
  `narration_text` de la primera escena.
- Maximo seis palabras: debe durar unos tres segundos o menos.
- Promesa concreta, nada de formulas vacias.

EVIDENCIA (regla dura)
- Toda afirmacion factual va en `claims`, con su `claim_text`, las escenas
  donde suena y los `fact_id` del catalogo que la respaldan.
- `claim_refs` de cada escena debe apuntar a `claim_id` que existan.
- Puedes explicar y parafrasear el material del catalogo. NO puedes anadir
  cifras, porcentajes, fechas, causas ni detalles que no esten ahi.
- Si una escena menciona una cifra, esa escena debe referenciar la afirmacion
  correspondiente.

VISUAL
- `image_prompt` en {{visual_prompt_language}}, autocontenido: objeto, accion,
  encuadre, iluminacion. `motion_prompt` describe SOLO el movimiento.
- Usa los `character_id` de la biblia visual cuando aparezcan manos u otros
  elementos recurrentes.
- Como maximo {{video_scene_budget}} escenas pueden llevar `asset_type=video`.

SUBTITULOS Y AUDIO
- `emphasis_words`: palabras que ya aparecen en el `narration_text` de esa
  escena. Estilo dinamico con enfasis.
- `sfx_description` breve y ligado a la accion; `music_mood` opcional y de uso
  comercial permitido.

LOOP
Deja `loop.enabled=true`, indica escena de apertura y de cierre y explica en
`connection_explanation` como enlaza el final con el principio. El loop es una
conexion narrativa; no repitas material para alargar el video.

PUBLICACION
Un elemento de `publishing` por plataforma de destino, con titulo, texto y
hashtags en espanol. No propongas horarios ni promesas de ingresos.
