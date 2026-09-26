"""Logica local de `auth youtube`, con transporte y callback simulados.

El consentimiento de Google necesita a una persona y queda como **integración
externa pendiente**. Lo que si se puede probar aqui, y se prueba, es todo lo que
rodea a ese clic: el reto PKCE, la correspondencia del `state`, el rechazo de un
callback invalido ANTES de canjear nada, la denegacion, el tiempo agotado y el
contenido exacto de la peticion de token.

El receptor de loopback tambien se prueba de verdad: se levanta en 127.0.0.1 y
se le hace una peticion HTTP real. No sale nada de la maquina.
"""

from __future__ import annotations

import base64
import hashlib
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx2
import pytest

from viralgen.config import Settings
from viralgen.publish.errors import AuthRequiredError
from viralgen.publish.providers.google_oauth import (
    SCOPES,
    TOKEN_FILE,
    LoopbackReceiver,
    authorization_url,
    new_pkce,
    run_loopback_flow,
    validate_callback,
)
from viralgen.publish.providers.youtube import YouTubeAdapter
from viralgen.publish.secrets import SecretStore
from viralgen.publish.transport import PublishHttpClient

UTC = timezone.utc
AHORA = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
CLIENTE = "cliente.apps.googleusercontent.com"


def _ajustes(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        log_level="ERROR",
        youtube_client_id=CLIENTE,
    )


class ReceptorFalso:
    """Receptor de callback doble: devuelve lo que le digan, sin abrir puertos."""

    def __init__(self, params: dict[str, str], *, puerto: int = 54321) -> None:
        self.params = params
        self.puerto = puerto
        self.cerrado = False
        self.esperas = 0

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.puerto}/"

    def wait(self, timeout_s: float) -> dict[str, str]:
        self.esperas += 1
        return dict(self.params)

    def close(self) -> None:
        self.cerrado = True


class _ReceptorQueResponde:
    """Receptor doble que contesta con el `state` que acaba de anunciarse.

    Asi se prueban los caminos en los que el `state` SI corresponde, sin
    tener que adivinarlo: se lee de la URL que el flujo acaba de anunciar.
    """

    def __init__(
        self,
        urls: list[str],
        *,
        params: dict[str, str] | None = None,
        puerto: int = 54321,
    ) -> None:
        self.urls = urls
        self.params = {"code": "codigo-del-navegador"} if params is None else params
        self.puerto = puerto

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.puerto}/"

    def wait(self, timeout_s: float) -> dict[str, str]:
        consulta = urllib.parse.urlparse(self.urls[-1]).query
        estado = urllib.parse.parse_qs(consulta)["state"][0]
        return {**self.params, "state": estado}

    def close(self) -> None:
        pass


def _flujo(tmp_path: Path, params: dict[str, str], *, respuesta=None):
    """Ejecuta el flujo con receptor doble y devuelve (resultado, peticiones)."""
    peticiones: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        peticiones.append(request)
        return respuesta or httpx2.Response(
            200,
            json={
                "access_token": "token-nuevo",
                "refresh_token": "refresco",
                "expires_in": 3600,
                "scope": " ".join(SCOPES),
                "token_type": "Bearer",
            },
        )

    ajustes = _ajustes(tmp_path)
    cliente = PublishHttpClient(
        timeout_s=5.0,
        allowed_hosts=YouTubeAdapter.allowed_hosts(ajustes),
        client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )
    almacen = SecretStore(tmp_path / "secretos")
    almacen.ensure()
    receptor = ReceptorFalso(params)
    urls: list[str] = []
    resultado = run_loopback_flow(
        cliente,
        almacen,
        settings=ajustes,
        now=AHORA,
        timeout_s=1.0,
        announce=urls.append,
        receiver=receptor,
    )
    return resultado, peticiones, urls, almacen, receptor


# ---------------------------------------------------------------------------
# PKCE
# ---------------------------------------------------------------------------


def test_el_reto_pkce_es_s256_del_verificador() -> None:
    """Se recalcula el reto: no basta con que el codigo diga 'S256'."""
    pareja = new_pkce()
    esperado = (
        base64.urlsafe_b64encode(hashlib.sha256(pareja.verifier.encode("ascii")).digest())
        .decode()
        .rstrip("=")
    )
    assert pareja.challenge == esperado
    assert pareja.method == "S256"
    # Longitud del verificador dentro del rango que exige el estandar.
    assert 43 <= len(pareja.verifier) <= 128
    assert "=" not in pareja.challenge  # base64url sin relleno


def test_dos_flujos_no_comparten_verificador() -> None:
    assert new_pkce().verifier != new_pkce().verifier


def test_la_url_de_autorizacion_lleva_el_reto_y_no_el_verificador() -> None:
    pareja = new_pkce()
    url = authorization_url(
        auth_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
        client_id=CLIENTE,
        redirect_uri="http://127.0.0.1:5000/",
        pkce=pareja,
        state="estado-1",
    )
    parametros = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert parametros["code_challenge"] == [pareja.challenge]
    assert parametros["code_challenge_method"] == ["S256"]
    assert parametros["response_type"] == ["code"]
    assert parametros["access_type"] == ["offline"]
    assert parametros["redirect_uri"] == ["http://127.0.0.1:5000/"]
    assert parametros["state"] == ["estado-1"]
    assert parametros["scope"] == [" ".join(SCOPES)]
    # El verificador NO viaja en la URL: solo su reto.
    assert pareja.verifier not in url


# ---------------------------------------------------------------------------
# Validacion del callback, antes de canjear nada
# ---------------------------------------------------------------------------


def test_un_state_distinto_se_rechaza_antes_del_canje(tmp_path: Path) -> None:
    with pytest.raises(AuthRequiredError, match="state"):
        _flujo(tmp_path, {"code": "codigo", "state": "el-de-otra-solicitud"})


def test_una_denegacion_no_canjea_nada(tmp_path: Path) -> None:
    """El usuario dice no: no hay `code` y no se llama al endpoint de token."""
    ajustes = _ajustes(tmp_path)
    peticiones: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:  # pragma: no cover
        peticiones.append(request)
        return httpx2.Response(200, json={})

    cliente = PublishHttpClient(
        timeout_s=5.0,
        allowed_hosts=YouTubeAdapter.allowed_hosts(ajustes),
        client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )
    almacen = SecretStore(tmp_path / "secretos")
    almacen.ensure()
    urls: list[str] = []
    with pytest.raises(AuthRequiredError, match="access_denied"):
        run_loopback_flow(
            cliente, almacen, settings=ajustes, now=AHORA, timeout_s=1.0,
            announce=urls.append,
            receiver=_ReceptorQueResponde(urls, params={"error": "access_denied"}),
        )
    assert peticiones == []
    assert not almacen.exists(TOKEN_FILE)


def test_el_tiempo_agotado_no_canjea_nada(tmp_path: Path) -> None:
    """Sin respuesta del navegador no hay nada que canjear."""
    with pytest.raises(AuthRequiredError, match="se agoto la espera"):
        _flujo(tmp_path, {})


def test_un_callback_sin_code_ni_error_tambien_se_rechaza() -> None:
    with pytest.raises(AuthRequiredError, match="sin `code`"):
        validate_callback({"state": "x", "scope": "algo"}, expected_state="x")


def test_el_state_se_compara_completo() -> None:
    """Un prefijo correcto no vale."""
    with pytest.raises(AuthRequiredError):
        validate_callback({"state": "abc", "code": "c"}, expected_state="abcdef")


# ---------------------------------------------------------------------------
# Canje correcto
# ---------------------------------------------------------------------------


def test_el_canje_envia_el_verificador_y_la_uri_de_redireccion(tmp_path: Path) -> None:
    """Lo que se manda al endpoint de token, comprobado campo a campo."""
    ajustes = _ajustes(tmp_path)
    peticiones: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        peticiones.append(request)
        return httpx2.Response(
            200,
            json={
                "access_token": "token-nuevo",
                "refresh_token": "refresco",
                "expires_in": 3600,
                "scope": " ".join(SCOPES),
            },
        )

    cliente = PublishHttpClient(
        timeout_s=5.0,
        allowed_hosts=YouTubeAdapter.allowed_hosts(ajustes),
        client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )
    almacen = SecretStore(tmp_path / "secretos")
    almacen.ensure()

    urls: list[str] = []
    receptor = _ReceptorQueResponde(urls)
    bundle = run_loopback_flow(
        cliente, almacen, settings=ajustes, now=AHORA, timeout_s=1.0,
        announce=urls.append, receiver=receptor,
    )

    assert len(peticiones) == 1
    cuerpo = urllib.parse.parse_qs(peticiones[0].content.decode("ascii"))
    assert cuerpo["grant_type"] == ["authorization_code"]
    assert cuerpo["code"] == ["codigo-del-navegador"]
    assert cuerpo["redirect_uri"] == [receptor.redirect_uri]
    assert cuerpo["client_id"] == [CLIENTE]
    # El verificador enviado corresponde al reto anunciado: PKCE de punta a punta.
    reto = urllib.parse.parse_qs(urllib.parse.urlparse(urls[0]).query)["code_challenge"][0]
    verificador = cuerpo["code_verifier"][0]
    esperado = (
        base64.urlsafe_b64encode(hashlib.sha256(verificador.encode("ascii")).digest())
        .decode()
        .rstrip("=")
    )
    assert esperado == reto
    # Y el token queda guardado con permisos privados.
    assert bundle.access_token == "token-nuevo"
    ruta = almacen.path_for(TOKEN_FILE)
    assert oct(ruta.stat().st_mode & 0o777) == "0o600"
    assert bundle.expires_at == AHORA + timedelta(hours=1)


def test_el_secreto_de_cliente_se_envia_solo_si_existe(tmp_path: Path) -> None:
    ajustes = Settings(
        _env_file=None, data_dir=tmp_path, log_level="ERROR",
        youtube_client_id=CLIENTE, youtube_client_secret="secreto-del-cliente",
    )
    peticiones: list[httpx2.Request] = []
    cliente = PublishHttpClient(
        timeout_s=5.0,
        allowed_hosts=YouTubeAdapter.allowed_hosts(ajustes),
        client=httpx2.Client(
            transport=httpx2.MockTransport(
                lambda r: (
                    peticiones.append(r),
                    httpx2.Response(200, json={"access_token": "t", "expires_in": 60}),
                )[1]
            )
        ),
    )
    almacen = SecretStore(tmp_path / "secretos")
    almacen.ensure()
    urls: list[str] = []
    run_loopback_flow(
        cliente, almacen, settings=ajustes, now=AHORA, timeout_s=1.0,
        announce=urls.append, receiver=_ReceptorQueResponde(urls),
    )
    cuerpo = urllib.parse.parse_qs(peticiones[0].content.decode("ascii"))
    assert cuerpo["client_secret"] == ["secreto-del-cliente"]
    # Y el secreto no aparece en la URL que se le muestra al operador.
    assert "secreto-del-cliente" not in urls[0]


# ---------------------------------------------------------------------------
# El receptor de loopback, de verdad
# ---------------------------------------------------------------------------


def test_el_receptor_de_loopback_recibe_los_parametros_reales() -> None:
    """Se levanta en 127.0.0.1 y se le hace una peticion HTTP real.

    No sale nada de la maquina: es el mismo bucle que usara el navegador.
    """
    receptor = LoopbackReceiver()
    recibido: dict[str, str] = {}

    def esperar() -> None:
        recibido.update(receptor.wait(timeout_s=5.0))

    hilo = threading.Thread(target=esperar)
    hilo.start()
    try:
        destino = receptor.redirect_uri + "?code=abc123&state=xyz&scope=uno+dos"
        with urllib.request.urlopen(destino, timeout=5) as respuesta:
            assert respuesta.status == 200
            assert b"Autorizacion recibida" in respuesta.read()
    finally:
        hilo.join(timeout=5)
        receptor.close()

    assert recibido["code"] == "abc123"
    assert recibido["state"] == "xyz"


def test_el_receptor_devuelve_vacio_si_nadie_llama() -> None:
    receptor = LoopbackReceiver()
    try:
        assert receptor.wait(timeout_s=0.3) == {}
    finally:
        receptor.close()


def test_la_uri_de_redireccion_es_de_loopback() -> None:
    receptor = LoopbackReceiver()
    try:
        assert receptor.redirect_uri.startswith("http://127.0.0.1:")
        assert receptor.port > 0
    finally:
        receptor.close()


# ---------------------------------------------------------------------------
# Lo que sigue pendiente
# ---------------------------------------------------------------------------


def test_el_consentimiento_sigue_siendo_integracion_externa_pendiente() -> None:
    """Esto no es una prueba de integracion con Google, y se dice.

    Lo que falta es una persona aceptando el consentimiento en la pantalla de
    Google y un canje contra su endpoint real. Los dobles de aqui no lo
    sustituyen, y la entrada `yt_oauth_installed_app` sigue bloqueando el modo
    real.
    """
    from viralgen.publish import verification

    entrada = verification.find("yt_oauth_installed_app")
    assert entrada.blocking is True
    assert entrada.evidence is None
