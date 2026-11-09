"""Deterministic evidence validation: the gate every draft answer passes.

THE RULE (ARCHITECTURE 2.7): validation is code, never LLM judgment.

1. CITATIONS -- every accession number the answer references must appear in
   what THIS RUN actually retrieved (tool observations). An answer citing a
   filing the agent never saw is fabrication, however plausible.
2. NUMBERS -- every financial figure in the answer must match (within 1%
   relative tolerance, because "96.2 billion" vs "96,221,000,000" is the
   same figure) a figure present in tool observations. The LLM quotes
   numbers; the engine produces them.

Honest looseness (documented, tested):
- Years (1900-2100) and small plain integers (<1000) without $ / % / K/M/B
  context are exempt: "Item 1A", "three risks", "fiscal 2026" are not
  financial claims. Claims WITH $ / % / a scale suffix are always checked.
- Accessions/dates inside the answer are masked before number extraction so
  "0001045810-26-000075" is never mistaken for the number 1045810.

Everything here is a pure function over strings: no DB, no network, no LLM --
which is exactly why the agent loop can trust it.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Literal, Mapping, Sequence, TypedDict

# 10-digit CIK-ish base, 2-digit year, 6-digit sequence: SEC accession format.
ACCESSION_RE = re.compile(r"\b\d{10}-\d{2}-\d{6}\b")
# Chunk ids are "{accession}:{chunk_index}" (M4 convention).
CHUNK_ID_RE = re.compile(r"\b\d{10}-\d{2}-\d{6}:\d+\b")
# ISO dates: masked before number extraction so "2026-08-26" isn't a number.
DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
# Financial-looking tokens: scaled/percent ("$96.2B", "15%") or >=4 digits
# ("96,221"). Plain small integers are non-financial (exempt by rule below).
FINANCIAL_TOKEN_RE = re.compile(
    r"\$?\d[\d,]*(?:\.\d+)?\s*[KMBkmb%]|\b\d[\d,]{3,}(?:\.\d+)?\b"
)

_SCALE_SUFFIX = {"k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12}

# Relative tolerance for figure matching (a quoted "$96.2B" is the same
# figure as 96,221,000,000 to within rounding).
TOLERANCE = 0.01
FACT_DISPLAY_TOLERANCE = Decimal("0.5")
METRIC_DISPLAY_TOLERANCE = Decimal("0.005")
MAX_DECIMAL_DIGITS = 256


# Internal, checkpoint-friendly records used by the graph. Financial values
# are decimal strings representing the values read from the database. This
# preserves the stored value's representation through JSON; it cannot recover
# precision already lost when source values were stored as database floats.
EvidenceSourceKind = Literal[
    "filing_chunk", "financial_fact", "calculated_metric", "investigation_signal"
]
ToolOutcome = Literal["success", "no_results", "error", "unknown_tool"]
ClaimType = Literal[
    "verified_fact", "calculated_metric", "filing_quote",
    "investigation_signal", "interpretation"
]
ClaimValueScope = Literal["as_filed", "original", "restated", "latest"]
JSONValue = str | int | float | bool | None | list["JSONValue"] | dict[str, "JSONValue"]


class EvidenceRecord(TypedDict):
    """One retrieved source item with stable identity and provenance."""

    evidence_id: str
    source_kind: EvidenceSourceKind
    ticker: str | None
    cik: int | None
    accession: str | None
    form: str | None
    filed_at: str | None
    section: str | None
    chunk_id: str | None
    metric: str | None
    source_concept: str | None
    fiscal_year: int | None
    fiscal_period: str | None
    period_type: str | None
    unit: str | None
    numeric_value: str | None
    value_original: str | None
    is_restated: bool
    source_accessions: list[str]
    formula: str | None
    calculation_version: str | None
    signal_type: str | None
    severity: str | None
    text: str


class ToolObservation(TypedDict):
    """Auditable tool result; operational outcomes carry no evidence IDs."""

    tool: str
    args: dict[str, JSONValue]
    content: str
    status: ToolOutcome
    evidence_ids: list[str]


class AnswerClaim(TypedDict):
    """A claim and its explicit links to records in the evidence ledger."""

    text: str
    claim_type: ClaimType
    metric: str | None
    source_concept: str | None
    fiscal_year: int | None
    fiscal_period: str | None
    period_type: str | None
    unit: str | None
    numeric_value: str | None
    accession: str | None
    value_scope: ClaimValueScope
    quote: str | None
    supporting_evidence_ids: list[str]


def build_evidence_id(
    source_kind: EvidenceSourceKind,
    source_identity: Mapping[str, JSONValue],
) -> str:
    """Build a reproducible ID from a source kind and JSON-safe identity.

    Callers must supply stable source identity fields (for example accession,
    chunk ID, metric, and fiscal period), not a process-local object ID.
    """
    if source_kind not in {
        "filing_chunk", "financial_fact", "calculated_metric", "investigation_signal"
    }:
        raise ValueError(f"unsupported evidence source kind: {source_kind}")
    if not source_identity:
        raise ValueError("source_identity must contain at least one stable field")
    canonical = json.dumps(
        {"source_kind": source_kind, "source_identity": source_identity},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"ev:{source_kind}:{digest}"


def build_filing_chunk_evidence_id(cik: int, accession: str, chunk_id: str) -> str:
    """Build a stable evidence ID for one filing chunk."""
    return build_evidence_id(
        "filing_chunk", {"cik": cik, "accession": accession, "chunk_id": chunk_id}
    )


def build_financial_evidence_id(
    source_kind: Literal["financial_fact", "calculated_metric"],
    *,
    ticker: str,
    cik: int,
    accession: str | None,
    source_accessions: Sequence[str] = (),
    metric: str,
    source_concept: str | None,
    fiscal_year: int,
    fiscal_period: str,
    period_type: str,
    unit: str,
    calculation_version: str | None = None,
) -> str:
    """Build a stable ID from CIK and the record's source identity.

    Ticker is intentionally excluded: it is mutable company metadata, while
    CIK is the stable issuer identifier. A version distinguishes calculated
    rows if their deterministic formula implementation changes; raw facts do
    not require one.
    """
    if source_kind not in {"financial_fact", "calculated_metric"}:
        raise ValueError(f"unsupported financial evidence kind: {source_kind}")
    if source_kind == "financial_fact" and calculation_version is not None:
        raise ValueError("raw financial facts do not have a calculation version")
    return build_evidence_id(source_kind, {
        "cik": cik,
        "accession": accession,
        "source_accessions": sorted(source_accessions),
        "metric": metric,
        "source_concept": source_concept,
        "fiscal_year": fiscal_year,
        "fiscal_period": fiscal_period,
        "period_type": period_type,
        "unit": unit,
        "calculation_version": calculation_version,
    })


_CANONICAL_DECIMAL_RE = re.compile(r"^[+-]?(?:0|[1-9]\d*)(?:\.\d+)?$")


def validate_decimal_string(value: str) -> str:
    """Validate a finite, non-exponent decimal string without float conversion.

    The original spelling (including fractional trailing zeroes) is returned
    unchanged so its decimal precision survives JSON serialization.
    """
    if not isinstance(value, str) or len(value) > MAX_DECIMAL_DIGITS + 2:
        raise ValueError("numeric value must be a bounded canonical decimal string")
    if not _CANONICAL_DECIMAL_RE.fullmatch(value):
        raise ValueError("numeric value must use canonical decimal notation")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("numeric value is not a valid decimal") from exc
    if not parsed.is_finite():
        raise ValueError("numeric value must be finite")
    if len(parsed.as_tuple().digits) > MAX_DECIMAL_DIGITS:
        raise ValueError("numeric value exceeds supported precision")
    return value


def make_tool_observation(
    tool_name: str,
    args: Mapping[str, JSONValue],
    content: str,
    *,
    status: ToolOutcome = "success",
    evidence_ids: Sequence[str] = (),
) -> ToolObservation:
    """Create an observation, defaulting to no evidence for operational results."""
    if status not in {"success", "no_results", "error", "unknown_tool"}:
        raise ValueError(f"unsupported tool observation status: {status}")
    ids = list(evidence_ids)
    if status != "success" and ids:
        raise ValueError("non-success tool observations cannot produce evidence IDs")
    return {
        "tool": tool_name,
        "args": dict(args),
        "content": content,
        "status": status,
        "evidence_ids": ids if status == "success" else [],
    }


def _parse_number(raw: str) -> float | None:
    """'$96.2B' -> 96200000000.0; '15%' -> 15.0; '96,221' -> 96221.0."""
    token = raw.replace("$", "").replace(",", "").replace("%", "").strip()
    if not token:
        return None
    suffix = ""
    if len(token) > 1 and token[-1].lower() in _SCALE_SUFFIX and token[-2].isdigit():
        suffix = token[-1].lower()
        token = token[:-1]
    try:
        value = float(token)
    except ValueError:
        return None
    return value * _SCALE_SUFFIX[suffix] if suffix else value


def _mask_structural(text: str) -> str:
    """Blank out accessions, chunk ids, and dates before number extraction."""
    text = CHUNK_ID_RE.sub(" ", text)
    text = ACCESSION_RE.sub(" ", text)
    text = DATE_RE.sub(" ", text)
    return text


def extract_accessions(text: str) -> set[str]:
    return set(ACCESSION_RE.findall(text))


def extract_chunk_ids(text: str) -> set[str]:
    return set(CHUNK_ID_RE.findall(text))


def extract_financial_numbers(text: str) -> list[float]:
    """Financial claims in `text`, per the honesty rules in the module doc.

    Returns raw floats (not scaled-normalized strings): callers compare with
    `matches_any`.
    """
    masked = _mask_structural(text)
    values: list[float] = []
    for token in FINANCIAL_TOKEN_RE.findall(masked):
        value = _parse_number(token)
        if value is None:
            continue
        has_context = any(ch in token for ch in "$%") or token.rstrip()[-1:].lower() in _SCALE_SUFFIX
        if not has_context and value.is_integer() and 1900 <= value <= 2100:
            continue  # a year
        if not has_context and value < 1000:
            continue  # counts, item numbers, positions -- not financial claims
        values.append(value)
    return values


def matches_any(value: float, allowlist: Sequence[float]) -> bool:
    """True if any allowed figure is within TOLERANCE of `value`."""
    return any(abs(value - a) <= TOLERANCE * max(abs(a), 1.0) for a in allowlist)


def _display(value: float) -> str:
    """Human-readable figure for reports: '999,999,999,999', not '1e+12'."""
    return f"{value:,.0f}" if value.is_integer() else f"{value:,.4g}"


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


@dataclass
class ValidationReport:
    """Structured verdict; stored in graph state as a dict (checkpoint-safe)."""

    ok: bool
    citations: list[str] = field(default_factory=list)
    unsupported_citations: list[str] = field(default_factory=list)
    verified_numbers: list[str] = field(default_factory=list)
    unverified_numbers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "citations": self.citations,
            "unsupported_citations": self.unsupported_citations,
            "verified_numbers": self.verified_numbers,
            "unverified_numbers": self.unverified_numbers,
            "notes": self.notes,
        }

    def summary(self) -> str:
        parts = [f"{'PASS' if self.ok else 'FAIL'}"]
        if self.citations:
            parts.append(f"{len(self.citations)} citation(s), "
                         f"{len(self.unsupported_citations)} unsupported")
        if self.unverified_numbers:
            parts.append(f"{len(self.unverified_numbers)} unverified number(s)")
        parts.extend(self.notes)
        return "; ".join(parts)


def parse_answer_claims(
    content: str,
) -> tuple[list[AnswerClaim], list[dict[str, object]]]:
    """Parse model claims and retain structured findings for discarded entries."""
    findings: list[dict[str, object]] = []
    try:
        payload = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return [], [{"claim": "", "reason": "answer is not valid JSON"}]
    if not isinstance(payload, dict) or not isinstance(payload.get("claims"), list):
        return [], [{"claim": "", "reason": "answer has no claims list"}]
    claims: list[AnswerClaim] = []
    for index, item in enumerate(payload["claims"]):
        if not isinstance(item, dict):
            findings.append({
                "claim": "", "reason": f"claim entry {index + 1} is malformed (expected an object)"
            })
            continue
        text = item.get("text")
        claim_type = item.get("claim_type")
        ids = item.get("supporting_evidence_ids")
        if not isinstance(text, str) or not isinstance(ids, list) or not all(
            isinstance(i, str) for i in ids
        ):
            findings.append({
                "claim": text[:200] if isinstance(text, str) else "",
                "reason": "claim entry is malformed (invalid text or evidence IDs)",
            })
            continue
        if not isinstance(claim_type, str) or claim_type not in {
            "verified_fact", "calculated_metric", "filing_quote",
            "investigation_signal", "interpretation",
        }:
            findings.append({
                "claim": text[:200], "reason": "claim has an unknown or invalid type"
            })
            continue
        scope = item.get("value_scope", "as_filed")
        if not isinstance(scope, str) or scope not in {
            "as_filed", "original", "restated", "latest"
        }:
            findings.append({
                "claim": text[:200], "reason": "claim has an unsupported or invalid value_scope"
            })
            continue
        claims.append({
            "text": text,
            "claim_type": claim_type,
            "metric": item.get("metric") if isinstance(item.get("metric"), str) else None,
            "source_concept": item.get("source_concept")
                if isinstance(item.get("source_concept"), str) else None,
            "fiscal_year": item.get("fiscal_year")
                if isinstance(item.get("fiscal_year"), int) else None,
            "fiscal_period": item.get("fiscal_period")
                if isinstance(item.get("fiscal_period"), str) else None,
            "period_type": item.get("period_type")
                if isinstance(item.get("period_type"), str) else None,
            "unit": item.get("unit") if isinstance(item.get("unit"), str) else None,
            "numeric_value": item.get("numeric_value")
                if isinstance(item.get("numeric_value"), str) else None,
            "accession": item.get("accession")
                if isinstance(item.get("accession"), str) else None,
            "value_scope": scope,
            "quote": item.get("quote") if isinstance(item.get("quote"), str) else None,
            "supporting_evidence_ids": list(dict.fromkeys(ids)),
        })
    return claims, findings


@dataclass
class ClaimValidation:
    """Internal validation result; only `report` retains the public shape."""

    report: ValidationReport
    accepted_claims: list[AnswerClaim] = field(default_factory=list)
    findings: list[dict[str, object]] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)


_CLAIM_NUMBER_RE = re.compile(
    r"(?<![\w-])\$?[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)"
    r"(?:\.\d+)?\s*[KMBTkmbt%]?"
)
_CLAIM_SCALES = {
    "k": Decimal("1000"),
    "m": Decimal("1000000"),
    "b": Decimal("1000000000"),
    "t": Decimal("1000000000000"),
}


def _claim_numbers(text: str) -> list[Decimal]:
    masked = _mask_structural(text)
    result: list[Decimal] = []
    for raw in _CLAIM_NUMBER_RE.findall(masked):
        token = raw.replace("$", "").replace(",", "").strip()
        suffix = token[-1:].lower()
        if suffix in _CLAIM_SCALES or suffix == "%":
            token = token[:-1].strip()
        else:
            suffix = ""
        try:
            value = Decimal(token)
        except InvalidOperation:
            continue
        if not value.is_finite():
            continue
        if not suffix and value == value.to_integral_value() and 1900 <= value <= 2100:
            continue  # fiscal year or date fragment, not a claimed financial value
        if not suffix and value == value.to_integral_value() and abs(value) < 1000:
            continue  # plain counts and item numbers
        result.append(value * _CLAIM_SCALES.get(suffix, Decimal(1)))
    return result


def _within_display_precision(left: Decimal, right: Decimal, precision: Decimal) -> bool:
    """Use an absolute half-unit tolerance matching the displayed precision."""
    return abs(left - right) <= precision


def _within_rounding_tolerance(left: Decimal, right: Decimal) -> bool:
    """Allow the existing 1% shorthand tolerance only for claim text display."""
    tolerance = Decimal(str(TOLERANCE))
    return abs(left - right) <= tolerance * max(abs(right), Decimal(1))


def _record_accessions(record: EvidenceRecord) -> set[str]:
    accessions = set(record.get("source_accessions", []))
    if record.get("accession"):
        accessions.add(record["accession"])
    return accessions


def _find_conflicts(records: Sequence[EvidenceRecord]) -> list[str]:
    grouped: dict[tuple, dict[str, set[str]]] = {}
    for record in records:
        if record["source_kind"] not in {"financial_fact", "calculated_metric"}:
            continue
        value = record.get("numeric_value")
        if value is None:
            continue
        key = (record.get("source_kind"), record.get("cik"), record.get("metric"), record.get("fiscal_year"),
               record.get("fiscal_period"), record.get("period_type"), record.get("unit"))
        grouped.setdefault(key, {}).setdefault(value, set()).update(_record_accessions(record))
    conflicts = []
    for key, values in grouped.items():
        if len(values) > 1:
            conflicts.append(
                f"Conflicting {key[0]} evidence for CIK {key[1]} {key[2]} FY{key[3]} "
                f"{key[4]} ({key[6]}): " + "; ".join(
                    f"{value} from {', '.join(sorted(accessions)) or 'unknown filing'}"
                    for value, accessions in sorted(values.items())
                )
            )
    return conflicts


def _fmt_decimal(value: str) -> str:
    return format(Decimal(validate_decimal_string(value)), ",f")


def _display_number(value: Decimal) -> str:
    return format(value, ",f")


def _render_claim(claim: AnswerClaim, records: Sequence[EvidenceRecord]) -> str:
    record = records[0]
    accessions = sorted(set().union(*(_record_accessions(r) for r in records)))
    citation = f" [{', '.join(accessions)}]" if accessions else ""
    if claim["claim_type"] in {"verified_fact", "calculated_metric"}:
        value = record["numeric_value"]
        if claim["value_scope"] == "original":
            value = record.get("value_original")
        period = f"FY{record['fiscal_year']} {record['fiscal_period']}"
        metric = (record.get("metric") or "financial metric").replace("_", " ")
        attribution = "(restated filing)" if claim["value_scope"] == "restated" else "(as filed)"
        return (f"{metric} for {period} ({record['period_type']}) was "
                f"{_fmt_decimal(value)} {record['unit']} {attribution}.{citation}")
    if claim["claim_type"] == "filing_quote":
        quote = (claim.get("quote") or "").replace("\r", " ").replace("\n", " ")
        return f"“{quote}”{citation}"
    if claim["claim_type"] == "investigation_signal":
        return (f"Investigation signal for analyst review: {record['signal_type']} "
                f"({record['severity']}) for FY{record['fiscal_year']} "
                f"{record['fiscal_period']}: {record['text']}{citation}")
    return ""


def render_claims(claims: Sequence[AnswerClaim], ledger: Sequence[EvidenceRecord]) -> str:
    """Render only validated claim data, deriving citations from linked records."""
    by_id = {record["evidence_id"]: record for record in ledger}
    rendered = []
    for claim in claims:
        linked = [by_id[eid] for eid in claim["supporting_evidence_ids"] if eid in by_id]
        if linked and claim["claim_type"] != "interpretation":
            rendered.append(_render_claim(claim, linked))
    return " ".join(rendered).strip()


def validate_answer(
    claims: Sequence[AnswerClaim],
    evidence_records: Sequence[EvidenceRecord],
    *,
    expected_cik: int | None = None,
) -> ClaimValidation:
    """Validate explicit claim-to-record associations using deterministic rules.

    Financial fact display permits at most half a displayed unit because the
    existing tool formats those values to whole units. Calculated metrics are
    displayed to two decimal places, so their maximum display tolerance is
    half a cent in the metric's unit. No unit conversion is performed.
    """
    record_by_id: dict[str, EvidenceRecord] = {}
    collisions: set[str] = set()
    for record in evidence_records:
        existing = record_by_id.get(record["evidence_id"])
        if existing is not None and existing != record:
            collisions.add(record["evidence_id"])
        else:
            record_by_id[record["evidence_id"]] = record
    for evidence_id in collisions:
        record_by_id.pop(evidence_id, None)

    findings: list[dict[str, object]] = []
    accepted: list[AnswerClaim] = []
    unsupported_citations: set[str] = set()
    unverified_numbers: set[str] = set()
    conflicts = _find_conflicts(list(record_by_id.values()))

    def reject(claim: AnswerClaim, reason: str) -> None:
        findings.append({"claim": claim.get("text", "")[:200], "reason": reason})

    for evidence_id in sorted(collisions):
        findings.append({"evidence_id": evidence_id,
                         "reason": "duplicate evidence ID has inconsistent records"})

    for claim in claims:
        kind = claim["claim_type"]
        if kind == "interpretation":
            reject(claim, "qualitative interpretation cannot be deterministically verified")
            continue
        ids = claim["supporting_evidence_ids"]
        if not ids:
            reject(claim, "claim has no supporting evidence IDs")
            continue
        linked = [record_by_id.get(evidence_id) for evidence_id in ids]
        if any(record is None for record in linked):
            reject(claim, "claim references missing or inconsistent evidence ID")
            for evidence_id in ids:
                if evidence_id not in record_by_id:
                    unsupported_citations.update(extract_accessions(claim["text"]))
            continue
        records = [record for record in linked if record is not None]
        ciks = {record.get("cik") for record in records}
        if len(ciks) != 1 or (expected_cik is not None and ciks != {expected_cik}):
            reject(claim, "supporting evidence belongs to the wrong company")
            continue
        cited = extract_accessions(claim["text"])
        allowed_citations = set().union(*(_record_accessions(record) for record in records))
        if cited - allowed_citations:
            unsupported_citations.update(cited - allowed_citations)
            reject(claim, "claim cites an accession not present in its supporting records")
            continue
        requested_accession = claim.get("accession")
        if requested_accession and requested_accession not in allowed_citations:
            unsupported_citations.add(requested_accession)
            reject(claim, "claim accession does not match its supporting records")
            continue

        if kind == "filing_quote":
            quote = claim.get("quote")
            if isinstance(quote, str) and _claim_numbers(quote):
                reject(claim, "numeric excerpts require a structured financial record")
                unverified_numbers.update(_display_number(n) for n in _claim_numbers(quote))
                continue
            matching = [r for r in records if r["source_kind"] == "filing_chunk"
                        and isinstance(quote, str) and quote and quote in r["text"]]
            if len(matching) != len(records):
                reject(claim, "qualitative claim is not an exact excerpt from linked filing text")
                continue
            accepted.append(claim)
            continue

        if kind == "investigation_signal":
            matching = [r for r in records if r["source_kind"] == "investigation_signal"
                        and r.get("signal_type") == claim.get("metric")
                        and r.get("fiscal_year") == claim.get("fiscal_year")
                        and r.get("fiscal_period") == claim.get("fiscal_period")]
            if len(matching) != len(records):
                reject(claim, "signal type or fiscal period does not match the linked signal")
                continue
            accepted.append(claim)
            continue

        expected_kind = "financial_fact" if kind == "verified_fact" else "calculated_metric"
        financial = [r for r in records if r["source_kind"] == expected_kind]
        if len(financial) != len(records):
            reject(claim, "claim type does not match supporting evidence kind")
            continue
        if claim["value_scope"] == "latest":
            reject(claim, "the available evidence does not establish a latest value")
            continue
        if any(
            claim.get("metric") != r.get("metric")
            or claim.get("source_concept") != r.get("source_concept")
            or claim.get("fiscal_year") != r.get("fiscal_year")
            or claim.get("fiscal_period") != r.get("fiscal_period")
            or claim.get("period_type") != r.get("period_type")
            or claim.get("unit") != r.get("unit")
            for r in financial
        ):
            reject(claim, "metric, concept, fiscal period, or unit does not match evidence")
            continue
        if claim["value_scope"] == "restated" and not all(r.get("is_restated") for r in financial):
            reject(claim, "evidence does not establish a restatement")
            continue
        if claim["value_scope"] == "original" and any(r.get("is_restated") for r in financial):
            reject(claim, "restated row does not establish the original filing attribution")
            continue
        numeric_text = claim.get("numeric_value")
        try:
            claimed_value = Decimal(validate_decimal_string(numeric_text)) if numeric_text else None
        except ValueError:
            claimed_value = None
        if claimed_value is None:
            unverified_numbers.update(_display(value) for value in _claim_numbers(claim["text"]))
            reject(claim, "financial claim is missing a valid decimal value")
            continue
        textual_numbers = _claim_numbers(claim["text"])
        if textual_numbers and not any(_within_rounding_tolerance(n, claimed_value)
                                       for n in textual_numbers):
            unverified_numbers.add(_fmt_decimal(numeric_text))
            reject(claim, "claim text and structured numeric value disagree")
            continue
        tolerance = (FACT_DISPLAY_TOLERANCE if kind == "verified_fact"
                     else METRIC_DISPLAY_TOLERANCE)
        values_match = True
        for record in financial:
            source_value = record.get("numeric_value")
            if claim["value_scope"] == "original":
                source_value = record.get("value_original")
            try:
                source_decimal = Decimal(validate_decimal_string(source_value)) if source_value else None
            except ValueError:
                source_decimal = None
            if source_decimal is None or not _within_display_precision(
                claimed_value, source_decimal, tolerance
            ):
                values_match = False
                break
        if not values_match:
            unverified_numbers.add(_fmt_decimal(numeric_text))
            reject(claim, "numeric value does not match the linked metric and period")
            continue
        accepted.append(claim)

    report_notes = [finding["reason"] for finding in findings]
    report_notes.extend(conflicts)
    if not claims:
        report_notes.append("no structured answer claims")
    cited_final = sorted(set().union(*(
        _record_accessions(record)
        for claim in accepted
        for evidence_id in claim["supporting_evidence_ids"]
        if (record := record_by_id.get(evidence_id)) is not None
    ))) if accepted else []
    verified = []
    for claim in accepted:
        if claim["claim_type"] in {"verified_fact", "calculated_metric"}:
            # _render_claim renders the source record's value (not the
            # possibly rounded model claim). Use the same linked record and
            # scope here so the report describes the returned answer exactly.
            record = record_by_id.get(claim["supporting_evidence_ids"][0])
            if record is not None:
                rendered_value = record.get("numeric_value")
                if claim["value_scope"] == "original":
                    rendered_value = record.get("value_original")
                if rendered_value is not None:
                    verified.append(_fmt_decimal(rendered_value))
    report = ValidationReport(
        ok=bool(accepted) and not findings,
        citations=cited_final,
        unsupported_citations=sorted(unsupported_citations),
        verified_numbers=sorted(set(verified)),
        unverified_numbers=sorted(unverified_numbers),
        notes=report_notes,
    )
    return ClaimValidation(report, accepted, findings, conflicts)
