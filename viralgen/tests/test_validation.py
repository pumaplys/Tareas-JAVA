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


# ---------------------------------------------------------------------------
# Continuidad de personajes en el prompt de imagen
# ---------------------------------------------------------------------------


def test_prompt_de_imagen_es_autocontenido(documento_infantil: ScriptDocument) -> None:
    """Cada escena describe el aspecto de sus personajes dentro del prompt."""
    from viralgen.textutil import normalize_for_compare

    personajes = {c.character_id: c for c in documento_infantil.visual_bible.characters}
    assert any(escena.character_ids for escena in documento_infantil.scenes)
    for escena in documento_infantil.scenes:
        prompt = normalize_for_compare(escena.visual.image_prompt)
        for character_id in escena.character_ids:
            huella = normalize_for_compare(personajes[character_id].description)[:40]
            assert huella in prompt, f"escena {escena.order} no describe a {character_id}"


def test_el_prompt_solo_nombra_a_los_personajes_de_la_escena(
    documento_infantil: ScriptDocument,
) -> None:
    from viralgen.textutil import normalize_for_compare

    personajes = {c.character_id: c for c in documento_infantil.visual_bible.characters}
    for escena in documento_infantil.scenes:
        prompt = normalize_for_compare(escena.visual.image_prompt)
        ausentes = set(personajes) - set(escena.character_ids)
        for character_id in ausentes:
            huella = normalize_for_compare(personajes[character_id].description)[:40]
            assert huella not in prompt


def test_prompt_sin_continuidad_es_fatal(documento_infantil: ScriptDocument) -> None:
    def mutar(d):
        d["scenes"][0]["visual"]["image_prompt"] = "Un plano generico sin describir a nadie."

    informe = _revalidar(documento_infantil, mutar)
    assert "prompt_sin_continuidad" in _codigos(informe)
    assert informe.fatal


# ---------------------------------------------------------------------------
# Criterio de admision para consumidores reales
# ---------------------------------------------------------------------------


def test_una_simulacion_nunca_es_admisible(documento_infantil: ScriptDocument) -> None:
    from viralgen.validation import check_admission

    informe = validate_document(
        documento_infantil, profile=get_profile("infantil_cuentos"), allowed_facts=None
    )
    assert informe.issues == []
    assert documento_infantil.control.production_status is ProductionStatus.READY_FOR_PRODUCTION

    admision = check_admission(documento_infantil, export_complete=True, report=informe)
    assert admision.admissible is False
    assert admision.checks["no_es_simulacion"] is False
    assert any("simulation=true" in motivo for motivo in admision.reasons)


def _documento_real(documento: ScriptDocument, **cambios) -> ScriptDocument:
    datos = copy.deepcopy(documento.model_dump(mode="json"))
    datos["simulation"] = False
    for ruta, valor in cambios.items():
        objetivo = datos
        partes = ruta.split(".")
        for parte in partes[:-1]:
            objetivo = objetivo[parte]
        objetivo[partes[-1]] = valor
    return ScriptDocument.model_validate(datos)


def test_admision_exige_las_cinco_condiciones(documento_infantil: ScriptDocument) -> None:
    from viralgen.validation import check_admission

    real = _documento_real(documento_infantil)
    informe = validate_document(real, profile=get_profile("infantil_cuentos"), allowed_facts=None)
    admision = check_admission(real, export_complete=True, report=informe)
    assert admision.admissible is True
    assert all(admision.checks.values())

    # 1) Exportacion incompleta.
    assert check_admission(real, export_complete=False, report=informe).admissible is False

    # 2) Borrador con avisos: se conserva, pero no habilita produccion.
    borrador = _documento_real(
        documento_infantil,
        **{"control.production_status": "needs_review", "control.warnings": ["aviso de prueba"]},
    )
    rechazo = check_admission(borrador, export_complete=True, report=informe)
    assert rechazo.admissible is False
    assert rechazo.checks["ready_for_production"] is False

    # 3) Documento con integridad rota.
    roto = validate_document(
        real, profile=get_profile("curiosidades_largo"), allowed_facts=None
    )
    assert check_admission(real, export_complete=True, report=roto).admissible is False


def test_admision_rechaza_una_version_de_esquema_desconocida(
    documento_infantil: ScriptDocument,
) -> None:
    from viralgen.validation import SUPPORTED_SCHEMA_VERSIONS, check_admission

    real = _documento_real(documento_infantil)
    assert real.schema_version in SUPPORTED_SCHEMA_VERSIONS
    # El esquema fija schema_version como constante, asi que un documento de
    # otra version ni siquiera valida: esa es la primera barrera.
    datos = copy.deepcopy(real.model_dump(mode="json"))
    datos["schema_version"] = "2.0"
    with pytest.raises(ValidationError):
        ScriptDocument.model_validate(datos)

    # Y si el objeto llegara por otra via, el criterio lo rechaza igualmente.
    object.__setattr__(real, "__dict__", {**real.__dict__, "schema_version": "2.0"})
    admision = check_admission(real, export_complete=True, report=None)
    assert admision.checks["version_de_esquema_compatible"] is False
    assert admision.admissible is False
