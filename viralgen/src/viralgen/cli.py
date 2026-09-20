"""Interfaz de linea de comandos.

Convenio de salidas:

* **stdout**: exclusivamente un resumen JSON (job_id, estado, rutas...). Es lo
  que deben leer los scripts y los modulos siguientes.
* **stderr**: mensajes para personas (registro, avisos, errores).
* **codigo de salida**: familia de error, definida en :class:`ExitCode`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import PROMPT_VERSION, SCHEMA_VERSION, __version__
from .config import Settings, load_settings
from .diskutil import atomic_write_json
from .errors import ConfigError, ExitCode, ViralgenError
from .logging_setup import configure_logging, get_logger
from .pipeline import JobRequest, Pipeline
from .profiles import get_profile, load_profiles
from .schemas.document import ScriptDocument
from .validation import validate_document

PROGRAM = "viralgen"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description=(
            "Modulo 1: genera ideas, guiones y prompts de medios como un plan JSON "
            "validado para los modulos 2-5. No produce medios ni publica nada."
        ),
    )
    parser.add_argument("--version", action="version", version=f"{PROGRAM} {__version__}")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Directorio de datos (por defecto DATA_DIR de la configuracion).",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
        help="Nivel de registro en stderr y en el archivo de log.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    ideas = subparsers.add_parser(
        "ideas", help="Propone ideas sin desarrollar escenas ni guion."
    )
    _add_generation_args(ideas)
    ideas.add_argument(
        "--count", type=int, default=None, help="Numero de ideas a pedir (por defecto 5)."
    )

    generate = subparsers.add_parser(
        "generate", help="Genera el plan completo y exporta un unico script.json."
    )
    _add_generation_args(generate)

    validate = subparsers.add_parser(
        "validate", help="Valida un script.json existente contra el contrato."
    )
    validate.add_argument("--input", type=Path, required=True, help="Ruta del script.json.")

    schema = subparsers.add_parser("schema", help="Exporta el JSON Schema del contrato.")
    schema.add_argument("--output", type=Path, required=True, help="Ruta del esquema a escribir.")

    subparsers.add_parser("profiles", help="Lista los perfiles editoriales disponibles.")
    return parser


def _add_generation_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", required=True, help="Identificador del perfil editorial.")
    parser.add_argument("--topic", default=None, help="Tema o encargo, en espanol.")
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Duracion objetivo en segundos, dentro del rango del perfil.",
    )
    parser.add_argument(
        "--source-pack",
        type=Path,
        default=None,
        help="Catalogo JSON de hechos revisados (obligatorio en curiosidades).",
    )
    parser.add_argument(
        "--job-key",
        default=None,
        help="Clave de idempotencia. Si se omite se genera una nueva.",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Usa el proveedor simulado: sin claves, sin red, simulation=true.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Semilla del proveedor simulado. No promete determinismo en modo real.",
    )


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------


def _emit(payload: dict) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    sys.stdout.flush()


def _run_generation(args: argparse.Namespace, settings: Settings) -> int:
    request = JobRequest(
        command=args.command,
        profile_id=args.profile,
        topic=args.topic,
        duration_s=args.duration,
        source_pack=args.source_pack,
        simulation=bool(args.mock),
        seed=args.seed,
        job_key=args.job_key,
        idea_count=getattr(args, "count", None),
    )
    outcome = Pipeline(settings, request).run()
    _emit(outcome.summary())
    return int(outcome.exit_code)


def _run_validate(args: argparse.Namespace, settings: Settings) -> int:
    logger = get_logger("cli")
    path: Path = args.input
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"No existe el archivo: {path}") from None
    except json.JSONDecodeError as exc:
        raise ConfigError(f"JSON invalido en {path}: {exc}") from None

    try:
        document = ScriptDocument.model_validate(raw)
    except Exception as exc:
        logger.error("El documento no cumple el esquema: %s", exc)
        _emit(
            {
                "input": str(path),
                "schema_valid": False,
                "issues": [{"code": "esquema", "message": str(exc)[:2000], "severity": "fatal"}],
                "exit_code": int(ExitCode.VALIDATION),
            }
        )
        return int(ExitCode.VALIDATION)

    profile = get_profile(document.profile_id, settings.profiles_path)
    report = validate_document(document, profile=profile, allowed_facts=None, promise="")
    exit_code = ExitCode.OK
    if report.fatal:
        exit_code = ExitCode.VALIDATION
    elif report.warnings:
        exit_code = ExitCode.NEEDS_REVIEW
    _emit(
        {
            "input": str(path),
            "schema_valid": True,
            "job_id": document.job_id,
            "profile_id": document.profile_id,
            "simulation": document.simulation,
            "declared_production_status": document.control.production_status.value,
            "issues": [issue.to_dict() for issue in report.issues],
            "exit_code": int(exit_code),
        }
    )
    return int(exit_code)


def _run_schema(args: argparse.Namespace) -> int:
    schema = ScriptDocument.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "viralgen script document"
    schema["description"] = (
        "Contrato JSON del modulo 1. Lo consumen los modulos 2-5. "
        f"schema_version={SCHEMA_VERSION}."
    )
    output: Path = args.output
    atomic_write_json(output, schema)
    _emit(
        {
            "schema_path": str(output),
            "schema_version": SCHEMA_VERSION,
            "prompt_version": PROMPT_VERSION,
            "exit_code": int(ExitCode.OK),
        }
    )
    return int(ExitCode.OK)


def _run_profiles(settings: Settings) -> int:
    profiles = load_profiles(settings.profiles_path)
    _emit(
        {
            "profiles": [
                {
                    "profile_id": profile.profile_id,
                    "channel": profile.channel.value,
                    "target_platforms": [item.value for item in profile.target_platforms],
                    "default_duration_s": profile.default_duration_s,
                    "duration_range_s": [profile.min_duration_s, profile.max_duration_s],
                    "target_wpm": profile.target_wpm,
                    "scenes_range": [profile.min_scenes, profile.max_scenes],
                    "video_scene_budget": profile.video_scene_budget,
                    "requires_evidence": profile.requires_evidence,
                    "made_for_kids": profile.made_for_kids,
                    "series_bible_id": profile.series_bible_id,
                }
                for profile in profiles.profiles
            ],
            "exit_code": int(ExitCode.OK),
        }
    )
    return int(ExitCode.OK)


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    overrides: dict = {}
    if args.data_dir is not None:
        overrides["data_dir"] = args.data_dir
    if args.log_level is not None:
        overrides["log_level"] = args.log_level

    try:
        settings = load_settings(**overrides)
    except ViralgenError as exc:
        print(f"ERROR {exc.code}: {exc.message}", file=sys.stderr)
        _emit({**exc.to_dict(), "exit_code": int(exc.exit_code)})
        return int(exc.exit_code)

    simulation = bool(getattr(args, "mock", False))
    log_dir = settings.effective_data_dir(simulation=simulation) / "logs"
    configure_logging(
        log_dir,
        settings.log_level,
        max_bytes=settings.log_max_bytes,
        backup_count=settings.log_backup_count,
    )
    logger = get_logger("cli")

    try:
        if args.command in {"ideas", "generate"}:
            return _run_generation(args, settings)
        if args.command == "validate":
            return _run_validate(args, settings)
        if args.command == "schema":
            return _run_schema(args)
        if args.command == "profiles":
            return _run_profiles(settings)
        parser.error(f"Comando desconocido: {args.command}")
        return int(ExitCode.USAGE)
    except ViralgenError as exc:
        logger.error("%s: %s", exc.code, exc.message)
        _emit({**exc.to_dict(), "exit_code": int(exc.exit_code)})
        return int(exc.exit_code)
    except KeyboardInterrupt:  # pragma: no cover
        logger.error("Interrumpido por el usuario.")
        return int(ExitCode.UNEXPECTED)
    except Exception as exc:  # pragma: no cover - bug real
        logger.exception("Error inesperado: %s", exc)
        _emit(
            {
                "error_code": "unexpected_error",
                "error_message": str(exc),
                "exit_code": int(ExitCode.UNEXPECTED),
            }
        )
        return int(ExitCode.UNEXPECTED)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
