# Primer piloto: un cuento infantil subido en privado a YouTube

Guía ejecutable. Usa los comandos y los nombres de configuración **reales** del
repositorio; nada de aquí está inventado ni parafraseado.

Qué hace este recorrido: un cuento infantil de unos 50 segundos, con perfil
**solo de imágenes**, voz real, imágenes reales, montaje local, revisión del MP4
y, por último, **subida privada a un único canal de YouTube**.

Qué **no** hace: no usa almacenamiento temporal, no publica en Instagram, no
genera clips con Runway, no instala ni activa nada en una VPS y no publica nada
en público.

> **Los pasos 1, 2 y 3 gastan dinero** en tu cuenta de OpenAI y de ElevenLabs.
> El paso 4 es local y no gasta. El paso 7 sube el vídeo. Todo lo demás solo
> lee. Cada comando con gasto está marcado con 💸.

---

## 0. Diagnóstico: qué falta antes de empezar

```bash
python tools/diagnostico_piloto.py
```

Informa de presencia o ausencia, **nunca de valores**: no imprime ninguna clave,
no comprueba que una credencial sea válida y no sale a la red. Mira cuatro
cosas: intérprete y dependencias, FFmpeg y tipografía, variables por etapa, y
rutas, disco y tarifas.

### Variables obligatorias, por etapa

| Etapa | Variables |
| --- | --- |
| 1. Guion | `OPENAI_API_KEY`, `OPENAI_MODEL` |
| 2. Voz | `ELEVENLABS_API_KEY`, `ELEVENLABS_MODEL_ID`, y un `voice_id` (ver abajo) |
| 3. Imágenes | `OPENAI_API_KEY`, `OPENAI_IMAGE_MODEL` |
| 4. Montaje | ninguna: FFmpeg y la tipografía ya están |
| 5. Publicación | `YOUTUBE_CLIENT_ID`, `YOUTUBE_CHANNEL_ID`, `VIRALGEN_PUBLISH_SECRETS_DIR`, `VIRALGEN_PUBLISH_ACCOUNTS_PATH` |

**Los modelos no tienen valor por defecto a propósito**: un modelo de texto no
sirve para imágenes y este proyecto no sustituye uno por otro en silencio.
Declara los identificadores exactos de tu cuenta.

**El `voice_id` es de tu cuenta de ElevenLabs.** El catálogo interno lo trae
vacío (`voice_id: null`) y el proyecto **no inventa ninguno**. Dos formas de
darlo:

```bash
# a) Variable de entorno, válida para todos los perfiles:
export ELEVENLABS_VOICE_ID=<el id exacto de tu voz>

# b) Catálogo propio, si quieres una voz por perfil:
cp src/viralgen/voice/data/voice_profiles.json ~/mis_voces.json
# edita "voice_id" del perfil infantil_cuentos
export VIRALGEN_VOICE_PROFILES_PATH=~/mis_voces.json
```

### Tarifas: lo desconocido sigue siendo desconocido

Si no declaras tarifas, el coste estimado que aparece en los manifiestos es
`null`, y eso es correcto: **no se inventa ninguna cifra**. Para que el proyecto
calcule un estimado, pon **tus** precios reales:

```bash
export VIRALGEN_PRICE_INPUT_PER_1M_USD=<tu tarifa>
export VIRALGEN_PRICE_OUTPUT_PER_1M_USD=<tu tarifa>
export VIRALGEN_PRICE_VOICE_PER_1K_CHARS_USD=<tu tarifa>
export VIRALGEN_PRICE_IMAGE_PER_UNIT_USD=<tu tarifa>
```

### Archivos privados y catálogo de cuentas

```bash
# Directorio de secretos: 0700, FUERA del repositorio (se rechaza si está dentro).
mkdir -p ~/.config/viralgen/secretos && chmod 700 ~/.config/viralgen/secretos
export VIRALGEN_PUBLISH_SECRETS_DIR=~/.config/viralgen/secretos

# Catálogo de cuentas: alias -> ID exacto. No es un secreto, pero sin él no se
# autoriza ningún envío real.
cat > ~/.config/viralgen/cuentas.json <<'JSON'
{
  "accounts": [
    {
      "alias": "nubaria",
      "platform": "youtube_shorts",
      "account_id": "<el ID exacto del canal, empieza por UC>",
      "label": "Nubaria Cuentos (@nubariacuentos)"
    }
  ]
}
JSON
export VIRALGEN_PUBLISH_ACCOUNTS_PATH=~/.config/viralgen/cuentas.json
```

---

## 1. El perfil, ANTES de generar el guion

Esto no es un detalle: el perfil decide la duración, la audiencia infantil y
**si el guion puede pedir clips de vídeo**.

```bash
export VIRALGEN_PROFILES_PATH=$PWD/examples/perfiles_solo_imagenes.json
export VIRALGEN_DATA_DIR=~/viralgen-datos          # fuera del repositorio
```

| Perfil | `video_scene_budget` | Consecuencia |
| --- | --- | --- |
| `infantil_cuentos` interno | 2 | el guion puede pedir clips → haría falta Runway |
| `infantil_cuentos` de `examples/perfiles_solo_imagenes.json` | **0** | solo imágenes; cualquier escena de vídeo **se rechaza en validación** |

El perfil de ejemplo también trae `made_for_kids: true`, duración 40-60 s
(50 por defecto), `target_platforms: [youtube_shorts]`, `requires_evidence:
false` -un cuento no necesita catálogo de hechos- y `caption_style:
calm_readable`.

Comprueba que es el que se va a usar:

```bash
viralgen profiles
```

---

## 2. 💸 Guion

```bash
mkdir -p ~/piloto
viralgen generate \
  --profile infantil_cuentos \
  --duration 50 \
  --topic "Una ardilla aprende a esperar su turno en el comedero del bosque" \
  --job-key piloto-001 | tee ~/piloto/01_guion.json

SCRIPT=$(python -c "import json;print(json.load(open('$HOME/piloto/01_guion.json'))['script_path'])")
echo "$SCRIPT"
```

* `--job-key` es la clave de idempotencia: repetir el mismo comando con la misma
  clave **no vuelve a llamar al proveedor**.
* Sin `--mock`: es una llamada real. El resumen JSON sale por `stdout`, y el
  `tee` lo deja también en un archivo para poder leer las rutas sin volver a
  ejecutar nada.
* Código 0 significa `ready_for_production`; 10 es `needs_review` (hay avisos
  que debes leer antes de seguir); 6 es que faltan fuentes aprobadas.

El `script.json` queda en `$VIRALGEN_DATA_DIR/jobs/<job_id>/script.json`.

**Revisa el guion antes de gastar en voz**: el texto que se va a narrar, el
aprendizaje final, y que ninguna escena pida `asset_type: video`.

```bash
python -c "
import json; d=json.load(open('$SCRIPT'))
print(d['idea']['title'], '|', d['video']['estimated_duration_s'], 's')
print('escenas de video:', [s['scene_id'] for s in d['scenes'] if s['visual']['asset_type']=='video'])
print(d['narration']['full_text'][:400])
"
```

---

## 3. 💸 Voz

```bash
viralgen voice generate --script "$SCRIPT" --voice-key piloto-001 \
  | tee ~/piloto/02_voz.json

VOICE=$(python -c "import json;print(json.load(open('$HOME/piloto/02_voz.json'))['manifest_path'])")
echo "$VOICE"
```

El `voice.json` queda en `jobs/<job_id>/voice/<voice_run_id>/`. Repetir con la
misma `--voice-key` reutiliza los clips ya sintetizados.

> Si el `voice_id` no se puede resolver, el módulo **se detiene** en vez de
> elegir una voz por ti.

---

## 4. 💸 Imágenes

Primero el preflight, que **no llama a nadie**:

```bash
viralgen media plan --script "$SCRIPT" --voice "$VOICE"
```

Dice cuántas imágenes se van a pedir y qué presupuesto consumirían. Con el
perfil solo de imágenes, `video_seconds` debe ser 0 y no debe aparecer Runway.

```bash
viralgen media generate --script "$SCRIPT" --voice "$VOICE" --media-key piloto-001 \
  | tee ~/piloto/03_medios.json

MEDIA=$(python -c "import json;print(json.load(open('$HOME/piloto/03_medios.json'))['manifest_path'])")
echo "$MEDIA"
```

Si sale código 11 (`waiting_remote`) hay una tarea remota en curso: **vuelve a
ejecutar el mismo comando**, que retoma esa misma tarea por su identificador y
no crea otra. Con solo imágenes no debería ocurrir.

---

## 5. Montaje local (sin gasto)

```bash
viralgen render plan --script "$SCRIPT" --voice "$VOICE" --media "$MEDIA"

viralgen render generate --script "$SCRIPT" --voice "$VOICE" --media "$MEDIA" \
  --render-key piloto-001 | tee ~/piloto/04_render.json

RENDER=$(python -c "import json;print(json.load(open('$HOME/piloto/04_render.json'))['manifest_path'])")
echo "$RENDER"
```

**Sin `--preview`.** Una salida preview lleva una marca incrustada en los
píxeles y nunca llega al publicador, aunque sus fuentes sean reales.

El resumen imprime `manifest_path`. Repetir el mismo comando da `reused: true`
y `renders_new: 0`.

---

## 6. Revisión del MP4 antes de pensar en subirlo

```bash
viralgen render validate --script "$SCRIPT" --voice "$VOICE" \
  --media "$MEDIA" --manifest "$RENDER"
```

Sin `--allow-simulation`: aquí **tiene** que salir
`admissible_for_publisher: true`. Si sale false, el informe dice exactamente por
qué y no hay nada que subir todavía.

Revisa el archivo con tus ojos, no solo con el validador:

```bash
python -c "
import json; d=json.load(open('$RENDER'))
print('salida:', d['output']['path'], d['output']['size_bytes'], 'bytes')
print('geometria:', d['output']['video']['width'], 'x', d['output']['video']['height'],
      d['output']['video']['fps'], 'fps,', d['output']['video']['frame_count'], 'fotogramas')
print('sonoridad:', d['audio']['measured'])
print('fotogramas de inspeccion:', [f['path'] for f in d['inspection']['frames']])
"
```

Mira esos fotogramas y, si puedes, reproduce el MP4 completo: comprueba que los
subtítulos no tapan nada, que la narración cuadra con la imagen y que el final
cierra la historia. El validador mide; **la revisión editorial es tuya**.

---

## 7. Publicación privada

### 7.1 Conectar el canal

```bash
viralgen auth youtube
```

Abre un servidor local en `127.0.0.1` y te da una URL para autorizar. Necesita
un navegador **en esta máquina**: en una VPS, conéctate desde tu equipo y copia
el archivo de token al directorio privado, o abre un túnel a ese puerto. El
flujo OOB retirado no se usa.

Scopes previstos: `youtube.upload` y `youtube.readonly` -el segundo para poder
verificar el canal y el resultado-.

### 7.2 Comprobar que el canal es el que crees

```bash
viralgen publish plan \
  --script "$SCRIPT" --voice "$VOICE" --media "$MEDIA" --render "$RENDER" \
  --mode real --publish-key piloto-001 \
  --destination "id=yt,platform=youtube_shorts,account=nubaria,at=2026-10-06T18:30:00,tz=Europe/Madrid,visibility=private,notify=false" \
  | tee ~/piloto/05_plan.json

PLAN=$(python -c "import json;print(json.load(open('$HOME/piloto/05_plan.json'))['plan_path'])")

viralgen publish accounts check --plan "$PLAN"
```

`accounts check` **no publica**: compara el canal que responde
`channels.list(mine=true)` con el `account_id` del catálogo. Si no coinciden, se
detiene ahí.

Tres detalles del destino que conviene entender:

* `visibility=private` — el piloto sube en privado. Es reversible desde el
  estudio del canal.
* `notify=false` — se envía **explícito** porque el valor predeterminado de la
  plataforma es notificar. Un piloto no avisa a nadie.
* `at=` y `tz=` — hora de pared y zona IANA. La entrega **empieza** a esa hora;
  no es una promesa de visibilidad en ese segundo. Hay 15 minutos de ventana
  para arrancar: si el trabajador no corre dentro de ese margen, la tarea pasa a
  `needs_review` en vez de publicarse tarde.

### 7.3 Completar, autorizar y encolar

El plan sale con los requisitos que falten. Para un cuento infantil con el
perfil de ejemplo, la audiencia ya viene decidida (`made_for_kids`), pero **la
divulgación de contenido sintético la decides tú**: es editorial, distinta de
`simulation` y distinta de "se usó IA". Edita el plan y resuélvela:

```jsonc
// en destinations[0].metadata
"synthetic_disclosure": "contains_realistic_synthetic_media"   // o "no_realistic_synthetic_media"
```

Si la dejas en `not_reviewed`, el envío se bloquea con
`disclosure_not_transmittable` y no se sube nada. Un `false` aprobado **sí** se
transmite: se envía `status.containsSyntheticMedia` en los dos casos.

```bash
viralgen publish validate --plan "$PLAN"     # recalcula todo, no modifica nada
viralgen publish approve  --plan "$PLAN" --operator "<tu identidad local>"
viralgen publish enqueue  --plan "$PLAN"
```

`approve` ata la autorización a la intención, al MP4 y al ID de cuenta. Si
después cambias el texto, la hora, la privacidad o la cuenta, **hay que volver a
autorizar**; renovar el token no la invalida.

### 7.4 Subir y verificar

```bash
viralgen publish worker --once --mode real --publish-key piloto-001
```

Un paso por invocación. Sube el MP4 en bloques de 8 MiB y deja `next_poll_at`
en vez de dormir. Código 11 significa que hay espera remota: vuelve a
ejecutarlo.

```bash
viralgen publish status --publish-key piloto-001 --mode real --refresh \
  --out ~/piloto-001-publication.json
```

`--refresh` consulta `videos.list` de forma explícita. Lo que hay que mirar en
el recibo:

| Campo | Qué significa |
| --- | --- |
| `state` | `delivered` solo con evidencia de la consulta remota |
| `real_remote_id` | el `video_id` que devolvió la plataforma |
| `observed_account_id` | el canal en el que está de verdad |
| `observed_visibility` | `private` si se pidió privado |
| `publicly_visible` | `false` en este piloto: entregado **no** es publicado |
| `last_evidence` | de dónde sale lo anterior, con su fecha |
| `budget.requests_used` / `bytes_transferred` | gasto real, persistido |

Si se pidió privado y la plataforma lo mantiene privado, el estado correcto es
`delivered` con `publicly_visible: false`. Si pidieras público y siguiera
privado, el destino iría a `needs_review` con la diferencia explicada: **no se
declara publicado lo que no lo está**.

### 7.5 Repetir no duplica

```bash
viralgen publish worker --once --mode real --publish-key piloto-001   # otra vez
```

Mismo `publish-key` y misma intención: recupera la tarea, no crea otra. El mismo
MP4 en la misma cuenta **no se cuela con otra clave**: la reserva es
`(espacio, plataforma, cuenta, sha256 del vídeo)`.

---

## 8. Qué impide que esto publique de más

* Sin autorización viva para esa intención exacta, la cola no acepta nada.
* Antes del primer byte se **rehashea el MP4**: si cambió, no se transfiere.
* Un paquete de preview o con fuentes simuladas no es admisible en modo real.
* Una operación mutante no se reintenta a ciegas: un timeout tras enviar bytes
  **consulta la sesión**; si no se puede resolver, va a `needs_reconciliation` y
  deja de crear cosas para ese destino.
* Los intentos de la operación que crea algo están acotados
  (`operation_attempts`, 3 por defecto, persistidos).
* La URI de sesión vive en el directorio privado 0600 y no aparece en ningún
  documento exportable.

---

## 9. Ensayo completo sin gastar nada

Antes de gastar en proveedores conviene recorrer la misma secuencia en modo
simulado. No hace falta ninguna credencial y no sale nada a la red. Es el
recorrido que se ejecutó al escribir esta guía, con el mismo perfil solo de
imágenes.

```bash
export VIRALGEN_DATA_DIR=~/ensayo-datos
export VIRALGEN_PROFILES_PATH=$PWD/examples/perfiles_solo_imagenes.json
mkdir -p ~/ensayo

viralgen generate --profile infantil_cuentos --duration 50 \
  --topic "Una ardilla aprende a esperar su turno en el comedero del bosque" \
  --job-key ensayo-001 --mock --seed 7 | tee ~/ensayo/01.json
SCRIPT=$(python -c "import json;print(json.load(open('$HOME/ensayo/01.json'))['script_path'])")

viralgen voice generate --script "$SCRIPT" --voice-key ensayo-001 --mock --seed 7 \
  | tee ~/ensayo/02.json
VOICE=$(python -c "import json;print(json.load(open('$HOME/ensayo/02.json'))['manifest_path'])")

viralgen media generate --script "$SCRIPT" --voice "$VOICE" --media-key ensayo-001 \
  --mock --seed 7 | tee ~/ensayo/03.json
MEDIA=$(python -c "import json;print(json.load(open('$HOME/ensayo/03.json'))['manifest_path'])")

# En el ensayo SI va --preview: las fuentes son simuladas, y una salida de
# produccion con fuentes simuladas no existe.
viralgen render generate --script "$SCRIPT" --voice "$VOICE" --media "$MEDIA" \
  --render-key ensayo-001 --preview | tee ~/ensayo/04.json
RENDER=$(python -c "import json;print(json.load(open('$HOME/ensayo/04.json'))['manifest_path'])")

viralgen publish plan --script "$SCRIPT" --voice "$VOICE" --media "$MEDIA" \
  --render "$RENDER" --accounts <tu catalogo> --mode mock --publish-key ensayo-001 \
  --destination "id=yt,platform=youtube_shorts,account=nubaria,at=2026-10-06T18:30:00,tz=Europe/Madrid,visibility=private,notify=false" \
  | tee ~/ensayo/05.json
PLAN=$(python -c "import json;print(json.load(open('$HOME/ensayo/05.json'))['plan_path'])")

# Resuelve la divulgacion sintetica en el plan y autoriza.
viralgen publish approve --plan "$PLAN" --operator operador_ensayo
viralgen publish enqueue --plan "$PLAN"
viralgen publish worker --once --publish-key ensayo-001 --mode mock \
  --now 2026-10-06T16:35:00Z          # sale con 11: espera remota simulada
viralgen publish worker --once --publish-key ensayo-001 --mode mock \
  --now 2026-10-06T16:40:00Z          # sale con 0: verificado
viralgen publish status --publish-key ensayo-001 --mode mock
```

Resultado del ensayo que se ejecutó: guion de 7 escenas y **ninguna de vídeo**,
50,3 s estimados, `made_for_kids: true`; render preview con 10 codificaciones;
y el destino en `delivered` con `mock_remote_id`, `real_remote_id: null`,
`observed_visibility: private` y `publicly_visible: false`.

Dos diferencias con el piloto real, aparte del gasto:

* En modo simulado los datos van a `$VIRALGEN_DATA_DIR/simulation/…`; en real,
  directamente a `$VIRALGEN_DATA_DIR/…`. Son **espacios de identidad
  separados**: la misma clave en los dos modos son dos trabajos distintos.
* El ensayo usa `--preview`, y una salida preview **nunca** es admisible para
  envío real. Comprobarlo también es parte del ensayo:

```bash
viralgen publish plan ... --mode real --publish-key ensayo-real-001 \
  --destination "id=yt,...,visibility=private,notify=false"
```

Sobre el paquete simulado eso devuelve `admissible_for_real_dispatch: false`, y
el motivo que da es el que corresponde: **el origen es simulado**. Con la
revisión documental del 2026-10-05 ya no aparece ningún bloqueo de protocolo de
YouTube, y `pending_verification` sale vacío para un plan dirigido solo a ese
destino.

---

## 10. Qué sigue pendiente y no forma parte de este piloto

| Pendiente | Estado |
| --- | --- |
| Instagram Reels | 4 entradas de verificación **bloqueando** (`publish verification --target instagram`) |
| Almacenamiento temporal | `s3_presign_expiry` bloqueando; además falta elegir proveedor |
| TikTok Direct Post | fuera de alcance por sus directrices; la entrega es manual |
| Timer de systemd | unidades preparadas en `deploy/systemd/`, **sin instalar** |
| Cuotas reales de YouTube | `yt_quota_units` es información documental fechada, no una cuota concedida |
| Límites de texto | `yt_text_limits`: los topes locales están en caracteres y el de descripción de la plataforma en bytes; no son comparables |

Estado de la verificación, siempre al día:

```bash
viralgen publish verification                      # las 13 entradas
viralgen publish verification --target youtube     # 0 bloqueos
viralgen publish verification --target instagram   # 4 bloqueos
```

---

## 11. Actualizar la instalación de la VPS

La instalación que informa la sesión local está en `/opt/viralgen-repo/viralgen`
sobre el commit `925381b`. **No he comprobado esa instalación desde aquí**: lo
que sigue son los comandos para llevarla al commit nuevo, no una constatación de
su estado.

```bash
# 1. Comprobar que el árbol está limpio ANTES de mover la historia.
#    `git merge --ff-only` controla la historia, no la limpieza del árbol:
#    si hay cambios locales, este comando los muestra y hay que decidir.
cd /opt/viralgen-repo
git status --short

# 2. Traer el commit nuevo de la misma rama.
git fetch origin claude/brave-sagan-bewv55
git log --oneline -1 HEAD                 # anota el commit actual antes de moverlo
git merge --ff-only FETCH_HEAD

# 3. Reinstalar sólo si cambió el bloqueo de dependencias.
cd /opt/viralgen-repo/viralgen
. .venv/bin/activate
git diff --name-only 925381b..HEAD -- requirements.lock.txt pyproject.toml

# 4. Comprobar que el árbol nuevo pasa sus propias pruebas en la VPS.
python -m pytest tests -m "not ffmpeg"    # rápido, sin FFmpeg
python -m pytest tests -m ffmpeg          # la puerta obligatoria: tarda ~9 min

# 5. Ver el catálogo de modelos y su verificación (no llama a ninguna API).
python -m viralgen media models
```

### Configuración del piloto que hay que fijar

```bash
# El modelo vigente. gpt-image-1 se apagó el 2026-10-23: ponerlo bloquea el
# plan en modo real, aunque se siga resolviendo para leer el historial.
export OPENAI_IMAGE_MODEL=gpt-image-2.6-sunburst

# DEJAR VACÍA: la guía de 2.6 dice omitir `input_fidelity` en las ediciones.
unset VIRALGEN_MEDIA_INPUT_FIDELITY

# Reintentos: 1, no 2. Con 7 escenas y 3 referencias son 10 solicitudes
# nominales y hasta 20 con un reintento, que caben en el tope de 24. Con 2
# reintentos serían 30 y el trabajo se detendría con escenas sin imagen.
export VIRALGEN_MEDIA_MAX_GENERATION_ATTEMPTS=24
export VIRALGEN_MEDIA_MAX_SAFE_RETRIES=1

# Tarifas por millón de tokens, para valorar el consumo que declare el
# proveedor. 30 USD NO es el precio de una imagen.
export VIRALGEN_PRICE_IMAGE_TEXT_INPUT_PER_1M_USD=5
export VIRALGEN_PRICE_IMAGE_INPUT_PER_1M_USD=8
export VIRALGEN_PRICE_IMAGE_OUTPUT_PER_1M_USD=30
```

### Qué mirar después de actualizar

```bash
python -m viralgen media plan --script <script.json> --voice <voice.json> --mock \
  | python3 -c "import json,sys; d=json.load(sys.stdin); \
print(json.dumps({'budget': d['budget'], 'cost': d['cost']}, indent=2, ensure_ascii=False))"
```

1. **`cost.billing.model` será `tokens`** y las tres tarifas aparecerán ahí.
2. **`cost.cost_estimate_usd.expected` será `null`**, y `unknown_reason` dirá
   que es por falta de **recuentos**, no de tarifas: los tokens que gastará una
   imagen todavía no pedida no se conocen.
3. **`cost.planned_requests`** debe decir `10` nominales y `20` con un
   reintento, y `retry_ceiling_exceeds_limit` debe ser `false`.

### Antes de las diez imágenes: la prueba acordada

Que el transporte funcione contra un servidor simulado **no demuestra** que el
modelo esté disponible en la cuenta «Viralgen VPS», ni que la facturación sea la
esperada, ni la continuidad visual de los personajes. Por eso la primera
ejecución con gasto no es el cuento completo, sino **una generación y una
edición usando la primera como referencia**, con presupuesto acordado:

```bash
# Dos solicitudes, no diez. El tope del trabajo lo garantiza.
export VIRALGEN_MEDIA_MAX_GENERATION_ATTEMPTS=2
export VIRALGEN_MEDIA_MAX_SAFE_RETRIES=0
```

Con su resultado se comparan el consumo declarado y la facturación real, y se
decide si seguir.

### Lo que esta actualización NO cambia

* **Las claves de caché de los assets ya generados.** `input_fidelity` sólo
  entra en la identidad cuando se envía, y en Sunburst no se envía nunca. Las
  imágenes ya cacheadas siguen encontrándose y no hay que volver a pagarlas.
* **La lectura del historial.** Un manifiesto o una caché que citen
  `gpt-image-1` siguen resolviéndose con sus tamaños y calidades de entonces.

### La voz, antes de reutilizar el vídeo

Según la evidencia del revisor (2026-11-18), las condiciones del EEE limitan el
plan **Free** de ElevenLabs a usos **no comerciales**, y el contenido generado
fuera de una suscripción de pago **no adquiere** licencia comercial al
contratarla después. Si el vídeo se va a reutilizar comercialmente, su voz tiene
que generarse **durante** un plan que lo permita. La prueba puramente técnica
puede seguir con voz simulada.

---

## 12. `media smoke`: la prueba aislada de generación + edición

El §11 fija dos intentos y cero reintentos, pero no daba una orden
reproducible. Ésta es. No necesita guion, ni voz, ni YouTube, ni perfil, y usa
**el mismo adaptador de imágenes del módulo 3**, así que lo que se prueba es el
camino real.

Hace exactamente **dos** peticiones:

| Paso | Endpoint | Qué pide |
|---|---|---|
| 1 | `POST /v1/images/generations` | Lumi de cuerpo entero, con su descripción y su vestuario tal como los fija la biblia. JSON. |
| 2 | `POST /v1/images/edits` | La **misma** Lumi cruzando un puente de madera, adjuntando como **única** referencia los bytes de la imagen anterior. Multipart con un `image[]`. |

En las dos: `gpt-image-2.6-sunburst`, `720x1280`, calidad `medium`, `n=1`, PNG.
**Sin `input_fidelity`** (la guía de 2.6 dice omitirlo) y **sin
`response_format`** (es heredado y GPT Image no lo admite).

### Ensayo sin claves y sin red — pégalo tal cual

```bash
cd /opt/viralgen-repo/viralgen && . .venv/bin/activate
rm -rf /tmp/viralgen-smoke
VIRALGEN_DATA_DIR=/tmp/viralgen-smoke OPENAI_IMAGE_MODEL=gpt-image-2.6-sunburst \
  python -m viralgen media smoke --simulated-transport > /tmp/smoke.json
```

```bash
python3 - /tmp/smoke.json <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print("secuencia:", " -> ".join(f"{c['method']} {c['path']}" for c in d["http_calls"]))
print("referencia adjunta == imagen 1:", d["reference_sha256_matches"])
print("peticiones enviadas:", d["requests_sent"])
print("salida:", d["out_dir"])
for s in d["steps"]:
    usage = "si" if s["usage"] else "no"
    print(f"  {s['step_id']}: {s['width']}x{s['height']} {s['mime']} "
          f"sha={s['sha256'][:12]} dimensiones_ok={s['dimensions_match_request']} usage={usage}")
PY
```

No hay nada que sustituir: `/tmp/viralgen-smoke` y `/tmp/smoke.json` son rutas
fijas. El `rm -rf` del principio es lo que garantiza ver la secuencia completa,
porque una segunda ejecución reutiliza la caché (ver abajo). Debe imprimir:

```
secuencia: POST /v1/images/generations -> POST /v1/images/edits
referencia adjunta == imagen 1: True
peticiones enviadas: 2
  1_generacion: 720x1280 image/png sha=... dimensiones_ok=True usage=si
  2_edicion:    720x1280 image/png sha=... dimensiones_ok=True usage=no
```

Dos cosas que conviene entender de esa salida:

* **`usage=no` en la edición es deliberado.** El transporte simulado devuelve
  consumo en la generación y lo omite en la edición, para recorrer las dos
  ramas: un `usage` ausente se registra como ausente, nunca como cero.
* **Los dos `sha` son distintos.** Si fueran iguales, comparar las imágenes no
  diría nada.

Vuelve a ejecutar el **mismo** comando sin el `rm -rf` y verás la otra mitad de
la prueba:

```
secuencia:
referencia adjunta == imagen 1: None
peticiones enviadas: 0
```

Cero peticiones y ninguna llamada HTTP: repetir la orden **no reenvía nada**.
La identidad de un asset es su contenido, no la ejecución, así que incluso con
otro `--run-id` se reutiliza. `reference_sha256_matches` es `None` porque no
hubo edición que comparar, no porque fallara.

### Sólo el plan, sin construir nada

```bash
cd /opt/viralgen-repo/viralgen && . .venv/bin/activate
OPENAI_IMAGE_MODEL=gpt-image-2.6-sunburst python -m viralgen media smoke
```

Imprime los dos prompts completos, los parámetros y el presupuesto. No abre
red, no lee la clave y no construye el proveedor. Si el modelo configurado no
admite alguno de los parámetros, sale en `capability_problems` y el código de
salida es 3.

### El comando real — 💸 GASTA, no lo he ejecutado

```bash
cd /opt/viralgen-repo/viralgen && . .venv/bin/activate
export OPENAI_API_KEY=<la clave del proyecto «Viralgen VPS»>
export OPENAI_IMAGE_MODEL=gpt-image-2.6-sunburst
python -m viralgen media smoke --real --run-id piloto-01
```

No hace falta exportar límites: `--real` fija por sí mismo el tope de **dos
peticiones** y **cero reintentos**, también en el cliente HTTP
(`max_retries=0`). Es la única variable que falta en el ensayo anterior: la
clave.

Después, las dos imágenes están en el `out_dir` que imprime el resumen.
Ábrelas y compáralas: eso es lo único que dice si la edición conservó al
personaje.

### Lo que impide que esto gaste de más

| Riesgo | Qué lo impide |
|---|---|
| Más de dos peticiones | Tope duro de 2, no configurable, con la reserva **persistida antes** de cada envío. |
| Un reintento silencioso | `media_max_safe_retries=0` forzado, y `max_retries=0` en el SDK. Un 500 gasta una petición y se detiene. |
| Editar sin referencia | Si la primera imagen falla, la segunda **ni se intenta**: sería otra prueba. |
| Repetir y pagar dos veces | La identidad del asset es su contenido, no la ejecución: repetir la orden —incluso con otro `--run-id`— devuelve las imágenes de la caché y envía **0 peticiones**. |
| Un cargo incierto | Un corte después de enviar marca la operación como incierta y **bloquea** su repetición automática hasta reconciliar a mano. |
| Que esto acabe publicado | No escribe `media.json` ni entra en la cola: el resumen declara `diagnostic_only: true` y `admissible_for_assembly: false`. Ningún módulo posterior puede recogerlas porque no hay manifiesto que las declare. |

### Qué NO demuestra

Dos imágenes no acreditan continuidad visual, ni que la facturación sea la
esperada. El modo simulado tampoco demuestra que el modelo esté disponible en
la cuenta: eso sigue siendo `sunburst_account_availability`, y sólo lo cierra
el modo `--real`.
