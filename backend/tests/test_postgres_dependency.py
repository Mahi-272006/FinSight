"""Focused tests for FastAPI and service database session lifecycles."""

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import postgres
from app.database.models import Base, Company, Thesis, ThesisAssessment
from app.services.thesis_service import ThesisService


class TrackingSession(Session):
    """SQLAlchemy Session that records lifecycle calls for assertions."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.was_closed = False
        self.commit_count = 0
        self.rollback_count = 0

    def commit(self):
        self.commit_count += 1
        super().commit()

    def rollback(self):
        self.rollback_count += 1
        super().rollback()

    def close(self):
        self.was_closed = True
        super().close()


@pytest.fixture
def sqlite_session_factory(monkeypatch):
    """Bind the production session helpers to an isolated shared SQLite DB."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine, expire_on_commit=False, class_=TrackingSession
    )
    monkeypatch.setattr(postgres, "_engine", engine)
    monkeypatch.setattr(postgres, "_session_factory", factory)
    try:
        yield factory
    finally:
        engine.dispose()


def test_get_session_yields_session_and_commits_then_closes(sqlite_session_factory):
    dependency = postgres.get_session()
    session = next(dependency)

    assert isinstance(session, Session)
    session.add(Company(cik=789019, ticker="MSFT", name="Microsoft"))
    with pytest.raises(StopIteration):
        next(dependency)

    assert session.commit_count == 1
    assert session.rollback_count == 0
    assert session.was_closed
    with sqlite_session_factory() as verification:
        assert verification.get(Company, 789019) is not None


def test_get_session_rolls_back_and_closes_after_exception(sqlite_session_factory):
    dependency = postgres.get_session()
    session = next(dependency)
    session.add(Company(cik=789019, ticker="MSFT", name="Microsoft"))

    with pytest.raises(RuntimeError, match="request failed"):
        dependency.throw(RuntimeError("request failed"))

    assert session.commit_count == 0
    assert session.rollback_count == 1
    assert session.was_closed

    with sqlite_session_factory() as verification:
        assert verification.get(Company, 789019) is None


def test_session_scope_preserves_script_commit_and_close_behavior(
    sqlite_session_factory,
):
    with postgres.session_scope() as session:
        session.add(Company(cik=789019, ticker="MSFT", name="Microsoft"))

    assert session.commit_count == 1
    assert session.was_closed
    with sqlite_session_factory() as verification:
        assert verification.get(Company, 789019) is not None


def test_get_session_factory_lazily_creates_usable_factory(monkeypatch):
    engine = None
    monkeypatch.setattr(postgres, "_engine", None)
    monkeypatch.setattr(postgres, "_session_factory", None)
    monkeypatch.setattr(
        postgres,
        "get_settings",
        lambda: SimpleNamespace(database_url="sqlite://"),
    )
    try:
        factory = postgres.get_session_factory()
        assert isinstance(factory, sessionmaker)

        engine = postgres.get_engine()
        Base.metadata.create_all(engine)
        with factory() as session:
            session.add(Company(cik=789019, ticker="MSFT", name="Microsoft"))
            session.commit()
        with factory() as session:
            assert session.get(Company, 789019) is not None
    finally:
        if engine is not None:
            engine.dispose()


def test_thesis_service_keeps_own_transaction_with_session_factory(
    sqlite_session_factory,
):
    with sqlite_session_factory() as session:
        session.add(Company(cik=789019, ticker="MSFT", name="Microsoft"))
        session.commit()

    service = ThesisService(postgres.get_session_factory(), manager=None)
    thesis_id = service.create_thesis(
        "MSFT", "Microsoft grows", "FY2026", ["revenue>=1"], fiscal_year=2026
    )
    assessment = service.assess(thesis_id, interpret=False)

    with sqlite_session_factory() as verification:
        assert verification.get(Thesis, thesis_id) is not None
        row = verification.query(ThesisAssessment).filter_by(
            thesis_id=thesis_id
        ).one()
        assert row.id == assessment.assessment_id
