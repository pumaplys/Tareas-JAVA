"""Mezcla de narracion, efectos y musica, y normalizacion de sonoridad.

Reglas que este modulo sostiene:

* `narration.wav` del modulo 2 se usa UNA SOLA VEZ. Los clips por escena ya
  estan dentro del maestro: volver a concatenarlos duplicaria la voz y sus
  pausas.
* Los segmentos visuales no aportan audio (`use_source_audio=false` en
  `media.json`). El audio de un clip de video NUNCA entra en la mezcla.
* Solo se consumen `sound_cues` YA RESUELTOS y verificables del manifiesto de
  voz. Un asset obligatorio que falta es un error, no una excusa para declarar
  el paquete valido. Nada se descarga.
* La narracion no se acelera, no se recorta y no se alarga para cuadrar con la
  duracion redondeada del video.

Frecuencia de trabajo: 48 000 Hz, la del AAC de salida. Con una narracion de
24 000 Hz la conversion es exactamente 2x, asi que la mezcla tiene 2N muestras
por canal y no hay redondeo que documentar. Para otras frecuencias admitidas
se aplica una conversion racional y se registra el factor y el redondeo.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

from ..errors import ViralgenError
from .ffmpeg import ProcessRunner

logger = logging.getLogger(__name__)

#: Frecuencia de la mezcla de trabajo y de la pista AAC final.
WORK_SAMPLE_RATE_HZ = 48_000

#: Objetivos PROPIOS del proyecto, no requisitos universales de ninguna
#: plataforma. Se pueden cambiar por configuracion.
DEFAULT_LUFS_TARGET = -16.0
DEFAULT_TRUE_PEAK_CEILING_DBTP = -1.5

#: Tolerancias al comprobar el audio ya codificado.
LUFS_TOLERANCE_LU = 1.0
TRUE_PEAK_MAX_DBTP = -1.0

#: Un frame AAC son 1024 muestras. A 48 kHz, 21,33 ms.
AAC_FRAME_SAMPLES = 1024


class AudioMixError(ViralgenError):
    code = "audio_mix_error"


@dataclass(frozen=True)
class ResampleDecision:
    """Como se lleva la narracion a la frecuencia de trabajo."""

    source_rate_hz: int
    target_rate_hz: int
    source_samples: int
    expected_samples: int
    ratio_numerator: int
    ratio_denominator: int
    exact: bool

    def describe(self) -> dict:
        return {
            "source_rate_hz": self.source_rate_hz,
            "target_rate_hz": self.target_rate_hz,
            "source_samples": self.source_samples,
            "expected_samples": self.expected_samples,
            "ratio": f"{self.ratio_numerator}/{self.ratio_denominator}",
            "exact_integer_ratio": self.exact,
            "note": (
                "Conversion de relacion entera: la cuenta de muestras es exacta."
                if self.exact
                else "Conversion racional: la cuenta se redondea hacia arriba y se "
                     "declara aqui; la narracion no se estira ni se acorta."
            ),
        }


def plan_resample(
    *, source_rate_hz: int, source_samples: int, target_rate_hz: int = WORK_SAMPLE_RATE_HZ
) -> ResampleDecision:
    """Decide y DOCUMENTA la conversion de frecuencia de la narracion."""
    divisor = math.gcd(target_rate_hz, source_rate_hz)
    numerador = target_rate_hz // divisor
    denominador = source_rate_hz // divisor
    esperadas = -(-source_samples * numerador // denominador)  # ceil entero
    return ResampleDecision(
        source_rate_hz=source_rate_hz,
        target_rate_hz=target_rate_hz,
        source_samples=source_samples,
        expected_samples=esperadas,
        ratio_numerator=numerador,
        ratio_denominator=denominador,
        exact=denominador == 1,
    )


@dataclass(frozen=True)
class CuePlacement:
    """Un cue ya verificado, listo para colocarse en su tiempo global."""

    cue_type: str
    asset_id: str
    path: Path
    sha256: str
    start_s: float
    end_s: float
    gain_db: float

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s

    def describe(self) -> dict:
        return {
            "cue_type": self.cue_type,
            "asset_id": self.asset_id,
            "start_s": round(self.start_s, 6),
            "end_s": round(self.end_s, 6),
            "gain_db": self.gain_db,
            "sha256": self.sha256,
        }


@dataclass
class LoudnessMeasurement:
    """Medida de sonoridad. `usable=False` cuando el analisis no sirve."""

    integrated_lufs: float | None = None
    true_peak_dbtp: float | None = None
    loudness_range_lu: float | None = None
    threshold_lufs: float | None = None
    usable: bool = False
    note: str = ""

    def describe(self) -> dict:
        return {
            "integrated_lufs": self.integrated_lufs,
            "true_peak_dbtp": self.true_peak_dbtp,
            "loudness_range_lu": self.loudness_range_lu,
            "threshold_lufs": self.threshold_lufs,
            "usable": self.usable,
            "note": self.note,
        }


@dataclass
class AudioMixResult:
    """Resultado de la mezcla, con todo lo medido y lo decidido."""

    path: Path
    sample_rate_hz: int
    channels: int
    sample_count: int
    resample: ResampleDecision
    cues: list[CuePlacement]
    normalization: dict
    measured: LoudnessMeasurement
    ducking: dict
    warnings: list[str] = field(default_factory=list)

    def describe(self) -> dict:
        return {
            "work_sample_rate_hz": self.sample_rate_hz,
            "channels": self.channels,
            "work_sample_count": self.sample_count,
            "resample": self.resample.describe(),
            "cues_used": [cue.describe() for cue in self.cues],
            "normalization": dict(self.normalization),
            "measured": self.measured.describe(),
            "ducking": dict(self.ducking),
            "warnings": list(self.warnings),
        }


# ---------------------------------------------------------------------------
# Construccion del grafo de filtros
# ---------------------------------------------------------------------------


def build_mix_filter(
    *,
    cues: list[CuePlacement],
    narration_rate_hz: int,
    ducking_enabled: bool,
    ducking_reduction_db: float,
    fade_s: float,
) -> tuple[str, str]:
    """Grafo de mezcla. Devuelve `(filtergraph, etiqueta_de_salida)`.

    La entrada 0 es SIEMPRE la narracion; las siguientes son los cues en orden.
    Cada cue se coloca en su tiempo global con `adelay`, se recorta a su
    intervalo y recibe su ganancia y sus fades DENTRO de ese intervalo.

    El ducking baja la musica mientras suena la voz. Se implementa con una
    ganancia fija sobre la musica y no con compresion sidechain: el resultado
    es predecible y reproducible, que es lo que hace falta para poder
    comprobarlo. Los efectos puntuales no se atenuan.
    """
    partes: list[str] = [
        # La narracion solo cambia de frecuencia y de disposicion de canales.
        f"[0:a]aresample={WORK_SAMPLE_RATE_HZ}:resampler=soxr,"
        f"aformat=sample_fmts=fltp:channel_layouts=stereo[voz]"
    ]
    etiquetas = ["[voz]"]

    for indice, cue in enumerate(cues, start=1):
        etiqueta = f"[cue{indice}]"
        ganancia = cue.gain_db
        if ducking_enabled and cue.cue_type == "music":
            ganancia += ducking_reduction_db
        duracion = max(cue.duration_s, 0.0)
        fundido = min(fade_s, duracion / 2) if duracion > 0 else 0.0
        cadena = [
            f"[{indice}:a]aresample={WORK_SAMPLE_RATE_HZ}:resampler=soxr",
            "aformat=sample_fmts=fltp:channel_layouts=stereo",
            # Un asset mas corto que su intervalo NO se repite: se deja sonar
            # lo que dura. Repetirlo automaticamente seria inventar contenido.
            f"atrim=0:{duracion:.6f}",
            "asetpts=PTS-STARTPTS",
            f"volume={ganancia:.2f}dB",
        ]
        if fundido > 0.01:
            cadena.append(f"afade=t=in:st=0:d={fundido:.3f}")
            cadena.append(
                f"afade=t=out:st={max(duracion - fundido, 0):.3f}:d={fundido:.3f}"
            )
        retardo_ms = int(cue.start_s * 1000 + 0.5)
        if retardo_ms > 0:
            cadena.append(f"adelay={retardo_ms}|{retardo_ms}")
        partes.append(",".join(cadena) + etiqueta)
        etiquetas.append(etiqueta)

    if len(etiquetas) == 1:
        return ";".join(partes), "[voz]"

    partes.append(
        "".join(etiquetas)
        + f"amix=inputs={len(etiquetas)}:duration=first:dropout_transition=0:"
        "normalize=0[mezcla]"
    )
    return ";".join(partes), "[mezcla]"


# ---------------------------------------------------------------------------
# Medicion y normalizacion
# ---------------------------------------------------------------------------


def measure_loudness(
    runner: ProcessRunner, path: Path, *, stage: str = "loudnorm_analyze"
) -> LoudnessMeasurement:
    """Primera pasada de `loudnorm`: analiza sin escribir audio.

    Si la senal es silenciosa o el analisis no es interpretable, se DICE, no se
    inventa una medida.
    """
    args = [
        *runner.base_args()[:5],  # ejecutable, -hide_banner, -nostdin, -loglevel, error
        "-i", str(path),
        "-af",
        f"loudnorm=I={DEFAULT_LUFS_TARGET}:TP={DEFAULT_TRUE_PEAK_CEILING_DBTP}:"
        "LRA=11:print_format=json",
        "-f", "null", "-",
    ]
    # loudnorm imprime su JSON por stderr con loglevel info.
    args[args.index("error")] = "info"
    resultado = runner.run(args, stage=stage)
    return _parse_loudnorm(resultado.log_tail if resultado.log_path else "")


def _parse_loudnorm(texto: str) -> LoudnessMeasurement:
    """Extrae el bloque JSON que imprime `loudnorm`."""
    inicio = texto.rfind("{")
    fin = texto.rfind("}")
    if inicio < 0 or fin <= inicio:
        return LoudnessMeasurement(
            usable=False,
            note="loudnorm no devolvio un bloque JSON interpretable",
        )
    try:
        datos = json.loads(texto[inicio:fin + 1])
    except json.JSONDecodeError:
        return LoudnessMeasurement(
            usable=False, note="el bloque de loudnorm no es JSON valido"
        )

    def numero(clave: str) -> float | None:
        crudo = datos.get(clave)
        if crudo in (None, "", "-inf", "inf", "nan"):
            return None
        try:
            valor = float(crudo)
        except (TypeError, ValueError):
            return None
        return None if math.isinf(valor) or math.isnan(valor) else valor

    integrada = numero("input_i")
    pico = numero("input_tp")
    if integrada is None:
        return LoudnessMeasurement(
            integrated_lufs=None,
            true_peak_dbtp=pico,
            usable=False,
            note=(
                "la sonoridad integrada no es utilizable (senal silenciosa o "
                "demasiado corta). No se inventa una medida."
            ),
        )
    return LoudnessMeasurement(
        integrated_lufs=integrada,
        true_peak_dbtp=pico,
        loudness_range_lu=numero("input_lra"),
        threshold_lufs=numero("input_thresh"),
        usable=True,
        note="medido con loudnorm (EBU R128) sobre el audio decodificado",
    )


def loudnorm_second_pass_filter(medida: LoudnessMeasurement) -> str:
    """Filtro de la segunda pasada, con las medidas de la primera.

    Se fija `ar` explicitamente: `loudnorm` remuestrea a 192 kHz por dentro y
    dejarlo sin fijar produciria una frecuencia de salida inesperada.
    """
    if not medida.usable or medida.integrated_lufs is None:
        # Sin medida utilizable no se hace la segunda pasada: aplicar
        # parametros inventados desplazaria el nivel a ciegas.
        raise AudioMixError(
            "no hay una medida de sonoridad utilizable para la segunda pasada",
            details={"note": medida.note},
        )
    partes = [
        f"loudnorm=I={DEFAULT_LUFS_TARGET}",
        f"TP={DEFAULT_TRUE_PEAK_CEILING_DBTP}",
        "LRA=11",
        f"measured_I={medida.integrated_lufs:.2f}",
    ]
    if medida.true_peak_dbtp is not None:
        partes.append(f"measured_TP={medida.true_peak_dbtp:.2f}")
    if medida.loudness_range_lu is not None:
        partes.append(f"measured_LRA={medida.loudness_range_lu:.2f}")
    if medida.threshold_lufs is not None:
        partes.append(f"measured_thresh={medida.threshold_lufs:.2f}")
    partes.append("linear=true")
    partes.append("print_format=summary")
    return ":".join(partes) + f",aresample={WORK_SAMPLE_RATE_HZ}:resampler=soxr"


def check_loudness(
    medida: LoudnessMeasurement,
    *,
    target_lufs: float = DEFAULT_LUFS_TARGET,
    tolerance_lu: float = LUFS_TOLERANCE_LU,
    max_true_peak_dbtp: float = TRUE_PEAK_MAX_DBTP,
) -> list[str]:
    """Comprueba el audio FINAL decodificado. Devuelve los incumplimientos.

    Un incumplimiento conserva el candidato como `needs_review`: no se abre un
    bucle de recodificacion automatica buscando que salga el numero.
    """
    problemas: list[str] = []
    if not medida.usable:
        problemas.append(
            f"no se pudo medir la sonoridad del audio final: {medida.note}"
        )
        return problemas
    assert medida.integrated_lufs is not None
    desviacion = abs(medida.integrated_lufs - target_lufs)
    if desviacion > tolerance_lu:
        problemas.append(
            f"sonoridad integrada {medida.integrated_lufs:.2f} LUFS, fuera de "
            f"{target_lufs:.1f} ±{tolerance_lu:.1f} LU"
        )
    if medida.true_peak_dbtp is not None and medida.true_peak_dbtp > max_true_peak_dbtp:
        problemas.append(
            f"pico verdadero {medida.true_peak_dbtp:.2f} dBTP, por encima de "
            f"{max_true_peak_dbtp:.1f} dBTP"
        )
    return problemas


def aac_tolerance_samples() -> int:
    """Margen tecnico admitido en la pista AAC: un frame de 1024 muestras.

    El codificador AAC anade priming al principio y padding al final. Ese
    margen NO sirve para tapar voz truncada: solo cubre el relleno del codec.
    """
    return AAC_FRAME_SAMPLES
