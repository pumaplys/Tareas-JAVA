"""Validaciones del documento final.

Se separan dos niveles, porque no significan lo mismo:

* ``fatal``   - la integridad del plan falla (referencias que no resuelven,
  hechos no permitidos, presupuestos superados). El trabajo termina en
  ``failed`` y NO se exporta ningun ``script.json``.
* ``warning`` - el plan es integro pero no cumple algun criterio editorial o
  de duracion. Se exporta con ``production_status=needs_review`` y el motivo
  queda en ``control.warnings``.

Las comprobaciones deterministas (referencias, conteos, tiempos, metadatos)
estan separadas de las valoraciones editoriales (conexion del cierre con la
promesa, adecuacion del gancho). Estas ultimas son heuristicas de apoyo: no
son garantias automaticas de calidad.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .profiles import Profile
from .schemas.common import AssetType, Beat, ProductionStatus
from .schemas.document import FactRecord, ScriptDocument
from .textutil import (
    count_words,
    jaccard_similarity,
    normalize_for_compare,
    starts_with_normalized,
)
from .timing import (
    DURATION_TOLERANCE,
    MAX_HOOK_DURATION_S,
    MAX_SCENE_DURATION_S,
    MIN_SCENE_DURATION_S,
    build_timeline,
    speech_seconds,
)

Severity = Literal["fatal", "warning"]

#: Cifras sueltas en la narracion de curiosidades: sospechosas si la escena
#: no referencia ninguna afirmacion respaldada.
_NUMERIC_RE = re.compile(r"\d")


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    severity: Severity
    path: str = ""

    def as_text(self) -> str:
        location = f" [{self.path}]" if self.path else ""
        return f"{self.code}{location}: {self.message}"

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "path": self.path,
        }


@dataclass
class ValidationReport:
    issues: list[ValidationIssue]

    @property
    def fatal(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "fatal"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.issues

    def production_status(self) -> ProductionStatus:
        if self.issues:
            return ProductionStatus.NEEDS_REVIEW
        return ProductionStatus.READY_FOR_PRODUCTION

    def warning_texts(self) -> list[str]:
        return [issue.as_text()[:200] for issue in self.issues]

    def repair_instructions(self) -> list[str]:
        return [issue.as_text() for issue in self.issues]


def validate_document(
    document: ScriptDocument,
    *,
    profile: Profile,
    allowed_facts: dict[str, FactRecord] | None = None,
    promise: str = "",
) -> ValidationReport:
    """Ejecuta todas las validaciones y devuelve el informe completo."""
    issues: list[ValidationIssue] = []
    issues += _check_structure(document, profile)
    issues += _check_references(document, allowed_facts)
    issues += _check_timing(document, profile)
    issues += _check_narration(document)
    issues += _check_metadata(document, profile)
    issues += _check_editorial(document, profile, promise)
    return ValidationReport(issues=issues)


# ---------------------------------------------------------------------------
# Estructura
# ---------------------------------------------------------------------------


def _check_structure(document: ScriptDocument, profile: Profile) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    scenes = document.scenes

    if not (profile.min_scenes <= len(scenes) <= profile.max_scenes):
        issues.append(
            ValidationIssue(
                "numero_de_escenas",
                f"el perfil {profile.profile_id} admite entre {profile.min_scenes} y "
                f"{profile.max_scenes} escenas; hay {len(scenes)}",
                "fatal",
                "scenes",
            )
        )

    scene_ids = [scene.scene_id for scene in scenes]
    if len(set(scene_ids)) != len(scene_ids):
        issues.append(
            ValidationIssue("scene_id_duplicado", "hay scene_id repetidos", "fatal", "scenes")
        )

    expected_orders = list(range(1, len(scenes) + 1))
    if [scene.order for scene in scenes] != expected_orders:
        issues.append(
            ValidationIssue(
                "orden_de_escenas",
                "el campo order debe ser 1..N consecutivo y coincidir con la posicion",
                "fatal",
                "scenes[].order",
            )
        )

    for scene in scenes:
        if not (0.0 <= scene.pause_after_s <= 1.5):
            issues.append(
                ValidationIssue(
                    "pausa_fuera_de_rango",
                    f"pause_after_s={scene.pause_after_s} fuera de [0, 1.5]",
                    "fatal",
                    f"scenes[{scene.order}].pause_after_s",
                )
            )

    video_scenes = [s for s in scenes if s.visual.asset_type is AssetType.VIDEO]
    if len(video_scenes) > profile.video_scene_budget:
        issues.append(
            ValidationIssue(
                "presupuesto_de_video",
                f"{len(video_scenes)} escenas de video superan el presupuesto del perfil "
                f"({profile.video_scene_budget})",
                "fatal",
                "scenes[].visual.asset_type",
            )
        )

    hook_ids = {variant.hook_id for variant in document.idea.hook_variants}
    if document.idea.selected_hook_id not in hook_ids:
        issues.append(
            ValidationIssue(
                "gancho_inexistente",
                f"selected_hook_id={document.idea.selected_hook_id} no esta entre las alternativas",
                "fatal",
                "idea.selected_hook_id",
            )
        )

    loop = document.loop
    if loop.enabled:
        missing = [
            name
            for name, value in (
                ("opening_scene_id", loop.opening_scene_id),
                ("closing_scene_id", loop.closing_scene_id),
                ("connection_explanation", loop.connection_explanation),
            )
            if not value
        ]
        if missing:
            issues.append(
                ValidationIssue(
                    "loop_incompleto",
                    f"loop.enabled=true pero faltan: {', '.join(missing)}",
                    "fatal",
                    "loop",
                )
            )
        for name, value in (
            ("opening_scene_id", loop.opening_scene_id),
            ("closing_scene_id", loop.closing_scene_id),
        ):
            if value and value not in scene_ids:
                issues.append(
                    ValidationIssue(
                        "loop_referencia_invalida",
                        f"loop.{name}={value} no corresponde a ninguna escena",
                        "fatal",
                        f"loop.{name}",
                    )
                )
    else:
        extra = [
            name
            for name, value in (
                ("opening_scene_id", loop.opening_scene_id),
                ("closing_scene_id", loop.closing_scene_id),
                ("connection_explanation", loop.connection_explanation),
            )
            if value is not None
        ]
        if extra:
            issues.append(
                ValidationIssue(
                    "loop_desactivado_con_datos",
                    f"loop.enabled=false exige null en: {', '.join(extra)}",
                    "fatal",
                    "loop",
                )
            )
    return issues


# ---------------------------------------------------------------------------
# Referencias
# ---------------------------------------------------------------------------


def _check_references(
    document: ScriptDocument, allowed_facts: dict[str, FactRecord] | None
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    scene_ids = {scene.scene_id for scene in document.scenes}
    character_ids = {character.character_id for character in document.visual_bible.characters}
    claim_ids = {claim.claim_id for claim in document.evidence.claims}
    fact_ids_in_doc = {fact.fact_id for fact in document.evidence.facts}

    for scene in document.scenes:
        unknown_chars = sorted(set(scene.character_ids) - character_ids)
        if unknown_chars:
            issues.append(
                ValidationIssue(
                    "personaje_inexistente",
                    f"character_ids no presentes en la biblia visual: {', '.join(unknown_chars)}",
                    "fatal",
                    f"scenes[{scene.order}].character_ids",
                )
            )
        unknown_claims = sorted(set(scene.claim_refs) - claim_ids)
        if unknown_claims:
            issues.append(
                ValidationIssue(
                    "claim_ref_inexistente",
                    f"claim_refs sin claim correspondiente: {', '.join(unknown_claims)}",
                    "fatal",
                    f"scenes[{scene.order}].claim_refs",
                )
            )

    for claim in document.evidence.claims:
        unknown_scenes = sorted(set(claim.scene_ids) - scene_ids)
        if unknown_scenes:
            issues.append(
                ValidationIssue(
                    "claim_escena_inexistente",
                    f"{claim.claim_id} referencia escenas inexistentes: {', '.join(unknown_scenes)}",
                    "fatal",
                    f"evidence.claims[{claim.claim_id}].scene_ids",
                )
            )
        unknown_facts = sorted(set(claim.fact_ids) - fact_ids_in_doc)
        if unknown_facts:
            issues.append(
                ValidationIssue(
                    "fact_id_inexistente",
                    f"{claim.claim_id} referencia hechos ausentes de evidence.facts: "
                    f"{', '.join(unknown_facts)}",
                    "fatal",
                    f"evidence.claims[{claim.claim_id}].fact_ids",
                )
            )
        referenced_scenes = {
            scene.scene_id for scene in document.scenes if claim.claim_id in scene.claim_refs
        }
        if referenced_scenes and set(claim.scene_ids) != referenced_scenes:
            issues.append(
                ValidationIssue(
                    "claim_escenas_incoherentes",
                    f"{claim.claim_id}.scene_ids no coincide con las escenas que lo referencian",
                    "fatal",
                    f"evidence.claims[{claim.claim_id}].scene_ids",
                )
            )

    if allowed_facts is not None:
        not_allowed = sorted(fact_ids_in_doc - set(allowed_facts))
        if not_allowed:
            issues.append(
                ValidationIssue(
                    "hecho_no_aprobado",
                    "hechos que no estan aprobados o no son utilizables en este modo: "
                    + ", ".join(not_allowed),
                    "fatal",
                    "evidence.facts",
                )
            )
        for fact in document.evidence.facts:
            original = allowed_facts.get(fact.fact_id)
            if original and original.model_dump(mode="json") != fact.model_dump(mode="json"):
                issues.append(
                    ValidationIssue(
                        "procedencia_alterada",
                        f"{fact.fact_id} no coincide literalmente con el catalogo importado",
                        "fatal",
                        f"evidence.facts[{fact.fact_id}]",
                    )
                )

    orphan_facts = fact_ids_in_doc - {
        fact_id for claim in document.evidence.claims for fact_id in claim.fact_ids
    }
    if orphan_facts:
        issues.append(
            ValidationIssue(
                "hecho_sin_uso",
                "evidence.facts solo debe contener los hechos realmente usados: "
                + ", ".join(sorted(orphan_facts)),
                "fatal",
                "evidence.facts",
            )
        )
    return issues


# ---------------------------------------------------------------------------
# Tiempos
# ---------------------------------------------------------------------------


def _check_timing(document: ScriptDocument, profile: Profile) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    wpm = document.narration.target_wpm
    expected = build_timeline(
        [count_words(scene.narration_text) for scene in document.scenes],
        [scene.pause_after_s for scene in document.scenes],
        wpm,
    )

    for scene, timing in zip(document.scenes, expected, strict=True):
        path = f"scenes[{scene.order}]"
        if scene.word_count != timing.word_count:
            issues.append(
                ValidationIssue(
                    "word_count_incorrecto",
                    f"word_count={scene.word_count} pero el texto tiene {timing.word_count} palabras",
                    "fatal",
                    f"{path}.word_count",
                )
            )
        for field_name, actual, wanted in (
            ("estimated_start_s", scene.estimated_start_s, timing.start_s),
            ("estimated_end_s", scene.estimated_end_s, timing.end_s),
            ("estimated_duration_s", scene.estimated_duration_s, timing.duration_s),
        ):
            if abs(actual - wanted) > 0.011:
                issues.append(
                    ValidationIssue(
                        "tiempo_incoherente",
                        f"{field_name}={actual} no coincide con el calculo local ({wanted})",
                        "fatal",
                        f"{path}.{field_name}",
                    )
                )
        if not (MIN_SCENE_DURATION_S <= timing.duration_s <= MAX_SCENE_DURATION_S):
            issues.append(
                ValidationIssue(
                    "duracion_de_escena",
                    f"la escena dura {timing.duration_s:.2f} s, fuera del rango razonable "
                    f"[{MIN_SCENE_DURATION_S}, {MAX_SCENE_DURATION_S}] s; ajusta el texto",
                    "warning",
                    f"{path}.estimated_duration_s",
                )
            )

    total = round(expected[-1].end_s, 3) if expected else 0.0
    target = document.video.target_duration_s
    if abs(document.video.estimated_duration_s - total) > 0.011:
        issues.append(
            ValidationIssue(
                "duracion_total_incoherente",
                f"video.estimated_duration_s={document.video.estimated_duration_s} no coincide "
                f"con la suma de escenas ({total})",
                "fatal",
                "video.estimated_duration_s",
            )
        )
    if abs(total - target) > target * DURATION_TOLERANCE:
        issues.append(
            ValidationIssue(
                "duracion_fuera_de_tolerancia",
                f"la duracion estimada ({total:.1f} s) se aleja mas de un "
                f"{DURATION_TOLERANCE:.0%} del objetivo ({target:.1f} s); "
                "ajusta el texto de la narracion, no la velocidad de la voz",
                "warning",
                "video.estimated_duration_s",
            )
        )
    if not (profile.min_duration_s <= total <= profile.max_duration_s):
        issues.append(
            ValidationIssue(
                "duracion_fuera_del_perfil",
                f"la duracion estimada ({total:.1f} s) esta fuera del rango del perfil "
                f"({profile.min_duration_s:g}-{profile.max_duration_s:g} s)",
                "warning",
                "video.estimated_duration_s",
            )
        )

    hook_text = _selected_hook_text(document)
    if hook_text:
        hook_seconds = speech_seconds(count_words(hook_text), wpm)
        if hook_seconds > MAX_HOOK_DURATION_S + 0.01:
            issues.append(
                ValidationIssue(
                    "gancho_demasiado_largo",
                    f"el gancho seleccionado dura unos {hook_seconds:.1f} s estimados; "
                    f"el limite es {MAX_HOOK_DURATION_S:g} s",
                    "warning",
                    "idea.selected_hook_id",
                )
            )
    return issues


# ---------------------------------------------------------------------------
# Narracion
# ---------------------------------------------------------------------------


def _check_narration(document: ScriptDocument) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    joined = " ".join(scene.narration_text.strip() for scene in document.scenes).strip()
    if document.narration.full_text.strip() != joined:
        issues.append(
            ValidationIssue(
                "narracion_incoherente",
                "narration.full_text debe ser la concatenacion exacta, en orden, "
                "de narration_text de las escenas",
                "fatal",
                "narration.full_text",
            )
        )
    expected_words = count_words(joined)
    if document.narration.word_count != expected_words:
        issues.append(
            ValidationIssue(
                "word_count_total_incorrecto",
                f"narration.word_count={document.narration.word_count} pero el texto tiene "
                f"{expected_words} palabras",
                "fatal",
                "narration.word_count",
            )
        )

    for scene in document.scenes:
        normalized = normalize_for_compare(scene.narration_text)
        for word in scene.captions.emphasis_words:
            if normalize_for_compare(word) not in normalized:
                issues.append(
                    ValidationIssue(
                        "enfasis_inexistente",
                        f"la palabra destacada {word!r} no aparece en la narracion de la escena",
                        "warning",
                        f"scenes[{scene.order}].captions.emphasis_words",
                    )
                )
    return issues


# ---------------------------------------------------------------------------
# Metadatos
# ---------------------------------------------------------------------------


def _check_metadata(document: ScriptDocument, profile: Profile) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if document.channel is not profile.channel:
        issues.append(
            ValidationIssue(
                "canal_incoherente",
                f"channel={document.channel.value} no corresponde al perfil {profile.profile_id}",
                "fatal",
                "channel",
            )
        )
    if document.profile_id != profile.profile_id:
        issues.append(
            ValidationIssue(
                "perfil_incoherente",
                "profile_id no corresponde al perfil cargado",
                "fatal",
                "profile_id",
            )
        )
    expected_platforms = set(profile.target_platforms)
    if set(document.target_platforms) != expected_platforms:
        issues.append(
            ValidationIssue(
                "plataformas_incoherentes",
                "target_platforms no coincide con las plataformas del perfil",
                "fatal",
                "target_platforms",
            )
        )
    published = [item.platform for item in document.publishing]
    if set(published) != expected_platforms:
        issues.append(
            ValidationIssue(
                "publicacion_incompleta",
                "debe haber exactamente un elemento de publishing por plataforma del perfil",
                "fatal",
                "publishing",
            )
        )
    for item in document.publishing:
        if item.platform.value == "youtube_shorts" and profile.made_for_kids is not None:
            if item.made_for_kids is not profile.made_for_kids:
                issues.append(
                    ValidationIssue(
                        "made_for_kids_incoherente",
                        f"el perfil {profile.profile_id} exige made_for_kids="
                        f"{str(profile.made_for_kids).lower()} en YouTube",
                        "fatal",
                        "publishing[youtube_shorts].made_for_kids",
                    )
                )
        if item.ai_disclosure_review_required is not True:
            issues.append(
                ValidationIssue(
                    "divulgacion_ia",
                    "ai_disclosure_review_required debe ser true siempre",
                    "fatal",
                    "publishing[].ai_disclosure_review_required",
                )
            )

    if profile.requires_evidence and not document.evidence.claims:
        issues.append(
            ValidationIssue(
                "sin_evidencia",
                f"el perfil {profile.profile_id} exige al menos una afirmacion respaldada",
                "fatal",
                "evidence.claims",
            )
        )
    if document.video.width != profile.width or document.video.height != profile.height:
        issues.append(
            ValidationIssue(
                "composicion_incoherente",
                "width/height no coinciden con el perfil",
                "fatal",
                "video",
            )
        )
    if document.video.fps != profile.fps or document.video.aspect_ratio != profile.aspect_ratio:
        issues.append(
            ValidationIssue(
                "formato_incoherente",
                "fps/aspect_ratio no coinciden con el perfil",
                "fatal",
                "video",
            )
        )
    if document.narration.target_wpm != profile.target_wpm:
        issues.append(
            ValidationIssue(
                "ritmo_incoherente",
                "narration.target_wpm no coincide con el ritmo del perfil",
                "fatal",
                "narration.target_wpm",
            )
        )
    return issues


# ---------------------------------------------------------------------------
# Criterios editoriales (heuristicas, no garantias)
# ---------------------------------------------------------------------------


def _selected_hook_text(document: ScriptDocument) -> str:
    for variant in document.idea.hook_variants:
        if variant.hook_id == document.idea.selected_hook_id:
            return variant.text
    return ""


def _check_editorial(
    document: ScriptDocument, profile: Profile, promise: str
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    scenes = document.scenes
    if not scenes:
        return issues

    hook_text = _selected_hook_text(document)
    if hook_text and not starts_with_normalized(scenes[0].narration_text, hook_text):
        issues.append(
            ValidationIssue(
                "gancho_no_integrado",
                "el gancho elegido debe aparecer literalmente al principio de la primera escena; "
                "las alternativas no se suman a la narracion",
                "warning",
                "scenes[1].narration_text",
            )
        )
    if scenes[0].beat is not Beat.HOOK:
        issues.append(
            ValidationIssue(
                "primer_beat",
                "la primera escena debe tener beat=hook",
                "warning",
                "scenes[1].beat",
            )
        )
    if scenes[-1].beat is not Beat.CLOSE:
        issues.append(
            ValidationIssue(
                "ultimo_beat",
                "la ultima escena debe tener beat=close",
                "warning",
                f"scenes[{len(scenes)}].beat",
            )
        )
    if not any(scene.beat is Beat.RESOLUTION for scene in scenes):
        issues.append(
            ValidationIssue(
                "sin_resolucion",
                "falta una escena con beat=resolution: la historia debe tener desenlace",
                "warning",
                "scenes[].beat",
            )
        )

    if promise:
        closing_text = " ".join(
            scene.narration_text for scene in scenes if scene.beat in (Beat.RESOLUTION, Beat.CLOSE)
        )
        if jaccard_similarity(promise, closing_text) < 0.05:
            issues.append(
                ValidationIssue(
                    "cierre_sin_promesa",
                    "el desenlace no parece responder a la promesa de la idea "
                    "(comprobacion lexica orientativa, requiere criterio humano)",
                    "warning",
                    "scenes[].narration_text",
                )
            )

    if profile.channel.value == "curiosidades":
        for scene in scenes:
            if _NUMERIC_RE.search(scene.narration_text) and not scene.claim_refs:
                issues.append(
                    ValidationIssue(
                        "cifra_sin_respaldo",
                        "la escena incluye cifras pero no referencia ninguna afirmacion "
                        "respaldada por el catalogo; revisar antes de producir",
                        "warning",
                        f"scenes[{scene.order}].claim_refs",
                    )
                )
        if not document.loop.enabled:
            issues.append(
                ValidationIssue(
                    "sin_loop",
                    "el perfil de curiosidades espera un cierre que conecte con el comienzo",
                    "warning",
                    "loop.enabled",
                )
            )
    if profile.channel.value == "infantil" and not document.idea.educational_goal:
        issues.append(
            ValidationIssue(
                "sin_aprendizaje",
                "el perfil infantil exige un aprendizaje concreto (educational_goal)",
                "warning",
                "idea.educational_goal",
            )
        )
    return issues
