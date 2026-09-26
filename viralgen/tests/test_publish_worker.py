"""El trabajador: que se envia, cuando, y que lo detiene.

Estas pruebas usan adaptadores dobles para poder comprobar lo que de verdad
importa: que nada salga sin autorizacion viva, que el MP4 se vuelva a verificar
antes de transferir, que una ventana vencida no publique y que un fallo en un
destino no arrastre a los demas.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from viralgen.config import Settings
from viralgen.publish.authorize import build_authorization, store_authorization
from viralgen.publish.clock import ManualClock
from viralgen.publish.providers.base import AccountCheck, PublisherAdapter, StepResult
from viralgen.publish.providers.mock import MockPublisherAdapter
from viralgen.publish.queue import enqueue_plan
from viralgen.publish.receipt import build_receipt
from viralgen.publish.schemas import (
    DestinationState,
    ErrorClass,
    EvidenceRecord,
    EvidenceSource,
    OverallOutcome,
    PublishMode,
    StructuredError,
    TransferPhase,
    Visibility,
)
from viralgen.publish.storage import PublishStorage
from viralgen.publish.worker import PublishWorker, build_adapter
from viralgen.schemas.common import Platform
from viralgen.storage import Storage

from conftest import publish_destination, publish_plan, publish_sources

UTC = timezone.utc
PROGRAMADO = datetime(2026, 9, 24, 16, 30, tzinfo=UTC)
PUB_ID = "a1a39781-bc30-520d-81e6-a26dace0fa59"  # derivado de demo-001 en mock


class AdaptadorEspia(PublisherAdapter):
    """Registra las llamadas y devuelve lo que se le diga."""

    platform = Platform.YOUTUBE_SHORTS
    name = "espia"
    remote = False

    def __init__(self, resultado: StepResult | None = None) -> None:
        self.llamadas: list[str] = []
        self.resultado = resultado

    def check_account(self, ctx):  # pragma: no cover - no se usa aqui
        return AccountCheck(ok=True)

    def start(self, ctx) -> StepResult:
        self.llamadas.append("start")
        return self.resultado or StepResult(
            state=DestinationState.WAITING_REMOTE,
            phase=TransferPhase.BYTES_ACCEPTED,
            remote_id="mock_espia",
            evidence=EvidenceRecord(
                source=EvidenceSource.SIMULATED,
                checked_at=ctx.now,
                summary="espia: envio simulado",
            ),
        )

    def poll(self, ctx) -> StepResult:
        self.llamadas.append("poll")
        return StepResult(
            state=DestinationState.DELIVERED,
            phase=TransferPhase.VERIFIED,
            remote_id="mock_espia",
            observed_visibility=ctx.requested_visibility,
            publicly_visible=ctx.requested_visibility is Visibility.PUBLIC,
            evidence=EvidenceRecord(
                source=EvidenceSource.SIMULATED,
                checked_at=ctx.now,
                summary="espia: verificado",
            ),
        )


@pytest.fixture
def entorno(tmp_path: Path, settings: Settings):
    """Un trabajo simulado ya autorizado y encolado, con su video real."""
    video = tmp_path / "video.mp4"
    video.write_bytes(b"\x02" * 4096)
    from viralgen.diskutil import sha256_file

    fuentes = publish_sources(
        video=publish_sources().video.model_copy(
            update={
                "path": str(video),
                "sha256": sha256_file(video),
                "size_bytes": video.stat().st_size,
            }
        )
    )
    plan = publish_plan(sources=fuentes)
    almacenamiento = PublishStorage(Storage(tmp_path / "datos"))
    almacenamiento.migrate()
    store_authorization(
        almacenamiento,
        publication_id=PUB_ID,
        registro=build_authorization(
            plan,
            plan_sha256="e" * 64,
            operator_identity="operador",
            clock=ManualClock(PROGRAMADO - timedelta(hours=2)),
        ),
    )
    enqueue_plan(
        almacenamiento,
        plan=plan,
        plan_sha256="e" * 64,
        publication_id=PUB_ID,
        settings=settings,
        clock=ManualClock(PROGRAMADO - timedelta(hours=2)),
    )
    return almacenamiento, plan, video


def _trabajador(almacenamiento, settings, *, now, adaptador=None, tmp_path=None):
    return PublishWorker(
        storage=almacenamiento,
        settings=settings,
        clock=ManualClock(now),
        export_root=(tmp_path or Path("/tmp")) / "exportaciones",
        adapter_factory=(lambda *a, **k: adaptador) if adaptador else None,
    )


# ---------------------------------------------------------------------------
# Reloj
# ---------------------------------------------------------------------------


def test_antes_de_la_hora_no_se_envia_nada(entorno, settings: Settings) -> None:
    almacenamiento, _plan, _video = entorno
    espia = AdaptadorEspia()
    informe = _trabajador(
        almacenamiento, settings, now=PROGRAMADO - timedelta(minutes=5), adaptador=espia
    ).run_once()
    assert espia.llamadas == []
    assert informe.outcomes == []


def test_en_ventana_se_envia_una_vez(entorno, settings: Settings, tmp_path: Path) -> None:
    almacenamiento, _plan, _video = entorno
    espia = AdaptadorEspia()
    informe = _trabajador(
        almacenamiento, settings, now=PROGRAMADO + timedelta(minutes=2),
        adaptador=espia, tmp_path=tmp_path,
    ).run_once()
    assert espia.llamadas == ["start"]
    assert informe.outcomes[0].state == DestinationState.WAITING_REMOTE.value
    fila = almacenamiento.get_destination(PUB_ID, "yt_principal")
    assert fila["mock_remote_id"] == "mock_espia"
    assert fila["real_remote_id"] is None
    assert fila["dispatch_started_at"] is not None


def test_una_ventana_vencida_no_publica(entorno, settings: Settings) -> None:
    almacenamiento, _plan, _video = entorno
    espia = AdaptadorEspia()
    informe = _trabajador(
        almacenamiento, settings, now=PROGRAMADO + timedelta(hours=6), adaptador=espia
    ).run_once()
    assert espia.llamadas == []
    assert informe.outcomes[0].state == DestinationState.NEEDS_REVIEW.value


# ---------------------------------------------------------------------------
# Lo que detiene el envio
# ---------------------------------------------------------------------------


def test_si_el_video_cambia_no_se_transfiere(entorno, settings: Settings) -> None:
    """El MP4 se rehashea justo antes de enviar, no se cree al JSON."""
    almacenamiento, _plan, video = entorno
    video.write_bytes(b"\x03" * 4096)
    espia = AdaptadorEspia()
    informe = _trabajador(
        almacenamiento, settings, now=PROGRAMADO + timedelta(minutes=2), adaptador=espia
    ).run_once()
    assert espia.llamadas == []
    assert informe.outcomes[0].state == DestinationState.NEEDS_REVIEW.value
    fila = almacenamiento.get_destination(PUB_ID, "yt_principal")
    assert "cambiado" in fila["last_error_json"]


def test_si_falta_el_video_no_se_transfiere(entorno, settings: Settings) -> None:
    almacenamiento, _plan, video = entorno
    video.unlink()
    espia = AdaptadorEspia()
    informe = _trabajador(
        almacenamiento, settings, now=PROGRAMADO + timedelta(minutes=2), adaptador=espia
    ).run_once()
    assert espia.llamadas == []
    assert informe.outcomes[0].action == "revision"


def test_revocar_la_autorizacion_detiene_la_cola(entorno, settings: Settings) -> None:
    almacenamiento, _plan, _video = entorno
    almacenamiento.revoke_authorizations(PUB_ID, at=PROGRAMADO)
    espia = AdaptadorEspia()
    informe = _trabajador(
        almacenamiento, settings, now=PROGRAMADO + timedelta(minutes=2), adaptador=espia
    ).run_once()
    assert espia.llamadas == []
    assert informe.outcomes[0].state == DestinationState.NEEDS_REVIEW.value
    assert "autorizacion" in informe.outcomes[0].detail.lower()


def test_el_presupuesto_agotado_manda_a_revision(entorno, tmp_path: Path) -> None:
    ajustes = Settings(
        _env_file=None,
        data_dir=tmp_path / "datos",
        log_level="ERROR",
        publish_max_requests_per_destination=1,
    )
    almacenamiento, _plan, _video = entorno

    class Gastoso(AdaptadorEspia):
        def start(self, ctx):
            self.llamadas.append("start")
            ctx.spend(1, 0)
            ctx.spend(1, 0)  # el segundo se pasa del tope
            return StepResult(
                state=DestinationState.WAITING_REMOTE, phase=TransferPhase.UPLOADING
            )

    espia = Gastoso()
    informe = _trabajador(
        almacenamiento, ajustes, now=PROGRAMADO + timedelta(minutes=2), adaptador=espia
    ).run_once()
    assert informe.outcomes[0].state == DestinationState.NEEDS_REVIEW.value
    assert "solicitudes" in informe.outcomes[0].detail


# ---------------------------------------------------------------------------
# Concurrencia y resultado parcial
# ---------------------------------------------------------------------------


def test_dos_invocaciones_no_envian_el_mismo_destino(entorno, settings: Settings) -> None:
    almacenamiento, _plan, _video = entorno
    ahora = PROGRAMADO + timedelta(minutes=2)
    primero = _trabajador(
        almacenamiento, settings, now=ahora, adaptador=AdaptadorEspia()
    )
    # El primero reclama y no suelta la concesion.
    almacenamiento.claim_destination(
        PUB_ID, "yt_principal", owner="otro_worker", now=ahora, lease_seconds=300
    )
    espia = AdaptadorEspia()
    informe = _trabajador(
        almacenamiento, settings, now=ahora, adaptador=espia
    ).run_once()
    assert espia.llamadas == []
    assert informe.skipped and "concesion" in informe.skipped[0]["reason"]
    assert primero is not None


def test_un_fallo_en_un_destino_no_arrastra_al_otro(
    tmp_path: Path, settings: Settings
) -> None:
    from viralgen.diskutil import sha256_file

    video = tmp_path / "video.mp4"
    video.write_bytes(b"\x02" * 2048)
    fuentes = publish_sources(
        video=publish_sources().video.model_copy(
            update={
                "path": str(video),
                "sha256": sha256_file(video),
                "size_bytes": video.stat().st_size,
            }
        )
    )
    from viralgen.publish.schemas import AccountRef, DestinationOptions

    segundo = publish_destination(
        destination_id="ig_principal",
        platform=Platform.INSTAGRAM_REELS,
        account=AccountRef(
            platform=Platform.INSTAGRAM_REELS,
            alias="reels_demo",
            expected_account_id="17841400000000000",
            account_id_kind="instagram_user_id",
        ),
        requested_visibility=Visibility.PUBLIC,
        options=DestinationOptions(share_to_feed=True, requires_staging=True),
    )
    plan = publish_plan(sources=fuentes, destinations=[publish_destination(), segundo])

    almacenamiento = PublishStorage(Storage(tmp_path / "datos"))
    almacenamiento.migrate()
    store_authorization(
        almacenamiento,
        publication_id=PUB_ID,
        registro=build_authorization(
            plan, plan_sha256="e" * 64, operator_identity="operador",
            clock=ManualClock(PROGRAMADO - timedelta(hours=2)),
        ),
    )
    enqueue_plan(
        almacenamiento, plan=plan, plan_sha256="e" * 64, publication_id=PUB_ID,
        settings=settings, clock=ManualClock(PROGRAMADO - timedelta(hours=2)),
    )

    def fabrica(platform, mode, **_kwargs):
        if platform is Platform.INSTAGRAM_REELS:
            return MockPublisherAdapter(
                platform=platform, settings=settings, fail_on_start=True
            )
        return MockPublisherAdapter(platform=platform, settings=settings)

    trabajador = PublishWorker(
        storage=almacenamiento,
        settings=settings,
        clock=ManualClock(PROGRAMADO + timedelta(minutes=10)),
        adapter_factory=fabrica,
    )
    trabajador.run_once()
    trabajador.clock = ManualClock(PROGRAMADO + timedelta(minutes=12))
    trabajador.run_once()

    recibo = build_receipt(almacenamiento, publication_id=PUB_ID, settings=settings)
    estados = {d.destination_id: d.state for d in recibo.destinations}
    assert estados["yt_principal"] is DestinationState.DELIVERED
    assert estados["ig_principal"] is DestinationState.FAILED
    assert recibo.summary.overall is OverallOutcome.PARTIAL
    # Y el que salio bien no se vuelve a enviar por el que fallo.
    entregado = next(d for d in recibo.destinations if d.destination_id == "yt_principal")
    assert entregado.budget.attempts <= 2


# ---------------------------------------------------------------------------
# Modos
# ---------------------------------------------------------------------------


def test_el_modo_simulado_no_construye_un_adaptador_real(
    tmp_path: Path, monkeypatch
) -> None:
    """Aunque el entorno tenga credenciales de verdad."""
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "cliente-real")
    monkeypatch.setenv("YOUTUBE_CHANNEL_ID", "UCreal")
    monkeypatch.setenv("META_GRAPH_API_VERSION", "v21.0")
    ajustes = Settings(data_dir=tmp_path, log_level="ERROR")

    for plataforma in (Platform.YOUTUBE_SHORTS, Platform.INSTAGRAM_REELS):
        adaptador = build_adapter(
            plataforma, PublishMode.MOCK, settings=ajustes, export_root=tmp_path
        )
        assert isinstance(adaptador, MockPublisherAdapter)
        assert not hasattr(adaptador, "client")


def test_en_modo_real_se_construye_el_adaptador_real(tmp_path: Path) -> None:
    from viralgen.publish.providers.youtube import YouTubeAdapter

    ajustes = Settings(
        _env_file=None, data_dir=tmp_path, log_level="ERROR",
        youtube_client_id="cliente", youtube_channel_id="UCx",
    )
    adaptador = build_adapter(
        Platform.YOUTUBE_SHORTS, PublishMode.REAL, settings=ajustes, export_root=tmp_path
    )
    assert isinstance(adaptador, YouTubeAdapter)


def test_tiktok_usa_el_mismo_adaptador_en_los_dos_modos(tmp_path: Path) -> None:
    from viralgen.publish.providers.tiktok import TikTokManualAdapter

    ajustes = Settings(_env_file=None, data_dir=tmp_path, log_level="ERROR")
    for modo in (PublishMode.MOCK, PublishMode.REAL):
        adaptador = build_adapter(
            Platform.TIKTOK, modo, settings=ajustes, export_root=tmp_path
        )
        assert isinstance(adaptador, TikTokManualAdapter)


def test_un_error_del_adaptador_deja_el_motivo_escrito(
    entorno, settings: Settings
) -> None:
    almacenamiento, _plan, _video = entorno

    class Roto(AdaptadorEspia):
        def start(self, ctx):
            self.llamadas.append("start")
            return StepResult(
                state=DestinationState.FAILED,
                phase=TransferPhase.UPLOADING,
                error=StructuredError(
                    code="payload_invalido",
                    error_class=ErrorClass.INVALID_PAYLOAD,
                    message="la plataforma rechazo el payload",
                    retryable=False,
                    occurred_at=ctx.now,
                ),
            )

    _trabajador(
        almacenamiento, settings, now=PROGRAMADO + timedelta(minutes=2), adaptador=Roto()
    ).run_once()
    fila = almacenamiento.get_destination(PUB_ID, "yt_principal")
    assert fila["state"] == DestinationState.FAILED.value
    assert "payload_invalido" in fila["last_error_json"]
