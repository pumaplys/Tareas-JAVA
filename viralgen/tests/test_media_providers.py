"""Payload real de los adaptadores con transporte HTTP simulado.

Se usan el SDK de `openai` y `httpx2` de verdad: la peticion recorre el mismo
camino de serializacion que en produccion, sin red ni claves reales.

LIMITE: esto comprueba lo que se ENVIA y como se interpreta lo recibido. NO es
integracion externa y no demuestra que los servidores lo acepten.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx2
import pytest
from PIL import Image

from viralgen.config import Settings
from viralgen.errors import ConfigError
from viralgen.media.providers.base import (
    ImageRequest,
    MediaBudget,
    MediaBudgetExceededError,
    MediaOutcomeUnknownError,
    MediaPermanentError,
    MediaTransientError,
    VideoRequest,
)
from viralgen.media.providers.openai_images import OpenAIImageProvider
from viralgen.media.providers.runway import RunwayVideoProvider

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _presupuesto(maximo: int = 24):
    reservas: list[dict] = []

    def reservar(kind, scene_id, video_seconds):
        reservas.append(
            {"kind": kind, "scene_id": scene_id, "video_seconds": video_seconds,
             "status": "reserved"}
        )
        return len(reservas) - 1

    def liquidar(token, **campos):
        reservas[token].update(campos)

    presupuesto = MediaBudget(
        limits={
            "generation_attempts": float(maximo),
            "status_requests": float(maximo),
            "download_attempts": float(maximo),
            "video_seconds": 60.0,
        },
        used={
            "generation_attempts": 0.0, "status_requests": 0.0,
            "download_attempts": 0.0, "video_seconds": 0.0,
        },
        reserve=reservar,
        settle=liquidar,
    )
    return presupuesto, reservas


# ---------------------------------------------------------------------------
# Imagenes
# ---------------------------------------------------------------------------


@pytest.fixture
def ajustes_imagen(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path,
        openai_api_key="sk-de-prueba-no-real",
        openai_image_model="gpt-image-1",
        media_max_safe_retries=2,
        log_level="ERROR",
    )


def _proveedor_imagen(ajustes, respuestas, dormidas=None):
    capturadas: list[httpx2.Request] = []
    pendientes = list(respuestas)

    def handler(request: httpx2.Request) -> httpx2.Response:
        capturadas.append(request)
        siguiente = pendientes.pop(0)
        if isinstance(siguiente, Exception):
            raise siguiente
        return siguiente

    from openai import OpenAI

    cliente = OpenAI(
        api_key="sk-de-prueba-no-real",
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )
    proveedor = OpenAIImageProvider(
        settings=ajustes, client=cliente,
        sleep=(dormidas.append if dormidas is not None else (lambda _s: None)),
    )
    return proveedor, capturadas


def _respuesta_imagen(contenido: bytes = PNG_1X1) -> httpx2.Response:
    return httpx2.Response(
        200,
        json={
            "created": 0,
            "data": [{"b64_json": base64.b64encode(contenido).decode("ascii")}],
            "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
        },
    )


def test_payload_de_generacion_sin_referencias(ajustes_imagen) -> None:
    proveedor, capturadas = _proveedor_imagen(ajustes_imagen, [_respuesta_imagen()])
    presupuesto, reservas = _presupuesto()
    resultado = proveedor.create_image(
        ImageRequest(
            operation_id="op", prompt="un zorro", model="gpt-image-1",
            size="1024x1536", quality="medium", output_format="jpeg", scene_id="sc_01",
        ),
        presupuesto,
    )
    peticion = capturadas[0]
    assert peticion.url.path == "/v1/images/generations"
    assert peticion.headers["content-type"].startswith("application/json")
    cuerpo = json.loads(peticion.content)
    assert cuerpo == {
        "prompt": "un zorro", "model": "gpt-image-1", "n": 1,
        "output_format": "jpeg", "quality": "medium", "size": "1024x1536",
    }
    assert resultado.image_bytes == PNG_1X1
    assert resultado.provider_usage == {
        "input_tokens": 10, "output_tokens": 20, "total_tokens": 30
    }
    assert reservas[0]["status"] == "ok"


def test_payload_de_edicion_con_referencias(ajustes_imagen, tmp_path) -> None:
    referencias = []
    for nombre in ("lumi", "tobi"):
        ruta = tmp_path / f"{nombre}.jpeg"
        Image.new("RGB", (8, 8), "#336699").save(ruta, format="JPEG")
        referencias.append(ruta)

    proveedor, capturadas = _proveedor_imagen(ajustes_imagen, [_respuesta_imagen()])
    proveedor.create_image(
        ImageRequest(
            operation_id="op", prompt="escena con los dos", model="gpt-image-1",
            size="1024x1536", quality="medium", output_format="jpeg",
            reference_paths=referencias, scene_id="sc_02",
        ),
        _presupuesto()[0],
    )
    peticion = capturadas[0]
    assert peticion.url.path == "/v1/images/edits"
    assert peticion.headers["content-type"].startswith("multipart/form-data")
    cuerpo = peticion.content.decode("utf-8", "replace")
    disposiciones = [l for l in cuerpo.split("\r\n") if l.startswith("Content-Disposition")]
    # Una entrada `image[]` por referencia, y solo por las presentes.
    assert sum('name="image[]"' in linea for linea in disposiciones) == 2
    assert 'filename="lumi.jpeg"' in cuerpo and 'filename="tobi.jpeg"' in cuerpo
    assert 'name="prompt"' in cuerpo and 'name="size"' in cuerpo


def test_combinacion_invalida_no_llega_a_http(ajustes_imagen) -> None:
    proveedor, capturadas = _proveedor_imagen(ajustes_imagen, [])
    with pytest.raises(ConfigError, match="no soportada"):
        proveedor.create_image(
            ImageRequest(
                operation_id="op", prompt="x", model="gpt-image-1",
                size="1080x1920", quality="medium", output_format="jpeg",
            ),
            _presupuesto()[0],
        )
    assert capturadas == []


def test_prompt_demasiado_largo_no_se_trunca(ajustes_imagen) -> None:
    ajustes_imagen.media_max_prompt_chars = 50
    proveedor, capturadas = _proveedor_imagen(ajustes_imagen, [])
    with pytest.raises(MediaPermanentError, match="caracteres"):
        proveedor.create_image(
            ImageRequest(
                operation_id="op", prompt="x" * 200, model="gpt-image-1",
                size="1024x1536", quality="medium", output_format="jpeg",
            ),
            _presupuesto()[0],
        )
    assert capturadas == []


def test_respuesta_sobredimensionada(ajustes_imagen) -> None:
    ajustes_imagen.media_max_image_response_mib = 1
    grande = base64.b64encode(b"\x00" * (2 * 1024 * 1024)).decode("ascii")
    proveedor, _ = _proveedor_imagen(
        ajustes_imagen, [httpx2.Response(200, json={"created": 0, "data": [{"b64_json": grande}]})]
    )
    with pytest.raises(MediaPermanentError, match="limite"):
        proveedor.create_image(
            ImageRequest(
                operation_id="op", prompt="x", model="gpt-image-1",
                size="1024x1536", quality="medium", output_format="jpeg",
            ),
            _presupuesto()[0],
        )


def test_base64_invalido(ajustes_imagen) -> None:
    proveedor, _ = _proveedor_imagen(
        ajustes_imagen,
        [httpx2.Response(200, json={"created": 0, "data": [{"b64_json": "no-es-base64!!"}]})],
    )
    with pytest.raises(MediaPermanentError, match="base64"):
        proveedor.create_image(
            ImageRequest(
                operation_id="op", prompt="x", model="gpt-image-1",
                size="1024x1536", quality="medium", output_format="jpeg",
            ),
            _presupuesto()[0],
        )


@pytest.mark.parametrize("estado", [401, 403, 404, 400])
def test_errores_permanentes_no_se_reintentan(ajustes_imagen, estado) -> None:
    proveedor, capturadas = _proveedor_imagen(
        ajustes_imagen,
        [httpx2.Response(estado, json={"error": {"message": "no"}}), _respuesta_imagen()],
    )
    with pytest.raises(MediaPermanentError):
        proveedor.create_image(
            ImageRequest(
                operation_id="op", prompt="x", model="gpt-image-1",
                size="1024x1536", quality="medium", output_format="jpeg",
            ),
            _presupuesto()[0],
        )
    assert len(capturadas) == 1


def test_5xx_se_reintenta_de_forma_acotada(ajustes_imagen) -> None:
    dormidas: list[float] = []
    proveedor, capturadas = _proveedor_imagen(
        ajustes_imagen,
        [httpx2.Response(503, json={"error": {"message": "x"}}), _respuesta_imagen()],
        dormidas,
    )
    presupuesto, _ = _presupuesto()
    proveedor.create_image(
        ImageRequest(
            operation_id="op", prompt="x", model="gpt-image-1",
            size="1024x1536", quality="medium", output_format="jpeg",
        ),
        presupuesto,
    )
    assert len(capturadas) == 2
    assert presupuesto.used_total["generation_attempts"] == 2  # los reintentos cuentan
    assert dormidas


def test_un_timeout_deja_resultado_incierto(ajustes_imagen) -> None:
    """Sin identificador de tarea no se puede saber si la peticion se acepto."""
    proveedor, _ = _proveedor_imagen(ajustes_imagen, [httpx2.TimeoutException("x")])
    presupuesto, reservas = _presupuesto()
    with pytest.raises(MediaOutcomeUnknownError):
        proveedor.create_image(
            ImageRequest(
                operation_id="op", prompt="x", model="gpt-image-1",
                size="1024x1536", quality="medium", output_format="jpeg", scene_id="sc_01",
            ),
            presupuesto,
        )
    assert reservas[0]["status"] == "outcome_unknown"


def test_el_presupuesto_corta_antes_de_enviar(ajustes_imagen) -> None:
    proveedor, capturadas = _proveedor_imagen(ajustes_imagen, [_respuesta_imagen()])
    presupuesto, _ = _presupuesto(maximo=1)
    presupuesto.reserve("generation", "sc_00")
    with pytest.raises(MediaBudgetExceededError):
        proveedor.create_image(
            ImageRequest(
                operation_id="op", prompt="x", model="gpt-image-1",
                size="1024x1536", quality="medium", output_format="jpeg",
            ),
            presupuesto,
        )
    assert capturadas == []


def test_faltan_credenciales_de_imagen() -> None:
    with pytest.raises(ConfigError, match="OPENAI_IMAGE_MODEL"):
        OpenAIImageProvider(settings=Settings(_env_file=None))


# ---------------------------------------------------------------------------
# Runway
# ---------------------------------------------------------------------------


@pytest.fixture
def ajustes_video(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path,
        runwayml_api_secret="key_de_prueba_no_real",
        runway_model="gen4_turbo",
        log_level="ERROR",
    )


def _proveedor_video(ajustes, respuestas, dormidas=None):
    capturadas: list[httpx2.Request] = []
    pendientes = list(respuestas)

    def handler(request: httpx2.Request) -> httpx2.Response:
        capturadas.append(request)
        siguiente = pendientes.pop(0)
        if isinstance(siguiente, Exception):
            raise siguiente
        return siguiente

    proveedor = RunwayVideoProvider(
        settings=ajustes,
        client=httpx2.Client(transport=httpx2.MockTransport(handler)),
        sleep=(dormidas.append if dormidas is not None else (lambda _s: None)),
    )
    return proveedor, capturadas


def _semilla(tmp_path: Path) -> Path:
    ruta = tmp_path / "seed.jpg"
    Image.new("RGB", (72, 128), "#204060").save(ruta, format="JPEG", quality=70)
    return ruta


def _peticion_video(tmp_path: Path, duracion: int = 10) -> VideoRequest:
    return VideoRequest(
        operation_id="op",
        scene_id="sc_01",
        init_image_path=_semilla(tmp_path),
        init_image_sha256="a" * 64,
        prompt_text="slow push-in",
        model="gen4_turbo",
        ratio="720:1280",
        duration_s=duracion,
    )


def test_payload_de_creacion_de_tarea(ajustes_video, tmp_path) -> None:
    proveedor, capturadas = _proveedor_video(
        ajustes_video, [httpx2.Response(200, json={"id": "task-123"})]
    )
    presupuesto, reservas = _presupuesto()
    tarea = proveedor.create_task(_peticion_video(tmp_path), presupuesto)

    peticion = capturadas[0]
    assert peticion.method == "POST"
    assert peticion.url.path == "/v1/image_to_video"
    assert peticion.headers["authorization"] == "Bearer key_de_prueba_no_real"
    assert peticion.headers["x-runway-version"] == "2024-11-06"
    cuerpo = json.loads(peticion.content)
    assert cuerpo["model"] == "gen4_turbo"
    assert cuerpo["ratio"] == "720:1280"
    assert cuerpo["duration"] == 10
    assert cuerpo["promptText"] == "slow push-in"
    assert cuerpo["promptImage"].startswith("data:image/jpeg;base64,")
    assert tarea.task_id == "task-123"
    # La reserva cuenta los segundos de video pedidos.
    assert reservas[0]["video_seconds"] == 10
    assert presupuesto.used_total["video_seconds"] == 10


def test_el_limite_se_aplica_a_la_data_uri_codificada(ajustes_video, tmp_path) -> None:
    ruta = tmp_path / "grande.jpg"
    Image.effect_noise((900, 1600), 100).convert("RGB").save(ruta, format="JPEG", quality=95)
    binario = ruta.stat().st_size
    # El limite se fija ENTRE el tamano binario y el de la URI codificada
    # (4/3 del binario): asi solo falla si se mide la cadena final.
    ajustes_video.media_data_uri_max_bytes = int(binario * 1.1)
    proveedor, capturadas = _proveedor_video(ajustes_video, [])
    peticion = _peticion_video(tmp_path)
    peticion.init_image_path = ruta
    with pytest.raises(MediaPermanentError, match="data URI"):
        proveedor.create_task(peticion, _presupuesto()[0])
    assert capturadas == []


def test_duracion_no_declarada(ajustes_video, tmp_path) -> None:
    proveedor, capturadas = _proveedor_video(ajustes_video, [])
    with pytest.raises(MediaPermanentError, match="Duracion"):
        proveedor.create_task(_peticion_video(tmp_path, duracion=7), _presupuesto()[0])
    assert capturadas == []


def test_una_creacion_sin_task_id_queda_incierta(ajustes_video, tmp_path) -> None:
    proveedor, _ = _proveedor_video(ajustes_video, [httpx2.Response(200, json={})])
    with pytest.raises(MediaOutcomeUnknownError, match="identificador"):
        proveedor.create_task(_peticion_video(tmp_path), _presupuesto()[0])


def test_un_corte_tras_enviar_la_creacion_queda_incierto(ajustes_video, tmp_path) -> None:
    proveedor, _ = _proveedor_video(ajustes_video, [httpx2.TimeoutException("x")])
    presupuesto, reservas = _presupuesto()
    with pytest.raises(MediaOutcomeUnknownError, match="no se puede saber"):
        proveedor.create_task(_peticion_video(tmp_path), presupuesto)
    assert reservas[0]["status"] == "outcome_unknown"


def test_consulta_de_estado(ajustes_video) -> None:
    proveedor, capturadas = _proveedor_video(
        ajustes_video,
        [
            httpx2.Response(200, json={"status": "RUNNING"}),
            httpx2.Response(
                200, json={"status": "SUCCEEDED", "output": ["https://ejemplo/clip.mp4"]}
            ),
        ],
    )
    presupuesto, _ = _presupuesto()
    primero = proveedor.poll_task("task-123", presupuesto)
    assert primero.state == "running" and not primero.terminal
    segundo = proveedor.poll_task("task-123", presupuesto)
    assert segundo.state == "succeeded" and segundo.output_url.endswith("clip.mp4")
    assert capturadas[0].url.path == "/v1/tasks/task-123"
    assert presupuesto.used_total["status_requests"] == 2


def test_descarga_con_limite_de_tamano(ajustes_video, tmp_path) -> None:
    from viralgen.media.providers.base import VideoStatus

    proveedor, _ = _proveedor_video(
        ajustes_video, [httpx2.Response(200, content=b"x" * 5000)]
    )
    destino = tmp_path / "clip.mp4"
    with pytest.raises(MediaPermanentError, match="limite"):
        proveedor.download_output(
            VideoStatus(task_id="t", state="succeeded", output_url="https://e/c.mp4"),
            destino, _presupuesto()[0], max_bytes=1000,
        )
    assert not destino.exists()


def test_cuota_agotada_no_se_reintenta(ajustes_video, tmp_path) -> None:
    proveedor, capturadas = _proveedor_video(
        ajustes_video, [httpx2.Response(402, text="insufficient credits")]
    )
    with pytest.raises(MediaPermanentError, match="Credito|Peticion|402"):
        proveedor.create_task(_peticion_video(tmp_path), _presupuesto()[0])
    assert len(capturadas) == 1


def test_faltan_credenciales_de_video() -> None:
    with pytest.raises(ConfigError, match="RUNWAYML_API_SECRET"):
        RunwayVideoProvider(settings=Settings(_env_file=None))
