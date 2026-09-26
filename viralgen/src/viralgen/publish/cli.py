"""Comandos `publish` y `auth`.

Convenio heredado del resto del proyecto: stdout lleva EXCLUSIVAMENTE un JSON
estructurado, stderr lleva los mensajes para personas y el codigo de salida
dice como fue el COMANDO. Que un comando salga con 0 no significa que se haya
publicado nada: eso lo dicen el estado y la evidencia de cada destino.

Mapa de comandos:

* `publish plan`          - comprueba en local y escribe el borrador editorial.
* `publish accounts check`- comprobacion remota explicita de cuenta y acceso.
* `publish approve`       - registra la autorizacion de una revision concreta.
* `publish enqueue`       - persiste horario y destinos autorizados.
* `publish worker --once` - procesa lo vencido y deja checkpoints.
* `publish status`        - estado local; `--refresh` consulta en remoto.
* `publish cancel`        - cancelacion local, con sus limites.
* `publish export-manual` / `record-manual` - flujo de TikTok.
* `publish validate` / `schema` - contratos y admision local.
* `publish gc`            - limpieza de lo que creo este modulo.
* `auth youtube` / `auth instagram` - conexion de credenciales.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from ..errors import ConfigError, DocumentValidationError, ExitCode
from ..logging_setup import get_logger
from ..schemas.common import Platform
from ..storage import ProcessLock, Storage, iso
from .accounts import load_accounts
from .admission import check_publication_admission, require_mode
from .authorize import build_authorization, find_authorization, identity_space, store_authorization
from .clock import ManualClock, SystemClock, ensure_zone
from .plan import (
    DestinationRequest,
    build_publication_plan,
    load_plan,
    normalize_plan,
    refresh_plan,
    write_plan,
)
from .providers.tiktok import build_manual_report
from .queue import enqueue_plan, publication_id_for, request_cancel
from .receipt import build_receipt, write_receipt
from .schemas import DestinationState, PublishMode, Visibility, schema_documents
from .secrets import SecretStore, require_secret_store
from .storage import PublishStorage
from .verification import describe_all
from .worker import PublishWorker

logger = get_logger("publish.cli")


def _emit(payload: dict) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    sys.stdout.flush()


def _aviso(mensaje: str) -> None:
    print(mensaje, file=sys.stderr)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

DESTINO_AYUDA = (
    "Destino en forma clave=valor separada por comas: "
    "id=yt,platform=youtube_shorts,account=canal_demo,"
    "at=2026-09-24T18:30:00,tz=Europe/Madrid,visibility=private"
    "[,notify=false][,share=true][,fold=0]. Repetible."
)


def add_publish_parser(subparsers: argparse._SubParsersAction) -> None:
    publish = subparsers.add_parser(
        "publish",
        help="Publicacion y programacion (modulo 5). No publica sin autorizacion.",
    )
    sub = publish.add_subparsers(dest="publish_command", required=True)

    plan = sub.add_parser("plan", help="Valida en local y escribe el borrador.")
    for nombre in ("script", "voice", "media", "render"):
        plan.add_argument(f"--{nombre}", type=Path, required=True)
    plan.add_argument("--destination", action="append", required=True, help=DESTINO_AYUDA)
    plan.add_argument(
        "--mode", choices=["plan", "mock", "real"], default="plan",
        help="Elegir modo NO cambia lo que es admisible: solo que se exige.",
    )
    plan.add_argument("--publish-key", required=True, help="Identidad de la intencion.")
    plan.add_argument("--out", type=Path, default=None)
    plan.add_argument("--accounts", type=Path, default=None, help="Catalogo de cuentas.")

    cuentas = sub.add_parser(
        "accounts", help="Comprobaciones de cuenta. No publica nada."
    )
    cuentas_sub = cuentas.add_subparsers(dest="accounts_command", required=True)
    check = cuentas_sub.add_parser("check", help="Comprueba identidad y acceso.")
    check.add_argument("--plan", type=Path, required=True)
    check.add_argument("--destination", default=None)

    approve = sub.add_parser("approve", help="Autoriza una revision concreta.")
    approve.add_argument("--plan", type=Path, required=True)
    approve.add_argument("--operator", required=True, help="Identidad local del operador.")

    enqueue = sub.add_parser("enqueue", help="Persiste horario y destinos autorizados.")
    enqueue.add_argument("--plan", type=Path, required=True)

    worker = sub.add_parser("worker", help="Procesa lo vencido y termina.")
    worker.add_argument("--once", action="store_true", required=True)
    worker.add_argument("--publish-key", default=None)
    worker.add_argument("--mode", choices=["mock", "real"], default="mock")
    worker.add_argument(
        "--now",
        default=None,
        help=(
            "Reloj fijado, solo para ensayos. Se rechaza si hay algun destino "
            "real vencido: no sirve para saltarse la ventana de una entrega real."
        ),
    )

    status = sub.add_parser("status", help="Estado local del trabajo.")
    status.add_argument("--publish-key", required=True)
    status.add_argument("--mode", choices=["mock", "real"], default="mock")
    status.add_argument("--refresh", action="store_true", help="Consulta en remoto.")
    status.add_argument("--out", type=Path, default=None, help="Escribe publication.json.")

    cancel = sub.add_parser("cancel", help="Cancela en local lo que aun no ha salido.")
    cancel.add_argument("--publish-key", required=True)
    cancel.add_argument("--mode", choices=["mock", "real"], default="mock")
    cancel.add_argument("--destination", required=True)
    cancel.add_argument("--note", default="")

    exportar = sub.add_parser("export-manual", help="Paquete para publicar a mano.")
    exportar.add_argument("--publish-key", required=True)
    exportar.add_argument("--mode", choices=["mock", "real"], default="mock")
    exportar.add_argument("--destination", required=True)
    exportar.add_argument("--out", type=Path, default=None)

    registrar = sub.add_parser("record-manual", help="Registra lo publicado a mano.")
    registrar.add_argument("--publish-key", required=True)
    registrar.add_argument("--mode", choices=["mock", "real"], default="mock")
    registrar.add_argument("--destination", required=True)
    registrar.add_argument("--url", default=None)
    registrar.add_argument("--remote-id", default=None)
    registrar.add_argument("--at", default=None, help="Fecha de publicacion (ISO, con zona).")

    validate = sub.add_parser("validate", help="Recalcula contratos y admision.")
    validate.add_argument("--plan", type=Path, required=True)

    esquema = sub.add_parser("schema", help="Exporta los JSON Schema del modulo.")
    esquema.add_argument("--out", type=Path, default=None)

    gc = sub.add_parser("gc", help="Limpieza de temporales y objetos propios.")
    gc.add_argument("--apply", action="store_true", help="Sin esto, solo enumera.")


def add_auth_parser(subparsers: argparse._SubParsersAction) -> None:
    auth = subparsers.add_parser("auth", help="Conexion de credenciales del modulo 5.")
    sub = auth.add_subparsers(dest="auth_command", required=True)

    youtube = sub.add_parser("youtube", help="Conecta el canal (loopback + PKCE).")
    youtube.add_argument(
        "--open-browser", action="store_true", help="Abre el navegador automaticamente."
    )
    youtube.add_argument("--timeout", type=float, default=300.0)

    instagram = sub.add_parser(
        "instagram", help="Importa un token obtenido del flujo oficial de Meta."
    )
    instagram.add_argument(
        "--from-file",
        type=Path,
        required=True,
        help=(
            "Archivo JSON con access_token y, si se conoce, expires_at. No se "
            "acepta el token como argumento: quedaria en el historial."
        ),
    )


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def _parse_destination(texto: str) -> DestinationRequest:
    partes = dict(
        trozo.split("=", 1) for trozo in texto.split(",") if "=" in trozo
    )
    faltan = [c for c in ("id", "platform", "account", "at", "tz") if c not in partes]
    if faltan:
        raise ConfigError(
            f"Al destino {texto!r} le faltan claves: {', '.join(faltan)}. {DESTINO_AYUDA}"
        )
    try:
        plataforma = Platform(partes["platform"].strip())
    except ValueError as exc:
        raise ConfigError(
            f"Plataforma desconocida: {partes['platform']!r}. "
            f"Opciones: {[p.value for p in Platform]}"
        ) from exc
    visibilidad = Visibility(partes.get("visibility", "public").strip())
    ensure_zone(partes["tz"].strip())
    return DestinationRequest(
        destination_id=partes["id"].strip(),
        platform=plataforma,
        account_alias=partes["account"].strip(),
        local_time=partes["at"].strip(),
        timezone=partes["tz"].strip(),
        visibility=visibilidad,
        fold=int(partes["fold"]) if "fold" in partes else None,
        notify_subscribers=(
            partes["notify"].strip().lower() == "true" if "notify" in partes else False
        ),
        share_to_feed=(
            partes["share"].strip().lower() == "true" if "share" in partes else True
        ),
    )


def _storage(settings: Any, *, mode: PublishMode) -> tuple[Storage, PublishStorage]:
    base = settings.effective_data_dir(simulation=mode is not PublishMode.REAL)
    almacenamiento = Storage(base)
    publicacion = PublishStorage(almacenamiento)
    publicacion.migrate()
    return almacenamiento, publicacion


def _secrets(settings: Any, *, mode: PublishMode) -> SecretStore | None:
    if mode is not PublishMode.REAL:
        directorio = getattr(settings, "publish_secrets_dir", None)
        return SecretStore(Path(directorio)) if directorio else None
    return require_secret_store(settings)


def _plan_por_defecto(settings: Any, clave: str, mode: PublishMode) -> Path:
    base = settings.effective_data_dir(simulation=mode is not PublishMode.REAL)
    return base / "publicaciones" / clave / "publication_plan.json"


def _admision_del_plan(plan, settings: Any):
    """Revalida la cadena desde los archivos que el plan declara."""
    return check_publication_admission(
        script_path=Path(plan.sources.script.path),
        voice_path=Path(plan.sources.voice.path),
        media_path=Path(plan.sources.media.path),
        manifest_path=Path(plan.sources.render.path),
        settings=settings,
        targets=[destino.platform for destino in plan.destinations],
    )


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------


def _cmd_plan(args: argparse.Namespace, settings: Any) -> int:
    modo = PublishMode(args.mode)
    peticiones = [_parse_destination(texto) for texto in args.destination]
    cuentas = load_accounts(settings, path=args.accounts)

    admision = check_publication_admission(
        script_path=args.script,
        voice_path=args.voice,
        media_path=args.media,
        manifest_path=args.render,
        settings=settings,
        targets=[peticion.platform for peticion in peticiones],
    )
    if admision.sources is None:
        _emit(
            {
                "command": "publish plan",
                "admission": admision.to_dict(),
                "plan_written": False,
                "exit_code": int(ExitCode.VALIDATION),
            }
        )
        return int(ExitCode.VALIDATION)

    plan = build_publication_plan(
        admission=admision,
        script_path=args.script,
        requests=peticiones,
        accounts=cuentas,
        mode=modo,
        publish_key=args.publish_key,
        settings=settings,
    )
    destino = args.out or _plan_por_defecto(settings, args.publish_key, modo)
    firma = write_plan(destino, plan)

    pendientes = [
        {
            "destination_id": d.destination_id,
            "state": d.state.value,
            "requirements": [r.model_dump(mode="json") for r in d.pending_requirements],
        }
        for d in plan.destinations
    ]
    bloqueado = any(
        d.state is DestinationState.BLOCKED for d in plan.destinations
    )
    codigo = ExitCode.NEEDS_REVIEW if bloqueado else ExitCode.OK
    _aviso(f"Plan escrito en {destino}")
    _emit(
        {
            "command": "publish plan",
            "mode": modo.value,
            "publish_key": plan.publish_key,
            "plan_path": str(destino),
            "plan_sha256": firma,
            "plan_revision": plan.revision,
            "intent_fingerprint": plan.intent_fingerprint,
            "admission": plan.admission.model_dump(mode="json"),
            "destinations": pendientes,
            "pending_verification": [
                p.describe() for p in admision.blocking_verification()
            ],
            "readable_view": plan.readable_view,
            "external_effects": False,
            "note": (
                "Un plan no publica ni transfiere nada. Editalo si falta algo y "
                "despues autorizalo con `publish approve`."
            ),
            "exit_code": int(codigo),
        }
    )
    return int(codigo)


def _cmd_validate(args: argparse.Namespace, settings: Any) -> int:
    """Recalcula TODO: no se fia de lo que el archivo declare de si mismo."""
    plan, firma = load_plan(args.plan)
    normalizado, cambio = normalize_plan(plan)
    admision = _admision_del_plan(plan, settings)
    recalculado = refresh_plan(normalizado, admision)
    _emit(
        {
            "command": "publish validate",
            "destinations": [
                {
                    "destination_id": d.destination_id,
                    "state": d.state.value,
                    "text_source": d.metadata.text_source.value,
                    "requirements": [
                        r.model_dump(mode="json") for r in d.pending_requirements
                    ],
                }
                for d in recalculado.destinations
            ],
            "plan_path": str(args.plan),
            "plan_sha256": firma,
            "schema_valid": True,
            "fingerprint_declared": plan.intent_fingerprint,
            "fingerprint_recomputed": normalizado.intent_fingerprint,
            "fingerprint_matches": not cambio,
            "admission": admision.to_dict(),
            "inputs_unchanged": True,
            "note": (
                "Las comprobaciones se recalculan sobre los archivos reales. Un "
                "recibo editado no concede autorizacion ni demuestra exito remoto."
            ),
            "exit_code": int(
                ExitCode.OK if admision.contract_valid else ExitCode.VALIDATION
            ),
        }
    )
    return int(ExitCode.OK if admision.contract_valid else ExitCode.VALIDATION)


def _cmd_schema(args: argparse.Namespace) -> int:
    esquemas = schema_documents()
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        for nombre, contenido in esquemas.items():
            (args.out / f"{nombre}.schema.json").write_text(
                json.dumps(contenido, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        _emit(
            {
                "command": "publish schema",
                "written": sorted(str(args.out / f"{n}.schema.json") for n in esquemas),
                "exit_code": int(ExitCode.OK),
            }
        )
        return int(ExitCode.OK)
    _emit({"command": "publish schema", "schemas": esquemas, "exit_code": int(ExitCode.OK)})
    return int(ExitCode.OK)


def _cmd_approve(args: argparse.Namespace, settings: Any) -> int:
    plan, _ = load_plan(args.plan)
    normalizado, cambio = normalize_plan(plan)
    if cambio:
        _aviso(
            "El plan se edito despues de generarse: se autoriza la intencion "
            f"actual como revision {normalizado.revision}."
        )
    firma = write_plan(args.plan, normalizado)

    admision = _admision_del_plan(normalizado, settings)
    normalizado = refresh_plan(normalizado, admision)
    firma = write_plan(args.plan, normalizado)
    require_mode(admision, normalizado.mode)
    bloqueados = [
        d.destination_id
        for d in normalizado.destinations
        if d.state is DestinationState.BLOCKED
    ]
    if bloqueados:
        raise DocumentValidationError(
            "Hay destinos con requisitos sin resolver: " + ", ".join(bloqueados),
            details={"blocked": bloqueados},
        )

    _, publicacion = _storage(settings, mode=normalizado.mode)
    identificador = publication_id_for(normalizado.publish_key, mode=normalizado.mode)
    registro = build_authorization(
        normalizado,
        plan_sha256=firma,
        operator_identity=args.operator,
        clock=SystemClock(),
    )
    store_authorization(publicacion, publication_id=identificador, registro=registro)
    _emit(
        {
            "command": "publish approve",
            "publication_id": identificador,
            "publish_key": normalizado.publish_key,
            "mode": normalizado.mode.value,
            "plan_revision": normalizado.revision,
            "plan_sha256": firma,
            "authorization": registro.model_dump(mode="json"),
            "note": (
                "Registro del operador de esta instalacion. Cubre tambien subir "
                "el MP4 al almacenamiento temporal cuando el destino lo necesite. "
                "No certifica la veracidad del contenido."
            ),
            "exit_code": int(ExitCode.OK),
        }
    )
    return int(ExitCode.OK)


def _cmd_enqueue(args: argparse.Namespace, settings: Any) -> int:
    plan, firma = load_plan(args.plan)
    normalizado, _ = normalize_plan(plan)
    admision = _admision_del_plan(normalizado, settings)
    normalizado = refresh_plan(normalizado, admision)
    require_mode(admision, normalizado.mode)

    _, publicacion = _storage(settings, mode=normalizado.mode)
    identificador = publication_id_for(normalizado.publish_key, mode=normalizado.mode)
    resultado = enqueue_plan(
        publicacion,
        plan=normalizado,
        plan_sha256=firma,
        publication_id=identificador,
        settings=settings,
    )
    _emit(
        {
            "command": "publish enqueue",
            "publication_id": identificador,
            "publish_key": normalizado.publish_key,
            "mode": normalizado.mode.value,
            "identity_space": resultado.publication["identity_space"],
            "destinations": [
                {
                    "destination_id": fila["destination_id"],
                    "state": fila["state"],
                    "scheduled_at": fila["scheduled_at"],
                    "timezone": fila["timezone"],
                }
                for fila in resultado.destinations
            ],
            "note": (
                "Programacion LOCAL: a la hora autorizada empieza la entrega. No "
                "es una promesa de visibilidad publica en ese segundo."
            ),
            "exit_code": int(ExitCode.OK),
        }
    )
    return int(ExitCode.OK)


def _cmd_worker(args: argparse.Namespace, settings: Any) -> int:
    modo = PublishMode(args.mode)
    almacenamiento, publicacion = _storage(settings, mode=modo)
    reloj = SystemClock()
    if args.now:
        momento = datetime.fromisoformat(args.now.replace("Z", "+00:00"))
        if momento.tzinfo is None:
            raise ConfigError("--now necesita zona horaria explicita")
        pendientes = publicacion.due_destinations(now=momento)
        if any(not fila["simulation"] for fila in pendientes):
            raise ConfigError(
                "--now no se acepta cuando hay destinos reales vencidos: el reloj "
                "de ensayo no sirve para saltarse la ventana de una entrega real."
            )
        _aviso(f"Reloj fijado en {iso(momento)} (ensayo).")
        reloj = ManualClock(momento)

    identificador = (
        publication_id_for(args.publish_key, mode=modo) if args.publish_key else None
    )
    with ProcessLock(almacenamiento.data_dir / "publish.lock"):
        trabajador = PublishWorker(
            storage=publicacion,
            settings=settings,
            clock=reloj,
            secrets=_secrets(settings, mode=modo),
        )
        informe = trabajador.run_once(publication_id=identificador)
    pendiente = any(
        salida.state in ("dispatching", "waiting_remote") for salida in informe.outcomes
    )
    codigo = ExitCode.WAITING_REMOTE if pendiente else ExitCode.OK
    _emit({"command": "publish worker", **informe.describe(), "exit_code": int(codigo)})
    return int(codigo)


def _cmd_status(args: argparse.Namespace, settings: Any) -> int:
    modo = PublishMode(args.mode)
    _, publicacion = _storage(settings, mode=modo)
    identificador = publication_id_for(args.publish_key, mode=modo)
    if publicacion.get_publication(identificador) is None:
        raise ConfigError(
            f"No hay ningun trabajo con clave {args.publish_key!r} en el espacio "
            f"{identity_space(modo)}."
        )
    if args.refresh:
        _aviso("Consultando estado remoto de los destinos en curso...")
        trabajador = PublishWorker(
            storage=publicacion,
            settings=settings,
            secrets=_secrets(settings, mode=modo),
        )
        trabajador.refresh(publication_id=identificador)

    recibo = build_receipt(publicacion, publication_id=identificador, settings=settings)
    salida = None
    if args.out:
        salida = str(args.out)
        write_receipt(args.out, recibo)
    codigo = {
        "delivered": ExitCode.OK,
        "draft": ExitCode.OK,
        "scheduled": ExitCode.OK,
        "cancelled": ExitCode.OK,
        "in_progress": ExitCode.WAITING_REMOTE,
        "partial": ExitCode.NEEDS_REVIEW,
        "needs_attention": ExitCode.NEEDS_REVIEW,
        "failed": ExitCode.PROVIDER,
    }[recibo.summary.overall.value]
    _emit(
        {
            "command": "publish status",
            "publication_id": identificador,
            "receipt_path": salida,
            "receipt": recibo.model_dump(mode="json"),
            "exit_code": int(codigo),
        }
    )
    return int(codigo)


def _cmd_cancel(args: argparse.Namespace, settings: Any) -> int:
    modo = PublishMode(args.mode)
    _, publicacion = _storage(settings, mode=modo)
    identificador = publication_id_for(args.publish_key, mode=modo)
    fila = request_cancel(
        publicacion,
        publication_id=identificador,
        destination_id=args.destination,
        now=SystemClock().now(),
        note=args.note,
    )
    detenido = fila["state"] == DestinationState.CANCELLED.value
    _emit(
        {
            "command": "publish cancel",
            "destination_id": args.destination,
            "state": fila["state"],
            "stopped_locally": detenido,
            "note": fila["cancel_note"],
            "remote_publication_management": (
                "fuera del alcance de este MVP: no se retira ni se borra nada en "
                "la plataforma"
            ),
            "exit_code": int(ExitCode.OK if detenido else ExitCode.NEEDS_REVIEW),
        }
    )
    return int(ExitCode.OK if detenido else ExitCode.NEEDS_REVIEW)


def _cmd_export_manual(args: argparse.Namespace, settings: Any) -> int:
    from .providers.tiktok import TikTokManualAdapter

    modo = PublishMode(args.mode)
    _, publicacion = _storage(settings, mode=modo)
    identificador = publication_id_for(args.publish_key, mode=modo)
    trabajo = publicacion.get_publication(identificador)
    if trabajo is None:
        raise ConfigError(f"No hay trabajo con clave {args.publish_key!r}.")
    fila = publicacion.get_destination(identificador, args.destination)
    if fila is None:
        raise ConfigError(f"El trabajo no tiene el destino {args.destination!r}.")

    from .plan import PublicationPlan

    plan = PublicationPlan.model_validate_json(trabajo["plan_json"])
    destino_plan = next(
        d for d in plan.destinations if d.destination_id == args.destination
    )
    if modo is PublishMode.REAL:
        admision = _admision_del_plan(plan, settings)
        require_mode(admision, modo)
        from .authorize import require_authorization

        require_authorization(publicacion, publication_id=identificador, plan=plan)

    raiz = args.out or (
        settings.effective_data_dir(simulation=modo is not PublishMode.REAL)
        / "publicaciones"
        / "tiktok"
    )
    adaptador = TikTokManualAdapter(settings=settings, export_root=raiz)
    trabajador = PublishWorker(
        storage=publicacion,
        settings=settings,
        secrets=_secrets(settings, mode=modo),
        export_root=raiz,
        adapter_factory=lambda *_a, **_k: adaptador,
    )
    resultado = trabajador.step_now(
        publication_id=identificador, destination_id=args.destination, step="start"
    )
    _emit(
        {
            "command": "publish export-manual",
            "destination_id": args.destination,
            "state": resultado["state"],
            "package": json.loads(resultado["manual_export_json"] or "{}"),
            "published": False,
            "simulation": bool(trabajo["simulation"]),
            "note": (
                "Exportar no es publicar. Publica desde las herramientas "
                "oficiales de TikTok y despues usa `publish record-manual`."
                + (
                    " Este paquete es una SIMULACION y no debe publicarse."
                    if trabajo["simulation"]
                    else ""
                )
            ),
            "exit_code": int(ExitCode.OK),
        }
    )
    return int(ExitCode.OK)


def _cmd_record_manual(args: argparse.Namespace, settings: Any) -> int:
    if not args.url and not args.remote_id:
        raise ConfigError("Indica al menos --url o --remote-id.")
    modo = PublishMode(args.mode)
    _, publicacion = _storage(settings, mode=modo)
    identificador = publication_id_for(args.publish_key, mode=modo)
    ahora = SystemClock().now()
    momento = (
        datetime.fromisoformat(args.at.replace("Z", "+00:00")) if args.at else ahora
    )
    if momento.tzinfo is None:
        raise ConfigError("--at necesita zona horaria explicita")
    reporte = build_manual_report(
        url=args.url, remote_id=args.remote_id, reported_at=momento, now=ahora
    )
    fila = publicacion.update_destination(
        identificador,
        args.destination,
        state=DestinationState.MANUALLY_REPORTED,
        manual_report_json=reporte.model_dump_json(),
    )
    publicacion.record_event(
        publication_id=identificador,
        destination_id=args.destination,
        kind="manual_report",
        state=fila["state"],
        detail={"evidence_source": "operator_reported"},
    )
    _emit(
        {
            "command": "publish record-manual",
            "destination_id": args.destination,
            "state": fila["state"],
            "evidence_source": "operator_reported",
            "api_confirmed": False,
            "note": (
                "Dato aportado por una persona. No se ha verificado con ninguna "
                "API y no se ha generado ningun identificador remoto."
            ),
            "exit_code": int(ExitCode.OK),
        }
    )
    return int(ExitCode.OK)


def _cmd_accounts_check(args: argparse.Namespace, settings: Any) -> int:
    from .worker import build_adapter

    plan, _ = load_plan(args.plan)
    modo = plan.mode
    _, publicacion = _storage(settings, mode=modo)
    secretos = _secrets(settings, mode=modo)
    resultados = []
    codigo = ExitCode.OK
    for destino in plan.destinations:
        if args.destination and destino.destination_id != args.destination:
            continue
        adaptador = build_adapter(
            destino.platform,
            modo,
            settings=settings,
            export_root=Path(settings.data_dir) / "publicaciones",
        )
        ctx = _contexto_minimo(plan, destino, modo, settings, secretos)
        comprobacion = adaptador.check_account(ctx)
        if not comprobacion.ok:
            codigo = ExitCode.CONFIG
        resultados.append(
            {
                "destination_id": destino.destination_id,
                "platform": destino.platform.value,
                "expected_account_id": destino.account.expected_account_id,
                "ok": comprobacion.ok,
                "observed_account_id": comprobacion.observed_account_id,
                "candidates": comprobacion.candidates,
                "detail": comprobacion.detail,
            }
        )
    _emit(
        {
            "command": "publish accounts check",
            "mode": modo.value,
            "results": resultados,
            "published": False,
            "exit_code": int(codigo),
        }
    )
    return int(codigo)


def _contexto_minimo(plan, destino, modo, settings, secretos):
    from .providers.base import DispatchContext

    return DispatchContext(
        publication_id=publication_id_for(plan.publish_key, mode=modo),
        destination_id=destino.destination_id,
        platform=destino.platform,
        account_alias=destino.account.alias,
        expected_account_id=destino.account.expected_account_id,
        metadata=destino.metadata,
        requested_visibility=destino.requested_visibility,
        options=destino.options,
        video_path=Path(plan.sources.video.path),
        video_sha256=plan.sources.video.sha256,
        video_size=plan.sources.video.size_bytes,
        mode=modo,
        row={"remote_refs_json": "{}", "attempts": 0},
        now=SystemClock().now(),
        settings=settings,
        secrets=secretos,
    )


def _cmd_gc(args: argparse.Namespace, settings: Any) -> int:
    from .staging import plan_cleanup

    resultados = []
    for modo in (PublishMode.MOCK, PublishMode.REAL):
        _, publicacion = _storage(settings, mode=modo)
        candidatos, conservados = plan_cleanup(publicacion, now=SystemClock().now())
        borrados: list[str] = []
        if args.apply and candidatos and modo is PublishMode.REAL:
            from .staging import S3Staging, load_staging_config

            almacen = S3Staging(load_staging_config(settings))
            for candidato in candidatos:
                almacen.delete(candidato.object_key)
                publicacion.mark_staging_deleted(
                    bucket_alias=candidato.bucket_alias,
                    object_key=candidato.object_key,
                    at=SystemClock().now(),
                )
                borrados.append(candidato.object_key)
        resultados.append(
            {
                "identity_space": identity_space(modo),
                "candidates": [
                    {
                        "object_key": c.object_key,
                        "size_bytes": c.size_bytes,
                        "reason": c.reason,
                    }
                    for c in candidatos
                ],
                "retained": conservados,
                "deleted": borrados,
            }
        )
    _emit(
        {
            "command": "publish gc",
            "dry_run": not args.apply,
            "scopes": resultados,
            "never_deletes": (
                "guiones, voces, medios, renders y recibos. Solo se limpian "
                "temporales y objetos remotos creados por este modulo."
            ),
            "exit_code": int(ExitCode.OK),
        }
    )
    return int(ExitCode.OK)


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------


def _cmd_auth_youtube(args: argparse.Namespace, settings: Any) -> int:
    from .providers.google_oauth import describe_scopes, run_loopback_flow
    from .providers.youtube import YouTubeAdapter
    from .transport import PublishHttpClient

    almacen = require_secret_store(settings)
    cliente = PublishHttpClient(
        timeout_s=float(settings.publish_http_timeout_s),
        allowed_hosts=YouTubeAdapter.allowed_hosts(settings),
    )
    _aviso(
        "Se abrira un servidor local en 127.0.0.1 para recibir la respuesta de "
        "Google. En una VPS sin navegador, conecta desde un equipo local y copia "
        "el archivo de token, o abre un tunel a ese puerto."
    )
    bundle = run_loopback_flow(
        cliente,
        almacen,
        settings=settings,
        now=SystemClock().now(),
        open_browser=args.open_browser,
        timeout_s=args.timeout,
        announce=lambda url: _aviso(f"Abre esta URL para autorizar:\n{url}"),
    )
    _emit(
        {
            "command": "auth youtube",
            "connected": True,
            "token_file": str(almacen.path_for("youtube_token.json")),
            "expires_at": iso(bundle.expires_at),
            "scopes": describe_scopes(),
            "note": "El token no se muestra ni se registra en ningun sitio.",
            "exit_code": int(ExitCode.OK),
        }
    )
    return int(ExitCode.OK)


def _cmd_auth_instagram(args: argparse.Namespace, settings: Any) -> int:
    from .providers.instagram import TOKEN_FILE

    almacen = require_secret_store(settings)
    try:
        datos = json.loads(args.from_file.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConfigError(f"No se puede leer {args.from_file}: {exc}") from exc
    token = datos.get("access_token")
    if not token or not isinstance(token, str):
        raise ConfigError(
            "El archivo debe traer `access_token` obtenido con el flujo oficial "
            "de Meta. Este modulo no pide contrasenas de Facebook ni de Instagram."
        )
    payload = {
        "access_token": token,
        "expires_at": datos.get("expires_at"),
        "imported_at": iso(SystemClock().now()),
        "imported_from": str(args.from_file.name),
    }
    almacen.put(TOKEN_FILE, payload)
    _emit(
        {
            "command": "auth instagram",
            "imported": True,
            "token_file": str(almacen.path_for(TOKEN_FILE)),
            "expires_at": payload["expires_at"],
            "permanent": False,
            "note": (
                "Ningun token es permanente: si no trae caducidad, compruebala "
                "en las herramientas de Meta y renuevalo antes de que expire. "
                "El archivo original puedes borrarlo: aqui queda con 0600."
            ),
            "exit_code": int(ExitCode.OK),
        }
    )
    return int(ExitCode.OK)


# ---------------------------------------------------------------------------
# Enrutado
# ---------------------------------------------------------------------------


def run_publish(args: argparse.Namespace, settings: Any) -> int:
    comando = args.publish_command
    if comando == "plan":
        return _cmd_plan(args, settings)
    if comando == "validate":
        return _cmd_validate(args, settings)
    if comando == "schema":
        return _cmd_schema(args)
    if comando == "approve":
        return _cmd_approve(args, settings)
    if comando == "enqueue":
        return _cmd_enqueue(args, settings)
    if comando == "worker":
        return _cmd_worker(args, settings)
    if comando == "status":
        return _cmd_status(args, settings)
    if comando == "cancel":
        return _cmd_cancel(args, settings)
    if comando == "export-manual":
        return _cmd_export_manual(args, settings)
    if comando == "record-manual":
        return _cmd_record_manual(args, settings)
    if comando == "accounts":
        return _cmd_accounts_check(args, settings)
    if comando == "gc":
        return _cmd_gc(args, settings)
    raise ConfigError(f"Subcomando de publish desconocido: {comando}")


def run_auth(args: argparse.Namespace, settings: Any) -> int:
    if args.auth_command == "youtube":
        return _cmd_auth_youtube(args, settings)
    if args.auth_command == "instagram":
        return _cmd_auth_instagram(args, settings)
    raise ConfigError(f"Subcomando de auth desconocido: {args.auth_command}")


def describe_verification() -> dict:
    """Lo que quedo sin contrastar, para informes y documentacion."""
    return describe_all()
