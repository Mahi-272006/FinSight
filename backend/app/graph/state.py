"""Typed, JSON-serializable state for one bounded research graph run."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages

from app.graph.evidence import AnswerClaim, EvidenceRecord, ToolObservation


class ResearchState(TypedDict, total=False):
    # Inputs and resolved company context.
    question: str
    ticker: str
    company: dict[str, Any] | None  # {cik, ticker, name} or None if unknown

    # Conversation, structured provenance, and per-call audit trail.
    messages: Annotated[list, add_messages]
    evidence_records: Annotated[list[EvidenceRecord], operator.add]
    tool_observations: Annotated[list[ToolObservation], operator.add]
    trace: Annotated[list[str], operator.add]

    # Bounded research loop counters.
    iterations: int
    revises: int

    # Draft and safe-finalization state.
    draft_answer: str
    draft_claims: list[AnswerClaim]
    accepted_claims: list[AnswerClaim]
    claim_findings: list[dict[str, Any]]
    validation_report: dict[str, Any] | None
    final_answer: str
    limitations: Annotated[list[str], operator.add]
