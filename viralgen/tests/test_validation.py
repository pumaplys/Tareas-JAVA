"""Validaciones del documento: integridad (fatal) frente a avisos (needs_review)."""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from viralgen.assembly import apply_validation_result
from viralgen.evidence import load_source_pack, usable_facts
from viralgen.profiles import get_profile
from viralgen.schemas.common import ProductionStatus
from viralgen.schemas.document import ScriptDocument
from viralgen.validation import validate_document


def _revalidar(documento: ScriptDocument, mutacion, *, profile_id=None, allowed=None):
    datos = copy.deepcopy(documento.model_dump(mode="json"))
    mutacion(datos)
    nuevo = ScriptDocument.model_validate(datos)
    profile = get_profile(profile_id or documento.profile_id)
    return validate_document(nuevo, profile=profile, allowed_facts=allowed, promise="")


def _codigos(informe) -> set[str]:
    return {issue.code for issue in informe.issues}


def test_documento_generado_es_valido(documento_infantil: ScriptDocument) -> None:
    informe = validate_document(
        documento_infantil, profile=get_profile("infantil_cuentos"), allowed_facts=None
    )
    assert informe.issues == []
    assert informe.production_status() is ProductionStatus.READY_FOR_PRODUCTION


def test_personaje_inexistente(documento_infantil: ScriptDocument) -> None:
    informe = _revalidar(
        documento_infantil, lambda d: d["scenes"][0]["character_ids"].append("personaje_fantasma")
    )
    assert "personaje_inexistente" in _codigos(informe)
    assert informe.fatal


def test_claim_ref_inexistente(documento_curiosidades: ScriptDocument) -> None:
    informe = _revalidar(
        documento_curiosidades, lambda d: d["scenes"][0]["claim_refs"].append("cl_inventado")
    )
    assert "claim_ref_inexistente" in _codigos(informe)


def test_hecho_no_aprobado(documento_curiosidades: ScriptDocument, pending_pack) -> None:
    """Si el catalogo permitido no contiene el hecho usado, es fatal."""
    pack = load_source_pack(pending_pack)
    usables, _ = usable_facts(pack, simulation=True)
    informe = validate_document(
        documento_curiosidades,
        profile=get_profile("curiosidades_corto"),
        allowed_facts={fact.fact_id: fact for fact in usables} or {"otro": None},
    )
    assert "hecho_no_aprobado" in _codigos(informe)


def test_procedencia_alterada(documento_curiosidades: ScriptDocument, demo_pack) -> None:
    pack = load_source_pack(demo_pack)
    permitidos = {fact.fact_id: fact for fact in pack.facts}
    informe = _revalidar(
        documento_curiosidades,
        lambda d: d["evidence"]["facts"][0].update({"source_title": "Titulo cambiado"}),
        allowed=permitidos,
    )
    assert "procedencia_alterada" in _codigos(informe)


def test_word_count_manipulado(documento_infantil: ScriptDocument) -> None:
    informe = _revalidar(documento_infantil, lambda d: d["scenes"][0].update({"word_count": 3}))
    assert "word_count_incorrecto" in _codigos(informe)


def test_narracion_no_coincide_con_las_escenas(documento_infantil: ScriptDocument) -> None:
    informe = _revalidar(
        documento_infantil, lambda d: d["narration"].update({"full_text": "Otro texto distinto."})
    )
    assert "narracion_incoherente" in _codigos(informe)


def test_tiempos_manipulados(documento_infantil: ScriptDocument) -> None:
    informe = _revalidar(
        documento_infantil, lambda d: d["scenes"][1].update({"estimated_start_s": 99.0})
    )
    assert "tiempo_incoherente" in _codigos(informe)


def test_duracion_fuera_de_tolerancia_es_aviso(documento_infantil: ScriptDocument) -> None:
    informe = _revalidar(documento_infantil, lambda d: d["video"].update({"target_duration_s": 40.0}))
    codigos = _codigos(informe)
    assert "duracion_fuera_de_tolerancia" in codigos
    assert not informe.fatal  # sigue siendo exportable como needs_review
    assert informe.production_status() is ProductionStatus.NEEDS_REVIEW


def test_presupuesto_de_video_superado(documento_infantil: ScriptDocument) -> None:
    def mutar(d):
        for escena in d["scenes"]:
            escena["visual"]["asset_type"] = "video"

    informe = _revalidar(documento_infantil, mutar)
    assert "presupuesto_de_video" in _codigos(informe)


def test_numero_de_escenas_fuera_del_perfil(documento_infantil: ScriptDocument) -> None:
    informe = validate_document(
        documento_infantil, profile=get_profile("curiosidades_largo"), allowed_facts=None
    )
    assert "numero_de_escenas" in _codigos(informe)


def test_gancho_no_integrado_es_aviso(documento_infantil: ScriptDocument) -> None:
    def mutar(d):
        elegido = d["idea"]["selected_hook_id"]
        for variante in d["idea"]["hook_variants"]:
            if variante["hook_id"] == elegido:
                variante["text"] = "Un gancho que no aparece"

    informe = _revalidar(documento_infantil, mutar)
    assert "gancho_no_integrado" in _codigos(informe)
    assert not informe.fatal


def test_made_for_kids_incoherente(documento_infantil: ScriptDocument) -> None:
    informe = _revalidar(
        documento_infantil, lambda d: d["publishing"][0].update({"made_for_kids": False})
    )
    assert "made_for_kids_incoherente" in _codigos(informe)


def test_loop_incompleto(documento_curiosidades: ScriptDocument) -> None:
    informe = _revalidar(
        documento_curiosidades, lambda d: d["loop"].update({"closing_scene_id": None})
    )
    assert "loop_incompleto" in _codigos(informe)


def test_loop_desactivado_con_datos(documento_infantil: ScriptDocument) -> None:
    informe = _revalidar(
        documento_infantil, lambda d: d["loop"].update({"opening_scene_id": d["scenes"][0]["scene_id"]})
    )
    assert "loop_desactivado_con_datos" in _codigos(informe)


def test_cifra_sin_respaldo_produce_aviso(documento_curiosidades: ScriptDocument) -> None:
    """Una ambiguedad detectada produce needs_review, no una verificacion."""

    def mutar(d):
        for escena in d["scenes"]:
            if not escena["claim_refs"]:
                palabras = escena["narration_text"].split(" ")
                palabras[-1] = "7."
                escena["narration_text"] = " ".join(palabras)
                escena["word_count"] = len(
                    [p for p in " ".join(palabras).split() if any(c.isalnum() for c in p)]
                )
                break
        d["narration"]["full_text"] = " ".join(e["narration_text"] for e in d["scenes"])

    informe = _revalidar(documento_curiosidades, mutar)
    assert "cifra_sin_respaldo" in _codigos(informe)


def test_pausa_fuera_de_rango_la_rechaza_el_esquema(documento_infantil: ScriptDocument) -> None:
    datos = copy.deepcopy(documento_infantil.model_dump(mode="json"))
    datos["scenes"][0]["pause_after_s"] = 3.0
    with pytest.raises(ValidationError):
        ScriptDocument.model_validate(datos)


def test_aplicar_resultado_marca_needs_review(documento_infantil: ScriptDocument) -> None:
    documento = apply_validation_result(
        documento_infantil, warnings=["duracion_fuera_de_tolerancia: prueba"], has_evidence_doubt=False
    )
    assert documento.control.production_status is ProductionStatus.NEEDS_REVIEW
    assert documento.control.warnings == ["duracion_fuera_de_tolerancia: prueba"]

    documento = apply_validation_result(documento, warnings=[], has_evidence_doubt=False)
    assert documento.control.production_status is ProductionStatus.READY_FOR_PRODUCTION
