"""Configuracion centralizada (pydantic-settings).

Todo ajuste del modulo vive aqui. Las variables de entorno llevan el prefijo
``VIRALGEN_`` salvo las dos del proveedor (``OPENAI_API_KEY`` y
``OPENAI_MODEL``), que conservan su nombre habitual.

La clave y el modelo SOLO son obligatorios en modo real: ``--mock`` funciona
sin claves y sin red.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .errors import ConfigError
from .textutil import sha256_json


class Settings(BaseSettings):
    """Ajustes del proceso. Se leen de entorno y de un ``.env`` opcional."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="VIRALGEN_",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Proveedor -------------------------------------------------------
    openai_api_key: SecretStr | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str | None = Field(default=None, alias="OPENAI_MODEL")
    openai_base_url: str | None = Field(default=None, alias="OPENAI_BASE_URL")

    # --- Rutas y registro ------------------------------------------------
    data_dir: Path = Path("./.viralgen")
    log_level: str = "INFO"
    log_max_bytes: int = 5 * 1024 * 1024
    log_backup_count: int = 3
    simulation_data_subdir: str = "simulation"

    # --- Limites por peticion y por trabajo ------------------------------
    request_timeout_seconds: int = Field(default=60, ge=1, le=600)
    max_calls_per_job: int = Field(default=6, ge=1, le=50)
    max_transport_retries: int = Field(default=2, ge=0, le=10)
    max_output_tokens: int = Field(default=6000, ge=256, le=128_000)
    max_input_chars: int = Field(default=60_000, ge=1000)
    max_source_pack_facts_to_model: int = Field(default=40, ge=1, le=500)

    # --- Disco -----------------------------------------------------------
    min_free_disk_mb: int = Field(default=1500, ge=0)

    # --- Seleccion de ideas ----------------------------------------------
    duplicate_similarity_threshold: float = Field(default=0.80, ge=0.0, le=1.0)
    history_lookback_days: int = Field(default=90, ge=1, le=3650)
    history_max_items: int = Field(default=30, ge=1, le=200)
    ideas_per_batch: int = Field(default=5, ge=1, le=20)

    # --- Muestreo --------------------------------------------------------
    send_temperature: bool = False
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)

    # --- Tarifas (USD por millon de tokens) ------------------------------
    price_input_per_1m_usd: float | None = Field(default=None, ge=0.0)
    price_output_per_1m_usd: float | None = Field(default=None, ge=0.0)

    # --- Catalogos propios -----------------------------------------------
    profiles_path: Path | None = None
    series_bible_path: Path | None = None

    # --- Modulo 2: voz ----------------------------------------------------
    # Estas credenciales son independientes de las de OpenAI: si el guion ya
    # existe, generar voz NO exige OPENAI_API_KEY ni OPENAI_MODEL.
    elevenlabs_api_key: SecretStr | None = Field(default=None, alias="ELEVENLABS_API_KEY")
    elevenlabs_model_id: str | None = Field(default=None, alias="ELEVENLABS_MODEL_ID")
    elevenlabs_voice_id: str | None = Field(default=None, alias="ELEVENLABS_VOICE_ID")
    elevenlabs_base_url: str = Field(default="https://api.elevenlabs.io", alias="ELEVENLABS_BASE_URL")

    #: Formato que se pide al proveedor. Se decodifica despues a WAV PCM.
    voice_output_format: str = "mp3_44100_128"
    #: Formato interno del proyecto. Decision fija, registrada en el manifiesto.
    voice_sample_rate_hz: int = Field(default=24_000, ge=8_000, le=48_000)
    voice_channels: int = Field(default=1, ge=1, le=2)
    voice_sample_width_bytes: int = Field(default=2, ge=1, le=4)

    voice_max_requests_per_job: int = Field(default=24, ge=1, le=500)
    voice_max_transport_retries: int = Field(default=2, ge=0, le=10)
    voice_request_timeout_seconds: int = Field(default=60, ge=1, le=600)
    voice_max_request_chars: int = Field(default=2_500, ge=1, le=20_000)
    #: Limite por respuesta HTTP, en bytes (10 MiB).
    voice_max_response_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    #: Limite de almacenamiento por trabajo de voz, en MiB.
    voice_max_job_storage_mb: int = Field(default=250, ge=1)

    #: Tolerancia para redondeos temporales en la alineacion, en milisegundos.
    voice_alignment_tolerance_ms: int = Field(default=20, ge=0, le=1000)
    #: Alineacion forzada sobre el WAV ya sintetizado. Desactivada por defecto.
    voice_allow_forced_alignment: bool = False

    voice_profiles_path: Path | None = None
    sound_assets_path: Path | None = None
    voice_enable_sfx: bool = False
    voice_enable_music: bool = False

    ffmpeg_path: str = "ffmpeg"
    ffmpeg_timeout_seconds: int = Field(default=120, ge=1, le=3600)

    #: Tarifa de voz en USD por cada 1000 caracteres enviados. Sin ella, el
    #: coste estimado es null.
    price_voice_per_1k_chars_usd: float | None = Field(default=None, ge=0.0)

    # --- Modulo 3: medios visuales ---------------------------------------
    # Independientes de OPENAI_MODEL (guion) y de las credenciales de voz.
    openai_image_model: str | None = Field(default=None, alias="OPENAI_IMAGE_MODEL")
    runwayml_api_secret: SecretStr | None = Field(default=None, alias="RUNWAYML_API_SECRET")
    runway_model: str | None = Field(default=None, alias="RUNWAY_MODEL")
    runway_base_url: str = Field(
        default="https://api.dev.runwayml.com", alias="RUNWAY_BASE_URL"
    )
    #: Cabecera X-Runway-Version. Viaja tambien en la procedencia del manifiesto.
    runway_api_version: str = Field(default="2024-11-06", alias="RUNWAY_API_VERSION")

    #: Presupuestos del trabajo. Decisiones DEL PROYECTO, no limites del proveedor.
    media_max_generation_attempts: int = Field(default=24, ge=1, le=500)
    media_max_video_scenes: int = Field(default=2, ge=0, le=20)
    #: Segundos de video reservados sumando la duracion de cada intento de creacion.
    media_max_video_seconds: int = Field(default=20, ge=0, le=600)
    media_max_safe_retries: int = Field(default=2, ge=0, le=10)
    media_max_status_requests: int = Field(default=120, ge=1, le=10_000)
    media_max_download_attempts: int = Field(default=3, ge=1, le=20)
    #: Espera local por invocacion; despues se devuelve waiting_remote.
    media_poll_wait_s: int = Field(default=60, ge=1, le=3600)
    #: Intervalo minimo entre consultas de estado, en segundos.
    media_poll_interval_s: float = Field(default=5.0, ge=5.0, le=120.0)
    media_image_timeout_s: int = Field(default=180, ge=1, le=1800)
    media_http_timeout_s: int = Field(default=60, ge=1, le=600)
    media_status_timeout_s: int = Field(default=20, ge=1, le=600)
    #: Limite de la respuesta de imagen ANTES de decodificar base64, en MiB.
    media_max_image_response_mib: int = Field(default=32, ge=1, le=512)
    media_max_video_file_mib: int = Field(default=100, ge=1, le=2048)
    #: Tamano total del trabajo, incluidos temporales y derivados, en MiB.
    media_max_job_mib: int = Field(default=500, ge=1, le=20_480)
    #: Limite global de la cache de assets, en MiB.
    media_cache_max_mib: int = Field(default=1024, ge=1, le=102_400)

    #: Limite propio para la data URI YA CODIFICADA que se envia a Runway.
    media_data_uri_max_bytes: int = Field(default=4_000_000, ge=100_000)
    #: Tope de pixeles descomprimidos al abrir una imagen (proteccion local).
    media_max_image_pixels: int = Field(default=40_000_000, ge=1_000_000)
    media_max_prompt_chars: int = Field(default=4_000, ge=100, le=32_000)

    media_image_quality: str = "medium"
    media_image_format: str = "jpeg"
    #: Politica de adaptacion geometrica: contain (relleno) o crop (recorte).
    media_geometry_policy: str = "contain"
    #: Version del conjunto de referencias; por defecto se deriva de la biblia.
    media_reference_set_version: str | None = None
    #: Referencias importadas: archivo JSON con procedencia declarada.
    media_reference_pack_path: Path | None = None

    ffprobe_path: str = "ffprobe"

    #: Tarifas explicitas. Sin ellas, el coste estimado es null.
    price_image_per_unit_usd: float | None = Field(default=None, ge=0.0)
    price_video_per_second_usd: float | None = Field(default=None, ge=0.0)

    @field_validator("log_level")
    @classmethod
    def _upper_level(cls, value: str) -> str:
        level = value.strip().upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if level not in allowed:
            raise ValueError(f"LOG_LEVEL debe ser uno de {sorted(allowed)}")
        return level

    @field_validator(
        "openai_model",
        "openai_base_url",
        "elevenlabs_model_id",
        "elevenlabs_voice_id",
        "openai_image_model",
        "runway_model",
        mode="before",
    )
    @classmethod
    def _empty_to_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    # -- Derivados ---------------------------------------------------------

    def effective_data_dir(self, *, simulation: bool) -> Path:
        """Espacio de datos real o simulado.

        La simulacion usa un subdirectorio propio para no contaminar el
        historial de ideas ni la base de datos reales.
        """
        base = self.data_dir.expanduser()
        return (base / self.simulation_data_subdir) if simulation else base

    def require_provider_settings(self) -> tuple[str, str]:
        """Devuelve (api_key, model) o lanza ConfigError en modo real."""
        missing: list[str] = []
        if self.openai_api_key is None or not self.openai_api_key.get_secret_value().strip():
            missing.append("OPENAI_API_KEY")
        if not self.openai_model:
            missing.append("OPENAI_MODEL")
        if missing:
            raise ConfigError(
                "Faltan variables obligatorias en modo real: "
                + ", ".join(missing)
                + ". Usa --mock para ejecutar sin proveedor, o define esas variables "
                "en el entorno o en .env (ver .env.example). El modelo no tiene valor "
                "por defecto a proposito: debe ser uno real que admita Structured Outputs.",
                details={"missing": missing},
            )
        assert self.openai_api_key is not None and self.openai_model is not None
        return self.openai_api_key.get_secret_value(), self.openai_model

    def require_voice_settings(self) -> tuple[str, str]:
        """Devuelve (api_key, model_id) o lanza ConfigError en modo real.

        No toca la configuracion de OpenAI: generar voz sobre un guion que ya
        existe no necesita credenciales del modulo 1.
        """
        missing: list[str] = []
        if (
            self.elevenlabs_api_key is None
            or not self.elevenlabs_api_key.get_secret_value().strip()
        ):
            missing.append("ELEVENLABS_API_KEY")
        if not self.elevenlabs_model_id:
            missing.append("ELEVENLABS_MODEL_ID")
        if missing:
            raise ConfigError(
                "Faltan variables obligatorias para la voz real: "
                + ", ".join(missing)
                + ". Usa --mock para ejecutar sin proveedor, o definelas en el entorno "
                "o en .env (ver .env.example). No hay modelo por defecto a proposito.",
                details={"missing": missing},
            )
        assert self.elevenlabs_api_key is not None and self.elevenlabs_model_id is not None
        return self.elevenlabs_api_key.get_secret_value(), self.elevenlabs_model_id

    def require_image_settings(self) -> str:
        """Modelo de imagenes en modo real. No hay valor por defecto.

        Es independiente de OPENAI_MODEL (que usa el modulo 1 para el guion):
        un modelo de texto no sirve para imagenes y no se sustituye en silencio.
        """
        if not self.openai_image_model:
            raise ConfigError(
                "Falta OPENAI_IMAGE_MODEL. Es independiente de OPENAI_MODEL: hay que "
                "declarar explicitamente el modelo de imagenes. Usa --mock para un "
                "recorrido de pruebas sin proveedor.",
                details={"missing": ["OPENAI_IMAGE_MODEL"]},
            )
        if self.openai_api_key is None or not self.openai_api_key.get_secret_value().strip():
            raise ConfigError(
                "Falta OPENAI_API_KEY para generar imagenes.",
                details={"missing": ["OPENAI_API_KEY"]},
            )
        return self.openai_image_model

    def require_video_settings(self) -> tuple[str, str]:
        """(secreto, modelo) de Runway. Solo hace falta si el guion pide clips."""
        missing: list[str] = []
        if (
            self.runwayml_api_secret is None
            or not self.runwayml_api_secret.get_secret_value().strip()
        ):
            missing.append("RUNWAYML_API_SECRET")
        if not self.runway_model:
            missing.append("RUNWAY_MODEL")
        if missing:
            raise ConfigError(
                "Faltan variables para generar clips: "
                + ", ".join(missing)
                + ". Un trabajo solo de imagenes no necesita Runway.",
                details={"missing": missing},
            )
        assert self.runwayml_api_secret is not None and self.runway_model is not None
        return self.runwayml_api_secret.get_secret_value(), self.runway_model

    def media_hashable_view(self) -> dict[str, Any]:
        """Ajustes de medios que influyen en el resultado. Sin secretos.

        Los limites administrativos (presupuestos, tamanos) quedan FUERA a
        proposito: cambiarlos no debe invalidar la identidad de un asset.
        """
        return {
            "media_image_quality": self.media_image_quality,
            "media_image_format": self.media_image_format,
            "media_geometry_policy": self.media_geometry_policy,
            "media_reference_set_version": self.media_reference_set_version,
            "runway_api_version": self.runway_api_version,
        }

    def media_pricing(self) -> dict[str, float | None]:
        return {
            "image_per_unit_usd": self.price_image_per_unit_usd,
            "video_per_second_usd": self.price_video_per_second_usd,
        }

    def voice_pricing(self) -> float | None:
        """Tarifa explicita por 1000 caracteres, o None."""
        return self.price_voice_per_1k_chars_usd

    def pricing(self) -> tuple[float, float] | None:
        """Tarifas explicitas o None. Sin tarifas, el coste estimado es null."""
        if self.price_input_per_1m_usd is None or self.price_output_per_1m_usd is None:
            return None
        return (self.price_input_per_1m_usd, self.price_output_per_1m_usd)

    def hashable_view(self) -> dict[str, Any]:
        """Subconjunto de ajustes que influye en el resultado generado.

        NUNCA incluye secretos. Se usa para `config_hash`, que viaja en el
        documento exportado y en el fingerprint de idempotencia.
        """
        return {
            "duplicate_similarity_threshold": self.duplicate_similarity_threshold,
            "history_lookback_days": self.history_lookback_days,
            "history_max_items": self.history_max_items,
            "ideas_per_batch": self.ideas_per_batch,
            "max_calls_per_job": self.max_calls_per_job,
            "max_output_tokens": self.max_output_tokens,
            "max_source_pack_facts_to_model": self.max_source_pack_facts_to_model,
            "send_temperature": self.send_temperature,
            "temperature": self.temperature if self.send_temperature else None,
        }

    def voice_hashable_view(self) -> dict[str, Any]:
        """Ajustes de voz que influyen en el resultado. Sin secretos."""
        return {
            "voice_output_format": self.voice_output_format,
            "voice_sample_rate_hz": self.voice_sample_rate_hz,
            "voice_channels": self.voice_channels,
            "voice_sample_width_bytes": self.voice_sample_width_bytes,
            "voice_alignment_tolerance_ms": self.voice_alignment_tolerance_ms,
            "voice_allow_forced_alignment": self.voice_allow_forced_alignment,
            "voice_enable_sfx": self.voice_enable_sfx,
            "voice_enable_music": self.voice_enable_music,
        }

    def config_hash(self, extra: dict[str, Any] | None = None) -> str:
        """SHA-256 de los ajustes relevantes mas `extra` (perfil, biblia...)."""
        payload: dict[str, Any] = {"settings": self.hashable_view()}
        if extra:
            payload["extra"] = extra
        return sha256_json(payload)


def load_settings(**overrides: Any) -> Settings:
    """Carga los ajustes, convirtiendo errores de pydantic en ConfigError."""
    try:
        return Settings(**overrides)
    except Exception as exc:  # pragma: no cover - mensaje uniforme
        raise ConfigError(f"Configuracion invalida: {exc}") from exc
