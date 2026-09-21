"""Persistencia del modulo 2 sobre la misma base SQLite del modulo 1.

La migracion es IDEMPOTENTE (`CREATE TABLE IF NOT EXISTS`) y solo anade
tablas: los trabajos, guiones y candidatos anteriores se conservan intactos.

Las transacciones son cortas y nunca se mantiene una abierta durante una
llamada HTTP: cada reserva y cada liquidacion son escrituras independientes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..storage import Storage, iso, utcnow

VOICE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS voice_runs (
    voice_run_id   TEXT PRIMARY KEY,
    job_id         TEXT NOT NULL,
    voice_key      TEXT NOT NULL UNIQUE,
    fingerprint    TEXT NOT NULL,
    simulation     INTEGER NOT NULL,
    status         TEXT NOT NULL,
    provider       TEXT,
    model_id       TEXT,
    voice_id       TEXT,
    script_sha256  TEXT NOT NULL,
    script_path    TEXT,
    manifest_json  TEXT,
    voice_status   TEXT,
    error_code     TEXT,
    error_message  TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_voice_runs_job ON voice_runs(job_id);

CREATE TABLE IF NOT EXISTS voice_clips (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    voice_run_id    TEXT NOT NULL,
    scene_id        TEXT NOT NULL,
    identity_sha256 TEXT NOT NULL,
    clip_path       TEXT NOT NULL,
    clip_sha256     TEXT NOT NULL,
    clip_samples    INTEGER NOT NULL,
    alignment_json  TEXT,
    request_id      TEXT,
    characters_sent INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    UNIQUE(voice_run_id, scene_id)
);

CREATE TABLE IF NOT EXISTS voice_requests (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    voice_run_id  TEXT NOT NULL,
    job_id        TEXT NOT NULL,
    kind          TEXT NOT NULL,
    scene_id      TEXT,
    status        TEXT NOT NULL,
    request_id    TEXT,
    characters    INTEGER NOT NULL DEFAULT 0,
    latency_ms    INTEGER NOT NULL DEFAULT 0,
    http_status   INTEGER,
    error_code    TEXT,
    error_message TEXT,
    reserved_at   TEXT NOT NULL,
    settled_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_voice_requests_job ON voice_requests(job_id);

CREATE TABLE IF NOT EXISTS voice_exports (
    voice_run_id TEXT PRIMARY KEY,
    path         TEXT NOT NULL,
    sha256       TEXT NOT NULL,
    status       TEXT NOT NULL,
    exported_at  TEXT NOT NULL
);
"""


class VoiceStorage:
    """Repositorio de voz. Reutiliza la conexion y el esquema del modulo 1."""

    def __init__(self, storage: Storage) -> None:
        self.storage = storage
        self._migrated = False

    # -- Migracion ---------------------------------------------------------

    def migrate(self) -> None:
        """Anade las tablas de voz si no existen. Se puede llamar N veces."""
        if self._migrated:
            return
        conexion = self.storage.connect()
        conexion.executescript(VOICE_SCHEMA_SQL)
        conexion.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES('voice_schema_version', '1')"
        )
        self._migrated = True

    # -- Ejecuciones -------------------------------------------------------

    def get_run_by_key(self, voice_key: str) -> dict | None:
        fila = self.storage.connect().execute(
            "SELECT * FROM voice_runs WHERE voice_key = ?", (voice_key,)
        ).fetchone()
        return dict(fila) if fila else None

    def get_run(self, voice_run_id: str) -> dict | None:
        fila = self.storage.connect().execute(
            "SELECT * FROM voice_runs WHERE voice_run_id = ?", (voice_run_id,)
        ).fetchone()
        return dict(fila) if fila else None

    def create_run(self, run: dict) -> dict:
        ahora = iso(utcnow())
        datos = {**run, "created_at": ahora, "updated_at": ahora}
        columnas = ", ".join(datos)
        marcadores = ", ".join(f":{nombre}" for nombre in datos)
        with self.storage.write() as conexion:
            conexion.execute(
                f"INSERT INTO voice_runs ({columnas}) VALUES ({marcadores})", datos
            )
        return self.get_run(datos["voice_run_id"])  # type: ignore[return-value]

    def update_run(self, voice_run_id: str, **campos: Any) -> None:
        if not campos:
            return
        campos["updated_at"] = iso(utcnow())
        asignaciones = ", ".join(f"{nombre} = :{nombre}" for nombre in campos)
        with self.storage.write() as conexion:
            conexion.execute(
                f"UPDATE voice_runs SET {asignaciones} WHERE voice_run_id = :voice_run_id",
                {**campos, "voice_run_id": voice_run_id},
            )

    def save_manifest(self, voice_run_id: str, manifest: dict, voice_status: str) -> None:
        self.update_run(
            voice_run_id,
            manifest_json=json.dumps(manifest, ensure_ascii=False),
            voice_status=voice_status,
        )

    def load_manifest(self, voice_run_id: str) -> dict | None:
        fila = self.storage.connect().execute(
            "SELECT manifest_json FROM voice_runs WHERE voice_run_id = ?", (voice_run_id,)
        ).fetchone()
        if not fila or not fila["manifest_json"]:
            return None
        return json.loads(fila["manifest_json"])

    # -- Presupuesto -------------------------------------------------------

    def requests_used(self, job_id: str) -> int:
        """Peticiones reservadas historicamente para el TRABAJO.

        Cuenta las reservas, no las respuestas correctas: un timeout ya pudo
        consumir credito, asi que tambien gasta presupuesto.
        """
        fila = self.storage.connect().execute(
            "SELECT COUNT(*) AS total FROM voice_requests WHERE job_id = ?", (job_id,)
        ).fetchone()
        return int(fila["total"])

    def request_totals(self, job_id: str) -> dict:
        fila = self.storage.connect().execute(
            "SELECT COUNT(*) AS requests, COALESCE(SUM(characters),0) AS characters, "
            "COALESCE(SUM(latency_ms),0) AS latency_ms FROM voice_requests WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        return dict(fila)

    def reserve_request(
        self, *, voice_run_id: str, job_id: str, kind: str, scene_id: str | None
    ) -> int:
        """Persiste la reserva ANTES de enviar la peticion."""
        with self.storage.write() as conexion:
            cursor = conexion.execute(
                "INSERT INTO voice_requests "
                "(voice_run_id, job_id, kind, scene_id, status, reserved_at) "
                "VALUES (?, ?, ?, ?, 'reserved', ?)",
                (voice_run_id, job_id, kind, scene_id, iso(utcnow())),
            )
            return int(cursor.lastrowid)

    def settle_request(self, token: int, **campos: Any) -> None:
        permitidos = {
            "status", "request_id", "characters", "latency_ms",
            "http_status", "error_code", "error_message",
        }
        datos = {clave: valor for clave, valor in campos.items() if clave in permitidos}
        datos["settled_at"] = iso(utcnow())
        asignaciones = ", ".join(f"{nombre} = :{nombre}" for nombre in datos)
        with self.storage.write() as conexion:
            conexion.execute(
                f"UPDATE voice_requests SET {asignaciones} WHERE id = :id",
                {**datos, "id": token},
            )

    def request_ids(self, job_id: str) -> list[str]:
        filas = self.storage.connect().execute(
            "SELECT DISTINCT request_id FROM voice_requests "
            "WHERE job_id = ? AND request_id IS NOT NULL ORDER BY id",
            (job_id,),
        ).fetchall()
        return [fila["request_id"] for fila in filas]

    # -- Clips -------------------------------------------------------------

    def save_clip(self, voice_run_id: str, clip: dict) -> None:
        with self.storage.write() as conexion:
            conexion.execute(
                "INSERT INTO voice_clips "
                "(voice_run_id, scene_id, identity_sha256, clip_path, clip_sha256, "
                "clip_samples, alignment_json, request_id, characters_sent, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(voice_run_id, scene_id) DO UPDATE SET "
                "identity_sha256 = excluded.identity_sha256, clip_path = excluded.clip_path, "
                "clip_sha256 = excluded.clip_sha256, clip_samples = excluded.clip_samples, "
                "alignment_json = excluded.alignment_json, request_id = excluded.request_id, "
                "characters_sent = excluded.characters_sent, created_at = excluded.created_at",
                (
                    voice_run_id,
                    clip["scene_id"],
                    clip["identity_sha256"],
                    clip["clip_path"],
                    clip["clip_sha256"],
                    int(clip["clip_samples"]),
                    json.dumps(clip.get("alignment"), ensure_ascii=False)
                    if clip.get("alignment") is not None
                    else None,
                    clip.get("request_id"),
                    int(clip.get("characters_sent", 0)),
                    iso(utcnow()),
                ),
            )

    def load_clips(self, voice_run_id: str) -> dict[str, dict]:
        filas = self.storage.connect().execute(
            "SELECT * FROM voice_clips WHERE voice_run_id = ? ORDER BY id", (voice_run_id,)
        ).fetchall()
        resultado: dict[str, dict] = {}
        for fila in filas:
            datos = dict(fila)
            datos["alignment"] = (
                json.loads(datos["alignment_json"]) if datos["alignment_json"] else None
            )
            resultado[datos["scene_id"]] = datos
        return resultado

    # -- Exportaciones -----------------------------------------------------

    def record_export(self, voice_run_id: str, *, path: Path | str, sha256: str, status: str) -> None:
        with self.storage.write() as conexion:
            conexion.execute(
                "INSERT INTO voice_exports (voice_run_id, path, sha256, status, exported_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(voice_run_id) DO UPDATE SET path = excluded.path, "
                "sha256 = excluded.sha256, status = excluded.status, "
                "exported_at = excluded.exported_at",
                (voice_run_id, str(path), sha256, status, iso(utcnow())),
            )

    def get_export(self, voice_run_id: str) -> dict | None:
        fila = self.storage.connect().execute(
            "SELECT * FROM voice_exports WHERE voice_run_id = ?", (voice_run_id,)
        ).fetchone()
        return dict(fila) if fila else None
