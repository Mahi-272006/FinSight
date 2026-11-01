"""Health-check router.

Responsibility: expose GET /health so load-balancers, the Vite dev proxy,
and integration tests have a lightweight way to confirm the API process is
alive and correctly deployed.

Design decisions:
- Separate router, not a route on main.py directly, so:
    (a) it follows the same mounting pattern as every other router, and
    (b) tests can hit it without importing the full application.
- Synchronous handler (def, not async def) per the M9 architecture decision.
    FastAPI runs sync handlers in a thread-pool so they never block the event
    loop.  Using sync consistently also means we can freely call blocking
    service code in future handlers without remembering to switch to async.
- No DB ping, no service call.  A liveness check must succeed even when
    downstream services are degraded; if we need readiness (all deps up) that
    is a separate /readiness endpoint, planned for a later stage.
- Returns APP_VERSION so a client can verify which build is deployed.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config import APP_VERSION, get_settings

router = APIRouter()


@router.get(
    "/health",
    summary="Liveness check",
    tags=["ops"],
    # No authentication required — load-balancers and CI pipelines call this
    # without credentials.
)
def health() -> JSONResponse:
    """Return 200 + a small status payload.

    Fields:
        status      -- always "ok" when this handler runs (non-200 means the
                       process is unreachable or crashing before routing).
        version     -- APP_VERSION constant from config.py; lets clients
                       confirm which build they are talking to.
        environment -- app_env setting (e.g. "development", "production");
                       useful to catch a mis-aimed proxy.
    """
    settings = get_settings()
    return JSONResponse(
        content={
            "status": "ok",
            "version": APP_VERSION,
            "environment": settings.app_env,
        }
    )

