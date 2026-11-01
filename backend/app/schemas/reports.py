"""Persisted research report response schemas."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ReportSummaryResponse(BaseModel):
    id: int
    cik: int
    ticker: str
    question: str
    thread_id: str
    created_at: datetime


class ReportDetailResponse(ReportSummaryResponse):
    result: dict[str, Any]


class ReportPageResponse(BaseModel):
    items: list[ReportSummaryResponse]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
