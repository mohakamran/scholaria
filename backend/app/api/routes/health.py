"""Health, system status, runtime settings and dashboard activity."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.api.deps import current_user
from app.database.connection import check_database, get_db
from app.database.models import User
from app.database.repositories import recent_activity
from app.services.settings_service import RuntimeSettings, RuntimeSettingsUpdate, get_runtime_settings, update_runtime_settings
from app.services.system_service import ollama_status, system_status

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, Any]:
    settings = get_settings()
    db = check_database()
    return {"status": "ok" if db.get("ok") else "degraded", "version": settings.app_version,
            "database": "ok" if db.get("ok") else "error"}


@router.get("/system/status")
async def status(db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> dict[str, Any]:
    return await system_status(db)


@router.post("/system/test-ollama")
async def test_ollama(db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> dict[str, Any]:
    return await ollama_status(db)


@router.get("/settings", response_model=RuntimeSettings)
def read_settings(db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> RuntimeSettings:
    return get_runtime_settings(db)


@router.put("/settings", response_model=RuntimeSettings)
def write_settings(update: RuntimeSettingsUpdate, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> RuntimeSettings:
    return update_runtime_settings(db, update)


@router.get("/settings/info")
def app_info(user: User = Depends(current_user)) -> dict[str, Any]:
    """Non-sensitive application facts for the Settings page (no absolute paths)."""
    s = get_settings()
    return {"app_name": s.app_name, "version": s.app_version, "database_engine": "SQLite",
            "database_file": "storage/research.db" if not s.database_url else "custom",
            "storage_folder": "storage/", "max_concurrent_requests": s.max_concurrent_requests,
            "chunk_chars": s.chunk_chars}


@router.get("/activity")
def activity(limit: int = 10, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> list[dict[str, Any]]:
    return recent_activity(db, user.id, max(1, min(limit, 50)))
