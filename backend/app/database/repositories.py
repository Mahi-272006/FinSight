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

from datetime import date, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.database.models import Company, FinancialFact, FinancialMetric
from app.ingestion.data_cleaner import NormalizedFact
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


class MetricRepository:
    def __init__(self, session: Session):
        self._session = session

    def upsert_metrics(self, cik: int, metrics: list[CalculatedMetric]) -> int:
        """Upsert calculated metrics (unique on company+metric+period).

        Re-running the engine overwrites in place: the table always holds the
        latest engine version per period. No duplicate-period corruption.
        """
        count = 0
        for m in metrics:
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
