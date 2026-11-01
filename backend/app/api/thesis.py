"""Investment-thesis API backed by the transaction-owning ThesisService."""

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status

from app.api.dependencies import get_thesis_service
from app.schemas.thesis import (
    AssessmentRequest,
    AssessmentResultResponse,
    ConditionEvaluationResponse,
    ThesisAssessmentHistoryResponse,
    ThesisCreateRequest,
    ThesisPageResponse,
    ThesisResponse,
)
from app.services.thesis_service import ThesisService

router = APIRouter(prefix="/theses", tags=["theses"])

ThesisId = Annotated[int, Path(gt=0, description="Positive thesis identifier.")]
PageLimit = Annotated[int, Query(ge=1, le=100, description="Maximum rows per page.")]
PageOffset = Annotated[int, Query(ge=0, description="Number of rows to skip.")]


@router.post("", response_model=ThesisResponse, status_code=status.HTTP_201_CREATED)
def create_thesis(
    payload: ThesisCreateRequest,
    service: ThesisService = Depends(get_thesis_service),
):
    """Create a thesis and immutable condition snapshots."""
    thesis_id = service.create_thesis(
        ticker=payload.ticker,
        statement=payload.statement,
        horizon=payload.horizon,
        condition_texts=payload.conditions,
        fiscal_year=payload.fiscal_year,
        fiscal_period=(payload.fiscal_period.value if payload.fiscal_period else None),
    )
    return service.get_thesis(thesis_id)


@router.get("", response_model=ThesisPageResponse)
def list_theses(
    service: ThesisService = Depends(get_thesis_service),
    limit: PageLimit = 50,
    offset: PageOffset = 0,
):
    """List theses in stable creation order with limit/offset pagination."""
    return service.list_theses_page(limit=limit, offset=offset)


@router.get("/{thesis_id}", response_model=ThesisResponse)
def get_thesis(
    thesis_id: ThesisId,
    service: ThesisService = Depends(get_thesis_service),
):
    """Retrieve thesis details, conditions, and its assessment history."""
    return service.get_thesis(thesis_id)


@router.post(
    "/{thesis_id}/assess",
    response_model=AssessmentResultResponse,
)
def assess_thesis(
    thesis_id: ThesisId,
    payload: AssessmentRequest | None = None,
    service: ThesisService = Depends(get_thesis_service),
):
    """Assess deterministic conditions; optional LLM output is explanatory."""
    result = service.assess(
        thesis_id,
        interpret=payload.interpret if payload is not None else True,
    )
    return AssessmentResultResponse(
        assessment_id=result.assessment_id,
        thesis_id=result.thesis_id,
        status=result.status,
        status_changed_from=result.status_changed_from,
        evaluations=[
            ConditionEvaluationResponse(**asdict(evaluation))
            for evaluation in result.evaluations
        ],
        explanation=result.explanation,
        explanation_source=result.explanation_source,
        signals_context=list(result.signals_context),
    )


@router.get(
    "/{thesis_id}/assessments",
    response_model=ThesisAssessmentHistoryResponse,
)
def get_assessment_history(
    thesis_id: ThesisId,
    service: ThesisService = Depends(get_thesis_service),
    limit: PageLimit = 50,
    offset: PageOffset = 0,
):
    """Read an append-only assessment history page."""
    return service.get_assessment_history_page(
        thesis_id=thesis_id,
        limit=limit,
        offset=offset,
    )
