"""Registro de lo que se pudo y no se pudo comprobar contra la fuente oficial.

Los adaptadores de este modulo hablan con APIs de terceros. Sus parametros
exactos -nombres de campo, versiones, codigos, cuotas- cambian, y el encargo es
explicito: *no sustituyas una referencia inaccesible por una afirmacion de
haberla leido*.

Durante el desarrollo **ninguna** de las referencias citadas era alcanzable: el
proxy de egreso respondio 403 a `developers.google.com`,
`developers.tiktok.com`, `docs.aws.amazon.com`, `www.postman.com`,
`developers.facebook.com` y `graph.facebook.com`. El codigo, los contratos y
las pruebas de transporte estan completos; lo que falta es contrastar los
parametros con la fuente.

Por eso esto es un modulo y no un comentario: cada entrada aparece en
`publish plan`, y las entradas pendientes que afectan a un destino **bloquean
el modo real** de ese destino. Asi la limitacion viaja con el producto en vez
de quedarse en un README que nadie relee.

Cada entrada declara cuatro cosas:

* `assumption`   - el valor o supuesto que el codigo usa HOY.
* `verified_when`- la condicion concreta para darla por verificada.
* `blocks_real`  - si impide el envio real mientras siga pendiente.
* `evidence`     - quien leyo la fuente, cuando y que confirmo. Mientras sea
  `None`, la entrada esta pendiente.

Una entrada se levanta aportando evidencia, no porque el parametro "parezca
correcto". Y la evidencia dice **quien** la aporto: si la leyo el revisor, eso
es lo que consta, sin que este modulo se atribuya un acceso que no tuvo.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Origen de una evidencia documental.
REVIEWER = "revisor"
DEVELOPER = "desarrollo"


@dataclass(frozen=True)
class SourceEvidence:
    """Constancia de que alguien leyo la fuente y que confirmo.

    No afirma nada sobre quien NO la leyo: si `confirmed_by` es el revisor, es
    porque el entorno de desarrollo no pudo abrir la URL.
    """

    confirmed_by: str
    confirmed_on: str
    states: str
    #: Hasta donde alcanza esta evidencia. Confirmar un campo no verifica la
    #: lista entera de campos de la que forma parte.
    scope: str

    def describe(self) -> dict:
        return {
            "confirmed_by": self.confirmed_by,
            "confirmed_on": self.confirmed_on,
            "states": self.states,
            "scope": self.scope,
        }


@dataclass(frozen=True)
class PendingCheck:
    """Un parametro de protocolo, con el supuesto que se usa y su estado."""

    check_id: str
    #: Destino afectado: "youtube", "instagram", "staging", "tiktok" o "*".
    target: str
    source_tag: str
    source_url: str
    what: str
    #: Lo que el codigo hace hoy con este parametro.
    assumption: str
    #: Condicion exacta para considerarlo verificado.
    verified_when: str
    #: Si impide el envio real MIENTRAS siga pendiente.
    blocks_real: bool
    #: Evidencia documental aportada. None = pendiente.
    evidence: SourceEvidence | None = None

    @property
    def verified(self) -> bool:
        return self.evidence is not None

    @property
    def blocking(self) -> bool:
        """Bloquea solo si sigue pendiente."""
        return self.blocks_real and not self.verified

    def describe(self) -> dict:
        return {
            "check_id": self.check_id,
            "target": self.target,
            "source": self.source_tag,
            "source_url": self.source_url,
            "what": self.what,
            "assumption": self.assumption,
            "verified_when": self.verified_when,
            "status": "verified" if self.verified else "pending",
            "blocks_real_dispatch": self.blocking,
            "evidence": self.evidence.describe() if self.evidence else None,
        }


#: Motivo por el que esto quedo pendiente. Se repite en cada informe.
UNREACHABLE_REASON = (
    "El proxy de egreso del entorno de desarrollo respondio 403 a los hosts de "
    "documentacion (developers.google.com, developers.facebook.com, "
    "www.postman.com, developers.tiktok.com, docs.aws.amazon.com). Las "
    "entradas sin evidencia no estan contrastadas con su fuente; las que la "
    "tienen declaran quien la aporto."
)

CHECKS: tuple[PendingCheck, ...] = (
    # --- YouTube ---------------------------------------------------------
    PendingCheck(
        check_id="yt_insert_part_and_fields",
        target="youtube",
        source_tag="S1",
        source_url="https://developers.google.com/youtube/v3/docs/videos/insert",
        what=(
            "Nombres exactos de `part`, de los campos de `snippet`/`status` y de "
            "las propiedades de audiencia y divulgacion admitidas por la version "
            "vigente."
        ),
        assumption=(
            "part='snippet,status'; cuerpo con snippet.title, snippet.description, "
            "snippet.tags (solo si hay), snippet.defaultLanguage, "
            "status.privacyStatus en {public, private, unlisted}, "
            "status.selfDeclaredMadeForKids booleano y "
            "status.containsSyntheticMedia booleano; notifySubscribers como "
            "parametro de consulta con 'true'/'false'."
        ),
        verified_when=(
            "Cada nombre y cada valor admitido se compara con el recurso `videos` "
            "y con `videos.insert`, y se corrige lo que difiera. La evidencia de "
            "`yt_synthetic_media_property` cubre UNA de estas propiedades, no la "
            "lista completa."
        ),
        blocks_real=True,
    ),
    PendingCheck(
        check_id="yt_synthetic_media_property",
        target="youtube",
        source_tag="S2",
        source_url=(
            "https://developers.google.com/youtube/v3/docs/videos"
            "#status.containsSyntheticMedia"
        ),
        what=(
            "Nombre y tipo de la propiedad con la que YouTube recoge la "
            "divulgacion de contenido sintetico realista."
        ),
        assumption=(
            "Se envia `status.containsSyntheticMedia` como booleano en "
            "`videos.insert`: true cuando la decision editorial aprobada dice que "
            "contiene medios sinteticos realistas y false cuando dice que no."
        ),
        verified_when=(
            "Confirmado que la propiedad existe con ese nombre, es booleana y la "
            "admite `videos.insert`."
        ),
        blocks_real=True,
        evidence=SourceEvidence(
            confirmed_by=REVIEWER,
            confirmed_on="2026-09-26",
            states=(
                "`status.containsSyntheticMedia` existe, es booleano y lo admite "
                "`videos.insert`."
            ),
            scope=(
                "Solo esta propiedad. No verifica el resto de los campos de "
                "`snippet`/`status` ni el valor de `part`, que siguen en "
                "`yt_insert_part_and_fields`."
            ),
        ),
    ),
    PendingCheck(
        check_id="yt_resumable_protocol",
        target="youtube",
        source_tag="S5",
        source_url=(
            "https://developers.google.com/youtube/v3/guides/"
            "using_resumable_upload_protocol"
        ),
        what=(
            "Cabeceras exactas de inicio de sesion, semantica de 308, formato de "
            "`Range`/`Content-Range` y multiplo de bloque exigido."
        ),
        assumption=(
            "POST con uploadType=resumable y cabeceras X-Upload-Content-Length y "
            "X-Upload-Content-Type; la URI de sesion llega en `Location`; cada "
            "bloque va en PUT con 'Content-Range: bytes a-b/total'; un 308 es "
            "subida incompleta y su `Range: bytes=0-N` marca el offset "
            "confirmado; el estado se consulta con 'Content-Range: bytes */total'; "
            "404 o 410 significan sesion desaparecida; bloque de 8 MiB (multiplo "
            "de 256 KiB)."
        ),
        verified_when=(
            "Cada cabecera, el significado del 308 y el multiplo de bloque "
            "coinciden con la guia del protocolo, o el adaptador se ajusta."
        ),
        blocks_real=True,
    ),
    PendingCheck(
        check_id="yt_oauth_installed_app",
        target="youtube",
        source_tag="S3",
        source_url="https://developers.google.com/youtube/v3/guides/auth/installed-apps",
        what=(
            "Endpoints de autorizacion y token, parametros de PKCE y scopes "
            "efectivos para subir y para verificar el canal."
        ),
        assumption=(
            "Autorizacion en accounts.google.com/o/oauth2/v2/auth y token en "
            "oauth2.googleapis.com/token; response_type=code, "
            "code_challenge_method=S256, access_type=offline, prompt=consent y "
            "redirect_uri de loopback http://127.0.0.1:<puerto>/; scopes "
            "youtube.upload y youtube.readonly."
        ),
        verified_when=(
            "Los dos endpoints, los parametros de PKCE y los scopes minimos "
            "necesarios para subir y para leer el resultado se confirman en la "
            "guia de aplicaciones instaladas."
        ),
        blocks_real=True,
    ),
    PendingCheck(
        check_id="yt_quota_units",
        target="youtube",
        source_tag="S1",
        source_url="https://developers.google.com/youtube/v3/docs/videos/insert",
        what="Unidades de cuota por operacion y bucket.",
        assumption=(
            "NINGUNA cifra de cuota de Google se usa en el codigo. El presupuesto "
            "local (200 solicitudes por destino) es un limite del producto y no "
            "se presenta como cuota de la plataforma."
        ),
        verified_when=(
            "Se documentan las unidades por operacion y se hacen configurables, "
            "sin mezclarlas con el presupuesto local."
        ),
        # No bloquea: no publicar de mas no depende de esta cifra, y el limite
        # local es conservador.
        blocks_real=False,
    ),
    PendingCheck(
        check_id="yt_text_limits",
        target="youtube",
        source_tag="S2",
        source_url="https://developers.google.com/youtube/v3/docs/videos",
        what="Longitudes maximas de titulo, descripcion y etiquetas.",
        assumption=(
            "Topes LOCALES conservadores: 100 caracteres de titulo, 2200 de "
            "descripcion, 30 etiquetas y 12 hashtags. Superarlos marca revision; "
            "el texto no se recorta en silencio."
        ),
        verified_when=(
            "Los limites reales se comparan con los topes locales y estos se "
            "ajustan si son mayores que los de la plataforma."
        ),
        # Un payload excesivo produce un rechazo limpio y clasificado, no una
        # publicacion equivocada.
        blocks_real=False,
    ),
    # --- Instagram -------------------------------------------------------
    PendingCheck(
        check_id="ig_graph_version",
        target="instagram",
        source_tag="S7a",
        source_url=(
            "https://www.postman.com/meta/instagram/folder/u4g5a2a/"
            "instagram-api-with-facebook-login"
        ),
        what="Version de Graph soportada y vigente.",
        assumption=(
            "NINGUNA por defecto: META_GRAPH_API_VERSION es obligatoria y "
            "explicita en modo real. No se usa `latest` ni se cambia de version "
            "automaticamente."
        ),
        verified_when=(
            "Se comprueba que la version que fija el operador esta soportada y "
            "vigente, y que los campos de esta entrega corresponden a ella."
        ),
        blocks_real=True,
    ),
    PendingCheck(
        check_id="ig_container_fields",
        target="instagram",
        source_tag="S7b",
        source_url=(
            "https://www.postman.com/meta/instagram/request/5kkpkh6/"
            "upload-a-reel-to-an-ig-container"
        ),
        what="Campos exactos del contenedor de Reel y su host.",
        assumption=(
            "POST a {graph_base}/{version}/{ig_user_id}/media con media_type="
            "'REELS', video_url (URL firmada temporal), caption y share_to_feed "
            "'true'/'false'; host graph.facebook.com, no el endpoint de Reels de "
            "una Pagina de Facebook."
        ),
        verified_when=(
            "Los cuatro campos, el host y la ruta coinciden con la coleccion "
            "oficial para la version fijada."
        ),
        blocks_real=True,
    ),
    PendingCheck(
        check_id="ig_status_values",
        target="instagram",
        source_tag="S7c",
        source_url=(
            "https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api"
        ),
        what="Valores de `status_code` del contenedor y respuesta de `media_publish`.",
        assumption=(
            "Se tratan IN_PROGRESS, FINISHED, ERROR, EXPIRED y PUBLISHED; "
            "cualquier otro valor se considera desconocido y va a reconciliacion. "
            "La publicacion es POST a {ig_user_id}/media_publish con "
            "creation_id, y devuelve el id del medio."
        ),
        verified_when=(
            "La lista de valores y la forma de la respuesta de `media_publish` se "
            "confirman, y se anade el tratamiento de cualquier valor que falte."
        ),
        blocks_real=True,
    ),
    PendingCheck(
        check_id="ig_permissions",
        target="instagram",
        source_tag="S7a",
        source_url=(
            "https://www.postman.com/meta/instagram/folder/u4g5a2a/"
            "instagram-api-with-facebook-login"
        ),
        what="Lista de permisos vigente y requisitos de revision de la app.",
        assumption=(
            "Se exigen concedidos pages_show_list, pages_read_engagement, "
            "instagram_basic e instagram_content_publish, comprobados en "
            "me/permissions. No se piden permisos de mensajes ni de comentarios."
        ),
        verified_when=(
            "La lista vigente y los requisitos de revision de la app se "
            "confirman, y se ajusta lo que falte o sobre."
        ),
        blocks_real=True,
    ),
    PendingCheck(
        check_id="ig_caption_limits",
        target="instagram",
        source_tag="S7b",
        source_url=(
            "https://www.postman.com/meta/instagram/request/5kkpkh6/"
            "upload-a-reel-to-an-ig-container"
        ),
        what="Longitud maxima del `caption` y numero de hashtags admitidos.",
        assumption="Los mismos topes locales conservadores que en YouTube.",
        verified_when="Se comparan con los limites reales y se ajustan si son menores.",
        blocks_real=False,
    ),
    # --- Staging ---------------------------------------------------------
    PendingCheck(
        check_id="s3_presign_expiry",
        target="staging",
        source_tag="S10",
        source_url="https://docs.aws.amazon.com/boto3/latest/guide/s3-presigned-urls.html",
        what=(
            "Limites de caducidad de una URL firmada y su interaccion con la "
            "caducidad de las credenciales que la firman."
        ),
        assumption=(
            "URL GET firmada con generate_presigned_url('get_object', "
            "ExpiresIn=7200). Si las credenciales son renovables se comprueba con "
            "refresh_needed(ttl) que duran mas que la URL; si no declaran "
            "caducidad, no se afirma que la cubran."
        ),
        verified_when=(
            "El tope de caducidad admitido y su relacion con las credenciales se "
            "confirman para el proveedor elegido, y el TTL se ajusta si excede el "
            "maximo."
        ),
        blocks_real=True,
    ),
    # --- TikTok ----------------------------------------------------------
    PendingCheck(
        check_id="tiktok_guidelines",
        target="tiktok",
        source_tag="S8",
        source_url="https://developers.tiktok.com/docs/en/content-sharing-guidelines",
        what="Directrices de Direct Post.",
        assumption=(
            "No se implementa Direct Post ni ningun interruptor que lo eluda: la "
            "entrega es manual. Es la decision CONSERVADORA, asi que no bloquea."
        ),
        verified_when=(
            "Solo haria falta para AMPLIAR el alcance a una integracion oficial, "
            "que seria un cambio documentado de producto."
        ),
        blocks_real=False,
    ),
)

#: Nombre historico. `CHECKS` incluye tambien las entradas ya verificadas.
PENDING_CHECKS: tuple[PendingCheck, ...] = CHECKS


def checks_for(target: str) -> list[PendingCheck]:
    """Todas las entradas que afectan a un destino, verificadas o no."""
    return [entrada for entrada in CHECKS if entrada.target in (target, "*")]


def pending_for(target: str) -> list[PendingCheck]:
    """Las que siguen sin evidencia para ese destino."""
    return [entrada for entrada in checks_for(target) if not entrada.verified]


def blocking_for(target: str) -> list[PendingCheck]:
    """Las que impiden operar ese destino en modo real."""
    return [entrada for entrada in checks_for(target) if entrada.blocking]


def verified_for(target: str) -> list[PendingCheck]:
    """Las que ya tienen evidencia, con quien la aporto."""
    return [entrada for entrada in checks_for(target) if entrada.verified]


def find(check_id: str) -> PendingCheck:
    """Una entrada por su identificador. Falla si no existe: es un error tipografico."""
    for entrada in CHECKS:
        if entrada.check_id == check_id:
            return entrada
    raise KeyError(f"no hay ninguna verificacion con id {check_id!r}")


def describe_all() -> dict:
    return {
        "reason": UNREACHABLE_REASON,
        "checks": [entrada.describe() for entrada in CHECKS],
        "totals": {
            "entries": len(CHECKS),
            "pending": sum(1 for entrada in CHECKS if not entrada.verified),
            "verified": sum(1 for entrada in CHECKS if entrada.verified),
            "blocking": sum(1 for entrada in CHECKS if entrada.blocking),
        },
        "blocked_targets": sorted(
            {entrada.target for entrada in CHECKS if entrada.blocking}
        ),
        "note": (
            "Estas entradas describen el estado de la VERIFICACION, no defectos "
            "conocidos. El codigo y sus pruebas de transporte estan completos. "
            "Una entrada verificada declara quien aporto la evidencia: este "
            "modulo no se atribuye accesos que no tuvo."
        ),
    }
