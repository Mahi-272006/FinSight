"""Tests for filing discovery (M4): submissions parsing, selection, URLs.

Parsing tests run on a fixture submissions payload (the parallel-array
shape SEC actually returns, including ragged/missing fields). Client tests
use httpx.MockTransport -- zero real SEC requests, same pattern as M1.
"""

import httpx
import pytest

from app.ingestion.filing_downloader import (
    DEFAULT_FORMS,
    FilingRef,
    parse_recent_filings,
    select_filings,
)
from app.ingestion.sec_client import SECBlockedError, SECClient, SECClientError, SECNotFoundError

# Fixture shaped like data.sec.gov/submissions: parallel arrays. Includes
# edge cases: an entry with a missing primary document, a 10-K/A amendment,
# and entries with missing report dates.
SUBMISSIONS = {
    "filings": {
        "recent": {
            "accessionNumber": [
                "0000950170-25-000111",  # 10-K
                "0000950170-25-000222",  # 10-Q
                "0000950170-24-000333",  # 10-K/A (amendment)
                "0000950170-24-000444",  # 8-K with NO primary document
                "0000950170-24-000555",  # 10-Q with blank reportDate
                "0000950170-24-000666",  # 10-Q
            ],
            "filingDate": [
                "2025-07-30", "2025-04-24", "2024-11-01",
                "2024-09-12", "2024-08-01", "2024-04-25",
            ],
            "form": ["10-K", "10-Q", "10-K/A", "8-K", "10-Q", "10-Q"],
            "primaryDocument": [
                "msft-20250630.htm", "msft-20250331.htm", "msft-20240630.htm",
                "", "msft-20240131.htm", "msft-20240331.htm",
            ],
            "reportDate": [
                "2025-06-30", "2025-03-31", "2024-06-30",
                "", "", "2024-03-31",
            ],
        }
    }
}


class TestParseRecentFilings:
    def test_parses_entries_with_documents(self):
        refs = parse_recent_filings(SUBMISSIONS, cik=789019)
        # 5 of 6 entries have a primary document (one is blank).
        assert len(refs) == 5

    def test_fields_mapped_correctly(self):
        refs = parse_recent_filings(SUBMISSIONS, cik=789019)
        latest = refs[0]  # newest first
        assert latest.accession == "0000950170-25-000111"
        assert latest.form == "10-K"
        assert latest.filing_date == "2025-07-30"
        assert latest.primary_document == "msft-20250630.htm"
        assert latest.report_date == "2025-06-30"
        assert latest.cik == 789019

    def test_sorted_newest_first(self):
        refs = parse_recent_filings(SUBMISSIONS, cik=789019)
        dates = [r.filing_date for r in refs]
        assert dates == sorted(dates, reverse=True)

    def test_missing_report_date_tolerated(self):
        refs = parse_recent_filings(SUBMISSIONS, cik=789019)
        # 000555 has a primary document but a blank reportDate.
        blank = next(r for r in refs if r.accession == "0000950170-24-000555")
        assert blank.report_date is None

    def test_ragged_arrays_do_not_crash(self):
        # A payload missing secondary arrays entirely still parses the
        # anchor array (accessionNumber).
        minimal = {"filings": {"recent": {
            "accessionNumber": ["0001-01-0001"],
            "primaryDocument": ["doc.htm"],
        }}}
        refs = parse_recent_filings(minimal, cik=1)
        assert refs[0].form == ""
        assert refs[0].filing_date == ""

    def test_missing_anchor_key_raises(self):
        with pytest.raises(SECClientError):
            parse_recent_filings({"filings": {}}, cik=1)


class TestSelectFilings:
    def test_filters_to_10k_10q(self):
        refs = parse_recent_filings(SUBMISSIONS, cik=789019)
        selected = select_filings(refs)
        assert all(r.form in DEFAULT_FORMS for r in selected)
        # 10-K/A and 8-K excluded.
        assert len(selected) == 4

    def test_limit_applies_after_form_filter(self):
        refs = parse_recent_filings(SUBMISSIONS, cik=789019)
        selected = select_filings(refs, limit=2)
        assert len(selected) == 2
        assert selected[0].filing_date >= selected[1].filing_date

    def test_no_limit_returns_all_matches(self):
        refs = parse_recent_filings(SUBMISSIONS, cik=789019)
        assert len(select_filings(refs, limit=None)) == 4

    def test_invalid_limit_raises(self):
        refs = parse_recent_filings(SUBMISSIONS, cik=789019)
        with pytest.raises(SECClientError):
            select_filings(refs, limit=0)


class TestDocumentUrl:
    def test_accession_dashes_stripped(self):
        ref = FilingRef(
            cik=789019, accession="0000950170-25-000111", form="10-K",
            filing_date="2025-07-30", primary_document="msft-20250630.htm",
        )
        url = ref.document_url("https://www.sec.gov")
        assert url == (
            "https://www.sec.gov/Archives/edgar/data/789019/"
            "000095017025000111/msft-20250630.htm"
        )

    def test_accession_nodash_property(self):
        ref = FilingRef(
            cik=1, accession="0001-01-01-01", form="10-K",
            filing_date="2001-01-01", primary_document="x.htm",
        )
        assert ref.accession_nodash == "0001010101"


def _client(handler) -> SECClient:
    """SECClient over a MockTransport with a no-op rate limiter."""
    return SECClient(
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
    )


class TestClientExtensions:
    def test_get_submissions_returns_payload(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert "/submissions/CIK0000789019.json" in str(request.url)
            assert request.headers["User-Agent"]  # SEC identification present
            return httpx.Response(200, json=SUBMISSIONS)

        with _client(handler) as client:
            data = client.get_submissions(789019)
        assert data["filings"]["recent"]["form"][0] == "10-K"

    def test_get_bytes_returns_raw_content(self):
        html = b"<html><p>10-K body</p></html>"

        def handler(request: httpx.Request) -> httpx.Response:
            assert "/Archives/edgar/data/" in str(request.url)
            # Document requests must advertise an HTML-friendly Accept.
            assert "text/html" in request.headers["Accept"]
            return httpx.Response(200, content=html)

        with _client(handler) as client:
            content = client.get_bytes(
                "https://www.sec.gov/Archives/edgar/data/789019/0001/msft.htm"
            )
        assert content == html

    def test_get_bytes_404_fails_fast(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        with _client(handler) as client:
            with pytest.raises(SECNotFoundError):
                client.get_bytes("https://www.sec.gov/Archives/edgar/data/1/2/gone.htm")

    def test_get_bytes_403_surfaces_fair_access_message(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403)

        with _client(handler) as client:
            with pytest.raises(SECBlockedError, match="SEC_USER_AGENT"):
                client.get_bytes("https://www.sec.gov/Archives/edgar/data/1/2/blocked.htm")

    def test_invalid_cik_rejected(self):
        with _client(lambda r: httpx.Response(200, json={})) as client:
            with pytest.raises(SECClientError):
                client.get_submissions(0)
