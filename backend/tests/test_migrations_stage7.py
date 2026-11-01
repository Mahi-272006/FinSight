"""Stage 7 migrations remain linear and reversible on portable SQLite."""

from datetime import datetime, timezone
import subprocess
import sys

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import BACKEND_ROOT
from app.database.models import Company


REPORTS_REVISION = "9b0e3f75f1a2"
WATCHLIST_REVISION = "e41f7c6a93d0"
PREVIOUS_REVISION = "7cee87c1bedf"


def _alembic_config() -> Config:
    return Config(str(BACKEND_ROOT / "alembic.ini"))


def _run_alembic(*arguments: str) -> None:
    """Run Alembic in a child process so fileConfig cannot reset pytest logs."""
    subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(BACKEND_ROOT / "alembic.ini"),
            *arguments,
        ],
        cwd=BACKEND_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_stage7_revisions_are_chained_from_latest_existing_revision():
    scripts = ScriptDirectory.from_config(_alembic_config())
    assert scripts.get_current_head() == WATCHLIST_REVISION
    assert scripts.get_revision(WATCHLIST_REVISION).down_revision == REPORTS_REVISION
    assert scripts.get_revision(REPORTS_REVISION).down_revision == PREVIOUS_REVISION


def test_stage7_migrations_upgrade_and_downgrade_on_sqlite(tmp_path, monkeypatch):
    database_path = tmp_path / "stage7-migrations.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
    _run_alembic("upgrade", PREVIOUS_REVISION)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with Session(engine) as session:
        session.add(
            Company(
                cik=789019,
                ticker="MSFT",
                name="Microsoft Corp",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        session.commit()
    engine.dispose()

    _run_alembic("upgrade", "head")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    inspector = inspect(engine)
    assert "research_reports" in inspector.get_table_names()
    assert "watchlist_entries" in inspector.get_table_names()
    assert "ix_reports_ticker_created" in {
        index["name"] for index in inspector.get_indexes("research_reports")
    }
    assert inspector.get_pk_constraint("watchlist_entries")["constrained_columns"] == [
        "cik"
    ]
    with Session(engine) as session:
        preserved_company = session.execute(
            select(Company).where(Company.cik == 789019)
        ).scalar_one()
        assert preserved_company.ticker == "MSFT"
    engine.dispose()

    _run_alembic("downgrade", PREVIOUS_REVISION)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    tables = set(inspect(engine).get_table_names())
    assert "research_reports" not in tables
    assert "watchlist_entries" not in tables
    engine.dispose()

    _run_alembic("upgrade", "head")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    tables = set(inspect(engine).get_table_names())
    assert {"research_reports", "watchlist_entries"} <= tables
    engine.dispose()
