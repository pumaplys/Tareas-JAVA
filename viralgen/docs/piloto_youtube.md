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
      "alias": "canal_piloto",
      "platform": "youtube_shorts",
      "account_id": "<el ID exacto de tu canal, empieza por UC>",
      "label": "Canal del piloto"
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
  --destination "id=yt,platform=youtube_shorts,account=canal_piloto,at=2026-10-06T18:30:00,tz=Europe/Madrid,visibility=private,notify=false" \
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
  --destination "id=yt,platform=youtube_shorts,account=canal_piloto,at=2026-10-06T18:30:00,tz=Europe/Madrid,visibility=private,notify=false" \
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
