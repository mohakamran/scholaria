"""Research data assistant endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.api.deps import current_user
from app.database.connection import get_db
from app.database.models import AnalysisRun, Dataset, User
from app.database.repositories import AnalysisRunRepository, DatasetRepository, ProjectRepository
from app.schemas.common import Deleted
from app.schemas.datasets import AnalysisRunOut, AnalyzeRequest, DatasetOut, InterpretRequest
from app.schemas.research import LinkRequest
from app.services.analysis_service import ALLOWED_DATASET_TYPES, AnalysisService
from app.services.settings_service import get_runtime_settings
from app.services.storage_service import save_upload
from app.services.writing_service import WritingService

router = APIRouter(prefix="/datasets", tags=["datasets"], dependencies=[Depends(current_user)])


@router.post("/upload", response_model=DatasetOut, status_code=201)
async def upload_dataset(file: UploadFile = File(...), project_id: int | None = Form(None),
                         db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> Dataset:
    if project_id is not None:
        ProjectRepository(db, user.id).get_or_404(project_id)
    runtime = get_runtime_settings(db)
    stored = await save_upload(file, get_settings().datasets_dir, ALLOWED_DATASET_TYPES,
                               runtime.max_dataset_mb * 1024 * 1024)
    service = AnalysisService(db, user.id)
    return await run_in_threadpool(service.register_upload, stored, project_id)


@router.get("", response_model=list[DatasetOut])
def list_datasets(project_id: int | None = None, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> list[Dataset]:
    return list(DatasetRepository(db, user.id).list(project_id))


@router.get("/{dataset_id}", response_model=DatasetOut)
def get_dataset(dataset_id: int, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> Dataset:
    return DatasetRepository(db, user.id).get_or_404(dataset_id)


@router.patch("/{dataset_id}", response_model=DatasetOut)
def link_dataset(dataset_id: int, body: LinkRequest, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> Dataset:
    repo = DatasetRepository(db, user.id)
    ds = repo.get_or_404(dataset_id)
    if body.project_id is not None:
        ProjectRepository(db, user.id).get_or_404(body.project_id)
    return repo.update(ds, project_id=body.project_id)


@router.delete("/{dataset_id}", response_model=Deleted)
def delete_dataset(dataset_id: int, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> Deleted:
    service = AnalysisService(db, user.id)
    service.delete(service.datasets.get_or_404(dataset_id))
    return Deleted(id=dataset_id)


@router.get("/{dataset_id}/preview")
async def preview_dataset(dataset_id: int, rows: int = Query(50, ge=1, le=500),
                          db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> dict[str, Any]:
    service = AnalysisService(db, user.id)
    ds = service.datasets.get_or_404(dataset_id)
    return await run_in_threadpool(service.preview, ds, rows)


@router.post("/{dataset_id}/analyze", response_model=AnalysisRunOut, status_code=201)
async def analyze_dataset(dataset_id: int, body: AnalyzeRequest, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> AnalysisRun:
    service = AnalysisService(db, user.id)
    ds = service.datasets.get_or_404(dataset_id)
    return await run_in_threadpool(service.analyze, ds, body)


@router.get("/{dataset_id}/analyses", response_model=list[AnalysisRunOut])
def list_analyses(dataset_id: int, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> list[AnalysisRun]:
    DatasetRepository(db, user.id).get_or_404(dataset_id)
    return list(AnalysisRunRepository(db, user.id).list_for_dataset(dataset_id))


@router.delete("/{dataset_id}/analyses/{analysis_id}", response_model=Deleted)
def delete_analysis(dataset_id: int, analysis_id: int, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> Deleted:
    repo = AnalysisRunRepository(db, user.id)
    run = repo.get_or_404(analysis_id)
    if run.dataset_id != dataset_id:
        raise NotFoundError(f"Analysis {analysis_id} not found for dataset {dataset_id}")
    repo.delete(run)
    return Deleted(id=analysis_id)


@router.post("/{dataset_id}/analyses/{analysis_id}/interpret")
async def interpret_analysis(dataset_id: int, analysis_id: int, body: InterpretRequest | None = None,
                             db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> dict[str, Any]:
    run = AnalysisRunRepository(db, user.id).get_or_404(analysis_id)
    if run.dataset_id != dataset_id:
        raise NotFoundError(f"Analysis {analysis_id} not found for dataset {dataset_id}")
    body = body or InterpretRequest()
    return await WritingService(db, user.id).interpret_analysis(run, body.model, body.question)


@router.get("/{dataset_id}/export")
async def export_dataset_analyses(dataset_id: int, format: str = Query("xlsx", pattern="^(xlsx|csv|json)$"),
                                  analysis_id: int | None = None, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> Response:
    service = AnalysisService(db, user.id)
    ds = service.datasets.get_or_404(dataset_id)
    content, media_type, filename = await run_in_threadpool(service.export, ds, format, analysis_id)
    return Response(content, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
