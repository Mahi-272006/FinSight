"""Tests for the M6 research tools (retrieval only, injected dependencies).

SQLite-in-memory for DB tools (M2's portable-schema pattern) and pure fakes
for retrieval -- no Chroma, no model, no network.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import (
    Base,
    Company,
    FinancialFact,
    FinancialMetric,
    InvestigationSignal,
)
from app.graph.tools import (
    make_get_financial_facts,
    make_get_investigation_signals,
    make_search_filing_sections,
)
from app.services.vector_store import SearchHit

ACCESSION = "0001045810-26-000075"


# ---------------------------------------------------------------------------
# Fakes / fixtures
# ---------------------------------------------------------------------------


class FakeEmbedder:
    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


class FakeStore:
    def __init__(self, hits):
        self.hits = hits
        self.calls = []

    def search(self, query_vector, k=5, where=None):
        self.calls.append({"k": k, "where": where})
        return self.hits[:k]


def _hit(text, *, ident=f"{ACCESSION}:0", section="financial_statements"):
    return SearchHit(
        id=ident,
        text=text,
        metadata={
            "ticker": "NVDA", "form": "10-Q", "accession": ACCESSION,
            "section": section, "filing_date": "2026-08-26",
        },
        distance=0.21,
    )


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    session.add(Company(cik=1045810, ticker="NVDA", name="NVIDIA Corp",
                        fiscal_year_end_month=1))
    session.add(FinancialFact(
        cik=1045810, metric="revenue", value=46_743_000_000.0, unit="USD",
        period_type="quarterly", fiscal_year=2026, fiscal_period="Q2",
        source_concept="RevenueFromContractWithCustomerExcludingAssessedTax",
        form="10-Q", accession_number=ACCESSION,
        filed_at=datetime(2026, 8, 26, tzinfo=timezone.utc),
    ))
    session.add(FinancialFact(
        cik=1045810, metric="net_income", value=26_000_000_000.0, unit="USD",
        period_type="quarterly", fiscal_year=2026, fiscal_period="Q2",
        source_concept="NetIncomeLoss", form="10-Q", accession_number=ACCESSION,
        filed_at=datetime(2026, 8, 26, tzinfo=timezone.utc),
        is_restated=True, value_original=25_000_000_000.0,
    ))
    session.add(FinancialMetric(
        cik=1045810, metric="operating_margin", value=0.563, unit="ratio",
        fiscal_year=2026, fiscal_period="Q2", period_type="quarterly",
        formula="operating_margin", inputs_json="{}",
        source_accessions_json=f'["{ACCESSION}"]',
    ))
    session.add(InvestigationSignal(
        cik=1045810, ticker="NVDA", signal_type="revenue_growth_step_change",
        severity="elevated", fiscal_year=2026, fiscal_period="Q2",
        period_type="quarterly",
        headline="Revenue growth step change detected",
        explanation="YoY growth moved above the historical band.",
        evidence_json='{"z_score": 2.4}',
        source_accessions_json=f'["{ACCESSION}"]',
    ))
    session.commit()
    session.close()
    try:
        yield factory
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# search_filing_sections
# ---------------------------------------------------------------------------


class TestSearchFilingSections:
    def test_output_carries_chunk_id_and_provenance(self):
        store = FakeStore([_hit("Export controls constrain sales.")])
        tool = make_search_filing_sections(store, FakeEmbedder())
        result = tool.invoke({"ticker": "nvda", "query": "export controls"})
        assert f"chunk_id: {ACCESSION}:0" in result
        assert "NVDA 10-Q" in result
        assert "section:financial_statements" in result
        assert "Export controls constrain sales." in result

    def test_ticker_uppercased_and_section_filter_forwarded(self):
        store = FakeStore([])
        tool = make_search_filing_sections(store, FakeEmbedder())
        tool.invoke({"ticker": "nvda", "query": "q", "section": "risk_factors", "k": 3})
        assert store.calls[0]["where"] == {"ticker": "NVDA", "section": "risk_factors"}
        assert store.calls[0]["k"] == 3

    def test_k_clamped_to_eight(self):
        store = FakeStore([])
        tool = make_search_filing_sections(store, FakeEmbedder())
        tool.invoke({"ticker": "NVDA", "query": "q", "k": 500})
        assert store.calls[0]["k"] == 8

    def test_no_results_message(self):
        tool = make_search_filing_sections(FakeStore([]), FakeEmbedder())
        result = tool.invoke({"ticker": "NVDA", "query": "q", "section": "mdna"})
        assert "No filing excerpts" in result
        assert "mdna" in result


# ---------------------------------------------------------------------------
# get_financial_facts
# ---------------------------------------------------------------------------


class TestGetFinancialFacts:
    def test_verified_fact_with_provenance(self, session_factory):
        tool = make_get_financial_facts(session_factory)
        result = tool.invoke({"ticker": "NVDA", "metric": "revenue"})
        assert "VERIFIED FACT revenue FY2026 Q2" in result
        assert "46,743,000,000 USD" in result
        assert f"[10-Q {ACCESSION}" in result

    def test_restated_fact_flags_original_value(self, session_factory):
        tool = make_get_financial_facts(session_factory)
        result = tool.invoke({"ticker": "NVDA", "metric": "net_income"})
        assert "RESTATED" in result
        assert "25,000,000,000" in result

    def test_calculated_metric_includes_formula(self, session_factory):
        tool = make_get_financial_facts(session_factory)
        result = tool.invoke({"ticker": "NVDA", "metric": "operating_margin"})
        assert "CALCULATED METRIC operating_margin" in result
        assert "formula: operating_margin" in result

    def test_unknown_ticker_is_honest(self, session_factory):
        tool = make_get_financial_facts(session_factory)
        result = tool.invoke({"ticker": "ZZZZ"})
        assert "Unknown ticker" in result

    def test_no_match_says_so(self, session_factory):
        tool = make_get_financial_facts(session_factory)
        result = tool.invoke({"ticker": "NVDA", "metric": "revenue", "fiscal_year": 1999})
        assert "no facts matched" in result


# ---------------------------------------------------------------------------
# get_investigation_signals
# ---------------------------------------------------------------------------


class TestGetInvestigationSignals:
    def test_disclaimer_first_then_signal(self, session_factory):
        tool = make_get_investigation_signals(session_factory)
        result = tool.invoke({"ticker": "NVDA"})
        lines = result.splitlines()
        assert lines[0].startswith("NOTE: these are investigation signals")
        assert "SIGNAL revenue_growth_step_change severity:elevated" in result
        assert '"z_score": 2.4' in result

    def test_no_signals_message(self, session_factory):
        tool = make_get_investigation_signals(session_factory)
        result = tool.invoke({"ticker": "MSFT"})
        assert "No recorded investigation signals for MSFT" in result
