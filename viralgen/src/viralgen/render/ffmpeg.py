"""Ejecucion de FFmpeg/ffprobe y deteccion de capacidades REALES.

Reglas de ejecucion, todas obligatorias:

* Lista de argumentos y ``shell=False``. Nunca una cadena de comando.
* ``stdin`` cerrado y ``-nostdin``: FFmpeg no puede quedarse esperando a que
  alguien conteste "sobrescribir?".
* Timeout por etapa y terminacion del GRUPO de procesos: matar solo al padre
  deja hijos codificando y ocupando disco.
* Logs acotados y rotados. Un fallo de codec puede escupir megabytes.
* El progreso legible por maquina sale por ``-progress``, a un archivo propio,
  jamas al stdout de la CLI (que lleva el resumen JSON).

Las capacidades se leen de los EJECUTABLES INSTALADOS, no de la documentacion
en linea: el paquete de Ubuntu puede no traer un codificador o un filtro que la
documentacion describe.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..errors import ConfigError, ViralgenError
from ..errors import ExitCode

logger = logging.getLogger(__name__)

#: Version minima REALMENTE probada en esta entrega. No se asume nada sobre
#: versiones anteriores: si alguien ejecuta con una mas vieja, se avisa.
MIN_FFMPEG_VERSION = (6, 1)

#: Codificadores y filtros sin los cuales el montaje no se puede hacer.
REQUIRED_ENCODERS = ("libx264", "aac")
REQUIRED_FILTERS = (
    "ass",       # subtitulos integrados por libass
    "scale",     # encuadre
    "pad",       # politica contain
    "crop",      # politica crop y recorte de clips
    "fps",       # normalizacion a CFR
    "format",    # yuv420p
    "setpts",    # desplazamiento de PTS para el reloj global de subtitulos
    "atrim",     # recorte de la mezcla
    "adelay",    # colocacion de cues en su tiempo global
    "volume",    # ganancias
    "afade",     # fades de musica/efectos
    "amix",      # mezcla
    "aresample", # conversion de frecuencia documentada
    "loudnorm",  # normalizacion de sonoridad
)

#: Tamano maximo de salida capturada de un subproceso, por seguridad.
MAX_CAPTURE_BYTES = 2 * 1024 * 1024


class RenderToolError(ViralgenError):
    """FFmpeg/ffprobe no esta, no sirve o fallo."""

    exit_code = ExitCode.CONFIG
    code = "render_tool_error"


class RenderStageError(ViralgenError):
    """Una etapa de codificacion fallo, agoto su tiempo o fue cancelada."""

    exit_code = ExitCode.VALIDATION
    code = "render_stage_error"


class RenderStageTimeout(RenderStageError):
    code = "render_stage_timeout"


@dataclass(frozen=True)
class ToolCapabilities:
    """Lo que los ejecutables instalados SABEN hacer."""

    ffmpeg_path: str
    ffprobe_path: str
    ffmpeg_version: str
    ffprobe_version: str
    version_tuple: tuple[int, ...]
    encoders: frozenset[str]
    filters: frozenset[str]
    has_libass: bool
    configuration: str = ""
    missing: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        return not self.missing

    def describe(self) -> dict:
        return {
            "ffmpeg_version": self.ffmpeg_version,
            "ffprobe_version": self.ffprobe_version,
            "min_tested_version": ".".join(str(n) for n in MIN_FFMPEG_VERSION),
            "meets_min_tested_version": self.version_tuple >= MIN_FFMPEG_VERSION,
            "encoders_required": list(REQUIRED_ENCODERS),
            "filters_required": list(REQUIRED_FILTERS),
            "libass": self.has_libass,
            "missing": list(self.missing),
        }


def tool_available(path: str) -> bool:
    return shutil.which(path) is not None


def _capture(args: list[str], *, timeout_s: int = 30) -> str:
    """Ejecuta una consulta corta y devuelve stdout+stderr acotados."""
    try:
        proceso = subprocess.run(  # noqa: S603 - lista de argumentos, shell=False
            args,
            shell=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RenderToolError(f"no se encuentra el ejecutable {args[0]!r}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RenderToolError(
            f"{args[0]!r} no respondio en {timeout_s} s"
        ) from exc
    salida = (proceso.stdout or b"") + (proceso.stderr or b"")
    return salida[:MAX_CAPTURE_BYTES].decode("utf-8", errors="replace")


_VERSION_RE = re.compile(r"version\s+n?(\d+)\.(\d+)(?:\.(\d+))?")


def _parse_version(texto: str) -> tuple[str, tuple[int, ...]]:
    primera = texto.splitlines()[0] if texto else ""
    encontrado = _VERSION_RE.search(primera)
    if not encontrado:
        # Una compilacion desde git puede no traer numeros. Se conserva la
        # cadena y se deja la tupla vacia: no se inventa un numero.
        return primera.strip()[:200], ()
    numeros = tuple(int(g) for g in encontrado.groups() if g is not None)
    return primera.strip()[:200], numeros


def probe_capabilities(ffmpeg_path: str, ffprobe_path: str) -> ToolCapabilities:
    """Interroga a los ejecutables instalados. Nunca supone nada."""
    faltan: list[str] = []
    if not tool_available(ffmpeg_path):
        faltan.append("ffmpeg")
    if not tool_available(ffprobe_path):
        faltan.append("ffprobe")
    if faltan:
        return ToolCapabilities(
            ffmpeg_path=ffmpeg_path,
            ffprobe_path=ffprobe_path,
            ffmpeg_version="",
            ffprobe_version="",
            version_tuple=(),
            encoders=frozenset(),
            filters=frozenset(),
            has_libass=False,
            missing=tuple(faltan),
        )

    version_ffmpeg_txt = _capture([ffmpeg_path, "-hide_banner", "-version"])
    version_ffprobe_txt = _capture([ffprobe_path, "-hide_banner", "-version"])
    version_ffmpeg, tupla = _parse_version(version_ffmpeg_txt)
    version_ffprobe, _ = _parse_version(version_ffprobe_txt)

    configuracion = ""
    for linea in version_ffmpeg_txt.splitlines():
        if linea.startswith("configuration:"):
            configuracion = linea[len("configuration:"):].strip()
            break

    codificadores = _listed_names(
        _capture([ffmpeg_path, "-hide_banner", "-encoders"]), _ENCODER_RE
    )
    filtros = _listed_names(
        _capture([ffmpeg_path, "-hide_banner", "-filters"]), _FILTER_RE
    )

    # libass se comprueba por el filtro `ass`, que es lo que realmente se usa;
    # la cadena de `configuration` solo lo corrobora.
    tiene_libass = "ass" in filtros
    for nombre in REQUIRED_ENCODERS:
        if nombre not in codificadores:
            faltan.append(f"codificador:{nombre}")
    for nombre in REQUIRED_FILTERS:
        if nombre not in filtros:
            faltan.append(f"filtro:{nombre}")
    if not tiene_libass:
        faltan.append("libass")

    return ToolCapabilities(
        ffmpeg_path=ffmpeg_path,
        ffprobe_path=ffprobe_path,
        ffmpeg_version=version_ffmpeg,
        ffprobe_version=version_ffprobe,
        version_tuple=tupla,
        encoders=frozenset(codificadores),
        filters=frozenset(filtros),
        has_libass=tiene_libass,
        configuration=configuracion[:2000],
        missing=tuple(faltan),
    )


#: Un nombre de codificador o de filtro empieza por letra o digito. Exigirlo
#: descarta las lineas de LEYENDA de `-encoders`, que tienen la misma forma
#: que una entrada (" V..... = Video") y colarian un "codificador" llamado "=".
_NAME = r"([A-Za-z0-9][\w.-]*)"

#: `-encoders` imprime seis banderas (p. ej. "V....D") y luego el nombre.
_ENCODER_RE = re.compile(r"^\s*[VAS.][F.][S.][X.][B.][D.]\s+" + _NAME + r"\s")

#: `-filters` imprime tres banderas (p. ej. "T.C") y una firma con "->".
#: Su cabecera es una leyenda, no una linea de guiones: no se puede buscar
#: un separador como en `-encoders`, hay que reconocer las entradas.
_FILTER_RE = re.compile(r"^\s*[TA.][S.][C.]\s+" + _NAME + r"\s+\S*->\S*")


def _listed_names(texto: str, pattern: re.Pattern[str]) -> set[str]:
    """Extrae los nombres de un listado de FFmpeg con su patron de entrada.

    Se reconoce cada ENTRADA en vez de recortar por una cabecera: los dos
    listados la tienen distinta y `-filters` ni siquiera lleva separador.
    """
    return {
        encontrado.group(1)
        for encontrado in (pattern.match(linea) for linea in texto.splitlines())
        if encontrado is not None
    }


def require_tools(ffmpeg_path: str, ffprobe_path: str) -> ToolCapabilities:
    """Exige capacidades completas ANTES de codificar nada."""
    capacidades = probe_capabilities(ffmpeg_path, ffprobe_path)
    if capacidades.missing:
        raise ConfigError(
            "El montaje necesita FFmpeg con estas capacidades y faltan: "
            + ", ".join(capacidades.missing)
            + ". Instalalo (apt install ffmpeg) o ajusta VIRALGEN_FFMPEG_PATH / "
            "VIRALGEN_FFPROBE_PATH.",
            details={"missing": list(capacidades.missing)},
        )
    return capacidades


# ---------------------------------------------------------------------------
# Ejecucion de etapas
# ---------------------------------------------------------------------------


@dataclass
class StageResult:
    """Resultado de una etapa de FFmpeg."""

    args: list[str]
    returncode: int
    elapsed_s: float
    log_path: Path | None
    log_tail: str
    progress_path: Path | None = None

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclass
class ProcessRunner:
    """Ejecuta FFmpeg con limites de tiempo, de log y de grupo de procesos."""

    ffmpeg_path: str
    ffprobe_path: str
    stage_timeout_s: int = 600
    log_max_bytes: int = 5 * 1024 * 1024
    threads: int = 2
    filter_threads: int = 2
    #: Crecimiento maximo permitido de un archivo de salida vigilado, en bytes.
    watch_interval_s: float = 0.5
    log_dir: Path | None = None
    _logs_written: int = field(default=0, init=False)

    def base_args(self) -> list[str]:
        """Prefijo comun: sin banner, sin stdin, sobrescritura explicita."""
        return [
            self.ffmpeg_path,
            "-hide_banner",
            "-nostdin",
            "-loglevel", "error",
            "-y",
            "-threads", str(self.threads),
            "-filter_threads", str(self.filter_threads),
            "-filter_complex_threads", str(self.filter_threads),
        ]

    def run(
        self,
        args: list[str],
        *,
        stage: str,
        cwd: Path | None = None,
        timeout_s: int | None = None,
        watch_path: Path | None = None,
        watch_max_bytes: int | None = None,
        progress_path: Path | None = None,
    ) -> StageResult:
        """Ejecuta una etapa y devuelve su resultado.

        `watch_path`/`watch_max_bytes` detienen el proceso si el archivo de
        salida crece mas de lo presupuestado: CRF no impone tamano, asi que un
        contenido patologico podria llenar el disco. El archivo resultante
        queda invalido a proposito, no se presenta como exportacion correcta.
        """
        limite = timeout_s if timeout_s is not None else self.stage_timeout_s
        log_path = self._log_path(stage)
        inicio = time.monotonic()

        if progress_path is not None:
            args = [*args[:1], "-progress", str(progress_path), "-stats_period", "1", *args[1:]]

        logger.debug("etapa %s: %d argumentos", stage, len(args))
        salida_log = open(log_path, "wb") if log_path else subprocess.DEVNULL
        try:
            proceso = subprocess.Popen(  # noqa: S603 - lista de argumentos, shell=False
                args,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=salida_log,
                stderr=subprocess.STDOUT,
                cwd=str(cwd) if cwd else None,
                # Grupo propio: al terminar se mata al grupo entero, no solo al
                # padre, para no dejar hijos codificando.
                start_new_session=True,
            )
            motivo = self._wait(
                proceso,
                limite=limite,
                log_path=log_path,
                watch_path=watch_path,
                watch_max_bytes=watch_max_bytes,
            )
        finally:
            if salida_log is not subprocess.DEVNULL:
                salida_log.close()

        transcurrido = time.monotonic() - inicio
        cola = self._tail(log_path)
        if motivo is not None:
            raise RenderStageTimeout(
                f"etapa {stage}: {motivo}",
                details={"stage": stage, "log_tail": cola, "elapsed_s": round(transcurrido, 3)},
            )
        return StageResult(
            args=list(args),
            returncode=proceso.returncode,
            elapsed_s=transcurrido,
            log_path=log_path,
            log_tail=cola,
            progress_path=progress_path,
        )

    def run_checked(self, args: list[str], *, stage: str, **kwargs) -> StageResult:
        """Como `run`, pero un codigo distinto de cero es un error de etapa."""
        resultado = self.run(args, stage=stage, **kwargs)
        if not resultado.ok:
            raise RenderStageError(
                f"etapa {stage}: FFmpeg termino con codigo {resultado.returncode}",
                details={
                    "stage": stage,
                    "returncode": resultado.returncode,
                    "log_tail": resultado.log_tail,
                },
            )
        return resultado

    def _wait(
        self,
        proceso: subprocess.Popen,
        *,
        limite: int,
        log_path: Path | None,
        watch_path: Path | None,
        watch_max_bytes: int | None,
    ) -> str | None:
        """Espera vigilando tiempo, tamano del log y tamano de la salida."""
        fin = time.monotonic() + limite
        while True:
            try:
                proceso.wait(timeout=self.watch_interval_s)
                return None
            except subprocess.TimeoutExpired:
                pass
            if time.monotonic() > fin:
                self._terminate(proceso)
                return f"se agoto el tiempo de la etapa ({limite} s)"
            if watch_path is not None and watch_max_bytes is not None:
                try:
                    if watch_path.stat().st_size > watch_max_bytes:
                        self._terminate(proceso)
                        return (
                            f"la salida supero el presupuesto de "
                            f"{watch_max_bytes} bytes; el archivo queda invalido"
                        )
                except FileNotFoundError:
                    pass
            if log_path is not None:
                try:
                    if log_path.stat().st_size > self.log_max_bytes:
                        self._terminate(proceso)
                        return f"el log supero {self.log_max_bytes} bytes"
                except FileNotFoundError:
                    pass

    @staticmethod
    def _terminate(proceso: subprocess.Popen) -> None:
        """Termina el GRUPO de procesos, con SIGKILL si hace falta."""
        try:
            grupo = os.getpgid(proceso.pid)
        except (ProcessLookupError, OSError):
            return
        for senal, espera in ((signal.SIGTERM, 5.0), (signal.SIGKILL, 5.0)):
            try:
                os.killpg(grupo, senal)
            except (ProcessLookupError, OSError):
                return
            try:
                proceso.wait(timeout=espera)
                return
            except subprocess.TimeoutExpired:
                continue

    def _log_path(self, stage: str) -> Path | None:
        if self.log_dir is None:
            return None
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._logs_written += 1
        seguro = re.sub(r"[^a-zA-Z0-9_.-]", "_", stage)[:60]
        return self.log_dir / f"{self._logs_written:03d}_{seguro}.log"

    @staticmethod
    def _tail(log_path: Path | None, limit: int = 2000) -> str:
        if log_path is None or not log_path.is_file():
            return ""
        try:
            datos = log_path.read_bytes()[-limit:]
        except OSError:
            return ""
        return datos.decode("utf-8", errors="replace")
