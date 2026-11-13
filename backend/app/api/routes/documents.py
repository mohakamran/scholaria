"""Literature & document assistant endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.api.deps import current_user
from app.database.connection import get_db
from app.database.models import Document, User
from app.database.repositories import ChunkRepository, DocumentRepository, ProjectRepository
from app.schemas.common import Deleted
from app.schemas.documents import (
    AskRequest,
    AskResponse,
    ChunkOut,
    DocumentOut,
    DocumentUpdate,
    SummarizeRequest,
    SummarizeResponse,
)
from app.services.document_service import ALLOWED_DOCUMENT_TYPES, TASKS, DocumentService
from app.services.settings_service import get_runtime_settings
from app.services.storage_service import save_upload

router = APIRouter(prefix="/documents", tags=["documents"], dependencies=[Depends(current_user)])


@router.post("/upload", response_model=DocumentOut, status_code=201)
async def upload_document(file: UploadFile = File(...), project_id: int | None = Form(None),
                          process: bool = Form(True), db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> Document:
    if project_id is not None:
        ProjectRepository(db, user.id).get_or_404(project_id)
    runtime = get_runtime_settings(db)
    stored = await save_upload(file, get_settings().uploads_dir, ALLOWED_DOCUMENT_TYPES,
                               runtime.max_upload_mb * 1024 * 1024)
    service = DocumentService(db, user.id)
    doc = service.register_upload(stored, project_id)
    if process:
        doc = await run_in_threadpool(service.process, doc)
    return doc


@router.get("", response_model=list[DocumentOut])
def list_documents(project_id: int | None = None, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> list[Document]:
    return list(DocumentRepository(db, user.id).list(project_id))


@router.get("/tasks")
def literature_tasks() -> list[dict[str, str]]:
    return [{"id": k, "label": v["label"], "instruction": v["instruction"]} for k, v in TASKS.items()]


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(document_id: int, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> Document:
    return DocumentRepository(db, user.id).get_or_404(document_id)


@router.patch("/{document_id}", response_model=DocumentOut)
def update_document(document_id: int, body: DocumentUpdate, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> Document:
    repo = DocumentRepository(db, user.id)
    doc = repo.get_or_404(document_id)
    data = body.model_dump(exclude_unset=True)
    if data.get("project_id") is not None:
        ProjectRepository(db, user.id).get_or_404(data["project_id"])
    return repo.update(doc, **data)


@router.delete("/{document_id}", response_model=Deleted)
def delete_document(document_id: int, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> Deleted:
    service = DocumentService(db, user.id)
    service.delete(service.docs.get_or_404(document_id))
    return Deleted(id=document_id)


@router.post("/{document_id}/process", response_model=DocumentOut)
async def process_document(document_id: int, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> Document:
    service = DocumentService(db, user.id)
    doc = service.docs.get_or_404(document_id)
    return await run_in_threadpool(service.process, doc)


@router.get("/{document_id}/chunks", response_model=list[ChunkOut])
def document_chunks(document_id: int, page: int | None = None, limit: int = Query(20, ge=1, le=200),
                    offset: int = Query(0, ge=0), db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> list[Any]:
    DocumentRepository(db, user.id).get_or_404(document_id)
    chunks = [c for c in ChunkRepository(db, user.id).iter_for_document(document_id)
              if page is None or c.page_number == page]
    return chunks[offset: offset + limit]


@router.post("/{document_id}/ask", response_model=AskResponse)
async def ask_document(document_id: int, body: AskRequest, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> AskResponse:
    service = DocumentService(db, user.id)
    return await service.ask(service.docs.get_or_404(document_id), body)


@router.post("/{document_id}/summarize", response_model=SummarizeResponse)
async def summarize_document(document_id: int, body: SummarizeRequest | None = None,
                             db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> SummarizeResponse:
    service = DocumentService(db, user.id)
    return await service.summarize(service.docs.get_or_404(document_id), body or SummarizeRequest())
