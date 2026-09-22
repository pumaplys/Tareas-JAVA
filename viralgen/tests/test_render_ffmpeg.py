"""Capacidades de FFmpeg y ejecucion segura de subprocesos.

Estas pruebas NO codifican video: comprueban que las capacidades se leen de
los ejecutables instalados, que los procesos se lanzan sin shell, que un
timeout termina el GRUPO de procesos y que un crecimiento desbocado de la
salida se corta antes de llenar el disco.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from viralgen.errors import ConfigError
from viralgen.render.ffmpeg import (
    MIN_FFMPEG_VERSION,
    REQUIRED_ENCODERS,
    REQUIRED_FILTERS,
    ProcessRunner,
    RenderStageError,
    RenderStageTimeout,
    _ENCODER_RE,
    _FILTER_RE,
    _listed_names,
    _parse_version,
    probe_capabilities,
    require_tools,
)

#: Marca de integracion local real, igual que en las pruebas del modulo 3.
TIENE_FFMPEG = probe_capabilities("ffmpeg", "ffprobe").usable


def necesita_ffmpeg(prueba):
    """Aplica DOS marcas: `ffmpeg` (seleccion) y `skipif` (salto sin las herramientas).

    OJO con la forma corta: `pytest.mark.ffmpeg(pytest.mark.skipif(...))` NO
    compone dos marcas. Convierte el `skipif` en un ARGUMENTO de la marca
    `ffmpeg` y el salto queda muerto, asi que sin FFmpeg la prueba intenta
    ejecutarse en vez de saltarse. Se componen apilandolas.
    """
    con_salto = pytest.mark.skipif(
        not TIENE_FFMPEG,
        reason="FFmpeg/ffprobe (con libx264, aac y libass) no estan disponibles",
    )(prueba)
    return pytest.mark.ffmpeg(con_salto)


# ---------------------------------------------------------------------------
# Lectura de capacidades
# ---------------------------------------------------------------------------


def test_los_listados_se_parsean_por_entrada_no_por_cabecera() -> None:
    """`-encoders` lleva separador de guiones y `-filters` solo una leyenda."""
    codificadores = """Encoders:
 V..... = Video
 ------
 V....D libx264              libx264 H.264 / AVC
 A....D aac                  AAC (Advanced Audio Coding)
"""
    filtros = """Filters:
  T.. = Timeline support
  | = Source or sink filter
 ... ass               V->V       Render ASS subtitles.
 T.C volume            A->A       Change input volume.
"""
    assert _listed_names(codificadores, _ENCODER_RE) == {"libx264", "aac"}
    assert _listed_names(filtros, _FILTER_RE) == {"ass", "volume"}


def test_la_version_sale_del_ejecutable_no_de_la_documentacion() -> None:
    texto, numeros = _parse_version(
        "ffmpeg version 6.1.1-3ubuntu5 Copyright (c) 2000-2023"
    )
    assert numeros == (6, 1, 1)
    assert "6.1.1" in texto
    # Una compilacion de git no trae numeros: se conserva la cadena y la tupla
    # queda vacia en vez de inventarse un numero.
    _texto, vacios = _parse_version("ffmpeg version N-119999-gabc1234")
    assert vacios == ()


def test_un_ejecutable_ausente_se_declara_ausente() -> None:
    capacidades = probe_capabilities("ffmpeg-que-no-existe", "ffprobe-que-no-existe")
    assert not capacidades.usable
    assert "ffmpeg" in capacidades.missing
    assert "ffprobe" in capacidades.missing
    # Y no se inventa ninguna capacidad.
    assert capacidades.encoders == frozenset()
    assert capacidades.has_libass is False


def test_require_tools_falla_con_diagnostico_cuando_falta_algo() -> None:
    with pytest.raises(ConfigError, match="ffmpeg"):
        require_tools("ffmpeg-que-no-existe", "ffprobe-que-no-existe")


@necesita_ffmpeg
def test_las_capacidades_reales_cubren_lo_que_el_montaje_necesita() -> None:
    capacidades = probe_capabilities("ffmpeg", "ffprobe")
    assert capacidades.usable, capacidades.missing
    for nombre in REQUIRED_ENCODERS:
        assert nombre in capacidades.encoders
    for nombre in REQUIRED_FILTERS:
        assert nombre in capacidades.filters
    assert capacidades.has_libass
    # La version minima declarada es la REALMENTE probada.
    assert capacidades.version_tuple >= MIN_FFMPEG_VERSION


# ---------------------------------------------------------------------------
# Ejecucion de subprocesos
# ---------------------------------------------------------------------------


def _runner(tmp_path: Path, **kwargs) -> ProcessRunner:
    return ProcessRunner(
        ffmpeg_path=sys.executable,
        ffprobe_path=sys.executable,
        log_dir=tmp_path / "logs",
        watch_interval_s=0.05,
        **kwargs,
    )


def test_los_argumentos_van_en_lista_y_sin_shell(tmp_path: Path) -> None:
    """Un metacaracter de shell es un argumento literal, no una instruccion."""
    testigo = tmp_path / "no-debe-existir.txt"
    ejecutor = _runner(tmp_path)
    resultado = ejecutor.run(
        [sys.executable, "-c", "print('hola')", f"; touch {testigo}"],
        stage="sin_shell",
    )
    assert resultado.ok
    assert not testigo.exists(), "el shell no debe haber interpretado el ';'"


def test_stdin_esta_cerrado(tmp_path: Path) -> None:
    """FFmpeg no puede quedarse esperando a que alguien conteste."""
    ejecutor = _runner(tmp_path)
    resultado = ejecutor.run(
        [
            sys.executable, "-c",
            "import sys; sys.exit(0 if sys.stdin.read() == '' else 1)",
        ],
        stage="stdin_cerrado",
    )
    assert resultado.ok


def test_un_codigo_distinto_de_cero_es_un_error_de_etapa(tmp_path: Path) -> None:
    ejecutor = _runner(tmp_path)
    with pytest.raises(RenderStageError, match="codigo 3"):
        ejecutor.run_checked(
            [sys.executable, "-c", "import sys; sys.exit(3)"], stage="fallo"
        )


def test_el_timeout_termina_el_grupo_de_procesos(tmp_path: Path) -> None:
    """Matar solo al padre dejaria hijos codificando y ocupando disco."""
    marcador = tmp_path / "hijo_vivo.txt"
    programa = (
        "import subprocess, sys, time\n"
        "hijo = subprocess.Popen([sys.executable, '-c',\n"
        "  \"import time, pathlib, sys;\"\n"
        f"  \"pathlib.Path(r'{marcador}').write_text(str(1));\"\n"
        "  \"time.sleep(60)\"])\n"
        "print(hijo.pid, flush=True)\n"
        "time.sleep(60)\n"
    )
    ejecutor = _runner(tmp_path, stage_timeout_s=1)
    inicio = time.monotonic()
    with pytest.raises(RenderStageTimeout, match="se agoto el tiempo"):
        ejecutor.run([sys.executable, "-c", programa], stage="timeout")
    assert time.monotonic() - inicio < 25, "debe cortar pronto, no esperar 60 s"

    # El hijo tampoco sobrevive: se mato el grupo entero.
    deadline = time.monotonic() + 5
    pid_hijo = None
    while time.monotonic() < deadline and pid_hijo is None:
        if marcador.exists():
            pid_hijo = True
        time.sleep(0.05)
    vivos = [
        proceso
        for proceso in subprocess.run(
            ["ps", "-eo", "args"], capture_output=True, text=True, check=False
        ).stdout.splitlines()
        if "time.sleep(60)" in proceso
    ]
    assert not vivos, f"quedaron procesos vivos: {vivos}"


def test_una_salida_que_crece_de_mas_se_corta(tmp_path: Path) -> None:
    """CRF no impone tamano: el crecimiento se vigila y se detiene."""
    destino = tmp_path / "crece.bin"
    programa = (
        "import pathlib, time\n"
        f"ruta = pathlib.Path(r'{destino}')\n"
        "with ruta.open('wb') as f:\n"
        "    for _ in range(2000):\n"
        "        f.write(b'x' * 65536); f.flush()\n"
        "        time.sleep(0.005)\n"
    )
    ejecutor = _runner(tmp_path, stage_timeout_s=60)
    with pytest.raises(RenderStageTimeout, match="presupuesto"):
        ejecutor.run(
            [sys.executable, "-c", programa],
            stage="crecimiento",
            watch_path=destino,
            watch_max_bytes=512 * 1024,
        )
    # El archivo queda INVALIDO a proposito: no se presenta como exportacion.
    assert destino.exists()


def test_el_log_se_acota(tmp_path: Path) -> None:
    """Un fallo de codec puede escupir megabytes: el log tiene tope."""
    programa = (
        "import sys, time\n"
        "for _ in range(400):\n"
        "    sys.stdout.write('x' * 65536); sys.stdout.flush()\n"
        "    time.sleep(0.01)\n"
    )
    ejecutor = _runner(tmp_path, log_max_bytes=64 * 1024, stage_timeout_s=60)
    with pytest.raises(RenderStageTimeout, match="log"):
        ejecutor.run([sys.executable, "-c", programa], stage="log_grande")


def test_el_progreso_va_a_su_archivo_y_la_salida_al_log(tmp_path: Path) -> None:
    """El resumen JSON de la CLI no puede mezclarse con el progreso.

    `-progress` se inserta justo despues del ejecutable, que es donde FFmpeg
    espera sus opciones globales, y la salida del proceso va al log del
    trabajo, nunca al stdout del comando.
    """
    progreso = tmp_path / "progress.txt"
    ejecutor = _runner(tmp_path)
    resultado = ejecutor.run(
        [sys.executable, "-c", "print('ok')"], stage="progreso"
    )
    # Sin `progress_path` los argumentos van tal cual.
    assert "-progress" not in resultado.args
    assert resultado.log_path is not None and resultado.log_path.is_file()
    assert "ok" in resultado.log_path.read_text(encoding="utf-8")

    # Con `progress_path`, la opcion se coloca antes de los argumentos.
    fallido = ejecutor.run(
        [sys.executable, "-c", "print('ok')"],
        stage="progreso_con_archivo",
        progress_path=progreso,
    )
    assert fallido.args[1] == "-progress"
    assert fallido.args[2] == str(progreso)
