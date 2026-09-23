"""Persistencia del modulo 5 sobre la misma base SQLite.

SQLite es la fuente de verdad. El plan y el recibo son exportaciones; lo que
decide si hay que enviar algo, si ya se envio y cuanto se ha gastado esta
aqui.

Cuatro reglas que gobiernan el diseno de estas tablas:

1. **Las transacciones son cortas.** Ninguna escritura queda abierta mientras
   se transfiere un archivo o se espera a una API. Se escribe la intencion
   antes de la operacion y el resultado justo despues.
2. **La concesion vence.** Un trabajador reclama un destino con una concesion
   con caducidad; si el proceso muere, otra invocacion la recupera al vencer
   en vez de dejar la tarea bloqueada para siempre.
3. **Los espacios de identidad no se mezclan.** La misma `publish_key` en
   modo simulado y en modo real son dos trabajos distintos, y la proteccion
   antiduplicados tambien es independiente.
4. **El gasto es persistente.** Solicitudes, bytes e intentos sobreviven al
   reinicio: abrir otro proceso no regala presupuesto.

Las migraciones solo anaden tablas: nada de los modulos 1-4 se toca.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Iterable

from ..errors import IdempotencyConflictError
from ..storage import Storage, iso, utcnow
from .errors import DuplicateDeliveryError
from .schemas import DestinationState, transition_allowed

PUBLISH_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS publication_jobs (
    publication_id      TEXT PRIMARY KEY,
    publish_key         TEXT NOT NULL,
    identity_space      TEXT NOT NULL,
    mode                TEXT NOT NULL,
    simulation          INTEGER NOT NULL,
    job_id              TEXT NOT NULL,
    render_run_id       TEXT NOT NULL,
    intent_fingerprint  TEXT NOT NULL,
    plan_revision       INTEGER NOT NULL,
    plan_sha256         TEXT NOT NULL,
    plan_json           TEXT NOT NULL,
    video_sha256        TEXT NOT NULL,
    video_path          TEXT NOT NULL,
    status              TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE (publish_key, identity_space)
);
CREATE INDEX IF NOT EXISTS idx_publication_jobs_job ON publication_jobs(job_id);

CREATE TABLE IF NOT EXISTS publication_destinations (
    publication_id       TEXT NOT NULL,
    destination_id       TEXT NOT NULL,
    platform             TEXT NOT NULL,
    account_alias        TEXT NOT NULL,
    expected_account_id  TEXT,
    observed_account_id  TEXT,
    state                TEXT NOT NULL,
    phase                TEXT NOT NULL DEFAULT 'not_started',
    simulation           INTEGER NOT NULL,
    requested_visibility TEXT NOT NULL,
    observed_visibility  TEXT,
    publicly_visible     INTEGER,
    real_remote_id       TEXT,
    mock_remote_id       TEXT,
    remote_refs_json     TEXT NOT NULL DEFAULT '{}',
    permalink            TEXT,
    scheduled_at         TEXT,
    timezone             TEXT,
    fold                 INTEGER NOT NULL DEFAULT 0,
    late_start_window_s  INTEGER NOT NULL DEFAULT 900,
    dispatch_started_at  TEXT,
    delivered_at         TEXT,
    next_attempt_at      TEXT,
    next_poll_at         TEXT,
    requests_used        INTEGER NOT NULL DEFAULT 0,
    bytes_transferred    INTEGER NOT NULL DEFAULT 0,
    attempts             INTEGER NOT NULL DEFAULT 0,
    lease_owner          TEXT,
    lease_expires_at     TEXT,
    last_error_json      TEXT,
    last_evidence_json   TEXT,
    staging_json         TEXT,
    manual_export_json   TEXT,
    manual_report_json   TEXT,
    cancel_requested_at  TEXT,
    cancel_note          TEXT,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    PRIMARY KEY (publication_id, destination_id)
);
CREATE INDEX IF NOT EXISTS idx_publication_destinations_state
    ON publication_destinations(state, scheduled_at);

CREATE TABLE IF NOT EXISTS publication_authorizations (
    authorization_id    TEXT PRIMARY KEY,
    publication_id      TEXT NOT NULL,
    intent_fingerprint  TEXT NOT NULL,
    plan_sha256         TEXT NOT NULL,
    plan_revision       INTEGER NOT NULL,
    video_sha256        TEXT NOT NULL,
    account_ids_json    TEXT NOT NULL,
    mode                TEXT NOT NULL,
    operator_identity   TEXT NOT NULL,
    staging_authorized  INTEGER NOT NULL DEFAULT 0,
    authorized_at       TEXT NOT NULL,
    revoked_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_publication_auth_pub
    ON publication_authorizations(publication_id, revoked_at);

CREATE TABLE IF NOT EXISTS publication_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    publication_id  TEXT NOT NULL,
    destination_id  TEXT,
    kind            TEXT NOT NULL,
    state           TEXT,
    phase           TEXT,
    detail_json     TEXT NOT NULL DEFAULT '{}',
    requests_delta  INTEGER NOT NULL DEFAULT 0,
    bytes_delta     INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_publication_events_pub
    ON publication_events(publication_id, created_at);

CREATE TABLE IF NOT EXISTS publication_deliveries (
    identity_space  TEXT NOT NULL,
    platform        TEXT NOT NULL,
    account_key     TEXT NOT NULL,
    video_sha256    TEXT NOT NULL,
    publication_id  TEXT NOT NULL,
    destination_id  TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (identity_space, platform, account_key, video_sha256)
);

CREATE TABLE IF NOT EXISTS publication_staging_objects (
    object_key      TEXT NOT NULL,
    bucket_alias    TEXT NOT NULL,
    publication_id  TEXT NOT NULL,
    destination_id  TEXT NOT NULL,
    object_sha256   TEXT NOT NULL,
    size_bytes      INTEGER NOT NULL,
    uploaded_at     TEXT NOT NULL,
    retain_until    TEXT,
    deleted_at      TEXT,
    PRIMARY KEY (bucket_alias, object_key)
);
"""


def _fila(row) -> dict | None:
    return dict(row) if row is not None else None


class PublishStorage:
    """Acceso a las tablas del modulo 5."""

    def __init__(self, storage: Storage) -> None:
        self.storage = storage
        self._migrated = False

    def migrate(self) -> None:
        """Anade las tablas del modulo 5 si no existen. Idempotente.

        `executescript` hace su propio commit: no va dentro de una transaccion
        explicita.
        """
        if self._migrated:
            return
        conexion = self.storage.connect()
        conexion.executescript(PUBLISH_SCHEMA_SQL)
        self._migrated = True

    # -- Trabajos ----------------------------------------------------------

    def get_publication(self, publication_id: str) -> dict | None:
        self.migrate()
        return _fila(
            self.storage.connect()
            .execute(
                "SELECT * FROM publication_jobs WHERE publication_id = ?",
                (publication_id,),
            )
            .fetchone()
        )

    def find_publication(self, publish_key: str, *, identity_space: str) -> dict | None:
        """Busca por clave DENTRO de su espacio de identidad."""
        self.migrate()
        return _fila(
            self.storage.connect()
            .execute(
                "SELECT * FROM publication_jobs "
                "WHERE publish_key = ? AND identity_space = ?",
                (publish_key, identity_space),
            )
            .fetchone()
        )

    def upsert_publication(self, datos: dict) -> dict:
        """Crea el trabajo o recupera el existente si la intencion es la misma.

        Misma clave y mismo fingerprint: se reanuda. Misma clave con otra
        intencion: conflicto, que es lo que el proyecto hace en los modulos
        anteriores. Reutilizar la clave para otro video seria justo la forma
        de perder la trazabilidad.
        """
        self.migrate()
        existente = self.find_publication(
            datos["publish_key"], identity_space=datos["identity_space"]
        )
        if existente is not None:
            if existente["intent_fingerprint"] != datos["intent_fingerprint"]:
                raise IdempotencyConflictError(
                    f"La clave {datos['publish_key']!r} ya existe en el espacio "
                    f"{datos['identity_space']} con otra intencion. Cambiar cuenta, "
                    "video, texto, privacidad, horario o destino exige una clave "
                    "nueva o una revision autorizada.",
                    details={
                        "publish_key": datos["publish_key"],
                        "identity_space": datos["identity_space"],
                        "stored_fingerprint": existente["intent_fingerprint"],
                        "incoming_fingerprint": datos["intent_fingerprint"],
                    },
                )
            return existente

        ahora = iso(utcnow())
        payload = {**datos, "created_at": ahora, "updated_at": ahora}
        columnas = ", ".join(payload)
        marcadores = ", ".join(f":{nombre}" for nombre in payload)
        with self.storage.write() as conexion:
            conexion.execute(
                f"INSERT INTO publication_jobs ({columnas}) VALUES ({marcadores})",
                payload,
            )
        return self.get_publication(payload["publication_id"])  # type: ignore[return-value]

    def update_publication(self, publication_id: str, **campos: Any) -> None:
        if not campos:
            return
        self.migrate()
        campos["updated_at"] = iso(utcnow())
        asignaciones = ", ".join(f"{nombre} = :{nombre}" for nombre in campos)
        with self.storage.write() as conexion:
            conexion.execute(
                f"UPDATE publication_jobs SET {asignaciones} "
                "WHERE publication_id = :publication_id",
                {**campos, "publication_id": publication_id},
            )

    # -- Destinos ----------------------------------------------------------

    def get_destination(self, publication_id: str, destination_id: str) -> dict | None:
        self.migrate()
        return _fila(
            self.storage.connect()
            .execute(
                "SELECT * FROM publication_destinations "
                "WHERE publication_id = ? AND destination_id = ?",
                (publication_id, destination_id),
            )
            .fetchone()
        )

    def list_destinations(self, publication_id: str) -> list[dict]:
        self.migrate()
        filas = self.storage.connect().execute(
            "SELECT * FROM publication_destinations WHERE publication_id = ? "
            "ORDER BY destination_id",
            (publication_id,),
        )
        return [dict(fila) for fila in filas]

    def create_destination(self, datos: dict) -> dict:
        """Inserta un destino nuevo; si ya existe, lo devuelve sin tocarlo."""
        self.migrate()
        existente = self.get_destination(datos["publication_id"], datos["destination_id"])
        if existente is not None:
            return existente
        ahora = iso(utcnow())
        payload = {**datos, "created_at": ahora, "updated_at": ahora}
        columnas = ", ".join(payload)
        marcadores = ", ".join(f":{nombre}" for nombre in payload)
        with self.storage.write() as conexion:
            conexion.execute(
                f"INSERT INTO publication_destinations ({columnas}) "
                f"VALUES ({marcadores})",
                payload,
            )
        return self.get_destination(datos["publication_id"], datos["destination_id"])  # type: ignore[return-value]

    def update_destination(
        self,
        publication_id: str,
        destination_id: str,
        *,
        state: DestinationState | None = None,
        requests_delta: int = 0,
        bytes_delta: int = 0,
        attempts_delta: int = 0,
        **campos: Any,
    ) -> dict:
        """Actualiza un destino en una transaccion corta.

        El cambio de estado se comprueba contra la tabla de transiciones
        dentro de la misma transaccion: asi dos invocaciones concurrentes no
        pueden inventarse un camino que no existe.
        """
        self.migrate()
        with self.storage.write() as conexion:
            fila = conexion.execute(
                "SELECT * FROM publication_destinations "
                "WHERE publication_id = ? AND destination_id = ?",
                (publication_id, destination_id),
            ).fetchone()
            if fila is None:
                raise KeyError(f"destino desconocido: {destination_id}")
            actual = DestinationState(fila["state"])
            if state is not None and not transition_allowed(actual, state):
                raise ValueError(
                    f"transicion no documentada: {actual.value} -> {state.value} "
                    f"en {destination_id}"
                )
            asignaciones = dict(campos)
            if state is not None:
                asignaciones["state"] = state.value
            asignaciones["requests_used"] = fila["requests_used"] + requests_delta
            asignaciones["bytes_transferred"] = fila["bytes_transferred"] + bytes_delta
            asignaciones["attempts"] = fila["attempts"] + attempts_delta
            asignaciones["updated_at"] = iso(utcnow())
            sql = ", ".join(f"{nombre} = :{nombre}" for nombre in asignaciones)
            conexion.execute(
                f"UPDATE publication_destinations SET {sql} "
                "WHERE publication_id = :pub AND destination_id = :dest",
                {**asignaciones, "pub": publication_id, "dest": destination_id},
            )
        return self.get_destination(publication_id, destination_id)  # type: ignore[return-value]

    # -- Concesion del trabajador -----------------------------------------

    def claim_destination(
        self,
        publication_id: str,
        destination_id: str,
        *,
        owner: str,
        now: datetime,
        lease_seconds: int,
    ) -> dict | None:
        """Reclama un destino si nadie lo tiene, o si la concesion vencio.

        Devuelve la fila reclamada o None si otro trabajador la tiene viva.
        La comprobacion y la escritura ocurren en la MISMA transaccion: dos
        invocaciones simultaneas no pueden reclamar el mismo destino.
        """
        self.migrate()
        vencimiento = iso(now + timedelta(seconds=lease_seconds))
        with self.storage.write() as conexion:
            fila = conexion.execute(
                "SELECT * FROM publication_destinations "
                "WHERE publication_id = ? AND destination_id = ?",
                (publication_id, destination_id),
            ).fetchone()
            if fila is None:
                return None
            vivo = (
                fila["lease_owner"] is not None
                and fila["lease_expires_at"] is not None
                and fila["lease_expires_at"] > iso(now)
                and fila["lease_owner"] != owner
            )
            if vivo:
                return None
            conexion.execute(
                "UPDATE publication_destinations "
                "SET lease_owner = ?, lease_expires_at = ?, updated_at = ? "
                "WHERE publication_id = ? AND destination_id = ?",
                (owner, vencimiento, iso(utcnow()), publication_id, destination_id),
            )
        return self.get_destination(publication_id, destination_id)

    def renew_lease(
        self,
        publication_id: str,
        destination_id: str,
        *,
        owner: str,
        now: datetime,
        lease_seconds: int,
    ) -> bool:
        """Renueva la concesion durante una transferencia larga."""
        self.migrate()
        with self.storage.write() as conexion:
            cursor = conexion.execute(
                "UPDATE publication_destinations "
                "SET lease_expires_at = ?, updated_at = ? "
                "WHERE publication_id = ? AND destination_id = ? AND lease_owner = ?",
                (
                    iso(now + timedelta(seconds=lease_seconds)),
                    iso(utcnow()),
                    publication_id,
                    destination_id,
                    owner,
                ),
            )
            return cursor.rowcount == 1

    def release_lease(self, publication_id: str, destination_id: str, *, owner: str) -> None:
        self.migrate()
        with self.storage.write() as conexion:
            conexion.execute(
                "UPDATE publication_destinations "
                "SET lease_owner = NULL, lease_expires_at = NULL, updated_at = ? "
                "WHERE publication_id = ? AND destination_id = ? AND lease_owner = ?",
                (iso(utcnow()), publication_id, destination_id, owner),
            )

    def due_destinations(
        self, *, now: datetime, states: Iterable[DestinationState] | None = None
    ) -> list[dict]:
        """Destinos con trabajo pendiente: programados vencidos o en espera.

        No filtra por la ventana de retraso: esa decision es del trabajador,
        que debe poder ver una tarea vencida para mandarla a revision.
        """
        self.migrate()
        estados = [
            estado.value
            for estado in (
                states
                or (
                    DestinationState.SCHEDULED_LOCAL,
                    DestinationState.DISPATCHING,
                    DestinationState.WAITING_REMOTE,
                )
            )
        ]
        marcadores = ", ".join("?" for _ in estados)
        filas = self.storage.connect().execute(
            f"SELECT * FROM publication_destinations WHERE state IN ({marcadores}) "
            "AND (scheduled_at IS NULL OR scheduled_at <= ?) "
            "AND (next_attempt_at IS NULL OR next_attempt_at <= ?) "
            "AND (next_poll_at IS NULL OR next_poll_at <= ?) "
            "ORDER BY scheduled_at, destination_id",
            (*estados, iso(now), iso(now), iso(now)),
        )
        return [dict(fila) for fila in filas]

    # -- Autorizacion ------------------------------------------------------

    def save_authorization(self, datos: dict) -> dict:
        self.migrate()
        with self.storage.write() as conexion:
            columnas = ", ".join(datos)
            marcadores = ", ".join(f":{nombre}" for nombre in datos)
            conexion.execute(
                f"INSERT INTO publication_authorizations ({columnas}) "
                f"VALUES ({marcadores})",
                datos,
            )
        return datos

    def active_authorization(self, publication_id: str) -> dict | None:
        """La ultima autorizacion viva de ese trabajo."""
        self.migrate()
        return _fila(
            self.storage.connect()
            .execute(
                "SELECT * FROM publication_authorizations "
                "WHERE publication_id = ? AND revoked_at IS NULL "
                "ORDER BY authorized_at DESC, rowid DESC LIMIT 1",
                (publication_id,),
            )
            .fetchone()
        )

    def revoke_authorizations(self, publication_id: str, *, at: datetime) -> int:
        self.migrate()
        with self.storage.write() as conexion:
            cursor = conexion.execute(
                "UPDATE publication_authorizations SET revoked_at = ? "
                "WHERE publication_id = ? AND revoked_at IS NULL",
                (iso(at), publication_id),
            )
            return cursor.rowcount

    # -- Antiduplicados y cuota diaria ------------------------------------

    def reserve_delivery(
        self,
        *,
        identity_space: str,
        platform: str,
        account_key: str,
        video_sha256: str,
        publication_id: str,
        destination_id: str,
    ) -> None:
        """Reserva (plataforma, cuenta, video) para que no salga dos veces.

        La identidad NO es el titulo: dos planes con el mismo video y la misma
        cuenta son un duplicado aunque se llamen distinto. La misma tarea
        reintentandose no lo es.
        """
        self.migrate()
        with self.storage.write() as conexion:
            fila = conexion.execute(
                "SELECT * FROM publication_deliveries WHERE identity_space = ? "
                "AND platform = ? AND account_key = ? AND video_sha256 = ?",
                (identity_space, platform, account_key, video_sha256),
            ).fetchone()
            if fila is not None:
                if (
                    fila["publication_id"] == publication_id
                    and fila["destination_id"] == destination_id
                ):
                    return
                raise DuplicateDeliveryError(
                    "Ese mismo MP4 ya esta reservado para esa plataforma y cuenta "
                    f"por el trabajo {fila['publication_id']} "
                    f"({fila['destination_id']}). Una republicacion deliberada "
                    "queda fuera del flujo automatico de esta entrega.",
                    details={
                        "platform": platform,
                        "account_key": account_key,
                        "video_sha256": video_sha256,
                        "existing_publication_id": fila["publication_id"],
                    },
                )
            conexion.execute(
                "INSERT INTO publication_deliveries (identity_space, platform, "
                "account_key, video_sha256, publication_id, destination_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    identity_space,
                    platform,
                    account_key,
                    video_sha256,
                    publication_id,
                    destination_id,
                    iso(utcnow()),
                ),
            )

    def count_new_deliveries(
        self, *, identity_space: str, platform: str, account_key: str, day: str
    ) -> int:
        """Entregas nuevas reservadas para esa cuenta en un dia UTC."""
        self.migrate()
        fila = self.storage.connect().execute(
            "SELECT COUNT(*) AS total FROM publication_deliveries "
            "WHERE identity_space = ? AND platform = ? AND account_key = ? "
            "AND substr(created_at, 1, 10) = ?",
            (identity_space, platform, account_key, day),
        ).fetchone()
        return int(fila["total"])

    # -- Trazabilidad ------------------------------------------------------

    def record_event(
        self,
        *,
        publication_id: str,
        kind: str,
        destination_id: str | None = None,
        state: str | None = None,
        phase: str | None = None,
        detail: dict | None = None,
        requests_delta: int = 0,
        bytes_delta: int = 0,
    ) -> None:
        """Deja constancia ANTES de cada operacion mutante y despues de ella.

        `detail` se serializa tal cual: no debe llevar secretos. Los contratos
        del modulo ya rechazan URLs firmadas y cabeceras de autorizacion.
        """
        self.migrate()
        with self.storage.write() as conexion:
            conexion.execute(
                "INSERT INTO publication_events (publication_id, destination_id, "
                "kind, state, phase, detail_json, requests_delta, bytes_delta, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    publication_id,
                    destination_id,
                    kind,
                    state,
                    phase,
                    json.dumps(detail or {}, ensure_ascii=False),
                    requests_delta,
                    bytes_delta,
                    iso(utcnow()),
                ),
            )

    def events(self, publication_id: str, *, limit: int = 200) -> list[dict]:
        self.migrate()
        filas = self.storage.connect().execute(
            "SELECT * FROM publication_events WHERE publication_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (publication_id, limit),
        )
        return [dict(fila) for fila in filas]

    # -- Staging -----------------------------------------------------------

    def record_staging_object(self, datos: dict) -> None:
        self.migrate()
        with self.storage.write() as conexion:
            conexion.execute(
                "INSERT OR REPLACE INTO publication_staging_objects (object_key, "
                "bucket_alias, publication_id, destination_id, object_sha256, "
                "size_bytes, uploaded_at, retain_until, deleted_at) "
                "VALUES (:object_key, :bucket_alias, :publication_id, "
                ":destination_id, :object_sha256, :size_bytes, :uploaded_at, "
                ":retain_until, :deleted_at)",
                {"retain_until": None, "deleted_at": None, **datos},
            )

    def staging_objects(self, *, only_live: bool = True) -> list[dict]:
        self.migrate()
        sql = "SELECT * FROM publication_staging_objects"
        if only_live:
            sql += " WHERE deleted_at IS NULL"
        return [dict(fila) for fila in self.storage.connect().execute(sql)]

    def mark_staging_deleted(self, *, bucket_alias: str, object_key: str, at: datetime) -> None:
        self.migrate()
        with self.storage.write() as conexion:
            conexion.execute(
                "UPDATE publication_staging_objects SET deleted_at = ? "
                "WHERE bucket_alias = ? AND object_key = ?",
                (iso(at), bucket_alias, object_key),
            )
