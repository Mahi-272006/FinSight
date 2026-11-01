"""Pydantic request/response models for the Companies API."""

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class PageResponse(BaseModel, Generic[T]):
    """Stable pagination envelope shared by company and financial collections."""

    items: list[T]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)


class CompanyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    cik: int
    ticker: str
    name: str
    fiscal_year_end_month: int | None
    created_at: datetime
    updated_at: datetime


class FinancialMetricResponse(BaseModel):
    id: int
    cik: int
    metric: str
    value: float | None
    unit: str
    fiscal_year: int
    fiscal_period: str
    period_type: str
    formula: str
    inputs: dict[str, Any]
    source_accessions: list[str]
    calculated_at: datetime


class FinancialFactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cik: int
    metric: str
    value: float
    unit: str
    period_type: str
    fiscal_year: int
    fiscal_period: str
    period_start: datetime | None
    period_end: datetime | None
    source_concept: str
    form: str
    accession_number: str
    filed_at: datetime | None
    source_url: str | None
    is_restated: bool
    value_original: float | None
    created_at: datetime
