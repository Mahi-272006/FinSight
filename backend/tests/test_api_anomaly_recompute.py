"""API coverage for real, transactional anomaly recomputation."""

from datetime import datetime, timezone
from types import SimpleNamespace

from app.database.models import Company, FinancialMetric
from app.database.repositories import SignalRepository


def _seed_inputs(factory):
    with factory() as session:
        session.add(Company(cik=789019, ticker="MSFT", name="Microsoft Corp"))
        session.flush()
        for metric, value in (
            ("earnings_cashflow_divergence", 0.35),
            ("net_income_growth_yoy", 0.40),
            ("operating_cash_flow_growth_yoy", 0.05),
        ):
            session.add(
                FinancialMetric(
                    cik=789019,
                    metric=metric,
                    value=value,
                    unit="ratio",
                    fiscal_year=2024,
                    fiscal_period="FY",
                    period_type="annual",
                    formula="test_formula",
                    inputs_json="{}",
                    source_accessions_json='["0000789019-24-000001"]',
                )
            )
        session.commit()


def test_recompute_endpoint_returns_and_persists_actual_signals(
    client_factory, api_session_factory
):
    _seed_inputs(api_session_factory)
    with client_factory(components={"session_factory": api_session_factory}) as client:
        response = client.post("/api/v1/anomalies/recompute", json={"ticker": "msft"})

    assert response.status_code == 200
    body = response.json()
    assert body["ticker"] == "MSFT"
    assert body["cik"] == 789019
    assert body["total"] == 1
    signal = body["items"][0]
    assert signal["signal_type"] == "earnings_cashflow_divergence"
    assert signal["evidence"]["formula"] == (
        "net_income_growth_yoy - operating_cash_flow_growth_yoy"
    )
    assert signal["source_accessions"] == ["0000789019-24-000001"]
    assert signal["detected_at"]
    with api_session_factory() as session:
        persisted, = SignalRepository(session).get_signals_for_company(789019)
    assert persisted.id == signal["id"]


def test_recompute_endpoint_maps_invalid_missing_and_insufficient_cases(
    client_factory, api_session_factory
):
    with client_factory(components={"session_factory": api_session_factory}) as client:
        invalid = client.post(
            "/api/v1/anomalies/recompute", json={"ticker": "TSLA"}
        )
        missing = client.post(
            "/api/v1/anomalies/recompute", json={"ticker": "MSFT"}
        )
    assert invalid.status_code == 422
    assert invalid.json() == {"detail": "Ticker is outside the supported company scope."}
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Company not found."}

    with api_session_factory() as session:
        session.add(Company(cik=789019, ticker="MSFT", name="Microsoft Corp"))
        session.add(
            FinancialMetric(
                cik=789019,
                metric="revenue_growth_yoy",
                value=0.2,
                unit="ratio",
                fiscal_year=2024,
                fiscal_period="FY",
                period_type="annual",
                formula="test_formula",
                inputs_json="{}",
                source_accessions_json="[]",
            )
        )
        session.commit()
    with client_factory(components={"session_factory": api_session_factory}) as client:
        insufficient = client.post(
            "/api/v1/anomalies/recompute", json={"ticker": "MSFT"}
        )
    assert insufficient.status_code == 422
    assert insufficient.json() == {
        "detail": "Insufficient annual financial data to recompute signals."
    }


def test_recompute_endpoint_sanitizes_pipeline_failures(
    client_factory, api_session_factory, monkeypatch
):
    _seed_inputs(api_session_factory)

    def fail(_series):
        raise RuntimeError("DSN secret provider token")

    monkeypatch.setattr("app.services.anomaly_service.detect_signals", fail)
    with client_factory(components={"session_factory": api_session_factory}) as client:
        response = client.post(
            "/api/v1/anomalies/recompute", json={"ticker": "MSFT"}
        )

    assert response.status_code == 500
    assert response.json() == {
        "detail": "Investigation signal recomputation failed."
    }
    assert "DSN" not in response.text
    assert "provider token" not in response.text


def test_recompute_endpoint_uses_injected_service(client_factory):
    signal = SimpleNamespace(
        id=4,
        cik=789019,
        ticker="MSFT",
        signal_type="margin_compression_streak",
        severity="elevated",
        fiscal_year=2025,
        fiscal_period="FY",
        period_type="annual",
        headline="Stored detector output.",
        explanation="A review prompt, not a verdict.",
        evidence_json='{"formula":"test","input":1}',
        source_accessions_json='["accession"]',
        detected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    class FakeService:
        def recompute(self, ticker):
            assert ticker == "NVDA"
            return 1045810, "NVDA", [signal]

    with client_factory(components={"anomaly_service": FakeService()}) as client:
        response = client.post(
            "/api/v1/anomalies/recompute", json={"ticker": "NVDA"}
        )
    assert response.status_code == 200
    assert response.json()["items"][0]["evidence"] == {"formula": "test", "input": 1}
    assert response.json()["items"][0]["source_accessions"] == ["accession"]


def test_recompute_request_validation_is_sanitized(
    client_factory, api_session_factory
):
    with client_factory(components={"session_factory": api_session_factory}) as client:
        response = client.post(
            "/api/v1/anomalies/recompute", json={"ticker": "BAD!"}
        )
    assert response.status_code == 422
    assert response.json() == {"detail": "Request validation failed."}
