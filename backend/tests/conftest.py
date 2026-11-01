"""Shared pytest fixtures for FinSight tests.

M9 fixtures:
    client          -- FastAPI TestClient wrapping a fresh create_app() call.
    client_factory  -- scoped TestClient with optional components/overrides.
    api_session_factory -- per-test SQLite DB shared across TestClient threads.
    override_settings -- clears the lru_cache so monkeypatched env vars are
                         picked up by the next get_settings() call.

Earlier stubs are preserved below -- per-milestone fixtures are added as
milestones land.

# Intentionally minimal for now. Fixtures are added per-milestone:
#   M2: engine/session fixtures for PostgreSQL tests
#   M4: ChromaDB temporary-collection fixture
#   M5: mocked-provider fixtures for fallback tests
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.database.models import Base
from app.main import create_app


# ---------------------------------------------------------------------------
# Settings cache management
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=False)
def override_settings():
    """Clear the get_settings() lru_cache before AND after each test.

    Use this fixture (or monkeypatch.setenv) in any test that needs to
    exercise a different configuration value:

        def test_custom_cors(override_settings, monkeypatch):
            monkeypatch.setenv("CORS_ORIGINS", "http://example.com")
            settings = get_settings()           # re-reads env
            assert settings.cors_origins == "http://example.com"

    The cache is cleared on the way out too so the next test always starts
    from a clean slate regardless of order.
    """
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# HTTP test client
# ---------------------------------------------------------------------------

@pytest.fixture()
def api_session_factory():
    """Return a per-test SQLite sessionmaker shared across TestClient threads."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.fixture()
def client_factory(override_settings):
    """Build scoped TestClients with injected components and dependency overrides.

    ``configure_app`` is a test-only seam for mounting probe routes before the
    TestClient starts. Overrides are always cleared, even if setup or a test
    body raises.
    """
    from contextlib import contextmanager

    @contextmanager
    def make_client(*, components=None, dependency_overrides=None, configure_app=None):
        app = create_app(components=components)
        if configure_app is not None:
            configure_app(app)
        app.dependency_overrides.update(dependency_overrides or {})
        try:
            with TestClient(app, raise_server_exceptions=False) as tc:
                yield tc
        finally:
            app.dependency_overrides.clear()

    return make_client


@pytest.fixture()
def client(client_factory):
    """Default isolated TestClient, retaining the Stage 1 fixture interface."""
    with client_factory() as tc:
        yield tc
