"""Instagram Reels y el almacenamiento temporal, con transporte y SDK simulados.

No se instala ningun servicio: el SDK de almacenamiento es un doble y las
llamadas a Graph pasan por `httpx2.MockTransport`. Lo que se comprueba es que
FINISHED no se confunde con publicado, que una respuesta perdida no publica dos
veces y que la URL firmada no se escapa a ningun documento.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx2
import pytest

from viralgen.config import Settings
from viralgen.errors import ConfigError
from viralgen.publish.errors import AuthRequiredError
from viralgen.publish.providers.base import DispatchContext
from viralgen.publish.providers.instagram import (
    PERMISSIONS,
    TOKEN_FILE,
    InstagramReelsAdapter,
)
from viralgen.publish.schemas import (
    AudienceDecision,
    DestinationMetadata,
    DestinationOptions,
    DestinationState,
    ErrorClass,
    PublishMode,
    SyntheticDisclosure,
    TextSource,
    TransferPhase,
    Visibility,
)
from viralgen.publish.secrets import SecretStore
from viralgen.publish.staging import (
    S3Staging,
    StagingConfig,
    StagingConflictError,
    load_staging_config,
    plan_cleanup,
)
from viralgen.publish.transport import PublishHttpClient
from viralgen.schemas.common import Platform

UTC = timezone.utc
AHORA = datetime(2026, 9, 24, 16, 45, tzinfo=UTC)
IG_USER = "17841400000000000"
CONTENEDOR = "18000000000000000"
MEDIO = "17900000000000000"
URL_FIRMADA = (
    "https://bucket.example.com/viralgen/pub1/ig/abc.mp4"
    "?X-Amz-Signature=deadbeefdeadbeef&X-Amz-Expires=7200"
)


# ---------------------------------------------------------------------------
# Doble del SDK de almacenamiento
# ---------------------------------------------------------------------------


class ClienteS3Falso:
    """Doble minimo del cliente de boto3. No instala ni contacta nada."""

    def __init__(self) -> None:
        self.objetos: dict[str, dict] = {}
        self.subidas: list[str] = []
        self.borrados: list[str] = []
        self.firmas: list[tuple[str, int]] = []

    def head_object(self, *, Bucket: str, Key: str) -> dict:
        if Key not in self.objetos:
            error = Exception("not found")
            error.response = {"Error": {"Code": "404"}}
            raise error
        return self.objetos[Key]

    def upload_fileobj(self, Fileobj, Bucket, Key, ExtraArgs=None, Callback=None, Config=None):
        leido = 0
        while True:
            trozo = Fileobj.read(1024 * 1024)
            if not trozo:
                break
            leido += len(trozo)
            if Callback:
                Callback(len(trozo))
        self.subidas.append(Key)
        self.objetos[Key] = {
            "ContentLength": leido,
            "ETag": '"etag-inventado-por-el-proveedor"',
            "Metadata": dict((ExtraArgs or {}).get("Metadata", {})),
        }

    def generate_presigned_url(self, ClientMethod, Params=None, ExpiresIn=3600, HttpMethod=None):
        self.firmas.append((Params["Key"], ExpiresIn))
        return URL_FIRMADA

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self.borrados.append(Key)
        self.objetos.pop(Key, None)


def _staging(tmp_path: Path) -> tuple[S3Staging, ClienteS3Falso]:
    cliente = ClienteS3Falso()
    configuracion = StagingConfig(
        endpoint_url="https://bucket.example.com",
        region="auto",
        bucket="videos-privados",
        prefix="viralgen",
        access_key_id="clave",
        secret_access_key="secreto",
        url_ttl_s=7200,
        retention_s=86_400,
        max_objects=20,
        max_bytes=200 * 1024 * 1024,
    )
    return S3Staging(configuracion, client=cliente), cliente


# ---------------------------------------------------------------------------
# Contexto
# ---------------------------------------------------------------------------


def _ajustes(tmp_path: Path, **cambios) -> Settings:
    base = dict(
        _env_file=None,
        data_dir=tmp_path / "data",
        log_level="ERROR",
        meta_graph_api_version="v21.0",
        instagram_user_id=IG_USER,
        publish_poll_interval_s=30.0,
    )
    base.update(cambios)
    return Settings(**base)


def _almacen(tmp_path: Path, *, caducado: bool = False) -> SecretStore:
    almacen = SecretStore(tmp_path / "secretos")
    almacen.ensure()
    almacen.put(
        TOKEN_FILE,
        {
            "access_token": "EAAG-token-de-prueba",
            "expires_at": (
                (AHORA - timedelta(days=1)) if caducado else (AHORA + timedelta(days=30))
            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "imported_from": "flujo oficial de Meta",
        },
    )
    return almacen


def _contexto(tmp_path: Path, ajustes: Settings, *, row=None, visibility=Visibility.PUBLIC, caducado=False, gasto=None) -> DispatchContext:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"\x01" * 1_500_000)
    fila = {"remote_refs_json": "{}", "attempts": 0, "dispatch_started_at": None, "real_remote_id": None}
    fila.update(row or {})
    return DispatchContext(
        publication_id="pub1",
        destination_id="ig",
        platform=Platform.INSTAGRAM_REELS,
        account_alias="reels_demo",
        expected_account_id=IG_USER,
        metadata=DestinationMetadata(
            title="Titulo",
            description="El texto del guion.",
            tags=[],
            hashtags=["curiosidades", "sabiasque"],
            language="es",
            audience=AudienceDecision.NOT_MADE_FOR_KIDS,
            synthetic_disclosure=SyntheticDisclosure.NO_REALISTIC_SYNTHETIC_MEDIA,
            text_source=TextSource.SCRIPT_PUBLISHING_PLAN,
            within_local_limits=True,
        ),
        requested_visibility=visibility,
        options=DestinationOptions(share_to_feed=True, requires_staging=True),
        video_path=video,
        video_sha256="c" * 64,
        video_size=video.stat().st_size,
        mode=PublishMode.REAL,
        row=fila,
        now=AHORA,
        settings=ajustes,
        secrets=_almacen(tmp_path, caducado=caducado),
        spend=(lambda s, b: gasto.append((s, b))) if gasto is not None else (lambda s, b: None),
    )


def _adaptador(ajustes: Settings, handler, staging=None):
    capturadas: list[httpx2.Request] = []

    def envoltorio(request: httpx2.Request) -> httpx2.Response:
        capturadas.append(request)
        return handler(request)

    cliente = PublishHttpClient(
        timeout_s=5.0,
        allowed_hosts=InstagramReelsAdapter.allowed_hosts(ajustes),
        client=httpx2.Client(transport=httpx2.MockTransport(envoltorio)),
        sleep=lambda _s: None,
    )
    return (
        InstagramReelsAdapter(
            settings=ajustes, client=cliente, staging=staging, sleep=lambda _s: None
        ),
        capturadas,
    )


# ---------------------------------------------------------------------------
# Cuenta y permisos
# ---------------------------------------------------------------------------


def _handler_cuenta(cuentas=(IG_USER,), permisos=PERMISSIONS):
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/me/accounts"):
            return httpx2.Response(
                200,
                json={
                    "data": [
                        {"id": f"pagina-{i}", "instagram_business_account": {"id": c}}
                        for i, c in enumerate(cuentas)
                    ]
                },
            )
        if request.url.path.endswith("/me/permissions"):
            return httpx2.Response(
                200,
                json={
                    "data": [
                        {"permission": p, "status": "granted"} for p in permisos
                    ]
                },
            )
        return httpx2.Response(404)

    return handler


def test_la_cuenta_y_la_pagina_deben_ser_las_autorizadas(tmp_path: Path) -> None:
    ajustes = _ajustes(tmp_path)
    adaptador, _ = _adaptador(ajustes, _handler_cuenta(cuentas=("otra-cuenta",)))
    resultado = adaptador.check_account(_contexto(tmp_path, ajustes))
    assert not resultado.ok
    assert "otra-cuenta" in resultado.candidates


def test_faltan_permisos_es_un_problema_de_permisos(tmp_path: Path) -> None:
    ajustes = _ajustes(tmp_path)
    adaptador, _ = _adaptador(
        ajustes, _handler_cuenta(permisos=("pages_show_list", "instagram_basic"))
    )
    resultado = adaptador.check_account(_contexto(tmp_path, ajustes))
    assert not resultado.ok
    assert resultado.error.error_class is ErrorClass.PERMISSION
    assert "instagram_content_publish" in resultado.detail


def test_no_se_piden_permisos_de_mensajes_ni_comentarios() -> None:
    assert set(PERMISSIONS) == {
        "pages_show_list",
        "pages_read_engagement",
        "instagram_basic",
        "instagram_content_publish",
    }


def test_la_cuenta_correcta_se_acepta_sin_publicar_nada(tmp_path: Path) -> None:
    ajustes = _ajustes(tmp_path)
    adaptador, capturadas = _adaptador(ajustes, _handler_cuenta())
    resultado = adaptador.check_account(_contexto(tmp_path, ajustes))
    assert resultado.ok and resultado.observed_account_id == IG_USER
    assert all(p.method == "GET" for p in capturadas)


def test_un_token_caducado_exige_renovarlo(tmp_path: Path) -> None:
    ajustes = _ajustes(tmp_path)
    adaptador, _ = _adaptador(ajustes, _handler_cuenta())
    with pytest.raises(AuthRequiredError, match="caduco"):
        adaptador.check_account(_contexto(tmp_path, ajustes, caducado=True))


def test_la_version_de_graph_es_obligatoria(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="META_GRAPH_API_VERSION"):
        InstagramReelsAdapter(settings=Settings(_env_file=None, data_dir=tmp_path))


# ---------------------------------------------------------------------------
# Staging
# ---------------------------------------------------------------------------


def test_el_staging_sube_en_streaming_y_guarda_el_hash_local(tmp_path: Path) -> None:
    staging, cliente = _staging(tmp_path)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 3_000_000)
    objeto = staging.upload(video, key="k.mp4", sha256="d" * 64, now=AHORA)
    assert objeto.size_bytes == 3_000_000
    assert objeto.sha256 == "d" * 64
    # El ETag se conserva como dato del proveedor, no como prueba del SHA-256.
    assert objeto.etag and objeto.etag != objeto.sha256
    assert cliente.objetos["k.mp4"]["Metadata"]["viralgen-sha256"] == "d" * 64


def test_no_se_sobrescribe_un_objeto_con_contenido_distinto(tmp_path: Path) -> None:
    staging, cliente = _staging(tmp_path)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 1000)
    staging.upload(video, key="k.mp4", sha256="d" * 64, now=AHORA)
    with pytest.raises(StagingConflictError, match="No se sobrescribe"):
        staging.upload(video, key="k.mp4", sha256="e" * 64, now=AHORA)


def test_el_mismo_objeto_no_se_vuelve_a_subir(tmp_path: Path) -> None:
    staging, cliente = _staging(tmp_path)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 1000)
    staging.upload(video, key="k.mp4", sha256="d" * 64, now=AHORA)
    otra = staging.upload(video, key="k.mp4", sha256="d" * 64, now=AHORA)
    assert otra.reused is True
    assert cliente.subidas == ["k.mp4"]


def test_la_clave_del_objeto_sale_de_la_intencion_y_del_hash(tmp_path: Path) -> None:
    staging, _ = _staging(tmp_path)
    clave = staging.object_key(
        publication_id="pub1", destination_id="ig", video_sha256="f" * 64
    )
    assert clave == "viralgen/pub1/ig/" + "f" * 16 + ".mp4"


def test_el_staging_exige_configuracion_completa(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="almacenamiento temporal"):
        load_staging_config(Settings(_env_file=None, data_dir=tmp_path))


# ---------------------------------------------------------------------------
# Publicacion
# ---------------------------------------------------------------------------


def _handler_publicacion(estado: dict):
    def handler(request: httpx2.Request) -> httpx2.Response:
        ruta = request.url.path
        if ruta.endswith("/media"):
            estado["contenedor_creado"] = estado.get("contenedor_creado", 0) + 1
            estado["cuerpo"] = json.loads(request.content)
            return httpx2.Response(200, json={"id": CONTENEDOR})
        if ruta.endswith("/media_publish"):
            estado["publicaciones"] = estado.get("publicaciones", 0) + 1
            return httpx2.Response(200, json={"id": MEDIO})
        if ruta.endswith(f"/{CONTENEDOR}"):
            return httpx2.Response(
                200, json={"id": CONTENEDOR, "status_code": estado.get("status", "FINISHED")}
            )
        if ruta.endswith(f"/{MEDIO}"):
            return httpx2.Response(
                200,
                json={
                    "id": MEDIO,
                    "permalink": "https://www.instagram.com/reel/ABC/",
                    "media_type": "VIDEO",
                },
            )
        return httpx2.Response(404, json={"error": {"message": ruta}})

    return handler


def test_el_contenedor_se_crea_con_los_campos_del_flujo_elegido(tmp_path: Path) -> None:
    ajustes = _ajustes(tmp_path)
    staging, _ = _staging(tmp_path)
    estado: dict = {}
    adaptador, capturadas = _adaptador(ajustes, _handler_publicacion(estado), staging)
    resultado = adaptador.start(_contexto(tmp_path, ajustes))

    assert resultado.state is DestinationState.WAITING_REMOTE
    assert resultado.remote_refs["container_id"] == CONTENEDOR
    cuerpo = estado["cuerpo"]
    assert cuerpo["media_type"] == "REELS"
    assert cuerpo["video_url"] == URL_FIRMADA
    assert cuerpo["share_to_feed"] == "true"
    assert "#curiosidades" in cuerpo["caption"]
    assert "El texto del guion." in cuerpo["caption"]
    # Se usa graph.facebook.com con la version fijada, no otro host.
    assert capturadas[-1].url.host == "graph.facebook.com"
    assert "/v21.0/" in str(capturadas[-1].url)


def test_la_url_firmada_no_aparece_en_la_referencia_persistida(tmp_path: Path) -> None:
    """Es un secreto operativo: del recibo solo sale su huella y su caducidad."""
    ajustes = _ajustes(tmp_path)
    staging, _ = _staging(tmp_path)
    adaptador, _ = _adaptador(ajustes, _handler_publicacion({}), staging)
    ctx = _contexto(tmp_path, ajustes)
    resultado = adaptador.start(ctx)

    referencia = resultado.staging
    assert referencia is not None
    exportado = referencia.model_dump_json()
    assert "X-Amz-Signature" not in exportado
    assert URL_FIRMADA not in exportado
    assert referencia.url_sha256 and len(referencia.url_sha256) == 64
    assert referencia.url_expires_at == AHORA + timedelta(hours=2)
    assert referencia.hash_source == "local_file"
    # Pero la URL si esta disponible en el almacen privado, con 0600.
    privado = ctx.secrets.path_for(ctx.session_name("instagram_staging"))
    assert oct(privado.stat().st_mode & 0o777) == "0o600"
    assert URL_FIRMADA in privado.read_text()


def test_finished_no_es_publicado(tmp_path: Path) -> None:
    """El contenedor listo no publica solo: hace falta media_publish."""
    ajustes = _ajustes(tmp_path)
    staging, _ = _staging(tmp_path)
    estado = {"status": "IN_PROGRESS"}
    adaptador, _ = _adaptador(ajustes, _handler_publicacion(estado), staging)
    ctx = _contexto(
        tmp_path,
        ajustes,
        row={"remote_refs_json": json.dumps({"container_id": CONTENEDOR}),
             "dispatch_started_at": "2026-09-24T16:44:00Z"},
    )
    resultado = adaptador.poll(ctx)
    assert resultado.state is DestinationState.WAITING_REMOTE
    assert resultado.phase is TransferPhase.REMOTE_PROCESSING
    assert estado.get("publicaciones") is None


def test_el_recorrido_completo_termina_en_entregado(tmp_path: Path) -> None:
    ajustes = _ajustes(tmp_path)
    staging, _ = _staging(tmp_path)
    estado = {"status": "FINISHED"}
    adaptador, _ = _adaptador(ajustes, _handler_publicacion(estado), staging)
    ctx = _contexto(
        tmp_path, ajustes, row={"remote_refs_json": json.dumps({"container_id": CONTENEDOR})}
    )
    resultado = adaptador.poll(ctx)
    assert resultado.state is DestinationState.DELIVERED
    assert resultado.remote_id == MEDIO
    assert resultado.publicly_visible is True
    assert resultado.permalink.startswith("https://www.instagram.com/")
    assert estado["publicaciones"] == 1


def test_perder_la_respuesta_de_media_publish_no_publica_otra_vez(
    tmp_path: Path,
) -> None:
    ajustes = _ajustes(tmp_path)
    staging, _ = _staging(tmp_path)
    estado = {"status": "FINISHED", "publicaciones": 0}

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/media_publish"):
            estado["publicaciones"] += 1
            estado["status"] = "PUBLISHED"
            raise httpx2.ReadTimeout("respuesta perdida", request=request)
        return _handler_publicacion(estado)(request)

    adaptador, capturadas = _adaptador(ajustes, handler, staging)
    ctx = _contexto(
        tmp_path, ajustes, row={"remote_refs_json": json.dumps({"container_id": CONTENEDOR})}
    )
    resultado = adaptador.poll(ctx)

    assert estado["publicaciones"] == 1, "no se repite el POST de publicacion"
    # El contenedor dice PUBLISHED pero no hay id de medio recuperable.
    assert resultado.state is DestinationState.NEEDS_RECONCILIATION
    assert resultado.error.code == "published_without_media_id"
    assert resultado.error.retryable is False


def test_un_contenedor_con_error_termina_en_fallo(tmp_path: Path) -> None:
    ajustes = _ajustes(tmp_path)
    adaptador, _ = _adaptador(ajustes, _handler_publicacion({"status": "ERROR"}))
    ctx = _contexto(
        tmp_path, ajustes, row={"remote_refs_json": json.dumps({"container_id": CONTENEDOR})}
    )
    resultado = adaptador.poll(ctx)
    assert resultado.state is DestinationState.FAILED
    assert resultado.error.code == "container_error"


def test_un_contenedor_caducado_no_se_reintenta_en_silencio(tmp_path: Path) -> None:
    ajustes = _ajustes(tmp_path)
    adaptador, _ = _adaptador(ajustes, _handler_publicacion({"status": "EXPIRED"}))
    ctx = _contexto(
        tmp_path, ajustes, row={"remote_refs_json": json.dumps({"container_id": CONTENEDOR})}
    )
    resultado = adaptador.poll(ctx)
    assert resultado.state is DestinationState.FAILED
    assert resultado.error.code == "container_expired"


def test_un_estado_desconocido_va_a_reconciliacion(tmp_path: Path) -> None:
    ajustes = _ajustes(tmp_path)
    adaptador, _ = _adaptador(ajustes, _handler_publicacion({"status": "ALGO_NUEVO"}))
    ctx = _contexto(
        tmp_path, ajustes, row={"remote_refs_json": json.dumps({"container_id": CONTENEDOR})}
    )
    resultado = adaptador.poll(ctx)
    assert resultado.state is DestinationState.NEEDS_RECONCILIATION
    assert resultado.error.error_class is ErrorClass.AMBIGUOUS


def test_no_se_crea_un_segundo_contenedor_si_ya_hay_uno(tmp_path: Path) -> None:
    ajustes = _ajustes(tmp_path)
    staging, _ = _staging(tmp_path)
    estado = {"status": "IN_PROGRESS"}
    adaptador, _ = _adaptador(ajustes, _handler_publicacion(estado), staging)
    ctx = _contexto(
        tmp_path,
        ajustes,
        row={"remote_refs_json": json.dumps({"container_id": CONTENEDOR}),
             "dispatch_started_at": "2026-09-24T16:44:00Z"},
    )
    adaptador.start(ctx)
    assert estado.get("contenedor_creado") is None


def test_una_visibilidad_no_publica_se_rechaza_antes_de_tocar_nada(
    tmp_path: Path,
) -> None:
    ajustes = _ajustes(tmp_path)
    staging, cliente = _staging(tmp_path)
    adaptador, capturadas = _adaptador(ajustes, _handler_publicacion({}), staging)
    resultado = adaptador.start(
        _contexto(tmp_path, ajustes, visibility=Visibility.PRIVATE)
    )
    assert resultado.state is DestinationState.NEEDS_REVIEW
    assert capturadas == [] and cliente.subidas == []


# ---------------------------------------------------------------------------
# Limpieza
# ---------------------------------------------------------------------------


def test_la_limpieza_respeta_referencias_y_estados_ambiguos(tmp_path: Path) -> None:
    from viralgen.publish.storage import PublishStorage
    from viralgen.storage import Storage

    almacenamiento = PublishStorage(Storage(tmp_path / "datos"))
    almacenamiento.migrate()
    almacenamiento.create_destination(
        {
            "publication_id": "pub1",
            "destination_id": "ig",
            "platform": "instagram_reels",
            "account_alias": "reels_demo",
            "state": "needs_reconciliation",
            "simulation": 0,
            "requested_visibility": "public",
            "staging_json": json.dumps({"object_key": "viralgen/pub1/ig/abc.mp4"}),
        }
    )
    almacenamiento.record_staging_object(
        {
            "object_key": "viralgen/pub1/ig/abc.mp4",
            "bucket_alias": "videos-privados",
            "publication_id": "pub1",
            "destination_id": "ig",
            "object_sha256": "c" * 64,
            "size_bytes": 1000,
            "uploaded_at": "2026-09-20T00:00:00Z",
            "retain_until": "2026-09-21T00:00:00Z",
        }
    )
    candidatos, conservados = plan_cleanup(almacenamiento, now=AHORA)
    assert candidatos == []
    assert "sin resolver" in conservados[0]

    # Resuelto y con retencion vencida: ya se puede borrar.
    almacenamiento.update_destination("pub1", "ig", state=DestinationState.DELIVERED)
    candidatos, _ = plan_cleanup(almacenamiento, now=AHORA)
    assert [c.object_key for c in candidatos] == ["viralgen/pub1/ig/abc.mp4"]
