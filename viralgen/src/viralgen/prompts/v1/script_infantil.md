TAREA: desarrolla el cuento completo a partir de la idea seleccionada que
recibes como DATOS. Debe durar unos {{target_duration_s}} segundos leidos a
{{target_wpm}} palabras por minuto.

EXTENSION
- Escribe entre {{min_scenes}} y {{max_scenes}} escenas.
- Reparte en total unas {{total_words}} palabras de narracion entre todas las
  escenas (aproximadamente {{words_per_scene}} por escena).
- `pause_after_s` es la pausa AL FINAL de cada escena, entre 0 y 1,5 segundos.

BEATS
La primera escena es `hook`, la ultima es `close`, y entre medias van
`context`, `development` y una `resolution` que cierra la historia.

GANCHO
- Escribe TRES alternativas de gancho, identificadas, y elige una en
  `selected_hook_id`.
- El gancho elegido debe aparecer literalmente al principio del
  `narration_text` de la primera escena.
- Maximo seis palabras: debe durar unos tres segundos o menos.
- Nada de "no creeras lo que paso" sin una promesa concreta.

VISUAL
- Usa los `character_id` de la biblia visual que recibes; no inventes
  personajes nuevos ni cambies su aspecto o su ropa.
- `image_prompt` en {{visual_prompt_language}}, autocontenido: personajes,
  accion, encuadre, iluminacion. `motion_prompt` describe SOLO el movimiento.
- `continuity_notes` explica que se mantiene respecto a la escena anterior.
- Como maximo {{video_scene_budget}} escenas pueden llevar `asset_type=video`.

SUBTITULOS Y AUDIO
- `emphasis_words`: palabras que ya aparecen en el `narration_text` de esa
  escena. Estilo tranquilo y legible.
- `sfx_description` breve y ligado a la accion; `music_mood` opcional y de uso
  comercial permitido.

LOOP
Para el perfil infantil deja `loop.enabled=false` y los demas campos del loop a
null, salvo que el cierre conecte de verdad con el inicio sin romper el
desenlace.

EVIDENCIA
Deja `claims` vacio si el cuento es ficcion pura. Si explicas algo real, cada
afirmacion debe apoyarse en los `fact_id` del catalogo de DATOS.

PUBLICACION
Un elemento de `publishing` por plataforma de destino, con titulo, texto y
hashtags en espanol. No propongas horarios ni promesas de ingresos.
