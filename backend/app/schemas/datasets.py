"""Schemas for datasets and analyses."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel

AnalysisType = Literal[
    "overview", "descriptive", "missing", "duplicates", "frequencies", "correlation", "regression",
    "histogram", "scatter",
]


class DatasetOut(ORMModel):
    id: int
    project_id: int | None
    filename: str
    file_type: str
    file_size: int
    row_count: int
    column_count: int
    created_at: datetime


class AnalyzeRequest(BaseModel):
    analysis_type: AnalysisType
    columns: list[str] | None = Field(None, max_length=200)
    dependent: str | None = Field(None, max_length=300)
    independents: list[str] | None = Field(None, max_length=50)
    method: Literal["pearson", "spearman", "kendall"] = "pearson"
    bins: int = Field(20, ge=2, le=100)
    top_n: int = Field(30, ge=1, le=200)
    x: str | None = Field(None, max_length=300)
    y: str | None = Field(None, max_length=300)


class AnalysisRunOut(ORMModel):
    id: int
    dataset_id: int
    analysis_type: str
    parameters: dict[str, Any]
    results: dict[str, Any]
    created_at: datetime

    @field_validator("parameters", "results", mode="before")
    @classmethod
    def _json(cls, value: Any) -> Any:
        return json.loads(value) if isinstance(value, str) else value


class InterpretRequest(BaseModel):
    model: str | None = Field(None, max_length=200)
    question: str | None = Field(None, max_length=2000)
