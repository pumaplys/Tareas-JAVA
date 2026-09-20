"""Filtrado, duplicados y puntuacion editorial."""

from __future__ import annotations

import pytest

from viralgen.schemas.provider import ProviderIdea, ProviderRating, ProviderRatings
from viralgen.scoring import (
    HistoryItem,
    compute_score,
    evaluate_candidates,
    idea_hash,
    select_best,
)


def _idea(ref: str, titulo: str, premisa: str, *, fact_ids=(), notas=(4, 4, 4, 4)) -> ProviderIdea:
    hook, clarity, payoff, visual = notas
    return ProviderIdea(
        idea_ref=ref,
        title=titulo,
        premise=premisa,
        topic="tema",
        promise="promesa concreta",
        possible_ending="desenlace",
        visual_concept="concepto visual",
        educational_goal=None,
        fact_ids=list(fact_ids),
        ratings=ProviderRatings(
            hook=ProviderRating(score=hook, rationale="r"),
            clarity=ProviderRating(score=clarity, rationale="r"),
            payoff=ProviderRating(score=payoff, rationale="r"),
            visual_potential=ProviderRating(score=visual, rationale="r"),
        ),
    )


def test_formula_de_puntuacion() -> None:
    # Todo a 5 -> 100. Todo a 0 -> 0.
    assert compute_score(dict.fromkeys(
        ["hook", "clarity", "payoff", "visual_potential", "novelty"], 5.0)) == 100.0
    assert compute_score(dict.fromkeys(
        ["hook", "clarity", "payoff", "visual_potential", "novelty"], 0.0)) == 0.0
    # 20 x (0,25*4 + 0,20*3 + 0,20*5 + 0,15*2 + 0,20*5) = 78
    assert compute_score(
        {"hook": 4, "clarity": 3, "payoff": 5, "visual_potential": 2, "novelty": 5}
    ) == pytest.approx(78.0)


def test_sin_historial_la_novedad_es_maxima() -> None:
    evaluaciones = evaluate_candidates(
        [_idea("idea_1", "Un titulo unico", "Una premisa unica sobre ranas azules")],
        history=[],
        threshold=0.8,
        requires_evidence=False,
        allowed_fact_ids=None,
    )
    assert evaluaciones[0].components["novelty"] == 5.0
    assert evaluaciones[0].is_valid


def test_duplicado_exacto_por_hash() -> None:
    titulo, premisa = "Lumi y la manzana", "Lumi comparte la manzana con Tobi"
    historial = [HistoryItem(titulo, premisa, idea_hash(titulo, premisa))]
    evaluaciones = evaluate_candidates(
        [_idea("idea_1", "¡LUMI Y LA MANZANA!", "lumi comparte la manzana con tobi")],
        history=historial,
        threshold=0.99,
        requires_evidence=False,
        allowed_fact_ids=None,
    )
    assert evaluaciones[0].rejected_reason.startswith("duplicado_exacto")


def test_duplicado_lexico_por_jaccard() -> None:
    historial = [
        HistoryItem(
            "Agua helada en la Luna",
            "Un video sobre agua helada encontrada en crateres de la Luna",
            idea_hash("Agua helada en la Luna", "otra"),
        )
    ]
    evaluaciones = evaluate_candidates(
        [
            _idea(
                "idea_1",
                "Agua helada en la Luna",
                "Video sobre agua helada encontrada en crateres de la Luna",
            )
        ],
        history=historial,
        threshold=0.8,
        requires_evidence=False,
        allowed_fact_ids=None,
    )
    assert evaluaciones[0].rejected_reason.startswith("duplicado_lexico")


def test_duplicados_dentro_de_la_misma_tanda() -> None:
    idea = _idea("idea_1", "Titulo repetido", "Premisa repetida palabra por palabra")
    otra = _idea("idea_2", "Titulo repetido", "Premisa repetida palabra por palabra")
    evaluaciones = evaluate_candidates(
        [idea, otra], history=[], threshold=0.8, requires_evidence=False, allowed_fact_ids=None
    )
    assert evaluaciones[0].is_valid
    assert evaluaciones[1].rejected_reason.startswith("duplicado_exacto")


def test_evidencia_obligatoria() -> None:
    evaluaciones = evaluate_candidates(
        [_idea("idea_1", "Sin fuentes", "Una premisa sin ningun fact_id asociado")],
        history=[],
        threshold=0.8,
        requires_evidence=True,
        allowed_fact_ids={"f_1"},
    )
    assert evaluaciones[0].rejected_reason.startswith("sin_evidencia")


def test_fact_id_no_utilizable() -> None:
    evaluaciones = evaluate_candidates(
        [_idea("idea_1", "Con fuente mala", "Premisa", fact_ids=["f_inventado"])],
        history=[],
        threshold=0.8,
        requires_evidence=True,
        allowed_fact_ids={"f_1"},
    )
    assert "fact_id_no_utilizable" in evaluaciones[0].rejected_reason


def test_hecho_central_repetido() -> None:
    historial = [HistoryItem("Otra cosa", "Otra premisa", "hash", central_fact_id="f_1")]
    evaluaciones = evaluate_candidates(
        [_idea("idea_1", "Titulo distinto", "Premisa distinta", fact_ids=["f_1"])],
        history=historial,
        threshold=0.8,
        requires_evidence=True,
        allowed_fact_ids={"f_1"},
    )
    assert evaluaciones[0].rejected_reason.startswith("hecho_central_repetido")


def test_seleccion_determinista_y_desempate() -> None:
    # Vocabularios disjuntos: ninguna penaliza la novedad de la otra, asi que
    # idea_1 e idea_2 empatan y decide el desempate determinista.
    ideas = [
        _idea("idea_1", "Zeta", "Cuento de zorros nocturnos", notas=(4, 4, 4, 4)),
        _idea("idea_2", "Alfa", "Historia sobre buhos diurnos", notas=(4, 4, 4, 4)),
        _idea("idea_3", "Beta", "Relato con erizos lectores", notas=(3, 3, 3, 3)),
    ]
    evaluaciones = evaluate_candidates(
        ideas, history=[], threshold=0.8, requires_evidence=False, allowed_fact_ids=None
    )
    elegida = select_best(evaluaciones)
    # Empate entre idea_1 e idea_2: gana el titulo normalizado menor ("alfa").
    assert elegida.idea.idea_ref == "idea_2"
    # Repetir la seleccion da el mismo resultado.
    assert select_best(evaluaciones).idea.idea_ref == "idea_2"


def test_sin_candidatos_validos() -> None:
    assert select_best([]) is None
