"""Revision editorial local de un `script.json` del modulo 1.

PARA QUE SIRVE
--------------

Un guion puede quedar en `needs_review` por un aviso editorial y necesitar tres
cambios de texto. Volver a llamar al proveedor para eso seria absurdo: cuesta
dinero y devuelve otro guion, no el mismo con una frase arreglada. Esto aplica
los cambios que decide una persona, **sin red y sin proveedores**, y recalcula
con las funciones del proyecto lo que depende de ellos.

QUE RECALCULA, Y CON QUE
-----------------------

Nada se copia a mano. Tras cambiar un `narration_text`:

* `count_words` da el recuento de cada escena;
* `build_timeline` encadena de nuevo TODAS las escenas desde el segundo 0
  -cambiar la escena 2 mueve las cinco siguientes-;
* `narration.full_text` y `narration.word_count` se recomponen;
* `video.estimated_duration_s` sale del final de la linea temporal;
* `validate_document` y `apply_validation_result` deciden los avisos y el
  `production_status`.

Ese ultimo punto es el importante: **el estado no se escribe a mano**. Si tras
la revision siguen quedando avisos, el documento sigue en `needs_review`, y eso
es informacion, no un obstaculo que convenga quitar.

QUE NO PUEDE TOCAR
------------------

Solo se admiten campos EDITORIALES (`ALLOWED_FIELDS`). Cualquier otro se
rechaza antes de abrir nada. En particular no se pueden editar
`control.production_status`, `control.warnings`, `simulation`, `provenance` ni
`evidence`: cambiar eso a mano seria fabricar una admision en vez de ganarla.

EL ORIGINAL SE CONSERVA
-----------------------

La revision se escribe en un archivo APARTE (`script.revision-N.json`) y en su
propia tabla (`script_revisions`). El `script.json` original, su fila de
`scripts` y su fila de `exports` quedan intactos. La revision se enlaza al
original por el SHA-256 de sus bytes.

Repetir la misma peticion no crea otra revision: la identidad de una revision
es el conjunto de ediciones mas el original sobre el que se aplican.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .assembly import apply_validation_result
from .diskutil import atomic_write_json
from .errors import ConfigError, ViralgenError
from .logging_setup import get_logger
from .profiles import get_profile
from .schemas.document import (
    EditorialChange,
    EditorialRevision,
    ScriptDocument,
)
from .storage import Storage, utcnow
from .textutil import count_words, sha256_json, sha256_text, truncate
from .timing import build_timeline
from .validation import check_admission, validate_document

logger = get_logger("revision")

#: Campos editables. La lista es corta a proposito: es una revision EDITORIAL,
#: no un editor de documentos.
ALLOWED_FIELDS: tuple[str, ...] = (
    "narration_text",
    "captions.emphasis_words",
    "captions.overlay_text",
)

#: Campos que alguien podria querer editar para forzar una admision. Se nombran
#: para que el error diga por que NO, en vez de un "campo desconocido".
FORBIDDEN_FIELDS: dict[str, str] = {
    "control.production_status": (
        "el estado se deriva de la validacion, no se escribe: arreglalo o "
        "deja el documento en needs_review"
    ),
    "control.warnings": (
        "los avisos los calcula la validacion; borrarlos no resuelve lo que "
        "avisan"
    ),
    "simulation": "el modo de origen de un documento no se reescribe",
    "provenance": "describe la generacion inicial y una revision no la altera",
    "evidence": "la evidencia se copia del catalogo, no se edita a mano",
    "video.estimated_duration_s": "se recalcula a partir de palabras y pausas",
    "narration.word_count": "se recalcula con count_words",
    "narration.full_text": "se recompone de las escenas",
}


@dataclass(frozen=True)
class Edit:
    """Una edicion pedida por el operador."""

    scene_id: str
    field: str
    value: Any

    def identity(self) -> dict:
        return {"scene_id": self.scene_id, "field": self.field, "value": self.value}


@dataclass
class RevisionPlan:
    """Resultado de revisar, se aplique o no."""

    job: dict
    original: ScriptDocument
    revised: ScriptDocument
    edits: list[Edit]
    edits_sha256: str
    original_bytes_sha256: str
    original_content_sha256: str
    changes: list[EditorialChange]
    report: Any
    promise: str
    profile_id: str
    original_path: Path
    before: dict = field(default_factory=dict)
    after: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Lectura de las ediciones
# ---------------------------------------------------------------------------


def load_edits(path: Path) -> tuple[list[Edit], str | None]:
    """Lee el archivo de ediciones. Es DATOS, no codigo."""
    try:
        datos = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"No se pudo leer el archivo de ediciones {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"El archivo de ediciones {path} no es JSON valido: {exc}") from exc

    if not isinstance(datos, dict) or not isinstance(datos.get("edits"), list):
        raise ConfigError(
            f"El archivo de ediciones {path} debe ser un objeto con una lista `edits`."
        )
    if not datos["edits"]:
        raise ConfigError("La lista `edits` esta vacia: no hay nada que revisar.")

    ediciones: list[Edit] = []
    for indice, entrada in enumerate(datos["edits"]):
        if not isinstance(entrada, dict):
            raise ConfigError(f"edits[{indice}] debe ser un objeto.")
        faltan = [clave for clave in ("scene_id", "field", "value") if clave not in entrada]
        if faltan:
            raise ConfigError(f"edits[{indice}] le falta: {', '.join(faltan)}.")
        campo = str(entrada["field"])
        if campo in FORBIDDEN_FIELDS:
            raise ConfigError(
                f"edits[{indice}]: el campo {campo!r} NO se puede editar aqui porque "
                f"{FORBIDDEN_FIELDS[campo]}. Campos admitidos: "
                f"{', '.join(ALLOWED_FIELDS)}.",
                details={"field": campo, "allowed": list(ALLOWED_FIELDS)},
            )
        if campo not in ALLOWED_FIELDS:
            raise ConfigError(
                f"edits[{indice}]: campo no editable {campo!r}. Esta herramienta aplica "
                f"revisiones EDITORIALES; campos admitidos: {', '.join(ALLOWED_FIELDS)}.",
                details={"field": campo, "allowed": list(ALLOWED_FIELDS)},
            )
        ediciones.append(
            Edit(scene_id=str(entrada["scene_id"]), field=campo, value=entrada["value"])
        )

    duplicadas = [
        f"{e.scene_id}.{e.field}"
        for e in ediciones
        if sum(1 for o in ediciones if (o.scene_id, o.field) == (e.scene_id, e.field)) > 1
    ]
    if duplicadas:
        raise ConfigError(
            "Hay dos ediciones para el mismo campo: "
            + ", ".join(sorted(set(duplicadas)))
            + ". Deja una sola, para que la revision sea reproducible."
        )

    nota = datos.get("note")
    return ediciones, (str(nota) if nota else None)


def edits_identity(edits: list[Edit]) -> str:
    """Identidad del conjunto de ediciones. El orden no cuenta."""
    return sha256_json(
        sorted(
            (edit.identity() for edit in edits),
            key=lambda item: (item["scene_id"], item["field"]),
        )
    )


# ---------------------------------------------------------------------------
# Aplicacion
# ---------------------------------------------------------------------------


def _valor_actual(document: ScriptDocument, edit: Edit) -> Any:
    escena = next((s for s in document.scenes if s.scene_id == edit.scene_id), None)
    if escena is None:
        disponibles = ", ".join(s.scene_id for s in document.scenes)
        raise ConfigError(
            f"La escena {edit.scene_id!r} no existe en el documento. Escenas: {disponibles}.",
            details={"scene_id": edit.scene_id},
        )
    if edit.field == "narration_text":
        return escena.narration_text
    if edit.field == "captions.emphasis_words":
        return list(escena.captions.emphasis_words)
    if edit.field == "captions.overlay_text":
        return escena.captions.overlay_text
    raise ConfigError(f"Campo no editable: {edit.field!r}")


def _asignar(document: ScriptDocument, edit: Edit) -> None:
    escena = next(s for s in document.scenes if s.scene_id == edit.scene_id)
    if edit.field == "narration_text":
        if not isinstance(edit.value, str):
            raise ConfigError(f"{edit.scene_id}.narration_text debe ser una cadena.")
        escena.narration_text = edit.value
    elif edit.field == "captions.emphasis_words":
        if not isinstance(edit.value, list) or not all(
            isinstance(palabra, str) for palabra in edit.value
        ):
            raise ConfigError(
                f"{edit.scene_id}.captions.emphasis_words debe ser una lista de cadenas."
            )
        escena.captions.emphasis_words = list(edit.value)
    elif edit.field == "captions.overlay_text":
        if edit.value is not None and not isinstance(edit.value, str):
            raise ConfigError(
                f"{edit.scene_id}.captions.overlay_text debe ser una cadena o null."
            )
        escena.captions.overlay_text = edit.value


def _resumen_valor(valor: Any) -> str:
    if isinstance(valor, str):
        return truncate(valor, 200)
    return truncate(json.dumps(valor, ensure_ascii=False), 200)


def recompute(document: ScriptDocument, *, target_wpm: int) -> ScriptDocument:
    """Recalcula recuentos, linea temporal y narracion con las funciones del proyecto.

    Se recalcula TODO, no solo las escenas editadas: cambiar la escena 2 mueve
    el inicio y el final de todas las siguientes.
    """
    ordenadas = sorted(document.scenes, key=lambda escena: escena.order)
    recuentos = [count_words(escena.narration_text) for escena in ordenadas]
    pausas = [float(escena.pause_after_s) for escena in ordenadas]
    linea = build_timeline(recuentos, pausas, target_wpm)

    for escena, tiempo in zip(ordenadas, linea, strict=True):
        escena.word_count = tiempo.word_count
        escena.estimated_start_s = tiempo.start_s
        escena.estimated_end_s = tiempo.end_s
        escena.estimated_duration_s = tiempo.duration_s

    document.narration.full_text = " ".join(
        escena.narration_text for escena in ordenadas
    ).strip()
    document.narration.word_count = sum(recuentos)
    document.video.estimated_duration_s = linea[-1].end_s if linea else 0.0
    return document


def _promesa_seleccionada(storage: Storage, job_id: str) -> str:
    """La promesa de la idea SELECCIONADA, tal como quedo en SQLite.

    Si no se encuentra, se detiene: validar la coherencia editorial contra una
    promesa vacia daria un verde que no significa nada.
    """
    candidatos = storage.load_candidates(job_id)
    if not candidatos:
        raise ConfigError(
            f"No hay ideas candidatas guardadas para el trabajo {job_id}: no se puede "
            "recuperar la promesa real de la idea seleccionada. Se detiene.",
            details={"job_id": job_id},
        )
    seleccionadas = [c for c in candidatos if c.get("selected")]
    if not seleccionadas:
        raise ConfigError(
            f"Ninguna idea esta marcada como seleccionada para el trabajo {job_id}. "
            "Se detiene en vez de validar contra una promesa inventada.",
            details={"job_id": job_id, "candidatos": len(candidatos)},
        )
    if len(seleccionadas) > 1:
        raise ConfigError(
            f"Hay {len(seleccionadas)} ideas marcadas como seleccionadas para "
            f"{job_id}; deberia haber una.",
            details={"job_id": job_id},
        )
    promesa = (seleccionadas[0].get("promise") or "").strip()
    if not promesa:
        raise ConfigError(
            "La idea seleccionada no tiene promesa guardada en SQLite. Se detiene: "
            "la coherencia editorial se valida contra la promesa real.",
            details={"job_id": job_id, "idea_ref": seleccionadas[0].get("idea_ref")},
        )
    return promesa


def build_plan(
    *,
    settings: Any,
    storage: Storage,
    job_key: str,
    edits: list[Edit],
    note: str | None,
    revised_by: str,
    tool: str,
) -> RevisionPlan:
    """Calcula la revision completa SIN escribir nada."""
    trabajo = storage.get_job_by_key(job_key)
    if trabajo is None:
        raise ConfigError(
            f"No hay ningun trabajo con --job-key {job_key!r} en este DATA_DIR. "
            "Se detiene.",
            details={"job_key": job_key},
        )
    job_id = trabajo["job_id"]

    guardado = storage.load_script(job_id)
    if guardado is None:
        raise ConfigError(
            f"El trabajo {job_id} no tiene guion guardado en SQLite.",
            details={"job_id": job_id},
        )

    exportacion = storage.get_export(job_id)
    if exportacion is None or exportacion.get("status") != "ok":
        raise ConfigError(
            f"El trabajo {job_id} no tiene una exportacion completa registrada: "
            "la revision se enlaza al SHA-256 de los bytes originales, asi que hace "
            "falta el archivo.",
            details={"job_id": job_id},
        )
    ruta_original = Path(exportacion["path"])
    try:
        bytes_originales = ruta_original.read_bytes()
    except OSError as exc:
        raise ConfigError(
            f"No se pudo leer el script.json original en {ruta_original}: {exc}",
            details={"path": str(ruta_original)},
        ) from exc

    # SQLite y el archivo tienen que decir lo mismo ANTES de revisar nada.
    contenido_archivo = sha256_text(
        json.dumps(json.loads(bytes_originales), ensure_ascii=False, sort_keys=True)
    )
    if contenido_archivo != exportacion["sha256"]:
        raise ConfigError(
            f"El script.json de {ruta_original} no coincide con el hash registrado en "
            "SQLite. No se revisa un original dudoso.",
            details={
                "path": str(ruta_original),
                "sha256_archivo": contenido_archivo,
                "sha256_registrado": exportacion["sha256"],
            },
        )

    original = ScriptDocument.model_validate(guardado)
    if original.editorial_revision is not None:
        raise ConfigError(
            "El documento guardado en `scripts` ya es una revision. Esta herramienta "
            "revisa el ORIGINAL; aplica las ediciones sobre el original o encadena "
            "revisiones a mano con cuidado.",
            details={"job_id": job_id},
        )

    perfil = get_profile(original.profile_id, settings.profiles_path)
    if perfil.profile_id != original.profile_id:  # pragma: no cover - get_profile ya falla
        raise ConfigError("El perfil cargado no corresponde al del documento.")

    promesa = _promesa_seleccionada(storage, job_id)

    antes = _instantanea(original)

    revisado = ScriptDocument.model_validate(guardado)
    cambios: list[EditorialChange] = []
    sin_efecto: list[str] = []
    for edit in edits:
        anterior = _valor_actual(revisado, edit)
        if anterior == edit.value:
            sin_efecto.append(f"{edit.scene_id}.{edit.field}")
            continue
        _asignar(revisado, edit)
        cambios.append(
            EditorialChange(
                scene_id=edit.scene_id,
                field=edit.field,
                before_sha256=sha256_json(anterior),
                after_sha256=sha256_json(edit.value),
                before_preview=_resumen_valor(anterior),
                after_preview=_resumen_valor(edit.value),
            )
        )
    if sin_efecto:
        raise ConfigError(
            "Estas ediciones no cambian nada porque el valor ya es el pedido: "
            + ", ".join(sin_efecto)
            + ". Quitalas del archivo: una revision sin cambios no se registra.",
            details={"no_op": sin_efecto},
        )

    recompute(revisado, target_wpm=perfil.target_wpm)

    informe = validate_document(
        revisado, profile=perfil, allowed_facts=None, promise=promesa
    )
    # El estado y los avisos SALEN de la validacion. No se escriben a mano.
    apply_validation_result(
        revisado,
        warnings=[problema.message for problema in informe.issues],
        has_evidence_doubt=False,
    )

    return RevisionPlan(
        job=trabajo,
        original=original,
        revised=revisado,
        edits=edits,
        edits_sha256=edits_identity(edits),
        original_bytes_sha256=hashlib.sha256(bytes_originales).hexdigest(),
        original_content_sha256=exportacion["sha256"],
        changes=cambios,
        report=informe,
        promise=promesa,
        profile_id=perfil.profile_id,
        original_path=ruta_original,
        before=antes,
        after=_instantanea(revisado),
    )


def _instantanea(document: ScriptDocument) -> dict:
    """Las cifras que el operador compara antes y despues."""
    return {
        "production_status": document.control.production_status.value,
        "warnings": list(document.control.warnings),
        "word_count": document.narration.word_count,
        "estimated_duration_s": document.video.estimated_duration_s,
        "scene_count": len(document.scenes),
        "image_scenes": sum(
            1 for escena in document.scenes if escena.visual.asset_type == "image"
        ),
        "video_scenes": sum(
            1 for escena in document.scenes if escena.visual.asset_type == "video"
        ),
        "scene_word_counts": {
            escena.scene_id: escena.word_count for escena in document.scenes
        },
    }


# ---------------------------------------------------------------------------
# Informe y escritura
# ---------------------------------------------------------------------------


def describe(plan: RevisionPlan, *, applied: bool, extra: dict | None = None) -> dict:
    """Resumen JSON para stdout. El mismo en previsualizacion y al aplicar."""
    admision = check_admission(
        plan.revised,
        # En previsualizacion no hay archivo todavia, asi que la admision se
        # presenta como PROYECTADA: lo que daria si la exportacion se completa.
        export_complete=True,
        report=plan.report,
    )
    informe = {
        "command": "revise",
        "mode": "apply" if applied else "preview",
        "job_id": plan.job["job_id"],
        "job_key": plan.job["job_key"],
        "profile_id": plan.profile_id,
        "promise": plan.promise,
        "original": {
            "bytes_sha256": plan.original_bytes_sha256,
            "content_sha256": plan.original_content_sha256,
            **plan.before,
        },
        "revised": dict(plan.after),
        "delta": {
            "word_count": plan.after["word_count"] - plan.before["word_count"],
            "estimated_duration_s": round(
                plan.after["estimated_duration_s"] - plan.before["estimated_duration_s"], 3
            ),
        },
        "edits_sha256": plan.edits_sha256,
        "changes": [cambio.model_dump(mode="json") for cambio in plan.changes],
        "issues": [problema.to_dict() for problema in plan.report.issues],
        "provider_calls": 0,
        ("admission_for_voice" if applied else "admission_for_voice_if_exported"): (
            admision.to_dict()
        ),
        "notes": [
            "La revision es local: no se ha llamado a ningun proveedor.",
            "El estado y los avisos los calcula la validacion; no se escriben a mano.",
            "El script.json original no se modifica: la revision va a un archivo aparte.",
        ],
    }
    if extra:
        informe.update(extra)
    return informe


def apply(
    plan: RevisionPlan,
    *,
    storage: Storage,
    note: str | None,
    revised_by: str,
    tool: str,
) -> dict:
    """Escribe la revision. Repetir la misma peticion es un no-op."""
    job_id = plan.job["job_id"]

    ya_existe = storage.find_revision(
        job_id,
        edits_sha256=plan.edits_sha256,
        revision_of_sha256=plan.original_bytes_sha256,
    )
    if ya_existe is not None:
        return describe(
            plan,
            applied=True,
            extra={
                "reused": True,
                "revision_no": ya_existe["revision_no"],
                "revision_path": ya_existe["path"],
                "revision_sha256": ya_existe["sha256"],
                "reuse_note": (
                    "Esta misma revision ya estaba aplicada sobre este mismo original: "
                    "no se crea otra ni se reescribe nada."
                ),
            },
        )

    numero = storage.next_revision_no(job_id)
    revisado = plan.revised
    revisado.editorial_revision = EditorialRevision(
        revision_no=numero,
        revision_of_sha256=plan.original_bytes_sha256,
        revision_of_content_sha256=plan.original_content_sha256,
        revised_at=utcnow(),
        revised_by=revised_by,
        tool=tool,
        edits_sha256=plan.edits_sha256,
        note=note,
        changes=plan.changes,
    )

    datos = revisado.model_dump(mode="json")
    # La revision se escribe JUNTO al original, en la carpeta del trabajo.
    ruta = plan.original_path.parent / f"script.revision-{numero}.json"
    try:
        atomic_write_json(ruta, datos)
    except OSError as exc:
        raise ViralgenError(
            f"No se pudo escribir la revision en {ruta}: {exc}",
            details={"path": str(ruta)},
        ) from exc

    # Se relee para no dar por exportado lo que no se puede volver a leer.
    try:
        releido = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ViralgenError(
            f"La revision se escribio en {ruta} pero no se pudo releer: {exc}",
            details={"path": str(ruta)},
        ) from exc
    ScriptDocument.model_validate(releido)

    contenido_sha = sha256_text(json.dumps(releido, ensure_ascii=False, sort_keys=True))
    guardada = storage.save_revision(
        job_id,
        {
            "revision_no": numero,
            "edits_sha256": plan.edits_sha256,
            "revision_of_sha256": plan.original_bytes_sha256,
            "production_status": revisado.control.production_status.value,
            "document": datos,
            "path": str(ruta),
            "sha256": contenido_sha,
        },
    )

    logger.info(
        "revision %d aplicada al trabajo %s (%s)",
        numero,
        job_id,
        revisado.control.production_status.value,
    )
    return describe(
        plan,
        applied=True,
        extra={
            "reused": False,
            "revision_no": guardada["revision_no"],
            "revision_path": guardada["path"],
            "revision_sha256": guardada["sha256"],
            "revision_bytes_sha256": hashlib.sha256(ruta.read_bytes()).hexdigest(),
            "original_untouched": _original_intacto(plan),
            "sqlite_matches_export": True,
        },
    )


def _original_intacto(plan: RevisionPlan) -> dict:
    """Relee el original y comprueba que sus bytes son los mismos.

    No basta con afirmarlo: la revision escribe en la misma carpeta, asi que se
    vuelve a leer el archivo y se compara el hash con el que se tomo antes de
    tocar nada. Si cambiara, se dice, en vez de dar por bueno el supuesto.
    """
    try:
        actual = hashlib.sha256(plan.original_path.read_bytes()).hexdigest()
    except OSError as exc:
        return {
            "path": str(plan.original_path),
            "unchanged": False,
            "error": f"no se pudo releer el original: {exc}",
        }
    return {
        "path": str(plan.original_path),
        "bytes_sha256_before": plan.original_bytes_sha256,
        "bytes_sha256_after": actual,
        "unchanged": actual == plan.original_bytes_sha256,
    }


__all__ = [
    "ALLOWED_FIELDS",
    "FORBIDDEN_FIELDS",
    "Edit",
    "RevisionPlan",
    "load_edits",
    "edits_identity",
    "recompute",
    "build_plan",
    "describe",
    "apply",
]
