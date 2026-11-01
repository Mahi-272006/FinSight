"""Rule-based anomaly detection: deterministic INVESTIGATION SIGNALS.

THE GOVERNING PRINCIPLE (from the project brief): an anomaly is a signal to
INVESTIGATE, never a verdict of wrongdoing. Every function here returns
evidence a human analyst can act on; none returns a conclusion. The LLM layer
(M5/M6) will INTERPRET these signals in context -- it never CREATES them,
because a fluent model inventing thresholds is exactly the failure mode this
architecture exists to prevent.

Design decisions:
- DETERMINISTIC RULES with DOCUMENTED, UNIT-TESTED thresholds. No learned
  parameters in M3 (ML statistical flags arrive in M3.5+; the shape here
  reserves a `kind` field so rule-based and ML signals can coexist and be
  distinguished in reports).
- EVIDENCE-READY: every signal carries the input values that triggered it,
  the threshold, the formula, and source accession numbers -- the audit
  requirement inherited from M2's engine.
- MISSING DATA NEVER FIRES A SIGNAL. A rule with absent inputs returns
  nothing for that period (a missing input is not evidence of a problem;
  firing on gaps would train users to ignore the tool).
- SEVERITY is a label for triage ordering, not a probability. Two levels
  (ELEVATED / HIGH) with documented thresholds; below-threshold observations
  are recorded in the trend table but are not signals.
- SIGN AWARENESS: divergence only fires in the DANGEROUS direction (income
  up, cash flow down). Income and cash flow both growing but at different
  rates is normal noise, not a signal.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from app.schemas.company import FiscalPeriod, Metric, PeriodType, SignalType
from app.services.trend import trailing_streak

Severity = Literal["elevated", "high"]

# ---------------------------------------------------------------------------
# Signal record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InvestigationSignal:
    """One rule's finding for one company-period. A prompt for review."""

    cik: int
    ticker: str
    signal_type: str            # stable identifier, e.g. "earnings_cashflow_divergence"
    severity: Severity
    fiscal_year: int
    fiscal_period: str          # FY / Q1..Q4
    period_type: str            # annual / quarterly

    headline: str               # one-line, numbers included
    explanation: str            # why this fired, in plain language
    evidence: dict[str, Any]    # the actual input values + threshold + formula
    source_accessions: list[str] = field(default_factory=list)
    detected_at: str = ""

    def __post_init__(self) -> None:
        if not self.detected_at:
            # Frozen dataclass: use object.__setattr__ rather than mutation.
            object.__setattr__(
                self, "detected_at", datetime.now(timezone.utc).isoformat()
            )


# ---------------------------------------------------------------------------
# Rule 1: earnings-cash-flow divergence
# ---------------------------------------------------------------------------

# Thresholds (documented, unit-tested). Divergence = NI growth - OCF growth.
# 0.20 = income growing >=20pp faster than cash flow (ELEVATED);
# 0.50 = extreme gap (HIGH). Rationale: working-capital swings move cash
# around legitimately, so single-year gaps are common; the SIGNAL is for gaps
# large enough to merit an MD&A read, not proof of anything.
DIVERGENCE_ELEVATED = 0.20
DIVERGENCE_HIGH = 0.50


def check_earnings_cashflow_divergence(
    divergence_value: float | None,
    ni_growth: float | None,
    ocf_growth: float | None,
    cik: int,
    ticker: str,
    fiscal_year: int,
    fiscal_period: str,
    period_type: str,
    accessions: list[str] | None = None,
) -> InvestigationSignal | None:
    """Fire when net income growth outruns operating cash-flow growth.

    Both growth rates are required (a None means the engine could not compute
    one -- no signal). Only the dangerous direction fires: income accelerating
    AWAY from cash flow. The reverse (cash flowing faster than book income)
    is conservative accounting, not a concern.
    """
    if divergence_value is None or ni_growth is None or ocf_growth is None:
        return None
    # Dangerous direction only: income growth must EXCEED cash-flow growth,
    # and income must actually be growing (a shrinking-loss situation can
    # produce a positive divergence with both negative -- not the red flag).
    if divergence_value <= 0 or ni_growth <= 0:
        return None
    if divergence_value < DIVERGENCE_ELEVATED:
        return None

    severity: Severity = "high" if divergence_value >= DIVERGENCE_HIGH else "elevated"
    return InvestigationSignal(
        cik=cik,
        ticker=ticker,
        signal_type=SignalType.EARNINGS_CASHFLOW_DIVERGENCE.value,
        severity=severity,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        period_type=period_type,
        headline=(
            f"Net income grew {ni_growth*100:.1f}% while operating cash flow grew "
            f"{ocf_growth*100:.1f}% (gap {divergence_value*100:.1f}pp)"
        ),
        explanation=(
            "Reported profit is growing faster than the cash the business actually "
            "generates. Legitimate causes include working-capital build, timing of "
            "collections, or acquisitions. This is a prompt to review the cash-flow "
            "statement and MD&A -- NOT a finding of misstatement."
        ),
        evidence={
            "net_income_growth": ni_growth,
            "operating_cash_flow_growth": ocf_growth,
            "divergence": divergence_value,
            "threshold_elevated": DIVERGENCE_ELEVATED,
            "threshold_high": DIVERGENCE_HIGH,
            "formula": "net_income_growth_yoy - operating_cash_flow_growth_yoy",
        },
        source_accessions=list(accessions or []),
    )


# ---------------------------------------------------------------------------
# Rule 2: receivables outpacing revenue
# ---------------------------------------------------------------------------

# Thresholds. AR growth - revenue growth >= 25pp ELEVATED, >= 50pp HIGH.
# Rationale: receivables growing much faster than sales means customers take
# longer to pay (or revenue is recognized before collection is likely). A
# one-year spike can be a single large customer's timing; sustained gaps are
# the concerning pattern (M3.5 adds persistence; M3 flags the spike).
AR_GAP_ELEVATED = 0.25
AR_GAP_HIGH = 0.50


def check_receivables_outpacing_revenue(
    ar_growth: float | None,
    revenue_growth: float | None,
    cik: int,
    ticker: str,
    fiscal_year: int,
    fiscal_period: str,
    period_type: str,
    ar_value: float | None = None,
    revenue_value: float | None = None,
    accessions: list[str] | None = None,
) -> InvestigationSignal | None:
    """Fire when accounts-receivable growth exceeds revenue growth.

    Requires both growth rates (None -> no signal). Fires only when AR grows
    FASTER than revenue AND AR itself grew (a shrinking AR base with faster
    shrinkage than revenue is a collection improvement, not a concern).
    """
    if ar_growth is None or revenue_growth is None:
        return None
    if ar_growth <= 0 or ar_growth <= revenue_growth:
        return None
    gap = ar_growth - revenue_growth
    if gap < AR_GAP_ELEVATED:
        return None

    severity: Severity = "high" if gap >= AR_GAP_HIGH else "elevated"
    return InvestigationSignal(
        cik=cik,
        ticker=ticker,
        signal_type=SignalType.RECEIVABLES_OUTPACING_REVENUE.value,
        severity=severity,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        period_type=period_type,
        headline=(
            f"Receivables grew {ar_growth*100:.1f}% vs revenue {revenue_growth*100:.1f}% "
            f"(gap {gap*100:.1f}pp)"
        ),
        explanation=(
            "Amounts owed by customers are growing faster than sales, which can "
            "indicate slower collections, looser credit terms, or revenue timing. "
            "Review days-sales-outstanding and the receivables note. This is a "
            "prompt for review, NOT a finding of misstatement."
        ),
        evidence={
            "accounts_receivable_growth": ar_growth,
            "revenue_growth": revenue_growth,
            "gap": gap,
            "accounts_receivable_value": ar_value,
            "revenue_value": revenue_value,
            "threshold_elevated": AR_GAP_ELEVATED,
            "threshold_high": AR_GAP_HIGH,
            "formula": "ar_growth_yoy - revenue_growth_yoy",
        },
        source_accessions=list(accessions or []),
    )


# ---------------------------------------------------------------------------
# Rule 3: sustained margin compression
# ---------------------------------------------------------------------------

# Threshold: operating margin declined for >= 3 consecutive fiscal years.
# Rationale: one down year is noise; three straight declines is a trend
# management must explain. Severity scales with streak length.
MARGIN_STREAK_ELEVATED = 3
MARGIN_STREAK_HIGH = 5


def check_margin_compression_streak(
    operating_margins_by_year: dict[int, float],
    cik: int,
    ticker: str,
    fiscal_period: str = "FY",
    period_type: str = "annual",
    accessions_by_year: dict[int, list[str]] | None = None,
) -> list[InvestigationSignal]:
    """Fire for every fiscal year at the END of a >=3-year margin decline run.

    Takes {fiscal_year: operating_margin} (None values must be pre-filtered --
    a gap breaks the streak; callers pass only years with values). Emits a
    signal at each year where the trailing decline count reaches a threshold,
    so the UI can show the streak developing over time rather than only its
    final year.
    """
    signals: list[InvestigationSignal] = []
    years = sorted(operating_margins_by_year)
    if len(years) < 2:
        return signals

    for idx, year in enumerate(years):
        # A missing fiscal year breaks a consecutive streak. Restrict each
        # calculation to the contiguous run ending in this fiscal year.
        start_idx = idx
        while start_idx > 0 and years[start_idx] == years[start_idx - 1] + 1:
            start_idx -= 1
        sub_series = [
            operating_margins_by_year[point_year]
            for point_year in years[start_idx : idx + 1]
        ]
        streak = trailing_streak(sub_series, "down")
        # streak == k declines means k+1 consecutive declining periods ending
        # this year; we require >= MARGIN_STREAK_ELEVATED declines.
        if streak < MARGIN_STREAK_ELEVATED:
            continue
        severity: Severity = "high" if streak >= MARGIN_STREAK_HIGH else "elevated"
        start_year = years[idx - streak]
        first = operating_margins_by_year.get(start_year)
        last = operating_margins_by_year[year]
        signals.append(
            InvestigationSignal(
                cik=cik,
                ticker=ticker,
                signal_type=SignalType.MARGIN_COMPRESSION_STREAK.value,
                severity=severity,
                fiscal_year=year,
                fiscal_period=fiscal_period,
                period_type=period_type,
                headline=(
                    f"Operating margin declined {streak} consecutive years "
                    f"(to {last*100:.1f}% in FY{year})"
                ),
                explanation=(
                    f"Operating margin fell every year from FY{start_year} to FY{year}. "
                    "Sustained compression points to pricing pressure, cost inflation, "
                    "or mix shift. Review segment disclosures and MD&A cost discussion. "
                    "A trend prompt, NOT a finding of misstatement."
                ),
                evidence={
                    "streak_years": streak,
                    "margin_start_year": start_year,
                    "margin_start": first,
                    "margin_end_year": year,
                    "margin_end": last,
                    "threshold_elevated": MARGIN_STREAK_ELEVATED,
                    "threshold_high": MARGIN_STREAK_HIGH,
                    "formula": "trailing_streak(operating_margin series, 'down')",
                },
                source_accessions=list(
                    (accessions_by_year or {}).get(year, [])
                ),
            )
        )
    return signals


# ---------------------------------------------------------------------------
# Rule 4: growth deceleration (second derivative)
# ---------------------------------------------------------------------------

# Thresholds. Revenue growth FELL by >= 15pp vs prior year ELEVATED, >= 30pp
# HIGH. Rationale: a company growing 40% then 10% has decelerated hard --
# often the market's key transition point. Fires only on DEceleration of an
# already-material growth rate (shrinking from 2% to -1% is not a growth
# story; that is a decline rule's job, and it is out of M3's scope).
DECEL_ELEVATED = 0.15
DECEL_HIGH = 0.30
DECEL_MIN_PRIOR_GROWTH = 0.10  # only meaningful if prior growth was >= 10%


def check_growth_deceleration(
    current_growth: float | None,
    prior_growth: float | None,
    cik: int,
    ticker: str,
    fiscal_year: int,
    fiscal_period: str,
    period_type: str,
    accessions: list[str] | None = None,
) -> InvestigationSignal | None:
    """Fire when revenue growth decelerates sharply year over year.

    Requires both years' growth. Guards:
    - prior growth must have been material (>= 10%) so we do not fire on a
      company oscillating around zero;
    - deceleration = prior - current (positive = slowing).
    """
    if current_growth is None or prior_growth is None:
        return None
    if prior_growth < DECEL_MIN_PRIOR_GROWTH:
        return None
    deceleration = prior_growth - current_growth
    if deceleration < DECEL_ELEVATED:
        return None

    severity: Severity = "high" if deceleration >= DECEL_HIGH else "elevated"
    return InvestigationSignal(
        cik=cik,
        ticker=ticker,
        signal_type=SignalType.GROWTH_DECELERATION.value,
        severity=severity,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        period_type=period_type,
        headline=(
            f"Revenue growth slowed from {prior_growth*100:.1f}% to "
            f"{current_growth*100:.1f}% ({deceleration*100:.1f}pp deceleration)"
        ),
        explanation=(
            "The growth rate fell sharply year over year, a transition investors "
            "often under-price. Check demand commentary, guidance, and any one-time "
            "prior-year boost. A trend prompt, NOT a finding of misstatement."
        ),
        evidence={
            "current_growth": current_growth,
            "prior_growth": prior_growth,
            "deceleration": deceleration,
            "threshold_elevated": DECEL_ELEVATED,
            "threshold_high": DECEL_HIGH,
            "min_prior_growth": DECEL_MIN_PRIOR_GROWTH,
            "formula": "prior_growth_yoy - current_growth_yoy",
        },
        source_accessions=list(accessions or []),
    )
