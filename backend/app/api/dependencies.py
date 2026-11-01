"""Typed dependencies for services injected through ``app.state.components``."""

from typing import Any, cast

from fastapi import Request

from app.database.postgres import get_session_factory
from app.graph.agent import ResearchAgent
from app.services.embeddings import Embedder
from app.services.llm_manager import LLMManager
from app.services.thesis_service import ThesisService
from app.services.vector_store import VectorStore


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
