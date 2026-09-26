"""Adaptador real de Instagram Reels con Facebook Login.

Recorrido, en el orden en que ocurre:

1. Se comprueba la cuenta: Paginas accesibles, cuenta profesional vinculada a
   la Pagina y permisos concedidos. El IG User ID observado debe ser el
   autorizado.
2. Se sube el MP4 autorizado al almacenamiento temporal y se firma una URL GET
   de corta duracion, porque este flujo publica por `video_url`: la plataforma
   se descarga el archivo.
3. Se crea el contenedor del Reel (`media_type=REELS`) y se guarda su id.
4. Se sondea el contenedor hasta `FINISHED`. FINISHED significa "listo para
   publicar", NO "publicado".
5. Se pide `media_publish` con ese `creation_id` y se guarda el id de medio.
6. Se consulta el medio para tener evidencia de la publicacion.

Decisiones de alcance:

* Solo la variante con **Facebook Login**. No se mezclan tokens, scopes ni
  hosts con los de Instagram Login: son cosas distintas.
* **Sin subida binaria reanudable.** No se ha verificado aqui su
  compatibilidad con cada modalidad de login, asi que no se implementa a
  medias: el alcance elegido es `video_url`.
* `META_GRAPH_API_VERSION` es obligatoria y explicita en modo real. Nada de
  `latest` ni de saltar de version sola.

VERIFICACION PENDIENTE (`ig_graph_version`, `ig_container_fields`,
`ig_status_values`, `ig_permissions`): ni los campos, ni los valores de estado,
ni la lista de permisos se han podido contrastar con la documentacion en esta
entrega. Los cuatro bloquean el modo real.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ...errors import ConfigError
from ...schemas.common import Platform
from ..schemas import (
    DestinationState,
    ErrorClass,
    EvidenceRecord,
    EvidenceSource,
    StagingRef,
    StructuredError,
    TransferPhase,
    Visibility,
    compose_caption,
)
from ..staging import S3Staging, SignedUrl
from ..transport import PublishHttpClient, PublishTransportError
from .base import AccountCheck, DispatchContext, PublisherAdapter, StepResult

#: Permisos PREVISTOS para identificar la cuenta y publicar. Ni mensajes ni
#: comentarios: no se piden permisos que este modulo no usa.
PERMISSIONS = (
    "pages_show_list",
    "pages_read_engagement",
    "instagram_basic",
    "instagram_content_publish",
)

#: Archivo privado con el token importado del flujo oficial de Meta.
TOKEN_FILE = "instagram_token.json"

#: Estados de contenedor que este adaptador reconoce. Cualquier otro valor se
#: trata como desconocido y va a revision: no se interpreta como exito.
EN_CURSO = "IN_PROGRESS"
LISTO = "FINISHED"
ERROR = "ERROR"
CADUCADO = "EXPIRED"
PUBLICADO = "PUBLISHED"
CONOCIDOS = frozenset({EN_CURSO, LISTO, ERROR, CADUCADO, PUBLICADO})


def _iso(momento: datetime) -> str:
    return momento.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class InstagramReelsAdapter(PublisherAdapter):
    platform = Platform.INSTAGRAM_REELS
    name = "instagram_graph_facebook_login"
    remote = True

    def __init__(
        self,
        *,
        settings: Any,
        client: PublishHttpClient | None = None,
        staging: S3Staging | None = None,
        sleep=time.sleep,
    ) -> None:
        version = getattr(settings, "meta_graph_api_version", None)
        if not version:
            raise ConfigError(
                "Falta META_GRAPH_API_VERSION. Es obligatoria y explicita: este "
                "adaptador no usa `latest` ni cambia de version por su cuenta, "
                "porque una version distinta cambia campos y comportamiento.",
                details={"missing": ["META_GRAPH_API_VERSION"]},
            )
        self.settings = settings
        self.version = str(version)
        self.base = f"{str(settings.meta_graph_base_url).rstrip('/')}/{self.version}"
        self.staging = staging
        self.sleep = sleep
        self.client = client or PublishHttpClient(
            timeout_s=float(settings.publish_http_timeout_s),
            allowed_hosts=self.allowed_hosts(settings),
        )

    @staticmethod
    def allowed_hosts(settings: Any) -> frozenset[str]:
        host = (urlparse(str(settings.meta_graph_base_url)).hostname or "").lower()
        return frozenset({host})

    # -- Credenciales ------------------------------------------------------

    def _token(self, ctx: DispatchContext) -> str:
        """Token importado del flujo oficial de Meta, desde archivo privado.

        Nunca se pide una contrasena de Facebook o de Instagram, y no se
        presenta ningun token como permanente: su caducidad se comprueba.
        """
        if ctx.secrets is None:
            raise ConfigError("el modo real necesita el almacen privado de secretos")
        datos = ctx.secrets.get(TOKEN_FILE)
        if not datos or not datos.get("access_token"):
            raise ConfigError(
                "No hay token de Instagram importado. Obtenlo con el flujo "
                "oficial de Meta y guardalo con `viralgen auth instagram "
                "--from-file`; este modulo no pide contrasenas.",
                details={"expected_file": str(ctx.secrets.path_for(TOKEN_FILE))},
            )
        caduca = datos.get("expires_at")
        if caduca:
            momento = datetime.fromisoformat(str(caduca).replace("Z", "+00:00"))
            if momento <= ctx.now:
                from ..errors import AuthRequiredError

                raise AuthRequiredError(
                    f"El token de Instagram caduco el {caduca}. Renuevalo con el "
                    "flujo oficial: un token no es permanente aunque lo parezca.",
                    details={"expires_at": str(caduca)},
                )
        return str(datos["access_token"])

    def _params(self, ctx: DispatchContext, **extra: Any) -> dict[str, Any]:
        """El token viaja como parametro; por eso nunca se registra la query."""
        return {"access_token": self._token(ctx), **extra}

    # -- Cuenta ------------------------------------------------------------

    def check_account(self, ctx: DispatchContext) -> AccountCheck:
        """Pagina, cuenta profesional y permisos. No publica nada."""
        try:
            ctx.spend(1, 0)
            paginas = self.client.get_with_backoff(
                f"{self.base}/me/accounts",
                params=self._params(ctx, fields="id,name,instagram_business_account"),
            ).json()
        except PublishTransportError as exc:
            return AccountCheck(
                ok=False,
                detail=exc.message,
                error=StructuredError(
                    code="me_accounts_failed",
                    error_class=exc.error_class,
                    message=exc.message[:200],
                    retryable=exc.retryable,
                    http_status=exc.http_status,
                    occurred_at=ctx.now,
                ),
            )

        candidatos: list[str] = []
        pagina_elegida: dict | None = None
        for pagina in paginas.get("data", []):
            vinculada = (pagina.get("instagram_business_account") or {}).get("id")
            if vinculada:
                candidatos.append(str(vinculada))
                if str(vinculada) == str(ctx.expected_account_id):
                    pagina_elegida = pagina

        if not candidatos:
            return AccountCheck(
                ok=False,
                detail=(
                    "ninguna Pagina accesible tiene una cuenta de Instagram "
                    "profesional vinculada"
                ),
            )
        if pagina_elegida is None:
            return AccountCheck(
                ok=False,
                candidates=candidatos,
                detail=(
                    f"las cuentas accesibles son {candidatos} y el destino "
                    f"autorizado es {ctx.expected_account_id}"
                ),
            )

        ctx.spend(1, 0)
        permisos = self.client.get_with_backoff(
            f"{self.base}/me/permissions", params=self._params(ctx)
        ).json()
        concedidos = {
            str(entrada.get("permission"))
            for entrada in permisos.get("data", [])
            if str(entrada.get("status")) == "granted"
        }
        faltan = [permiso for permiso in PERMISSIONS if permiso not in concedidos]
        if faltan:
            return AccountCheck(
                ok=False,
                observed_account_id=str(ctx.expected_account_id),
                candidates=candidatos,
                detail="faltan permisos concedidos: " + ", ".join(faltan),
                error=StructuredError(
                    code="missing_permissions",
                    error_class=ErrorClass.PERMISSION,
                    message="faltan permisos: " + ", ".join(faltan),
                    retryable=False,
                    occurred_at=ctx.now,
                ),
            )
        return AccountCheck(
            ok=True,
            observed_account_id=str(ctx.expected_account_id),
            candidates=candidatos,
            detail=(
                f"cuenta {ctx.expected_account_id} vinculada a la Pagina "
                f"{pagina_elegida.get('id')} con los permisos necesarios"
            ),
        )

    # -- Staging -----------------------------------------------------------

    def _staging_name(self, ctx: DispatchContext) -> str:
        return ctx.session_name("instagram_staging")

    def _url_vigente(self, ctx: DispatchContext) -> tuple[SignedUrl, str] | None:
        """URL firmada guardada que todavia sirve, o nada."""
        if ctx.secrets is None:
            return None
        datos = ctx.secrets.get(self._staging_name(ctx))
        if not datos:
            return None
        if datos.get("video_sha256") != ctx.video_sha256:
            return None
        caduca = datetime.fromisoformat(str(datos["expires_at"]).replace("Z", "+00:00"))
        if caduca <= ctx.now + timedelta(minutes=5):
            return None
        firmada = SignedUrl(
            url=str(datos["url"]),
            issued_at=datetime.fromisoformat(
                str(datos["issued_at"]).replace("Z", "+00:00")
            ),
            expires_at=caduca,
        )
        return firmada, str(datos["object_key"])

    def prepare_media(self, ctx: DispatchContext) -> tuple[SignedUrl, StagingRef]:
        """Sube el MP4 autorizado y firma su URL temporal.

        La autorizacion de la publicacion cubre esta subida: no es un permiso
        aparte. Lo que no hace es subir antes de tiempo, porque esto ocurre
        cuando la tarea ya esta en curso.
        """
        if self.staging is None:
            raise ConfigError(
                "Instagram necesita almacenamiento temporal configurado: la "
                "plataforma descarga el video por URL."
            )
        vigente = self._url_vigente(ctx)
        clave = self.staging.object_key(
            publication_id=ctx.publication_id,
            destination_id=ctx.destination_id,
            video_sha256=ctx.video_sha256,
        )
        if vigente is not None:
            firmada, clave_guardada = vigente
            cabecera = self.staging.head(clave_guardada) or {}
            referencia = StagingRef(
                bucket_alias=self.staging.config.bucket_alias,
                object_key=clave_guardada,
                object_sha256=ctx.video_sha256,
                etag=cabecera.get("ETag"),
                size_bytes=ctx.video_size,
                uploaded_at=firmada.issued_at,
                url_issued_at=firmada.issued_at,
                url_expires_at=firmada.expires_at,
                url_sha256=firmada.fingerprint,
                retain_until=firmada.issued_at
                + timedelta(seconds=self.staging.config.retention_s),
            )
            return firmada, referencia

        objeto = self.staging.upload(
            Path(ctx.video_path),
            key=clave,
            sha256=ctx.video_sha256,
            now=ctx.now,
            progress=lambda _bytes: ctx.heartbeat(),
        )
        ctx.spend(1, 0 if objeto.reused else objeto.size_bytes)
        firmada = self.staging.presign(clave, now=ctx.now)
        duran = self.staging.credentials_outlive(self.staging.config.url_ttl_s)
        if duran is False:
            raise ConfigError(
                "Las credenciales que firman la URL caducan antes que la propia "
                "URL: la plataforma se quedaria sin poder descargar el video. "
                "Usa credenciales de mas duracion o baja el TTL."
            )
        # La URL es un secreto operativo: al almacen privado, nunca a la base
        # de datos ni al recibo.
        assert ctx.secrets is not None
        ctx.secrets.put(
            self._staging_name(ctx),
            {
                "url": firmada.url,
                "issued_at": _iso(firmada.issued_at),
                "expires_at": _iso(firmada.expires_at),
                "object_key": clave,
                "video_sha256": ctx.video_sha256,
            },
        )
        referencia = StagingRef(
            bucket_alias=self.staging.config.bucket_alias,
            object_key=clave,
            object_sha256=ctx.video_sha256,
            etag=objeto.etag,
            size_bytes=objeto.size_bytes,
            uploaded_at=objeto.uploaded_at,
            url_issued_at=firmada.issued_at,
            url_expires_at=firmada.expires_at,
            url_sha256=firmada.fingerprint,
            retain_until=ctx.now + timedelta(seconds=self.staging.config.retention_s),
        )
        return firmada, referencia

    # -- Envio -------------------------------------------------------------

    def start(self, ctx: DispatchContext) -> StepResult:
        """Sube al staging y crea el contenedor del Reel."""
        if ctx.requested_visibility is not Visibility.PUBLIC:
            return StepResult(
                state=DestinationState.NEEDS_REVIEW,
                phase=TransferPhase.NOT_STARTED,
                error=StructuredError(
                    code="visibility_not_supported",
                    error_class=ErrorClass.INVALID_PAYLOAD,
                    message=(
                        "Un Reel publicado es visible para la audiencia de la "
                        f"cuenta; no hay equivalente a {ctx.requested_visibility.value}. "
                        "Cambia la visibilidad solicitada o quita este destino."
                    ),
                    retryable=False,
                    occurred_at=ctx.now,
                ),
            )

        existente = ctx.remote_refs.get("container_id")
        if existente:
            # Ya hay contenedor: no se crea otro. Se sigue desde donde estaba.
            return self.poll(ctx)

        firmada, referencia = self.prepare_media(ctx)
        cuerpo = {
            "media_type": "REELS",
            "video_url": firmada.url,
            "caption": compose_caption(ctx.metadata),
            "share_to_feed": "true" if ctx.options.share_to_feed else "false",
        }
        ctx.spend(1, 0)
        try:
            respuesta = self.client.request(
                "POST",
                f"{self.base}/{ctx.expected_account_id}/media",
                params=self._params(ctx),
                json=cuerpo,
                mutating=True,
            )
        except PublishTransportError as exc:
            if exc.ambiguous:
                # Un contenedor NO es una publicacion: sin `media_publish` no
                # hay nada visible, y el contenedor caduca solo. Por eso esto
                # no bloquea el destino, pero queda registrado como ambiguo.
                #
                # La excepcion tiene limite: el trabajador cuenta los intentos
                # de operacion y, agotados, manda el destino a revision. Y como
                # aqui no se guarda ningun `container_id`, un contenedor cuya
                # identidad no llego NO se publica nunca: `media_publish` solo
                # se pide con un identificador recibido y guardado.
                return StepResult(
                    state=DestinationState.DISPATCHING,
                    phase=TransferPhase.UPLOADING,
                    staging=referencia,
                    error=StructuredError(
                        code="container_create_lost",
                        error_class=ErrorClass.AMBIGUOUS,
                        message=(
                            "Se perdio la respuesta al crear el contenedor. No "
                            "se ha publicado nada: un contenedor sin "
                            "media_publish no es visible y caduca solo."
                        ),
                        retryable=False,
                        occurred_at=ctx.now,
                    ),
                    next_poll_in_s=float(self.settings.publish_poll_interval_s),
                )
            raise
        contenedor = str(respuesta.json().get("id") or "")
        if not contenedor:
            return StepResult(
                state=DestinationState.NEEDS_REVIEW,
                phase=TransferPhase.UPLOADING,
                staging=referencia,
                error=StructuredError(
                    code="container_without_id",
                    error_class=ErrorClass.AMBIGUOUS,
                    message="La creacion del contenedor no devolvio `id`.",
                    retryable=False,
                    occurred_at=ctx.now,
                ),
            )
        return StepResult(
            state=DestinationState.WAITING_REMOTE,
            phase=TransferPhase.REMOTE_PROCESSING,
            remote_refs={"container_id": contenedor},
            staging=referencia,
            evidence=EvidenceRecord(
                source=EvidenceSource.API_QUERY,
                checked_at=ctx.now,
                summary=(
                    "contenedor de Reel creado; falta que termine de procesarse "
                    "y despues publicarlo"
                ),
                remote_status="created",
            ),
            next_poll_in_s=float(self.settings.publish_poll_interval_s),
        )

    # -- Sondeo y publicacion ---------------------------------------------

    def poll(self, ctx: DispatchContext) -> StepResult:
        medio = ctx.row.get("real_remote_id")
        if medio:
            return self._verificar_medio(ctx, str(medio))

        contenedor = ctx.remote_refs.get("container_id")
        if not contenedor:
            return StepResult(
                state=DestinationState.NEEDS_REVIEW,
                phase=TransferPhase.NOT_STARTED,
                error=StructuredError(
                    code="sin_contenedor",
                    error_class=ErrorClass.LOCAL,
                    message="No hay contenedor que consultar.",
                    retryable=False,
                    occurred_at=ctx.now,
                ),
            )

        estado, datos = self._estado_contenedor(ctx, contenedor)
        evidencia = EvidenceRecord(
            source=EvidenceSource.API_QUERY,
            checked_at=ctx.now,
            summary=f"estado del contenedor: {estado or 'desconocido'}",
            remote_status=estado,
        )

        if estado == LISTO:
            return self._publicar(ctx, contenedor, evidencia)
        if estado == PUBLICADO:
            # Ya publicado, posiblemente por una llamada cuya respuesta se
            # perdio. Si no se puede recuperar el id, hay que reconciliar: no
            # se crea otro contenedor para "repetir" la publicacion.
            return self._publicado_sin_respuesta(ctx, contenedor, evidencia, datos)
        if estado == EN_CURSO:
            vencido = self._ventana_agotada(ctx)
            return StepResult(
                state=(
                    DestinationState.NEEDS_REVIEW
                    if vencido
                    else DestinationState.WAITING_REMOTE
                ),
                phase=TransferPhase.REMOTE_PROCESSING,
                remote_refs={"container_id": contenedor},
                evidence=evidencia,
                next_poll_in_s=self._siguiente_sondeo(ctx),
                error=(
                    StructuredError(
                        code="container_window_expired",
                        error_class=ErrorClass.AMBIGUOUS,
                        message=(
                            "El contenedor sigue procesandose despues de la "
                            "ventana configurada."
                        ),
                        retryable=False,
                        occurred_at=ctx.now,
                    )
                    if vencido
                    else None
                ),
            )
        if estado in (ERROR, CADUCADO):
            return StepResult(
                state=DestinationState.FAILED,
                phase=TransferPhase.REMOTE_PROCESSING,
                remote_refs={"container_id": contenedor},
                evidence=evidencia,
                error=StructuredError(
                    code=f"container_{estado.lower()}",
                    error_class=(
                        ErrorClass.INVALID_PAYLOAD if estado == ERROR else ErrorClass.TRANSIENT
                    ),
                    message=(
                        f"El contenedor termino en {estado}: "
                        + str(datos.get("status") or "sin detalle")[:200]
                    ),
                    retryable=False,
                    occurred_at=ctx.now,
                ),
            )
        return StepResult(
            state=DestinationState.NEEDS_RECONCILIATION,
            phase=TransferPhase.REMOTE_PROCESSING,
            remote_refs={"container_id": contenedor},
            evidence=evidencia,
            error=StructuredError(
                code="container_status_desconocido",
                error_class=ErrorClass.AMBIGUOUS,
                message=(
                    f"Estado de contenedor desconocido: {estado!r}. No se "
                    "interpreta como exito ni como fallo."
                ),
                retryable=False,
                occurred_at=ctx.now,
            ),
        )

    def _estado_contenedor(
        self, ctx: DispatchContext, contenedor: str
    ) -> tuple[str | None, dict]:
        ctx.spend(1, 0)
        datos = self.client.get_with_backoff(
            f"{self.base}/{contenedor}",
            params=self._params(ctx, fields="status_code,status,id"),
        ).json()
        codigo = datos.get("status_code")
        return (str(codigo) if codigo else None), datos

    def _publicar(
        self, ctx: DispatchContext, contenedor: str, evidencia: EvidenceRecord
    ) -> StepResult:
        """`media_publish` con el `creation_id`. Es la operacion que publica."""
        ctx.spend(1, 0)
        try:
            respuesta = self.client.request(
                "POST",
                f"{self.base}/{ctx.expected_account_id}/media_publish",
                params=self._params(ctx, creation_id=contenedor),
                mutating=True,
            )
        except PublishTransportError as exc:
            if exc.ambiguous:
                # Se perdio la respuesta de la publicacion: se consulta el
                # contenedor existente. Nunca se repite el POST a ciegas.
                estado, datos = self._estado_contenedor(ctx, contenedor)
                if estado == PUBLICADO:
                    return self._publicado_sin_respuesta(
                        ctx, contenedor, evidencia, datos
                    )
                return StepResult(
                    state=DestinationState.NEEDS_RECONCILIATION,
                    phase=TransferPhase.PUBLISH_REQUESTED,
                    remote_refs={"container_id": contenedor},
                    evidence=EvidenceRecord(
                        source=EvidenceSource.API_QUERY,
                        checked_at=ctx.now,
                        summary=(
                            "se perdio la respuesta de media_publish; el "
                            f"contenedor dice {estado or 'desconocido'}"
                        ),
                        remote_status=estado,
                    ),
                    error=StructuredError(
                        code="media_publish_lost",
                        error_class=ErrorClass.AMBIGUOUS,
                        message=(
                            "No se sabe si la publicacion se completo. No se "
                            "repite la peticion ni se crea otro contenedor."
                        ),
                        retryable=False,
                        occurred_at=ctx.now,
                    ),
                )
            raise
        medio = str(respuesta.json().get("id") or "")
        if not medio:
            return StepResult(
                state=DestinationState.NEEDS_RECONCILIATION,
                phase=TransferPhase.PUBLISH_REQUESTED,
                remote_refs={"container_id": contenedor},
                evidence=evidencia,
                error=StructuredError(
                    code="publish_without_id",
                    error_class=ErrorClass.AMBIGUOUS,
                    message="`media_publish` no devolvio id de medio.",
                    retryable=False,
                    occurred_at=ctx.now,
                ),
            )
        return self._verificar_medio(ctx, medio, contenedor=contenedor)

    def _publicado_sin_respuesta(
        self,
        ctx: DispatchContext,
        contenedor: str,
        evidencia: EvidenceRecord,
        datos: dict,
    ) -> StepResult:
        """PUBLISHED sin id recuperable: reconciliacion, no una segunda vuelta."""
        posible = datos.get("id")
        if posible and str(posible) != contenedor:
            return self._verificar_medio(ctx, str(posible), contenedor=contenedor)
        return StepResult(
            state=DestinationState.NEEDS_RECONCILIATION,
            phase=TransferPhase.PUBLISH_REQUESTED,
            remote_refs={"container_id": contenedor},
            evidence=evidencia,
            error=StructuredError(
                code="published_without_media_id",
                error_class=ErrorClass.AMBIGUOUS,
                message=(
                    "El contenedor figura como PUBLISHED pero no se recupera el "
                    "id del medio. Hay que resolverlo mirando la cuenta: no se "
                    "publica otra vez."
                ),
                retryable=False,
                occurred_at=ctx.now,
            ),
        )

    def _verificar_medio(
        self, ctx: DispatchContext, medio: str, *, contenedor: str | None = None
    ) -> StepResult:
        ctx.spend(1, 0)
        try:
            datos = self.client.get_with_backoff(
                f"{self.base}/{medio}",
                params=self._params(ctx, fields="id,permalink,media_type,timestamp"),
            ).json()
        except PublishTransportError as exc:
            return StepResult(
                state=DestinationState.NEEDS_RECONCILIATION,
                phase=TransferPhase.PUBLISH_REQUESTED,
                remote_id=medio,
                remote_refs=({"container_id": contenedor} if contenedor else {}),
                error=StructuredError(
                    code="media_check_failed",
                    error_class=ErrorClass.AMBIGUOUS,
                    message=(
                        f"No se pudo consultar el medio {medio}: {exc.message[:120]}"
                    ),
                    retryable=False,
                    http_status=exc.http_status,
                    occurred_at=ctx.now,
                ),
            )
        enlace = datos.get("permalink")
        return StepResult(
            state=DestinationState.DELIVERED,
            phase=TransferPhase.VERIFIED,
            remote_id=medio,
            remote_refs=({"container_id": contenedor} if contenedor else {}),
            observed_account_id=str(ctx.expected_account_id),
            observed_visibility=Visibility.PUBLIC,
            publicly_visible=True,
            permalink=str(enlace) if enlace else None,
            evidence=EvidenceRecord(
                source=EvidenceSource.API_QUERY,
                checked_at=ctx.now,
                summary=(
                    f"medio {medio} consultado"
                    + (f" con permalink {enlace}" if enlace else " sin permalink")
                ),
                remote_status="published",
            ),
        )

    # -- Utilidades --------------------------------------------------------

    def _ventana_agotada(self, ctx: DispatchContext) -> bool:
        inicio = ctx.row.get("dispatch_started_at")
        if not inicio:
            return False
        comenzado = datetime.fromisoformat(str(inicio).replace("Z", "+00:00"))
        return ctx.now - comenzado > timedelta(
            seconds=int(self.settings.publish_remote_window_s)
        )

    def _siguiente_sondeo(self, ctx: DispatchContext) -> float:
        base = float(self.settings.publish_poll_interval_s)
        tope = float(self.settings.publish_poll_max_interval_s)
        intentos = int(ctx.row.get("attempts") or 0)
        return min(base * (2 ** min(intentos, 5)), tope)

    def capabilities(self) -> dict[str, Any]:
        return {
            **super().capabilities(),
            "login": "facebook_login",
            "graph_version": self.version,
            "upload": "video_url (sin subida binaria reanudable en esta entrega)",
            "requires_staging": True,
            "permissions": list(PERMISSIONS),
            "schedules_remotely": False,
            "verifies_result": "consulta del medio publicado",
        }
