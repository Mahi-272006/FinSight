"""Tests for the SEC client and Company Facts inspection (M1).

Design: ALL tests use httpx.MockTransport -- zero real SEC requests.
This lets us deterministically simulate 429s, 500s, timeouts, and 403s,
which we could never do reliably against the live API. It also means the
test suite runs in milliseconds and never risks SEC blocking us.

Run: pytest tests/test_sec_ingestion.py -v
"""

import json

import httpx
import pytest

from app.config import Settings
from app.ingestion.company_facts import summarize
from app.ingestion.sec_client import (
    SECBlockedError,
    SECClient,
    SECClientError,
    SECNotFoundError,
    SECServerError,
    RateLimiter,
)

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

# Minimal ticker file shaped like SEC's real one (dict of numeric keys ->
# {cik_str, ticker, title}). Truncated to just what our tests need.
TICKER_FIXTURE = {
    "0": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
    "1": {"cik_str": 320193, "ticker": "AAPL", "title": "APPLE INC."},
    "2": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
    "3": {"cik_str": 1652044, "ticker": "GOOGL", "title": "Alphabet Inc."},
}

# Minimal company-facts payload shaped like SEC's real one:
# facts -> taxonomy -> concept -> units -> [fact dicts].
FACTS_FIXTURE = {
    "cik": 789019,
    "entityName": "MICROSOFT CORP",
    "facts": {
        "us-gaap": {
            "Revenues": {
                "units": {
                    "USD": [
                        {
                            "start": "2023-07-01",
                            "end": "2024-06-30",
                            "val": 245122000000,
                            "form": "10-K",
                            "fy": 2024,
                            "fp": "FY",
                            "accn": "0000950170-24-087554",
                        },
                        {
                            "start": "2024-07-01",
                            "end": "2024-12-31",
                            "val": 127000000000,
                            "form": "10-Q",
                            "fy": 2025,
                            "fp": "Q2",
                            "accn": "0000950170-25-012345",
                        },
                    ]
                }
            },
            "NetIncomeLoss": {
                "units": {
                    "USD": [
                        {
                            "start": "2023-07-01",
                            "end": "2024-06-30",
                            "val": 88136000000,
                            "form": "10-K",
                            "fy": 2024,
                            "fp": "FY",
                            "accn": "0000950170-24-087554",
                        }
                    ]
                }
            },
        },
        "dei": {
            "EntityCommonStockSharesOutstanding": {
                "units": {
                    "shares": [
                        {
                            "end": "2024-06-30",
                            "val": 7434000000,
                            "form": "10-K",
                            "fy": 2024,
                            "fp": "FY",
                            "accn": "0000950170-24-087554",
                        }
                    ]
                }
            }
        },
    },
}


def make_settings(**overrides) -> Settings:
    """Build a Settings object for tests, bypassing .env entirely.

    We override the User-Agent to avoid the example.com validation, and use
    a very high request rate so the rate limiter never slows tests down
    (except in the dedicated RateLimiter tests, which construct one directly).
    """
    defaults = dict(
        sec_user_agent="FinSight Tests test@example.org",
        sec_requests_per_second=10_000.0,
        sec_max_retries=2,
        sec_backoff_base_seconds=0.001,  # near-zero backoff for fast tests
    )
    defaults.update(overrides)
    return Settings(**defaults)


def make_client(handler, settings: Settings | None = None, sleep=None) -> SECClient:
    """Build an SECClient wired to a mock HTTP handler."""
    return SECClient(
        settings=settings or make_settings(),
        transport=httpx.MockTransport(handler),
        sleep=sleep or (lambda _seconds: None),  # no real sleeping in tests
    )


# --------------------------------------------------------------------------- #
# Company resolution
# --------------------------------------------------------------------------- #


class TestResolveCompany:
    def test_exact_ticker_match(self):
        client = make_client(lambda _req: httpx.Response(200, json=TICKER_FIXTURE))
        result = client.resolve_company("MSFT")
        assert result == {"cik": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"}

    def test_ticker_match_is_case_insensitive(self):
        client = make_client(lambda _req: httpx.Response(200, json=TICKER_FIXTURE))
        assert client.resolve_company("msft")["cik"] == 789019

    def test_name_substring_resolves_unique_match(self):
        client = make_client(lambda _req: httpx.Response(200, json=TICKER_FIXTURE))
        result = client.resolve_company("microsoft")
        assert result["ticker"] == "MSFT"

    def test_ambiguous_name_raises_with_suggestions(self):
        # 'corp' matches two entries -> must NOT guess.
        client = make_client(lambda _req: httpx.Response(200, json=TICKER_FIXTURE))
        with pytest.raises(SECClientError, match="Ambiguous"):
            client.resolve_company("corp")

    def test_unknown_company_raises_not_found(self):
        client = make_client(lambda _req: httpx.Response(200, json=TICKER_FIXTURE))
        with pytest.raises(SECNotFoundError, match="No company found"):
            client.resolve_company("TSLAAA")

    def test_empty_query_raises(self):
        client = make_client(lambda _req: httpx.Response(200, json=TICKER_FIXTURE))
        with pytest.raises(SECClientError, match="Empty"):
            client.resolve_company("   ")

    def test_ticker_file_fetched_only_once(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(200, json=TICKER_FIXTURE)

        client = make_client(handler)
        client.resolve_company("MSFT")
        client.resolve_company("AAPL")
        assert len(calls) == 1, "ticker file should be cached after first fetch"


# --------------------------------------------------------------------------- #
# Company facts fetch
# --------------------------------------------------------------------------- #


class TestGetCompanyFacts:
    def test_url_contains_zero_padded_cik(self):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, json=FACTS_FIXTURE)

        client = make_client(handler)
        client.get_company_facts(789019)
        # SEC requires 10-digit zero-padded CIK in the URL; a non-padded CIK
        # silently 404s, so this assertion guards a real failure mode.
        assert "CIK0000789019.json" in seen[0]

    def test_invalid_cik_raises(self):
        client = make_client(lambda _req: httpx.Response(200, json={}))
        with pytest.raises(SECClientError, match="Invalid CIK"):
            client.get_company_facts(-1)

    def test_user_agent_header_sent(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["user_agent"] = request.headers["User-Agent"]
            return httpx.Response(200, json=FACTS_FIXTURE)

        client = make_client(handler, make_settings(sec_user_agent="FinSight Tests contact@test.org"))
        client.get_company_facts(789019)
        assert seen["user_agent"] == "FinSight Tests contact@test.org"


# --------------------------------------------------------------------------- #
# Retry / error policy -- the heart of reliable ingestion
# --------------------------------------------------------------------------- #


class TestRetryPolicy:
    def test_500_then_success_retries_and_recovers(self):
        attempts = []

        def handler(_request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            if len(attempts) < 3:
                return httpx.Response(500)
            return httpx.Response(200, json=FACTS_FIXTURE)

        client = make_client(handler)
        result = client.get_company_facts(789019)
        assert result["entityName"] == "MICROSOFT CORP"
        assert len(attempts) == 3, "should retry past two 500s"

    def test_persistent_500_raises_server_error(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        client = make_client(handler)
        with pytest.raises(SECServerError, match="500"):
            client.get_company_facts(789019)

    def test_429_respects_retry_after_header(self):
        delays = []
        attempts = []

        def handler(_request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            if len(attempts) == 1:
                return httpx.Response(429, headers={"Retry-After": "7"})
            return httpx.Response(200, json=FACTS_FIXTURE)

        client = make_client(handler, sleep=delays.append)
        client.get_company_facts(789019)
        assert delays and delays[0] == 7.0, "Retry-After must override backoff heuristic"

    def test_403_fails_immediately_without_retry(self):
        attempts = []

        def handler(_request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            return httpx.Response(403)

        client = make_client(handler)
        with pytest.raises(SECBlockedError, match="403 Forbidden"):
            client.get_company_facts(789019)
        assert len(attempts) == 1, "403 must NOT be retried (hammering SEC looks like abuse)"

    def test_404_fails_immediately(self):
        attempts = []

        def handler(_request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            return httpx.Response(404)

        client = make_client(handler)
        with pytest.raises(SECNotFoundError):
            client.get_company_facts(9999999999)
        assert len(attempts) == 1

    def test_timeout_retries_then_raises(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("simulated timeout")

        client = make_client(handler)
        with pytest.raises(SECClientError, match="Network error"):
            client.get_company_facts(789019)

    def test_exponential_backoff_grows(self):
        delays = []
        attempts = []

        def handler(_request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            if len(attempts) < 3:
                return httpx.Response(503)
            return httpx.Response(200, json=FACTS_FIXTURE)

        client = make_client(
            handler,
            make_settings(sec_backoff_base_seconds=1.0),
            sleep=delays.append,
        )
        client.get_company_facts(789019)
        # The injected `sleep` is shared by BOTH the rate limiter (tiny
        # ~0.0001s waits at 10k req/s) and the retry backoff (1.0s, 2.0s).
        # Filter to backoff-sized delays to assert on backoff growth alone.
        backoff_delays = [d for d in delays if d >= 0.5]
        assert backoff_delays == [1.0, 2.0], "backoff must double each attempt"


# --------------------------------------------------------------------------- #
# Rate limiting
# --------------------------------------------------------------------------- #


class TestRateLimiter:
    def test_enforces_minimum_interval(self):
        clock = {"now": 0.0}
        slept = []

        def fake_sleep(seconds: float) -> None:
            slept.append(seconds)
            clock["now"] += seconds

        limiter = RateLimiter(requests_per_second=10.0, sleep=fake_sleep)
        limiter._clock = lambda: clock["now"]

        limiter.wait()  # first call: no wait
        clock["now"] += 0.01  # only 10ms elapse
        limiter.wait()  # must sleep the remaining ~90ms
        assert slept and abs(slept[0] - 0.09) < 1e-9

    def test_no_sleep_when_interval_already_elapsed(self):
        slept = []
        clock = {"now": 0.0}
        limiter = RateLimiter(requests_per_second=10.0, sleep=slept.append)
        limiter._clock = lambda: clock["now"]

        limiter.wait()  # first call: sets last_call = 0.0, no sleep
        clock["now"] += 1.0  # 1s elapses -- far beyond the 0.1s min interval
        limiter.wait()  # enough time already passed -> must NOT sleep
        assert slept == [], "no sleep needed if enough time already passed"

    def test_rejects_nonpositive_rate(self):
        with pytest.raises(ValueError):
            RateLimiter(0)


# --------------------------------------------------------------------------- #
# Raw storage
# --------------------------------------------------------------------------- #


class TestRawStorage:
    def test_save_and_reload_round_trip(self, tmp_path):
        settings = make_settings(data_raw_dir=tmp_path)

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=FACTS_FIXTURE)

        client = make_client(handler, settings)
        client.get_company_facts(789019)
        payload = client.get_company_facts(789019)
        path = client.save_raw(payload, 789019)

        assert path.exists()
        assert path.name == "CIK0000789019.json"
        reloaded = json.loads(path.read_text(encoding="utf-8"))
        assert reloaded == FACTS_FIXTURE, "raw file must match the API response exactly"


# --------------------------------------------------------------------------- #
# Inspection summary
# --------------------------------------------------------------------------- #


class TestSummarize:
    def test_summary_counts(self):
        report = summarize(FACTS_FIXTURE)
        assert report["entity_name"] == "MICROSOFT CORP"
        assert report["cik"] == 789019
        assert report["concept_count"] == 3  # Revenues, NetIncomeLoss, dei:Shares
        assert report["concepts_by_taxonomy"] == {"us-gaap": 2, "dei": 1}
        assert report["forms"] == {"10-K": 3, "10-Q": 1}

    def test_annual_years_detected_from_period_length(self):
        # The 10-K fact spans 2023-07-01..2024-06-30 (~365 days) -> FY2024.
        # The 10-Q fact spans 6 months -> must NOT be counted as annual even
        # though we key off the 10-K form; this validates the date-duration
        # heuristic that M2 will rely on for period classification.
        report = summarize(FACTS_FIXTURE)
        assert report["fiscal_years"] == [2024]

    def test_key_concept_presence(self):
        report = summarize(FACTS_FIXTURE)
        present = report["key_concepts_present"]
        assert present["Revenues"] is True
        assert present["NetIncomeLoss"] is True
        assert present["GrossProfit"] is False  # absent from fixture

    def test_empty_payload_does_not_crash(self):
        report = summarize({"facts": {}})
        assert report["concept_count"] == 0
        assert report["fiscal_years"] == []
