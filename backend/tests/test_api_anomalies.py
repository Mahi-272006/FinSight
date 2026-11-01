"""Anomalies API tests: stored signals, filters, paging, and provenance."""

import pytest
from fastapi import status

from app.database.models import Company, InvestigationSignal as SignalRow
from app.database.postgres import get_session
from app.database.repositories import SignalRepository
from app.services.anomaly_detection import InvestigationSignal


@pytest.fixture
def anomalies_client(client_factory, api_session_factory):
    def override_get_session():
        session = api_session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    with client_factory(
        dependency_overrides={get_session: override_get_session}
    ) as client:
        yield client


def _add_company_and_signals(factory, signals=True):
    with factory() as session:
        session.add(
            Company(
                cik=789019,
                ticker="MSFT",
                name="Microsoft Corp",
                fiscal_year_end_month=6,
            )
        )
        session.flush()
        if signals:
            SignalRepository(session).upsert_signals(
                [
                    InvestigationSignal(
                        cik=789019,
                        ticker="MSFT",
                        signal_type="earnings_cashflow_divergence",
                        severity="high",
                        fiscal_year=2024,
                        fiscal_period="FY",
                        period_type="annual",
                        headline="Net income growth exceeded cash flow growth.",
                        explanation="Stored detector explanation.",
                        evidence={
                            "net_income_growth": 0.65,
                            "operating_cash_flow_growth": 0.10,
                            "threshold_high": 0.50,
                            "formula": "net_income_growth - operating_cash_flow_growth",
                        },
                        source_accessions=["0000789019-24-000001"],
                    ),
                    InvestigationSignal(
                        cik=789019,
                        ticker="MSFT",
                        signal_type="growth_deceleration",
                        severity="elevated",
                        fiscal_year=2023,
                        fiscal_period="FY",
                        period_type="annual",
                        headline="Revenue growth decelerated.",
                        explanation="Stored detector explanation.",
                        evidence={"current_growth": 0.12, "prior_growth": 0.30},
                        source_accessions=["0000789019-23-000001"],
                    ),
                ]
            )
        session.commit()


def test_empty_signal_list_returns_empty_page(anomalies_client, api_session_factory):
    _add_company_and_signals(api_session_factory, signals=False)

    response = anomalies_client.get("/api/v1/anomalies/MSFT")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"items": [], "total": 0, "offset": 0, "limit": 50}


def test_signal_response_preserves_evidence_and_provenance(
    anomalies_client, api_session_factory
):
    _add_company_and_signals(api_session_factory)

    response = anomalies_client.get("/api/v1/anomalies/msft")

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["total"] == 2
    signal = body["items"][0]
    assert signal["signal_type"] == "earnings_cashflow_divergence"
    assert signal["severity"] == "high"
    assert signal["fiscal_year"] == 2024
    assert signal["fiscal_period"] == "FY"
    assert signal["period_type"] == "annual"
    assert signal["headline"] == "Net income growth exceeded cash flow growth."
    assert signal["explanation"] == "Stored detector explanation."
    assert signal["evidence"]["net_income_growth"] == 0.65
    assert signal["evidence"]["threshold_high"] == 0.50
    assert signal["source_accessions"] == ["0000789019-24-000001"]
    assert signal["detected_at"]


def test_signal_type_filter_and_pagination_use_filtered_total(
    anomalies_client, api_session_factory
):
    _add_company_and_signals(api_session_factory)

    response = anomalies_client.get(
        "/api/v1/anomalies/MSFT?signal_type=growth_deceleration&limit=1&offset=0"
    )

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["total"] == 1
    assert body["limit"] == 1
    assert body["offset"] == 0
    assert len(body["items"]) == 1
    assert body["items"][0]["signal_type"] == "growth_deceleration"


def test_signal_pagination_advances_in_stable_period_order(
    anomalies_client, api_session_factory
):
    _add_company_and_signals(api_session_factory)

    response = anomalies_client.get("/api/v1/anomalies/MSFT?limit=1&offset=1")

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["total"] == 2
    assert body["offset"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["fiscal_year"] == 2023
    assert body["items"][0]["signal_type"] == "growth_deceleration"


def test_empty_stored_evidence_and_accessions_serialize_safely(
    anomalies_client, api_session_factory
):
    _add_company_and_signals(api_session_factory)
    with api_session_factory() as session:
        row = (
            session.query(SignalRow)
            .filter(SignalRow.signal_type == "earnings_cashflow_divergence")
            .one()
        )
        row.evidence_json = ""
        row.source_accessions_json = ""
        session.commit()

    response = anomalies_client.get("/api/v1/anomalies/MSFT")

    assert response.status_code == status.HTTP_200_OK
    signal = response.json()["items"][0]
    assert signal["evidence"] == {}
    assert signal["source_accessions"] == []


def test_unfiltered_response_preserves_other_persisted_signal_types(
    anomalies_client, api_session_factory
):
    _add_company_and_signals(api_session_factory)
    with api_session_factory() as session:
        row = (
            session.query(SignalRow)
            .filter(SignalRow.signal_type == "growth_deceleration")
            .one()
        )
        row.signal_type = "future_signal_type"
        session.commit()

    response = anomalies_client.get("/api/v1/anomalies/MSFT")

    assert response.status_code == status.HTTP_200_OK
    assert "future_signal_type" in {
        signal["signal_type"] for signal in response.json()["items"]
    }


def test_missing_company_returns_404(anomalies_client):
    response = anomalies_client.get("/api/v1/anomalies/ZZZZ")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "Company not found."}


def test_invalid_ticker_returns_422(anomalies_client):
    response = anomalies_client.get("/api/v1/anomalies/BAD!")

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_invalid_signal_type_returns_422(anomalies_client, api_session_factory):
    _add_company_and_signals(api_session_factory)

    response = anomalies_client.get(
        "/api/v1/anomalies/MSFT?signal_type=possible_fraud"
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_supported_signal_type_without_data_returns_404(
    anomalies_client, api_session_factory
):
    _add_company_and_signals(api_session_factory)

    response = anomalies_client.get(
        "/api/v1/anomalies/MSFT?signal_type=margin_compression_streak"
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1"])
def test_invalid_pagination_returns_422(anomalies_client, query):
    response = anomalies_client.get(f"/api/v1/anomalies/MSFT?{query}")

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
