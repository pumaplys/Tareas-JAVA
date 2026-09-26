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
from .publish.cli import add_auth_parser, add_publish_parser, run_auth, run_publish
from .schemas.document import ScriptDocument
from .validation import check_admission, validate_document

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

    _add_voice_parser(subparsers)
    _add_media_parser(subparsers)
    _add_render_parser(subparsers)
    add_publish_parser(subparsers)
    add_auth_parser(subparsers)
    return parser


def _add_render_parser(subparsers: argparse._SubParsersAction) -> None:
    """Comandos del modulo 4 bajo `viralgen render ...`.

    Ninguno necesita credenciales: se montan archivos que ya existen. Una
    configuracion de proveedores ausente no impide montar.
    """
    render = subparsers.add_parser(
        "render", help="Modulo 4: montaje local, subtitulos y exportacion."
    )
    render_sub = render.add_subparsers(dest="render_command", required=True)

    def entradas(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--script", type=Path, required=True, help="Ruta del script.json del modulo 1."
        )
        parser.add_argument(
            "--voice", type=Path, required=True, help="Ruta del voice.json del modulo 2."
        )
        parser.add_argument(
            "--media", type=Path, required=True, help="Ruta del media.json del modulo 3."
        )

    plan = render_sub.add_parser(
        "plan", help="Preflight local: valida, calcula y estima. No codifica nada."
    )
    entradas(plan)
    plan.add_argument(
        "--preview",
        action="store_true",
        help="Admite las excepciones de ORIGEN que permite el validador preview.",
    )

    generate = render_sub.add_parser(
        "generate", help="Monta el video y exporta render.json."
    )
    entradas(generate)
    generate.add_argument(
        "--render-key", default=None, help="Clave de idempotencia de la ejecucion."
    )
    generate.add_argument(
        "--preview",
        action="store_true",
        help=(
            "Modo preview: admite fuentes simuladas, marca el video como PREVIEW "
            "y NUNCA produce una salida admisible para el publicador."
        ),
    )
    generate.add_argument("--seed", type=int, default=None, help="Semilla reservada.")

    validate = render_sub.add_parser(
        "validate",
        help="Valida el cuarteto guion+voz+medios+render. Solo lectura.",
    )
    entradas(validate)
    validate.add_argument(
        "--manifest", type=Path, required=True, help="Ruta del render.json."
    )
    validate.add_argument(
        "--allow-simulation",
        action="store_true",
        help="Elige el modo del informe y su codigo de salida. No cambia los checks.",
    )

    schema = render_sub.add_parser("schema", help="Exporta el JSON Schema de render.json.")
    schema.add_argument("--output", type=Path, required=True, help="Ruta del esquema.")


def _add_media_parser(subparsers: argparse._SubParsersAction) -> None:
    """Comandos del modulo 3 bajo `viralgen media ...`."""
    media = subparsers.add_parser(
        "media", help="Modulo 3: generacion de medios visuales."
    )
    media_sub = media.add_subparsers(dest="media_command", required=True)

    def entradas(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--script", type=Path, required=True, help="Ruta del script.json del modulo 1."
        )
        parser.add_argument(
            "--voice", type=Path, required=True, help="Ruta del voice.json del modulo 2."
        )

    plan = media_sub.add_parser(
        "plan", help="Preflight sin red ni generacion: valida y enumera lo que haria."
    )
    entradas(plan)
    plan.add_argument("--mock", action="store_true", help="Preflight del recorrido simulado.")

    generate = media_sub.add_parser(
        "generate", help="Genera imagenes y clips y exporta media.json."
    )
    entradas(generate)
    generate.add_argument(
        "--media-key", default=None, help="Clave de idempotencia de la ejecucion."
    )
    generate.add_argument(
        "--mock",
        action="store_true",
        help="Proveedores simulados: sin claves ni red. simulation=true.",
    )
    generate.add_argument("--seed", type=int, default=None, help="Semilla del simulado.")

    validate = media_sub.add_parser(
        "validate",
        help="Valida contrato y admision del trio guion+voz+medios. Solo lectura.",
    )
    entradas(validate)
    validate.add_argument(
        "--manifest", type=Path, required=True, help="Ruta del media.json."
    )
    validate.add_argument(
        "--allow-simulation",
        action="store_true",
        help="Elige el modo preview y su codigo de salida. No cambia los checks.",
    )

    schema = media_sub.add_parser("schema", help="Exporta el JSON Schema de media.json.")
    schema.add_argument("--output", type=Path, required=True, help="Ruta del esquema.")


def _add_voice_parser(subparsers: argparse._SubParsersAction) -> None:
    """Comandos del modulo 2 bajo `viralgen voice ...`."""
    voice = subparsers.add_parser(
        "voice", help="Modulo 2: voz, alineacion temporal y preparacion de sonido."
    )
    voice_sub = voice.add_subparsers(dest="voice_command", required=True)

    generate = voice_sub.add_parser(
        "generate", help="Sintetiza la narracion y exporta voice.json."
    )
    generate.add_argument(
        "--script", type=Path, required=True, help="Ruta del script.json del modulo 1."
    )
    generate.add_argument(
        "--voice-key", default=None, help="Clave de idempotencia de la ejecucion."
    )
    generate.add_argument(
        "--mock",
        action="store_true",
        help="Proveedor simulado: sin claves, sin red y sin FFmpeg. simulation=true.",
    )
    generate.add_argument(
        "--seed", type=int, default=None, help="Semilla del proveedor simulado."
    )

    validate = voice_sub.add_parser(
        "validate",
        help="Valida contrato y admision de una pareja guion + manifiesto. No genera audio.",
    )
    validate.add_argument("--script", type=Path, required=True, help="Ruta del script.json.")
    validate.add_argument("--manifest", type=Path, required=True, help="Ruta del voice.json.")
    validate.add_argument(
        "--allow-simulation",
        action="store_true",
        help="Audita un recorrido simulado sin exigir origen real.",
    )

    schema = voice_sub.add_parser("schema", help="Exporta el JSON Schema del manifiesto de voz.")
    schema.add_argument("--output", type=Path, required=True, help="Ruta del esquema a escribir.")


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
    # El archivo se leyo entero y valido contra el esquema, asi que la
    # exportacion consta como completa para el criterio de admision.
    admission = check_admission(document, export_complete=True, report=report)
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
            **admission.to_dict(),
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


def _run_voice(args: argparse.Namespace, settings: Settings) -> int:
    from .voice import VOICE_SCHEMA_VERSION
    from .voice.admission import check_voice_admission
    from .voice.pipeline import VoiceJobRequest, VoicePipeline
    from .voice.schemas import VoiceManifest

    if args.voice_command == "generate":
        peticion = VoiceJobRequest(
            script_path=args.script,
            voice_key=args.voice_key,
            simulation=bool(args.mock),
            seed=args.seed,
        )
        resultado = VoicePipeline(settings, peticion).run()
        _emit(resultado.summary())
        return int(resultado.exit_code)

    if args.voice_command == "validate":
        # Solo lectura: no toca script.json ni voice.json.
        informe = check_voice_admission(
            script_path=args.script, manifest_path=args.manifest, settings=settings
        )
        # Tres veredictos independientes. `--allow-simulation` cambia SOLO cual
        # de ellos decide el codigo de salida; el resumen los lleva los tres, y
        # `admissible_for_assembly` sigue siendo false para una simulacion.
        modo = "preview" if args.allow_simulation else "production"
        veredicto = (
            informe.admissible_for_preview
            if args.allow_simulation
            else informe.admissible_for_assembly
        )
        if not informe.contract_valid:
            codigo = ExitCode.VALIDATION
        else:
            codigo = ExitCode.OK if veredicto else ExitCode.NEEDS_REVIEW
        _emit(
            {
                "script": str(args.script),
                "manifest": str(args.manifest),
                "mode": modo,
                **informe.to_dict(),
                "exit_code": int(codigo),
                "note": (
                    "El codigo de salida de una validacion en modo preview NO autoriza "
                    "produccion: los consumidores reales deben leer admissible_for_assembly."
                ),
            }
        )
        return int(codigo)

    if args.voice_command == "schema":
        schema = VoiceManifest.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["title"] = "viralgen voice manifest"
        schema["description"] = (
            "Manifiesto lateral de voz del modulo 2. Fuente de tiempos medidos para "
            f"los modulos 3 y 4. schema_version={VOICE_SCHEMA_VERSION}."
        )
        atomic_write_json(args.output, schema)
        _emit(
            {
                "schema_path": str(args.output),
                "document_type": "voice_manifest",
                "schema_version": VOICE_SCHEMA_VERSION,
                "exit_code": int(ExitCode.OK),
            }
        )
        return int(ExitCode.OK)

    raise ConfigError(f"Subcomando de voz desconocido: {args.voice_command}")


def _run_media(args: argparse.Namespace, settings: Settings) -> int:
    from .media import MEDIA_SCHEMA_VERSION
    from .media.admission import check_media_admission
    from .media.pipeline import MediaJobRequest, MediaPipeline
    from .media.schemas import MediaManifest

    if args.media_command == "schema":
        schema = MediaManifest.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["title"] = "viralgen media manifest"
        schema["description"] = (
            "Manifiesto lateral de medios visuales del modulo 3. "
            f"schema_version={MEDIA_SCHEMA_VERSION}."
        )
        atomic_write_json(args.output, schema)
        _emit(
            {
                "schema_path": str(args.output),
                "document_type": "media_manifest",
                "schema_version": MEDIA_SCHEMA_VERSION,
                "exit_code": int(ExitCode.OK),
            }
        )
        return int(ExitCode.OK)

    if args.media_command == "validate":
        # Solo lectura: no toca ningun byte ni el historial.
        informe = check_media_admission(
            script_path=args.script,
            voice_path=args.voice,
            manifest_path=args.manifest,
            settings=settings,
        )
        modo = "preview" if args.allow_simulation else "production"
        veredicto = (
            informe.admissible_for_preview
            if args.allow_simulation
            else informe.admissible_for_assembly
        )
        if not informe.contract_valid:
            codigo = ExitCode.VALIDATION
        else:
            codigo = ExitCode.OK if veredicto else ExitCode.NEEDS_REVIEW
        _emit(
            {
                "script": str(args.script),
                "voice": str(args.voice),
                "manifest": str(args.manifest),
                "mode": modo,
                **informe.to_dict(),
                "exit_code": int(codigo),
                "note": (
                    "El codigo de salida en modo preview NO autoriza produccion: los "
                    "consumidores reales deben leer admissible_for_assembly."
                ),
            }
        )
        return int(codigo)

    peticion = MediaJobRequest(
        script_path=args.script,
        voice_path=args.voice,
        media_key=getattr(args, "media_key", None),
        simulation=bool(args.mock),
        seed=getattr(args, "seed", None),
    )

    if args.media_command == "plan":
        resultado = MediaPipeline(settings, peticion).preflight()
        _emit(resultado)
        return int(resultado["exit_code"])

    resultado = MediaPipeline(settings, peticion).run()
    _emit(resultado.summary())
    return int(resultado.exit_code)


def _run_render(args: argparse.Namespace, settings: Settings) -> int:
    """Modulo 4. `stdout` lleva el resumen JSON; `stderr`, los mensajes humanos."""
    from .render import RENDER_SCHEMA_VERSION
    from .render.admission import check_render_admission
    from .render.pipeline import RenderJobRequest, RenderPipeline
    from .render.schemas import RenderManifest

    if args.render_command == "schema":
        schema = RenderManifest.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["title"] = "viralgen render manifest"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(args.output, schema)
        _emit(
            {
                "schema_path": str(args.output),
                "document_type": "render_manifest",
                "schema_version": RENDER_SCHEMA_VERSION,
                "exit_code": int(ExitCode.OK),
            }
        )
        return int(ExitCode.OK)

    if args.render_command == "validate":
        informe = check_render_admission(
            script_path=args.script,
            voice_path=args.voice,
            media_path=args.media,
            manifest_path=args.manifest,
            settings=settings,
        )
        preview = bool(args.allow_simulation)
        aprobado = (
            informe.admissible_for_preview if preview
            else informe.admissible_for_publisher
        )
        codigo = ExitCode.OK if aprobado else ExitCode.NEEDS_REVIEW
        _emit(
            {
                "script": str(args.script),
                "voice": str(args.voice),
                "media": str(args.media),
                "manifest": str(args.manifest),
                "mode": "preview" if preview else "publisher",
                **informe.to_dict(),
                "exit_code": int(codigo),
            }
        )
        return int(codigo)

    peticion = RenderJobRequest(
        script_path=args.script,
        voice_path=args.voice,
        media_path=args.media,
        render_key=getattr(args, "render_key", None),
        preview=bool(getattr(args, "preview", False)),
        seed=getattr(args, "seed", None),
    )

    if args.render_command == "plan":
        resultado = RenderPipeline(settings, peticion).preflight()
        _emit(resultado)
        return int(resultado["exit_code"])

    resultado = RenderPipeline(settings, peticion).run()
    _emit(resultado.summary())
    return int(resultado.exit_code)


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
        if args.command == "voice":
            return _run_voice(args, settings)
        if args.command == "media":
            return _run_media(args, settings)
        if args.command == "render":
            return _run_render(args, settings)
        if args.command == "publish":
            return run_publish(args, settings)
        if args.command == "auth":
            return run_auth(args, settings)
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
