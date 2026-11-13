"""Runtime (user-editable) settings persisted in SQLite on top of static config."""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database.repositories import SettingRepository

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


def validate_local_url(value: str) -> str:
    """Only allow Ollama on this machine: all inference must stay local."""
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Ollama URL must look like http://127.0.0.1:11434")
    if parsed.hostname not in LOCAL_HOSTS:
        raise ValueError("Only a local Ollama server (127.0.0.1 / localhost) is allowed")
    return value.strip().rstrip("/")


class RuntimeSettings(BaseModel):
    """Effective settings used by services for every request."""

    ollama_url: str
    default_model: str
    fallback_model: str
    temperature: float = Field(ge=0.0, le=2.0)
    context_length: int = Field(ge=512, le=32768)
    max_tokens: int = Field(ge=32, le=8192)
    keep_alive: str
    max_upload_mb: int = Field(ge=1, le=500)
    max_dataset_mb: int = Field(ge=1, le=500)
    history_max_messages: int = Field(ge=2, le=100)
    retrieval_top_k: int = Field(ge=1, le=20)
    theme: Literal["light", "dark"] = "light"
    default_assistant_id: int | None = None


class RuntimeSettingsUpdate(BaseModel):
    """Partial update from the Settings page. Every field is validated."""

    ollama_url: str | None = None
    default_model: str | None = Field(None, min_length=1, max_length=200)
    fallback_model: str | None = Field(None, min_length=1, max_length=200)
    temperature: float | None = Field(None, ge=0.0, le=2.0)
    context_length: int | None = Field(None, ge=512, le=32768)
    max_tokens: int | None = Field(None, ge=32, le=8192)
    keep_alive: str | None = Field(None, pattern=r"^(-1|0|\d+(s|m|h))$")
    max_upload_mb: int | None = Field(None, ge=1, le=500)
    max_dataset_mb: int | None = Field(None, ge=1, le=500)
    history_max_messages: int | None = Field(None, ge=2, le=100)
    retrieval_top_k: int | None = Field(None, ge=1, le=20)
    theme: Literal["light", "dark"] | None = None
    default_assistant_id: int | None = None

    @field_validator("ollama_url")
    @classmethod
    def _local_only(cls, value: str | None) -> str | None:
        return validate_local_url(value) if value is not None else None


def _defaults() -> dict[str, Any]:
    s = get_settings()
    return {
        "ollama_url": s.ollama_url,
        "default_model": s.default_model,
        "fallback_model": s.fallback_model,
        "temperature": s.temperature,
        "context_length": s.context_length,
        "max_tokens": s.max_tokens,
        "keep_alive": s.keep_alive,
        "max_upload_mb": s.max_upload_mb,
        "max_dataset_mb": s.max_dataset_mb,
        "history_max_messages": s.history_max_messages,
        "retrieval_top_k": s.retrieval_top_k,
        "theme": "light",
        "default_assistant_id": None,
    }


def get_runtime_settings(db: Session) -> RuntimeSettings:
    values = _defaults()
    stored = SettingRepository(db).all_values()
    for key in values:
        if key in stored:
            values[key] = stored[key]
    try:
        return RuntimeSettings(**values)
    except ValueError:
        # A corrupted stored value must never brick the app: fall back to defaults.
        return RuntimeSettings(**_defaults())


def update_runtime_settings(db: Session, update: RuntimeSettingsUpdate) -> RuntimeSettings:
    repo = SettingRepository(db)
    for key, value in update.model_dump(exclude_unset=True).items():
        repo.set_value(key, value)
    return get_runtime_settings(db)
