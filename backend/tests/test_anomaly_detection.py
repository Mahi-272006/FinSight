"""Tests for anomaly detection rules (M3).

TWO KINDS OF TEST PER RULE, both mandatory:
1. TRIGGER case: crafted inputs that MUST fire, with the expected severity.
2. SILENT case: inputs that must NOT fire (normal values, wrong direction,
   or missing data). False-positive guards are as important as the triggers
   -- a rule that fires on noise trains users to ignore it.
"""

from app.services.anomaly_detection import (
    DIVERGENCE_ELEVATED,
    DIVERGENCE_HIGH,
    AR_GAP_ELEVATED,
    AR_GAP_HIGH,
    check_earnings_cashflow_divergence,
    check_growth_deceleration,
    check_margin_compression_streak,
    check_receivables_outpacing_revenue,
    DECEL_ELEVATED,
    DECEL_HIGH,
    MARGIN_STREAK_ELEVATED,
    MARGIN_STREAK_HIGH,
)


class TestEarningsCashflowDivergence:
    def _check(self, div, ni, ocf):
        return check_earnings_cashflow_divergence(
            divergence_value=div, ni_growth=ni, ocf_growth=ocf,
            cik=1, ticker="TEST", fiscal_year=2025, fiscal_period="FY",
            period_type="annual",
        )

    def test_elevated_when_divergence_at_threshold(self):
        # Exactly at the 0.20 threshold -> elevated.
        s = self._check(DIVERGENCE_ELEVATED, ni=0.30, ocf=0.10)
        assert s is not None and s.severity == "elevated"
        assert s.signal_type == "earnings_cashflow_divergence"
        assert s.evidence["divergence"] == DIVERGENCE_ELEVATED

    def test_high_when_divergence_large(self):
        s = self._check(0.60, ni=0.55, ocf=-0.05)
        assert s is not None and s.severity == "high"

    def test_explanation_labels_it_as_signal_not_verdict(self):
        s = self._check(0.55, ni=0.50, ocf=-0.05)
        assert "NOT a finding" in s.explanation

    def test_silent_below_threshold(self):
        assert self._check(0.10, ni=0.30, ocf=0.20) is None

    def test_silent_when_ni_not_growing(self):
        # Divergence positive but income shrinking -> not the red flag.
        assert self._check(0.30, ni=-0.10, ocf=-0.40) is None

    def test_silent_when_input_missing(self):
        assert self._check(None, ni=0.30, ocf=0.10) is None
        assert self._check(0.30, None, 0.10) is None
        assert self._check(0.30, 0.30, None) is None

    def test_silent_when_cash_grows_faster(self):
        # Reverse direction (cash ahead of income) is conservative, not a flag.
        assert self._check(-0.20, ni=0.10, ocf=0.30) is None


class TestReceivablesOutpacingRevenue:
    def _check(self, ar_g, rev_g):
        return check_receivables_outpacing_revenue(
            ar_growth=ar_g, revenue_growth=rev_g,
            cik=1, ticker="TEST", fiscal_year=2025, fiscal_period="FY",
            period_type="annual",
        )

    def test_fires_at_threshold_elevated(self):
        # AR +40% vs revenue +15% -> gap 0.25 = threshold -> elevated.
        s = self._check(0.40, 0.15)
        assert s is not None and s.severity == "elevated"
        assert s.evidence["gap"] == AR_GAP_ELEVATED

    def test_high_when_gap_large(self):
        s = self._check(0.80, 0.10)  # gap 0.70 >= 0.50
        assert s is not None and s.severity == "high"

    def test_silent_when_ar_grows_slower_than_revenue(self):
        assert self._check(0.10, 0.30) is None

    def test_silent_when_ar_shrinking(self):
        # AR declining while revenue grows is a collection improvement.
        assert self._check(-0.10, 0.20) is None

    def test_silent_gap_below_threshold(self):
        assert self._check(0.30, 0.10) is None  # gap 0.20 < 0.25

    def test_silent_when_missing(self):
        assert self._check(None, 0.20) is None
        assert self._check(0.40, None) is None


class TestMarginCompressionStreak:
    def _check(self, margins):
        return check_margin_compression_streak(
            operating_margins_by_year=margins,
            cik=1, ticker="TEST",
        )

    def test_fires_at_three_year_streak(self):
        # 0.30 -> 0.28 -> 0.26 -> 0.24: three declines ending 2024 -> signal.
        signals = self._check({2021: 0.30, 2022: 0.28, 2023: 0.26, 2024: 0.24})
        assert len(signals) == 1
        s = signals[0]
        assert s.fiscal_year == 2024
        assert s.evidence["streak_years"] == 3
        assert s.severity == "elevated"  # 3 < HIGH threshold 5

    def test_high_at_five_year_streak(self):
        margins = {2020: 0.30, 2021: 0.29, 2022: 0.28, 2023: 0.27, 2024: 0.26, 2025: 0.25}
        signals = self._check(margins)
        # Streak grows: by 2023 streak=3 (elevated), by 2024=4, by 2025=5 (high).
        # The FINAL year should carry the HIGH label; earlier qualifying years
        # also emit (timeline visibility), so check the last one.
        assert signals[-1].fiscal_year == 2025
        assert signals[-1].severity == "high"
        assert signals[-1].evidence["streak_years"] == 5

    def test_silent_two_year_streak(self):
        # Only two declines: below the three-year threshold.
        assert self._check({2023: 0.30, 2024: 0.28}) == []

    def test_silent_when_streak_broken(self):
        # Decline then recovery: trailing streak at the end is 0.
        assert self._check({2021: 0.30, 2022: 0.28, 2023: 0.32}) == []

    def test_silent_with_missing_years(self):
        assert self._check({}) == []
        assert self._check({2024: 0.30}) == []  # single point


class TestGrowthDeceleration:
    def _check(self, cur, prior):
        return check_growth_deceleration(
            current_growth=cur, prior_growth=prior,
            cik=1, ticker="TEST", fiscal_year=2025, fiscal_period="FY",
            period_type="annual",
        )

    def test_elevated_at_threshold(self):
        # 0.30 -> 0.15: deceleration 0.15 = threshold -> elevated.
        s = self._check(0.15, 0.30)
        assert s is not None and s.severity == "elevated"
        assert s.evidence["deceleration"] == DECEL_ELEVATED

    def test_high_when_deceleration_large(self):
        # 0.60 -> 0.20: deceleration 0.40 -> high.
        s = self._check(0.20, 0.60)
        assert s is not None and s.severity == "high"

    def test_silent_gentle_slowdown(self):
        assert self._check(0.25, 0.30) is None  # decel 0.05 < 0.15

    def test_silent_when_prior_growth_immaterial(self):
        # Prior growth 5% < 10% floor -> not a growth story, no signal.
        assert self._check(0.0, 0.05) is None

    def test_silent_acceleration(self):
        # Growth speeding up (prior < current) -> negative deceleration.
        assert self._check(0.40, 0.20) is None

    def test_silent_when_missing(self):
        assert self._check(None, 0.40) is None
        assert self._check(0.10, None) is None
