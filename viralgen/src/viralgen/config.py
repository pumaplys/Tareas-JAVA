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

    @field_validator("log_level")
    @classmethod
    def _upper_level(cls, value: str) -> str:
        level = value.strip().upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if level not in allowed:
            raise ValueError(f"LOG_LEVEL debe ser uno de {sorted(allowed)}")
        return level

    @field_validator("openai_model", "openai_base_url", mode="before")
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
