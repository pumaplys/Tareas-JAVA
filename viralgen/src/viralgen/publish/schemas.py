"""Contratos del modulo 5: ``publication_plan.json`` y ``publication.json``.

Dos documentos con versiones INDEPENDIENTES entre si y de los modulos 1-4:

* **El plan** es la intencion editorial: que video, a que cuenta, con que
  texto, con que visibilidad y a que hora. Es un borrador que el operador
  puede editar antes de autorizarlo.
* **El recibo** es una exportacion del estado persistido. Documenta lo que ha
  pasado; NO es una fuente de permisos. Editarlo a mano no autoriza nada ni
  demuestra que una plataforma haya recibido nada.

Reglas heredadas de los modulos anteriores: ``extra="forbid"``, enums para los
valores cerrados, colecciones acotadas y separacion explicita entre lo MEDIDO,
lo DECIDIDO y lo OBSERVADO en remoto.

Regla propia de este modulo: **ningun campo contiene secretos**. Las URLs
firmadas, las URIs de sesion, los tokens y las cabeceras de autorizacion son
material operativo; de ellas solo se guardan referencias (clave del objeto,
caducidad, huella) que no permiten descargar ni publicar nada.
"""

from __future__ import annotations

from datetime import timezone
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from ..schemas.common import Platform
from ..schemas.document import (
    HexHash,
    HttpUrlStr,
    Identifier,
    LanguageCode,
    LongText,
    MediumText,
    ShortText,
    StrictModel,
    UtcDatetime,
    Word,
)
from ..textutil import sha256_json
from . import PLAN_SCHEMA_VERSION, PUBLISHER_VERSION, RECEIPT_SCHEMA_VERSION
from .clock import WALL_FORMAT, ensure_zone, is_ambiguous, is_nonexistent, parse_wall

Uuid = Annotated[str, StringConstraints(pattern=r"^[0-9a-f\-]{36}$")]
PathText = Annotated[
    str, StringConstraints(min_length=1, max_length=1000, strip_whitespace=True)
]
ObjectKey = Annotated[
    str, StringConstraints(min_length=1, max_length=512, strip_whitespace=True)
]
RemoteId = Annotated[
    str, StringConstraints(min_length=1, max_length=200, strip_whitespace=True)
]


# ---------------------------------------------------------------------------
# Higiene de secretos
# ---------------------------------------------------------------------------

#: Marcas que delatan material sensible copiado a un campo de texto. Se
#: comprueban en minusculas sobre el valor completo.
SECRET_MARKERS: tuple[str, ...] = (
    "x-amz-signature",
    "x-amz-credential",
    "x-amz-security-token",
    "x-goog-signature",
    "goog4-rsa-sha256",
    "access_token=",
    "refresh_token",
    "client_secret",
    "code_verifier",
    "authorization:",
    "bearer ",
    "signature=",
    "upload_id=",
    "&sig=",
    "?sig=",
)


def assert_no_secret_material(value: str, *, field_name: str) -> str:
    """Rechaza un texto que parezca llevar una URL firmada o una credencial.

    No es un detector universal: es un cortafuegos contra el error tipico de
    copiar la URL de staging o una cabecera a un campo que despues se exporta,
    se registra o se comparte.
    """
    plano = value.lower()
    for marca in SECRET_MARKERS:
        if marca in plano:
            raise ValueError(
                f"{field_name} parece contener material sensible ({marca!r}). "
                "Las URLs firmadas, las URIs de sesion y los tokens no entran "
                "en planes, recibos ni mensajes de error."
            )
    return value


def _clean(value: str | None, *, field_name: str) -> str | None:
    return None if value is None else assert_no_secret_material(value, field_name=field_name)


# ---------------------------------------------------------------------------
# Enumeraciones
# ---------------------------------------------------------------------------


class PublishMode(StrEnum):
    """Los tres modos. Elegir uno NO cambia lo que es admisible.

    * ``plan`` - comprobacion local y borrador. Sin red, OAuth, staging ni envios.
    * ``mock`` - publicador simulado completo, en su propio espacio de identidad.
      Jamas usa un cliente de red real, aunque haya credenciales en el entorno.
    * ``real`` - solo fuentes admitidas para produccion y destinos autorizados.
    """

    PLAN = "plan"
    MOCK = "mock"
    REAL = "real"

    @property
    def simulation(self) -> bool:
        return self is not PublishMode.REAL


class DestinationState(StrEnum):
    """Estado de UN destino. El resumen global se deriva de estos, no al reves."""

    DRAFT = "draft"
    BLOCKED = "blocked"
    SCHEDULED_LOCAL = "scheduled_local"
    DISPATCHING = "dispatching"
    WAITING_REMOTE = "waiting_remote"
    DELIVERED = "delivered"
    NEEDS_RECONCILIATION = "needs_reconciliation"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"
    AWAITING_MANUAL = "awaiting_manual"
    MANUALLY_REPORTED = "manually_reported"
    CANCELLED = "cancelled"


#: Estados desde los que ya no sale nada por si solo.
TERMINAL_STATES: frozenset[DestinationState] = frozenset(
    {
        DestinationState.DELIVERED,
        DestinationState.FAILED,
        DestinationState.CANCELLED,
        DestinationState.MANUALLY_REPORTED,
    }
)

#: Estados en los que PUEDE existir algo creado en remoto. Ninguno permite
#: crear otra publicacion para el mismo destino.
REMOTE_ACTIVE_STATES: frozenset[DestinationState] = frozenset(
    {
        DestinationState.DISPATCHING,
        DestinationState.WAITING_REMOTE,
        DestinationState.NEEDS_RECONCILIATION,
    }
)

#: Transiciones admitidas. Cualquier otra es un error de programacion, no un
#: caso raro: saltar de `scheduled_local` a `delivered` sin pasar por el envio
#: significaria dar por entregado algo que nadie envio.
ALLOWED_TRANSITIONS: dict[DestinationState, frozenset[DestinationState]] = {
    DestinationState.DRAFT: frozenset(
        {
            DestinationState.BLOCKED,
            DestinationState.SCHEDULED_LOCAL,
            DestinationState.AWAITING_MANUAL,
            DestinationState.CANCELLED,
            DestinationState.NEEDS_REVIEW,
        }
    ),
    DestinationState.BLOCKED: frozenset(
        {
            DestinationState.DRAFT,
            DestinationState.SCHEDULED_LOCAL,
            DestinationState.AWAITING_MANUAL,
            DestinationState.CANCELLED,
        }
    ),
    DestinationState.SCHEDULED_LOCAL: frozenset(
        {
            DestinationState.DISPATCHING,
            DestinationState.CANCELLED,
            DestinationState.NEEDS_REVIEW,
            DestinationState.BLOCKED,
        }
    ),
    DestinationState.DISPATCHING: frozenset(
        {
            DestinationState.WAITING_REMOTE,
            DestinationState.DELIVERED,
            DestinationState.NEEDS_RECONCILIATION,
            DestinationState.NEEDS_REVIEW,
            DestinationState.FAILED,
        }
    ),
    DestinationState.WAITING_REMOTE: frozenset(
        {
            DestinationState.DISPATCHING,
            DestinationState.WAITING_REMOTE,
            DestinationState.DELIVERED,
            DestinationState.NEEDS_RECONCILIATION,
            DestinationState.NEEDS_REVIEW,
            DestinationState.FAILED,
        }
    ),
    DestinationState.NEEDS_RECONCILIATION: frozenset(
        {
            DestinationState.DELIVERED,
            DestinationState.NEEDS_REVIEW,
            DestinationState.FAILED,
            DestinationState.WAITING_REMOTE,
        }
    ),
    DestinationState.NEEDS_REVIEW: frozenset(
        {
            DestinationState.SCHEDULED_LOCAL,
            DestinationState.NEEDS_RECONCILIATION,
            DestinationState.CANCELLED,
            DestinationState.FAILED,
            DestinationState.DELIVERED,
        }
    ),
    DestinationState.AWAITING_MANUAL: frozenset(
        {
            DestinationState.MANUALLY_REPORTED,
            DestinationState.CANCELLED,
            DestinationState.NEEDS_REVIEW,
        }
    ),
    # Terminales: no salen solos.
    DestinationState.DELIVERED: frozenset(),
    DestinationState.FAILED: frozenset({DestinationState.NEEDS_REVIEW}),
    DestinationState.CANCELLED: frozenset(),
    DestinationState.MANUALLY_REPORTED: frozenset(),
}


def transition_allowed(desde: DestinationState, hacia: DestinationState) -> bool:
    """Una transicion documentada, o no se hace."""
    if desde is hacia:
        return True
    return hacia in ALLOWED_TRANSITIONS.get(desde, frozenset())


class TransferPhase(StrEnum):
    """Fase INTERNA de la operacion. Nunca es el resultado.

    Que los bytes se hayan aceptado no significa que exista una publicacion, y
    que un contenedor este `FINISHED` no significa que se haya publicado.
    """

    NOT_STARTED = "not_started"
    SESSION_OPEN = "session_open"
    UPLOADING = "uploading"
    BYTES_ACCEPTED = "bytes_accepted"
    REMOTE_PROCESSING = "remote_processing"
    PUBLISH_REQUESTED = "publish_requested"
    VERIFIED = "verified"


class Visibility(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"
    UNLISTED = "unlisted"


class AudienceDecision(StrEnum):
    """Audiencia declarada. `undecided` bloquea el envio real a proposito."""

    MADE_FOR_KIDS = "made_for_kids"
    NOT_MADE_FOR_KIDS = "not_made_for_kids"
    UNDECIDED = "undecided"


class SyntheticDisclosure(StrEnum):
    """Divulgacion de contenido sintetico realista.

    Es una decision DISTINTA de `simulation`: un video real de produccion puede
    contener medios generados con IA, y un preview simulado puede no contener
    nada realista. No se deriva una de la otra.
    """

    NOT_REVIEWED = "not_reviewed"
    NO_REALISTIC_SYNTHETIC_MEDIA = "no_realistic_synthetic_media"
    CONTAINS_REALISTIC_SYNTHETIC_MEDIA = "contains_realistic_synthetic_media"


class TextSource(StrEnum):
    """De donde salio el texto final. Nunca de un LLM en este modulo."""

    SCRIPT_PUBLISHING_PLAN = "script_publishing_plan"
    OPERATOR_EDITED = "operator_edited"


class ChecklistState(StrEnum):
    PENDING = "pending"
    RESOLVED_BY_OPERATOR = "resolved_by_operator"
    NOT_APPLICABLE = "not_applicable"


class EvidenceSource(StrEnum):
    """Origen de una evidencia. Se conserva siempre."""

    API_QUERY = "api_query"
    OPERATOR_REPORTED = "operator_reported"
    SIMULATED = "simulated"
    LOCAL_PACKAGE = "local_package"


class ErrorClass(StrEnum):
    """Familias de fallo remoto, que exigen respuestas distintas."""

    AUTH = "auth"
    """401 y similares: PUEDE permitir renovar la credencial y reintentar."""

    PERMISSION = "permission"
    """Permiso ausente, invalid_grant, app sin revision: accion humana."""

    QUOTA = "quota"
    INVALID_PAYLOAD = "invalid_payload"
    TRANSIENT = "transient"

    AMBIGUOUS = "ambiguous"
    """La operacion pudo completarse. Nunca se reintenta a ciegas."""

    LOCAL = "local"


class OverallOutcome(StrEnum):
    """Resumen del trabajo, CALCULADO a partir de los destinos."""

    DRAFT = "draft"
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    DELIVERED = "delivered"
    PARTIAL = "partial"
    NEEDS_ATTENTION = "needs_attention"
    FAILED = "failed"
    CANCELLED = "cancelled"


#: Limites de texto del PRODUCTO, no de las plataformas. Ninguna cifra de aqui
#: esta contrastada con la documentacion de YouTube o Meta: son topes
#: conservadores para no enviar payloads absurdos. Superarlos marca
#: `needs_review`; el texto NO se recorta en silencio.
LOCAL_TEXT_LIMITS: dict[str, int] = {
    "title_chars": 100,
    "description_chars": 2200,
    "tags": 30,
    "hashtags": 12,
}


# ---------------------------------------------------------------------------
# Origen: los cuatro documentos y el MP4
# ---------------------------------------------------------------------------


class DocumentRef(StrictModel):
    """Un documento de origen, por sus BYTES."""

    path: PathText
    sha256: HexHash
    size_bytes: int = Field(ge=1)
    schema_version: ShortText
    simulation: bool


class VideoRef(StrictModel):
    """El MP4 autorizado. Se resuelve desde el manifiesto, no se adivina."""

    path: PathText
    sha256: HexHash
    size_bytes: int = Field(ge=1)
    container_duration_s: float | None = None
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    fps: float = Field(gt=0)
    verified_at: UtcDatetime
    verification: Literal["bytes_rehashed"] = Field(
        default="bytes_rehashed",
        description="El hash se recalculo leyendo el archivo, no se copio del JSON.",
    )


class SourceBundle(StrictModel):
    """Identidad y procedencia del paquete que se va a publicar."""

    job_id: Uuid
    render_run_id: Uuid
    voice_run_id: Uuid
    media_run_id: Uuid
    channel: Identifier
    profile_id: Identifier
    language: LanguageCode
    render_mode: Identifier
    render_simulation: bool
    script: DocumentRef
    voice: DocumentRef
    media: DocumentRef
    render: DocumentRef
    video: VideoRef
    admissible_for_publisher_declared: bool = Field(
        description="Lo que DECIA el manifiesto de render. Es informativo."
    )
    admissible_for_publisher_recomputed: bool = Field(
        description=(
            "Lo que se acaba de comprobar sobre los archivos reales. Es el "
            "unico valor con efecto: el booleano guardado no autoriza nada."
        )
    )

    @property
    def declaration_matches(self) -> bool:
        return (
            self.admissible_for_publisher_declared
            == self.admissible_for_publisher_recomputed
        )


# ---------------------------------------------------------------------------
# Destino: cuenta, texto, horario y opciones
# ---------------------------------------------------------------------------


class AccountRef(StrictModel):
    """La cuenta PREVISTA. El ID observado vive en el recibo, no aqui."""

    platform: Platform
    alias: Identifier = Field(description="Nombre local de la cuenta en la configuracion.")
    expected_account_id: ShortText | None = Field(
        default=None,
        description=(
            "ID exacto que debe responder la plataforma (canal de YouTube, "
            "IG User ID). Sin el, un envio real no se autoriza: publicar en la "
            "cuenta equivocada no se deshace."
        ),
    )
    account_id_kind: Identifier = Field(
        description="'youtube_channel_id', 'instagram_user_id' o 'manual_handle'."
    )


class ScheduleSpec(StrictModel):
    """Instante UTC + zona del operador + hora de pared, los tres a la vez.

    Guardar solo el UTC perderia la intencion ("las 18:30 de Madrid"); guardar
    solo la hora local seria ambiguo dos veces al ano. El validador rehace la
    conversion: un recibo editado a mano con horas incoherentes no pasa.
    """

    scheduled_at_utc: UtcDatetime
    timezone: ShortText = Field(description="Zona IANA, p. ej. 'Europe/Madrid'.")
    local_time: ShortText = Field(description=f"Hora de pared, formato {WALL_FORMAT}.")
    fold: int = Field(
        default=0,
        ge=0,
        le=1,
        description=(
            "Desambigua una hora repetida por el cambio de horario: 0 la "
            "primera vez que ocurre, 1 la segunda. Irrelevante el resto del ano."
        ),
    )
    late_start_window_s: int = Field(
        ge=0,
        le=86_400,
        description=(
            "Margen para INICIAR un envio nuevo tras la hora prevista. Pasado "
            "ese margen la tarea pide revision: no se publica lo acumulado de golpe."
        ),
    )
    note: Literal[
        "La hora autorizada es cuando EMPIEZA la entrega local. No es una "
        "promesa de visibilidad publica en ese segundo."
    ] = (
        "La hora autorizada es cuando EMPIEZA la entrega local. No es una "
        "promesa de visibilidad publica en ese segundo."
    )

    @model_validator(mode="after")
    def _coherent(self) -> ScheduleSpec:
        zona = ensure_zone(self.timezone)
        pared = parse_wall(self.local_time)
        if is_nonexistent(pared, zona):
            raise ValueError(
                f"la hora local {self.local_time} no existe en {self.timezone} "
                "por el cambio de horario"
            )
        if is_ambiguous(pared, zona):
            esperado = pared.replace(tzinfo=zona, fold=self.fold)
        else:
            esperado = pared.replace(tzinfo=zona, fold=0)
        esperado_utc = esperado.astimezone(timezone.utc).replace(microsecond=0)
        if esperado_utc != self.scheduled_at_utc:
            raise ValueError(
                f"el instante UTC ({self.scheduled_at_utc.isoformat()}) no "
                f"corresponde a {self.local_time} en {self.timezone} "
                f"(fold={self.fold}): daria {esperado_utc.isoformat()}"
            )
        return self


class DestinationOptions(StrictModel):
    """Opciones aplicables al destino. Las que no apliquen van en null."""

    notify_subscribers: bool | None = Field(
        default=None, description="YouTube. Se envia explicito, nunca por omision."
    )
    share_to_feed: bool | None = Field(default=None, description="Instagram Reels.")
    requires_staging: bool = Field(
        default=False,
        description=(
            "El destino necesita que el MP4 sea descargable por la plataforma. "
            "Subir al staging esta cubierto por la misma autorizacion."
        ),
    )
    manual_delivery: bool = Field(
        default=False,
        description="La entrega la hace una persona con las herramientas oficiales.",
    )


class DestinationMetadata(StrictModel):
    """Texto final del destino. Sale del guion o del operador; de nadie mas.

    Este modulo NO genera contenido: no resume, no anade ganchos, no inventa
    hashtags ni promesas. Si falta un dato obligatorio, el destino queda en
    revision y el operador lo completa.
    """

    title: ShortText | None = None
    description: LongText | None = None
    tags: list[Word] = Field(default_factory=list, max_length=30)
    hashtags: list[Word] = Field(default_factory=list, max_length=12)
    language: LanguageCode
    audience: AudienceDecision
    synthetic_disclosure: SyntheticDisclosure
    text_source: TextSource
    edited_fields: list[Identifier] = Field(
        default_factory=list,
        max_length=10,
        description="Campos que el operador cambio respecto del guion.",
    )
    within_local_limits: bool = Field(
        description=(
            "Los topes de LOCAL_TEXT_LIMITS son decisiones del producto, no "
            "limites verificados de las plataformas. Superarlos no recorta nada."
        )
    )

    @field_validator("title", "description")
    @classmethod
    def _sin_secretos(cls, value: str | None) -> str | None:
        return _clean(value, field_name="el texto de publicacion")


def compose_caption(metadata: "DestinationMetadata") -> str:
    """Texto final de un destino con pie: descripcion + hashtags aprobados.

    Es una union MECANICA de dos campos que el operador ya reviso, no una
    redaccion: no se resume, no se recorta y no se anade nada que no estuviera
    en el plan. Se hace aqui, en un solo sitio, para que la vista legible y lo
    que se envia digan exactamente lo mismo.
    """
    partes = [metadata.description or ""]
    if metadata.hashtags:
        partes.append(" ".join(f"#{etiqueta}" for etiqueta in metadata.hashtags))
    return "\n\n".join(parte for parte in partes if parte).strip()


class PendingRequirement(StrictModel):
    """Algo que falta. Dice a quien bloquea y que hay que hacer."""

    code: Identifier
    message: MediumText
    blocks: Literal["real", "all"] = Field(
        description="'real': impide el envio real. 'all': impide incluso simular."
    )
    resolution: MediumText


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


class PlanDestination(StrictModel):
    """Un destino del borrador. Aun no esta en cola ni autorizado."""

    destination_id: Identifier
    platform: Platform
    account: AccountRef
    metadata: DestinationMetadata
    requested_visibility: Visibility
    schedule: ScheduleSpec
    options: DestinationOptions
    state: DestinationState = Field(
        description="Solo 'draft' o 'blocked': un plan no programa por si solo."
    )
    pending_requirements: list[PendingRequirement] = Field(
        default_factory=list, max_length=40
    )

    @field_validator("state")
    @classmethod
    def _solo_borrador(cls, value: DestinationState) -> DestinationState:
        if value not in (DestinationState.DRAFT, DestinationState.BLOCKED):
            raise ValueError(
                "un destino del plan solo puede estar en 'draft' o 'blocked': "
                "programar exige autorizacion y cola"
            )
        return value

    @model_validator(mode="after")
    def _plataforma_coherente(self) -> PlanDestination:
        if self.account.platform is not self.platform:
            raise ValueError("la cuenta pertenece a otra plataforma")
        if self.platform is Platform.TIKTOK and not self.options.manual_delivery:
            raise ValueError(
                "TikTok se entrega manualmente en este alcance: no hay Direct Post"
            )
        return self

    def intent_view(self) -> dict[str, Any]:
        """Los campos que DEFINEN la intencion autorizada.

        Fuera quedan los descriptivos (mensajes, requisitos pendientes): que se
        reescriba un aviso no invalida una autorizacion. Dentro esta todo lo
        que el encargo enumera: cuenta, video, texto, privacidad y horario.
        """
        return {
            "destination_id": self.destination_id,
            "platform": self.platform.value,
            "account_alias": self.account.alias,
            "expected_account_id": self.account.expected_account_id,
            "title": self.metadata.title,
            "description": self.metadata.description,
            "tags": list(self.metadata.tags),
            "hashtags": list(self.metadata.hashtags),
            "audience": self.metadata.audience.value,
            "synthetic_disclosure": self.metadata.synthetic_disclosure.value,
            "visibility": self.requested_visibility.value,
            "scheduled_at_utc": self.schedule.scheduled_at_utc.strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "timezone": self.schedule.timezone,
            "fold": self.schedule.fold,
            "options": {
                "notify_subscribers": self.options.notify_subscribers,
                "share_to_feed": self.options.share_to_feed,
                "requires_staging": self.options.requires_staging,
                "manual_delivery": self.options.manual_delivery,
            },
        }


class ChecklistItem(StrictModel):
    """Una casilla de la revision editorial. No se marca sola."""

    state: ChecklistState = ChecklistState.PENDING
    decided_by: ShortText | None = None
    decided_at: UtcDatetime | None = None
    note: MediumText | None = None

    @model_validator(mode="after")
    def _con_firma(self) -> ChecklistItem:
        if self.state is ChecklistState.RESOLVED_BY_OPERATOR and not self.decided_by:
            raise ValueError("una casilla resuelta indica quien la resolvio")
        return self


class ReviewChecklist(StrictModel):
    """Lo que una persona debe resolver antes de autorizar."""

    childrens_audience: ChecklistItem = Field(default_factory=ChecklistItem)
    synthetic_media_disclosure: ChecklistItem = Field(default_factory=ChecklistItem)
    claim_sources: ChecklistItem = Field(default_factory=ChecklistItem)
    media_rights: ChecklistItem = Field(default_factory=ChecklistItem)
    note: Literal[
        "verification_level=source_pack_only significa que las afirmaciones "
        "enlazan con el catalogo aportado. No es verificacion semantica de los "
        "hechos ni sustituye esta revision."
    ] = (
        "verification_level=source_pack_only significa que las afirmaciones "
        "enlazan con el catalogo aportado. No es verificacion semantica de los "
        "hechos ni sustituye esta revision."
    )

    @property
    def pending_items(self) -> list[str]:
        return [
            nombre
            for nombre in (
                "childrens_audience",
                "synthetic_media_disclosure",
                "claim_sources",
                "media_rights",
            )
            if getattr(self, nombre).state is ChecklistState.PENDING
        ]


class VerificationNotice(StrictModel):
    """Un parametro de protocolo sin contrastar con su fuente."""

    check_id: Identifier
    target: Identifier
    source: ShortText
    source_url: HttpUrlStr
    what: MediumText
    blocks_real_dispatch: bool


class VerificationSummary(StrictModel):
    """Que quedo sin verificar y a quien bloquea. Viaja con el plan."""

    reason: LongText
    checks: list[VerificationNotice] = Field(default_factory=list, max_length=60)
    blocked_targets: list[Identifier] = Field(default_factory=list, max_length=10)
    note: MediumText


class AdmissionSummary(StrictModel):
    """Los TRES veredictos, siempre juntos y siempre independientes."""

    contract_valid: bool
    admissible_for_simulation: bool
    admissible_for_real_dispatch: bool
    checks: dict[str, bool] = Field(default_factory=dict)
    real_only_checks: list[Identifier] = Field(default_factory=list, max_length=40)
    reasons: list[MediumText] = Field(default_factory=list, max_length=60)
    simulation_reasons: list[MediumText] = Field(default_factory=list, max_length=60)
    unverified: list[MediumText] = Field(default_factory=list, max_length=60)
    note: Literal[
        "Elegir un modo no cambia estos veredictos. Un plan localmente correcto "
        "puede tener comprobaciones remotas pendientes; pendientes no es aprobado."
    ] = (
        "Elegir un modo no cambia estos veredictos. Un plan localmente correcto "
        "puede tener comprobaciones remotas pendientes; pendientes no es aprobado."
    )


class PublicationPlan(StrictModel):
    """Borrador editorial versionado. No tiene efectos externos."""

    document_type: Literal["publication_plan"] = "publication_plan"
    schema_version: Literal["1.0"] = PLAN_SCHEMA_VERSION
    plan_id: Uuid
    revision: int = Field(ge=1, description="Sube cuando cambia la intencion.")
    created_at: UtcDatetime
    publisher_version: Identifier = PUBLISHER_VERSION
    mode: PublishMode
    publish_key: ShortText
    intent_fingerprint: HexHash = Field(
        description="SHA-256 de la INTENCION (ver `compute_intent_fingerprint`)."
    )
    sources: SourceBundle
    destinations: list[PlanDestination] = Field(min_length=1, max_length=3)
    review: ReviewChecklist = Field(default_factory=ReviewChecklist)
    admission: AdmissionSummary
    verification: VerificationSummary
    readable_view: list[MediumText] = Field(
        default_factory=list,
        max_length=200,
        description="Vista legible para la revision humana. Sin secretos.",
    )
    notes: list[MediumText] = Field(default_factory=list, max_length=40)

    @field_validator("mode")
    @classmethod
    def _modo_del_plan(cls, value: PublishMode) -> PublishMode:
        return value

    @model_validator(mode="after")
    def _destinos_unicos(self) -> PublicationPlan:
        ids = [destino.destination_id for destino in self.destinations]
        if len(set(ids)) != len(ids):
            raise ValueError("hay destination_id repetidos")
        parejas = [
            (destino.platform.value, destino.account.alias)
            for destino in self.destinations
        ]
        if len(set(parejas)) != len(parejas):
            raise ValueError(
                "hay dos destinos a la misma plataforma y cuenta: una "
                "republicacion deliberada queda fuera de este flujo"
            )
        return self

    def intent_view(self) -> dict[str, Any]:
        """La intencion completa: lo que la autorizacion cubre.

        `created_at`, `plan_id`, `revision`, los avisos y la vista legible NO
        entran: reexportar el mismo plan no debe invalidar una autorizacion,
        pero cambiar una cuenta, un texto, una hora o un video si.
        """
        return {
            "publisher_version": self.publisher_version,
            "publish_key": self.publish_key,
            "identity_space": "real" if self.mode is PublishMode.REAL else "mock",
            "video_sha256": self.sources.video.sha256,
            "script_sha256": self.sources.script.sha256,
            "voice_sha256": self.sources.voice.sha256,
            "media_sha256": self.sources.media.sha256,
            "render_sha256": self.sources.render.sha256,
            "destinations": [d.intent_view() for d in self.destinations],
        }

    def compute_intent_fingerprint(self) -> str:
        return sha256_json(self.intent_view())

    def fingerprint_matches(self) -> bool:
        """El fingerprint guardado se recalcula: no se cree."""
        return self.intent_fingerprint == self.compute_intent_fingerprint()


# ---------------------------------------------------------------------------
# Autorizacion del operador
# ---------------------------------------------------------------------------


class AuthorizationRecord(StrictModel):
    """Registro de que el operador de ESTA instalacion autorizo una revision.

    No es una firma criptografica ni certifica que el contenido sea veraz:
    dice quien, cuando y sobre que intencion exacta se dio el permiso.

    Queda atada a tres cosas a la vez -intencion, video y cuentas- para que
    cambiar cualquiera de ellas la invalide. Renovar un token de la misma
    cuenta o reemitir una URL temporal del mismo objeto no toca ninguna.
    """

    authorization_id: Uuid
    authorized_at: UtcDatetime
    operator_identity: ShortText = Field(
        description="Identidad LOCAL del operador (usuario del sistema o alias)."
    )
    mode: PublishMode
    intent_fingerprint: HexHash
    plan_sha256: HexHash = Field(description="Hash del archivo del plan autorizado.")
    plan_revision: int = Field(ge=1)
    video_sha256: HexHash
    account_ids: dict[str, str] = Field(
        default_factory=dict,
        description="destination_id -> ID de cuenta esperado en el momento de autorizar.",
    )
    staging_authorized: bool = Field(
        default=False,
        description=(
            "Autoriza subir ESE MP4 al staging configurado. Va incluido en la "
            "autorizacion de la publicacion, no se pide aparte."
        ),
    )
    revoked_at: UtcDatetime | None = None
    note: Literal[
        "Registro del operador de esta instalacion. No certifica la veracidad "
        "del contenido ni sustituye la revision editorial."
    ] = (
        "Registro del operador de esta instalacion. No certifica la veracidad "
        "del contenido ni sustituye la revision editorial."
    )

    @property
    def active(self) -> bool:
        return self.revoked_at is None

    def covers(self, plan: PublicationPlan) -> bool:
        """True si esta autorizacion cubre EXACTAMENTE esta intencion."""
        if not self.active or self.mode is not plan.mode:
            return False
        if self.intent_fingerprint != plan.compute_intent_fingerprint():
            return False
        if self.video_sha256 != plan.sources.video.sha256:
            return False
        esperadas = {
            destino.destination_id: (destino.account.expected_account_id or "")
            for destino in plan.destinations
        }
        return esperadas == {k: (v or "") for k, v in self.account_ids.items()}


# ---------------------------------------------------------------------------
# Recibo: estado observado
# ---------------------------------------------------------------------------


class StructuredError(StrictModel):
    """Fallo clasificado. La clase decide la respuesta, no el codigo HTTP."""

    code: Identifier
    error_class: ErrorClass
    message: MediumText
    retryable: bool
    http_status: int | None = Field(default=None, ge=100, le=599)
    occurred_at: UtcDatetime
    request_id: ShortText | None = None

    @field_validator("message")
    @classmethod
    def _sin_secretos(cls, value: str) -> str:
        return assert_no_secret_material(value, field_name="el mensaje de error")

    @model_validator(mode="after")
    def _ambiguo_no_se_reintenta(self) -> StructuredError:
        if self.error_class is ErrorClass.AMBIGUOUS and self.retryable:
            raise ValueError(
                "un resultado ambiguo no es reintentable: primero se consulta "
                "el estado remoto, nunca se repite la operacion mutante"
            )
        return self


class EvidenceRecord(StrictModel):
    """De donde sale lo que creemos saber del estado remoto."""

    source: EvidenceSource
    checked_at: UtcDatetime
    summary: MediumText
    remote_status: ShortText | None = None
    request_id: ShortText | None = None

    @field_validator("summary")
    @classmethod
    def _sin_secretos(cls, value: str) -> str:
        return assert_no_secret_material(value, field_name="la evidencia")


class StagingRef(StrictModel):
    """Referencia al objeto temporal. La URL firmada NO se guarda.

    De la URL solo queda su huella y su caducidad: sirve para saber si la que
    tiene la plataforma sigue viva y si es la misma que se emitio, sin que el
    recibo permita descargar el video a quien lo lea.
    """

    provider: Identifier = "s3_compatible"
    bucket_alias: ShortText
    object_key: ObjectKey
    object_sha256: HexHash
    hash_source: Literal["local_file"] = Field(
        default="local_file",
        description="El SHA-256 es del archivo local. Un ETag no lo demuestra.",
    )
    etag: ShortText | None = Field(
        default=None, description="Lo que devolvio el proveedor. Informativo."
    )
    size_bytes: int = Field(ge=1)
    uploaded_at: UtcDatetime
    url_issued_at: UtcDatetime | None = None
    url_expires_at: UtcDatetime | None = None
    url_sha256: HexHash | None = Field(
        default=None, description="Huella de la URL emitida. La URL no se guarda."
    )
    retain_until: UtcDatetime | None = Field(
        default=None,
        description="Antes de esta fecha no se borra aunque el estado sea terminal.",
    )
    deleted_at: UtcDatetime | None = None

    @field_validator("object_key", "bucket_alias", "etag")
    @classmethod
    def _sin_secretos(cls, value: str | None) -> str | None:
        return _clean(value, field_name="la referencia de staging")


class ManualExportRef(StrictModel):
    """Paquete preparado para publicar a mano. Exportar no es publicar."""

    package_path: PathText
    video_sha256: HexHash
    text_sha256: HexHash
    sheet_sha256: HexHash
    exported_at: UtcDatetime
    simulation: bool
    publishable_by_this_module: Literal[False] = False


class ManualReport(StrictModel):
    """Lo que el operador dice haber publicado. Sin confirmacion por API."""

    reported_url: HttpUrlStr | None = None
    reported_id: RemoteId | None = None
    reported_at: UtcDatetime
    recorded_at: UtcDatetime
    evidence_source: Literal["operator_reported"] = "operator_reported"
    note: Literal[
        "Dato aportado por una persona. No esta verificado mediante API y no "
        "se convierte en confirmacion remota."
    ] = (
        "Dato aportado por una persona. No esta verificado mediante API y no "
        "se convierte en confirmacion remota."
    )

    @model_validator(mode="after")
    def _algo_que_registrar(self) -> ManualReport:
        if not self.reported_url and not self.reported_id:
            raise ValueError("hay que aportar al menos una URL o un ID")
        return self


class BudgetUsage(StrictModel):
    """Gasto PERSISTIDO. Abrir otro proceso no lo reinicia."""

    requests_used: int = Field(default=0, ge=0)
    request_limit: int = Field(ge=1)
    bytes_transferred: int = Field(default=0, ge=0)
    attempts: int = Field(default=0, ge=0)
    attempt_limit: int = Field(ge=1)
    note: Literal[
        "Limites LOCALES del producto (incluyen OAuth, staging y adaptadores). "
        "No son las cuotas de las plataformas."
    ] = (
        "Limites LOCALES del producto (incluyen OAuth, staging y adaptadores). "
        "No son las cuotas de las plataformas."
    )

    @property
    def exhausted(self) -> bool:
        return self.requests_used >= self.request_limit


class ReceiptDestination(StrictModel):
    """Estado observado de un destino. Distingue local, enviado y visible."""

    destination_id: Identifier
    platform: Platform
    account_alias: Identifier
    expected_account_id: ShortText | None = None
    observed_account_id: ShortText | None = Field(
        default=None, description="Lo que respondio la plataforma."
    )
    state: DestinationState
    phase: TransferPhase = TransferPhase.NOT_STARTED
    simulation: bool
    requested_visibility: Visibility
    observed_visibility: Visibility | None = None
    publicly_visible: bool | None = Field(
        default=None,
        description=(
            "Solo true con evidencia de que la plataforma lo muestra publicamente. "
            "Una entrega privada puede estar entregada y no ser visible."
        ),
    )
    real_remote_id: RemoteId | None = None
    mock_remote_id: RemoteId | None = Field(
        default=None, description="Identificador del espacio SIMULADO. No existe en remoto."
    )
    remote_refs: dict[str, str] = Field(
        default_factory=dict,
        description="IDs intermedios no secretos (p. ej. container_id).",
    )
    permalink: HttpUrlStr | None = None
    scheduled_at_utc: UtcDatetime | None = None
    timezone: ShortText | None = None
    dispatch_started_at: UtcDatetime | None = None
    delivered_at: UtcDatetime | None = None
    next_attempt_at: UtcDatetime | None = None
    next_poll_at: UtcDatetime | None = None
    budget: BudgetUsage
    last_error: StructuredError | None = None
    last_evidence: EvidenceRecord | None = None
    staging: StagingRef | None = None
    manual_export: ManualExportRef | None = None
    manual_report: ManualReport | None = None
    cancel_requested_at: UtcDatetime | None = None
    cancel_note: MediumText | None = None

    @field_validator("remote_refs")
    @classmethod
    def _refs_limpias(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 10:
            raise ValueError("demasiadas referencias remotas")
        for clave, dato in value.items():
            assert_no_secret_material(clave, field_name="la clave de referencia remota")
            assert_no_secret_material(dato, field_name="la referencia remota")
            if len(dato) > 200:
                raise ValueError(f"la referencia {clave} es demasiado larga")
        return value

    @model_validator(mode="after")
    def _coherencia(self) -> ReceiptDestination:
        if self.simulation and self.real_remote_id is not None:
            raise ValueError(
                "una simulacion no tiene ID remoto real: usa mock_remote_id"
            )
        if not self.simulation and self.mock_remote_id is not None:
            raise ValueError("un envio real no lleva identificadores simulados")
        if self.state is DestinationState.DELIVERED:
            if self.last_evidence is None:
                raise ValueError("delivered exige evidencia de la consulta remota")
            if self.observed_visibility is None or self.publicly_visible is None:
                raise ValueError(
                    "delivered exige visibilidad observada y si es publica o no"
                )
            if self.simulation and self.last_evidence.source is not EvidenceSource.SIMULATED:
                raise ValueError("una entrega simulada declara evidencia simulada")
            if not self.simulation and self.last_evidence.source is EvidenceSource.SIMULATED:
                raise ValueError("una entrega real no se acredita con evidencia simulada")
        if self.state is DestinationState.AWAITING_MANUAL and self.manual_export is None:
            raise ValueError("awaiting_manual exige el paquete exportado")
        if self.state is DestinationState.MANUALLY_REPORTED:
            if self.manual_report is None:
                raise ValueError("manually_reported exige el dato aportado por el operador")
            if self.real_remote_id is not None:
                raise ValueError(
                    "un reporte manual no adjudica un ID remoto verificado: "
                    "queda en manual_report con su origen humano"
                )
        if self.publicly_visible and self.observed_visibility is not Visibility.PUBLIC:
            raise ValueError(
                "publicly_visible=true exige observed_visibility=public"
            )
        return self


class ReceiptSummary(StrictModel):
    """Resumen CALCULADO. Un exito parcial se dice, no se redondea."""

    overall: OverallOutcome
    total: int = Field(ge=1)
    delivered: int = Field(ge=0)
    pending: int = Field(ge=0)
    needs_attention: int = Field(ge=0)
    failed: int = Field(ge=0)
    cancelled: int = Field(ge=0)
    publicly_visible: int = Field(ge=0)
    note: Literal[
        "El codigo de salida indica el resultado del comando. Solo el estado y "
        "la evidencia dicen si hubo una entrega real."
    ] = (
        "El codigo de salida indica el resultado del comando. Solo el estado y "
        "la evidencia dicen si hubo una entrega real."
    )


def summarize(destinos: list[ReceiptDestination]) -> ReceiptSummary:
    """Deriva el resumen de los estados individuales.

    Un YouTube entregado con un Instagram fallido es `partial`: ni exito ni
    fallo. Y un exito en un destino nunca provoca otra subida en el que ya
    habia salido bien.
    """
    if not destinos:
        raise ValueError("un recibo sin destinos no se resume")
    estados = [destino.state for destino in destinos]
    entregados = sum(
        1
        for estado in estados
        if estado in (DestinationState.DELIVERED, DestinationState.MANUALLY_REPORTED)
    )
    fallidos = sum(1 for estado in estados if estado is DestinationState.FAILED)
    cancelados = sum(1 for estado in estados if estado is DestinationState.CANCELLED)
    atencion = sum(
        1
        for estado in estados
        if estado
        in (
            DestinationState.NEEDS_REVIEW,
            DestinationState.NEEDS_RECONCILIATION,
            DestinationState.BLOCKED,
        )
    )
    en_curso = sum(
        1
        for estado in estados
        if estado
        in (
            DestinationState.DISPATCHING,
            DestinationState.WAITING_REMOTE,
        )
    )
    programados = sum(1 for estado in estados if estado is DestinationState.SCHEDULED_LOCAL)
    borradores = sum(
        1
        for estado in estados
        if estado in (DestinationState.DRAFT, DestinationState.AWAITING_MANUAL)
    )
    total = len(estados)

    if atencion:
        overall = OverallOutcome.NEEDS_ATTENTION
    elif en_curso:
        overall = OverallOutcome.IN_PROGRESS
    elif entregados and (fallidos or cancelados or programados or borradores):
        overall = OverallOutcome.PARTIAL
    elif entregados == total:
        overall = OverallOutcome.DELIVERED
    elif fallidos:
        overall = OverallOutcome.FAILED
    elif cancelados == total:
        overall = OverallOutcome.CANCELLED
    elif programados:
        overall = OverallOutcome.SCHEDULED
    else:
        overall = OverallOutcome.DRAFT

    return ReceiptSummary(
        overall=overall,
        total=total,
        delivered=entregados,
        pending=en_curso + programados + borradores,
        needs_attention=atencion,
        failed=fallidos,
        cancelled=cancelados,
        publicly_visible=sum(1 for destino in destinos if destino.publicly_visible),
    )


class PublicationReceipt(StrictModel):
    """``publication.json``: exportacion del estado persistido.

    No concede permisos. Editarlo no autoriza nada, no cambia lo que hay en
    SQLite y no demuestra que ninguna plataforma haya recibido nada: la
    autorizacion se comprueba contra el registro, y el estado remoto contra la
    plataforma.
    """

    document_type: Literal["publication_receipt"] = "publication_receipt"
    schema_version: Literal["1.0"] = RECEIPT_SCHEMA_VERSION
    receipt_id: Uuid
    publish_key: ShortText
    intent_fingerprint: HexHash
    plan_revision: int = Field(ge=1)
    plan_sha256: HexHash
    publisher_version: Identifier = PUBLISHER_VERSION
    mode: PublishMode
    simulation: bool
    created_at: UtcDatetime
    updated_at: UtcDatetime
    sources: SourceBundle
    authorization: AuthorizationRecord | None = None
    destinations: list[ReceiptDestination] = Field(min_length=1, max_length=3)
    summary: ReceiptSummary
    note: Literal[
        "Exportacion del estado persistido. No es una fuente de permisos ni "
        "prueba de exito remoto por si misma."
    ] = (
        "Exportacion del estado persistido. No es una fuente de permisos ni "
        "prueba de exito remoto por si misma."
    )

    @model_validator(mode="after")
    def _coherencia(self) -> PublicationReceipt:
        if self.simulation != self.mode.simulation:
            raise ValueError(
                f"simulation={self.simulation} no corresponde al modo {self.mode.value}"
            )
        for destino in self.destinations:
            if destino.simulation != self.simulation:
                raise ValueError(
                    f"el destino {destino.destination_id} mezcla espacios de "
                    "identidad: simulado y real no conviven en un recibo"
                )
        esperado = summarize(list(self.destinations))
        if esperado.overall is not self.summary.overall:
            raise ValueError(
                f"el resumen dice {self.summary.overall.value} y los estados dan "
                f"{esperado.overall.value}"
            )
        return self


def schema_documents() -> dict[str, dict]:
    """Los dos contratos como JSON Schema, para `publish schema`."""
    return {
        "publication_plan": PublicationPlan.model_json_schema(),
        "publication_receipt": PublicationReceipt.model_json_schema(),
    }
