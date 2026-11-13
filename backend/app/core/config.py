"""Application configuration.

Static configuration is loaded from environment variables (prefix ``RA_``) and the
project-level ``.env`` file. Values that the user may change at runtime from the
Settings page (model, temperature, ...) are persisted in the SQLite ``settings``
table and merged on top of these defaults by ``services.settings_service``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> project root is three levels above ``app``.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
APP_VERSION = "1.0.0"


class Settings(BaseSettings):
    """Static, process-wide settings."""

    model_config = SettingsConfigDict(
        env_prefix="RA_",
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Scholaria"
    app_version: str = APP_VERSION
    environment: str = "local"

    # --- Network binding (localhost only by default) -----------------------
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    # --- Ollama -------------------------------------------------------------
    ollama_url: str = "http://127.0.0.1:11434"
    default_model: str = "qwen3:4b-instruct-2507-q4_K_M"
    fallback_model: str = "qwen3:1.7b"
    context_length: int = Field(4096, ge=512, le=32768)
    max_tokens: int = Field(1024, ge=32, le=8192)
    temperature: float = Field(0.6, ge=0.0, le=2.0)
    keep_alive: str = "2m"
    max_concurrent_requests: int = Field(1, ge=1, le=4)
    ollama_connect_timeout: float = 5.0
    ollama_read_timeout: float = 300.0
    # Appends Qwen3's "/no_think" soft switch for hybrid-thinking models
    # (e.g. qwen3:1.7b) so they answer directly without long reasoning traces.
    disable_thinking: bool = True

    # --- Conversation budget -----------------------------------------------
    history_max_messages: int = Field(12, ge=2, le=100)

    # --- Storage ------------------------------------------------------------
    storage_dir: Path = PROJECT_ROOT / "storage"
    database_url: str | None = None  # defaults to sqlite file in storage_dir

    # --- Uploads ------------------------------------------------------------
    max_upload_mb: int = Field(25, ge=1, le=500)
    max_dataset_mb: int = Field(20, ge=1, le=500)

    # --- Document processing / retrieval -----------------------------------
    chunk_chars: int = Field(1200, ge=200, le=8000)
    chunk_overlap: int = Field(150, ge=0, le=1000)
    retrieval_top_k: int = Field(5, ge=1, le=20)
    summary_window_chars: int = Field(6000, ge=1000, le=40000)
    summary_max_windows: int = Field(40, ge=1, le=500)

    # --- Logging -----------------------------------------------------------
    log_level: str = "INFO"

    @field_validator("storage_dir", mode="before")
    @classmethod
    def _resolve_storage(cls, value: str | Path) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path

    # Derived paths ---------------------------------------------------------
    @property
    def uploads_dir(self) -> Path:
        return self.storage_dir / "uploads"

    @property
    def datasets_dir(self) -> Path:
        return self.storage_dir / "datasets"

    @property
    def exports_dir(self) -> Path:
        return self.storage_dir / "exports"

    @property
    def logs_dir(self) -> Path:
        return self.storage_dir / "logs"

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{self.storage_dir / 'research.db'}"

    def ensure_directories(self) -> None:
        for path in (self.storage_dir, self.uploads_dir, self.datasets_dir, self.exports_dir, self.logs_dir):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings instance (call ``cache_clear`` in tests)."""
    return Settings()
