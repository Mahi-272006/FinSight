"""End-to-end pipeline: SEC fetch -> normalize -> PostgreSQL.

Usage:
    python scripts/process_company.py MSFT
    python scripts/process_company.py MSFT AAPL NVDA

Steps per company:
1. Resolve ticker -> CIK (SEC ticker file).
2. Load raw facts (re-fetch if missing; raw file is the audit trail).
3. Normalize: concept mapping, period classification, YTD->quarter splitting,
   restatement detection.
4. Store: upsert Company + FinancialFact + calculated FinancialMetric.
5. Report: counts, restatements, and a sample of computed metrics.

Idempotent: safe to re-run; facts are replaced, metrics upserted.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json

from app.config import get_settings
from app.database.postgres import create_tables, get_session
from app.database.repositories import CompanyRepository, FactRepository, MetricRepository
from app.ingestion.company_facts import fetch_and_save
from app.ingestion.data_cleaner import normalize_company_facts
from app.ingestion.sec_client import SECClient, SECClientError
from app.services.financial_engine import calculate_all_metrics
from app.utils.logging import configure_logging, get_logger


def main(queries: list[str]) -> int:
    settings = get_settings()
    configure_logging(settings.log_level, str(settings.logs_dir))
    settings.ensure_directories()
    logger = get_logger("process")
    create_tables()

    failures = 0
    with SECClient(settings) as client, get_session() as session:
        companies = CompanyRepository(session)
        facts_repo = FactRepository(session)
        metrics_repo = MetricRepository(session)

        for query in queries:
            try:
                identity, payload = fetch_and_save(client, query)
            except SECClientError as exc:
                logger.error("Ingestion failed for %r: %s", query, exc)
                failures += 1
                continue

            # Normalize (pure function -- no DB involved).
            result = normalize_company_facts(payload)

            # Persist.
            companies.upsert(identity["cik"], identity["ticker"], identity["title"])
            fact_count = facts_repo.replace_company_facts(identity["cik"], result.facts)
            metrics = calculate_all_metrics(identity["cik"], result.facts)
            metric_count = metrics_repo.upsert_metrics(identity["cik"], metrics)

            _print_report(identity, result, fact_count, metric_count, metrics)
    return 1 if failures else 0


def _print_report(identity, result, fact_count, metric_count, metrics) -> None:
    print()
    print("=" * 70)
    print(f"{identity['title']} ({identity['ticker']}, CIK {identity['cik']:010d})")
    print(f"  Facts stored:      {fact_count}")
    print(f"  Metrics stored:    {metric_count}")
    print(f"  Derived quarters:  {result.derived_quarters}")
    print(f"  Restatements:      {len(result.restatements)}")
    if result.restatements:
        for r in result.restatements[:3]:
            print(f"    - {r['metric']} FY{r['fiscal_year']} {r['fiscal_period']}: "
                  f"{r['original_value']:,.0f} -> {r['restated_value']:,.0f}")
    if result.skipped_concepts:
        print(f"  Metrics with no data: {', '.join(result.skipped_concepts)}")

    # Show a few recent annual metrics for immediate sanity-checking.
    annual = [m for m in metrics if m.fiscal_period == "FY"]
    recent_years = sorted({m.fiscal_year for m in annual}, reverse=True)[:2]
    for fy in recent_years:
        print(f"  --- FY{fy} annual metrics ---")
        for m in annual:
            if m.fiscal_year != fy:
                continue
            if m.value is None:
                continue
            if m.metric in ("revenue_growth_yoy", "operating_income_margin",
                            "net_income_margin", "free_cash_flow",
                            "earnings_cashflow_divergence"):
                if m.unit == "ratio":
                    print(f"    {m.metric:<34} {m.value * 100:8.2f}%")
                else:
                    print(f"    {m.metric:<34} {m.value:18,.0f} USD")
    print("=" * 70)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
