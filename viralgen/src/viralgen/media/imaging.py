"""Inspeccion y preparacion ligera de imagenes con Pillow.

Este modulo NO monta nada: mide, verifica y produce derivados acotados.

Reglas:

* Todo byte recibido se DECODIFICA y se mide antes de aceptarlo: la extension
  o el tamano no demuestran que un archivo sea una imagen.
* Los pixeles descomprimidos estan acotados (proteccion local frente a
  imagenes bomba).
* La adaptacion conservadora por defecto es `contain` con relleno de un color
  de la paleta. `crop` solo por configuracion explicita, y se registran sus
  coordenadas. NUNCA se estira la imagen.
* Se conserva siempre el original; cada derivado se registra con su hash y la
  transformacion aplicada.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont, UnidentifiedImageError

from ..errors import ViralgenError

#: Formatos que este MVP acepta como imagen.
ACCEPTED_FORMATS = {"JPEG", "PNG", "WEBP"}

#: Colores de relleno por defecto si la paleta no aporta ninguno utilizable.
FALLBACK_PAD_COLOR = "#101418"


class MediaImageError(ViralgenError):
    exit_code = 5
    code = "media_image_error"


@dataclass(frozen=True)
class ImageInfo:
    """Medidas REALES de un archivo de imagen, leidas de sus bytes."""

    path: Path
    format: str
    width: int
    height: int
    mode: str
    size_bytes: int

    @property
    def aspect(self) -> float:
        return self.width / self.height

    @property
    def mime(self) -> str:
        return {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}.get(
            self.format, "application/octet-stream"
        )

    def describe(self) -> dict:
        return {
            "format": self.format,
            "width": self.width,
            "height": self.height,
            "mode": self.mode,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class GeometryPlan:
    """Como llevar una imagen de su geometria real a la proporcion objetivo.

    `applied=False` significa INSTRUCCION PENDIENTE para el modulo 4; con
    `applied=True` la transformacion ya esta incorporada al archivo y el
    modulo 4 no debe repetirla.
    """

    policy: str
    source_width: int
    source_height: int
    target_width: int
    target_height: int
    scaled_width: int
    scaled_height: int
    offset_x: int
    offset_y: int
    pad_color: str
    crop_box: tuple[int, int, int, int] | None
    applied: bool

    def to_dict(self) -> dict:
        datos = {
            "policy": self.policy,
            "applied": self.applied,
            "source": {"width": self.source_width, "height": self.source_height},
            "target": {"width": self.target_width, "height": self.target_height},
            "scaled": {"width": self.scaled_width, "height": self.scaled_height},
            "offset": {"x": self.offset_x, "y": self.offset_y},
            "pad_color": self.pad_color,
        }
        if self.crop_box is not None:
            izq, sup, der, inf = self.crop_box
            datos["crop_box"] = {"left": izq, "top": sup, "right": der, "bottom": inf}
        return datos


def _limit_pixels(max_pixels: int) -> None:
    Image.MAX_IMAGE_PIXELS = max_pixels


def inspect_image(path: Path, *, max_pixels: int) -> ImageInfo:
    """Abre y verifica un archivo de imagen. Falla si no se puede decodificar."""
    _limit_pixels(max_pixels)
    try:
        with Image.open(path) as imagen:
            imagen.verify()
        with Image.open(path) as imagen:
            formato = (imagen.format or "").upper()
            ancho, alto = imagen.size
            modo = imagen.mode
            imagen.load()  # fuerza la decodificacion completa
    except UnidentifiedImageError as exc:
        raise MediaImageError(
            f"El archivo {path.name} no es una imagen decodificable."
        ) from exc
    except Image.DecompressionBombError as exc:
        raise MediaImageError(
            f"La imagen {path.name} supera el limite local de pixeles ({max_pixels})."
        ) from exc
    except OSError as exc:
        raise MediaImageError(f"No se pudo leer la imagen {path.name}: {exc}") from exc

    if formato not in ACCEPTED_FORMATS:
        raise MediaImageError(
            f"Formato {formato or 'desconocido'} no aceptado en {path.name}; "
            f"admitidos: {', '.join(sorted(ACCEPTED_FORMATS))}."
        )
    if ancho <= 0 or alto <= 0:
        raise MediaImageError(f"Dimensiones invalidas en {path.name}: {ancho}x{alto}.")
    return ImageInfo(
        path=path,
        format=formato,
        width=ancho,
        height=alto,
        mode=modo,
        size_bytes=path.stat().st_size,
    )


def write_bytes_as_image(
    data: bytes,
    destination: Path,
    *,
    max_pixels: int,
    expected_format: str | None = None,
) -> ImageInfo:
    """Escribe bytes y los verifica como imagen. Si no valen, no deja archivo."""
    if not data:
        raise MediaImageError("El proveedor devolvio una imagen vacia.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    try:
        info = inspect_image(destination, max_pixels=max_pixels)
    except MediaImageError:
        destination.unlink(missing_ok=True)
        raise
    if expected_format and info.format != expected_format.upper():
        destination.unlink(missing_ok=True)
        raise MediaImageError(
            f"Se esperaba {expected_format.upper()} y llego {info.format}."
        )
    return info


def pad_color_from_palette(palette: list[str]) -> str:
    """Elige un color de relleno de la paleta de la biblia visual."""
    for color in palette:
        limpio = color.strip()
        if limpio.startswith("#") and len(limpio) in (4, 7):
            return limpio
    return FALLBACK_PAD_COLOR


def plan_geometry(
    *,
    source_width: int,
    source_height: int,
    target_width: int,
    target_height: int,
    policy: str,
    pad_color: str,
    applied: bool,
) -> GeometryPlan:
    """Calcula la adaptacion sin aplicarla.

    `contain`: la imagen entra entera y el resto se rellena con `pad_color`.
    `crop`: se recorta centrado a la proporcion objetivo, registrando la caja.
    """
    if policy not in {"contain", "crop"}:
        raise MediaImageError(
            f"Politica de geometria desconocida: {policy!r} (contain|crop)."
        )
    if min(source_width, source_height, target_width, target_height) <= 0:
        raise MediaImageError("Geometria invalida: alguna dimension no es positiva.")

    if policy == "contain":
        factor = min(target_width / source_width, target_height / source_height)
        escalado_ancho = max(1, int(round(source_width * factor)))
        escalado_alto = max(1, int(round(source_height * factor)))
        return GeometryPlan(
            policy="contain",
            source_width=source_width,
            source_height=source_height,
            target_width=target_width,
            target_height=target_height,
            scaled_width=escalado_ancho,
            scaled_height=escalado_alto,
            offset_x=(target_width - escalado_ancho) // 2,
            offset_y=(target_height - escalado_alto) // 2,
            pad_color=pad_color,
            crop_box=None,
            applied=applied,
        )

    objetivo = Fraction(target_width, target_height)
    ancho_recorte = min(source_width, int(source_height * objetivo))
    alto_recorte = min(source_height, int(source_width / objetivo))
    izquierda = (source_width - ancho_recorte) // 2
    superior = (source_height - alto_recorte) // 2
    return GeometryPlan(
        policy="crop",
        source_width=source_width,
        source_height=source_height,
        target_width=target_width,
        target_height=target_height,
        scaled_width=target_width,
        scaled_height=target_height,
        offset_x=0,
        offset_y=0,
        pad_color=pad_color,
        crop_box=(izquierda, superior, izquierda + ancho_recorte, superior + alto_recorte),
        applied=applied,
    )


def apply_geometry(
    source: Path, destination: Path, plan: GeometryPlan, *, max_pixels: int, quality: int = 90
) -> ImageInfo:
    """Aplica un plan de geometria y devuelve las medidas del derivado."""
    _limit_pixels(max_pixels)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as imagen:
        imagen = imagen.convert("RGB")
        if plan.policy == "crop" and plan.crop_box is not None:
            recortada = imagen.crop(plan.crop_box)
            salida = recortada.resize(
                (plan.target_width, plan.target_height), Image.LANCZOS
            )
        else:
            lienzo = Image.new(
                "RGB", (plan.target_width, plan.target_height), plan.pad_color
            )
            escalada = imagen.resize((plan.scaled_width, plan.scaled_height), Image.LANCZOS)
            lienzo.paste(escalada, (plan.offset_x, plan.offset_y))
            salida = lienzo
        # JPEG opaco: sin canal alfa.
        salida.save(destination, format="JPEG", quality=quality, optimize=True)
    return inspect_image(destination, max_pixels=max_pixels)


def encode_jpeg_under_limit(
    source: Path, destination: Path, *, max_bytes: int, max_pixels: int
) -> tuple[ImageInfo, dict]:
    """Recomprime a JPEG hasta caber en `max_bytes` mediante una politica acotada.

    La politica esta registrada: calidades 88, 80, 72, 64 y, si aun no cabe,
    reduccion del lado mayor a 90 % una sola vez por calidad. Si no cabe, se
    bloquea: no se recorta contenido ni se cambia la escena.
    """
    _limit_pixels(max_pixels)
    calidades = (88, 80, 72, 64)
    intentos: list[dict] = []
    with Image.open(source) as original:
        base = original.convert("RGB")
        ancho, alto = base.size
        for reduccion in (1.0, 0.9, 0.8):
            escala_ancho = max(1, int(ancho * reduccion))
            escala_alto = max(1, int(alto * reduccion))
            imagen = (
                base
                if reduccion == 1.0
                else base.resize((escala_ancho, escala_alto), Image.LANCZOS)
            )
            for calidad in calidades:
                buffer = io.BytesIO()
                imagen.save(buffer, format="JPEG", quality=calidad, optimize=True)
                datos = buffer.getvalue()
                intentos.append(
                    {
                        "scale": reduccion,
                        "quality": calidad,
                        "bytes": len(datos),
                        "width": imagen.width,
                        "height": imagen.height,
                    }
                )
                if len(datos) <= max_bytes:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(datos)
                    return (
                        inspect_image(destination, max_pixels=max_pixels),
                        {"attempts": intentos, "chosen": intentos[-1]},
                    )
    raise MediaImageError(
        f"No se pudo comprimir {source.name} por debajo de {max_bytes} bytes con la "
        "politica acotada; el trabajo se bloquea en vez de degradar la escena.",
        details={"attempts": intentos},
    )


# ---------------------------------------------------------------------------
# Hoja de contacto
# ---------------------------------------------------------------------------


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover - Pillow antiguo
        return ImageFont.load_default()


def build_contact_sheet(
    entries: list[tuple[str, Path]],
    destination: Path,
    *,
    simulation: bool,
    max_pixels: int,
    thumb_width: int = 180,
    columns: int = 4,
) -> ImageInfo:
    """Hoja de contacto pequena para INSPECCION HUMANA.

    Es un recurso auxiliar: no forma parte del montaje y no acredita calidad.
    """
    _limit_pixels(max_pixels)
    if not entries:
        raise MediaImageError("No hay imagenes para la hoja de contacto.")
    thumb_height = int(thumb_width * 16 / 9)
    etiqueta = 22
    filas = math.ceil(len(entries) / columns)
    cabecera = 34
    ancho = columns * thumb_width
    alto = cabecera + filas * (thumb_height + etiqueta)

    lienzo = Image.new("RGB", (ancho, alto), "#14181d")
    lapiz = ImageDraw.Draw(lienzo)
    titulo = "HOJA DE CONTACTO" + ("  ·  SIMULACION" if simulation else "")
    lapiz.rectangle([0, 0, ancho, cabecera], fill="#8a1c1c" if simulation else "#1f3a5f")
    lapiz.text((8, 9), titulo, fill="#ffffff", font=_font(16))

    for indice, (etiqueta_texto, ruta) in enumerate(entries):
        columna = indice % columns
        fila = indice // columns
        x = columna * thumb_width
        y = cabecera + fila * (thumb_height + etiqueta)
        try:
            with Image.open(ruta) as imagen:
                miniatura = imagen.convert("RGB")
                miniatura.thumbnail((thumb_width - 8, thumb_height - 8), Image.LANCZOS)
                lienzo.paste(
                    miniatura,
                    (
                        x + (thumb_width - miniatura.width) // 2,
                        y + (thumb_height - miniatura.height) // 2,
                    ),
                )
        except (OSError, UnidentifiedImageError):
            lapiz.rectangle(
                [x + 4, y + 4, x + thumb_width - 4, y + thumb_height - 4], outline="#8a1c1c"
            )
        lapiz.text(
            (x + 6, y + thumb_height + 4), etiqueta_texto[:28], fill="#d7dee6", font=_font(13)
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    lienzo.save(destination, format="JPEG", quality=78, optimize=True)
    return inspect_image(destination, max_pixels=max_pixels)


def render_placeholder_image(
    destination: Path,
    *,
    width: int,
    height: int,
    seed_text: str,
    label_lines: list[str],
    palette: list[str],
    image_format: str,
    max_pixels: int,
) -> ImageInfo:
    """Imagen de PRUEBA determinista, rotulada SIMULACION.

    Son formas geometricas derivadas de un hash: no son ilustraciones de IA ni
    evidencia de calidad visual de ningun proveedor.
    """
    import hashlib

    _limit_pixels(max_pixels)
    resumen = hashlib.sha256(seed_text.encode("utf-8")).digest()
    colores = [color for color in palette if color.strip().startswith("#")] or [
        "#2E6F9E",
        "#D9622B",
        "#8C8F94",
    ]
    fondo = colores[resumen[0] % len(colores)]
    lienzo = Image.new("RGB", (width, height), fondo)
    lapiz = ImageDraw.Draw(lienzo)

    def _variar(color: str, desplazamiento: bytes) -> tuple[int, int, int]:
        """Deriva un color visible del hash, aunque la paleta tenga un solo tono.

        Sin esto, una paleta de un color produciria formas invisibles y dos
        semillas distintas darian la misma imagen.
        """
        base = ImageColor.getrgb(color)
        return tuple(
            max(0, min(255, canal + (byte - 128) // 2))
            for canal, byte in zip(base, desplazamiento, strict=False)
        )

    for indice in range(6):
        base = resumen[indice * 3 : indice * 3 + 3]
        color = _variar(colores[base[0] % len(colores)], resumen[indice + 8 : indice + 11])
        x0 = int(base[1] / 255 * width * 0.8)
        y0 = int(base[2] / 255 * height * 0.8)
        ancho_forma = int(width * (0.15 + (base[0] % 40) / 200))
        alto_forma = int(height * (0.08 + (base[1] % 40) / 300))
        if indice % 2:
            lapiz.ellipse([x0, y0, x0 + ancho_forma, y0 + alto_forma], fill=color)
        else:
            lapiz.rectangle([x0, y0, x0 + ancho_forma, y0 + alto_forma], fill=color)

    banda = max(48, height // 12)
    lapiz.rectangle([0, 0, width, banda], fill="#8a1c1c")
    lapiz.text((12, banda // 3), "SIMULACION - NO ES UNA IMAGEN REAL", fill="#ffffff",
               font=_font(max(14, banda // 3)))
    for numero, linea in enumerate(label_lines[:4]):
        lapiz.text(
            (12, banda + 12 + numero * (banda // 2)),
            linea[:60],
            fill="#ffffff",
            font=_font(max(12, banda // 4)),
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    formato = image_format.upper()
    formato = "JPEG" if formato in {"JPG", "JPEG"} else formato
    lienzo.save(destination, format=formato, quality=82 if formato == "JPEG" else None)
    return inspect_image(destination, max_pixels=max_pixels)
