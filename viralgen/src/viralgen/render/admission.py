"""Validacion y admision del modulo 4: la puerta de entrada del modulo 5.

Validador LOCAL sin red. Recibe los CUATRO documentos por rutas explicitas
—guion, voz, medios y manifiesto de render— y revalida desde los archivos
reales. Puede medir y decodificar a salida nula, pero no modifica ni un byte
ni toca SQLite.

Tres veredictos INDEPENDIENTES, nunca uno solo:

* ``contract_valid``            - los cuatro documentos cumplen su esquema, sus
  vinculos y hashes cuadran, las rutas quedan dentro del paquete y el MP4
  existe, decodifica y coincide con lo declarado. No mira el origen.
* ``admissible_for_preview``    - ademas, todas las invariantes tecnicas:
  fotogramas exactos, reloj, audio completo, subtitulos coherentes. Ignora
  UNICAMENTE el origen.
* ``admissible_for_publisher``  - lo anterior Y `render_mode=production`,
  `simulation=false` y origen real en guion, voz y medios. Significa que el
  modulo 5 PUEDE RECIBIR el archivo; no certifica monetizacion, calidad
  editorial ni permiso para publicarlo.

`--allow-simulation` solo elige el modo del informe y su codigo de salida:
nunca cambia `checks` ni habilita al publicador. Una salida preview jamas llega
al publicador automaticamente, aunque todas sus fuentes fueran reales.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..diskutil import sha256_file
from ..media.admission import check_media_admission
from ..media.schemas import MediaManifest
from ..schemas.document import ScriptDocument
from ..voice.schemas import VoiceManifest
from .audio import AAC_FRAME_SAMPLES
from .captions import PLAY_RES_X, PLAY_RES_Y
from .ffmpeg import ProcessRunner, probe_capabilities
from .probe import (
    analyze_cfr,
    decode_audio_pcm_samples,
    decode_check,
    probe_file,
    read_video_pts,
)
from .schemas import RenderManifest, RenderMode, RenderStatus
from .video import VIDEO_TIMESCALE

#: Comprobaciones que solo miran el ORIGEN. Son las unicas que preview ignora.
ORIGIN_CHECKS: frozenset[str] = frozenset(
    {"medios_reales", "guion_real", "voz_real", "modo_produccion", "render_real"}
)

#: Margen al comparar duraciones derivadas de fotogramas.
TIME_EPSILON_S = 0.002


@dataclass
class RenderAdmissionReport:
    checks: dict[str, bool] = field(default_factory=dict)
    failures: list[tuple[str, str]] = field(default_factory=list)
    contract_valid: bool = False
    unverified: list[str] = field(default_factory=list)
    measured: dict[str, Any] = field(default_factory=dict)

    @property
    def admissible_for_publisher(self) -> bool:
        return self.contract_valid and all(self.checks.values())

    @property
    def admissible_for_preview(self) -> bool:
        return self.contract_valid and all(
            ok for nombre, ok in self.checks.items() if nombre not in ORIGIN_CHECKS
        )

    @property
    def reasons(self) -> list[str]:
        return [motivo for _n, motivo in self.failures]

    @property
    def preview_reasons(self) -> list[str]:
        return [motivo for nombre, motivo in self.failures if nombre not in ORIGIN_CHECKS]

    def to_dict(self) -> dict:
        return {
            "contract_valid": self.contract_valid,
            "admissible_for_preview": self.admissible_for_preview,
            "admissible_for_publisher": self.admissible_for_publisher,
            "checks": dict(self.checks),
            "origin_checks": sorted(ORIGIN_CHECKS),
            "reasons": self.reasons,
            "preview_reasons": self.preview_reasons,
            "unverified_checks": list(self.unverified),
            "measured": dict(self.measured),
            "note": (
                "admissible_for_publisher significa que el modulo 5 puede RECIBIR "
                "el archivo. No certifica monetizacion, calidad editorial ni "
                "permiso para publicarlo."
            ),
        }


def _inside(base: Path, relativa: str) -> Path | None:
    """Resuelve una ruta del paquete rechazando escapes, tambien por symlink."""
    candidato = base / relativa
    try:
        resuelto = candidato.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    base_resuelta = base.resolve()
    if base_resuelta != resuelto and base_resuelta not in resuelto.parents:
        return None
    return resuelto


def check_render_admission(
    *,
    script_path: Path,
    voice_path: Path,
    media_path: Path,
    manifest_path: Path,
    settings: Any,
) -> RenderAdmissionReport:
    """Audita el cuarteto (guion, voz, medios, render) desde los archivos."""
    informe = RenderAdmissionReport()

    def registrar(nombre: str, ok: bool, motivo: str = "") -> bool:
        informe.checks[nombre] = ok
        if not ok:
            informe.failures.append((nombre, motivo))
        return ok

    # --- Documentos ---------------------------------------------------------
    try:
        document = ScriptDocument.model_validate(
            json.loads(script_path.read_text(encoding="utf-8"))
        )
    except Exception as exc:
        registrar("guion_valido", False, f"el guion no se puede leer: {str(exc)[:200]}")
        return informe
    registrar("guion_valido", True)

    try:
        voz = VoiceManifest.model_validate(json.loads(voice_path.read_text(encoding="utf-8")))
    except Exception as exc:
        registrar("voz_valida", False, f"la voz no se puede leer: {str(exc)[:200]}")
        return informe
    registrar("voz_valida", True)

    try:
        medios = MediaManifest.model_validate(
            json.loads(media_path.read_text(encoding="utf-8"))
        )
    except Exception as exc:
        registrar("medios_validos", False, f"los medios no se leen: {str(exc)[:200]}")
        return informe
    registrar("medios_validos", True)

    try:
        render = RenderManifest.model_validate(
            json.loads(manifest_path.read_text(encoding="utf-8"))
        )
    except Exception as exc:
        registrar(
            "manifiesto_valido", False,
            f"el manifiesto de render no cumple su contrato: {str(exc)[:200]}",
        )
        return informe
    registrar("manifiesto_valido", True)

    # --- La cadena anterior se revalida ENTERA -----------------------------
    # No se confia en ningun booleano guardado, y la admision de voz no se
    # omite: va dentro de la de medios.
    medios_informe = check_media_admission(
        script_path=script_path,
        voice_path=voice_path,
        manifest_path=media_path,
        settings=settings,
    )
    registrar(
        "cadena_anterior_consistente",
        medios_informe.admissible_for_preview,
        "guion/voz/medios: " + "; ".join(medios_informe.preview_reasons),
    )
    registrar(
        "guion_real",
        document.simulation is False and render.sources.script_simulation is False,
        "el guion de origen es simulado",
    )
    registrar(
        "voz_real",
        voz.simulation is False and render.sources.voice_simulation is False,
        "la voz de origen es simulada",
    )
    registrar(
        "medios_reales",
        medios.simulation is False and render.sources.media_simulation is False,
        "los medios de origen son simulados",
    )
    registrar(
        "render_real",
        render.simulation is False,
        "simulation=true en el render: una simulacion nunca llega al publicador",
    )
    registrar(
        "modo_produccion",
        render.render_mode is RenderMode.PRODUCTION,
        "render_mode=preview: una salida preview no se entrega al publicador "
        "aunque sus fuentes fueran reales",
    )

    # --- Vinculos -----------------------------------------------------------
    registrar(
        "mismo_job",
        render.job_id == document.job_id == voz.job_id == medios.job_id,
        "el manifiesto de render apunta a otro job_id",
    )
    registrar(
        "misma_voz",
        render.voice_run_id == voz.voice_run_id,
        "el manifiesto de render apunta a otra ejecucion de voz",
    )
    registrar(
        "mismos_medios",
        render.media_run_id == medios.media_run_id,
        "el manifiesto de render apunta a otra ejecucion de medios",
    )
    registrar(
        "hash_del_guion",
        render.sources.script_sha256 == sha256_file(script_path),
        "el guion ha cambiado desde que se monto",
    )
    registrar(
        "hash_de_la_voz",
        render.sources.voice_sha256 == sha256_file(voice_path),
        "la voz ha cambiado desde que se monto",
    )
    registrar(
        "hash_de_los_medios",
        render.sources.media_sha256 == sha256_file(media_path),
        "los medios han cambiado desde que se monto",
    )

    # --- Reloj --------------------------------------------------------------
    registrar(
        "reloj_coincide_con_la_voz",
        render.timeline.sample_rate_hz == voz.master.sample_rate_hz
        and render.timeline.sample_count == voz.master.sample_count,
        "el reloj del manifiesto no coincide con el maestro de voz",
    )
    problemas_reloj = _check_timeline(render, voz)
    registrar("cuantizacion_coherente", not problemas_reloj, "; ".join(problemas_reloj))

    # --- Archivos del paquete ----------------------------------------------
    base = manifest_path.parent
    ruta_video = _inside(base, render.output.path)
    if ruta_video is None:
        registrar(
            "archivos_integros", False,
            f"el video {render.output.path} no existe o queda fuera del paquete",
        )
        return informe
    ruta_ass = _inside(base, render.captions.path)
    problemas_archivo: list[str] = []
    if sha256_file(ruta_video) != render.output.sha256:
        problemas_archivo.append("el hash del video no coincide")
    if ruta_video.stat().st_size != render.output.size_bytes:
        problemas_archivo.append("el tamano del video no coincide")
    if ruta_ass is None:
        problemas_archivo.append(
            f"el archivo de subtitulos {render.captions.path} no existe o escapa"
        )
    elif sha256_file(ruta_ass) != render.captions.sha256:
        problemas_archivo.append("el hash de captions.ass no coincide")
    for muestra in render.inspection.frames:
        ruta_muestra = _inside(base, muestra.path)
        if ruta_muestra is None:
            problemas_archivo.append(f"el fotograma {muestra.path} no existe o escapa")
        elif sha256_file(ruta_muestra) != muestra.sha256:
            problemas_archivo.append(f"el hash del fotograma {muestra.path} no coincide")
    registrar("archivos_integros", not problemas_archivo, "; ".join(problemas_archivo))

    # --- Medicion del archivo ----------------------------------------------
    capacidades = probe_capabilities(settings.ffmpeg_path, settings.ffprobe_path)
    if capacidades.missing:
        # Sin herramientas NO se puede emitir un resultado tecnico positivo: se
        # dice que quedo sin verificar, no que este bien.
        informe.unverified.append(
            "medicion y decodificacion del MP4: faltan "
            + ", ".join(capacidades.missing)
        )
        registrar(
            "video_medido", False,
            "no se pudo medir el video: faltan " + ", ".join(capacidades.missing),
        )
        registrar(
            "audio_medido", False,
            "no se pudo medir el audio: faltan " + ", ".join(capacidades.missing),
        )
        informe.contract_valid = False
        return informe

    ejecutor = ProcessRunner(
        ffmpeg_path=settings.ffmpeg_path,
        ffprobe_path=settings.ffprobe_path,
        stage_timeout_s=settings.render_stage_timeout_s,
        threads=settings.render_ffmpeg_threads,
        filter_threads=settings.render_filter_threads,
    )
    reporte = probe_file(ruta_video, ffprobe_path=settings.ffprobe_path)
    informe.measured = reporte.describe()

    problemas_video = _check_video(reporte, render)
    registrar("video_medido", not problemas_video, "; ".join(problemas_video))

    # Una sola decodificacion del PCM, reutilizada por el chequeo de audio.
    try:
        pcm_samples = decode_audio_pcm_samples(
            ejecutor,
            ruta_video,
            channels=reporte.audio.channels if reporte.audio else 0,
            max_bytes=settings.render_max_duration_s * 48_000 * 2 * 2 * 2,
            timeout_s=settings.render_stage_timeout_s,
        )
    except Exception:
        pcm_samples = None
    informe.measured["audio_pcm_samples"] = pcm_samples
    problemas_audio = _check_audio(reporte, render, voz, pcm_samples)
    registrar("audio_medido", not problemas_audio, "; ".join(problemas_audio))

    # PTS y CFR: con B-frames el orden de decodificacion no es el de
    # presentacion, asi que se ordena por PTS antes de analizar.
    pts = read_video_pts(ruta_video, ffprobe_path=settings.ffprobe_path)
    paso = VIDEO_TIMESCALE // render.timeline.fps
    monotonos, cfr, saltos = analyze_cfr(pts, expected_step=paso)
    registrar(
        "pts_consistentes",
        monotonos and cfr,
        (
            "los PTS no son monotonos" if not monotonos
            else f"la cadencia no es constante: {len(saltos)} salto(s)"
        ),
    )

    decodifica, registro = decode_check(ejecutor, ruta_video, stage="probe_decode")
    registrar(
        "decodifica_entero",
        decodifica,
        f"la decodificacion completa fallo: {registro[:200]}",
    )

    # --- Subtitulos ---------------------------------------------------------
    problemas_subtitulos = _check_captions(render, voz, ruta_ass)
    registrar(
        "subtitulos_coherentes", not problemas_subtitulos, "; ".join(problemas_subtitulos)
    )

    # --- Estado -------------------------------------------------------------
    registrar(
        "render_listo",
        render.control.render_status is RenderStatus.READY,
        "render_status no es ready",
    )
    registrar(
        "sin_bloqueos",
        not any(issue.blocking for issue in render.control.issues),
        "el manifiesto conserva incidencias bloqueantes",
    )
    registrar(
        "sonoridad_conforme",
        render.audio.loudness_compliant,
        "la sonoridad medida no cumple los objetivos declarados",
    )

    informe.contract_valid = all(
        informe.checks.get(nombre, False)
        for nombre in (
            "guion_valido", "voz_valida", "medios_validos", "manifiesto_valido",
            "mismo_job", "misma_voz", "mismos_medios",
            "hash_del_guion", "hash_de_la_voz", "hash_de_los_medios",
            "archivos_integros", "decodifica_entero",
        )
    )
    return informe


def _check_timeline(render: RenderManifest, voz: VoiceManifest) -> list[str]:
    """La cuantizacion del manifiesto se recalcula, no se cree."""
    from .timeline import ceil_div, samples_to_frame

    problemas: list[str] = []
    fps = render.timeline.fps
    tasa = render.timeline.sample_rate_hz
    esperado_total = ceil_div(render.timeline.sample_count * fps, tasa)
    if render.timeline.total_frames != esperado_total:
        problemas.append(
            f"total_frames declara {render.timeline.total_frames} y el reloj da "
            f"{esperado_total}"
        )
    if abs(render.timeline.visual_duration_s - esperado_total / fps) > TIME_EPSILON_S:
        problemas.append("visual_duration_s no corresponde a total_frames/F")
    if render.timeline.quantization_excess_s >= 1.0 / fps + TIME_EPSILON_S:
        problemas.append(
            f"el exceso de cuantizacion es {render.timeline.quantization_excess_s:.6f} s "
            f"y deberia ser menor que {1.0 / fps:.6f} s"
        )

    escenas_voz = {escena.scene_id: escena for escena in voz.scenes}
    cursor = 0
    for frontera in sorted(render.timeline.scenes, key=lambda item: item.order):
        fuente = escenas_voz.get(frontera.scene_id)
        if fuente is None:
            problemas.append(f"{frontera.scene_id} no existe en la voz")
            continue
        if (frontera.start_sample, frontera.end_sample) != (
            fuente.start_sample, fuente.end_sample
        ):
            problemas.append(f"{frontera.scene_id} no usa los tiempos medidos de la voz")
        if frontera.start_frame != cursor:
            problemas.append(
                f"hueco o solapamiento de fotogramas antes de {frontera.scene_id}"
            )
        esperado_inicio = samples_to_frame(
            frontera.start_sample, fps=fps, sample_rate_hz=tasa
        )
        esperado_fin = samples_to_frame(frontera.end_sample, fps=fps, sample_rate_hz=tasa)
        if (frontera.start_frame, frontera.end_frame) != (esperado_inicio, esperado_fin):
            problemas.append(
                f"{frontera.scene_id}: las fronteras no salen de cuantizar los "
                "limites acumulados"
            )
        if frontera.frames != frontera.end_frame - frontera.start_frame:
            problemas.append(f"{frontera.scene_id}: frames no cuadra con sus fronteras")
        cursor = frontera.end_frame
    if cursor != render.timeline.total_frames:
        problemas.append(
            f"las escenas suman {cursor} fotogramas y el total es "
            f"{render.timeline.total_frames}"
        )
    return problemas


def _check_video(reporte, render: RenderManifest) -> list[str]:
    problemas: list[str] = []
    if reporte.video is None:
        return ["el archivo no tiene stream de video"]
    medido = reporte.video
    declarado = render.output.video

    if medido.codec_name != declarado.codec_name:
        problemas.append(
            f"el codec medido es {medido.codec_name} y se declaro {declarado.codec_name}"
        )
    if (medido.width, medido.height) != (declarado.width, declarado.height):
        problemas.append("las dimensiones medidas no coinciden con las declaradas")
    if (medido.width, medido.height) != (PLAY_RES_X, PLAY_RES_Y):
        problemas.append(
            f"el video mide {medido.width}x{medido.height} y el objetivo es "
            f"{PLAY_RES_X}x{PLAY_RES_Y}"
        )
    if medido.pix_fmt != "yuv420p":
        problemas.append(f"el formato de pixel es {medido.pix_fmt} y deberia ser yuv420p")
    if medido.sample_aspect_ratio not in ("1:1", ""):
        problemas.append(f"SAR {medido.sample_aspect_ratio}: los pixeles no son cuadrados")
    if medido.rotation not in (0, 360):
        problemas.append(f"el video declara rotacion {medido.rotation}")

    # La cuenta de fotogramas es EXACTA: ni uno mas ni uno menos.
    if medido.nb_read_frames is None:
        problemas.append("no se pudo contar los fotogramas del archivo")
    elif medido.nb_read_frames != render.timeline.total_frames:
        problemas.append(
            f"el archivo tiene {medido.nb_read_frames} fotogramas y el reloj exige "
            f"{render.timeline.total_frames}"
        )
    if medido.r_frame_rate != f"{render.timeline.fps}/1":
        problemas.append(
            f"la cadencia es {medido.r_frame_rate} y deberia ser "
            f"{render.timeline.fps}/1"
        )
    esperada = render.timeline.total_frames / render.timeline.fps
    if medido.duration_s is not None and abs(medido.duration_s - esperada) > 0.05:
        problemas.append(
            f"la duracion del stream de video es {medido.duration_s:.3f} s y "
            f"total_frames/F da {esperada:.3f} s"
        )
    return problemas


def _check_audio(
    reporte, render: RenderManifest, voz: VoiceManifest, pcm_samples: int | None
) -> list[str]:
    """El audio debe contener la NARRACION COMPLETA.

    Se separan tres duraciones —video, audio y contenedor— y se admite un
    margen tecnico de un frame AAC (1024 muestras) por el priming y el padding
    del codec. Ese margen NO tapa voz truncada: cubre relleno, no contenido.
    """
    problemas: list[str] = []
    if reporte.audio is None:
        return ["el archivo no tiene stream de audio"]
    medido = reporte.audio

    if medido.codec_name != "aac":
        problemas.append(f"el codec de audio es {medido.codec_name} y deberia ser aac")
    if medido.sample_rate_hz != render.audio.work_sample_rate_hz:
        problemas.append(
            f"el audio esta a {medido.sample_rate_hz} Hz y se declaro "
            f"{render.audio.work_sample_rate_hz} Hz"
        )

    esperadas = round(
        voz.master.sample_count * medido.sample_rate_hz / voz.master.sample_rate_hz
    )
    # Se DECODIFICA el PCM para contar. La duracion declarada por el contenedor
    # no vale: en un AAC real difiere de la cuenta, porque el contenedor
    # descuenta el priming y el codificador rellena el ultimo frame.
    if pcm_samples is None:
        problemas.append(
            "no se pudo contar el PCM del audio; sin esa medida no se puede "
            "afirmar que la narracion este completa"
        )
        return problemas

    deficit = pcm_samples - esperadas
    if deficit < -AAC_FRAME_SAMPLES:
        faltan_s = -deficit / medido.sample_rate_hz
        problemas.append(
            f"al audio le faltan {-deficit} muestras ({faltan_s:.3f} s) respecto "
            f"de la narracion: eso es voz truncada, no relleno del codec"
        )

    # Y lo que declara el manifiesto tiene que ser lo MEDIDO, no la duracion.
    declaradas = render.output.audio.decoded_samples
    if declaradas is None:
        problemas.append("el manifiesto no declara decoded_samples")
    elif declaradas != pcm_samples:
        problemas.append(
            f"el manifiesto declara {declaradas} muestras decodificadas y el PCM "
            f"tiene {pcm_samples}: la cuenta declarada no sale de decodificar"
        )
    if render.output.audio.decoded_samples_source != "pcm_decode":
        problemas.append(
            "decoded_samples no se declara medido por decodificacion del PCM"
        )
    return problemas


def _check_captions(
    render: RenderManifest, voz: VoiceManifest, ass_path: Path | None
) -> list[str]:
    """Cada evento debe venir de palabras fuente, caber y usar estilos conocidos."""
    problemas: list[str] = []
    if ass_path is None:
        return ["no hay archivo de subtitulos que comprobar"]

    if render.captions.word_count != len(voz.words):
        problemas.append(
            f"el manifiesto declara {render.captions.word_count} palabras y la voz "
            f"trae {len(voz.words)}"
        )

    texto = ass_path.read_text(encoding="utf-8")
    estilos = {
        linea.split(":", 1)[1].split(",")[0].strip()
        for linea in texto.splitlines()
        if linea.startswith("Style:")
    }
    eventos = [linea for linea in texto.splitlines() if linea.startswith("Dialogue:")]
    if not eventos:
        problemas.append("el archivo de subtitulos no tiene ningun evento")

    limite_cs = int(render.timeline.visual_duration_s * 100 + 0.5)
    vocabulario = {palabra.text for palabra in voz.words}
    for linea in eventos:
        campos = linea.split(",", 9)
        if len(campos) < 10:
            problemas.append("un evento de subtitulo no tiene el formato esperado")
            continue
        inicio, fin, estilo, cuerpo = campos[1], campos[2], campos[3].strip(), campos[9]
        if estilo not in estilos:
            problemas.append(f"un evento usa el estilo desconocido {estilo!r}")
        inicio_cs, fin_cs = _ass_time(inicio), _ass_time(fin)
        if inicio_cs is None or fin_cs is None:
            problemas.append("un evento tiene un tiempo ilegible")
            continue
        if fin_cs <= inicio_cs:
            problemas.append("un evento tiene duracion no positiva")
        if fin_cs > limite_cs + 100:
            problemas.append("un evento se sale del reloj del video")
        if estilo != "Preview":
            for palabra in _words_of(cuerpo):
                if palabra not in vocabulario:
                    problemas.append(
                        f"el subtitulo muestra {palabra!r}, que no es una palabra de la voz"
                    )
                    break
    return problemas


def _ass_time(texto: str) -> int | None:
    partes = texto.strip().split(":")
    if len(partes) != 3:
        return None
    try:
        horas = int(partes[0])
        minutos = int(partes[1])
        segundos, centesimas = partes[2].split(".")
        return (
            horas * 360_000 + minutos * 6_000 + int(segundos) * 100 + int(centesimas)
        )
    except (ValueError, IndexError):
        return None


def _words_of(cuerpo: str) -> list[str]:
    """Texto visible de un evento: se quitan las etiquetas y los saltos."""
    salida: list[str] = []
    profundidad = 0
    actual: list[str] = []
    indice = 0
    while indice < len(cuerpo):
        caracter = cuerpo[indice]
        if caracter == "\\" and indice + 1 < len(cuerpo):
            siguiente = cuerpo[indice + 1]
            if siguiente in "{}":
                actual.append(siguiente)   # llave escapada: texto literal
                indice += 2
                continue
            if siguiente in "Nnh":
                salida.extend("".join(actual).split())
                actual = []
                indice += 2
                continue
        if caracter == "{":
            profundidad += 1
        elif caracter == "}":
            profundidad = max(0, profundidad - 1)
        elif profundidad == 0:
            actual.append(caracter)
        indice += 1
    salida.extend("".join(actual).split())
    return [palabra for palabra in salida if palabra]
