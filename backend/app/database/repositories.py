"""Repository layer: all database access for companies/facts/metrics.

Why a repository layer instead of raw ORM calls in services:
- Single place holding DB query knowledge. If the schema evolves, only
  repositories change -- services and the API stay untouched.
- Services stay testable: financial engine takes plain lists, no sessions.
- Consistent upsert semantics in one place (idempotent re-ingestion).

Upsert policy: re-running ingestion for the same company must UPDATE, not
duplicate. `merge` + explicit unique constraints give us that; without it,
running the ingest script twice would double every row -- a bug that would
silently corrupt every growth calculation (duplicated periods).
"""

import json
from datetime import date, datetime

from sqlalchemy import case, delete, func, select
from sqlalchemy.orm import Session

from app.database.models import (
    Company,
    FinancialFact,
    FinancialMetric,
    InvestigationSignal,
    ResearchReport,
    WatchlistEntry,
)
from app.ingestion.data_cleaner import NormalizedFact
from app.services.anomaly_detection import InvestigationSignal as SignalDataclass
from app.services.financial_engine import CalculatedMetric


class CompanyRepository:
    def __init__(self, session: Session):
        self._session = session

    def upsert(self, cik: int, ticker: str, name: str) -> Company:
        """Insert or update a company (idempotent)."""
        company = self._session.get(Company, cik)
        if company is None:
            company = Company(cik=cik, ticker=ticker, name=name)
            self._session.add(company)
        else:
            company.ticker = ticker
            company.name = name
        self._session.flush()
        return company

    def get(self, cik: int) -> Company | None:
        return self._session.get(Company, cik)

    def get_by_ticker(self, ticker: str) -> Company | None:
        stmt = select(Company).where(Company.ticker == ticker.upper())
        return self._session.execute(stmt).scalar_one_or_none()

    def list_all(self) -> list[Company]:
        return list(self._session.execute(select(Company).order_by(Company.ticker)).scalars())

    def count_all(self) -> int:
        return self._session.execute(select(func.count()).select_from(Company)).scalar_one()

    def list_page(self, limit: int, offset: int = 0) -> list[Company]:
        stmt = (
            select(Company)
            .order_by(Company.ticker, Company.cik)
            .offset(offset)
            .limit(limit)
        )
        return list(self._session.execute(stmt).scalars())


class FactRepository:
    def __init__(self, session: Session):
        self._session = session

    def replace_company_facts(self, cik: int, facts: list[NormalizedFact]) -> int:
        """Replace ALL facts for a company with a fresh normalized set.

        Full-replace (not incremental merge) is deliberate for M2:
        - Ingestion is idempotent: re-running always converges to the same
          state, regardless of how many times it ran before.
        - Simpler correctness story than incremental diffing; SEC data is
          re-fetched fresh each time, so there is nothing to preserve.

        Trade-off (documented): this loses per-run history of derived
        quarters. Raw filings remain on disk as the audit trail; restatement
        history is preserved via value_original/is_restated columns.
        """
        self._session.execute(delete(FinancialFact).where(FinancialFact.cik == cik))
        for fact in facts:
            self._session.add(
                FinancialFact(
                    cik=cik,
                    metric=fact.metric.value,
                    value=fact.value,
                    unit=fact.unit,
                    period_type=fact.period_type.value,
                    fiscal_year=fact.fiscal_year,
                    fiscal_period=fact.fiscal_period.value,
                    period_start=fact.period_start,
                    period_end=fact.period_end,
                    source_concept=fact.source_concept,
                    form=fact.form,
                    accession_number=fact.accession_number,
                    filed_at=fact.filed_at,
                    source_url=fact.source_url,
                    is_restated=fact.is_restated,
                    value_original=fact.value_original,
                )
            )
        self._session.flush()
        return len(facts)

    def get_facts(
        self,
        cik: int,
        metric: str | None = None,
        period_type: str | None = None,
    ) -> list[FinancialFact]:
        stmt = select(FinancialFact).where(FinancialFact.cik == cik)
        if metric:
            stmt = stmt.where(FinancialFact.metric == metric)
        if period_type:
            stmt = stmt.where(FinancialFact.period_type == period_type)
        stmt = stmt.order_by(FinancialFact.fiscal_year, FinancialFact.fiscal_period)
        return list(self._session.execute(stmt).scalars())

    def count_facts(
        self,
        cik: int,
        metric: str | None = None,
        period_type: str | None = None,
    ) -> int:
        stmt = select(func.count()).select_from(FinancialFact).where(
            FinancialFact.cik == cik
        )
        if metric:
            stmt = stmt.where(FinancialFact.metric == metric)
        if period_type:
            stmt = stmt.where(FinancialFact.period_type == period_type)
        return self._session.execute(stmt).scalar_one()

    def get_facts_page(
        self,
        cik: int,
        limit: int,
        offset: int = 0,
        metric: str | None = None,
        period_type: str | None = None,
    ) -> list[FinancialFact]:
        stmt = select(FinancialFact).where(FinancialFact.cik == cik)
        if metric:
            stmt = stmt.where(FinancialFact.metric == metric)
        if period_type:
            stmt = stmt.where(FinancialFact.period_type == period_type)
        stmt = stmt.order_by(
            FinancialFact.metric,
            FinancialFact.fiscal_year,
            FinancialFact.fiscal_period,
            FinancialFact.filed_at,
            FinancialFact.id,
        )
        stmt = stmt.offset(offset).limit(limit)
        return list(self._session.execute(stmt).scalars())


class MetricRepository:
    def __init__(self, session: Session):
        self._session = session

    def upsert_metrics(self, cik: int, metrics: list[CalculatedMetric]) -> int:
        """Upsert calculated metrics (unique on company+metric+period).

        Re-running the engine overwrites in place: the table always holds the
        latest engine version per period. No duplicate-period corruption.

        None-valued metrics are DROPPED, not stored. The engine emits
        value=None (with a `reason`) when inputs are missing or division is
        impossible -- that is a legitimate in-memory result, but persisting it
        would create rows every consumer must defensively filter (a NULL row
        carries no number, and its reason column isn't in the schema). Found
        460 such rows (14% of the table) after the M3 engine extension; the
        honest fix is to never write them.
        """
        count = 0
        for m in metrics:
            if m.value is None:
                continue
            stmt = select(FinancialMetric).where(
                FinancialMetric.cik == cik,
                FinancialMetric.metric == m.metric,
                FinancialMetric.fiscal_year == m.fiscal_year,
                FinancialMetric.fiscal_period == m.fiscal_period,
            )
            row = self._session.execute(stmt).scalar_one_or_none()
            if row is None:
                row = FinancialMetric(cik=cik, metric=m.metric)
                self._session.add(row)
            row.value = m.value
            row.unit = m.unit
            # fiscal_year/fiscal_period are part of the unique identity AND
            # NOT NULL columns -- forgetting them (caught by our DB tests,
            # which surfaced NOT NULL params in the error) would corrupt the
            # unique constraint and crash on PostgreSQL.
            row.fiscal_year = m.fiscal_year
            row.fiscal_period = m.fiscal_period
            row.period_type = m.period_type
            row.formula = m.formula
            row.inputs_json = m.inputs_json()
            row.source_accessions_json = m.accessions_json()
            # calculated_at is an ISO string in the dataclass but a DateTime
            # column in the DB -- parse it rather than passing the string
            # through (SQLite rejects strings; PostgreSQL would misbehave).
            row.calculated_at = datetime.fromisoformat(m.calculated_at)
            count += 1
        self._session.flush()
        return count

    def get_metrics(self, cik: int, metric: str | None = None) -> list[FinancialMetric]:
        stmt = select(FinancialMetric).where(FinancialMetric.cik == cik)
        if metric:
            stmt = stmt.where(FinancialMetric.metric == metric)
        stmt = stmt.order_by(FinancialMetric.fiscal_year, FinancialMetric.fiscal_period)
        return list(self._session.execute(stmt).scalars())

    def count_metrics(self, cik: int, metric: str | None = None) -> int:
        stmt = select(func.count()).select_from(FinancialMetric).where(
            FinancialMetric.cik == cik
        )
        if metric:
            stmt = stmt.where(FinancialMetric.metric == metric)
        return self._session.execute(stmt).scalar_one()

    def get_metrics_page(
        self, cik: int, limit: int, offset: int = 0, metric: str | None = None
    ) -> list[FinancialMetric]:
        stmt = select(FinancialMetric).where(FinancialMetric.cik == cik)
        if metric:
            stmt = stmt.where(FinancialMetric.metric == metric)
        period_order = case(
            (FinancialMetric.fiscal_period == "Q1", 1),
            (FinancialMetric.fiscal_period == "Q2", 2),
            (FinancialMetric.fiscal_period == "Q3", 3),
            (FinancialMetric.fiscal_period == "Q4", 4),
            (FinancialMetric.fiscal_period == "FY", 5),
            else_=6,
        )
        stmt = stmt.order_by(
            FinancialMetric.metric,
            FinancialMetric.fiscal_year,
            period_order,
            FinancialMetric.id,
        )
        stmt = stmt.offset(offset).limit(limit)
        return list(self._session.execute(stmt).scalars())


class SignalRepository:
    """Persist and query investigation signals (M3).

    Upsert keyed on (cik, signal_type, fiscal_year, fiscal_period) so
    re-running detection refreshes severity/evidence rather than piling up
    duplicates. A rule that stops firing (e.g. after the company improves)
    leaves its old row -- `replace_company_signals` handles full-refresh when
    the caller wants a clean slate per run.
    """

    def __init__(self, session: Session):
        self._session = session

    def upsert_signals(self, signals: list[SignalDataclass]) -> int:
        count = 0
        for s in signals:
            stmt = select(InvestigationSignal).where(
                InvestigationSignal.cik == s.cik,
                InvestigationSignal.signal_type == s.signal_type,
                InvestigationSignal.fiscal_year == s.fiscal_year,
                InvestigationSignal.fiscal_period == s.fiscal_period,
            )
            row = self._session.execute(stmt).scalar_one_or_none()
            if row is None:
                row = InvestigationSignal(cik=s.cik, signal_type=s.signal_type)
                self._session.add(row)
            row.ticker = s.ticker
            row.severity = s.severity
            row.fiscal_year = s.fiscal_year
            row.fiscal_period = s.fiscal_period
            row.period_type = s.period_type
            row.headline = s.headline
            row.explanation = s.explanation
            row.evidence_json = json.dumps(s.evidence)
            row.source_accessions_json = json.dumps(s.source_accessions)
            row.detected_at = datetime.fromisoformat(s.detected_at)
            count += 1
        self._session.flush()
        return count

    def replace_company_signals(self, cik: int, signals: list[SignalDataclass]) -> int:
        """Wipe a company's signals and insert the current set (full refresh)."""
        self._session.execute(delete(InvestigationSignal).where(InvestigationSignal.cik == cik))
        return self.upsert_signals(signals)

    def get_signals_for_company(self, cik: int) -> list[InvestigationSignal]:
        """Return a company's current signals in stable display order."""
        stmt = (
            select(InvestigationSignal)
            .where(InvestigationSignal.cik == cik)
            .order_by(
                InvestigationSignal.fiscal_year,
                InvestigationSignal.signal_type,
                InvestigationSignal.fiscal_period,
                InvestigationSignal.id,
            )
        )
        return list(self._session.execute(stmt).scalars())

    def get_signals(
        self, cik: int | None = None, signal_type: str | None = None
    ) -> list[InvestigationSignal]:
        stmt = select(InvestigationSignal)
        if cik is not None:
            stmt = stmt.where(InvestigationSignal.cik == cik)
        if signal_type:
            stmt = stmt.where(InvestigationSignal.signal_type == signal_type)
        stmt = stmt.order_by(
            InvestigationSignal.fiscal_year.desc(), InvestigationSignal.severity
        )
        return list(self._session.execute(stmt).scalars())

    def count_signals(self, cik: int, signal_type: str | None = None) -> int:
        stmt = select(func.count()).select_from(InvestigationSignal).where(
            InvestigationSignal.cik == cik
        )
        if signal_type:
            stmt = stmt.where(InvestigationSignal.signal_type == signal_type)
        return self._session.execute(stmt).scalar_one()

    def get_signals_page(
        self,
        cik: int,
        limit: int,
        offset: int = 0,
        signal_type: str | None = None,
    ) -> list[InvestigationSignal]:
        stmt = select(InvestigationSignal).where(InvestigationSignal.cik == cik)
        if signal_type:
            stmt = stmt.where(InvestigationSignal.signal_type == signal_type)
        stmt = stmt.order_by(
            InvestigationSignal.fiscal_year.desc(),
            InvestigationSignal.severity,
            InvestigationSignal.signal_type,
            InvestigationSignal.fiscal_period,
            InvestigationSignal.id,
        )
        stmt = stmt.offset(offset).limit(limit)
        return list(self._session.execute(stmt).scalars())


class ReportRepository:
    """Create and page durable research-result snapshots."""

    def __init__(self, session: Session):
        self._session = session

    def add(self, report: ResearchReport) -> ResearchReport:
        self._session.add(report)
        self._session.flush()
        return report

    def get(self, report_id: int) -> ResearchReport | None:
        return self._session.get(ResearchReport, report_id)

    def count_all(self) -> int:
        return self._session.execute(
            select(func.count()).select_from(ResearchReport)
        ).scalar_one()

    def list_page(self, limit: int, offset: int = 0) -> list[ResearchReport]:
        stmt = (
            select(ResearchReport)
            .order_by(
                ResearchReport.created_at.desc(),
                ResearchReport.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        )
        return list(self._session.execute(stmt).scalars())


class WatchlistRepository:
    """Persist the set of companies on the user's single local watchlist."""

    def __init__(self, session: Session):
        self._session = session

    def get(self, cik: int) -> WatchlistEntry | None:
        return self._session.get(WatchlistEntry, cik)

    def add(self, cik: int) -> WatchlistEntry:
        entry = WatchlistEntry(cik=cik)
        self._session.add(entry)
        self._session.flush()
        return entry

    def delete(self, entry: WatchlistEntry) -> None:
        self._session.delete(entry)
        self._session.flush()

    def count_all(self) -> int:
        return self._session.execute(
            select(func.count()).select_from(WatchlistEntry)
        ).scalar_one()

    def list_page(self, limit: int, offset: int = 0) -> list[WatchlistEntry]:
        stmt = (
            select(WatchlistEntry)
            .order_by(WatchlistEntry.created_at.desc(), WatchlistEntry.cik)
            .offset(offset)
            .limit(limit)
        )
        return list(self._session.execute(stmt).scalars())
