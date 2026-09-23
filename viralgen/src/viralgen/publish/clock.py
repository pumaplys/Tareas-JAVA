"""Reloj inyectable y horarios con zona horaria real.

Tres reglas que el modulo 5 no negocia:

1. **Toda fecha lleva zona.** Se conserva el instante UTC *y* la zona IANA que
   escribio el operador, porque "las 18:30 de Madrid" y el UTC equivalente
   dejan de coincidir dos veces al ano.
2. **Una hora que no existe se rechaza.** En el salto de primavera las 02:30
   locales no ocurren: convertirlas en silencio a las 03:30 cambiaria la
   intencion autorizada sin que nadie lo decida.
3. **Una hora repetida se desambigua.** En el salto de otono las 02:30 ocurren
   dos veces. `fold=0` es la primera (horario de verano) y `fold=1` la segunda.
   Sin eleccion explicita no se programa.

El reloj es un objeto, no `datetime.now()` esparcido por el codigo: las pruebas
de ventanas, vencimientos y cambios estacionales necesitan adelantarlo a mano.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..errors import DocumentValidationError

#: Formato del reloj de pared que guardan el plan y el recibo. Sin zona: la
#: zona viaja aparte, en su campo, para que se vea cual se uso.
WALL_FORMAT = "%Y-%m-%dT%H:%M:%S"


class ScheduleError(DocumentValidationError):
    """Horario imposible, ambiguo o con zona desconocida."""

    code = "invalid_schedule"


class Clock(Protocol):
    """Fuente de tiempo. Devuelve SIEMPRE un instante con zona UTC."""

    def now(self) -> datetime: ...


class SystemClock:
    """El reloj de verdad."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc).replace(microsecond=0)


@dataclass
class ManualClock:
    """Reloj de pruebas. Solo avanza cuando se le dice.

    No se usa en produccion: existe para poder comprobar ventanas vencidas,
    concesiones caducadas y cambios de hora sin esperar a que ocurran.
    """

    instant: datetime = field(
        default_factory=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc)
    )

    def __post_init__(self) -> None:
        if self.instant.tzinfo is None:
            raise ScheduleError("el reloj de pruebas necesita un instante con zona")
        self.instant = self.instant.astimezone(timezone.utc).replace(microsecond=0)

    def now(self) -> datetime:
        return self.instant

    def advance(self, seconds: float) -> datetime:
        self.instant = self.instant + timedelta(seconds=seconds)
        return self.instant

    def set(self, instant: datetime) -> datetime:
        if instant.tzinfo is None:
            raise ScheduleError("el instante necesita zona horaria explicita")
        self.instant = instant.astimezone(timezone.utc).replace(microsecond=0)
        return self.instant


def ensure_zone(name: str) -> ZoneInfo:
    """Resuelve una zona IANA. Un nombre desconocido es un error, no UTC."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise ScheduleError(
            f"zona horaria desconocida: {name!r}. Usa un identificador IANA "
            "(p. ej. 'Europe/Madrid'); no se sustituye por UTC en silencio.",
            details={"timezone": name},
        ) from exc


def parse_wall(text: str) -> datetime:
    """Lee la hora de pared del operador. Sin zona: la zona va aparte."""
    try:
        parsed = datetime.strptime(text.strip(), WALL_FORMAT)
    except ValueError as exc:
        raise ScheduleError(
            f"hora local invalida: {text!r}. Formato esperado {WALL_FORMAT} "
            "(la zona se indica por separado).",
            details={"local_time": text},
        ) from exc
    return parsed


def is_nonexistent(wall: datetime, zone: ZoneInfo) -> bool:
    """True si esa hora de pared no ocurre (salto de primavera).

    Se comprueba yendo a UTC y volviendo: si la hora de pared cambia por el
    camino, es que no existia.
    """
    con_zona = wall.replace(tzinfo=zone)
    vuelta = con_zona.astimezone(timezone.utc).astimezone(zone)
    return vuelta.replace(tzinfo=None) != wall


def is_ambiguous(wall: datetime, zone: ZoneInfo) -> bool:
    """True si esa hora de pared ocurre dos veces (salto de otono)."""
    primera = wall.replace(tzinfo=zone, fold=0)
    segunda = wall.replace(tzinfo=zone, fold=1)
    return primera.utcoffset() != segunda.utcoffset()


def local_to_utc(wall: datetime, zone: ZoneInfo, *, fold: int | None = None) -> datetime:
    """Convierte hora de pared + zona en instante UTC, o falla explicando.

    `fold` solo es obligatorio cuando la hora esta repetida. Pedirlo siempre
    seria ruido; ignorarlo en una hora repetida seria elegir por el operador.
    """
    if wall.tzinfo is not None:
        raise ScheduleError("la hora de pared no debe llevar zona: se indica aparte")
    if is_nonexistent(wall, zone):
        raise ScheduleError(
            f"la hora local {wall.strftime(WALL_FORMAT)} no existe en "
            f"{zone.key} por el cambio de horario. Elige otra hora: no se "
            "desplaza automaticamente.",
            details={"local_time": wall.strftime(WALL_FORMAT), "timezone": zone.key},
        )
    if is_ambiguous(wall, zone):
        if fold is None:
            raise ScheduleError(
                f"la hora local {wall.strftime(WALL_FORMAT)} ocurre dos veces en "
                f"{zone.key} por el cambio de horario. Indica cual: fold=0 la "
                "primera (aun en horario de verano), fold=1 la segunda.",
                details={
                    "local_time": wall.strftime(WALL_FORMAT),
                    "timezone": zone.key,
                    "ambiguous": True,
                },
            )
        if fold not in (0, 1):
            raise ScheduleError("fold solo admite 0 o 1")
        elegido = wall.replace(tzinfo=zone, fold=fold)
    else:
        # Hora normal: `fold` es irrelevante y se normaliza a 0.
        elegido = wall.replace(tzinfo=zone, fold=0)
    return elegido.astimezone(timezone.utc).replace(microsecond=0)


def utc_to_local(instant: datetime, zone: ZoneInfo) -> datetime:
    """Instante UTC visto en la zona del operador."""
    if instant.tzinfo is None:
        raise ScheduleError("el instante necesita zona horaria explicita")
    return instant.astimezone(zone)


def effective_fold(wall: datetime, zone: ZoneInfo, fold: int | None) -> int:
    """El `fold` que se guarda: 0 salvo que la hora repetida pida la segunda."""
    if is_ambiguous(wall, zone) and fold == 1:
        return 1
    return 0


@dataclass(frozen=True)
class DueVerdict:
    """Por que una tarea programada puede o no empezar AHORA."""

    due: bool
    expired: bool
    seconds_until_due: float
    seconds_late: float
    reason: str


def evaluate_due(
    scheduled_at_utc: datetime, now: datetime, *, late_window_s: int
) -> DueVerdict:
    """Decide si una entrega NUEVA puede iniciarse.

    La ventana de retraso protege de la avalancha: tras una caida larga, lo
    vencido no se publica de golpe. Esto solo gobierna el INICIO de un envio
    nuevo; seguir una transferencia ya empezada o consultar un resultado
    pendiente no pasa por aqui.
    """
    if scheduled_at_utc.tzinfo is None or now.tzinfo is None:
        raise ScheduleError("las comparaciones de tiempo exigen zona horaria")
    delta = (now - scheduled_at_utc).total_seconds()
    if delta < 0:
        return DueVerdict(
            due=False,
            expired=False,
            seconds_until_due=-delta,
            seconds_late=0.0,
            reason=f"faltan {-delta:.0f} s para la hora autorizada",
        )
    if delta > late_window_s:
        return DueVerdict(
            due=False,
            expired=True,
            seconds_until_due=0.0,
            seconds_late=delta,
            reason=(
                f"la hora autorizada paso hace {delta:.0f} s y la ventana de "
                f"inicio era de {late_window_s} s: requiere revision del "
                "operador, no se publica de golpe"
            ),
        )
    return DueVerdict(
        due=True,
        expired=False,
        seconds_until_due=0.0,
        seconds_late=delta,
        reason=f"dentro de la ventana de inicio ({delta:.0f} s de retraso)",
    )
