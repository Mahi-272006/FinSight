"""Pydantic models for persisted investigation-signal API responses."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.schemas.api_company import PageResponse
from app.schemas.company import SignalType


class InvestigationSignalResponse(BaseModel):
    """Stored investigation signal with its original evidence and provenance."""

    id: int
    cik: int
    ticker: str
    # The database column intentionally allows future signal implementations;
    # only request filters are constrained to the detector's current enum.
    signal_type: str
    severity: str
    fiscal_year: int
    fiscal_period: str
    period_type: str
    headline: str
    explanation: str
    evidence: dict[str, Any]
    source_accessions: list[str]
    detected_at: datetime


class InvestigationSignalPage(PageResponse[InvestigationSignalResponse]):
    """Paginated investigation signal response."""
