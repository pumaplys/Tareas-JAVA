"""Adaptador real de ElevenLabs con transporte HTTP simulado.

Se usa `httpx2.MockTransport` (el cliente que el lockfile trae de verdad): la
peticion recorre el mismo camino de serializacion que en produccion, pero no
sale nada a la red y no hace falta ninguna clave real.

LIMITE: esto comprueba lo que se ENVIA y como se interpreta lo recibido. No
demuestra que el servidor de ElevenLabs acepte la peticion.
"""

from __future__ import annotations

import base64
import json

import httpx2
import pytest

from viralgen.config import Settings
from viralgen.voice.providers.base import (
    SynthesisRequest,
    VoiceBudget,
    VoiceBudgetExceededError,
    VoicePermanentError,
    VoiceTransientError,
)
from viralgen.voice.providers.elevenlabs import ElevenLabsProvider

AUDIO = b"ID3-falso-de-prueba"


@pytest.fixture
def ajustes() -> Settings:
    return Settings(
        _env_file=None,
        elevenlabs_api_key="xi-de-prueba-no-real",
        elevenlabs_model_id="eleven_multilingual_v2",
        voice_max_transport_retries=2,
        log_level="ERROR",
    )


def _presupuesto(maximo: int = 24) -> tuple[VoiceBudget, list]:
    reservas: list = []

    def reservar(kind, scene_id):
        reservas.append({"kind": kind, "scene_id": scene_id, "status": "reserved"})
        return len(reservas) - 1

    def liquidar(token, **campos):
        reservas[token].update(campos)

    return (
        VoiceBudget(max_requests=maximo, used_total=0, reserve=reservar, settle=liquidar),
        reservas,
    )


def _cuerpo_ok(con_alineacion: bool = True, normalizada: bool = False, texto: str = "Hola mundo") -> dict:
    datos = {"audio_base64": base64.b64encode(AUDIO).decode("ascii")}
    if con_alineacion:
        n = len(texto)
        datos["alignment"] = {
            "characters": list(texto),
            "character_start_times_seconds": [i * 0.1 for i in range(n)],
            "character_end_times_seconds": [(i + 1) * 0.1 for i in range(n)],
        }
    if normalizada:
        datos["normalized_alignment"] = datos.get("alignment")
    return datos


def _proveedor(ajustes, respuestas, dormidas=None):
    capturadas: list[httpx2.Request] = []
    pendientes = list(respuestas)

    def handler(request: httpx2.Request) -> httpx2.Response:
        capturadas.append(request)
        siguiente = pendientes.pop(0)
        if isinstance(siguiente, Exception):
            raise siguiente
        return siguiente

    cliente = httpx2.Client(transport=httpx2.MockTransport(handler))
    proveedor = ElevenLabsProvider(
        settings=ajustes,
        client=cliente,
        sleep=(dormidas.append if dormidas is not None else (lambda _s: None)),
    )
    return proveedor, capturadas


def _peticion(texto: str = "Hola mundo") -> SynthesisRequest:
    return SynthesisRequest(
        scene_id="sc_01",
        text=texto,
        previous_text="Escena anterior.",
        next_text="Escena siguiente.",
        voice_id="voz-de-la-cuenta",
        model_id="eleven_multilingual_v2",
        output_format="mp3_44100_128",
        settings={"stability": 0.6, "use_speaker_boost": True},
    )


# ---------------------------------------------------------------------------
# Payload enviado
# ---------------------------------------------------------------------------


def test_payload_http_real(ajustes: Settings) -> None:
    proveedor, capturadas = _proveedor(
        ajustes, [httpx2.Response(200, json=_cuerpo_ok(), headers={"request-id": "req-abc"})]
    )
    presupuesto, reservas = _presupuesto()
    resultado = proveedor.synthesize(_peticion(), presupuesto)

    peticion = capturadas[0]
    assert peticion.method == "POST"
    assert peticion.url.path == "/v1/text-to-speech/voz-de-la-cuenta/with-timestamps"
    # output_format va como parametro de consulta.
    assert peticion.url.params["output_format"] == "mp3_44100_128"
    # Autenticacion por cabecera dedicada.
    assert peticion.headers["xi-api-key"] == "xi-de-prueba-no-real"

    cuerpo = json.loads(peticion.content)
    assert cuerpo["text"] == "Hola mundo"
    assert cuerpo["model_id"] == "eleven_multilingual_v2"
    assert cuerpo["voice_settings"] == {"stability": 0.6, "use_speaker_boost": True}
    # El contexto viaja aparte: no se concatena al texto que se pronuncia.
    assert cuerpo["previous_text"] == "Escena anterior."
    assert cuerpo["next_text"] == "Escena siguiente."
    assert cuerpo["previous_text"] not in cuerpo["text"]
    assert cuerpo["next_text"] not in cuerpo["text"]

    assert resultado.audio_bytes == AUDIO
    assert resultado.audio_format == "mp3"
    assert resultado.request_id == "req-abc"
    assert resultado.alignment is not None
    assert reservas[0]["status"] == "ok"


def test_el_texto_va_tal_cual_sin_acotaciones(ajustes: Settings) -> None:
    texto = "Lumi encontro algo brillante. La luz entraba entre las ramas."
    proveedor, capturadas = _proveedor(
        ajustes, [httpx2.Response(200, json=_cuerpo_ok(texto=texto))]
    )
    proveedor.synthesize(_peticion(texto), _presupuesto()[0])
    assert json.loads(capturadas[0].content)["text"] == texto


def test_sin_request_id_no_se_inventa_ninguno(ajustes: Settings) -> None:
    proveedor, _ = _proveedor(ajustes, [httpx2.Response(200, json=_cuerpo_ok())])
    resultado = proveedor.synthesize(_peticion(), _presupuesto()[0])
    assert resultado.request_id is None


# ---------------------------------------------------------------------------
# Respuestas
# ---------------------------------------------------------------------------


def test_respuesta_sin_alineacion(ajustes: Settings) -> None:
    """La respuesta puede no traer alignment: no se presupone."""
    proveedor, _ = _proveedor(
        ajustes, [httpx2.Response(200, json=_cuerpo_ok(con_alineacion=False))]
    )
    resultado = proveedor.synthesize(_peticion(), _presupuesto()[0])
    assert resultado.alignment is None
    assert resultado.normalized_alignment is None
    assert resultado.audio_bytes == AUDIO  # el audio se conserva


def test_respuesta_sin_audio(ajustes: Settings) -> None:
    proveedor, _ = _proveedor(ajustes, [httpx2.Response(200, json={"alignment": None})])
    with pytest.raises(VoicePermanentError, match="audio_base64"):
        proveedor.synthesize(_peticion(), _presupuesto()[0])


def test_respuesta_demasiado_grande(ajustes: Settings) -> None:
    ajustes.voice_max_response_bytes = 64
    proveedor, _ = _proveedor(ajustes, [httpx2.Response(200, json=_cuerpo_ok())])
    with pytest.raises(VoicePermanentError, match="limite"):
        proveedor.synthesize(_peticion(), _presupuesto()[0])


def test_texto_demasiado_largo_no_llega_a_enviarse(ajustes: Settings) -> None:
    ajustes.voice_max_request_chars = 5
    proveedor, capturadas = _proveedor(ajustes, [])
    with pytest.raises(VoicePermanentError, match="caracteres"):
        proveedor.synthesize(_peticion("texto bastante largo"), _presupuesto()[0])
    assert capturadas == []


# ---------------------------------------------------------------------------
# Errores y reintentos
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("estado", "cuerpo"),
    [(401, "clave"), (403, "permisos"), (404, "voz"), (422, "parametros"), (402, "credito")],
    ids=["clave", "permisos", "no_encontrado", "parametros", "credito"],
)
def test_errores_permanentes_no_se_reintentan(ajustes: Settings, estado: int, cuerpo: str) -> None:
    proveedor, capturadas = _proveedor(
        ajustes,
        [httpx2.Response(estado, json={"detail": cuerpo}), httpx2.Response(200, json=_cuerpo_ok())],
    )
    with pytest.raises(VoicePermanentError):
        proveedor.synthesize(_peticion(), _presupuesto()[0])
    assert len(capturadas) == 1


def test_cuota_agotada_no_se_reintenta_aunque_llegue_con_429(ajustes: Settings) -> None:
    proveedor, capturadas = _proveedor(
        ajustes, [httpx2.Response(429, json={"detail": {"status": "quota_exceeded"}})]
    )
    with pytest.raises(VoicePermanentError, match="Cuota"):
        proveedor.synthesize(_peticion(), _presupuesto()[0])
    assert len(capturadas) == 1


def test_429_temporal_y_5xx_se_reintentan(ajustes: Settings) -> None:
    dormidas: list[float] = []
    proveedor, capturadas = _proveedor(
        ajustes,
        [
            httpx2.Response(429, json={"detail": "slow down"}, headers={"retry-after": "3"}),
            httpx2.Response(503, json={"detail": "upstream"}),
            httpx2.Response(200, json=_cuerpo_ok()),
        ],
        dormidas,
    )
    presupuesto, reservas = _presupuesto()
    proveedor.synthesize(_peticion(), presupuesto)
    assert len(capturadas) == 3
    assert presupuesto.used_total == 3  # los reintentos cuentan
    assert dormidas[0] == 3.0  # se respeta Retry-After
    assert [r["status"] for r in reservas] == ["error", "error", "ok"]


def test_tiempo_de_espera_es_transitorio(ajustes: Settings) -> None:
    proveedor, capturadas = _proveedor(
        ajustes, [httpx2.TimeoutException("agotado"), httpx2.Response(200, json=_cuerpo_ok())]
    )
    proveedor.synthesize(_peticion(), _presupuesto()[0])
    assert len(capturadas) == 2


def test_se_agotan_los_reintentos(ajustes: Settings) -> None:
    proveedor, capturadas = _proveedor(ajustes, [httpx2.Response(500, text="boom")] * 3)
    with pytest.raises(VoiceTransientError, match="Se agotaron"):
        proveedor.synthesize(_peticion(), _presupuesto()[0])
    assert len(capturadas) == 3


def test_el_presupuesto_corta_antes_de_enviar(ajustes: Settings) -> None:
    proveedor, capturadas = _proveedor(ajustes, [httpx2.Response(200, json=_cuerpo_ok())])
    presupuesto, _ = _presupuesto(maximo=1)
    presupuesto.reserve("synthesis", "sc_00")
    with pytest.raises(VoiceBudgetExceededError):
        proveedor.synthesize(_peticion(), presupuesto)
    assert capturadas == []


def test_una_reserva_se_persiste_aunque_falle(ajustes: Settings) -> None:
    """Un timeout puede haber consumido credito: la reserva se conserva."""
    proveedor, _ = _proveedor(ajustes, [httpx2.TimeoutException("x")] * 3)
    presupuesto, reservas = _presupuesto()
    with pytest.raises(VoiceTransientError):
        proveedor.synthesize(_peticion(), presupuesto)
    assert len(reservas) == 3
    assert all(r["status"] == "error" for r in reservas)


def test_la_alineacion_forzada_gasta_presupuesto(ajustes: Settings, tmp_path) -> None:
    ruta = tmp_path / "clip.wav"
    ruta.write_bytes(b"RIFF....WAVE")
    cuerpo = {
        "characters": [
            {"text": "H", "start": 0.0, "end": 0.1},
            {"text": "i", "start": 0.1, "end": 0.2},
        ]
    }
    proveedor, capturadas = _proveedor(ajustes, [httpx2.Response(200, json=cuerpo)])
    presupuesto, _ = _presupuesto()
    alineacion = proveedor.force_align(ruta, "Hi", presupuesto)
    assert capturadas[0].url.path == "/v1/forced-alignment"
    assert alineacion is not None and alineacion.joined == "Hi"
    assert presupuesto.used_total == 1


def test_la_alineacion_forzada_sin_caracteres_devuelve_none(ajustes: Settings, tmp_path) -> None:
    ruta = tmp_path / "clip.wav"
    ruta.write_bytes(b"RIFF")
    proveedor, _ = _proveedor(ajustes, [httpx2.Response(200, json={"words": [{"text": "Hi"}]})])
    assert proveedor.force_align(ruta, "Hi", _presupuesto()[0]) is None


def test_faltan_credenciales_en_modo_real() -> None:
    from viralgen.errors import ConfigError

    with pytest.raises(ConfigError, match="ELEVENLABS_API_KEY"):
        ElevenLabsProvider(settings=Settings(_env_file=None))
