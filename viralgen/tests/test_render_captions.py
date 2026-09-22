"""Subtitulos ASS: agrupacion, tiempos, escapado y ausencia de inyeccion.

El texto del guion es DATO. Las etiquetas de estilo salen solo de plantillas
de este modulo, y nada de lo que venga en `narration_text` puede convertirse
en una instruccion de libass.
"""

from __future__ import annotations

import pytest

from viralgen.render.captions import (
    CAPTION_STYLES,
    MAX_WORDS_PER_GROUP,
    MIN_FONT_SCALE,
    PREVIEW_STYLE,
    TEXT_LEFT,
    TEXT_RIGHT,
    CaptionError,
    CaptionWord,
    build_ass_document,
    build_events,
    escape_ass_text,
    format_ass_time,
    group_words,
    render_group_text,
    to_centiseconds,
)
from viralgen.render.fonts import load_font

DINAMICO = CAPTION_STYLES["dynamic_emphasis"]
CALMADO = CAPTION_STYLES["calm_readable"]


@pytest.fixture(scope="module")
def fuente():
    return load_font(None)


def _palabras(textos: list[str], *, inicio: float = 0.0, paso: float = 0.4):
    return [
        CaptionWord(
            scene_id="sc_01",
            word_index=indice,
            text=texto,
            start_s=inicio + indice * paso,
            end_s=inicio + indice * paso + paso * 0.9,
        )
        for indice, texto in enumerate(textos)
    ]


# ---------------------------------------------------------------------------
# Cuantizacion: una sola politica de redondeo
# ---------------------------------------------------------------------------


def test_se_cuantizan_fronteras_no_duraciones() -> None:
    """Dos eventos contiguos comparten su frontera: no hay hueco ni solape."""
    fronteras = [0.0, 0.333, 0.667, 1.0]
    centesimas = [to_centiseconds(valor) for valor in fronteras]
    assert centesimas == [0, 33, 67, 100]
    # Las duraciones derivadas suman EXACTAMENTE el total: sin deriva.
    duraciones = [b - a for a, b in zip(centesimas, centesimas[1:])]
    assert sum(duraciones) == centesimas[-1]


def test_formato_de_tiempo_ass() -> None:
    assert format_ass_time(0) == "0:00:00.00"
    assert format_ass_time(2547) == "0:00:25.47"
    assert format_ass_time(360_000) == "1:00:00.00"


def test_los_eventos_no_se_solapan_ni_tienen_duracion_negativa(fuente) -> None:
    palabras = _palabras(["Mira", "otra", "vez", "este", "objeto", "pequeno"])
    grupos = group_words(palabras, font=fuente, style=DINAMICO)
    eventos = build_events(grupos, style=DINAMICO, total_duration_s=10.0)
    anterior = None
    for evento in eventos:
        assert evento.duration_cs > 0
        if anterior is not None:
            assert evento.start_cs >= anterior.end_cs
        anterior = evento


def test_una_palabra_que_colapsa_conserva_su_texto(fuente) -> None:
    """Menos de una centesima: se pierde el resaltado, NO el texto."""
    palabras = [
        CaptionWord(scene_id="sc_01", word_index=0, text="uno", start_s=0.0, end_s=0.40),
        # 2 ms: al cuantizar, inicio y fin caen en la misma centesima.
        CaptionWord(scene_id="sc_01", word_index=1, text="ya", start_s=0.40, end_s=0.402),
        CaptionWord(scene_id="sc_01", word_index=2, text="tres", start_s=0.402, end_s=0.9),
    ]
    grupos = group_words(palabras, font=fuente, style=DINAMICO)
    eventos = build_events(grupos, style=DINAMICO, total_duration_s=3.0)
    assert grupos[0].collapsed == [1], "debe registrarse la limitacion"
    # El texto sigue estando en pantalla, dentro del grupo.
    assert any("ya" in evento.text for evento in eventos)
    # Y no se fabrico una duracion de voz para ella.
    assert all(evento.active_word != 1 for evento in eventos)


# ---------------------------------------------------------------------------
# Agrupacion
# ---------------------------------------------------------------------------


def test_los_grupos_tienen_entre_dos_y_cinco_palabras(fuente) -> None:
    palabras = _palabras(
        ["Mira", "otra", "vez", "este", "objeto", "que", "usas", "todos", "los", "dias"]
    )
    grupos = group_words(palabras, font=fuente, style=DINAMICO)
    assert grupos
    for grupo in grupos:
        assert 1 <= len(grupo.words) <= MAX_WORDS_PER_GROUP
    # Todas las palabras quedan cubiertas, ninguna se pierde.
    cubiertas = [palabra.word_index for grupo in grupos for palabra in grupo.words]
    assert cubiertas == list(range(len(palabras)))


def test_un_grupo_largo_se_reparte_en_dos_lineas(fuente) -> None:
    palabras = _palabras(["cremallera", "dientes", "enganchan", "siempre"])
    grupos = group_words(palabras, font=fuente, style=DINAMICO)
    assert any(len(grupo.lines) == 2 for grupo in grupos)
    for grupo in grupos:
        assert len(grupo.lines) <= 2


def test_una_palabra_larga_encoge_lo_justo_en_vez_de_bloquear(fuente) -> None:
    """"extraordinariamente" mide 889 px a tamano 76 y la region tiene 888.

    Es una palabra legitima del espanol: bloquear el render entero por ella
    seria peor que encogerla un poco. El encogido esta ACOTADO.
    """
    palabras = _palabras(["extraordinariamente", "raro"])
    grupos = group_words(palabras, font=fuente, style=DINAMICO)
    assert grupos[0].font_scale < 1.0
    assert grupos[0].font_scale >= MIN_FONT_SCALE
    # La palabra no se recorta ni desaparece.
    assert "extraordinariamente" in grupos[0].text
    # Y el tamano propio se escribe una sola vez, al principio del evento.
    texto = render_group_text(grupos[0], style=DINAMICO, active=0, intro=False)
    assert texto.count("\\fs") == 1
    assert texto.startswith("{\\fs")


def test_el_encogido_no_es_ilimitado(fuente) -> None:
    """Si ni al minimo cabe, se bloquea: no se recorta el texto."""
    palabras = _palabras(["a" * 120, "corta"])
    with pytest.raises(CaptionError, match="no cabe"):
        group_words(palabras, font=fuente, style=DINAMICO)


def test_la_escala_no_cambia_entre_los_eventos_de_un_grupo(fuente) -> None:
    """La caja sigue quieta aunque el grupo lleve tamano propio."""
    palabras = _palabras(["extraordinariamente", "raro"])
    grupos = group_words(palabras, font=fuente, style=DINAMICO)
    eventos = build_events(grupos, style=DINAMICO, total_duration_s=5.0)
    del_grupo = [e for e in eventos if e.group_index == grupos[0].index]
    tamanos = {e.text.split("}")[0] for e in del_grupo if e.text.startswith("{\\fs")}
    assert len(tamanos) == 1


def test_ningun_grupo_se_pasa_del_ancho_disponible(fuente) -> None:
    ancho = TEXT_RIGHT - TEXT_LEFT
    palabras = _palabras(
        ["cremallera", "dientes", "enganchan", "deslizador", "mecanismo", "sencillo"]
    )
    grupos = group_words(palabras, font=fuente, style=DINAMICO)
    for grupo in grupos:
        for linea in grupo.lines:
            texto = " ".join(grupo.words[indice].text for indice in linea)
            assert fuente.text_width(texto, font_size_px=DINAMICO.font_size) <= ancho


def test_una_pausa_larga_corta_el_grupo(fuente) -> None:
    palabras = [
        CaptionWord(scene_id="sc_01", word_index=0, text="antes", start_s=0.0, end_s=0.3),
        CaptionWord(scene_id="sc_01", word_index=1, text="de", start_s=0.3, end_s=0.5),
        # Pausa de 1 s: el grupo se corta aqui.
        CaptionWord(scene_id="sc_01", word_index=2, text="la", start_s=1.5, end_s=1.8),
        CaptionWord(scene_id="sc_01", word_index=3, text="pausa", start_s=1.8, end_s=2.1),
    ]
    grupos = group_words(palabras, font=fuente, style=DINAMICO)
    assert len(grupos) == 2
    assert [palabra.text for palabra in grupos[0].words] == ["antes", "de"]


def test_las_escenas_no_se_mezclan_en_un_grupo(fuente) -> None:
    palabras = [
        CaptionWord(scene_id="sc_01", word_index=0, text="final", start_s=0.0, end_s=0.3),
        CaptionWord(scene_id="sc_01", word_index=1, text="escena", start_s=0.3, end_s=0.6),
        CaptionWord(scene_id="sc_02", word_index=0, text="nueva", start_s=0.6, end_s=0.9),
        CaptionWord(scene_id="sc_02", word_index=1, text="escena", start_s=0.9, end_s=1.2),
    ]
    grupos = group_words(palabras, font=fuente, style=DINAMICO)
    for grupo in grupos:
        assert len({palabra.scene_id for palabra in grupo.words}) == 1


# ---------------------------------------------------------------------------
# Resaltado
# ---------------------------------------------------------------------------


def test_la_caja_no_cambia_al_cambiar_la_palabra_activa(fuente) -> None:
    """Solo cambia el COLOR: mismos saltos de linea, mismo tamano.

    Si cambiara el tamano de la palabra activa, la frase saltaria de lado en
    cada palabra.
    """
    palabras = _palabras(["Mira", "otra", "vez"])
    grupo = group_words(palabras, font=fuente, style=DINAMICO)[0]
    textos = [
        render_group_text(grupo, style=DINAMICO, active=indice, intro=False)
        for indice in range(len(grupo.words))
    ]
    for texto in textos:
        # Ninguna variante reescala: no hay \fscx/\fscy fuera de la entrada.
        assert "\\fscx" not in texto
        # Los saltos de linea son identicos en todas.
        assert texto.count("\\N") == textos[0].count("\\N")
    # Y cada variante resalta una palabra distinta.
    assert len({texto for texto in textos}) == len(textos)


def test_la_animacion_de_entrada_solo_va_en_el_primer_evento(fuente) -> None:
    """Repetirla reiniciaria la animacion en cada palabra."""
    palabras = _palabras(["Mira", "otra", "vez", "esto"])
    grupos = group_words(palabras, font=fuente, style=DINAMICO)
    eventos = build_events(grupos, style=DINAMICO, total_duration_s=5.0)
    del_primer_grupo = [e for e in eventos if e.group_index == grupos[0].index]
    con_animacion = [e for e in del_primer_grupo if "\\t(" in e.text]
    assert len(con_animacion) == 1
    assert con_animacion[0] is del_primer_grupo[0]


def test_el_estilo_infantil_no_resalta_por_palabra(fuente) -> None:
    """Grupos estables: sin sacudidas, destellos ni saltos por palabra."""
    palabras = _palabras(["Habia", "una", "vez", "un", "zorro"])
    grupos = group_words(palabras, font=fuente, style=CALMADO)
    eventos = build_events(grupos, style=CALMADO, total_duration_s=5.0)
    assert all(evento.active_word is None for evento in eventos)
    assert all("\\t(" not in evento.text for evento in eventos)
    # Un solo evento por grupo.
    assert len(eventos) == len(grupos)


def test_el_resaltado_no_se_queda_pegado_en_los_huecos(fuente) -> None:
    palabras = [
        CaptionWord(scene_id="sc_01", word_index=0, text="una", start_s=0.0, end_s=0.3),
        # Hueco de 0,2 s dentro del mismo grupo.
        CaptionWord(scene_id="sc_01", word_index=1, text="dos", start_s=0.5, end_s=0.8),
    ]
    grupos = group_words(palabras, font=fuente, style=DINAMICO)
    eventos = build_events(grupos, style=DINAMICO, total_duration_s=3.0)
    huecos = [e for e in eventos if e.active_word is None and e.start_cs == 30]
    assert huecos, "el hueco debe mostrar el grupo sin palabra activa"


def test_un_grupo_no_se_adelanta_al_siguiente(fuente) -> None:
    palabras = [
        CaptionWord(scene_id="sc_01", word_index=0, text="uno", start_s=0.0, end_s=0.3),
        CaptionWord(scene_id="sc_01", word_index=1, text="dos", start_s=0.3, end_s=0.6),
        CaptionWord(scene_id="sc_01", word_index=2, text="tres", start_s=2.0, end_s=2.3),
        CaptionWord(scene_id="sc_01", word_index=3, text="cuatro", start_s=2.3, end_s=2.6),
    ]
    grupos = group_words(palabras, font=fuente, style=DINAMICO)
    eventos = build_events(grupos, style=DINAMICO, total_duration_s=5.0)
    primer_grupo = [e for e in eventos if e.group_index == grupos[0].index]
    segundo_grupo = [e for e in eventos if e.group_index == grupos[1].index]
    assert primer_grupo[-1].end_cs <= segundo_grupo[0].start_cs
    # Y el primero no se queda visible durante toda la pausa larga.
    assert primer_grupo[-1].end_cs <= to_centiseconds(0.6 + 0.61)


# ---------------------------------------------------------------------------
# Escapado e inyeccion
# ---------------------------------------------------------------------------


def test_las_llaves_se_escapan_como_texto_literal() -> None:
    assert escape_ass_text("valor {x}") == "valor \\{x\\}"
    assert escape_ass_text("{tag}") == "\\{tag\\}"


def test_una_etiqueta_ass_en_el_guion_no_se_ejecuta(fuente) -> None:
    """Un intento de inyeccion nunca se ejecuta: o se escapa o se bloquea.

    Con barra invertida —`{\\an8}`— se BLOQUEA, porque no hay forma
    inequivoca de escribir una barra invertida literal. Sin ella, las llaves
    se escapan y quedan como texto visible.
    """
    con_barra = _palabras(["{\\an8}posicion", "cambiada"])
    grupos = group_words(con_barra, font=fuente, style=DINAMICO)
    with pytest.raises(CaptionError, match="barra invertida"):
        render_group_text(grupos[0], style=DINAMICO, active=0, intro=False)

    sin_barra = _palabras(["{an8}posicion", "cambiada"])
    grupos = group_words(sin_barra, font=fuente, style=DINAMICO)
    texto = render_group_text(grupos[0], style=DINAMICO, active=0, intro=False)
    # La llave del guion aparece escapada, no abriendo un bloque de etiquetas.
    assert "\\{an8\\}" in texto


def test_una_barra_invertida_bloquea_con_motivo() -> None:
    """`\\N` en el texto seria un salto de linea de libass: se bloquea."""
    with pytest.raises(CaptionError, match="barra invertida"):
        escape_ass_text("primera\\Nsegunda")
    with pytest.raises(CaptionError, match="barra invertida"):
        escape_ass_text("ruta\\con\\barras")


def test_una_barra_normal_es_texto_corriente() -> None:
    assert escape_ass_text("50/50 km/h") == "50/50 km/h"


def test_los_saltos_de_linea_del_origen_se_normalizan() -> None:
    """Un salto crudo romperia el formato por lineas del ASS."""
    assert escape_ass_text("dos\nlineas") == "dos lineas"
    assert escape_ass_text("  espacios   sobrantes ") == "espacios sobrantes"


def test_el_espanol_se_escribe_tal_cual() -> None:
    texto = "¿Por qué la cremallera no se suelta? ¡Míralo! «así» —sí—"
    assert escape_ass_text(texto) == texto


# ---------------------------------------------------------------------------
# Documento ASS
# ---------------------------------------------------------------------------


def test_el_documento_declara_el_lienzo_y_los_estilos(fuente) -> None:
    palabras = _palabras(["Mira", "esto"])
    grupos = group_words(palabras, font=fuente, style=DINAMICO)
    eventos = build_events(grupos, style=DINAMICO, total_duration_s=2.0)
    documento = build_ass_document(
        eventos, font=fuente, style=DINAMICO, preview_mark=None, total_duration_s=2.0
    )
    assert "PlayResX: 1080" in documento
    assert "PlayResY: 1920" in documento
    assert f"Style: {DINAMICO.name}," in documento
    assert fuente.family in documento
    assert "Dialogue:" in documento


def test_la_marca_de_preview_va_arriba_y_fuera_de_la_region_de_texto(fuente) -> None:
    palabras = _palabras(["Mira", "esto"])
    grupos = group_words(palabras, font=fuente, style=DINAMICO)
    eventos = build_events(grupos, style=DINAMICO, total_duration_s=2.0)
    documento = build_ass_document(
        eventos,
        font=fuente,
        style=DINAMICO,
        preview_mark="PREVIEW · SIMULACIÓN",
        total_duration_s=2.0,
    )
    linea_preview = next(
        linea for linea in documento.splitlines()
        if linea.startswith(f"Style: {PREVIEW_STYLE.name},")
    )
    campos = linea_preview.split(",")
    # Alineacion 8 = arriba-centro; el texto normal usa 2 = abajo-centro.
    assert campos[18] == "8"
    linea_texto = next(
        linea for linea in documento.splitlines()
        if linea.startswith(f"Style: {DINAMICO.name},")
    )
    assert linea_texto.split(",")[18] == "2"
    assert "PREVIEW · SIMULACIÓN" in documento


def test_sin_preview_no_hay_marca(fuente) -> None:
    palabras = _palabras(["Mira", "esto"])
    grupos = group_words(palabras, font=fuente, style=DINAMICO)
    eventos = build_events(grupos, style=DINAMICO, total_duration_s=2.0)
    documento = build_ass_document(
        eventos, font=fuente, style=DINAMICO, preview_mark=None, total_duration_s=2.0
    )
    assert "PREVIEW" not in documento
    assert f"Style: {PREVIEW_STYLE.name}," not in documento
