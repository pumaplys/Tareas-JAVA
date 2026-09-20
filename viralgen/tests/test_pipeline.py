"""Recorrido completo del pipeline, idempotencia, reparacion y recuperacion.

Todas las pruebas usan el proveedor simulado: sin red y sin claves.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from viralgen.errors import (
    DiskSpaceError,
    ExitCode,
    IdempotencyConflictError,
    ProfileError,
    WorkerLockedError,
)
from viralgen.pipeline import JobRequest, Pipeline
from viralgen.profiles import get_profile
from viralgen.providers.mock_provider import MockProvider
from viralgen.schemas.common import ProductionStatus
from viralgen.schemas.document import ScriptDocument
from viralgen.storage import ProcessLock, Storage
from viralgen.validation import validate_document

PERFILES = [
    ("infantil_cuentos", None),
    ("curiosidades_corto", "demo"),
    ("curiosidades_largo", "demo"),
]


@pytest.mark.parametrize(("profile_id", "pack"), PERFILES, ids=[p for p, _ in PERFILES])
def test_recorrido_completo_simulado(run_pipeline, demo_pack, profile_id, pack) -> None:
    """Un recorrido por perfil que exporta y vuelve a validar su JSON."""
    resultado = run_pipeline(
        profile_id=profile_id,
        topic="pieza que reparte la fuerza",
        source_pack=demo_pack if pack else None,
        job_key=f"completo-{profile_id}",
    )
    assert resultado.exit_code == ExitCode.OK
    assert resultado.production_status == ProductionStatus.READY_FOR_PRODUCTION.value
    assert resultado.warnings == []

    ruta = Path(resultado.script_path)
    assert ruta.is_file()
    documento = ScriptDocument.model_validate(json.loads(ruta.read_text(encoding="utf-8")))

    assert documento.simulation is True
    assert documento.schema_version == "1.0"
    assert documento.video.actual_duration_s is None
    assert documento.provenance.provider == "mock"

    perfil = get_profile(profile_id)
    informe = validate_document(documento, profile=perfil, allowed_facts=None)
    assert informe.issues == []

    # La narracion es exactamente la concatenacion de las escenas.
    assert documento.narration.full_text == " ".join(
        escena.narration_text for escena in documento.scenes
    )
    # Los tiempos encadenan sin huecos.
    assert documento.scenes[0].estimated_start_s == 0.0
    assert documento.scenes[-1].estimated_end_s == documento.video.estimated_duration_s
    # Se respeta el presupuesto de escenas de video del perfil.
    videos = [e for e in documento.scenes if e.visual.asset_type.value == "video"]
    assert len(videos) <= perfil.video_scene_budget
    # Ninguna publicacion promete monetizacion ni fija horario.
    for item in documento.publishing:
        assert item.ai_disclosure_review_required is True


def test_infantil_no_necesita_fuentes(run_pipeline) -> None:
    resultado = run_pipeline(job_key="ficcion")
    documento = ScriptDocument.model_validate(
        json.loads(Path(resultado.script_path).read_text(encoding="utf-8"))
    )
    assert documento.evidence.claims == []
    assert documento.evidence.facts == []
    assert documento.evidence.verification_level.value == "none"
    assert documento.publishing[0].made_for_kids is True


def test_curiosidades_enlaza_cada_afirmacion(run_pipeline, demo_pack) -> None:
    resultado = run_pipeline(
        profile_id="curiosidades_corto", source_pack=demo_pack, job_key="evidencia"
    )
    documento = ScriptDocument.model_validate(
        json.loads(Path(resultado.script_path).read_text(encoding="utf-8"))
    )
    assert documento.evidence.claims
    ids_en_documento = {hecho.fact_id for hecho in documento.evidence.facts}
    for afirmacion in documento.evidence.claims:
        assert set(afirmacion.fact_ids) <= ids_en_documento
    assert documento.evidence.verification_level.value == "source_pack_only"
    assert documento.control.source_pack_hash is not None


def test_reutilizacion_de_job_key(run_pipeline) -> None:
    primero = run_pipeline(job_key="misma-clave")
    assert primero.calls["used"] == 2  # ideas + guion

    segundo = run_pipeline(job_key="misma-clave")
    assert segundo.reused is True
    assert segundo.job_id == primero.job_id
    assert segundo.calls["used"] == 0  # no se vuelve a llamar al proveedor
    assert segundo.script_path == primero.script_path


def test_conflicto_de_parametros(run_pipeline) -> None:
    run_pipeline(job_key="clave-conflicto", topic="aprender a compartir")
    with pytest.raises(IdempotencyConflictError) as exc:
        run_pipeline(job_key="clave-conflicto", topic="otro tema distinto")
    assert exc.value.exit_code == ExitCode.CONFLICT


def test_recuperacion_de_exportacion_perdida(run_pipeline) -> None:
    """Un fallo escribiendo el archivo se recupera desde SQLite."""
    primero = run_pipeline(job_key="recuperar")
    ruta = Path(primero.script_path)
    ruta.unlink()
    assert not ruta.exists()

    segundo = run_pipeline(job_key="recuperar")
    assert segundo.reused is True
    assert segundo.calls["used"] == 0
    assert ruta.is_file()
    assert json.loads(ruta.read_text(encoding="utf-8"))["job_id"] == primero.job_id


def test_espacio_insuficiente(settings, run_pipeline) -> None:
    settings.min_free_disk_mb = 10**9  # un petabyte: imposible
    with pytest.raises(DiskSpaceError) as exc:
        run_pipeline(job_key="sin-disco")
    assert exc.value.exit_code == ExitCode.DISK


def test_needs_research_sin_catalogo(run_pipeline) -> None:
    resultado = run_pipeline(
        profile_id="curiosidades_corto", source_pack=None, job_key="sin-fuentes"
    )
    assert resultado.exit_code == ExitCode.NEEDS_RESEARCH
    assert resultado.status == "needs_research"
    assert resultado.calls["used"] == 0  # se corta antes de cualquier llamada
    assert resultado.script_path is None


def test_needs_research_sin_hechos_aprobados(run_pipeline, pending_pack) -> None:
    resultado = run_pipeline(
        profile_id="curiosidades_corto", source_pack=pending_pack, job_key="pendientes"
    )
    assert resultado.exit_code == ExitCode.NEEDS_RESEARCH
    assert resultado.calls["used"] == 0
    assert "rejected" in resultado.details


def test_catalogo_de_prueba_rechazado_en_modo_real(settings, demo_pack) -> None:
    """demo_only=true solo se acepta en simulacion."""
    peticion = JobRequest(
        command="generate",
        profile_id="curiosidades_corto",
        topic="pieza",
        source_pack=demo_pack,
        simulation=False,  # modo real
        job_key="demo-en-real",
    )
    # Se inyecta el proveedor simulado para no necesitar claves: lo que se
    # comprueba es que el trabajo se corta ANTES de emitir ninguna peticion.
    resultado = Pipeline(settings, peticion, provider=MockProvider(seed=1)).run()
    assert resultado.exit_code == ExitCode.NEEDS_RESEARCH
    assert resultado.calls["used"] == 0


def test_ideas_si_puede_proponer_sin_fuentes(run_pipeline) -> None:
    resultado = run_pipeline(
        command="ideas", profile_id="curiosidades_corto", source_pack=None, job_key="ideas-sin"
    )
    assert resultado.exit_code == ExitCode.OK
    assert resultado.script_path is None
    datos = json.loads(Path(resultado.ideas_path).read_text(encoding="utf-8"))
    assert datos["verified"] is False
    assert datos["candidates"]
    assert all(candidato["research_only"] for candidato in datos["candidates"])
    assert resultado.calls["used"] == 1  # una sola llamada, sin desarrollar escenas


def test_ideas_reutiliza_la_logica_de_candidatos(run_pipeline) -> None:
    resultado = run_pipeline(command="ideas", job_key="ideas-infantil")
    datos = json.loads(Path(resultado.ideas_path).read_text(encoding="utf-8"))
    candidato = datos["candidates"][0]
    assert set(candidato["score_components"]) == {
        "hook",
        "clarity",
        "payoff",
        "visual_potential",
        "novelty",
    }
    assert 0 <= candidato["score"] <= 100
    assert datos["selected_idea_ref"]


def test_duracion_dentro_del_rango(run_pipeline) -> None:
    resultado = run_pipeline(duration_s=42, job_key="duracion-ok")
    documento = ScriptDocument.model_validate(
        json.loads(Path(resultado.script_path).read_text(encoding="utf-8"))
    )
    assert documento.video.target_duration_s == 42.0
    assert abs(documento.video.estimated_duration_s - 42.0) <= 4.2


def test_duracion_fuera_del_rango(run_pipeline) -> None:
    with pytest.raises(ProfileError):
        run_pipeline(duration_s=200, job_key="duracion-mala")


def test_bloqueo_de_proceso(settings, run_pipeline) -> None:
    directorio = settings.effective_data_dir(simulation=True)
    directorio.mkdir(parents=True, exist_ok=True)
    with ProcessLock(directorio / "worker.lock"):
        with pytest.raises(WorkerLockedError) as exc:
            run_pipeline(job_key="bloqueado")
    assert exc.value.exit_code == ExitCode.LOCKED


def test_espacio_de_datos_separado_en_simulacion(settings, run_pipeline) -> None:
    resultado = run_pipeline(job_key="separado")
    assert settings.simulation_data_subdir in resultado.script_path
    assert not (settings.data_dir / "viralgen.sqlite3").exists()


def test_determinismo_con_semilla(tmp_path) -> None:
    """Misma semilla y mismas entradas -> misma salida.

    El historial es una entrada mas, asi que cada ejecucion usa su propio
    espacio de datos: si se compartiera, la segunda veria la idea de la primera
    y elegiria otra, que es justo lo que queremos que ocurra en produccion.
    """
    from viralgen.config import Settings

    textos = []
    for indice in (1, 2):
        ajustes = Settings(
            _env_file=None, data_dir=tmp_path / f"datos{indice}", min_free_disk_mb=0,
            log_level="ERROR",
        )
        peticion = JobRequest(
            command="generate",
            profile_id="infantil_cuentos",
            topic="aprender a compartir",
            simulation=True,
            seed=99,
            job_key="determinismo",
        )
        resultado = Pipeline(ajustes, peticion).run()
        textos.append(
            json.loads(Path(resultado.script_path).read_text(encoding="utf-8"))["narration"][
                "full_text"
            ]
        )
    assert textos[0] == textos[1]


def test_el_historial_alimenta_la_novedad(settings, run_pipeline) -> None:
    run_pipeline(job_key="hist-1", seed=5)
    run_pipeline(job_key="hist-2", seed=5)
    almacen = Storage(settings.effective_data_dir(simulation=True))
    with almacen:
        historial = almacen.recent_history("infantil", days=90, limit=30)
        candidatos = almacen.load_candidates(
            almacen.get_job_by_key("hist-2")["job_id"]
        )
    assert len(historial) == 2
    # La segunda tanda ya no puede tener novedad maxima en todo.
    assert any(candidato["score_components"]["novelty"] < 5.0 for candidato in candidatos)


def test_uso_registrado_en_sqlite(settings, run_pipeline) -> None:
    resultado = run_pipeline(job_key="uso")
    almacen = Storage(settings.effective_data_dir(simulation=True))
    with almacen:
        totales = almacen.usage_totals(resultado.job_id)
    assert totales["calls"] == 2
    assert totales["input_tokens"] > 0


# ---------------------------------------------------------------------------
# Proveedores simulados que fallan a proposito
# ---------------------------------------------------------------------------


class ProveedorQueRompeElGuion(MockProvider):
    """Devuelve un guion con una referencia rota la primera vez."""

    def __init__(self, seed: int | None = None) -> None:
        super().__init__(seed=seed)
        self.etapas: list[str] = []

    def generate_structured(self, request, budget):
        resultado = super().generate_structured(request, budget)
        self.etapas.append(request.stage)
        if request.stage == "script":
            resultado.parsed.scenes[0].claim_refs = ["cl_que_no_existe"]
        return resultado


def test_una_sola_reparacion(settings, demo_pack) -> None:
    proveedor = ProveedorQueRompeElGuion(seed=3)
    peticion = JobRequest(
        command="generate",
        profile_id="curiosidades_corto",
        topic="pieza",
        source_pack=demo_pack,
        simulation=True,
        job_key="reparacion",
    )
    resultado = Pipeline(settings, peticion, provider=proveedor).run()
    assert proveedor.etapas == ["ideas", "script", "repair"]
    assert resultado.calls["by_stage"]["repair"] == 1
    assert resultado.exit_code == ExitCode.OK


class ProveedorQueSiempreRompe(ProveedorQueRompeElGuion):
    def generate_structured(self, request, budget):
        resultado = MockProvider.generate_structured(self, request, budget)
        self.etapas.append(request.stage)
        if request.stage in {"script", "repair"}:
            resultado.parsed.scenes[0].claim_refs = ["cl_que_no_existe"]
        return resultado


def test_sin_exportacion_si_la_reparacion_no_arregla(settings, demo_pack) -> None:
    proveedor = ProveedorQueSiempreRompe(seed=3)
    peticion = JobRequest(
        command="generate",
        profile_id="curiosidades_corto",
        topic="pieza",
        source_pack=demo_pack,
        simulation=True,
        job_key="reparacion-fallida",
    )
    resultado = Pipeline(settings, peticion, provider=proveedor).run()
    assert resultado.exit_code == ExitCode.VALIDATION
    assert resultado.status == "failed"
    assert resultado.script_path is None
    # Una sola reparacion: no hay ciclos abiertos.
    assert proveedor.etapas.count("repair") == 1
    directorio = settings.effective_data_dir(simulation=True) / "jobs" / resultado.job_id
    assert not (directorio / "script.json").exists()


class ProveedorSinIdeasValidas(MockProvider):
    """Devuelve ideas vacias, que el filtro local descarta siempre."""

    def __init__(self, seed: int | None = None) -> None:
        super().__init__(seed=seed)
        self.tandas = 0

    def generate_structured(self, request, budget):
        resultado = super().generate_structured(request, budget)
        if request.stage == "ideas":
            self.tandas += 1
            for idea in resultado.parsed.ideas:
                idea.title = ""
                idea.premise = ""
        return resultado


def test_solo_una_tanda_adicional_de_ideas(settings) -> None:
    proveedor = ProveedorSinIdeasValidas(seed=3)
    peticion = JobRequest(
        command="generate",
        profile_id="infantil_cuentos",
        topic="compartir",
        simulation=True,
        job_key="sin-ideas",
    )
    resultado = Pipeline(settings, peticion, provider=proveedor).run()
    assert proveedor.tandas == 2  # la original mas UNA sola tanda adicional
    assert resultado.exit_code == ExitCode.VALIDATION
    assert resultado.status == "failed"


def test_presupuesto_de_llamadas_por_trabajo(settings) -> None:
    settings.max_calls_per_job = 1
    peticion = JobRequest(
        command="generate",
        profile_id="infantil_cuentos",
        topic="compartir",
        simulation=True,
        job_key="presupuesto",
    )
    resultado = Pipeline(settings, peticion, provider=MockProvider(seed=1)).run()
    assert resultado.exit_code == ExitCode.PROVIDER
    assert resultado.error_code == "call_budget_exceeded"
    assert resultado.calls["used"] == 1
