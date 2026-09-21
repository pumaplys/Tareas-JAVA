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
    """Audio valido, alineacion inutilizable en la escena indicada."""

    def __init__(self, romper_en: str | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.romper_en = romper_en
        self.sintetizadas: list[str] = []

    def synthesize(self, request, budget):
        resultado = super().synthesize(request, budget)
        self.sintetizadas.append(request.scene_id)
        if self.romper_en is None or request.scene_id == self.romper_en:
            resultado.alignment = None
            resultado.normalized_alignment = None
        return resultado


def _escenas_del_guion(script: Path) -> list[str]:
    guion = json.loads(script.read_text(encoding="utf-8"))
    return [escena["scene_id"] for escena in guion["scenes"]]


def test_un_bloqueo_detiene_las_escenas_siguientes(voice_settings, script_path) -> None:
    """Prueba focalizada del corte: escena intermedia sin alineacion utilizable.

    Recibe audio valido, su recuperacion esta desactivada, y a partir de ahi no
    se emite ninguna peticion mas. Se conservan el audio obtenido y el
    presupuesto.
    """
    escenas = _escenas_del_guion(script_path)
    assert len(escenas) >= 5
    intermedia = escenas[2]

    proveedor = ProveedorSinAlineacion(
        romper_en=intermedia, settings=voice_settings, seed=5
    )
    peticion = VoiceJobRequest(
        script_path=script_path, voice_key="corte", simulation=True, seed=5
    )
    resultado = VoicePipeline(voice_settings, peticion, provider=proveedor).run()

    # Se sintetizo hasta la escena problematica, ni una mas.
    assert proveedor.sintetizadas == escenas[:3]
    assert resultado.partial is True
    assert resultado.pending_scenes == escenas[3:]
    assert resultado.status == "needs_review"
    assert resultado.exit_code == ExitCode.NEEDS_REVIEW

    # No se publica un manifiesto que aparente tener todas las escenas.
    assert resultado.manifest_path is None
    assert resultado.master_path is None

    # El audio obtenido se conserva y las rutas del resumen son reales.
    assert len(resultado.available_paths) == 3
    for ruta in resultado.available_paths:
        assert Path(ruta).is_file()

    # El presupuesto refleja exactamente lo gastado.
    assert resultado.requests_new == 3
    assert resultado.requests_total == 3

    # El bloqueo esta en campos estructurados, no en el texto.
    bloqueantes = [issue for issue in resultado.issues if issue["blocking"]]
    assert len(bloqueantes) == 1
    assert bloqueantes[0]["code"] == "alineacion_no_utilizable"
    assert bloqueantes[0]["scene_id"] == intermedia
    assert bloqueantes[0]["severity"] == "error"


def test_repetir_un_bloqueo_no_gasta_solicitudes_nuevas(voice_settings, script_path) -> None:
    """Reanudar reutiliza los clips y no gasta nada mientras el bloqueo siga."""
    escenas = _escenas_del_guion(script_path)
    intermedia = escenas[2]
    peticion = VoiceJobRequest(
        script_path=script_path, voice_key="corte-repetido", simulation=True, seed=5
    )

    primero = VoicePipeline(
        voice_settings,
        peticion,
        provider=ProveedorSinAlineacion(romper_en=intermedia, settings=voice_settings, seed=5),
    ).run()
    assert primero.requests_new == 3

    segundo_proveedor = ProveedorSinAlineacion(
        romper_en=intermedia, settings=voice_settings, seed=5
    )
    segundo = VoicePipeline(voice_settings, peticion, provider=segundo_proveedor).run()

    assert segundo_proveedor.sintetizadas == []  # nada se resintetiza
    assert segundo.requests_new == 0  # ni una solicitud nueva
    assert segundo.requests_total == primero.requests_total  # historico intacto
    assert segundo.partial is True
    assert segundo.pending_scenes == escenas[3:]
    assert segundo.manifest_path is None


def test_un_aviso_informativo_no_detiene_la_narracion(voice_settings, script_path, run_voice) -> None:
    """La ausencia de un efecto opcional es informativa, no un bloqueo."""
    resultado = run_voice(script_path, voice_key="aviso-sfx")
    assert resultado.exit_code == ExitCode.OK
    assert resultado.partial is False
    assert resultado.pending_scenes == []

    informativos = [issue for issue in resultado.issues if issue["code"] == "sfx_desactivado"]
    assert informativos, "el guion de prueba pide algun efecto"
    assert all(issue["blocking"] is False for issue in informativos)
    assert all(issue["severity"] == "info" for issue in informativos)
    # Y aun asi el manifiesto sale completo y ready.
    assert _manifest(resultado).control.voice_status is VoiceStatus.READY


def test_un_bloqueo_en_la_ultima_escena_produce_manifiesto_completo(
    voice_settings, script_path
) -> None:
    """Si no quedan escenas por pedir, el manifiesto se publica con needs_review.

    El esquema 1.0 solo conoce `ready` y `needs_review` para manifiestos
    completos: un corte parcial no inventa un tercer estado, simplemente no
    publica manifiesto.
    """
    escenas = _escenas_del_guion(script_path)
    proveedor = ProveedorSinAlineacion(
        romper_en=escenas[-1], settings=voice_settings, seed=5
    )
    resultado = VoicePipeline(
        voice_settings,
        VoiceJobRequest(
            script_path=script_path, voice_key="ultima", simulation=True, seed=5
        ),
        provider=proveedor,
    ).run()

    assert proveedor.sintetizadas == escenas  # no quedaba nada pendiente
    assert resultado.partial is False
    assert resultado.status == "needs_review"
    assert resultado.manifest_path is not None
    manifiesto = _manifest(resultado)
    assert manifiesto.control.voice_status.value == "needs_review"
    assert manifiesto.control.admissible_for_assembly is False
    assert len(manifiesto.scenes) == len(escenas)
    # Solo faltan las palabras de la escena bloqueada.
    escenas_con_palabras = {palabra.scene_id for palabra in manifiesto.words}
    assert escenas[-1] not in escenas_con_palabras
    assert len(escenas_con_palabras) == len(escenas) - 1


def test_sin_alineacion_en_la_primera_escena_corta_enseguida(
    voice_settings, script_path
) -> None:
    proveedor = ProveedorSinAlineacion(settings=voice_settings, seed=5)
    resultado = VoicePipeline(
        voice_settings,
        VoiceJobRequest(
            script_path=script_path, voice_key="sin-align", simulation=True, seed=5
        ),
        provider=proveedor,
    ).run()
    assert len(proveedor.sintetizadas) == 1
    assert resultado.partial is True
    assert resultado.requests_new == 1
    # El audio de la primera escena se conserva para poder recuperar el trabajo.
    assert len(resultado.available_paths) == 1
    assert Path(resultado.available_paths[0]).is_file()


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


# ---------------------------------------------------------------------------
# Recuperacion configurada: alineacion forzada
# ---------------------------------------------------------------------------


class ProveedorConRecuperacion(ProveedorSinAlineacion):
    """Sin alineacion en la sintesis, pero con alineacion forzada disponible."""

    supports_forced_alignment = True

    def __init__(self, recuperacion_valida: bool, **kwargs) -> None:
        super().__init__(**kwargs)
        self.recuperacion_valida = recuperacion_valida
        self.recuperaciones = 0

    def force_align(self, audio_path, text, budget):
        from viralgen.voice.alignment import CharAlignment
        from viralgen.voice.audio import read_wav_info

        budget.reserve("forced_alignment", None)
        self.recuperaciones += 1
        if not self.recuperacion_valida:
            return None
        duracion = read_wav_info(audio_path).duration_s
        n = len(text)
        paso = duracion / max(n, 1)
        return CharAlignment(
            list(text),
            [i * paso for i in range(n)],
            [min((i + 1) * paso, duracion) for i in range(n)],
        )


def test_la_recuperacion_resuelve_el_bloqueo_y_el_trabajo_continua(
    voice_settings, script_path
) -> None:
    voice_settings.voice_allow_forced_alignment = True
    escenas = _escenas_del_guion(script_path)
    proveedor = ProveedorConRecuperacion(
        recuperacion_valida=True, romper_en=escenas[2], settings=voice_settings, seed=5
    )
    resultado = VoicePipeline(
        voice_settings,
        VoiceJobRequest(
            script_path=script_path, voice_key="recuperado", simulation=True, seed=5
        ),
        provider=proveedor,
    ).run()

    assert proveedor.recuperaciones == 1
    assert proveedor.sintetizadas == escenas  # continua tras recuperarse
    assert resultado.partial is False
    assert resultado.exit_code == ExitCode.OK
    manifiesto = _manifest(resultado)
    problematica = next(e for e in manifiesto.scenes if e.scene_id == escenas[2])
    assert problematica.alignment_method.value == "forced_alignment"
    # La recuperacion cuenta contra el mismo presupuesto.
    assert resultado.requests_new == len(escenas) + 1


def test_una_recuperacion_fallida_no_se_repite_al_reanudar(
    voice_settings, script_path
) -> None:
    """Repetir sin resolver el bloqueo no gasta solicitudes en silencio."""
    voice_settings.voice_allow_forced_alignment = True
    escenas = _escenas_del_guion(script_path)
    peticion = VoiceJobRequest(
        script_path=script_path, voice_key="recuperacion-fallida", simulation=True, seed=5
    )

    primero_proveedor = ProveedorConRecuperacion(
        recuperacion_valida=False, romper_en=escenas[1], settings=voice_settings, seed=5
    )
    primero = VoicePipeline(voice_settings, peticion, provider=primero_proveedor).run()
    assert primero_proveedor.recuperaciones == 1
    assert primero.partial is True
    assert primero.requests_new == 3  # 2 sintesis + 1 recuperacion

    segundo_proveedor = ProveedorConRecuperacion(
        recuperacion_valida=False, romper_en=escenas[1], settings=voice_settings, seed=5
    )
    segundo = VoicePipeline(voice_settings, peticion, provider=segundo_proveedor).run()
    assert segundo_proveedor.sintetizadas == []
    assert segundo_proveedor.recuperaciones == 0  # no se reintenta la recuperacion
    assert segundo.requests_new == 0
    assert segundo.requests_total == primero.requests_total
    assert segundo.partial is True


def test_sin_recuperacion_configurada_se_dice_en_el_motivo(
    voice_settings, script_path
) -> None:
    assert voice_settings.voice_allow_forced_alignment is False
    proveedor = ProveedorSinAlineacion(settings=voice_settings, seed=5)
    resultado = VoicePipeline(
        voice_settings,
        VoiceJobRequest(
            script_path=script_path, voice_key="sin-recuperacion", simulation=True, seed=5
        ),
        provider=proveedor,
    ).run()
    bloqueante = next(issue for issue in resultado.issues if issue["blocking"])
    assert "recuperacion desactivada" in bloqueante["message"]
