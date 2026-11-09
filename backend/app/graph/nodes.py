"""Graph nodes: deterministic plumbing around the LLM research loop.

Node behavior (each is a small function over ResearchState):

- `resolve`    deterministic ticker -> company lookup (no LLM).
- `research`   one LLM call through the tool-bound, failover-preserving
               runnable; increments the iteration counter.
- `execute_tools` runs each tool call from the last AI message and records
               a STRUCTURED observation per call (full text + chunk ids).
               Hand-rolled instead of langgraph.prebuilt.ToolNode on
               purpose: the validation node needs the evidence as data, and
               capturing it where it is produced is the only way to keep
               one source of truth.
- `force_stop` the iteration-cap voice: tells the model to answer now.
- `validate`   pure-function verdict over the draft vs. observations.
- `revise`     turns the validation report into feedback for one more
               research round (bounded by max_revises).
- `finalize`   strips sentences the evidence cannot back, reports every
               removal as a limitation, and assembles the final answer.

The LLM nodes never decide control flow; routing functions (pure, below)
do, so every loop bound is code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

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
            ai_message: AIMessage = chain.invoke(state["messages"])
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
                content=("Research iteration limit reached. Answer NOW using only "
                         "evidence already retrieved; do not call more tools.")
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
        report = state.get("validation_report") or {}
        unsupported = report.get("unsupported_citations", [])
        unverified = report.get("unverified_numbers", [])
        notes = report.get("notes", [])
        note_text = f"; {'; '.join(notes)}" if notes else ""
        feedback = HumanMessage(
            content=(
                "Your draft failed evidence validation: "
                f"{len(unsupported)} unsupported citation(s), "
                f"{len(unverified)} unverified number(s){note_text}. "
                f"Unsupported citations: {unsupported}. "
                f"Unverified numbers: {unverified}. "
                "Revise using the required JSON claims format. Each factual "
                "claim must name supporting_evidence_ids from this run and "
                "include exact metric, fiscal period, unit, and numeric_value "
                "fields when applicable. Do not include unsupported interpretations."
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
            return "validate"
        if iterations < max_iterations:
            return "tools"
        if iterations == max_iterations:
            return "force_stop"  # one last LLM call, instructed to answer
        return "validate"  # model ignored the force-stop; validate anyway

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
