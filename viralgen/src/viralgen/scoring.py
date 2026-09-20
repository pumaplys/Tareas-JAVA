"""Filtrado, deteccion de duplicados y puntuacion editorial de ideas.

Todo lo de este modulo es LOCAL y determinista. El modelo aporta cuatro
valoraciones razonadas (hook, clarity, payoff, visual_potential); la novedad y
la puntuacion final las calcula la aplicacion.

La deteccion de duplicados es lexica: hash exacto sobre texto normalizado mas
indice de Jaccard sobre conjuntos de tokens. NO es busqueda semantica y no
detecta una idea equivalente escrita con otro vocabulario.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .schemas.provider import ProviderIdea
from .textutil import content_hash, jaccard_similarity, normalize_for_compare

#: Pesos de la puntuacion editorial. Suman 1,0; la escala final es 0-100.
WEIGHTS: dict[str, float] = {
    "hook": 0.25,
    "clarity": 0.20,
    "payoff": 0.20,
    "visual_potential": 0.15,
    "novelty": 0.20,
}

SCORE_SCALE = 20.0  # 20 x (media ponderada 0-5) = 0-100


@dataclass(frozen=True)
class HistoryItem:
    """Idea previa del mismo canal usada para medir novedad y duplicados."""

    title: str
    premise: str
    norm_hash: str
    central_fact_id: str | None = None
    job_id: str | None = None

    @property
    def comparable_text(self) -> str:
        return f"{self.title} {self.premise}"


@dataclass
class CandidateEvaluation:
    """Resultado de evaluar una idea propuesta por el proveedor."""

    idea: ProviderIdea
    norm_hash: str
    central_fact_id: str | None
    max_similarity: float
    similar_to: str | None
    novelty: float
    components: dict[str, float]
    score: float
    rationale: str
    rejected_reason: str | None = None
    batch_index: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return self.rejected_reason is None

    def to_row(self) -> dict:
        return {
            "idea_ref": self.idea.idea_ref,
            "title": self.idea.title,
            "premise": self.idea.premise,
            "topic": self.idea.topic,
            "promise": self.idea.promise,
            "possible_ending": self.idea.possible_ending,
            "visual_concept": self.idea.visual_concept,
            "educational_goal": self.idea.educational_goal,
            "fact_ids": list(self.idea.fact_ids),
            "score": round(self.score, 3),
            "score_components": {k: round(v, 3) for k, v in self.components.items()},
            "score_rationale": self.rationale,
            "max_similarity": round(self.max_similarity, 4),
            "similar_to": self.similar_to,
            "norm_hash": self.norm_hash,
            "rejected_reason": self.rejected_reason,
            "batch_index": self.batch_index,
            "notes": list(self.notes),
        }


def idea_hash(title: str, premise: str) -> str:
    """Hash de duplicado exacto sobre el texto normalizado de titulo+premisa."""
    return content_hash(title, premise)


def compute_score(components: dict[str, float]) -> float:
    """20 x suma ponderada de los cinco componentes (cada uno entre 0 y 5)."""
    weighted = sum(WEIGHTS[name] * components[name] for name in WEIGHTS)
    return round(SCORE_SCALE * weighted, 3)


def _rationale(idea: ProviderIdea, components: dict[str, float], max_similarity: float) -> str:
    ratings = idea.ratings
    return (
        f"gancho {components['hook']:.1f} ({ratings.hook.rationale.strip()[:70]}); "
        f"claridad {components['clarity']:.1f}; "
        f"cumplimiento {components['payoff']:.1f}; "
        f"potencial visual {components['visual_potential']:.1f}; "
        f"novedad {components['novelty']:.2f} (similitud lexica maxima "
        f"{max_similarity:.2f} frente al historial del canal)."
    )[:600]


def evaluate_candidates(
    ideas: list[ProviderIdea],
    *,
    history: list[HistoryItem],
    threshold: float,
    requires_evidence: bool,
    allowed_fact_ids: set[str] | None,
    batch_index: int = 0,
) -> list[CandidateEvaluation]:
    """Evalua una tanda de ideas: filtra, mide novedad y puntua.

    `allowed_fact_ids` es el conjunto de hechos realmente utilizables. Si es
    None, no se exige evidencia (ficcion infantil).
    """
    evaluations: list[CandidateEvaluation] = []
    seen_hashes = {item.norm_hash: item for item in history}
    seen_central_facts = {
        item.central_fact_id: item for item in history if item.central_fact_id
    }
    accepted_texts: list[tuple[str, str]] = []  # (etiqueta, texto comparable)

    for idea in ideas:
        norm_hash = idea_hash(idea.title, idea.premise)
        fact_ids = sorted(set(idea.fact_ids))
        central_fact_id = fact_ids[0] if fact_ids else None
        comparable = f"{idea.title} {idea.premise}"

        similarities: list[tuple[float, str]] = [
            (jaccard_similarity(comparable, item.comparable_text), item.title)
            for item in history
        ]
        similarities += [
            (jaccard_similarity(comparable, text), label) for label, text in accepted_texts
        ]
        max_similarity, similar_to = max(similarities, default=(0.0, None))
        if similar_to is not None and max_similarity <= 0.0:
            similar_to = None

        novelty = round(5.0 * (1.0 - max_similarity), 3)
        components = {
            "hook": float(idea.ratings.hook.score),
            "clarity": float(idea.ratings.clarity.score),
            "payoff": float(idea.ratings.payoff.score),
            "visual_potential": float(idea.ratings.visual_potential.score),
            "novelty": novelty,
        }
        evaluation = CandidateEvaluation(
            idea=idea,
            norm_hash=norm_hash,
            central_fact_id=central_fact_id,
            max_similarity=max_similarity,
            similar_to=similar_to,
            novelty=novelty,
            components=components,
            score=compute_score(components),
            rationale=_rationale(idea, components, max_similarity),
            batch_index=batch_index,
        )

        reason = _rejection_reason(
            idea,
            norm_hash=norm_hash,
            central_fact_id=central_fact_id,
            max_similarity=max_similarity,
            threshold=threshold,
            requires_evidence=requires_evidence,
            allowed_fact_ids=allowed_fact_ids,
            seen_hashes=seen_hashes,
            seen_central_facts=seen_central_facts,
        )
        evaluation.rejected_reason = reason
        if reason is None:
            accepted_texts.append((idea.title, comparable))
            seen_hashes[norm_hash] = HistoryItem(idea.title, idea.premise, norm_hash)
            if central_fact_id:
                seen_central_facts[central_fact_id] = seen_hashes[norm_hash]
        evaluations.append(evaluation)

    return evaluations


def _rejection_reason(
    idea: ProviderIdea,
    *,
    norm_hash: str,
    central_fact_id: str | None,
    max_similarity: float,
    threshold: float,
    requires_evidence: bool,
    allowed_fact_ids: set[str] | None,
    seen_hashes: dict[str, HistoryItem],
    seen_central_facts: dict[str, HistoryItem],
) -> str | None:
    if not normalize_for_compare(idea.title) or not normalize_for_compare(idea.premise):
        return "campos_vacios: la idea no tiene titulo o premisa utilizables"
    if norm_hash in seen_hashes:
        return "duplicado_exacto: mismo titulo+premisa normalizados que una idea ya registrada"
    if max_similarity >= threshold:
        return (
            f"duplicado_lexico: similitud Jaccard {max_similarity:.2f} >= umbral {threshold:.2f}"
        )
    if requires_evidence:
        if not idea.fact_ids:
            return "sin_evidencia: el perfil exige al menos un fact_id del catalogo"
        if allowed_fact_ids is not None:
            unknown = sorted(set(idea.fact_ids) - allowed_fact_ids)
            if unknown:
                return f"fact_id_no_utilizable: {', '.join(unknown)}"
        if central_fact_id and central_fact_id in seen_central_facts:
            return (
                f"hecho_central_repetido: {central_fact_id} ya fue el eje de una idea anterior"
            )
    elif idea.fact_ids and allowed_fact_ids is not None:
        unknown = sorted(set(idea.fact_ids) - allowed_fact_ids)
        if unknown:
            return f"fact_id_no_utilizable: {', '.join(unknown)}"
    return None


def select_best(evaluations: list[CandidateEvaluation]) -> CandidateEvaluation | None:
    """Elige la idea valida con mayor puntuacion, con desempate determinista.

    Orden de desempate: puntuacion, luego gancho, luego novedad, luego titulo
    normalizado en orden alfabetico y por ultimo `idea_ref`. Dos ejecuciones
    con los mismos candidatos eligen siempre la misma idea.
    """
    valid = [item for item in evaluations if item.is_valid]
    if not valid:
        return None
    return min(
        valid,
        key=lambda item: (
            -round(item.score, 6),
            -item.components["hook"],
            -round(item.components["novelty"], 6),
            normalize_for_compare(item.idea.title),
            item.idea.idea_ref,
        ),
    )
