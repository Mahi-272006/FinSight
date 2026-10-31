"""SEC EDGAR HTTP client.

Responsibilities (all of them matter for reliability):

1. Identification: every request carries a descriptive User-Agent with contact
   info. SEC's fair-access policy requires this; without it requests are blocked.
2. Rate limiting: SEC allows max 10 requests/second. We throttle to a
   configurable 8 req/s using a minimum-interval gate, so even a burst of
   concurrent calls cannot exceed the limit.
3. Retries with backoff: HTTP 429 (rate limited) and 5xx (server error) are
   transient -- we retry a bounded number of times with exponential backoff.
   403/400/404 mean WE did something wrong (bad CIK, blocked UA); retrying
   would only hammer the server, so we fail immediately with a clear message.
4. Observability: every request and retry is logged, so throttling and
   backoff are visible when running scripts interactively.

Testing: the httpx transport is injectable. Tests pass httpx.MockTransport
to simulate 429/500/timeouts deterministically -- no real SEC requests, no
quota burn, no flaky network tests.
"""

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

import httpx

from app.config import Settings, get_settings
from app.utils.logging import get_logger

logger = get_logger(__name__)

# SEC endpoints
TICKER_FILE_URL = "{files_url}/files/company_tickers.json"
COMPANY_FACTS_URL = "{base_url}/api/xbrl/companyfacts/CIK{cik:010d}.json"
# data.sec.gov/submissions/ lists every filing a company has made (form type,
# accession number, primary document filename, dates). It is the correct entry
# point for downloading actual filing documents -- no HTML scraping needed.
SUBMISSIONS_URL = "{base_url}/submissions/CIK{cik:010d}.json"


class SECClientError(Exception):
    """Base error for SEC client failures."""


class SECNotFoundError(SECClientError):
    """Resource does not exist (HTTP 404) -- retrying will not help."""


class SECBlockedError(SECClientError):
    """SEC refused the request (HTTP 403) -- usually a User-Agent problem."""


class SECServerError(SECClientError):
    """SEC returned 5xx and retries were exhausted."""


class RateLimiter:
    """Enforce a minimum interval between operations (thread-safe).

    Why min-interval instead of counting requests per second window:
    a sliding window allows two requests back-to-back at a window boundary,
    which can momentarily double the rate. A minimum interval (1/rate seconds
    between starts) guarantees the average AND the instantaneous rate stay
    under the limit. Simpler to reason about, and we have plenty of slack.
    """

    def __init__(self, requests_per_second: float, sleep: Callable[[float], None] = time.sleep):
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")
        self._min_interval = 1.0 / requests_per_second
        self._lock = threading.Lock()
        self._last_call: float | None = None
        # Injectable sleep makes tests fast: tests pass a fake sleep and a
        # fake clock instead of actually waiting.
        self._sleep = sleep
        self._clock = time.monotonic

    def wait(self) -> None:
        """Block until it is safe to make the next request."""
        with self._lock:
            now = self._clock()
            if self._last_call is not None:
                elapsed = now - self._last_call
                remaining = self._min_interval - elapsed
                if remaining > 0:
                    self._sleep(remaining)
                    now = self._clock()
            self._last_call = now


class SECClient:
    """Thin, polite client for SEC EDGAR JSON APIs."""

    def __init__(
        self,
        settings: Settings | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._settings = settings or get_settings()
        self._limiter = RateLimiter(self._settings.sec_requests_per_second, sleep=sleep)
        self._sleep = sleep
        # One client per process: httpx reuses connections, which matters when
        # ingesting several companies in one run.
        self._http = httpx.Client(
            transport=transport,
            headers={
                # SEC fair-access policy: identify yourself. A descriptive UA
                # with contact info is required, not optional.
                "User-Agent": self._settings.sec_user_agent,
                # SEC serves gzip; accepting it cuts payload sizes ~5-10x for
                # large company-facts files (Apple's is several MB).
                "Accept-Encoding": "gzip",
                "Accept": "application/json",
            },
            timeout=self._settings.sec_timeout_seconds,
            follow_redirects=True,
        )
        self._ticker_cache: dict[str, Any] | None = None

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def resolve_company(self, query: str) -> dict[str, Any]:
        """Resolve a ticker or company-name fragment to SEC identity.

        Returns {"cik": int, "ticker": str, "title": str}.
        Matching order: exact ticker -> case-insensitive name substring.
        """
        query = query.strip()
        if not query:
            raise SECClientError("Empty company query")

        data = self._fetch_ticker_file()

        # 1) Exact ticker match (fast path, unambiguous).
        query_upper = query.upper()
        for entry in data.values():
            if entry["ticker"].upper() == query_upper:
                return _normalize_entry(entry)

        # 2) Name substring match (for 'microsoft', 'nvidia corp', etc.).
        query_lower = query.lower()
        matches = [
            entry for entry in data.values() if query_lower in entry["title"].lower()
        ]
        if len(matches) == 1:
            return _normalize_entry(matches[0])
        if len(matches) > 1:
            # Never guess: multiple matches mean the user must disambiguate.
            preview = ", ".join(f"{m['ticker']} ({m['title']})" for m in matches[:5])
            raise SECClientError(
                f"Ambiguous company query {query!r}: {len(matches)} matches. "
                f"Examples: {preview}. Use the exact ticker instead."
            )

        raise SECNotFoundError(f"No company found for {query!r} in SEC ticker file.")

    def get_company_facts(self, cik: int) -> dict[str, Any]:
        """Fetch the full Company Facts payload for a CIK.

        The Company Facts API aggregates every XBRL fact the company has
        filed (all concepts, all periods, all filings). It is large but
        complete -- exactly what M2's normalizer needs.
        """
        if cik <= 0:
            raise SECClientError(f"Invalid CIK: {cik}")
        url = COMPANY_FACTS_URL.format(base_url=self._settings.sec_base_url, cik=cik)
        logger.info("Fetching company facts for CIK %010d", cik)
        return self._get_json(url)

    def get_submissions(self, cik: int) -> dict[str, Any]:
        """Fetch the filing index (submissions payload) for a CIK.

        The payload's `filings.recent` section holds parallel arrays
        (accessionNumber / filingDate / form / primaryDocument ...) covering
        the most recent ~1000 filings; older filings live in the extra files
        listed under `filings.files` (M4 processes `recent` only).
        """
        if cik <= 0:
            raise SECClientError(f"Invalid CIK: {cik}")
        url = SUBMISSIONS_URL.format(base_url=self._settings.sec_base_url, cik=cik)
        logger.info("Fetching submissions for CIK %010d", cik)
        return self._get_json(url)

    def get_bytes(self, url: str) -> bytes:
        """Download a raw document (e.g. a filing's primary HTML) as bytes.

        Byte-faithful by design, same rule as raw JSON storage: we never
        re-encode at this stage. Filing HTML goes to disk exactly as SEC
        served it, so the on-disk file is the audit trail for everything
        extracted from it (text, sections, chunks).
        """
        response = self._get_response(
            url, accept="text/html,application/xhtml+xml,*/*"
        )
        return response.content

    def save_raw(self, payload: dict[str, Any], cik: int) -> Path:
        """Persist a raw API payload byte-for-byte under data/raw/.

        Raw storage rule: never re-serialize or 'clean' at this stage. The
        file is the audit trail -- if a metric looks wrong in M2, we diff
        this file against a fresh SEC response to locate the discrepancy.
        """
        directory = self._settings.data_raw_dir / "companyfacts"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"CIK{cik:010d}.json"
        # ensure_ascii=False keeps company names readable (e.g. accents);
        # the content is semantically identical to the SEC response.
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        logger.info("Saved raw payload -> %s", path)
        return path

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "SECClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _fetch_ticker_file(self) -> dict[str, Any]:
        """Fetch (and cache) SEC's official ticker->CIK mapping file."""
        if self._ticker_cache is None:
            url = TICKER_FILE_URL.format(files_url=self._settings.sec_files_url)
            logger.info("Fetching SEC ticker->CIK file")
            self._ticker_cache = self._get_json(url)
        return self._ticker_cache

    def _get_response(
        self, url: str, accept: str = "application/json"
    ) -> httpx.Response:
        """GET a URL with rate limiting + bounded retries; return the response.

        Retry policy (deliberately asymmetric):
        - 429 / 5xx / network errors -> transient; retry with backoff.
        - 400 / 403 / 404 -> our fault or permanent; fail immediately.

        This is the single choke point for ALL SEC traffic (JSON APIs and raw
        document downloads alike), so rate limiting and retry behavior cannot
        drift between endpoints. Callers decide how to interpret the body.
        """
        max_retries = self._settings.sec_max_retries
        backoff_base = self._settings.sec_backoff_base_seconds

        for attempt in range(max_retries + 1):
            self._limiter.wait()
            try:
                response = self._http.get(url, headers={"Accept": accept})
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                # Network-level problems: server unreachable, connection reset.
                # Transient by nature -> retry.
                if attempt == max_retries:
                    raise SECClientError(
                        f"Network error after {max_retries + 1} attempts: {exc}"
                    ) from exc
                delay = backoff_base * (2**attempt)
                logger.warning(
                    "Network error (%s); retry %d/%d in %.1fs",
                    type(exc).__name__, attempt + 1, max_retries, delay,
                )
                self._sleep(delay)
                continue

            status = response.status_code

            if status == 200:
                return response

            if status == 429 or status >= 500:
                if attempt == max_retries:
                    raise SECServerError(
                        f"SEC returned {status} after {max_retries + 1} attempts: {url}"
                    )
                # Exponential backoff: 0.5s, 1s, 2s... SEC's Retry-After
                # header (when present) takes priority -- the server knows
                # better than our heuristic.
                retry_after = response.headers.get("Retry-After")
                delay = (
                    float(retry_after)
                    if retry_after and retry_after.isdigit()
                    else backoff_base * (2**attempt)
                )
                logger.warning(
                    "SEC %d; retry %d/%d in %.1fs", status, attempt + 1, max_retries, delay
                )
                self._sleep(delay)
                continue

            if status == 404:
                raise SECNotFoundError(f"Not found (404): {url}")
            if status == 403:
                raise SECBlockedError(
                    "SEC returned 403 Forbidden. Check that SEC_USER_AGENT in "
                    "backend/.env is descriptive and contains a real contact "
                    "address (SEC fair-access policy), then try again later."
                )
            # Any other status (400, etc.) is a client-side bug: fail loudly.
            raise SECClientError(f"Unexpected HTTP {status} from {url}")

    def _get_json(self, url: str) -> dict[str, Any]:
        """GET a URL as JSON (thin wrapper over `_get_response`)."""
        response = self._get_response(url)
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            # A 200 with a corrupt body is rare but possible (proxy
            # interference). Treat as transient server problem.
            raise SECServerError(f"Invalid JSON from {url}: {exc}") from exc


def _normalize_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Convert a ticker-file entry to our internal identity shape."""
    return {
        "cik": int(entry["cik_str"]),
        "ticker": entry["ticker"],
        "title": entry["title"],
    }
