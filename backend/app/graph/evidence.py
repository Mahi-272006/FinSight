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

import re
from dataclasses import dataclass, field
from typing import Sequence

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


def build_allowlists(observation_texts: Sequence[str]) -> tuple[set[str], list[float]]:
    """Accessions and figures THIS RUN actually retrieved (ground truth)."""
    accessions: set[str] = set()
    numbers: list[float] = []
    for text in observation_texts:
        accessions |= extract_accessions(text)
        numbers.extend(extract_financial_numbers(text))
    return accessions, numbers


def validate_answer(answer: str, observation_texts: Sequence[str]) -> ValidationReport:
    """Check citations and financial figures against the run's evidence."""
    allowed_accessions, allowed_numbers = build_allowlists(observation_texts)

    cited = sorted(extract_accessions(answer))
    unsupported = sorted(a for a in cited if a not in allowed_accessions)

    unverified: list[str] = []
    verified: list[str] = []
    for value in extract_financial_numbers(answer):
        bucket = verified if matches_any(value, allowed_numbers) else unverified
        bucket.append(_display(value))

    notes: list[str] = []
    if not answer.strip():
        notes.append("empty draft: the model produced no answer text")
    if not cited:
        notes.append("no citations: an uncited answer is ungrounded")

    ok = bool(answer.strip()) and bool(cited) and not unsupported and not unverified
    return ValidationReport(
        ok=ok,
        citations=cited,
        unsupported_citations=unsupported,
        verified_numbers=sorted(set(verified)),
        unverified_numbers=sorted(set(unverified)),
        notes=notes,
    )


def strip_unsupported(answer: str, observation_texts: Sequence[str]) -> tuple[str, list[str]]:
    """Drop sentences carrying unsupported claims; return (kept, limitations).

    Sentence-level granularity keeps verified content while removing exactly
    what the evidence cannot back -- and every removal is reported, so the
    final answer's omissions are auditable rather than silent.
    """
    allowed_accessions, allowed_numbers = build_allowlists(observation_texts)
    kept: list[str] = []
    limitations: list[str] = []
    for sentence in split_sentences(answer):
        bad_citations = [a for a in extract_accessions(sentence) if a not in allowed_accessions]
        bad_numbers = [_display(v) for v in extract_financial_numbers(sentence)
                       if not matches_any(v, allowed_numbers)]
        if bad_citations or bad_numbers:
            reasons = []
            if bad_citations:
                reasons.append(f"unsupported citation(s): {', '.join(bad_citations)}")
            if bad_numbers:
                reasons.append(f"unverified number(s): {', '.join(bad_numbers)}")
            limitations.append(f"removed: {sentence[:160]} [{' ; '.join(reasons)}]")
        else:
            kept.append(sentence)
    return " ".join(kept).strip(), limitations
