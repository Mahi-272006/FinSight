"""Companies API tests: persisted records, validation, provenance, and paging."""

from datetime import datetime, timezone

import pytest
from fastapi import status

from app.database.models import Company, FinancialFact, FinancialMetric
from app.database.postgres import get_session


@pytest.fixture
def companies_client(client_factory, api_session_factory):
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


def _add_company(factory, cik=789019, ticker="MSFT", name="Microsoft Corp"):
    with factory() as session:
        company = Company(
            cik=cik,
            ticker=ticker,
            name=name,
            fiscal_year_end_month=6,
        )
        session.add(company)
        session.commit()
        return company.cik


def _add_metric(factory, *, cik=789019, year, value, metric="revenue_growth_yoy"):
    with factory() as session:
        session.add(
            FinancialMetric(
                cik=cik,
                metric=metric,
                value=value,
                unit="ratio",
                fiscal_year=year,
                fiscal_period="FY",
                period_type="annual",
                formula="(current - prior) / abs(prior)",
                inputs_json='{"current": 120.0, "prior": 100.0}',
                source_accessions_json='["0000789019-24-000001"]',
                calculated_at=datetime(year, 8, 1, tzinfo=timezone.utc),
            )
        )
        session.commit()


def _add_fact(
    factory,
    *,
    cik=789019,
    year=2024,
    accession="0000789019-24-000001",
    value=120.0,
    period_type="annual",
    is_restated=False,
    value_original=None,
):
    with factory() as session:
        session.add(
            FinancialFact(
                cik=cik,
                metric="revenue",
                value=value,
                unit="USD",
                period_type=period_type,
                fiscal_year=year,
                fiscal_period="FY",
                period_start=datetime(year - 1, 7, 1, tzinfo=timezone.utc),
                period_end=datetime(year, 6, 30, tzinfo=timezone.utc),
                source_concept="RevenueFromContractWithCustomerExcludingAssessedTax",
                form="10-K",
                accession_number=accession,
                filed_at=datetime(year, 8, 1, tzinfo=timezone.utc),
                source_url=f"https://www.sec.gov/Archives/{accession}",
                is_restated=is_restated,
                value_original=value_original,
            )
        )
        session.commit()


def test_list_companies_is_paginated_and_ordered(companies_client, api_session_factory):
    _add_company(api_session_factory, 1045810, "NVDA", "NVIDIA Corp")
    _add_company(api_session_factory, 320193, "AAPL", "Apple Inc")
    _add_company(api_session_factory)

    response = companies_client.get("/api/v1/companies?limit=2&offset=1")

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["total"] == 3
    assert body["offset"] == 1
    assert body["limit"] == 2
    assert [item["ticker"] for item in body["items"]] == ["MSFT", "NVDA"]


def test_list_companies_returns_empty_page(companies_client):
    response = companies_client.get("/api/v1/companies")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"items": [], "total": 0, "offset": 0, "limit": 50}


def test_get_company_is_case_insensitive_and_returns_details(
    companies_client, api_session_factory
):
    _add_company(api_session_factory)

    response = companies_client.get("/api/v1/companies/msft")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["ticker"] == "MSFT"
    assert response.json()["cik"] == 789019
    assert response.json()["fiscal_year_end_month"] == 6


def test_missing_company_returns_404(companies_client):
    response = companies_client.get("/api/v1/companies/ZZZZ")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "Company not found."}


def test_invalid_ticker_returns_422(companies_client):
    response = companies_client.get("/api/v1/companies/BAD!")

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_company_without_financial_rows_returns_empty_collections(
    companies_client, api_session_factory
):
    _add_company(api_session_factory)

    metrics = companies_client.get("/api/v1/companies/MSFT/metrics")
    facts = companies_client.get("/api/v1/companies/MSFT/facts")

    assert metrics.status_code == status.HTTP_200_OK
    assert metrics.json()["items"] == []
    assert metrics.json()["total"] == 0
    assert facts.status_code == status.HTTP_200_OK
    assert facts.json()["items"] == []
    assert facts.json()["total"] == 0


def test_metrics_return_formula_inputs_and_accessions(
    companies_client, api_session_factory
):
    _add_company(api_session_factory)
    _add_metric(api_session_factory, year=2024, value=0.2)

    response = companies_client.get("/api/v1/companies/MSFT/metrics")

    assert response.status_code == status.HTTP_200_OK
    item = response.json()["items"][0]
    assert item["metric"] == "revenue_growth_yoy"
    assert item["value"] == 0.2
    assert item["formula"] == "(current - prior) / abs(prior)"
    assert item["inputs"] == {"current": 120.0, "prior": 100.0}
    assert item["source_accessions"] == ["0000789019-24-000001"]


def test_metrics_pagination_and_supported_metric_filter(
    companies_client, api_session_factory
):
    _add_company(api_session_factory)
    for year in (2022, 2023, 2024):
        _add_metric(api_session_factory, year=year, value=year / 10000)

    response = companies_client.get(
        "/api/v1/companies/MSFT/metrics?metric=revenue_growth_yoy&limit=1&offset=1"
    )

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["total"] == 3
    assert body["offset"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["fiscal_year"] == 2023


def test_invalid_calculated_metric_returns_422(companies_client, api_session_factory):
    _add_company(api_session_factory)

    response = companies_client.get(
        "/api/v1/companies/MSFT/metrics?metric=unsupported_metric"
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_supported_metric_with_no_data_returns_404(
    companies_client, api_session_factory
):
    _add_company(api_session_factory)

    response = companies_client.get(
        "/api/v1/companies/MSFT/metrics?metric=current_ratio"
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": "No financial data found for this metric."}


def test_metric_history_is_chronological_and_paginated(
    companies_client, api_session_factory
):
    _add_company(api_session_factory)
    for year in (2022, 2023, 2024):
        _add_metric(api_session_factory, year=year, value=year / 10000)

    response = companies_client.get(
        "/api/v1/companies/MSFT/metrics/revenue_growth_yoy/history?limit=1&offset=1"
    )

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["total"] == 3
    assert body["items"][0]["fiscal_year"] == 2023


def test_metric_history_missing_data_returns_404(
    companies_client, api_session_factory
):
    _add_company(api_session_factory)

    response = companies_client.get(
        "/api/v1/companies/MSFT/metrics/current_ratio/history"
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_facts_preserve_as_filed_and_restatement_provenance(
    companies_client, api_session_factory
):
    _add_company(api_session_factory)
    _add_fact(
        api_session_factory,
        accession="0000789019-24-000001",
        value=110.0,
        is_restated=True,
        value_original=100.0,
    )

    response = companies_client.get(
        "/api/v1/companies/MSFT/facts?metric=revenue"
    )

    assert response.status_code == status.HTTP_200_OK
    fact = response.json()["items"][0]
    assert fact["value"] == 110.0
    assert fact["value_original"] == 100.0
    assert fact["is_restated"] is True
    assert fact["accession_number"] == "0000789019-24-000001"
    assert fact["source_url"].startswith("https://www.sec.gov/")


def test_facts_are_paginated(companies_client, api_session_factory):
    _add_company(api_session_factory)
    for year in (2022, 2023, 2024):
        _add_fact(
            api_session_factory,
            year=year,
            accession=f"0000789019-{year % 100:02d}-000001",
            value=float(year),
        )

    response = companies_client.get("/api/v1/companies/MSFT/facts?limit=1&offset=1")

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["total"] == 3
    assert len(body["items"]) == 1
    assert body["items"][0]["fiscal_year"] == 2023


def test_facts_period_filter_counts_only_matching_rows(
    companies_client, api_session_factory
):
    _add_company(api_session_factory)
    _add_fact(api_session_factory, year=2023, period_type="annual")
    _add_fact(
        api_session_factory,
        year=2024,
        accession="0000789019-24-000002",
        period_type="quarterly",
    )

    response = companies_client.get(
        "/api/v1/companies/MSFT/facts?period_type=quarterly"
    )

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["period_type"] == "quarterly"


def test_invalid_fact_period_returns_422(companies_client, api_session_factory):
    _add_company(api_session_factory)

    response = companies_client.get(
        "/api/v1/companies/MSFT/facts?period_type=monthly"
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_invalid_fact_metric_and_missing_fact_filter(
    companies_client, api_session_factory
):
    _add_company(api_session_factory)

    invalid = companies_client.get("/api/v1/companies/MSFT/facts?metric=made_up")
    missing = companies_client.get("/api/v1/companies/MSFT/facts?metric=revenue")

    assert invalid.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert missing.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1"])
def test_invalid_pagination_returns_422(companies_client, query):
    response = companies_client.get(f"/api/v1/companies?{query}")

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
