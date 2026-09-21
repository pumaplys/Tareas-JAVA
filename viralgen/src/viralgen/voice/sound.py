"""Planificacion de efectos y musica opcionales.

Este modulo NO mezcla nada: solo propone posiciones. La mezcla definitiva es
del modulo 4.

Reglas:

* Los efectos y la musica estan DESACTIVADOS por defecto hasta que existan
  assets utilizables.
* Las descripciones del guion (`audio.sfx_description`) son texto libre: no se
  interpretan como rutas ni como URLs. Solo se usan los assets declarados en el
  catalogo local y el mapeo explicito.
* `scene_start` es el comienzo real de la escena; `scene_end` es el final del
  clip hablado, ANTES de su pausa anadida.
* Una indicacion opcional sin asset produce un aviso informativo
  (`blocking=False`): el montaje puede continuar solo con narracion.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..diskutil import sha256_file
from ..schemas.document import Scene, ScriptDocument
from .audio import read_wav_info
from .profiles import SoundAssetsFile, SoundKind, VoiceProfile
from .schemas import CueType, SoundAssetRef, SoundCue, VoiceIssue, IssueSeverity


@dataclass
class SceneTiming:
    """Tiempos medidos de una escena, en segundos."""

    scene_id: str
    start_s: float
    clip_end_s: float
    end_s: float


@dataclass
class SoundPlan:
    cues: list[SoundCue]
    assets: list[SoundAssetRef]
    issues: list[VoiceIssue]
    files_to_copy: list[tuple[str, Path]]


def plan_sound(
    document: ScriptDocument,
    timings: dict[str, SceneTiming],
    *,
    catalog: SoundAssetsFile,
    voice_profile: VoiceProfile,
    enable_sfx: bool,
    enable_music: bool,
    master_duration_s: float,
) -> SoundPlan:
    """Propone las posiciones de efectos y musica que caben en el video."""
    cues: list[SoundCue] = []
    usados: dict[str, SoundAssetRef] = {}
    issues: list[VoiceIssue] = []
    copiar: list[tuple[str, Path]] = []
    por_id = catalog.by_id()

    #: Mapeo explicito del catalogo, escena -> asset.
    mapeo = {(cue.scene_id, cue.cue_type): cue for cue in catalog.scene_cues}

    def registrar(asset_id: str) -> tuple[SoundAssetRef | None, float | None]:
        asset = por_id.get(asset_id)
        if asset is None:
            return None, None
        ruta = Path(asset.path)
        if not ruta.is_file():
            issues.append(
                VoiceIssue(
                    code="asset_no_encontrado",
                    message=f"el asset {asset_id} no esta en {asset.path}",
                    severity=IssueSeverity.WARNING,
                    blocking=False,
                )
            )
            return None, None
        real = sha256_file(ruta)
        if real != asset.sha256:
            issues.append(
                VoiceIssue(
                    code="asset_hash_distinto",
                    message=f"el asset {asset_id} no coincide con su hash declarado",
                    severity=IssueSeverity.WARNING,
                    blocking=False,
                )
            )
            return None, None
        try:
            duracion = read_wav_info(ruta).duration_s
        except Exception:
            issues.append(
                VoiceIssue(
                    code="asset_ilegible",
                    message=f"el asset {asset_id} no es un WAV legible",
                    severity=IssueSeverity.WARNING,
                    blocking=False,
                )
            )
            return None, None
        referencia = usados.get(asset_id)
        if referencia is None:
            referencia = SoundAssetRef(
                asset_id=asset.asset_id,
                path=f"audio/assets/{ruta.name}",
                sha256=real,
                license_note=asset.license_note,
            )
            usados[asset_id] = referencia
            copiar.append((referencia.path, ruta))
        return referencia, duracion

    for escena in document.scenes:
        tiempos = timings.get(escena.scene_id)
        if tiempos is None:
            continue
        descripcion = escena.audio.sfx_description
        if not descripcion:
            continue
        if not enable_sfx:
            issues.append(
                VoiceIssue(
                    code="sfx_desactivado",
                    message=(
                        f"la escena {escena.scene_id} pide un efecto y los efectos estan "
                        "desactivados; el montaje puede seguir solo con narracion"
                    ),
                    severity=IssueSeverity.INFO,
                    blocking=False,
                    scene_id=escena.scene_id,
                )
            )
            continue

        config = mapeo.get((escena.scene_id, SoundKind.SFX))
        asset_id = config.asset_id if config else voice_profile.sfx_asset_id
        if not asset_id:
            issues.append(
                VoiceIssue(
                    code="sfx_sin_asset",
                    message=(
                        f"la escena {escena.scene_id} pide un efecto pero no hay asset "
                        "asignado; se omite"
                    ),
                    severity=IssueSeverity.INFO,
                    blocking=False,
                    scene_id=escena.scene_id,
                )
            )
            continue

        referencia, duracion = registrar(asset_id)
        if referencia is None or duracion is None:
            continue
        inicio = _cue_start(escena, tiempos)
        if inicio + duracion > master_duration_s + 1e-6:
            issues.append(
                VoiceIssue(
                    code="sfx_no_cabe",
                    message=(
                        f"el efecto {asset_id} no cabe en la escena {escena.scene_id}: "
                        f"terminaria despues del final del video"
                    ),
                    severity=IssueSeverity.WARNING,
                    blocking=False,
                    scene_id=escena.scene_id,
                )
            )
            continue
        cues.append(
            SoundCue(
                cue_type=CueType.SFX,
                scene_id=escena.scene_id,
                asset_id=referencia.asset_id,
                start_s=round(inicio, 6),
                end_s=round(inicio + duracion, 6),
                gain_db=config.gain_db if config else -14.0,
            )
        )

    musica_id = voice_profile.music_asset_id
    if enable_music and musica_id:
        referencia, duracion = registrar(musica_id)
        if referencia is not None and duracion is not None:
            cues.append(
                SoundCue(
                    cue_type=CueType.MUSIC,
                    scene_id=None,
                    asset_id=referencia.asset_id,
                    start_s=0.0,
                    end_s=round(min(duracion, master_duration_s), 6),
                    gain_db=-22.0,
                )
            )
    elif enable_music and not musica_id:
        issues.append(
            VoiceIssue(
                code="musica_sin_asset",
                message="la musica esta activada pero el perfil no tiene music_asset_id",
                severity=IssueSeverity.INFO,
                blocking=False,
            )
        )

    return SoundPlan(
        cues=cues, assets=list(usados.values()), issues=issues, files_to_copy=copiar
    )


def _cue_start(scene: Scene, timings: SceneTiming) -> float:
    """`scene_start` = comienzo real; `scene_end` = fin del habla, sin la pausa.

    Un efecto al inicio acompana al gancho sin retrasarlo: empieza a la vez que
    la voz, no antes, y el modulo 4 lo mezclara por debajo de la narracion.
    """
    if scene.audio.sfx_cue.value == "scene_start":
        return timings.start_s
    return timings.clip_end_s
