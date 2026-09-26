"""Adaptador real de YouTube Data API v3 con subida reanudable.

Recorrido:

1. `channels.list(mine=true)` para comprobar que el canal autorizado es el
   destino autorizado. Si hay varios, se exige eleccion explicita: no se
   escoge el primero.
2. `videos.insert` iniciando una **sesion reanudable**. La URI de sesion es
   material sensible y se guarda en el almacen privado ANTES de enviar ningun
   byte.
3. Subida por bloques de 8 MiB con `Content-Range`. Un **308** significa
   "subida incompleta", no una redireccion: se lee el `Range` confirmado por
   el servidor y se continua desde ahi.
4. Un fallo de conexion despues de enviar bytes NO se interpreta como fallo:
   se consulta la sesion. Si la sesion ya no existe y el resultado es
   incierto, el destino va a reconciliacion y no se crea un segundo video.
5. `videos.list` para verificar canal, procesamiento y privacidad. Que el
   binario se acepte no basta para decir "entregado".

Lo que este MVP NO hace: no envia `publishAt` (la programacion es local, y asi
cancelar antes de empezar significa lo mismo en todos los destinos), no toca
monetizacion y no promete que `#Shorts` clasifique ni monetice nada.

VERIFICACION PENDIENTE: ni los nombres de campo de `videos.insert` ni el
detalle del protocolo reanudable se han podido contrastar con la documentacion
en esta entrega. `yt_insert_part_and_fields` y `yt_resumable_protocol`
bloquean el modo real hasta comprobarlos.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from ...schemas.common import Platform
from ..errors import DisclosureNotTransmittableError, ReconciliationRequiredError
from ..schemas import (
    AudienceDecision,
    DestinationState,
    ErrorClass,
    EvidenceRecord,
    EvidenceSource,
    StructuredError,
    SyntheticDisclosure,
    TransferPhase,
    Visibility,
)
from ..secrets import SecretStore
from ..transport import PublishHttpClient, PublishTransportError
from .base import AccountCheck, DispatchContext, PublisherAdapter, StepResult
from .google_oauth import SCOPES, ensure_token

#: Decision inicial del bloque de subida. Multiplo de 256 KiB.
CHUNK_UNIT = 256 * 1024

#: Nombre OFICIAL de la propiedad de `status` con la que YouTube recoge la
#: divulgacion de contenido sintetico realista.
#:
#: Ya no se lee de configuracion: el revisor consulto la documentacion oficial
#: y confirmo que `status.containsSyntheticMedia` existe, es booleano y lo
#: admite `videos.insert`. Esa evidencia consta en
#: `verification.yt_synthetic_media_property`, con quien la aporto: este modulo
#: no pudo abrir la URL y no dice lo contrario.
SYNTHETIC_MEDIA_PROPERTY = "containsSyntheticMedia"

#: Mapeo TIPADO de la decision editorial al valor que se transmite.
#:
#: `NOT_REVIEWED` no aparece a proposito: una decision sin tomar no se manda
#: como `false`, porque eso seria declarar algo que nadie ha declarado. Y el
#: valor `false` SI se transmite: se busca con `.get()` y se compara con `None`,
#: nunca por veracidad, que es justo el error que omitiria los `false`.
SYNTHETIC_MEDIA_VALUE: dict[SyntheticDisclosure, bool] = {
    SyntheticDisclosure.CONTAINS_REALISTIC_SYNTHETIC_MEDIA: True,
    SyntheticDisclosure.NO_REALISTIC_SYNTHETIC_MEDIA: False,
}

# Lo que la divulgacion NO es, escrito donde se decide:
#
# * No es `simulation`: eso dice de donde salio el paquete. Un video real de
#   produccion puede llevar medios sinteticos realistas.
# * No es "se uso IA": un guion escrito con ayuda de un modelo, o una voz
#   sintetizada, no implican por si mismos contenido REALISTA que se pueda
#   confundir con algo grabado.
#
# La decision la toma una persona en el plan; aqui solo se transmite.

#: Motivo legible cuando la divulgacion no se puede transmitir.
_MOTIVO_DIVULGACION = {
    SyntheticDisclosure.NOT_REVIEWED: (
        "la divulgacion de contenido sintetico realista no esta resuelta en el "
        "plan. Es una decision editorial de una persona: no se deduce de "
        "`simulation` ni de haber usado IA, y no se envia como `false` por "
        "omision. Resuelvela y vuelve a autorizar."
    ),
}

#: Mapa de privacidad del contrato a lo que espera la API.
PRIVACY = {
    Visibility.PUBLIC: "public",
    Visibility.PRIVATE: "private",
    Visibility.UNLISTED: "unlisted",
}
PRIVACY_INVERSA = {valor: clave for clave, valor in PRIVACY.items()}


@dataclass
class UploadSession:
    """Sesion reanudable persistida. La URI vive en el almacen privado."""

    uri: str
    video_size: int
    video_sha256: str
    created_at: str
    offset: int = 0

    def to_payload(self) -> dict[str, Any]:
        return {
            "uri": self.uri,
            "video_size": self.video_size,
            "video_sha256": self.video_sha256,
            "created_at": self.created_at,
            "offset": self.offset,
        }


def _iso(momento: datetime) -> str:
    return momento.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rango_confirmado(cabeceras: dict[str, str]) -> int:
    """Offset confirmado por el servidor a partir de `Range: bytes=0-N`.

    Sin cabecera `Range` el servidor no ha confirmado ningun byte: se vuelve a
    empezar desde cero en vez de suponer que llego lo que enviamos.
    """
    rango = cabeceras.get("range")
    if not rango or "-" not in rango:
        return 0
    try:
        ultimo = int(rango.split("-")[-1])
    except ValueError:
        return 0
    return ultimo + 1


class YouTubeAdapter(PublisherAdapter):
    platform = Platform.YOUTUBE_SHORTS
    name = "youtube_data_v3"
    remote = True

    def __init__(
        self,
        *,
        settings: Any,
        client: PublishHttpClient | None = None,
        store: SecretStore | None = None,
        sleep=time.sleep,
    ) -> None:
        self.settings = settings
        self.store = store
        self.sleep = sleep
        self.api_base = str(settings.youtube_api_base_url).rstrip("/")
        self.upload_base = str(settings.youtube_upload_base_url).rstrip("/")
        self.chunk_bytes = int(settings.youtube_chunk_mib) * 1024 * 1024
        if self.chunk_bytes % CHUNK_UNIT:
            raise ValueError(
                "el bloque de subida debe ser multiplo de 256 KiB para que el "
                "servidor pueda confirmar el rango"
            )
        self.client = client or PublishHttpClient(
            timeout_s=float(settings.publish_http_timeout_s),
            allowed_hosts=self.allowed_hosts(settings),
        )

    @staticmethod
    def allowed_hosts(settings: Any) -> frozenset[str]:
        """Solo los hosts configurados. Nada de seguir a donde diga un tercero."""
        urls = [
            settings.youtube_api_base_url,
            settings.youtube_upload_base_url,
            settings.youtube_oauth_token_url,
            settings.youtube_oauth_auth_url,
        ]
        return frozenset(
            (urlparse(str(url)).hostname or "").lower() for url in urls if url
        )

    # -- Utilidades --------------------------------------------------------

    def _token_headers(self, ctx: DispatchContext) -> dict[str, str]:
        if ctx.secrets is None:
            raise ValueError("el modo real necesita el almacen privado de secretos")
        token = ensure_token(
            ctx.secrets, self.client, settings=self.settings, now=ctx.now
        )
        return token.authorization_header()

    def _session_name(self, ctx: DispatchContext) -> str:
        return ctx.session_name("youtube_upload")

    def _load_session(self, ctx: DispatchContext) -> UploadSession | None:
        if ctx.secrets is None:
            return None
        datos = ctx.secrets.get(self._session_name(ctx))
        if not datos:
            return None
        sesion = UploadSession(**datos)
        if (
            sesion.video_sha256 != ctx.video_sha256
            or sesion.video_size != ctx.video_size
        ):
            # La sesion es de otro archivo: no se reutiliza jamas.
            ctx.secrets.delete(self._session_name(ctx))
            return None
        return sesion

    def _save_session(self, ctx: DispatchContext, sesion: UploadSession) -> None:
        assert ctx.secrets is not None
        ctx.secrets.put(self._session_name(ctx), sesion.to_payload())

    # -- Cuenta ------------------------------------------------------------

    def check_account(self, ctx: DispatchContext) -> AccountCheck:
        """Comprueba que el canal autorizado es el canal de destino.

        Ojo con una confusion habitual: la auditoria del PROYECTO de API no es
        lo mismo que `auditDetails` de un canal. Aqui solo se comprueba
        identidad; la restriccion por falta de auditoria del proyecto es otra
        cosa y se trata como capacidad pendiente.
        """
        ctx.spend(1, 0)
        try:
            respuesta = self.client.get_with_backoff(
                f"{self.api_base}/youtube/v3/channels",
                headers=self._token_headers(ctx),
                params={"part": "id,snippet", "mine": "true"},
            )
        except PublishTransportError as exc:
            return AccountCheck(
                ok=False,
                detail=exc.message,
                error=StructuredError(
                    code="channels_list_failed",
                    error_class=exc.error_class,
                    message=exc.message[:200],
                    retryable=exc.retryable,
                    http_status=exc.http_status,
                    occurred_at=ctx.now,
                ),
            )
        datos = respuesta.json()
        candidatos = [
            str(item.get("id"))
            for item in datos.get("items", [])
            if item.get("id")
        ]
        if not candidatos:
            return AccountCheck(
                ok=False,
                detail="la credencial no da acceso a ningun canal",
            )
        if not ctx.expected_account_id:
            return AccountCheck(
                ok=False,
                candidates=candidatos,
                detail=(
                    "el destino no declara el ID de canal esperado. Elige uno "
                    "explicitamente en el catalogo de cuentas: "
                    + ", ".join(candidatos)
                ),
            )
        if ctx.expected_account_id not in candidatos:
            return AccountCheck(
                ok=False,
                candidates=candidatos,
                detail=(
                    f"la credencial da acceso a {candidatos} y el destino "
                    f"autorizado es {ctx.expected_account_id}"
                ),
            )
        if len(candidatos) > 1:
            # Coincide, pero se deja constancia de que habia mas de uno.
            return AccountCheck(
                ok=True,
                observed_account_id=ctx.expected_account_id,
                candidates=candidatos,
                detail="el canal autorizado coincide; la credencial ve varios canales",
            )
        return AccountCheck(
            ok=True,
            observed_account_id=candidatos[0],
            candidates=candidatos,
            detail="el canal autorizado coincide con el destino",
        )

    # -- Metadatos ---------------------------------------------------------

    def build_body(self, ctx: DispatchContext) -> dict[str, Any]:
        """Solo metadatos aprobados. Nada que el operador no haya visto."""
        if ctx.metadata.audience is AudienceDecision.UNDECIDED:
            raise DisclosureNotTransmittableError(
                "la audiencia infantil no esta decidida: `selfDeclaredMadeForKids` "
                "no se puede rellenar por nadie mas, asi que no se envia nada",
                details={"audience": ctx.metadata.audience.value},
            )
        snippet: dict[str, Any] = {
            "title": ctx.metadata.title,
            "description": ctx.metadata.description or "",
        }
        if ctx.metadata.tags:
            snippet["tags"] = list(ctx.metadata.tags)
        if ctx.metadata.language:
            snippet["defaultLanguage"] = ctx.metadata.language
        estado: dict[str, Any] = {
            "privacyStatus": PRIVACY[ctx.requested_visibility],
            "selfDeclaredMadeForKids": (
                ctx.metadata.audience is AudienceDecision.MADE_FOR_KIDS
            ),
        }
        # `.get()` + comparacion con None: un `false` aprobado se transmite
        # igual que un `true`. Comprobar veracidad omitiria el `false`, que es
        # una declaracion tan explicita como la otra.
        sintetico = SYNTHETIC_MEDIA_VALUE.get(ctx.metadata.synthetic_disclosure)
        if sintetico is None:
            raise DisclosureNotTransmittableError(
                _MOTIVO_DIVULGACION[ctx.metadata.synthetic_disclosure]
                if ctx.metadata.synthetic_disclosure in _MOTIVO_DIVULGACION
                else (
                    "no hay forma de transmitir la divulgacion "
                    f"{ctx.metadata.synthetic_disclosure.value!r}: no se envia el "
                    "video en vez de enviarlo sin declararla"
                ),
                details={
                    "synthetic_disclosure": ctx.metadata.synthetic_disclosure.value,
                    "property": SYNTHETIC_MEDIA_PROPERTY,
                },
            )
        estado[SYNTHETIC_MEDIA_PROPERTY] = sintetico
        return {"snippet": snippet, "status": estado}

    # -- Envio -------------------------------------------------------------

    def start(self, ctx: DispatchContext) -> StepResult:
        # Antes de tocar nada: si una decision aprobada no se puede transmitir,
        # el envio se bloquea con su motivo. No se sube "y ya se vera".
        try:
            self.build_body(ctx)
        except DisclosureNotTransmittableError as exc:
            return StepResult(
                state=DestinationState.NEEDS_REVIEW,
                phase=TransferPhase.NOT_STARTED,
                error=StructuredError(
                    code=exc.code,
                    error_class=ErrorClass.LOCAL,
                    message=exc.message[:400],
                    retryable=False,
                    occurred_at=ctx.now,
                ),
                evidence=EvidenceRecord(
                    source=EvidenceSource.LOCAL_PACKAGE,
                    checked_at=ctx.now,
                    summary=(
                        "comprobacion local: los metadatos aprobados no se pueden "
                        "transmitir tal cual, asi que no se envia nada"
                    ),
                ),
            )
        sesion = self._load_session(ctx)
        cabeceras = self._token_headers(ctx)
        if sesion is None:
            sesion = self._open_session(ctx, cabeceras)
        return self._upload(ctx, sesion, cabeceras)

    def _open_session(
        self, ctx: DispatchContext, cabeceras: dict[str, str]
    ) -> UploadSession:
        """Abre la sesion y la guarda ANTES de enviar un solo byte.

        Abrir una sesion no crea ningun video: sin bytes no hay contenido. Por
        eso esta llamada no se trata como mutante y puede repetirse si se
        pierde la respuesta; lo que no se repite nunca es la subida en si.
        """
        cuerpo = self.build_body(ctx)
        ctx.spend(1, 0)
        respuesta = self.client.request(
            "POST",
            f"{self.upload_base}/upload/youtube/v3/videos",
            headers={
                **cabeceras,
                "content-type": "application/json; charset=UTF-8",
                "X-Upload-Content-Length": str(ctx.video_size),
                "X-Upload-Content-Type": "video/mp4",
            },
            params={
                "uploadType": "resumable",
                "part": "snippet,status",
                "notifySubscribers": (
                    "true" if ctx.options.notify_subscribers else "false"
                ),
            },
            json=cuerpo,
            mutating=False,
            # `Location` es la URI de sesion: material sensible que se pide
            # expresamente y va directo al almacen privado, nunca al log.
            capture_headers=("location",),
        )
        uri = respuesta.headers.get("location") or ""
        if not uri:
            raise PublishTransportError(
                "La respuesta de `videos.insert` no trae URI de sesion.",
                error_class=ErrorClass.INVALID_PAYLOAD,
                retryable=False,
                http_status=respuesta.status,
            )
        sesion = UploadSession(
            uri=uri,
            video_size=ctx.video_size,
            video_sha256=ctx.video_sha256,
            created_at=_iso(ctx.now),
        )
        self._save_session(ctx, sesion)
        return sesion

    def _upload(
        self, ctx: DispatchContext, sesion: UploadSession, cabeceras: dict[str, str]
    ) -> StepResult:
        """Sube por bloques, reanudando desde el offset CONFIRMADO."""
        total = ctx.video_size
        enviados = 0
        with ctx.video_path.open("rb") as archivo:
            while sesion.offset < total:
                ctx.heartbeat()
                archivo.seek(sesion.offset)
                bloque = archivo.read(self.chunk_bytes)
                if not bloque:
                    break
                fin = sesion.offset + len(bloque) - 1
                ctx.spend(1, len(bloque))
                try:
                    respuesta = self.client.request(
                        "PUT",
                        sesion.uri,
                        headers={
                            **cabeceras,
                            "content-type": "video/mp4",
                            "Content-Range": f"bytes {sesion.offset}-{fin}/{total}",
                        },
                        content=bloque,
                        mutating=True,
                        expected=(200, 201, 308),
                    )
                except PublishTransportError as exc:
                    if exc.ambiguous or exc.retryable:
                        # Se perdio la respuesta de un bloque: NO se reenvia a
                        # ciegas. Se pregunta al servidor que tiene.
                        return self._query_session(ctx, sesion, cabeceras)
                    if exc.http_status in (404, 410):
                        return self._session_perdida(ctx, exc)
                    raise
                enviados += len(bloque)
                if respuesta.status == 308:
                    confirmado = _rango_confirmado(respuesta.headers)
                    if confirmado <= sesion.offset and confirmado != 0:
                        raise PublishTransportError(
                            "El servidor no confirma avance en la subida.",
                            error_class=ErrorClass.TRANSIENT,
                            retryable=False,
                            http_status=308,
                        )
                    sesion.offset = confirmado or sesion.offset + len(bloque)
                    self._save_session(ctx, sesion)
                    continue
                return self._subida_completa(ctx, respuesta.json(), enviados)
        # El bucle termino sin respuesta final: se pregunta.
        return self._query_session(ctx, sesion, cabeceras)

    def _query_session(
        self, ctx: DispatchContext, sesion: UploadSession, cabeceras: dict[str, str]
    ) -> StepResult:
        """Pregunta al servidor cuanto tiene. Es lo contrario de reintentar."""
        ctx.spend(1, 0)
        try:
            respuesta = self.client.request(
                "PUT",
                sesion.uri,
                headers={
                    **cabeceras,
                    "Content-Range": f"bytes */{ctx.video_size}",
                },
                content=b"",
                mutating=False,
                expected=(200, 201, 308),
            )
        except PublishTransportError as exc:
            if exc.http_status in (404, 410):
                return self._session_perdida(ctx, exc)
            return StepResult(
                state=DestinationState.NEEDS_RECONCILIATION,
                phase=TransferPhase.UPLOADING,
                error=StructuredError(
                    code="session_query_failed",
                    error_class=ErrorClass.AMBIGUOUS,
                    message=(
                        "No se pudo consultar la sesion de subida tras un fallo: "
                        "el resultado es incierto y no se crea otro video."
                    ),
                    retryable=False,
                    http_status=exc.http_status,
                    occurred_at=ctx.now,
                ),
                evidence=EvidenceRecord(
                    source=EvidenceSource.API_QUERY,
                    checked_at=ctx.now,
                    summary="consulta de la sesion de subida sin respuesta util",
                ),
            )
        if respuesta.status in (200, 201):
            return self._subida_completa(ctx, respuesta.json(), 0)
        confirmado = _rango_confirmado(respuesta.headers)
        sesion.offset = confirmado
        self._save_session(ctx, sesion)
        return StepResult(
            state=DestinationState.DISPATCHING,
            phase=TransferPhase.UPLOADING,
            evidence=EvidenceRecord(
                source=EvidenceSource.API_QUERY,
                checked_at=ctx.now,
                summary=(
                    f"la sesion confirma {confirmado} de {ctx.video_size} bytes; "
                    "se reanudara desde ahi"
                ),
                remote_status="308",
            ),
            next_poll_in_s=float(self.settings.publish_poll_interval_s),
            note="subida incompleta",
        )

    def _session_perdida(
        self, ctx: DispatchContext, exc: PublishTransportError
    ) -> StepResult:
        """Sesion caducada o desaparecida: resultado incierto."""
        return StepResult(
            state=DestinationState.NEEDS_RECONCILIATION,
            phase=TransferPhase.UPLOADING,
            error=StructuredError(
                code="upload_session_gone",
                error_class=ErrorClass.AMBIGUOUS,
                message=(
                    "La sesion de subida ya no existe. No se puede saber desde "
                    "aqui si llego a crearse un video, asi que no se crea otro."
                ),
                retryable=False,
                http_status=exc.http_status,
                occurred_at=ctx.now,
            ),
            evidence=EvidenceRecord(
                source=EvidenceSource.API_QUERY,
                checked_at=ctx.now,
                summary="la sesion reanudable devolvio 404/410",
            ),
        )

    def _subida_completa(
        self, ctx: DispatchContext, recurso: dict[str, Any], enviados: int
    ) -> StepResult:
        """Bytes aceptados. Todavia NO es una entrega."""
        video_id = str(recurso.get("id") or "")
        if not video_id:
            return StepResult(
                state=DestinationState.NEEDS_RECONCILIATION,
                phase=TransferPhase.BYTES_ACCEPTED,
                error=StructuredError(
                    code="insert_without_id",
                    error_class=ErrorClass.AMBIGUOUS,
                    message=(
                        "La subida termino pero la respuesta no trae `id`. No se "
                        "adjudica un video por titulo ni por fecha."
                    ),
                    retryable=False,
                    occurred_at=ctx.now,
                ),
                bytes_sent=enviados,
            )
        if ctx.secrets is not None:
            ctx.secrets.delete(self._session_name(ctx))
        return StepResult(
            state=DestinationState.WAITING_REMOTE,
            phase=TransferPhase.BYTES_ACCEPTED,
            remote_id=video_id,
            evidence=EvidenceRecord(
                source=EvidenceSource.API_QUERY,
                checked_at=ctx.now,
                summary=(
                    "la subida se completo y devolvio un identificador; falta "
                    "comprobar procesamiento y privacidad"
                ),
                remote_status=str(
                    (recurso.get("status") or {}).get("uploadStatus", "uploaded")
                ),
            ),
            bytes_sent=enviados,
            next_poll_in_s=float(self.settings.publish_poll_interval_s),
        )

    # -- Verificacion ------------------------------------------------------

    def poll(self, ctx: DispatchContext) -> StepResult:
        """`videos.list` sobre el ID conocido. Nunca busca por titulo y fecha."""
        video_id = ctx.row.get("real_remote_id")
        if not video_id:
            raise ReconciliationRequiredError(
                "No hay identificador de video que consultar. Buscar por titulo "
                "y fecha podria adjudicar el video de otra persona.",
                details={"destination_id": ctx.destination_id},
            )
        ctx.spend(1, 0)
        respuesta = self.client.get_with_backoff(
            f"{self.api_base}/youtube/v3/videos",
            headers=self._token_headers(ctx),
            params={
                "part": "status,snippet,processingDetails",
                "id": str(video_id),
            },
        )
        datos = respuesta.json()
        items = datos.get("items") or []
        if not items:
            return StepResult(
                state=DestinationState.NEEDS_RECONCILIATION,
                phase=TransferPhase.REMOTE_PROCESSING,
                error=StructuredError(
                    code="video_not_visible",
                    error_class=ErrorClass.AMBIGUOUS,
                    message=(
                        f"El video {video_id} no aparece en `videos.list`. Puede "
                        "estar propagandose o haber sido retirado: hace falta "
                        "resolucion manual."
                    ),
                    retryable=False,
                    occurred_at=ctx.now,
                ),
                evidence=EvidenceRecord(
                    source=EvidenceSource.API_QUERY,
                    checked_at=ctx.now,
                    summary="videos.list no devolvio el recurso",
                ),
            )
        return self._interpretar(ctx, items[0])

    def _interpretar(self, ctx: DispatchContext, recurso: dict[str, Any]) -> StepResult:
        estado = recurso.get("status") or {}
        snippet = recurso.get("snippet") or {}
        subida = str(estado.get("uploadStatus", ""))
        privacidad = str(estado.get("privacyStatus", ""))
        canal = str(snippet.get("channelId") or "") or None
        video_id = str(recurso.get("id") or "")
        observada = PRIVACY_INVERSA.get(privacidad)
        evidencia = EvidenceRecord(
            source=EvidenceSource.API_QUERY,
            checked_at=ctx.now,
            summary=(
                f"videos.list: uploadStatus={subida or 'desconocido'}, "
                f"privacyStatus={privacidad or 'desconocido'}"
            ),
            remote_status=subida or None,
        )

        if canal and ctx.expected_account_id and canal != ctx.expected_account_id:
            return StepResult(
                state=DestinationState.NEEDS_REVIEW,
                phase=TransferPhase.VERIFIED,
                remote_id=video_id,
                observed_account_id=canal,
                observed_visibility=observada,
                publicly_visible=False,
                evidence=evidencia,
                error=StructuredError(
                    code="channel_mismatch",
                    error_class=ErrorClass.PERMISSION,
                    message=(
                        f"El video esta en el canal {canal} y el destino "
                        f"autorizado era {ctx.expected_account_id}."
                    ),
                    retryable=False,
                    occurred_at=ctx.now,
                ),
            )

        if subida in ("failed", "rejected"):
            motivo = str(estado.get("failureReason") or estado.get("rejectionReason") or "")
            return StepResult(
                state=DestinationState.FAILED,
                phase=TransferPhase.VERIFIED,
                remote_id=video_id,
                observed_account_id=canal,
                observed_visibility=observada,
                publicly_visible=False,
                evidence=evidencia,
                error=StructuredError(
                    code=f"upload_{subida}",
                    error_class=ErrorClass.INVALID_PAYLOAD,
                    message=f"YouTube rechazo el video ({subida}): {motivo or 'sin motivo'}",
                    retryable=False,
                    occurred_at=ctx.now,
                ),
            )

        if subida and subida not in ("processed", "uploaded"):
            # Valor desconocido: no se interpreta como exito.
            return StepResult(
                state=DestinationState.NEEDS_REVIEW,
                phase=TransferPhase.REMOTE_PROCESSING,
                remote_id=video_id,
                observed_visibility=observada,
                evidence=evidencia,
                error=StructuredError(
                    code="upload_status_desconocido",
                    error_class=ErrorClass.AMBIGUOUS,
                    message=f"uploadStatus desconocido: {subida!r}",
                    retryable=False,
                    occurred_at=ctx.now,
                ),
            )

        if subida != "processed":
            vencido = self._ventana_agotada(ctx)
            return StepResult(
                state=(
                    DestinationState.NEEDS_REVIEW
                    if vencido
                    else DestinationState.WAITING_REMOTE
                ),
                phase=TransferPhase.REMOTE_PROCESSING,
                remote_id=video_id,
                observed_account_id=canal,
                observed_visibility=observada,
                evidence=evidencia,
                next_poll_in_s=self._siguiente_sondeo(ctx),
                error=(
                    StructuredError(
                        code="processing_window_expired",
                        error_class=ErrorClass.AMBIGUOUS,
                        message=(
                            "El video sigue procesandose despues de la ventana "
                            "configurada. No se declara entregado."
                        ),
                        retryable=False,
                        occurred_at=ctx.now,
                    )
                    if vencido
                    else None
                ),
            )

        publico = privacidad == "public"
        if observada is not ctx.requested_visibility:
            return StepResult(
                state=DestinationState.NEEDS_REVIEW,
                phase=TransferPhase.VERIFIED,
                remote_id=video_id,
                observed_account_id=canal,
                observed_visibility=observada,
                publicly_visible=publico,
                evidence=evidencia,
                error=StructuredError(
                    code="visibility_mismatch",
                    error_class=ErrorClass.PERMISSION,
                    message=(
                        f"Se solicito {ctx.requested_visibility.value} y la "
                        f"plataforma mantiene {privacidad or 'desconocido'}. No se "
                        "declara publicado publicamente. Si el proyecto de API "
                        "esta sujeto a la restriccion por falta de auditoria, "
                        "esta es la causa habitual."
                    ),
                    retryable=False,
                    occurred_at=ctx.now,
                ),
            )

        return StepResult(
            state=DestinationState.DELIVERED,
            phase=TransferPhase.VERIFIED,
            remote_id=video_id,
            observed_account_id=canal,
            observed_visibility=observada,
            publicly_visible=publico,
            permalink=f"https://www.youtube.com/watch?v={video_id}" if publico else None,
            evidence=evidencia,
        )

    def _ventana_agotada(self, ctx: DispatchContext) -> bool:
        inicio = ctx.row.get("dispatch_started_at")
        if not inicio:
            return False
        comenzado = datetime.fromisoformat(str(inicio).replace("Z", "+00:00"))
        ventana = timedelta(seconds=int(self.settings.publish_remote_window_s))
        return ctx.now - comenzado > ventana

    def _siguiente_sondeo(self, ctx: DispatchContext) -> float:
        base = float(self.settings.publish_poll_interval_s)
        tope = float(self.settings.publish_poll_max_interval_s)
        intentos = int(ctx.row.get("attempts") or 0)
        return min(base * (2 ** min(intentos, 5)), tope)

    def capabilities(self) -> dict[str, Any]:
        return {
            **super().capabilities(),
            "upload": "resumable",
            "chunk_bytes": self.chunk_bytes,
            "scopes": list(SCOPES),
            "schedules_remotely": False,
            "publish_at_supported_by_this_mvp": False,
            "verifies_result": "videos.list",
            "note": (
                "La programacion es local: el envio empieza a la hora autorizada. "
                "No se envia publishAt en este MVP."
            ),
        }
