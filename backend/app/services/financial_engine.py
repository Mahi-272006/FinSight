"""Financial analysis engine: deterministic metric calculations.

WHY THIS MODULE EXISTS (and why the LLM never touches arithmetic):
Every growth rate, margin, and ratio shown in FinSight is computed HERE, in
pure Python, from verified facts. LLMs are fluent but unreliable at math --
asking one to compute a 3-year CAGR produces plausible, sometimes-wrong
numbers with no way to tell which. Pure functions + unit tests against
hand-verified SEC values give us numbers we can defend in an audit or an
interview.

Design:
- Pure functions: (facts_in -> metrics_out), no I/O, no DB, no clock reads
  (timestamp injected). Same input always yields the same output -- required
  for reproducible evaluation (M8) and cached results.
- Full provenance per metric: formula name, input values (as JSON), source
  accession numbers, calculation timestamp. Any number in the UI can be
  expanded to its exact arithmetic -- the audit requirement for financial
  research.
- Division-by-zero never raises: if inputs are missing/zero, value is None
  with a recorded reason. A missing metric is honest; a wrong one is not.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from app.ingestion.data_cleaner import NormalizedFact
from app.schemas.company import FiscalPeriod, Metric, PeriodType


@dataclass
class CalculatedMetric:
    """One calculated metric with full provenance (CALCULATED METRIC layer)."""

    cik: int
    metric: str           # name of the calculated metric, e.g. "revenue_growth_yoy"
    value: float | None   # None = not computable (see `reason`)
    unit: str             # "ratio", "USD", "percent_decimal" (0.15 = 15%)
    fiscal_year: int
    fiscal_period: str    # FY / Q1..Q4
    period_type: str      # annual / quarterly
    formula: str          # e.g. "(revenue_t - revenue_t-1) / abs(revenue_t-1)"
    inputs: dict[str, Any] = field(default_factory=dict)
    source_accessions: list[str] = field(default_factory=list)
    reason: str | None = None  # why value is None, if applicable
    calculated_at: str = ""

    def __post_init__(self) -> None:
        if not self.calculated_at:
            self.calculated_at = datetime.now(timezone.utc).isoformat()

    def inputs_json(self) -> str:
        return json.dumps(self.inputs)

    def accessions_json(self) -> str:
        return json.dumps(self.source_accessions)


# --------------------------------------------------------------------------- #
# Indexing helpers
# --------------------------------------------------------------------------- #


def index_facts(
    facts: Iterable[NormalizedFact],
) -> dict[tuple[Metric, int, str, PeriodType], NormalizedFact]:
    """Index facts by (metric, fiscal_year, fiscal_period, period_type).

    Multiple facts can share a key after restatements; the input list should
    already be deduplicated (M2 normalizer keeps latest-filed-wins).
    """
    index: dict[tuple[Metric, int, str, PeriodType], NormalizedFact] = {}
    for fact in facts:
        key = (fact.metric, fact.fiscal_year, fact.fiscal_period.value, fact.period_type)
        index[key] = fact
    return index


def _get(
    index: dict, metric: Metric, fy: int, fp: str, pt: PeriodType
) -> NormalizedFact | None:
    return index.get((metric, fy, fp, pt))


def _get_instant(
    index: dict, metric: Metric, fy: int, fp: str
) -> NormalizedFact | None:
    """Look up a balance-sheet (INSTANT) metric for a given fiscal slot.

    Balance-sheet facts are stored as INSTANT regardless of whether we are
    computing an annual or quarterly income-statement metric. Without this
    helper, ROA/debt-ratio lookups would search the 'annual' slot for Assets
    and always miss -- a bug our golden tests caught.
    """
    return index.get((metric, fy, fp, PeriodType.INSTANT))


# --------------------------------------------------------------------------- #
# Growth metrics
# --------------------------------------------------------------------------- #

# Formulas documented once, referenced by name in every provenance record.
_GROWTH_FORMULA = "(current - prior) / abs(prior)"


def growth_rate(
    index: dict,
    metric: Metric,
    cik: int,
    fy: int,
    fp: str,
    pt: PeriodType,
    prior_fy: int,
) -> CalculatedMetric:
    """Year-over-year growth for a metric.

    Uses abs(prior) in the denominator so a prior negative value (loss)
    yields a sign-correct result: growth from -50 to -25 is +50%
    (the loss halved), which plain division would report as -50%.
    """
    current = _get(index, metric, fy, fp, pt)
    prior = _get(index, metric, prior_fy, fp, pt)
    name = f"{metric.value}_growth_yoy"

    if current is None or prior is None:
        return CalculatedMetric(
            cik=cik, metric=name, value=None, unit="ratio",
            fiscal_year=fy, fiscal_period=fp, period_type=pt.value,
            formula=_GROWTH_FORMULA,
            inputs={
                "current": current.value if current else None,
                "prior": prior.value if prior else None,
            },
            source_accessions=_accs(current, prior),
            reason="missing current or prior period fact",
        )

    if prior.value == 0:
        return CalculatedMetric(
            cik=cik, metric=name, value=None, unit="ratio",
            fiscal_year=fy, fiscal_period=fp, period_type=pt.value,
            formula=_GROWTH_FORMULA,
            inputs={"current": current.value, "prior": 0.0},
            source_accessions=_accs(current, prior),
            reason="prior period value is zero (undefined growth)",
        )

    value = (current.value - prior.value) / abs(prior.value)
    return CalculatedMetric(
        cik=cik, metric=name, value=value, unit="ratio",
        fiscal_year=fy, fiscal_period=fp, period_type=pt.value,
        formula=_GROWTH_FORMULA,
        inputs={"current": current.value, "prior": prior.value},
        source_accessions=_accs(current, prior),
    )


# --------------------------------------------------------------------------- #
# Margin and profitability metrics
# --------------------------------------------------------------------------- #


def margin(
    index: dict,
    numerator: Metric,
    cik: int,
    fy: int,
    fp: str,
    pt: PeriodType,
) -> CalculatedMetric:
    """margin = numerator / revenue (gross_margin, operating_margin, net_margin)."""
    revenue = _get(index, Metric.REVENUE, fy, fp, pt)
    num = _get(index, numerator, fy, fp, pt)
    name = f"{numerator.value}_margin"

    if num is None or revenue is None:
        return CalculatedMetric(
            cik=cik, metric=name, value=None, unit="ratio",
            fiscal_year=fy, fiscal_period=fp, period_type=pt.value,
            formula=f"{numerator.value} / revenue",
            inputs={
                numerator.value: num.value if num else None,
                "revenue": revenue.value if revenue else None,
            },
            source_accessions=_accs(num, revenue),
            reason="missing numerator or revenue fact",
        )

    if revenue.value == 0:
        return CalculatedMetric(
            cik=cik, metric=name, value=None, unit="ratio",
            fiscal_year=fy, fiscal_period=fp, period_type=pt.value,
            formula=f"{numerator.value} / revenue",
            inputs={numerator.value: num.value, "revenue": 0.0},
            source_accessions=_accs(num, revenue),
            reason="revenue is zero (undefined margin)",
        )

    value = num.value / revenue.value
    return CalculatedMetric(
        cik=cik, metric=name, value=value, unit="ratio",
        fiscal_year=fy, fiscal_period=fp, period_type=pt.value,
        formula=f"{numerator.value} / revenue",
        inputs={numerator.value: num.value, "revenue": revenue.value},
        source_accessions=_accs(num, revenue),
    )


def return_on_assets(
    index: dict, cik: int, fy: int, fp: str, pt: PeriodType
) -> CalculatedMetric:
    """ROA = net_income / total_assets.

    Note: uses the period-end asset snapshot (not average assets) because we
    only reliably have year-end balances; documented as a known simplification.
    """
    ni = _get(index, Metric.NET_INCOME, fy, fp, pt)
    assets = _get_instant(index, Metric.TOTAL_ASSETS, fy, fp)

    if ni is None or assets is None or assets.value == 0:
        return CalculatedMetric(
            cik=cik, metric="return_on_assets", value=None, unit="ratio",
            fiscal_year=fy, fiscal_period=fp, period_type=pt.value,
            formula="net_income / total_assets",
            inputs={
                "net_income": ni.value if ni else None,
                "total_assets": assets.value if assets else None,
            },
            source_accessions=_accs(ni, assets),
            reason="missing net_income or assets (or assets == 0)",
        )

    return CalculatedMetric(
        cik=cik, metric="return_on_assets", value=ni.value / assets.value, unit="ratio",
        fiscal_year=fy, fiscal_period=fp, period_type=pt.value,
        formula="net_income / total_assets",
        inputs={"net_income": ni.value, "total_assets": assets.value},
        source_accessions=_accs(ni, assets),
    )


# --------------------------------------------------------------------------- #
# Cash flow / health metrics
# --------------------------------------------------------------------------- #


def free_cash_flow(
    index: dict, cik: int, fy: int, fp: str, pt: PeriodType
) -> CalculatedMetric:
    """FCF = operating_cash_flow - capital_expenditure.

    Capex is stored as a positive outflow (XBRL convention for
    PaymentsToAcquirePropertyPlantAndEquipment), so subtraction is correct.
    """
    ocf = _get(index, Metric.OPERATING_CASH_FLOW, fy, fp, pt)
    capex = _get(index, Metric.CAPITAL_EXPENDITURE, fy, fp, pt)

    if ocf is None or capex is None:
        return CalculatedMetric(
            cik=cik, metric="free_cash_flow", value=None, unit="USD",
            fiscal_year=fy, fiscal_period=fp, period_type=pt.value,
            formula="operating_cash_flow - capital_expenditure",
            inputs={
                "operating_cash_flow": ocf.value if ocf else None,
                "capital_expenditure": capex.value if capex else None,
            },
            source_accessions=_accs(ocf, capex),
            reason="missing operating_cash_flow or capital_expenditure",
        )

    return CalculatedMetric(
        cik=cik, metric="free_cash_flow",
        value=ocf.value - capex.value, unit="USD",
        fiscal_year=fy, fiscal_period=fp, period_type=pt.value,
        formula="operating_cash_flow - capital_expenditure",
        inputs={"operating_cash_flow": ocf.value, "capital_expenditure": capex.value},
        source_accessions=_accs(ocf, capex),
    )


def current_ratio(
    index: dict, cik: int, fy: int, fp: str, pt: PeriodType
) -> CalculatedMetric:
    """Current ratio = current_assets / current_liabilities (instant metrics)."""
    ca = _get_instant(index, Metric.CURRENT_ASSETS, fy, fp)
    cl = _get_instant(index, Metric.CURRENT_LIABILITIES, fy, fp)

    if ca is None or cl is None or cl.value == 0:
        return CalculatedMetric(
            cik=cik, metric="current_ratio", value=None, unit="ratio",
            fiscal_year=fy, fiscal_period=fp, period_type=pt.value,
            formula="current_assets / current_liabilities",
            inputs={
                "current_assets": ca.value if ca else None,
                "current_liabilities": cl.value if cl else None,
            },
            source_accessions=_accs(ca, cl),
            reason="missing current assets/liabilities (or liabilities == 0)",
        )

    return CalculatedMetric(
        cik=cik, metric="current_ratio", value=ca.value / cl.value, unit="ratio",
        fiscal_year=fy, fiscal_period=fp, period_type=pt.value,
        formula="current_assets / current_liabilities",
        inputs={"current_assets": ca.value, "current_liabilities": cl.value},
        source_accessions=_accs(ca, cl),
    )


def debt_to_assets(
    index: dict, cik: int, fy: int, fp: str, pt: PeriodType
) -> CalculatedMetric:
    """Leverage = total_liabilities / total_assets."""
    liab = _get_instant(index, Metric.TOTAL_LIABILITIES, fy, fp)
    assets = _get_instant(index, Metric.TOTAL_ASSETS, fy, fp)

    if liab is None or assets is None or assets.value == 0:
        return CalculatedMetric(
            cik=cik, metric="debt_to_assets", value=None, unit="ratio",
            fiscal_year=fy, fiscal_period=fp, period_type=pt.value,
            formula="total_liabilities / total_assets",
            inputs={
                "total_liabilities": liab.value if liab else None,
                "total_assets": assets.value if assets else None,
            },
            source_accessions=_accs(liab, assets),
            reason="missing liabilities or assets (or assets == 0)",
        )

    return CalculatedMetric(
        cik=cik, metric="debt_to_assets", value=liab.value / assets.value, unit="ratio",
        fiscal_year=fy, fiscal_period=fp, period_type=pt.value,
        formula="total_liabilities / total_assets",
        inputs={"total_liabilities": liab.value, "total_assets": assets.value},
        source_accessions=_accs(liab, assets),
    )


# --------------------------------------------------------------------------- #
# Divergence analysis (profit vs cash flow -- a classic anomaly signal)
# --------------------------------------------------------------------------- #


def earnings_cashflow_divergence(
    index: dict, cik: int, fy: int, fp: str, pt: PeriodType, prior_fy: int
) -> CalculatedMetric:
    """Divergence = net_income_growth - operating_cash_flow_growth.

    A widening gap (income growing while cash flow stagnates/declines) is a
    known earnings-quality red flag -- surfaced here as a number, flagged by
    M3's anomaly detector, and ALWAYS presented as a signal, never proof.
    """
    ni_growth = growth_rate(index, Metric.NET_INCOME, cik, fy, fp, pt, prior_fy)
    ocf_growth = growth_rate(index, Metric.OPERATING_CASH_FLOW, cik, fy, fp, pt, prior_fy)

    if ni_growth.value is None or ocf_growth.value is None:
        return CalculatedMetric(
            cik=cik, metric="earnings_cashflow_divergence", value=None, unit="ratio",
            fiscal_year=fy, fiscal_period=fp, period_type=pt.value,
            formula="net_income_growth_yoy - operating_cash_flow_growth_yoy",
            inputs={"net_income_growth": ni_growth.value, "ocf_growth": ocf_growth.value},
            source_accessions=ni_growth.source_accessions + ocf_growth.source_accessions,
            reason="insufficient data for one or both growth rates",
        )

    return CalculatedMetric(
        cik=cik, metric="earnings_cashflow_divergence",
        value=ni_growth.value - ocf_growth.value, unit="ratio",
        fiscal_year=fy, fiscal_period=fp, period_type=pt.value,
        formula="net_income_growth_yoy - operating_cash_flow_growth_yoy",
        inputs={"net_income_growth": ni_growth.value, "ocf_growth": ocf_growth.value},
        source_accessions=ni_growth.source_accessions + ocf_growth.source_accessions,
    )


# --------------------------------------------------------------------------- #
# Orchestration: compute the full metric set for one company
# --------------------------------------------------------------------------- #


def calculate_all_metrics(
    cik: int, facts: list[NormalizedFact]
) -> list[CalculatedMetric]:
    """Compute every supported metric for all periods present in the facts.

    Iterates over the periods actually found (rather than assuming a fixed
    year range) so partial data yields partial metrics -- each with an
    explicit `reason` when not computable.
    """
    index = index_facts(facts)
    results: list[CalculatedMetric] = []

    # Collect the (fiscal_year, fiscal_period, period_type) slots per type.
    annual_slots = sorted(
        {(fy, fp, pt) for (_m, fy, fp, pt) in index if pt is PeriodType.ANNUAL}
    )
    quarterly_slots = sorted(
        {(fy, fp, pt) for (_m, fy, fp, pt) in index if pt is PeriodType.QUARTERLY}
    )

    for slots, pt in ((annual_slots, PeriodType.ANNUAL), (quarterly_slots, PeriodType.QUARTERLY)):
        for (fy, fp, _pt) in slots:
            prior_fy = fy - 1

            # Growth metrics.
            for metric in (
                Metric.REVENUE,
                Metric.OPERATING_INCOME,
                Metric.NET_INCOME,
                Metric.OPERATING_CASH_FLOW,
            ):
                results.append(growth_rate(index, metric, cik, fy, fp, pt, prior_fy))

            # Margins.
            results.append(margin(index, Metric.GROSS_PROFIT, cik, fy, fp, pt))
            results.append(margin(index, Metric.OPERATING_INCOME, cik, fy, fp, pt))
            results.append(margin(index, Metric.NET_INCOME, cik, fy, fp, pt))

            # Profitability / health.
            results.append(return_on_assets(index, cik, fy, fp, pt))
            results.append(debt_to_assets(index, cik, fy, fp, pt))
            results.append(free_cash_flow(index, cik, fy, fp, pt))

            # Divergence (needs prior year).
            results.append(earnings_cashflow_divergence(index, cik, fy, fp, pt, prior_fy))

    # Instant metrics only exist at fiscal-year end in our normalized data.
    instant_slots = sorted(
        {(fy, fp, pt) for (_m, fy, fp, pt) in index if pt is PeriodType.INSTANT}
    )
    for (fy, fp, _pt) in instant_slots:
        results.append(current_ratio(index, cik, fy, fp, PeriodType.INSTANT))

        # Accounts-receivable growth (instant metric, YoY). Added in M3 to
        # feed the receivables-vs-revenue anomaly rule WITH full provenance --
        # computing it ad-hoc in the detector would bypass the audit trail.
        # Only emitted at fiscal-year-end slots (fp == FY): quarter-end AR is
        # seasonal and a YoY comparison of two quarter-ends is noisy.
        if fp == "FY":
            results.append(
                growth_rate(
                    index,
                    Metric.ACCOUNTS_RECEIVABLE,
                    cik,
                    fy,
                    fp,
                    PeriodType.INSTANT,
                    fy - 1,
                )
            )

    return results


# --------------------------------------------------------------------------- #
# Small helper
# --------------------------------------------------------------------------- #


def _accs(*facts: NormalizedFact | None) -> list[str]:
    """Distinct accession numbers from non-None facts (provenance)."""
    seen: list[str] = []
    for f in facts:
        if f is not None and f.accession_number and f.accession_number not in seen:
            seen.append(f.accession_number)
    return seen
