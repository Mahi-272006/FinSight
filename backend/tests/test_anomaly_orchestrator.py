"""Tests for the anomaly orchestrator (M3): series -> signals end-to-end.

Uses a realistic MSFT-like fixture to prove the wiring (metric-name lookup,
year alignment, provenance pass-through) works, not just the individual rules.
"""

from app.services.anomaly_orchestrator import CompanySeries, detect_signals


def make_series(**metrics: dict[int, float]) -> CompanySeries:
    return CompanySeries(cik=789019, ticker="MSFT", annual_metrics=metrics)


class TestDetectSignals:
    def test_no_data_no_signals(self):
        # Empty series -> no rules can fire; must not crash.
        series = make_series()
        assert detect_signals(series) == []

    def test_quiet_company_emits_nothing(self):
        # Healthy company: income and cash flow in step, AR in line, margins
        # stable, growth steady. Must be silent (false-positive guard).
        series = make_series(
            revenue_growth_yoy={2024: 0.15, 2025: 0.14},
            net_income_growth_yoy={2024: 0.16, 2025: 0.13},
            operating_cash_flow_growth_yoy={2024: 0.15, 2025: 0.14},
            earnings_cashflow_divergence={2024: 0.01, 2025: -0.01},
            operating_income_margin={2023: 0.44, 2024: 0.45, 2025: 0.46},
            accounts_receivable_growth_yoy={2024: 0.10, 2025: 0.12},
        )
        assert detect_signals(series) == []

    def test_divergence_company_triggers_rule_1(self):
        series = make_series(
            net_income_growth_yoy={2025: 0.40},
            operating_cash_flow_growth_yoy={2025: 0.05},
            earnings_cashflow_divergence={2025: 0.35},
        )
        signals = detect_signals(series)
        assert len(signals) == 1
        assert signals[0].signal_type == "earnings_cashflow_divergence"
        assert signals[0].severity == "elevated"  # 0.35 is between 0.20 and 0.50

    def test_signals_sorted_chronologically(self):
        # Divergence in 2023, deceleration in 2025 -> 2023 first.
        series = make_series(
            revenue_growth_yoy={2024: 0.40, 2025: 0.10},
            net_income_growth_yoy={2023: 0.30},
            operating_cash_flow_growth_yoy={2023: 0.0},
            earnings_cashflow_divergence={2023: 0.30},
        )
        signals = detect_signals(series)
        years = [s.fiscal_year for s in signals]
        assert years == sorted(years)
        assert {s.signal_type for s in signals} == {
            "earnings_cashflow_divergence", "growth_deceleration",
        }

    def test_multiple_rules_can_fire_same_year(self):
        # 2025: divergence AND receivables gap AND deceleration.
        series = make_series(
            revenue_growth_yoy={2024: 0.40, 2025: 0.10},
            net_income_growth_yoy={2025: 0.35},
            operating_cash_flow_growth_yoy={2025: 0.0},
            earnings_cashflow_divergence={2025: 0.35},
            accounts_receivable_growth_yoy={2025: 0.55},
        )
        signals = detect_signals(series)
        types_2025 = {s.signal_type for s in signals if s.fiscal_year == 2025}
        assert types_2025 == {
            "earnings_cashflow_divergence",
            "receivables_outpacing_revenue",
            "growth_deceleration",
        }

    def test_partial_data_does_not_crash(self):
        # Only one metric present, gaps everywhere -> graceful, some signals.
        series = make_series(operating_income_margin={2020: 0.30, 2021: 0.28, 2022: 0.26, 2023: 0.24})
        signals = detect_signals(series)
        # Only the margin-streak rule can fire here.
        assert all(s.signal_type == "margin_compression_streak" for s in signals)
        assert len(signals) == 1  # fires once at 2023 (streak 3)
