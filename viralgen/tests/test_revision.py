"""`viralgen revise`: revision editorial local, sin red ni proveedores.

El caso bajo prueba reproduce el real descrito por el operador: siete escenas
de imagen a 125 wpm, 109 palabras y 56,12 s estimados, con el guion en
`needs_review`. Las tres ediciones editoriales lo dejan en 101 palabras y
52,28 s.

Los dos avisos del original son los que las ediciones resuelven de verdad:

* `enfasis_inexistente`: la palabra destacada de escena_7 no aparece en su
  narracion.
* `duracion_fuera_de_tolerancia`: 56,12 s se aleja mas del 10 % del objetivo
  de 50 s.

Por eso el documento revisado queda sin incidencias: **no porque se hayan
borrado los avisos**, sino porque el contenido dejo de incumplir.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import SecretStr

from viralgen.assembly import apply_validation_result
from viralgen.config import Settings
from viralgen.errors import ConfigError
from viralgen.profiles import get_profile
from viralgen.schemas.document import ScriptDocument
from viralgen.storage import Storage, iso, utcnow
from viralgen.textutil import count_words, sha256_text
from viralgen.validation import validate_document
from viralgen import revision as rev

PERFIL = "infantil_cuentos"
JOB_KEY = "nubaria-001-puente-con-hipo"
JOB_ID = "bb3a5a11-8c8e-43aa-baf7-fe5e21a7604a"
PROMESA = "Descubrir de donde sale un toc que nadie encuentra en el puente."

#: Narracion del original. Suma 109 palabras; las cinco escenas que NO se
#: editan suman 74, asi que tras los cambios quedan 74 + 10 + 17 = 101.
TEXTOS_ORIGINALES = {
    # Empieza con el gancho seleccionado, literal: lo exige la validacion.
    "escena_1": (
        "Lumi oyó un toc muy raro al cruzar el viejo puente de madera, justo "
        "debajo de sus patas."
    ),
    "escena_2": (
        "Se detuvo en seco y miró alrededor, pero el bosque entero parecía "
        "completamente callado."
    ),
    "escena_3": (
        "Tobi llegó rodando entre las hojas secas y preguntó qué estaba "
        "pasando allí arriba."
    ),
    "escena_4": (
        "Los dos apoyaron la oreja en la madera tibia y esperaron un rato "
        "sin respirar."
    ),
    "escena_5": (
        "Nube bajó desde una rama alta y les dijo que el puente no tenía "
        "ningún hipo."
    ),
    "escena_6": "Lumi se quitó la mochila y la dejó encima del tablón.",
    "escena_7": (
        "Entonces los tres entendieron que el ruido venía de dentro de la "
        "mochila de Lumi, y se echaron todos a reír."
    ),
}

#: Palabra destacada de cada escena en el original. La de escena_7 NO aparece
#: en su narracion: es uno de los dos avisos que la revision resuelve.
ENFASIS_ORIGINALES = {
    "escena_1": ["puente"],
    "escena_2": [],
    "escena_3": ["hojas"],
    "escena_4": ["madera"],
    "escena_5": ["rama"],
    "escena_6": ["mochila"],
    "escena_7": ["bellota"],
}

PAUSAS = {
    "escena_1": 0.5,
    "escena_2": 0.5,
    "escena_3": 0.5,
    "escena_4": 0.6,
    "escena_5": 0.6,
    "escena_6": 0.5,
    "escena_7": 0.6,
}

#: Las tres ediciones del encargo, literales.
EDICION_2 = "Al pisar la hierba, Lumi levantó las orejas. ¡Otro toc!"
EDICION_7 = (
    "Metió la bellota otra vez. ¡Toc! «¡Era mi mochila! Sin bellota, una taza. "
    "Con bellota… ¡una maraca!»"
)
EDICION_ENFASIS = ["mochila", "maraca"]

#: Gancho seleccionado. Aparece literal al principio de escena_1 y dura 1,9 s,
#: por debajo del limite de 3 s.
GANCHO = "Lumi oyo un toc"


def test_el_caso_cuadra_con_las_cifras_declaradas() -> None:
    """Antes de probar nada: que el fixture sea el caso, no otro parecido."""
    recuentos = {k: count_words(v) for k, v in TEXTOS_ORIGINALES.items()}
    assert sum(recuentos.values()) == 109
    otras = sum(v for k, v in recuentos.items() if k not in ("escena_2", "escena_7"))
    assert otras == 74
    assert count_words(EDICION_2) == 10
    assert count_words(EDICION_7) == 17
    assert otras + 10 + 17 == 101
    assert round(sum(PAUSAS.values()), 3) == 3.8


# ---------------------------------------------------------------------------
# Fixture: el trabajo tal como estaria en disco y en SQLite
# ---------------------------------------------------------------------------


def _documento_base(tmp_path: Path) -> dict:
    """Un documento estructuralmente valido, generado con el proveedor simulado.

    Se usa como ESQUELETO: de el salen la biblia, los metadatos y la forma del
    contrato. Las narraciones y los identificadores de escena se sustituyen
    despues por los del caso.
    """
    from viralgen.pipeline import JobRequest, Pipeline

    ajustes = Settings(
        _env_file=None,
        data_dir=tmp_path / "semilla",
        min_free_disk_mb=0,
        log_level="ERROR",
        profiles_path=Path("examples/perfiles_solo_imagenes.json"),
    )
    resultado = Pipeline(
        ajustes,
        JobRequest(
            command="generate",
            profile_id=PERFIL,
            topic="el puente con hipo",
            simulation=True,
            job_key="semilla",
            seed=11,
        ),
    ).run()
    return json.loads(Path(resultado.script_path).read_text(encoding="utf-8"))


def _construir_original(tmp_path: Path) -> tuple[Settings, Storage, Path, dict]:
    """Deja el trabajo en SQLite y en disco, coherentes entre si."""
    base = _documento_base(tmp_path)
    assert len(base["scenes"]) == 7, "el perfil infantil simulado da 7 escenas"

    documento = json.loads(json.dumps(base))
    documento["job_id"] = JOB_ID
    # El caso del operador es un documento de produccion, no simulado.
    documento["simulation"] = False
    for indice, escena in enumerate(documento["scenes"], start=1):
        scene_id = f"escena_{indice}"
        escena["scene_id"] = scene_id
        escena["narration_text"] = TEXTOS_ORIGINALES[scene_id]
        escena["pause_after_s"] = PAUSAS[scene_id]
        # Cada escena destaca una palabra que SI aparece en su narracion,
        # salvo escena_7: su palabra ("bellota") no esta en el texto viejo, y de
        # ahi sale el aviso que la revision resuelve de verdad.
        # escena_2 se queda sin destacadas a proposito: su texto se sustituye
        # entero, y cualquier palabra del viejo dejaria de aparecer.
        escena["captions"]["emphasis_words"] = ENFASIS_ORIGINALES[scene_id]
    documento["idea"]["hook_variants"][0] = {"hook_id": "hook_a", "text": GANCHO}
    documento["idea"]["selected_hook_id"] = "hook_a"
    documento["loop"] = {
        "enabled": False,
        "opening_scene_id": None,
        "closing_scene_id": None,
        "connection_explanation": None,
    }

    perfil = get_profile(PERFIL, Path("examples/perfiles_solo_imagenes.json"))
    modelo = ScriptDocument.model_validate(documento)
    rev.recompute(modelo, target_wpm=perfil.target_wpm)
    informe = validate_document(modelo, profile=perfil, allowed_facts=None, promise=PROMESA)
    apply_validation_result(
        modelo,
        warnings=[problema.message for problema in informe.issues],
        has_evidence_doubt=False,
    )
    datos = modelo.model_dump(mode="json")

    ajustes = Settings(
        _env_file=None,
        data_dir=tmp_path / "datos",
        min_free_disk_mb=0,
        log_level="ERROR",
        profiles_path=Path("examples/perfiles_solo_imagenes.json"),
    )
    data_dir = ajustes.effective_data_dir(simulation=False)
    storage = Storage(data_dir)
    storage.connect()

    storage.create_job(
        {
            "job_id": JOB_ID,
            "job_key": JOB_KEY,
            "command": "generate",
            "profile_id": PERFIL,
            "channel": "infantil",
            "topic": "el puente con hipo",
            "duration_s": 50.0,
            "simulation": 0,
            "fingerprint": sha256_text("fixture"),
            "request_json": "{}",
            "status": "succeeded",
            "provider": "openai",
            "model": "gpt-6.1-terra",
            "prompt_version": datos["control"]["prompt_version"],
            "config_hash": datos["control"]["config_hash"],
            "source_pack_hash": None,
        }
    )
    storage.save_candidates(
        JOB_ID,
        [
            {
                "idea_ref": "idea_1",
                "title": "El puente con hipo",
                "premise": "Un toc que nadie encuentra.",
                "topic": "el puente con hipo",
                "promise": PROMESA,
                "possible_ending": "Era la bellota de la mochila.",
                "visual_concept": "Puente de madera en el bosque.",
                "educational_goal": "Escuchar antes de concluir.",
                "fact_ids": [],
                "score": 8.5,
                "score_components": {
                    "hook": 8.0,
                    "clarity": 9.0,
                    "payoff": 8.0,
                    "visual_potential": 9.0,
                    "novelty": 1.0,
                },
                "score_rationale": "fixture",
                "max_similarity": 0.0,
                "similar_to": None,
                "norm_hash": sha256_text("idea_1"),
                "rejected_reason": None,
                "batch_index": 1,
                "notes": [],
            }
        ],
        batch_index=1,
    )
    storage.mark_selected(JOB_ID, "idea_1")
    storage.save_script(JOB_ID, datos, datos["control"]["production_status"])

    carpeta = data_dir / "jobs" / JOB_ID
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / "script.json"
    ruta.write_text(
        json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    storage.record_export(
        JOB_ID,
        path=str(ruta),
        sha256=sha256_text(json.dumps(datos, ensure_ascii=False, sort_keys=True)),
        status="ok",
    )
    return ajustes, storage, ruta, datos


@pytest.fixture
def caso(tmp_path):
    ajustes, storage, ruta, datos = _construir_original(tmp_path)
    yield ajustes, storage, ruta, datos
    storage.close()


@pytest.fixture
def ediciones() -> list[rev.Edit]:
    return [
        rev.Edit("escena_2", "narration_text", EDICION_2),
        rev.Edit("escena_7", "narration_text", EDICION_7),
        rev.Edit("escena_7", "captions.emphasis_words", EDICION_ENFASIS),
    ]


def _plan(ajustes, storage, ediciones):
    return rev.build_plan(
        settings=ajustes,
        storage=storage,
        job_key=JOB_KEY,
        edits=ediciones,
        note="revision editorial del operador",
        revised_by="operador_local",
        tool="viralgen revise",
    )


# ---------------------------------------------------------------------------
# El original, antes de tocarlo
# ---------------------------------------------------------------------------


def test_el_original_esta_en_needs_review_con_los_dos_avisos(caso) -> None:
    _, _, _, datos = caso
    assert datos["control"]["production_status"] == "needs_review"
    assert datos["narration"]["word_count"] == 109
    assert datos["video"]["estimated_duration_s"] == 56.12
    avisos = " | ".join(datos["control"]["warnings"])
    assert "no aparece en la narracion" in avisos
    assert "se aleja mas de un 10%" in avisos


# ---------------------------------------------------------------------------
# Previsualizacion
# ---------------------------------------------------------------------------


def test_la_previsualizacion_da_las_cifras_de_aceptacion(caso, ediciones) -> None:
    ajustes, storage, _, _ = caso
    informe = rev.describe(_plan(ajustes, storage, ediciones), applied=False)

    assert informe["mode"] == "preview"
    assert informe["original"]["word_count"] == 109
    assert informe["original"]["estimated_duration_s"] == 56.12
    assert informe["revised"]["word_count"] == 101
    assert informe["revised"]["estimated_duration_s"] == 52.28
    assert informe["delta"] == {"word_count": -8, "estimated_duration_s": -3.84}
    assert informe["revised"]["scene_count"] == 7
    assert informe["revised"]["image_scenes"] == 7
    assert informe["revised"]["video_scenes"] == 0
    # Cero incidencias, y por tanto el estado cambia por si solo.
    assert informe["issues"] == []
    assert informe["revised"]["production_status"] == "ready_for_production"
    assert informe["revised"]["warnings"] == []
    assert informe["provider_calls"] == 0


def test_la_previsualizacion_no_escribe_nada(caso, ediciones) -> None:
    ajustes, storage, ruta, _ = caso
    data_dir = ajustes.effective_data_dir(simulation=False)
    antes = {p: p.read_bytes() for p in sorted(data_dir.rglob("*.json"))}
    hash_antes = hashlib.sha256(ruta.read_bytes()).hexdigest()

    rev.describe(_plan(ajustes, storage, ediciones), applied=False)

    assert {p: p.read_bytes() for p in sorted(data_dir.rglob("*.json"))} == antes
    assert hashlib.sha256(ruta.read_bytes()).hexdigest() == hash_antes
    assert storage.list_revisions(JOB_ID) == []


def test_la_previsualizacion_declara_la_admision_como_proyectada(caso, ediciones) -> None:
    """No afirma que este exportado lo que todavia no se ha escrito."""
    ajustes, storage, _, _ = caso
    informe = rev.describe(_plan(ajustes, storage, ediciones), applied=False)
    assert "admission_for_voice_if_exported" in informe
    assert "admission_for_voice" not in informe


# ---------------------------------------------------------------------------
# Recalculo con las funciones del proyecto
# ---------------------------------------------------------------------------


def test_editar_una_escena_mueve_la_linea_temporal_de_las_siguientes(caso, ediciones) -> None:
    ajustes, storage, _, datos = caso
    plan = _plan(ajustes, storage, ediciones)

    original = {e["scene_id"]: e for e in datos["scenes"]}
    for escena in plan.revised.scenes:
        previo = original[escena.scene_id]["estimated_start_s"]
        if escena.order <= 2:
            # La 1 no se edito y la 2 empieza donde acaba la 1: no se mueven.
            assert escena.estimated_start_s == previo, escena.scene_id
        else:
            # De la 3 en adelante, el acortamiento de la 2 las adelanta.
            assert escena.estimated_start_s < previo, escena.scene_id
    # Y la cadena sigue sin huecos ni solapamientos.
    cursor = 0.0
    for escena in sorted(plan.revised.scenes, key=lambda e: e.order):
        assert abs(escena.estimated_start_s - cursor) < 0.011
        cursor = escena.estimated_end_s
    assert abs(plan.revised.video.estimated_duration_s - cursor) < 0.011


def test_la_narracion_completa_y_los_recuentos_se_recomponen(caso, ediciones) -> None:
    ajustes, storage, _, _ = caso
    plan = _plan(ajustes, storage, ediciones)

    esperado = " ".join(
        escena.narration_text
        for escena in sorted(plan.revised.scenes, key=lambda e: e.order)
    )
    assert plan.revised.narration.full_text == esperado
    assert plan.revised.narration.word_count == 101
    assert EDICION_2 in plan.revised.narration.full_text
    assert "maraca" in plan.revised.narration.full_text
    for escena in plan.revised.scenes:
        assert escena.word_count == count_words(escena.narration_text)


# ---------------------------------------------------------------------------
# Aplicar
# ---------------------------------------------------------------------------


def test_aplicar_escribe_la_revision_y_deja_el_original_intacto(caso, ediciones) -> None:
    ajustes, storage, ruta, datos = caso
    bytes_antes = ruta.read_bytes()
    hash_antes = hashlib.sha256(bytes_antes).hexdigest()

    informe = rev.apply(
        _plan(ajustes, storage, ediciones),
        storage=storage,
        note="revision editorial del operador",
        revised_by="operador_local",
        tool="viralgen revise",
    )

    assert informe["reused"] is False
    assert informe["revision_no"] == 1
    # El original, byte a byte, sigue siendo el mismo.
    assert ruta.read_bytes() == bytes_antes
    assert informe["original_untouched"]["unchanged"] is True
    assert informe["original_untouched"]["bytes_sha256_after"] == hash_antes
    # Y su fila de `scripts` tampoco cambio.
    assert storage.load_script(JOB_ID) == datos

    # La revision esta al lado, en su propio archivo.
    revisada = Path(informe["revision_path"])
    assert revisada.name == "script.revision-1.json"
    assert revisada.parent == ruta.parent
    guardado = json.loads(revisada.read_text(encoding="utf-8"))
    assert guardado["narration"]["word_count"] == 101
    assert guardado["video"]["estimated_duration_s"] == 52.28


def test_la_revision_queda_enlazada_al_sha256_de_los_bytes_originales(
    caso, ediciones
) -> None:
    ajustes, storage, ruta, _ = caso
    hash_original = hashlib.sha256(ruta.read_bytes()).hexdigest()

    informe = rev.apply(
        _plan(ajustes, storage, ediciones),
        storage=storage,
        note=None,
        revised_by="operador_local",
        tool="viralgen revise",
    )
    guardado = json.loads(Path(informe["revision_path"]).read_text(encoding="utf-8"))
    bloque = guardado["editorial_revision"]

    assert bloque["revision_of_sha256"] == hash_original
    assert bloque["revision_no"] == 1
    assert bloque["revised_by"] == "operador_local"
    assert bloque["tool"] == "viralgen revise"
    # Por contrato, una revision no puede declarar una llamada al proveedor.
    assert bloque["provider_calls"] == 0
    # Los tres cambios, con el antes y el despues por hash.
    assert len(bloque["changes"]) == 3
    campos = {(c["scene_id"], c["field"]) for c in bloque["changes"]}
    assert campos == {
        ("escena_2", "narration_text"),
        ("escena_7", "narration_text"),
        ("escena_7", "captions.emphasis_words"),
    }
    for cambio in bloque["changes"]:
        assert cambio["before_sha256"] != cambio["after_sha256"]


def test_la_procedencia_de_la_generacion_se_conserva(caso, ediciones) -> None:
    """Lo que escribio el proveedor y lo que cambio una persona no se mezclan."""
    ajustes, storage, _, datos = caso
    informe = rev.apply(
        _plan(ajustes, storage, ediciones),
        storage=storage,
        note=None,
        revised_by="operador_local",
        tool="viralgen revise",
    )
    guardado = json.loads(Path(informe["revision_path"]).read_text(encoding="utf-8"))

    assert guardado["provenance"] == datos["provenance"]
    assert guardado["control"]["prompt_version"] == datos["control"]["prompt_version"]
    assert guardado["control"]["config_hash"] == datos["control"]["config_hash"]
    assert guardado["job_id"] == datos["job_id"]
    assert guardado["created_at"] == datos["created_at"]
    # Y el original no tiene bloque de revision.
    assert datos.get("editorial_revision") is None


def test_sqlite_y_la_exportacion_concuerdan(caso, ediciones) -> None:
    ajustes, storage, _, _ = caso
    informe = rev.apply(
        _plan(ajustes, storage, ediciones),
        storage=storage,
        note=None,
        revised_by="operador_local",
        tool="viralgen revise",
    )
    filas = storage.list_revisions(JOB_ID)
    assert len(filas) == 1
    fila = filas[0]

    ruta = Path(fila["path"])
    assert ruta == Path(informe["revision_path"])
    contenido = json.loads(ruta.read_text(encoding="utf-8"))
    assert fila["sha256"] == sha256_text(
        json.dumps(contenido, ensure_ascii=False, sort_keys=True)
    )
    assert json.loads(fila["document_json"]) == contenido
    assert fila["production_status"] == "ready_for_production"


def test_repetir_no_crea_otra_revision(caso, ediciones) -> None:
    ajustes, storage, _, _ = caso
    primera = rev.apply(
        _plan(ajustes, storage, ediciones),
        storage=storage,
        note=None,
        revised_by="operador_local",
        tool="viralgen revise",
    )
    contenido_primero = Path(primera["revision_path"]).read_bytes()

    segunda = rev.apply(
        _plan(ajustes, storage, ediciones),
        storage=storage,
        note=None,
        revised_by="operador_local",
        tool="viralgen revise",
    )

    assert segunda["reused"] is True
    assert segunda["revision_no"] == primera["revision_no"] == 1
    assert len(storage.list_revisions(JOB_ID)) == 1
    # No se reescribe el archivo ya guardado.
    assert Path(primera["revision_path"]).read_bytes() == contenido_primero


def test_la_admision_para_voz_usa_el_criterio_real(caso, ediciones) -> None:
    ajustes, storage, _, _ = caso
    informe = rev.apply(
        _plan(ajustes, storage, ediciones),
        storage=storage,
        note=None,
        revised_by="operador_local",
        tool="viralgen revise",
    )
    admision = informe["admission_for_voice"]

    assert set(admision["checks"]) == {
        "documento_valido",
        "version_de_esquema_compatible",
        "exportacion_completa",
        "ready_for_production",
        "no_es_simulacion",
    }
    assert admision["admissible_for_media"] is True
    assert admision["reasons"] == []


# ---------------------------------------------------------------------------
# Lo que no se puede hacer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "campo",
    [
        "control.production_status",
        "control.warnings",
        "simulation",
        "provenance",
        "narration.word_count",
        "video.estimated_duration_s",
    ],
)
def test_no_se_puede_editar_el_estado_ni_los_avisos(tmp_path, campo) -> None:
    """Fabricar la admision a mano es justo lo que no debe poder hacerse."""
    ruta = tmp_path / "edits.json"
    ruta.write_text(
        json.dumps({"edits": [{"scene_id": "escena_1", "field": campo, "value": "x"}]}),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as excinfo:
        rev.load_edits(ruta)
    assert "NO se puede editar" in excinfo.value.message
    # El error explica por que, y que si se admite.
    assert "Campos admitidos" in excinfo.value.message


def test_un_campo_desconocido_se_rechaza(tmp_path) -> None:
    ruta = tmp_path / "edits.json"
    ruta.write_text(
        json.dumps({"edits": [{"scene_id": "escena_1", "field": "beat", "value": "hook"}]}),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="campo no editable"):
        rev.load_edits(ruta)


def test_una_edicion_sin_efecto_se_rechaza(caso) -> None:
    """Una revision que no cambia nada no se registra."""
    ajustes, storage, _, _ = caso
    sin_cambio = [
        rev.Edit("escena_2", "narration_text", TEXTOS_ORIGINALES["escena_2"]),
    ]
    with pytest.raises(ConfigError) as excinfo:
        _plan(ajustes, storage, sin_cambio)
    assert "no cambian nada" in excinfo.value.message
    assert "escena_2.narration_text" in excinfo.value.message


def test_una_escena_inexistente_se_rechaza(caso) -> None:
    ajustes, storage, _, _ = caso
    with pytest.raises(ConfigError) as excinfo:
        _plan(ajustes, storage, [rev.Edit("escena_9", "narration_text", "hola")])
    assert "no existe en el documento" in excinfo.value.message
    assert "escena_1" in excinfo.value.message


def test_dos_ediciones_del_mismo_campo_se_rechazan(tmp_path) -> None:
    ruta = tmp_path / "edits.json"
    ruta.write_text(
        json.dumps(
            {
                "edits": [
                    {"scene_id": "escena_2", "field": "narration_text", "value": "a"},
                    {"scene_id": "escena_2", "field": "narration_text", "value": "b"},
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="dos ediciones para el mismo campo"):
        rev.load_edits(ruta)


# ---------------------------------------------------------------------------
# Donde se detiene
# ---------------------------------------------------------------------------


def test_sin_trabajo_se_detiene(caso, ediciones) -> None:
    ajustes, storage, _, _ = caso
    with pytest.raises(ConfigError) as excinfo:
        rev.build_plan(
            settings=ajustes,
            storage=storage,
            job_key="no-existe",
            edits=ediciones,
            note=None,
            revised_by="operador_local",
            tool="viralgen revise",
        )
    assert "No hay ningun trabajo" in excinfo.value.message


def test_sin_idea_seleccionada_se_detiene(caso, ediciones) -> None:
    """La coherencia editorial se valida contra la promesa REAL."""
    ajustes, storage, _, _ = caso
    with storage.write() as conexion:
        conexion.execute("UPDATE idea_candidates SET selected = 0 WHERE job_id = ?", (JOB_ID,))

    with pytest.raises(ConfigError) as excinfo:
        _plan(ajustes, storage, ediciones)
    assert "seleccionada" in excinfo.value.message


def test_sin_promesa_guardada_se_detiene(caso, ediciones) -> None:
    ajustes, storage, _, _ = caso
    fila = storage.load_candidates(JOB_ID)[0]
    fila.pop("selected", None)
    fila["promise"] = ""
    with storage.write() as conexion:
        conexion.execute(
            "UPDATE idea_candidates SET payload_json = ? WHERE job_id = ?",
            (json.dumps(fila, ensure_ascii=False), JOB_ID),
        )

    with pytest.raises(ConfigError) as excinfo:
        _plan(ajustes, storage, ediciones)
    assert "no tiene promesa" in excinfo.value.message


def test_si_el_archivo_no_coincide_con_sqlite_se_detiene(caso, ediciones) -> None:
    """No se revisa un original dudoso."""
    ajustes, storage, ruta, datos = caso
    manipulado = json.loads(json.dumps(datos))
    manipulado["scenes"][0]["narration_text"] = "Alguien edito el archivo a mano."
    ruta.write_text(
        json.dumps(manipulado, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    with pytest.raises(ConfigError) as excinfo:
        _plan(ajustes, storage, ediciones)
    assert "no coincide con el hash registrado" in excinfo.value.message


def test_no_se_revisa_una_revision(caso, ediciones) -> None:
    ajustes, storage, _, datos = caso
    rev.apply(
        _plan(ajustes, storage, ediciones),
        storage=storage,
        note=None,
        revised_by="operador_local",
        tool="viralgen revise",
    )
    # Si alguien mete la revision en `scripts`, la herramienta se niega.
    revisado = json.loads(
        Path(storage.list_revisions(JOB_ID)[0]["path"]).read_text(encoding="utf-8")
    )
    storage.save_script(JOB_ID, revisado, revisado["control"]["production_status"])

    with pytest.raises(ConfigError) as excinfo:
        _plan(ajustes, storage, ediciones)
    assert "ya es una revision" in excinfo.value.message
