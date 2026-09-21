"""Alineacion por palabra a partir de los tiempos por caracter del proveedor.

Entrada: `characters`, `character_start_times_seconds` y
`character_end_times_seconds`, tal y como los devuelve el proveedor.

Politica de agrupacion (determinista y documentada):

1. Una PALABRA es una secuencia maxima de caracteres que no son espacio en
   blanco. La puntuacion pegada se conserva en el texto mostrado
   ("compartir," es una sola palabra mostrada).
2. Los espacios en blanco NO reciben duracion propia: son separadores.
3. Un grupo sin ningun caracter alfanumerico (un guion largo suelto, unas
   comillas sueltas) no es una palabra por si mismo: se une al texto mostrado
   de la palabra ANTERIOR y amplia su rango temporal. Si no hay anterior, se
   une a la SIGUIENTE.
4. Los indices de caracteres son puntos de codigo de Python sobre el
   `narration_text` original de la escena, `char_start` inclusivo y `char_end`
   exclusivo.

Los tiempos que salen de aqui son LOCALES a la escena. El llamador les suma el
inicio de la escena para hacerlos globales; las pausas de escenas anteriores ya
estan dentro de ese inicio.

Nunca se fabrican timestamps repartiendo la duracion uniformemente: si la
alineacion no es utilizable, se dice y se bloquea.
"""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass, field

from ..textutil import normalize_for_compare
from .schemas import AlignmentMethod, AlignmentStatus


@dataclass(frozen=True)
class CharAlignment:
    """Alineacion por caracter tal cual llega del proveedor."""

    characters: list[str]
    start_times_s: list[float]
    end_times_s: list[float]

    @property
    def joined(self) -> str:
        return "".join(self.characters)


@dataclass(frozen=True)
class WordSpan:
    text: str
    char_start: int
    char_end: int
    start_s: float
    end_s: float


@dataclass
class AlignmentResult:
    words: list[WordSpan] = field(default_factory=list)
    method: AlignmentMethod = AlignmentMethod.NONE
    status: AlignmentStatus = AlignmentStatus.MISSING
    issues: list[str] = field(default_factory=list)
    adjustments: list[str] = field(default_factory=list)
    provider_text: str | None = None

    @property
    def usable(self) -> bool:
        return self.status is AlignmentStatus.OK and bool(self.words)


# ---------------------------------------------------------------------------
# Correspondencia con el texto original
# ---------------------------------------------------------------------------


def text_matches(joined: str, source_text: str) -> tuple[bool, str]:
    """Decide si la alineacion corresponde al texto fuente.

    Se acepta:

    * igualdad exacta, o
    * igualdad tras normalizacion Unicode NFC cuando conserva la LONGITUD y
      las unicas diferencias son espacios en blanco equivalentes (por ejemplo
      un espacio duro frente a un espacio normal).

    Se rechaza cualquier otra cosa. En concreto, NO se asume correspondencia
    entre "12 km" y "doce kilometros": una normalizacion que expande numeros o
    abreviaturas no es reversible y exige otra alineacion o revision humana.
    """
    if joined == source_text:
        return True, "exacta"
    nfc_joined = unicodedata.normalize("NFC", joined)
    nfc_source = unicodedata.normalize("NFC", source_text)
    if nfc_joined == nfc_source:
        return True, "nfc"
    if len(nfc_joined) != len(nfc_source):
        return False, "longitud distinta tras normalizar"
    for izquierda, derecha in zip(nfc_joined, nfc_source, strict=True):
        if izquierda == derecha:
            continue
        if izquierda.isspace() and derecha.isspace():
            continue
        return False, "difiere en caracteres que no son espacios"
    return True, "espacios equivalentes"


# ---------------------------------------------------------------------------
# Validacion estructural
# ---------------------------------------------------------------------------


def validate_char_alignment(
    alignment: CharAlignment,
    *,
    clip_duration_s: float,
    tolerance_s: float,
) -> list[str]:
    """Comprueba tamanos, valores finitos, orden y limites. Devuelve problemas."""
    problemas: list[str] = []
    n = len(alignment.characters)
    if n == 0:
        return ["la alineacion viene vacia"]
    if len(alignment.start_times_s) != n or len(alignment.end_times_s) != n:
        return [
            "los arrays de la alineacion no miden lo mismo: "
            f"{n} caracteres, {len(alignment.start_times_s)} inicios, "
            f"{len(alignment.end_times_s)} finales"
        ]

    anterior = -math.inf
    for indice, (inicio, fin) in enumerate(
        zip(alignment.start_times_s, alignment.end_times_s, strict=True)
    ):
        if not (math.isfinite(inicio) and math.isfinite(fin)):
            problemas.append(f"tiempo no finito en el caracter {indice}")
            break
        if fin < inicio - tolerance_s:
            problemas.append(f"el caracter {indice} termina antes de empezar")
            break
        if inicio < anterior - tolerance_s:
            problemas.append(f"los tiempos retroceden en el caracter {indice}")
            break
        anterior = inicio

    if not problemas:
        primero = alignment.start_times_s[0]
        ultimo = alignment.end_times_s[-1]
        if primero < -tolerance_s:
            problemas.append(f"la alineacion empieza en {primero:.3f} s, antes del clip")
        if ultimo > clip_duration_s + tolerance_s:
            problemas.append(
                f"la alineacion termina en {ultimo:.3f} s y el clip dura "
                f"{clip_duration_s:.3f} s"
            )
    return problemas


# ---------------------------------------------------------------------------
# Agrupacion en palabras
# ---------------------------------------------------------------------------


def _groups(source_text: str) -> list[tuple[int, int]]:
    """Rangos [inicio, fin) de los grupos sin espacios del texto."""
    grupos: list[tuple[int, int]] = []
    inicio: int | None = None
    for indice, caracter in enumerate(source_text):
        if caracter.isspace():
            if inicio is not None:
                grupos.append((inicio, indice))
                inicio = None
        elif inicio is None:
            inicio = indice
    if inicio is not None:
        grupos.append((inicio, len(source_text)))
    return grupos


def _has_alnum(text: str) -> bool:
    return any(caracter.isalnum() for caracter in text)


def word_spans_of(source_text: str) -> list[tuple[int, int]]:
    """Rangos (char_start, char_end) de las palabras, sin usar tiempos.

    Aplica exactamente la misma politica que `group_words`, de modo que la
    validacion pueda saber cuantas palabras DEBERIA haber narradas en una
    escena sin depender de la alineacion.
    """
    palabras: list[list[int]] = []
    pendiente: list[int] | None = None

    for inicio, fin in _groups(source_text):
        if not _has_alnum(source_text[inicio:fin]):
            if palabras:
                palabras[-1][1] = fin
            elif pendiente is None:
                pendiente = [inicio, fin]
            else:
                pendiente[1] = fin
            continue
        char_start = pendiente[0] if pendiente else inicio
        palabras.append([char_start, fin])
        pendiente = None

    return [(inicio, fin) for inicio, fin in palabras]


def group_words(alignment: CharAlignment, source_text: str) -> list[WordSpan]:
    """Agrupa los caracteres en palabras segun la politica del modulo."""
    palabras: list[WordSpan] = []
    for char_start, char_end in word_spans_of(source_text):
        tiempos_inicio = alignment.start_times_s[char_start:char_end]
        tiempos_fin = alignment.end_times_s[char_start:char_end]
        if not tiempos_inicio or not tiempos_fin:
            continue
        palabras.append(
            WordSpan(
                text=source_text[char_start:char_end],
                char_start=char_start,
                char_end=char_end,
                start_s=min(tiempos_inicio),
                end_s=max(tiempos_fin),
            )
        )
    return palabras


def build_alignment(
    alignment: CharAlignment,
    source_text: str,
    *,
    clip_duration_s: float,
    tolerance_s: float,
    method: AlignmentMethod,
) -> AlignmentResult:
    """Valida y agrupa. Devuelve el resultado con su estado y sus motivos."""
    resultado = AlignmentResult(method=method, provider_text=alignment.joined)

    coincide, detalle = text_matches(alignment.joined, source_text)
    if not coincide:
        resultado.status = AlignmentStatus.REJECTED
        resultado.issues.append(
            f"la alineacion no corresponde al texto de la escena ({detalle})"
        )
        return resultado
    if detalle != "exacta":
        resultado.adjustments.append(f"correspondencia aceptada por normalizacion: {detalle}")

    problemas = validate_char_alignment(
        alignment, clip_duration_s=clip_duration_s, tolerance_s=tolerance_s
    )
    if problemas:
        resultado.status = AlignmentStatus.REJECTED
        resultado.issues.extend(problemas)
        return resultado

    palabras = group_words(alignment, source_text)
    if not palabras:
        resultado.status = AlignmentStatus.REJECTED
        resultado.issues.append("no se pudo formar ninguna palabra con la alineacion")
        return resultado

    ajustadas: list[WordSpan] = []
    for palabra in palabras:
        inicio = palabra.start_s
        fin = palabra.end_s
        if inicio < 0.0:
            if inicio < -tolerance_s:
                resultado.status = AlignmentStatus.REJECTED
                resultado.issues.append(f"la palabra {palabra.text!r} empieza antes del clip")
                return resultado
            resultado.adjustments.append(
                f"{palabra.text!r}: inicio {inicio:.4f} s ajustado a 0 dentro de tolerancia"
            )
            inicio = 0.0
        if fin > clip_duration_s:
            if fin > clip_duration_s + tolerance_s:
                resultado.status = AlignmentStatus.REJECTED
                resultado.issues.append(
                    f"la palabra {palabra.text!r} termina en {fin:.3f} s, fuera del clip "
                    f"({clip_duration_s:.3f} s)"
                )
                return resultado
            resultado.adjustments.append(
                f"{palabra.text!r}: final {fin:.4f} s ajustado al fin del clip dentro de tolerancia"
            )
            fin = clip_duration_s
        if fin < inicio:
            resultado.status = AlignmentStatus.REJECTED
            resultado.issues.append(f"la palabra {palabra.text!r} tiene duracion negativa")
            return resultado
        ajustadas.append(
            WordSpan(
                text=palabra.text,
                char_start=palabra.char_start,
                char_end=palabra.char_end,
                start_s=inicio,
                end_s=fin,
            )
        )

    resultado.words = ajustadas
    resultado.status = AlignmentStatus.OK
    return resultado


def emphasis_set(emphasis_words: list[str]) -> set[str]:
    """Conjunto normalizado de palabras a destacar.

    Normalizacion documentada: la misma del modulo 1
    (`textutil.normalize_for_compare`): NFKD, sin tildes, minusculas y sin
    puntuacion. Asi "Compartir" destaca "compartir," sin tocar el texto narrado.
    """
    return {
        normalize_for_compare(palabra)
        for palabra in emphasis_words
        if normalize_for_compare(palabra)
    }


def is_emphasis(word_text: str, normalized_emphasis: set[str]) -> bool:
    return normalize_for_compare(word_text) in normalized_emphasis
