"""Conversations and chat (JSON and server-sent-event streaming)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.database.connection import get_db
from app.database.models import Conversation, User
from app.database.repositories import ConversationRepository, MessageRepository, ProjectRepository
from app.schemas.chat import (
    ChatRequest,
    ChatResponse,
    ConversationCreate,
    ConversationDetail,
    ConversationOut,
    ConversationUpdate,
    MessageOut,
    RegenerateRequest,
)
from app.schemas.common import Deleted
from app.services.chat_service import ChatService, request_stop

router = APIRouter(tags=["chat"], dependencies=[Depends(current_user)])

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}


def _out(repo: ConversationRepository, conv: Conversation) -> ConversationOut:
    out = ConversationOut.model_validate(conv)
    out.message_count = repo.message_count(conv.id)
    return out


@router.post("/conversations", response_model=ConversationOut, status_code=201)
def create_conversation(body: ConversationCreate, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> ConversationOut:
    if body.project_id is not None:
        ProjectRepository(db, user.id).get_or_404(body.project_id)
    repo = ConversationRepository(db, user.id)
    conv = repo.create(title=body.title or "New conversation", project_id=body.project_id,
                       assistant_type=body.assistant_type, assistant_id=body.assistant_id,
                       model_name=body.model_name)
    return _out(repo, conv)


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(project_id: int | None = None, assistant_type: str | None = None,
                       limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                       db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> list[ConversationOut]:
    repo = ConversationRepository(db, user.id)
    return [_out(repo, c) for c in repo.list(project_id=project_id, assistant_type=assistant_type,
                                             limit=limit, offset=offset)]


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: int, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> ConversationDetail:
    repo = ConversationRepository(db, user.id)
    conv = repo.get_or_404(conversation_id)
    detail = ConversationDetail.model_validate(conv)
    detail.messages = [MessageOut.model_validate(m) for m in MessageRepository(db, user.id).list_for_conversation(conv.id)]
    detail.message_count = len(detail.messages)
    return detail


@router.patch("/conversations/{conversation_id}", response_model=ConversationOut)
def update_conversation(conversation_id: int, body: ConversationUpdate, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> ConversationOut:
    repo = ConversationRepository(db, user.id)
    conv = repo.get_or_404(conversation_id)
    data = body.model_dump(exclude_unset=True)
    if data.get("project_id") is not None:
        ProjectRepository(db, user.id).get_or_404(data["project_id"])
    return _out(repo, repo.update(conv, **data))


@router.delete("/conversations/{conversation_id}", response_model=Deleted)
def delete_conversation(conversation_id: int, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> Deleted:
    repo = ConversationRepository(db, user.id)
    repo.delete(repo.get_or_404(conversation_id))
    return Deleted(id=conversation_id)


@router.delete("/conversations/{conversation_id}/messages")
def clear_conversation(conversation_id: int, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> dict[str, int]:
    ConversationRepository(db, user.id).get_or_404(conversation_id)
    return {"deleted_messages": MessageRepository(db, user.id).clear(conversation_id)}


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> ChatResponse:
    service = ChatService(db, user.id)
    prepared = await service.prepare(body)
    reply = await service.complete(prepared)
    user_msg = MessageRepository(db, user.id).get(prepared.user_message_id) if prepared.user_message_id else None
    return ChatResponse(conversation_id=prepared.conversation_id, model=prepared.model,
                        user_message=MessageOut.model_validate(user_msg) if user_msg else None,
                        assistant_message=MessageOut.model_validate(reply))


@router.post("/chat/stream")
async def chat_stream(body: ChatRequest, db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> StreamingResponse:
    service = ChatService(db, user.id)
    prepared = await service.prepare(body)  # validation errors are returned as normal JSON errors
    return StreamingResponse(service.stream(prepared), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/chat/{conversation_id}/regenerate")
async def regenerate(conversation_id: int, body: RegenerateRequest | None = None,
                     db: Session = Depends(get_db),
        user: User = Depends(current_user)) -> StreamingResponse:
    service = ChatService(db, user.id)
    prepared = await service.prepare_regenerate(conversation_id, body or RegenerateRequest())
    return StreamingResponse(service.stream(prepared), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/chat/{conversation_id}/stop")
def stop(conversation_id: int, db: Session = Depends(get_db),
         user: User = Depends(current_user)) -> dict[str, bool]:
    ConversationRepository(db, user.id).get_or_404(conversation_id)
    return {"stopping": request_stop(conversation_id)}
