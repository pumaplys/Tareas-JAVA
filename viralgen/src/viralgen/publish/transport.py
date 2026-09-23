"""Cliente HTTP del publicador: clasificado, acotado y sin reintentos ciegos.

Tres decisiones que valen mas que el codigo que las implementa:

1. **Una operacion mutante NO se reintenta automaticamente.** Crear un video,
   crear un contenedor o pedir una publicacion son operaciones que pueden
   haberse completado aunque no veamos la respuesta. Repetirlas a ciegas es
   como se producen los duplicados.
2. **Un fallo de transporte en una operacion mutante es AMBIGUO, no un fallo.**
   Un timeout tras enviar bytes no demuestra nada. El adaptador tendra que
   consultar la sesion o el contenedor existente antes de decidir.
3. **Los destinos estan restringidos y no se siguen redirecciones.** Una
   redireccion arrastraria la cabecera `Authorization` a otro host; y en el
   protocolo reanudable un 308 no es una redireccion ordinaria, es "falta
   subida". Seguirla automaticamente romperia las dos cosas.

Nada de lo que pasa por aqui se registra con cabeceras ni cuerpos completos:
en el log van metodo, host, ruta y codigo.
"""

from __future__ import annotations

import json as jsonlib
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

import httpx2

from ..errors import ExitCode, ViralgenError
from ..logging_setup import get_logger
from .schemas import ErrorClass

logger = get_logger("publish.http")

#: Tope por respuesta. Las respuestas de estas APIs son JSON pequeno.
MAX_RESPONSE_BYTES = 2 * 1024 * 1024

BACKOFF_BASE_S = 1.0
BACKOFF_MAX_S = 30.0

#: Cabeceras que se pueden registrar o guardar. El resto no se toca.
SAFE_HEADERS = frozenset(
    {
        "content-type",
        "content-length",
        "range",
        "retry-after",
        "x-guploader-uploadid",
        "x-request-id",
        "x-fb-request-id",
        "x-fb-trace-id",
        "date",
    }
)


class PublishTransportError(ViralgenError):
    """Fallo de una llamada del publicador, ya clasificado."""

    exit_code = ExitCode.PROVIDER
    code = "publish_transport_error"

    def __init__(
        self,
        message: str,
        *,
        error_class: ErrorClass,
        retryable: bool,
        http_status: int | None = None,
        request_id: str | None = None,
        details: dict | None = None,
    ) -> None:
        super().__init__(message, details={**(details or {}), "http_status": http_status})
        self.error_class = error_class
        self.retryable = retryable
        self.http_status = http_status
        self.request_id = request_id

    @property
    def ambiguous(self) -> bool:
        return self.error_class is ErrorClass.AMBIGUOUS


@dataclass(frozen=True)
class HttpOutcome:
    """Respuesta ya leida y acotada."""

    status: int
    headers: dict[str, str]
    body: bytes
    request_id: str | None = None
    elapsed_ms: int = 0

    def json(self) -> dict[str, Any]:
        if not self.body:
            return {}
        try:
            datos = jsonlib.loads(self.body)
        except ValueError as exc:
            raise PublishTransportError(
                f"La respuesta no es JSON valido ({self.status}).",
                error_class=ErrorClass.INVALID_PAYLOAD,
                retryable=False,
                http_status=self.status,
            ) from exc
        return datos if isinstance(datos, dict) else {"data": datos}


def safe_headers(headers: Any, *, capture: Iterable[str] = ()) -> dict[str, str]:
    """Solo las cabeceras que no son material sensible.

    `capture` anade cabeceras que el adaptador necesita de verdad y que NO son
    publicables -el caso es `Location`, que en una subida reanudable es la URI
    de sesion-. Nunca se registran: el log solo lleva metodo, host, ruta y
    codigo, y quien las pide es responsable de guardarlas en el almacen
    privado.
    """
    permitidas = SAFE_HEADERS | {nombre.lower() for nombre in capture}
    return {
        clave.lower(): valor
        for clave, valor in dict(headers).items()
        if clave.lower() in permitidas
    }


def _mensaje_de_error(cuerpo: bytes) -> str:
    """Resumen corto del error remoto, sin volcar el payload entero."""
    try:
        datos = jsonlib.loads(cuerpo)
    except ValueError:
        return cuerpo[:200].decode("utf-8", "replace")
    if isinstance(datos, dict):
        error = datos.get("error")
        if isinstance(error, dict):
            partes = [
                str(error.get(clave))
                for clave in ("status", "type", "code", "message", "error_user_msg")
                if error.get(clave)
            ]
            if partes:
                return " | ".join(partes)[:300]
        if isinstance(error, str):
            descripcion = datos.get("error_description")
            return f"{error}: {descripcion}"[:300] if descripcion else error[:300]
    return str(datos)[:200]


def classify(status: int, cuerpo: bytes) -> tuple[ErrorClass, bool]:
    """Clasifica una respuesta de error. El codigo solo no siempre basta.

    Nota honesta: las cadenas exactas con las que cada plataforma distingue
    "sin permiso" de "sin cuota" dentro de un 403 no se han podido contrastar
    con su documentacion en esta entrega. Por eso, ante la duda, un 403 se
    trata como falta de PERMISO, que exige accion humana, en vez de como algo
    que se arregla esperando.
    """
    texto = cuerpo[:2000].decode("utf-8", "replace").lower()
    if status == 401:
        # Puede permitir renovar la credencial; el adaptador decide.
        return ErrorClass.AUTH, False
    if status == 403:
        if "quota" in texto or "rate" in texto or "limit" in texto:
            return ErrorClass.QUOTA, False
        return ErrorClass.PERMISSION, False
    if status == 429:
        return ErrorClass.QUOTA, True
    if status in (400, 404, 405, 409, 410, 415, 422):
        if "invalid_grant" in texto:
            return ErrorClass.PERMISSION, False
        return ErrorClass.INVALID_PAYLOAD, False
    if status >= 500:
        return ErrorClass.TRANSIENT, True
    return ErrorClass.TRANSIENT, True


def retry_after_seconds(headers: dict[str, str]) -> float | None:
    valor = headers.get("retry-after")
    if not valor:
        return None
    try:
        return max(0.0, float(valor))
    except ValueError:
        return None


@dataclass
class PublishHttpClient:
    """Cliente comun de los adaptadores reales.

    `allowed_hosts` restringe a donde se puede llamar: una URL que no este en
    la lista no se envia, ni aunque venga de una respuesta remota. Asi una
    redireccion o un campo manipulado no arrastran la credencial a otro sitio.
    """

    timeout_s: float
    allowed_hosts: frozenset[str]
    client: Any = None
    sleep: Callable[[float], None] = time.sleep
    max_response_bytes: int = MAX_RESPONSE_BYTES
    _rng: random.Random = field(default_factory=lambda: random.Random(7))

    def __post_init__(self) -> None:
        if self.client is None:
            self.client = httpx2.Client(
                timeout=self.timeout_s,
                # Sin reintentos en el transporte y sin seguir redirecciones:
                # las dos cosas las decide el adaptador.
                transport=httpx2.HTTPTransport(retries=0),
                follow_redirects=False,
            )

    # -- Comprobaciones ----------------------------------------------------

    def check_host(self, url: str) -> str:
        host = (urlparse(url).hostname or "").lower()
        if host not in self.allowed_hosts:
            raise PublishTransportError(
                f"Destino no permitido: {host or '(sin host)'}. Hosts "
                f"configurados: {sorted(self.allowed_hosts)}.",
                error_class=ErrorClass.LOCAL,
                retryable=False,
                details={"host": host},
            )
        return host

    # -- Llamada -----------------------------------------------------------

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json: Any = None,
        content: bytes | Iterable[bytes] | None = None,
        mutating: bool,
        expected: Iterable[int] | None = None,
        capture_headers: Iterable[str] = (),
    ) -> HttpOutcome:
        """Una llamada, sin reintentos. Devuelve la respuesta ya leida.

        `mutating=True` cambia el modo de fallar: un problema de transporte no
        se reporta como fallo, sino como AMBIGUO, porque la operacion pudo
        completarse al otro lado.
        """
        host = self.check_host(url)
        ruta = urlparse(url).path
        comenzado = time.monotonic()
        try:
            with self.client.stream(
                method,
                url,
                headers=headers or {},
                params=params,
                json=json,
                content=content,
            ) as respuesta:
                trozos: list[bytes] = []
                total = 0
                for trozo in respuesta.iter_bytes():
                    total += len(trozo)
                    if total > self.max_response_bytes:
                        raise PublishTransportError(
                            f"La respuesta supera {self.max_response_bytes} bytes.",
                            error_class=ErrorClass.INVALID_PAYLOAD,
                            retryable=False,
                            http_status=respuesta.status_code,
                        )
                    trozos.append(trozo)
                cuerpo = b"".join(trozos)
                cabeceras = safe_headers(respuesta.headers, capture=capture_headers)
                estado = respuesta.status_code
        except (httpx2.TimeoutException, httpx2.TransportError) as exc:
            transcurrido = int((time.monotonic() - comenzado) * 1000)
            logger.warning(
                "publish %s %s%s fallo de transporte tras %d ms",
                method, host, ruta, transcurrido,
            )
            if mutating:
                raise PublishTransportError(
                    "Se perdio la respuesta de una operacion que modifica estado "
                    f"({method} {host}{ruta}): pudo completarse. Hay que "
                    "consultar el estado remoto antes de decidir nada.",
                    error_class=ErrorClass.AMBIGUOUS,
                    retryable=False,
                ) from exc
            raise PublishTransportError(
                f"Fallo de transporte: {exc}",
                error_class=ErrorClass.TRANSIENT,
                retryable=True,
            ) from exc

        transcurrido = int((time.monotonic() - comenzado) * 1000)
        identificador = (
            cabeceras.get("x-request-id")
            or cabeceras.get("x-fb-request-id")
            or cabeceras.get("x-guploader-uploadid")
        )
        logger.info(
            "publish %s %s%s -> %d (%d ms)", method, host, ruta, estado, transcurrido
        )
        resultado = HttpOutcome(
            status=estado,
            headers=cabeceras,
            body=cuerpo,
            request_id=identificador,
            elapsed_ms=transcurrido,
        )
        esperados = set(expected or ())
        if esperados and estado in esperados:
            return resultado
        if estado >= 400:
            clase, reintentable = classify(estado, cuerpo)
            raise PublishTransportError(
                f"{method} {host}{ruta} devolvio {estado}: {_mensaje_de_error(cuerpo)}",
                error_class=clase,
                retryable=reintentable and not mutating,
                http_status=estado,
                request_id=identificador,
                details={"retry_after_s": retry_after_seconds(cabeceras)},
            )
        if 300 <= estado < 400:
            # No se sigue: ni con credenciales a otro host, ni confundiendo un
            # 308 del protocolo reanudable con una redireccion.
            raise PublishTransportError(
                f"{method} {host}{ruta} devolvio {estado} y no se siguen "
                "redirecciones automaticamente.",
                error_class=ErrorClass.INVALID_PAYLOAD,
                retryable=False,
                http_status=estado,
            )
        return resultado

    def get_with_backoff(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        attempts: int = 3,
        on_attempt: Callable[[int], None] | None = None,
    ) -> HttpOutcome:
        """Consulta con backoff acotado y `Retry-After` respetado.

        Solo para GET y sondeos: leer dos veces no publica dos veces.
        """
        ultimo: PublishTransportError | None = None
        for intento in range(attempts):
            if on_attempt is not None:
                on_attempt(intento)
            try:
                return self.request(
                    "GET", url, headers=headers, params=params, mutating=False
                )
            except PublishTransportError as exc:
                if not exc.retryable or intento == attempts - 1:
                    raise
                ultimo = exc
                espera = exc.details.get("retry_after_s") or self._backoff(intento)
                self.sleep(min(float(espera), BACKOFF_MAX_S))
        assert ultimo is not None
        raise ultimo

    def _backoff(self, intento: int) -> float:
        base = min(BACKOFF_BASE_S * (2**intento), BACKOFF_MAX_S)
        return base * (0.5 + self._rng.random() / 2)

    def close(self) -> None:
        cerrar = getattr(self.client, "close", None)
        if callable(cerrar):
            cerrar()
