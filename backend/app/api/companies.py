"""Read-only company and financial-data endpoints."""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from app.database.postgres import get_session
from app.database.repositories import CompanyRepository, FactRepository, MetricRepository
from app.database.models import FinancialMetric
from app.schemas.api_company import (
    CompanyResponse,
    FinancialFactResponse,
    FinancialMetricResponse,
    PageResponse,
)
from app.schemas.company import CalculatedMetricName, Metric, PeriodType

router = APIRouter(prefix="/companies", tags=["companies"])

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


def _company_or_404(session: Session, ticker: str):
    company = CompanyRepository(session).get_by_ticker(ticker.upper())
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found.")
    return company


def _metric_response(row: FinancialMetric) -> FinancialMetricResponse:
    """Expose stored formula inputs and source accessions without recomputing."""
    return FinancialMetricResponse(
        id=row.id,
        cik=row.cik,
        metric=row.metric,
        value=row.value,
        unit=row.unit,
        fiscal_year=row.fiscal_year,
        fiscal_period=row.fiscal_period,
        period_type=row.period_type,
        formula=row.formula,
        inputs=json.loads(row.inputs_json),
        source_accessions=json.loads(row.source_accessions_json),
        calculated_at=row.calculated_at,
    )


@router.get("", response_model=PageResponse[CompanyResponse])
def list_companies(
    session: Session = Depends(get_session),
    limit: PageLimit = 50,
    offset: PageOffset = 0,
):
    """List companies present in the database, ordered by ticker."""
    repository = CompanyRepository(session)
    return PageResponse[CompanyResponse](
        items=repository.list_page(limit=limit, offset=offset),
        total=repository.count_all(),
        offset=offset,
        limit=limit,
    )


@router.get("/{ticker}", response_model=CompanyResponse)
def get_company(ticker: Ticker, session: Session = Depends(get_session)):
    """Return a company's persisted identity and fiscal-year metadata."""
    return _company_or_404(session, ticker)


@router.get("/{ticker}/metrics", response_model=PageResponse[FinancialMetricResponse])
def list_company_metrics(
    ticker: Ticker,
    session: Session = Depends(get_session),
    metric: CalculatedMetricName | None = None,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
):
    """List stored calculated metrics; optional metric values are validated."""
    company = _company_or_404(session, ticker)
    metric_name = metric.value if metric is not None else None
    repository = MetricRepository(session)
    total = repository.count_metrics(company.cik, metric=metric_name)
    if metric_name is not None and total == 0:
        raise HTTPException(
            status_code=404, detail="No financial data found for this metric."
        )
    rows = repository.get_metrics_page(
        company.cik, limit=limit, offset=offset, metric=metric_name
    )
    return PageResponse[FinancialMetricResponse](
        items=[_metric_response(row) for row in rows],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get(
    "/{ticker}/metrics/{metric}/history",
    response_model=PageResponse[FinancialMetricResponse],
)
def get_metric_history(
    ticker: Ticker,
    metric: CalculatedMetricName,
    session: Session = Depends(get_session),
    limit: PageLimit = 100,
    offset: PageOffset = 0,
):
    """Return the stored fiscal-period series for one supported metric."""
    company = _company_or_404(session, ticker)
    metric_name = metric.value
    repository = MetricRepository(session)
    total = repository.count_metrics(company.cik, metric=metric_name)
    if total == 0:
        raise HTTPException(
            status_code=404, detail="No financial history found for this metric."
        )
    rows = repository.get_metrics_page(
        company.cik, limit=limit, offset=offset, metric=metric_name
    )
    return PageResponse[FinancialMetricResponse](
        items=[_metric_response(row) for row in rows],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/{ticker}/facts", response_model=PageResponse[FinancialFactResponse])
def list_company_facts(
    ticker: Ticker,
    session: Session = Depends(get_session),
    metric: Metric | None = None,
    period_type: PeriodType | None = None,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
):
    """List as-filed facts, including accession and restatement provenance."""
    company = _company_or_404(session, ticker)
    metric_name = metric.value if metric is not None else None
    period_name = period_type.value if period_type is not None else None
    repository = FactRepository(session)
    total = repository.count_facts(
        company.cik, metric=metric_name, period_type=period_name
    )
    if (metric_name is not None or period_name is not None) and total == 0:
        raise HTTPException(
            status_code=404, detail="No financial facts found for the requested filter."
        )
    return PageResponse[FinancialFactResponse](
        items=repository.get_facts_page(
            company.cik,
            limit=limit,
            offset=offset,
            metric=metric_name,
            period_type=period_name,
        ),
        total=total,
        offset=offset,
        limit=limit,
    )
