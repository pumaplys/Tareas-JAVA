"""Proveedor simulado y determinista.

Funciona sin claves y sin acceso de red. Dado el mismo `--seed` y la misma
peticion produce exactamente la misma salida, lo que permite probar el
recorrido completo (ideas -> seleccion -> guion -> validacion -> exportacion)
en CI.

No imita la calidad de un modelo real: compone texto en espanol a partir de
bancos de frases por beat, ajustando el numero de palabras para que la
duracion estimada caiga dentro de la tolerancia del perfil. Sus resultados
llevan siempre ``simulation=true`` y NUNCA deben presentarse como una
integracion real.
"""

from __future__ import annotations

import random
import time
from typing import Any

from ..schemas.provider import (
    ProviderClaim,
    ProviderHookVariant,
    ProviderIdea,
    ProviderIdeaBatch,
    ProviderLoop,
    ProviderPublishing,
    ProviderRating,
    ProviderRatings,
    ProviderScene,
    ProviderSceneAudio,
    ProviderSceneCaptions,
    ProviderSceneVisual,
    ProviderScript,
)
from ..textutil import count_words, sha256_json, words
from ..timing import words_budget
from .base import CallBudget, ProviderRequest, ProviderResult, ProviderUsage, TextProvider

MOCK_MODEL = "mock-deterministic-v1"

# ---------------------------------------------------------------------------
# Bancos de frases (simulacion; no pretenden ser buena escritura)
# ---------------------------------------------------------------------------

_INFANTIL_PHRASES = {
    "hook": [
        "el bosque todavia estaba medio dormido",
        "la luz entraba entre las ramas",
        "olia a musgo recien mojado",
        "nadie esperaba una manana asi",
        "todo empezo junto al arroyo",
    ],
    "context": [
        "Tobi llegaba despacio con su chaleco azul",
        "los dos querian exactamente lo mismo",
        "ninguno sabia como repartirlo sin enfadarse",
        "Nube los miraba desde la rama alta",
        "el problema parecia mas grande de lo que era",
        "Lumi apreto su bufanda verde sin decir nada",
        "la mochila beige se quedo abierta en la hierba",
        "hacia falta una idea buena y rapida",
    ],
    "development": [
        "Lumi respiro hondo antes de hablar",
        "pensaron juntos una idea bastante sencilla",
        "midieron las partes con una hoja grande",
        "probaron una vez y despues otra",
        "Tobi propuso empezar por el principio",
        "Nube conto en voz baja hasta tres",
        "cada uno dijo lo que sentia de verdad",
        "descubrieron que hablar tranquilos ayudaba mucho",
        "marcaron una raya suave en la arena",
        "compararon los dos trozos con cuidado",
        "el erizo sonrio al ver el reparto",
        "la zorrita acepto esperar un momento",
        "se turnaron sin discutir ni empujar",
        "el buho recordo una regla facil",
        "guardaron lo que sobraba para despues",
        "repasaron el plan una ultima vez",
        "nadie tuvo que gritar para entenderse",
        "las manos dejaron de temblar enseguida",
        "el enfado se fue quedando pequeno",
        "aprendieron a mirar antes de decidir",
        "una idea llevo a la siguiente",
        "todo encajo mejor de lo previsto",
        "la prisa dejo de tener sentido",
        "el bosque parecio escuchar en silencio",
    ],
    "resolution": [
        "al final cada uno tuvo su parte justa",
        "la solucion resulto mas facil de lo que parecia",
        "los dos se miraron muy aliviados",
        "el problema se deshizo casi solo",
        "el reparto dejo contentos a todos",
        "ya nadie queria volver a discutir",
    ],
    "close": [
        "el bosque volvio a quedarse tranquilo",
        "y la merienda supo mejor compartida",
        "manana habra otra pequena aventura",
        "Nube cerro los ojos satisfecha",
        "la tarde termino con una risa corta",
        "todo quedo listo para el dia siguiente",
    ],
}

_CURIOSIDADES_PHRASES = {
    "hook": [
        "lo tienes delante todos los dias",
        "y casi nunca te paras a mirarlo",
        "parece simple pero no lo es",
        "casi nadie sabe como funciona",
        "el detalle esta a la vista",
    ],
    "context": [
        "la idea original venia de otro problema",
        "durante anos se hizo de otra manera",
        "alguien busco una forma mas comoda",
        "el material cambia mucho el resultado",
        "al principio fallaba casi siempre",
        "la solucion tardo bastante en aparecer",
        "nadie pensaba que seria tan util",
        "el primer diseno era mucho mas basto",
    ],
    "development": [
        "la clave esta en la forma de las piezas",
        "cada parte empuja a la siguiente",
        "el diseno reparte la fuerza sin romperse",
        "por eso aguanta mejor el uso diario",
        "los ensayos fueron corrigiendo cada detalle",
        "el resultado se volvio casi invisible",
        "la geometria hace todo el trabajo",
        "el orden del montaje importa muchisimo",
        "una curva sencilla anade rigidez",
        "el rozamiento se reparte por toda la pieza",
        "el conjunto cede antes de partirse",
        "la tension viaja hasta el punto fuerte",
        "cambiar un angulo lo cambia todo",
        "el acabado evita que se atasque",
        "las piezas encajan siempre en el mismo orden",
        "la fabricacion en serie obligo a simplificar",
        "cada version quito una pieza innecesaria",
        "el desgaste avisa antes de fallar",
        "el diseno tolera pequenos errores de uso",
        "por eso se puede reparar tan facil",
        "el mecanismo se explica en un gesto",
        "nada de esto se ve desde fuera",
        "el truco lleva decadas sin cambiar",
        "y sigue siendo la mejor opcion",
    ],
    "resolution": [
        "por eso funciona tan bien",
        "y esa es la respuesta completa",
        "ahi esta el truco de verdad",
        "esa es toda la explicacion",
        "el mecanismo no esconde nada mas",
        "queda claro en cuanto lo miras",
    ],
    "close": [
        "la proxima vez lo veras distinto",
        "y volveras a fijarte en ese detalle",
        "empieza a mirar de nuevo",
        "vuelve al principio y compruebalo",
        "el objeto sigue igual, tu ya no",
        "ese gesto diario cambia de sentido",
    ],
}

#: Coletillas de longitud exacta para cuadrar el ultimo hueco de palabras.
#: Se pegan a la frase anterior con una coma, nunca sueltas.
_TAILS_INFANTIL: dict[int, list[str]] = {
    1: ["despacio", "tambien", "entonces"],
    2: ["sin prisa", "muy contentos", "otra vez"],
    3: ["poco a poco", "como cada manana", "con mucho cuidado"],
    4: ["igual que el dia anterior", "sin soltar la bufanda verde"],
    5: ["y el arroyo siguio sonando bajito", "mientras la luz subia entre las ramas"],
    6: ["y nadie volvio a levantar la voz", "mientras el musgo seguia oliendo a lluvia"],
}

_TAILS_CURIOSIDADES: dict[int, list[str]] = {
    1: ["siempre", "ademas", "todavia"],
    2: ["sin excepcion", "en realidad", "poco despues"],
    3: ["una y otra vez", "sin apenas esfuerzo", "desde el primer dia"],
    4: ["aunque no lo parezca ahora", "incluso con el uso diario"],
    5: ["y por eso nadie lo cambia", "aunque casi nadie se fije en ello"],
    6: ["y sigue siendo asi en todos los modelos", "aunque el material haya cambiado varias veces"],
}

_INFANTIL_IDEAS = [
    ("una manzana brillante", "compartir hace la merienda mas dulce", "compartir"),
    ("un charco lleno de estrellas", "esperar el turno tambien es jugar", "esperar el turno"),
    ("una hoja que no cabia", "pedir ayuda no quita valor a nadie", "pedir ayuda"),
    ("un nido caido del arbol", "cuidar lo pequeno cambia el dia entero", "cuidar a los demas"),
    ("un camino de piedras torcidas", "ordenar las cosas calma el enfado", "nombrar lo que siento"),
]

_CURIOSIDADES_ANGLES = [
    "el detalle que nadie mira",
    "el problema que lo hizo nacer",
    "la pieza que lo cambia todo",
    "por que sigue igual desde entonces",
    "lo que pasa cuando falla",
]

_HOOKS_INFANTIL = [
    "Lumi encontro algo brillante",
    "Hoy el bosque tenia un problema",
    "Mira lo que paso aqui",
]

_HOOKS_CURIOSIDADES = [
    "Mira otra vez este objeto",
    "Aqui hay un detalle escondido",
    "Esto explica algo que usas",
]


class MockProvider(TextProvider):
    """Implementacion simulada de :class:`TextProvider`."""

    name = "mock"
    model = MOCK_MODEL

    def __init__(self, seed: int | None = None) -> None:
        self.seed = seed

    # -- API ---------------------------------------------------------------

    def generate_structured(self, request: ProviderRequest, budget: CallBudget) -> ProviderResult:
        budget.spend(request.stage)
        started = time.monotonic()
        rng = self._rng(request)

        if request.schema_model is ProviderIdeaBatch:
            parsed: Any = self._ideas(request.payload, rng)
        elif request.schema_model is ProviderScript:
            parsed = self._script(request.payload, rng)
        else:  # pragma: no cover - no hay mas esquemas en el MVP
            raise NotImplementedError(f"Esquema no soportado por el mock: {request.schema_model}")

        latency_ms = int((time.monotonic() - started) * 1000)
        usage = ProviderUsage(
            # Cifras sinteticas y claramente etiquetadas: no son consumo real.
            input_tokens=len(request.input_text) // 4,
            output_tokens=len(parsed.model_dump_json()) // 4,
            request_id=f"mock-{request.stage}-{sha256_json(request.payload)[:12]}",
            latency_ms=latency_ms,
            model=MOCK_MODEL,
            known=True,
        )
        return ProviderResult(parsed=parsed, usage=usage, raw_status="completed")

    # -- Interno -----------------------------------------------------------

    def _rng(self, request: ProviderRequest) -> random.Random:
        """Semilla derivada del `--seed` o, si falta, del contenido de la peticion."""
        if self.seed is not None:
            # La tanda se incluye para que una segunda peticion de ideas no
            # devuelva exactamente lo mismo que la primera.
            material = sha256_json(
                [self.seed, request.stage, request.payload.get("batch_index", 0)]
            )
        else:
            material = sha256_json([request.stage, request.payload])
        return random.Random(int(material[:16], 16))

    # ---- Etapa de ideas --------------------------------------------------

    def _ideas(self, payload: dict, rng: random.Random) -> ProviderIdeaBatch:
        profile = payload.get("profile", {})
        channel = profile.get("channel", "infantil")
        count = int(payload.get("count", 5))
        topic = (payload.get("topic") or "").strip()
        facts: list[dict] = list(payload.get("facts") or [])

        ideas: list[ProviderIdea] = []
        for index in range(count):
            if channel == "curiosidades":
                ideas.append(self._curiosidad_idea(index, topic, facts, rng))
            else:
                ideas.append(self._infantil_idea(index, topic, rng))
        return ProviderIdeaBatch(ideas=ideas)

    def _infantil_idea(self, index: int, topic: str, rng: random.Random) -> ProviderIdea:
        obj, lesson, tag = _INFANTIL_IDEAS[index % len(_INFANTIL_IDEAS)]
        subject = topic or "una tarde en el bosque"
        title = f"Lumi y {obj}"
        premise = (
            f"Lumi encuentra {obj} mientras piensa en {subject}; con Tobi y Nube busca "
            f"una forma justa de resolverlo y descubre que {lesson}."
        )
        return ProviderIdea(
            idea_ref=f"idea_{index + 1}",
            title=title,
            premise=premise,
            topic=subject,
            promise=f"que {lesson}",
            possible_ending=f"Lumi y Tobi reparten {obj} y se quedan tranquilos.",
            visual_concept=(
                f"Bosque calido al amanecer con {obj} como objeto protagonista, "
                "planos cercanos de Lumi y Tobi."
            ),
            educational_goal=f"Aprender que {lesson}.",
            fact_ids=[],
            ratings=_ratings(rng, tag),
        )

    def _curiosidad_idea(
        self, index: int, topic: str, facts: list[dict], rng: random.Random
    ) -> ProviderIdea:
        angle = _CURIOSIDADES_ANGLES[index % len(_CURIOSIDADES_ANGLES)]
        fact = facts[index % len(facts)] if facts else None
        subject = topic or "un objeto cotidiano"
        if fact is None:
            # Sin catalogo el comando `ideas` solo puede proponer preguntas
            # abiertas, marcadas como no verificadas.
            return ProviderIdea(
                idea_ref=f"idea_{index + 1}",
                title=f"Pregunta abierta sobre {subject}: {angle}",
                premise=(
                    f"Pregunta sin verificar sobre {subject}, centrada en {angle}. "
                    "Requiere buscar y aprobar fuentes antes de escribir guion."
                ),
                topic=subject,
                promise=f"por que importa {angle}",
                possible_ending="Pendiente de fuentes aprobadas.",
                visual_concept=f"Objeto cotidiano sobre fondo neutro, {angle}.",
                educational_goal=None,
                fact_ids=[],
                ratings=_ratings(rng, angle),
            )
        head = _lower_first(_fragment(fact.get("claim_text", subject), 8))
        return ProviderIdea(
            idea_ref=f"idea_{index + 1}",
            title=f"{head[:1].upper()}{head[1:]}: {angle}",
            premise=(
                f"Partiendo de que {head}, el video explica {angle} paso a paso y "
                "cierra respondiendo la pregunta inicial."
            ),
            topic=topic or head,
            promise=f"por que {head}",
            possible_ending=f"Se resuelve mostrando {angle}.",
            visual_concept=(
                "Objeto protagonista centrado sobre fondo neutro con luz lateral suave; "
                "manos que lo giran para mostrar el detalle."
            ),
            educational_goal=None,
            fact_ids=[str(fact["fact_id"])],
            ratings=_ratings(rng, angle),
        )

    # ---- Etapa de guion --------------------------------------------------

    def _script(self, payload: dict, rng: random.Random) -> ProviderScript:
        profile = payload["profile"]
        idea = payload["idea"]
        facts: list[dict] = list(payload.get("facts") or [])
        characters: list[dict] = list(payload.get("characters") or [])
        target_duration = float(payload["target_duration_s"])
        wpm = int(profile["target_wpm"])
        channel = profile["channel"]

        n_scenes = _scene_count(profile, target_duration)
        pause_regular, pause_last = (0.3, 0.5) if channel == "infantil" else (0.25, 0.4)
        pauses = [pause_regular] * (n_scenes - 1) + [pause_last]
        total_words = words_budget(target_duration, wpm, sum(pauses))

        beats = _beats(n_scenes)
        hooks = _hook_variants(channel, idea)
        selected_hook = hooks[0]

        counts = _distribute(total_words, n_scenes)
        phrases = _INFANTIL_PHRASES if channel == "infantil" else _CURIOSIDADES_PHRASES
        tails = _TAILS_INFANTIL if channel == "infantil" else _TAILS_CURIOSIDADES
        used_phrases: set[str] = set()

        claim_assignments, claims_meta = _plan_claims(facts, beats)

        scenes: list[ProviderScene] = []
        for index, (beat, count, pause) in enumerate(zip(beats, counts, pauses, strict=True)):
            scene_id = f"sc_{index + 1:02d}"
            lead = ""
            if index == 0:
                lead = selected_hook.text
            elif beat == "close":
                # El cierre retoma literalmente la promesa de la idea, para que
                # el desenlace responda a lo prometido al principio.
                fragment = _fragment(
                    idea.get("promise", ""), max(3, min(8, count - 4)), drop_digits=True
                )
                lead = f"Ahora ya sabes {fragment}" if fragment else ""
            elif scene_id in claim_assignments:
                fact = claim_assignments[scene_id]["fact"]
                lead = _fragment(fact.get("claim_text", ""), max(4, min(12, count - 4))).capitalize()

            narration = _compose(
                rng, count, phrases[beat], tails, used_phrases, lead=lead
            )
            scene_characters = _scene_characters(characters, index, n_scenes)
            scenes.append(
                ProviderScene(
                    scene_id=scene_id,
                    order=index + 1,
                    beat=beat,
                    narration_text=narration,
                    pause_after_s=pause,
                    character_ids=scene_characters,
                    visual=ProviderSceneVisual(
                        asset_type=_asset_type(index, n_scenes, int(profile["video_scene_budget"])),
                        image_prompt=_image_prompt(channel, beat, characters, idea),
                        motion_prompt=_motion_prompt(beat),
                        continuity_notes=_continuity(channel, index, n_scenes),
                    ),
                    captions=ProviderSceneCaptions(
                        emphasis_words=_emphasis(narration, rng),
                        overlay_text=None,
                    ),
                    audio=ProviderSceneAudio(
                        sfx_description=_sfx(channel, beat),
                        sfx_cue="scene_start" if beat in {"hook", "context"} else "scene_end",
                        music_mood=profile.get("music_mood_hint", "")[:180] or None,
                    ),
                    claim_refs=(
                        [claim_assignments[scene_id]["claim_id"]]
                        if scene_id in claim_assignments
                        else []
                    ),
                )
            )

        claims = [
            ProviderClaim(
                claim_id=meta["claim_id"],
                claim_text=meta["claim_text"],
                scene_ids=meta["scene_ids"],
                fact_ids=meta["fact_ids"],
            )
            for meta in claims_meta
        ]

        loop_enabled = channel == "curiosidades"
        return ProviderScript(
            title=idea["title"],
            premise=idea["premise"],
            topic=idea["topic"],
            educational_goal=idea.get("educational_goal"),
            hook_variants=hooks,
            selected_hook_id=selected_hook.hook_id,
            voice_direction=profile.get("voice_direction_hint", "Voz clara y natural."),
            pronunciation_notes=_pronunciation(characters, channel),
            scenes=scenes,
            loop=ProviderLoop(
                enabled=loop_enabled,
                opening_scene_id=scenes[0].scene_id if loop_enabled else None,
                closing_scene_id=scenes[-1].scene_id if loop_enabled else None,
                connection_explanation=(
                    "El cierre retoma el objeto de la primera escena y lo mira de nuevo "
                    "con la respuesta ya dada, sin repetir material."
                    if loop_enabled
                    else None
                ),
            ),
            claims=claims,
            publishing=[
                ProviderPublishing(
                    platform=platform,
                    title=_publishing_title(idea["title"], platform),
                    caption=_publishing_caption(idea, channel),
                    hashtags=_hashtags(channel),
                )
                for platform in profile["target_platforms"]
            ],
        )


# ---------------------------------------------------------------------------
# Ayudas de composicion
# ---------------------------------------------------------------------------


def _ratings(rng: random.Random, salt: str) -> ProviderRatings:
    local = random.Random(f"{salt}|{rng.random()}")

    def rating(name: str) -> ProviderRating:
        score = local.choice([3, 4, 4, 5])
        return ProviderRating(
            score=score,
            rationale=f"Valoracion simulada de {name}: {score}/5 segun el banco de frases del mock.",
        )

    return ProviderRatings(
        hook=rating("gancho"),
        clarity=rating("claridad"),
        payoff=rating("cumplimiento"),
        visual_potential=rating("potencial visual"),
    )


def _scene_count(profile: dict, target_duration_s: float) -> int:
    """Escenas de unos 7 segundos, ajustadas al rango del perfil."""
    raw = round(target_duration_s / 7.0)
    return max(int(profile["min_scenes"]), min(int(profile["max_scenes"]), int(raw)))


def _beats(n_scenes: int) -> list[str]:
    beats = ["hook", "context"]
    beats += ["development"] * max(0, n_scenes - 4)
    beats += ["resolution", "close"]
    return beats[:n_scenes] if len(beats) >= n_scenes else beats + ["development"] * (n_scenes - len(beats))


def _distribute(total_words: int, n_scenes: int) -> list[int]:
    base, remainder = divmod(total_words, n_scenes)
    return [base + (1 if index < remainder else 0) for index in range(n_scenes)]


#: Palabras de enlace que dejarian la frase cortada si quedaran al final.
_TRAILING_CONNECTORS = {
    "a", "al", "como", "con", "de", "del", "el", "en", "entre", "la", "las", "lo", "los",
    "mas", "modo", "o", "para", "pero", "por", "que", "se", "si", "sin", "sobre", "su",
    "sus", "un", "una", "unas", "unos", "y",
}


def _fragment(text: str, max_words: int, *, drop_digits: bool = False) -> str:
    """Primeras palabras de un texto, sin dejar una conjuncion colgando.

    Con `drop_digits` se descartan los tokens con cifras: se usa en la escena
    de cierre, que no referencia ninguna afirmacion, para no arrastrar alli un
    dato numerico sin respaldo.
    """
    tokens = words(text)
    if drop_digits:
        tokens = [token for token in tokens if not any(char.isdigit() for char in token)]
    tokens = tokens[:max_words]
    while len(tokens) > 3 and tokens[-1].lower() in _TRAILING_CONNECTORS:
        tokens.pop()
    return " ".join(tokens)


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text


def _compose(
    rng: random.Random,
    n_words: int,
    bank: list[str],
    tails: dict[int, list[str]],
    used: set[str],
    lead: str = "",
) -> str:
    """Compone un texto con EXACTAMENTE `n_words` palabras.

    `lead` va al principio como oracion propia (el gancho, el enlace con la
    promesa o la parafrasis del hecho). El resto se rellena con frases del
    banco sin repetir ninguna dentro del mismo guion, y el hueco final se
    cierra con una coletilla de la longitud justa, pegada a la frase anterior.
    """
    sentences: list[str] = []
    remaining = n_words

    if lead:
        lead_clean = lead.strip().rstrip(" .,;:")
        lead_words = count_words(lead_clean)
        if lead_words >= n_words:
            return _finish([" ".join(words(lead_clean)[:n_words])])
        sentences.append(lead_clean)
        remaining -= lead_words

    available = sorted(phrase for phrase in bank if phrase not in used)
    if not available:  # banco agotado: se permite reutilizar
        used.clear()
        available = sorted(bank)

    current: list[str] = []
    while remaining > 0:
        options = [phrase for phrase in available if count_words(phrase) <= remaining]
        if not options:
            break
        choice = rng.choice(options)
        available.remove(choice)
        used.add(choice)
        current.append(choice)
        remaining -= count_words(choice)
        if len(current) == 2:
            sentences.append(", ".join(current))
            current = []
        if not available:
            used.clear()
            available = sorted(phrase for phrase in bank if phrase not in current)

    if remaining > 0:
        tail = _tail_for(rng, tails, remaining, used)
        if current:
            current[-1] = f"{current[-1]}, {tail}"
        elif sentences:
            sentences[-1] = f"{sentences[-1]}, {tail}"
        else:
            current.append(tail)
    if current:
        sentences.append(", ".join(current))
    return _finish(sentences)


def _tail_for(
    rng: random.Random, tails: dict[int, list[str]], n_words: int, used: set[str]
) -> str:
    """Coletilla de exactamente `n_words` palabras, combinando si hace falta."""
    parts: list[str] = []
    remaining = n_words
    guard = 0
    while remaining > 0 and guard < 12:
        guard += 1
        size = min(remaining, max(tails))
        while size > 0 and size not in tails:
            size -= 1
        if size == 0:
            break
        options = sorted(item for item in tails[size] if item not in used) or sorted(tails[size])
        choice = rng.choice(options)
        used.add(choice)
        parts.append(choice)
        remaining -= size
    return " ".join(parts)


def _finish(sentences: list[str]) -> str:
    """Mayuscula inicial por oracion y punto final. No cambia el conteo."""
    rendered = ". ".join(
        sentence[0].upper() + sentence[1:] if sentence else sentence for sentence in sentences
    )
    return rendered.rstrip(" .,") + "."


def _hook_variants(channel: str, idea: dict) -> list[ProviderHookVariant]:
    """Tres alternativas de gancho, de seis palabras o menos.

    Solo la primera entra en la narracion: las otras dos quedan registradas
    como alternativas y NO se suman al guion.
    """
    texts = _HOOKS_INFANTIL if channel == "infantil" else _HOOKS_CURIOSIDADES
    return [
        ProviderHookVariant(hook_id=f"hook_{letter}", text=_trim_words(text, 6))
        for letter, text in zip("abc", texts, strict=True)
    ]


def _trim_words(text: str, max_words: int) -> str:
    tokens = words(text)
    if len(tokens) <= max_words:
        return text.strip()
    return " ".join(tokens[:max_words])


def _plan_claims(facts: list[dict], beats: list[str]) -> tuple[dict[str, dict], list[dict]]:
    """Reparte hasta tres hechos entre las escenas de desarrollo."""
    if not facts:
        return {}, []
    eligible = [
        f"sc_{index + 1:02d}"
        for index, beat in enumerate(beats)
        if beat in {"context", "development", "resolution"}
    ]
    assignments: dict[str, dict] = {}
    meta: list[dict] = []
    for position, fact in enumerate(facts[: min(3, len(eligible))]):
        scene_id = eligible[position]
        claim_id = f"cl_{position + 1}"
        assignments[scene_id] = {"claim_id": claim_id, "fact": fact}
        meta.append(
            {
                "claim_id": claim_id,
                "claim_text": _paraphrase(fact.get("claim_text", "")),
                "scene_ids": [scene_id],
                "fact_ids": [str(fact["fact_id"])],
            }
        )
    return assignments, meta


def _paraphrase(claim_text: str) -> str:
    text = claim_text.strip().rstrip(".")
    return f"Segun la fuente revisada, {text[:1].lower()}{text[1:]}."[:600]


def _scene_characters(characters: list[dict], index: int, n_scenes: int) -> list[str]:
    if not characters:
        return []
    ids = [str(character["character_id"]) for character in characters]
    if len(ids) == 1:
        return ids if index in {0, n_scenes - 1} or index % 2 == 0 else []
    if index == 0:
        return ids[:1]
    if index == n_scenes - 1:
        return ids[:2]
    return [ids[index % len(ids)]]


def _asset_type(index: int, n_scenes: int, budget: int) -> str:
    """La primera y la penultima escena piden video si el presupuesto llega."""
    if budget <= 0:
        return "image"
    video_slots = {0}
    if budget >= 2:
        video_slots.add(max(0, n_scenes - 2))
    if budget >= 3:
        video_slots.add(n_scenes // 2)
    return "video" if index in sorted(video_slots)[:budget] else "image"


def _image_prompt(channel: str, beat: str, characters: list[dict], idea: dict) -> str:
    names = ", ".join(str(character["name"]) for character in characters[:2]) or "hero object"
    if channel == "infantil":
        prompt = (
            f"Storybook forest scene, {names} in a warm clearing, {beat} moment of the story, "
            "medium shot at eye level, soft morning light from the left, gentle rounded shapes, "
            "no text in the image"
        )
    else:
        prompt = (
            f"Single everyday object centered on a seamless neutral backdrop, {names} entering "
            f"frame to point at the detail, {beat} moment, close-up three quarter angle, soft key "
            "light with long shadow, no text in the image"
        )
    return prompt[:600]


def _motion_prompt(beat: str) -> str:
    moves = {
        "hook": "slow push-in of about five percent, steady and calm",
        "context": "slight lateral drift to the right, constant speed",
        "development": "gentle parallax with a small tilt down",
        "resolution": "slow pull-back that reveals the whole scene",
        "close": "hold almost still with a very slow breathing zoom",
    }
    return moves.get(beat, "slow push-in, steady and calm")


def _continuity(channel: str, index: int, n_scenes: int) -> str:
    if index == 0:
        return "Primera escena: fija paleta, luz y encuadre de referencia para el resto."
    if index == n_scenes - 1:
        return "Ultima escena: repite el encuadre de la primera con la luz un punto mas calida."
    subject = "los personajes" if channel == "infantil" else "el objeto protagonista"
    return f"Mantiene {subject}, la paleta y la direccion de la luz de la escena anterior."


def _emphasis(narration: str, rng: random.Random) -> list[str]:
    candidates = sorted({token for token in words(narration) if len(token) >= 6})
    if not candidates:
        return []
    return [rng.choice(candidates)]


def _sfx(channel: str, beat: str) -> str | None:
    if channel == "infantil":
        return {"hook": "Hojas que crujen suavemente", "close": "Brisa corta y calida"}.get(beat)
    return {"hook": "Clic seco del objeto", "resolution": "Golpe suave de confirmacion"}.get(beat)


def _pronunciation(characters: list[dict], channel: str) -> list[str]:
    notes = [f"{character['name']}: se lee tal cual se escribe." for character in characters[:2]]
    if channel == "curiosidades":
        notes.append("Marcar una pausa breve antes de la respuesta final.")
    return notes[:12]


def _publishing_title(title: str, platform: str) -> str:
    suffix = {"youtube_shorts": "", "instagram_reels": "", "tiktok": ""}.get(platform, "")
    return f"{title}{suffix}"[:200]


def _publishing_caption(idea: dict, channel: str) -> str:
    if channel == "infantil":
        base = f"{idea['premise']} Un cuento corto para ver en familia."
    else:
        base = f"{idea['premise']} Fuentes revisadas en el catalogo del proyecto."
    return base[:600]


def _hashtags(channel: str) -> list[str]:
    if channel == "infantil":
        return ["cuentosinfantiles", "cuentocorto", "paraninos", "aprenderjugando"]
    return ["curiosidades", "sabiasque", "datoscuriosos", "objetoscotidianos"]


__all__ = ["MockProvider", "MOCK_MODEL"]
