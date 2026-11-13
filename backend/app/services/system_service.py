"""Startup seeding and system status reporting."""

from __future__ import annotations

import shutil
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import OllamaError
from app.core.prompts import DEFAULT_ASSISTANTS
from app.database.connection import check_database
from app.database.models import Assistant
from app.database.repositories import AssistantRepository
from app.services.ollama_service import OllamaService, normalize_model_name
from app.services.settings_service import get_runtime_settings


def seed_defaults(db: Session) -> None:
    """Insert the built-in assistants once (never overwrites user edits)."""
    repo = AssistantRepository(db)
    has_default = repo.get_default() is not None
    for spec in DEFAULT_ASSISTANTS:
        if repo.get_by_name(str(spec["name"])) is None:
            db.add(Assistant(**{**spec, "is_default": bool(spec["is_default"]) and not has_default}))
    db.commit()


def storage_status() -> dict[str, Any]:
    settings = get_settings()
    try:
        usage = shutil.disk_usage(settings.storage_dir)
        writable = settings.storage_dir.exists()
        probe = settings.storage_dir / ".write_test"
        probe.write_text("ok")
        probe.unlink()
    except OSError:
        return {"ok": False, "writable": False}
    return {"ok": writable, "writable": writable, "free_gb": round(usage.free / 1e9, 1),
            "total_gb": round(usage.total / 1e9, 1)}


async def ollama_status(db: Session) -> dict[str, Any]:
    runtime = get_runtime_settings(db)
    service = OllamaService(runtime.ollama_url)
    out: dict[str, Any] = {"connected": False, "default_model": runtime.default_model,
                           "fallback_model": runtime.fallback_model}
    try:
        out["version"] = await service.version()
        models = await service.list_models()
        names = {normalize_model_name(m["name"]) for m in models if m.get("name")}
        out.update(
            connected=True, installed_models=len(models),
            default_model_available=normalize_model_name(runtime.default_model) in names,
            fallback_model_available=normalize_model_name(runtime.fallback_model) in names,
        )
        try:
            out["loaded_models"] = await service.running_models()
        except OllamaError:
            out["loaded_models"] = []
    except OllamaError as exc:
        out["error"] = exc.message
    return out


async def system_status(db: Session) -> dict[str, Any]:
    settings = get_settings()
    return {
        "app": {"name": settings.app_name, "version": settings.app_version, "api": "ok"},
        "database": check_database(),
        "storage": storage_status(),
        "ollama": await ollama_status(db),
    }
