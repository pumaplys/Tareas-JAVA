"""Registro del estado de verificacion de cada parametro de protocolo.

Los adaptadores de este modulo hablan con APIs de terceros. Sus parametros
exactos -nombres de campo, versiones, codigos, cuotas- cambian, y el encargo es
explicito: *no sustituyas una referencia inaccesible por una afirmacion de
haberla leido*.

**Este entorno no alcanza la documentacion.** El proxy de egreso respondio 403
a `developers.google.com`, `developers.facebook.com`, `docs.aws.amazon.com`,
`www.postman.com` y `developers.tiktok.com`; se volvio a comprobar el
2026-10-05 con el mismo resultado, con PyPI respondiendo 200 como control. Por
tanto **ninguna evidencia de este registro la aporto el desarrollo**: las que
hay vienen del revisor, y cada una dice quien y cuando.

Por eso esto es un modulo y no un comentario: cada entrada aparece en
`publish plan` y en `publish verification`, y una entrada que siga sin
verificar **bloquea el modo real** del destino al que afecta. Asi la limitacion
viaja con el producto en vez de quedarse en un README que nadie relee.

Cada entrada declara:

* `assumption`    - el valor o supuesto que el codigo usa HOY.
* `verified_when` - la condicion concreta para darla por verificada.
* `status`        - `pending`, `partial` o `verified`.
* `blocks_real`   - si impediria el envio real mientras no este verificada.
* `evidence`      - historial: quien leyo la fuente, cuando, que confirma y
  hasta donde alcanza. Varias entradas si se consulto mas de una vez.

`partial` existe porque es lo que de verdad pasa: una consulta puede confirmar
el host y la ruta de un endpoint y dejar sin cerrar la version, o documentar
una cuota sin que el codigo la haga configurable. Un `partial` NO levanta un
bloqueo: solo deja escrito lo que ya se sabe y lo que falta.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

#: Quien aporta una evidencia.
REVIEWER = "revisor"
DEVELOPER = "desarrollo"


class CheckStatus(StrEnum):
    """Estado de la verificacion de un parametro."""

    PENDING = "pending"
    """Sin evidencia."""

    PARTIAL = "partial"
    """Hay evidencia, pero no cubre la condicion completa. Sigue bloqueando."""

    VERIFIED = "verified"
    """La condicion se cumple con la evidencia aportada."""


@dataclass(frozen=True)
class SourceEvidence:
    """Constancia de que alguien leyo la fuente, y de que confirmo.

    No afirma nada sobre quien NO la leyo: si `confirmed_by` es el revisor, es
    porque este entorno no pudo abrir la URL. El `scope` acota el alcance:
    confirmar un campo no verifica la lista entera de la que forma parte.
    """

    confirmed_by: str
    confirmed_on: str
    states: str
    scope: str
    sources: tuple[str, ...] = ()

    def describe(self) -> dict:
        return {
            "confirmed_by": self.confirmed_by,
            "confirmed_on": self.confirmed_on,
            "states": self.states,
            "scope": self.scope,
            "sources": list(self.sources),
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
    #: Si impediria el envio real mientras no este verificado.
    blocks_real: bool
    status: CheckStatus = CheckStatus.PENDING
    #: Historial de consultas, de la mas antigua a la mas reciente.
    evidence: tuple[SourceEvidence, ...] = field(default_factory=tuple)
    #: Observaciones fechadas que NO confirman nada, como un intento de
    #: consulta que devolvio un error de acceso. Se guardan para que conste que
    #: alguien lo intento, sin que eso cuente como evidencia.
    notes: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.status is not CheckStatus.PENDING and not self.evidence:
            raise ValueError(
                f"{self.check_id}: un estado distinto de `pending` necesita "
                "evidencia que lo respalde"
            )
        if self.status is CheckStatus.PENDING and self.evidence:
            raise ValueError(
                f"{self.check_id}: hay evidencia, asi que el estado no puede ser "
                "`pending`; usa `partial` si no cierra la condicion"
            )

    @property
    def verified(self) -> bool:
        return self.status is CheckStatus.VERIFIED

    @property
    def blocking(self) -> bool:
        """Bloquea mientras no este VERIFICADO. Un `partial` sigue bloqueando."""
        return self.blocks_real and not self.verified

    @property
    def latest_evidence(self) -> SourceEvidence | None:
        return self.evidence[-1] if self.evidence else None

    def describe(self) -> dict:
        return {
            "check_id": self.check_id,
            "target": self.target,
            "source": self.source_tag,
            "source_url": self.source_url,
            "what": self.what,
            "assumption": self.assumption,
            "verified_when": self.verified_when,
            "status": self.status.value,
            "blocks_real_dispatch": self.blocking,
            "evidence": [entrada.describe() for entrada in self.evidence],
            "notes": list(self.notes),
        }


#: Por que este entorno no aporta evidencia propia. Viaja en cada informe.
UNREACHABLE_REASON = (
    "El proxy de egreso de este entorno respondio 403 a los hosts de "
    "documentacion (developers.google.com, developers.facebook.com, "
    "www.postman.com, developers.tiktok.com, docs.aws.amazon.com), comprobado "
    "de nuevo el 2026-10-05 con PyPI respondiendo 200 como control. Ninguna "
    "evidencia de este registro la aporto el desarrollo: cada una declara "
    "quien la leyo y cuando."
)

#: URLs oficiales citadas por el revisor, por etiqueta.
SOURCES: dict[str, str] = {
    "S1": "https://developers.google.com/youtube/v3/docs/videos/insert",
    "S2": "https://developers.google.com/youtube/v3/docs/videos",
    "S3": "https://developers.google.com/youtube/v3/guides/auth/installed-apps",
    "S4": "https://developers.google.com/youtube/v3/docs/channels/list",
    "S5": "https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol",
    "S6": "https://developers.google.com/youtube/v3/docs/videos/list",
    "S7a": (
        "https://www.postman.com/meta/instagram/folder/u4g5a2a/"
        "instagram-api-with-facebook-login"
    ),
    "S7b": (
        "https://www.postman.com/meta/instagram/request/5kkpkh6/"
        "upload-a-reel-to-an-ig-container"
    ),
    "S7c": "https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api",
    "S8": "https://developers.tiktok.com/docs/en/content-sharing-guidelines",
    "S10": "https://docs.aws.amazon.com/boto3/latest/guide/s3-presigned-urls.html",
    "S11": (
        "https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-presigned-url.html"
    ),
    "S12": "https://developers.google.com/identity/protocols/oauth2/web-server",
}

#: Fecha de la revision documental que aporto la mayor parte de la evidencia.
REVIEW_2026_10_05 = "2026-10-05"

CHECKS: tuple[PendingCheck, ...] = (
    # --- YouTube ---------------------------------------------------------
    PendingCheck(
        check_id="yt_insert_part_and_fields",
        target="youtube",
        source_tag="S1",
        source_url=SOURCES["S1"],
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
            "status.containsSyntheticMedia booleano. `notifySubscribers` viaja "
            "como parametro de consulta y SIEMPRE explicito ('true'/'false'), "
            "precisamente porque su valor predeterminado es verdadero: asi no "
            "decide por nosotros."
        ),
        verified_when=(
            "Cada nombre y cada valor admitido se compara con el recurso `videos` "
            "y con `videos.insert`, y se corrige lo que difiera."
        ),
        blocks_real=True,
        status=CheckStatus.VERIFIED,
        evidence=(
            SourceEvidence(
                confirmed_by=REVIEWER,
                confirmed_on=REVIEW_2026_10_05,
                states=(
                    "Se admiten part='snippet,status'; snippet.title, "
                    "snippet.description, snippet.tags[] y snippet.defaultLanguage; "
                    "los campos de `status` enumerados. Privacidad: private, "
                    "public, unlisted. `selfDeclaredMadeForKids` y "
                    "`containsSyntheticMedia` son booleanos. `notifySubscribers` "
                    "es un parametro booleano de consulta cuyo valor "
                    "predeterminado es verdadero."
                ),
                scope=(
                    "Acredita el VOCABULARIO del protocolo para los nombres y "
                    "tipos enumerados. Los valores concretos siguen saliendo del "
                    "plan aprobado, y no dice nada de campos no enumerados."
                ),
                sources=(SOURCES["S1"], SOURCES["S2"]),
            ),
        ),
    ),
    PendingCheck(
        check_id="yt_synthetic_media_property",
        target="youtube",
        source_tag="S2",
        source_url=SOURCES["S2"] + "#status.containsSyntheticMedia",
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
        status=CheckStatus.VERIFIED,
        evidence=(
            SourceEvidence(
                confirmed_by=REVIEWER,
                confirmed_on="2026-09-26",
                states=(
                    "`status.containsSyntheticMedia` existe, es booleano y lo "
                    "admite `videos.insert`."
                ),
                scope=(
                    "Solo esta propiedad. No verifica el resto de los campos de "
                    "`snippet`/`status` ni el valor de `part`."
                ),
                sources=(SOURCES["S2"],),
            ),
            SourceEvidence(
                confirmed_by=REVIEWER,
                confirmed_on=REVIEW_2026_10_05,
                states="Reconfirmado: propiedad booleana admitida por `videos.insert`.",
                scope="Segunda consulta de la misma propiedad.",
                sources=(SOURCES["S1"], SOURCES["S2"]),
            ),
        ),
    ),
    PendingCheck(
        check_id="yt_resumable_protocol",
        target="youtube",
        source_tag="S5",
        source_url=SOURCES["S5"],
        what=(
            "Cabeceras exactas de inicio de sesion, semantica de 308, formato de "
            "`Range`/`Content-Range` y multiplo de bloque exigido."
        ),
        assumption=(
            "POST con uploadType=resumable y cabeceras X-Upload-Content-Length y "
            "X-Upload-Content-Type; la URI de sesion llega en `Location`; cada "
            "bloque va en PUT con 'Content-Range: bytes a-b/total' y todos los "
            "bloques ordinarios miden lo mismo (8 MiB, multiplo de 256 KiB), con "
            "el ultimo como excepcion; un 308 es subida incompleta y su "
            "'Range: bytes=0-N' marca el offset confirmado, y si falta `Range` no "
            "hay ningun byte confirmado; el estado se consulta con "
            "'Content-Range: bytes */total'. La regla DOCUMENTADA de sesion "
            "caducada es 404. El tratamiento de 410 es una decision DEFENSIVA "
            "PROPIA de este proyecto, no atribuida a Google: lleva a "
            "reconciliacion igual que el 404, nunca a crear otro video."
        ),
        verified_when=(
            "Las cabeceras, el significado del 308 y el multiplo y la igualdad de "
            "tamano de los bloques coinciden con la guia del protocolo."
        ),
        blocks_real=True,
        status=CheckStatus.VERIFIED,
        evidence=(
            SourceEvidence(
                confirmed_by=REVIEWER,
                confirmed_on=REVIEW_2026_10_05,
                states=(
                    "La guia confirma POST con uploadType=resumable, cabeceras "
                    "X-Upload-Content-Length/Type, sesion en `Location`, PUT con "
                    "rangos, consulta vacia con 'bytes */total' y 308 con el "
                    "ultimo byte confirmado en `Range`; si falta `Range`, no hay "
                    "bytes confirmados. Los bloques ordinarios deben ser "
                    "multiplos de 256 KiB y del mismo tamano, con el ultimo como "
                    "excepcion; 8 MiB cumple. Documenta 404 para una sesion "
                    "caducada y NO menciona 410."
                ),
                scope=(
                    "Cubre el protocolo documentado. El tratamiento de 410 NO "
                    "esta respaldado por la guia y queda declarado como decision "
                    "propia; una sesion inaccesible tras un resultado incierto no "
                    "prueba que no exista ya un video, asi que se conserva la "
                    "prevencion de duplicados."
                ),
                sources=(SOURCES["S5"],),
            ),
        ),
    ),
    PendingCheck(
        check_id="yt_oauth_installed_app",
        target="youtube",
        source_tag="S3",
        source_url=SOURCES["S3"],
        what=(
            "Endpoints de autorizacion y token, parametros de PKCE y scopes "
            "efectivos para subir y para verificar el canal."
        ),
        assumption=(
            "Autorizacion en accounts.google.com/o/oauth2/v2/auth y token en "
            "oauth2.googleapis.com/token; response_type=code, "
            "code_challenge_method=S256, access_type=offline, prompt=consent y "
            "redirect_uri de loopback http://127.0.0.1:<puerto>/, identico en la "
            "autorizacion y en el canje -puerto y ruta incluidos-; scopes "
            "youtube.upload y youtube.readonly."
        ),
        verified_when=(
            "Los dos endpoints, los parametros de PKCE y los scopes minimos "
            "necesarios para subir y para leer el resultado se confirman en la "
            "guia de aplicaciones instaladas."
        ),
        blocks_real=True,
        status=CheckStatus.VERIFIED,
        evidence=(
            SourceEvidence(
                confirmed_by=REVIEWER,
                confirmed_on=REVIEW_2026_10_05,
                states=(
                    "La guia de aplicaciones instaladas confirma autorizacion en "
                    "https://accounts.google.com/o/oauth2/v2/auth, canje en "
                    "https://oauth2.googleapis.com/token, response_type=code, "
                    "PKCE S256 y receptor loopback para cliente de escritorio, y "
                    "documenta los scopes youtube.upload y youtube.readonly. La "
                    "documentacion complementaria de Google explica "
                    "access_type=offline y prompt=consent. El redirect_uri debe "
                    "mantenerse consistente entre autorizacion y canje, puerto y "
                    "ruta incluidos."
                ),
                scope=(
                    "Acredita los parametros del protocolo. NO acredita que el "
                    "usuario haya concedido los permisos ni que el token "
                    "pertenezca al canal esperado: eso se comprueba en ejecucion "
                    "con channels.list(mine=true) contra el ID autorizado."
                ),
                sources=(SOURCES["S3"], SOURCES["S12"]),
            ),
        ),
    ),
    PendingCheck(
        check_id="yt_quota_units",
        target="youtube",
        source_tag="S1",
        source_url=SOURCES["S1"],
        what="Unidades de cuota por operacion y bucket.",
        assumption=(
            "NINGUNA cifra de cuota de Google se usa en el codigo. El presupuesto "
            "local (200 solicitudes por destino) es un limite del producto y no "
            "se presenta como cuota de la plataforma."
        ),
        verified_when=(
            "Las unidades por operacion se documentan Y se hacen configurables, "
            "sin mezclarlas con el presupuesto local."
        ),
        blocks_real=False,
        status=CheckStatus.PARTIAL,
        evidence=(
            SourceEvidence(
                confirmed_by=REVIEWER,
                confirmed_on=REVIEW_2026_10_05,
                states=(
                    "La referencia consultada indica 100 llamadas diarias y una "
                    "unidad en el grupo de cuota de subidas."
                ),
                scope=(
                    "Informacion documental FECHADA. No es una cuota concedida a "
                    "este proyecto, no sustituye al presupuesto local de "
                    "solicitudes y no cierra la entrada, porque esas unidades "
                    "siguen sin ser configurables en el codigo."
                ),
                sources=(SOURCES["S1"],),
            ),
        ),
    ),
    PendingCheck(
        check_id="yt_text_limits",
        target="youtube",
        source_tag="S2",
        source_url=SOURCES["S2"],
        what="Longitudes maximas de titulo, descripcion y etiquetas.",
        assumption=(
            "Topes LOCALES conservadores, medidos en CARACTERES: 100 de titulo, "
            "2200 de descripcion, 30 etiquetas y 12 hashtags. Superarlos marca "
            "revision y el texto no se recorta en silencio. OJO: el limite de "
            "descripcion de la plataforma esta en BYTES y el de etiquetas es "
            "AGREGADO, asi que los topes locales no son comparables y no "
            "garantizan el cumplimiento; un payload excesivo produce un rechazo "
            "clasificado, no una publicacion equivocada."
        ),
        verified_when=(
            "Los topes locales se expresan en las MISMAS unidades que los de la "
            "plataforma -bytes para la descripcion, agregado para las etiquetas- "
            "y se comprueban antes de enviar."
        ),
        blocks_real=False,
        status=CheckStatus.PARTIAL,
        evidence=(
            SourceEvidence(
                confirmed_by=REVIEWER,
                confirmed_on=REVIEW_2026_10_05,
                states=(
                    "La referencia establece titulo de 100 caracteres, "
                    "descripcion de 5.000 bytes y etiquetas con limite agregado "
                    "de 500 caracteres, contando separadores y comillas "
                    "aplicables."
                ),
                scope=(
                    "Documenta los limites de la plataforma. No cierra la entrada: "
                    "2.200 caracteres no garantizan 5.000 bytes y 30 etiquetas no "
                    "garantizan el agregado de 500 caracteres. Alinear las "
                    "unidades es un cambio de metadatos que queda fuera del "
                    "encargo actual."
                ),
                sources=(SOURCES["S2"],),
            ),
        ),
    ),
    # --- Instagram -------------------------------------------------------
    PendingCheck(
        check_id="ig_graph_version",
        target="instagram",
        source_tag="S7a",
        source_url=SOURCES["S7a"],
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
        notes=(
            "2026-10-05 (revisor): no se ha elegido una version concreta, asi que "
            "no hay nada que contrastar todavia.",
        ),
    ),
    PendingCheck(
        check_id="ig_container_fields",
        target="instagram",
        source_tag="S7b",
        source_url=SOURCES["S7b"],
        what="Campos exactos del contenedor de Reel y su host.",
        assumption=(
            "POST a {graph_base}/{version}/{ig_user_id}/media con media_type="
            "'REELS', video_url (URL firmada temporal), caption y share_to_feed "
            "'true'/'false'; host graph.facebook.com, no el endpoint de Reels de "
            "una Pagina de Facebook."
        ),
        verified_when=(
            "Los cuatro campos, el host y la ruta se confirman PARA LA VERSION "
            "que fije el operador."
        ),
        blocks_real=True,
        status=CheckStatus.PARTIAL,
        evidence=(
            SourceEvidence(
                confirmed_by=REVIEWER,
                confirmed_on=REVIEW_2026_10_05,
                states=(
                    "La coleccion oficial de Meta muestra el host, la ruta y los "
                    "cuatro parametros indicados."
                ),
                scope=(
                    "La coleccion usa una VARIABLE de version, asi que la "
                    "compatibilidad con la version concreta que se configure "
                    "sigue sin contrastar. No levanta el bloqueo."
                ),
                sources=(SOURCES["S7b"],),
            ),
        ),
    ),
    PendingCheck(
        check_id="ig_status_values",
        target="instagram",
        source_tag="S7c",
        source_url=SOURCES["S7c"],
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
        notes=(
            "2026-10-05 (revisor): la pagina documental general recuperada ese dia "
            "no expuso la lista completa de estados. No se verifica nada.",
        ),
    ),
    PendingCheck(
        check_id="ig_permissions",
        target="instagram",
        source_tag="S7a",
        source_url=SOURCES["S7a"],
        what="Lista de permisos vigente y requisitos de revision de la app.",
        assumption=(
            "Se exigen concedidos pages_show_list, pages_read_engagement, "
            "instagram_basic e instagram_content_publish, comprobados en "
            "me/permissions. No se piden permisos de mensajes ni de comentarios."
        ),
        verified_when=(
            "La lista vigente y los requisitos de revision de la app se "
            "confirman para la configuracion concreta, y se ajusta lo que falte."
        ),
        blocks_real=True,
        notes=(
            "2026-10-05 (revisor): no se pudo cerrar la lista aplicable ni los "
            "requisitos de revision para la configuracion concreta.",
        ),
    ),
    PendingCheck(
        check_id="ig_caption_limits",
        target="instagram",
        source_tag="S7b",
        source_url=SOURCES["S7b"],
        what="Longitud maxima del `caption` y numero de hashtags admitidos.",
        assumption="Los mismos topes locales conservadores que en YouTube.",
        verified_when="Se comparan con los limites reales y se ajustan si son menores.",
        blocks_real=False,
        notes=(
            "2026-10-05 (revisor): pendiente. Los topes propios no acreditan los "
            "limites de Instagram.",
        ),
    ),
    # --- Staging ---------------------------------------------------------
    PendingCheck(
        check_id="s3_presign_expiry",
        target="staging",
        source_tag="S10",
        source_url=SOURCES["S10"],
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
            "confirman PARA EL PROVEEDOR ELEGIDO y su configuracion efectiva."
        ),
        blocks_real=True,
        status=CheckStatus.PARTIAL,
        evidence=(
            SourceEvidence(
                confirmed_by=REVIEWER,
                confirmed_on=REVIEW_2026_10_05,
                states=(
                    "Boto3 admite `get_object` con `ExpiresIn`, y 7.200 segundos "
                    "esta dentro del maximo documentado para SDK. Con credenciales "
                    "temporales, una URL no conserva validez mas alla de las "
                    "credenciales que la firmaron, y las politicas del bucket "
                    "pueden imponer una restriccion adicional."
                ),
                scope=(
                    "Vale para AWS S3. No cubre el proveedor compatible que se "
                    "elija ni su configuracion efectiva, y no certifica la "
                    "implementacion de refresh_needed(ttl) ni la vida util de las "
                    "credenciales que use el codigo."
                ),
                sources=(SOURCES["S10"], SOURCES["S11"]),
            ),
        ),
    ),
    # --- TikTok ----------------------------------------------------------
    PendingCheck(
        check_id="tiktok_guidelines",
        target="tiktok",
        source_tag="S8",
        source_url=SOURCES["S8"],
        what="Directrices de Direct Post.",
        assumption=(
            "No se implementa Direct Post ni ningun interruptor que lo eluda: la "
            "entrega es manual."
        ),
        verified_when=(
            "Se confirma que las directrices excluyen este caso de uso, que es el "
            "fundamento de la entrega manual."
        ),
        blocks_real=False,
        status=CheckStatus.VERIFIED,
        evidence=(
            SourceEvidence(
                confirmed_by=REVIEWER,
                confirmed_on=REVIEW_2026_10_05,
                states=(
                    "La guia de Direct Post excluye utilidades privadas "
                    "destinadas a las cuentas propias o del equipo."
                ),
                scope=(
                    "Confirma el fundamento de mantener la entrega manual. No se "
                    "ha verificado ni habilitado ninguna integracion Direct Post; "
                    "ampliar el alcance seria un cambio documentado de producto."
                ),
                sources=(SOURCES["S8"],),
            ),
        ),
    ),
)

#: Nombre historico. `CHECKS` incluye tambien las entradas ya verificadas.
PENDING_CHECKS: tuple[PendingCheck, ...] = CHECKS


def checks_for(target: str) -> list[PendingCheck]:
    """Todas las entradas que afectan a un destino, verificadas o no."""
    return [entrada for entrada in CHECKS if entrada.target in (target, "*")]


def pending_for(target: str) -> list[PendingCheck]:
    """Las que no estan verificadas para ese destino (incluye las parciales)."""
    return [entrada for entrada in checks_for(target) if not entrada.verified]


def blocking_for(target: str) -> list[PendingCheck]:
    """Las que impiden operar ese destino en modo real."""
    return [entrada for entrada in checks_for(target) if entrada.blocking]


def verified_for(target: str) -> list[PendingCheck]:
    """Las que ya cierran su condicion, con quien aporto la evidencia."""
    return [entrada for entrada in checks_for(target) if entrada.verified]


def find(check_id: str) -> PendingCheck:
    """Una entrada por su identificador. Falla si no existe: seria un error."""
    for entrada in CHECKS:
        if entrada.check_id == check_id:
            return entrada
    raise KeyError(f"no hay ninguna verificacion con id {check_id!r}")


def totals() -> dict[str, int]:
    return {
        "entries": len(CHECKS),
        "verified": sum(1 for e in CHECKS if e.status is CheckStatus.VERIFIED),
        "partial": sum(1 for e in CHECKS if e.status is CheckStatus.PARTIAL),
        "pending": sum(1 for e in CHECKS if e.status is CheckStatus.PENDING),
        "blocking": sum(1 for e in CHECKS if e.blocking),
    }


def describe_all() -> dict:
    return {
        "reason": UNREACHABLE_REASON,
        "checks": [entrada.describe() for entrada in CHECKS],
        "totals": totals(),
        "blocked_targets": sorted({e.target for e in CHECKS if e.blocking}),
        "note": (
            "Estas entradas describen el estado de la VERIFICACION, no defectos "
            "conocidos. Una entrada verificada declara quien aporto la evidencia "
            "y hasta donde alcanza; una parcial tiene evidencia que no cierra su "
            "condicion y sigue bloqueando si le corresponde."
        ),
    }
