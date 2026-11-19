"""Prueba aislada de generacion + edicion con referencia.

PARA QUE SIRVE
--------------

Antes de gastar en las diez imagenes del cuento hay que comprobar tres cosas
que ninguna prueba de transporte simulado puede demostrar: que el modelo
responde en nuestra cuenta, que factura lo que dice y que una edicion con
referencia conserva al personaje. Esto hace exactamente **dos** peticiones:

1. `POST /v1/images/generations` - Lumi de cuerpo entero, con su descripcion y
   su vestuario tal como los fija la biblia.
2. `POST /v1/images/edits` - la MISMA Lumi cruzando un puente de madera,
   adjuntando como unica referencia los bytes de la imagen anterior.

Usa el mismo adaptador del modulo 3 (`OpenAIImageProvider`), asi que lo que se
prueba es el camino real, no una maqueta paralela.

QUE NO ES
---------

* No necesita guion, ni voz, ni YouTube, ni perfil.
* No escribe `media.json` ni toca la cola de publicacion. Sus salidas son
  **material de diagnostico**: sirven para mirarlas, no para montar un video.
  Un modulo posterior no puede recogerlas por error porque no hay manifiesto
  que las declare.
* No es una medida de calidad. Dos imagenes no demuestran continuidad visual;
  solo permiten verla con los propios ojos.

TRES MODOS, Y EL GASTO ES EXPLICITO
-----------------------------------

* **`plan`** (por defecto): no abre red, no lee la clave, no construye el
  proveedor. Imprime lo que enviaria.
* **`--simulated-transport`**: el adaptador real y el SDK real contra un
  transporte falso. Demuestra la secuencia de endpoints y comprueba que los
  bytes adjuntos son los de la primera imagen. Sin clave y sin red.
* **`--real`**: las dos peticiones de verdad. Tope duro de DOS peticiones y
  CERO reintentos, tambien en el cliente HTTP.

Si la primera imagen falla, no se intenta la segunda: editar sin referencia
seria otra prueba. Si una peticion queda con resultado incierto, se bloquea su
repeticion automatica, igual que en el pipeline.
"""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..diskutil import atomic_write_json
from ..errors import ConfigError
from ..logging_setup import get_logger
from ..storage import ProcessLock, Storage
from ..textutil import sha256_json
from .capabilities import image_capabilities, parse_size
from .imaging import inspect_image, write_bytes_as_image
from .providers.base import (
    ImageRequest,
    MediaBudget,
    MediaOutcomeUnknownError,
    MediaProviderError,
)
from .storage import MediaStorage

logger = get_logger("media.smoke")

#: Version del compositor de prompts de esta prueba. Entra en la identidad de
#: sus assets: cambiarla invalida los guardados.
SMOKE_PROMPT_VERSION = "v1"

#: Parametros fijos de la prueba. Son los del piloto, no una eleccion libre.
SMOKE_SIZE = "720x1280"
SMOKE_QUALITY = "medium"
SMOKE_FORMAT = "png"
SMOKE_CHARACTER = "lumi"
SMOKE_SERIES = "bosque_lumina"

#: Tope duro de peticiones de una invocacion. Dos: una generacion y una
#: edicion. No es configurable a proposito.
SMOKE_MAX_REQUESTS = 2

#: `job_id` con el que se persisten las reservas de esta prueba. Las separa del
#: historial de cualquier trabajo real.
SMOKE_JOB_ID = "smoke"


@dataclass(frozen=True)
class SmokeStep:
    """Una de las dos peticiones, con su identidad."""

    step_id: str
    operation: str
    prompt: str
    uses_reference: bool

    def identity(self, *, model: str, reference_sha256: str | None, simulation: bool) -> str:
        """Identidad del asset de diagnostico.

        Lleva `operation` propio (`smoke_*`), asi que nunca puede colisionar
        con un asset del pipeline aunque el prompt coincidiera.
        """
        return sha256_json(
            {
                "operation": self.operation,
                "prompt": self.prompt,
                "prompt_version": SMOKE_PROMPT_VERSION,
                "model": model,
                "size": SMOKE_SIZE,
                "quality": SMOKE_QUALITY,
                "output_format": SMOKE_FORMAT,
                "references": [reference_sha256] if reference_sha256 else [],
                "simulation": simulation,
            }
        )


def build_steps(bible: Any, character_id: str = SMOKE_CHARACTER) -> tuple[SmokeStep, SmokeStep]:
    """Los dos prompts, derivados de la biblia y de nada mas.

    No hay un LLM reescribiendo esto: la descripcion y el vestuario salen tal
    cual de la biblia visual, igual que en el pipeline.
    """
    personaje = next(
        (item for item in bible.characters if item.character_id == character_id), None
    )
    if personaje is None:
        disponibles = ", ".join(sorted(item.character_id for item in bible.characters))
        raise ConfigError(
            f"Personaje desconocido en la biblia: {character_id!r}. "
            f"Disponibles: {disponibles}.",
            details={"character_id": character_id},
        )

    estilo = bible.style_prompt.strip().rstrip(".")
    evitar = (bible.negative_prompt or "").strip().rstrip(".")
    ficha = (
        f"{personaje.name}: {personaje.description.strip()} "
        f"Wardrobe: {personaje.wardrobe.strip()}"
    )
    comun = [f"Style: {estilo}"]
    if bible.color_palette:
        comun.append("Palette: " + ", ".join(bible.color_palette))
    if evitar:
        comun.append(f"Avoid: {evitar}")
    comun.append(
        "No text, no letters, no numbers, no captions, no logos, no watermarks "
        "anywhere in the image"
    )

    generacion = ". ".join(
        [
            "Full body character illustration, single character, full figure "
            "visible from head to feet, neutral standing pose, plain uncluttered "
            "background",
            ficha,
            *comun,
        ]
    ) + "."

    edicion = ". ".join(
        [
            "Keep this exact character, wardrobe and palette unchanged",
            "New scene: the character walks across a wooden footbridge over a "
            "small forest stream, full body visible, calm daylight",
            ficha,
            *comun,
        ]
    ) + "."

    return (
        SmokeStep(
            step_id="1_generacion",
            operation="smoke_generate",
            prompt=generacion,
            uses_reference=False,
        ),
        SmokeStep(
            step_id="2_edicion",
            operation="smoke_edit",
            prompt=edicion,
            uses_reference=True,
        ),
    )


def _load_bible(settings: Any) -> Any:
    from ..profiles import get_series_bible

    return get_series_bible(SMOKE_SERIES, settings.series_bible_path)


def _model_for(settings: Any, *, simulation: bool) -> str:
    """El modelo que se usaria. En diagnostico NO se sustituye por el mock.

    La prueba existe para ejercitar el adaptador real, asi que incluso con
    transporte simulado se declara el modelo real: lo que se simula es la red,
    no el catalogo.
    """
    modelo = settings.openai_image_model or ""
    if not modelo:
        raise ConfigError(
            "Falta OPENAI_IMAGE_MODEL. Esta prueba declara el modelo "
            "explicitamente; no hay valor por defecto.",
            details={"missing": ["OPENAI_IMAGE_MODEL"]},
        )
    return modelo


def plan(settings: Any, *, character_id: str = SMOKE_CHARACTER) -> dict:
    """Lo que se enviaria. Sin red, sin clave y sin construir el proveedor."""
    bible = _load_bible(settings)
    pasos = build_steps(bible, character_id)
    modelo = _model_for(settings, simulation=False)
    capacidades = image_capabilities(modelo)

    ancho, alto = parse_size(SMOKE_SIZE)
    problemas: list[str] = []
    if SMOKE_SIZE not in capacidades.sizes:
        problemas.append(f"el modelo no declara el tamano {SMOKE_SIZE}")
    if capacidades.size_rules is not None:
        problemas.extend(capacidades.size_rules.problems(SMOKE_SIZE))
    if SMOKE_QUALITY not in capacidades.qualities:
        problemas.append(f"el modelo no declara la calidad {SMOKE_QUALITY}")
    if SMOKE_FORMAT not in capacidades.output_formats:
        problemas.append(f"el modelo no declara el formato {SMOKE_FORMAT}")
    if not capacidades.supports_edit:
        problemas.append("el modelo no declara soporte de edicion con referencias")

    return {
        "command": "media smoke",
        "mode": "plan",
        "character_id": character_id,
        "model": modelo,
        "model_verified": capacidades.verified,
        "model_retired_on": capacidades.retired_on,
        "size": SMOKE_SIZE,
        "width": ancho,
        "height": alto,
        "quality": SMOKE_QUALITY,
        "output_format": SMOKE_FORMAT,
        "images_per_request": 1,
        "input_fidelity": None,
        "response_format": None,
        "prompt_version": SMOKE_PROMPT_VERSION,
        "steps": [
            {
                "step_id": paso.step_id,
                "endpoint": (
                    "POST /v1/images/edits"
                    if paso.uses_reference
                    else "POST /v1/images/generations"
                ),
                "attaches_reference": paso.uses_reference,
                "prompt_chars": len(paso.prompt),
                "prompt": paso.prompt,
            }
            for paso in pasos
        ],
        "budget": {
            "max_requests": SMOKE_MAX_REQUESTS,
            "safe_retries": 0,
            "sdk_max_retries": 0,
        },
        "capability_problems": problemas,
        "notes": [
            "`input_fidelity` NO se envia: la guia de gpt-image-2.6 indica omitirlo.",
            "`response_format` NO se envia: es heredado y GPT Image no lo admite.",
            "Este modo no abre red ni lee la clave de API.",
            "Las salidas son de diagnostico: no se escribe media.json y nada "
            "entra en la cola de publicacion.",
        ],
    }


class _SmokeRunner:
    """Ejecuta los dos pasos con presupuesto, cache y bloqueo de incertidumbre."""

    def __init__(
        self,
        settings: Any,
        *,
        provider: Any,
        simulation: bool,
        character_id: str,
        run_id: str,
    ) -> None:
        self.settings = settings
        self.provider = provider
        self.simulation = simulation
        self.character_id = character_id
        self.run_id = run_id
        self.data_dir = settings.effective_data_dir(simulation=simulation)
        self.out_dir = self.data_dir / "smoke" / run_id

    def execute(self) -> dict:
        bible = _load_bible(self.settings)
        pasos = build_steps(bible, self.character_id)
        modelo = self.provider.model
        self.out_dir.mkdir(parents=True, exist_ok=True)

        lock_path = self.data_dir / "worker.lock"
        resultados: list[dict] = []
        with ProcessLock(lock_path), Storage(self.data_dir) as storage:
            almacen = MediaStorage(storage)
            # Migracion idempotente: la prueba puede ser lo primero que se
            # ejecute en un DATA_DIR recien creado.
            almacen.migrate()
            presupuesto = self._build_budget(almacen)

            referencia_ruta: Path | None = None
            referencia_sha: str | None = None
            for paso in pasos:
                if paso.uses_reference and referencia_ruta is None:
                    resultados.append(
                        {
                            "step_id": paso.step_id,
                            "status": "not_attempted",
                            "reason": (
                                "la primera imagen no esta disponible; editar sin "
                                "referencia seria otra prueba"
                            ),
                        }
                    )
                    break

                try:
                    resultado = self._run_step(
                        paso,
                        almacen=almacen,
                        presupuesto=presupuesto,
                        modelo=modelo,
                        referencia_ruta=referencia_ruta,
                        referencia_sha=referencia_sha,
                    )
                except MediaOutcomeUnknownError as exc:
                    resultados.append(
                        {
                            "step_id": paso.step_id,
                            "status": "outcome_unknown",
                            "reason": exc.message,
                            "note": (
                                "La peticion pudo aceptarse y consumir credito. La "
                                "repeticion automatica queda bloqueada; hay que "
                                "reconciliar a mano."
                            ),
                        }
                    )
                    break
                except MediaProviderError as exc:
                    resultados.append(
                        {
                            "step_id": paso.step_id,
                            "status": "error",
                            "error_code": exc.code,
                            "reason": exc.message,
                        }
                    )
                    break

                resultados.append(resultado)
                if paso.step_id == "1_generacion":
                    referencia_ruta = Path(resultado["path"])
                    referencia_sha = resultado["sha256"]

            resumen = {
                "command": "media smoke",
                "mode": "simulated_transport" if self.simulation else "real",
                "run_id": self.run_id,
                "character_id": self.character_id,
                "model": modelo,
                "size": SMOKE_SIZE,
                "quality": SMOKE_QUALITY,
                "output_format": SMOKE_FORMAT,
                "prompt_version": SMOKE_PROMPT_VERSION,
                "out_dir": str(self.out_dir),
                "steps": resultados,
                "requests_sent": int(
                    presupuesto.used_this_run.get("generation_attempts", 0)
                ),
                "budget": presupuesto.snapshot(),
                "diagnostic_only": True,
                "admissible_for_assembly": False,
                "admission_reasons": [
                    "`media smoke` es una prueba de diagnostico: no produce "
                    "media.json y sus imagenes no describen ninguna escena de un "
                    "guion, asi que ningun modulo posterior puede consumirlas."
                ],
            }
            ruta_resumen = self.out_dir / "smoke.json"
            atomic_write_json(ruta_resumen, resumen)
            resumen["summary_path"] = str(ruta_resumen)
            return resumen

    def _build_budget(self, almacen: MediaStorage) -> MediaBudget:
        """Tope de DOS peticiones por invocacion, con reserva persistida.

        El limite es por invocacion: `used` arranca en cero. Lo que impide que
        repetir la orden duplique un envio no es el contador, es la cache de
        assets -una imagen ya obtenida no se vuelve a pedir- y el bloqueo de
        resultados inciertos. Las reservas si se persisten, para que quede
        rastro de cada intento.
        """
        return MediaBudget(
            limits={"generation_attempts": float(SMOKE_MAX_REQUESTS)},
            used={"generation_attempts": 0.0},
            reserve=lambda kind, scene_id, video_seconds: almacen.reserve_request(
                media_run_id=f"smoke-{self.run_id}",
                job_id=SMOKE_JOB_ID,
                kind=kind,
                scene_id=scene_id,
                video_seconds=video_seconds,
            ),
            settle=almacen.settle_request,
        )

    def _run_step(
        self,
        paso: SmokeStep,
        *,
        almacen: MediaStorage,
        presupuesto: MediaBudget,
        modelo: str,
        referencia_ruta: Path | None,
        referencia_sha: str | None,
    ) -> dict:
        identidad = paso.identity(
            model=modelo, reference_sha256=referencia_sha, simulation=self.simulation
        )

        bloqueo = almacen.get_unknown_outcome(identidad)
        if bloqueo is not None:
            raise MediaOutcomeUnknownError(
                f"El paso {paso.step_id} quedo con resultado incierto en una "
                f"ejecucion anterior ({bloqueo['reason']}). No se repite "
                "automaticamente.",
                details={"operation_key": identidad},
            )

        destino = self.out_dir / f"{paso.step_id}.{SMOKE_FORMAT}"

        en_cache = almacen.get_cached_asset(identidad)
        if en_cache is not None and Path(en_cache["path"]).exists():
            origen = Path(en_cache["path"])
            if origen != destino:
                destino.write_bytes(origen.read_bytes())
            almacen.touch_cached_asset(identidad)
            info = inspect_image(destino, max_pixels=self.settings.media_max_image_pixels)
            return self._describe(
                paso, destino, info, cache_hit=True, request_id=None, usage=None,
                reference_sha256=referencia_sha,
            )

        peticion = ImageRequest(
            operation_id=f"smoke_{paso.step_id}",
            prompt=paso.prompt,
            model=modelo,
            size=SMOKE_SIZE,
            quality=SMOKE_QUALITY,
            output_format=SMOKE_FORMAT,
            reference_paths=[referencia_ruta] if referencia_ruta is not None else [],
            scene_id=None,
            # Se deja en None SIEMPRE: la guia de 2.6 indica omitirlo.
            input_fidelity=None,
        )
        try:
            resultado = self.provider.create_image(peticion, presupuesto)
        except MediaOutcomeUnknownError as exc:
            almacen.mark_unknown_outcome(
                operation_key=identidad,
                job_id=SMOKE_JOB_ID,
                scene_id=paso.step_id,
                reason=exc.message,
            )
            raise

        temporal = destino.with_suffix(destino.suffix + ".tmp")
        info = write_bytes_as_image(
            resultado.image_bytes,
            temporal,
            max_pixels=self.settings.media_max_image_pixels,
        )
        temporal.replace(destino)
        info = inspect_image(destino, max_pixels=self.settings.media_max_image_pixels)
        almacen.put_cached_asset(
            identidad,
            {
                "kind": "image",
                "simulation": self.simulation,
                "path": str(destino),
                "sha256": _sha256_file(destino),
                "width": info.width,
                "height": info.height,
                "meta": {
                    "request_id": resultado.request_id,
                    "usage": resultado.provider_usage,
                    "diagnostic": True,
                },
            },
        )
        return self._describe(
            paso,
            destino,
            info,
            cache_hit=False,
            request_id=resultado.request_id,
            # Solo se registra si el proveedor lo devolvio. Un dict vacio
            # significa que no lo devolvio, no que fuera cero.
            usage=dict(resultado.provider_usage) or None,
            reference_sha256=referencia_sha,
        )

    def _describe(
        self,
        paso: SmokeStep,
        destino: Path,
        info: Any,
        *,
        cache_hit: bool,
        request_id: str | None,
        usage: dict | None,
        reference_sha256: str | None,
    ) -> dict:
        esperado_ancho, esperado_alto = parse_size(SMOKE_SIZE)
        return {
            "step_id": paso.step_id,
            "status": "ok",
            "endpoint": (
                "POST /v1/images/edits"
                if paso.uses_reference
                else "POST /v1/images/generations"
            ),
            "cache_hit": cache_hit,
            "path": str(destino),
            "sha256": _sha256_file(destino),
            "bytes": destino.stat().st_size,
            "mime": info.mime,
            "width": info.width,
            "height": info.height,
            "dimensions_match_request": (
                info.width == esperado_ancho and info.height == esperado_alto
            ),
            "attached_reference_sha256": reference_sha256,
            "request_id": request_id,
            "usage": usage,
            "usage_note": _usage_note(usage, cache_hit=cache_hit),
        }


def _usage_note(usage: dict | None, *, cache_hit: bool) -> str | None:
    """Por que no hay consumo. Son dos razones distintas y no se confunden."""
    if usage:
        return None
    if cache_hit:
        return (
            "sin consumo porque no se pidio nada: la imagen venia de la cache. "
            "No es que el proveedor devolviera cero."
        )
    return "el proveedor no devolvio `usage`; ausente no es cero"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for bloque in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(bloque)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Transporte simulado
# ---------------------------------------------------------------------------


def _png_bytes(width: int, height: int, *, variant: str) -> bytes:
    """PNG sintetico con las dimensiones pedidas.

    Lo produce ESTE codigo, no el proveedor. Existe para que la comprobacion de
    dimensiones y la decodificacion sean reales; no es una imagen de Lumi y no
    se debe presentar como tal.

    Las dos variantes se diferencian a proposito: si devolviera el mismo PNG en
    los dos pasos, los SHA-256 de las dos salidas coincidirian y la
    comparacion entre imagenes perderia sentido.
    """
    import io

    from PIL import Image, ImageDraw

    imagen = Image.new("RGB", (width, height), "#3F7A6B")
    dibujo = ImageDraw.Draw(imagen)
    dibujo.rectangle(
        [width // 4, height // 3, 3 * width // 4, 2 * height // 3], fill="#E9A26A"
    )
    if variant == "edit":
        # Una banda horizontal que insinua el puente, para que el archivo de la
        # edicion sea distinto del de la generacion.
        dibujo.rectangle([0, 2 * height // 3, width, 2 * height // 3 + 40], fill="#8FBF7A")
    buffer = io.BytesIO()
    imagen.save(buffer, format="PNG")
    return buffer.getvalue()


def extract_multipart_reference(content: bytes) -> bytes | None:
    """Bytes del campo `image[]` de un multipart, exactamente como viajaron.

    Se extraen del cuerpo serializado por el SDK, no de la variable local, para
    que la comprobacion del SHA-256 pruebe lo que de verdad se envio.
    """
    if b"name=\"image[]\"" not in content:
        return None
    primera = content.split(b"\r\n", 1)[0]
    if not primera.startswith(b"--"):
        return None
    separador = b"\r\n" + primera
    for parte in content.split(separador):
        cabeceras, _, cuerpo = parte.partition(b"\r\n\r\n")
        if b'name="image[]"' not in cabeceras:
            continue
        return cuerpo[:-2] if cuerpo.endswith(b"\r\n") else cuerpo
    return None


def _multipart_field_names(content: bytes) -> list[str]:
    """Nombres de los campos de un multipart, en el orden en que aparecen."""
    import re

    # `(?<!file)` evita confundir `filename="x.png"` con un nombre de campo.
    return [
        coincidencia.decode("utf-8", "replace")
        for coincidencia in re.findall(rb'(?<!file)name="([^"]+)"', content)
    ]


def build_simulated_client(registro: list[dict], *, width: int, height: int) -> Any:
    """Cliente real de OpenAI contra un transporte falso.

    No sale ni un byte a la red y no se usa ninguna clave real: la que recibe
    el SDK es un valor con el formato que espera, para que pueda construirse.
    """
    import httpx2
    from openai import OpenAI

    def handler(request: "httpx2.Request") -> "httpx2.Response":
        contenido = request.content
        tipo = request.headers.get("content-type", "").split(";")[0]
        entrada: dict[str, Any] = {
            "method": request.method,
            "path": request.url.path,
            "content_type": tipo,
            "attached_reference_sha256": None,
        }
        if tipo == "application/json":
            # Las claves exactas que viajan, para poder afirmar que
            # `response_format` e `input_fidelity` NO estan.
            entrada["body_keys"] = sorted(json.loads(contenido))
        else:
            entrada["multipart_fields"] = sorted(
                set(_multipart_field_names(contenido))
            )
        adjunto = extract_multipart_reference(contenido)
        if adjunto is not None:
            entrada["attached_reference_sha256"] = hashlib.sha256(adjunto).hexdigest()
            entrada["attached_reference_bytes"] = len(adjunto)
        registro.append(entrada)

        es_generacion = request.url.path.endswith("/images/generations")
        png = _png_bytes(width, height, variant="generate" if es_generacion else "edit")
        cuerpo: dict[str, Any] = {
            "created": 0,
            "data": [{"b64_json": base64.b64encode(png).decode("ascii")}],
        }
        # El consumo se devuelve SOLO en la generacion, para que la prueba
        # recorra las dos ramas: con `usage` y sin el.
        if es_generacion:
            cuerpo["usage"] = {
                "input_tokens": 190,
                "input_tokens_details": {"text_tokens": 190, "image_tokens": 0},
                "output_tokens": 1056,
                "total_tokens": 1246,
            }
        return httpx2.Response(200, json=cuerpo)

    return OpenAI(
        api_key="sk-transporte-simulado-no-real",
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------


def run(
    settings: Any,
    *,
    mode: str = "plan",
    character_id: str = SMOKE_CHARACTER,
    run_id: str | None = None,
) -> dict:
    """Ejecuta la prueba en el modo pedido.

    `mode`:

    * ``"plan"``                - nada de red, nada de clave.
    * ``"simulated_transport"`` - adaptador real, transporte falso.
    * ``"real"``                - dos peticiones de verdad.
    """
    if mode == "plan":
        return plan(settings, character_id=character_id)

    if mode not in {"simulated_transport", "real"}:
        raise ConfigError(
            f"Modo de prueba desconocido: {mode!r}. Usa plan, simulated_transport o real."
        )

    # CERO reintentos seguros, en los dos modos: una prueba de dos peticiones
    # no reintenta nada. El cliente HTTP ya lleva max_retries=0.
    ajustes = settings.model_copy(update={"media_max_safe_retries": 0})
    identificador = run_id or uuid.uuid4().hex[:12]
    ancho, alto = parse_size(SMOKE_SIZE)

    if mode == "simulated_transport":
        from pydantic import SecretStr

        from .providers.openai_images import OpenAIImageProvider

        registro: list[dict] = []
        cliente = build_simulated_client(registro, width=ancho, height=alto)
        # El adaptador exige OPENAI_API_KEY porque protege el camino real. Con
        # un cliente inyectado no hay camino real, pero la guarda se deja
        # intacta: se le da un marcador visiblemente falso que no sale de este
        # proceso, en vez de relajar la comprobacion para todo el modulo.
        ajustes_simulados = ajustes.model_copy(
            update={
                "openai_api_key": SecretStr("sk-transporte-simulado-no-real"),
            }
        )
        proveedor = OpenAIImageProvider(settings=ajustes_simulados, client=cliente)
        ajustes = ajustes_simulados
        corredor = _SmokeRunner(
            ajustes,
            provider=proveedor,
            simulation=True,
            character_id=character_id,
            run_id=identificador,
        )
        resumen = corredor.execute()
        resumen["http_calls"] = registro
        resumen["transport"] = "simulated"
        resumen["transport_note"] = (
            "Las imagenes las produjo ESTE codigo, no el proveedor: sirven para "
            "comprobar la secuencia de endpoints, el adjunto y la decodificacion. "
            "No son imagenes de Lumi y no acreditan nada visual."
        )
        resumen["reference_sha256_matches"] = _reference_matches(resumen, registro)
        return resumen

    from .providers import build_image_provider

    # Exige clave y modelo, y rechaza un modelo retirado o sin verificar.
    proveedor = build_image_provider(simulation=False, settings=ajustes)
    corredor = _SmokeRunner(
        ajustes,
        provider=proveedor,
        simulation=False,
        character_id=character_id,
        run_id=identificador,
    )
    resumen = corredor.execute()
    resumen["transport"] = "real"
    return resumen


def _reference_matches(resumen: dict, registro: list[dict]) -> bool | None:
    """Si los bytes adjuntos en la edicion son los de la primera imagen.

    None cuando no hubo edicion: no se puede comparar lo que no se envio.
    """
    primera = next(
        (p for p in resumen["steps"] if p["step_id"] == "1_generacion" and p["status"] == "ok"),
        None,
    )
    adjuntos = [c for c in registro if c.get("attached_reference_sha256")]
    if primera is None or not adjuntos:
        return None
    return adjuntos[-1]["attached_reference_sha256"] == primera["sha256"]


__all__ = [
    "SMOKE_PROMPT_VERSION",
    "_SmokeRunner",
    "SMOKE_SIZE",
    "SMOKE_QUALITY",
    "SMOKE_FORMAT",
    "SMOKE_CHARACTER",
    "SMOKE_MAX_REQUESTS",
    "SmokeStep",
    "build_steps",
    "plan",
    "run",
    "build_simulated_client",
    "extract_multipart_reference",
]
