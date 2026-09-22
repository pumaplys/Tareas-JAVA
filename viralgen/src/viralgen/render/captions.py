"""Subtitulos ASS generados desde la alineacion de `voice.json`.

La alineacion YA EXISTE: este modulo no transcribe, no inventa timestamps y no
vuelve a pedir voz. Agrupa las palabras que el modulo 2 midio, decide donde
cortar lineas y escribe un `captions.ass` que libass renderiza.

Tres relojes distintos, que no se mezclan nunca:

=====================  ==========================================
Unidad                 Donde se usa
=====================  ==========================================
segundos globales      la alineacion original de `voice.json`
centesimas de segundo  los tiempos de un evento ASS (`0:00:01.23`)
milisegundos           las transformaciones `\\t(...)` DENTRO del evento,
                       relativas al INICIO de ese evento
=====================  ==========================================

Politica de redondeo, UNICA para todo el modulo: se cuantiza cada FRONTERA en
segundos a centesimas con redondeo al mas cercano (`floor(t*100 + 0.5)`), nunca
la duracion. Como dos eventos contiguos comparten el valor de origen de su
frontera, cuantizarla una sola vez garantiza que no haya solapes, huecos ni
deriva acumulada. Las cifras originales en segundos se conservan en el plan.

Seguridad del texto: `narration_text` es DATO. Las etiquetas de estilo salen
solo de plantillas controladas de este modulo; el texto del guion se serializa
literalmente y nunca puede inyectar una etiqueta ASS.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..errors import ConfigError
from .fonts import FontAsset

#: Lienzo declarado en el ASS. Coincide con el objetivo del MVP.
PLAY_RES_X = 1080
PLAY_RES_Y = 1920

#: Region de composicion del texto. Es una DECISION DE DISENO, no una garantia
#: de zona segura: cada plataforma recorta y superpone su propia interfaz.
TEXT_LEFT = 96
TEXT_RIGHT = 984
TEXT_TOP = 1080
TEXT_BOTTOM = 1530

#: La marca de preview vive fuera de esa region, arriba.
PREVIEW_MARGIN_V = 60

MIN_WORDS_PER_GROUP = 2
MAX_WORDS_PER_GROUP = 5
MAX_LINES = 2

#: Separacion a partir de la cual se corta grupo: una pausa larga no debe
#: quedar cubierta por el mismo grupo.
GROUP_SPLIT_PAUSE_S = 0.45

#: Cuanto puede quedarse visible un grupo despues de su ultima palabra.
MAX_TAIL_HOLD_S = 0.60

#: Encogido MAXIMO de un grupo que no cabe de otro modo. Es acotado a
#: proposito: "no reduzcas la letra indefinidamente" no es "no la reduzcas
#: nunca". Una palabra larga y legitima del espanol —"extraordinariamente"
#: mide 889 px a tamano 76 y la region tiene 888— no debe bloquear un render
#: entero, pero tampoco vale encoger hasta que quepa cualquier cosa.
MIN_FONT_SCALE = 0.80
FONT_SCALE_STEP = 0.05


class CaptionError(ConfigError):
    code = "caption_error"


# ---------------------------------------------------------------------------
# Estilos
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CaptionStyleSpec:
    """Valores INICIALES de un estilo, para ajustar mirando el render."""

    name: str
    font_size: int
    primary: str          # color del texto en reposo, &HAABBGGRR
    highlight: str        # color de la palabra activa
    outline_colour: str
    back_colour: str
    outline: float
    shadow: float
    #: Escala de entrada del grupo, en porcentaje. 100 = sin animacion.
    intro_scale: int
    #: Duracion de la entrada, en MILISEGUNDOS relativos al evento.
    intro_ms: int
    highlight_words: bool
    #: 1 = contorno y sombra; 3 = caja opaca detras del texto. La caja se usa
    #: en la marca de preview para que siga legible sobre cualquier imagen.
    border_style: int = 1


#: `dynamic_emphasis` (curiosidades) y `calm_readable` (infantil) son los dos
#: valores de `CaptionStyle` que el modulo 1 escribe en el perfil.
CAPTION_STYLES: dict[str, CaptionStyleSpec] = {
    "dynamic_emphasis": CaptionStyleSpec(
        name="Curiosidades",
        font_size=76,
        primary="&H00FFFFFF",      # blanco opaco
        highlight="&H0030D7FF",    # ambar; BGR invertido respecto de RGB
        outline_colour="&H00101418",
        back_colour="&H80000000",
        outline=4.0,
        shadow=1.0,
        intro_scale=106,
        intro_ms=120,
        highlight_words=True,
    ),
    "calm_readable": CaptionStyleSpec(
        name="Infantil",
        font_size=72,
        primary="&H00FFFFFF",
        highlight="&H00C8F0FF",    # realce muy suave: sin sacudidas
        outline_colour="&H00201008",
        back_colour="&H80000000",
        outline=5.0,
        shadow=0.0,
        intro_scale=100,           # sin escala: nada de saltos
        intro_ms=0,
        highlight_words=False,
    ),
}

PREVIEW_STYLE = CaptionStyleSpec(
    name="Preview",
    font_size=52,
    primary="&H00FFFFFF",
    highlight="&H00FFFFFF",
    outline_colour="&H00000000",
    back_colour="&H30000000",
    outline=2.0,
    shadow=0.0,
    intro_scale=100,
    intro_ms=0,
    highlight_words=False,
    border_style=3,
)


# ---------------------------------------------------------------------------
# Cuantizacion y escapado
# ---------------------------------------------------------------------------


def to_centiseconds(seconds: float) -> int:
    """Politica UNICA de redondeo: frontera en segundos -> centesimas."""
    if seconds < 0:
        return 0
    return int(seconds * 100 + 0.5)


def format_ass_time(centiseconds: int) -> str:
    """`H:MM:SS.cc`, el formato de tiempo de un evento ASS."""
    if centiseconds < 0:
        centiseconds = 0
    total_segundos, centesimas = divmod(centiseconds, 100)
    minutos, segundos = divmod(total_segundos, 60)
    horas, minutos = divmod(minutos, 60)
    return f"{horas:d}:{minutos:02d}:{segundos:02d}.{centesimas:02d}"


_WHITESPACE = re.compile(r"\s+")


def escape_ass_text(text: str) -> str:
    """Serializa texto del guion como TEXTO LITERAL de un evento ASS.

    `{` y `}` abren y cierran un bloque de etiquetas, asi que se escapan. Una
    barra invertida se RECHAZA en vez de escaparse: en el cuerpo de un evento,
    `\\N`, `\\n` y `\\h` son directivas de libass, y no hay forma de escribir
    una barra invertida literal que sea inequivoca en todas las versiones.
    Antes que mutilar el texto en silencio, se bloquea con motivo.
    """
    if "\\" in text:
        raise CaptionError(
            "El texto contiene una barra invertida, que en un evento ASS puede "
            "interpretarse como directiva (\\N, \\n, \\h). Se bloquea en vez de "
            "alterar el texto del guion.",
            details={"text": text[:120]},
        )
    normalizado = _WHITESPACE.sub(" ", text).strip()
    return normalizado.replace("{", "\\{").replace("}", "\\}")


# ---------------------------------------------------------------------------
# Agrupacion
# ---------------------------------------------------------------------------


@dataclass
class CaptionWord:
    """Palabra alineada, con sus tiempos GLOBALES en segundos."""

    scene_id: str
    word_index: int
    text: str
    start_s: float
    end_s: float
    emphasis: bool = False


@dataclass
class CaptionGroup:
    """Grupo visible: 2-5 palabras, como mucho dos lineas."""

    scene_id: str
    index: int
    words: list[CaptionWord]
    lines: list[list[int]]           # indices dentro de `words`, por linea
    start_s: float
    end_s: float
    #: Palabras cuyo resaltado colapso al cuantizar. Su texto SIGUE visible.
    collapsed: list[int] = field(default_factory=list)
    #: Escala aplicada a la letra de ESTE grupo, en [MIN_FONT_SCALE, 1.0].
    font_scale: float = 1.0

    @property
    def text(self) -> str:
        return " ".join(palabra.text for palabra in self.words)


def group_words(
    words: list[CaptionWord],
    *,
    font: FontAsset,
    style: CaptionStyleSpec,
    max_width_px: int = TEXT_RIGHT - TEXT_LEFT,
) -> list[CaptionGroup]:
    """Agrupa por escena, por pausa, por numero de palabras y por anchura.

    Nunca se elimina una palabra ni se reduce la letra: si un grupo no cabe en
    dos lineas, se parte en grupos mas pequenos.
    """
    grupos: list[CaptionGroup] = []
    por_escena: dict[str, list[CaptionWord]] = {}
    for palabra in words:
        por_escena.setdefault(palabra.scene_id, []).append(palabra)

    for scene_id in sorted(
        por_escena, key=lambda clave: por_escena[clave][0].start_s
    ):
        de_la_escena = sorted(por_escena[scene_id], key=lambda w: w.word_index)
        for tanda in _split_on_pauses(de_la_escena):
            grupos.extend(
                _pack(tanda, font=font, style=style, max_width_px=max_width_px)
            )

    for indice, grupo in enumerate(grupos):
        grupo.index = indice
    return grupos


def _split_on_pauses(words: list[CaptionWord]) -> list[list[CaptionWord]]:
    """Corta donde el modulo 2 midio una pausa apreciable."""
    tandas: list[list[CaptionWord]] = []
    actual: list[CaptionWord] = []
    for palabra in words:
        if actual and palabra.start_s - actual[-1].end_s >= GROUP_SPLIT_PAUSE_S:
            tandas.append(actual)
            actual = []
        actual.append(palabra)
    if actual:
        tandas.append(actual)
    return tandas


def _pack(
    words: list[CaptionWord],
    *,
    font: FontAsset,
    style: CaptionStyleSpec,
    max_width_px: int,
) -> list[CaptionGroup]:
    """Empaqueta una tanda en grupos que caben en dos lineas."""
    grupos: list[CaptionGroup] = []
    pendientes = list(words)
    while pendientes:
        tamano = min(MAX_WORDS_PER_GROUP, len(pendientes))
        escala = 1.0
        while tamano >= 1:
            candidato = pendientes[:tamano]
            lineas = _wrap(candidato, font=font, style=style, max_width_px=max_width_px)
            if lineas is not None:
                break
            tamano -= 1
        if tamano < 1:
            # Ni una sola palabra cabe al tamano del estilo. Antes de rendirse
            # se prueba un encogido ACOTADO, solo para este grupo.
            tamano, lineas, escala = _shrink_to_fit(
                pendientes, font=font, style=style, max_width_px=max_width_px
            )
        candidato = pendientes[:tamano]
        # Evita dejar una sola palabra suelta al final de la tanda cuando se
        # puede repartir: dos grupos de dos leen mejor que uno de tres y uno
        # de uno.
        resto = len(pendientes) - tamano
        if resto == 1 and tamano > MIN_WORDS_PER_GROUP and escala == 1.0:
            tamano -= 1
            candidato = pendientes[:tamano]
            lineas = _wrap(candidato, font=font, style=style, max_width_px=max_width_px)
            if lineas is None:  # pragma: no cover - menos palabras siempre cabe
                tamano += 1
                candidato = pendientes[:tamano]
                lineas = _wrap(
                    candidato, font=font, style=style, max_width_px=max_width_px
                )
        assert lineas is not None
        grupos.append(
            CaptionGroup(
                scene_id=candidato[0].scene_id,
                index=0,
                words=list(candidato),
                lines=lineas,
                start_s=candidato[0].start_s,
                end_s=candidato[-1].end_s,
                font_scale=escala,
            )
        )
        pendientes = pendientes[tamano:]
    return grupos


def _shrink_to_fit(
    pendientes: list[CaptionWord],
    *,
    font: FontAsset,
    style: CaptionStyleSpec,
    max_width_px: int,
) -> tuple[int, list[list[int]], float]:
    """Ultimo recurso: encoge la letra de este grupo, hasta MIN_FONT_SCALE.

    Devuelve `(numero_de_palabras, lineas, escala)`. Si ni al minimo cabe, se
    bloquea: el texto no se recorta y no se elimina ninguna palabra.
    """
    escala = 1.0 - FONT_SCALE_STEP
    while escala >= MIN_FONT_SCALE - 1e-9:
        for tamano in range(min(MAX_WORDS_PER_GROUP, len(pendientes)), 0, -1):
            lineas = _wrap(
                pendientes[:tamano],
                font=font,
                style=style,
                max_width_px=max_width_px,
                scale=escala,
            )
            if lineas is not None:
                return tamano, lineas, round(escala, 4)
        escala -= FONT_SCALE_STEP
    raise CaptionError(
        f"la palabra {pendientes[0].text!r} no cabe en la region de texto "
        f"({max_width_px} px) ni al {MIN_FONT_SCALE:.0%} del tamano "
        f"{style.font_size}. No se recorta el texto ni se elimina la palabra.",
        details={
            "word": pendientes[0].text,
            "scene_id": pendientes[0].scene_id,
            "min_font_scale": MIN_FONT_SCALE,
        },
    )


def _wrap(
    words: list[CaptionWord],
    *,
    font: FontAsset,
    style: CaptionStyleSpec,
    max_width_px: int,
    scale: float = 1.0,
) -> list[list[int]] | None:
    """Reparte las palabras en <= 2 lineas que quepan, o None si no caben.

    La anchura sale de las metricas de la fuente: es una AYUDA. El encuadre
    real se comprueba sobre el fotograma renderizado.
    """
    def ancho(indices: list[int]) -> float:
        texto = " ".join(words[i].text for i in indices)
        # La escala de entrada agranda momentaneamente el grupo: se reserva
        # ese margen para que la animacion no se salga de la region.
        factor = max(style.intro_scale, 100) / 100
        return font.text_width(texto, font_size_px=style.font_size * scale) * factor

    todos = list(range(len(words)))
    if ancho(todos) <= max_width_px:
        return [todos]
    if len(words) < 2:
        return None
    mejor: list[list[int]] | None = None
    mejor_desequilibrio = float("inf")
    for corte in range(1, len(words)):
        primera, segunda = todos[:corte], todos[corte:]
        a, b = ancho(primera), ancho(segunda)
        if a <= max_width_px and b <= max_width_px:
            desequilibrio = abs(a - b)
            if desequilibrio < mejor_desequilibrio:
                mejor_desequilibrio = desequilibrio
                mejor = [primera, segunda]
    if mejor is None or len(mejor) > MAX_LINES:
        return None
    return mejor


# ---------------------------------------------------------------------------
# Eventos
# ---------------------------------------------------------------------------


@dataclass
class CaptionEvent:
    """Un Dialogue del ASS, ya cuantizado."""

    start_cs: int
    end_cs: int
    style: str
    text: str
    group_index: int
    active_word: int | None
    #: Cifras ORIGINALES en segundos, antes de cuantizar. Van al plan.
    source_start_s: float
    source_end_s: float

    @property
    def duration_cs(self) -> int:
        return self.end_cs - self.start_cs


def build_events(
    groups: list[CaptionGroup],
    *,
    style: CaptionStyleSpec,
    total_duration_s: float,
) -> list[CaptionEvent]:
    """Convierte los grupos en eventos ASS sin solapes ni duraciones negativas.

    Cada grupo produce un evento por palabra activa mas, si hay hueco, eventos
    sin palabra activa: el grupo sigue visible y el resaltado no se queda
    pegado durante una pausa.
    """
    eventos: list[CaptionEvent] = []
    for indice, grupo in enumerate(groups):
        siguiente = groups[indice + 1].start_s if indice + 1 < len(groups) else None
        fin_visible = _visible_end(grupo, siguiente, total_duration_s)
        eventos.extend(_group_events(grupo, style=style, visible_end_s=fin_visible))
    _assert_no_overlap(eventos)
    return eventos


def _visible_end(
    grupo: CaptionGroup, next_start_s: float | None, total_duration_s: float
) -> float:
    """Hasta cuando se queda el grupo en pantalla.

    Se mantiene un poco despues de su ultima palabra, pero nunca invade el
    tiempo del grupo siguiente ni se prolonga durante una pausa larga.
    """
    limite = grupo.end_s + MAX_TAIL_HOLD_S
    if next_start_s is not None:
        limite = min(limite, next_start_s)
    return max(grupo.end_s, min(limite, total_duration_s))


def _group_events(
    grupo: CaptionGroup, *, style: CaptionStyleSpec, visible_end_s: float
) -> list[CaptionEvent]:
    eventos: list[CaptionEvent] = []
    primero = True

    def anadir(inicio_s: float, fin_s: float, activo: int | None) -> None:
        nonlocal primero
        inicio_cs = to_centiseconds(inicio_s)
        fin_cs = to_centiseconds(fin_s)
        if fin_cs <= inicio_cs:
            # Colapso al cuantizar: no se fabrica duracion. El texto de la
            # palabra sigue visible porque el grupo entero se dibuja en los
            # eventos vecinos; solo se pierde SU resaltado.
            if activo is not None and activo not in grupo.collapsed:
                grupo.collapsed.append(activo)
            return
        eventos.append(
            CaptionEvent(
                start_cs=inicio_cs,
                end_cs=fin_cs,
                style=style.name,
                text=render_group_text(grupo, style=style, active=activo, intro=primero),
                group_index=grupo.index,
                active_word=activo,
                source_start_s=inicio_s,
                source_end_s=fin_s,
            )
        )
        primero = False

    if not style.highlight_words:
        # Grupos estables: un solo evento, sin resaltado por palabra.
        anadir(grupo.start_s, visible_end_s, None)
        return eventos

    for posicion, palabra in enumerate(grupo.words):
        anterior_fin = grupo.words[posicion - 1].end_s if posicion else None
        if anterior_fin is not None and palabra.start_s > anterior_fin:
            anadir(anterior_fin, palabra.start_s, None)
        anadir(palabra.start_s, palabra.end_s, posicion)
    if visible_end_s > grupo.words[-1].end_s:
        anadir(grupo.words[-1].end_s, visible_end_s, None)
    if not eventos:
        # Todo el grupo colapso: se muestra al menos una vez, sin resaltado.
        anadir(grupo.start_s, max(visible_end_s, grupo.start_s + 0.01), None)
    return eventos


def render_group_text(
    grupo: CaptionGroup,
    *,
    style: CaptionStyleSpec,
    active: int | None,
    intro: bool,
) -> str:
    """Compone el cuerpo del evento: plantillas propias + texto literal.

    La caja y los saltos de linea no cambian entre eventos del mismo grupo:
    solo cambia el COLOR de la palabra activa. Cambiar su tamano moveria el
    resto de la frase y produciria un salto lateral en cada palabra.
    """
    partes: list[str] = []
    if grupo.font_scale != 1.0:
        # Tamano propio de ESTE grupo, en pixeles. No cambia entre sus eventos,
        # asi que la caja sigue quieta mientras cambia la palabra activa.
        partes.append("{" f"\\fs{round(style.font_size * grupo.font_scale)}" "}")
    if intro and style.intro_scale != 100 and style.intro_ms > 0:
        # `\t` usa MILISEGUNDOS relativos al inicio de ESTE evento. Solo se
        # pone en el primer evento del grupo: repetirlo reiniciaria la
        # animacion en cada palabra.
        partes.append(
            "{"
            f"\\fscx{style.intro_scale}\\fscy{style.intro_scale}"
            f"\\t(0,{style.intro_ms},\\fscx100\\fscy100)"
            "}"
        )
    for numero_linea, linea in enumerate(grupo.lines):
        if numero_linea:
            partes.append("\\N")
        for posicion_en_linea, indice in enumerate(linea):
            if posicion_en_linea:
                partes.append(" ")
            color = style.highlight if indice == active else style.primary
            partes.append("{" f"\\c{color}" "}")
            partes.append(escape_ass_text(grupo.words[indice].text))
    return "".join(partes)


def _assert_no_overlap(eventos: list[CaptionEvent]) -> None:
    anterior: CaptionEvent | None = None
    for evento in eventos:
        if evento.duration_cs <= 0:
            raise CaptionError(
                "se genero un evento de duracion no positiva",
                details={"start_cs": evento.start_cs, "end_cs": evento.end_cs},
            )
        if anterior is not None and evento.start_cs < anterior.end_cs:
            raise CaptionError(
                "dos eventos de subtitulo se solapan",
                details={
                    "previous_end_cs": anterior.end_cs,
                    "start_cs": evento.start_cs,
                },
            )
        anterior = evento


# ---------------------------------------------------------------------------
# Archivo ASS
# ---------------------------------------------------------------------------


#: Alineaciones ASS usadas: 2 = abajo-centro, 8 = arriba-centro.
ALIGN_BOTTOM_CENTER = 2
ALIGN_TOP_CENTER = 8


def _style_line(
    spec: CaptionStyleSpec,
    *,
    font_family: str,
    margin_v: int,
    alignment: int = ALIGN_BOTTOM_CENTER,
) -> str:
    """Compone la linea `Style:`.

    La alineacion es un PARAMETRO explicito. Antes se parcheaba la cadena ya
    formateada buscando ",2,", y eso puede coincidir con el grosor del
    contorno en vez de con la alineacion.
    """
    return (
        f"Style: {spec.name},{font_family},{spec.font_size},"
        f"{spec.primary},{spec.primary},{spec.outline_colour},{spec.back_colour},"
        # Negrita si, cursiva/subrayado/tachado no.
        "-1,0,0,0,"
        "100,100,0,0,"
        f"{spec.border_style},{spec.outline:g},{spec.shadow:g},"
        # Anclada por MarginV: con alineacion 2 crece hacia arriba desde
        # TEXT_BOTTOM; con 8, hacia abajo desde el borde superior.
        f"{alignment},{TEXT_LEFT},{PLAY_RES_X - TEXT_RIGHT},{margin_v},1"
    )


def build_ass_document(
    events: list[CaptionEvent],
    *,
    font: FontAsset,
    style: CaptionStyleSpec,
    preview_mark: str | None,
    total_duration_s: float,
) -> str:
    """Escribe el `captions.ass` completo, en UTF-8.

    Guardarlo es DIAGNOSTICO: que el archivo exista no demuestra que el texto
    se vea en pantalla. Eso se comprueba sobre fotogramas del MP4 final.
    """
    margen_inferior = PLAY_RES_Y - TEXT_BOTTOM
    lineas = [
        "[Script Info]",
        "; Generado por viralgen (modulo 4). No editar a mano: se regenera.",
        "ScriptType: v4.00+",
        f"PlayResX: {PLAY_RES_X}",
        f"PlayResY: {PLAY_RES_Y}",
        "WrapStyle: 2",            # sin ajuste automatico: las lineas son nuestras
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        _style_line(style, font_family=font.family, margin_v=margen_inferior),
    ]
    if preview_mark is not None:
        lineas.append(
            # La marca vive ARRIBA, fuera de la region de texto.
            _style_line(
                PREVIEW_STYLE,
                font_family=font.family,
                margin_v=PREVIEW_MARGIN_V,
                alignment=ALIGN_TOP_CENTER,
            )
        )
    lineas += [
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    if preview_mark is not None:
        lineas.append(
            "Dialogue: 0,"
            f"{format_ass_time(0)},{format_ass_time(to_centiseconds(total_duration_s))},"
            f"{PREVIEW_STYLE.name},,0,0,0,,"
            + escape_ass_text(preview_mark)
        )
    for evento in events:
        lineas.append(
            "Dialogue: 0,"
            f"{format_ass_time(evento.start_cs)},{format_ass_time(evento.end_cs)},"
            f"{evento.style},,0,0,0,,{evento.text}"
        )
    return "\n".join(lineas) + "\n"
