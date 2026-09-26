"""Adaptador de YouTube probado con transporte simulado.

No hay red ni credenciales: `httpx2.MockTransport` responde en su lugar, pero
la peticion pasa por el cliente real, con sus cabeceras, su `Content-Range` y
su clasificacion de errores. Lo que se comprueba es justamente lo que puede
duplicar contenido o declarar entregado algo que no lo esta.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx2
import pytest

from viralgen.config import Settings
from viralgen.publish.errors import (
    AuthRequiredError,
    DisclosureNotTransmittableError,
    ReconciliationRequiredError,
)
from viralgen.publish.providers.base import DispatchContext
from viralgen.publish.providers.google_oauth import TOKEN_FILE, TokenBundle, ensure_token
from viralgen.publish.providers.youtube import (
    SYNTHETIC_MEDIA_PROPERTY,
    SYNTHETIC_MEDIA_VALUE,
    YouTubeAdapter,
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
from viralgen.publish.transport import PublishHttpClient
from viralgen.schemas.common import Platform

UTC = timezone.utc
AHORA = datetime(2026, 9, 24, 16, 30, tzinfo=UTC)
CANAL = "UCcanal_autorizado"
VIDEO_ID = "vid_0123456789"
TAMANO = 2_500_000


def _ajustes(tmp_path: Path, **cambios) -> Settings:
    base = dict(
        _env_file=None,
        data_dir=tmp_path / "data",
        log_level="ERROR",
        youtube_chunk_mib=1,
        youtube_client_id="cliente.apps.googleusercontent.com",
        youtube_channel_id=CANAL,
        publish_poll_interval_s=30.0,
    )
    base.update(cambios)
    return Settings(**base)


def _almacen(tmp_path: Path, *, caducado: bool = False) -> SecretStore:
    almacen = SecretStore(tmp_path / "secretos")
    almacen.ensure()
    almacen.put(
        TOKEN_FILE,
        TokenBundle(
            access_token="token-de-prueba",
            refresh_token="refresco-de-prueba",
            expires_at=(AHORA - timedelta(hours=1)) if caducado else (AHORA + timedelta(hours=1)),
            scopes=("https://www.googleapis.com/auth/youtube.upload",),
            client_id="cliente.apps.googleusercontent.com",
        ).to_payload(),
    )
    return almacen


def _video(tmp_path: Path, tamano: int = TAMANO) -> Path:
    destino = tmp_path / "video.mp4"
    destino.write_bytes(b"\x00" * tamano)
    return destino


def _metadata(**cambios) -> DestinationMetadata:
    base = dict(
        title="Un titulo aprobado por el operador",
        description="El texto que venia del guion.",
        tags=[],
        hashtags=[],
        language="es",
        audience=AudienceDecision.NOT_MADE_FOR_KIDS,
        synthetic_disclosure=SyntheticDisclosure.NO_REALISTIC_SYNTHETIC_MEDIA,
        text_source=TextSource.SCRIPT_PUBLISHING_PLAN,
        within_local_limits=True,
    )
    base.update(cambios)
    return DestinationMetadata(**base)


def _contexto(
    tmp_path: Path,
    ajustes: Settings,
    *,
    row: dict | None = None,
    visibility: Visibility = Visibility.PRIVATE,
    metadata: DestinationMetadata | None = None,
    gasto: list | None = None,
    caducado: bool = False,
) -> DispatchContext:
    video = _video(tmp_path)
    fila = {
        "remote_refs_json": "{}",
        "attempts": 0,
        "dispatch_started_at": None,
        "real_remote_id": None,
    }
    fila.update(row or {})

    def spend(solicitudes: int, bytes_: int) -> None:
        if gasto is not None:
            gasto.append((solicitudes, bytes_))

    return DispatchContext(
        publication_id="pub1",
        destination_id="yt",
        platform=Platform.YOUTUBE_SHORTS,
        account_alias="canal_demo",
        expected_account_id=CANAL,
        metadata=metadata or _metadata(),
        requested_visibility=visibility,
        options=DestinationOptions(notify_subscribers=False),
        video_path=video,
        video_sha256="a" * 64,
        video_size=video.stat().st_size,
        mode=PublishMode.REAL,
        row=fila,
        now=AHORA,
        settings=ajustes,
        secrets=_almacen(tmp_path, caducado=caducado),
        spend=spend,
    )


def _adaptador(ajustes: Settings, handler) -> tuple[YouTubeAdapter, list]:
    capturadas: list[httpx2.Request] = []

    def envoltorio(request: httpx2.Request) -> httpx2.Response:
        capturadas.append(request)
        return handler(request)

    cliente = PublishHttpClient(
        timeout_s=5.0,
        allowed_hosts=YouTubeAdapter.allowed_hosts(ajustes),
        client=httpx2.Client(
            transport=httpx2.MockTransport(envoltorio), follow_redirects=False
        ),
        sleep=lambda _s: None,
    )
    return YouTubeAdapter(settings=ajustes, client=cliente, sleep=lambda _s: None), capturadas


def _respuesta_canales(ids=(CANAL,)) -> httpx2.Response:
    return httpx2.Response(
        200,
        json={"items": [{"id": identificador, "snippet": {}} for identificador in ids]},
    )


def _recurso(uploadStatus="processed", privacy="private", channel=CANAL) -> dict:
    return {
        "id": VIDEO_ID,
        "snippet": {"channelId": channel},
        "status": {"uploadStatus": uploadStatus, "privacyStatus": privacy},
    }


# ---------------------------------------------------------------------------
# Cuenta
# ---------------------------------------------------------------------------


def test_la_cuenta_debe_coincidir_con_el_destino_autorizado(tmp_path: Path) -> None:
    ajustes = _ajustes(tmp_path)
    adaptador, _ = _adaptador(ajustes, lambda r: _respuesta_canales(("UCotro_canal",)))
    resultado = adaptador.check_account(_contexto(tmp_path, ajustes))
    assert not resultado.ok
    assert "UCotro_canal" in resultado.candidates
    assert "autorizado" in resultado.detail


def test_varios_canales_exigen_eleccion_explicita(tmp_path: Path) -> None:
    ajustes = _ajustes(tmp_path)
    adaptador, _ = _adaptador(ajustes, lambda r: _respuesta_canales(("UCa", "UCb")))
    ctx = _contexto(tmp_path, ajustes)
    ctx.expected_account_id = None
    resultado = adaptador.check_account(ctx)
    assert not resultado.ok
    assert resultado.candidates == ["UCa", "UCb"]
    assert "explicitamente" in resultado.detail


def test_la_cuenta_correcta_se_acepta(tmp_path: Path) -> None:
    ajustes = _ajustes(tmp_path)
    adaptador, capturadas = _adaptador(ajustes, lambda r: _respuesta_canales())
    resultado = adaptador.check_account(_contexto(tmp_path, ajustes))
    assert resultado.ok and resultado.observed_account_id == CANAL
    assert capturadas[0].url.params["mine"] == "true"
    # Comprobar la cuenta no publica nada.
    assert all(peticion.method == "GET" for peticion in capturadas)


def _handler_subida(estado: dict):
    """Simula una sesion reanudable que confirma bloque a bloque."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/upload/youtube/v3/videos"):
            estado["sesion"] = True
            return httpx2.Response(
                200, headers={"Location": "https://www.googleapis.com/session/abc123"}
            )
        if "/session/" in request.url.path:
            rango = request.headers.get("Content-Range", "")
            if rango.startswith("bytes */"):
                return httpx2.Response(
                    308, headers={"Range": f"bytes=0-{estado['offset'] - 1}"}
                )
            fin = int(rango.split("-")[1].split("/")[0])
            total = int(rango.split("/")[1])
            estado["offset"] = fin + 1
            estado["peticiones"] = estado.get("peticiones", 0) + 1
            if estado["offset"] >= total:
                return httpx2.Response(200, json=_recurso(uploadStatus="uploaded"))
            return httpx2.Response(308, headers={"Range": f"bytes=0-{fin}"})
        return httpx2.Response(404)

    return handler


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------


def test_el_payload_lleva_audiencia_privacidad_y_ningun_publishAt(tmp_path: Path) -> None:
    ajustes = _ajustes(tmp_path)
    adaptador, _ = _adaptador(ajustes, lambda r: httpx2.Response(200))
    ctx = _contexto(
        tmp_path,
        ajustes,
        metadata=_metadata(audience=AudienceDecision.MADE_FOR_KIDS),
    )
    cuerpo = adaptador.build_body(ctx)
    assert cuerpo["status"]["privacyStatus"] == "private"
    assert cuerpo["status"]["selfDeclaredMadeForKids"] is True
    assert "publishAt" not in json.dumps(cuerpo)
    assert cuerpo["snippet"]["title"] == ctx.metadata.title


def test_sin_decidir_la_audiencia_no_se_construye_payload(tmp_path: Path) -> None:
    ajustes = _ajustes(tmp_path)
    adaptador, _ = _adaptador(ajustes, lambda r: httpx2.Response(200))
    ctx = _contexto(
        tmp_path, ajustes, metadata=_metadata(audience=AudienceDecision.UNDECIDED)
    )
    with pytest.raises(DisclosureNotTransmittableError, match="audiencia"):
        adaptador.build_body(ctx)


@pytest.mark.parametrize(
    ("decision", "esperado"),
    [
        (SyntheticDisclosure.CONTAINS_REALISTIC_SYNTHETIC_MEDIA, True),
        (SyntheticDisclosure.NO_REALISTIC_SYNTHETIC_MEDIA, False),
    ],
)
def test_la_divulgacion_aprobada_viaja_en_el_cuerpo_http(
    tmp_path: Path, decision: SyntheticDisclosure, esperado: bool
) -> None:
    """Con el nombre OFICIAL, y tambien cuando el valor aprobado es `false`.

    Regresion de un defecto real: la version anterior solo enviaba la propiedad
    cuando la decision era afirmativa, asi que un `false` explicitamente
    aprobado se omitia. Un `false` es una declaracion, no la ausencia de una.
    """
    ajustes = _ajustes(tmp_path)
    estado = {"offset": 0}
    adaptador, capturadas = _adaptador(ajustes, _handler_subida(estado))
    ctx = _contexto(tmp_path, ajustes, metadata=_metadata(synthetic_disclosure=decision))

    adaptador.start(ctx)

    posts = [p for p in capturadas if p.method == "POST"]
    assert len(posts) == 1
    cuerpo = json.loads(posts[0].content)
    assert cuerpo["status"]["containsSyntheticMedia"] is esperado
    assert SYNTHETIC_MEDIA_PROPERTY == "containsSyntheticMedia"


def test_una_divulgacion_sin_resolver_bloquea_el_envio(tmp_path: Path) -> None:
    """Sin decision no se sube: no se manda `false` por omision."""
    ajustes = _ajustes(tmp_path)
    estado = {"offset": 0}
    adaptador, capturadas = _adaptador(ajustes, _handler_subida(estado))
    ctx = _contexto(
        tmp_path,
        ajustes,
        metadata=_metadata(synthetic_disclosure=SyntheticDisclosure.NOT_REVIEWED),
    )

    resultado = adaptador.start(ctx)

    assert resultado.state is DestinationState.NEEDS_REVIEW
    assert resultado.error.code == "disclosure_not_transmittable"
    assert "decision editorial" in resultado.error.message
    assert capturadas == [], "no se abre sesion ni se envia un solo byte"

    with pytest.raises(DisclosureNotTransmittableError, match="no esta resuelta"):
        adaptador.build_body(ctx)


def test_la_divulgacion_no_depende_de_simulation_ni_del_uso_de_ia(
    tmp_path: Path,
) -> None:
    """Son decisiones distintas y el adaptador no las mezcla.

    El cuerpo que se enviaria es el MISMO en modo simulado y en modo real con
    los mismos metadatos: el valor sale de la decision editorial, no de como se
    produjo el paquete. Y un paquete de fuentes simuladas cuya decision diga
    "no contiene medios sinteticos realistas" transmite `false`, no `true`.
    """
    ajustes = _ajustes(tmp_path)
    adaptador, _ = _adaptador(ajustes, lambda r: httpx2.Response(200))
    metadata = _metadata(
        synthetic_disclosure=SyntheticDisclosure.NO_REALISTIC_SYNTHETIC_MEDIA
    )

    simulado = _contexto(tmp_path, ajustes, metadata=metadata)
    simulado.mode = PublishMode.MOCK
    real = _contexto(tmp_path, ajustes, metadata=metadata)

    assert adaptador.build_body(simulado) == adaptador.build_body(real)
    assert adaptador.build_body(real)["status"]["containsSyntheticMedia"] is False
    # Y el mapeo tipado no tiene entrada para "sin revisar": no hay valor por
    # omision que se pueda colar.
    assert SyntheticDisclosure.NOT_REVIEWED not in SYNTHETIC_MEDIA_VALUE


def test_la_audiencia_sin_decidir_bloquea_el_envio(tmp_path: Path) -> None:
    ajustes = _ajustes(tmp_path)
    adaptador, capturadas = _adaptador(ajustes, _handler_subida({"offset": 0}))
    ctx = _contexto(
        tmp_path, ajustes, metadata=_metadata(audience=AudienceDecision.UNDECIDED)
    )
    resultado = adaptador.start(ctx)
    assert resultado.state is DestinationState.NEEDS_REVIEW
    assert "selfDeclaredMadeForKids" in resultado.error.message
    assert capturadas == []


# ---------------------------------------------------------------------------
# Subida reanudable
# ---------------------------------------------------------------------------


def test_la_subida_se_hace_por_bloques_y_reanuda_desde_el_rango_confirmado(
    tmp_path: Path,
) -> None:
    ajustes = _ajustes(tmp_path)
    estado = {"offset": 0}
    adaptador, capturadas = _adaptador(ajustes, _handler_subida(estado))
    gasto: list = []
    resultado = adaptador.start(_contexto(tmp_path, ajustes, gasto=gasto))

    assert resultado.state is DestinationState.WAITING_REMOTE
    assert resultado.phase is TransferPhase.BYTES_ACCEPTED
    assert resultado.remote_id == VIDEO_ID
    # 2,5 MB en bloques de 1 MiB: tres PUT y un POST de sesion.
    puts = [p for p in capturadas if p.method == "PUT"]
    assert len(puts) == 3
    assert puts[0].headers["Content-Range"] == "bytes 0-1048575/2500000"
    assert puts[-1].headers["Content-Range"].endswith("/2500000")
    # El gasto se anota ANTES de cada peticion, con sus bytes.
    assert sum(bytes_ for _s, bytes_ in gasto) == TAMANO
    assert resultado.bytes_sent == TAMANO


def test_la_uri_de_sesion_se_guarda_en_privado_y_no_en_el_resultado(
    tmp_path: Path,
) -> None:
    ajustes = _ajustes(tmp_path)
    estado = {"offset": 0}
    adaptador, _ = _adaptador(ajustes, _handler_subida(estado))
    ctx = _contexto(tmp_path, ajustes)
    nombre = ctx.session_name("youtube_upload")

    # Al terminar, la sesion se borra; durante la subida existe con 0600.
    adaptador._open_session(ctx, {"Authorization": "Bearer x"})
    ruta = ctx.secrets.path_for(nombre)
    assert ruta.is_file()
    assert oct(ruta.stat().st_mode & 0o777) == "0o600"
    assert "session/abc123" in ruta.read_text()

    resultado = adaptador.start(ctx)
    assert not ctx.secrets.exists(nombre)
    assert "session" not in json.dumps(resultado.remote_refs)


def test_una_sesion_de_otro_archivo_no_se_reutiliza(tmp_path: Path) -> None:
    ajustes = _ajustes(tmp_path)
    estado = {"offset": 0}
    adaptador, _ = _adaptador(ajustes, _handler_subida(estado))
    ctx = _contexto(tmp_path, ajustes)
    ctx.secrets.put(
        ctx.session_name("youtube_upload"),
        {
            "uri": "https://www.googleapis.com/session/vieja",
            "video_size": 999,
            "video_sha256": "b" * 64,
            "created_at": "2026-09-01T00:00:00Z",
            "offset": 500,
        },
    )
    assert adaptador._load_session(ctx) is None


def test_un_timeout_en_el_ultimo_bloque_consulta_la_sesion(tmp_path: Path) -> None:
    """Perder la respuesta no es fallar: se pregunta cuanto tiene el servidor."""
    ajustes = _ajustes(tmp_path)
    estado = {"offset": 0, "fallado": False}
    base = _handler_subida(estado)

    def handler(request: httpx2.Request) -> httpx2.Response:
        rango = request.headers.get("Content-Range", "")
        if (
            request.method == "PUT"
            and rango.startswith("bytes 2097152-")
            and not estado["fallado"]
        ):
            estado["fallado"] = True
            # El servidor SI recibio el bloque, pero la respuesta se perdio.
            estado["offset"] = TAMANO
            raise httpx2.ReadTimeout("se perdio la respuesta", request=request)
        if rango.startswith("bytes */"):
            return httpx2.Response(200, json=_recurso(uploadStatus="uploaded"))
        return base(request)

    adaptador, capturadas = _adaptador(ajustes, handler)
    resultado = adaptador.start(_contexto(tmp_path, ajustes))

    assert resultado.state is DestinationState.WAITING_REMOTE
    assert resultado.remote_id == VIDEO_ID
    consultas = [
        p for p in capturadas if p.headers.get("Content-Range", "").startswith("bytes */")
    ]
    assert len(consultas) == 1, "se consulta la sesion una vez, no se reenvia el bloque"
    # Y no se abrio una segunda sesion.
    assert len([p for p in capturadas if p.method == "POST"]) == 1


def test_una_sesion_caducada_va_a_reconciliacion_sin_crear_otro_video(
    tmp_path: Path,
) -> None:
    ajustes = _ajustes(tmp_path)

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/upload/youtube/v3/videos"):
            return httpx2.Response(
                200, headers={"Location": "https://www.googleapis.com/session/abc123"}
            )
        return httpx2.Response(410, json={"error": {"message": "sesion caducada"}})

    adaptador, capturadas = _adaptador(ajustes, handler)
    resultado = adaptador.start(_contexto(tmp_path, ajustes))

    assert resultado.state is DestinationState.NEEDS_RECONCILIATION
    assert resultado.error is not None
    assert resultado.error.error_class is ErrorClass.AMBIGUOUS
    assert resultado.error.retryable is False
    assert len([p for p in capturadas if p.method == "POST"]) == 1


def test_una_subida_sin_id_no_adjudica_ningun_video(tmp_path: Path) -> None:
    ajustes = _ajustes(tmp_path)

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.method == "POST":
            return httpx2.Response(
                200, headers={"Location": "https://www.googleapis.com/session/abc123"}
            )
        return httpx2.Response(200, json={"kind": "youtube#video"})

    adaptador, _ = _adaptador(ajustes, handler)
    resultado = adaptador.start(_contexto(tmp_path, ajustes))
    assert resultado.state is DestinationState.NEEDS_RECONCILIATION
    assert "titulo" in resultado.error.message


# ---------------------------------------------------------------------------
# Verificacion del resultado
# ---------------------------------------------------------------------------


def _poll(tmp_path: Path, recurso: dict, *, visibility=Visibility.PRIVATE, row=None):
    ajustes = _ajustes(tmp_path)
    items = [recurso] if recurso else []
    adaptador, capturadas = _adaptador(
        ajustes, lambda r: httpx2.Response(200, json={"items": items})
    )
    fila = {"real_remote_id": VIDEO_ID}
    fila.update(row or {})
    ctx = _contexto(tmp_path, ajustes, row=fila, visibility=visibility)
    return adaptador.poll(ctx), capturadas


def test_privado_entregado_no_es_publicamente_visible(tmp_path: Path) -> None:
    resultado, _ = _poll(tmp_path, _recurso(privacy="private"))
    assert resultado.state is DestinationState.DELIVERED
    assert resultado.observed_visibility is Visibility.PRIVATE
    assert resultado.publicly_visible is False
    assert resultado.permalink is None


def test_publico_solicitado_y_privado_observado_no_se_declara_publicado(
    tmp_path: Path,
) -> None:
    resultado, _ = _poll(
        tmp_path, _recurso(privacy="private"), visibility=Visibility.PUBLIC
    )
    assert resultado.state is DestinationState.NEEDS_REVIEW
    assert resultado.publicly_visible is False
    assert "auditoria" in resultado.error.message


def test_un_video_rechazado_termina_en_fallo(tmp_path: Path) -> None:
    resultado, _ = _poll(tmp_path, _recurso(uploadStatus="rejected"))
    assert resultado.state is DestinationState.FAILED
    assert resultado.error.code == "upload_rejected"


def test_un_estado_desconocido_no_se_interpreta_como_exito(tmp_path: Path) -> None:
    resultado, _ = _poll(tmp_path, _recurso(uploadStatus="algo_nuevo"))
    assert resultado.state is DestinationState.NEEDS_REVIEW
    assert resultado.error.error_class is ErrorClass.AMBIGUOUS


def test_mientras_procesa_se_sigue_esperando(tmp_path: Path) -> None:
    resultado, _ = _poll(
        tmp_path,
        _recurso(uploadStatus="uploaded"),
        row={"dispatch_started_at": "2026-09-24T16:25:00Z"},
    )
    assert resultado.state is DestinationState.WAITING_REMOTE
    assert resultado.next_poll_in_s == 30.0


def test_si_se_agota_la_ventana_de_procesamiento_se_revisa(tmp_path: Path) -> None:
    resultado, _ = _poll(
        tmp_path,
        _recurso(uploadStatus="uploaded"),
        row={"dispatch_started_at": "2026-09-24T12:00:00Z"},
    )
    assert resultado.state is DestinationState.NEEDS_REVIEW
    assert resultado.error.code == "processing_window_expired"


def test_un_video_en_otro_canal_es_un_problema_de_permisos(tmp_path: Path) -> None:
    resultado, _ = _poll(tmp_path, _recurso(channel="UCotro"))
    assert resultado.state is DestinationState.NEEDS_REVIEW
    assert resultado.error.code == "channel_mismatch"


def test_sin_el_recurso_hay_reconciliacion_no_busqueda_por_titulo(
    tmp_path: Path,
) -> None:
    resultado, capturadas = _poll(tmp_path, None)
    assert resultado.state is DestinationState.NEEDS_RECONCILIATION
    # La consulta fue por ID, nunca por titulo o fecha.
    assert capturadas[0].url.params["id"] == VIDEO_ID
    assert "q" not in capturadas[0].url.params


def test_sin_identificador_no_se_consulta_nada(tmp_path: Path) -> None:
    ajustes = _ajustes(tmp_path)
    adaptador, capturadas = _adaptador(ajustes, lambda r: httpx2.Response(200, json={}))
    with pytest.raises(ReconciliationRequiredError, match="otra persona"):
        adaptador.poll(_contexto(tmp_path, ajustes))
    assert capturadas == []


# ---------------------------------------------------------------------------
# Credenciales
# ---------------------------------------------------------------------------


def test_un_token_caducado_se_renueva_y_se_guarda(tmp_path: Path) -> None:
    ajustes = _ajustes(tmp_path)
    almacen = _almacen(tmp_path, caducado=True)

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path.endswith("/token")
        return httpx2.Response(
            200, json={"access_token": "token-nuevo", "expires_in": 3600}
        )

    cliente = PublishHttpClient(
        timeout_s=5.0,
        allowed_hosts=YouTubeAdapter.allowed_hosts(ajustes),
        client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )
    renovado = ensure_token(almacen, cliente, settings=ajustes, now=AHORA)
    assert renovado.access_token == "token-nuevo"
    # El refresh token anterior se conserva aunque la respuesta no lo repita.
    assert renovado.refresh_token == "refresco-de-prueba"
    assert almacen.get(TOKEN_FILE)["access_token"] == "token-nuevo"


def test_invalid_grant_exige_volver_a_autorizar(tmp_path: Path) -> None:
    ajustes = _ajustes(tmp_path)
    almacen = _almacen(tmp_path, caducado=True)
    cliente = PublishHttpClient(
        timeout_s=5.0,
        allowed_hosts=YouTubeAdapter.allowed_hosts(ajustes),
        client=httpx2.Client(
            transport=httpx2.MockTransport(
                lambda r: httpx2.Response(400, json={"error": "invalid_grant"})
            )
        ),
    )
    with pytest.raises(AuthRequiredError, match="auth youtube"):
        ensure_token(almacen, cliente, settings=ajustes, now=AHORA)


def test_no_se_llama_a_hosts_no_configurados(tmp_path: Path) -> None:
    ajustes = _ajustes(tmp_path)
    adaptador, capturadas = _adaptador(ajustes, lambda r: httpx2.Response(200))
    from viralgen.publish.transport import PublishTransportError

    with pytest.raises(PublishTransportError, match="Destino no permitido"):
        adaptador.client.request(
            "GET", "https://evil.example.com/robo", mutating=False
        )
    assert capturadas == []
