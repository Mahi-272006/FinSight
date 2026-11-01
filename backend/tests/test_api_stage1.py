"""Stage 1 API tests: health endpoint, middleware, exception handlers.

These tests cover everything added in M9 Stage 1 *without* touching any
heavy service (no DB, no ChromaDB, no LLM calls).  They run in-process
using FastAPI's TestClient so no network port is needed.

Test layout:
    TestHealth              -- GET /api/v1/health: status, payload, headers
    TestRequestIDMiddleware -- X-Request-ID header present on every response
    TestCORS                -- origins allowed / blocked per Settings
    TestExceptionHandlers   -- each custom handler → correct HTTP status + body
    TestAppFactory          -- create_app() isolation + components seam
"""

import logging
import re

import pytest
from fastapi.testclient import TestClient
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config import APP_VERSION, get_settings
from app.main import create_app
from app.services.llm_providers import LLMAllProvidersFailed, LLMConfigurationError
from app.services.thesis_service import ThesisInputError, ThesisNotFoundError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _app_with_probe_route(exc_factory):
    """Build a fresh app and add a /probe route that raises exc_factory().

    This lets us test every exception handler without needing a real service
    to throw the exception -- the probe endpoint is only added for the
    duration of the test.

    Args:
        exc_factory: zero-argument callable that returns an exception instance.
    """
    app = create_app()
    probe = APIRouter()

    @probe.get("/probe")
    def _raise():
        raise exc_factory()

    app.include_router(probe, prefix="/api/v1")
    return app


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_200(self, client):
        """GET /api/v1/health must return 200."""
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200

    def test_health_payload_keys(self, client):
        """Response body must have status, version, and environment."""
        body = client.get("/api/v1/health").json()
        assert "status" in body
        assert "version" in body
        assert "environment" in body

    def test_health_status_ok(self, client):
        """status field must be the literal string 'ok'."""
        body = client.get("/api/v1/health").json()
        assert body["status"] == "ok"

    def test_health_version_matches_constant(self, client):
        """version field must match the APP_VERSION constant from config."""
        body = client.get("/api/v1/health").json()
        assert body["version"] == APP_VERSION

    def test_health_environment_is_string(self, client):
        """environment field must be a non-empty string."""
        body = client.get("/api/v1/health").json()
        assert isinstance(body["environment"], str)
        assert body["environment"]  # non-empty


# ---------------------------------------------------------------------------
# Request-ID middleware
# ---------------------------------------------------------------------------

class TestRequestIDMiddleware:
    def test_health_response_has_request_id_header(self, client):
        """Every response must carry X-Request-ID."""
        resp = client.get("/api/v1/health")
        assert "x-request-id" in resp.headers

    def test_request_id_is_non_empty_string(self, client):
        """X-Request-ID must be a non-empty string (8 hex chars)."""
        rid = client.get("/api/v1/health").headers["x-request-id"]
        assert isinstance(rid, str)
        assert len(rid) > 0

    def test_request_id_changes_per_request(self, client):
        """Two consecutive requests must get distinct request IDs."""
        rid1 = client.get("/api/v1/health").headers["x-request-id"]
        rid2 = client.get("/api/v1/health").headers["x-request-id"]
        assert rid1 != rid2

    def test_unknown_path_still_has_request_id(self, client):
        """Even a 404 response must carry X-Request-ID (middleware ran)."""
        resp = client.get("/api/v1/does-not-exist")
        assert "x-request-id" in resp.headers


# ---------------------------------------------------------------------------
# CORS middleware
# ---------------------------------------------------------------------------

class TestCORS:
    """CORS headers depend on the Origin header sent by the browser.

    We test with the defaults: http://localhost:5173 and http://127.0.0.1:5173.
    A preflight OPTIONS request is the canonical way to inspect CORS behavior.
    """

    ALLOWED_ORIGIN = "http://localhost:5173"
    BLOCKED_ORIGIN = "http://evil.example.com"

    def test_allowed_origin_gets_acao_header(self, client):
        """A preflight from an allowed origin must receive ACAO header."""
        resp = client.options(
            "/api/v1/health",
            headers={
                "Origin": self.ALLOWED_ORIGIN,
                "Access-Control-Request-Method": "GET",
            },
        )
        assert "access-control-allow-origin" in resp.headers

    def test_allowed_origin_reflected(self, client):
        """ACAO header must echo back the specific allowed origin."""
        resp = client.options(
            "/api/v1/health",
            headers={
                "Origin": self.ALLOWED_ORIGIN,
                "Access-Control-Request-Method": "GET",
            },
        )
        acao = resp.headers.get("access-control-allow-origin", "")
        # Starlette may reflect the exact origin or use "*".
        assert acao == self.ALLOWED_ORIGIN or acao == "*"

    def test_disallowed_origin_no_acao(self, client):
        """A preflight from a blocked origin must NOT receive ACAO header."""
        resp = client.options(
            "/api/v1/health",
            headers={
                "Origin": self.BLOCKED_ORIGIN,
                "Access-Control-Request-Method": "GET",
            },
        )
        acao = resp.headers.get("access-control-allow-origin", "")
        assert acao != self.BLOCKED_ORIGIN
        assert acao != "*"

    def test_second_allowed_origin(self, client):
        """http://127.0.0.1:5173 is a separate origin; it must also be allowed."""
        resp = client.options(
            "/api/v1/health",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert "access-control-allow-origin" in resp.headers


# ---------------------------------------------------------------------------
# Centralized exception handlers
# ---------------------------------------------------------------------------

class TestExceptionHandlers:
    """Each custom exception class must map to the correct HTTP status.

    We inject a /probe route that raises the exception, then assert:
      - the status code is correct
      - the body has a 'detail' key
      - no stack trace or internal detail leaks into the body
    """

    def _client_for(self, exc_factory) -> TestClient:
        app = _app_with_probe_route(exc_factory)
        # raise_server_exceptions=False: return the HTTP response even on error
        return TestClient(app, raise_server_exceptions=False)

    # --- ThesisNotFoundError → 404 -----------------------------------------

    def test_thesis_not_found_status(self):
        with self._client_for(lambda: ThesisNotFoundError("Thesis 99 not found")) as c:
            resp = c.get("/api/v1/probe")
        assert resp.status_code == 404

    def test_thesis_not_found_has_detail(self):
        with self._client_for(lambda: ThesisNotFoundError("Thesis 99 not found")) as c:
            body = c.get("/api/v1/probe").json()
        assert "detail" in body

    def test_thesis_not_found_message_is_safe(self):
        """The safe exception message is forwarded (it contains no internals)."""
        with self._client_for(lambda: ThesisNotFoundError("Thesis 99 not found")) as c:
            body = c.get("/api/v1/probe").json()
        assert "99" in body["detail"]  # the ID is safe to surface

    # --- ThesisInputError → 400 --------------------------------------------

    def test_thesis_input_error_status(self):
        with self._client_for(lambda: ThesisInputError("condition parse failed")) as c:
            resp = c.get("/api/v1/probe")
        assert resp.status_code == 400

    def test_thesis_input_error_has_detail(self):
        with self._client_for(lambda: ThesisInputError("condition parse failed")) as c:
            body = c.get("/api/v1/probe").json()
        assert "detail" in body

    # --- LLMConfigurationError → 503 (sanitized) ---------------------------

    def test_llm_config_error_status(self):
        with self._client_for(lambda: LLMConfigurationError("no key set")) as c:
            resp = c.get("/api/v1/probe")
        assert resp.status_code == 503

    def test_llm_config_error_body_sanitized(self):
        """Internal provider message 'no key set' must NOT appear in response."""
        with self._client_for(lambda: LLMConfigurationError("no key set")) as c:
            body = c.get("/api/v1/probe").json()
        assert "detail" in body
        assert "no key set" not in body["detail"]

    # --- LLMAllProvidersFailed → 503 (sanitized) ---------------------------

    def test_llm_all_failed_status(self):
        with self._client_for(
            lambda: LLMAllProvidersFailed([("groq", "timeout"), ("gemini", "auth")])
        ) as c:
            resp = c.get("/api/v1/probe")
        assert resp.status_code == 503

    def test_llm_all_failed_body_sanitized(self):
        """Provider names and failure causes must NOT appear in response."""
        with self._client_for(
            lambda: LLMAllProvidersFailed([("groq", "timeout"), ("gemini", "auth")])
        ) as c:
            body = c.get("/api/v1/probe").json()
        assert "detail" in body
        assert "groq" not in body["detail"]
        assert "gemini" not in body["detail"]
        assert "timeout" not in body["detail"]

    # --- Generic Exception → 500 (sanitized) --------------------------------

    def test_unhandled_exception_status(self):
        with self._client_for(lambda: RuntimeError("secret internal detail")) as c:
            resp = c.get("/api/v1/probe")
        assert resp.status_code == 500

    def test_unhandled_exception_has_request_id_and_is_logged(self, caplog):
        caplog.set_level(logging.ERROR, logger="app.main")
        secret = "private-diagnostic-marker"
        with self._client_for(lambda: RuntimeError(secret)) as c:
            resp = c.get("/api/v1/probe")

        request_id = resp.headers["x-request-id"]
        assert re.fullmatch(r"[0-9a-f]{8}", request_id)
        assert resp.status_code == 500
        assert resp.json() == {"detail": "An internal server error occurred."}
        assert secret not in resp.text
        assert "Traceback" not in resp.text
        assert "RuntimeError" not in resp.text
        assert request_id in caplog.text
        assert "GET /api/v1/probe" in caplog.text
        assert "→ 500" in caplog.text
        assert "RuntimeError" in caplog.text

    def test_unhandled_exception_no_internal_detail(self):
        """Internal error message must NEVER reach the client in a 500."""
        with self._client_for(lambda: RuntimeError("secret internal detail")) as c:
            body = c.get("/api/v1/probe").json()
        assert "detail" in body
        assert "secret internal detail" not in body["detail"]

    def test_unhandled_exception_no_traceback(self):
        """'Traceback' keyword must never appear in the 500 response body."""
        with self._client_for(lambda: RuntimeError("oops")) as c:
            text = c.get("/api/v1/probe").text
        assert "Traceback" not in text
        assert "RuntimeError" not in text


# ---------------------------------------------------------------------------
# Application factory isolation
# ---------------------------------------------------------------------------

class TestAppFactory:
    def test_two_calls_return_distinct_instances(self):
        """create_app() must never return the same object twice."""
        app1 = create_app()
        app2 = create_app()
        assert app1 is not app2

    def test_components_stored_on_state(self):
        """Components dict is stored on app.state for Stage 2 injection."""
        fake = {"db": "mock-session", "llm": "mock-manager"}
        app = create_app(components=fake)
        assert app.state.components == fake

    def test_no_components_defaults_to_empty_dict(self):
        """create_app() with no args must set components to an empty dict."""
        app = create_app()
        # state.components isn't set until lifespan startup; we verify that
        # a TestClient run (which triggers lifespan) sets it correctly.
        with TestClient(app, raise_server_exceptions=False) as c:
            c.get("/api/v1/health")  # triggers startup
        assert app.state.components == {}

