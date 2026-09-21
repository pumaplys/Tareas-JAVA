"""Admision del modulo 3: se revalida todo desde los archivos reales."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from viralgen.media.admission import ORIGIN_CHECKS, check_media_admission


@pytest.fixture
def trio(media_settings, media_inputs, run_media):
    guion, voz = media_inputs
    resultado = run_media(guion, voz, media_key="admision")
    return guion, voz, Path(resultado.manifest_path), media_settings


def test_una_simulacion_pasa_pruebas_y_nunca_produccion(trio) -> None:
    guion, voz, manifiesto, ajustes = trio
    informe = check_media_admission(
        script_path=guion, voice_path=voz, manifest_path=manifiesto, settings=ajustes
    )
    assert informe.contract_valid is True
    assert informe.admissible_for_preview is True, informe.preview_reasons
    assert informe.admissible_for_assembly is False

    fallidas = {nombre for nombre, ok in informe.checks.items() if not ok}
    assert fallidas == set(ORIGIN_CHECKS)
    assert informe.preview_reasons == []

    datos = informe.to_dict()
    assert datos["admissible_for_preview"] is True
    assert datos["admissible_for_assembly"] is False
    assert datos["origin_checks"] == sorted(ORIGIN_CHECKS)


def test_el_hash_del_guion_bloquea_en_ambos_modos(trio, tmp_path) -> None:
    guion, voz, manifiesto, ajustes = trio
    datos = json.loads(guion.read_text(encoding="utf-8"))
    datos["narration"]["voice_direction"] += " "
    otro = tmp_path / "otro.json"
    otro.write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    informe = check_media_admission(
        script_path=otro, voice_path=voz, manifest_path=manifiesto, settings=ajustes
    )
    assert informe.checks["hash_del_guion"] is False
    assert informe.admissible_for_preview is False
    assert informe.admissible_for_assembly is False


def test_el_hash_de_la_voz_bloquea(trio, tmp_path) -> None:
    guion, voz, manifiesto, ajustes = trio
    datos = json.loads(voz.read_text(encoding="utf-8"))
    datos["provider"]["voice_id"] = datos["provider"]["voice_id"] + "-x"
    otra = tmp_path / "otra_voz.json"
    otra.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
    informe = check_media_admission(
        script_path=guion, voice_path=otra, manifest_path=manifiesto, settings=ajustes
    )
    assert informe.checks["hash_de_la_voz"] is False


def test_un_asset_alterado_rompe_el_hash(trio) -> None:
    guion, voz, manifiesto, ajustes = trio
    datos = json.loads(manifiesto.read_text(encoding="utf-8"))
    primera = next(a for a in datos["assets"] if a["role"] == "scene_image")
    ruta = manifiesto.parent / primera["path"]
    # Se rompe el enlace fisico antes de escribir, para no tocar la cache.
    contenido = ruta.read_bytes()
    ruta.unlink()
    ruta.write_bytes(contenido[:-20] + b"\x00" * 20)

    informe = check_media_admission(
        script_path=guion, voice_path=voz, manifest_path=manifiesto, settings=ajustes
    )
    assert informe.checks["archivos_integros"] is False
    assert informe.contract_valid is False


def test_un_asset_ausente_bloquea(trio) -> None:
    guion, voz, manifiesto, ajustes = trio
    datos = json.loads(manifiesto.read_text(encoding="utf-8"))
    primera = next(a for a in datos["assets"] if a["role"] == "scene_image")
    (manifiesto.parent / primera["path"]).unlink()

    informe = check_media_admission(
        script_path=guion, voice_path=voz, manifest_path=manifiesto, settings=ajustes
    )
    assert informe.checks["archivos_integros"] is False


def test_una_ruta_que_escapa_por_symlink_se_rechaza(trio, tmp_path) -> None:
    guion, voz, manifiesto, ajustes = trio
    datos = json.loads(manifiesto.read_text(encoding="utf-8"))
    externo = tmp_path / "fuera.jpeg"
    externo.write_bytes((manifiesto.parent / datos["assets"][0]["path"]).read_bytes())

    primera = next(a for a in datos["assets"] if a["role"] == "scene_image")
    ruta = manifiesto.parent / primera["path"]
    ruta.unlink()
    os.symlink(externo, ruta)

    informe = check_media_admission(
        script_path=guion, voice_path=voz, manifest_path=manifiesto, settings=ajustes
    )
    assert informe.checks["archivos_integros"] is False
    assert any("fuera del paquete" in motivo for motivo in informe.reasons)


def test_una_escena_sin_cobertura_bloquea(trio) -> None:
    guion, voz, manifiesto, ajustes = trio
    datos = json.loads(manifiesto.read_text(encoding="utf-8"))
    datos["scenes"][1]["start_sample"] += 100
    roto = manifiesto.parent / "media_roto.json"
    roto.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")

    informe = check_media_admission(
        script_path=guion, voice_path=voz, manifest_path=roto, settings=ajustes
    )
    assert informe.checks["cobertura_completa"] is False


def test_faltan_referencias_de_un_personaje(trio) -> None:
    guion, voz, manifiesto, ajustes = trio
    datos = json.loads(manifiesto.read_text(encoding="utf-8"))
    datos["references"]["entries"] = datos["references"]["entries"][:-1]
    roto = manifiesto.parent / "media_sin_ref.json"
    roto.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")

    informe = check_media_admission(
        script_path=guion, voice_path=voz, manifest_path=roto, settings=ajustes
    )
    assert informe.checks["referencias_resueltas"] is False


def test_un_manifiesto_que_no_cumple_el_contrato(trio, tmp_path) -> None:
    guion, voz, _manifiesto, ajustes = trio
    malo = tmp_path / "malo.json"
    malo.write_text('{"document_type": "media_manifest"}', encoding="utf-8")
    informe = check_media_admission(
        script_path=guion, voice_path=voz, manifest_path=malo, settings=ajustes
    )
    assert informe.contract_valid is False
    assert informe.admissible_for_preview is False


def test_la_validacion_no_modifica_ningun_byte(trio) -> None:
    guion, voz, manifiesto, ajustes = trio
    base = manifiesto.parent
    antes = {
        ruta: ruta.read_bytes()
        for ruta in sorted(base.rglob("*"))
        if ruta.is_file()
    }
    antes[guion] = guion.read_bytes()
    antes[voz] = voz.read_bytes()

    check_media_admission(
        script_path=guion, voice_path=voz, manifest_path=manifiesto, settings=ajustes
    )
    for ruta, contenido in antes.items():
        assert ruta.read_bytes() == contenido
