"""Full-graph tests for the M6 research agent (M6 loop behavior, offline).

A scripted manager stands in for the LLM: each `invoke` pops the next
pre-built AIMessage (tool call or final answer), so every loop path --
success, revision, iteration cap, fabrication, unknown ticker -- is
deterministic and asserts the GRAPH's behavior: routing, caps, evidence
capture, validation gating, limitation reporting.
"""

import json
from dataclasses import asdict

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import Base, Company
from app.graph import evidence
from app.graph.agent import ResearchAgent
from app.graph.nodes import GraphDeps, make_execute_tools_node
from app.graph.tools import make_search_filing_sections
from app.services.vector_store import SearchHit

ACCESSION = "0001045810-26-000075"
REVENUE = 46_743_000_000.0


# ---------------------------------------------------------------------------
# Scripted LLM (stands in for LLMManager.bind_tools(...).invoke)
# ---------------------------------------------------------------------------


class ScriptedRunnable:
    def __init__(self, script):
        self._script = list(script)

    def invoke(self, messages):
        if not self._script:
            raise AssertionError("LLM script exhausted -- graph looped past its cap?")
        return self._script.pop(0)


class ScriptedManager:
    def __init__(self, script):
        self._script = script
        self.bound_tools = None

    def bind_tools(self, tools):
        self.bound_tools = tools
        return ScriptedRunnable(self._script)


def tool_call(name="search_filing_sections", args=None, call_id="call_1"):
    return AIMessage(content="", tool_calls=[{
        "name": name,
        "args": args or {"ticker": "NVDA", "query": "export controls revenue"},
        "id": call_id,
        "type": "tool_call",
    }])


# ---------------------------------------------------------------------------
# Fakes / fixtures
# ---------------------------------------------------------------------------


class FakeEmbedder:
    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


class FakeStore:
    def __init__(self, hits):
        self.hits = hits

    def search(self, query_vector, k=5, where=None):
        return self.hits[:k]


def _hit():
    return SearchHit(
        id=f"{ACCESSION}:3",
        text=(f"Revenue was ${REVENUE:,.0f} USD. "
              "Export controls constrain sales in certain markets."),
        metadata={"cik": 1045810, "ticker": "NVDA", "form": "10-Q", "accession": ACCESSION,
                  "section": "financial_statements", "filing_date": "2026-08-26"},
        distance=0.2,
    )


FILING_EVIDENCE_ID = evidence.build_filing_chunk_evidence_id(
    1045810, ACCESSION, f"{ACCESSION}:3"
)
GOOD_QUOTE = "Export controls constrain sales in certain markets."
GOOD_CLAIM = {
    "text": GOOD_QUOTE,
    "claim_type": "filing_quote",
    "supporting_evidence_ids": [FILING_EVIDENCE_ID],
    "metric": None,
    "source_concept": None,
    "fiscal_year": None,
    "fiscal_period": None,
    "period_type": None,
    "unit": None,
    "numeric_value": None,
    "accession": ACCESSION,
    "value_scope": "as_filed",
    "quote": GOOD_QUOTE,
}
GOOD_CLAIM_JSON = json.dumps({"claims": [GOOD_CLAIM]})
GOOD_ANSWER = f"“{GOOD_QUOTE}” [{ACCESSION}]"


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    session.add(Company(cik=1045810, ticker="NVDA", name="NVIDIA Corp"))
    session.commit()
    session.close()
    try:
        yield factory
    finally:
        engine.dispose()


def make_agent(script, session_factory):
    manager = ScriptedManager(script)
    agent = ResearchAgent(
        session_factory=session_factory,
        store=FakeStore([_hit()]),
        embedder=FakeEmbedder(),
        manager=manager,
        checkpointer=None,  # InMemorySaver default
    )
    return agent, manager


# ---------------------------------------------------------------------------
# Loop scenarios
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_tool_call_then_grounded_answer_passes(self, session_factory):
        agent, manager = make_agent(
            [tool_call(), AIMessage(content=GOOD_CLAIM_JSON)], session_factory
        )
        result = agent.run("How did NVDA do?", "nvda")

        assert result.final_answer == GOOD_ANSWER
        assert result.citations == [ACCESSION]
        assert (result.validation or {})["ok"] is True
        assert result.iterations == 2
        assert result.revises == 0
        assert result.limitations == []
        # The audit trail: resolve + tool + validation + finalize.
        assert any(line.startswith("resolve: NVIDIA") for line in result.trace)
        assert any(line.startswith("tool search_filing_sections") for line in result.trace)
        # Tool call captured structurally, with its chunk ids.
        assert result.tool_calls[0]["tool"] == "search_filing_sections"
        assert result.tool_calls[0]["chunk_ids"] == [f"{ACCESSION}:3"]
        # Real tool schemas were bound (the tool actually ran in the graph).
        assert [t.name for t in manager.bound_tools] == [
            "search_filing_sections", "get_financial_facts", "get_investigation_signals",
        ]
        public_payload = asdict(result)
        json.dumps(public_payload)
        assert set(public_payload) == {
            "question", "ticker", "company", "final_answer", "citations",
            "limitations", "trace", "validation", "iterations", "revises", "tool_calls",
        }

    def test_company_resolved(self, session_factory):
        agent, _ = make_agent(
            [tool_call(), AIMessage(content=GOOD_CLAIM_JSON)], session_factory
        )
        result = agent.run("q", "NVDA")
        assert result.company == {"cik": 1045810, "ticker": "NVDA", "name": "NVIDIA Corp"}


class TestRevisePath:
    def test_bad_draft_triggers_one_revision(self, session_factory):
        bad_answer = "Revenue was $999,000,000,000 USD [0000000000-00-000001]."
        agent, _ = make_agent(
            [tool_call(), AIMessage(content=bad_answer), AIMessage(content=GOOD_CLAIM_JSON)],
            session_factory,
        )
        result = agent.run("q", "NVDA")

        assert result.revises == 1
        assert result.final_answer == GOOD_ANSWER
        assert (result.validation or {})["ok"] is True
        # The revise round is visible in the audit trail; its feedback
        # message reaches the LLM via the message history reducer.
        assert any(line.startswith("revise #1") for line in result.trace)


class TestIterationCap:
    def test_model_that_never_stops_is_still_bounded(self, session_factory):
        # 8 scripted tool-calling replies: force-stop fires at the cap
        # (default 6), one defensive extra call follows, then validation
        # runs on the empty draft and the graph finalizes with limitations.
        script = [tool_call(call_id=f"call_{i}") for i in range(8)]
        agent, _ = make_agent(script, session_factory)
        result = agent.run("q", "NVDA")

        assert result.iterations == 8  # 6 cap + 1 force-stop + 1 defensive
        assert result.revises == 1
        assert "insufficient" in result.final_answer.lower()
        assert result.limitations
        assert any(line.startswith("force-stop") for line in result.trace)
        # Nothing else was scripted; if the loop wanted a 9th call the
        # ScriptedRunnable would have raised AssertionError inside the graph.


class TestFabricationGuard:
    def test_answer_without_any_tool_call_is_stripped(self, session_factory):
        hallucinated = (
            f"Clearly, NVDA revenue was ${REVENUE:,.0f} USD [0001045810-26-000075]. "
            "Outlook remains very strong."
        )
        agent, _ = make_agent(
            [AIMessage(content=hallucinated), AIMessage(content="No further evidence.")],
            session_factory,
        )
        result = agent.run("q", "NVDA")

        # The first draft is fabricated; validation FAILs it, revision runs,
        # and the revised honest text is what survives to the user...
        assert (result.validation or {})["ok"] is False
        assert result.revises == 1
        assert "insufficient to answer" in result.final_answer
        # ...while the fabrication never reaches the final answer.
        assert f"{REVENUE:,.0f}" not in result.final_answer
        assert "0001045810-26-000075" not in result.final_answer
        assert result.limitations


class TestUnknownTicker:
    def test_unknown_ticker_ends_gracefully_without_llm(self, session_factory):
        agent, _ = make_agent([], session_factory)  # empty script: LLM must never run
        result = agent.run("q", "ZZZZ")

        assert result.company is None
        assert "Unknown ticker" in result.final_answer
        assert result.limitations
        assert any(line.startswith("end: unknown ticker") for line in result.trace)


class TestValidationGating:
    def test_failed_validation_with_no_revisions_left_finalizes_with_limitations(
        self, session_factory
    ):
        # max_revises default is 1; give the bad draft twice with a manager
        # that answers instantly again: revise round 1 also fails ->
        # finalize with limitations, still bounded.
        bad = "Revenue was $999,000,000,000 USD [0000000000-00-000001]."
        agent, _ = make_agent(
            [tool_call(), AIMessage(content=bad), AIMessage(content=bad)],
            session_factory,
        )
        result = agent.run("q", "NVDA")

        assert result.revises == 1
        assert result.validation["ok"] is False
        assert result.limitations  # removals reported
        assert f"{999_000_000_000:,}" not in result.final_answer

    def test_validation_report_describes_the_finalized_answer(self, session_factory):
        unsupported_sentence = (
            "Revenue for the next fiscal year was $999,000,000,000 USD "
            f"[{ACCESSION}]."
        )
        unsupported_claim = {
            **GOOD_CLAIM,
            "text": unsupported_sentence,
            "claim_type": "verified_fact",
            "metric": "revenue",
            "source_concept": "RevenueFromContractWithCustomerExcludingAssessedTax",
            "fiscal_year": 2027,
            "fiscal_period": "FY",
            "period_type": "annual",
            "unit": "USD",
            "numeric_value": "999000000000",
            "quote": None,
        }
        draft = json.dumps({"claims": [GOOD_CLAIM, unsupported_claim]})
        agent, _ = make_agent(
            [tool_call(), AIMessage(content=draft), AIMessage(content=draft)],
            session_factory,
        )

        result = agent.run("What was revenue?", "NVDA")
        from app.graph.tools import make_search_filing_sections

        expected_record = make_search_filing_sections(
            FakeStore([_hit()]), FakeEmbedder()
        ).execute({"ticker": "NVDA", "query": "revenue"}).evidence_records[0]
        finalized_report = evidence.validate_answer(
            [GOOD_CLAIM], [expected_record], expected_cik=1045810
        ).report

        assert GOOD_QUOTE in result.final_answer
        assert "999,000,000,000" not in result.final_answer
        relevant_fields = (
            "ok",
            "citations",
            "unsupported_citations",
            "verified_numbers",
            "unverified_numbers",
            "notes",
        )
        assert result.validation is not None
        assert {
            field: result.validation[field]
            for field in relevant_fields
        } == {
            field: finalized_report.to_dict()[field]
            for field in relevant_fields
        }
        assert any("next fiscal year" in item for item in result.limitations)

    def test_malformed_claims_are_audited_without_revealing_their_text(self, session_factory):
        unsupported = "Unsupported hidden assertion: revenue is guaranteed to triple."
        malformed = {
            **GOOD_CLAIM,
            "text": unsupported,
            "claim_type": "verified_fact",
            "supporting_evidence_ids": "malformed ids",
        }
        draft = json.dumps({"claims": [GOOD_CLAIM, malformed]})
        agent, _ = make_agent(
            [tool_call(), AIMessage(content=draft), AIMessage(content=draft)],
            session_factory,
        )

        result = agent.run("Summarize company evidence.", "NVDA")

        assert GOOD_QUOTE in result.final_answer
        assert "guaranteed to triple" not in result.final_answer
        assert any("malformed" in item.lower() for item in result.limitations)
        assert any("guaranteed to triple" in item for item in result.limitations)


def test_tool_execution_error_is_not_accepted_as_financial_evidence():
    class FailingTool:
        def invoke(self, args):
            raise RuntimeError(
                f"Query failed after reading $999,000,000,000 "
                f"from accession {ACCESSION}"
            )

    node = make_execute_tools_node(GraphDeps(
        session_factory=None,
        manager=None,
        tools_by_name={"get_financial_facts": FailingTool()},
    ))
    state = {"messages": [AIMessage(content="", tool_calls=[{
        "name": "get_financial_facts",
        "args": {"ticker": "NVDA"},
        "id": "tool-error-1",
        "type": "tool_call",
    }])]}

    result = node(state)
    assert result["tool_observations"][0]["status"] == "error"
    assert result["tool_observations"][0]["evidence_ids"] == []
    assert result["evidence_records"] == []
    assert ACCESSION not in result["tool_observations"][0]["content"]
    assert "999,000,000,000" not in result["tool_observations"][0]["content"]
    report = evidence.validate_answer(
        [GOOD_CLAIM], result.get("evidence_records", []), expected_cik=1045810
    ).report

    assert not report.ok


def test_duplicate_tool_results_are_deduplicated_in_graph_state():
    tool = make_search_filing_sections(FakeStore([_hit()]), FakeEmbedder())
    node = make_execute_tools_node(GraphDeps(
        session_factory=None, manager=None, tools_by_name={tool.name: tool},
    ))
    state = {"messages": [AIMessage(content="", tool_calls=[
        {"name": tool.name, "args": {"ticker": "NVDA", "query": "revenue"},
         "id": "duplicate-1", "type": "tool_call"},
        {"name": tool.name, "args": {"ticker": "NVDA", "query": "revenue"},
         "id": "duplicate-2", "type": "tool_call"},
    ])]}

    result = node(state)

    assert len(result["evidence_records"]) == 1
    assert all(item["status"] == "success" for item in result["tool_observations"])
    assert result["tool_observations"][0]["evidence_ids"] == (
        result["tool_observations"][1]["evidence_ids"]
    )

    later = node({
        "messages": [AIMessage(content="", tool_calls=[{
            "name": tool.name,
            "args": {"ticker": "NVDA", "query": "revenue"},
            "id": "later-iteration",
            "type": "tool_call",
        }])],
        "evidence_records": result["evidence_records"],
    })
    assert later["evidence_records"] == []
    assert later["tool_observations"][0]["evidence_ids"] == (
        result["tool_observations"][0]["evidence_ids"]
    )


def test_unknown_tool_and_empty_result_have_no_evidence_ids():
    empty_tool = make_search_filing_sections(FakeStore([]), FakeEmbedder())
    node = make_execute_tools_node(GraphDeps(
        session_factory=None, manager=None, tools_by_name={empty_tool.name: empty_tool},
    ))
    state = {"messages": [AIMessage(content="", tool_calls=[
        {"name": empty_tool.name, "args": {"ticker": "NVDA", "query": "none"},
         "id": "empty-1", "type": "tool_call"},
        {"name": "unknown", "args": {}, "id": "unknown-1", "type": "tool_call"},
    ])]}

    result = node(state)

    assert result["evidence_records"] == []
    assert [o["status"] for o in result["tool_observations"]] == ["no_results", "unknown_tool"]
    assert all(o["evidence_ids"] == [] for o in result["tool_observations"])


class TestResearchNodeTaxonomy:
    """Raw chain failures must surface as OUR LLMError taxonomy (M6).

    langchain's RunnableWithFallbacks re-raises the first branch's RAW SDK
    error when every provider failed (observed live): without this boundary
    classification the CLI would print a groq/google traceback instead of
    exiting 2 with a readable cause.
    """

    @staticmethod
    def _node_for(chain):
        from app.graph import nodes as graph_nodes

        deps = graph_nodes.GraphDeps(
            session_factory=None, manager=None, tools_by_name={},
        )
        return graph_nodes.make_research_node(deps, chain)

    def test_raw_exception_is_classified(self):
        from app.services.llm_providers import LLMProviderError

        class ExplodingChain:
            def invoke(self, messages):
                raise RuntimeError("provider exploded")

        node = self._node_for(ExplodingChain())
        with pytest.raises(LLMProviderError, match="provider exploded"):
            node({"messages": [], "iterations": 0})

    def test_already_classified_error_passes_through_unchanged(self):
        from app.services.llm_providers import LLMAllProvidersFailed

        class FailingChain:
            def invoke(self, messages):
                raise LLMAllProvidersFailed([("groq", "all down")])

        node = self._node_for(FailingChain())
        with pytest.raises(LLMAllProvidersFailed, match="all down"):
            node({"messages": [], "iterations": 0})
