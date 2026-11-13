"""Academic writing assistant endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.routes.chat import SSE_HEADERS
from app.api.deps import current_user
from app.database.connection import get_db
from app.database.models import User
from app.schemas.writing import WritingRequest
from app.services.writing_service import WritingService, list_tasks, stream_llm_sse

router = APIRouter(prefix="/writing", tags=["writing"], dependencies=[Depends(current_user)])


@router.get("/tasks")
def writing_tasks() -> list[dict[str, str]]:
    return list_tasks()


@router.post("/generate")
async def generate(body: WritingRequest, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> StreamingResponse:
    service = WritingService(db, user.id)
    model, messages, temperature, num_ctx, num_predict = await service.prepare(body)
    stream = stream_llm_sse(service.ollama, model, messages, temperature=temperature, num_ctx=num_ctx,
                            num_predict=num_predict, keep_alive=service.runtime.keep_alive,
                            post_check=service.citation_check(body))
    return StreamingResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)
