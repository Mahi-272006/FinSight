"""Read-only API for persisted investigation signals."""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from app.database.postgres import get_session
from app.database.repositories import CompanyRepository, SignalRepository
from app.database.models import InvestigationSignal
from app.schemas.api_anomaly import (
    InvestigationSignalPage,
    InvestigationSignalResponse,
    SignalType,
)

router = APIRouter(prefix="/anomalies", tags=["anomalies"])

Ticker = Annotated[
    str,
    Path(
        min_length=1,
        max_length=10,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9.-]{0,9}$",
        description="SEC ticker symbol.",
    ),
]
PageLimit = Annotated[int, Query(ge=1, le=100, description="Maximum rows per page.")]
PageOffset = Annotated[int, Query(ge=0, description="Number of rows to skip.")]


def _signal_response(row: InvestigationSignal) -> InvestigationSignalResponse:
    """Decode the stored evidence while preserving its values and accessions."""
    evidence = json.loads(row.evidence_json) if row.evidence_json else {}
    accessions = (
        json.loads(row.source_accessions_json) if row.source_accessions_json else []
    )
    return InvestigationSignalResponse(
        id=row.id,
        cik=row.cik,
        ticker=row.ticker,
        signal_type=row.signal_type,
        severity=row.severity,
        fiscal_year=row.fiscal_year,
        fiscal_period=row.fiscal_period,
        period_type=row.period_type,
        headline=row.headline,
        explanation=row.explanation,
        evidence=evidence if isinstance(evidence, dict) else {},
        source_accessions=accessions if isinstance(accessions, list) else [],
        detected_at=row.detected_at,
    )


@router.get("/{ticker}", response_model=InvestigationSignalPage)
def list_company_signals(
    ticker: Ticker,
    session: Session = Depends(get_session),
    signal_type: SignalType | None = None,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
):
    """List persisted signals for a company; detection is never run here."""
    company = CompanyRepository(session).get_by_ticker(ticker.upper())
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found.")

    signal_name = signal_type.value if signal_type is not None else None
    repository = SignalRepository(session)
    total = repository.count_signals(company.cik, signal_type=signal_name)
    if signal_name is not None and total == 0:
        raise HTTPException(
            status_code=404,
            detail="No investigation signals found for this signal type.",
        )
    rows = repository.get_signals_page(
        company.cik,
        limit=limit,
        offset=offset,
        signal_type=signal_name,
    )
    return InvestigationSignalPage(
        items=[_signal_response(row) for row in rows],
        total=total,
        offset=offset,
        limit=limit,
    )
