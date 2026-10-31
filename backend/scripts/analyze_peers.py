"""M3 analysis: peer comparison table + investigation signals, from the DB.

Usage:
    python scripts/analyze_peers.py

Steps:
1. Load all companies and their calculated metrics (M2 output).
2. Build cross-company comparison tables for the report metrics, with ranks
   and leave-one-out z-scores (labeled 'indicative only' when the peer set
   is too small for statistical significance).
3. Run the anomaly rules over each company's annual series.
4. Persist signals (full-refresh per company, so a fixed company's old
   signals disappear) and print the report.

The report distinguishes evidence layers explicitly:
  CALCULATED METRIC = computed by our deterministic engine from facts
  INVESTIGATION SIGNAL = deterministic rule output, a prompt for review,
                          NEVER a verdict of wrongdoing
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.database.models import Company, FinancialMetric
from app.database.postgres import get_session
from app.database.repositories import SignalRepository
from app.services.anomaly_orchestrator import CompanySeries, detect_signals
from app.services.peer_comparison import (
    REPORT_METRICS,
    CompanyMetricPoint,
    compare_all_periods,
)
from app.utils.logging import configure_logging


def load_series(session, company: Company) -> CompanySeries:
    """Assemble one company's annual metric time series from FinancialMetric.

    Only FY/annual rows feed the rules (quarterly signals need separate
    seasonality handling -- out of M3 scope, documented).
    """
    rows = session.execute(
        select(FinancialMetric).where(FinancialMetric.cik == company.cik)
    ).scalars()

    annual: dict[str, dict[int, float]] = {}
    accessions: dict[str, dict[int, list[str]]] = {}

    for r in rows:
        if r.fiscal_period != "FY" or r.period_type != "annual":
            continue
        if r.value is None:
            continue
        annual.setdefault(r.metric, {})[r.fiscal_year] = r.value
        try:
            accs = json.loads(r.source_accessions_json or "[]")
        except ValueError:
            accs = []
        if accs:
            accessions.setdefault(r.metric, {})[r.fiscal_year] = accs

    return CompanySeries(
        cik=company.cik, ticker=company.ticker,
        annual_metrics=annual, accessions=accessions,
    )


def load_comparison_points(session, companies: list[Company]) -> list[CompanyMetricPoint]:
    """Flat list of (company, metric, period, value) points for comparison.

    ANNUAL rows only: quarterly cross-company comparison is misleading here
    because the three companies' fiscal quarters cover different calendar
    windows (Jun/Sep/Jan year-ends) and quarterly metrics are seasonal. The
    report compares complete fiscal years. (Caught by reviewing live output:
    incomplete FY2027 quarterly rows were being displayed beside complete
    years -- a partial-year number must never masquerade as annual.)
    """
    points: list[CompanyMetricPoint] = []
    for c in companies:
        rows = session.execute(
            select(FinancialMetric).where(
                FinancialMetric.cik == c.cik,
                FinancialMetric.metric.in_(REPORT_METRICS),
                FinancialMetric.period_type == "annual",
                FinancialMetric.fiscal_period == "FY",
            )
        ).scalars()
        for r in rows:
            if r.value is None:
                continue
            points.append(CompanyMetricPoint(
                cik=c.cik, ticker=c.ticker, metric=r.metric,
                fiscal_year=r.fiscal_year, fiscal_period=r.fiscal_period,
                period_type=r.period_type, value=r.value,
            ))
    return points


def main() -> int:
    configure_logging("WARNING")
    with get_session() as session:
        companies = list(session.execute(
            select(Company).order_by(Company.ticker)
        ).scalars())
        if len(companies) < 2:
            print("Need at least 2 companies for peer comparison.")
            return 1

        points = load_comparison_points(session, companies)
        rankings = compare_all_periods(points)
        signals_repo = SignalRepository(session)

        # ---- Comparison table (newest fiscal year first) -------------------
        # NOTE: print uses ASCII only -- the Windows console (cp1252) garbles
        # Unicode em-dashes/sigma, and a financial report that mojibakes its
        # own headers undermines confidence in its numbers.
        print("=" * 78)
        print("PEER COMPARISON - CALCULATED METRICS (deterministic engine output)")
        print(f"Peer set: {', '.join(c.ticker for c in companies)} "
              f"(n={len(companies)})")
        print("Fiscal-year labels: companies' own FY (MSFT Jun / AAPL Sep / NVDA Jan")
        print("year-ends) - windows overlap but are not calendar-identical.")
        print("Only complete ANNUAL (FY) periods shown; partial years excluded.")
        if rankings and not rankings[0].is_z_statistically_meaningful():
            print("Z-SCORES ARE INDICATIVE ONLY - peer set too small for statistical")
            print("significance (leave-one-out needs >=8 reference points). Ranks and")
            print("raw values are the trustworthy comparison at n=3.")
        print("=" * 78)

        # Show only the two newest fiscal years to keep the table readable.
        shown_years = sorted({r.fiscal_year for r in rankings}, reverse=True)[:2]
        for fy in shown_years:
            print(f"\n--- FY{fy} (annual) ---")
            for metric in REPORT_METRICS:
                rows_for_metric = [
                    r for r in rankings if r.fiscal_year == fy and r.metric == metric
                ]
                if not rows_for_metric:
                    continue
                r = rows_for_metric[0]
                parts = []
                for p in r.points:
                    z = r.z_scores.get(p.cik)
                    z_str = f" (z={z:+.2f})" if z is not None else ""
                    parts.append(f"{p.ticker} {p.value:.3f}{z_str}")
                mean_s = f"{r.peer_mean:.3f}" if r.peer_mean is not None else "n/a"
                print(f"  {metric:<30} " + " | ".join(parts) + f"   mean={mean_s}")

        # ---- Investigation signals -----------------------------------------
        print()
        print("=" * 78)
        print("INVESTIGATION SIGNALS - prompts for analyst review, NOT fraud verdicts")
        print("Every signal is a deterministic rule over calculated metrics; the")
        print("evidence values and thresholds are stored for audit.")
        print("=" * 78)

        total_signals = 0
        for company in companies:
            series = load_series(session, company)
            signals = detect_signals(series)
            signals_repo.replace_company_signals(company.cik, signals)
            total_signals += len(signals)

            print(f"\n{company.ticker} (CIK {company.cik:010d}): "
                  f"{len(signals)} signal(s)")
            if not signals:
                print("  (none - metrics within normal rule thresholds)")
                continue
            # Group by fiscal year, newest first.
            by_year: dict[int, list] = {}
            for s in signals:
                by_year.setdefault(s.fiscal_year, []).append(s)
            for fy in sorted(by_year, reverse=True):
                for s in by_year[fy]:
                    print(f"  [FY{fy} | {s.severity.upper()}] {s.signal_type}")
                    print(f"    {s.headline}")
                    print(f"    -> {s.explanation.split('.')[0]}.")

        print()
        print(f"Signals persisted to investigation_signals "
              f"(full-refresh per company). Total: {total_signals}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
