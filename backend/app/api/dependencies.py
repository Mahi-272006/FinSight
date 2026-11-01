"""Typed dependencies for services injected through ``app.state.components``."""

from typing import Any, Callable, cast

from fastapi import HTTPException, Request

from app.database.postgres import get_session_factory
from app.graph.agent import ResearchAgent
from app.services.embeddings import Embedder
from app.services.llm_manager import LLMManager
from app.services.report_service import ReportService
from app.services.thesis_service import ThesisService
from app.services.vector_store import VectorStore
from app.services.watchlist_service import WatchlistService


def _require_component(request: Request, name: str) -> Any:
    """Return a configured component or fail clearly as a server error."""
    components = request.app.state.components
    component = components.get(name)
    if component is None:
        raise RuntimeError(f"Required API component {name!r} is not configured.")
    return component


def get_research_agent(request: Request) -> ResearchAgent:
    """Get the injected research agent; never constructs a live agent here."""
    return cast(ResearchAgent, _require_component(request, "research_agent"))


def get_research_agent_factory(request: Request) -> Callable[[], ResearchAgent]:
    """Return a lazy, app-cached agent factory using injected dependencies.

    Components are injected by the application owner. The factory receives
    the session factory, store, embedder, and manager explicitly, then its
    resulting agent is cached on this app's state so its InMemorySaver remains
    stable for supplied thread IDs without sharing the cache across apps.
    """
    components = request.app.state.components
    existing_agent = components.get("research_agent")
    if existing_agent is not None:
        return lambda: cast(ResearchAgent, existing_agent)

    required = ("store", "embedder", "manager")
    if any(components.get(name) is None for name in required):
        raise HTTPException(
            status_code=503,
            detail="Research service is not configured.",
        )

    session_factory = components.get("session_factory")
    if session_factory is None:
        session_factory = get_session_factory()
    factory = components.get("research_agent_factory", ResearchAgent)
    if not callable(factory):
        raise HTTPException(
            status_code=503,
            detail="Research service is not configured.",
        )

    def get_or_create_agent() -> ResearchAgent:
        agent = getattr(request.app.state, "research_agent", None)
        if agent is None:
            agent = factory(
                session_factory=session_factory,
                store=components["store"],
                embedder=components["embedder"],
                manager=components["manager"],
            )
            request.app.state.research_agent = agent
        return cast(ResearchAgent, agent)

    return get_or_create_agent


def get_store(request: Request) -> VectorStore:
    """Get the injected vector store; never opens ChromaDB here."""
    return cast(VectorStore, _require_component(request, "store"))


def get_embedder(request: Request) -> Embedder:
    """Get the injected embedder; never loads an embedding model here."""
    return cast(Embedder, _require_component(request, "embedder"))


def get_manager(request: Request) -> LLMManager:
    """Get the injected LLM manager; never builds provider clients here."""
    return cast(LLMManager, _require_component(request, "manager"))


def get_thesis_service(request: Request) -> ThesisService:
    """Get or lazily create ThesisService with its own session factory.

    The service owns and commits its write transactions. It must receive a
    sessionmaker, never the request-scoped ``Depends(get_session)`` session.
    Tests can inject either a ready service or a test ``session_factory``.
    """
    components = request.app.state.components
    service = components.get("thesis_service")
    if service is None:
        session_factory = components.get("session_factory")
        if session_factory is None:
            session_factory = get_session_factory()
        service = ThesisService(
            session_factory=session_factory,
            manager=components.get("manager"),
        )
        components["thesis_service"] = service
    return cast(ThesisService, service)


def get_report_service_factory(request: Request) -> Callable[[], ReportService]:
    """Lazily construct the transaction-owning writer only when saving is requested."""
    components = request.app.state.components

    def get_service() -> ReportService:
        service = components.get("report_service")
        if service is None:
            service = getattr(request.app.state, "report_service", None)
        if service is None:
            session_factory = components.get("session_factory")
            if session_factory is None:
                session_factory = get_session_factory()
            service = ReportService(session_factory=session_factory)
            request.app.state.report_service = service
        return cast(ReportService, service)

    return get_service


def get_watchlist_service(request: Request) -> WatchlistService:
    """Get an injected or app-cached watchlist writer."""
    components = request.app.state.components
    service = components.get("watchlist_service")
    if service is None:
        service = getattr(request.app.state, "watchlist_service", None)
    if service is None:
        session_factory = components.get("session_factory")
        if session_factory is None:
            session_factory = get_session_factory()
        service = WatchlistService(session_factory=session_factory)
        request.app.state.watchlist_service = service
    return cast(WatchlistService, service)
