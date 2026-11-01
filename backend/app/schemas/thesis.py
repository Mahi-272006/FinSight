"""Request and response models for the Thesis API."""

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, Field, StringConstraints

from app.schemas.api_company import PageResponse
from app.schemas.company import FiscalPeriod


Ticker = Annotated[
    str,
    Field(
        min_length=1,
        max_length=10,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9.-]{0,9}$",
        description="SEC ticker symbol.",
    ),
]
ConditionText = Annotated[str, StringConstraints(min_length=1, max_length=512)]


class ThesisCreateRequest(BaseModel):
    ticker: Ticker
    statement: Annotated[str, StringConstraints(min_length=1, max_length=10000)]
    horizon: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    conditions: Annotated[list[ConditionText], Field(min_length=1, max_length=50)]
    fiscal_year: int | None = Field(default=None, ge=1)
    fiscal_period: FiscalPeriod | None = None


class AssessmentRequest(BaseModel):
    """Whether to ask the optional LLM for explanatory wording."""

    interpret: bool = True


class ThesisConditionResponse(BaseModel):
    id: int
    description: str
    metric: str
    comparator: str
    threshold: float
    fiscal_year: int | None
    fiscal_period: str | None
    creation_evidence: dict[str, Any]


class ThesisListItemResponse(BaseModel):
    id: int
    ticker: str
    statement: str
    horizon: str
    status: str
    created_at: datetime


class ConditionEvaluationResponse(BaseModel):
    condition_id: int | None
    description: str
    metric: str
    comparator: str
    threshold: float
    verdict: str
    value: float | None
    unit: str | None
    tier: str | None
    fiscal_year: int | None
    fiscal_period: str | None
    formula: str | None
    accessions: list[str]
    reason: str


class ThesisAssessmentResponse(BaseModel):
    id: int
    assessed_at: datetime
    status: str
    status_changed_from: str | None
    summary: list[ConditionEvaluationResponse]
    explanation: str
    explanation_source: str
    signals: list[dict[str, Any]]


class ThesisResponse(BaseModel):
    id: int
    ticker: str
    statement: str
    horizon: str
    status: str
    created_at: datetime
    conditions: list[ThesisConditionResponse]
    assessments: list[ThesisAssessmentResponse]


class ThesisPageResponse(PageResponse[ThesisListItemResponse]):
    """Paginated thesis list."""


class ThesisAssessmentHistoryResponse(PageResponse[ThesisAssessmentResponse]):
    """Paginated append-only assessment history."""


class AssessmentResultResponse(BaseModel):
    assessment_id: int
    thesis_id: int
    status: str
    status_changed_from: str | None
    evaluations: list[ConditionEvaluationResponse]
    explanation: str
    explanation_source: str
    signals_context: list[dict[str, Any]]
