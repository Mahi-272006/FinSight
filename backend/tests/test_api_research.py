"""Research API tests use fake agents and injected component dependencies."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
from fastapi import status

from app.graph.agent import ResearchResult
from app.database.models import Company
from app.services.llm_manager import LLMManager
from app.services.llm_providers import (
    LLMAllProvidersFailed,
    LLMConfigurationError,
    LLMProviderError,
    LLMRequestTooLargeError,
)


class FakeStore:
    pass


class FakeEmbedder:
    pass


class FakeManager:
    pass


class ScriptedResearchAgent:
    def __init__(self, *, company=None, error=None, delay=0):
        self.company = company or {"cik": 789019, "ticker": "MSFT", "name": "Microsoft Corp"}
        self.error = error
        self.delay = delay
        self.calls = []
        self.results = []
        self._guard = threading.Lock()
        self.active = 0
        self.max_active = 0

    def run(self, question, ticker, *, thread_id=None):
        with self._guard:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.calls.append((question, ticker, thread_id))
        try:
            if self.delay:
                time.sleep(self.delay)
            if self.error is not None:
                raise self.error
            result = ResearchResult(
                question=question,
                ticker=ticker.upper(),
                company=self.company,
                final_answer=f"Scripted evidence-backed answer for: {question}",
                citations=["0000789019-26-000001"],
                limitations=["One filing was available."],
                trace=["resolve: Microsoft Corp", "finalize: 1 citation(s)"],
                validation={"ok": True, "unsupported_citations": []},
                iterations=2,
                revises=0,
                tool_calls=[
                    {
                        "tool": "search_filing_sections",
                        "args": {"ticker": ticker.upper(), "query": question},
                        "chunk_ids": ["0000789019-26-000001:3"],
                    }
                ],
            )
            self.results.append(result)
            return result
        finally:
            with self._guard:
                self.active -= 1


class RecordingAgentFactory:
    def __init__(self, agent):
        self.agent = agent
        self.calls = []

    def __call__(self, *, session_factory, store, embedder, manager):
        self.calls.append((session_factory, store, embedder, manager))
        return self.agent


@pytest.fixture
def research_setup(api_session_factory):
    with api_session_factory() as session:
        session.add(Company(cik=789019, ticker="MSFT", name="Microsoft Corp"))
        session.commit()
    agent = ScriptedResearchAgent()
    factory = RecordingAgentFactory(agent)
    components = {
        "session_factory": api_session_factory,
        "store": FakeStore(),
        "embedder": FakeEmbedder(),
        "manager": FakeManager(),
        "research_agent_factory": factory,
    }
    return components, factory, agent


@pytest.fixture
def research_client(client_factory, research_setup):
    components, factory, agent = research_setup
    with client_factory(components=components) as client:
        yield client, client.app.state.components, factory, agent


def test_research_serializes_actual_research_result_fields(research_client):
    client, _, factory, agent = research_client

    response = client.post(
        "/api/v1/research",
        json={"ticker": "MSFT", "question": "What supports revenue growth?"},
    )

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["question"] == "What supports revenue growth?"
    assert body["ticker"] == "MSFT"
    assert body["company"] == {
        "cik": 789019,
        "ticker": "MSFT",
        "name": "Microsoft Corp",
    }
    assert body["final_answer"] == (
        "Scripted evidence-backed answer for: What supports revenue growth?"
    )
    assert body["citations"] == ["0000789019-26-000001"]
    assert body["limitations"] == ["One filing was available."]
    assert body["trace"] == ["resolve: Microsoft Corp", "finalize: 1 citation(s)"]
    assert body["validation"] == {"ok": True, "unsupported_citations": []}
    assert body["iterations"] == 2
    assert body["revises"] == 0
    assert body["tool_calls"][0]["chunk_ids"] == ["0000789019-26-000001:3"]
    assert body["save_requested"] is True
    assert isinstance(body["report_id"], int)
    assert factory.calls == [
        (
            research_client[1]["session_factory"],
            research_client[1]["store"],
            research_client[1]["embedder"],
            research_client[1]["manager"],
        )
    ]
    assert client.app.state.research_agent is agent


@pytest.mark.parametrize(
    "payload",
    [
        {"ticker": "BAD!", "question": "Valid question"},
        {"ticker": "MSFT", "question": "   "},
        {"ticker": "MSFT", "question": "Valid question", "thread_id": "  "},
    ],
)
def test_invalid_ticker_or_question_fields_return_422(research_client, payload):
    client, _, _, agent = research_client

    response = client.post("/api/v1/research", json=payload)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert response.json() == {"detail": "Request validation failed."}
    assert agent.calls == []


def test_generated_thread_ids_are_unique_uuid4_values(research_client):
    client, _, _, agent = research_client

    first = client.post(
        "/api/v1/research", json={"ticker": "MSFT", "question": "First question"}
    )
    second = client.post(
        "/api/v1/research", json={"ticker": "MSFT", "question": "Second question"}
    )

    first_id = first.json()["thread_id"]
    second_id = second.json()["thread_id"]
    assert first.status_code == second.status_code == status.HTTP_200_OK
    assert first_id != second_id
    assert len(first_id) == len(second_id) == 36
    assert UUID(first_id).version == UUID(second_id).version == 4
    assert agent.calls[0][2] == first_id
    assert agent.calls[1][2] == second_id


def test_supplied_thread_id_continues_thread_and_save_can_be_disabled(research_client):
    client, _, _, agent = research_client
    thread_id = "existing-conversation-123"

    response = client.post(
        "/api/v1/research",
        json={
            "ticker": "msft",
            "question": "Continue the prior research",
            "thread_id": thread_id,
            "save": False,
        },
    )
    continued = client.post(
        "/api/v1/research",
        json={"ticker": "MSFT", "question": "One more question", "thread_id": thread_id},
    )

    assert response.status_code == status.HTTP_200_OK
    assert continued.status_code == status.HTTP_200_OK
    assert response.json()["thread_id"] == thread_id
    assert response.json()["save_requested"] is False
    assert continued.json()["thread_id"] == thread_id
    assert agent.calls == [
        ("Continue the prior research", "MSFT", thread_id),
        ("One more question", "MSFT", thread_id),
    ]


def test_unknown_company_is_not_returned_as_success(
    client_factory, research_setup
):
    components, _, agent = research_setup
    agent.company = None
    with client_factory(components=components) as client:
        response = client.post(
            "/api/v1/research", json={"ticker": "MSFT", "question": "A question"}
        )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "Company not found."}


@pytest.mark.parametrize(
    "error,leaked_text",
    [
        (LLMConfigurationError("private api key configuration"), "private api key"),
        (
            LLMAllProvidersFailed([("private-provider", "private timeout detail")]),
            "private-provider",
        ),
    ],
)
def test_llm_failures_return_sanitized_503(research_client, error, leaked_text, caplog):
    client, _, _, agent = research_client
    agent.error = error

    response = client.post(
        "/api/v1/research", json={"ticker": "MSFT", "question": "A question"}
    )

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert leaked_text not in response.text
    assert leaked_text not in caplog.text


@pytest.mark.parametrize(
    "error,expected_status,expected_detail,secret",
    [
        (
            LLMRequestTooLargeError("provider said TPM limit 8000 key=secret", provider="groq", status_code=413),
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "Research context exceeded a provider request limit.",
            "key=secret",
        ),
        (
            LLMProviderError("provider body includes token=private", provider="gemini", status_code=502),
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "LLM service is temporarily unavailable.",
            "token=private",
        ),
    ],
)
def test_provider_failures_return_sanitized_response(
    research_client, error, expected_status, expected_detail, secret, caplog
):
    client, _, _, agent = research_client
    agent.error = error
    response = client.post(
        "/api/v1/research", json={"ticker": "MSFT", "question": "A question"}
    )
    assert response.status_code == expected_status
    assert expected_detail in response.json()["detail"]
    assert secret not in response.text
    assert secret not in caplog.text


def test_unhandled_agent_failure_is_sanitized_500(research_client):
    client, _, _, agent = research_client
    agent.error = RuntimeError("private database url")

    response = client.post(
        "/api/v1/research", json={"ticker": "MSFT", "question": "A question"}
    )

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "private database url" not in response.text


def test_unhandled_agent_failure_does_not_leak_exception_into_logs(
    research_client, caplog
):
    client, _, _, agent = research_client
    agent.error = RuntimeError("postgres://private-user:secret@internal/db")

    response = client.post(
        "/api/v1/research", json={"ticker": "MSFT", "question": "A question"}
    )

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "postgres://private-user:secret@internal/db" not in caplog.text
    assert "Traceback" not in caplog.text
    assert response.headers["X-Request-ID"] in caplog.text


def test_llm_manager_does_not_log_provider_exception_details(caplog):
    class SecretFailingProvider:
        name = "scripted-provider"

        def generate(self, messages):
            raise RuntimeError("https://user:credential@provider.invalid/api")

    manager = LLMManager([SecretFailingProvider()])
    with pytest.raises(LLMAllProvidersFailed):
        manager.generate([object()])

    assert "https://user:credential@provider.invalid/api" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_factory_receives_injected_components_once_and_agent_is_cached(
    research_client,
):
    client, components, factory, agent = research_client
    for question in ("First", "Second"):
        response = client.post(
            "/api/v1/research", json={"ticker": "MSFT", "question": question}
        )
        assert response.status_code == status.HTTP_200_OK

    assert len(factory.calls) == 1
    assert client.app.state.research_agent is agent


def test_factory_created_agent_cache_is_isolated_per_app(
    client_factory, api_session_factory
):
    class DistinctAgentFactory:
        def __init__(self):
            self.agents = []

        def __call__(self, **kwargs):
            agent = ScriptedResearchAgent()
            self.agents.append(agent)
            return agent

    factory = DistinctAgentFactory()
    components = {
        "session_factory": api_session_factory,
        "store": FakeStore(),
        "embedder": FakeEmbedder(),
        "manager": FakeManager(),
        "research_agent_factory": factory,
    }
    for _ in range(2):
        with client_factory(components=components) as client:
            response = client.post(
                "/api/v1/research", json={"ticker": "MSFT", "question": "A question"}
            )
            assert response.status_code == status.HTTP_200_OK
            assert client.app.state.research_agent is factory.agents[-1]

    assert len(factory.agents) == 2
    assert factory.agents[0] is not factory.agents[1]
    assert "research_agent" not in components


def test_failed_agent_initialization_is_not_cached_and_can_retry(
    client_factory, api_session_factory
):
    class FailOnceFactory:
        def __init__(self):
            self.attempts = 0

        def __call__(self, **kwargs):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("private initialization detail")
            return ScriptedResearchAgent()

    factory = FailOnceFactory()
    components = {
        "session_factory": api_session_factory,
        "store": FakeStore(),
        "embedder": FakeEmbedder(),
        "manager": FakeManager(),
        "research_agent_factory": factory,
    }
    with client_factory(components=components) as client:
        failed = client.post(
            "/api/v1/research", json={"ticker": "MSFT", "question": "First"}
        )
        assert not hasattr(client.app.state, "research_agent")
        succeeded = client.post(
            "/api/v1/research", json={"ticker": "MSFT", "question": "Retry"}
        )

        assert failed.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "private initialization detail" not in failed.text
        assert succeeded.status_code == status.HTTP_200_OK
        assert isinstance(client.app.state.research_agent, ScriptedResearchAgent)

    assert factory.attempts == 2


def test_research_lock_serializes_concurrent_agent_runs(client_factory, api_session_factory):
    agent = ScriptedResearchAgent(delay=0.04)
    factory = RecordingAgentFactory(agent)
    components = {
        "session_factory": api_session_factory,
        "store": FakeStore(),
        "embedder": FakeEmbedder(),
        "manager": FakeManager(),
        "research_agent_factory": factory,
    }
    with client_factory(components=components) as client:
        barrier = threading.Barrier(3)

        def post_question(number):
            barrier.wait(timeout=2)
            return client.post(
                "/api/v1/research",
                json={"ticker": "MSFT", "question": f"Question {number}"},
            )

        with ThreadPoolExecutor(max_workers=3) as executor:
            responses = list(executor.map(post_question, range(3)))

    assert all(response.status_code == status.HTTP_200_OK for response in responses)
    assert agent.max_active == 1
    thread_ids = [response.json()["thread_id"] for response in responses]
    assert len(set(thread_ids)) == 3


def test_unconfigured_research_dependencies_return_503(client_factory):
    with client_factory() as client:
        response = client.post(
            "/api/v1/research", json={"ticker": "MSFT", "question": "A question"}
        )

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json() == {"detail": "Research service is not configured."}
