"""Production research-service composition and lifecycle tests."""

import pytest

import app.main as main_module
from app.config import get_settings
from app.main import app as production_app
from app.main import create_app
from app.services.llm_providers import LLMConfigurationError


class FakeManager:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeResearchAgent:
    def __init__(self, **kwargs):
        self.dependencies = kwargs


def _patch_research_constructors(monkeypatch, *, manager, store, embedder):
    from app.database import postgres
    from app.graph import agent
    from app.services import embeddings, llm_manager, vector_store

    monkeypatch.setattr(llm_manager, "build_manager", lambda settings: manager)
    monkeypatch.setattr(
        vector_store,
        "VectorStore",
        lambda *, persist_dir: store,
    )
    monkeypatch.setattr(embeddings, "SentenceTransformerEmbedder", lambda: embedder)
    monkeypatch.setattr(agent, "ResearchAgent", FakeResearchAgent)
    monkeypatch.setattr(postgres, "get_session_factory", lambda: "test-session-factory")


def _advance_without_event_loop(awaitable):
    """Drive this synchronous startup lifespan to its next async boundary."""
    try:
        awaitable.send(None)
    except StopIteration as completed:
        return completed.value
    raise AssertionError("Application lifespan unexpectedly requires an event loop")


def _run_lifespan(app, during=None):
    context = app.router.lifespan_context(app)
    _advance_without_event_loop(context.__aenter__())
    try:
        if during is not None:
            during()
    finally:
        _advance_without_event_loop(context.__aexit__(None, None, None))


def test_production_app_composes_and_closes_research_services(monkeypatch):
    manager = FakeManager()
    store = object()
    embedder = object()
    _patch_research_constructors(
        monkeypatch, manager=manager, store=store, embedder=embedder
    )
    app = create_app(initialize_production_research=True)

    def check_components():
        assert app.state.components["manager"] is manager
        assert app.state.components["store"] is store
        assert app.state.components["embedder"] is embedder
        research_agent = app.state.components["research_agent"]
        assert research_agent.dependencies == {
            "session_factory": "test-session-factory",
            "store": store,
            "embedder": embedder,
            "manager": manager,
            "settings": get_settings(),
        }

    _run_lifespan(app, check_components)

    assert manager.closed
    assert app.state.components == {}


def test_module_level_uvicorn_app_runs_production_research_initialization(
    monkeypatch,
):
    manager = FakeManager()
    store = object()
    embedder = object()
    _patch_research_constructors(
        monkeypatch, manager=manager, store=store, embedder=embedder
    )
    from app.database import postgres

    engine_resets = []
    monkeypatch.setattr(postgres, "reset_engine", lambda: engine_resets.append(True))
    builder_calls = []
    real_builder = main_module._build_production_research_components

    def tracked_builder(settings):
        builder_calls.append(settings)
        return real_builder(settings)

    monkeypatch.setattr(
        main_module, "_build_production_research_components", tracked_builder
    )

    def check_components():
        assert set(production_app.state.components) == {
            "store",
            "embedder",
            "manager",
            "session_factory",
            "research_agent",
        }
        assert production_app.state.components["store"] is store
        assert production_app.state.components["embedder"] is embedder
        assert production_app.state.components["manager"] is manager

    _run_lifespan(production_app, check_components)

    assert len(builder_calls) == 1
    assert manager.closed
    assert engine_resets == [True]
    assert production_app.state.components == {}


def test_missing_llm_configuration_aborts_startup_without_leaking_details(
    monkeypatch, caplog
):
    secret_marker = "gsk-never-log-this"

    def missing_configuration(settings):
        raise LLMConfigurationError(f"invalid provider config {secret_marker}")

    from app.services import llm_manager

    monkeypatch.setattr(llm_manager, "build_manager", missing_configuration)
    app = create_app(initialize_production_research=True)

    with pytest.raises(RuntimeError, match="provider configuration") as error:
        _run_lifespan(app)

    assert secret_marker not in str(error.value)
    assert secret_marker not in caplog.text
    assert "LLM provider manager" in caplog.text
    assert "LLMConfigurationError" in caplog.text
    assert "LLM_PROVIDER_CHAIN" in caplog.text
    assert app.state.components == {}


def test_later_startup_failure_closes_manager_and_publishes_no_partial_components(
    monkeypatch, caplog,
):
    manager = FakeManager()
    from app.database import postgres
    from app.graph import agent
    from app.services import embeddings, llm_manager, vector_store

    monkeypatch.setattr(llm_manager, "build_manager", lambda settings: manager)
    monkeypatch.setattr(
        vector_store,
        "VectorStore",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("private details")),
    )
    monkeypatch.setattr(embeddings, "SentenceTransformerEmbedder", lambda: object())
    monkeypatch.setattr(agent, "ResearchAgent", FakeResearchAgent)
    monkeypatch.setattr(postgres, "get_session_factory", lambda: "test-session-factory")
    app = create_app(initialize_production_research=True)

    with pytest.raises(RuntimeError, match="ChromaDB vector store") as error:
        _run_lifespan(app)

    assert "private details" not in str(error.value)
    assert "ChromaDB vector store" in caplog.text
    assert "RuntimeError" in caplog.text
    assert "CHROMA_PERSIST_DIRECTORY" in caplog.text
    assert manager.closed
    assert app.state.components == {}


def test_database_configuration_failure_logs_safe_stage_hint(monkeypatch, caplog):
    manager = FakeManager()
    store = object()
    embedder = object()
    _patch_research_constructors(
        monkeypatch, manager=manager, store=store, embedder=embedder
    )
    from app.database import postgres

    secret_marker = "postgres-password-must-not-appear"

    def missing_database_configuration():
        raise RuntimeError(f"connection string contains {secret_marker}")

    monkeypatch.setattr(
        postgres, "get_session_factory", missing_database_configuration
    )
    app = create_app(initialize_production_research=True)

    with pytest.raises(RuntimeError, match="database session factory") as error:
        _run_lifespan(app)

    assert secret_marker not in str(error.value)
    assert secret_marker not in caplog.text
    assert "DATABASE_URL" in caplog.text
    assert manager.closed
    assert app.state.components == {}


def test_agent_initialization_failure_disposes_database_pool(monkeypatch):
    manager = FakeManager()
    from app.database import postgres
    from app.graph import agent
    from app.services import embeddings, llm_manager, vector_store

    monkeypatch.setattr(llm_manager, "build_manager", lambda settings: manager)
    monkeypatch.setattr(vector_store, "VectorStore", lambda **kwargs: object())
    monkeypatch.setattr(embeddings, "SentenceTransformerEmbedder", lambda: object())
    monkeypatch.setattr(
        agent,
        "ResearchAgent",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("private details")),
    )
    monkeypatch.setattr(postgres, "get_session_factory", lambda: "test-session-factory")
    disposed = []
    monkeypatch.setattr(postgres, "reset_engine", lambda: disposed.append(True))
    app = create_app(initialize_production_research=True)

    with pytest.raises(RuntimeError, match="research agent graph") as error:
        _run_lifespan(app)

    assert "private details" not in str(error.value)
    assert manager.closed
    assert disposed == [True]
    assert app.state.components == {}


def test_injected_test_app_skips_production_construction(monkeypatch):
    from app.services import embeddings, llm_manager, vector_store

    def unexpected_construction(*args, **kwargs):
        raise AssertionError("injected API test attempted production construction")

    monkeypatch.setattr(llm_manager, "build_manager", unexpected_construction)
    monkeypatch.setattr(vector_store, "VectorStore", unexpected_construction)
    monkeypatch.setattr(
        embeddings, "SentenceTransformerEmbedder", unexpected_construction
    )
    monkeypatch.setattr("app.graph.agent.ResearchAgent", unexpected_construction)
    components = {"store": object(), "embedder": object(), "manager": FakeManager()}
    app = create_app(components=components)

    _run_lifespan(app, lambda: assert_components_reference(app, components))
    assert not components["manager"].closed


def assert_components_reference(app, components):
    assert app.state.components is components
