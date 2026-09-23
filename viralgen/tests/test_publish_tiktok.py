"""TikTok manual y publicador simulado.

Lo que se prueba: que exportar no publique, que el MP4 salga intacto (marca de
preview incluida), que un paquete simulado lo diga sin ambiguedad y que lo que
aporta una persona conserve su origen humano.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from viralgen.config import Settings
from viralgen.diskutil import sha256_file
from viralgen.publish.providers.base import DispatchContext
from viralgen.publish.providers.mock import MockPublisherAdapter, mock_remote_id
from viralgen.publish.providers.tiktok import TikTokManualAdapter, build_manual_report
from viralgen.publish.schemas import (
    AudienceDecision,
    DestinationMetadata,
    DestinationOptions,
    DestinationState,
    EvidenceSource,
    PublishMode,
    SyntheticDisclosure,
    TextSource,
    Visibility,
)
from viralgen.schemas.common import Platform

UTC = timezone.utc
AHORA = datetime(2026, 9, 24, 17, 0, tzinfo=UTC)
CONTENIDO = b"MP4 de prueba con marca de preview" * 1000


def _contexto(
    tmp_path: Path,
    *,
    mode: PublishMode = PublishMode.MOCK,
    platform: Platform = Platform.TIKTOK,
) -> DispatchContext:
    video = tmp_path / "preview.mp4"
    video.write_bytes(CONTENIDO)
    return DispatchContext(
        publication_id="pub1",
        destination_id="tk",
        platform=platform,
        account_alias="tiktok_demo",
        expected_account_id="@cuenta_demo",
        metadata=DestinationMetadata(
            title="Un titulo",
            description="El texto del guion.",
            tags=[],
            hashtags=["curiosidades"],
            language="es",
            audience=AudienceDecision.NOT_MADE_FOR_KIDS,
            synthetic_disclosure=SyntheticDisclosure.NO_REALISTIC_SYNTHETIC_MEDIA,
            text_source=TextSource.SCRIPT_PUBLISHING_PLAN,
            within_local_limits=True,
        ),
        requested_visibility=Visibility.PUBLIC,
        options=DestinationOptions(manual_delivery=True),
        video_path=video,
        video_sha256=sha256_file(video),
        video_size=video.stat().st_size,
        mode=mode,
        row={
            "remote_refs_json": "{}",
            "attempts": 0,
            "scheduled_at": "2026-09-24T17:00:00Z",
            "timezone": "Europe/Madrid",
            "real_remote_id": None,
            "mock_remote_id": None,
        },
        now=AHORA,
        settings=Settings(_env_file=None, data_dir=tmp_path / "data", log_level="ERROR"),
    )


def _adaptador(tmp_path: Path) -> TikTokManualAdapter:
    return TikTokManualAdapter(
        settings=Settings(_env_file=None, data_dir=tmp_path / "data"),
        export_root=tmp_path / "exportaciones",
    )


# ---------------------------------------------------------------------------
# Exportacion
# ---------------------------------------------------------------------------


def test_exportar_no_es_publicar(tmp_path: Path) -> None:
    adaptador = _adaptador(tmp_path)
    resultado = adaptador.start(_contexto(tmp_path))
    assert resultado.state is DestinationState.AWAITING_MANUAL
    assert resultado.state is not DestinationState.DELIVERED
    assert resultado.manual_export is not None
    assert resultado.manual_export.publishable_by_this_module is False
    assert resultado.evidence.source is EvidenceSource.LOCAL_PACKAGE


def test_el_mp4_sale_intacto_con_su_marca(tmp_path: Path) -> None:
    """No se recodifica ni se quita nada: es una copia binaria."""
    adaptador = _adaptador(tmp_path)
    ctx = _contexto(tmp_path)
    referencia = adaptador.export(ctx)
    copia = Path(referencia.package_path) / "video.mp4"
    assert copia.read_bytes() == CONTENIDO
    assert referencia.video_sha256 == ctx.video_sha256


def test_el_paquete_trae_texto_editable_y_ficha_legible(tmp_path: Path) -> None:
    adaptador = _adaptador(tmp_path)
    ctx = _contexto(tmp_path)
    referencia = adaptador.export(ctx)
    carpeta = Path(referencia.package_path)

    texto = (carpeta / "texto.txt").read_text(encoding="utf-8")
    assert "El texto del guion." in texto
    assert "#curiosidades" in texto

    ficha = (carpeta / "ficha.md").read_text(encoding="utf-8")
    assert "@cuenta_demo" in ficha
    assert "2026-09-24T17:00:00Z" in ficha
    assert "no es publicar" in ficha
    assert referencia.video_sha256 in ficha
    assert "not_made_for_kids" in ficha
    assert "no una programacion confirmada" in ficha.replace("\n", " ")


def test_un_paquete_simulado_lo_dice_sin_ambiguedad(tmp_path: Path) -> None:
    adaptador = _adaptador(tmp_path)
    referencia = adaptador.export(_contexto(tmp_path, mode=PublishMode.MOCK))
    carpeta = Path(referencia.package_path)
    assert referencia.simulation is True
    assert (carpeta / "NO_PUBLICABLE.txt").is_file()
    assert "NO PUBLICABLE" in (carpeta / "ficha.md").read_text(encoding="utf-8")


def test_un_paquete_real_no_lleva_el_aviso(tmp_path: Path) -> None:
    adaptador = _adaptador(tmp_path)
    referencia = adaptador.export(_contexto(tmp_path, mode=PublishMode.REAL))
    assert referencia.simulation is False
    assert not (Path(referencia.package_path) / "NO_PUBLICABLE.txt").exists()


def test_no_hay_estado_remoto_que_consultar(tmp_path: Path) -> None:
    adaptador = _adaptador(tmp_path)
    resultado = adaptador.poll(_contexto(tmp_path))
    assert resultado.state is DestinationState.AWAITING_MANUAL
    assert resultado.remote_id is None
    assert "sin integracion remota" in resultado.evidence.summary


def test_las_capacidades_declaran_que_no_hay_direct_post(tmp_path: Path) -> None:
    capacidades = _adaptador(tmp_path).capabilities()
    assert capacidades["direct_post"] is False
    assert capacidades["remote_api"] is False
    assert "utilidades privadas" in capacidades["reason"]


# ---------------------------------------------------------------------------
# Registro manual
# ---------------------------------------------------------------------------


def test_lo_que_aporta_el_operador_conserva_su_origen() -> None:
    reporte = build_manual_report(
        url="https://www.tiktok.com/@cuenta_demo/video/123",
        remote_id=None,
        reported_at=AHORA,
        now=AHORA,
    )
    assert reporte.evidence_source == "operator_reported"
    assert "No esta verificado" in reporte.note


def test_un_registro_vacio_no_se_acepta() -> None:
    with pytest.raises(Exception, match="al menos una URL o un ID"):
        build_manual_report(url=None, remote_id=None, reported_at=AHORA, now=AHORA)


def test_manually_reported_no_adjudica_un_id_remoto(tmp_path: Path) -> None:
    from viralgen.publish.schemas import BudgetUsage, ReceiptDestination

    reporte = build_manual_report(
        url="https://www.tiktok.com/@cuenta_demo/video/123",
        remote_id=None,
        reported_at=AHORA,
        now=AHORA,
    )
    destino = ReceiptDestination(
        destination_id="tk",
        platform=Platform.TIKTOK,
        account_alias="tiktok_demo",
        state=DestinationState.MANUALLY_REPORTED,
        simulation=False,
        requested_visibility=Visibility.PUBLIC,
        budget=BudgetUsage(request_limit=200, attempt_limit=3),
        manual_report=reporte,
    )
    assert destino.real_remote_id is None
    assert destino.state is not DestinationState.DELIVERED


# ---------------------------------------------------------------------------
# Publicador simulado
# ---------------------------------------------------------------------------


def test_el_simulado_no_tiene_cliente_de_red(tmp_path: Path) -> None:
    """La imposibilidad es estructural: no hay nada que apuntar a la red."""
    adaptador = MockPublisherAdapter(
        platform=Platform.YOUTUBE_SHORTS,
        settings=Settings(_env_file=None, data_dir=tmp_path),
    )
    assert not hasattr(adaptador, "client")
    assert adaptador.capabilities()["network"] is False


def test_el_simulado_usa_su_propio_espacio_de_identidad(tmp_path: Path) -> None:
    ajustes = Settings(_env_file=None, data_dir=tmp_path, log_level="ERROR")
    adaptador = MockPublisherAdapter(
        platform=Platform.YOUTUBE_SHORTS, settings=ajustes
    )
    ctx = _contexto(tmp_path, platform=Platform.YOUTUBE_SHORTS)
    ctx.settings = ajustes
    resultado = adaptador.start(ctx)
    assert resultado.remote_id.startswith("mock_")
    assert resultado.remote_id == mock_remote_id(ctx)
    assert resultado.evidence.source is EvidenceSource.SIMULATED


def test_el_simulado_espera_antes_de_dar_por_verificado(tmp_path: Path) -> None:
    ajustes = Settings(_env_file=None, data_dir=tmp_path, log_level="ERROR")
    adaptador = MockPublisherAdapter(
        platform=Platform.YOUTUBE_SHORTS, settings=ajustes, polls_until_ready=2
    )
    ctx = _contexto(tmp_path, platform=Platform.YOUTUBE_SHORTS)
    ctx.settings = ajustes

    ctx.row["attempts"] = 0
    assert adaptador.poll(ctx).state is DestinationState.WAITING_REMOTE
    ctx.row["attempts"] = 2
    entregado = adaptador.poll(ctx)
    assert entregado.state is DestinationState.DELIVERED
    assert entregado.permalink is None, "no se fabrica una URL que parezca real"
