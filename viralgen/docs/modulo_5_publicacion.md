# Módulo 5: publicación y programación

Documento del módulo. Describe qué permite cada modo, cómo se autoriza una
revisión, qué pasa tras un resultado ambiguo, cómo se reanuda, cómo se
desbloquea, qué borra `gc` y qué queda pendiente antes de operar en una VPS.

Contratos: [`schema/publication_plan.schema.json`](../schema/publication_plan.schema.json)
y [`schema/publication_receipt.schema.json`](../schema/publication_receipt.schema.json).
Entrada del módulo: [contrato del módulo 4](contrato_modulo_4_montaje.md).

---

## 0. Qué hace y qué no hace

Toma un paquete de montaje **ya admitido** (`script.json`, `voice.json`,
`media.json`, `render.json` y el MP4 que el manifiesto resuelve) y lo entrega a
los destinos que el operador **haya autorizado**.

No genera contenido, no monta, no edita los documentos de origen y no reclasifica
nada: `simulation`, `render_mode` y los estados de producción se leen tal como
están. `control.visual_review` del manifiesto antiguo **conserva su estado**; una
revisión editorial independiente puede referenciar los mismos hashes en un
documento nuevo.

Tres cosas distintas que este módulo nunca confunde:

| Concepto | Quién lo decide | Qué significa |
| --- | --- | --- |
| **Admisión técnica** | El código, sobre los archivos | El paquete cumple sus contratos y sus invariantes |
| **Autorización del operador** | Una persona de esta instalación | Esto, a esta cuenta, con este texto, a esta hora |
| **Estado remoto** | La plataforma | Qué ha pasado de verdad al otro lado |

Un `ready` del módulo 4 **no da permiso** para subir nada. Un `exit 0` de `plan`,
de la simulación o de la exportación **no significa que se haya publicado**.

---

## 1. Los tres modos

| Modo | Red | OAuth | Staging | Envíos | Admisión que exige |
| --- | --- | --- | --- | --- | --- |
| `plan` | no | no | no | no | ninguna: su trabajo es decir qué falta |
| `mock` | **no** | no | no | simulados | `admissible_for_simulation` |
| `real` | sí | sí | sí | reales | `admissible_for_real_dispatch` |

En `mock` el publicador simulado **no tiene cliente HTTP**: la imposibilidad de
alcanzar la red es estructural, no una promesa. Aunque el entorno tenga
credenciales, `build_adapter` devuelve el adaptador simulado.

**Elegir un modo no cambia lo que es admisible.** `check_publication_admission`
no recibe el modo: los tres veredictos salen de los archivos y de los destinos.
El modo solo decide **cuál se exige** (`require_mode`). Por eso un preview se
puede simular y nunca enviar.

### Los tres veredictos

| Veredicto | Qué añade |
| --- | --- |
| `contract_valid` | Los cuatro documentos se leen, sus vínculos y hashes cuadran, y el MP4 existe, decodifica y coincide con lo declarado |
| `admissible_for_simulation` | Además, todas las invariantes técnicas del montaje (lo que el módulo 4 llama `admissible_for_preview`) |
| `admissible_for_real_dispatch` | Además: origen de producción sin simulación, destinos con transporte real y **ningún parámetro de protocolo pendiente** de contrastar |

Un plan puede ser **estructuralmente correcto** y tener comprobaciones remotas
pendientes. Pendiente no es aprobado, y el informe lo dice por separado.

El `control.admissible_for_publisher` que trae `render.json` es **informativo**:
se recalcula sobre los archivos reales y el recibo publica los dos valores, el
declarado y el recalculado. Ponerlo a `true` a mano no autoriza nada.

---

## 2. Capacidades por plataforma

| | YouTube Shorts | Instagram Reels | TikTok |
| --- | --- | --- | --- |
| Adaptador | Data API v3 | Graph con **Facebook Login** | exportación manual |
| Autorización | OAuth del propietario del canal (app instalada, loopback + PKCE) | token importado del flujo oficial de Meta | ninguna: publica una persona |
| Subida | reanudable por bloques de 8 MiB | `video_url` (la plataforma descarga) | copia local del MP4 |
| Necesita almacenamiento temporal | no | **sí**, bucket privado + URL firmada 2 h | no |
| Programación remota | **no** (`publishAt` no se envía en este MVP) | no | no |
| Verificación del resultado | `videos.list` | consulta del medio publicado | **ninguna**: lo aportado por el operador no se verifica |
| Visibilidad | `public` / `private` / `unlisted` | pública por definición | la que elija el operador en la app |
| Estado final típico | `delivered` | `delivered` | `awaiting_manual` → `manually_reported` |

### Por qué TikTok no tiene integración remota

Sus **Content Sharing Guidelines** excluyen las utilidades privadas para
gestionar cuentas propias o del equipo, que es exactamente este caso. La
respuesta honesta no es un atajo con cookies, navegador automatizado o endpoints
privados: es cubrir el canal **sin atribuirle** una automatización que no existe.
Una integración oficial futura sería un cambio de alcance documentado, con
revisión de producto, elegibilidad y experiencia de consentimiento.

### Qué no se promete

* `#Shorts` no garantiza clasificación ni monetización, y este módulo no lo
  insinúa en ningún sitio.
* Que la plataforma acepte el binario **no es** una entrega: hasta que
  `videos.list` o la consulta del medio digan lo contrario, el destino sigue en
  `waiting_remote`.
* Un proyecto de API sujeto a la restricción por falta de auditoría **no puede
  asumir publicación pública**. Si se pidió pública y sigue privada, el destino
  va a `needs_review` con la diferencia explicada; la intención pública **no se
  degrada a privada** en silencio.

---

## 3. Estados por destino

```
                  ┌──────────┐
                  │  draft   │ borrador editable
                  └────┬─────┘
       requisito sin   │   autorizado + en cola
       resolver ┌──────┴───────┐
                ▼              ▼
          ┌─────────┐   ┌────────────────┐
          │ blocked │   │ scheduled_local│ plan autorizado, aún sin envío
          └────┬────┘   └───┬───────┬────┘
               │            │       │ ventana de inicio vencida
     resuelto  │            │       └──────────────┐
               └────────────┘                      │
                            │ hora autorizada      │
                            ▼                      │
                    ┌──────────────┐               │
                    │ dispatching  │ operación externa iniciada
                    └──┬────────┬──┘               │
        respuesta       │        │ resultado        │
        aceptada        ▼        │ dudoso           │
              ┌────────────────┐ │                 │
              │ waiting_remote │ │                 │
              └───┬────────┬───┘ │                 │
     confirmado   │        │     │                 │
                  ▼        ▼     ▼                 ▼
          ┌───────────┐  ┌──────────────────────┐ ┌──────────────┐
          │ delivered │  │ needs_reconciliation │ │ needs_review │
          └───────────┘  └──────────┬───────────┘ └──────┬───────┘
                                    │ resuelto por       │
                                    │ una persona        │
                                    └────────┬───────────┘
                                             ▼
                                      ┌────────────┐
                                      │  failed    │
                                      └────────────┘

  TikTok:   draft ──export-manual──▶ awaiting_manual ──record-manual──▶ manually_reported
  Cancelar: draft | blocked | scheduled_local | awaiting_manual ──▶ cancelled
```

| Estado | Significado |
| --- | --- |
| `draft` | Borrador editable. Falta autorización, que es lo normal en un plan |
| `blocked` | Hay un requisito sin satisfacer; no se puede continuar |
| `scheduled_local` | Plan autorizado en cola, **todavía sin envío** |
| `dispatching` | Operación externa iniciada y registrada |
| `waiting_remote` | Hay identificador o sesión remota y queda procesamiento o comprobación |
| `delivered` | La plataforma confirmó la entrega, con su visibilidad observada |
| `needs_reconciliation` | La operación **pudo** completarse y falta evidencia para decidir |
| `needs_review` / `failed` | Requiere resolución del operador, o terminó con fallo explicado |
| `awaiting_manual` | Paquete de TikTok preparado. **No equivale a publicado** |
| `manually_reported` | El operador aportó una referencia; **no verificada por API** |
| `cancelled` | Se canceló antes de iniciar operaciones externas |

Las transiciones están en `ALLOWED_TRANSITIONS` y se comprueban **dentro de la
misma transacción** que escribe el estado. Saltar de `scheduled_local` a
`delivered` es imposible: daría por entregado algo que nadie envió.

Aparte del estado hay una **fase interna** (`TransferPhase`) que no es el
resultado: `session_open`, `uploading`, `bytes_accepted`, `remote_processing`,
`publish_requested`, `verified`. Que un contenedor esté `FINISHED` significa
"listo para publicar", no "publicado".

El **resumen global** se calcula a partir de los estados individuales. Un
YouTube entregado con un Instagram fallido es `partial`: ni éxito ni fallo. Un
éxito en un destino **nunca** provoca otra subida allí porque otro haya fallado.

---

## 4. El plan (`publication_plan.json`)

Versionado por separado (`schema_version: "1.0"`, `publisher_version: "v1"`).
Construirlo **no tiene efectos externos**: ni red, ni staging, ni envíos.

| Grupo | Qué lleva |
| --- | --- |
| Identidad | `plan_id`, `revision`, `publish_key`, `intent_fingerprint`, `mode` |
| `sources` | Los cuatro documentos por **hash de sus bytes**, el MP4 (ruta, hash, tamaño, geometría) y el `admissible_for_publisher` declarado frente al recalculado |
| `destinations[]` | Plataforma, alias e **ID esperado** de cuenta, metadatos finales, privacidad solicitada, horario (UTC + zona IANA + hora de pared) y opciones aplicables |
| `review` | Las cuatro casillas que una persona debe resolver |
| `admission` | Los tres veredictos, sus comprobaciones y sus motivos |
| `verification` | Lo que **no** se pudo contrastar con su fuente, y a quién bloquea |
| `readable_view` | Vista legible para revisar sin leer JSON |

### De dónde sale el texto

Del **guion** (`publishing[]`: título, `caption`, hashtags, `made_for_kids`) y de
las **ediciones del operador**. De ningún otro sitio: no se resume, no se
acorta, no se añaden promesas, cifras, llamadas a la acción ni hashtags, y **no
interviene ningún LLM**. Si falta un dato obligatorio, el plan lo dice y el
operador lo completa editando el archivo.

Dos ejemplos concretos de esa disciplina:

* El guion **no tiene** campo de etiquetas de YouTube. El plan deja `tags: []`
  en vez de fabricarlas con los hashtags, que serían un dato inventado con
  aspecto de dato real.
* `made_for_kids: null` significa **sin decidir**, no "no". Queda como requisito
  `audiencia_sin_decidir`. En el canal infantil se declara `made_for_kids`
  directamente; en el resto la decisión es explícita.

Los topes de `LOCAL_TEXT_LIMITS` (100 caracteres de título, 2200 de texto, 30
etiquetas, 12 hashtags) son **decisiones del producto**, no límites verificados
de ninguna plataforma. Superarlos marca revisión; el texto **no se recorta en
silencio**.

### La decisión sobre contenido sintético es distinta de `simulation`

`simulation` es técnico: de dónde salió el paquete. La divulgación de contenido
sintético realista es editorial: un vídeo real de producción puede contener
medios generados con IA, y un preview simulado puede no contener nada realista.
Ninguna de las dos se deriva de la otra.

Esa decisión **se transmite a YouTube** como
`status.containsSyntheticMedia`, booleano, en `videos.insert`. El nombre no lo
adivinó este módulo: **el revisor consultó la documentación oficial y lo
confirmó**, y esa evidencia consta en el registro de verificación
(`yt_synthetic_media_property`) con quien la aportó.

Dos detalles que importan:

* **El `false` también se transmite.** Un "no contiene medios sintéticos
  realistas" es una declaración, no la ausencia de una. El mapeo es tipado y el
  valor se busca con `.get()` comparando contra `None`, nunca por veracidad:
  comprobar veracidad omitiría exactamente los `false`.
* **Sin decisión no se sube.** `not_reviewed` no tiene entrada en el mapeo, así
  que el envío se bloquea con `disclosure_not_transmittable` y el motivo
  escrito. No se manda `false` por omisión, que sería declarar algo que nadie
  ha declarado.

### Editar el borrador es parte del flujo

`approve` y `enqueue` **recalculan** los requisitos sobre el texto actual, marcan
como `operator_edited` lo que ya no coincide con el guion y suben la `revision`
si la intención cambió. El `intent_fingerprint` guardado nunca decide por su
cuenta: se recalcula.

---

## 5. Autorización

```bash
viralgen publish approve --plan <publication_plan.json> --operator <identidad>
```

Guarda un registro atado a **tres cosas a la vez**: la intención
(`intent_fingerprint`), el MP4 (`video_sha256`) y los IDs de cuenta. Incluye la
autorización del almacenamiento temporal cuando el destino lo necesita: subir ese
MP4 al staging **está cubierto**, no se pide aparte.

Es un registro **del operador de esta instalación**: no es una firma que
certifique la veracidad del contenido ni sustituye la revisión editorial.

| Cambio | ¿Sigue valiendo la autorización? |
| --- | --- |
| Reexportar el mismo plan (otra fecha de emisión, otra nota) | **Sí** |
| Renovar el token de la misma cuenta | **Sí** |
| Reemitir la URL temporal del mismo objeto | **Sí** |
| Cambiar cuenta, vídeo, texto, privacidad, horario o destino | **No**: revisión nueva |
| Revocarla | **No** |

La autorización **persiste entre reinicios y reintentos** sobre la misma
intención: no se vuelve a pedir sin cambios. El trabajador la comprueba **en
cada paso**, así que revocarla detiene la cola inmediatamente.

---

## 6. Reloj, cola y reanudación

La programación es **local**: a la hora autorizada **empieza** la entrega y se
sigue su procesamiento. No es una promesa de visibilidad pública en ese segundo,
y no se programa nada en la plataforma (así cancelar antes de empezar significa
lo mismo en todos los destinos).

* Toda fecha lleva zona. Se conserva el instante UTC **y** la zona IANA.
* Una hora que no existe (salto de primavera) **se rechaza**; no se desplaza.
* Una hora repetida (salto de otoño) exige elegir `fold=0` o `fold=1`.
* **Ventana de inicio: 15 minutos.** Tras una caída larga, una publicación
  vencida pasa a `needs_review`; no sale de golpe todo lo acumulado. Esto no
  impide seguir una transferencia ya iniciada ni consultar un resultado
  pendiente.
* La **concesión** del trabajador vence (300 s por defecto) y se recupera tras
  una caída. Las transferencias largas la renuevan mientras avanzan.
* Dos invocaciones **no pueden** reclamar el mismo destino: la comprobación y la
  escritura ocurren en la misma transacción, y además hay un bloqueo de proceso.

`worker --once` procesa lo vencido y termina. Una espera del proveedor libera el
trabajador (`next_poll_at`) en vez de dejar un proceso durmiendo horas.

### Identidad y duplicados

`--publish-key` identifica la intención completa. Misma clave y mismo
fingerprint **recuperan la tarea**; misma clave con otra intención es el
conflicto establecido del proyecto (código 8).

Los espacios de identidad `mock` y `real` están **separados**: la misma clave en
los dos modos son dos trabajos distintos, y la protección antiduplicados también
es independiente.

Además, el mismo MP4 en la misma plataforma y cuenta **no puede colarse con otra
clave**: la reserva es `(espacio, plataforma, cuenta, sha256 del vídeo)`. La
identidad **no es el título**. Una republicación deliberada queda fuera del flujo
automático de esta entrega.

---

## 7. Respuestas ambiguas: qué pasa tras un timeout

Un timeout después de enviar bytes o de pedir la publicación **no demuestra que
la operación falló**. El cliente HTTP:

* **no reintenta** operaciones mutantes;
* clasifica un fallo de transporte en una operación mutante como **AMBIGUO**;
* permite backoff con `Retry-After` acotado solo en GET y sondeos;
* **no sigue redirecciones** (un 308 del protocolo reanudable no es una
  redirección, y seguir una llevaría la cabecera `Authorization` a otro host).

| Situación | Qué hace el módulo |
| --- | --- |
| Se pierde la respuesta de un bloque de subida | **Consulta la sesión** (`Content-Range: bytes */total`) y reanuda desde el offset confirmado |
| La sesión reanudable devuelve 404/410 | `needs_reconciliation`: no se sabe si se creó un vídeo, así que **no se crea otro** |
| `videos.insert` termina sin `id` | `needs_reconciliation`. **No se adjudica** un vídeo por título y fecha: una coincidencia aparente puede ser de otra persona |
| Se pierde la respuesta de `media_publish` | Se consulta **el contenedor existente**. Si figura `PUBLISHED` sin id recuperable → `needs_reconciliation`, y no se crea otro contenedor |
| Se pierde la respuesta al **crear** un contenedor | Queda registrado como ambiguo y se puede reintentar **con límite**: un contenedor sin `media_publish` no es visible y caduca solo. Ver el recuadro de abajo |
| Estado remoto desconocido | Nunca se interpreta como éxito: `needs_reconciliation` o `needs_review` |

Toda resolución manual **conserva la evidencia y su origen** (`api_query`,
`operator_reported`, `simulated`, `local_package`).

### La única excepción, y sus tres límites

Perder la respuesta al **crear** un contenedor de Instagram sí admite otro
intento, al contrario que perder la de `media_publish`. El motivo es concreto:
un contenedor sin publicar **no es visible para nadie** y caduca por su cuenta,
así que reintentar no puede producir una publicación duplicada. Lo que sí
consumiría es cuota, y por eso la excepción está acotada por tres cosas:

1. **Los intentos se cuentan y se agotan.** `operation_attempts` cuenta solo las
   operaciones que *crean* algo —consultar no gasta intentos, porque leer dos
   veces no publica dos veces— y al llegar a `publish_max_attempts_per_operation`
   (3 por defecto) el destino pasa a `needs_review` con
   `operation_attempts_exhausted`. El contador está en SQLite: reiniciar el
   proceso no devuelve intentos.
2. **Un contenedor cuya identidad no llegó no se publica nunca.** Si la
   respuesta se perdió, no se guarda ningún `container_id`, y `media_publish`
   solo se pide con un identificador **recibido y guardado**.
3. **El vídeo no se vuelve a transferir.** El objeto del almacenamiento temporal
   y su URL firmada se reutilizan mientras sigan vivos.

Perder la respuesta de `media_publish` no entra en esta excepción: eso va a
`needs_reconciliation`, y un destino en ese estado **el trabajador no lo toca**.
Ni repite la publicación, ni crea otro contenedor, ni adjudica un medio.

### Cómo se reanuda

Volver a ejecutar `publish worker --once` con la misma clave. La URI de sesión
vive en el almacén privado y se reutiliza si corresponde al mismo archivo; si es
de otro vídeo, se descarta. Para una consulta remota explícita fuera de la cola:

```bash
viralgen publish status --publish-key <clave> --refresh
```

`--refresh` ignora `next_poll_at` porque lo pide una persona, pero **no adelanta
ninguna entrega**: un destino programado sigue esperando su hora.

### Cómo se desbloquea

1. `publish status --publish-key <clave>` y leer `last_error` y `last_evidence`.
2. Si es `needs_reconciliation`: comprobar **en la cuenta** qué existe. El módulo
   no adivina y no vuelve a crear nada para ese destino.
3. Si es `needs_review` por ventana vencida: decidir si sigue teniendo sentido
   publicar y, si sí, volver a encolar con un horario nuevo (eso es una revisión
   nueva y se vuelve a autorizar).
4. Si es un requisito del plan: editarlo, `approve` y `enqueue` otra vez.
5. `publish cancel` detiene lo que aún no ha salido. Si ya hubo envío, registra la
   petición y explica qué puede pararse: **no declara cancelación remota ni borra
   publicaciones**. Gestionar una publicación ya creada queda fuera del MVP.

---

## 8. El recibo (`publication.json`)

Exportación del **estado persistido**, con identidad de revisión y fecha de
actualización. **No es una fuente de permisos**: editarlo no cambia SQLite, no
autoriza nada y no demuestra éxito remoto. De hecho, editarlo para declarar una
entrega simulada como real ni siquiera vuelve a validar.

Lleva: origen y hashes, `publish_key`/`intent_fingerprint`, revisión autorizada,
modo, IDs de cuenta esperado y observado, estado y fase por destino, IDs remotos
conocidos, visibilidad solicitada y observada, `publicly_visible`, solicitudes
consumidas, bytes transferidos, próximos intentos, errores estructurados y la
evidencia de la última consulta.

En simulación: `simulation: true`, `real_remote_id: null` y, si hacen falta
identificadores internos, `mock_remote_id` con prefijo `mock_` en un espacio
separado. **No se inventan URLs que funcionen ni fechas de publicación real.**

### Dos destinos de la demostración, tal como salen

Extractos reales del `publication.json` que genera
`tools/demo_publicacion.sh`, con la ruta local acortada y los campos nulos
omitidos.

**YouTube, entregado en privado.** Entregado no es lo mismo que visible:

```json
{
  "destination_id": "yt",
  "state": "delivered",
  "phase": "verified",
  "simulation": true,
  "requested_visibility": "private",
  "observed_visibility": "private",
  "publicly_visible": false,
  "mock_remote_id": "mock_16d6988e975c573d",
  "scheduled_at_utc": "2026-09-24T16:30:00Z",
  "timezone": "Europe/Madrid",
  "dispatch_started_at": "2026-09-24T16:35:00Z",
  "delivered_at": "2026-09-24T16:46:00Z",
  "last_evidence": {
    "source": "simulated",
    "summary": "publicador simulado: recurso verificado con la visibilidad solicitada. No existe en ninguna plataforma."
  }
}
```

`real_remote_id` no aparece porque es `null`: en simulación el identificador
vive en `mock_remote_id`, con prefijo, y la evidencia dice `simulated`. El
contrato **rechaza** un recibo que mezcle las dos cosas.

**TikTok, publicado a mano.** El estado es `manually_reported`, no `delivered`:

```json
{
  "destination_id": "tk",
  "state": "manually_reported",
  "phase": "not_started",
  "manual_export": {
    "package_path": "<datos>/publicaciones/tiktok/<publicacion>/tk",
    "video_sha256": "fe314fa77bc4501595a392d79527d83d1a4aa998cd6e9a14ecb9bd802b3a9cdf",
    "exported_at": "2026-09-26T14:36:07Z",
    "simulation": true,
    "publishable_by_this_module": false
  },
  "manual_report": {
    "reported_url": "https://www.tiktok.com/@cuenta_demo/video/0000000000000000000",
    "reported_at": "2026-09-24T17:20:00Z",
    "evidence_source": "operator_reported",
    "note": "Dato aportado por una persona. No esta verificado mediante API y no se convierte en confirmacion remota."
  }
}
```

El `video_sha256` del paquete es el del **preview del repositorio**: la copia es
binaria, con su marca incrustada incluida.

En los dos casos `budget.requests_used` y `bytes_transferred` son `0`, y es lo
honesto: en simulación no se hizo ninguna petición ni se transfirió ningún byte.

---

## 9. Secretos

| Qué | Dónde vive | Qué aparece en plan/recibo/logs |
| --- | --- | --- |
| Token de OAuth de YouTube | `<secrets>/youtube_token.json` (0600) | nada |
| Token importado de Meta | `<secrets>/instagram_token.json` (0600) | su caducidad |
| URI de sesión de subida | `<secrets>/session_*.json` (0600) | nada |
| URL firmada del staging | `<secrets>/session_*_instagram_staging.json` (0600) | su **huella** y su caducidad |

El directorio es 0700, está **fuera del repositorio** (se rechaza si está
dentro) y las escrituras son atómicas. Ningún token se acepta como argumento de
la línea de órdenes: `auth instagram --from-file` lo importa de un archivo.

No se registran cabeceras `Authorization`, códigos OAuth ni payloads completos:
el log lleva método, host, ruta y código. Los contratos además **rechazan en
validación** cualquier texto con marcas de URL firmada o credencial, para que un
descuido no acabe en un documento exportable.

No se añade un cifrado cuya clave viviría al lado del archivo: eso no protegería
de nada. Lo que protege aquí son los permisos del sistema de archivos y la
ubicación. Si hace falta más, el sitio correcto es un gestor de secretos del
sistema.

---

## 10. Límites locales y cuotas externas son dos cosas

Los valores de esta tabla son **decisiones del producto**. No son cuotas de
YouTube, de Meta ni del proveedor de almacenamiento, y no se deben presentar
como tales.

| Ajuste | Valor inicial |
| --- | --- |
| Trabajadores | 1 |
| Entregas reales nuevas por cuenta y día | 1, configurable |
| Solicitudes por destino (persistidas; incluyen sondeos, reintentos, OAuth y staging) | 200 |
| Intentos por operación recuperable (`operation_attempts`, solo los envíos) | 3 |
| Intervalo inicial de sondeo | 30 s, con backoff hasta 300 s |
| Ventana de procesamiento remoto | 3600 s |
| Ventana para iniciar una tarea atrasada | 900 s |
| Tamaño máximo del MP4 | 200 MiB |
| Temporales propios de publicación | 256 MiB |
| Log por trabajo | 5 MiB, con redacción de secretos |
| Espacio mínimo libre | `MIN_FREE_DISK_MB=1500` (reutilizado) |
| URL firmada del staging | 2 h |
| Retención del objeto tras resultado terminal | 24 h |

El presupuesto es **persistente**: abrir otro proceso no lo reinicia. En
simulación no se transfiere nada, así que los contadores de bytes se quedan a
cero, que es lo honesto.

Las cuotas reales de cada plataforma **no están aquí** y no se deducen de
memoria: `yt_quota_units` es una entrada de verificación pendiente precisamente
por eso.

---

## 11. `gc`: qué borra y qué no

```bash
viralgen publish gc            # solo enumera
viralgen publish gc --apply    # borra los candidatos propios
```

Borra **solo** temporales y objetos remotos creados por este módulo cuya
retención venció y cuyo estado lo permite. Comprueba referencias desde otros
destinos antes de tocar nada.

**Nunca borra** guiones, voces, medios, renders ni recibos, y jamás para
resolver falta de espacio. Un estado ambiguo **conserva el objeto** y consume
cuota hasta que alguien lo resuelva: borrar bajo la duda es como se pierde una
publicación a medias.

Si el disco no permite continuar, se bloquean las entregas nuevas con
diagnóstico. La política de archivo o eliminación de trabajos completos de los
módulos 1-4 queda como **decisión de operación** antes de producción sostenida.

---

## 12. Verificación pendiente: por qué el modo real está bloqueado

Durante esta entrega **ninguna** de las referencias citadas era alcanzable: el
proxy de egreso respondió 403 a `developers.google.com`,
`developers.facebook.com`, `www.postman.com`, `developers.tiktok.com` y
`docs.aws.amazon.com`. El código, los contratos y las pruebas de transporte están
completos; lo que falta es **contrastar los parámetros con su fuente**.

Eso no se disimula en una nota al pie: vive en
`viralgen.publish.verification`, aparece en `publish plan` y **bloquea el modo
real** del destino al que afecta.

| Entrada | Destino | Estado | ¿Bloquea? |
| --- | --- | --- | --- |
| `yt_insert_part_and_fields` | youtube | **pendiente** | **sí** |
| `yt_synthetic_media_property` | youtube | verificada | no |
| `yt_resumable_protocol` | youtube | **pendiente** | **sí** |
| `yt_oauth_installed_app` | youtube | **pendiente** | **sí** |
| `yt_quota_units` | youtube | **pendiente** | no |
| `yt_text_limits` | youtube | **pendiente** | no |
| `ig_graph_version` | instagram | **pendiente** | **sí** |
| `ig_container_fields` | instagram | **pendiente** | **sí** |
| `ig_status_values` | instagram | **pendiente** | **sí** |
| `ig_permissions` | instagram | **pendiente** | **sí** |
| `ig_caption_limits` | instagram | **pendiente** | no |
| `s3_presign_expiry` | staging | **pendiente** | **sí** |
| `tiktok_guidelines` | tiktok | **pendiente** | no |

**13 entradas: 12 pendientes y 1 verificada; 8 bloquean el modo real.** La lista viva se obtiene con `viralgen publish verification` (con `--target` para un solo destino), que es la misma que viaja en cada plan.

### Detalle de cada entrada

#### `yt_insert_part_and_fields` — ⏳ pendiente, bloquea el envío real

* **Destino**: youtube  
* **Qué falta comprobar**: Nombres exactos de `part`, de los campos de `snippet`/`status` y de las propiedades de audiencia y divulgacion admitidas por la version vigente.  
* **Supuesto que usa el código hoy**: part='snippet,status'; cuerpo con snippet.title, snippet.description, snippet.tags (solo si hay), snippet.defaultLanguage, status.privacyStatus en {public, private, unlisted}, status.selfDeclaredMadeForKids booleano y status.containsSyntheticMedia booleano; notifySubscribers como parametro de consulta con 'true'/'false'.  
* **Se da por verificada cuando**: Cada nombre y cada valor admitido se compara con el recurso `videos` y con `videos.insert`, y se corrige lo que difiera. La evidencia de `yt_synthetic_media_property` cubre UNA de estas propiedades, no la lista completa.  
* **Fuente** (S1): <https://developers.google.com/youtube/v3/docs/videos/insert>

#### `yt_synthetic_media_property` — ✅ verificada, no bloquea

* **Destino**: youtube  
* **Qué falta comprobar**: Nombre y tipo de la propiedad con la que YouTube recoge la divulgacion de contenido sintetico realista.  
* **Supuesto que usa el código hoy**: Se envia `status.containsSyntheticMedia` como booleano en `videos.insert`: true cuando la decision editorial aprobada dice que contiene medios sinteticos realistas y false cuando dice que no.  
* **Se da por verificada cuando**: Confirmado que la propiedad existe con ese nombre, es booleana y la admite `videos.insert`.  
* **Fuente** (S2): <https://developers.google.com/youtube/v3/docs/videos#status.containsSyntheticMedia>
* **Evidencia**: aportada por el **revisor** el 2026-09-26. Confirma: `status.containsSyntheticMedia` existe, es booleano y lo admite `videos.insert`. Alcance: Solo esta propiedad. No verifica el resto de los campos de `snippet`/`status` ni el valor de `part`, que siguen en `yt_insert_part_and_fields`.

#### `yt_resumable_protocol` — ⏳ pendiente, bloquea el envío real

* **Destino**: youtube  
* **Qué falta comprobar**: Cabeceras exactas de inicio de sesion, semantica de 308, formato de `Range`/`Content-Range` y multiplo de bloque exigido.  
* **Supuesto que usa el código hoy**: POST con uploadType=resumable y cabeceras X-Upload-Content-Length y X-Upload-Content-Type; la URI de sesion llega en `Location`; cada bloque va en PUT con 'Content-Range: bytes a-b/total'; un 308 es subida incompleta y su `Range: bytes=0-N` marca el offset confirmado; el estado se consulta con 'Content-Range: bytes */total'; 404 o 410 significan sesion desaparecida; bloque de 8 MiB (multiplo de 256 KiB).  
* **Se da por verificada cuando**: Cada cabecera, el significado del 308 y el multiplo de bloque coinciden con la guia del protocolo, o el adaptador se ajusta.  
* **Fuente** (S5): <https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol>

#### `yt_oauth_installed_app` — ⏳ pendiente, bloquea el envío real

* **Destino**: youtube  
* **Qué falta comprobar**: Endpoints de autorizacion y token, parametros de PKCE y scopes efectivos para subir y para verificar el canal.  
* **Supuesto que usa el código hoy**: Autorizacion en accounts.google.com/o/oauth2/v2/auth y token en oauth2.googleapis.com/token; response_type=code, code_challenge_method=S256, access_type=offline, prompt=consent y redirect_uri de loopback http://127.0.0.1:<puerto>/; scopes youtube.upload y youtube.readonly.  
* **Se da por verificada cuando**: Los dos endpoints, los parametros de PKCE y los scopes minimos necesarios para subir y para leer el resultado se confirman en la guia de aplicaciones instaladas.  
* **Fuente** (S3): <https://developers.google.com/youtube/v3/guides/auth/installed-apps>

#### `yt_quota_units` — ⏳ pendiente, no bloquea

* **Destino**: youtube  
* **Qué falta comprobar**: Unidades de cuota por operacion y bucket.  
* **Supuesto que usa el código hoy**: NINGUNA cifra de cuota de Google se usa en el codigo. El presupuesto local (200 solicitudes por destino) es un limite del producto y no se presenta como cuota de la plataforma.  
* **Se da por verificada cuando**: Se documentan las unidades por operacion y se hacen configurables, sin mezclarlas con el presupuesto local.  
* **Fuente** (S1): <https://developers.google.com/youtube/v3/docs/videos/insert>

#### `yt_text_limits` — ⏳ pendiente, no bloquea

* **Destino**: youtube  
* **Qué falta comprobar**: Longitudes maximas de titulo, descripcion y etiquetas.  
* **Supuesto que usa el código hoy**: Topes LOCALES conservadores: 100 caracteres de titulo, 2200 de descripcion, 30 etiquetas y 12 hashtags. Superarlos marca revision; el texto no se recorta en silencio.  
* **Se da por verificada cuando**: Los limites reales se comparan con los topes locales y estos se ajustan si son mayores que los de la plataforma.  
* **Fuente** (S2): <https://developers.google.com/youtube/v3/docs/videos>

#### `ig_graph_version` — ⏳ pendiente, bloquea el envío real

* **Destino**: instagram  
* **Qué falta comprobar**: Version de Graph soportada y vigente.  
* **Supuesto que usa el código hoy**: NINGUNA por defecto: META_GRAPH_API_VERSION es obligatoria y explicita en modo real. No se usa `latest` ni se cambia de version automaticamente.  
* **Se da por verificada cuando**: Se comprueba que la version que fija el operador esta soportada y vigente, y que los campos de esta entrega corresponden a ella.  
* **Fuente** (S7a): <https://www.postman.com/meta/instagram/folder/u4g5a2a/instagram-api-with-facebook-login>

#### `ig_container_fields` — ⏳ pendiente, bloquea el envío real

* **Destino**: instagram  
* **Qué falta comprobar**: Campos exactos del contenedor de Reel y su host.  
* **Supuesto que usa el código hoy**: POST a {graph_base}/{version}/{ig_user_id}/media con media_type='REELS', video_url (URL firmada temporal), caption y share_to_feed 'true'/'false'; host graph.facebook.com, no el endpoint de Reels de una Pagina de Facebook.  
* **Se da por verificada cuando**: Los cuatro campos, el host y la ruta coinciden con la coleccion oficial para la version fijada.  
* **Fuente** (S7b): <https://www.postman.com/meta/instagram/request/5kkpkh6/upload-a-reel-to-an-ig-container>

#### `ig_status_values` — ⏳ pendiente, bloquea el envío real

* **Destino**: instagram  
* **Qué falta comprobar**: Valores de `status_code` del contenedor y respuesta de `media_publish`.  
* **Supuesto que usa el código hoy**: Se tratan IN_PROGRESS, FINISHED, ERROR, EXPIRED y PUBLISHED; cualquier otro valor se considera desconocido y va a reconciliacion. La publicacion es POST a {ig_user_id}/media_publish con creation_id, y devuelve el id del medio.  
* **Se da por verificada cuando**: La lista de valores y la forma de la respuesta de `media_publish` se confirman, y se anade el tratamiento de cualquier valor que falte.  
* **Fuente** (S7c): <https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api>

#### `ig_permissions` — ⏳ pendiente, bloquea el envío real

* **Destino**: instagram  
* **Qué falta comprobar**: Lista de permisos vigente y requisitos de revision de la app.  
* **Supuesto que usa el código hoy**: Se exigen concedidos pages_show_list, pages_read_engagement, instagram_basic e instagram_content_publish, comprobados en me/permissions. No se piden permisos de mensajes ni de comentarios.  
* **Se da por verificada cuando**: La lista vigente y los requisitos de revision de la app se confirman, y se ajusta lo que falte o sobre.  
* **Fuente** (S7a): <https://www.postman.com/meta/instagram/folder/u4g5a2a/instagram-api-with-facebook-login>

#### `ig_caption_limits` — ⏳ pendiente, no bloquea

* **Destino**: instagram  
* **Qué falta comprobar**: Longitud maxima del `caption` y numero de hashtags admitidos.  
* **Supuesto que usa el código hoy**: Los mismos topes locales conservadores que en YouTube.  
* **Se da por verificada cuando**: Se comparan con los limites reales y se ajustan si son menores.  
* **Fuente** (S7b): <https://www.postman.com/meta/instagram/request/5kkpkh6/upload-a-reel-to-an-ig-container>

#### `s3_presign_expiry` — ⏳ pendiente, bloquea el envío real

* **Destino**: staging  
* **Qué falta comprobar**: Limites de caducidad de una URL firmada y su interaccion con la caducidad de las credenciales que la firman.  
* **Supuesto que usa el código hoy**: URL GET firmada con generate_presigned_url('get_object', ExpiresIn=7200). Si las credenciales son renovables se comprueba con refresh_needed(ttl) que duran mas que la URL; si no declaran caducidad, no se afirma que la cubran.  
* **Se da por verificada cuando**: El tope de caducidad admitido y su relacion con las credenciales se confirman para el proveedor elegido, y el TTL se ajusta si excede el maximo.  
* **Fuente** (S10): <https://docs.aws.amazon.com/boto3/latest/guide/s3-presigned-urls.html>

#### `tiktok_guidelines` — ⏳ pendiente, no bloquea

* **Destino**: tiktok  
* **Qué falta comprobar**: Directrices de Direct Post.  
* **Supuesto que usa el código hoy**: No se implementa Direct Post ni ningun interruptor que lo eluda: la entrega es manual. Es la decision CONSERVADORA, asi que no bloquea.  
* **Se da por verificada cuando**: Solo haria falta para AMPLIAR el alcance a una integracion oficial, que seria un cambio documentado de producto.  
* **Fuente** (S8): <https://developers.tiktok.com/docs/en/content-sharing-guidelines>

**Para levantar un bloqueo**: comprobar el parámetro contra su fuente, ajustar el
adaptador si difiere y marcar la entrada como verificada indicando la fecha y qué
se leyó. No se levanta "porque parece correcto".

---

## 13. Qué queda pendiente antes de operar en una VPS

1. **Integración externa real.** Faltan cuentas, permisos, credenciales y un
   paquete de producción. Los mocks y stubs **no la sustituyen**.
2. **Verificación de los parámetros** de la tabla anterior.
3. **Un paquete de producción admisible.** Hoy el único ejemplo del repositorio
   es un preview simulado, y eso no se publica.
4. **Decidir el almacenamiento temporal**: proveedor, bucket privado,
   credenciales y si su caducidad cubre las 2 h de la URL firmada.
5. **Instalar el timer**, que esta entrega deja preparado y sin instalar
   (`deploy/systemd/`).

### Procedimiento para probarlo después (no se ejecuta aquí)

**YouTube, subida privada.** Es el primer caso razonable porque su efecto es
reversible desde el estudio del canal:

```bash
export VIRALGEN_PUBLISH_SECRETS_DIR=~/.config/viralgen/secretos   # 0700
viralgen auth youtube                       # loopback + PKCE, desde un equipo con navegador
viralgen publish plan ... --mode real --destination "...,visibility=private"
viralgen publish accounts check --plan <plan>   # no publica: solo comprueba el canal
viralgen publish approve --plan <plan> --operator <tu identidad>
viralgen publish enqueue --plan <plan>
viralgen publish worker --once --mode real
viralgen publish status --publish-key <clave> --refresh
```

En una VPS sin navegador: conecta desde un equipo local y **copia el archivo de
token** al directorio privado, o abre un túnel al puerto de loopback. El flujo
OOB retirado no se usa.

**Instagram.** ⚠️ **Tiene efecto real e inmediato**: un Reel publicado es
público en la cuenta. Antes hacen falta la cuenta profesional vinculada a una
Página, los permisos concedidos, la app revisada si corresponde, la versión de
Graph fijada y el almacenamiento temporal configurado. Hazlo con una cuenta de
pruebas propia y con un vídeo que no te importe publicar.

---

## 14. Mapa de comandos

| Comando | Qué hace | Efectos externos |
| --- | --- | --- |
| `publish plan` | Valida en local y escribe el borrador | ninguno |
| `publish validate` | Recalcula contratos, admisión y requisitos | ninguno: no modifica las entradas |
| `publish schema` | Exporta los dos JSON Schema | ninguno |
| `publish accounts check` | Comprueba identidad y acceso | consultas de lectura |
| `publish approve` | Registra la autorización de una revisión | ninguno |
| `publish enqueue` | Persiste horario y destinos autorizados | ninguno |
| `publish worker --once` | Procesa lo vencido y deja checkpoints | **sí**, en modo real |
| `publish status [--refresh]` | Estado local; con `--refresh`, consulta remota | lectura con `--refresh` |
| `publish cancel` | Cancelación local, con sus límites | ninguno |
| `publish export-manual` | Paquete de TikTok | escribe en disco |
| `publish record-manual` | Registra lo publicado a mano | ninguno |
| `publish gc [--apply]` | Limpieza de lo propio | borra objetos con `--apply` |
| `publish verification` | Estado de la verificación documental de cada parámetro | ninguno |
| `auth youtube` | Conecta el canal (loopback + PKCE) | sí: OAuth |
| `auth instagram --from-file` | Importa un token del flujo oficial | ninguno |

`stdout` es JSON estructurado; `stderr`, los mensajes para personas. Códigos de
salida reutilizados: `3` configuración, `5` validación, `8` conflicto de
identidad, `9` trabajador bloqueado, `10` requiere revisión, `11` espera remota.

**El código de salida indica el resultado del comando.** Solo el estado y la
evidencia dicen si hubo una entrega real.
