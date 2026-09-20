"""Catalogo de hechos: validacion, hash, filtros de aprobacion."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from viralgen.errors import SourcePackError
from viralgen.evidence import (
    facts_for_prompt,
    load_source_pack,
    select_facts_for_model,
    usable_facts,
)


def test_carga_y_hash_estable(demo_pack: Path, tmp_path: Path) -> None:
    pack = load_source_pack(demo_pack)
    assert pack.pack_id == "pruebas_demo"
    hash_original = pack.content_hash()

    # Reordenar los hechos NO cambia el hash: se ordena por fact_id.
    datos = json.loads(demo_pack.read_text(encoding="utf-8"))
    datos["facts"].reverse()
    otra = tmp_path / "reordenado.json"
    otra.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
    assert load_source_pack(otra).content_hash() == hash_original

    # Cambiar un caracter SI lo cambia.
    datos["facts"][0]["claim_text"] += "."
    otra.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
    assert load_source_pack(otra).content_hash() != hash_original


def test_solo_hechos_aprobados(demo_pack: Path) -> None:
    pack = load_source_pack(demo_pack)
    usables, rechazados = usable_facts(pack, simulation=True)
    assert {fact.fact_id for fact in usables} == {"f_1", "f_2", "f_3", "f_4", "f_5"}
    assert rechazados["f_9"].startswith("review_status=pending")


def test_demo_only_no_vale_en_modo_real(demo_pack: Path, real_pack: Path) -> None:
    demo = load_source_pack(demo_pack)
    usables, rechazados = usable_facts(demo, simulation=False)
    assert usables == []
    assert all("demo_only" in motivo for motivo in rechazados.values() if "demo" in motivo)

    real = load_source_pack(real_pack)
    usables_real, _ = usable_facts(real, simulation=False)
    assert len(usables_real) == 3


def test_sin_hechos_aprobados(pending_pack: Path) -> None:
    pack = load_source_pack(pending_pack)
    usables, rechazados = usable_facts(pack, simulation=True)
    assert usables == []
    assert len(rechazados) == 3


def test_seleccion_local_recorta_y_reporta(demo_pack: Path) -> None:
    pack = load_source_pack(demo_pack)
    seleccion = select_facts_for_model(pack, simulation=True, topic="pieza 3", limit=2)
    assert len(seleccion.selected) == 2
    assert seleccion.truncated is True
    informe = seleccion.report()
    assert informe["selected_count"] == 2
    assert len(informe["selected_fact_ids"]) == 2


def test_el_prompt_no_recibe_url_ni_fecha(demo_pack: Path) -> None:
    """La procedencia se copia del catalogo, no se confia al modelo."""
    pack = load_source_pack(demo_pack)
    vista = facts_for_prompt(pack.facts)
    for entrada in vista:
        assert set(entrada) == {"fact_id", "claim_text", "evidence_excerpt", "source_title"}


def test_identificadores_duplicados(tmp_path: Path) -> None:
    fact = {
        "fact_id": "f_1",
        "claim_text": "Texto de prueba suficientemente largo para el modelo.",
        "source_url": "https://example.org/x",
        "source_title": "Titulo",
        "evidence_excerpt": "Extracto",
        "checked_at": "2026-02-01T10:00:00Z",
        "review_status": "approved",
        "demo_only": True,
    }
    ruta = tmp_path / "dup.json"
    ruta.write_text(
        json.dumps({"schema_version": "1.0", "pack_id": "dup", "facts": [fact, dict(fact)]}),
        encoding="utf-8",
    )
    with pytest.raises(SourcePackError, match="duplicado"):
        load_source_pack(ruta)


@pytest.mark.parametrize(
    "url", ["ftp://example.org/x", "javascript:alert(1)", "no-es-una-url", "https://"]
)
def test_urls_invalidas(tmp_path: Path, url: str) -> None:
    ruta = tmp_path / "url.json"
    ruta.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "pack_id": "urls",
                "facts": [
                    {
                        "fact_id": "f_1",
                        "claim_text": "Texto de prueba.",
                        "source_url": url,
                        "source_title": "Titulo",
                        "evidence_excerpt": "Extracto",
                        "checked_at": "2026-02-01T10:00:00Z",
                        "review_status": "approved",
                        "demo_only": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SourcePackError):
        load_source_pack(ruta)


def test_fecha_sin_zona_horaria(tmp_path: Path) -> None:
    ruta = tmp_path / "fecha.json"
    ruta.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "pack_id": "fecha",
                "facts": [
                    {
                        "fact_id": "f_1",
                        "claim_text": "Texto de prueba.",
                        "source_url": "https://example.org/x",
                        "source_title": "Titulo",
                        "evidence_excerpt": "Extracto",
                        "checked_at": "2026-02-01 10:00:00",
                        "review_status": "approved",
                        "demo_only": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SourcePackError):
        load_source_pack(ruta)


def test_archivo_inexistente(tmp_path: Path) -> None:
    with pytest.raises(SourcePackError, match="No existe"):
        load_source_pack(tmp_path / "no_esta.json")
