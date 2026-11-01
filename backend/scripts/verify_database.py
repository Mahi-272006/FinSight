"""Verify what process_company.py stored in PostgreSQL.

Uses our own SQLAlchemy layer (not psql) so it works with the DATABASE_URL
in .env without exposing the password in shell commands.

Run: python scripts/verify_database.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select

from app.database.models import Company, FinancialFact, FinancialMetric
from app.database.postgres import session_scope
from app.utils.logging import configure_logging


def main() -> int:
    configure_logging("WARNING")
    with session_scope() as session:
        n_companies = session.execute(select(func.count()).select_from(Company)).scalar()
        n_facts = session.execute(select(func.count()).select_from(FinancialFact)).scalar()
        n_metrics = session.execute(select(func.count()).select_from(FinancialMetric)).scalar()
        print(f"companies={n_companies}  facts={n_facts}  metrics={n_metrics}")

        print("\nCompanies:")
        for c in session.execute(select(Company).order_by(Company.ticker)).scalars():
            print(f"  {c.ticker:<8} CIK {c.cik:010d}  {c.name}")

        print("\nMSFT FY2025/2026 key metrics (from DB):")
        stmt = (
            select(FinancialMetric.metric, FinancialMetric.fiscal_year,
                   FinancialMetric.value, FinancialMetric.unit, FinancialMetric.formula)
            .where(FinancialMetric.cik == 789019)
            .where(FinancialMetric.fiscal_period == "FY")
            .where(FinancialMetric.fiscal_year.in_([2025, 2026]))
            .where(FinancialMetric.metric.in_(
                ["revenue_growth_yoy", "operating_income_margin",
                 "net_income_margin", "free_cash_flow"]))
            .order_by(FinancialMetric.fiscal_year.desc(), FinancialMetric.metric)
        )
        for metric, fy, value, unit, formula in session.execute(stmt):
            if unit == "ratio":
                print(f"  FY{fy} {metric:<28} {value * 100:7.2f}%   [{formula}]")
            else:
                print(f"  FY{fy} {metric:<28} {value:16,.0f}   [{formula}]")

        print("\nRestated facts (sample):")
        stmt = (
            select(FinancialFact.metric, FinancialFact.fiscal_year,
                   FinancialFact.fiscal_period, FinancialFact.value,
                   FinancialFact.value_original, FinancialFact.source_url)
            .where(FinancialFact.cik == 789019)
            .where(FinancialFact.is_restated.is_(True))
            .limit(5)
        )
        for metric, fy, fp, value, original, url in session.execute(stmt):
            print(f"  {metric} FY{fy} {fp}: {original:,.0f} -> {value:,.0f}  ({url})")

        print("\nDerived quarterly revenue (recent, MSFT):")
        stmt = (
            select(FinancialFact.fiscal_year, FinancialFact.fiscal_period,
                   FinancialFact.value, FinancialFact.source_concept)
            .where(FinancialFact.cik == 789019)
            .where(FinancialFact.metric == "revenue")
            .where(FinancialFact.period_type == "quarterly")
            .where(FinancialFact.fiscal_year >= 2025)
            .order_by(FinancialFact.fiscal_year, FinancialFact.fiscal_period)
        )
        for fy, fp, value, concept in session.execute(stmt):
            derived = " (derived)" if "derived" in concept else ""
            print(f"  FY{fy} {fp}: {value:,.0f}{derived}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
