"""Read endpoints for persisted research reports."""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from app.database.postgres import get_session
from app.database.repositories import ReportRepository
from app.schemas.reports import (
    ReportDetailResponse,
    ReportPageResponse,
    ReportSummaryResponse,
)

router = APIRouter(prefix="/reports", tags=["reports"])

ReportId = Annotated[int, Path(gt=0, description="Positive report identifier.")]
PageLimit = Annotated[int, Query(ge=1, le=100, description="Maximum rows per page.")]
PageOffset = Annotated[int, Query(ge=0, description="Number of rows to skip.")]


def _summary(row) -> ReportSummaryResponse:
    return ReportSummaryResponse(
        id=row.id,
        cik=row.cik,
        ticker=row.ticker,
        question=row.question,
        thread_id=row.thread_id,
        created_at=row.created_at,
    )


@router.get("", response_model=ReportPageResponse)
def list_reports(
    session: Session = Depends(get_session),
    limit: PageLimit = 50,
    offset: PageOffset = 0,
):
    repository = ReportRepository(session)
    return ReportPageResponse(
        items=[_summary(row) for row in repository.list_page(limit, offset)],
        total=repository.count_all(),
        limit=limit,
        offset=offset,
    )


@router.get("/{report_id}", response_model=ReportDetailResponse)
def get_report(report_id: ReportId, session: Session = Depends(get_session)):
    row = ReportRepository(session).get(report_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Report not found.")
    return ReportDetailResponse(
        **_summary(row).model_dump(),
        result=json.loads(row.result_json),
    )
