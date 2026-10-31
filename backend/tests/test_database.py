"""Database + repository tests (M2).

Runs on SQLite in-memory for speed and zero setup; production uses
PostgreSQL via the same SQLAlchemy models. Only portable column types are
used in models.py specifically so this is possible -- a deliberate design
constraint documented there.

These tests verify the DATA-INTEGRITY guarantees the whole system depends on:
- idempotent re-ingestion (no duplicate rows / no corrupted growth math)
- upsert semantics for companies and metrics
- restatement flags persist correctly
"""

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.database.models import Base, Company, FinancialFact, FinancialMetric
from app.database.repositories import CompanyRepository, FactRepository, MetricRepository
from app.ingestion.data_cleaner import NormalizedFact
from app.schemas.company import FiscalPeriod, Metric, PeriodType
from app.services.financial_engine import CalculatedMetric


@pytest.fixture
def session():
    """In-memory SQLite session; schema created/destroyed per test."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    s = factory()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def make_normalized_fact(
    metric: Metric = Metric.REVENUE,
    value: float = 100.0,
    fy: int = 2024,
    fp: FiscalPeriod = FiscalPeriod.FY,
    pt: PeriodType = PeriodType.ANNUAL,
    accn: str = "ACC-1",
    is_restated: bool = False,
    value_original: float | None = None,
) -> NormalizedFact:
    return NormalizedFact(
        cik=789019, metric=metric, value=value, unit="USD",
        period_type=pt, fiscal_year=fy, fiscal_period=fp,
        period_start=date(fy - 1, 7, 1) if pt is PeriodType.ANNUAL else None,
        period_end=date(fy, 6, 30),
        source_concept="TestConcept", form="10-K",
        accession_number=accn, filed_at=date(fy, 7, 30),
        source_url=f"https://www.sec.gov/Archives/edgar/data/789019/{accn}/",
        is_restated=is_restated, value_original=value_original,
    )


class TestCompanyRepository:
    def test_upsert_inserts_then_updates(self, session):
        repo = CompanyRepository(session)
        c = repo.upsert(789019, "MSFT", "MICROSOFT CORP")
        assert c.ticker == "MSFT"
        # Re-upsert with new name: must UPDATE, not create a second row.
        repo.upsert(789019, "MSFT", "MICROSOFT CORPORATION")
        all_companies = repo.list_all()
        assert len(all_companies) == 1
        assert all_companies[0].name == "MICROSOFT CORPORATION"

    def test_get_by_ticker_case_insensitive(self, session):
        repo = CompanyRepository(session)
        repo.upsert(789019, "MSFT", "MICROSOFT CORP")
        assert repo.get_by_ticker("msft") is not None
        assert repo.get_by_ticker("aapl") is None


class TestFactRepository:
    def test_replace_is_idempotent(self, session):
        """Re-ingesting the same facts must not duplicate rows.

        This is the guarantee that prevents double-counted periods from
        corrupting every downstream growth calculation.
        """
        repo = FactRepository(session)
        facts = [
            make_normalized_fact(Metric.REVENUE, 100.0, 2023),
            make_normalized_fact(Metric.REVENUE, 120.0, 2024),
        ]
        repo.replace_company_facts(789019, facts)
        assert len(repo.get_facts(789019)) == 2

        # Run again (simulating a re-ingest).
        repo.replace_company_facts(789019, facts)
        assert len(repo.get_facts(789019)) == 2  # still 2, not 4

    def test_replace_removes_stale_facts(self, session):
        """A fresh normalization that drops a period must drop the DB row too
        (e.g. a restated value now rejected by sign-sanity checks)."""
        repo = FactRepository(session)
        repo.replace_company_facts(789019, [
            make_normalized_fact(Metric.REVENUE, 100.0, 2023),
            make_normalized_fact(Metric.REVENUE, 120.0, 2024),
        ])
        repo.replace_company_facts(789019, [
            make_normalized_fact(Metric.REVENUE, 100.0, 2023),
        ])
        facts = repo.get_facts(789019)
        assert len(facts) == 1
        assert facts[0].fiscal_year == 2023

    def test_restatement_flags_persist(self, session):
        repo = FactRepository(session)
        repo.replace_company_facts(789019, [
            make_normalized_fact(
                Metric.REVENUE, 105.0, 2024,
                is_restated=True, value_original=100.0,
            )
        ])
        fact = repo.get_facts(789019, metric="revenue")[0]
        assert fact.is_restated is True
        assert fact.value_original == 100.0
        assert fact.value == 105.0

    def test_filter_by_metric_and_period_type(self, session):
        repo = FactRepository(session)
        repo.replace_company_facts(789019, [
            make_normalized_fact(Metric.REVENUE, 100.0, 2024),
            make_normalized_fact(
                Metric.TOTAL_ASSETS, 1000.0, 2024, pt=PeriodType.INSTANT,
                fp=FiscalPeriod.FY,
            ),
        ])
        instants = repo.get_facts(789019, period_type="instant")
        assert len(instants) == 1
        assert instants[0].metric == "total_assets"


class TestMetricRepository:
    def test_upsert_overwrites_same_period(self, session):
        """Recalculating a period overwrites (no duplicate corruption)."""
        repo = MetricRepository(session)

        def make_metric(value: float) -> CalculatedMetric:
            return CalculatedMetric(
                cik=789019, metric="revenue_growth_yoy", value=value, unit="ratio",
                fiscal_year=2024, fiscal_period="FY", period_type="annual",
                formula="(current - prior) / abs(prior)",
                inputs={"current": 120.0, "prior": 100.0},
                source_accessions=["A1", "A2"],
            )

        repo.upsert_metrics(789019, [make_metric(0.20)])
        repo.upsert_metrics(789019, [make_metric(0.21)])  # engine v2 recalc

        rows = repo.get_metrics(789019, metric="revenue_growth_yoy")
        assert len(rows) == 1  # still one row
        assert rows[0].value == 0.21  # latest value wins

    def test_provenance_columns_survive(self, session):
        repo = MetricRepository(session)
        repo.upsert_metrics(789019, [CalculatedMetric(
            cik=789019, metric="operating_income_margin", value=0.456, unit="ratio",
            fiscal_year=2025, fiscal_period="FY", period_type="annual",
            formula="operating_income / revenue",
            inputs={"operating_income": 128528.0, "revenue": 281724.0},
            source_accessions=["0000950170-25-100235"],
        )])
        row = repo.get_metrics(789019, metric="operating_income_margin")[0]
        assert row.formula == "operating_income / revenue"
        assert "operating_income" in row.inputs_json
        assert "0000950170-25-100235" in row.source_accessions_json
