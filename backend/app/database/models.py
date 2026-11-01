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


class InvestigationSignal(Base):
    """One anomaly-detection finding for one company-period (SIGNAL layer).

    Deliberately named 'InvestigationSignal', not 'Anomaly' or 'Fraud': the
    project rule is that anomalies are prompts for ANALYST REVIEW, never
    verdicts. The LLM layer (M5/M6) will interpret these; it never creates
    them. Rows are upserted (unique on company+type+period) so re-running
    detection refreshes rather than duplicates.

    Evidence/provenance columns mirror FinancialMetric: the triggering input
    values, the threshold, and the formula are stored so any signal shown in
    the UI can be expanded to its exact arithmetic.
    """

    __tablename__ = "investigation_signals"
    __table_args__ = (
        UniqueConstraint(
            "cik", "signal_type", "fiscal_year", "fiscal_period",
            name="uq_signal_period",
        ),
        Index("ix_signals_company", "cik", "signal_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    cik: Mapped[int] = mapped_column(ForeignKey("companies.cik"), nullable=False)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    signal_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)  # elevated / high

    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    fiscal_period: Mapped[str] = mapped_column(String(4), nullable=False)
    period_type: Mapped[str] = mapped_column(String(16), nullable=False)

    headline: Mapped[str] = mapped_column(String(512), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    source_accessions_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ResearchReport(Base):
    """Durable snapshot of one actual ResearchResult (M9 Stage 7)."""

    __tablename__ = "research_reports"
    __table_args__ = (
        Index("ix_reports_ticker_created", "ticker", "created_at", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cik: Mapped[int] = mapped_column(ForeignKey("companies.cik"), nullable=False)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WatchlistEntry(Base):
    """One watchlist membership per canonical company CIK."""

    __tablename__ = "watchlist_entries"

    cik: Mapped[int] = mapped_column(
        ForeignKey("companies.cik", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    company: Mapped["Company"] = relationship(lazy="joined")


# ---------------------------------------------------------------------------
# M7: Investment thesis monitoring
#
# Three tables with three different write policies, chosen per table:
# - theses: mutable pointer row (status = latest assessment, NULL = never
#   assessed); history lives in the assessments table, never here.
# - thesis_conditions: immutable after creation (the commitments of a
#   thesis don't change; if the investor changes their mind they write a
#   new thesis).
# - thesis_assessments: append-only; no updated_at column exists, so an
#   "edit" would require schema change + service method -- deliberately
#   absent. History is a fact about the past.
# ---------------------------------------------------------------------------


class Thesis(Base):
    """An investment thesis for one company (M7).

    A thesis is a prose statement ("NVDA sustains double-digit growth
    despite export controls") PLUS measurable conditions that commit the
    statement to numbers. Assessment evaluates the conditions in pure
    Python (VERIFIED layer); an optional LLM summarizes what the verdicts
    mean (interpretive layer). Keeping the two apart is the same
    fact/signal/interpretation split as M2/M3, applied to opinions.

    status is NULL until the first assessment ("Unassessed"). After that
    it caches the latest verdict; the full timeline stays in
    thesis_assessments.
    """

    __tablename__ = "theses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    cik: Mapped[int] = mapped_column(ForeignKey("companies.cik"), nullable=False, index=True)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)  # denormalized for display

    statement: Mapped[str] = mapped_column(Text, nullable=False)
    horizon: Mapped[str] = mapped_column(String(64), nullable=False)  # e.g. "FY2026", "12 months"

    status: Mapped[str | None] = mapped_column(String(32), nullable=True)  # NULL = Unassessed
    status_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    conditions: Mapped[list["ThesisCondition"]] = relationship(
        back_populates="thesis", cascade="all, delete-orphan", lazy="selectin"
    )


class ThesisCondition(Base):
    """One measurable commitment of a thesis (M7).

    A condition is (metric, comparator, threshold) over one period of the
    M2 metric/fact tables -- e.g. `revenue_growth_yoy >= 0.10` for FY2026.
    fiscal_year/fiscal_period are nullable meaning "latest available
    period", so "growth stays above 10%" doesn't hardcode a filing that
    doesn't exist yet.

    evidence_json snapshots the metric's value AT CREATION (value, tier
    CALCULATED/VERIFIED, formula, accessions): the thesis is judged
    against the world as the investor saw it, and any later restatement
    shows up as drift between snapshot and assessment instead of
    silently rewriting history.

    Immutable after creation -- no service updates this row.
    """

    __tablename__ = "thesis_conditions"
    __table_args__ = (Index("ix_conditions_thesis", "thesis_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    thesis_id: Mapped[int] = mapped_column(
        ForeignKey("theses.id", ondelete="CASCADE"), nullable=False
    )

    description: Mapped[str] = mapped_column(String(512), nullable=False)  # human wording
    metric: Mapped[str] = mapped_column(String(64), nullable=False)  # M2 metric/fact name
    comparator: Mapped[str] = mapped_column(String(4), nullable=False)  # >= <= > < ==
    threshold: Mapped[float] = mapped_column(Float, nullable=False)

    fiscal_year: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None = latest
    fiscal_period: Mapped[str | None] = mapped_column(String(4), nullable=True)  # None = latest

    evidence_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    thesis: Mapped["Thesis"] = relationship(back_populates="conditions")


class ThesisAssessment(Base):
    """One point-in-time evaluation of a thesis (M7, APPEND-ONLY).

    Layers stored side by side, each labelled:
    - summary_json: VERIFIED layer -- per-condition verdict (met /
      not_met / incomplete) with the evaluated number, tier and
      provenance (formula or accession) as computed by Python.
    - explanation + explanation_source: interpretive prose with a record
      of WHICH layer produced it (`llm:groq/...` vs
      `deterministic:condition-counts`) -- provenance discipline applied
      to words, not just numbers.
    - signals_json: M3 investigation signals shown as CONTEXT ONLY.
      They never enter status derivation: an anomaly alone can never
      move a thesis toward Contradicted (the rule is enforced in
      code and pinned by test, not by prompt).

    No updated_at column: append-only is structural, and each row keeps
    status_changed_from so transitions are readable directly from the
    timeline.
    """

    __tablename__ = "thesis_assessments"
    __table_args__ = (Index("ix_assessments_thesis", "thesis_id", "assessed_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    thesis_id: Mapped[int] = mapped_column(
        ForeignKey("theses.id", ondelete="CASCADE"), nullable=False
    )

    status: Mapped[str] = mapped_column(String(32), nullable=False)  # Supported/Weakened/...
    status_changed_from: Mapped[str | None] = mapped_column(String(32), nullable=True)

    summary_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    explanation_source: Mapped[str] = mapped_column(String(96), nullable=False, default="")

    evidence_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    signals_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    assessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
