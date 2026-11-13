"""Schemas for academic writing workflows."""

from __future__ import annotations

from pydantic import BaseModel, Field


class WritingRequest(BaseModel):
    task: str = Field(..., pattern=r"^[a-z_]{2,50}$")
    text: str = Field("", max_length=30000)
    context: str | None = Field(None, max_length=10000)
    project_id: int | None = None
    include_project_context: bool = False
    include_bibliography: bool = False
    output_format: str = Field("markdown", pattern=r"^(markdown|latex|plain)$")
    model: str | None = Field(None, max_length=200)
    temperature: float | None = Field(None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(None, ge=32, le=8192)
