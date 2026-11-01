"""Load annual metric history and transactionally recompute investigation signals."""

import json
import math
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.database.models import Company, FinancialMetric
from app.database.repositories import CompanyRepository, SignalRepository
from app.services.anomaly_detection import InvestigationSignal
from app.services.anomaly_orchestrator import CompanySeries, detect_signals

SUPPORTED_TICKERS = frozenset({"MSFT", "AAPL", "NVDA"})


class UnsupportedAnomalyTickerError(ValueError):
    """The requested ticker is outside this project's supported company set."""


class AnomalyCompanyNotFoundError(LookupError):
    """A supported company is not present in the database."""


class InsufficientAnomalyDataError(ValueError):
    """Stored annual metrics cannot evaluate any complete detector rule."""


class AnomalyRecomputeError(RuntimeError):
    """Detection or persistence failed; details are kept out of HTTP responses."""

    def __init__(self):
        super().__init__("Investigation signal recomputation failed.")


@dataclass(frozen=True)
class PersistedSignal:
    """Detached fields from a flushed signal row, safe after session close."""

    id: int
    cik: int
    ticker: str
    signal_type: str
    severity: str
    fiscal_year: int
    fiscal_period: str
    period_type: str
    headline: str
    explanation: str
    evidence_json: str
    source_accessions_json: str
    detected_at: datetime


def load_company_series(session: Session, company: Company) -> CompanySeries:
    """Build one company's annual series from stored calculated metrics.

    Quarterly and YTD rows are excluded because current detector rules operate
    on complete fiscal years. Invalid/non-finite values do not become inputs.
    Accessions are copied from the calculated-metric provenance unchanged.
    """
    rows = session.execute(
        select(FinancialMetric).where(FinancialMetric.cik == company.cik)
    ).scalars()

    annual: dict[str, dict[int, float]] = {}
    accessions: dict[str, dict[int, list[str]]] = {}

    for row in rows:
        if row.fiscal_period != "FY" or row.period_type != "annual":
            continue
        if row.value is None or not math.isfinite(row.value):
            continue
        annual.setdefault(row.metric, {})[row.fiscal_year] = row.value
        try:
            decoded = json.loads(row.source_accessions_json or "[]")
        except (TypeError, ValueError):
            decoded = []
        if isinstance(decoded, list):
            usable = [
                accession
                for accession in decoded
                if isinstance(accession, str) and accession
            ]
            if usable:
                accessions.setdefault(row.metric, {})[row.fiscal_year] = usable

    return CompanySeries(
        cik=company.cik,
        ticker=company.ticker,
        annual_metrics=annual,
        accessions=accessions,
    )


def has_evaluable_rule_inputs(series: CompanySeries) -> bool:
    """Require complete annual inputs for at least one real detector rule."""
    metrics = series.annual_metrics

    divergence_years = (
        set(metrics.get("earnings_cashflow_divergence", {}))
        & set(metrics.get("net_income_growth_yoy", {}))
        & set(metrics.get("operating_cash_flow_growth_yoy", {}))
    )
    if divergence_years:
        return True

    receivables_years = (
        set(metrics.get("accounts_receivable_growth_yoy", {}))
        & set(metrics.get("revenue_growth_yoy", {}))
    )
    if receivables_years:
        return True

    revenue_years = set(metrics.get("revenue_growth_yoy", {}))
    if any(year - 1 in revenue_years for year in revenue_years):
        return True

    margin_years = set(metrics.get("operating_income_margin", {}))
    if any(
        all(year + delta in margin_years for delta in range(4))
        for year in margin_years
    ):
        return True
    return False


class AnomalyService:
    """Run the deterministic detector and atomically replace a company's signals."""

    def __init__(self, session_factory: sessionmaker[Session]):
        self._session_factory = session_factory

    def recompute(self, ticker: str) -> tuple[int, str, list[PersistedSignal]]:
        normalized = ticker.upper()
        if normalized not in SUPPORTED_TICKERS:
            raise UnsupportedAnomalyTickerError()

        session = None
        try:
            session = self._session_factory()
            company = CompanyRepository(session).get_by_ticker(normalized)
            if company is None:
                raise AnomalyCompanyNotFoundError()

            series = load_company_series(session, company)
            if not has_evaluable_rule_inputs(series):
                raise InsufficientAnomalyDataError()

            detected = detect_signals(series)
            repository = SignalRepository(session)
            repository.replace_company_signals(company.cik, detected)
            persisted = [
                PersistedSignal(
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
                    evidence_json=row.evidence_json,
                    source_accessions_json=row.source_accessions_json,
                    detected_at=row.detected_at,
                )
                for row in repository.get_signals_for_company(company.cik)
            ]
            session.commit()
            return company.cik, company.ticker, persisted
        except (
            UnsupportedAnomalyTickerError,
            AnomalyCompanyNotFoundError,
            InsufficientAnomalyDataError,
        ):
            if session is not None:
                session.rollback()
            raise
        except Exception as exc:
            if session is not None:
                session.rollback()
            raise AnomalyRecomputeError() from exc
        finally:
            if session is not None:
                session.close()
