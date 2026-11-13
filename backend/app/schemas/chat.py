"""Schemas for conversations, messages, chat and assistants."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel


class ConversationCreate(BaseModel):
    title: str | None = Field(None, max_length=300)
    project_id: int | None = None
    assistant_type: str = Field("research", pattern=r"^[a-z_]{2,50}$")
    assistant_id: int | None = None
    model_name: str | None = Field(None, max_length=200)


class ConversationUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    project_id: int | None = None
    assistant_id: int | None = None
    model_name: str | None = Field(None, max_length=200)


class MessageOut(ORMModel):
    id: int
    conversation_id: int
    role: str
    content: str
    model_name: str | None
    token_count: int | None
    status: str
    sources: list[dict[str, Any]] | None = None
    created_at: datetime

    @field_validator("sources", mode="before")
    @classmethod
    def _parse_sources(cls, value: Any) -> Any:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return None
        return value


class ConversationOut(ORMModel):
    id: int
    project_id: int | None
    title: str
    assistant_type: str
    assistant_id: int | None
    model_name: str | None
    created_at: datetime
    updated_at: datetime
    message_count: int | None = None


class ConversationDetail(ConversationOut):
    messages: list[MessageOut] = []


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=20000)
    conversation_id: int | None = None
    project_id: int | None = None
    assistant_id: int | None = None
    assistant_type: str = Field("research", pattern=r"^[a-z_]{2,50}$")
    model: str | None = Field(None, max_length=200)
    temperature: float | None = Field(None, ge=0.0, le=2.0)
    context_length: int | None = Field(None, ge=512, le=32768)
    max_tokens: int | None = Field(None, ge=32, le=8192)
    document_ids: list[int] | None = Field(None, max_length=20)

    @field_validator("message")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message must not be blank")
        return value


class RegenerateRequest(BaseModel):
    model: str | None = Field(None, max_length=200)
    temperature: float | None = Field(None, ge=0.0, le=2.0)
    context_length: int | None = Field(None, ge=512, le=32768)
    max_tokens: int | None = Field(None, ge=32, le=8192)
    assistant_id: int | None = None
    document_ids: list[int] | None = Field(None, max_length=20)


class ChatResponse(BaseModel):
    conversation_id: int
    model: str
    user_message: MessageOut | None
    assistant_message: MessageOut


class AssistantBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str | None = Field(None, max_length=1000)
    system_prompt: str = Field(..., min_length=1, max_length=8000)


class AssistantCreate(AssistantBase):
    is_default: bool = False


class AssistantUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = Field(None, max_length=1000)
    system_prompt: str | None = Field(None, min_length=1, max_length=8000)
    is_default: bool | None = None


class AssistantOut(ORMModel):
    id: int
    name: str
    description: str | None
    system_prompt: str
    is_default: bool
    created_at: datetime
    updated_at: datetime


class ModelSelect(BaseModel):
    model: str = Field(..., min_length=1, max_length=200)
