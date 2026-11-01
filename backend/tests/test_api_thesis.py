"""Thesis API tests use the transaction-owning service with isolated SQLite."""

import pytest
from fastapi import status
from sqlalchemy.orm import Session, sessionmaker

from app.database.models import Company, FinancialMetric, Thesis, ThesisAssessment
from app.services.llm_providers import (
    LLMAllProvidersFailed,
    LLMConfigurationError,
)
from app.services.thesis_service import ThesisService

THESIS_BODY = {
    "ticker": "MSFT",
    "statement": "Microsoft maintains double-digit revenue growth.",
    "horizon": "FY2026",
    "conditions": ["revenue_growth_yoy >= 0.10"],
    "fiscal_year": 2026,
}


@pytest.fixture
def thesis_api(client_factory, api_session_factory):
    service = ThesisService(api_session_factory, manager=None)
    with client_factory(components={"thesis_service": service}) as client:
        yield client, service


def _seed_company_and_metrics(factory, revenue_growth=0.1779):
    with factory() as session:
        session.add(Company(cik=789019, ticker="MSFT", name="Microsoft Corp"))
        session.add(
            FinancialMetric(
                cik=789019,
                metric="revenue_growth_yoy",
                value=revenue_growth,
                unit="ratio",
                fiscal_year=2026,
                fiscal_period="FY",
                period_type="annual",
                formula="(current - prior) / abs(prior)",
                inputs_json='{"current": 120.0, "prior": 100.0}',
                source_accessions_json='["0000789019-26-000001"]',
            )
        )
        session.commit()


def _tracking_factory(base_factory):
    with base_factory() as session:
        bind = session.get_bind()

    opened_sessions = []

    class TrackingSession(Session):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.was_closed = False
            self.commit_count = 0
            opened_sessions.append(self)

        def commit(self):
            self.commit_count += 1
            return super().commit()

        def close(self):
            self.was_closed = True
            return super().close()

    factory = sessionmaker(
        bind=bind,
        class_=TrackingSession,
        expire_on_commit=False,
    )
    return factory, opened_sessions


def test_create_thesis_returns_condition_snapshot_and_commits(
    thesis_api, api_session_factory
):
    client, service = thesis_api
    _seed_company_and_metrics(api_session_factory)

    response = client.post("/api/v1/theses", json=THESIS_BODY)

    assert response.status_code == status.HTTP_201_CREATED
    body = response.json()
    assert body["ticker"] == "MSFT"
    assert body["status"] == "Unassessed"
    assert len(body["conditions"]) == 1
    snapshot = body["conditions"][0]["creation_evidence"]
    assert snapshot["value"] == 0.1779
    assert snapshot["formula"] == "(current - prior) / abs(prior)"
    assert snapshot["accessions"] == ["0000789019-26-000001"]
    assert service._session_factory is api_session_factory

    # A new session sees the write, proving the service committed its own unit
    # of work rather than depending on FastAPI's request-session teardown.
    with api_session_factory() as session:
        assert session.query(Thesis).count() == 1
        assert session.query(ThesisAssessment).count() == 0


def test_successful_service_reads_and_writes_close_owned_sessions(
    client_factory, api_session_factory
):
    _seed_company_and_metrics(api_session_factory)
    tracking_factory, opened_sessions = _tracking_factory(api_session_factory)
    service = ThesisService(tracking_factory, manager=None)

    with client_factory(components={"thesis_service": service}) as client:
        response = client.post("/api/v1/theses", json=THESIS_BODY)

    assert response.status_code == status.HTTP_201_CREATED
    # Creation commits, detail retrieval is read-only, and both sessions close.
    assert len(opened_sessions) == 2
    assert [session.commit_count for session in opened_sessions] == [1, 0]
    assert all(session.was_closed for session in opened_sessions)


def test_create_rejects_invalid_condition_and_empty_condition_list(
    thesis_api, api_session_factory
):
    client, _ = thesis_api
    _seed_company_and_metrics(api_session_factory)

    invalid = client.post(
        "/api/v1/theses",
        json={**THESIS_BODY, "conditions": ["not a condition"]},
    )
    empty = client.post("/api/v1/theses", json={**THESIS_BODY, "conditions": []})

    assert invalid.status_code == status.HTTP_400_BAD_REQUEST
    assert invalid.json() == {"detail": "Invalid thesis input."}
    assert "not a condition" not in invalid.text
    assert empty.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    with api_session_factory() as session:
        assert session.query(Company).count() == 1
        assert session.query(Thesis).count() == 0


def test_schema_validation_errors_do_not_reflect_submitted_values(thesis_api):
    client, _ = thesis_api
    private_value = "sk-invalid-input-secret"

    response = client.post(
        "/api/v1/theses",
        json={**THESIS_BODY, "fiscal_period": private_value},
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert response.json() == {"detail": "Request validation failed."}
    assert private_value not in response.text


def test_failed_creation_rolls_back_and_closes_service_session(
    client_factory, api_session_factory, monkeypatch
):
    _seed_company_and_metrics(api_session_factory)
    tracking_factory, opened_sessions = _tracking_factory(api_session_factory)
    service = ThesisService(tracking_factory, manager=None)

    def fail_after_thesis_flush(*args, **kwargs):
        raise RuntimeError("private database failure detail")

    monkeypatch.setattr(service, "_lookup", fail_after_thesis_flush)
    with client_factory(components={"thesis_service": service}) as client:
        response = client.post("/api/v1/theses", json=THESIS_BODY)

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "private database failure detail" not in response.text
    assert len(opened_sessions) == 1
    assert opened_sessions[0].was_closed is True
    assert opened_sessions[0].commit_count == 0
    with api_session_factory() as session:
        assert session.query(Thesis).count() == 0


def test_invalid_ticker_and_unknown_company_are_rejected(thesis_api):
    client, _ = thesis_api
    invalid = client.post(
        "/api/v1/theses", json={**THESIS_BODY, "ticker": "BAD!"}
    )
    unknown = client.post(
        "/api/v1/theses", json={**THESIS_BODY, "ticker": "ZZZZ"}
    )

    assert invalid.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert unknown.status_code == status.HTTP_400_BAD_REQUEST


def test_thesis_detail_and_paginated_list(thesis_api, api_session_factory):
    client, _ = thesis_api
    _seed_company_and_metrics(api_session_factory)
    created = [client.post("/api/v1/theses", json=THESIS_BODY).json() for _ in range(3)]

    response = client.get("/api/v1/theses?limit=1&offset=1")
    detail = client.get(f"/api/v1/theses/{created[1]['id']}")

    assert response.status_code == status.HTTP_200_OK
    page = response.json()
    assert page["total"] == 3
    assert page["limit"] == 1
    assert page["offset"] == 1
    assert [item["id"] for item in page["items"]] == [created[1]["id"]]
    assert detail.status_code == status.HTTP_200_OK
    assert detail.json()["conditions"][0]["metric"] == "revenue_growth_yoy"
    assert detail.json()["assessments"] == []


def test_empty_thesis_list_and_invalid_pagination(thesis_api):
    client, _ = thesis_api
    empty = client.get("/api/v1/theses")
    invalid = client.get("/api/v1/theses?limit=0")

    assert empty.status_code == status.HTTP_200_OK
    assert empty.json() == {"items": [], "total": 0, "offset": 0, "limit": 50}
    assert invalid.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/theses/999",
        "/api/v1/theses/999/assess",
        "/api/v1/theses/999/assessments",
    ],
)
def test_missing_thesis_returns_404(thesis_api, path):
    client, _ = thesis_api
    response = client.post(path) if path.endswith("/assess") else client.get(path)

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_assess_returns_deterministic_status_and_history(
    thesis_api, api_session_factory
):
    client, _ = thesis_api
    _seed_company_and_metrics(api_session_factory)
    thesis = client.post("/api/v1/theses", json=THESIS_BODY).json()

    response = client.post(
        f"/api/v1/theses/{thesis['id']}/assess", json={"interpret": False}
    )

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["status"] == "Supported"
    assert body["evaluations"][0]["verdict"] == "met"
    assert body["explanation_source"] == "deterministic:condition-counts"
    detail = client.get(f"/api/v1/theses/{thesis['id']}").json()
    assert detail["status"] == "Supported"
    assert len(detail["assessments"]) == 1


def test_assessment_history_is_append_only_and_paginated(
    thesis_api, api_session_factory
):
    client, _ = thesis_api
    _seed_company_and_metrics(api_session_factory)
    thesis = client.post("/api/v1/theses", json=THESIS_BODY).json()
    thesis_id = thesis["id"]

    first = client.post(f"/api/v1/theses/{thesis_id}/assess", json={"interpret": False})
    with api_session_factory() as session:
        metric = (
            session.query(FinancialMetric)
            .filter(FinancialMetric.metric == "revenue_growth_yoy")
            .one()
        )
        metric.value = 0.05
        session.commit()
    second = client.post(f"/api/v1/theses/{thesis_id}/assess", json={"interpret": False})

    history = client.get(
        f"/api/v1/theses/{thesis_id}/assessments?limit=1&offset=1"
    )
    detail = client.get(f"/api/v1/theses/{thesis_id}").json()

    assert first.json()["status"] == "Supported"
    assert second.json()["status"] == "Contradicted"
    assert history.status_code == status.HTTP_200_OK
    page = history.json()
    assert page["total"] == 2
    assert page["offset"] == 1
    assert len(page["items"]) == 1
    assert page["items"][0]["status"] == "Contradicted"
    assert [assessment["status"] for assessment in detail["assessments"]] == [
        "Supported",
        "Contradicted",
    ]


class FailingManager:
    def __init__(self, error):
        self.error = error

    def generate(self, messages):
        raise self.error


@pytest.mark.parametrize(
    "error",
    [
        LLMConfigurationError("private api key configuration detail"),
        LLMAllProvidersFailed([("private-provider", "private timeout detail")]),
    ],
)
def test_llm_interpretation_failure_falls_back_to_deterministic_result(
    client_factory, api_session_factory, error
):
    _seed_company_and_metrics(api_session_factory)
    service = ThesisService(api_session_factory, manager=FailingManager(error))
    with client_factory(components={"thesis_service": service}) as client:
        thesis = client.post("/api/v1/theses", json=THESIS_BODY).json()
        response = client.post(f"/api/v1/theses/{thesis['id']}/assess")

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["status"] == "Supported"
    assert body["explanation_source"] == "deterministic:condition-counts"
    assert "private" not in response.text


class RaisingAssessmentService:
    def __init__(self, error):
        self.error = error

    def assess(self, thesis_id, interpret=True):
        raise self.error


@pytest.mark.parametrize(
    "error,leaked_text",
    [
        (LLMConfigurationError("private api key detail"), "private api key"),
        (
            LLMAllProvidersFailed([("private-provider", "private timeout detail")]),
            "private-provider",
        ),
    ],
)
def test_escaped_llm_failures_use_sanitized_503_handler(
    client_factory, error, leaked_text
):
    service = RaisingAssessmentService(error)
    with client_factory(components={"thesis_service": service}) as client:
        response = client.post("/api/v1/theses/1/assess")

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert leaked_text not in response.text
