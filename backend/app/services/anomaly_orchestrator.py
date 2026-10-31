"""Orchestrator: run every anomaly rule over one company's metric history.

Takes plain metric rows (as loaded from FinancialMetric) and fact-derived
series, calls each rule, and returns all signals. Pure -- DB loading happens
in the analyze script, keeping this testable with in-memory fixtures.

Data contract: the caller supplies per-company time series built from M2's
stored metrics/facts. Missing series entries mean "no data" -- rules skip
them by design (anomaly_detection module).
"""

from dataclasses import dataclass
from typing import Any

from app.services.anomaly_detection import (
    InvestigationSignal,
    check_earnings_cashflow_divergence,
    check_growth_deceleration,
    check_margin_compression_streak,
    check_receivables_outpacing_revenue,
)


@dataclass
class CompanySeries:
    """Time series for one company, assembled by the caller (script/API)."""

    cik: int
    ticker: str
    # metric name -> fiscal_year -> value (annual FY slots)
    annual_metrics: dict[str, dict[int, float]]
    # metric name -> fiscal_year -> source accession (best-effort provenance)
    accessions: dict[str, dict[int, list[str]]] | None = None

    def metric_at(self, metric: str, fy: int) -> float | None:
        return self.annual_metrics.get(metric, {}).get(fy)

    def accessions_at(self, metric: str, fy: int) -> list[str]:
        if not self.accessions:
            return []
        return list(self.accessions.get(metric, {}).get(fy, []))

    def years_with(self, metric: str) -> list[int]:
        return sorted(self.annual_metrics.get(metric, {}))


def detect_signals(series: CompanySeries) -> list[InvestigationSignal]:
    """Run all rules across all available years; return every signal.

    Rules that need two consecutive years (divergence, deceleration, AR gap)
    run per fiscal year where both years exist. The streak rule runs once
    over the full operating-margin series (it emits one signal per qualifying
    year internally).
    """
    signals: list[InvestigationSignal] = []
    years = series.years_with("revenue_growth_yoy")
    # Use the union of years where ANY rule-relevant metric exists so a year
    # missing revenue growth but having margins still gets streak coverage.
    all_years = sorted(
        set(years)
        | set(series.years_with("operating_income_margin"))
        | set(series.years_with("net_income_growth_yoy"))
        | set(series.years_with("operating_cash_flow_growth_yoy"))
    )

    for fy in all_years:
        # Rule 1: earnings-cash-flow divergence.
        signals.append(
            check_earnings_cashflow_divergence(
                divergence_value=series.metric_at(
                    "earnings_cashflow_divergence", fy
                ),
                ni_growth=series.metric_at("net_income_growth_yoy", fy),
                ocf_growth=series.metric_at("operating_cash_flow_growth_yoy", fy),
                cik=series.cik,
                ticker=series.ticker,
                fiscal_year=fy,
                fiscal_period="FY",
                period_type="annual",
                accessions=series.accessions_at("earnings_cashflow_divergence", fy),
            )
        )

        # Rule 2: receivables outpacing revenue (needs AR growth + revenue growth).
        signals.append(
            check_receivables_outpacing_revenue(
                ar_growth=series.metric_at("accounts_receivable_growth_yoy", fy),
                revenue_growth=series.metric_at("revenue_growth_yoy", fy),
                cik=series.cik,
                ticker=series.ticker,
                fiscal_year=fy,
                fiscal_period="FY",
                period_type="annual",
                ar_value=series.metric_at("accounts_receivable", fy),
                revenue_value=series.metric_at("revenue", fy),
                accessions=series.accessions_at("accounts_receivable_growth_yoy", fy),
            )
        )

        # Rule 4: growth deceleration.
        signals.append(
            check_growth_deceleration(
                current_growth=series.metric_at("revenue_growth_yoy", fy),
                prior_growth=series.metric_at("revenue_growth_yoy", fy - 1),
                cik=series.cik,
                ticker=series.ticker,
                fiscal_year=fy,
                fiscal_period="FY",
                period_type="annual",
                accessions=series.accessions_at("revenue_growth_yoy", fy),
            )
        )

    # Rule 3: margin compression streak (single pass over the series).
    margin_series = {
        fy: v
        for fy, v in series.annual_metrics.get("operating_income_margin", {}).items()
        if v is not None
    }
    signals.extend(
        check_margin_compression_streak(
            operating_margins_by_year=margin_series,
            cik=series.cik,
            ticker=series.ticker,
        )
    )

    # Drop Nones from per-year rule returns, then sort chronologically.
    flat = [s for s in signals if s is not None]
    flat.sort(key=lambda s: (s.fiscal_year, s.signal_type))
    return flat
