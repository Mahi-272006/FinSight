"""Investment thesis monitoring: the verified layer (M7).

ARCHITECTURE 2.8's differentiating feature, built as two strictly separated
layers:

1. **VERIFIED layer (this module, pure Python).** A thesis carries measurable
   conditions (`revenue_growth_yoy >= 0.10` for FY2026). Each is evaluated
   against the M2 engine's output: CALCULATED metrics first (they carry a
   formula + source accessions), VERIFIED facts as fallback. A missing row is
   `incomplete` -- never a guess, never extrapolated from another period.

2. **Interpretive layer (LLM, best-effort).** Words over the finished verdict
   table. It receives pre-computed numbers, must restate them verbatim, and
   its text is stored with `explanation_source` saying which layer wrote it.
   If the LLM is down the assessment still saves with a deterministic
   explanation -- the verified layer never depends on it.

Rules enforced in code (not by prompt):
- `derive_status` sees ONLY condition verdicts. Investigation signals (M3)
  ride along as `signals_json` context and can never flip a thesis toward
  Contradicted -- anomalies are investigation prompts, not verdicts.
- Assessments are append-only: rows are inserted, never updated; the thesis
  row only caches the latest status. The timeline IS the audit trail.
- Statuses: Supported / Weakened / Contradicted / Insufficient evidence.

Condition strings use the form `metric OP threshold` where OP is one of
`>= <= > < ==` (unicode `>=`-variants accepted). No arithmetic exists
anywhere in this file: thresholds are compared against values the M2 engine
already computed.
"""

import json
import re
from dataclasses import asdict, dataclass
from typing import Sequence

from app.database.models import Thesis, ThesisAssessment, ThesisCondition, utcnow
from app.database.repositories import (
    CompanyRepository,
    FactRepository,
    MetricRepository,
    SignalRepository,
)
from app.services.llm_providers import LLMError, LLMResponse
from app.services.prompting import build_thesis_interpretation_messages
from app.utils.logging import get_logger

logger = get_logger(__name__)

# --- Vocabulary (stored verbatim in the DB; keep stable) --------------------

STATUS_SUPPORTED = "Supported"
STATUS_WEAKENED = "Weakened"
STATUS_CONTRADICTED = "Contradicted"
STATUS_INSUFFICIENT = "Insufficient evidence"

VERDICT_MET = "met"
VERDICT_NOT_MET = "not_met"
VERDICT_INCOMPLETE = "incomplete"

TIER_CALCULATED = "CALCULATED METRIC"
TIER_VERIFIED = "VERIFIED FACT"

# Within a year, which period is "latest"? FY (the full year) outranks Q4.
_PERIOD_RANK = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4, "FY": 5}


class ThesisInputError(ValueError):
    """Bad user input: unknown ticker, unparsable condition, empty thesis."""


class ThesisNotFoundError(LookupError):
    """Requested thesis id does not exist."""


# ---------------------------------------------------------------------------
# Pure verified-layer functions (no DB, no LLM -- unit-tested directly)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConditionSpec:
    """A parsed `metric OP threshold` condition with an optional period pin."""

    metric: str
    comparator: str
    threshold: float
    fiscal_year: int | None = None
    fiscal_period: str | None = None


@dataclass(frozen=True)
class MetricValue:
    """One looked-up number with full provenance (what the comparison sees)."""

    metric: str
    value: float
    unit: str
    tier: str  # TIER_CALCULATED or TIER_VERIFIED
    fiscal_year: int
    fiscal_period: str
    formula: str | None
    accessions: tuple[str, ...]


@dataclass(frozen=True)
class ConditionEvaluation:
    """The verdict for one condition -- the unit the status matrix consumes."""

    condition_id: int | None
    description: str
    metric: str
    comparator: str
    threshold: float
    verdict: str  # met | not_met | incomplete
    value: float | None
    unit: str | None
    tier: str | None
    fiscal_year: int | None  # the period actually evaluated (None if incomplete)
    fiscal_period: str | None
    formula: str | None
    accessions: tuple[str, ...]
    reason: str  # why incomplete / how the comparison came out


_CONDITION_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*"  # metric name
    r"(>=|<=|==|>|<|=|≥|≤)\s*"  # comparator (ascii + unicode)
    r"(-?\d+(?:\.\d+)?)\s*$"  # numeric threshold
)
_COMPARATOR_ALIASES = {"≥": ">=", "≤": "<=", "=": "=="}
_COMPARATORS = {">=", "<=", ">", "<", "=="}


def parse_condition(text: str, fiscal_year: int | None = None,
                    fiscal_period: str | None = None) -> ConditionSpec:
    """`revenue_growth_yoy>=0.10` -> ConditionSpec; bad input -> ValueError.

    `=` normalizes to `==` (a bare `=` in user input means equality, and
    silently treating it as anything else would change what was asked).
    """
    match = _CONDITION_RE.match(text or "")
    if not match:
        raise ThesisInputError(
            f"Cannot parse condition {text!r}. Expected 'metric OP threshold' "
            "with OP one of >= <= > < ==, e.g. 'revenue_growth_yoy>=0.10'."
        )
    metric, raw_op, raw_threshold = match.groups()
    comparator = _COMPARATOR_ALIASES.get(raw_op, raw_op)
    if comparator not in _COMPARATORS:  # defensive: regex already constrains
        raise ThesisInputError(f"Unsupported comparator {raw_op!r} in {text!r}.")
    return ConditionSpec(
        metric=metric,
        comparator=comparator,
        threshold=float(raw_threshold),
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
    )


def format_condition(spec: ConditionSpec) -> str:
    """Human description pinned to the condition row (audit-friendly)."""
    period = (
        f" (FY{spec.fiscal_year}" + (f" {spec.fiscal_period}" if spec.fiscal_period else "") + ")"
        if spec.fiscal_year is not None
        else " (latest available period)"
    )
    return f"{spec.metric} {spec.comparator} {spec.threshold:g}{period}"


def compare(value: float, comparator: str, threshold: float) -> bool:
    """Apply one comparator. `==` gets a tiny epsilon: 0.1+0.2-style float
    artifacts must not turn an exact-equality condition into a silent fail."""
    if comparator == ">=":
        return value >= threshold
    if comparator == "<=":
        return value <= threshold
    if comparator == ">":
        return value > threshold
    if comparator == "<":
        return value < threshold
    if comparator == "==":
        return abs(value - threshold) < 1e-9
    raise ThesisInputError(f"Unsupported comparator {comparator!r}.")


def select_row(rows: Sequence, fiscal_year: int | None,
               fiscal_period: str | None) -> tuple[object | None, str]:
    """Pick the row a condition evaluates against; (row, "") or (None, reason).

    Selection policy (the "never guess" rule made concrete):
    - fiscal_year pinned, fiscal_period not -> the ANNUAL (FY) row for that
      year; quarters are not interchangeable with a full year, so their
      absence is `incomplete`, not a silent substitution.
    - both pinned -> exact match only.
    - neither pinned -> the latest available row (max fiscal_year, FY > Q4 >
      ... within it); the caller records which period that was.
    """
    candidates = list(rows)
    if fiscal_year is not None:
        candidates = [r for r in candidates if r.fiscal_year == fiscal_year]
        if not candidates:
            available = sorted({r.fiscal_year for r in rows})
            return None, (
                f"no data for FY{fiscal_year} "
                f"(available periods: {available or 'none'})"
            )
    if fiscal_period is not None:
        candidates = [r for r in candidates if r.fiscal_period == fiscal_period]
        if not candidates:
            return None, f"no {fiscal_period} period data for FY{fiscal_year}"
    elif fiscal_year is not None:
        annual = [r for r in candidates if r.fiscal_period == "FY"]
        if not annual:
            periods = sorted({r.fiscal_period for r in candidates})
            return None, (
                f"no annual (FY) row for FY{fiscal_year} "
                f"(found only: {', '.join(periods)})"
            )
        candidates = annual
    if not candidates:
        return None, "no data"
    best = max(
        candidates, key=lambda r: (r.fiscal_year, _PERIOD_RANK.get(r.fiscal_period, 0))
    )
    return best, ""


def evaluate_condition(
    spec: ConditionSpec,
    value: MetricValue | None,
    condition_id: int | None = None,
    missing_reason: str = "",
) -> ConditionEvaluation:
    """Compare one looked-up value against one threshold. Pure."""
    description = format_condition(spec)
    if value is None:
        return ConditionEvaluation(
            condition_id=condition_id,
            description=description,
            metric=spec.metric,
            comparator=spec.comparator,
            threshold=spec.threshold,
            verdict=VERDICT_INCOMPLETE,
            value=None,
            unit=None,
            tier=None,
            fiscal_year=None,
            fiscal_period=None,
            formula=None,
            accessions=(),
            reason=missing_reason or "no data for this metric",
        )
    met = compare(value.value, spec.comparator, spec.threshold)
    return ConditionEvaluation(
        condition_id=condition_id,
        description=description,
        metric=spec.metric,
        comparator=spec.comparator,
        threshold=spec.threshold,
        verdict=VERDICT_MET if met else VERDICT_NOT_MET,
        value=value.value,
        unit=value.unit,
        tier=value.tier,
        fiscal_year=value.fiscal_year,
        fiscal_period=value.fiscal_period,
        formula=value.formula,
        accessions=value.accessions,
        reason=(
            f"{_fmt(value.value)} {spec.comparator} {spec.threshold:g} -> "
            f"{'holds' if met else 'does not hold'}"
        ),
    )


def derive_status(evaluations: Sequence[ConditionEvaluation]) -> tuple[str, str]:
    """Verdicts -> (status, deterministic explanation).

    The matrix, in one place so the "signals never contradict" rule is
    structural: this function's ONLY input is condition verdicts.
    """
    met = [e for e in evaluations if e.verdict == VERDICT_MET]
    not_met = [e for e in evaluations if e.verdict == VERDICT_NOT_MET]
    incomplete = [e for e in evaluations if e.verdict == VERDICT_INCOMPLETE]

    if not_met:
        status = STATUS_CONTRADICTED if not met else STATUS_WEAKENED
    elif incomplete:
        status = STATUS_INSUFFICIENT
    else:
        status = STATUS_SUPPORTED

    parts = [f"{status}: {len(met)}/{len(evaluations)} condition(s) met."]
    for e in not_met:
        parts.append(
            f"Not met: {e.description} evaluated at {_fmt(e.value)} "
            f"({_year_period(e.fiscal_year, e.fiscal_period)}, {e.tier})."
        )
    for e in incomplete:
        parts.append(f"Incomplete: {e.description} -- {e.reason}.")
    return status, " ".join(parts)


def _fmt(value: float | None) -> str:
    """Format a financial value without lying about its scale.

    Ratios/margins are small (<10) and keep 4 decimals; dollar amounts are
    large and get thousands separators. One formatter, used by every
    explanation, so a number reads the same everywhere in the product.
    """
    if value is None:
        return "n/a"
    if abs(value) < 10:
        return f"{value:.4f}"
    return f"{value:,.0f}"


def _year_period(year: int | None, period: str | None) -> str:
    """'FY2026' for annual rows, 'Q1 FY2026' for quarters."""
    if year is None:
        return "unknown period"
    if period in (None, "FY"):
        return f"FY{year}"
    return f"{period} FY{year}"


# ---------------------------------------------------------------------------
# Service: DB access + best-effort interpretation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AssessmentResult:
    """What `assess()` returns to callers (CLI in M7, API in M9)."""

    assessment_id: int
    thesis_id: int
    status: str
    status_changed_from: str | None
    evaluations: tuple[ConditionEvaluation, ...]
    explanation: str
    explanation_source: str
    signals_context: tuple[dict, ...]


class ThesisService:
    """Thesis lifecycle over one session factory (same pattern as M6's agent).

    `manager` is optional: without it (or when interpretation is skipped)
    assessments save with the deterministic explanation -- a legitimate
    degraded mode, not an error.
    """

    def __init__(self, session_factory, manager=None):
        self._session_factory = session_factory
        self._manager = manager

    # -- creation -----------------------------------------------------------

    def create_thesis(
        self,
        ticker: str,
        statement: str,
        horizon: str,
        condition_texts: Sequence[str],
        fiscal_year: int | None = None,
        fiscal_period: str | None = None,
    ) -> int:
        """Create a thesis + its conditions; returns the thesis id.

        Each condition snapshots the metric's CURRENT value into
        `evidence_json` at creation time, so later assessments can always
        show what the world looked like when the thesis was written --
        including when the metric did not exist yet.
        """
        if not statement or not statement.strip():
            raise ThesisInputError("statement must be non-empty")
        if not condition_texts:
            raise ThesisInputError("a thesis needs at least one condition")
        specs = [
            parse_condition(text, fiscal_year=fiscal_year, fiscal_period=fiscal_period)
            for text in condition_texts
        ]

        with self._session_factory() as session:
            company = CompanyRepository(session).get_by_ticker(ticker.upper())
            if company is None:
                raise ThesisInputError(f"unknown ticker {ticker!r} (run ingestion first)")

            thesis = Thesis(
                cik=company.cik,
                ticker=company.ticker,
                statement=statement.strip(),
                horizon=horizon,
            )
            session.add(thesis)
            session.flush()  # thesis.id needed for the FK rows below

            for spec in specs:
                value, _ = self._lookup(session, company.cik, spec)
                session.add(
                    ThesisCondition(
                        thesis_id=thesis.id,
                        description=format_condition(spec),
                        metric=spec.metric,
                        comparator=spec.comparator,
                        threshold=spec.threshold,
                        fiscal_year=spec.fiscal_year,
                        fiscal_period=spec.fiscal_period,
                        evidence_json=json.dumps(
                            _creation_snapshot(spec, value), default=str
                        ),
                    )
                )
            session.flush()
            thesis_id = thesis.id
            # Own the transaction boundary: works whether the factory is
            # production's get_session_factory() or a bare sessionmaker in
            # tests; either way this service owns and commits the transaction.
            session.commit()
        logger.info(
            "Thesis %s created for %s with %d condition(s)", thesis_id, ticker, len(specs)
        )
        return thesis_id

    # -- assessment ---------------------------------------------------------

    def assess(self, thesis_id: int, interpret: bool = True) -> AssessmentResult:
        """Run the two-layer assessment; append one immutable history row."""
        with self._session_factory() as session:
            thesis = session.get(Thesis, thesis_id)
            if thesis is None:
                raise ThesisNotFoundError(f"thesis {thesis_id} not found")

            conditions = (
                session.query(ThesisCondition)
                .filter(ThesisCondition.thesis_id == thesis_id)
                .order_by(ThesisCondition.id)
                .all()
            )
            evaluations = [
                self._evaluate_one(session, thesis.cik, cond) for cond in conditions
            ]
            status, deterministic_explanation = derive_status(evaluations)

            signals = SignalRepository(session).get_signals(cik=thesis.cik)
            signals_context = tuple(_signal_context(s) for s in signals)
            if signals_context:
                deterministic_explanation += (
                    f" {len(signals_context)} investigation signal(s) attached as "
                    "context; signals never change the status by themselves."
                )

            previous = thesis.status
            changed = previous is not None and previous != status
            if changed:
                deterministic_explanation = (
                    f"Status changed from {previous} to {status}. "
                    + deterministic_explanation
                )

            explanation = deterministic_explanation
            source = "deterministic:condition-counts"
            if interpret and self._manager is not None:
                try:
                    response = self._interpret(
                        thesis, status, evaluations, signals_context
                    )
                    explanation = response.text.strip()
                    source = f"llm:{response.provider}/{response.model}"
                except LLMError as exc:
                    logger.warning(
                        "Thesis interpretation unavailable (%s); saving "
                        "deterministic explanation", exc,
                    )

            assessment = ThesisAssessment(
                thesis_id=thesis_id,
                status=status,
                status_changed_from=previous if changed else None,
                summary_json=json.dumps(
                    [asdict(e) for e in evaluations], default=str
                ),
                explanation=explanation,
                explanation_source=source,
                evidence_json=json.dumps(
                    [_evidence_row(e) for e in evaluations], default=str
                ),
                signals_json=json.dumps(list(signals_context), default=str),
            )
            session.add(assessment)
            thesis.status = status
            thesis.status_explanation = deterministic_explanation
            session.flush()
            assessment_id = assessment.id
            session.commit()  # own the transaction (see create_thesis)

        return AssessmentResult(
            assessment_id=assessment_id,
            thesis_id=thesis_id,
            status=status,
            status_changed_from=previous if changed else None,
            evaluations=tuple(evaluations),
            explanation=explanation,
            explanation_source=source,
            signals_context=signals_context,
        )

    # -- reads --------------------------------------------------------------

    def get_thesis(self, thesis_id: int) -> dict:
        """Thesis + conditions + full (append-only) assessment history."""
        with self._session_factory() as session:
            thesis = session.get(Thesis, thesis_id)
            if thesis is None:
                raise ThesisNotFoundError(f"thesis {thesis_id} not found")
            conditions = (
                session.query(ThesisCondition)
                .filter(ThesisCondition.thesis_id == thesis_id)
                .order_by(ThesisCondition.id)
                .all()
            )
            assessments = (
                session.query(ThesisAssessment)
                .filter(ThesisAssessment.thesis_id == thesis_id)
                .order_by(ThesisAssessment.id)
                .all()
            )
            return {
                "id": thesis.id,
                "ticker": thesis.ticker,
                "statement": thesis.statement,
                "horizon": thesis.horizon,
                "status": thesis.status or "Unassessed",
                "created_at": thesis.created_at,
                "conditions": [_condition_dict(c) for c in conditions],
                "assessments": [
                    {
                        "id": a.id,
                        "assessed_at": a.assessed_at,
                        "status": a.status,
                        "status_changed_from": a.status_changed_from,
                        "summary": json.loads(a.summary_json or "[]"),
                        "explanation": a.explanation,
                        "explanation_source": a.explanation_source,
                        "signals": json.loads(a.signals_json or "[]"),
                    }
                    for a in assessments
                ],
            }

    def list_theses(self) -> list[dict]:
        with self._session_factory() as session:
            theses = session.query(Thesis).order_by(Thesis.id).all()
            return [
                {
                    "id": t.id,
                    "ticker": t.ticker,
                    "statement": t.statement,
                    "horizon": t.horizon,
                    "status": t.status or "Unassessed",
                    "created_at": t.created_at,
                }
                for t in theses
            ]

    # -- internals ----------------------------------------------------------

    def _lookup(self, session, cik: int, spec: ConditionSpec):
        """Find the value for a condition: CALCULATED first, VERIFIED fallback.

        Precedence subtlety: if the metric NAME exists in the calculated
        table but the pinned period does not, that is `incomplete` -- we do
        NOT fall back to a fact row, because silently swapping metric
        semantics (growth -> raw dollars) would answer a different question
        than the one asked.
        """
        metric_rows = MetricRepository(session).get_metrics(cik, spec.metric)
        if metric_rows:
            row, reason = select_row(metric_rows, spec.fiscal_year, spec.fiscal_period)
            if row is None:
                return None, reason
            return (
                MetricValue(
                    metric=row.metric,
                    value=row.value,
                    unit=row.unit,
                    tier=TIER_CALCULATED,
                    fiscal_year=row.fiscal_year,
                    fiscal_period=row.fiscal_period,
                    formula=row.formula,
                    accessions=tuple(json.loads(row.source_accessions_json or "[]")),
                ),
                "",
            )

        fact_rows = FactRepository(session).get_facts(cik, spec.metric)
        if fact_rows:
            row, reason = select_row(fact_rows, spec.fiscal_year, spec.fiscal_period)
            if row is None:
                return None, reason
            return (
                MetricValue(
                    metric=row.metric,
                    value=row.value,
                    unit=row.unit,
                    tier=TIER_VERIFIED,
                    fiscal_year=row.fiscal_year,
                    fiscal_period=row.fiscal_period,
                    formula=None,
                    accessions=(row.accession_number,),
                ),
                "",
            )
        return None, f"metric {spec.metric!r} has no rows for this company"

    def _evaluate_one(self, session, cik: int, cond: ThesisCondition):
        spec = ConditionSpec(
            metric=cond.metric,
            comparator=cond.comparator,
            threshold=cond.threshold,
            fiscal_year=cond.fiscal_year,
            fiscal_period=cond.fiscal_period,
        )
        value, missing_reason = self._lookup(session, cik, spec)
        return evaluate_condition(
            spec, value, condition_id=cond.id, missing_reason=missing_reason
        )

    def _interpret(self, thesis: Thesis, status: str,
                   evaluations: Sequence[ConditionEvaluation],
                   signals_context: Sequence[dict]) -> LLMResponse:
        """Interpretive layer: one constrained generate() call."""
        messages = build_thesis_interpretation_messages(
            ticker=thesis.ticker,
            statement=thesis.statement,
            horizon=thesis.horizon,
            status=status,
            evaluations=evaluations,
            signals_context=signals_context,
        )
        return self._manager.generate(messages)


# ---------------------------------------------------------------------------
# Serialization helpers (dict/JSON shapes shared by service, CLI, tests)
# ---------------------------------------------------------------------------


def _period_label(e: ConditionEvaluation) -> str:
    if e.fiscal_year is None:
        return "unknown period"
    return _year_period(e.fiscal_year, e.fiscal_period)


def _evidence_row(e: ConditionEvaluation) -> dict:
    """The numbers/accessions that caused this assessment (audit column)."""
    return {
        "condition_id": e.condition_id,
        "description": e.description,
        "verdict": e.verdict,
        "value": e.value,
        "unit": e.unit,
        "tier": e.tier,
        "period": _period_label(e),
        "formula": e.formula,
        "accessions": list(e.accessions),
    }


def _creation_snapshot(spec: ConditionSpec, value: MetricValue | None) -> dict:
    """What condition's metric looked like when the thesis was written."""
    snap: dict = {
        "metric": spec.metric,
        "comparator": spec.comparator,
        "threshold": spec.threshold,
        "captured_at": utcnow().isoformat(),
    }
    if value is None:
        snap["value"] = None
    else:
        snap.update(
            {
                "value": value.value,
                "unit": value.unit,
                "tier": value.tier,
                "period": _year_period(value.fiscal_year, value.fiscal_period),
                "formula": value.formula,
                "accessions": list(value.accessions),
            }
        )
    return snap


def _signal_context(signal) -> dict:
    """Trim an InvestigationSignal to interpretation-sized context."""
    return {
        "signal_type": signal.signal_type,
        "severity": signal.severity,
        "period": _year_period(signal.fiscal_year, signal.fiscal_period),
        "headline": signal.headline,
        "explanation": signal.explanation,
    }


def _condition_dict(cond: ThesisCondition) -> dict:
    return {
        "id": cond.id,
        "description": cond.description,
        "metric": cond.metric,
        "comparator": cond.comparator,
        "threshold": cond.threshold,
        "fiscal_year": cond.fiscal_year,
        "fiscal_period": cond.fiscal_period,
        "creation_evidence": json.loads(cond.evidence_json or "{}"),
    }
