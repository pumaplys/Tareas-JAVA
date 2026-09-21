"""Proveedor de voz simulado y determinista.

Funciona sin claves, sin red y sin FFmpeg: genera PCM de prueba con la
biblioteca estandar (`array` + `wave`) y devuelve ya un WAV en el formato
interno del proyecto, asi que no hay nada que decodificar.

QUE ES Y QUE NO ES: son SENALES DE PRUEBA (un tono con envolvente), no voz
hablada. Sus tiempos por caracter se reparten con un peso por tipo de caracter:
sirven para ejercitar el recorrido completo y para que las pruebas sean
deterministas, NO son evidencia de la precision del proveedor real. Todo lo que
produce se marca con `simulation=true`.
"""

from __future__ import annotations

import array
import hashlib
import io
import math
import time
import wave
from pathlib import Path

from ..alignment import CharAlignment
from ..audio import PcmFormat
from .base import SynthesisRequest, SynthesisResult, VoiceBudget, VoiceProvider

MOCK_MODEL_ID = "mock-voice-v1"

#: Peso relativo de cada tipo de caracter al repartir la duracion del clip.
_WEIGHT_ALNUM = 1.0
_WEIGHT_SPACE = 0.45
_WEIGHT_OTHER = 0.30


class MockVoiceProvider(VoiceProvider):
    name = "mock"
    supports_forced_alignment = False

    def __init__(self, *, settings, seed: int | None = None) -> None:
        self.settings = settings
        self.seed = seed
        self.model_id = MOCK_MODEL_ID
        self.fmt = PcmFormat(
            sample_rate_hz=settings.voice_sample_rate_hz,
            channels=settings.voice_channels,
            sample_width_bytes=settings.voice_sample_width_bytes,
        )

    # -- API ---------------------------------------------------------------

    def synthesize(self, request: SynthesisRequest, budget: VoiceBudget) -> SynthesisResult:
        token = budget.reserve("synthesis", request.scene_id)
        started = time.monotonic()
        try:
            clip_samples = self._clip_samples(request)
            audio = self._pcm_wav(request, clip_samples)
            alignment = self._alignment(request.text, clip_samples / self.fmt.sample_rate_hz)
            latency_ms = int((time.monotonic() - started) * 1000)
            resultado = SynthesisResult(
                audio_bytes=audio,
                audio_format="wav",
                alignment=alignment,
                normalized_alignment=None,
                request_id=f"mock-{self._digest(request)[:16]}",
                latency_ms=latency_ms,
                http_status=200,
                characters_sent=len(request.text),
            )
        except Exception as exc:  # pragma: no cover - el mock no deberia fallar
            budget.settle(token, status="error", error_code="mock_error", error_message=str(exc))
            raise
        budget.settle(
            token,
            status="ok",
            request_id=resultado.request_id,
            characters=resultado.characters_sent,
            latency_ms=latency_ms,
            http_status=200,
        )
        return resultado

    def decode_to_internal(
        self, result: SynthesisResult, source: Path, destination: Path
    ) -> None:
        """El simulado ya entrega el formato interno: solo copia los bytes."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())

    # -- Interno -----------------------------------------------------------

    def _digest(self, request: SynthesisRequest) -> str:
        material = "|".join(
            [
                str(self.seed),
                request.scene_id,
                request.text,
                request.previous_text or "",
                request.next_text or "",
                request.voice_id,
            ]
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _clip_samples(self, request: SynthesisRequest) -> int:
        """Duracion del clip simulado.

        Parte de la estimacion del modulo 1 y le aplica una desviacion
        determinista de hasta un +-3 %, para que la duracion MEDIDA no coincida
        exactamente con la estimada y el recorrido ejercite las tolerancias.
        """
        base_s = request.hint_speech_duration_s
        if not base_s or base_s <= 0:
            base_s = max(0.4, len(request.text) / 14.0)
        desvio = (int(self._digest(request)[:4], 16) % 61 - 30) / 1000.0  # [-0.030, +0.030]
        muestras = int(round(base_s * (1.0 + desvio) * self.fmt.sample_rate_hz))
        return max(muestras, self.fmt.sample_rate_hz // 10)

    def _pcm_wav(self, request: SynthesisRequest, clip_samples: int) -> bytes:
        """Tono de prueba con envolvente suave, en el formato interno."""
        semilla = int(self._digest(request)[:8], 16)
        frecuencia = 140.0 + (semilla % 60)
        sample_rate = self.fmt.sample_rate_hz
        amplitud = 0.18 * (2 ** (self.fmt.sample_width_bytes * 8 - 1) - 1)

        muestras = array.array("h")
        for indice in range(clip_samples):
            posicion = indice / clip_samples
            # Envolvente: entra y sale sin chasquidos.
            envolvente = math.sin(math.pi * posicion) ** 0.5
            valor = amplitud * envolvente * math.sin(2.0 * math.pi * frecuencia * indice / sample_rate)
            muestras.append(int(valor))

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as handle:
            handle.setnchannels(self.fmt.channels)
            handle.setsampwidth(self.fmt.sample_width_bytes)
            handle.setframerate(sample_rate)
            handle.writeframes(muestras.tobytes())
        return buffer.getvalue()

    def _alignment(self, text: str, duration_s: float) -> CharAlignment:
        """Tiempos por caracter sinteticos, ponderados por tipo de caracter."""
        if not text:
            return CharAlignment([], [], [])
        pesos = [
            _WEIGHT_SPACE if caracter.isspace()
            else _WEIGHT_ALNUM if caracter.isalnum()
            else _WEIGHT_OTHER
            for caracter in text
        ]
        total = sum(pesos) or 1.0
        inicios: list[float] = []
        finales: list[float] = []
        cursor = 0.0
        for peso in pesos:
            paso = duration_s * peso / total
            inicios.append(round(cursor, 6))
            cursor += paso
            finales.append(round(min(cursor, duration_s), 6))
        return CharAlignment(list(text), inicios, finales)
