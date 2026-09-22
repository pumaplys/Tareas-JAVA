"""Persistencia del modulo 4 sobre la misma base SQLite.

Migracion IDEMPOTENTE que solo anade tablas: nada de los modulos 1, 2 y 3 se
toca.

Tablas:

* ``render_runs``        - una fila por ejecucion (`--render-key`).
* ``render_stages``      - checkpoints por etapa, con hashes y parametros.
* ``render_artifacts``   - CACHE por identidad de etapa, no por ejecucion, para
  aprovechar trabajo coincidente desde otra `--render-key`.
* ``render_attempts``    - intentos por etapa, persistidos y acotados.
* ``render_exports``     - exportaciones consolidadas.

Ninguna transaccion queda abierta mientras FFmpeg trabaja: se escribe antes de
lanzar el proceso y despues de que termine, nunca durante.
"""

from __future__ import annotations

import json
from typing import Any

from ..storage import Storage, iso, utcnow

RENDER_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS render_runs (
    render_run_id TEXT PRIMARY KEY,
    job_id        TEXT NOT NULL,
    voice_run_id  TEXT NOT NULL,
    media_run_id  TEXT NOT NULL,
    render_key    TEXT NOT NULL UNIQUE,
    fingerprint   TEXT NOT NULL,
    render_mode   TEXT NOT NULL,
    simulation    INTEGER NOT NULL,
    status        TEXT NOT NULL,
    render_status TEXT,
    script_sha256 TEXT NOT NULL,
    voice_sha256  TEXT NOT NULL,
    media_sha256  TEXT NOT NULL,
    manifest_json TEXT,
    output_path   TEXT,
    error_code    TEXT,
    error_message TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_render_runs_job ON render_runs(job_id);

CREATE TABLE IF NOT EXISTS render_stages (
    render_run_id TEXT NOT NULL,
    stage         TEXT NOT NULL,
    state         TEXT NOT NULL,
    identity_key  TEXT NOT NULL,
    path          TEXT,
    sha256        TEXT,
    size_bytes    INTEGER,
    meta_json     TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (render_run_id, stage)
);

CREATE TABLE IF NOT EXISTS render_artifacts (
    identity_key  TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    simulation    INTEGER NOT NULL,
    render_mode   TEXT NOT NULL,
    path          TEXT NOT NULL,
    sha256        TEXT NOT NULL,
    size_bytes    INTEGER NOT NULL,
    meta_json     TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL,
    last_used_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS render_attempts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    render_run_id TEXT NOT NULL,
    job_id        TEXT NOT NULL,
    stage         TEXT NOT NULL,
    attempt       INTEGER NOT NULL,
    status        TEXT NOT NULL,
    error_code    TEXT,
    error_message TEXT,
    elapsed_s     REAL NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_render_attempts_run
    ON render_attempts(render_run_id, stage);

CREATE TABLE IF NOT EXISTS render_exports (
    render_run_id TEXT PRIMARY KEY,
    job_id        TEXT NOT NULL,
    path          TEXT NOT NULL,
    sha256        TEXT NOT NULL,
    size_bytes    INTEGER NOT NULL,
    status        TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
"""


class RenderStorage:
    """Acceso a las tablas del modulo 4."""

    def __init__(self, storage: Storage) -> None:
        self.storage = storage
        self._migrated = False

    def migrate(self) -> None:
        """Anade las tablas del modulo 4 si no existen. Se puede llamar N veces.

        `executescript` hace su propio commit, asi que NO va dentro de una
        transaccion explicita: envolverlo daria "cannot commit".
        """
        if self._migrated:
            return
        conexion = self.storage.connect()
        conexion.executescript(RENDER_SCHEMA_SQL)
        conexion.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES('render_schema_version', '1')"
        )
        self._migrated = True

    # -- Ejecuciones --------------------------------------------------------

    def get_run_by_key(self, render_key: str) -> dict | None:
        fila = self.storage.connect().execute(
            "SELECT * FROM render_runs WHERE render_key = ?", (render_key,)
        ).fetchone()
        return dict(fila) if fila else None

    def get_run(self, render_run_id: str) -> dict | None:
        fila = self.storage.connect().execute(
            "SELECT * FROM render_runs WHERE render_run_id = ?", (render_run_id,)
        ).fetchone()
        return dict(fila) if fila else None

    def create_run(self, run: dict) -> dict:
        ahora = iso(utcnow())
        datos = {**run, "created_at": ahora, "updated_at": ahora}
        columnas = ", ".join(datos)
        marcas = ", ".join("?" for _ in datos)
        with self.storage.write() as conexion:
            conexion.execute(
                f"INSERT INTO render_runs ({columnas}) VALUES ({marcas})",
                tuple(datos.values()),
            )
        return datos

    def update_run(self, render_run_id: str, **campos: Any) -> None:
        if not campos:
            return
        campos["updated_at"] = iso(utcnow())
        asignaciones = ", ".join(f"{clave} = ?" for clave in campos)
        with self.storage.write() as conexion:
            conexion.execute(
                f"UPDATE render_runs SET {asignaciones} WHERE render_run_id = ?",
                (*campos.values(), render_run_id),
            )

    def runs_for_job(self, job_id: str) -> list[dict]:
        filas = self.storage.connect().execute(
            "SELECT * FROM render_runs WHERE job_id = ? ORDER BY created_at",
            (job_id,),
        ).fetchall()
        return [dict(fila) for fila in filas]

    # -- Checkpoints por etapa ---------------------------------------------

    def get_stage(self, render_run_id: str, stage: str) -> dict | None:
        fila = self.storage.connect().execute(
            "SELECT * FROM render_stages WHERE render_run_id = ? AND stage = ?",
            (render_run_id, stage),
        ).fetchone()
        if not fila:
            return None
        datos = dict(fila)
        datos["meta"] = json.loads(datos.pop("meta_json") or "{}")
        return datos

    def put_stage(
        self,
        *,
        render_run_id: str,
        stage: str,
        state: str,
        identity_key: str,
        path: str | None = None,
        sha256: str | None = None,
        size_bytes: int | None = None,
        meta: dict | None = None,
    ) -> None:
        ahora = iso(utcnow())
        with self.storage.write() as conexion:
            conexion.execute(
                """
                INSERT INTO render_stages (
                    render_run_id, stage, state, identity_key, path, sha256,
                    size_bytes, meta_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(render_run_id, stage) DO UPDATE SET
                    state = excluded.state,
                    identity_key = excluded.identity_key,
                    path = excluded.path,
                    sha256 = excluded.sha256,
                    size_bytes = excluded.size_bytes,
                    meta_json = excluded.meta_json,
                    updated_at = excluded.updated_at
                """,
                (
                    render_run_id, stage, state, identity_key, path, sha256,
                    size_bytes, json.dumps(meta or {}, ensure_ascii=False),
                    ahora, ahora,
                ),
            )

    def stages(self, render_run_id: str) -> dict[str, dict]:
        filas = self.storage.connect().execute(
            "SELECT * FROM render_stages WHERE render_run_id = ?", (render_run_id,)
        ).fetchall()
        resultado: dict[str, dict] = {}
        for fila in filas:
            datos = dict(fila)
            datos["meta"] = json.loads(datos.pop("meta_json") or "{}")
            resultado[datos["stage"]] = datos
        return resultado

    # -- Cache por identidad de etapa --------------------------------------

    def get_artifact(self, identity_key: str) -> dict | None:
        fila = self.storage.connect().execute(
            "SELECT * FROM render_artifacts WHERE identity_key = ?", (identity_key,)
        ).fetchone()
        if not fila:
            return None
        datos = dict(fila)
        datos["meta"] = json.loads(datos.pop("meta_json") or "{}")
        return datos

    def put_artifact(
        self,
        *,
        identity_key: str,
        kind: str,
        simulation: bool,
        render_mode: str,
        path: str,
        sha256: str,
        size_bytes: int,
        meta: dict | None = None,
    ) -> None:
        ahora = iso(utcnow())
        with self.storage.write() as conexion:
            conexion.execute(
                """
                INSERT INTO render_artifacts (
                    identity_key, kind, simulation, render_mode, path, sha256,
                    size_bytes, meta_json, created_at, last_used_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(identity_key) DO UPDATE SET
                    path = excluded.path,
                    sha256 = excluded.sha256,
                    size_bytes = excluded.size_bytes,
                    meta_json = excluded.meta_json,
                    last_used_at = excluded.last_used_at
                """,
                (
                    identity_key, kind, int(simulation), render_mode, path, sha256,
                    size_bytes, json.dumps(meta or {}, ensure_ascii=False),
                    ahora, ahora,
                ),
            )

    def touch_artifact(self, identity_key: str) -> None:
        with self.storage.write() as conexion:
            conexion.execute(
                "UPDATE render_artifacts SET last_used_at = ? WHERE identity_key = ?",
                (iso(utcnow()), identity_key),
            )

    def drop_artifact(self, identity_key: str) -> None:
        with self.storage.write() as conexion:
            conexion.execute(
                "DELETE FROM render_artifacts WHERE identity_key = ?", (identity_key,)
            )

    # -- Intentos por etapa -------------------------------------------------

    def attempts(self, render_run_id: str, stage: str) -> int:
        fila = self.storage.connect().execute(
            "SELECT COUNT(*) AS n FROM render_attempts "
            "WHERE render_run_id = ? AND stage = ?",
            (render_run_id, stage),
        ).fetchone()
        return int(fila["n"]) if fila else 0

    def record_attempt(
        self,
        *,
        render_run_id: str,
        job_id: str,
        stage: str,
        attempt: int,
        status: str,
        elapsed_s: float = 0.0,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with self.storage.write() as conexion:
            conexion.execute(
                """
                INSERT INTO render_attempts (
                    render_run_id, job_id, stage, attempt, status, error_code,
                    error_message, elapsed_s, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    render_run_id, job_id, stage, attempt, status, error_code,
                    (error_message or "")[:500] or None, elapsed_s, iso(utcnow()),
                ),
            )

    def encodes_total(self, job_id: str) -> int:
        """Codificaciones REALES del trabajo. Una consulta con ffprobe no cuenta."""
        fila = self.storage.connect().execute(
            "SELECT COUNT(*) AS n FROM render_attempts "
            "WHERE job_id = ? AND status = 'ok' AND stage NOT LIKE 'probe%'",
            (job_id,),
        ).fetchone()
        return int(fila["n"]) if fila else 0

    # -- Exportaciones ------------------------------------------------------

    def record_export(
        self,
        *,
        render_run_id: str,
        job_id: str,
        path: str,
        sha256: str,
        size_bytes: int,
        status: str,
    ) -> None:
        with self.storage.write() as conexion:
            conexion.execute(
                """
                INSERT INTO render_exports (
                    render_run_id, job_id, path, sha256, size_bytes, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(render_run_id) DO UPDATE SET
                    path = excluded.path,
                    sha256 = excluded.sha256,
                    size_bytes = excluded.size_bytes,
                    status = excluded.status,
                    created_at = excluded.created_at
                """,
                (render_run_id, job_id, path, sha256, size_bytes, status, iso(utcnow())),
            )

    def get_export(self, render_run_id: str) -> dict | None:
        fila = self.storage.connect().execute(
            "SELECT * FROM render_exports WHERE render_run_id = ?", (render_run_id,)
        ).fetchone()
        return dict(fila) if fila else None
