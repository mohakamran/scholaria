"""Ollama model discovery/selection and assistant (system prompt) management."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.errors import ModelNotFoundError, ValidationFailed
from app.api.deps import current_user
from app.database.connection import get_db
from app.database.models import Assistant, User
from app.database.repositories import AssistantRepository
from app.schemas.chat import AssistantCreate, AssistantOut, AssistantUpdate, ModelSelect
from app.schemas.common import Deleted
from app.services.ollama_service import OllamaService, normalize_model_name
from app.services.settings_service import RuntimeSettingsUpdate, get_runtime_settings, update_runtime_settings
from app.services.system_service import ollama_status

router = APIRouter(tags=["models"], dependencies=[Depends(current_user)])


@router.get("/models")
async def list_models(db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> dict[str, Any]:
    runtime = get_runtime_settings(db)
    models = await OllamaService(runtime.ollama_url).list_models()
    return {"models": models, "default_model": runtime.default_model, "fallback_model": runtime.fallback_model}


@router.get("/models/status")
async def models_status(db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> dict[str, Any]:
    return await ollama_status(db)


@router.post("/models/select")
async def select_model(body: ModelSelect, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> dict[str, Any]:
    runtime = get_runtime_settings(db)
    installed = {normalize_model_name(m["name"]) for m in await OllamaService(runtime.ollama_url).list_models()}
    if normalize_model_name(body.model) not in installed:
        raise ModelNotFoundError(f"Model '{body.model}' is not installed. Run: ollama pull {body.model}")
    update_runtime_settings(db, RuntimeSettingsUpdate(default_model=body.model))
    return {"default_model": body.model}


# --- Assistants ----------------------------------------------------------------
@router.get("/assistants", response_model=list[AssistantOut])
def list_assistants(db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> list[Assistant]:
    return list(AssistantRepository(db).list())


@router.post("/assistants", response_model=AssistantOut, status_code=201)
def create_assistant(body: AssistantCreate, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> Assistant:
    repo = AssistantRepository(db)
    if repo.get_by_name(body.name):
        raise ValidationFailed(f"An assistant named '{body.name}' already exists")
    assistant = repo.create(**body.model_dump(exclude={"is_default"}), is_default=False)
    if body.is_default:
        repo.set_default(assistant)
    return assistant


@router.patch("/assistants/{assistant_id}", response_model=AssistantOut)
def update_assistant(assistant_id: int, body: AssistantUpdate, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> Assistant:
    repo = AssistantRepository(db)
    assistant = repo.get_or_404(assistant_id)
    data = body.model_dump(exclude_unset=True)
    make_default = data.pop("is_default", None)
    if data:
        repo.update(assistant, **data)
    if make_default:
        repo.set_default(assistant)
    return assistant


@router.delete("/assistants/{assistant_id}", response_model=Deleted)
def delete_assistant(assistant_id: int, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> Deleted:
    repo = AssistantRepository(db)
    assistant = repo.get_or_404(assistant_id)
    if assistant.is_default:
        raise ValidationFailed("The default assistant cannot be deleted; choose another default first.")
    repo.delete(assistant)
    return Deleted(id=assistant_id)
