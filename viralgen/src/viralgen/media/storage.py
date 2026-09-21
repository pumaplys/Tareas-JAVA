"""Persistencia del modulo 3 sobre la misma base SQLite.

Migracion IDEMPOTENTE que solo anade tablas: los trabajos, guiones, candidatos
y ejecuciones de voz anteriores se conservan intactos.

Tablas:

* ``media_runs``              - una fila por ejecucion (`--media-key`).
* ``media_requests``          - reserva persistida de cada intento remoto.
* ``media_assets``            - CACHE por identidad de asset, no por ejecucion.
* ``media_tasks``             - tareas remotas vivas, para reanudar sin repetir POST.
* ``media_unknown_outcomes``  - operaciones con resultado incierto, bloqueadas.
* ``media_references``        - conjunto de referencias por serie y version.
* ``media_exports``           - exportaciones del manifiesto.

Nunca se mantiene una transaccion abierta durante una llamada HTTP.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..storage import Storage, iso, utcnow

MEDIA_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS media_runs (
    media_run_id  TEXT PRIMARY KEY,
    job_id        TEXT NOT NULL,
    voice_run_id  TEXT NOT NULL,
    media_key     TEXT NOT NULL UNIQUE,
    fingerprint   TEXT NOT NULL,
    simulation    INTEGER NOT NULL,
    status        TEXT NOT NULL,
    media_status  TEXT,
    script_sha256 TEXT NOT NULL,
    voice_sha256  TEXT NOT NULL,
    manifest_json TEXT,
    error_code    TEXT,
    error_message TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_media_runs_job ON media_runs(job_id);

CREATE TABLE IF NOT EXISTS media_requests (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    media_run_id  TEXT NOT NULL,
    job_id        TEXT NOT NULL,
    kind          TEXT NOT NULL,
    scene_id      TEXT,
    video_seconds REAL NOT NULL DEFAULT 0,
    status        TEXT NOT NULL,
    request_id    TEXT,
    task_id       TEXT,
    latency_ms    INTEGER NOT NULL DEFAULT 0,
    error_code    TEXT,
    error_message TEXT,
    reserved_at   TEXT NOT NULL,
    settled_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_media_requests_job ON media_requests(job_id);

CREATE TABLE IF NOT EXISTS media_assets (
    cache_key   TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    simulation  INTEGER NOT NULL,
    path        TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    width       INTEGER NOT NULL,
    height      INTEGER NOT NULL,
    meta_json   TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    last_used_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS media_tasks (
    operation_key TEXT PRIMARY KEY,
    job_id        TEXT NOT NULL,
    media_run_id  TEXT NOT NULL,
    scene_id      TEXT,
    task_id       TEXT NOT NULL,
    state         TEXT NOT NULL,
    simulation    INTEGER NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS media_unknown_outcomes (
    operation_key TEXT PRIMARY KEY,
    job_id        TEXT NOT NULL,
    scene_id      TEXT,
    reason        TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS media_references (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    set_id       TEXT NOT NULL,
    character_id TEXT NOT NULL,
    simulation   INTEGER NOT NULL,
    path         TEXT NOT NULL,
    sha256       TEXT NOT NULL,
    width        INTEGER NOT NULL,
    height       INTEGER NOT NULL,
    provenance_json TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    UNIQUE(set_id, character_id, simulation)
);

CREATE TABLE IF NOT EXISTS media_exports (
    media_run_id TEXT PRIMARY KEY,
    path         TEXT NOT NULL,
    sha256       TEXT NOT NULL,
    status       TEXT NOT NULL,
    exported_at  TEXT NOT NULL
);
"""


class MediaStorage:
    """Repositorio de medios. Reutiliza la conexion del modulo 1."""

    def __init__(self, storage: Storage) -> None:
        self.storage = storage
        self._migrated = False

    def migrate(self) -> None:
        if self._migrated:
            return
        conexion = self.storage.connect()
        conexion.executescript(MEDIA_SCHEMA_SQL)
        conexion.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES('media_schema_version', '1')"
        )
        self._migrated = True

    # -- Ejecuciones -------------------------------------------------------

    def get_run_by_key(self, media_key: str) -> dict | None:
        fila = self.storage.connect().execute(
            "SELECT * FROM media_runs WHERE media_key = ?", (media_key,)
        ).fetchone()
        return dict(fila) if fila else None

    def get_run(self, media_run_id: str) -> dict | None:
        fila = self.storage.connect().execute(
            "SELECT * FROM media_runs WHERE media_run_id = ?", (media_run_id,)
        ).fetchone()
        return dict(fila) if fila else None

    def create_run(self, run: dict) -> dict:
        ahora = iso(utcnow())
        datos = {**run, "created_at": ahora, "updated_at": ahora}
        columnas = ", ".join(datos)
        marcadores = ", ".join(f":{nombre}" for nombre in datos)
        with self.storage.write() as conexion:
            conexion.execute(
                f"INSERT INTO media_runs ({columnas}) VALUES ({marcadores})", datos
            )
        return self.get_run(datos["media_run_id"])  # type: ignore[return-value]

    def update_run(self, media_run_id: str, **campos: Any) -> None:
        if not campos:
            return
        campos["updated_at"] = iso(utcnow())
        asignaciones = ", ".join(f"{nombre} = :{nombre}" for nombre in campos)
        with self.storage.write() as conexion:
            conexion.execute(
                f"UPDATE media_runs SET {asignaciones} WHERE media_run_id = :media_run_id",
                {**campos, "media_run_id": media_run_id},
            )

    def save_manifest(self, media_run_id: str, manifest: dict, media_status: str) -> None:
        self.update_run(
            media_run_id,
            manifest_json=json.dumps(manifest, ensure_ascii=False),
            media_status=media_status,
        )

    def load_manifest(self, media_run_id: str) -> dict | None:
        fila = self.storage.connect().execute(
            "SELECT manifest_json FROM media_runs WHERE media_run_id = ?", (media_run_id,)
        ).fetchone()
        if not fila or not fila["manifest_json"]:
            return None
        return json.loads(fila["manifest_json"])

    # -- Presupuesto -------------------------------------------------------

    def usage(self, job_id: str) -> dict[str, float]:
        """Uso HISTORICO del trabajo. Las reservas cuentan aunque fallaran."""
        fila = self.storage.connect().execute(
            "SELECT "
            "COALESCE(SUM(kind = 'generation'), 0) AS generation_attempts, "
            "COALESCE(SUM(kind = 'status'), 0) AS status_requests, "
            "COALESCE(SUM(kind = 'download'), 0) AS download_attempts, "
            "COALESCE(SUM(video_seconds), 0) AS video_seconds "
            "FROM media_requests WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        return {clave: float(fila[clave]) for clave in fila.keys()}

    def reserve_request(
        self,
        *,
        media_run_id: str,
        job_id: str,
        kind: str,
        scene_id: str | None,
        video_seconds: float = 0.0,
    ) -> int:
        with self.storage.write() as conexion:
            cursor = conexion.execute(
                "INSERT INTO media_requests "
                "(media_run_id, job_id, kind, scene_id, video_seconds, status, reserved_at) "
                "VALUES (?, ?, ?, ?, ?, 'reserved', ?)",
                (media_run_id, job_id, kind, scene_id, video_seconds, iso(utcnow())),
            )
            return int(cursor.lastrowid)

    def settle_request(self, token: int, **campos: Any) -> None:
        permitidos = {
            "status", "request_id", "task_id", "latency_ms", "error_code", "error_message",
        }
        datos = {clave: valor for clave, valor in campos.items() if clave in permitidos}
        datos["settled_at"] = iso(utcnow())
        asignaciones = ", ".join(f"{nombre} = :{nombre}" for nombre in datos)
        with self.storage.write() as conexion:
            conexion.execute(
                f"UPDATE media_requests SET {asignaciones} WHERE id = :id",
                {**datos, "id": token},
            )

    # -- Cache de assets ---------------------------------------------------

    def get_cached_asset(self, cache_key: str) -> dict | None:
        fila = self.storage.connect().execute(
            "SELECT * FROM media_assets WHERE cache_key = ?", (cache_key,)
        ).fetchone()
        if not fila:
            return None
        datos = dict(fila)
        datos["meta"] = json.loads(datos["meta_json"])
        return datos

    def put_cached_asset(self, cache_key: str, asset: dict) -> None:
        ahora = iso(utcnow())
        with self.storage.write() as conexion:
            conexion.execute(
                "INSERT INTO media_assets "
                "(cache_key, kind, simulation, path, sha256, width, height, meta_json, "
                "created_at, last_used_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(cache_key) DO UPDATE SET path = excluded.path, "
                "sha256 = excluded.sha256, width = excluded.width, height = excluded.height, "
                "meta_json = excluded.meta_json, last_used_at = excluded.last_used_at",
                (
                    cache_key,
                    asset["kind"],
                    int(asset["simulation"]),
                    str(asset["path"]),
                    asset["sha256"],
                    int(asset["width"]),
                    int(asset["height"]),
                    json.dumps(asset.get("meta", {}), ensure_ascii=False),
                    ahora,
                    ahora,
                ),
            )

    def touch_cached_asset(self, cache_key: str) -> None:
        with self.storage.write() as conexion:
            conexion.execute(
                "UPDATE media_assets SET last_used_at = ? WHERE cache_key = ?",
                (iso(utcnow()), cache_key),
            )

    def cache_size_mib(self) -> float:
        fila = self.storage.connect().execute(
            "SELECT COUNT(*) AS n FROM media_assets"
        ).fetchone()
        return float(fila["n"])  # el tamano real lo mide el llamador sobre disco

    # -- Tareas remotas ----------------------------------------------------

    def get_task(self, operation_key: str) -> dict | None:
        fila = self.storage.connect().execute(
            "SELECT * FROM media_tasks WHERE operation_key = ?", (operation_key,)
        ).fetchone()
        return dict(fila) if fila else None

    def put_task(
        self,
        *,
        operation_key: str,
        job_id: str,
        media_run_id: str,
        scene_id: str | None,
        task_id: str,
        state: str,
        simulation: bool,
    ) -> None:
        ahora = iso(utcnow())
        with self.storage.write() as conexion:
            conexion.execute(
                "INSERT INTO media_tasks "
                "(operation_key, job_id, media_run_id, scene_id, task_id, state, simulation, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(operation_key) DO UPDATE SET state = excluded.state, "
                "updated_at = excluded.updated_at",
                (
                    operation_key, job_id, media_run_id, scene_id, task_id, state,
                    int(simulation), ahora, ahora,
                ),
            )

    def update_task_state(self, operation_key: str, state: str) -> None:
        with self.storage.write() as conexion:
            conexion.execute(
                "UPDATE media_tasks SET state = ?, updated_at = ? WHERE operation_key = ?",
                (state, iso(utcnow()), operation_key),
            )

    def live_tasks(self, job_id: str) -> list[dict]:
        filas = self.storage.connect().execute(
            "SELECT * FROM media_tasks WHERE job_id = ? AND state NOT IN "
            "('succeeded','failed','cancelled') ORDER BY created_at",
            (job_id,),
        ).fetchall()
        return [dict(fila) for fila in filas]

    # -- Resultados inciertos ---------------------------------------------

    def get_unknown_outcome(self, operation_key: str) -> dict | None:
        fila = self.storage.connect().execute(
            "SELECT * FROM media_unknown_outcomes WHERE operation_key = ?", (operation_key,)
        ).fetchone()
        return dict(fila) if fila else None

    def mark_unknown_outcome(
        self, *, operation_key: str, job_id: str, scene_id: str | None, reason: str
    ) -> None:
        with self.storage.write() as conexion:
            conexion.execute(
                "INSERT OR IGNORE INTO media_unknown_outcomes "
                "(operation_key, job_id, scene_id, reason, created_at) VALUES (?, ?, ?, ?, ?)",
                (operation_key, job_id, scene_id, reason[:500], iso(utcnow())),
            )

    def clear_unknown_outcome(self, operation_key: str) -> None:
        """Reconciliacion EXPLICITA. Nunca ocurre de forma automatica."""
        with self.storage.write() as conexion:
            conexion.execute(
                "DELETE FROM media_unknown_outcomes WHERE operation_key = ?", (operation_key,)
            )

    # -- Referencias -------------------------------------------------------

    def get_reference(self, set_id: str, character_id: str, simulation: bool) -> dict | None:
        fila = self.storage.connect().execute(
            "SELECT * FROM media_references WHERE set_id = ? AND character_id = ? "
            "AND simulation = ?",
            (set_id, character_id, int(simulation)),
        ).fetchone()
        if not fila:
            return None
        datos = dict(fila)
        datos["provenance"] = json.loads(datos["provenance_json"])
        return datos

    def put_reference(self, entry: dict) -> None:
        with self.storage.write() as conexion:
            conexion.execute(
                "INSERT INTO media_references "
                "(set_id, character_id, simulation, path, sha256, width, height, "
                "provenance_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(set_id, character_id, simulation) DO UPDATE SET "
                "path = excluded.path, sha256 = excluded.sha256, width = excluded.width, "
                "height = excluded.height, provenance_json = excluded.provenance_json",
                (
                    entry["set_id"], entry["character_id"], int(entry["simulation"]),
                    str(entry["path"]), entry["sha256"], int(entry["width"]),
                    int(entry["height"]),
                    json.dumps(entry["provenance"], ensure_ascii=False), iso(utcnow()),
                ),
            )

    # -- Exportaciones -----------------------------------------------------

    def record_export(
        self, media_run_id: str, *, path: Path | str, sha256: str, status: str
    ) -> None:
        with self.storage.write() as conexion:
            conexion.execute(
                "INSERT INTO media_exports (media_run_id, path, sha256, status, exported_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(media_run_id) DO UPDATE SET path = excluded.path, "
                "sha256 = excluded.sha256, status = excluded.status, "
                "exported_at = excluded.exported_at",
                (media_run_id, str(path), sha256, status, iso(utcnow())),
            )
