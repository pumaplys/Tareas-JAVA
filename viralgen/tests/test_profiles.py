"""Perfiles editoriales y biblia visual."""

from __future__ import annotations

import json

import pytest

from viralgen.errors import ProfileError
from viralgen.profiles import get_profile, get_series_bible, load_profiles, load_series_bibles


def test_los_tres_perfiles_del_proyecto() -> None:
    ids = {profile.profile_id for profile in load_profiles().profiles}
    assert ids == {"infantil_cuentos", "curiosidades_corto", "curiosidades_largo"}


@pytest.mark.parametrize(
    ("profile_id", "duracion", "wpm", "escenas"),
    [
        ("infantil_cuentos", 50, 125, (6, 9)),
        ("curiosidades_corto", 35, 155, (5, 8)),
        ("curiosidades_largo", 70, 155, (9, 12)),
    ],
)
def test_parametros_de_cada_perfil(profile_id, duracion, wpm, escenas) -> None:
    profile = get_profile(profile_id)
    assert profile.default_duration_s == duracion
    assert profile.target_wpm == wpm
    assert (profile.min_scenes, profile.max_scenes) == escenas
    assert profile.aspect_ratio == "9:16"
    assert (profile.width, profile.height, profile.fps) == (1080, 1920, 30)
    assert profile.language == "es"
    assert profile.visual_prompt_language == "en"


def test_infantil_es_made_for_kids_y_no_exige_evidencia() -> None:
    profile = get_profile("infantil_cuentos")
    assert profile.made_for_kids is True
    assert profile.requires_evidence is False


def test_curiosidades_exige_evidencia() -> None:
    for profile_id in ("curiosidades_corto", "curiosidades_largo"):
        assert get_profile(profile_id).requires_evidence is True


def test_duracion_fuera_de_rango() -> None:
    profile = get_profile("curiosidades_corto")
    assert profile.resolve_duration(None) == 35.0
    assert profile.resolve_duration(45) == 45.0
    with pytest.raises(ProfileError):
        profile.resolve_duration(120)


def test_perfil_desconocido() -> None:
    with pytest.raises(ProfileError):
        get_profile("no_existe")


def test_biblia_visual_tiene_personajes_estables() -> None:
    bible = get_series_bible("bosque_lumina")
    ids = [character.character_id for character in bible.characters]
    assert "lumi" in ids
    assert len(set(ids)) == len(ids)
    assert "no text" in bible.negative_prompt


def test_todos_los_perfiles_apuntan_a_una_serie_existente() -> None:
    series = {item.series_id for item in load_series_bibles().series}
    for profile in load_profiles().profiles:
        assert profile.series_bible_id in series


def test_perfil_invalido_en_archivo(tmp_path) -> None:
    ruta = tmp_path / "profiles.json"
    ruta.write_text(json.dumps({"schema_version": "1.0", "profiles": []}), encoding="utf-8")
    with pytest.raises(ProfileError):
        load_profiles(ruta)
