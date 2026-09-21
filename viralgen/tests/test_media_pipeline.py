"""Recorrido del modulo 3 con proveedores simulados: sin red y sin claves."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from viralgen.errors import ExitCode, IdempotencyConflictError, WorkerLockedError
from viralgen.media.pipeline import MediaJobRequest, MediaPipeline
from viralgen.media.providers.base import MediaOutcomeUnknownError, MediaPermanentError
from viralgen.media.providers.mock import MockImageProvider
from viralgen.media.schemas import MediaManifest, MediaStatus
from viralgen.media.storage import MediaStorage
from viralgen.storage import ProcessLock, Storage


def _manifest(outcome) -> MediaManifest:
    return MediaManifest.model_validate(
        json.loads(Path(outcome.manifest_path).read_text(encoding="utf-8"))
    )


# ---------------------------------------------------------------------------
# Recorrido completo de imagenes
# ---------------------------------------------------------------------------


def test_recorrido_mock_de_imagenes(media_settings, media_inputs, run_media) -> None:
    guion, voz = media_inputs
    resultado = run_media(guion, voz)

    assert resultado.exit_code == ExitCode.OK
    assert resultado.status == MediaStatus.READY.value
    assert resultado.partial is False
    manifiesto = _manifest(resultado)
    assert manifiesto.document_type == "media_manifest"
    assert manifiesto.schema_version == "1.0"
    assert manifiesto.simulation is True
    # Una simulacion nunca es admisible para montaje.
    assert manifiesto.control.admissible_for_assembly is False
    assert manifiesto.control.visual_review.value == "not_performed"

    documento = json.loads(guion.read_text(encoding="utf-8"))
    assert len(manifiesto.scenes) == len(documento["scenes"])
    assert manifiesto.inspection.contact_sheet_path == "contact_sheet.jpg"
    base = Path(resultado.manifest_path).parent
    assert (base / "contact_sheet.jpg").is_file()

    # Para imagenes, la duracion intrinseca y los fps son null.
    for asset in manifiesto.assets:
        if asset.kind.value == "image":
            assert asset.intrinsic_duration_s is None
            assert asset.fps is None


def test_las_fuentes_no_cambian_ni_un_byte(media_settings, media_inputs, run_media) -> None:
    guion, voz = media_inputs
    antes_guion = guion.read_bytes()
    antes_voz = voz.read_bytes()
    run_media(guion, voz)
    assert guion.read_bytes() == antes_guion
    assert voz.read_bytes() == antes_voz


def test_la_cobertura_usa_las_muestras_de_la_voz(media_settings, media_inputs, run_media) -> None:
    guion, voz = media_inputs
    manifiesto = _manifest(run_media(guion, voz))
    voz_datos = json.loads(voz.read_text(encoding="utf-8"))
    escenas_voz = {escena["scene_id"]: escena for escena in voz_datos["scenes"]}

    cursor = 0
    for entrada in sorted(manifiesto.scenes, key=lambda item: item.order):
        fuente = escenas_voz[entrada.scene_id]
        assert entrada.start_sample == cursor == fuente["start_sample"]
        assert entrada.end_sample == fuente["end_sample"]
        # La pausa esta contada una sola vez, dentro del intervalo de la voz.
        assert entrada.end_sample - entrada.start_sample == (
            fuente["clip_samples"] + fuente["pause_samples"]
        )
        cursor = entrada.end_sample
    assert cursor == voz_datos["master"]["sample_count"]
    assert manifiesto.timeline.total_samples == voz_datos["master"]["sample_count"]


def test_la_presentacion_queda_pendiente_para_el_modulo_4(
    media_settings, media_inputs, run_media
) -> None:
    manifiesto = _manifest(run_media(*media_inputs))
    for entrada in manifiesto.scenes:
        assert entrada.presentation.applied is False
        assert entrada.presentation.decided_by == "module_3_plan"
        assert entrada.presentation.target_width == 1080
        assert entrada.presentation.target_height == 1920


# ---------------------------------------------------------------------------
# Referencias
# ---------------------------------------------------------------------------


def test_referencias_resueltas_y_personajes_ausentes_excluidos(
    media_settings, media_inputs, run_media
) -> None:
    guion, voz = media_inputs
    manifiesto = _manifest(run_media(guion, voz))
    documento = json.loads(guion.read_text(encoding="utf-8"))

    usados = {cid for escena in documento["scenes"] for cid in escena["character_ids"]}
    referenciados = {entrada.character_id for entrada in manifiesto.references.entries}
    assert referenciados == usados

    # Cada escena solo adjunta referencias de personajes PRESENTES.
    por_id = {asset.asset_id: asset for asset in manifiesto.assets}
    for escena in documento["scenes"]:
        asset = por_id[f"img_{escena['scene_id']}"]
        assert set(asset.reference_asset_ids) == {
            f"ref_{cid}" for cid in escena["character_ids"]
        }


def test_las_referencias_se_reutilizan_entre_episodios(
    media_settings, media_inputs, run_media
) -> None:
    """Cambiar de episodio no redibuja a los personajes."""
    guion, voz = media_inputs
    primero = run_media(guion, voz, media_key="ep1")
    generadas = primero.usage["used_this_run"]["generation_attempts"]

    segundo = run_media(guion, voz, media_key="ep2")
    # Misma seleccion de referencias y mismos prompts: todo sale de cache.
    assert segundo.usage["used_this_run"].get("generation_attempts", 0) == 0
    assert segundo.usage["cache_hits"] > 0
    assert generadas > 0


def test_cambiar_de_media_key_no_vuelve_a_comprar_un_asset_identico(
    media_settings, media_inputs, run_media
) -> None:
    guion, voz = media_inputs
    run_media(guion, voz, media_key="clave-a")
    with Storage(media_settings.effective_data_dir(simulation=True)) as almacen:
        antes = MediaStorage(almacen).usage(
            json.loads(guion.read_text(encoding="utf-8"))["job_id"]
        )
    segundo = run_media(guion, voz, media_key="clave-b")
    with Storage(media_settings.effective_data_dir(simulation=True)) as almacen:
        despues = MediaStorage(almacen).usage(
            json.loads(guion.read_text(encoding="utf-8"))["job_id"]
        )
    assert despues["generation_attempts"] == antes["generation_attempts"]
    assert segundo.status == MediaStatus.READY.value


def test_las_caches_real_y_simulada_estan_separadas(media_settings, media_inputs) -> None:
    from viralgen.media.planner import image_cache_key
    from viralgen.media.planner import ScenePlan

    plan = ScenePlan(
        scene_id="sc_01", order=1, requested_type="image", start_sample=0, end_sample=10,
        duration_s=1.0, character_ids=[], effective_prompt="p", prompt_chars=1,
        image_size="720x1280", image_quality="medium", image_format="jpeg", needs_video=False,
    )
    simulada = image_cache_key(plan=plan, model="m", reference_hashes=[], simulation=True)
    real = image_cache_key(plan=plan, model="m", reference_hashes=[], simulation=False)
    assert simulada != real


# ---------------------------------------------------------------------------
# Admision de entradas
# ---------------------------------------------------------------------------


class ProveedorEspia(MockImageProvider):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.llamadas = 0

    def create_image(self, request, budget):
        resultado = super().create_image(request, budget)
        self.llamadas += 1
        return resultado


def test_modo_real_bloquea_fuentes_simuladas_antes_de_http(
    media_settings, media_inputs
) -> None:
    guion, voz = media_inputs
    espia = ProveedorEspia(settings=media_settings, seed=5)
    resultado = MediaPipeline(
        media_settings,
        MediaJobRequest(
            script_path=guion, voice_path=voz, media_key="real", simulation=False
        ),
        image_provider=espia,
    ).run()
    assert resultado.exit_code == ExitCode.VALIDATION
    assert resultado.status == "blocked"
    assert resultado.error_code == "media_input_not_admitted"
    assert resultado.manifest_path is None
    assert espia.llamadas == 0


def test_preview_no_relaja_needs_review(media_settings, media_inputs, tmp_path) -> None:
    """`--mock` solo permite las excepciones de origen simulado."""
    guion, voz = media_inputs
    datos = json.loads(voz.read_text(encoding="utf-8"))
    datos["control"]["voice_status"] = "needs_review"
    datos["control"]["admissible_for_assembly"] = False
    roto = tmp_path / "voice_review.json"
    roto.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")

    espia = ProveedorEspia(settings=media_settings, seed=5)
    resultado = MediaPipeline(
        media_settings,
        MediaJobRequest(
            script_path=guion, voice_path=roto, media_key="review", simulation=True
        ),
        image_provider=espia,
    ).run()
    assert resultado.status == "blocked"
    assert espia.llamadas == 0


def test_preview_no_relaja_el_hash(media_settings, media_inputs, tmp_path) -> None:
    guion, voz = media_inputs
    datos = json.loads(guion.read_text(encoding="utf-8"))
    datos["idea"]["title"] += " (editado)"
    otro = tmp_path / "script_editado.json"
    otro.write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    espia = ProveedorEspia(settings=media_settings, seed=5)
    resultado = MediaPipeline(
        media_settings,
        MediaJobRequest(
            script_path=otro, voice_path=voz, media_key="hash", simulation=True
        ),
        image_provider=espia,
    ).run()
    assert resultado.status == "blocked"
    assert espia.llamadas == 0


# ---------------------------------------------------------------------------
# Bloqueo, presupuesto y reanudacion
# ---------------------------------------------------------------------------


class ProveedorQueFallaEn(MockImageProvider):
    """Falla de forma permanente en la escena indicada."""

    def __init__(self, fallar_en: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.fallar_en = fallar_en
        self.generadas: list[str] = []

    def create_image(self, request, budget):
        if request.scene_id == self.fallar_en:
            budget.reserve("generation", request.scene_id)
            raise MediaPermanentError(f"fallo simulado en {request.scene_id}")
        resultado = super().create_image(request, budget)
        self.generadas.append(request.operation_id)
        return resultado


def test_un_fallo_en_sc_03_detiene_las_escenas_siguientes(
    media_settings, media_inputs
) -> None:
    guion, voz = media_inputs
    escenas = [e["scene_id"] for e in json.loads(guion.read_text(encoding="utf-8"))["scenes"]]
    assert len(escenas) >= 7

    proveedor = ProveedorQueFallaEn("sc_03", settings=media_settings, seed=5)
    peticion = MediaJobRequest(
        script_path=guion, voice_path=voz, media_key="corte", simulation=True, seed=5
    )
    resultado = MediaPipeline(media_settings, peticion, image_provider=proveedor).run()

    # Se generaron las referencias y las dos primeras escenas, ni una mas.
    assert "img_sc_01" in proveedor.generadas and "img_sc_02" in proveedor.generadas
    assert not any(op.startswith("img_sc_0") and op > "img_sc_03" for op in proveedor.generadas)
    assert resultado.partial is True
    assert resultado.pending_scenes == escenas[2:]
    assert resultado.manifest_path is None
    assert resultado.exit_code == ExitCode.NEEDS_REVIEW
    bloqueantes = [issue for issue in resultado.issues if issue["blocking"]]
    assert bloqueantes and bloqueantes[-1]["scene_id"] == "sc_03"
    # Lo obtenido se conserva.
    assert resultado.available_paths
    assert all(Path(ruta).is_file() for ruta in resultado.available_paths)


def test_repetir_un_bloqueo_no_gasta_solicitudes_nuevas(media_settings, media_inputs) -> None:
    guion, voz = media_inputs
    peticion = MediaJobRequest(
        script_path=guion, voice_path=voz, media_key="corte-repetido", simulation=True, seed=5
    )
    primero = MediaPipeline(
        media_settings, peticion,
        image_provider=ProveedorQueFallaEn("sc_03", settings=media_settings, seed=5),
    ).run()
    gastadas = primero.usage["used_total"]["generation_attempts"]

    segundo_proveedor = ProveedorQueFallaEn("sc_03", settings=media_settings, seed=5)
    segundo = MediaPipeline(media_settings, peticion, image_provider=segundo_proveedor).run()
    # Las escenas previas salen de cache y la que falla vuelve a fallar.
    assert segundo_proveedor.generadas == []
    assert segundo.usage["used_total"]["generation_attempts"] == gastadas + 1
    assert segundo.partial is True


def test_el_presupuesto_persiste_entre_procesos(media_settings, media_inputs) -> None:
    guion, voz = media_inputs
    media_settings.media_max_generation_attempts = 4
    peticion = MediaJobRequest(
        script_path=guion, voice_path=voz, media_key="presupuesto", simulation=True, seed=5
    )
    primero = MediaPipeline(
        media_settings, peticion,
        image_provider=MockImageProvider(settings=media_settings, seed=5),
    ).run()
    assert primero.partial is True
    assert primero.usage["used_total"]["generation_attempts"] == 4

    espia = ProveedorEspia(settings=media_settings, seed=5)
    segundo = MediaPipeline(media_settings, peticion, image_provider=espia).run()
    assert segundo.partial is True
    assert segundo.usage["used_total"]["generation_attempts"] == 4  # no se reinicia
    assert espia.llamadas == 0


class ProveedorIncierto(MockImageProvider):
    """Deja un resultado incierto en la escena indicada."""

    def __init__(self, fallar_en: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.fallar_en = fallar_en
        self.intentos = 0

    def create_image(self, request, budget):
        if request.scene_id == self.fallar_en:
            self.intentos += 1
            budget.reserve("generation", request.scene_id)
            raise MediaOutcomeUnknownError(
                f"corte tras enviar la peticion de {request.scene_id}"
            )
        return super().create_image(request, budget)


def test_un_resultado_incierto_bloquea_la_repeticion_automatica(
    media_settings, media_inputs
) -> None:
    guion, voz = media_inputs
    peticion = MediaJobRequest(
        script_path=guion, voice_path=voz, media_key="incierto", simulation=True, seed=5
    )
    proveedor = ProveedorIncierto("sc_02", settings=media_settings, seed=5)
    primero = MediaPipeline(media_settings, peticion, image_provider=proveedor).run()
    assert primero.partial is True
    assert proveedor.intentos == 1
    assert any(issue["code"] == "outcome_unknown" for issue in primero.issues)

    # Ni siquiera desde OTRA media-key se repite automaticamente.
    otra = MediaJobRequest(
        script_path=guion, voice_path=voz, media_key="incierto-2", simulation=True, seed=5
    )
    segundo_proveedor = ProveedorIncierto("sc_02", settings=media_settings, seed=5)
    segundo = MediaPipeline(media_settings, otra, image_provider=segundo_proveedor).run()
    assert segundo_proveedor.intentos == 0
    assert segundo.partial is True
    assert any(issue["code"] == "outcome_unknown" for issue in segundo.issues)


# ---------------------------------------------------------------------------
# Idempotencia, recuperacion y concurrencia
# ---------------------------------------------------------------------------


def test_reutilizar_la_media_key_no_gasta_nada(media_settings, media_inputs, run_media) -> None:
    guion, voz = media_inputs
    primero = run_media(guion, voz, media_key="reuso")
    segundo = run_media(guion, voz, media_key="reuso")
    assert segundo.reused is True
    assert segundo.media_run_id == primero.media_run_id
    assert segundo.manifest_path == primero.manifest_path


def test_conflicto_de_media_key(media_settings, media_inputs, run_media) -> None:
    guion, voz = media_inputs
    run_media(guion, voz, media_key="clave")
    media_settings.media_geometry_policy = "crop"
    with pytest.raises(IdempotencyConflictError):
        run_media(guion, voz, media_key="clave")


def test_concurrencia_bloqueada(media_settings, media_inputs, run_media) -> None:
    guion, voz = media_inputs
    job_id = json.loads(guion.read_text(encoding="utf-8"))["job_id"]
    directorio = media_settings.effective_data_dir(simulation=True)
    directorio.mkdir(parents=True, exist_ok=True)
    with ProcessLock(directorio / f"media-{job_id}.lock"):
        with pytest.raises(WorkerLockedError):
            run_media(guion, voz, media_key="concurrente")


def test_caida_entre_archivo_y_checkpoint_no_regenera(
    media_settings, media_inputs, run_media
) -> None:
    """El archivo de cache se adopta tras validarlo, sin volver a generar."""
    guion, voz = media_inputs
    primero = run_media(guion, voz, media_key="checkpoint")
    job_id = json.loads(guion.read_text(encoding="utf-8"))["job_id"]

    # Se simula la caida: los archivos siguen, el indice de cache desaparece.
    with Storage(media_settings.effective_data_dir(simulation=True)) as almacen:
        almacen.connect().execute("DELETE FROM media_assets")
        almacen.connect().execute("DELETE FROM media_runs WHERE media_key = 'checkpoint'")
        almacen.connect().execute("DELETE FROM media_references")

    espia = ProveedorEspia(settings=media_settings, seed=5)
    segundo = MediaPipeline(
        media_settings,
        MediaJobRequest(
            script_path=guion, voice_path=voz, media_key="checkpoint",
            simulation=True, seed=5,
        ),
        image_provider=espia,
    ).run()
    assert segundo.status == MediaStatus.READY.value
    assert espia.llamadas == 0  # nada se regenera
    assert Path(segundo.manifest_path).is_file()


def test_exportacion_fallida_se_recupera_sin_regenerar(
    media_settings, media_inputs, monkeypatch
) -> None:
    import viralgen.media.pipeline as modulo

    guion, voz = media_inputs

    def explota(path, data):
        raise OSError("disco de prueba lleno")

    monkeypatch.setattr(modulo, "atomic_write_json", explota)
    peticion = MediaJobRequest(
        script_path=guion, voice_path=voz, media_key="export", simulation=True, seed=5
    )
    fallido = MediaPipeline(
        media_settings, peticion,
        image_provider=MockImageProvider(settings=media_settings, seed=5),
    ).run()
    assert fallido.status == "failed"
    assert fallido.manifest_path is None

    monkeypatch.undo()
    espia = ProveedorEspia(settings=media_settings, seed=5)
    recuperado = MediaPipeline(media_settings, peticion, image_provider=espia).run()
    assert recuperado.status == MediaStatus.READY.value
    assert espia.llamadas == 0
    assert Path(recuperado.manifest_path).is_file()


def test_el_paquete_sobrevive_sin_indice_de_cache(
    media_settings, media_inputs, run_media
) -> None:
    """Los enlaces fisicos mantienen vivos los archivos del paquete."""
    guion, voz = media_inputs
    resultado = run_media(guion, voz, media_key="sin-indice")
    base = Path(resultado.manifest_path).parent
    manifiesto = _manifest(resultado)

    import shutil

    shutil.rmtree(media_settings.effective_data_dir(simulation=True) / "media-cache")
    for asset in manifiesto.assets:
        ruta = base / asset.path
        assert ruta.is_file(), asset.asset_id
