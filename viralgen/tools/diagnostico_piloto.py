#!/usr/bin/env python3
"""Diagnostico local para el primer piloto de YouTube.

Dice que hay y que falta, **sin mostrar ningun valor de credencial**: solo el
nombre de la variable y si esta definida. Tampoco toca la red: no comprueba que
una clave sea valida, solo que exista.

Uso:
    python tools/diagnostico_piloto.py [--json]

Salida: JSON estructurado en stdout (como el resto del proyecto) y un resumen
legible en stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

#: Variables necesarias para cada etapa del piloto. El valor NUNCA se lee.
ETAPAS: dict[str, tuple[str, ...]] = {
    "1_guion": ("OPENAI_API_KEY", "OPENAI_MODEL"),
    "2_voz": ("ELEVENLABS_API_KEY", "ELEVENLABS_MODEL_ID"),
    "3_imagenes": ("OPENAI_API_KEY", "OPENAI_IMAGE_MODEL"),
    "4_montaje": (),
    "5_publicacion": (
        "YOUTUBE_CLIENT_ID",
        "YOUTUBE_CHANNEL_ID",
        "VIRALGEN_PUBLISH_SECRETS_DIR",
        "VIRALGEN_PUBLISH_ACCOUNTS_PATH",
    ),
}

#: Opcionales, con el efecto de no definirlas.
OPCIONALES: dict[str, str] = {
    "ELEVENLABS_VOICE_ID": (
        "sin esto, el voice_id debe venir del catalogo de voces; si los dos "
        "faltan, el modulo 2 se detiene y NO inventa un identificador"
    ),
    "YOUTUBE_CLIENT_SECRET": (
        "solo si tu tipo de cliente OAuth lo usa; el client_id de una app "
        "instalada es publico"
    ),
    "VIRALGEN_PROFILES_PATH": (
        "sin esto se usan los perfiles internos, cuyo infantil_cuentos admite "
        "clips de video (video_scene_budget=2) y pediria Runway"
    ),
    "VIRALGEN_VOICE_PROFILES_PATH": "catalogo de voces propio",
    "VIRALGEN_PRICE_INPUT_PER_1M_USD": "sin tarifa, el coste estimado es null",
    "VIRALGEN_PRICE_OUTPUT_PER_1M_USD": "sin tarifa, el coste estimado es null",
    "VIRALGEN_PRICE_VOICE_PER_1K_CHARS_USD": "sin tarifa, el coste estimado es null",
    "VIRALGEN_PRICE_IMAGE_PER_UNIT_USD": "sin tarifa, el coste estimado es null",
}

#: Lo que este piloto NO usa.
FUERA_DEL_PILOTO: dict[str, str] = {
    "RUNWAYML_API_SECRET": "el perfil solo de imagenes no genera clips",
    "META_GRAPH_API_VERSION": "el piloto no publica en Instagram",
    "PUBLISH_STAGING_BUCKET": "sin Instagram no hay almacenamiento temporal",
}


def _presente(nombre: str) -> bool:
    valor = os.environ.get(nombre)
    return bool(valor and valor.strip())


def _permisos(ruta: Path) -> str | None:
    try:
        return oct(stat.S_IMODE(ruta.stat().st_mode))
    except OSError:
        return None


def diagnosticar() -> dict:
    informe: dict = {"command": "diagnostico_piloto", "secrets_shown": False}

    # --- Interprete y paquete --------------------------------------------
    informe["python"] = {
        "version": sys.version.split()[0],
        "minimo_esperado": "3.12",
        "cumple": sys.version_info[:2] >= (3, 12),
    }
    try:
        import viralgen

        informe["paquete"] = {
            "instalado": True,
            "version": getattr(viralgen, "__version__", "desconocida"),
            "ruta": str(Path(viralgen.__file__).parent),
        }
    except Exception as exc:  # pragma: no cover - sin paquete no hay nada
        informe["paquete"] = {"instalado": False, "error": str(exc)[:200]}
        return informe

    # --- Dependencias ----------------------------------------------------
    dependencias = {}
    for modulo, para_que in (
        ("pydantic", "contratos"),
        ("pydantic_settings", "configuracion"),
        ("httpx2", "cliente HTTP de voz, imagenes y publicacion"),
        ("openai", "guion e imagenes"),
        ("PIL", "decodificacion y geometria de imagenes"),
    ):
        try:
            __import__(modulo)
            dependencias[modulo] = {"presente": True, "para": para_que}
        except ImportError:
            dependencias[modulo] = {"presente": False, "para": para_que}
    try:
        __import__("boto3")
        dependencias["boto3"] = {
            "presente": True,
            "para": "almacenamiento temporal de Instagram (NO lo usa este piloto)",
        }
    except ImportError:
        dependencias["boto3"] = {
            "presente": False,
            "para": "opcional; este piloto no lo necesita",
        }
    informe["dependencias"] = dependencias

    # --- FFmpeg y tipografia ---------------------------------------------
    from viralgen.render.ffmpeg import probe_capabilities

    capacidades = probe_capabilities("ffmpeg", "ffprobe")
    informe["ffmpeg"] = {
        "ffmpeg": shutil.which("ffmpeg"),
        "ffprobe": shutil.which("ffprobe"),
        "version": capacidades.ffmpeg_version,
        "libass": capacidades.has_libass,
        "usable": capacidades.usable,
        "faltan": list(capacidades.missing),
    }
    try:
        from viralgen.render.fonts import load_font

        fuente = load_font(None)
        informe["tipografia"] = {
            "encontrada": True,
            "familia": fuente.family,
            "ruta": str(fuente.path),
            "sha256_prefijo": fuente.sha256[:16],
        }
    except Exception as exc:
        informe["tipografia"] = {"encontrada": False, "motivo": str(exc)[:200]}

    # --- Configuracion: solo nombres y presencia -------------------------
    from viralgen.config import load_settings

    ajustes = load_settings()
    etapas = {}
    faltan_todas: list[str] = []
    for etapa, variables in ETAPAS.items():
        estado = {nombre: _presente(nombre) for nombre in variables}
        faltan = [nombre for nombre, hay in estado.items() if not hay]
        faltan_todas.extend(faltan)
        etapas[etapa] = {"variables": estado, "faltan": faltan}
    informe["configuracion"] = {
        "por_etapa": etapas,
        "faltan": sorted(set(faltan_todas)),
        "opcionales": {
            nombre: {"definida": _presente(nombre), "si_falta": efecto}
            for nombre, efecto in OPCIONALES.items()
        },
        "fuera_del_piloto": {
            nombre: {"definida": _presente(nombre), "motivo": motivo}
            for nombre, motivo in FUERA_DEL_PILOTO.items()
        },
    }

    # --- Rutas -----------------------------------------------------------
    rutas = {}
    datos = Path(ajustes.data_dir).expanduser()
    rutas["data_dir"] = {
        "ruta": str(datos),
        "existe": datos.is_dir(),
        "escribible": os.access(datos if datos.is_dir() else datos.parent, os.W_OK),
    }
    perfiles = ajustes.profiles_path
    rutas["profiles_path"] = {
        "ruta": str(perfiles) if perfiles else None,
        "existe": bool(perfiles and Path(perfiles).is_file()),
        "nota": (
            "para el piloto debe apuntar a un catalogo con "
            "video_scene_budget=0, p. ej. examples/perfiles_solo_imagenes.json"
        ),
    }
    cuentas = getattr(ajustes, "publish_accounts_path", None)
    rutas["publish_accounts_path"] = {
        "ruta": str(cuentas) if cuentas else None,
        "existe": bool(cuentas and Path(cuentas).is_file()),
    }
    secretos = getattr(ajustes, "publish_secrets_dir", None)
    if secretos:
        directorio = Path(secretos).expanduser()
        rutas["publish_secrets_dir"] = {
            "ruta": str(directorio),
            "existe": directorio.is_dir(),
            "permisos": _permisos(directorio),
            "permisos_esperados": "0o700",
            "token_youtube_presente": (directorio / "youtube_token.json").is_file(),
            "dentro_del_repositorio": str(RAIZ) in str(directorio.resolve())
            if directorio.exists()
            else None,
        }
    else:
        rutas["publish_secrets_dir"] = {"ruta": None, "existe": False}
    informe["rutas"] = rutas

    # --- Disco -----------------------------------------------------------
    base = datos if datos.is_dir() else datos.parent
    try:
        uso = shutil.disk_usage(base)
        libre_mb = uso.free / (1024 * 1024)
    except OSError:  # pragma: no cover
        libre_mb = -1.0
    informe["disco"] = {
        "ruta_medida": str(base),
        "libre_mb": round(libre_mb, 1),
        "min_free_disk_mb": ajustes.min_free_disk_mb,
        "suficiente": libre_mb >= ajustes.min_free_disk_mb,
        "tope_trabajo_render_mib": ajustes.render_max_work_mib,
        "tope_mp4_publicacion_mib": ajustes.publish_max_video_mib,
    }

    # --- Tarifas: desconocido sigue siendo desconocido -------------------
    informe["tarifas"] = {
        "texto_por_1m_tokens_usd": {
            "entrada": ajustes.price_input_per_1m_usd,
            "salida": ajustes.price_output_per_1m_usd,
        },
        "voz_por_1k_caracteres_usd": ajustes.price_voice_per_1k_chars_usd,
        "imagen_por_unidad_usd": ajustes.price_image_per_unit_usd,
        "nota": (
            "null significa DESCONOCIDO: sin tarifa declarada el coste estimado "
            "se queda en null y no se inventa ninguna cifra."
        ),
    }

    # --- Modelos y voz: lo que haya declarado, sin inventar nada ---------
    informe["modelos"] = {
        "OPENAI_MODEL": "definido" if _presente("OPENAI_MODEL") else "SIN DEFINIR",
        "OPENAI_IMAGE_MODEL": (
            "definido" if _presente("OPENAI_IMAGE_MODEL") else "SIN DEFINIR"
        ),
        "ELEVENLABS_MODEL_ID": (
            "definido" if _presente("ELEVENLABS_MODEL_ID") else "SIN DEFINIR"
        ),
        "nota": (
            "No hay valores por defecto a proposito: un modelo de texto no sirve "
            "para imagenes y este proyecto no sustituye uno por otro en silencio."
        ),
    }
    try:
        from viralgen.voice.profiles import get_voice_profile

        perfil_voz = get_voice_profile("infantil_cuentos", ajustes.voice_profiles_path)
        informe["voz"] = {
            "perfil": "infantil_cuentos",
            "voice_id_en_catalogo": perfil_voz.voice_id is not None,
            "ELEVENLABS_VOICE_ID_definido": _presente("ELEVENLABS_VOICE_ID"),
            "resoluble": (
                perfil_voz.voice_id is not None or _presente("ELEVENLABS_VOICE_ID")
            ),
            "nota": (
                "El voice_id es un identificador de TU cuenta de ElevenLabs. El "
                "proyecto no inventa ninguno: si falta, el modulo 2 se detiene."
            ),
        }
    except Exception as exc:
        informe["voz"] = {"error": str(exc)[:200]}

    # --- Verificacion documental por destino ------------------------------
    from viralgen.publish import verification

    informe["verificacion"] = {
        "totales": verification.totals(),
        "bloqueos_por_destino": {
            destino: [e.check_id for e in verification.blocking_for(destino)]
            for destino in ("youtube", "instagram", "staging", "tiktok")
        },
    }

    # --- Veredicto --------------------------------------------------------
    listo_local = bool(
        informe["python"]["cumple"]
        and informe["ffmpeg"]["usable"]
        and informe["tipografia"]["encontrada"]
        and informe["disco"]["suficiente"]
    )
    informe["veredicto"] = {
        "cadena_local_lista": listo_local,
        "falta_configurar": informe["configuracion"]["faltan"],
        "youtube_sin_bloqueos_documentales": (
            informe["verificacion"]["bloqueos_por_destino"]["youtube"] == []
        ),
        "nota": (
            "Esto comprueba presencia, no validez: que una clave exista no dice "
            "que funcione, y este diagnostico no sale a la red."
        ),
    }
    return informe


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json", action="store_true", help="Solo el JSON, sin resumen en stderr."
    )
    args = parser.parse_args(argv)

    informe = diagnosticar()
    json.dump(informe, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")

    if not args.json:
        veredicto = informe.get("veredicto", {})
        print("", file=sys.stderr)
        print(
            "Cadena local (guion -> montaje): "
            + ("lista" if veredicto.get("cadena_local_lista") else "INCOMPLETA"),
            file=sys.stderr,
        )
        faltan = veredicto.get("falta_configurar") or []
        if faltan:
            print("Falta definir: " + ", ".join(faltan), file=sys.stderr)
        else:
            print("No falta ninguna variable obligatoria del piloto.", file=sys.stderr)
        print(
            "Bloqueos documentales de YouTube: "
            + (
                "ninguno"
                if veredicto.get("youtube_sin_bloqueos_documentales")
                else "quedan pendientes"
            ),
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
