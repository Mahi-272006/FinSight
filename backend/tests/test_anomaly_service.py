"""Transactional service tests for recomputing persisted investigation signals."""

import json

import pytest

from app.database.models import Company, FinancialMetric
from app.database.repositories import SignalRepository
from app.services.anomaly_service import (
    AnomalyCompanyNotFoundError,
    AnomalyRecomputeError,
    AnomalyService,
    InsufficientAnomalyDataError,
    UnsupportedAnomalyTickerError,
    load_company_series,
)


def _company(factory, *, ticker="MSFT", cik=789019):
    with factory() as session:
        company = Company(cik=cik, ticker=ticker, name="Test Company")
        session.add(company)
        session.commit()
        return company


def _add_metric(session, *, cik, metric, value, year=2024, accessions=None):
    session.add(
        FinancialMetric(
            cik=cik,
            metric=metric,
            value=value,
            unit="ratio",
            fiscal_year=year,
            fiscal_period="FY",
            period_type="annual",
            formula="test_formula",
            inputs_json='{"input": 1}',
            source_accessions_json=json.dumps(accessions or []),
        )
    )


def _add_divergence_inputs(factory, cik=789019):
    with factory() as session:
        for metric, value in (
            ("earnings_cashflow_divergence", 0.35),
            ("net_income_growth_yoy", 0.40),
            ("operating_cash_flow_growth_yoy", 0.05),
        ):
            _add_metric(
                session,
                cik=cik,
                metric=metric,
                value=value,
                accessions=["0000789019-24-000001"],
            )
        session.commit()


def test_series_loader_excludes_nonannual_rows_and_keeps_accessions(
    api_session_factory,
):
    company = _company(api_session_factory)
    with api_session_factory() as session:
        _add_metric(
            session,
            cik=company.cik,
            metric="revenue_growth_yoy",
            value=0.2,
            accessions=["0000789019-24-000001"],
        )
        _add_metric(
            session,
            cik=company.cik,
            metric="revenue_growth_yoy",
            value=0.9,
            year=2023,
        )
        quarterly = session.query(FinancialMetric).filter_by(fiscal_year=2023).one()
        quarterly.fiscal_period = "Q4"
        quarterly.period_type = "quarterly"
        session.commit()

        loaded = load_company_series(session, company)

    assert loaded.annual_metrics == {"revenue_growth_yoy": {2024: 0.2}}
    assert loaded.accessions == {
        "revenue_growth_yoy": {2024: ["0000789019-24-000001"]}
    }


def test_recompute_persists_and_returns_actual_signal_with_provenance(api_session_factory):
    _company(api_session_factory)
    _add_divergence_inputs(api_session_factory)

    cik, ticker, signals = AnomalyService(api_session_factory).recompute("msft")

    assert (cik, ticker) == (789019, "MSFT")
    assert len(signals) == 1
    signal = signals[0]
    assert signal.signal_type == "earnings_cashflow_divergence"
    assert signal.severity == "elevated"
    assert signal.evidence_json
    assert signal.source_accessions_json == '["0000789019-24-000001"]'
    with api_session_factory() as session:
        persisted = SignalRepository(session).get_signals_for_company(789019)
    assert len(persisted) == 1
    assert persisted[0].id == signal.id


@pytest.mark.parametrize("ticker", ["TSLA", "bad!"])
def test_recompute_rejects_unsupported_ticker(api_session_factory, ticker):
    with pytest.raises(UnsupportedAnomalyTickerError):
        AnomalyService(api_session_factory).recompute(ticker)


def test_recompute_returns_missing_company_error(api_session_factory):
    with pytest.raises(AnomalyCompanyNotFoundError):
        AnomalyService(api_session_factory).recompute("MSFT")


def test_recompute_rejects_insufficient_inputs_without_changing_existing_signals(
    api_session_factory,
):
    _company(api_session_factory)
    with api_session_factory() as session:
        _add_metric(
            session,
            cik=789019,
            metric="revenue_growth_yoy",
            value=0.2,
            year=2024,
        )
        session.commit()

    with pytest.raises(InsufficientAnomalyDataError):
        AnomalyService(api_session_factory).recompute("MSFT")

    with api_session_factory() as session:
        assert SignalRepository(session).get_signals_for_company(789019) == []


def test_recompute_with_complete_quiet_inputs_returns_empty_and_clears_stale_signals(
    api_session_factory,
):
    from app.services.anomaly_detection import InvestigationSignal

    _company(api_session_factory)
    with api_session_factory() as session:
        for year, growth in ((2023, 0.15), (2024, 0.14)):
            _add_metric(
                session,
                cik=789019,
                metric="revenue_growth_yoy",
                value=growth,
                year=year,
            )
        SignalRepository(session).upsert_signals(
            [
                InvestigationSignal(
                    cik=789019,
                    ticker="MSFT",
                    signal_type="growth_deceleration",
                    severity="elevated",
                    fiscal_year=2020,
                    fiscal_period="FY",
                    period_type="annual",
                    headline="Old signal.",
                    explanation="Old evidence.",
                    evidence={},
                )
            ]
        )
        session.commit()

    _cik, _ticker, signals = AnomalyService(api_session_factory).recompute("MSFT")

    assert signals == []
    with api_session_factory() as session:
        assert SignalRepository(session).get_signals_for_company(789019) == []


def test_recompute_rolls_back_signal_replacement_when_persistence_fails(
    api_session_factory, monkeypatch
):
    from app.services.anomaly_detection import InvestigationSignal

    _company(api_session_factory)
    _add_divergence_inputs(api_session_factory)
    with api_session_factory() as session:
        SignalRepository(session).upsert_signals(
            [
                InvestigationSignal(
                    cik=789019,
                    ticker="MSFT",
                    signal_type="growth_deceleration",
                    severity="elevated",
                    fiscal_year=2020,
                    fiscal_period="FY",
                    period_type="annual",
                    headline="Existing persisted signal.",
                    explanation="Existing explanation.",
                    evidence={},
                )
            ]
        )
        session.commit()

    original = SignalRepository.replace_company_signals

    def fail_after_flush(repository, cik, signals):
        original(repository, cik, signals)
        raise RuntimeError("private database detail")

    monkeypatch.setattr(SignalRepository, "replace_company_signals", fail_after_flush)
    with pytest.raises(AnomalyRecomputeError) as error:
        AnomalyService(api_session_factory).recompute("MSFT")
    assert "private database detail" not in str(error.value)

    with api_session_factory() as session:
        rows = SignalRepository(session).get_signals_for_company(789019)
    assert [(row.signal_type, row.fiscal_year) for row in rows] == [
        ("growth_deceleration", 2020)
    ]


def test_script_uses_the_shared_series_loader():
    from app.services.anomaly_service import load_company_series
    from scripts.analyze_peers import load_series

    assert load_series is load_company_series
