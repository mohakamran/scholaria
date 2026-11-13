"""Schemas for projects, notes and citations."""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel

DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")


class ProjectBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    description: str | None = Field(None, max_length=20000)
    objectives: str | None = Field(None, max_length=20000)
    research_questions: str | None = Field(None, max_length=20000)
    hypotheses: str | None = Field(None, max_length=20000)


class ProjectCreate(ProjectBase):
    pass


class ProjectUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    description: str | None = Field(None, max_length=20000)
    objectives: str | None = Field(None, max_length=20000)
    research_questions: str | None = Field(None, max_length=20000)
    hypotheses: str | None = Field(None, max_length=20000)


class ProjectOut(ORMModel, ProjectBase):
    id: int
    created_at: datetime
    updated_at: datetime
    counts: dict[str, int] | None = None


class NoteCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    content: str = Field("", max_length=200000)


class NoteUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    content: str | None = Field(None, max_length=200000)


class NoteOut(ORMModel):
    id: int
    project_id: int
    title: str
    content: str
    created_at: datetime
    updated_at: datetime


class CitationBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    authors: str | None = Field(None, max_length=5000)
    publication_year: int | None = Field(None, ge=1400, le=2100)
    journal: str | None = Field(None, max_length=300)
    doi: str | None = Field(None, max_length=200)
    url: str | None = Field(None, max_length=1000)
    notes: str | None = Field(None, max_length=20000)

    @field_validator("doi")
    @classmethod
    def _doi(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", value.strip(), flags=re.IGNORECASE)
        if not DOI_RE.match(value):
            raise ValueError("DOI must look like 10.xxxx/xxxxx")
        return value

    @field_validator("url")
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        if not re.match(r"^https?://", value.strip()):
            raise ValueError("URL must start with http:// or https://")
        return value.strip()


class CitationCreate(CitationBase):
    pass


class CitationUpdate(CitationBase):
    title: str | None = Field(None, min_length=1, max_length=500)  # type: ignore[assignment]


class CitationOut(ORMModel):
    id: int
    project_id: int
    title: str
    authors: str | None
    publication_year: int | None
    journal: str | None
    doi: str | None
    url: str | None
    notes: str | None
    created_at: datetime


class LinkRequest(BaseModel):
    project_id: int | None = None
