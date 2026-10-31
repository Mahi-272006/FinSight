"""Database engine and session management.

Design decisions:
- Session factory + explicit context manager, NOT global sessions. A session
  is NOT thread-safe and must be short-lived: create -> use -> commit/rollback
  -> close. The context manager pattern makes that automatic and prevents the
  classic "session leak" that degrades connection pools.
- `get_session()` is shaped for FastAPI's dependency injection (M9): the API
  layer will use `Depends(get_session)`, keeping route handlers free of
  connection plumbing.
- create_all() is for dev/tests only; production schema is managed by Alembic
  migrations so schema changes are versioned and reversible.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.database.models import Base
from app.utils.logging import get_logger

logger = get_logger(__name__)

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    """Return the process-wide engine (created lazily, reused after).

    Why cache: creating an engine opens a connection pool; making one per
    request would exhaust PostgreSQL's max_connections under any load.
    """
    global _engine, _session_factory
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,  # verify connections are alive before use
            # pool_pre_ping catches the classic "server closed the connection
            # overnight" error that otherwise surfaces as a 500 in the API.
        )
        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
        logger.info("Database engine created")
    return _engine


@contextmanager
def get_session() -> Iterator[Session]:
    """Yield a Session; commit on success, rollback on error, always close.

    Usage:
        with get_session() as session:
            session.add(obj)
            # committed automatically here; rolled back if an exception raised
    """
    if _session_factory is None:
        get_engine()
    assert _session_factory is not None
    session = _session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_tables() -> None:
    """Create all tables from model metadata (dev/test convenience).

    Not a substitute for Alembic: migrations become the source of truth once
    the schema evolves beyond its initial version.
    """
    Base.metadata.create_all(get_engine())
    logger.info("Database tables created (if missing)")


def reset_engine() -> None:
    """Drop the cached engine/session factory (used by tests to rebind)."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
