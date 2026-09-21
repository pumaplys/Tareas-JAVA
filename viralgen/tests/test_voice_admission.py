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
        script_path=guion, manifest_path=manifiesto, settings=ajustes, require_real=False
    )
    assert informe.contract_valid is True
    assert informe.admissible is True, informe.reasons
    assert informe.measured_duration_s > 0


def test_una_simulacion_nunca_es_admisible_para_produccion(pareja) -> None:
    guion, manifiesto, ajustes = pareja
    informe = check_voice_admission(
        script_path=guion, manifest_path=manifiesto, settings=ajustes, require_real=True
    )
    assert informe.admissible is False
    assert informe.checks["voz_real"] is False
    assert any("simulation=true" in motivo for motivo in informe.reasons)


def test_si_cambia_el_guion_el_hash_bloquea(pareja, tmp_path) -> None:
    guion, manifiesto, ajustes = pareja
    datos = json.loads(guion.read_text(encoding="utf-8"))
    datos["idea"]["title"] = datos["idea"]["title"] + " (editado)"
    otro = tmp_path / "editado.json"
    otro.write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    informe = check_voice_admission(
        script_path=otro, manifest_path=manifiesto, settings=ajustes, require_real=False
    )
    assert informe.admissible is False
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
        script_path=guion, manifest_path=falso, settings=ajustes, require_real=True
    )
    assert informe.admissible is False


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
        script_path=guion, manifest_path=manifiesto, settings=ajustes, require_real=False
    )
    assert informe.admissible is False
    assert informe.checks["clips_integros"] is False


def test_si_falta_el_maestro_se_bloquea(pareja) -> None:
    guion, manifiesto, ajustes = pareja
    datos = json.loads(manifiesto.read_text(encoding="utf-8"))
    (manifiesto.parent / datos["master"]["path"]).unlink()

    informe = check_voice_admission(
        script_path=guion, manifest_path=manifiesto, settings=ajustes, require_real=False
    )
    assert informe.admissible is False
    assert informe.checks["maestro_presente"] is False


def test_un_hueco_entre_escenas_se_detecta(pareja, tmp_path) -> None:
    guion, manifiesto, ajustes = pareja
    datos = json.loads(manifiesto.read_text(encoding="utf-8"))
    datos["scenes"][1]["start_sample"] += 100
    datos["scenes"][1]["end_sample"] += 100
    roto = manifiesto.parent / "voice_roto.json"
    roto.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")

    informe = check_voice_admission(
        script_path=guion, manifest_path=roto, settings=ajustes, require_real=False
    )
    assert informe.admissible is False
    assert informe.checks["cobertura_sin_huecos"] is False


def test_faltan_palabras_de_una_escena(pareja) -> None:
    guion, manifiesto, ajustes = pareja
    datos = json.loads(manifiesto.read_text(encoding="utf-8"))
    datos["words"] = datos["words"][:-1]
    roto = manifiesto.parent / "voice_sin_palabra.json"
    roto.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")

    informe = check_voice_admission(
        script_path=guion, manifest_path=roto, settings=ajustes, require_real=False
    )
    assert informe.admissible is False
    assert informe.checks["palabras_cubren_la_narracion"] is False


def test_manifiesto_que_no_cumple_el_contrato(pareja, tmp_path) -> None:
    guion, _manifiesto, ajustes = pareja
    malo = tmp_path / "malo.json"
    malo.write_text('{"document_type": "voice_manifest"}', encoding="utf-8")
    informe = check_voice_admission(
        script_path=guion, manifest_path=malo, settings=ajustes, require_real=False
    )
    assert informe.contract_valid is False
    assert informe.admissible is False


def test_guion_inexistente(voice_settings, tmp_path) -> None:
    informe = check_voice_admission(
        script_path=tmp_path / "no.json",
        manifest_path=tmp_path / "no2.json",
        settings=voice_settings,
    )
    assert informe.admissible is False
    assert informe.checks["guion_legible"] is False
