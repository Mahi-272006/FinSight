"""Graph nodes: deterministic plumbing around the LLM research loop.

Node behavior (each is a small function over ResearchState):

- `resolve`    deterministic ticker -> company lookup (no LLM).
- `research`   one tool-enabled LLM call; increments the research counter.
- `answer`     one tool-free generation call for final JSON or correction.
- `execute_tools` runs each tool call from the last AI message and records
               a STRUCTURED observation per call (full text + chunk ids).
               Hand-rolled instead of langgraph.prebuilt.ToolNode on
               purpose: the validation node needs the evidence as data, and
               capturing it where it is produced is the only way to keep
               one source of truth.
- `force_stop` records the cap; the graph then invokes the tool-free answer node.
- `validate`   pure-function verdict over the draft vs. observations.
- `revise`     turns the invalid answer and validation findings into feedback
               for one tool-free correction (bounded by max_revises).
- `finalize`   strips sentences the evidence cannot back, reports every
               removal as a limitation, and assembles the final answer.

The LLM nodes never decide control flow; routing functions (pure, below)
do, so every loop bound is code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.database.models import Company
from app.graph import evidence
from app.graph.state import ResearchState
from app.services.llm_manager import LLMManager
from app.services.llm_providers import LLMError, _classify
from app.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class GraphDeps:
    """Everything nodes need, injected (tests substitute fakes)."""

    session_factory: Callable
    manager: LLMManager
    tools_by_name: dict[str, Any]
    max_iterations: int = 6
    max_revises: int = 1
    context_char_budget: int = 6_000


_COMPACT_EXCERPT_CHARS = 240
_COMPACT_OBSERVATION_CHARS = 1_800


def _compact_observation(
    observation: evidence.ToolObservation | None,
    records_by_id: dict[str, evidence.EvidenceRecord],
    *,
    excerpt_chars: int = _COMPACT_EXCERPT_CHARS,
    max_chars: int = _COMPACT_OBSERVATION_CHARS,
) -> str:
    """Render a compact tool-role summary from the structured ledger."""
    if observation is None:
        return "Earlier tool observation compacted; no structured observation was recorded."

    summary: dict[str, Any] = {
        "tool": observation["tool"],
        "status": observation["status"],
        "evidence_ids": observation["evidence_ids"],
    }
    if observation["evidence_ids"]:
        rendered_records = []
        for evidence_id in observation["evidence_ids"]:
            record = records_by_id.get(evidence_id)
            if record is None:
                rendered_records.append({"evidence_id": evidence_id})
                continue
            rendered = {
                key: record.get(key)
                for key in (
                    "evidence_id", "source_kind", "ticker", "cik", "accession",
                    "form", "filed_at", "section", "chunk_id", "metric",
                    "source_concept", "fiscal_year", "fiscal_period", "period_type",
                    "unit", "numeric_value", "value_original", "is_restated",
                    "source_accessions", "formula", "calculation_version",
                    "signal_type", "severity",
                )
            }
            source_text = record.get("text", "")
            rendered["text_preview"] = source_text[:excerpt_chars]
            rendered["preview_truncated"] = len(source_text) > excerpt_chars
            rendered_records.append(rendered)
        summary["records"] = rendered_records
        summary["note"] = (
            "Historical tool output compacted. Text previews may be incomplete; "
            "use only visible source text and identifiers."
        )
    else:
        summary["message"] = observation["content"][:240]

    content = json.dumps(summary, ensure_ascii=True, separators=(",", ":"))
    if len(content) > max_chars:
        # Preserve IDs and provenance; shorten previews before dropping them.
        for record in summary.get("records", []):
            preview = record.get("text_preview", "")
            if preview:
                record["text_preview"] = preview[:40]
                record["preview_truncated"] = True
        content = json.dumps(summary, ensure_ascii=True, separators=(",", ":"))
    if len(content) > max_chars:
        compact_records = []
        for record in summary.get("records", []):
            compact_records.append({
                key: record.get(key)
                for key in (
                    "evidence_id", "source_kind", "ticker", "cik", "accession",
                    "chunk_id", "metric", "source_concept", "fiscal_year",
                    "fiscal_period", "period_type", "unit", "numeric_value",
                )
                if record.get(key) is not None
            })
        compact = {
            "tool": observation["tool"],
            "status": observation["status"],
            "evidence_ids": observation["evidence_ids"],
            "records": compact_records,
        }
        content = json.dumps(compact, ensure_ascii=True, separators=(",", ":"))
        if len(content) > max_chars:
            # A very small caller budget cannot hold all metadata. Keep stable
            # IDs intact (never slice JSON or an identifier mid-value).
            content = json.dumps({
                "tool": observation["tool"],
                "status": observation["status"],
                "evidence_ids": observation["evidence_ids"],
            }, ensure_ascii=True, separators=(",", ":"))
    return content


def _message_size(message: Any) -> int:
    size = len(str(getattr(message, "content", "")))
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        size += len(json.dumps(tool_calls, ensure_ascii=True, default=str))
    return size


def build_bounded_research_messages(
    state: ResearchState,
    context_char_budget: int,
) -> list[Any]:
    """Bound variable conversation history while keeping tool-call pairs valid.

    The original system message and current question are always retained.
    Older tool replies stay in the tool role and are summarized from structured
    observations/evidence records; the evidence ledger itself is not changed.
    If summaries still exceed the configured budget, oldest complete exchanges
    are omitted from this model request, never from validation state.
    """
    if context_char_budget <= 0:
        raise ValueError("context_char_budget must be positive")

    messages = state.get("messages", [])
    system_message = next(
        (message for message in messages if isinstance(message, SystemMessage)),
        None,
    )
    question_message = HumanMessage(content=state.get("question", ""))
    observations = state.get("tool_observations", [])
    observation_by_call_id: dict[str, evidence.ToolObservation] = {}
    original_tool_message_by_call_id: dict[str, ToolMessage] = {}
    observation_index = 0
    for message in messages:
        if isinstance(message, ToolMessage):
            original_tool_message_by_call_id[message.tool_call_id] = message
            if observation_index < len(observations):
                observation_by_call_id[message.tool_call_id] = observations[observation_index]
            observation_index += 1
    records_by_id = {
        record["evidence_id"]: record for record in state.get("evidence_records", [])
    }

    units: list[tuple[str, list[Any]]] = []
    exchange: list[Any] = []

    def finish_exchange() -> None:
        nonlocal exchange
        if exchange:
            units.append(("exchange", exchange))
            exchange = []

    for message in messages:
        if isinstance(message, SystemMessage):
            continue
        if isinstance(message, HumanMessage):
            finish_exchange()
            content = str(message.content)
            if content.startswith((
                "Research iteration limit reached.",
                "Your draft failed evidence validation:",
            )):
                units.append(("control", [message]))
            continue
        if isinstance(message, AIMessage):
            finish_exchange()
            if message.tool_calls:
                exchange = [message]
            # Draft assistant prose is regenerated after revision and is not
            # needed as evidence or protocol history.
            continue
        if isinstance(message, ToolMessage):
            observation = observation_by_call_id.get(message.tool_call_id)
            compact = message.model_copy(update={
                "content": _compact_observation(observation, records_by_id)
            })
            if exchange:
                exchange.append(compact)
            else:
                units.append(("exchange", [compact]))
            continue
        finish_exchange()

    finish_exchange()

    # A tool call at the iteration cap is intentionally not executed. Never
    # forward such an orphan call (or an orphan result) into a later answer
    # request; only complete assistant-call/tool-result exchanges are valid
    # conversation history.
    complete_units: list[tuple[str, list[Any]]] = []
    for kind, unit_messages in units:
        if kind != "exchange":
            complete_units.append((kind, unit_messages))
            continue
        call_ids = {
            call["id"]
            for message in unit_messages
            if isinstance(message, AIMessage)
            for call in message.tool_calls
        }
        result_ids = {
            message.tool_call_id
            for message in unit_messages
            if isinstance(message, ToolMessage)
        }
        if call_ids and call_ids == result_ids:
            complete_units.append((kind, unit_messages))
    units = complete_units

    # Keep only the most recent control note (force-stop or validation
    # feedback); the current question is separately preserved above.
    latest_control = next(
        (index for index in range(len(units) - 1, -1, -1) if units[index][0] == "control"),
        None,
    )
    units = [
        unit for index, unit in enumerate(units)
        if unit[0] != "control" or index == latest_control
    ]

    # The latest tool exchange remains verbatim when it fits; older results
    # use structured summaries. Every ToolMessage retains its original call ID.
    exchange_indices = [index for index, unit in enumerate(units) if unit[0] == "exchange"]
    newest_exchange = exchange_indices[-1] if exchange_indices else None
    candidates: list[tuple[str, list[Any]]] = []
    for index, (kind, unit_messages) in enumerate(units):
        if kind == "exchange" and index == newest_exchange:
            candidates.append((kind, [
                original if isinstance(original, AIMessage)
                else original_tool_message_by_call_id[original.tool_call_id]
                for original in unit_messages
            ]))
        else:
            candidates.append((kind, unit_messages))

    def assemble(current_units: list[tuple[str, list[Any]]]) -> list[Any]:
        return ([system_message] if system_message is not None else []) + [
            question_message,
            *(message for _, unit_messages in current_units for message in unit_messages),
        ]

    def variable_size(current_units: list[tuple[str, list[Any]]]) -> int:
        return sum(
            _message_size(message)
            for _, unit_messages in current_units
            for message in unit_messages
        )

    if variable_size(candidates) <= context_char_budget:
        return assemble(candidates)

    # Compact the newest exchange too if the complete history crosses budget.
    compacted: list[tuple[str, list[Any]]] = []
    for kind, unit_messages in candidates:
        compacted_unit = []
        for message in unit_messages:
            if isinstance(message, ToolMessage):
                observation = observation_by_call_id.get(message.tool_call_id)
                message = message.model_copy(update={
                    "content": _compact_observation(
                        observation, records_by_id, excerpt_chars=80
                    )
                })
            elif isinstance(message, AIMessage) and message.tool_calls:
                message = message.model_copy(update={"content": ""})
            compacted_unit.append(message)
        compacted.append((kind, compacted_unit))

    while variable_size(compacted) > context_char_budget:
        oldest_exchange = next(
            (index for index, unit in enumerate(compacted) if unit[0] == "exchange"),
            None,
        )
        newest_exchange = next(
            (index for index in range(len(compacted) - 1, -1, -1)
             if compacted[index][0] == "exchange"),
            None,
        )
        if oldest_exchange is not None and oldest_exchange != newest_exchange:
            compacted.pop(oldest_exchange)
            continue
        if newest_exchange is not None:
            # If even one complete tool exchange cannot fit, omit that model
            # history unit; the unchanged ledger still supports validation,
            # and the agent can retrieve the evidence again if needed.
            compacted.pop(newest_exchange)
            continue
        if compacted:
            compacted.pop(0)
            continue
        break

    return assemble(compacted)


# ---------------------------------------------------------------------------
# Node factories (closures over deps)
# ---------------------------------------------------------------------------


def make_resolve_node(deps: GraphDeps) -> Callable[[ResearchState], dict]:
    def resolve(state: ResearchState) -> dict:
        ticker = state["ticker"].upper()
        with deps.session_factory() as session:
            company = session.query(Company).filter(Company.ticker == ticker).first()
        if company is None:
            return {"company": None, "trace": [f"resolve: unknown ticker {ticker}"]}
        return {
            "company": {"cik": company.cik, "ticker": company.ticker, "name": company.name},
            "trace": [f"resolve: {company.name} (CIK {company.cik})"],
        }

    return resolve


def make_research_node(deps: GraphDeps, chain: Any) -> Callable[[ResearchState], dict]:
    def research(state: ResearchState) -> dict:
        try:
            model_messages = build_bounded_research_messages(
                state, deps.context_char_budget
            )
            ai_message: AIMessage = chain.invoke(model_messages)
        except LLMError:
            raise  # already our taxonomy (rate-limit wait failed over, etc.)
        except Exception as exc:  # noqa: BLE001
            # RunnableWithFallbacks re-raises the FIRST branch's raw error
            # when every provider failed (observed live in M6): classify at
            # the graph boundary so the CLI exits 2 with a readable cause
            # instead of printing an SDK traceback.
            raise _classify(exc, "llm_chain") from exc
        if ai_message.tool_calls:
            names = ", ".join(c["name"] for c in ai_message.tool_calls)
            trace_line = f"llm#{state.get('iterations', 0) + 1}: tool calls -> {names}"
        else:
            trace_line = f"llm#{state.get('iterations', 0) + 1}: final answer ({len(str(ai_message.content))} chars)"
        return {
            "messages": [ai_message],
            "iterations": state.get("iterations", 0) + 1,
            "trace": [trace_line],
        }

    return research


def make_answer_node(
    deps: GraphDeps, answer_prompt: str
) -> Callable[[ResearchState], dict]:
    """Generate a final answer through the manager's tool-free provider path."""
    def answer(state: ResearchState) -> dict:
        messages = build_bounded_research_messages(
            state, deps.context_char_budget
        )
        system_index = next(
            (index for index, message in enumerate(messages)
             if isinstance(message, SystemMessage)),
            None,
        )
        if system_index is None:
            messages.insert(0, SystemMessage(content=answer_prompt))
        else:
            existing = str(messages[system_index].content)
            messages[system_index] = messages[system_index].model_copy(update={
                "content": f"{existing}\n\n{answer_prompt}"
            })
        try:
            response = deps.manager.generate(messages)
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001 -- classify at LLM boundary
            raise _classify(exc, "llm_answer") from exc
        content = response.text
        return {
            "messages": [AIMessage(content=content)],
            "trace": [f"answer: tool-free generation ({len(content)} chars)"],
        }

    return answer


def make_execute_tools_node(deps: GraphDeps) -> Callable[[ResearchState], dict]:
    def execute_tools(state: ResearchState) -> dict:
        last_ai = state["messages"][-1]
        observations: list[evidence.ToolObservation] = []
        records_to_add: list[evidence.EvidenceRecord] = []
        known_records = {
            record["evidence_id"]: record
            for record in state.get("evidence_records", [])
        }
        new_messages: list[ToolMessage] = []
        trace: list[str] = []
        for call in last_ai.tool_calls:
            name, args, call_id = call["name"], call["args"], call["id"]
            runner = deps.tools_by_name.get(name)
            evidence_ids: list[str] = []
            pending_records: dict[str, evidence.EvidenceRecord] = {}
            if runner is None:
                content = f"Unknown research tool {name!r}; no evidence was retrieved."
                status: evidence.ToolOutcome = "unknown_tool"
            else:
                try:
                    if hasattr(runner, "execute"):
                        execution = runner.execute(args)
                        content = execution.content
                        status = execution.status
                        produced = execution.evidence_records
                    else:
                        # Injected text-only tools stay operational and never
                        # become evidence merely because they returned text.
                        content = str(runner.invoke(args))
                        status = "success"
                        produced = []
                    for record in produced:
                        evidence_id = record["evidence_id"]
                        prior = pending_records.get(evidence_id, known_records.get(evidence_id))
                        if prior is not None and prior != record:
                            raise ValueError("inconsistent record reused an evidence ID")
                        pending_records[evidence_id] = record
                        evidence_ids.append(evidence_id)
                    evidence_ids = list(dict.fromkeys(evidence_ids))
                    if not evidence_ids and status == "success":
                        status = "no_results"
                    if status == "success":
                        for evidence_id, record in pending_records.items():
                            if evidence_id not in known_records:
                                records_to_add.append(record)
                            known_records[evidence_id] = record
                except Exception:  # noqa: BLE001 -- no raw error detail enters evidence
                    content = f"Tool {name} failed; no evidence was retrieved."
                    status = "error"
                    evidence_ids = []
            if status not in {"success", "no_results", "error", "unknown_tool"}:
                content = f"Tool {name} returned an invalid outcome; no evidence was retrieved."
                status = "error"
            if status != "success":
                evidence_ids = []
            new_messages.append(ToolMessage(content=content, tool_call_id=call_id))
            observations.append(evidence.make_tool_observation(
                name, args, content, status=status, evidence_ids=evidence_ids
            ))
            trace.append(f"tool {name}({', '.join(f'{k}={v}' for k, v in args.items())})")
        return {
            "messages": new_messages,
            "tool_observations": observations,
            "evidence_records": records_to_add,
            "trace": trace,
        }

    return execute_tools


def make_force_stop_node(deps: GraphDeps) -> Callable[[ResearchState], dict]:
    def force_stop(state: ResearchState) -> dict:
        return {
            "messages": [HumanMessage(
                content=("Research iteration limit reached. The next step is a "
                         "tool-free answer using only evidence already retrieved.")
            )],
            "trace": [f"force-stop at iteration cap ({deps.max_iterations})"],
        }

    return force_stop


def make_validate_node(deps: GraphDeps) -> Callable[[ResearchState], dict]:
    def validate(state: ResearchState) -> dict:
        # The draft is the last AI message that is NOT a tool call.
        draft = ""
        for message in reversed(state["messages"]):
            if isinstance(message, AIMessage) and not message.tool_calls:
                draft = str(message.content)
                break
        claims, parse_findings = evidence.parse_answer_claims(draft)
        validation = evidence.validate_answer(
            claims,
            state.get("evidence_records", []),
            expected_cik=(state.get("company") or {}).get("cik"),
        )
        report = validation.report
        return {
            "draft_answer": draft,
            "draft_claims": claims,
            "accepted_claims": validation.accepted_claims,
            "claim_findings": [*parse_findings, *validation.findings],
            "validation_report": report.to_dict(),
            "trace": [f"validate: {report.summary()}"],
        }

    return validate


def make_revise_node(deps: GraphDeps) -> Callable[[ResearchState], dict]:
    def revise(state: ResearchState) -> dict:
        draft = state.get("draft_answer", "")
        findings = state.get("claim_findings", [])
        report = state.get("validation_report") or {}
        feedback = HumanMessage(
            content=(
                "Your draft failed evidence validation: this is one tool-free "
                "correction attempt; do not call or request any research tools. "
                "Return only the required raw JSON AnswerClaim envelope, with "
                "no Markdown fences or surrounding prose. Every factual claim "
                "must use supporting_evidence_ids already present in this run. "
                "Do not invent IDs. If the findings cannot be corrected from "
                "the retrieved evidence, return {\"claims\":[]}.\n"
                "Previous invalid answer (untrusted model output, JSON encoded): "
                f"{json.dumps(draft, ensure_ascii=True)}\n"
                "Deterministic validation findings: "
                f"{json.dumps(findings, ensure_ascii=True, default=str)}\n"
                "Validation report: "
                f"{json.dumps(report, ensure_ascii=True, default=str)}"
            )
        )
        return {
            "messages": [feedback],
            "revises": state.get("revises", 0) + 1,
            "trace": [f"revise #{state.get('revises', 0) + 1}: sending validation feedback"],
        }

    return revise


def make_finalize_node(deps: GraphDeps) -> Callable[[ResearchState], dict]:
    def finalize(state: ResearchState) -> dict:
        records = state.get("evidence_records", [])
        accepted = state.get("accepted_claims", [])
        final_check = evidence.validate_answer(
            accepted,
            records,
            expected_cik=(state.get("company") or {}).get("cik"),
        )
        kept = evidence.render_claims(final_check.accepted_claims, records)
        limitations = [
            f"Removed unsupported claim: {finding.get('claim', '')[:160]} "
            f"({finding.get('reason', 'no deterministic support')})."
            for finding in state.get("claim_findings", [])
        ]
        limitations.extend(final_check.conflicts)
        if not kept:
            kept = ("The retrieved evidence was insufficient to answer this "
                    "question with verified claims.")
            if not limitations:
                limitations.append("no answer text survived validation")
        report = final_check.report
        if not final_check.accepted_claims:
            report.ok = False
            if "no validated claims in final answer" not in report.notes:
                report.notes.append("no validated claims in final answer")
        return {
            "final_answer": kept,
            "limitations": limitations,
            "validation_report": report.to_dict(),
            "accepted_claims": final_check.accepted_claims,
            "trace": [f"finalize: {len(report.citations)} citation(s), "
                      f"{len(limitations)} limitation(s)"],
        }

    return finalize


def make_unknown_company_node(deps: GraphDeps) -> Callable[[ResearchState], dict]:
    def end_unknown(state: ResearchState) -> dict:
        ticker = state["ticker"].upper()
        return {
            "final_answer": (
                f"Unknown ticker {ticker!r}: no company record in the database. "
                f"Ingest the company (scripts/process_company.py) before researching."
            ),
            "limitations": [f"company {ticker} not in database; no research performed"],
            "trace": [f"end: unknown ticker {ticker}"],
        }

    return end_unknown


# ---------------------------------------------------------------------------
# Routing (pure functions -- every loop bound lives here)
# ---------------------------------------------------------------------------


def route_resolve(state: ResearchState) -> str:
    return "research" if state.get("company") else "end_unknown"


def make_route_research(max_iterations: int) -> Callable[[ResearchState], str]:
    def route_research(state: ResearchState) -> str:
        last = state["messages"][-1]
        has_tool_calls = isinstance(last, AIMessage) and bool(last.tool_calls)
        iterations = state.get("iterations", 0)
        if not has_tool_calls:
            return "answer"
        if iterations < max_iterations:
            return "tools"
        if iterations == max_iterations:
            return "force_stop"
        return "answer"  # never execute a research call beyond the cap

    return route_research


def make_route_validate(max_revises: int) -> Callable[[ResearchState], str]:
    def route_validate(state: ResearchState) -> str:
        report = state.get("validation_report") or {"ok": False}
        if report.get("ok"):
            return "finalize"
        if state.get("revises", 0) < max_revises:
            return "revise"
        return "finalize"  # revisions exhausted: finalize with limitations

    return route_validate
