"""Typed graph state for the research agent.

Design notes:

- LangGraph state is a TypedDict; fields with reducers (`add_messages`,
  `operator.add`) may be updated by any node without clobbering; plain
  fields are last-write-wins.
- EVERYTHING the run needs to be auditable lives in state: the message
  history (what the LLM saw), tool_observations (what tools actually
  returned -- the ground truth the validation node checks against, kept
  structurally instead of only as message text), the trace (compact
  human-readable timeline), and the validation report. State is what gets
  checkpointed, so an interrupted or replayed run tells the same story.
- `tool_observations` content is the single source of truth for evidence:
  citation and number allowlists are derived from it at validation time,
  never stored separately (two sources of truth would drift).
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


class ResearchState(TypedDict, total=False):
    # --- inputs ---
    question: str
    ticker: str

    # --- resolved context ---
    company: dict[str, Any] | None  # {cik, ticker, name} or None if unknown

    # --- conversation / evidence ---
    # messages: [system policy, question, (AI tool calls | answers), tool
    # results, revise feedback...] -- exactly what the LLM was shown.
    messages: Annotated[list, add_messages]
    # One entry per executed tool call: name, args, the full returned text,
    # and the chunk ids it surfaced. This is the run's evidence ledger.
    tool_observations: Annotated[list[dict[str, Any]], operator.add]
    # Compact operator-facing timeline ("resolve: NVDA ...", "tool ...").
    trace: Annotated[list[str], operator.add]

    # --- research loop bookkeeping ---
    iterations: int  # LLM calls in the research loop (cap: max_iterations)
    revises: int  # validation-driven revision rounds (cap: max_revises)

    # --- outputs ---
    draft_answer: str  # the pre-validation synthesis
    validation_report: dict[str, Any] | None  # ValidationReport.to_dict()
    final_answer: str  # post-stripping, citations kept, limitations listed
    limitations: Annotated[list[str], operator.add]
