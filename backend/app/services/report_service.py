"""Transaction-owning service for persisted ResearchResult snapshots."""

import json
from dataclasses import asdict
from sqlalchemy.orm import Session, sessionmaker

from app.database.models import ResearchReport
from app.database.repositories import ReportRepository
from app.graph.agent import ResearchResult


class ReportPersistenceError(RuntimeError):
    """A research result could not be committed as a durable report."""

    def __init__(self):
        super().__init__("Research report persistence failed.")


class ReportService:
    """Persist complete results in a transaction separate from research."""

    def __init__(self, session_factory: sessionmaker[Session]):
        self._session_factory = session_factory

    def save(self, result: ResearchResult, *, thread_id: str) -> int:
        """Store a lossless JSON snapshot and return its committed database ID."""
        session = None
        try:
            session = self._session_factory()
            company = result.company
            if not company or company.get("cik") is None:
                raise ValueError("Research result is missing its company CIK.")
            content = json.dumps(
                asdict(result),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            report = ReportRepository(session).add(
                ResearchReport(
                    cik=int(company["cik"]),
                    ticker=result.ticker.upper(),
                    question=result.question,
                    thread_id=thread_id,
                    result_json=content,
                )
            )
            session.commit()
            return report.id
        except Exception as exc:
            if session is not None:
                session.rollback()
            raise ReportPersistenceError() from exc
        finally:
            if session is not None:
                session.close()
