"""Recorrido del modulo 2: admision, sintesis, medicion y reanudacion.

Todo con el proveedor simulado: sin red, sin claves y sin FFmpeg.
"""

from __future__ import annotations

import json
import wave
from pathlib import Path

import pytest

from viralgen.errors import ExitCode, IdempotencyConflictError, ViralgenError
from viralgen.pipeline import JobRequest, Pipeline
from viralgen.voice.pipeline import VoiceJobRequest, VoicePipeline
from viralgen.voice.providers.mock import MockVoiceProvider
from viralgen.voice.schemas import VoiceManifest, VoiceStatus
from viralgen.voice.storage import VoiceStorage


def _manifest(outcome) -> VoiceManifest:
    return VoiceManifest.model_validate(
        json.loads(Path(outcome.manifest_path).read_text(encoding="utf-8"))
    )


# ---------------------------------------------------------------------------
# Recorrido completo simulado
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "profile_id", ["infantil_cuentos", "curiosidades_corto", "curiosidades_largo"]
)
def test_recorrido_mock_por_perfil(voice_settings, demo_pack, profile_id) -> None:
    peticion = JobRequest(
        command="generate",
        profile_id=profile_id,
        topic="pieza que reparte la fuerza",
        source_pack=demo_pack if profile_id.startswith("curiosidades") else None,
        simulation=True,
        seed=5,
        job_key=f"voz-{profile_id}",
    )
    guion = Pipeline(voice_settings, peticion).run()
    assert guion.exit_code == ExitCode.OK

    resultado = VoicePipeline(
        voice_settings,
        VoiceJobRequest(
            script_path=Path(guion.script_path), voice_key=f"v-{profile_id}",
            simulation=True, seed=5,
        ),
    ).run()

    assert resultado.exit_code == ExitCode.OK
    assert resultado.status == VoiceStatus.READY.value
    manifiesto = _manifest(resultado)
    assert manifiesto.document_type == "voice_manifest"
    assert manifiesto.schema_version == "1.0"
    assert manifiesto.simulation is True
    # Una salida simulada NUNCA es admisible para montaje.
    assert manifiesto.control.admissible_for_assembly is False

    # El guion no se ha tocado.
    guion_datos = json.loads(Path(guion.script_path).read_text(encoding="utf-8"))
    assert guion_datos["video"]["actual_duration_s"] is None


def test_el_guion_de_entrada_no_cambia_ni_un_byte(voice_settings, script_path, run_voice) -> None:
    antes = script_path.read_bytes()
    run_voice(script_path)
    assert script_path.read_bytes() == antes


def test_muestras_exactas_del_maestro(voice_settings, script_path, run_voice) -> None:
    resultado = run_voice(script_path)
    manifiesto = _manifest(resultado)
    base = Path(resultado.manifest_path).parent

    with wave.open(str(base / manifiesto.master.path)) as handle:
        reales = handle.getnframes()
        assert handle.getframerate() == 24_000
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2

    assert reales == manifiesto.master.sample_count
    # La pausa de cada escena esta contada exactamente una vez.
    assert reales == sum(e.clip_samples + e.pause_samples for e in manifiesto.scenes)
    assert manifiesto.master.actual_duration_s == pytest.approx(reales / 24_000)


def test_las_escenas_cubren_el_maestro_sin_huecos(voice_settings, script_path, run_voice) -> None:
    manifiesto = _manifest(run_voice(script_path))
    cursor = 0
    for escena in sorted(manifiesto.scenes, key=lambda item: item.order):
        assert escena.start_sample == cursor
        assert escena.end_sample == escena.start_sample + escena.clip_samples + escena.pause_samples
        cursor = escena.end_sample
    assert cursor == manifiesto.master.sample_count


def test_las_palabras_caen_dentro_del_clip_de_su_escena(
    voice_settings, script_path, run_voice
) -> None:
    manifiesto = _manifest(run_voice(script_path))
    por_escena = {escena.scene_id: escena for escena in manifiesto.scenes}
    assert manifiesto.words
    for palabra in manifiesto.words:
        escena = por_escena[palabra.scene_id]
        # Tiempos globales, y nunca dentro del silencio anadido.
        assert palabra.start_s >= escena.start_s - 1e-6
        assert palabra.end_s <= escena.clip_end_s + 1e-6


def test_la_pausa_no_se_envia_al_proveedor(voice_settings, script_path) -> None:
    """El texto sintetizado es el del guion: la pausa se anade como silencio."""
    enviados: list[str] = []

    class Espia(MockVoiceProvider):
        def synthesize(self, request, budget):
            enviados.append(request.text)
            return super().synthesize(request, budget)

    resultado = VoicePipeline(
        voice_settings,
        VoiceJobRequest(script_path=script_path, voice_key="espia", simulation=True, seed=5),
        provider=Espia(settings=voice_settings, seed=5),
    ).run()
    guion = json.loads(script_path.read_text(encoding="utf-8"))
    assert enviados == [escena["narration_text"] for escena in guion["scenes"]]
    manifiesto = _manifest(resultado)
    assert any(escena.pause_samples > 0 for escena in manifiesto.scenes)


# ---------------------------------------------------------------------------
# Admision de la entrada
# ---------------------------------------------------------------------------


class ProveedorEspia(MockVoiceProvider):
    """Cuenta las sintesis que se completan de verdad."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.llamadas = 0

    def synthesize(self, request, budget):
        resultado = super().synthesize(request, budget)
        self.llamadas += 1
        return resultado


def test_entrada_simulada_bloqueada_en_modo_real(voice_settings, script_path) -> None:
    """El modo real corta ANTES de cualquier peticion."""
    proveedor = ProveedorEspia(settings=voice_settings, seed=5)
    resultado = VoicePipeline(
        voice_settings,
        VoiceJobRequest(script_path=script_path, voice_key="real-con-mock", simulation=False),
        provider=proveedor,
    ).run()
    assert resultado.exit_code == ExitCode.VALIDATION
    assert resultado.status == "blocked"
    assert resultado.error_code == "voice_input_not_admitted"
    assert any("simulation" in motivo for motivo in resultado.admission_reasons)
    assert proveedor.llamadas == 0
    assert resultado.requests_total == 0


def test_borrador_needs_review_bloqueado_incluso_en_mock(
    voice_settings, script_path, tmp_path
) -> None:
    """--mock no debilita las comprobaciones editoriales del guion."""
    datos = json.loads(script_path.read_text(encoding="utf-8"))
    datos["control"]["production_status"] = "needs_review"
    datos["control"]["warnings"] = ["aviso de prueba"]
    borrador = tmp_path / "borrador.json"
    borrador.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")

    proveedor = ProveedorEspia(settings=voice_settings, seed=5)
    resultado = VoicePipeline(
        voice_settings,
        VoiceJobRequest(script_path=borrador, voice_key="borrador", simulation=True),
        provider=proveedor,
    ).run()
    assert resultado.exit_code == ExitCode.VALIDATION
    assert resultado.status == "blocked"
    assert proveedor.llamadas == 0


def test_guion_manipulado_queda_bloqueado(voice_settings, script_path, tmp_path) -> None:
    """Un guion con conteos manipulados no pasa las comprobaciones del modulo 1."""
    datos = json.loads(script_path.read_text(encoding="utf-8"))
    datos["scenes"][0]["word_count"] = 3  # incoherente con el texto
    roto = tmp_path / "roto.json"
    roto.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")

    proveedor = ProveedorEspia(settings=voice_settings, seed=5)
    resultado = VoicePipeline(
        voice_settings,
        VoiceJobRequest(script_path=roto, voice_key="roto", simulation=True),
        provider=proveedor,
    ).run()
    assert resultado.exit_code == ExitCode.VALIDATION
    assert resultado.status == "blocked"
    assert proveedor.llamadas == 0


# ---------------------------------------------------------------------------
# Idempotencia, presupuesto y reanudacion
# ---------------------------------------------------------------------------


def test_reutilizar_la_voice_key_no_gasta_peticiones(voice_settings, script_path, run_voice) -> None:
    primero = run_voice(script_path, voice_key="reuso")
    assert primero.requests_new > 0

    segundo = run_voice(script_path, voice_key="reuso")
    assert segundo.reused is True
    assert segundo.requests_new == 0
    assert segundo.requests_total == primero.requests_total
    assert segundo.voice_run_id == primero.voice_run_id


def test_conflicto_de_voice_key(voice_settings, script_path, run_voice) -> None:
    run_voice(script_path, voice_key="clave", seed=5)
    with pytest.raises(IdempotencyConflictError):
        # Cambia la identidad de la sintesis: mismo key, otra solicitud.
        voice_settings.voice_output_format = "mp3_22050_32"
        run_voice(script_path, voice_key="clave", seed=5)


class ProveedorQueFallaUnaEscena(MockVoiceProvider):
    """Falla al sintetizar una escena concreta la primera vez."""

    def __init__(self, fallar_en: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.fallar_en = fallar_en
        self.sintetizadas: list[str] = []

    def synthesize(self, request, budget):
        if request.scene_id == self.fallar_en:
            budget.reserve("synthesis", request.scene_id)
            raise ViralgenError(f"fallo simulado en {request.scene_id}")
        self.sintetizadas.append(request.scene_id)
        return super().synthesize(request, budget)


def test_reanudacion_tras_fallo_en_una_escena(voice_settings, script_path) -> None:
    peticion = VoiceJobRequest(
        script_path=script_path, voice_key="reanudar", simulation=True, seed=5
    )
    fallido = ProveedorQueFallaUnaEscena(
        "sc_03", settings=voice_settings, seed=5
    )
    primero = VoicePipeline(voice_settings, peticion, provider=fallido).run()
    assert primero.status == "failed"
    assert fallido.sintetizadas == ["sc_01", "sc_02"]

    # Segunda ejecucion: reutiliza los dos clips ya guardados.
    segundo_proveedor = ProveedorEspia(settings=voice_settings, seed=5)
    segundo = VoicePipeline(voice_settings, peticion, provider=segundo_proveedor).run()
    assert segundo.exit_code == ExitCode.OK
    manifiesto = _manifest(segundo)
    assert segundo_proveedor.llamadas == len(manifiesto.scenes) - 2


def test_el_presupuesto_persiste_entre_procesos(voice_settings, script_path) -> None:
    voice_settings.voice_max_requests_per_job = 3
    peticion = VoiceJobRequest(
        script_path=script_path, voice_key="presupuesto", simulation=True, seed=5
    )

    primero = VoicePipeline(
        voice_settings, peticion, provider=MockVoiceProvider(settings=voice_settings, seed=5)
    ).run()
    assert primero.status == "failed"
    assert primero.error_code == "voice_budget_exceeded"
    assert primero.requests_total == 3

    # Otro "proceso": el contador NO se reinicia, y los clips ya hechos se reusan.
    segundo_proveedor = ProveedorEspia(settings=voice_settings, seed=5)
    segundo = VoicePipeline(voice_settings, peticion, provider=segundo_proveedor).run()
    assert segundo.status == "failed"
    assert segundo.error_code == "voice_budget_exceeded"
    assert segundo.requests_total == 3
    assert segundo_proveedor.llamadas == 0  # no se resintetiza lo ya guardado


# ---------------------------------------------------------------------------
# needs_review y exportacion fallida
# ---------------------------------------------------------------------------


class ProveedorSinAlineacion(MockVoiceProvider):
    def synthesize(self, request, budget):
        resultado = super().synthesize(request, budget)
        resultado.alignment = None
        resultado.normalized_alignment = None
        return resultado


def test_sin_alineacion_utilizable_queda_needs_review(voice_settings, script_path) -> None:
    """Se conserva el audio, pero el manifiesto no es admisible."""
    resultado = VoicePipeline(
        voice_settings,
        VoiceJobRequest(script_path=script_path, voice_key="sin-align", simulation=True, seed=5),
        provider=ProveedorSinAlineacion(settings=voice_settings, seed=5),
    ).run()
    assert resultado.exit_code == ExitCode.NEEDS_REVIEW
    assert resultado.status == VoiceStatus.NEEDS_REVIEW.value
    manifiesto = _manifest(resultado)
    assert manifiesto.control.admissible_for_assembly is False
    assert any(issue.blocking for issue in manifiesto.control.issues)
    assert manifiesto.words == []
    # El audio se conserva.
    base = Path(resultado.manifest_path).parent
    assert (base / manifiesto.master.path).is_file()
    for escena in manifiesto.scenes:
        assert (base / escena.clip_path).is_file()
        assert escena.alignment_status.value in {"missing", "rejected"}


def test_exportacion_fallida_se_recupera_sin_resintetizar(
    voice_settings, script_path, monkeypatch
) -> None:
    import viralgen.voice.pipeline as modulo

    def explota(path, data):
        raise OSError("disco de prueba lleno")

    monkeypatch.setattr(modulo, "atomic_write_json", explota)
    peticion = VoiceJobRequest(
        script_path=script_path, voice_key="export", simulation=True, seed=5
    )
    fallido = VoicePipeline(
        voice_settings, peticion, provider=MockVoiceProvider(settings=voice_settings, seed=5)
    ).run()
    assert fallido.status == "failed"
    assert fallido.manifest_path is None
    gastadas = fallido.requests_total
    assert gastadas > 0

    monkeypatch.undo()
    proveedor = ProveedorEspia(settings=voice_settings, seed=5)
    recuperado = VoicePipeline(voice_settings, peticion, provider=proveedor).run()
    assert recuperado.exit_code == ExitCode.OK
    assert Path(recuperado.manifest_path).is_file()
    assert proveedor.llamadas == 0  # no se vuelve a sintetizar una voz ya guardada
    assert recuperado.requests_total == gastadas


def test_los_datos_de_simulacion_van_aparte(voice_settings, script_path, run_voice) -> None:
    resultado = run_voice(script_path, voice_key="separado")
    assert voice_settings.simulation_data_subdir in resultado.manifest_path
    assert not (voice_settings.data_dir / "viralgen.sqlite3").exists()


def test_la_migracion_conserva_los_trabajos_anteriores(voice_settings, script_path, run_voice) -> None:
    from viralgen.storage import Storage

    run_voice(script_path, voice_key="migracion")
    with Storage(voice_settings.effective_data_dir(simulation=True)) as almacen:
        trabajos = almacen.connect().execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        guiones = almacen.connect().execute("SELECT COUNT(*) AS n FROM scripts").fetchone()["n"]
        VoiceStorage(almacen).migrate()  # idempotente
        assert trabajos >= 1 and guiones >= 1
