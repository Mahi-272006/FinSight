"""M9 Stage 2 tests for component injection and API database fixtures."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import (
    get_embedder,
    get_manager,
    get_research_agent,
    get_store,
    get_thesis_service,
)
from app.database import postgres
from app.database.models import Company
from app.database.postgres import get_session
from app.services.thesis_service import ThesisService


class FakeResearchAgent:
    pass


class FakeStore:
    pass


class FakeEmbedder:
    pass


class FakeManager:
    pass


def _install_probe_routes(app):
    router = APIRouter()

    @router.get("/components")
    def component_probe(
        agent=Depends(get_research_agent),
        store=Depends(get_store),
        embedder=Depends(get_embedder),
        manager=Depends(get_manager),
    ):
        components = app.state.components
        return {
            "agent": agent is components["research_agent"],
            "store": store is components["store"],
            "embedder": embedder is components["embedder"],
            "manager": manager is components["manager"],
        }

    @router.get("/thesis-service")
    def thesis_service_probe(service=Depends(get_thesis_service)):
        return {"service_type": isinstance(service, ThesisService)}

    @router.get("/db")
    def database_probe(session: Session = Depends(get_session)):
        return {
            "real_session": isinstance(session, Session),
            "companies": session.query(Company).count(),
        }

    app.include_router(router, prefix="/probe")


def test_component_accessors_return_fakes_without_constructing_live_services(
    client_factory, monkeypatch
):
    from app.graph.agent import ResearchAgent
    from app.services.embeddings import SentenceTransformerEmbedder
    from app.services.vector_store import VectorStore

    def unexpected_construction(*args, **kwargs):
        raise AssertionError("API test attempted to construct a live component")

    monkeypatch.setattr(ResearchAgent, "__init__", unexpected_construction)
    monkeypatch.setattr(VectorStore, "__init__", unexpected_construction)
    monkeypatch.setattr(
        SentenceTransformerEmbedder, "__init__", unexpected_construction
    )
    monkeypatch.setattr(
        "app.services.llm_manager.build_manager", unexpected_construction
    )

    components = {
        "research_agent": FakeResearchAgent(),
        "store": FakeStore(),
        "embedder": FakeEmbedder(),
        "manager": FakeManager(),
    }

    with client_factory(
        components=components, configure_app=_install_probe_routes
    ) as client:
        response = client.get("/probe/components")

    assert response.status_code == 200
    assert response.json() == {
        "agent": True,
        "store": True,
        "embedder": True,
        "manager": True,
    }


def test_thesis_service_uses_injected_factory_and_manager(
    client_factory, api_session_factory
):
    manager = FakeManager()
    components = {"session_factory": api_session_factory, "manager": manager}

    with client_factory(
        components=components, configure_app=_install_probe_routes
    ) as client:
        response = client.get("/probe/thesis-service")

    assert response.status_code == 200
    assert response.json() == {"service_type": True}
    service = components["thesis_service"]
    assert service._session_factory is api_session_factory
    assert service._manager is manager


def test_get_session_dependency_shares_sqlite_data_across_testclient_threads(
    client_factory, api_session_factory, monkeypatch
):
    monkeypatch.setattr(
        postgres, "get_session_factory", lambda: api_session_factory
    )
    with api_session_factory() as session:
        session.add(Company(cik=789019, ticker="MSFT", name="Microsoft"))
        session.commit()

    with client_factory(configure_app=_install_probe_routes) as client:
        response = client.get("/probe/db")

    assert response.status_code == 200
    assert response.json() == {"real_session": True, "companies": 1}


def test_dependency_override_is_used_and_cleared(
    client_factory, api_session_factory
):
    calls = []

    def override_get_session():
        session = api_session_factory()
        try:
            calls.append(True)
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    with client_factory(
        configure_app=_install_probe_routes,
        dependency_overrides={get_session: override_get_session},
    ) as client:
        app = client.app
        response = client.get("/probe/db")
        assert get_session in app.dependency_overrides

    assert response.status_code == 200
    assert calls == [True]
    assert app.dependency_overrides == {}
