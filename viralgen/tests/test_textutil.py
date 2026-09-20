"""Conteo de palabras, normalizacion y similitud lexica."""

from __future__ import annotations

import pytest

from viralgen.textutil import (
    canonical_json,
    content_hash,
    count_words,
    jaccard_similarity,
    normalize_for_compare,
    sha256_json,
    starts_with_normalized,
)


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("", 0),
        ("Hola", 1),
        ("Habia una vez", 3),
        ("¿Sabias que la miel nunca caduca?", 6),
        ("anti-gravedad", 1),  # el guion interior no parte la palabra
        ("teorico-practico y claro", 3),
        ("2.500 personas", 2),  # el numero con separador cuenta como una
        ("3,14 exacto", 2),
        ("¡Hola! ¿Que tal?", 3),  # la puntuacion no cuenta
        ("canción canción", 2),
        ("uno  dos\n\ttres", 3),
        ("... --- ...", 0),
    ],
)
def test_conteo_de_palabras(texto: str, esperado: int) -> None:
    assert count_words(texto) == esperado


def test_nfc_equivale_a_precompuesto() -> None:
    precompuesto = "canción"
    combinado = "canción"
    assert count_words(precompuesto) == count_words(combinado) == 1
    assert normalize_for_compare(precompuesto) == normalize_for_compare(combinado)


def test_normalizacion_quita_tildes_y_puntuacion() -> None:
    assert normalize_for_compare("¿Canción del Niño?") == "cancion del nino"


def test_jaccard_es_lexico_no_semantico() -> None:
    # Mismo vocabulario reordenado: similitud maxima.
    assert jaccard_similarity("agua helada en la Luna", "hay agua helada en la luna") == 1.0
    # Mismo significado, otro vocabulario: la medida NO lo detecta.
    assert jaccard_similarity("agua helada en la Luna", "hielo en el satelite terrestre") == 0.0


def test_jaccard_vacio() -> None:
    assert jaccard_similarity("", "algo") == 0.0


def test_prefijo_normalizado() -> None:
    assert starts_with_normalized("¿Sabias que la miel nunca caduca?", "Sabias que la miel")
    assert not starts_with_normalized("La miel nunca caduca", "Sabias que")
    assert not starts_with_normalized("cualquier cosa", "")


def test_hash_estable_e_independiente_del_orden_de_claves() -> None:
    assert sha256_json({"a": 1, "b": 2}) == sha256_json({"b": 2, "a": 1})
    assert canonical_json({"b": 2, "a": 1}) == '{"a":1,"b":2}'


def test_content_hash_ignora_mayusculas_y_tildes() -> None:
    assert content_hash("Título", "Premisa") == content_hash("titulo", "premisa")
    assert content_hash("Titulo", "Premisa") != content_hash("Titulo", "Otra premisa")
