"""Utilidades de texto deterministas: conteo de palabras, normalizacion,
huellas SHA-256 y similitud lexica.

Ninguna de estas funciones consulta al proveedor: son la autoridad local que
el modulo usa para recalcular duraciones y detectar duplicados.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Conteo de palabras
# ---------------------------------------------------------------------------
#
# Reglas de `count_words` (documentadas y probadas en tests/test_textutil.py):
#
# 1. El texto se normaliza a Unicode NFC antes de tokenizar, de modo que
#    "canción" escrito con tilde combinada y con tilde precompuesta cuentan
#    igual.
# 2. Una palabra es una secuencia de caracteres alfanumericos Unicode sin
#    guion bajo (`[^\W_]`), lo que cubre el espanol completo: a-z, A-Z,
#    digitos, vocales acentuadas, dieresis y la enie.
# 3. El apostrofo (' o ’) y el guion interior no parten la palabra:
#    "veintitrés", "anti-gravedad" y "d'artagnan" cuentan como UNA palabra.
#    Esto respeta los compuestos del espanol ("teorico-practico" = 1 palabra),
#    que es como los lee una voz sintetica.
# 4. Un numero con separadores interiores de miles o decimales cuenta como
#    UNA palabra: "2.500" y "3,14" son 1 palabra cada uno. Aun asi, para la
#    narracion se recomienda escribir las cifras con letras, porque la
#    duracion locutada de "2.500" ("dos mil quinientos") es mayor que la de
#    una palabra corriente.
# 5. Los signos de puntuacion, las comillas, los emojis y los espacios NO
#    cuentan como palabras.
# 6. El conteo nunca depende del modelo: es esta funcion la que fija
#    `word_count` en el documento exportado.

_WORD_RE = re.compile(r"\d+(?:[.,]\d+)+|[^\W_]+(?:['’\-][^\W_]+)*", re.UNICODE)

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+", re.UNICODE)


def nfc(text: str) -> str:
    """Normaliza a Unicode NFC (forma compuesta)."""
    return unicodedata.normalize("NFC", text)


def words(text: str) -> list[str]:
    """Devuelve la lista de palabras segun las reglas documentadas arriba."""
    return _WORD_RE.findall(nfc(text))


def count_words(text: str) -> int:
    """Cuenta palabras de un texto en espanol. Vease la doc del modulo."""
    return len(words(text))


# ---------------------------------------------------------------------------
# Normalizacion para comparacion y duplicados
# ---------------------------------------------------------------------------
#
# `normalize_for_compare` produce la forma canonica que se usa tanto para el
# hash de duplicado exacto como para la similitud lexica:
#
#   1. NFKD  (separa las tildes de sus vocales)
#   2. se eliminan los caracteres de marca combinante -> "canción" -> "cancion"
#   3. minusculas
#   4. la puntuacion se sustituye por un espacio
#   5. los espacios se colapsan y se recortan los extremos
#
# La enie pierde la tilde en el paso 2 ("nino" == "niño"): es intencionado,
# porque queremos detectar reescrituras superficiales del mismo titulo.


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_for_compare(text: str) -> str:
    """Forma canonica en minusculas, sin tildes ni puntuacion."""
    lowered = strip_accents(text).lower()
    no_punct = _PUNCT_RE.sub(" ", lowered)
    return _WS_RE.sub(" ", no_punct).strip()


# Lista corta de palabras vacias del espanol. Se descartan al construir los
# conjuntos de similitud para que "el agua de la Luna" y "agua en la Luna" no
# parezcan distintas solo por los articulos.
STOPWORDS_ES: frozenset[str] = frozenset(
    """
    a al algo alguna algunas alguno algunos ante antes aquel aquella aquello
    aqui asi aun aunque cada como con contra cual cuales cuando de del
    desde donde dos el ella ellas ello ellos en entre era eran es esa esas ese
    eso esos esta estan estas este esto estos fue fueron ha han hasta hay la
    las le les lo los mas me mi mis mucho muy nos nuestra nuestro o os otra
    otras otro otros para pero poco por porque que quien quienes se ser si sin
    sobre solo son su sus tan te tiene tienen todo todos tu tus un una uno unos
    y ya
    """.split()
)


def similarity_tokens(text: str) -> set[str]:
    """Conjunto de tokens comparables: normalizados, sin stopwords, len >= 3."""
    return {
        token
        for token in normalize_for_compare(text).split()
        if len(token) >= 3 and token not in STOPWORDS_ES
    }


def jaccard_similarity(a: str, b: str) -> float:
    """Indice de Jaccard entre los conjuntos de tokens de dos textos.

    Es una medida **lexica**: mide solape de vocabulario, no significado.
    No la llames busqueda semantica.
    """
    set_a = similarity_tokens(a)
    set_b = similarity_tokens(b)
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union else 0.0


def starts_with_normalized(haystack: str, needle: str) -> bool:
    """True si `haystack` empieza por `needle` tras normalizar ambos."""
    norm_hay = normalize_for_compare(haystack)
    norm_needle = normalize_for_compare(needle)
    if not norm_needle:
        return False
    return norm_hay.startswith(norm_needle)


# ---------------------------------------------------------------------------
# Huellas
# ---------------------------------------------------------------------------


def sha256_text(text: str) -> str:
    """SHA-256 en hexadecimal del texto codificado en UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_json(data: Any) -> str:
    """Serializacion canonica: claves ordenadas, sin espacios, UTF-8 literal.

    Es la base de todos los hashes del proyecto (config_hash, source_pack_hash,
    fingerprint de idempotencia), de modo que dos ejecuciones con los mismos
    datos produzcan exactamente la misma huella.
    """
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_json(data: Any) -> str:
    """SHA-256 de la serializacion canonica de `data`."""
    return sha256_text(canonical_json(data))


def content_hash(*parts: str) -> str:
    """Hash estable de varios textos (titulo + premisa, por ejemplo)."""
    joined = "\u001f".join(normalize_for_compare(part) for part in parts)
    return sha256_text(joined)


def truncate(text: str, limit: int) -> str:
    """Recorta a `limit` caracteres anadiendo elipsis si hizo falta."""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def join_nonempty(parts: Iterable[str], sep: str = " ") -> str:
    return sep.join(part for part in parts if part)
