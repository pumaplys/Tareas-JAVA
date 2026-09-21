"""Inspeccion y preparacion de imagenes: nada se acepta sin decodificar."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from viralgen.media.imaging import (
    MediaImageError,
    apply_geometry,
    build_contact_sheet,
    encode_jpeg_under_limit,
    inspect_image,
    pad_color_from_palette,
    plan_geometry,
    render_placeholder_image,
    write_bytes_as_image,
)

MAX_PIXELS = 40_000_000


def _imagen(path: Path, ancho: int, alto: int, color: str = "#336699") -> Path:
    Image.new("RGB", (ancho, alto), color).save(path, format="JPEG", quality=85)
    return path


def test_una_imagen_valida_se_mide_de_verdad(tmp_path: Path) -> None:
    ruta = _imagen(tmp_path / "a.jpg", 1024, 1536)
    info = inspect_image(ruta, max_pixels=MAX_PIXELS)
    assert (info.width, info.height, info.format) == (1024, 1536, "JPEG")
    assert info.mime == "image/jpeg"
    assert info.size_bytes == ruta.stat().st_size


def test_una_imagen_corrupta_se_rechaza(tmp_path: Path) -> None:
    ruta = tmp_path / "rota.jpg"
    ruta.write_bytes(b"\xff\xd8\xff\xe0 esto no es una imagen")
    with pytest.raises(MediaImageError):
        inspect_image(ruta, max_pixels=MAX_PIXELS)


def test_la_extension_no_demuestra_nada(tmp_path: Path) -> None:
    """Un MP4 renombrado a .jpg no cuela."""
    ruta = tmp_path / "falsa.jpg"
    ruta.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 200)
    with pytest.raises(MediaImageError):
        inspect_image(ruta, max_pixels=MAX_PIXELS)


def test_bytes_invalidos_no_dejan_archivo(tmp_path: Path) -> None:
    destino = tmp_path / "salida.jpg"
    with pytest.raises(MediaImageError):
        write_bytes_as_image(b"no soy una imagen", destino, max_pixels=MAX_PIXELS)
    assert not destino.exists()


def test_bytes_vacios(tmp_path: Path) -> None:
    with pytest.raises(MediaImageError, match="vacia"):
        write_bytes_as_image(b"", tmp_path / "x.jpg", max_pixels=MAX_PIXELS)


def test_limite_de_pixeles_descomprimidos(tmp_path: Path) -> None:
    ruta = _imagen(tmp_path / "grande.jpg", 2000, 2000)
    with pytest.raises(MediaImageError, match="pixeles"):
        inspect_image(ruta, max_pixels=1_000_000)


def test_contain_no_estira_y_rellena_con_la_paleta(tmp_path: Path) -> None:
    origen = _imagen(tmp_path / "src.jpg", 1024, 1536)  # 2:3
    plan = plan_geometry(
        source_width=1024, source_height=1536,
        target_width=720, target_height=1280,  # 9:16
        policy="contain", pad_color="#F7E3C3", applied=True,
    )
    # La escala conserva la proporcion original: no hay estiramiento.
    assert plan.scaled_width / plan.scaled_height == pytest.approx(1024 / 1536, abs=1e-3)
    assert plan.offset_y > 0 or plan.offset_x > 0

    info = apply_geometry(origen, tmp_path / "out.jpg", plan, max_pixels=MAX_PIXELS)
    assert (info.width, info.height) == (720, 1280)
    with Image.open(tmp_path / "out.jpg") as salida:
        # Las esquinas llevan el color de relleno (aprox, por la compresion).
        r, g, b = salida.convert("RGB").getpixel((2, 2))
        assert r > 200 and g > 190 and b > 150


def test_crop_registra_sus_coordenadas() -> None:
    plan = plan_geometry(
        source_width=1024, source_height=1536,
        target_width=720, target_height=1280,
        policy="crop", pad_color="#000000", applied=True,
    )
    assert plan.crop_box is not None
    izq, sup, der, inf = plan.crop_box
    assert der - izq <= 1024 and inf - sup <= 1536
    assert plan.to_dict()["crop_box"]["left"] == izq


def test_politica_desconocida() -> None:
    with pytest.raises(MediaImageError, match="contain|crop"):
        plan_geometry(
            source_width=10, source_height=10, target_width=10, target_height=10,
            policy="stretch", pad_color="#000000", applied=False,
        )


def test_un_plan_pendiente_no_se_aplica() -> None:
    plan = plan_geometry(
        source_width=1024, source_height=1536, target_width=1080, target_height=1920,
        policy="contain", pad_color="#101418", applied=False,
    )
    assert plan.to_dict()["applied"] is False


def test_compresion_acotada_para_la_data_uri(tmp_path: Path) -> None:
    origen = tmp_path / "pesada.jpg"
    Image.effect_noise((1200, 1600), 90).convert("RGB").save(
        origen, format="JPEG", quality=100
    )
    assert origen.stat().st_size > 1_000_000
    info, politica = encode_jpeg_under_limit(
        origen, tmp_path / "chica.jpg", max_bytes=700_000, max_pixels=MAX_PIXELS
    )
    assert (tmp_path / "chica.jpg").stat().st_size <= 700_000
    assert politica["attempts"] and politica["chosen"]["quality"] in (88, 80, 72, 64)
    assert info.format == "JPEG"


def test_si_no_cabe_bloquea(tmp_path: Path) -> None:
    origen = tmp_path / "ruido.jpg"
    Image.effect_noise((1400, 1800), 120).convert("RGB").save(
        origen, format="JPEG", quality=100
    )
    with pytest.raises(MediaImageError, match="comprimir"):
        encode_jpeg_under_limit(
            origen, tmp_path / "no.jpg", max_bytes=1_000, max_pixels=MAX_PIXELS
        )


def test_hoja_de_contacto_rotula_la_simulacion(tmp_path: Path) -> None:
    uno = _imagen(tmp_path / "1.jpg", 200, 320)
    dos = _imagen(tmp_path / "2.jpg", 200, 320, "#993322")
    info = build_contact_sheet(
        [("sc_01", uno), ("sc_02", dos)], tmp_path / "hoja.jpg",
        simulation=True, max_pixels=MAX_PIXELS,
    )
    assert info.format == "JPEG" and info.width > 0
    assert (tmp_path / "hoja.jpg").stat().st_size < 400_000


def test_hoja_sin_imagenes() -> None:
    with pytest.raises(MediaImageError, match="No hay imagenes"):
        build_contact_sheet([], Path("/tmp/x.jpg"), simulation=True, max_pixels=MAX_PIXELS)


def test_la_imagen_simulada_es_determinista(tmp_path: Path) -> None:
    comun = dict(
        width=720, height=1280, label_lines=["sc_01"], palette=["#2E6F9E"],
        image_format="jpeg", max_pixels=MAX_PIXELS,
    )
    render_placeholder_image(tmp_path / "a.jpg", seed_text="sc_01", **comun)
    render_placeholder_image(tmp_path / "b.jpg", seed_text="sc_01", **comun)
    render_placeholder_image(tmp_path / "c.jpg", seed_text="sc_02", **comun)
    assert (tmp_path / "a.jpg").read_bytes() == (tmp_path / "b.jpg").read_bytes()
    assert (tmp_path / "a.jpg").read_bytes() != (tmp_path / "c.jpg").read_bytes()


def test_color_de_relleno_desde_la_paleta() -> None:
    assert pad_color_from_palette(["#F7E3C3", "#8FBF7A"]) == "#F7E3C3"
    assert pad_color_from_palette(["rojo", "verde"]).startswith("#")
