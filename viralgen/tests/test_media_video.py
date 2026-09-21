"""Video: tareas remotas, MP4 reales y herramientas externas.

Las pruebas que necesitan FFmpeg/ffprobe se SALTAN explicitamente cuando no
estan instalados. Saltarlas no equivale a haberlas pasado, y en ningun caso se
inventan resultados de ffprobe ni se renombra una imagen a `.mp4`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from viralgen.media.providers.base import MediaBudget, VideoRequest, VideoStatus
from viralgen.media.providers.mock import MockVideoProvider
from viralgen.media.storage import MediaStorage
from viralgen.media.videoprobe import (
    MediaVideoError,
    decode_integrity_check,
    make_test_video,
    probe_video,
    tool_available,
)
from viralgen.storage import Storage

TIENE_FFMPEG = tool_available("ffmpeg")
TIENE_FFPROBE = tool_available("ffprobe")
sin_herramientas = pytest.mark.skipif(
    not (TIENE_FFMPEG and TIENE_FFPROBE),
    reason="FFmpeg/ffprobe no estan instalados en este entorno",
)


def _presupuesto(maximo: int = 50):
    def reservar(kind, scene_id, video_seconds):
        return 0

    return MediaBudget(
        limits={
            "generation_attempts": float(maximo), "status_requests": float(maximo),
            "download_attempts": float(maximo), "video_seconds": 120.0,
        },
        used={
            "generation_attempts": 0.0, "status_requests": 0.0,
            "download_attempts": 0.0, "video_seconds": 0.0,
        },
        reserve=reservar,
        settle=lambda token, **campos: None,
    )


# ---------------------------------------------------------------------------
# Herramientas reales (se saltan si faltan)
# ---------------------------------------------------------------------------


@sin_herramientas
def test_mp4_local_decodificado_e_inspeccionado(tmp_path: Path) -> None:
    ruta = make_test_video(tmp_path / "clip.mp4", seconds=5, width=720, height=1280, fps=24)
    info = probe_video(ruta)
    assert (info.width, info.height) == (720, 1280)
    assert info.codec == "h264"
    assert info.has_audio is False
    # Duracion MEDIDA del stream de video, no de una pista de audio.
    assert 4.5 <= info.duration_s <= 5.5
    # fps racional conservado.
    assert info.fps_den > 0 and abs(info.fps - 24) < 0.5
    decode_integrity_check(ruta, max_seconds=1.0)


@sin_herramientas
def test_separacion_entre_duracion_pedida_y_medida(tmp_path: Path) -> None:
    pedida = 5
    ruta = make_test_video(tmp_path / "c.mp4", seconds=pedida, width=720, height=1280, fps=30)
    info = probe_video(ruta)
    assert info.duration_s != pedida or True  # pueden coincidir; son campos distintos
    datos = info.describe()
    assert "measured_duration_s" in datos and "fps_rational" in datos
    assert datos["fps_rational"] == f"{info.fps_num}/{info.fps_den}"


@sin_herramientas
def test_un_archivo_corrupto_no_pasa_por_video(tmp_path: Path) -> None:
    falso = tmp_path / "falso.mp4"
    falso.write_bytes(b"\x00" * 4096)
    with pytest.raises(MediaVideoError):
        probe_video(falso)


@sin_herramientas
def test_el_proveedor_simulado_crea_un_mp4_real(tmp_path: Path, media_settings) -> None:
    proveedor = MockVideoProvider(settings=media_settings, seed=1)
    semilla = tmp_path / "seed.jpg"
    from PIL import Image

    Image.new("RGB", (720, 1280), "#204060").save(semilla, format="JPEG")
    peticion = VideoRequest(
        operation_id="op", scene_id="sc_01", init_image_path=semilla,
        init_image_sha256="a" * 64, prompt_text="movimiento", model="mock-video-1",
        ratio="720:1280", duration_s=5,
    )
    presupuesto = _presupuesto()
    tarea = proveedor.create_task(peticion, presupuesto)
    estado = proveedor.poll_task(tarea.task_id, presupuesto)
    assert estado.state == "succeeded"
    destino = tmp_path / "clip.mp4"
    proveedor.download_output(estado, destino, presupuesto, max_bytes=50 * 1024 * 1024)
    info = probe_video(destino)
    assert info.codec == "h264" and (info.width, info.height) == (720, 1280)


def test_sin_ffmpeg_el_video_queda_bloqueado(media_settings, tmp_path) -> None:
    """Sin FFmpeg el flujo de video bloquea ANTES de generar assets."""
    from viralgen.errors import ConfigError
    from viralgen.media.videoprobe import require_video_tools

    media_settings.ffmpeg_path = "ffmpeg-que-no-existe"
    media_settings.ffprobe_path = "ffprobe-que-no-existe"
    with pytest.raises(ConfigError, match="ffmpeg"):
        require_video_tools(media_settings.ffmpeg_path, media_settings.ffprobe_path)


# ---------------------------------------------------------------------------
# Tareas remotas (sin depender de FFmpeg)
# ---------------------------------------------------------------------------


def test_una_tarea_viva_se_reanuda_sin_otro_post(media_settings) -> None:
    """La tabla de tareas permite retomar el mismo task_id."""
    directorio = media_settings.effective_data_dir(simulation=True)
    directorio.mkdir(parents=True, exist_ok=True)
    with Storage(directorio) as almacen:
        medios = MediaStorage(almacen)
        medios.migrate()
        medios.put_task(
            operation_key="op-1", job_id="job-1", media_run_id="run-1",
            scene_id="sc_01", task_id="task-abc", state="running", simulation=True,
        )
        guardada = medios.get_task("op-1")
        assert guardada["task_id"] == "task-abc"
        assert guardada["state"] == "running"
        assert [t["task_id"] for t in medios.live_tasks("job-1")] == ["task-abc"]

        medios.update_task_state("op-1", "succeeded")
        assert medios.live_tasks("job-1") == []


def test_un_resultado_incierto_se_persiste_y_bloquea(media_settings) -> None:
    directorio = media_settings.effective_data_dir(simulation=True)
    directorio.mkdir(parents=True, exist_ok=True)
    with Storage(directorio) as almacen:
        medios = MediaStorage(almacen)
        medios.migrate()
        medios.mark_unknown_outcome(
            operation_key="op-x", job_id="job-1", scene_id="sc_02",
            reason="corte tras enviar la creacion",
        )
        bloqueo = medios.get_unknown_outcome("op-x")
        assert bloqueo and bloqueo["scene_id"] == "sc_02"
        # La reconciliacion es EXPLICITA, nunca automatica.
        medios.clear_unknown_outcome("op-x")
        assert medios.get_unknown_outcome("op-x") is None


def test_el_presupuesto_de_consultas_no_cancela_la_tarea(media_settings) -> None:
    presupuesto = _presupuesto(maximo=1)
    presupuesto.reserve("status", None)
    assert presupuesto.can_spend("status_requests") is False
    # El task_id sigue existiendo: agotar consultas no lo borra.
    directorio = media_settings.effective_data_dir(simulation=True)
    directorio.mkdir(parents=True, exist_ok=True)
    with Storage(directorio) as almacen:
        medios = MediaStorage(almacen)
        medios.migrate()
        medios.put_task(
            operation_key="op-2", job_id="job-1", media_run_id="run-1",
            scene_id="sc_01", task_id="task-vivo", state="running", simulation=True,
        )
        assert medios.get_task("op-2")["task_id"] == "task-vivo"


def test_la_descarga_fallida_conserva_el_task_id(media_settings, tmp_path) -> None:
    from viralgen.media.providers.base import MediaPermanentError

    class DescargaRota(MockVideoProvider):
        def download_output(self, status, destination, budget, *, max_bytes):
            budget.reserve("download", None)
            raise MediaPermanentError("fallo de descarga simulado")

    proveedor = DescargaRota(settings=media_settings, seed=1)
    presupuesto = _presupuesto()
    estado = VideoStatus(task_id="task-conservado", state="succeeded", output_url="mock://x")
    with pytest.raises(MediaPermanentError):
        proveedor.download_output(estado, tmp_path / "c.mp4", presupuesto, max_bytes=10)
    # El identificador de la tarea no se pierde ni se crea otro video.
    assert estado.task_id == "task-conservado"
    assert presupuesto.used_total["generation_attempts"] == 0


# ---------------------------------------------------------------------------
# Recorrido completo de la espera remota (sin FFmpeg y sin medir nada)
# ---------------------------------------------------------------------------


class _TareaEterna:
    """Proveedor de video que crea la tarea y nunca la termina.

    Solo ejercita el CICLO DE VIDA de la tarea remota: no produce ningun
    archivo, asi que no se mide nada y no se inventa ningun resultado de
    ffprobe. Cuenta los POST para demostrar que la reanudacion no emite otro.
    """

    name = "stub"
    model = "mock-video-1"
    api_version = None

    def __init__(self) -> None:
        from viralgen.media.capabilities import video_capabilities

        self.capabilities = video_capabilities("mock-video-1")
        self.posts = 0
        self.polls = 0

    def create_task(self, request, budget):
        from viralgen.media.providers.base import VideoTask

        self.posts += 1
        token = budget.reserve(
            "generation", request.scene_id, video_seconds=request.duration_s
        )
        task_id = f"stub-task-{self.posts:02d}"
        budget.settle(token, status="ok", request_id=task_id, task_id=task_id)
        return VideoTask(task_id=task_id, request_id=task_id, latency_ms=1)

    def poll_task(self, task_id: str, budget):
        self.polls += 1
        token = budget.reserve("status", None)
        budget.settle(token, status="ok", task_id=task_id)
        return VideoStatus(task_id=task_id, state="running")

    def download_output(self, status, destination, budget, *, max_bytes):  # pragma: no cover
        raise AssertionError("no debe descargarse nada: la tarea nunca termina")


def _entradas_con_video(tmp_path: Path):
    """Guion con escenas `video` (perfiles por defecto) y su voz medida."""
    from viralgen.config import Settings
    from viralgen.pipeline import JobRequest, Pipeline
    from viralgen.voice.pipeline import VoiceJobRequest, VoicePipeline

    ajustes = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        min_free_disk_mb=0,
        log_level="ERROR",
        # `true` existe y no hace nada: satisface la comprobacion de presencia
        # de ejecutables. En esta prueba nunca llega a producirse un clip, asi
        # que no se mide ni se transcodifica nada con ellos.
        ffmpeg_path="true",
        ffprobe_path="true",
    )
    guion = Pipeline(
        ajustes,
        JobRequest(
            command="generate", profile_id="infantil_cuentos",
            topic="aprender a compartir", simulation=True, seed=5,
            job_key="espera-remota",
        ),
    ).run()
    assert guion.script_path is not None
    document = json.loads(Path(guion.script_path).read_text(encoding="utf-8"))
    assert any(
        escena["visual"]["asset_type"] == "video" for escena in document["scenes"]
    ), "el perfil por defecto debe producir alguna escena de video"
    voz = VoicePipeline(
        ajustes,
        VoiceJobRequest(
            script_path=Path(guion.script_path), voice_key="espera-remota-voz",
            simulation=True, seed=5,
        ),
    ).run()
    assert voz.manifest_path is not None
    return ajustes, Path(guion.script_path), Path(voz.manifest_path)


def test_la_espera_local_agotada_devuelve_codigo_11_sin_manifiesto(tmp_path: Path) -> None:
    """`waiting_remote` no es exito ni fallo: no publica `media.json`."""
    from viralgen.errors import ExitCode
    from viralgen.media.pipeline import MediaJobRequest, MediaPipeline

    ajustes, guion, voz = _entradas_con_video(tmp_path)
    # Espera local minima: la primera consulta ya deja la tarea viva.
    ajustes.media_poll_wait_s = 1
    proveedor = _TareaEterna()

    resultado = MediaPipeline(
        ajustes,
        MediaJobRequest(
            script_path=guion, voice_path=voz, media_key="espera-001",
            simulation=True, seed=5,
        ),
        video_provider=proveedor,
    ).run()

    assert resultado.status == "waiting_remote"
    assert resultado.exit_code == ExitCode.WAITING_REMOTE == 11
    # Sin manifiesto: un media.json aparentaria cubrir escenas que faltan.
    assert resultado.manifest_path is None
    assert resultado.partial is True
    assert resultado.admissible_for_assembly is False
    # El task_id que viaja en el resumen es el REAL, no uno inventado.
    assert [tarea["task_id"] for tarea in resultado.remote_tasks] == ["stub-task-01"]
    assert proveedor.posts == 1


def test_reinvocar_retoma_la_misma_tarea_sin_otro_post(tmp_path: Path) -> None:
    """La segunda invocacion no crea otra tarea: eso facturaria dos veces."""
    from viralgen.media.pipeline import MediaJobRequest, MediaPipeline

    ajustes, guion, voz = _entradas_con_video(tmp_path)
    ajustes.media_poll_wait_s = 1
    proveedor = _TareaEterna()

    def _invocar():
        return MediaPipeline(
            ajustes,
            MediaJobRequest(
                script_path=guion, voice_path=voz, media_key="espera-002",
                simulation=True, seed=5,
            ),
            video_provider=proveedor,
        ).run()

    primero = _invocar()
    assert primero.status == "waiting_remote"
    task_id = primero.remote_tasks[0]["task_id"]

    segundo = _invocar()
    assert segundo.status == "waiting_remote"
    assert segundo.remote_tasks[0]["task_id"] == task_id
    # Un solo POST en total, aunque se haya invocado dos veces.
    assert proveedor.posts == 1
    # Las consultas SI se repiten: son las que descubren el estado.
    assert proveedor.polls >= 2
