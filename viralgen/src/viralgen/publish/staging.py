"""Almacenamiento temporal para que Instagram pueda descargar el MP4.

El flujo de Reels con `video_url` exige que la plataforma pueda bajarse el
archivo. La alternativa -servir el directorio de trabajos desde la VPS- queda
descartada: expondria guiones, voces y renders de todo lo demas.

Lo que hace este adaptador y lo que no:

* Sube **un solo archivo**: el MP4 autorizado, a un bucket privado, con clave
  derivada de la intencion y del hash. Nunca sobrescribe un objeto existente
  con contenido distinto.
* Emite una **URL firmada GET** de corta duracion. Esa URL es un secreto
  operativo: permite descargar el video sin credenciales, asi que no aparece
  en logs, planes, recibos ni informes. Solo se guarda su huella y su
  caducidad.
* **No elige proveedor** ni presupone ninguno gratuito. Endpoint, bucket,
  prefijo y credenciales los configura el operador.
* No borra nada mientras la plataforma pueda necesitarlo. La limpieza llega
  despues de un resultado terminal confirmado y con margen.

VERIFICACION PENDIENTE (`s3_presign_expiry`): los limites de caducidad de una
URL firmada y su interaccion con la caducidad de las credenciales que la
firman no se han podido contrastar con la documentacion en esta entrega.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from ..errors import ConfigError, ExitCode, ViralgenError
from ..logging_setup import get_logger

logger = get_logger("publish.staging")

#: Bloque de subida multiparte. Secuencial y acotado: un trabajador, un flujo.
MULTIPART_CHUNK_BYTES = 8 * 1024 * 1024

#: Clave de metadatos con NUESTRO hash local. Es una afirmacion propia, no una
#: prueba del contenido del objeto: por eso se guarda aparte del ETag.
SHA_METADATA_KEY = "viralgen-sha256"


class StagingError(ViralgenError):
    exit_code = ExitCode.PROVIDER
    code = "staging_error"


class StagingConflictError(StagingError):
    """Ya hay un objeto distinto en esa clave. No se sobrescribe."""

    code = "staging_object_conflict"


@dataclass(frozen=True)
class StagingConfig:
    endpoint_url: str | None
    region: str | None
    bucket: str
    prefix: str
    access_key_id: str | None
    secret_access_key: str | None
    url_ttl_s: int
    retention_s: int
    max_objects: int
    max_bytes: int

    @property
    def bucket_alias(self) -> str:
        """Nombre legible del destino. No incluye credenciales."""
        return self.bucket


def load_staging_config(settings: Any) -> StagingConfig:
    """Configuracion completa o error explicando que falta."""
    faltan = [
        nombre
        for nombre, valor in (
            ("VIRALGEN_PUBLISH_STAGING_BUCKET", settings.publish_staging_bucket),
            (
                "PUBLISH_STAGING_ACCESS_KEY_ID",
                settings.publish_staging_access_key_id,
            ),
            (
                "PUBLISH_STAGING_SECRET_ACCESS_KEY",
                settings.publish_staging_secret_access_key,
            ),
        )
        if not valor
    ]
    if faltan:
        raise ConfigError(
            "Falta configuracion del almacenamiento temporal: "
            + ", ".join(faltan)
            + ". Instagram necesita que la plataforma pueda descargar el MP4; "
            "no se sirve DATA_DIR desde la VPS.",
            details={"missing": faltan},
        )
    secreto = settings.publish_staging_secret_access_key
    return StagingConfig(
        endpoint_url=settings.publish_staging_endpoint_url,
        region=settings.publish_staging_region,
        bucket=str(settings.publish_staging_bucket),
        prefix=str(settings.publish_staging_prefix).strip("/"),
        access_key_id=str(settings.publish_staging_access_key_id),
        secret_access_key=secreto.get_secret_value() if secreto else None,
        url_ttl_s=int(settings.publish_staging_url_ttl_s),
        retention_s=int(settings.publish_staging_retention_s),
        max_objects=int(settings.publish_staging_max_objects),
        max_bytes=int(settings.publish_staging_max_mib) * 1024 * 1024,
    )


def _boto3():
    try:
        import boto3  # noqa: PLC0415 - dependencia opcional a proposito
    except ModuleNotFoundError as exc:
        raise ConfigError(
            "El almacenamiento temporal necesita boto3, que es una dependencia "
            "OPCIONAL: sin Instagram no hace falta. Instalala con "
            "`pip install 'viralgen[publish-staging]'`.",
            details={"missing_dependency": "boto3"},
        ) from exc
    return boto3


@dataclass(frozen=True)
class StagedObject:
    key: str
    sha256: str
    size_bytes: int
    etag: str | None
    uploaded_at: datetime
    reused: bool = False


@dataclass(frozen=True)
class SignedUrl:
    """La URL y su caducidad. La URL NO se persiste fuera del almacen privado."""

    url: str
    expires_at: datetime
    issued_at: datetime

    @property
    def fingerprint(self) -> str:
        """Huella para reconocerla sin revelarla."""
        return hashlib.sha256(self.url.encode("utf-8")).hexdigest()


class S3Staging:
    """Adaptador S3 compatible. Secuencial, en streaming y acotado."""

    def __init__(self, config: StagingConfig, *, client: Any = None) -> None:
        self.config = config
        self._client = client
        self._session = None

    # -- Cliente -----------------------------------------------------------

    @property
    def client(self):
        if self._client is None:
            boto3 = _boto3()
            self._session = boto3.session.Session()
            self._client = self._session.client(
                "s3",
                region_name=self.config.region,
                endpoint_url=self.config.endpoint_url,
                aws_access_key_id=self.config.access_key_id,
                aws_secret_access_key=self.config.secret_access_key,
            )
        return self._client

    def credentials_outlive(self, seconds: int) -> bool | None:
        """Si las credenciales que firman duran mas que la URL que firman.

        Devuelve None cuando no se puede saber (credenciales estaticas sin
        caducidad declarada): no se afirma que si, solo que no consta.
        """
        if self._session is None:
            return None
        credenciales = self._session.get_credentials()
        comprobar = getattr(credenciales, "refresh_needed", None)
        if not callable(comprobar):
            return None
        # `refresh_needed(refresh_in)` dice si caducan dentro de esa ventana.
        return not comprobar(seconds)

    # -- Claves ------------------------------------------------------------

    def object_key(
        self, *, publication_id: str, destination_id: str, video_sha256: str
    ) -> str:
        """Clave derivada de la INTENCION y del hash, no de la fecha.

        Reintentar la misma tarea apunta al mismo objeto; otro video da otra
        clave, de modo que nunca se pisa contenido distinto.
        """
        prefijo = f"{self.config.prefix}/" if self.config.prefix else ""
        return (
            f"{prefijo}{publication_id}/{destination_id}/{video_sha256[:16]}.mp4"
        )

    # -- Operaciones -------------------------------------------------------

    def head(self, key: str) -> dict | None:
        try:
            return self.client.head_object(Bucket=self.config.bucket, Key=key)
        except Exception as exc:  # botocore.ClientError y equivalentes del doble
            if _es_no_encontrado(exc):
                return None
            raise StagingError(
                f"No se pudo consultar el objeto {key}: {exc}",
                details={"key": key},
            ) from exc

    def upload(
        self,
        path: Path,
        *,
        key: str,
        sha256: str,
        now: datetime,
        progress: Callable[[int], None] | None = None,
    ) -> StagedObject:
        """Sube el MP4 autorizado. Si ya esta el mismo, no lo vuelve a subir."""
        tamano = path.stat().st_size
        if tamano > self.config.max_bytes:
            raise StagingError(
                f"El archivo ocupa {tamano} bytes y el tope propio del staging "
                f"son {self.config.max_bytes}.",
                details={"size_bytes": tamano},
            )

        existente = self.head(key)
        if existente is not None:
            declarado = (existente.get("Metadata") or {}).get(SHA_METADATA_KEY)
            if declarado == sha256 and int(existente.get("ContentLength", -1)) == tamano:
                logger.info("staging: el objeto ya estaba subido, no se repite")
                return StagedObject(
                    key=key,
                    sha256=sha256,
                    size_bytes=tamano,
                    etag=existente.get("ETag"),
                    uploaded_at=now,
                    reused=True,
                )
            raise StagingConflictError(
                f"Ya hay un objeto distinto en {key}. No se sobrescribe: "
                "podria estar sirviendo otra publicacion.",
                details={"key": key, "declared_sha256": declarado},
            )

        boto3 = _boto3()
        from boto3.s3.transfer import TransferConfig  # noqa: PLC0415

        transferencia = TransferConfig(
            # Un trabajador, un flujo: sin hilos y con bloques acotados.
            use_threads=False,
            max_concurrency=1,
            multipart_threshold=MULTIPART_CHUNK_BYTES,
            multipart_chunksize=MULTIPART_CHUNK_BYTES,
        )
        assert boto3 is not None
        try:
            with path.open("rb") as archivo:
                self.client.upload_fileobj(
                    archivo,
                    self.config.bucket,
                    key,
                    ExtraArgs={
                        "ContentType": "video/mp4",
                        "Metadata": {SHA_METADATA_KEY: sha256},
                    },
                    Callback=progress,
                    Config=transferencia,
                )
        except Exception as exc:
            raise StagingError(
                f"La subida al almacenamiento temporal fallo: {exc}",
                details={"key": key},
            ) from exc

        cabecera = self.head(key) or {}
        return StagedObject(
            key=key,
            sha256=sha256,
            size_bytes=tamano,
            etag=cabecera.get("ETag"),
            uploaded_at=now,
        )

    def presign(self, key: str, *, now: datetime, ttl_s: int | None = None) -> SignedUrl:
        """URL GET firmada. El resultado es material sensible."""
        segundos = int(ttl_s or self.config.url_ttl_s)
        try:
            url = self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.config.bucket, "Key": key},
                ExpiresIn=segundos,
            )
        except Exception as exc:
            raise StagingError(
                f"No se pudo firmar la URL de {key}: {exc}", details={"key": key}
            ) from exc
        return SignedUrl(
            url=url,
            issued_at=now,
            expires_at=now + timedelta(seconds=segundos),
        )

    def delete(self, key: str) -> None:
        try:
            self.client.delete_object(Bucket=self.config.bucket, Key=key)
        except Exception as exc:
            raise StagingError(
                f"No se pudo borrar {key}: {exc}", details={"key": key}
            ) from exc


def _es_no_encontrado(exc: Exception) -> bool:
    """404 de S3, tanto del SDK real como de un doble de pruebas."""
    respuesta = getattr(exc, "response", None)
    if isinstance(respuesta, dict):
        codigo = str(respuesta.get("Error", {}).get("Code", ""))
        estado = int(
            respuesta.get("ResponseMetadata", {}).get("HTTPStatusCode", 0) or 0
        )
        return codigo in ("404", "NoSuchKey", "NotFound") or estado == 404
    return exc.__class__.__name__ in ("NoSuchKey", "ClientError404")


# ---------------------------------------------------------------------------
# Limpieza
# ---------------------------------------------------------------------------

#: Estados en los que el objeto SIGUE haciendo falta o puede hacer falta.
ESTADOS_QUE_RETIENEN = frozenset(
    {"scheduled_local", "dispatching", "waiting_remote", "needs_reconciliation",
     "needs_review", "draft", "blocked"}
)


@dataclass(frozen=True)
class CleanupCandidate:
    bucket_alias: str
    object_key: str
    publication_id: str
    destination_id: str
    size_bytes: int
    retain_until: str | None
    reason: str


def plan_cleanup(storage, *, now: datetime) -> tuple[list[CleanupCandidate], list[str]]:
    """Candidatos a borrar y motivos por los que otros se conservan.

    Un objeto solo se borra si su destino esta en un estado terminal, su
    retencion ha vencido y ningun otro destino lo referencia. Los estados
    ambiguos conservan el objeto y consumen cuota hasta que alguien los
    resuelva: borrar bajo la duda es como se pierde una publicacion a medias.
    """
    candidatos: list[CleanupCandidate] = []
    conservados: list[str] = []
    for objeto in storage.staging_objects(only_live=True):
        referencias = [
            fila
            for fila in storage.list_destinations(objeto["publication_id"])
            if (fila["staging_json"] or "").find(objeto["object_key"]) >= 0
        ]
        bloqueantes = [
            fila["destination_id"]
            for fila in referencias
            if fila["state"] in ESTADOS_QUE_RETIENEN
        ]
        if bloqueantes:
            conservados.append(
                f"{objeto['object_key']}: lo referencian destinos sin resolver "
                f"({', '.join(bloqueantes)})"
            )
            continue
        retener_hasta = objeto.get("retain_until")
        if retener_hasta and str(retener_hasta) > now.strftime("%Y-%m-%dT%H:%M:%SZ"):
            conservados.append(
                f"{objeto['object_key']}: su retencion llega hasta {retener_hasta}"
            )
            continue
        candidatos.append(
            CleanupCandidate(
                bucket_alias=objeto["bucket_alias"],
                object_key=objeto["object_key"],
                publication_id=objeto["publication_id"],
                destination_id=objeto["destination_id"],
                size_bytes=int(objeto["size_bytes"]),
                retain_until=retener_hasta,
                reason="estado terminal y retencion vencida",
            )
        )
    return candidatos, conservados


