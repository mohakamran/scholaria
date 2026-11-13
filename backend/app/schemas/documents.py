"""Schemas for documents and literature tasks."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel


class DocumentOut(ORMModel):
    id: int
    project_id: int | None
    filename: str
    file_type: str
    file_size: int
    file_hash: str
    processing_status: str
    title: str | None
    page_count: int | None
    chunk_count: int | None
    char_count: int | None
    error_message: str | None
    metadata: dict[str, Any] | None = Field(None, validation_alias="metadata_json")
    created_at: datetime

    @field_validator("metadata", mode="before")
    @classmethod
    def _parse(cls, value: Any) -> Any:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return None
        return value


class DocumentUpdate(BaseModel):
    project_id: int | None = None
    title: str | None = Field(None, min_length=1, max_length=500)


class AskRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=4000)
    model: str | None = Field(None, max_length=200)
    top_k: int | None = Field(None, ge=1, le=12)
    temperature: float | None = Field(None, ge=0.0, le=2.0)
    additional_document_ids: list[int] | None = Field(None, max_length=20)


class SourceRef(BaseModel):
    label: str
    document_id: int
    document_title: str
    page_number: int | None
    chunk_index: int
    excerpt: str
    score: float | None = None


class AskResponse(BaseModel):
    answer: str
    sources: list[SourceRef]
    retrieved_passages: int
    model: str | None
    grounded: bool
    note: str


ExtractionTask = Literal[
    "summary", "objectives", "research_questions", "theoretical_framework", "methodology",
    "sample", "variables", "findings", "limitations", "future_research",
]


class SummarizeRequest(BaseModel):
    task: ExtractionTask = "summary"
    model: str | None = Field(None, max_length=200)
    full_scan: bool | None = Field(
        None, description="Process every chunk (map-reduce). Defaults to true for 'summary' only."
    )


class SummarizeResponse(BaseModel):
    task: str
    result: str
    model: str | None
    chunks_processed: int
    total_chunks: int
    coverage: Literal["full", "partial"]
    pages_referenced: list[int]
    removed_invalid_page_refs: int
    note: str


class ChunkOut(ORMModel):
    id: int
    chunk_index: int
    page_number: int | None
    content: str
