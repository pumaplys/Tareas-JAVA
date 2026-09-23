"""TikTok: exportacion para publicar a mano, y registro de lo publicado.

Por que no hay integracion remota en esta entrega: las directrices de Direct
Post excluyen las utilidades privadas para gestionar cuentas propias o del
equipo, que es exactamente este caso. La respuesta honesta no es buscar un
atajo -cookies, navegador automatizado, endpoints privados- sino cubrir el
canal sin atribuirle una automatizacion que no existe.

Asi que este adaptador prepara un paquete: el MP4 **intacto**, el texto
editable y una ficha legible con la cuenta prevista, la hora deseada y las
decisiones editoriales. El operador publica desde las herramientas oficiales
de TikTok y despues, si quiere, registra la referencia.

Dos limites que se respetan siempre:

* **Exportar no es publicar.** El estado queda en `awaiting_manual`.
* **Lo que aporta una persona es evidencia humana.** `record-manual` guarda la
  URL o el ID con `evidence_source=operator_reported`; no se convierte en
  confirmacion por API ni se fabrica un identificador remoto.

Una futura integracion oficial seria un cambio de alcance documentado, con
revision de producto, elegibilidad y experiencia de consentimiento.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...diskutil import sha256_file
from ...schemas.common import Platform
from ..schemas import (
    DestinationState,
    EvidenceRecord,
    EvidenceSource,
    ManualExportRef,
    ManualReport,
    PublishMode,
    TransferPhase,
    compose_caption,
)
from .base import AccountCheck, DispatchContext, PublisherAdapter, StepResult

AVISO_SIMULACION = (
    "=== PAQUETE DE SIMULACION: NO PUBLICABLE ===\n"
    "Este paquete procede de una ejecucion en modo simulado. El video puede "
    "ser un preview con marca, y las fuentes no son de produccion. No lo "
    "publiques.\n"
)


def _escribir(path: Path, texto: str) -> str:
    """Escribe de forma atomica y devuelve el hash del archivo."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporal = path.with_suffix(path.suffix + ".tmp")
    temporal.write_text(texto, encoding="utf-8")
    os.replace(temporal, path)
    return sha256_file(path)


class TikTokManualAdapter(PublisherAdapter):
    platform = Platform.TIKTOK
    name = "tiktok_manual_export"
    remote = False

    def __init__(self, *, settings: Any, export_root: Path) -> None:
        self.settings = settings
        self.export_root = Path(export_root)

    # -- Cuenta ------------------------------------------------------------

    def check_account(self, ctx: DispatchContext) -> AccountCheck:
        """No hay comprobacion remota que hacer, y se dice claramente."""
        return AccountCheck(
            ok=bool(ctx.expected_account_id),
            observed_account_id=None,
            detail=(
                "TikTok se entrega a mano en este alcance: no hay API que "
                "consultar, asi que la cuenta prevista no se verifica en remoto. "
                "El operador comprueba en la app que publica donde quiere."
            ),
        )

    # -- Exportacion -------------------------------------------------------

    def package_dir(self, ctx: DispatchContext) -> Path:
        return self.export_root / ctx.publication_id / ctx.destination_id

    def start(self, ctx: DispatchContext) -> StepResult:
        """Prepara el paquete. No publica nada: deja `awaiting_manual`."""
        referencia = self.export(ctx)
        return StepResult(
            state=DestinationState.AWAITING_MANUAL,
            phase=TransferPhase.NOT_STARTED,
            manual_export=referencia,
            evidence=EvidenceRecord(
                source=EvidenceSource.LOCAL_PACKAGE,
                checked_at=ctx.now,
                summary=(
                    "paquete preparado para publicar a mano; exportar no publica "
                    "nada y no hay confirmacion remota"
                ),
            ),
            note=str(self.package_dir(ctx)),
        )

    def export(self, ctx: DispatchContext) -> ManualExportRef:
        """Copia el MP4 intacto y escribe texto y ficha."""
        destino = self.package_dir(ctx)
        destino.mkdir(parents=True, exist_ok=True)

        simulacion = ctx.mode is not PublishMode.REAL
        video = destino / "video.mp4"
        # Copia binaria: no se recodifica, no se rota, no se quita ninguna
        # marca de preview. El archivo que sale es el que entro.
        shutil.copyfile(ctx.video_path, video)
        hash_video = sha256_file(video)
        if hash_video != ctx.video_sha256:
            raise ValueError(
                "la copia del MP4 no coincide con el original: no se exporta un "
                "archivo que no sea exactamente el autorizado"
            )

        texto = compose_caption(ctx.metadata)
        hash_texto = _escribir(destino / "texto.txt", texto + "\n")
        hash_ficha = _escribir(destino / "ficha.md", self._ficha(ctx, hash_video))
        if simulacion:
            _escribir(destino / "NO_PUBLICABLE.txt", AVISO_SIMULACION)

        return ManualExportRef(
            package_path=str(destino),
            video_sha256=hash_video,
            text_sha256=hash_texto,
            sheet_sha256=hash_ficha,
            exported_at=ctx.now,
            simulation=simulacion,
        )

    def _ficha(self, ctx: DispatchContext, hash_video: str) -> str:
        """Ficha legible: lo que el operador necesita para publicar bien."""
        simulacion = ctx.mode is not PublishMode.REAL
        lineas = []
        if simulacion:
            lineas += [AVISO_SIMULACION, ""]
        lineas += [
            "# Publicacion manual en TikTok",
            "",
            "Exportar **no es publicar**. Este paquete no ha llegado a TikTok: "
            "lo publicas tu desde la app o el estudio oficiales.",
            "",
            "## Cuenta prevista",
            "",
            f"- Alias local: `{ctx.account_alias}`",
            f"- Cuenta: `{ctx.expected_account_id or '(sin declarar)'}`",
            "",
            "## Hora deseada",
            "",
            f"- {ctx.row.get('scheduled_at') or '(sin programar)'} "
            f"({ctx.row.get('timezone') or 'UTC'})",
            "- Es una INDICACION para ti, no una programacion confirmada por "
            "TikTok: este modulo no programa nada en la plataforma.",
            "",
            "## Texto",
            "",
            "El texto exacto esta en `texto.txt`, editable. Copia y pega desde "
            "ahi para no perder saltos de linea ni hashtags.",
            "",
            "## Decisiones editoriales",
            "",
            f"- Audiencia declarada: {ctx.metadata.audience.value}",
            f"- Contenido sintetico realista: "
            f"{ctx.metadata.synthetic_disclosure.value}",
            f"- Idioma: {ctx.metadata.language}",
            "- Revisa en la app las declaraciones que TikTok pida en el momento "
            "de publicar: este paquete no las rellena por ti.",
            "",
            "## Archivos y hashes",
            "",
            f"- `video.mp4` — SHA-256 `{hash_video}` ({ctx.video_size} bytes)",
            "- El video es una copia binaria exacta del render autorizado: no "
            "se ha recodificado ni se le ha quitado ninguna marca.",
            "",
            "## Despues de publicar",
            "",
            "Registra la referencia con `viralgen publish record-manual "
            f"--publish-key <clave> --destination {ctx.destination_id} "
            "--url <url>`. Quedara guardada como dato aportado por una persona "
            "(`operator_reported`), no como confirmacion de la API.",
            "",
        ]
        return "\n".join(lineas)

    # -- Sondeo ------------------------------------------------------------

    def poll(self, ctx: DispatchContext) -> StepResult:
        """No hay nada que consultar: no existe integracion remota."""
        return StepResult(
            state=DestinationState.AWAITING_MANUAL,
            phase=TransferPhase.NOT_STARTED,
            evidence=EvidenceRecord(
                source=EvidenceSource.LOCAL_PACKAGE,
                checked_at=ctx.now,
                summary=(
                    "sin integracion remota: el estado solo cambia cuando el "
                    "operador registra lo que publico"
                ),
            ),
            note="awaiting_manual",
        )

    def capabilities(self) -> dict[str, Any]:
        return {
            **super().capabilities(),
            "direct_post": False,
            "reason": (
                "Las directrices de Direct Post excluyen utilidades privadas "
                "para cuentas propias o del equipo."
            ),
            "manual_export": True,
            "schedules_remotely": False,
            "verifies_result": "no: lo aportado por el operador no se verifica",
        }


def build_manual_report(
    *,
    url: str | None,
    remote_id: str | None,
    reported_at: datetime,
    now: datetime,
) -> ManualReport:
    """Registro de lo que una persona dice haber publicado.

    No se comprueba y no se convierte en otra cosa: queda con su origen humano
    a la vista, y el destino pasa a `manually_reported`, que no es `delivered`.
    """
    return ManualReport(
        reported_url=url,
        reported_id=remote_id,
        reported_at=reported_at.astimezone(timezone.utc),
        recorded_at=now.astimezone(timezone.utc),
    )
