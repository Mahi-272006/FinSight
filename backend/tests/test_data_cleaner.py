"""Tests for XBRL normalization (M2).

Focus: the failure modes that produce WRONG NUMBERS silently in financial
pipelines -- YTD misinterpretation, concept drift, restatements, sign errors.
Every fixture mirrors real SEC data shapes observed in M1.
"""

from datetime import date

from app.ingestion.data_cleaner import (
    NormalizedFact,
    classify_period,
    fiscal_year_of,
    infer_fiscal_year_end_month,
    normalize_company_facts,
)
from app.schemas.company import FiscalPeriod, Metric, PeriodType


# --------------------------------------------------------------------------- #
# Period classification (date arithmetic)
# --------------------------------------------------------------------------- #


class TestClassifyPeriod:
    def test_instant_when_no_start(self):
        assert classify_period(None, date(2024, 6, 30)) is PeriodType.INSTANT

    def test_annual_365_days(self):
        assert classify_period(date(2023, 7, 1), date(2024, 6, 30)) is PeriodType.ANNUAL

    def test_annual_leap_year_366_days(self):
        assert classify_period(date(2024, 1, 1), date(2024, 12, 31)) is PeriodType.ANNUAL

    def test_quarterly_90_days(self):
        assert classify_period(date(2024, 1, 1), date(2024, 3, 31)) is PeriodType.QUARTERLY

    def test_ytd_six_months(self):
        assert classify_period(date(2024, 1, 1), date(2024, 6, 30)) is PeriodType.YTD

    def test_ytd_nine_months(self):
        assert classify_period(date(2024, 1, 1), date(2024, 9, 30)) is PeriodType.YTD


class TestFiscalYear:
    def test_annual_uses_end_year_not_label(self):
        # Comparative columns in a later 10-K mislabel the fy field (FY2024's
        # period re-reported in the FY2025 10-K carries fy=2025). Annual facts
        # must use the END year so the same period always maps to one fiscal
        # year -- this is what makes comparative-filing dedup work.
        assert fiscal_year_of(date(2025, 6, 30), 2025, PeriodType.ANNUAL) == 2025
        assert fiscal_year_of(date(2024, 6, 30), 2025, PeriodType.ANNUAL) == 2024

    def test_quarterly_uses_fye_month_when_available(self):
        # With fye_month=6 (MSFT): quarter ending Sep-2024 (month 9>6) is
        # FY2025; quarter ending Mar-2025 (month 3<=6) is also FY2025.
        # This is the date-arithmetic fix for the comparative-label bug.
        assert fiscal_year_of(date(2024, 9, 30), 2025, PeriodType.QUARTERLY, 6) == 2025
        assert fiscal_year_of(date(2025, 3, 31), 2025, PeriodType.QUARTERLY, 6) == 2025
        assert fiscal_year_of(date(2024, 12, 31), 2025, PeriodType.QUARTERLY, 6) == 2025

    def test_quarterly_mislabel_overridden_by_date_arithmetic(self):
        # A comparative Sep-2023 quarter wrongly labelled fy=2025 by SEC:
        # with fye_month=6, date arithmetic correctly assigns FY2024.
        assert fiscal_year_of(date(2023, 9, 30), 2025, PeriodType.QUARTERLY, 6) == 2024

    def test_quarterly_falls_back_to_label_without_fye(self):
        assert fiscal_year_of(date(2024, 12, 31), 2025, PeriodType.QUARTERLY, None) == 2025

    def test_calendar_fy_company_unaffected(self):
        # fye_month=12 (calendar FY): every quarter's end year IS its FY.
        assert fiscal_year_of(date(2024, 3, 31), 2024, PeriodType.QUARTERLY, 12) == 2024
        assert fiscal_year_of(date(2024, 11, 30), 2024, PeriodType.QUARTERLY, 12) == 2024

    def test_falls_back_to_end_year(self):
        assert fiscal_year_of(date(2024, 12, 31), None, PeriodType.YTD) == 2024


class TestInferFiscalYearEndMonth:
    def test_infers_june_fy_end(self):
        """Microsoft-style company: annual facts end in June -> month 6."""
        payload = build_payload(
            [
                {"start": "2023-07-01", "end": "2024-06-30", "val": 100,
                 "form": "10-K", "fy": 2024, "fp": "FY", "accn": "A1", "filed": "2024-07-30"},
                {"start": "2022-07-01", "end": "2023-06-30", "val": 90,
                 "form": "10-K", "fy": 2023, "fp": "FY", "accn": "A0", "filed": "2023-07-30"},
            ]
        )
        assert infer_fiscal_year_end_month(payload) == 6

    def test_infers_december_fy_end(self):
        """Calendar-FY company: annual facts end in December -> month 12."""
        payload = build_payload(
            [
                {"start": "2024-01-01", "end": "2024-12-31", "val": 100,
                 "form": "10-K", "fy": 2024, "fp": "FY", "accn": "A1", "filed": "2025-02-01"},
            ]
        )
        assert infer_fiscal_year_end_month(payload) == 12

    def test_returns_none_when_no_annual_facts(self):
        assert infer_fiscal_year_end_month({"facts": {}}) is None


class TestQuarterlyFiscalYearAssignment:
    def test_comparative_quarter_not_mislabelled(self):
        """Regression: the Sep-2023 comparative in a FY2025-Q1 10-Q must be
        FY2024, NOT FY2025 (SEC mislabels it with the filing's fy).

        This is the exact off-by-one bug found by verifying real MSFT data:
        every derived quarter was shifted one fiscal year late.
        """
        payload = build_payload(
            [
                # Annual facts (June FY-end) -> infer fye_month=6.
                {"start": "2022-07-01", "end": "2023-06-30", "val": 90,
                 "form": "10-K", "fy": 2023, "fp": "FY", "accn": "AK1", "filed": "2023-07-30"},
                {"start": "2023-07-01", "end": "2024-06-30", "val": 100,
                 "form": "10-K", "fy": 2024, "fp": "FY", "accn": "AK2", "filed": "2024-07-30"},
                # FY2024 Q1 as filed (Sep 2023) -- correct fy=2024 label.
                _ytd(25, "2023-07-01", "2023-09-30", 2024, "Q1", "A1"),
                # The SAME Sep-2023 period as a COMPARATIVE inside the
                # FY2025-Q1 10-Q -- SEC wrongly labels it fy=2025.
                _ytd(25, "2023-07-01", "2023-09-30", 2025, "Q1", "B1"),
                # FY2025 Q1 as filed (Sep 2024) -- correct fy=2025 label.
                _ytd(30, "2024-07-01", "2024-09-30", 2025, "Q1", "A2"),
            ]
        )
        result = normalize_company_facts(payload)
        q1_facts = [
            f for f in result.facts
            if f.metric is Metric.REVENUE
            and f.period_type is PeriodType.QUARTERLY
            and f.fiscal_period is FiscalPeriod.Q1
        ]
        # Both Sep-2023 facts (one fy=2024 label, one mislabelled fy=2025)
        # must resolve to the SAME fiscal year 2024 -> deduplicated to 1.
        # Plus Sep-2024 -> FY2025. Total: 2 distinct Q1s, no FY-shifting.
        by_fy = {f.fiscal_year: f.value for f in q1_facts}
        assert by_fy == {2024: 25.0, 2025: 30.0}


# --------------------------------------------------------------------------- #
# Full normalization against a realistic fixture
# --------------------------------------------------------------------------- #


def _us_gaap_concept(facts: list[dict]) -> dict:
    """Wrap fact dicts into the SEC units structure."""
    return {"units": {"USD": facts}}


def build_payload(revenue_facts: list[dict], extra: dict | None = None) -> dict:
    us_gaap = {"RevenueFromContractWithCustomerExcludingAssessedTax": _us_gaap_concept(revenue_facts)}
    if extra:
        us_gaap.update(extra)
    return {"cik": 789019, "entityName": "TEST CORP", "facts": {"us-gaap": us_gaap}}


def test_normalizes_annual_revenue():
    payload = build_payload(
        [
            {
                "start": "2023-07-01", "end": "2024-06-30", "val": 245122000000,
                "form": "10-K", "fy": 2024, "fp": "FY",
                "accn": "0000950170-24-087554", "filed": "2024-07-30",
            }
        ]
    )
    result = normalize_company_facts(payload)
    assert len(result.facts) == 1
    fact = result.facts[0]
    assert fact.metric is Metric.REVENUE
    assert fact.period_type is PeriodType.ANNUAL
    assert fact.fiscal_year == 2024
    assert fact.fiscal_period is FiscalPeriod.FY
    assert fact.value == 245122000000
    # SEC archive URLs strip dashes from the accession number.
    assert "000095017024087554" in fact.source_url  # citation link built


def test_concept_fallback_picks_modern_tag():
    """When both legacy Revenues and modern tag exist, modern wins."""
    modern = _us_gaap_concept(
        [{"start": "2023-07-01", "end": "2024-06-30", "val": 100,
          "form": "10-K", "fy": 2024, "fp": "FY", "accn": "A1", "filed": "2024-07-30"}]
    )
    legacy = _us_gaap_concept(
        [{"start": "2023-07-01", "end": "2024-06-30", "val": 999,
          "form": "10-K", "fy": 2024, "fp": "FY", "accn": "A0", "filed": "2023-07-30"}]
    )
    payload = {
        "cik": 1, "facts": {"us-gaap": {
            "Revenues": legacy,
            "RevenueFromContractWithCustomerExcludingAssessedTax": modern,
        }}
    }
    result = normalize_company_facts(payload)
    revenue_facts = [f for f in result.facts if f.metric is Metric.REVENUE]
    assert len(revenue_facts) == 1
    assert revenue_facts[0].value == 100  # modern tag's value, not legacy 999


def test_restatement_detected_and_both_values_kept():
    """Same period, two filings, DIFFERENT value -> restatement flagged."""
    payload = build_payload(
        [
            {"start": "2023-07-01", "end": "2024-06-30", "val": 100,
             "form": "10-K", "fy": 2024, "fp": "FY", "accn": "A1", "filed": "2024-07-30"},
            {"start": "2023-07-01", "end": "2024-06-30", "val": 105,
             "form": "10-K/A", "fy": 2024, "fp": "FY", "accn": "A2", "filed": "2025-01-15"},
        ]
    )
    result = normalize_company_facts(payload)
    assert len(result.facts) == 1  # deduplicated
    fact = result.facts[0]
    assert fact.value == 105  # latest filed wins
    assert fact.is_restated is True
    assert fact.value_original == 100  # original preserved, not lost
    assert len(result.restatements) == 1


def test_comparative_filings_same_value_not_a_restatement():
    """Same value re-reported in a later filing (FY comparatives) is NOT a
    restatement -- must not be flagged."""
    payload = build_payload(
        [
            {"start": "2023-07-01", "end": "2024-06-30", "val": 100,
             "form": "10-K", "fy": 2024, "fp": "FY", "accn": "A1", "filed": "2024-07-30"},
            {"start": "2023-07-01", "end": "2024-06-30", "val": 100,
             "form": "10-K", "fy": 2025, "fp": "FY", "accn": "A2", "filed": "2025-07-30"},
        ]
    )
    result = normalize_company_facts(payload)
    assert len(result.facts) == 1
    assert result.facts[0].is_restated is False
    assert result.restatements == []


# --------------------------------------------------------------------------- #
# YTD -> standalone quarter derivation (the double-counting guard)
# --------------------------------------------------------------------------- #


def _ytd(val: float, start: str, end: str, fy: int, fp: str, accn: str) -> dict:
    return {"start": start, "end": end, "val": val, "form": "10-Q",
            "fy": fy, "fp": fp, "accn": accn, "filed": end[:10]}


def test_ytd_quarter_derivation():
    """Q1=100, H1=220, 9M=360, FY=500 -> Q1=100, Q2=120, Q3=140, Q4=140."""
    payload = build_payload(
        [
            _ytd(100, "2024-01-01", "2024-03-31", 2024, "Q1", "A1"),
            _ytd(220, "2024-01-01", "2024-06-30", 2024, "Q2", "A2"),
            _ytd(360, "2024-01-01", "2024-09-30", 2024, "Q3", "A3"),
            {"start": "2024-01-01", "end": "2024-12-31", "val": 500,
             "form": "10-K", "fy": 2024, "fp": "FY", "accn": "A4", "filed": "2025-02-01"},
        ]
    )
    result = normalize_company_facts(payload)
    quarters = {
        f.fiscal_period.value: f.value
        for f in result.facts
        if f.metric is Metric.REVENUE and f.period_type is PeriodType.QUARTERLY
    }
    assert quarters == {"Q1": 100, "Q2": 120, "Q3": 140, "Q4": 140}
    # Sanity: quarters sum to the annual total (no double counting).
    assert sum(quarters.values()) == 500


def test_missing_ytd_input_yields_no_quarter_not_wrong_number():
    """Missing H1 -> Q2 and Q3 must NOT be derived (correctly incomplete).

    Note Q1 (already a ~90d standalone) and Q4 (Annual - 9M, which does NOT
    need H1) are still derivable -- they have all their required inputs.
    """
    payload = build_payload(
        [
            _ytd(100, "2024-01-01", "2024-03-31", 2024, "Q1", "A1"),
            # H1 missing entirely
            _ytd(360, "2024-01-01", "2024-09-30", 2024, "Q3", "A3"),
            {"start": "2024-01-01", "end": "2024-12-31", "val": 500,
             "form": "10-K", "fy": 2024, "fp": "FY", "accn": "A4", "filed": "2025-02-01"},
        ]
    )
    result = normalize_company_facts(payload)
    quarters = {
        f.fiscal_period.value
        for f in result.facts
        if f.metric is Metric.REVENUE and f.period_type is PeriodType.QUARTERLY
    }
    # Q1 (standalone) and Q4 (Annual - 9M) are safe; Q2/Q3 need the missing H1.
    assert quarters == {"Q1", "Q4"}


def test_negative_revenue_quarter_rejected():
    """H1 < Q1 (misaligned inputs) -> Q2 must be rejected, not negative."""
    payload = build_payload(
        [
            _ytd(150, "2024-01-01", "2024-03-31", 2024, "Q1", "A1"),
            _ytd(100, "2024-01-01", "2024-06-30", 2024, "Q2", "A2"),  # < Q1!
        ]
    )
    result = normalize_company_facts(payload)
    q_values = {
        f.fiscal_period.value: f.value
        for f in result.facts
        if f.metric is Metric.REVENUE and f.period_type is PeriodType.QUARTERLY
    }
    assert "Q2" not in q_values  # rejected (negative), not emitted as -50


# --------------------------------------------------------------------------- #
# Instant metrics & unit safety
# --------------------------------------------------------------------------- #


def test_instant_metric_normalized():
    payload = {
        "cik": 1, "facts": {"us-gaap": {
            "Assets": _us_gaap_concept(
                [{"end": "2024-06-30", "val": 512163000000, "form": "10-K",
                  "fy": 2024, "fp": "FY", "accn": "A1", "filed": "2024-07-30"}]
            )
        }}
    }
    result = normalize_company_facts(payload)
    assert len(result.facts) == 1
    assert result.facts[0].period_type is PeriodType.INSTANT
    assert result.facts[0].metric is Metric.TOTAL_ASSETS


def test_missing_metric_recorded_not_crashing():
    payload = build_payload([])  # no usable facts at all
    result = normalize_company_facts(payload)
    assert result.facts == []
    assert Metric.REVENUE.value in result.skipped_concepts
