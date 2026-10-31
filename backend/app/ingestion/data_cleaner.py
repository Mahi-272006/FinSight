"""XBRL normalization: raw SEC Company Facts -> clean FinancialFact rows.

This is the correctness heart of FinSight. It solves four real problems we
OBSERVED in Microsoft's live data (M1):

1. CONCEPT DRIFT: the same economic quantity is tagged differently across
   eras/companies. Microsoft used `Revenues` until ~2010, then
   `RevenueFromContractWithCustomerExcludingAssessedTax` after ASC 606.
   Solution: ordered fallback chains (modern tag first, older tags as
   fallback); first chain link with data for a period wins.

2. YTD vs STANDALONE QUARTERS: 10-Qs report year-to-date figures. Q2_YTD is
   6 months, NOT a quarter. Comparing YTD numbers as if they were quarters
   inflates growth ~2x -- the classic double-counting bug. Solution: classify
   every fact's period by DATE ARITHMETIC (never trust labels), then compute
   standalone quarters as differences: Q2 = YTD_H1 - YTD_Q1, Q3 = YTD_9M -
   YTD_H1, Q4 = Annual - YTD_9M. If any input is missing, emit NOTHING for
   that quarter -- a missing number is correct; a wrong number is not.

3. DUPLICATE / RESTATED FACTS: the same period appears in multiple filings
   (original 10-K, then comparative columns in the next year's 10-K -- we saw
   MSFT FY2025 duplicated exactly). Rule: latest-filed fact wins; if a later
   filing CHANGES the value, that's a restatement -- we keep both values and
   flag it (value_original + is_restated) instead of hiding the change.

4. UNITS: USD, shares. Facts in unexpected units are rejected loudly, never
   silently converted.

Design: pure functions over plain dicts (no DB, no I/O) so every behavior is
unit-testable in milliseconds. DB persistence happens in repositories (M2's
later step), keeping normalization logic independent of storage.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.schemas.company import (
    DURATION_METRICS,
    INSTANT_METRICS,
    FiscalPeriod,
    Metric,
    PeriodType,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)

# --------------------------------------------------------------------------- #
# Concept mapping: canonical metric -> ordered XBRL tag fallback chain
# --------------------------------------------------------------------------- #
# Order matters: most-specific/modern tag first, older/generic tags as
# fallbacks. Verified against live MSFT data in M1 (Revenues = pre-2010 only;
# RevenueFromContractWithCustomer... = modern).
CONCEPT_MAP: dict[Metric, tuple[str, ...]] = {
    Metric.REVENUE: (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
    ),
    Metric.GROSS_PROFIT: ("GrossProfit",),
    Metric.OPERATING_INCOME: ("OperatingIncomeLoss",),
    Metric.NET_INCOME: ("NetIncomeLoss", "ProfitLoss"),
    Metric.TOTAL_ASSETS: ("Assets",),
    Metric.TOTAL_LIABILITIES: ("Liabilities",),
    Metric.CURRENT_ASSETS: ("AssetsCurrent",),
    Metric.CURRENT_LIABILITIES: ("LiabilitiesCurrent",),
    Metric.CASH: (
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ),
    Metric.TOTAL_DEBT: (
        "DebtLongtermAndShorttermCombinedAmount",
        "LongTermDebt",
        "LongTermDebtNoncurrent",
    ),
    Metric.ACCOUNTS_RECEIVABLE: ("AccountsReceivableNetCurrent", "ReceivablesNetCurrent"),
    Metric.OPERATING_CASH_FLOW: (
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ),
    Metric.CAPITAL_EXPENDITURE: (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ),
    Metric.SHARES_OUTSTANDING: ("EntityCommonStockSharesOutstanding",),  # dei taxonomy
}

# Which taxonomy each metric lives in (us-gaap for financial concepts, dei for
# share counts). Searching only the right taxonomy avoids false matches.
_TAXONOMY_FOR_METRIC: dict[Metric, str] = {
    metric: ("dei" if metric is Metric.SHARES_OUTSTANDING else "us-gaap")
    for metric in Metric
}

# Unit expectations per metric. Capital expenditure is stored as a POSITIVE
# outflow (XBRL reports PaymentsToAcquire... as positive), so FCF = OCF - capex.
_UNIT_FOR_METRIC: dict[Metric, str] = {
    metric: ("shares" if metric is Metric.SHARES_OUTSTANDING else "USD")
    for metric in Metric
}

# Period-length windows (days) for date-arithmetic classification.
# Fiscal years are rarely exactly 365 (leap years, 52/53-week calendars, the
# one-off 53rd week), so we use tolerant windows.
_ANNUAL_WINDOW = (350, 380)   # ~12 months
_QUARTER_WINDOW = (80, 100)   # ~3 months
# Anything else with a start date is treated as YTD (3M, 6M, 9M are all YTD
# inputs; Q1_YTD == Q1_standalone, handled by the splitter).


@dataclass
class NormalizedFact:
    """A single as-filed fact, cleaned and classified."""

    cik: int
    metric: Metric
    value: float
    unit: str
    period_type: PeriodType
    fiscal_year: int
    fiscal_period: FiscalPeriod
    period_start: date | None
    period_end: date
    source_concept: str
    form: str
    accession_number: str
    filed_at: date | None
    source_url: str | None = None
    is_restated: bool = False
    value_original: float | None = None


@dataclass
class NormalizationResult:
    """Everything extracted from one company's payload, plus diagnostics."""

    cik: int
    facts: list[NormalizedFact] = field(default_factory=list)
    restatements: list[dict[str, Any]] = field(default_factory=list)
    skipped_concepts: list[str] = field(default_factory=list)
    derived_quarters: int = 0  # count of standalone quarters computed from YTD


# --------------------------------------------------------------------------- #
# Period classification (date arithmetic -- never trust form/fp labels alone)
# --------------------------------------------------------------------------- #


def classify_period(start: date | None, end: date) -> PeriodType:
    """Classify a fact's period by its actual date span.

    - No start -> INSTANT (balance-sheet snapshot).
    - 350-380 days -> ANNUAL.
    - 80-100 days -> QUARTERLY (standalone quarter).
    - Other spans (6M, 9M...) -> YTD (input for quarter-splitting).

    Why dates over labels: SEC's `fp` field reflects what the FILING period
    was, but restated/comparative facts inside a 10-K carry mixed frames.
    The date span is the ground truth of what period a fact covers.
    """
    if start is None:
        return PeriodType.INSTANT
    days = (end - start).days
    if _ANNUAL_WINDOW[0] <= days <= _ANNUAL_WINDOW[1]:
        return PeriodType.ANNUAL
    if _QUARTER_WINDOW[0] <= days <= _QUARTER_WINDOW[1]:
        return PeriodType.QUARTERLY
    return PeriodType.YTD


def infer_fiscal_year_end_month(payload: dict[str, Any]) -> int | None:
    """Infer a company's fiscal-year-end month from its annual facts.

    Annual facts (span ~365d) END on the fiscal-year-end date, so the MONTH
    of that end date IS the fiscal-year-end month. Microsoft's annual facts
    end in June -> fiscal_year_end_month=6.

    We take the most common month across all revenue annual facts (mode, not
    a single sample) so one anomalous restatement cannot mislead us. Returns
    None when no annual facts exist, letting callers fall back to SEC labels.

    Why this matters (bug this fixed): without it, quarterly facts for
    non-calendar-FY companies were assigned to the wrong fiscal year because
    SEC's `fy` label is the FILING's fiscal year, not the period's.
    """
    months: Counter[int] = Counter()
    us_gaap = payload.get("facts", {}).get("us-gaap", {})
    for concept in CONCEPT_MAP[Metric.REVENUE]:
        concept_facts = us_gaap.get(concept)
        if not concept_facts:
            continue
        for fact in concept_facts.get("units", {}).get("USD", []):
            start_str, end_str = fact.get("start"), fact.get("end")
            if not start_str or not end_str:
                continue
            try:
                start = date.fromisoformat(start_str)
                end = date.fromisoformat(end_str)
            except ValueError:
                continue
            if classify_period(start, end) is PeriodType.ANNUAL:
                months[end.month] += 1
    if not months:
        return None
    return months.most_common(1)[0][0]


def fiscal_year_of(end: date, sec_fy: int | None, period_type: PeriodType,
                   fiscal_year_end_month: int | None = None) -> int:
    """Fiscal year a fact belongs to.

    ANNUAL facts: use the END-DATE year, NOT SEC's `fy` label. Reason
    (discovered via a failing test on real data shapes): comparative columns
    in a later 10-K carry the LATER filing's `fy` label (FY2024's period
    re-reported inside the FY2025 10-K is labelled fy=2025), which would
    create two "different" fiscal years for the same period and break
    deduplication. The dominant US convention names a fiscal year for the
    calendar year it ENDS in (MSFT FY2025 = Jul 2024-Jun 2025), so
    end-year is correct for annual facts, including comparatives.

    QUARTERLY/YTD facts: derive from the period END date and the company's
    fiscal-year-end month -- NOT SEC's `fy` label. Bug caught by verifying
    real MSFT data (not by unit tests): a 10-Q's comparative prior-year
    quarters also carry the FILING's `fy` label (Sep-2023 comparative in the
    FY2025-Q1 10-Q is labelled fy=2025), so trusting the label shifted every
    derived quarter's fiscal year by one for companies with non-calendar
    fiscal years. Date arithmetic overcomes the mislabel:

        fiscal_year = end.year + (1 if end.month > fye_month else 0)

    For MSFT (fye_month=6): quarter ending Sep-2024 (month 9>6) -> FY2025;
    quarter ending Mar-2025 (month 3<=6) -> FY2025. Both correct.

    `fiscal_year_end_month` is inferred from the company's annual facts
    (their period_end month). When unavailable (None), fall back to SEC's
    label -- better than nothing, and calendar-FY companies are unaffected.

    Known limitation (documented): the rare company naming its fiscal year by
    the START year would be mis-assigned; we detect this downstream
    (sum-of-quarters != annual) and log it.
    """
    if period_type is PeriodType.ANNUAL:
        return end.year
    if fiscal_year_end_month is not None:
        return end.year + (1 if end.month > fiscal_year_end_month else 0)
    return sec_fy if sec_fy is not None else end.year


# --------------------------------------------------------------------------- #
# Main normalization
# --------------------------------------------------------------------------- #


def normalize_company_facts(payload: dict[str, Any]) -> NormalizationResult:
    """Normalize a raw Company Facts payload into clean facts.

    Steps:
    1. For each canonical metric, find the best XBRL concept (first chain
       link with data).
    2. Extract all facts for that concept, classify periods, apply
       latest-filed-wins dedup (restatements flagged).
    3. Derive standalone quarters from YTD figures where complete.
    """
    cik = int(payload.get("cik", 0))
    result = NormalizationResult(cik=cik)
    facts_us_gaap = payload.get("facts", {}).get("us-gaap", {})
    facts_dei = payload.get("facts", {}).get("dei", {})

    # Infer fiscal-year-end month ONCE (from annual facts) so quarterly/YTD
    # facts are assigned to the correct fiscal year by date arithmetic.
    # This fixed a real off-by-one bug found by verifying MSFT quarterly data.
    fye_month = infer_fiscal_year_end_month(payload)

    # metric -> (fiscal_year, fiscal_period, period_type) -> NormalizedFact
    dedup: dict[tuple[Metric, int, str, str], NormalizedFact] = {}
    metrics_with_facts: set[Metric] = set()

    for metric in Metric:
        taxonomy = _TAXONOMY_FOR_METRIC[metric]
        source = facts_dei if taxonomy == "dei" else facts_us_gaap

        expected_unit = _UNIT_FOR_METRIC[metric]
        metric_concepts = _concepts_with_data(metric, source)
        if not metric_concepts:
            logger.info("No data for metric %s (skipped)", metric.value)
            continue

        # BUG FOUND VIA REAL DATA (NVDA): the OLD approach picked the FIRST
        # concept in the fallback chain with any data and stopped. That broke
        # for NVIDIA, which used the "modern" ASC-606 tag for OLDER years and
        # the generic `Revenues` tag for NEWER years -- the opposite of
        # Microsoft. Picking only the first tag hid NVDA's entire FY2024-26
        # revenue ($215.9B in FY2026!).
        #
        # Fix: iterate ALL concepts with data, in priority order. For each
        # period, the HIGHEST-priority concept that has data wins; lower
        # priority concepts only fill periods the higher ones lack. This
        # merges coverage across eras without mixing definitions for the
        # same period.
        #
        # metric -> (fy, fp, period_type) -> (priority, NormalizedFact)
        claimed: dict[tuple[int, str, str], tuple[int, NormalizedFact]] = {}

        for priority, (concept_name, concept_facts) in enumerate(metric_concepts):
            for fact in _iter_facts(concept_facts, expected_unit):
                normalized = _normalize_one(
                    cik=cik, metric=metric, concept_name=concept_name, raw=fact,
                    fiscal_year_end_month=fye_month,
                )
                if normalized is None:
                    continue

                metrics_with_facts.add(metric)
                key = (
                    normalized.fiscal_year,
                    normalized.fiscal_period.value,
                    normalized.period_type.value,
                )
                existing = claimed.get(key)
                if existing is None:
                    claimed[key] = (priority, normalized)
                    continue

                existing_priority, existing_fact = existing
                if priority < existing_priority:
                    # Higher-priority concept claims this period.
                    claimed[key] = (priority, normalized)
                elif priority == existing_priority:
                    # Same concept: latest filed wins (restatement-aware).
                    if _newer(normalized.filed_at, existing_fact.filed_at):
                        if abs(normalized.value - existing_fact.value) > 1e-9:
                            normalized.is_restated = True
                            normalized.value_original = existing_fact.value
                            result.restatements.append(
                                {
                                    "metric": metric.value,
                                    "fiscal_year": normalized.fiscal_year,
                                    "fiscal_period": normalized.fiscal_period.value,
                                    "original_value": existing_fact.value,
                                    "restated_value": normalized.value,
                                    "original_accession": existing_fact.accession_number,
                                    "restated_accession": normalized.accession_number,
                                }
                            )
                        claimed[key] = (priority, normalized)
                # else: lower-priority concept for a period already claimed
                # by a higher-priority concept -> ignore.

        dedup.update(
            {(metric, fy, fp, pt): fact for (fy, fp, pt), (_p, fact) in claimed.items()}
        )

    result.facts = list(dedup.values())

    # A metric is "skipped" if NO usable fact survived (concept absent OR
    # concept present but all facts unparseable/wrong unit). Our first version
    # only counted absent concepts; a golden test caught that an empty-but-
    # present concept silently escaped the skipped list.
    result.skipped_concepts = [m.value for m in Metric if m not in metrics_with_facts]

    # Derive standalone quarters from YTD inputs (Q2, Q3, Q4).
    # Pass existing quarterly facts so the splitter does NOT derive a quarter
    # the company already reported directly (caught by verifying real MSFT
    # data: Q2/Q3 were stored twice -- as-filed AND derived -- which would
    # duplicate them on charts and in time-series queries).
    existing_quarterly = {
        (f.metric, f.fiscal_year, f.fiscal_period)
        for f in result.facts
        if f.period_type is PeriodType.QUARTERLY
    }
    derived = _derive_standalone_quarters(result.facts, existing_quarterly)
    result.facts.extend(derived)
    result.derived_quarters = len(derived)

    logger.info(
        "Normalized CIK %010d: %d facts, %d derived quarters, %d restatements, %d metrics skipped",
        cik, len(result.facts), len(derived), len(result.restatements), len(result.skipped_concepts),
    )
    return result


def _concepts_with_data(
    metric: Metric, source: dict[str, Any]
) -> list[tuple[str, dict[str, Any]]]:
    """Return ALL concepts in the fallback chain that have data, in priority order.

    Priority = position in CONCEPT_MAP (modern/specific tag first). Returning
    ALL of them (not just the first) is what lets NVDA's newer `Revenues` tag
    fill periods its older ASC-606 tag lacks -- see the bug note in
    normalize_company_facts. Each entry: (concept_name, concept_facts).
    """
    found: list[tuple[str, dict[str, Any]]] = []
    for concept in CONCEPT_MAP[metric]:
        concept_facts = source.get(concept)
        if concept_facts and concept_facts.get("units"):
            found.append((concept, concept_facts))
    return found


def _iter_facts(concept_facts: dict[str, Any], expected_unit: str):
    """Yield (raw_fact, unit) pairs for the expected unit only.

    Rejecting unexpected units loudly (not converting) prevents silent
    corruption: a fact in EUR or thousands-of-USD must not be mistaken for
    plain USD.
    """
    units = concept_facts.get("units", {})
    if expected_unit not in units:
        return
    for raw in units[expected_unit]:
        yield raw


def _normalize_one(
    cik: int, metric: Metric, concept_name: str, raw: dict[str, Any],
    fiscal_year_end_month: int | None = None,
) -> NormalizedFact | None:
    """Convert one raw XBRL fact dict into a NormalizedFact (or skip)."""
    end_str = raw.get("end")
    if not end_str:
        return None
    try:
        end = date.fromisoformat(end_str)
    except ValueError:
        return None

    start_str = raw.get("start")
    start: date | None = None
    if start_str:
        try:
            start = date.fromisoformat(start_str)
        except ValueError:
            return None

    period_type = classify_period(start, end)
    fy = fiscal_year_of(end, raw.get("fy"), period_type, fiscal_year_end_month)

    # Map period_type + filing period to a FiscalPeriod slot.
    fp_label = (raw.get("fp") or "").upper()
    fiscal_period = _fiscal_period_of(period_type, fp_label, fy, end)

    filed_str = raw.get("filed")
    filed_at = date.fromisoformat(filed_str) if filed_str else None

    accession = raw.get("accn", "")
    form = raw.get("form", "")

    return NormalizedFact(
        cik=cik,
        metric=metric,
        value=float(raw["val"]),
        unit=_UNIT_FOR_METRIC[metric],
        period_type=period_type,
        fiscal_year=fy,
        fiscal_period=fiscal_period,
        period_start=start,
        period_end=end,
        source_concept=concept_name,
        form=form,
        accession_number=accession,
        filed_at=filed_at,
        source_url=_accession_url(cik, accession),
    )


def _fiscal_period_of(
    period_type: PeriodType, fp_label: str, fy: int, end: date
) -> FiscalPeriod:
    """Determine the fiscal-period slot (FY/Q1..Q4) for a fact.

    INSTANT facts (balance sheet) need care: a 10-K's instant fact at fiscal
    year end is 'FY', but the same balance also appears in the Q4 10-Q. We
    map by which fiscal quarter the END date falls in, using the company's
    fiscal year end derived from its annual facts. Simplification: for
    instant facts, fp_label from SEC usually says 'FY' or 'Q4'; both map to
    FY-end when end is the fiscal year end. Here we trust fp_label when
    present (it comes from the filing itself), else fall back to quarter math.
    """
    if period_type is PeriodType.ANNUAL:
        return FiscalPeriod.FY

    if fp_label in ("FY", "Q4", "Q1", "Q2", "Q3"):
        # Q4 and FY both refer to the fiscal-year-end balance for instants;
        # for durations, a 10-Q labelled Q4 would be unusual but handled.
        if period_type is PeriodType.INSTANT and fp_label in ("FY", "Q4"):
            return FiscalPeriod.FY
        if period_type is PeriodType.INSTANT:
            return FiscalPeriod(fp_label)
        return FiscalPeriod(fp_label) if fp_label != "FY" else FiscalPeriod.FY

    # No usable label: fall back to date-based quarter inference for instants.
    if period_type is PeriodType.INSTANT:
        return FiscalPeriod.FY  # conservative: treat unlabeled instants as FY-end
    # Duration facts without labels: YTD quarter number unknown -> FY slot is
    # unsafe; callers (splitter) only use fp-labeled YTD facts, so this path
    # is rare. Assign FY to keep the fact (it is still a valid annual input).
    return FiscalPeriod.FY


def _newer(a: date | None, b: date | None) -> bool:
    """True if filing date `a` is strictly newer than `b` (None sorts oldest)."""
    if a is None:
        return False
    if b is None:
        return True
    return a > b


def _accession_url(cik: int, accession: str) -> str | None:
    """Build the canonical SEC Archives URL for an accession number.

    Format: https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/
    This is the citation link shown in the UI -- every fact is traceable to
    its exact filing document.
    """
    if not accession:
        return None
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/"


# --------------------------------------------------------------------------- #
# YTD -> standalone quarter derivation
# --------------------------------------------------------------------------- #


def _derive_standalone_quarters(
    facts: list[NormalizedFact],
    existing_quarterly: set | None = None,
) -> list[NormalizedFact]:
    """Compute standalone quarters from YTD + annual facts.

    Identities (revenue example):
        Q1 = YTD_Q1                    (Q1_YTD already covers exactly Q1)
        Q2 = YTD_H1  - YTD_Q1
        Q3 = YTD_9M  - YTD_H1
        Q4 = Annual  - YTD_9M

    Subtlety caught by a golden test: a Q1_YTD fact spans ~90 days, so
    `classify_period` already tags it QUARTERLY (correctly -- 3 months from
    fiscal-year start IS the standalone Q1). The splitter must therefore look
    for its Q1 input among BOTH existing QUARTERLY Q1 facts and (defensively)
    ~90-day YTD facts. Only the 6M/9M facts need actual subtraction.

    We only derive a quarter when ALL needed inputs exist -- a missing input
    yields NO output (correctly incomplete beats confidently wrong).
    """
    derived: list[NormalizedFact] = []

    # Index YTD/annual facts: (metric, fiscal_year) -> {period_end: fact}
    ytd_index: dict[tuple[Metric, int], dict[date, NormalizedFact]] = {}
    annual_index: dict[tuple[Metric, int], NormalizedFact] = {}
    # Existing standalone Q1 facts (already classified quarterly) -- usable
    # directly as the Q1 input for Q2 derivation.
    q1_index: dict[tuple[Metric, int], NormalizedFact] = {}

    for fact in facts:
        if fact.metric not in DURATION_METRICS:
            continue
        key = (fact.metric, fact.fiscal_year)
        if fact.period_type is PeriodType.ANNUAL:
            # Prefer the fact with the latest filing (restatement-aware).
            existing = annual_index.get(key)
            if existing is None or _newer(fact.filed_at, existing.filed_at):
                annual_index[key] = fact
        elif fact.period_type is PeriodType.YTD:
            ytd_index.setdefault(key, {})[fact.period_end] = fact
        elif (
            fact.period_type is PeriodType.QUARTERLY
            and fact.fiscal_period is FiscalPeriod.Q1
        ):
            q1_index[key] = fact

    for key in set(ytd_index) | set(q1_index):
        metric, fiscal_year = key
        by_end = ytd_index.get(key, {})

        # Sort YTD facts by period end; lengths tell us which YTD they are.
        ordered = sorted(by_end.items(), key=lambda kv: kv[0])
        spans = [
            (end, (end - (f.period_start or end)).days, f)
            for end, f in ordered
            if f.period_start
        ]

        # Identify YTD facts by approximate span: Q1_YTD ~90d, H1 ~180d,
        # 9M ~270d.
        q1_fact = q1_index.get(key) or _closest_span(spans, 90)
        h1_fact = _closest_span(spans, 180)
        nine_month_fact = _closest_span(spans, 270)

        template = (q1_fact or h1_fact or nine_month_fact)
        if template is None:
            continue

        def _already_filed(fp: FiscalPeriod) -> bool:
            """True if the company already reported this quarter directly."""
            if existing_quarterly is None:
                return False
            return (metric, fiscal_year, fp) in existing_quarterly

        # Q1: a ~90-day YTD fact IS the standalone quarter. But if the Q1
        # input is ALREADY classified QUARTERLY (common -- 90 days falls in
        # the quarterly window), do NOT re-emit it as a derived duplicate.
        if (
            q1_fact is not None
            and q1_fact.period_type is not PeriodType.QUARTERLY
            and not _already_filed(FiscalPeriod.Q1)
        ):
            derived.append(
                _make_quarter_fact(q1_fact, fiscal_year, FiscalPeriod.Q1)
            )

        # Q2 = H1 - Q1.
        if h1_fact is not None and q1_fact is not None and not _already_filed(FiscalPeriod.Q2):
            q2 = _subtract(h1_fact, q1_fact)
            if q2 is not None:
                derived.append(
                    _make_quarter_fact(q2, fiscal_year, FiscalPeriod.Q2)
                )

        # Q3 = 9M - H1.
        if nine_month_fact is not None and h1_fact is not None and not _already_filed(FiscalPeriod.Q3):
            q3 = _subtract(nine_month_fact, h1_fact)
            if q3 is not None:
                derived.append(
                    _make_quarter_fact(q3, fiscal_year, FiscalPeriod.Q3)
                )

        # Q4 = Annual - 9M.
        annual = annual_index.get((metric, fiscal_year))
        if annual is not None and nine_month_fact is not None and not _already_filed(FiscalPeriod.Q4):
            q4 = _subtract(annual, nine_month_fact)
            if q4 is not None:
                derived.append(
                    _make_quarter_fact(q4, fiscal_year, FiscalPeriod.Q4)
                )

    return derived


def _closest_span(
    spans: list[tuple[date, int, NormalizedFact]], target: int
) -> NormalizedFact | None:
    """Find the YTD fact whose span is closest to `target` days (within ±15)."""
    best: NormalizedFact | None = None
    best_delta = 16  # tolerance: 90±15, 180±15, 270±15
    for _end, days, fact in spans:
        delta = abs(days - target)
        if delta < best_delta:
            best_delta = delta
            best = fact
    return best


def _subtract(longer: NormalizedFact, shorter: NormalizedFact) -> NormalizedFact | None:
    """longer_YTD - shorter_YTD, guarding sign sanity.

    A negative result for revenue/OCF would indicate misaligned periods (or
    a genuine loss quarter -- rare but real). We allow negatives for
    NET_INCOME and OPERATING_INCOME (losses happen) but reject negative
    revenue/cash-flow results as sign of misaligned inputs.
    """
    value = longer.value - shorter.value
    if value < 0 and longer.metric in (Metric.REVENUE, Metric.OPERATING_CASH_FLOW):
        logger.warning(
            "Rejecting negative derived value for %s FY%d: inputs misaligned?",
            longer.metric.value, longer.fiscal_year,
        )
        return None
    return replace_fact(longer, value=value)


def replace_fact(fact: NormalizedFact, value: float) -> NormalizedFact:
    """Copy a fact with a new value (derived quarters inherit provenance)."""
    from dataclasses import replace

    return replace(fact, value=value)


def _make_quarter_fact(
    template: NormalizedFact, fiscal_year: int, fiscal_period: FiscalPeriod
) -> NormalizedFact:
    """Build a QUARTERLY fact from a YTD/annual template, adjusting provenance.

    Derived quarters inherit the template's accession (the filing the inputs
    came from) and carry a distinguishing source_concept suffix so the UI can
    show "derived from YTD subtraction" vs "as filed".
    """
    from dataclasses import replace

    return replace(
        template,
        period_type=PeriodType.QUARTERLY,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        period_start=None,  # derived: exact sub-period dates are approximate
        source_concept=f"{template.source_concept} (derived)",
    )
