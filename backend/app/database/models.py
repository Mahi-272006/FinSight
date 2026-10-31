"""SQLAlchemy ORM models for FinSight's PostgreSQL database.

Design decisions:

1. SQLAlchemy 2.0 style (Mapped[mapped_column]) instead of the legacy
   Column-based style: gives static type checking in the IDE and catches
   column/type mismatches at import time rather than at runtime.

2. Fact vs Metric separation (mirrors the VERIFIED FACT / CALCULATED METRIC
   distinction required in reports):
   - FinancialFact: what the company REPORTED, as filed, with its source
     accession number. Never computed, never estimated.
   - FinancialMetric: what WE calculated from facts (growth, margins, ...),
     storing its formula name and input values so any number can be
     reproduced by hand.

3. Provenance everywhere: every fact/metric row carries the SEC accession
   number (identifies the exact filing), the filing date, and the source
   URL. This is what makes claims in research reports verifiable.

4. Restatement transparency: facts store `value_original` + `is_restated`.
   When a later filing restates a period, we keep BOTH values -- the original
   as first reported and the restated one -- instead of silently overwriting.
   Historical analysis needs the originally-reported figure; forward-looking
   analysis needs the restated one.

5. SQLite compatibility: only portable column types (String, Float, DateTime,
   Boolean, Text) are used so tests can run on in-memory SQLite while
   production uses PostgreSQL. No PostgreSQL-only types (JSONB, ARRAY) --
   those would make the test suite require a live Postgres.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    """Timezone-aware UTC timestamp -- naive datetimes are a classic bug
    source when servers span timezones."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class Company(Base):
    """A company tracked by FinSight, identified by its SEC CIK."""

    __tablename__ = "companies"

    # SEC CIK is the canonical, permanent identifier (tickers can change).
    cik: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Fiscal year end month (e.g. 6 for Microsoft's June FY-end) -- needed to
    # interpret periods correctly for non-calendar fiscal years.
    fiscal_year_end_month: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    facts: Mapped[list["FinancialFact"]] = relationship(back_populates="company")


class FinancialFact(Base):
    """A single as-filed financial fact from an SEC filing (VERIFIED FACT layer).

    One row = one (company, metric, period, value) as reported in one filing.
    Multiple rows can exist for the same period when filings restate values;
    `is_restated` + `value_original` preserve the change history.
    """

    __tablename__ = "financial_facts"
    __table_args__ = (
        # Fast lookups: "all revenue facts for MSFT" is the most common query.
        Index("ix_facts_company_metric", "cik", "metric"),
        Index("ix_facts_period", "cik", "fiscal_year", "fiscal_period"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    cik: Mapped[int] = mapped_column(ForeignKey("companies.cik"), nullable=False)
    metric: Mapped[str] = mapped_column(String(64), nullable=False)  # Metric enum value

    value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)  # "USD" or "shares"

    period_type: Mapped[str] = mapped_column(String(16), nullable=False)  # PeriodType
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    fiscal_period: Mapped[str] = mapped_column(String(4), nullable=False)  # FY/Q1..Q4
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- Provenance: which filing reported this fact ---
    source_concept: Mapped[str] = mapped_column(String(128), nullable=False)
    form: Mapped[str] = mapped_column(String(16), nullable=False)  # 10-K, 10-Q, 8-K
    accession_number: Mapped[str] = mapped_column(String(25), nullable=False)
    filed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_url: Mapped[str | None] = mapped_column(String(512))

    # --- Restatement tracking ---
    is_restated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    value_original: Mapped[float | None] = mapped_column(Float)  # value before restatement

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    company: Mapped["Company"] = relationship(back_populates="facts")


class FinancialMetric(Base):
    """A calculated metric derived from facts (CALCULATED METRIC layer).

    Every row stores the formula name and its input values (as JSON text) so
    any displayed number can be reproduced by hand -- the audit requirement
    for a financial research system.
    """

    __tablename__ = "financial_metrics"
    __table_args__ = (
        # One calculated value per (company, metric, period); recalculation
        # overwrites (upsert), so the table always holds the latest engine
        # version for each period.
        UniqueConstraint(
            "cik", "metric", "fiscal_year", "fiscal_period", name="uq_metric_period"
        ),
        Index("ix_metrics_company", "cik", "metric"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    cik: Mapped[int] = mapped_column(ForeignKey("companies.cik"), nullable=False)
    metric: Mapped[str] = mapped_column(String(64), nullable=False)

    value: Mapped[float | None] = mapped_column(Float)  # None = not computable
    unit: Mapped[str] = mapped_column(String(16), nullable=False)  # "ratio", "USD", ...

    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    fiscal_period: Mapped[str] = mapped_column(String(4), nullable=False)
    period_type: Mapped[str] = mapped_column(String(16), nullable=False)

    # --- Provenance: how this metric was calculated ---
    formula: Mapped[str] = mapped_column(String(64), nullable=False)  # e.g. "operating_margin"
    inputs_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    source_accessions_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
