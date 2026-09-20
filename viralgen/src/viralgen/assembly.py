"""Construccion del documento final a partir de la respuesta del proveedor.

Aqui se marca la frontera entre lo que decide el modelo y lo que decide la
aplicacion:

* del modelo vienen textos: titulo, premisa, narracion, prompts, subtitulos,
  audio, loop y las afirmaciones con sus `fact_id`;
* la aplicacion fija identidad (job_id, fecha, canal, perfil, simulacion),
  la biblia visual configurada, todas las duraciones y conteos de palabras,
  `narration.full_text`, los registros de procedencia copiados del catalogo,
  los metadatos de publicacion obligatorios y la procedencia tecnica.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from pydantic import ValidationError as PydanticValidationError

from .errors import DocumentValidationError
from .profiles import Profile, SeriesBible
from .schemas.common import ProductionStatus, VerificationLevel
from .schemas.document import (
    Claim,
    Control,
    Evidence,
    FactRecord,
    HookVariant,
    Idea,
    Loop,
    Narration,
    Provenance,
    PublishingPlan,
    Scene,
    SceneAudio,
    SceneCaptions,
    SceneVisual,
    ScoreComponents,
    ScriptDocument,
    Video,
    VisualBible,
    VisualCharacter,
)
from .schemas.provider import ProviderScript
from .scoring import CandidateEvaluation
from .textutil import count_words
from .timing import build_timeline

_SLUG_RE = re.compile(r"[^a-z0-9_\-]+")


def slug_id(value: str, fallback: str) -> str:
    """Normaliza un identificador propuesto por el modelo.

    Minusculas, sin acentos raros ni espacios. Es una normalizacion de forma,
    no de contenido: no cambia a que se refiere el identificador, solo evita
    rechazar el documento por un guion bajo o una mayuscula.
    """
    from .textutil import strip_accents

    slug = _SLUG_RE.sub("_", strip_accents(value).lower()).strip("_-")
    slug = re.sub(r"_{2,}", "_", slug)
    if not slug or not slug[0].isalnum():
        slug = f"{fallback}_{slug}".strip("_-") if slug else fallback
    return slug[:64]


@dataclass
class AssemblyContext:
    """Todo lo que la aplicacion aporta al documento."""

    job_id: str
    created_at: datetime
    profile: Profile
    bible: SeriesBible
    simulation: bool
    target_duration_s: float
    prompt_version: str
    config_hash: str
    source_pack_hash: str | None
    facts_by_id: dict[str, FactRecord]
    selection: CandidateEvaluation
    provider_name: str
    model: str
    experiment_tag: str
    input_tokens: int = 0
    output_tokens: int = 0
    request_ids: tuple[str, ...] = ()
    estimated_cost_usd: float | None = None


def build_document(script: ProviderScript, ctx: AssemblyContext) -> ScriptDocument:
    """Ensambla y valida estructuralmente el documento.

    Lanza :class:`DocumentValidationError` con la lista de problemas si el
    documento no cumple el esquema; esa lista alimenta la unica llamada de
    reparacion disponible.
    """
    profile = ctx.profile
    ordered = sorted(script.scenes, key=lambda scene: scene.order)

    word_counts = [count_words(scene.narration_text) for scene in ordered]
    pauses = [float(scene.pause_after_s) for scene in ordered]
    timeline = build_timeline(word_counts, pauses, profile.target_wpm)

    scene_id_map = {
        scene.scene_id: slug_id(scene.scene_id, f"sc_{index + 1:02d}")
        for index, scene in enumerate(ordered)
    }
    claim_id_map = {
        claim.claim_id: slug_id(claim.claim_id, f"cl_{index + 1}")
        for index, claim in enumerate(script.claims)
    }

    scenes: list[Scene] = []
    for index, (scene, timing) in enumerate(zip(ordered, timeline, strict=True)):
        scenes.append(
            Scene(
                scene_id=scene_id_map[scene.scene_id],
                order=index + 1,
                beat=scene.beat,
                narration_text=scene.narration_text.strip(),
                pause_after_s=round(float(scene.pause_after_s), 3),
                word_count=timing.word_count,
                estimated_start_s=timing.start_s,
                estimated_end_s=timing.end_s,
                estimated_duration_s=timing.duration_s,
                character_ids=[
                    slug_id(identifier, "char") for identifier in scene.character_ids
                ],
                visual=SceneVisual(
                    asset_type=scene.visual.asset_type,
                    image_prompt=scene.visual.image_prompt.strip(),
                    motion_prompt=scene.visual.motion_prompt.strip(),
                    continuity_notes=scene.visual.continuity_notes.strip(),
                ),
                captions=SceneCaptions(
                    emphasis_words=[word.strip() for word in scene.captions.emphasis_words if word.strip()],
                    overlay_text=(scene.captions.overlay_text or None),
                ),
                audio=SceneAudio(
                    sfx_description=scene.audio.sfx_description or None,
                    sfx_cue=scene.audio.sfx_cue,
                    music_mood=scene.audio.music_mood or None,
                ),
                claim_refs=[
                    claim_id_map.get(ref, slug_id(ref, "cl")) for ref in scene.claim_refs
                ],
            )
        )

    full_text = " ".join(scene.narration_text for scene in scenes).strip()
    estimated_total = timeline[-1].end_s if timeline else 0.0

    used_fact_ids: list[str] = []
    for claim in script.claims:
        for fact_id in claim.fact_ids:
            if fact_id not in used_fact_ids:
                used_fact_ids.append(fact_id)
    missing_facts = [fact_id for fact_id in used_fact_ids if fact_id not in ctx.facts_by_id]
    if missing_facts:
        raise DocumentValidationError(
            "El guion referencia hechos que no estan en el catalogo utilizable: "
            + ", ".join(sorted(missing_facts)),
            details={
                "issues": [
                    f"fact_id_desconocido: {fact_id} no existe en el catalogo aportado "
                    "o no esta aprobado para este modo"
                    for fact_id in sorted(missing_facts)
                ]
            },
        )

    # Los registros de procedencia se COPIAN del catalogo, nunca del modelo.
    facts = [ctx.facts_by_id[fact_id] for fact_id in sorted(used_fact_ids)]

    claims = [
        Claim(
            claim_id=claim_id_map[claim.claim_id],
            claim_text=claim.claim_text.strip(),
            scene_ids=[scene_id_map.get(sid, slug_id(sid, "sc")) for sid in claim.scene_ids],
            fact_ids=sorted(set(claim.fact_ids)),
        )
        for claim in script.claims
    ]

    hook_variants = [
        HookVariant(hook_id=slug_id(variant.hook_id, f"hook_{index + 1}"), text=variant.text.strip())
        for index, variant in enumerate(script.hook_variants)
    ]
    hook_map = {
        variant.hook_id: hook.hook_id
        for variant, hook in zip(script.hook_variants, hook_variants, strict=True)
    }

    publishing = [
        PublishingPlan(
            platform=item.platform,
            title=item.title.strip(),
            caption=item.caption.strip(),
            hashtags=[tag.strip().lstrip("#") for tag in item.hashtags if tag.strip()],
            made_for_kids=profile.made_for_kids,
            ai_disclosure_review_required=True,
        )
        for item in script.publishing
    ]

    verification = (
        VerificationLevel.SOURCE_PACK_ONLY if claims else VerificationLevel.NONE
    )

    try:
        document = ScriptDocument(
            job_id=ctx.job_id,
            created_at=ctx.created_at,
            channel=profile.channel,
            profile_id=profile.profile_id,
            language=profile.language,
            target_platforms=list(profile.target_platforms),
            simulation=ctx.simulation,
            control=Control(
                production_status=ProductionStatus.NEEDS_REVIEW,
                warnings=[],
                prompt_version=ctx.prompt_version,
                config_hash=ctx.config_hash,
                source_pack_hash=ctx.source_pack_hash,
            ),
            idea=Idea(
                title=script.title.strip(),
                premise=script.premise.strip(),
                topic=script.topic.strip(),
                selected_idea_id=slug_id(ctx.selection.idea.idea_ref, "idea_1"),
                editorial_score=ctx.selection.score,
                score_components=ScoreComponents(**ctx.selection.components),
                score_rationale=ctx.selection.rationale,
                hook_variants=hook_variants,
                selected_hook_id=hook_map.get(
                    script.selected_hook_id, slug_id(script.selected_hook_id, "hook_1")
                ),
                educational_goal=(script.educational_goal or None),
                experiment_tag=ctx.experiment_tag,
            ),
            video=Video(
                width=profile.width,
                height=profile.height,
                fps=profile.fps,
                aspect_ratio=profile.aspect_ratio,
                target_duration_s=round(float(ctx.target_duration_s), 3),
                estimated_duration_s=round(float(estimated_total), 3),
                actual_duration_s=None,
            ),
            narration=Narration(
                full_text=full_text,
                word_count=count_words(full_text),
                target_wpm=profile.target_wpm,
                voice_direction=script.voice_direction.strip(),
                pronunciation_notes=[note.strip() for note in script.pronunciation_notes if note.strip()],
            ),
            visual_bible=VisualBible(
                style_prompt=ctx.bible.style_prompt,
                color_palette=list(ctx.bible.color_palette),
                negative_prompt=ctx.bible.negative_prompt,
                characters=[
                    VisualCharacter(
                        character_id=character.character_id,
                        name=character.name,
                        description=character.description,
                        wardrobe=character.wardrobe,
                    )
                    for character in ctx.bible.characters
                ],
            ),
            scenes=scenes,
            loop=Loop(
                enabled=script.loop.enabled,
                opening_scene_id=(
                    scene_id_map.get(script.loop.opening_scene_id or "", None)
                    if script.loop.enabled
                    else None
                ),
                closing_scene_id=(
                    scene_id_map.get(script.loop.closing_scene_id or "", None)
                    if script.loop.enabled
                    else None
                ),
                connection_explanation=(
                    (script.loop.connection_explanation or None) if script.loop.enabled else None
                ),
            ),
            evidence=Evidence(claims=claims, facts=facts, verification_level=verification),
            publishing=publishing,
            provenance=Provenance(
                provider=ctx.provider_name,
                model=ctx.model,
                request_ids=list(ctx.request_ids),
                input_tokens=ctx.input_tokens,
                output_tokens=ctx.output_tokens,
                estimated_cost_usd=ctx.estimated_cost_usd,
            ),
        )
    except PydanticValidationError as exc:
        raise DocumentValidationError(
            "El guion devuelto no cumple el contrato JSON.",
            details={"issues": _pydantic_issues(exc)},
        ) from exc
    return document


def _pydantic_issues(exc: PydanticValidationError) -> list[str]:
    """Convierte los errores de pydantic en instrucciones concretas."""
    issues: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"])
        issues.append(f"esquema[{location}]: {error['msg']}")
    return issues[:40]


def apply_validation_result(
    document: ScriptDocument, *, warnings: list[str], has_evidence_doubt: bool
) -> ScriptDocument:
    """Fija `production_status`, los avisos y el nivel de verificacion."""
    document.control.warnings = warnings[:50]
    document.control.production_status = (
        ProductionStatus.NEEDS_REVIEW if warnings else ProductionStatus.READY_FOR_PRODUCTION
    )
    if document.evidence.claims and has_evidence_doubt:
        document.evidence.verification_level = VerificationLevel.NEEDS_REVIEW
    return document
