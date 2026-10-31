"""Tests for the financial engine (M2).

Every expected value here is HAND-COMPUTED (shown in comments) -- this is the
golden-value approach required for a financial system: if the engine's output
ever drifts, these tests fail before bad numbers reach users.

Run: pytest tests/test_financial_engine.py -v
"""

from datetime import date

from app.ingestion.data_cleaner import NormalizedFact
from app.services.financial_engine import (
    calculate_all_metrics,
    current_ratio,
    debt_to_assets,
    earnings_cashflow_divergence,
    free_cash_flow,
    growth_rate,
    margin,
    return_on_assets,
)
from app.schemas.company import FiscalPeriod, Metric, PeriodType


def make_fact(
    metric: Metric,
    value: float,
    fy: int,
    fp: str = "FY",
    pt: PeriodType = PeriodType.ANNUAL,
    accn: str = "ACC-001",
) -> NormalizedFact:
    return NormalizedFact(
        cik=789019,
        metric=metric,
        value=value,
        unit="USD",
        period_type=pt,
        fiscal_year=fy,
        fiscal_period=FiscalPeriod(fp),
        period_start=date(fy - 1, 7, 1) if pt is PeriodType.ANNUAL else None,
        period_end=date(fy, 6, 30),
        source_concept="TestConcept",
        form="10-K",
        accession_number=accn,
        filed_at=date(fy, 7, 30),
    )


def build_index(facts: list[NormalizedFact]):
    from app.services.financial_engine import index_facts

    return index_facts(facts)


# --------------------------------------------------------------------------- #
# Growth rates (hand-verified)
# --------------------------------------------------------------------------- #


class TestGrowthRate:
    def test_positive_growth(self):
        # 120 vs 100 -> (120-100)/100 = 0.20
        index = build_index([
            make_fact(Metric.REVENUE, 100.0, 2023),
            make_fact(Metric.REVENUE, 120.0, 2024),
        ])
        m = growth_rate(index, Metric.REVENUE, 789019, 2024, "FY", PeriodType.ANNUAL, 2023)
        assert m.value == 0.20
        assert m.formula == "(current - prior) / abs(prior)"
        assert m.inputs == {"current": 120.0, "prior": 100.0}

    def test_decline(self):
        # 80 vs 100 -> -0.20
        index = build_index([
            make_fact(Metric.REVENUE, 100.0, 2023),
            make_fact(Metric.REVENUE, 80.0, 2024),
        ])
        m = growth_rate(index, Metric.REVENUE, 789019, 2024, "FY", PeriodType.ANNUAL, 2023)
        assert m.value == -0.20

    def test_recovery_from_loss_sign_correct(self):
        # Loss narrowing from -50 to -25: the loss HALVED, so growth = +50%.
        # Plain division (-25 - -50)/-50 would wrongly give -50%.
        index = build_index([
            make_fact(Metric.NET_INCOME, -50.0, 2023),
            make_fact(Metric.NET_INCOME, -25.0, 2024),
        ])
        m = growth_rate(index, Metric.NET_INCOME, 789019, 2024, "FY", PeriodType.ANNUAL, 2023)
        assert m.value == 0.50

    def test_missing_prior_returns_none_with_reason(self):
        index = build_index([make_fact(Metric.REVENUE, 100.0, 2024)])
        m = growth_rate(index, Metric.REVENUE, 789019, 2024, "FY", PeriodType.ANNUAL, 2023)
        assert m.value is None
        assert "missing" in m.reason

    def test_zero_prior_returns_none_not_division_error(self):
        index = build_index([
            make_fact(Metric.REVENUE, 0.0, 2023),
            make_fact(Metric.REVENUE, 100.0, 2024),
        ])
        m = growth_rate(index, Metric.REVENUE, 789019, 2024, "FY", PeriodType.ANNUAL, 2023)
        assert m.value is None
        assert "zero" in m.reason


# --------------------------------------------------------------------------- #
# Margins (hand-verified)
# --------------------------------------------------------------------------- #


class TestMargins:
    def test_operating_margin(self):
        # 128528 / 281724 = 0.45622... (MSFT FY2025 actuals from M1 data)
        index = build_index([
            make_fact(Metric.REVENUE, 281724000000.0, 2025),
            make_fact(Metric.OPERATING_INCOME, 128528000000.0, 2025),
        ])
        m = margin(index, Metric.OPERATING_INCOME, 789019, 2025, "FY", PeriodType.ANNUAL)
        assert abs(m.value - 128528 / 281724) < 1e-9

    def test_net_margin(self):
        # 101832 / 281724 = 0.36146...
        index = build_index([
            make_fact(Metric.REVENUE, 281724000000.0, 2025),
            make_fact(Metric.NET_INCOME, 101832000000.0, 2025),
        ])
        m = margin(index, Metric.NET_INCOME, 789019, 2025, "FY", PeriodType.ANNUAL)
        assert abs(m.value - 101832 / 281724) < 1e-9

    def test_gross_profit_missing_yields_none(self):
        index = build_index([make_fact(Metric.REVENUE, 100.0, 2025)])
        m = margin(index, Metric.GROSS_PROFIT, 789019, 2025, "FY", PeriodType.ANNUAL)
        assert m.value is None
        assert "missing" in m.reason


# --------------------------------------------------------------------------- #
# Cash flow & health (hand-verified)
# --------------------------------------------------------------------------- #


class TestCashFlowAndHealth:
    def test_free_cash_flow(self):
        # OCF 74000 - capex 44000 = 30000
        index = build_index([
            make_fact(Metric.OPERATING_CASH_FLOW, 74000.0, 2024),
            make_fact(Metric.CAPITAL_EXPENDITURE, 44000.0, 2024),
        ])
        m = free_cash_flow(index, 789019, 2024, "FY", PeriodType.ANNUAL)
        assert m.value == 30000.0
        assert m.unit == "USD"

    def test_return_on_assets(self):
        # 100 / 1000 = 0.10
        index = build_index([
            make_fact(Metric.NET_INCOME, 100.0, 2024),
            make_fact(Metric.TOTAL_ASSETS, 1000.0, 2024, pt=PeriodType.INSTANT),
        ])
        m = return_on_assets(index, 789019, 2024, "FY", PeriodType.ANNUAL)
        assert m.value == 0.10

    def test_current_ratio(self):
        # 150 / 100 = 1.5 (instant metrics)
        index = build_index([
            make_fact(Metric.CURRENT_ASSETS, 150.0, 2024, pt=PeriodType.INSTANT),
            make_fact(Metric.CURRENT_LIABILITIES, 100.0, 2024, pt=PeriodType.INSTANT),
        ])
        m = current_ratio(index, 789019, 2024, "FY", PeriodType.INSTANT)
        assert m.value == 1.5

    def test_debt_to_assets(self):
        # 250 / 1000 = 0.25
        index = build_index([
            make_fact(Metric.TOTAL_LIABILITIES, 250.0, 2024, pt=PeriodType.INSTANT),
            make_fact(Metric.TOTAL_ASSETS, 1000.0, 2024, pt=PeriodType.INSTANT),
        ])
        m = debt_to_assets(index, 789019, 2024, "FY", PeriodType.INSTANT)
        assert m.value == 0.25


# --------------------------------------------------------------------------- #
# Divergence (earnings-quality signal)
# --------------------------------------------------------------------------- #


class TestDivergence:
    def test_income_growing_cashflow_declining_flagged(self):
        # NI: 100 -> 130 (+30%); OCF: 100 -> 80 (-20%); divergence = +0.50
        index = build_index([
            make_fact(Metric.NET_INCOME, 100.0, 2023),
            make_fact(Metric.NET_INCOME, 130.0, 2024),
            make_fact(Metric.OPERATING_CASH_FLOW, 100.0, 2023),
            make_fact(Metric.OPERATING_CASH_FLOW, 80.0, 2024),
        ])
        m = earnings_cashflow_divergence(index, 789019, 2024, "FY", PeriodType.ANNUAL, 2023)
        assert abs(m.value - 0.50) < 1e-9
        assert m.inputs["net_income_growth"] == 0.30
        assert m.inputs["ocf_growth"] == -0.20


# --------------------------------------------------------------------------- #
# Provenance (audit requirement)
# --------------------------------------------------------------------------- #


class TestProvenance:
    def test_metric_records_formula_inputs_and_accessions(self):
        index = build_index([
            make_fact(Metric.REVENUE, 100.0, 2023, accn="ACC-2023"),
            make_fact(Metric.REVENUE, 120.0, 2024, accn="ACC-2024"),
        ])
        m = growth_rate(index, Metric.REVENUE, 789019, 2024, "FY", PeriodType.ANNUAL, 2023)
        assert m.formula != ""
        assert m.inputs["current"] == 120.0
        assert set(m.source_accessions) == {"ACC-2023", "ACC-2024"}
        assert m.calculated_at != ""  # timestamp present


# --------------------------------------------------------------------------- #
# Orchestration smoke test
# --------------------------------------------------------------------------- #


class TestCalculateAll:
    def test_full_year_computation_produces_core_metrics(self):
        facts = [
            make_fact(Metric.REVENUE, 200.0, 2024),
            make_fact(Metric.REVENUE, 250.0, 2025),
            make_fact(Metric.OPERATING_INCOME, 50.0, 2025),
            make_fact(Metric.NET_INCOME, 30.0, 2025),
            make_fact(Metric.OPERATING_CASH_FLOW, 60.0, 2025),
            make_fact(Metric.CAPITAL_EXPENDITURE, 20.0, 2025),
            make_fact(Metric.TOTAL_ASSETS, 800.0, 2025, pt=PeriodType.INSTANT),
            make_fact(Metric.TOTAL_LIABILITIES, 300.0, 2025, pt=PeriodType.INSTANT),
        ]
        results = calculate_all_metrics(789019, facts)
        by_name = {m.metric: m for m in results if m.fiscal_year == 2025}

        assert by_name["revenue_growth_yoy"].value == 0.25  # (250-200)/200
        assert abs(by_name["operating_income_margin"].value - 0.20) < 1e-9  # 50/250
        assert by_name["free_cash_flow"].value == 40.0  # 60-20
        assert abs(by_name["debt_to_assets"].value - 0.375) < 1e-9  # 300/800
        # FY2024 revenue growth must be None (no 2023 fact) -- honest gap.
        y2024 = {m.metric: m for m in results if m.fiscal_year == 2024}
        assert y2024["revenue_growth_yoy"].value is None
