"""Fuente tipografica: eleccion explicita, hash y cobertura de glifos.

Una sustitucion silenciosa de fuente arruina un render sin avisar: libass
elige otra familia, el texto cambia de anchura y los grupos dejan de caber, o
peor, aparecen cuadros vacios donde habia una "n" o un signo de apertura.

Por eso el modulo:

* fija el ARCHIVO de la fuente por configuracion, no su nombre de familia;
* registra su SHA-256 en el manifiesto;
* comprueba que la fuente cubre los caracteres REALMENTE usados por el guion
  antes de renderizar, y bloquea si falta alguno.

La medida de anchura que ofrece este modulo es una AYUDA DE COMPOSICION. libass
aplica su propio kerning y shaping, asi que el encuadre final se valida sobre
el fotograma renderizado, no sobre esta medida.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from pathlib import Path

from ..diskutil import sha256_file
from ..errors import ConfigError
from .sfnt import SfntError, read_metrics

#: Primera opcion: viene en `fonts-dejavu-core`, tiene licencia redistribuible
#: (Bitstream Vera / Arev, permisiva) y cubre el espanol completo.
DEFAULT_FONT_CANDIDATES: tuple[str, ...] = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
)

#: Caracteres que el espanol necesita si o si. Se comprueban siempre, ademas
#: de los que aparezcan en el guion concreto.
SPANISH_PROBE = "áéíóúüñÁÉÍÓÚÜÑ¿¡«»—…"


@dataclass(frozen=True)
class FontAsset:
    """Fuente elegida, con su identidad y sus metricas."""

    path: Path
    sha256: str
    family: str
    units_per_em: int
    #: Anchura de avance por punto de codigo, en unidades de la fuente.
    advances: dict[str, int]
    default_advance: int

    def describe(self) -> dict:
        return {
            "path": str(self.path),
            "sha256": self.sha256,
            "family": self.family,
            "units_per_em": self.units_per_em,
        }

    def covers(self, text: str) -> set[str]:
        """Devuelve los caracteres del texto que la fuente NO cubre."""
        faltan: set[str] = set()
        for caracter in text:
            if caracter in ("\n", "\r", "\t", " "):
                continue
            if unicodedata.category(caracter) in ("Cc", "Cf"):
                continue
            if caracter not in self.advances:
                faltan.add(caracter)
        return faltan

    def text_width(self, text: str, *, font_size_px: float) -> float:
        """Anchura aproximada en pixeles. AYUDA de composicion, no garantia."""
        total = sum(
            self.advances.get(caracter, self.default_advance) for caracter in text
        )
        return total * font_size_px / self.units_per_em


def load_font(path: Path | str | None) -> FontAsset:
    """Carga la fuente elegida y lee su identidad y sus metricas.

    Sin `path` se prueban los candidatos por defecto. Si ninguno esta, se falla
    con diagnostico: NO se deja que libass sustituya por su cuenta.
    """
    candidatos: list[Path]
    if path is not None:
        candidatos = [Path(path)]
    else:
        candidatos = [Path(item) for item in DEFAULT_FONT_CANDIDATES]

    elegida: Path | None = None
    for candidato in candidatos:
        if candidato.is_file():
            elegida = candidato
            break
    if elegida is None:
        raise ConfigError(
            "No se encuentra una fuente tipografica utilizable. Instala "
            "`fonts-dejavu-core` o indica una con VIRALGEN_RENDER_FONT_PATH. "
            "El modulo no deja que libass sustituya la fuente en silencio.",
            details={"tried": [str(item) for item in candidatos]},
        )

    try:
        metricas = read_metrics(elegida)
    except (SfntError, OSError) as exc:
        raise ConfigError(
            f"La fuente {elegida} no se puede leer: {exc}",
            details={"path": str(elegida)},
        ) from exc

    return FontAsset(
        path=elegida,
        sha256=sha256_file(elegida),
        family=metricas.family or elegida.stem,
        units_per_em=metricas.units_per_em,
        advances={chr(codigo): avance for codigo, avance in metricas.advances.items()},
        default_advance=int(metricas.units_per_em * 0.6),
    )


def require_glyphs(font: FontAsset, texts: list[str]) -> None:
    """Bloquea si la fuente no cubre algun caracter que se va a dibujar.

    Un glifo ausente produce un cuadro vacio, y un cuadro vacio en un render
    declarado listo es un defecto silencioso. Se prefiere fallar aqui.
    """
    faltan: set[str] = set()
    for texto in texts:
        faltan |= font.covers(texto)
    faltan |= font.covers(SPANISH_PROBE)
    if faltan:
        muestra = "".join(sorted(faltan))[:60]
        raise ConfigError(
            f"La fuente {font.path.name} no cubre estos caracteres del guion: "
            f"{muestra!r}. Un glifo ausente se dibujaria como un cuadro vacio.",
            details={
                "font": str(font.path),
                "missing": sorted(faltan)[:60],
                "missing_count": len(faltan),
            },
        )
