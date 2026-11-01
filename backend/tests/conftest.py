"""Shared pytest fixtures for FinSight tests.

Stage 1 additions (M9):
    client          -- FastAPI TestClient wrapping a fresh create_app() call.
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

from app.config import get_settings
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
def client(override_settings):
    """Return a TestClient wrapping a fresh FastAPI application.

    Why ``create_app()`` instead of the module-level ``app``?
    - Each test gets its own isolated instance -- no shared middleware state,
      no leftover exception handlers from a previous test.
    - Stage 2 can parameterise this fixture with ``components`` to inject
      mock services (DB, LLM) without modifying any test.

    The ``with`` block ensures the lifespan runs:
      - startup: ensure_directories() + configure_logging()
      - shutdown: (no-op in Stage 1; Stage 2 closes DB connections)

    ``raise_server_exceptions=False`` tells TestClient to return the HTTP
    response even when the server raises an exception, so our exception-handler
    tests can assert the JSON body instead of seeing a pytest traceback.

    Note on asyncio_mode=auto (pytest.ini):
    This is a *sync* fixture. pytest-anyio does not wrap sync generators in an
    event loop, so the TestClient's internal anyio event loop starts cleanly
    without fighting the outer anyio event loop that asyncio_mode creates for
    async tests. The fixture is intentionally sync -- TestClient itself manages
    its own event loop via anyio.from_thread.
    """
    tc = TestClient(create_app(), raise_server_exceptions=False)
    tc.__enter__()
    yield tc
    tc.__exit__(None, None, None)
