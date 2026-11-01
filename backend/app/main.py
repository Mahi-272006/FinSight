"""FastAPI application factory for FinSight (M9).

Why a factory function instead of a module-level ``app = FastAPI()``?
- Tests call ``create_app()`` to get a *fresh* instance with controlled
  settings; no shared global state between test cases.
- Stage 2 will pass ``components`` (pre-built DB session, LLM manager, …)
  so handlers receive dependencies through ``request.state`` instead of
  importing module-level singletons.  The seam is here from day one.

Startup / shutdown ordering (lifespan):
    1. Ensure data & log directories exist.
    2. Configure logging (idempotent -- safe to call from CLI too).
    3. Log that the app is ready.
    Shutdown: currently a no-op; Stage 2 will close DB connections here.

Heavy services (ChromaDB, sentence-transformers, LLM providers) are NOT
initialised here.  That keeps ``import app.main`` fast, test startup cheap,
and CI green without credentials.
"""

import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.health import router as health_router
from app.api.companies import router as companies_router
from app.api.anomalies import router as anomalies_router
from app.api.thesis import router as thesis_router
from app.config import APP_VERSION, get_settings
from app.services.llm_providers import LLMAllProvidersFailed, LLMConfigurationError
from app.services.thesis_service import ThesisInputError, ThesisNotFoundError
from app.utils.logging import configure_logging, get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def create_app(components: dict[str, Any] | None = None) -> FastAPI:
    """Build and return a configured FastAPI instance.

    Args:
        components: Optional mapping of pre-built service objects (DB session
            factory, LLM manager, …).  Stored on ``app.state.components`` so
            route handlers can retrieve them via ``request.app.state.components``
            without importing module-level globals.  Pass ``None`` (the default)
            during Stage 1 -- the seam exists but nothing breaks.

    Returns:
        A fully configured ``FastAPI`` instance ready to be served by Uvicorn.
    """
    settings = get_settings()

    # ------------------------------------------------------------------
    # 1. Lifespan -- startup & shutdown hooks
    # ------------------------------------------------------------------
    @asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ANN001
        """Run setup before the first request; teardown after the last."""
        # Startup -------------------------------------------------------
        settings.ensure_directories()           # data/ and logs/ dirs
        configure_logging(                      # idempotent; safe to call twice
            level=settings.log_level,
            logs_dir=str(settings.logs_dir),
        )
        logger.info(
            "FinSight API %s starting (env=%s, log_level=%s)",
            APP_VERSION, settings.app_env, settings.log_level,
        )

        yield  # <-- app is now serving requests

        # Shutdown -------------------------------------------------------
        # Stage 2: close DB connection pool, flush async queues, etc.
        logger.info("FinSight API shutting down.")

    # ------------------------------------------------------------------
    # 2. FastAPI instance
    # ------------------------------------------------------------------
    app = FastAPI(
        title="FinSight API",
        description=(
            "Financial Evidence & Investment Thesis Monitoring Platform. "
            "Provides SEC ingestion, RAG-based research, anomaly detection, "
            "and investment thesis assessment."
        ),
        version=APP_VERSION,
        # Docs available in development; can be set to None in production via
        # an environment check if you want to hide the schema externally.
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # Store components immediately at construction time (not just in lifespan)
    # so the seam is available even before the first request.  The lifespan
    # below references this same dict -- it doesn't overwrite it.
    app.state.components = components or {}

    # ------------------------------------------------------------------
    # 3. CORS middleware
    # ------------------------------------------------------------------
    # Origins come from Settings so they can be overridden via environment
    # variable (CORS_ORIGINS=...) without touching source code.
    # allow_credentials=False: we use no cookies or HTTP auth -- sessions
    # would require True, but that also weakens the wildcard-method allowance
    # so we keep it False for now.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------
    # 4. Request-logging middleware
    # ------------------------------------------------------------------
    @app.middleware("http")
    async def log_requests(request: Request, call_next):  # noqa: ANN001
        """Log every request: method, path, status, duration, request_id.

        A short UUID prefix (8 hex chars) is generated per request and stored
        on ``request.state.request_id`` so handlers can include it in their
        own log lines for correlation.

        Using a full UUID4 would work but 8 chars is enough entropy for a
        single-server process -- collisions across requests are essentially
        impossible within any given log window.
        """
        request_id = uuid.uuid4().hex[:8]
        request.state.request_id = request_id
        start = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception as exc:
            # Starlette's ServerErrorMiddleware wraps user middleware. An
            # unhandled exception therefore escapes call_next() here and the
            # sanitized 500 is created outside this middleware. Log completion
            # before re-raising; the outer exception handler logs the traceback.
            duration_ms = (time.perf_counter() - start) * 1000
            logger.error(
                "[%s] %s %s → 500 (%.1f ms; unhandled %s)",
                request_id,
                request.method,
                request.url.path,
                duration_ms,
                type(exc).__name__,
            )
            raise

        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "[%s] %s %s → %s (%.1f ms)",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        # Echo the request_id back in a response header so client-side logs
        # can be correlated with server-side logs without grepping timestamps.
        response.headers["X-Request-ID"] = request_id
        return response

    # ------------------------------------------------------------------
    # 5. Centralized exception handlers
    # ------------------------------------------------------------------
    # IMPORTANT: these handlers must NEVER include stack traces, DSNs, API
    # keys, or provider-internal detail in the response body.  Log the real
    # error on the server; send only a safe summary to the client.

    @app.exception_handler(ThesisNotFoundError)
    async def thesis_not_found_handler(
        request: Request, exc: ThesisNotFoundError
    ) -> JSONResponse:
        """Thesis lookup failed -- 404 with the (safe) message from the exception."""
        logger.info(
            "[%s] ThesisNotFoundError: %s",
            getattr(request.state, "request_id", "-"), exc,
        )
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ThesisInputError)
    async def thesis_input_handler(
        request: Request, exc: ThesisInputError
    ) -> JSONResponse:
        """Caller sent bad thesis input -- 400 with a static safe message."""
        logger.info(
            "[%s] ThesisInputError: %s",
            getattr(request.state, "request_id", "-"), exc,
        )
        return JSONResponse(
            status_code=400,
            content={"detail": "Invalid thesis input."},
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Return 422 without reflecting submitted values or validation data."""
        logger.info(
            "[%s] RequestValidationError on %s %s (%d issue(s))",
            getattr(request.state, "request_id", "-"),
            request.method,
            request.url.path,
            len(exc.errors()),
        )
        return JSONResponse(
            status_code=422,
            content={"detail": "Request validation failed."},
        )

    @app.exception_handler(LLMConfigurationError)
    async def llm_config_handler(
        request: Request, exc: LLMConfigurationError
    ) -> JSONResponse:
        """LLM chain is misconfigured -- 503 with a safe, generic message.

        We deliberately do NOT forward ``str(exc)`` to the client because it
        may contain provider names, model IDs, or config hints that we don't
        want to expose.  The real message goes to the server log only.
        """
        logger.error(
            "[%s] LLMConfigurationError: %s",
            getattr(request.state, "request_id", "-"), exc,
        )
        return JSONResponse(
            status_code=503,
            content={"detail": "LLM service is not configured correctly. Check server logs."},
        )

    @app.exception_handler(LLMAllProvidersFailed)
    async def llm_all_failed_handler(
        request: Request, exc: LLMAllProvidersFailed
    ) -> JSONResponse:
        """Every provider in the chain failed -- 503, sanitized message only."""
        logger.error(
            "[%s] LLMAllProvidersFailed: %s",
            getattr(request.state, "request_id", "-"), exc,
        )
        return JSONResponse(
            status_code=503,
            content={"detail": "LLM service is temporarily unavailable. Try again later."},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        """Catch-all for anything we didn't anticipate -- 500, never leaks internals.

        The real traceback is logged at ERROR level; the client receives only
        a fixed string.  This prevents stack traces, DSNs, file paths, or any
        other internal detail from appearing in API responses.
        """
        request_id = getattr(request.state, "request_id", None)
        if request_id is None:
            # Defensive fallback: this handler sits outside user middleware,
            # so ensure an ID even if an earlier middleware failed.
            request_id = uuid.uuid4().hex[:8]
            request.state.request_id = request_id

        logger.exception(
            "[%s] Unhandled exception on %s %s",
            request_id,
            request.method,
            request.url.path,
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "An internal server error occurred."},
            headers={"X-Request-ID": request_id},
        )

    # ------------------------------------------------------------------
    # 6. Router mounting
    # ------------------------------------------------------------------
    # All routes live under /api/v1 so the API version is explicit in every
    # URL.  Future routers (thesis, companies, research, reports) are added
    # here in Stage 2 -- one line each.
    API_PREFIX = "/api/v1"

    app.include_router(health_router, prefix=API_PREFIX)
    app.include_router(companies_router, prefix=API_PREFIX)
    app.include_router(anomalies_router, prefix=API_PREFIX)
    app.include_router(thesis_router, prefix=API_PREFIX)
    # Later M9 stages:
    # app.include_router(thesis_router,    prefix=API_PREFIX)
    # app.include_router(research_router,  prefix=API_PREFIX)
    # app.include_router(reports_router,   prefix=API_PREFIX)

    return app


# ---------------------------------------------------------------------------
# Module-level app instance (used by Uvicorn: ``uvicorn app.main:app``)
# ---------------------------------------------------------------------------
# We create ONE instance here so ``uvicorn backend.app.main:app`` and
# ``uvicorn app.main:app`` both work out of the box.  Tests call
# ``create_app()`` directly so they always get a fresh, isolated instance.
app = create_app()
