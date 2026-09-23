"""Registro de lo que NO se pudo comprobar contra la documentacion oficial.

Los adaptadores de este modulo hablan con APIs de terceros. Sus parametros
exactos —nombres de campo, versiones, codigos, cuotas— cambian, y el encargo es
explicito: *no sustituyas una referencia inaccesible por una afirmacion de
haberla leido*.

Durante esta entrega **ninguna** de las referencias citadas era alcanzable: el
proxy de egreso del entorno respondio 403 a `developers.google.com`,
`developers.tiktok.com`, `docs.aws.amazon.com`, `www.postman.com`,
`developers.facebook.com` y `graph.facebook.com`. El codigo, los contratos y
las pruebas de transporte estan completos; lo que falta es contrastar los
parametros con la fuente.

Por eso esto es un modulo y no un comentario: cada entrada aparece en
`publish plan`, y las entradas que afectan a un destino **bloquean el modo
real** de ese destino hasta que alguien las verifique. Asi la limitacion viaja
con el producto en vez de quedarse en un README que nadie relee.

Para levantar un bloqueo: comprobar el parametro contra su fuente, ajustar el
adaptador si difiere, y marcar la entrada como verificada indicando la fecha y
que se leyo. No se levanta "porque parece correcto".
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PendingCheck:
    """Un parametro de protocolo que no se pudo contrastar con su fuente."""

    check_id: str
    #: Destino afectado: "youtube", "instagram", "staging", "tiktok" o "*".
    target: str
    source_tag: str
    source_url: str
    what: str
    #: True si el destino no puede operar en modo real hasta verificarlo.
    blocks_real: bool

    def describe(self) -> dict:
        return {
            "check_id": self.check_id,
            "target": self.target,
            "source": self.source_tag,
            "source_url": self.source_url,
            "what": self.what,
            "blocks_real_dispatch": self.blocks_real,
        }


#: Motivo por el que todo esto quedo pendiente. Se repite en cada informe.
UNREACHABLE_REASON = (
    "El proxy de egreso del entorno de desarrollo respondio 403 a los hosts de "
    "documentacion (developers.google.com, developers.facebook.com, "
    "www.postman.com, developers.tiktok.com, docs.aws.amazon.com). Ninguna "
    "referencia se pudo leer, asi que ningun parametro de protocolo esta "
    "contrastado con su fuente."
)

PENDING_CHECKS: tuple[PendingCheck, ...] = (
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
        blocks_real=True,
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
        blocks_real=True,
    ),
    PendingCheck(
        check_id="yt_quota_units",
        target="youtube",
        source_tag="S1",
        source_url="https://developers.google.com/youtube/v3/docs/videos/insert",
        what=(
            "Unidades de cuota por operacion y bucket. El presupuesto local de "
            "este modulo NO es la cuota de Google y no se debe presentar como tal."
        ),
        blocks_real=False,
    ),
    PendingCheck(
        check_id="yt_text_limits",
        target="youtube",
        source_tag="S2",
        source_url="https://developers.google.com/youtube/v3/docs/videos",
        what=(
            "Longitudes maximas de titulo, descripcion y etiquetas. Los topes "
            "de LOCAL_TEXT_LIMITS son decisiones del producto, NO limites "
            "verificados: superarlos marca revision y el texto no se recorta. "
            "Por eso esto no bloquea: un payload excesivo produce un rechazo "
            "limpio y clasificado, no una publicacion equivocada."
        ),
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
        what=(
            "Version de Graph soportada y vigente. El operador debe fijarla "
            "explicitamente; este modulo no elige una por su cuenta ni usa `latest`."
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
        what=(
            "Campos exactos del contenedor de Reel (`media_type`, `video_url`, "
            "`caption`, `share_to_feed`) y su host."
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
        what=(
            "Valores de `status_code` del contenedor y respuesta de `media_publish`. "
            "Se tratan IN_PROGRESS/FINISHED/ERROR/EXPIRED/PUBLISHED, y cualquier "
            "otro valor se considera desconocido y va a reconciliacion."
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
        what=(
            "Lista de permisos vigente y requisitos de revision de la app "
            "(pages_show_list, pages_read_engagement, instagram_basic, "
            "instagram_content_publish)."
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
        what=(
            "Longitud maxima del `caption` y numero de hashtags admitidos. "
            "Mismo criterio que en YouTube: topes locales conservadores, sin "
            "recortes silenciosos, y un rechazo clasificado si la plataforma "
            "los reduce."
        ),
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
        blocks_real=True,
    ),
    # --- TikTok ----------------------------------------------------------
    PendingCheck(
        check_id="tiktok_guidelines",
        target="tiktok",
        source_tag="S8",
        source_url="https://developers.tiktok.com/docs/en/content-sharing-guidelines",
        what=(
            "Directrices de Direct Post. La decision de esta entrega —exportacion "
            "manual, sin automatizacion remota— es la CONSERVADORA, asi que no "
            "bloquea: no se automatiza nada que hubiera que revisar."
        ),
        blocks_real=False,
    ),
)


def pending_for(target: str) -> list[PendingCheck]:
    """Pendientes que afectan a un destino, incluidas las universales."""
    return [
        pendiente
        for pendiente in PENDING_CHECKS
        if pendiente.target in (target, "*")
    ]


def blocking_for(target: str) -> list[PendingCheck]:
    """Las que impiden operar ese destino en modo real."""
    return [pendiente for pendiente in pending_for(target) if pendiente.blocks_real]


def describe_all() -> dict:
    return {
        "reason": UNREACHABLE_REASON,
        "checks": [pendiente.describe() for pendiente in PENDING_CHECKS],
        "blocked_targets": sorted(
            {pendiente.target for pendiente in PENDING_CHECKS if pendiente.blocks_real}
        ),
        "note": (
            "Estas entradas describen verificacion PENDIENTE, no defectos "
            "conocidos. El codigo y sus pruebas de transporte estan completos; lo "
            "que falta es contrastar los parametros con la fuente antes de "
            "habilitar el modo real."
        ),
    }
