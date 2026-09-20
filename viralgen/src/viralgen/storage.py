"""Persistencia en SQLite y bloqueo de proceso.

SQLite es la fuente de verdad: trabajos, candidatos de idea, guiones
validados, historial de ideas, uso del proveedor y exportaciones. El
documento completo se guarda en la base de datos, de modo que una exportacion
fallida se puede rehacer sin volver a llamar al proveedor.

Las transacciones son cortas y JAMAS se mantiene una transaccion de escritura
abierta durante una llamada HTTP: la orquestacion abre y cierra la escritura
alrededor de cada etapa.
"""

from __future__ import annotations

import errno
import fcntl
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .errors import WorkerLockedError
from .scoring import HistoryItem

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id           TEXT PRIMARY KEY,
    job_key          TEXT NOT NULL UNIQUE,
    command          TEXT NOT NULL,
    profile_id       TEXT NOT NULL,
    channel          TEXT NOT NULL,
    topic            TEXT,
    duration_s       REAL,
    simulation       INTEGER NOT NULL,
    fingerprint      TEXT NOT NULL,
    request_json     TEXT NOT NULL,
    status           TEXT NOT NULL,
    stages_json      TEXT NOT NULL DEFAULT '{}',
    provider         TEXT,
    model            TEXT,
    prompt_version   TEXT,
    config_hash      TEXT,
    source_pack_hash TEXT,
    error_code       TEXT,
    error_message    TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS idea_candidates (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id        TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    batch_index   INTEGER NOT NULL,
    idea_ref      TEXT NOT NULL,
    norm_hash     TEXT NOT NULL,
    score         REAL NOT NULL,
    selected      INTEGER NOT NULL DEFAULT 0,
    payload_json  TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_candidates_job ON idea_candidates(job_id);

CREATE TABLE IF NOT EXISTS scripts (
    job_id            TEXT PRIMARY KEY REFERENCES jobs(job_id) ON DELETE CASCADE,
    production_status TEXT NOT NULL,
    document_json     TEXT NOT NULL,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS idea_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          TEXT NOT NULL,
    channel         TEXT NOT NULL,
    profile_id      TEXT NOT NULL,
    title           TEXT NOT NULL,
    premise         TEXT NOT NULL,
    norm_hash       TEXT NOT NULL,
    central_fact_id TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_channel ON idea_history(channel, created_at);

CREATE TABLE IF NOT EXISTS usage_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id        TEXT NOT NULL,
    stage         TEXT NOT NULL,
    provider      TEXT NOT NULL,
    model         TEXT NOT NULL,
    request_id    TEXT,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    latency_ms    INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL,
    error_code    TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_job ON usage_events(job_id);

CREATE TABLE IF NOT EXISTS exports (
    job_id      TEXT PRIMARY KEY REFERENCES jobs(job_id) ON DELETE CASCADE,
    path        TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    status      TEXT NOT NULL,
    exported_at TEXT NOT NULL
);
"""

DB_SCHEMA_VERSION = "1"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ProcessLock:
    """Bloqueo exclusivo de archivo para impedir dos workers simultaneos."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle = None

    def __enter__(self) -> "ProcessLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a+")
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._handle.close()
            self._handle = None
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                raise WorkerLockedError(
                    f"Ya hay otra ejecucion de viralgen usando {self.path.parent}. "
                    "Espera a que termine o usa otro DATA_DIR.",
                    details={"lock_path": str(self.path)},
                ) from exc
            raise
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None


class Storage:
    """Acceso a la base de datos del espacio de datos activo."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.db_path = data_dir / "viralgen.sqlite3"
        self._conn: sqlite3.Connection | None = None

    # -- Ciclo de vida -----------------------------------------------------

    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path, timeout=30.0, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=30000")
            conn.executescript(SCHEMA_SQL)
            conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES('db_schema_version', ?)",
                (DB_SCHEMA_VERSION,),
            )
            self._conn = conn
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "Storage":
        self.connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """Transaccion de escritura corta. No envolver llamadas HTTP con esto."""
        conn = self.connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")

    # -- Trabajos ----------------------------------------------------------

    def get_job_by_key(self, job_key: str) -> dict | None:
        row = self.connect().execute(
            "SELECT * FROM jobs WHERE job_key = ?", (job_key,)
        ).fetchone()
        return dict(row) if row else None

    def get_job(self, job_id: str) -> dict | None:
        row = self.connect().execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def create_job(self, job: dict) -> dict:
        now = iso(utcnow())
        payload = {
            **job,
            "stages_json": job.get("stages_json", "{}"),
            "created_at": now,
            "updated_at": now,
        }
        columns = ", ".join(payload)
        placeholders = ", ".join(f":{name}" for name in payload)
        with self.write() as conn:
            conn.execute(f"INSERT INTO jobs ({columns}) VALUES ({placeholders})", payload)
        return self.get_job(payload["job_id"])  # type: ignore[return-value]

    def update_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = iso(utcnow())
        assignments = ", ".join(f"{name} = :{name}" for name in fields)
        with self.write() as conn:
            conn.execute(
                f"UPDATE jobs SET {assignments} WHERE job_id = :job_id",
                {**fields, "job_id": job_id},
            )

    def stages(self, job_id: str) -> dict:
        job = self.get_job(job_id)
        if not job:
            return {}
        try:
            return json.loads(job["stages_json"] or "{}")
        except json.JSONDecodeError:
            return {}

    def mark_stage(self, job_id: str, stage: str, data: Any = True) -> dict:
        stages = self.stages(job_id)
        stages[stage] = data
        self.update_job(job_id, stages_json=json.dumps(stages, ensure_ascii=False))
        return stages

    # -- Candidatos --------------------------------------------------------

    def save_candidates(self, job_id: str, rows: list[dict], *, batch_index: int) -> None:
        now = iso(utcnow())
        with self.write() as conn:
            conn.executemany(
                "INSERT INTO idea_candidates "
                "(job_id, batch_index, idea_ref, norm_hash, score, selected, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        job_id,
                        batch_index,
                        row["idea_ref"],
                        row["norm_hash"],
                        float(row["score"]),
                        0,
                        json.dumps(row, ensure_ascii=False),
                        now,
                    )
                    for row in rows
                ],
            )

    def mark_selected(self, job_id: str, idea_ref: str) -> None:
        with self.write() as conn:
            conn.execute(
                "UPDATE idea_candidates SET selected = CASE WHEN idea_ref = ? THEN 1 ELSE 0 END "
                "WHERE job_id = ?",
                (idea_ref, job_id),
            )

    def load_candidates(self, job_id: str) -> list[dict]:
        rows = self.connect().execute(
            "SELECT payload_json, batch_index, selected FROM idea_candidates "
            "WHERE job_id = ? ORDER BY batch_index, id",
            (job_id,),
        ).fetchall()
        result = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            payload["selected"] = bool(row["selected"])
            result.append(payload)
        return result

    # -- Guiones -----------------------------------------------------------

    def save_script(self, job_id: str, document: dict, production_status: str) -> None:
        with self.write() as conn:
            conn.execute(
                "INSERT INTO scripts (job_id, production_status, document_json, created_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(job_id) DO UPDATE SET production_status = excluded.production_status, "
                "document_json = excluded.document_json, created_at = excluded.created_at",
                (job_id, production_status, json.dumps(document, ensure_ascii=False), iso(utcnow())),
            )

    def load_script(self, job_id: str) -> dict | None:
        row = self.connect().execute(
            "SELECT document_json FROM scripts WHERE job_id = ?", (job_id,)
        ).fetchone()
        return json.loads(row["document_json"]) if row else None

    # -- Historial ---------------------------------------------------------

    def add_history(
        self,
        *,
        job_id: str,
        channel: str,
        profile_id: str,
        title: str,
        premise: str,
        norm_hash: str,
        central_fact_id: str | None,
    ) -> None:
        with self.write() as conn:
            conn.execute(
                "INSERT INTO idea_history "
                "(job_id, channel, profile_id, title, premise, norm_hash, central_fact_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (job_id, channel, profile_id, title, premise, norm_hash, central_fact_id, iso(utcnow())),
            )

    def recent_history(self, channel: str, *, days: int, limit: int) -> list[HistoryItem]:
        """Ideas recientes del mismo canal.

        La busqueda local de duplicados abarca `days` dias (90 por defecto);
        al proveedor solo se le envian `limit` resumenes (30 por defecto).
        """
        since = iso(utcnow() - timedelta(days=days))
        rows = self.connect().execute(
            "SELECT title, premise, norm_hash, central_fact_id, job_id FROM idea_history "
            "WHERE channel = ? AND created_at >= ? ORDER BY created_at DESC LIMIT ?",
            (channel, since, limit),
        ).fetchall()
        return [
            HistoryItem(
                title=row["title"],
                premise=row["premise"],
                norm_hash=row["norm_hash"],
                central_fact_id=row["central_fact_id"],
                job_id=row["job_id"],
            )
            for row in rows
        ]

    # -- Uso ---------------------------------------------------------------

    def log_usage(
        self,
        *,
        job_id: str,
        stage: str,
        provider: str,
        model: str,
        request_id: str | None,
        input_tokens: int,
        output_tokens: int,
        latency_ms: int,
        status: str,
        error_code: str | None = None,
    ) -> None:
        with self.write() as conn:
            conn.execute(
                "INSERT INTO usage_events "
                "(job_id, stage, provider, model, request_id, input_tokens, output_tokens, "
                "latency_ms, status, error_code, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job_id,
                    stage,
                    provider,
                    model,
                    request_id,
                    input_tokens,
                    output_tokens,
                    latency_ms,
                    status,
                    error_code,
                    iso(utcnow()),
                ),
            )

    def usage_totals(self, job_id: str) -> dict:
        row = self.connect().execute(
            "SELECT COALESCE(SUM(input_tokens),0) AS input_tokens, "
            "COALESCE(SUM(output_tokens),0) AS output_tokens, COUNT(*) AS calls "
            "FROM usage_events WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        return dict(row)

    # -- Exportaciones -----------------------------------------------------

    def record_export(self, job_id: str, *, path: str, sha256: str, status: str) -> None:
        with self.write() as conn:
            conn.execute(
                "INSERT INTO exports (job_id, path, sha256, status, exported_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(job_id) DO UPDATE SET path = excluded.path, sha256 = excluded.sha256, "
                "status = excluded.status, exported_at = excluded.exported_at",
                (job_id, path, sha256, status, iso(utcnow())),
            )

    def get_export(self, job_id: str) -> dict | None:
        row = self.connect().execute(
            "SELECT * FROM exports WHERE job_id = ?", (job_id,)
        ).fetchone()
        return dict(row) if row else None
