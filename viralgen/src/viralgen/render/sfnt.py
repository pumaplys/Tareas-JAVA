"""Lector minimo de fuentes TrueType/OpenType: cobertura y anchuras.

Solo lee tres tablas —`head`, `hhea`/`hmtx` y `cmap`— para contestar dos
preguntas: que caracteres cubre la fuente y cuanto avanza cada uno. Se hace
aqui, con `struct`, en vez de anadir una dependencia de tipografia completa
para un uso tan acotado.

De `cmap` se admiten los formatos 4 (BMP, el habitual) y 12 (Unicode
completo), que son los que traen las fuentes modernas. Una subtabla en otro
formato se ignora en vez de adivinarse; si al final no se pudo leer ninguna,
se dice claramente, porque una cobertura inventada es peor que no tenerla.

Referencia del formato: especificacion OpenType de Microsoft, tablas `cmap`,
`head` y `hmtx`.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path


class SfntError(ValueError):
    """La fuente no se puede leer con este lector minimo."""


@dataclass(frozen=True)
class SfntMetrics:
    units_per_em: int
    #: Punto de codigo -> anchura de avance, en unidades de la fuente.
    advances: dict[int, int]
    #: Nombre de FAMILIA declarado por la fuente (nameID 1). Es el que hay que
    #: escribir en el ASS: un nombre inventado haria que fontconfig sustituyera
    #: la fuente, que es justo lo que el modulo quiere evitar.
    family: str = ""

    @property
    def codepoints(self) -> set[int]:
        return set(self.advances)


def read_metrics(path: Path) -> SfntMetrics:
    """Lee cobertura y anchuras de una fuente TrueType/OpenType."""
    datos = path.read_bytes()
    tablas = _table_directory(datos)

    if "head" not in tablas:
        raise SfntError("la fuente no tiene tabla 'head'")
    head_off, _ = tablas["head"]
    # unitsPerEm esta en el offset 18 de 'head'.
    (unidades,) = struct.unpack_from(">H", datos, head_off + 18)
    if unidades == 0:
        raise SfntError("unitsPerEm es cero")

    glifo_a_avance = _hmtx(datos, tablas)
    cmap = _cmap(datos, tablas)
    if not cmap:
        raise SfntError("no se pudo leer ninguna subtabla 'cmap' conocida")

    avances: dict[int, int] = {}
    ultimo = glifo_a_avance[-1] if glifo_a_avance else unidades // 2
    for codigo, glifo in cmap.items():
        if glifo == 0:
            # 0 es .notdef: la fuente NO cubre ese caracter.
            continue
        avances[codigo] = (
            glifo_a_avance[glifo] if glifo < len(glifo_a_avance) else ultimo
        )
    return SfntMetrics(
        units_per_em=unidades, advances=avances, family=_family_name(datos, tablas)
    )


def _table_directory(datos: bytes) -> dict[str, tuple[int, int]]:
    if len(datos) < 12:
        raise SfntError("archivo demasiado corto para ser una fuente")
    etiqueta = datos[:4]
    if etiqueta == b"ttcf":
        # Coleccion: se toma la primera fuente.
        (offset_primera,) = struct.unpack_from(">I", datos, 12)
        return _table_directory_at(datos, offset_primera)
    return _table_directory_at(datos, 0)


def _table_directory_at(datos: bytes, base: int) -> dict[str, tuple[int, int]]:
    version = datos[base:base + 4]
    if version not in (b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1"):
        raise SfntError(f"version de fuente no reconocida: {version!r}")
    (num_tablas,) = struct.unpack_from(">H", datos, base + 4)
    tablas: dict[str, tuple[int, int]] = {}
    for indice in range(num_tablas):
        pos = base + 12 + indice * 16
        if pos + 16 > len(datos):
            raise SfntError("directorio de tablas truncado")
        etiqueta, _suma, offset, longitud = struct.unpack_from(">4sIII", datos, pos)
        tablas[etiqueta.decode("latin-1").strip()] = (offset, longitud)
    return tablas


def _hmtx(datos: bytes, tablas: dict[str, tuple[int, int]]) -> list[int]:
    """Anchuras de avance por indice de glifo."""
    if "hhea" not in tablas or "hmtx" not in tablas:
        return []
    hhea_off, _ = tablas["hhea"]
    # numberOfHMetrics es el ultimo uint16 de 'hhea' (offset 34).
    (num_metricas,) = struct.unpack_from(">H", datos, hhea_off + 34)
    hmtx_off, hmtx_len = tablas["hmtx"]
    avances: list[int] = []
    for indice in range(num_metricas):
        pos = hmtx_off + indice * 4
        if pos + 2 > hmtx_off + hmtx_len or pos + 2 > len(datos):
            break
        (avance,) = struct.unpack_from(">H", datos, pos)
        avances.append(avance)
    return avances


def _cmap(datos: bytes, tablas: dict[str, tuple[int, int]]) -> dict[int, int]:
    """Mapa punto de codigo -> indice de glifo, de la mejor subtabla."""
    if "cmap" not in tablas:
        return {}
    base, _ = tablas["cmap"]
    (_version, num_tablas) = struct.unpack_from(">HH", datos, base)

    #: Se prefiere Unicode completo (3,10 / 0,4) sobre BMP (3,1 / 0,3).
    preferencia = {
        (3, 10): 0, (0, 6): 1, (0, 4): 2,   # Unicode full
        (3, 1): 3, (0, 3): 4, (0, 2): 5, (0, 1): 6, (0, 0): 7,  # BMP
    }
    candidatas: list[tuple[int, int]] = []
    for indice in range(num_tablas):
        pos = base + 4 + indice * 8
        plataforma, codificacion, offset = struct.unpack_from(">HHI", datos, pos)
        puntuacion = preferencia.get((plataforma, codificacion))
        if puntuacion is not None:
            candidatas.append((puntuacion, base + offset))

    for _puntuacion, offset in sorted(candidatas):
        try:
            mapa = _cmap_subtable(datos, offset)
        except (struct.error, IndexError, ValueError):
            continue
        if mapa:
            return mapa
    return {}


def _cmap_subtable(datos: bytes, offset: int) -> dict[int, int]:
    (formato,) = struct.unpack_from(">H", datos, offset)
    if formato == 4:
        return _cmap_format4(datos, offset)
    if formato == 12:
        return _cmap_format12(datos, offset)
    # Otro formato: se ignora en vez de adivinar su contenido.
    return {}


def _cmap_format4(datos: bytes, offset: int) -> dict[int, int]:
    (_fmt, longitud, _lang, seg_x2) = struct.unpack_from(">HHHH", datos, offset)
    segmentos = seg_x2 // 2
    fin_off = offset + 14
    inicio_off = fin_off + seg_x2 + 2          # +2 por reservedPad
    delta_off = inicio_off + seg_x2
    rango_off = delta_off + seg_x2

    mapa: dict[int, int] = {}
    for indice in range(segmentos):
        (fin,) = struct.unpack_from(">H", datos, fin_off + indice * 2)
        (inicio,) = struct.unpack_from(">H", datos, inicio_off + indice * 2)
        (delta,) = struct.unpack_from(">h", datos, delta_off + indice * 2)
        (rango,) = struct.unpack_from(">H", datos, rango_off + indice * 2)
        if inicio > fin:
            continue
        for codigo in range(inicio, min(fin, 0xFFFF) + 1):
            if codigo == 0xFFFF:
                continue
            if rango == 0:
                glifo = (codigo + delta) & 0xFFFF
            else:
                pos = rango_off + indice * 2 + rango + (codigo - inicio) * 2
                if pos + 2 > offset + longitud or pos + 2 > len(datos):
                    continue
                (glifo,) = struct.unpack_from(">H", datos, pos)
                if glifo != 0:
                    glifo = (glifo + delta) & 0xFFFF
            if glifo:
                mapa[codigo] = glifo
    return mapa


def _cmap_format12(datos: bytes, offset: int) -> dict[int, int]:
    (_fmt, _res, _longitud, _lang, num_grupos) = struct.unpack_from(
        ">HHIII", datos, offset
    )
    mapa: dict[int, int] = {}
    #: Tope defensivo: una fuente CJK completa tiene decenas de miles de
    #: glifos y no hace falta materializarlos todos para el espanol.
    maximo = 200_000
    for indice in range(num_grupos):
        pos = offset + 16 + indice * 12
        inicio, fin, glifo_inicial = struct.unpack_from(">III", datos, pos)
        if fin < inicio or fin - inicio > maximo:
            continue
        for desplazamiento in range(fin - inicio + 1):
            mapa[inicio + desplazamiento] = glifo_inicial + desplazamiento
            if len(mapa) > maximo:
                return mapa
    return mapa


def _family_name(datos: bytes, tablas: dict[str, tuple[int, int]]) -> str:
    """Nombre de familia (nameID 1) de la tabla `name`.

    Se prefiere la version Windows/Unicode (UTF-16BE) y se acepta la Macintosh
    como alternativa. Sin tabla `name` se devuelve cadena vacia: quien llame
    decide, en vez de recibir un nombre inventado.
    """
    if "name" not in tablas:
        return ""
    base, _ = tablas["name"]
    try:
        (_formato, cuenta, offset_cadenas) = struct.unpack_from(">HHH", datos, base)
    except struct.error:
        return ""
    mejor = ""
    for indice in range(cuenta):
        pos = base + 6 + indice * 12
        try:
            plataforma, codificacion, _idioma, name_id, longitud, offset = (
                struct.unpack_from(">HHHHHH", datos, pos)
            )
        except struct.error:
            break
        if name_id != 1:
            continue
        inicio = base + offset_cadenas + offset
        crudo = datos[inicio:inicio + longitud]
        if not crudo:
            continue
        if plataforma == 3 or (plataforma == 0):
            try:
                texto = crudo.decode("utf-16-be")
            except UnicodeDecodeError:
                continue
            return texto.strip()
        if plataforma == 1 and codificacion == 0 and not mejor:
            mejor = crudo.decode("mac-roman", errors="replace").strip()
    return mejor
