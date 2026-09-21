"""Admision de voz para el modulo 4: se revalida todo desde los archivos."""

from __future__ import annotations

import json
import wave
from pathlib import Path

import pytest

from viralgen.voice.admission import check_voice_admission


@pytest.fixture
def pareja(voice_settings, script_path, run_voice):
    """Genera voz simulada y devuelve (guion, manifiesto, ajustes)."""
    resultado = run_voice(script_path, voice_key="admision")
    return script_path, Path(resultado.manifest_path), voice_settings


def test_una_simulacion_completa_pasa_la_auditoria(pareja) -> None:
    guion, manifiesto, ajustes = pareja
    informe = check_voice_admission(
        script_path=guion, manifest_path=manifiesto, settings=ajustes
    )
    assert informe.contract_valid is True
    assert informe.admissible_for_preview is True, informe.preview_reasons
    assert informe.measured_duration_s > 0


def test_una_simulacion_pasa_pruebas_y_nunca_produccion(pareja) -> None:
    """El mismo informe: valido y admisible para pruebas, jamas para montaje."""
    guion, manifiesto, ajustes = pareja
    informe = check_voice_admission(
        script_path=guion, manifest_path=manifiesto, settings=ajustes
    )
    assert informe.contract_valid is True
    assert informe.admissible_for_preview is True, informe.preview_reasons
    assert informe.admissible_for_assembly is False

    # Lo unico que falla es el origen.
    fallidas = {nombre for nombre, ok in informe.checks.items() if not ok}
    assert fallidas == {"voz_real", "guion_real"}
    assert informe.preview_reasons == []
    assert any("simulation=true" in motivo for motivo in informe.reasons)

    # El diccionario que consume la CLI lleva los tres veredictos separados.
    datos = informe.to_dict()
    assert datos["contract_valid"] is True
    assert datos["admissible_for_preview"] is True
    assert datos["admissible_for_assembly"] is False


def test_si_cambia_el_guion_el_hash_bloquea(pareja, tmp_path) -> None:
    guion, manifiesto, ajustes = pareja
    datos = json.loads(guion.read_text(encoding="utf-8"))
    datos["idea"]["title"] = datos["idea"]["title"] + " (editado)"
    otro = tmp_path / "editado.json"
    otro.write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    informe = check_voice_admission(
        script_path=otro, manifest_path=manifiesto, settings=ajustes
    )
    assert informe.admissible_for_preview is False
    assert informe.checks["hash_del_guion"] is False
    assert any("SHA-256" in motivo for motivo in informe.reasons)


def test_no_se_confia_en_el_booleano_del_manifiesto(pareja, tmp_path) -> None:
    """Marcar admissible_for_assembly a mano no cuela: se revalida todo."""
    guion, manifiesto, ajustes = pareja
    datos = json.loads(manifiesto.read_text(encoding="utf-8"))
    datos["control"]["admissible_for_assembly"] = True
    datos["simulation"] = True
    falso = tmp_path / "voice.json"
    falso.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
    # Los medios se resuelven respecto del manifiesto, asi que al moverlo se
    # detecta que faltan.
    informe = check_voice_admission(
        script_path=guion, manifest_path=falso, settings=ajustes
    )
    assert informe.admissible_for_preview is False


def test_un_wav_alterado_rompe_el_hash(pareja) -> None:
    guion, manifiesto, ajustes = pareja
    datos = json.loads(manifiesto.read_text(encoding="utf-8"))
    clip = manifiesto.parent / datos["scenes"][0]["clip_path"]
    with wave.open(str(clip), "rb") as handle:
        params = handle.getparams()
        frames = handle.readframes(handle.getnframes())
    with wave.open(str(clip), "wb") as handle:
        handle.setparams(params)
        handle.writeframes(bytes(len(frames)))  # silencio: mismas muestras, otro hash

    informe = check_voice_admission(
        script_path=guion, manifest_path=manifiesto, settings=ajustes
    )
    assert informe.admissible_for_preview is False
    assert informe.checks["clips_integros"] is False


def test_si_falta_el_maestro_se_bloquea(pareja) -> None:
    guion, manifiesto, ajustes = pareja
    datos = json.loads(manifiesto.read_text(encoding="utf-8"))
    (manifiesto.parent / datos["master"]["path"]).unlink()

    informe = check_voice_admission(
        script_path=guion, manifest_path=manifiesto, settings=ajustes
    )
    assert informe.admissible_for_preview is False
    assert informe.checks["maestro_presente"] is False


def test_un_hueco_entre_escenas_se_detecta(pareja, tmp_path) -> None:
    guion, manifiesto, ajustes = pareja
    datos = json.loads(manifiesto.read_text(encoding="utf-8"))
    datos["scenes"][1]["start_sample"] += 100
    datos["scenes"][1]["end_sample"] += 100
    roto = manifiesto.parent / "voice_roto.json"
    roto.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")

    informe = check_voice_admission(
        script_path=guion, manifest_path=roto, settings=ajustes
    )
    assert informe.admissible_for_preview is False
    assert informe.checks["cobertura_sin_huecos"] is False


def test_faltan_palabras_de_una_escena(pareja) -> None:
    guion, manifiesto, ajustes = pareja
    datos = json.loads(manifiesto.read_text(encoding="utf-8"))
    datos["words"] = datos["words"][:-1]
    roto = manifiesto.parent / "voice_sin_palabra.json"
    roto.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")

    informe = check_voice_admission(
        script_path=guion, manifest_path=roto, settings=ajustes
    )
    assert informe.admissible_for_preview is False
    assert informe.checks["palabras_cubren_la_narracion"] is False


def test_manifiesto_que_no_cumple_el_contrato(pareja, tmp_path) -> None:
    guion, _manifiesto, ajustes = pareja
    malo = tmp_path / "malo.json"
    malo.write_text('{"document_type": "voice_manifest"}', encoding="utf-8")
    informe = check_voice_admission(
        script_path=guion, manifest_path=malo, settings=ajustes
    )
    assert informe.contract_valid is False
    assert informe.admissible_for_preview is False


def test_guion_inexistente(voice_settings, tmp_path) -> None:
    informe = check_voice_admission(
        script_path=tmp_path / "no.json",
        manifest_path=tmp_path / "no2.json",
        settings=voice_settings,
    )
    assert informe.admissible_for_preview is False
    assert informe.checks["guion_legible"] is False


# ---------------------------------------------------------------------------
# Separacion entre validacion de pruebas y admision para produccion
# ---------------------------------------------------------------------------


def test_un_manifiesto_needs_review_se_rechaza_tambien_en_pruebas(
    voice_settings, script_path
) -> None:
    """Un borrador de voz no pasa ni siquiera la validacion de pruebas."""
    from viralgen.voice.pipeline import VoiceJobRequest, VoicePipeline
    from viralgen.voice.providers.mock import MockVoiceProvider

    class SinAlineacionEnLaUltima(MockVoiceProvider):
        """Solo falla la alineacion de la ultima escena.

        Asi el recorrido llega hasta el final y produce un manifiesto COMPLETO
        con needs_review, que es justo lo que queremos auditar.
        """

        def __init__(self, ultima: str, **kwargs) -> None:
            super().__init__(**kwargs)
            self.ultima = ultima

        def synthesize(self, request, budget):
            resultado = super().synthesize(request, budget)
            if request.scene_id == self.ultima:
                resultado.alignment = None
                resultado.normalized_alignment = None
            return resultado

    guion = json.loads(script_path.read_text(encoding="utf-8"))
    ultima = guion["scenes"][-1]["scene_id"]
    resultado = VoicePipeline(
        voice_settings,
        VoiceJobRequest(
            script_path=script_path, voice_key="borrador-voz", simulation=True, seed=5
        ),
        provider=SinAlineacionEnLaUltima(ultima, settings=voice_settings, seed=5),
    ).run()
    assert resultado.status == "needs_review"
    assert resultado.manifest_path is not None  # manifiesto COMPLETO

    informe = check_voice_admission(
        script_path=script_path,
        manifest_path=Path(resultado.manifest_path),
        settings=voice_settings,
    )
    assert informe.contract_valid is True  # el contrato si se cumple
    assert informe.admissible_for_preview is False  # pero no pasa ni en pruebas
    assert informe.admissible_for_assembly is False
    assert informe.checks["voz_lista"] is False
    assert informe.preview_reasons


def test_un_hash_incorrecto_se_rechaza_en_los_dos_modos(pareja, tmp_path) -> None:
    guion, manifiesto, ajustes = pareja
    datos = json.loads(guion.read_text(encoding="utf-8"))
    datos["narration"]["voice_direction"] += " "
    otro = tmp_path / "otro.json"
    otro.write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    informe = check_voice_admission(
        script_path=otro, manifest_path=manifiesto, settings=ajustes
    )
    assert informe.checks["hash_del_guion"] is False
    assert informe.admissible_for_preview is False
    assert informe.admissible_for_assembly is False


def test_la_validacion_no_modifica_ningun_archivo(pareja) -> None:
    """El modo de validacion es de solo lectura."""
    guion, manifiesto, ajustes = pareja
    antes_guion = guion.read_bytes()
    antes_manifiesto = manifiesto.read_bytes()
    audios = {
        ruta: ruta.read_bytes() for ruta in (manifiesto.parent / "audio").rglob("*.wav")
    }

    check_voice_admission(script_path=guion, manifest_path=manifiesto, settings=ajustes)

    assert guion.read_bytes() == antes_guion
    assert manifiesto.read_bytes() == antes_manifiesto
    for ruta, contenido in audios.items():
        assert ruta.read_bytes() == contenido
