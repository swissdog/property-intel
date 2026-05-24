"""Property-intel-moduulin asetukset."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_MODULE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """Env-prefiksi `JARVIS_PROPERTY_INTEL_`."""

    model_config = SettingsConfigDict(
        env_prefix="JARVIS_PROPERTY_INTEL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    core_url: str = "http://localhost:8000"
    bootstrap_token: str = "dev-bootstrap-token-change-me"
    x_module_auth_secret: str = "dev-x-module-auth-secret-change-me-min-32-bytes"

    host: str = "0.0.0.0"
    port: int = 8031

    manifest_path: Path = _MODULE_DIR / "module.yaml"

    database_url: str = (
        "postgresql+asyncpg://property_intel_user:changeme"
        "@localhost:5435/jarvis_property_intel"
    )

    raw_storage_path: Path = _MODULE_DIR / "data" / "property-raw"

    skip_registration: bool = False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
