"""SQLite-backed Reports and Watchlist API tests with fake research output."""

import json
from dataclasses import asdict
from datetime import datetime, timezone

import pytest
from fastapi import status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.database.models import Company, ResearchReport, WatchlistEntry
from app.database.postgres import get_session
from app.graph.agent import ResearchResult
from app.services.report_service import ReportPersistenceError
from app.services.report_service import ReportService


def _session_override(factory):
    def override():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return override


class FakeResearchAgent:
    def __init__(self, error=None):
        self.error = error
        self.results = []

    def run(self, question, ticker, *, thread_id):
        if self.error is not None:
            raise self.error
        result = ResearchResult(
            question=question,
            ticker=ticker,
            company={"cik": 789019, "ticker": "MSFT", "name": "Microsoft Corp"},
            final_answer="Revenue grew based on the reported filing.",
            citations=["0000789019-26-000001"],
            limitations=["The comparison covers one filing."],
            trace=["resolve: Microsoft Corp", "finalize"],
            validation={"ok": True, "unsupported_citations": []},
            iterations=2,
            revises=0,
            tool_calls=[{"tool": "search_filing_sections", "args": {"ticker": ticker}}],
        )
        self.results.append(result)
        return result


class FailReportService:
    def __init__(self):
        self.calls = 0

    def save(self, result, *, thread_id):
        self.calls += 1
        raise ReportPersistenceError()


@pytest.fixture
def reports_watchlist_setup(api_session_factory):
    with api_session_factory() as session:
        session.add_all(
            [
                Company(cik=789019, ticker="MSFT", name="Microsoft Corp"),
                Company(cik=320193, ticker="AAPL", name="Apple Inc."),
            ]
        )
        session.commit()
    agent = FakeResearchAgent()
    components = {
        "session_factory": api_session_factory,
        "research_agent": agent,
    }
    return components, agent


def test_save_true_persists_exact_research_result_and_returns_saved_id(
    client_factory, reports_watchlist_setup, api_session_factory
):
    components, agent = reports_watchlist_setup
    with client_factory(
        components=components,
        dependency_overrides={get_session: _session_override(api_session_factory)},
    ) as client:
        response = client.post(
            "/api/v1/research",
            json={
                "ticker": "MSFT",
                "question": "What supports the conclusion?",
                "thread_id": "thread-report-1",
            },
        )

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["save_requested"] is True
    assert isinstance(body["report_id"], int)
    with api_session_factory() as session:
        report = session.get(ResearchReport, body["report_id"])
        assert report is not None
        assert report.cik == 789019
        assert report.ticker == "MSFT"
        assert report.question == "What supports the conclusion?"
        assert report.thread_id == "thread-report-1"
        assert json.loads(report.result_json) == asdict(agent.results[0])


def test_save_false_creates_no_report_or_report_service_call(
    client_factory, reports_watchlist_setup, api_session_factory
):
    components, _ = reports_watchlist_setup
    failing_service = FailReportService()
    components["report_service"] = failing_service
    with client_factory(
        components=components,
        dependency_overrides={get_session: _session_override(api_session_factory)},
    ) as client:
        response = client.post(
            "/api/v1/research",
            json={"ticker": "MSFT", "question": "Do not persist", "save": False},
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["save_requested"] is False
    assert response.json()["report_id"] is None
    assert failing_service.calls == 0
    with api_session_factory() as session:
        assert session.query(ResearchReport).count() == 0


def test_research_failure_does_not_create_report(
    client_factory, reports_watchlist_setup, api_session_factory
):
    components, agent = reports_watchlist_setup
    agent.error = RuntimeError("private provider detail")
    with client_factory(
        components=components,
        dependency_overrides={get_session: _session_override(api_session_factory)},
    ) as client:
        response = client.post(
            "/api/v1/research", json={"ticker": "MSFT", "question": "Fail"}
        )

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "private provider detail" not in response.text
    with api_session_factory() as session:
        assert session.query(ResearchReport).count() == 0


def test_report_persistence_failure_does_not_claim_saved_success(
    client_factory, reports_watchlist_setup, api_session_factory
):
    components, _ = reports_watchlist_setup
    failing_service = FailReportService()
    components["report_service"] = failing_service
    with client_factory(
        components=components,
        dependency_overrides={get_session: _session_override(api_session_factory)},
    ) as client:
        response = client.post(
            "/api/v1/research", json={"ticker": "MSFT", "question": "Persist"}
        )

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json() == {
        "detail": "Research completed, but report persistence failed."
    }
    assert "report_id" not in response.json()
    assert failing_service.calls == 1
    with api_session_factory() as session:
        assert session.query(ResearchReport).count() == 0


def test_report_list_pagination_detail_and_missing_behavior(
    client_factory, reports_watchlist_setup, api_session_factory
):
    components, agent = reports_watchlist_setup
    with client_factory(
        components=components,
        dependency_overrides={get_session: _session_override(api_session_factory)},
    ) as client:
        empty = client.get("/api/v1/reports")
        created = [
            client.post(
                "/api/v1/research",
                json={"ticker": "MSFT", "question": f"Question {index}"},
            )
            for index in range(3)
        ]
        first = client.get("/api/v1/reports?limit=2&offset=0")
        second = client.get("/api/v1/reports?limit=2&offset=2")
        detail = client.get(f"/api/v1/reports/{created[0].json()['report_id']}")
        missing = client.get("/api/v1/reports/999999")

    assert all(response.status_code == status.HTTP_200_OK for response in created)
    assert empty.status_code == status.HTTP_200_OK
    assert empty.json()["items"] == []
    assert empty.json()["total"] == 0
    assert first.status_code == second.status_code == detail.status_code == 200
    assert first.json()["total"] == 3
    assert len(first.json()["items"]) == 2
    assert len(second.json()["items"]) == 1
    assert first.json()["items"][0]["id"] != first.json()["items"][1]["id"]
    assert detail.json()["result"]["final_answer"] == (
        "Revenue grew based on the reported filing."
    )
    assert detail.json()["result"] == asdict(agent.results[0])
    assert detail.json()["thread_id"] == created[0].json()["thread_id"]
    assert missing.status_code == status.HTTP_404_NOT_FOUND
    assert missing.json() == {"detail": "Report not found."}


def test_report_list_uses_id_as_tiebreak_for_equal_timestamps(
    client_factory, reports_watchlist_setup, api_session_factory
):
    components, _ = reports_watchlist_setup
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with api_session_factory() as session:
        rows = [
            ResearchReport(
                cik=789019,
                ticker="MSFT",
                question=f"Question {index}",
                thread_id=f"thread-{index}",
                result_json="{}",
                created_at=timestamp,
            )
            for index in range(2)
        ]
        session.add_all(rows)
        session.commit()
        expected_ids = [row.id for row in reversed(rows)]

    with client_factory(
        components=components,
        dependency_overrides={get_session: _session_override(api_session_factory)},
    ) as client:
        response = client.get("/api/v1/reports")

    assert response.status_code == status.HTTP_200_OK
    assert [row["id"] for row in response.json()["items"]] == expected_ids


def test_watchlist_create_list_delete_and_duplicate_conflict(
    client_factory, reports_watchlist_setup, api_session_factory
):
    components, _ = reports_watchlist_setup
    with client_factory(
        components=components,
        dependency_overrides={get_session: _session_override(api_session_factory)},
    ) as client:
        created = client.post("/api/v1/watchlist", json={"ticker": "msft"})
        duplicate = client.post("/api/v1/watchlist", json={"ticker": "MSFT"})
        second = client.post("/api/v1/watchlist", json={"ticker": "AAPL"})
        page = client.get("/api/v1/watchlist?limit=1&offset=0")
        deleted = client.delete("/api/v1/watchlist/MSFT")
        after_delete = client.get("/api/v1/watchlist")
        missing = client.delete("/api/v1/watchlist/MSFT")
        client.delete("/api/v1/watchlist/AAPL")
        empty = client.get("/api/v1/watchlist")

    assert created.status_code == status.HTTP_201_CREATED
    assert created.json()["ticker"] == "MSFT"
    assert duplicate.status_code == status.HTTP_409_CONFLICT
    assert second.status_code == status.HTTP_201_CREATED
    assert page.status_code == status.HTTP_200_OK
    assert page.json()["total"] == 2
    assert len(page.json()["items"]) == 1
    assert deleted.status_code == status.HTTP_204_NO_CONTENT
    assert after_delete.status_code == status.HTTP_200_OK
    assert after_delete.json()["total"] == 1
    assert missing.status_code == status.HTTP_404_NOT_FOUND
    assert empty.status_code == status.HTTP_200_OK
    assert empty.json()["items"] == []
    assert empty.json()["total"] == 0


@pytest.mark.parametrize("ticker", ["UNKNOWN", "BAD!"])
def test_watchlist_rejects_unsupported_or_malformed_ticker(
    client_factory, reports_watchlist_setup, api_session_factory, ticker
):
    components, _ = reports_watchlist_setup
    with client_factory(
        components=components,
        dependency_overrides={get_session: _session_override(api_session_factory)},
    ) as client:
        response = client.post("/api/v1/watchlist", json={"ticker": ticker})

    expected = (
        status.HTTP_422_UNPROCESSABLE_ENTITY
        if ticker == "BAD!"
        else status.HTTP_404_NOT_FOUND
    )
    assert response.status_code == expected


def test_watchlist_primary_key_prevents_duplicate_rows(
    reports_watchlist_setup, api_session_factory
):
    with api_session_factory() as session:
        session.add(WatchlistEntry(cik=789019))
        session.commit()
        session.add(WatchlistEntry(cik=789019))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_watchlist_list_uses_cik_tiebreak_for_equal_timestamps(
    client_factory, reports_watchlist_setup, api_session_factory
):
    components, _ = reports_watchlist_setup
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with api_session_factory() as session:
        session.add_all(
            [
                WatchlistEntry(cik=789019, created_at=timestamp),
                WatchlistEntry(cik=320193, created_at=timestamp),
            ]
        )
        session.commit()

    with client_factory(
        components=components,
        dependency_overrides={get_session: _session_override(api_session_factory)},
    ) as client:
        response = client.get("/api/v1/watchlist")

    assert response.status_code == status.HTTP_200_OK
    assert [row["cik"] for row in response.json()["items"]] == [320193, 789019]


def test_report_service_rolls_back_and_closes_after_commit_failure(
    api_session_factory,
):
    tracking = {}

    class CommitFailSession(Session):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            tracking["session"] = self
            tracking["rolled_back"] = False
            tracking["closed"] = False

        def commit(self):
            raise RuntimeError("simulated commit failure")

        def rollback(self):
            tracking["rolled_back"] = True
            return super().rollback()

        def close(self):
            tracking["closed"] = True
            return super().close()

    with api_session_factory() as session:
        session.add(Company(cik=789019, ticker="MSFT", name="Microsoft Corp"))
        session.commit()
        bind = session.get_bind()
    factory = sessionmaker(bind=bind, class_=CommitFailSession, expire_on_commit=False)
    result = FakeResearchAgent().run("Question", "MSFT", thread_id="thread-1")

    with pytest.raises(ReportPersistenceError):
        ReportService(factory).save(result, thread_id="thread-1")

    assert tracking["rolled_back"] is True
    assert tracking["closed"] is True
    with api_session_factory() as session:
        assert session.query(ResearchReport).count() == 0
