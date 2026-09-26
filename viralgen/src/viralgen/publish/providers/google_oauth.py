"""OAuth de aplicacion instalada para YouTube: loopback + PKCE.

Por que asi y no de otra forma:

* **Del propietario del canal.** Subir a un canal necesita la autorizacion de
  quien lo posee. Una clave de API o una cuenta de servicio no la sustituyen:
  identifican al proyecto, no al canal.
* **Loopback, no OOB.** El flujo "out of band" que copiaba un codigo a mano
  esta retirado. Aqui el navegador devuelve el codigo a un servidor local
  efimero en 127.0.0.1.
* **PKCE.** Un cliente instalado no puede guardar un secreto de verdad; el
  verificador de un solo uso es lo que impide que alguien reutilice el codigo.
* **En una VPS sin navegador no se conecta.** Se conecta desde un equipo con
  navegador y se copia el archivo de token, o se abre un tunel al puerto
  local. No se inventa un flujo intermedio.

VERIFICACION PENDIENTE: los nombres exactos de parametros y los scopes
efectivos no se han podido contrastar con la documentacion de Google en esta
entrega (el entorno respondio 403 a `developers.google.com`). Por eso
`yt_oauth_installed_app` bloquea el modo real hasta comprobarlos. El codigo
implementa OAuth 2.0 con PKCE tal y como lo define el estandar.
"""

from __future__ import annotations

import base64
import hashlib
import secrets as secretsmod
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ...errors import ConfigError
from ..errors import AuthRequiredError
from ..schemas import ErrorClass
from ..secrets import SecretStore
from ..transport import PublishHttpClient, PublishTransportError

#: Nombre del archivo privado con el token del canal.
TOKEN_FILE = "youtube_token.json"

#: Scopes PREVISTOS: subir y leer lo necesario para verificar canal y
#: resultado. No se piden permisos de gestion ni de otros productos.
SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
)

#: Margen para renovar antes de que caduque de verdad.
REFRESH_MARGIN_S = 120


@dataclass(frozen=True)
class PkcePair:
    verifier: str
    challenge: str
    method: str = "S256"


def new_pkce() -> PkcePair:
    """Verificador de un solo uso y su reto derivado."""
    verificador = base64.urlsafe_b64encode(secretsmod.token_bytes(48)).decode().rstrip("=")
    digest = hashlib.sha256(verificador.encode("ascii")).digest()
    reto = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return PkcePair(verifier=verificador, challenge=reto)


def authorization_url(
    *,
    auth_endpoint: str,
    client_id: str,
    redirect_uri: str,
    pkce: PkcePair,
    state: str,
    scopes: tuple[str, ...] = SCOPES,
) -> str:
    """URL que el operador abre en su navegador.

    No lleva secretos: el `client_id` de una app instalada es publico y el
    reto PKCE no permite deducir el verificador.
    """
    parametros = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "code_challenge": pkce.challenge,
        "code_challenge_method": pkce.method,
        "state": state,
        # Sin esto no llega refresh token y habria que reautorizar cada hora.
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{auth_endpoint}?{urllib.parse.urlencode(parametros)}"


@dataclass
class TokenBundle:
    """Lo que se guarda en el archivo privado. Nunca sale de ahi."""

    access_token: str
    refresh_token: str | None
    expires_at: datetime
    scopes: tuple[str, ...]
    client_id: str
    token_type: str = "Bearer"

    def expired(self, now: datetime, *, margin_s: int = REFRESH_MARGIN_S) -> bool:
        return self.expires_at - timedelta(seconds=margin_s) <= now

    def to_payload(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at.astimezone(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "scopes": list(self.scopes),
            "client_id": self.client_id,
            "token_type": self.token_type,
        }

    @classmethod
    def from_payload(cls, datos: dict[str, Any]) -> "TokenBundle":
        return cls(
            access_token=datos["access_token"],
            refresh_token=datos.get("refresh_token"),
            expires_at=datetime.fromisoformat(
                str(datos["expires_at"]).replace("Z", "+00:00")
            ),
            scopes=tuple(datos.get("scopes", SCOPES)),
            client_id=datos["client_id"],
            token_type=datos.get("token_type", "Bearer"),
        )

    def authorization_header(self) -> dict[str, str]:
        """La unica forma de construir la cabecera. No se registra jamas."""
        return {"Authorization": f"{self.token_type} {self.access_token}"}


def _token_request(
    client: PublishHttpClient, *, token_endpoint: str, datos: dict[str, str], now: datetime
) -> TokenBundle:
    """Pide o renueva un token. El cuerpo lleva secretos: no se registra."""
    cuerpo = urllib.parse.urlencode(datos).encode("ascii")
    try:
        respuesta = client.request(
            "POST",
            token_endpoint,
            headers={"content-type": "application/x-www-form-urlencoded"},
            content=cuerpo,
            # Obtener un token no publica nada: repetirlo no duplica contenido.
            mutating=False,
        )
    except PublishTransportError as exc:
        if exc.error_class in (ErrorClass.PERMISSION, ErrorClass.AUTH):
            raise AuthRequiredError(
                "La autorizacion del canal ya no vale (consentimiento retirado, "
                "credencial revocada o `invalid_grant`). Vuelve a ejecutar "
                "`viralgen auth youtube`.",
                details={"http_status": exc.http_status},
            ) from exc
        raise
    payload = respuesta.json()
    if "access_token" not in payload:
        raise AuthRequiredError(
            "La respuesta del endpoint de token no trae `access_token`.",
            details={"keys": sorted(payload)},
        )
    caduca = now + timedelta(seconds=int(payload.get("expires_in", 3600)))
    ambito = payload.get("scope")
    return TokenBundle(
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token") or datos.get("refresh_token"),
        expires_at=caduca.replace(microsecond=0),
        scopes=tuple(ambito.split()) if isinstance(ambito, str) else SCOPES,
        client_id=datos["client_id"],
        token_type=payload.get("token_type", "Bearer"),
    )


def exchange_code(
    client: PublishHttpClient,
    *,
    token_endpoint: str,
    client_id: str,
    client_secret: str | None,
    code: str,
    redirect_uri: str,
    verifier: str,
    now: datetime,
) -> TokenBundle:
    datos = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    }
    if client_secret:
        datos["client_secret"] = client_secret
    return _token_request(client, token_endpoint=token_endpoint, datos=datos, now=now)


def refresh_token(
    client: PublishHttpClient,
    *,
    token_endpoint: str,
    bundle: TokenBundle,
    client_secret: str | None,
    now: datetime,
) -> TokenBundle:
    """Renueva sin pedir consentimiento. Si hace falta, devuelve auth_required."""
    if not bundle.refresh_token:
        raise AuthRequiredError(
            "No hay refresh token guardado: hay que volver a autorizar el canal.",
        )
    datos = {
        "grant_type": "refresh_token",
        "refresh_token": bundle.refresh_token,
        "client_id": bundle.client_id,
    }
    if client_secret:
        datos["client_secret"] = client_secret
    renovado = _token_request(
        client, token_endpoint=token_endpoint, datos=datos, now=now
    )
    # Google puede no devolver el refresh token al renovar: se conserva el que
    # ya habia en vez de perderlo.
    if not renovado.refresh_token:
        renovado = TokenBundle(
            access_token=renovado.access_token,
            refresh_token=bundle.refresh_token,
            expires_at=renovado.expires_at,
            scopes=renovado.scopes,
            client_id=renovado.client_id,
            token_type=renovado.token_type,
        )
    return renovado


def load_token(store: SecretStore) -> TokenBundle | None:
    datos = store.get(TOKEN_FILE)
    return TokenBundle.from_payload(datos) if datos else None


def save_token(store: SecretStore, bundle: TokenBundle) -> None:
    """Escritura atomica con permisos 0600."""
    store.put(TOKEN_FILE, bundle.to_payload())


def ensure_token(
    store: SecretStore,
    client: PublishHttpClient,
    *,
    settings: Any,
    now: datetime,
) -> TokenBundle:
    """Token utilizable, renovandolo si hace falta y guardandolo enseguida."""
    bundle = load_token(store)
    if bundle is None:
        raise AuthRequiredError(
            "No hay ningun canal conectado. Ejecuta `viralgen auth youtube` "
            "desde un equipo con navegador.",
            details={"expected_file": str(store.path_for(TOKEN_FILE))},
        )
    if not bundle.expired(now):
        return bundle
    secreto = getattr(settings, "youtube_client_secret", None)
    renovado = refresh_token(
        client,
        token_endpoint=str(settings.youtube_oauth_token_url),
        bundle=bundle,
        client_secret=secreto.get_secret_value() if secreto else None,
        now=now,
    )
    save_token(store, renovado)
    return renovado


class LoopbackReceiver:
    """Servidor local efimero que recibe la respuesta del navegador.

    Se separa del flujo a proposito: asi la logica local -PKCE, `state`,
    validacion del callback- se puede probar con un receptor doble, y este
    receptor se puede probar por si mismo contra 127.0.0.1 sin salir de la
    maquina. Lo unico que sigue necesitando a una persona es aceptar el
    consentimiento en la pantalla de Google.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        import http.server

        recibido: dict[str, str] = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - nombre de la libreria
                consulta = urllib.parse.urlparse(self.path).query
                parametros = urllib.parse.parse_qs(consulta)
                recibido.update({k: v[0] for k, v in parametros.items()})
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    "Autorizacion recibida. Puedes cerrar esta pestana.".encode("utf-8")
                )

            def log_message(self, *_args: Any) -> None:
                """Silencio: la URL de vuelta lleva el codigo de autorizacion."""

        self._recibido = recibido
        self._servidor = http.server.HTTPServer((host, port), Handler)
        self.port = self._servidor.server_address[1]

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def wait(self, timeout_s: float) -> dict[str, str]:
        """Espera una peticion. Devuelve los parametros, o {} si se agota."""
        import threading

        hilo = threading.Thread(target=self._servidor.handle_request, daemon=True)
        hilo.start()
        limite = time.monotonic() + timeout_s
        while hilo.is_alive() and time.monotonic() < limite:
            hilo.join(timeout=0.2)
        return dict(self._recibido)

    def close(self) -> None:
        self._servidor.server_close()


def validate_callback(params: dict[str, str], *, expected_state: str) -> str:
    """Comprueba la respuesta del navegador ANTES de canjear nada.

    Tres rechazos, y ninguno llega al endpoint de token:

    * sin parametros: se agoto la espera;
    * `state` distinto: la respuesta no corresponde a esta solicitud;
    * sin `code`: hubo denegacion o error.

    El `state` se compara con `compare_digest` para no filtrar por tiempos.
    """
    if not params:
        raise AuthRequiredError(
            "No llego ninguna respuesta del navegador: se agoto la espera. No se "
            "ha canjeado ni guardado nada."
        )
    recibido = params.get("state", "")
    if not secretsmod.compare_digest(recibido, expected_state):
        raise AuthRequiredError(
            "La respuesta del navegador no corresponde a esta solicitud "
            "(`state` distinto). No se ha canjeado ni guardado nada.",
            details={"state_present": bool(recibido)},
        )
    codigo = params.get("code")
    if not codigo:
        motivo = params.get("error") or "sin `code` y sin `error`"
        raise AuthRequiredError(
            f"No llego ningun codigo de autorizacion: {motivo}. No se ha "
            "canjeado ni guardado nada.",
            details={"error": params.get("error")},
        )
    return codigo


def run_loopback_flow(
    client: PublishHttpClient,
    store: SecretStore,
    *,
    settings: Any,
    now: datetime,
    open_browser: bool = False,
    timeout_s: float = 300.0,
    announce=None,
    receiver: Any = None,
) -> TokenBundle:
    """Flujo interactivo completo. Necesita un navegador con una persona detras.

    El consentimiento no se puede automatizar y queda como prueba EXTERNA
    pendiente. Todo lo demas -reto PKCE, correspondencia de `state`, rechazo de
    un callback invalido antes del canje, denegacion, tiempo agotado y el
    contenido exacto de la peticion de token- se prueba con transporte y
    receptor simulados.
    """
    import webbrowser

    if not settings.youtube_client_id:
        raise ConfigError(
            "Falta YOUTUBE_CLIENT_ID. Crea unas credenciales de aplicacion "
            "instalada en tu proyecto de Google Cloud."
        )

    estado = secretsmod.token_urlsafe(16)
    pkce = new_pkce()
    receptor = receiver if receiver is not None else LoopbackReceiver()
    redirect_uri = receptor.redirect_uri
    url = authorization_url(
        auth_endpoint=str(settings.youtube_oauth_auth_url),
        client_id=str(settings.youtube_client_id),
        redirect_uri=redirect_uri,
        pkce=pkce,
        state=estado,
    )
    if announce is not None:
        announce(url)
    if open_browser:
        webbrowser.open(url)

    try:
        recibido = receptor.wait(timeout_s)
    finally:
        cerrar = getattr(receptor, "close", None)
        if callable(cerrar):
            cerrar()

    codigo = validate_callback(recibido, expected_state=estado)

    secreto = getattr(settings, "youtube_client_secret", None)
    bundle = exchange_code(
        client,
        token_endpoint=str(settings.youtube_oauth_token_url),
        client_id=str(settings.youtube_client_id),
        client_secret=secreto.get_secret_value() if secreto else None,
        code=codigo,
        redirect_uri=redirect_uri,
        verifier=pkce.verifier,
        now=now,
    )
    save_token(store, bundle)
    return bundle


def describe_scopes() -> dict[str, Any]:
    return {
        "scopes": list(SCOPES),
        "flow": "installed_app_loopback_pkce",
        "oob_retired": True,
        "note": (
            "Scopes PREVISTOS. Los efectivos deben comprobarse contra la "
            "documentacion antes de habilitar el modo real (yt_oauth_installed_app)."
        ),
    }


