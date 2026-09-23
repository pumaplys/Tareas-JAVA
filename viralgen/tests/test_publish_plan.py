"""El borrador editorial: de donde sale el texto y que se niega a inventar.

El riesgo aqui no es publicar de mas, es publicar OTRA COSA: un titulo
recortado en silencio, unos hashtags anadidos por conveniencia o una decision
de audiencia tomada por el programa. Estas pruebas fijan que el plan solo
copia lo que existe y nombra lo que falta.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from viralgen.config import Settings
from viralgen.diskutil import sha256_file
from viralgen.publish.accounts import Account
from viralgen.publish.admission import PublishAdmissionReport
from viralgen.publish.clock import ManualClock, ScheduleError
from viralgen.publish.plan import (
    DestinationRequest,
    build_publication_plan,
    load_plan,
    normalize_plan,
    write_plan,
)
from viralgen.publish.schemas import (
    AudienceDecision,
    DestinationState,
    PublishMode,
    SyntheticDisclosure,
    TextSource,
    Visibility,
)
from viralgen.schemas.common import Platform

from conftest import publish_sources

RAIZ = Path(__file__).parent.parent
GUION = RAIZ / "examples" / "visuales_simulados" / "script.json"
GUION_INFANTIL = RAIZ / "examples" / "ejemplo_cuento_infantil.json"

UTC = timezone.utc
AHORA = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)

CUENTAS = {
    "canal_demo": Account("canal_demo", Platform.YOUTUBE_SHORTS, "UCdemo000000000000000"),
    "reels_demo": Account("reels_demo", Platform.INSTAGRAM_REELS, "17841400000000000"),
    "tiktok_demo": Account("tiktok_demo", Platform.TIKTOK, "@cuenta_demo"),
    "sin_id": Account("sin_id", Platform.YOUTUBE_SHORTS, None),
}


def _admision(*, origen_real: bool = False) -> PublishAdmissionReport:
    """Informe de admision ya resuelto: aqui se prueba el plan, no la admision."""
    informe = PublishAdmissionReport(targets=(Platform.YOUTUBE_SHORTS,))
    informe.contract_valid = True
    informe.checks = {
        "cadena_contractualmente_valida": True,
        "montaje_tecnicamente_admisible": True,
        "origen_de_produccion": origen_real,
        "verificacion_de_protocolo": False,
    }
    if not origen_real:
        informe.failures.append(("origen_de_produccion", "el render es un preview"))
    informe.sources = publish_sources()
    return informe


def _peticion(plataforma: Platform, alias: str, **cambios) -> DestinationRequest:
    base = dict(
        destination_id=alias,
        platform=plataforma,
        account_alias=alias,
        local_time="2026-09-24T18:30:00",
        timezone="Europe/Madrid",
        visibility=Visibility.PRIVATE,
    )
    base.update(cambios)
    return DestinationRequest(**base)


def _plan(peticiones, *, guion: Path = GUION, settings: Settings, **cambios):
    return build_publication_plan(
        admission=cambios.pop("admision", _admision()),
        script_path=guion,
        requests=peticiones,
        accounts=CUENTAS,
        mode=cambios.pop("mode", PublishMode.MOCK),
        publish_key=cambios.pop("publish_key", "demo-001"),
        settings=settings,
        clock=ManualClock(AHORA),
        **cambios,
    )


def test_el_texto_se_copia_del_guion_sin_tocarlo(settings: Settings) -> None:
    guion = json.loads(GUION.read_text(encoding="utf-8"))
    entrada = next(
        e for e in guion["publishing"] if e["platform"] == "instagram_reels"
    )
    plan = _plan([_peticion(Platform.INSTAGRAM_REELS, "reels_demo")], settings=settings)
    destino = plan.destinations[0]

    assert destino.metadata.title == entrada["title"]
    assert destino.metadata.description == entrada["caption"]
    assert destino.metadata.hashtags == entrada["hashtags"]
    assert destino.metadata.text_source is TextSource.SCRIPT_PUBLISHING_PLAN
    assert destino.metadata.edited_fields == []


def test_no_se_fabrican_etiquetas_a_partir_de_los_hashtags(settings: Settings) -> None:
    """El guion no tiene campo de etiquetas: el plan tampoco se lo inventa."""
    plan = _plan([_peticion(Platform.INSTAGRAM_REELS, "reels_demo")], settings=settings)
    assert plan.destinations[0].metadata.tags == []
    assert plan.destinations[0].metadata.hashtags  # los hashtags si existen


def test_sin_plan_editorial_el_destino_queda_bloqueado(settings: Settings) -> None:
    """El guion de ejemplo no trae texto para YouTube: se dice, no se rellena."""
    plan = _plan([_peticion(Platform.YOUTUBE_SHORTS, "canal_demo")], settings=settings)
    destino = plan.destinations[0]
    assert destino.state is DestinationState.BLOCKED
    assert destino.metadata.title is None
    codigos = {requisito.code for requisito in destino.pending_requirements}
    assert "sin_plan_editorial" in codigos
    assert all(
        requisito.resolution for requisito in destino.pending_requirements
    ), "cada requisito dice como resolverlo"


def test_el_canal_infantil_se_declara_infantil(settings: Settings) -> None:
    plan = _plan(
        [_peticion(Platform.YOUTUBE_SHORTS, "canal_demo")],
        guion=GUION_INFANTIL,
        settings=settings,
    )
    assert plan.destinations[0].metadata.audience is AudienceDecision.MADE_FOR_KIDS


def test_sin_decision_de_audiencia_no_se_supone_que_no(settings: Settings) -> None:
    """`made_for_kids=null` es 'sin decidir', no 'no'."""
    plan = _plan([_peticion(Platform.INSTAGRAM_REELS, "reels_demo")], settings=settings)
    destino = plan.destinations[0]
    assert destino.metadata.audience is AudienceDecision.UNDECIDED
    assert "audiencia_sin_decidir" in {
        requisito.code for requisito in destino.pending_requirements
    }


def test_la_divulgacion_sintetica_no_se_deriva_de_simulation(settings: Settings) -> None:
    """Son decisiones distintas: una es tecnica, la otra editorial."""
    plan = _plan(
        [_peticion(Platform.INSTAGRAM_REELS, "reels_demo")],
        settings=settings,
        admision=_admision(origen_real=True),
    )
    destino = plan.destinations[0]
    assert destino.metadata.synthetic_disclosure is SyntheticDisclosure.NOT_REVIEWED
    assert "divulgacion_sintetica_sin_revisar" in {
        requisito.code for requisito in destino.pending_requirements
    }


def test_una_cuenta_sin_identificador_bloquea_el_envio_real(settings: Settings) -> None:
    plan = _plan(
        [_peticion(Platform.YOUTUBE_SHORTS, "sin_id")],
        guion=GUION_INFANTIL,
        settings=settings,
    )
    requisitos = {r.code: r for r in plan.destinations[0].pending_requirements}
    assert "cuenta_sin_identificador" in requisitos
    assert requisitos["cuenta_sin_identificador"].blocks == "real"


def test_tiktok_se_marca_como_entrega_manual(settings: Settings) -> None:
    plan = _plan([_peticion(Platform.TIKTOK, "tiktok_demo")], settings=settings)
    destino = plan.destinations[0]
    assert destino.options.manual_delivery is True
    assert "entrega_manual" in {r.code for r in destino.pending_requirements}
    assert "publicar a mano" in "\n".join(plan.readable_view)


def test_instagram_declara_la_transferencia_al_almacenamiento(settings: Settings) -> None:
    plan = _plan([_peticion(Platform.INSTAGRAM_REELS, "reels_demo")], settings=settings)
    assert plan.destinations[0].options.requires_staging is True
    texto = "\n".join(plan.readable_view)
    assert "almacenamiento temporal privado" in texto
    # La vista legible no lleva URLs firmadas: no existe ninguna todavia.
    assert "X-Amz-Signature" not in texto


def test_la_hora_local_se_convierte_y_se_conserva(settings: Settings) -> None:
    plan = _plan([_peticion(Platform.INSTAGRAM_REELS, "reels_demo")], settings=settings)
    horario = plan.destinations[0].schedule
    assert horario.local_time == "2026-09-24T18:30:00"
    assert horario.timezone == "Europe/Madrid"
    assert horario.scheduled_at_utc == datetime(2026, 9, 24, 16, 30, tzinfo=UTC)


def test_una_hora_inexistente_no_produce_plan(settings: Settings) -> None:
    with pytest.raises(ScheduleError, match="no existe"):
        _plan(
            [
                _peticion(
                    Platform.INSTAGRAM_REELS,
                    "reels_demo",
                    local_time="2026-03-29T02:30:00",
                )
            ],
            settings=settings,
        )


def test_planificar_no_modifica_los_documentos_de_origen(settings: Settings) -> None:
    antes = sha256_file(GUION)
    _plan([_peticion(Platform.INSTAGRAM_REELS, "reels_demo")], settings=settings)
    assert sha256_file(GUION) == antes


def test_el_plan_se_escribe_y_se_relee_igual(settings: Settings, tmp_path: Path) -> None:
    plan = _plan([_peticion(Platform.INSTAGRAM_REELS, "reels_demo")], settings=settings)
    destino = tmp_path / "publication_plan.json"
    firma = write_plan(destino, plan)
    releido, firma_leida = load_plan(destino)
    assert firma == firma_leida
    assert releido.compute_intent_fingerprint() == plan.compute_intent_fingerprint()


def test_editar_el_plan_sube_la_revision(settings: Settings, tmp_path: Path) -> None:
    """Editar es parte del flujo; lo que no vale es que nadie se entere."""
    plan = _plan([_peticion(Platform.INSTAGRAM_REELS, "reels_demo")], settings=settings)
    destino = tmp_path / "publication_plan.json"
    write_plan(destino, plan)

    datos = json.loads(destino.read_text(encoding="utf-8"))
    datos["destinations"][0]["metadata"]["title"] = "Un titulo que escribio el operador"
    destino.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")

    editado, _ = load_plan(destino)
    normalizado, cambio = normalize_plan(editado)
    assert cambio is True
    assert normalizado.revision == plan.revision + 1
    assert normalizado.fingerprint_matches()

    sin_cambios, otra_vez = normalize_plan(normalizado)
    assert otra_vez is False
    assert sin_cambios.revision == normalizado.revision


def test_el_plan_declara_que_no_tiene_efectos_externos(settings: Settings) -> None:
    plan = _plan([_peticion(Platform.INSTAGRAM_REELS, "reels_demo")], settings=settings)
    assert any("no tiene efectos externos" in nota for nota in plan.notes)
    assert plan.admission.admissible_for_real_dispatch is False
    assert plan.admission.admissible_for_simulation is True
