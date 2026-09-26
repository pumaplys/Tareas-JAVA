"""Trabajador secuencial: un paso por destino y por invocacion.

`worker --once` procesa lo que esta vencido y se va. No duerme durante horas
esperando a una plataforma: si hay que esperar, deja escrito `next_poll_at` y
libera el proceso, de modo que un timer de systemd baste para llevar la cola.

Orden de cada destino, que no se salta nunca:

1. Reclamar la concesion. Si otro la tiene viva, este no lo toca.
2. Releer el plan persistido y comprobar que la autorizacion sigue cubriendo
   esa intencion exacta.
3. Antes del primer byte, volver a comprobar que el MP4 es el autorizado: los
   bytes, el hash y el tamano, no un booleano guardado.
4. Ejecutar UN paso del adaptador.
5. Persistir lo que devuelva -estado, evidencia, gasto, errores- y soltar la
   concesion.

Ninguna transaccion queda abierta mientras se transfiere o se espera a una
API: se escribe antes de cada operacion y despues de ella.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from ..diskutil import sha256_file
from ..errors import ExitCode, ViralgenError
from ..logging_setup import get_logger
from ..storage import iso
from .authorize import require_authorization
from .clock import Clock, SystemClock
from .plan import PublicationPlan
from .providers.base import DispatchContext, PublisherAdapter, StepResult
from .providers.mock import MockPublisherAdapter
from .providers.tiktok import TikTokManualAdapter
from .queue import daily_budget_exhausted, mark_expired, start_verdict
from .schemas import (
    DestinationState,
    ErrorClass,
    EvidenceRecord,
    EvidenceSource,
    PublishMode,
    StructuredError,
    TransferPhase,
)
from .secrets import SecretStore
from .storage import PublishStorage

logger = get_logger("publish.worker")


class BudgetExhaustedError(ViralgenError):
    """Se agoto el presupuesto propio del destino. No se emite nada mas."""

    exit_code = ExitCode.PROVIDER
    code = "publish_budget_exhausted"


@dataclass
class DestinationOutcome:
    destination_id: str
    platform: str
    previous_state: str
    state: str
    action: str
    detail: str = ""

    def describe(self) -> dict:
        return {
            "destination_id": self.destination_id,
            "platform": self.platform,
            "previous_state": self.previous_state,
            "state": self.state,
            "action": self.action,
            "detail": self.detail,
        }


@dataclass
class WorkerReport:
    started_at: str
    outcomes: list[DestinationOutcome] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)

    def describe(self) -> dict:
        return {
            "started_at": self.started_at,
            "processed": [salida.describe() for salida in self.outcomes],
            "skipped": list(self.skipped),
            "note": (
                "Procesar un destino no significa publicarlo. El estado y la "
                "evidencia de cada destino dicen que ha pasado."
            ),
        }


def build_adapter(
    platform,
    mode: PublishMode,
    *,
    settings: Any,
    export_root: Path,
    staging=None,
) -> PublisherAdapter:
    from ..schemas.common import Platform

    if platform is Platform.TIKTOK:
        return TikTokManualAdapter(settings=settings, export_root=export_root)
    if mode is not PublishMode.REAL:
        return MockPublisherAdapter(platform=platform, settings=settings)
    if platform is Platform.YOUTUBE_SHORTS:
        from .providers.youtube import YouTubeAdapter

        return YouTubeAdapter(settings=settings)
    if platform is Platform.INSTAGRAM_REELS:
        from .providers.instagram import InstagramReelsAdapter
        from .staging import S3Staging, load_staging_config

        almacen = staging or S3Staging(load_staging_config(settings))
        return InstagramReelsAdapter(settings=settings, staging=almacen)
    raise ValueError(f"sin adaptador para {platform}")


class PublishWorker:
    """Un trabajador. Uno solo, por diseno."""

    def __init__(
        self,
        *,
        storage: PublishStorage,
        settings: Any,
        clock: Clock | None = None,
        secrets: SecretStore | None = None,
        export_root: Path | None = None,
        adapter_factory: Callable[..., PublisherAdapter] | None = None,
        owner: str = "worker",
    ) -> None:
        self.storage = storage
        self.settings = settings
        self.clock = clock or SystemClock()
        self.secrets = secrets
        self.export_root = Path(
            export_root or Path(settings.data_dir) / "publicaciones"
        )
        self.adapter_factory = adapter_factory or build_adapter
        self.owner = owner

    # -- Bucle -------------------------------------------------------------

    def run_once(self, *, publication_id: str | None = None) -> WorkerReport:
        """Procesa lo vencido y termina. Sin dormir esperando a nadie."""
        ahora = self.clock.now()
        informe = WorkerReport(started_at=iso(ahora))
        pendientes = self.storage.due_destinations(now=ahora)
        for fila in pendientes:
            if publication_id and fila["publication_id"] != publication_id:
                continue
            reclamada = self.storage.claim_destination(
                fila["publication_id"],
                fila["destination_id"],
                owner=self.owner,
                now=ahora,
                lease_seconds=int(self.settings.publish_lease_seconds),
            )
            if reclamada is None:
                informe.skipped.append(
                    {
                        "destination_id": fila["destination_id"],
                        "reason": "otro trabajador tiene la concesion",
                    }
                )
                continue
            try:
                salida = self._procesar(reclamada, now=ahora)
                if salida is not None:
                    informe.outcomes.append(salida)
            finally:
                self.storage.release_lease(
                    fila["publication_id"], fila["destination_id"], owner=self.owner
                )
        return informe

    def refresh(self, *, publication_id: str) -> WorkerReport:
        """Consulta remota EXPLICITA de lo que esta en curso.

        Ignora `next_poll_at` porque lo pide una persona, pero no adelanta
        ninguna entrega: un destino programado sigue esperando su hora.
        """
        ahora = self.clock.now()
        informe = WorkerReport(started_at=iso(ahora))
        for fila in self.storage.list_destinations(publication_id):
            if DestinationState(fila["state"]) not in (
                DestinationState.DISPATCHING,
                DestinationState.WAITING_REMOTE,
            ):
                continue
            reclamada = self.storage.claim_destination(
                publication_id,
                fila["destination_id"],
                owner=self.owner,
                now=ahora,
                lease_seconds=int(self.settings.publish_lease_seconds),
            )
            if reclamada is None:
                informe.skipped.append(
                    {
                        "destination_id": fila["destination_id"],
                        "reason": "otro trabajador tiene la concesion",
                    }
                )
                continue
            try:
                salida = self._procesar(reclamada, now=ahora)
                if salida is not None:
                    informe.outcomes.append(salida)
            finally:
                self.storage.release_lease(
                    publication_id, fila["destination_id"], owner=self.owner
                )
        return informe

    def step_now(
        self, *, publication_id: str, destination_id: str, step: str = "start"
    ) -> dict:
        """Ejecuta UN paso de un destino concreto, fuera de la cola.

        Lo usa la exportacion manual, que no depende del reloj: el operador
        pide el paquete cuando quiere.
        """
        ahora = self.clock.now()
        fila = self.storage.claim_destination(
            publication_id,
            destination_id,
            owner=self.owner,
            now=ahora,
            lease_seconds=int(self.settings.publish_lease_seconds),
        )
        if fila is None:
            raise ViralgenError(
                f"El destino {destination_id} esta en uso por otro trabajador."
            )
        try:
            trabajo = self.storage.get_publication(publication_id)
            assert trabajo is not None
            plan = PublicationPlan.model_validate_json(trabajo["plan_json"])
            destino_plan = next(
                d for d in plan.destinations if d.destination_id == destination_id
            )
            self._ejecutar(
                fila,
                plan,
                destino_plan,
                PublishMode(trabajo["mode"]),
                now=ahora,
                paso=step,
            )
        finally:
            self.storage.release_lease(
                publication_id, destination_id, owner=self.owner
            )
        actualizado = self.storage.get_destination(publication_id, destination_id)
        assert actualizado is not None
        return actualizado

    # -- Un destino --------------------------------------------------------

    def _procesar(self, fila: dict, *, now: datetime) -> DestinationOutcome | None:
        trabajo = self.storage.get_publication(fila["publication_id"])
        assert trabajo is not None
        plan = PublicationPlan.model_validate_json(trabajo["plan_json"])
        modo = PublishMode(trabajo["mode"])
        estado = DestinationState(fila["state"])
        destino_plan = next(
            (d for d in plan.destinations if d.destination_id == fila["destination_id"]),
            None,
        )
        if destino_plan is None:
            return self._a_revision(
                fila,
                now=now,
                codigo="destino_fuera_del_plan",
                mensaje="el destino no aparece en el plan persistido",
            )

        # La autorizacion se vuelve a comprobar en cada paso: revocarla o
        # cambiar la intencion detiene la cola de inmediato.
        try:
            require_authorization(
                self.storage, publication_id=fila["publication_id"], plan=plan
            )
        except ViralgenError as exc:
            return self._a_revision(
                fila, now=now, codigo="authorization_required", mensaje=exc.message
            )

        if estado is DestinationState.SCHEDULED_LOCAL:
            return self._iniciar(fila, plan, destino_plan, modo, now=now)
        if estado in (DestinationState.DISPATCHING, DestinationState.WAITING_REMOTE):
            return self._continuar(fila, plan, destino_plan, modo, now=now)
        return None

    def _iniciar(self, fila, plan, destino_plan, modo, *, now) -> DestinationOutcome:
        veredicto = start_verdict(fila, now=now)
        if veredicto.expired:
            mark_expired(self.storage, fila, now=now, verdict=veredicto)
            return DestinationOutcome(
                destination_id=fila["destination_id"],
                platform=fila["platform"],
                previous_state=fila["state"],
                state=DestinationState.NEEDS_REVIEW.value,
                action="ventana_vencida",
                detail=veredicto.reason,
            )
        if not veredicto.due:
            return DestinationOutcome(
                destination_id=fila["destination_id"],
                platform=fila["platform"],
                previous_state=fila["state"],
                state=fila["state"],
                action="todavia_no_toca",
                detail=veredicto.reason,
            )
        if modo is PublishMode.REAL and daily_budget_exhausted(
            self.storage, fila, now=now, settings=self.settings
        ):
            return self._a_revision(
                fila,
                now=now,
                codigo="daily_budget_exhausted",
                mensaje=(
                    "se alcanzo el tope propio de entregas nuevas por cuenta y "
                    "dia; no es una cuota de la plataforma"
                ),
            )

        # Antes del primer byte: los bytes reales, no lo que diga un JSON.
        problema = self._verificar_video(plan)
        if problema:
            return self._a_revision(
                fila, now=now, codigo="video_no_verificado", mensaje=problema
            )

        self.storage.record_event(
            publication_id=fila["publication_id"],
            destination_id=fila["destination_id"],
            kind="dispatch_intent",
            state=DestinationState.DISPATCHING.value,
            detail={
                "platform": fila["platform"],
                "video_sha256": plan.sources.video.sha256,
                "requested_visibility": fila["requested_visibility"],
            },
        )
        fila = self.storage.update_destination(
            fila["publication_id"],
            fila["destination_id"],
            state=DestinationState.DISPATCHING,
            phase=TransferPhase.UPLOADING.value,
            dispatch_started_at=iso(now),
            attempts_delta=1,
            operation_attempts_delta=1,
        )
        return self._ejecutar(fila, plan, destino_plan, modo, now=now, paso="start")

    def _continuar(self, fila, plan, destino_plan, modo, *, now) -> DestinationOutcome:
        """Retoma un destino en curso: o reintenta el envio, o consulta.

        La distincion importa para el limite: **consultar no gasta intentos**
        -leer dos veces no publica dos veces-, pero repetir la operacion que
        crea algo si. Por eso el tope se aplica solo al reintento.
        """
        reintento = fila["state"] == DestinationState.DISPATCHING.value
        if reintento:
            limite = int(self.settings.publish_max_attempts_per_operation)
            realizados = int(fila["operation_attempts"])
            if realizados >= limite:
                return self._a_revision(
                    fila,
                    now=now,
                    codigo="operation_attempts_exhausted",
                    mensaje=(
                        f"se agotaron los {limite} intentos de la operacion de "
                        f"envio de este destino ({realizados} realizados). No se "
                        "crea nada mas sin que una persona lo revise: repetir a "
                        "ciegas es como aparecen los duplicados."
                    ),
                )
            fila = self.storage.update_destination(
                fila["publication_id"],
                fila["destination_id"],
                attempts_delta=1,
                operation_attempts_delta=1,
            )
        else:
            fila = self.storage.update_destination(
                fila["publication_id"], fila["destination_id"], attempts_delta=1
            )
        return self._ejecutar(
            fila, plan, destino_plan, modo, now=now, paso="start" if reintento else "poll"
        )

    def _ejecutar(self, fila, plan, destino_plan, modo, *, now, paso) -> DestinationOutcome:
        adaptador = self.adapter_factory(
            destino_plan.platform,
            modo,
            settings=self.settings,
            export_root=self.export_root,
        )
        ctx = self._contexto(fila, plan, destino_plan, modo, now=now)
        anterior = fila["state"]
        try:
            resultado = (
                adaptador.start(ctx) if paso == "start" else adaptador.poll(ctx)
            )
        except BudgetExhaustedError as exc:
            return self._a_revision(
                fila, now=now, codigo=exc.code, mensaje=exc.message
            )
        except ViralgenError as exc:
            return self._a_revision(
                fila, now=now, codigo=getattr(exc, "code", "error"), mensaje=exc.message
            )
        actualizado = self._persistir(fila, resultado, modo, now=now)
        return DestinationOutcome(
            destination_id=fila["destination_id"],
            platform=fila["platform"],
            previous_state=anterior,
            state=actualizado["state"],
            action=paso,
            detail=resultado.note or (resultado.evidence.summary if resultado.evidence else ""),
        )

    # -- Piezas ------------------------------------------------------------

    def _verificar_video(self, plan) -> str | None:
        """Bytes, hash y tamano del MP4, recalculados justo antes de enviar."""
        ruta = Path(plan.sources.video.path)
        if not ruta.is_file():
            return f"el MP4 autorizado ya no esta en {ruta}"
        tamano = ruta.stat().st_size
        if tamano != plan.sources.video.size_bytes:
            return (
                f"el MP4 mide {tamano} bytes y se autorizo con "
                f"{plan.sources.video.size_bytes}"
            )
        if sha256_file(ruta) != plan.sources.video.sha256:
            return "el MP4 ha cambiado desde que se autorizo"
        return None

    def _contexto(self, fila, plan, destino_plan, modo, *, now) -> DispatchContext:
        limite = int(self.settings.publish_max_requests_per_destination)

        def spend(solicitudes: int, bytes_: int) -> None:
            actual = self.storage.get_destination(
                fila["publication_id"], fila["destination_id"]
            )
            assert actual is not None
            if actual["requests_used"] + solicitudes > limite:
                raise BudgetExhaustedError(
                    f"El destino {fila['destination_id']} alcanzo el tope propio "
                    f"de {limite} solicitudes (incluye sondeos y reintentos). "
                    "No es una cuota de la plataforma.",
                    details={"limit": limite, "used": actual["requests_used"]},
                )
            # Se anota ANTES de la peticion: una llamada perdida tambien gasta.
            self.storage.update_destination(
                fila["publication_id"],
                fila["destination_id"],
                requests_delta=solicitudes,
                bytes_delta=bytes_,
            )

        def heartbeat() -> None:
            self.storage.renew_lease(
                fila["publication_id"],
                fila["destination_id"],
                owner=self.owner,
                now=self.clock.now(),
                lease_seconds=int(self.settings.publish_lease_seconds),
            )

        return DispatchContext(
            publication_id=fila["publication_id"],
            destination_id=fila["destination_id"],
            platform=destino_plan.platform,
            account_alias=destino_plan.account.alias,
            expected_account_id=destino_plan.account.expected_account_id,
            metadata=destino_plan.metadata,
            requested_visibility=destino_plan.requested_visibility,
            options=destino_plan.options,
            video_path=Path(plan.sources.video.path),
            video_sha256=plan.sources.video.sha256,
            video_size=plan.sources.video.size_bytes,
            mode=modo,
            row=dict(fila),
            now=now,
            settings=self.settings,
            secrets=self.secrets,
            spend=spend,
            heartbeat=heartbeat,
        )

    def _persistir(self, fila, resultado: StepResult, modo: PublishMode, *, now) -> dict:
        """Guarda el resultado del paso. Los espacios de identidad no se mezclan."""
        campos: dict[str, Any] = {"phase": resultado.phase.value}
        if resultado.remote_id:
            if modo is PublishMode.REAL:
                campos["real_remote_id"] = resultado.remote_id
            else:
                campos["mock_remote_id"] = resultado.remote_id
        if resultado.observed_account_id:
            campos["observed_account_id"] = resultado.observed_account_id
        if resultado.observed_visibility is not None:
            campos["observed_visibility"] = resultado.observed_visibility.value
        if resultado.publicly_visible is not None:
            campos["publicly_visible"] = int(resultado.publicly_visible)
        if resultado.permalink:
            campos["permalink"] = resultado.permalink
        if resultado.remote_refs:
            refs = json.loads(fila.get("remote_refs_json") or "{}")
            refs.update(resultado.remote_refs)
            campos["remote_refs_json"] = json.dumps(refs, ensure_ascii=False)
        if resultado.evidence is not None:
            campos["last_evidence_json"] = resultado.evidence.model_dump_json()
        if resultado.error is not None:
            campos["last_error_json"] = resultado.error.model_dump_json()
        if resultado.staging is not None:
            campos["staging_json"] = resultado.staging.model_dump_json()
            self.storage.record_staging_object(
                {
                    "object_key": resultado.staging.object_key,
                    "bucket_alias": resultado.staging.bucket_alias,
                    "publication_id": fila["publication_id"],
                    "destination_id": fila["destination_id"],
                    "object_sha256": resultado.staging.object_sha256,
                    "size_bytes": resultado.staging.size_bytes,
                    "uploaded_at": iso(resultado.staging.uploaded_at),
                    "retain_until": (
                        iso(resultado.staging.retain_until)
                        if resultado.staging.retain_until
                        else None
                    ),
                }
            )
        if resultado.manual_export is not None:
            campos["manual_export_json"] = resultado.manual_export.model_dump_json()
        if resultado.next_poll_in_s:
            campos["next_poll_at"] = iso(
                now + timedelta(seconds=float(resultado.next_poll_in_s))
            )
        if resultado.state is DestinationState.DELIVERED:
            campos["delivered_at"] = iso(now)
            campos["next_poll_at"] = None

        # Los bytes que se contabilizan son los que anoto `spend` al enviarlos.
        # `bytes_sent` es informativo del paso: sumarlo aqui los contaria dos
        # veces, y en simulacion no se ha transferido nada que contar.
        actualizado = self.storage.update_destination(
            fila["publication_id"],
            fila["destination_id"],
            state=resultado.state,
            **campos,
        )
        self.storage.record_event(
            publication_id=fila["publication_id"],
            destination_id=fila["destination_id"],
            kind="step",
            state=actualizado["state"],
            phase=actualizado["phase"],
            detail={
                "evidence": (
                    resultado.evidence.summary if resultado.evidence else None
                ),
                "error_code": resultado.error.code if resultado.error else None,
                "bytes_sent": resultado.bytes_sent,
            },
        )
        return actualizado

    def _a_revision(self, fila, *, now, codigo: str, mensaje: str) -> DestinationOutcome:
        """Deja el destino en manos del operador, con el motivo escrito."""
        error = StructuredError(
            code=codigo,
            error_class=ErrorClass.LOCAL,
            message=mensaje[:400],
            retryable=False,
            occurred_at=now,
        )
        actualizado = self.storage.update_destination(
            fila["publication_id"],
            fila["destination_id"],
            state=DestinationState.NEEDS_REVIEW,
            last_error_json=error.model_dump_json(),
            last_evidence_json=EvidenceRecord(
                source=EvidenceSource.LOCAL_PACKAGE,
                checked_at=now,
                summary=f"comprobacion local: {mensaje[:200]}",
            ).model_dump_json(),
        )
        self.storage.record_event(
            publication_id=fila["publication_id"],
            destination_id=fila["destination_id"],
            kind="needs_review",
            state=actualizado["state"],
            detail={"code": codigo, "message": mensaje[:300]},
        )
        return DestinationOutcome(
            destination_id=fila["destination_id"],
            platform=fila["platform"],
            previous_state=fila["state"],
            state=actualizado["state"],
            action="revision",
            detail=mensaje[:200],
        )
