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
    3. Build research services for the production module-level app.
    4. Log that the app is ready.
    Shutdown closes provider clients and disposes the database pool created for
    the production research services.

Heavy services (ChromaDB, sentence-transformers, LLM providers) are not
initialised at import time. The documented module-level Uvicorn app builds
them in its lifespan; test-created apps remain dependency-injected by default.
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
from app.api.research import router as research_router
from app.api.reports import router as reports_router
from app.api.watchlist import router as watchlist_router
from app.config import APP_VERSION, get_settings
from app.services.llm_providers import (
    LLMAllProvidersFailed,
    LLMConfigurationError,
    LLMProviderError,
    LLMRequestTooLargeError,
)
from app.services.thesis_service import ThesisInputError, ThesisNotFoundError
from app.services.report_service import ReportPersistenceError
from app.utils.logging import configure_logging, get_logger

logger = get_logger(__name__)

_STARTUP_DIAGNOSTICS = {
    "LLM provider manager": (
        "Check LLM_PROVIDER_CHAIN and configure credentials for a listed provider."
    ),
    "ChromaDB vector store": (
        "Check the ChromaDB installation and CHROMA_PERSIST_DIRECTORY access."
    ),
    "sentence-transformer embedder": (
        "Check sentence-transformers installation and local model availability."
    ),
    "database session factory": "Check DATABASE_URL configuration.",
    "research agent graph": (
        "Check research tool configuration and tool-calling provider support."
    ),
}

# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def _build_production_research_components(settings) -> dict[str, Any]:
    """Construct production research services without publishing partial state."""
    from app.services.embeddings import SentenceTransformerEmbedder
    from app.services.llm_manager import build_manager
    from app.services.vector_store import VectorStore
    from app.database.postgres import get_session_factory
    from app.graph.agent import ResearchAgent

    manager = None
    database_initialized = False
    initializing = "LLM provider manager"
    try:
        # Validate provider configuration before loading local models or opening
        # persistent stores; this avoids expensive partial startup on bad config.
        manager = build_manager(settings)
        initializing = "ChromaDB vector store"
        store = VectorStore(persist_dir=settings.chroma_persist_directory)
        initializing = "sentence-transformer embedder"
        embedder = SentenceTransformerEmbedder()
        initializing = "database session factory"
        session_factory = get_session_factory()
        database_initialized = True
        initializing = "research agent graph"
        agent = ResearchAgent(
            session_factory=session_factory,
            store=store,
            embedder=embedder,
            manager=manager,
            settings=settings,
        )
    except Exception as exc:  # noqa: BLE001 -- startup errors must not leak secrets
        if manager is not None:
            _close_manager(manager)
        if database_initialized:
            _reset_database_engine()
        diagnostic = _STARTUP_DIAGNOSTICS.get(
            initializing, "Check backend startup configuration."
        )
        if isinstance(exc, LLMConfigurationError):
            logger.error(
                "Research startup configuration failed at stage '%s' (%s). %s "
                "Configuration values are omitted.",
                initializing,
                type(exc).__name__,
                diagnostic,
            )
            raise RuntimeError(
                "Research services could not start: LLM or tool-calling provider "
                "configuration is missing or invalid. Check backend configuration "
                "and startup logs."
            ) from None
        logger.error(
            "Research startup failed at stage '%s' (%s). %s",
            initializing,
            type(exc).__name__,
            diagnostic,
        )
        raise RuntimeError(
            f"Research services could not start while initializing {initializing}. "
            "Check backend configuration and startup logs."
        ) from None

    return {
        "store": store,
        "embedder": embedder,
        "manager": manager,
        "session_factory": session_factory,
        "research_agent": agent,
    }


def _close_manager(manager: Any) -> None:
    """Close provider clients without allowing teardown errors to mask startup."""
    try:
        manager.close()
    except Exception as exc:  # noqa: BLE001 -- do not log provider details
        logger.error("LLM manager cleanup failed (%s).", type(exc).__name__)


def _reset_database_engine() -> None:
    """Dispose the lazily created database pool without masking startup errors."""
    try:
        from app.database.postgres import reset_engine

        reset_engine()
    except Exception as exc:  # noqa: BLE001 -- never mask the original failure
        logger.error("Database engine cleanup failed (%s).", type(exc).__name__)


def create_app(
    components: dict[str, Any] | None = None,
    *,
    initialize_production_research: bool = False,
) -> FastAPI:
    """Build and return a configured FastAPI instance.

    Args:
        components: Optional mapping of pre-built service objects (DB session
            factory, LLM manager, …).  Stored on ``app.state.components`` so
            route handlers can retrieve them via ``request.app.state.components``
            without importing module-level globals. Pass ``None`` to start with
            an empty component mapping.
        initialize_production_research: Build the configured vector store,
            embedder, and LLM manager during lifespan startup. This is enabled
            for the module-level Uvicorn app and left off for injected test apps.

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

        owned_components: dict[str, Any] = {}
        try:
            if initialize_production_research:
                owned_components = _build_production_research_components(settings)
                app.state.components.update(owned_components)

            yield  # <-- app is now serving requests
        finally:
            # Shutdown ---------------------------------------------------
            if owned_components:
                manager = owned_components.get("manager")
                if manager is not None:
                    _close_manager(manager)
                for name, component in owned_components.items():
                    if app.state.components.get(name) is component:
                        app.state.components.pop(name, None)
                # Drop the agent (and its model/store references) before
                # disposing the process-wide SQLAlchemy pool, if it was used.
                if hasattr(app.state, "research_agent"):
                    del app.state.research_agent
                _reset_database_engine()
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
    app.state.components = components if components is not None else {}

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
        may contain provider names, model IDs, or configuration details. The
        exception text is also omitted from logs to avoid recording secrets.
        """
        logger.error(
            "[%s] LLMConfigurationError on %s %s",
            getattr(request.state, "request_id", "-"),
            request.method,
            request.url.path,
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
            "[%s] LLMAllProvidersFailed on %s %s",
            getattr(request.state, "request_id", "-"),
            request.method,
            request.url.path,
        )
        return JSONResponse(
            status_code=503,
            content={"detail": "LLM service is temporarily unavailable. Try again later."},
        )

    @app.exception_handler(LLMRequestTooLargeError)
    async def llm_request_too_large_handler(
        request: Request, exc: LLMRequestTooLargeError
    ) -> JSONResponse:
        logger.warning(
            "[%s] LLM request exceeded provider context limits on %s %s",
            getattr(request.state, "request_id", "-"),
            request.method,
            request.url.path,
        )
        return JSONResponse(
            status_code=413,
            content={"detail": "Research context exceeded a provider request limit. Narrow the question and try again."},
        )

    @app.exception_handler(LLMProviderError)
    async def llm_provider_error_handler(
        request: Request, exc: LLMProviderError
    ) -> JSONResponse:
        logger.error(
            "[%s] LLM provider request failed (%s) on %s %s",
            getattr(request.state, "request_id", "-"),
            type(exc).__name__,
            request.method,
            request.url.path,
        )
        return JSONResponse(
            status_code=503,
            content={"detail": "LLM service is temporarily unavailable. Try again later."},
        )

    @app.exception_handler(ReportPersistenceError)
    async def report_persistence_handler(
        request: Request, exc: ReportPersistenceError
    ) -> JSONResponse:
        """A failed report commit must never be presented as a saved result."""
        logger.error(
            "[%s] ReportPersistenceError on %s %s",
            getattr(request.state, "request_id", "-"),
            request.method,
            request.url.path,
        )
        return JSONResponse(
            status_code=503,
            content={"detail": "Research completed, but report persistence failed."},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        """Catch-all for anything we didn't anticipate -- 500, never leaks internals.

        Log safe request context and the exception type, without formatting
        exception text or a traceback that could contain secrets. The client
        receives only a fixed string.
        """
        request_id = getattr(request.state, "request_id", None)
        if request_id is None:
            # Defensive fallback: this handler sits outside user middleware,
            # so ensure an ID even if an earlier middleware failed.
            request_id = uuid.uuid4().hex[:8]
            request.state.request_id = request_id

        logger.error(
            "[%s] Unhandled %s on %s %s",
            request_id,
            type(exc).__name__,
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
    app.include_router(research_router, prefix=API_PREFIX)
    app.include_router(reports_router, prefix=API_PREFIX)
    app.include_router(watchlist_router, prefix=API_PREFIX)
    # Later M9 stages:
    # app.include_router(thesis_router,    prefix=API_PREFIX)

    return app


# ---------------------------------------------------------------------------
# Module-level app instance (used by Uvicorn: ``uvicorn app.main:app``)
# ---------------------------------------------------------------------------
# We create ONE instance here so ``uvicorn backend.app.main:app`` and
# ``uvicorn app.main:app`` both work out of the box.  Tests call
# ``create_app()`` directly so they always get a fresh, isolated instance.
app = create_app(initialize_production_research=True)
