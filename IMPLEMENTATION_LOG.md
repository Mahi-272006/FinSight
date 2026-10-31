# FinSight — Implementation Log

Chronological record of every milestone: what was built, what commands were run,
what was tested, what is still missing, and what comes next.

---

## M0 — Project Setup

**Status:** ✅ COMPLETE
**Date:** 2026-10-31
**Commit:** `ee8999d`

### Starting point (inspection findings)

- Project root: `E:\FinSight` (a `D:\FinSight` path was referenced in the
  original brief but does not exist; confirmed with the user that `E:\FinSight`
  is correct).
- `backend/` had a complete folder skeleton (~60 Python files) — **all empty
  (0 bytes)**, awaiting implementation.
- `backend/venv/` existed with Python 3.10.11 but only pip/setuptools installed.
- `frontend/` was a fresh Vite + React 19 scaffold with `node_modules`
  installed; no router/axios/recharts yet.
- `requirements.txt` was empty; `.env` was empty; `.gitignore` was empty;
  `README.md` was empty.
- PostgreSQL 18.3 was already installed and running as a Windows service
  (`postgresql-x64-18`), but `psql` was not on PATH; it lives at
  `C:\Program Files\PostgreSQL\18\bin\`.
- The directory was **not** a git repository.

### Files created / changed

| File | Purpose |
|---|---|
| `.gitignore` (root) | Protects `.env`, `venv/`, `data/*`, `logs/`, `node_modules/`, `dist/` from being committed |
| `backend/requirements.txt` | Pinned-range dependency list, grouped by concern, with Windows torch note |
| `backend/.env` | Environment template with placeholders (empty API keys, SEC User-Agent, DB URL, Chroma path) |
| `README.md` (root) | Project overview, design principles, setup instructions, roadmap |
| `ARCHITECTURE.md` | Beginner-friendly explanation of all components, data flow, and design rationale |
| `IMPLEMENTATION_LOG.md` | This file |
| `backend/pytest.ini` | Pytest config: test discovery, `asyncio_mode=auto` for M5/M6 async tests, known-warning filter |
| `backend/tests/conftest.py` | Shared fixture stub (filled per milestone) |
| `backend/tests/test_smoke.py` | 3 smoke tests proving pytest + async mode + package imports work |
| `backend/scripts/verify_environment.py` | 27-check environment verifier (imports, versions, integration smoke checks) |

### Decisions made

1. **Install CPU-only PyTorch first** (`--index-url .../whl/cpu`) before
   `requirements.txt`. Reason: on Windows the default PyTorch wheel pulls
   ~2.5 GB of CUDA libraries; the CPU build (~200 MB) is sufficient for the
   small all-MiniLM-L6-v2 embedding model and keeps the install zero-cost and
   fast.
2. **Pin dependency *ranges* (`>=X,<Y`) rather than exact versions** in
   `requirements.txt`. Reason: ranges document compatibility intent; exact
   pins can be added later (e.g. a `requirements-lock.txt`) once the stack is
   proven to work together, avoiding premature pinning to untested versions.
3. **Keep the existing empty file skeleton** rather than restructuring — it
   matches the architecture in the brief.

### Commands executed

```powershell
git init                                              # initialized repo
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -c "CREATE DATABASE finsight;"   # by user
venv\Scripts\pip.exe install torch --index-url https://download.pytorch.org/whl/cpu   # torch 2.14.1+cpu
venv\Scripts\pip.exe install -r requirements.txt      # 151 packages total
python scripts/verify_environment.py                  # 27/27 checks passed, exit 0
python -m pytest -q                                   # 3 passed, exit 0
git add -A && git commit                              # ee8999d
```

### Tests performed

1. **Environment verification** (`scripts/verify_environment.py`): all 24
   packages import cleanly. Three deeper integration checks passed:
   - `langchain_core.tools.tool` decorator constructs a working tool
     (proves LangChain tool-calling wiring, not just the import)
   - `langgraph.graph` START/END constants resolve
   - SQLAlchemy engine + connection via in-memory SQLite
2. **Pytest harness** (`tests/test_smoke.py`): 3 tests passed —
   sync test, async test (confirms `asyncio_mode=auto`), `import app`.

### Installed versions (key packages)

| Package | Version |
|---|---|
| Python | 3.10.11 |
| torch (CPU) | 2.14.1+cpu |
| langgraph | 0.6.11 |
| langchain-core | 0.3.86 |
| langchain-groq | 0.3.8 |
| langchain-google-genai | 2.1.12 |
| fastapi | 0.162.0 |
| sqlalchemy | 2.0.55 |
| alembic | 1.20.1 |
| psycopg | 3.3.6 |
| chromadb | 1.5.9 |
| sentence-transformers | 3.4.1 |
| scikit-learn | 1.7.2 |
| pandas | 2.3.3 |
| numpy | 2.2.6 |
| pytest | 8.4.2 |

### Known risks / warnings observed

- **Google Python 3.10 deprecation:** importing `langchain_google_genai`
  prints a FutureWarning — Google has stopped updating `google.api_core`
  for Python 3.10. Works today; affects only the Gemini fallback provider.
  Action deferred to M5 (when Gemini is actually integrated) — options then:
  keep 3.10 while it works, or upgrade the venv to 3.11+.
- `langgraph` emits a `LangChainPendingDeprecationWarning` about checkpoint
  serde defaults — cosmetic, no functional impact; revisit in M6 when
  checkpointing is implemented.

### Current limitations

- No application logic implemented yet; all `app/` files are still empty
  (verified importable, but containing no code).
- `.env` contains placeholders — user must supply real `GROQ_API_KEY` /
  `GEMINI_API_KEY`, a real SEC contact email, and the PostgreSQL password.
  (User has created the `finsight` database.)
- Frontend remains the untouched Vite scaffold.

### Next milestone (originally planned, now completed below)

**M1 — SEC Data Ingestion.**

---

## M1 — SEC Data Ingestion

**Status:** ✅ COMPLETE
**Date:** 2026-10-31

### What was built

| File | Purpose |
|---|---|
| `app/config.py` | Typed `Settings` via pydantic-settings; validates SEC User-Agent (rejects empty/placeholder), defines SEC URLs, rate limit (8 req/s), retry/backoff knobs, data paths |
| `app/utils/logging.py` | Consistent timestamped logging to console + rotating file (5 MB × 3); idempotent `configure_logging` |
| `app/ingestion/sec_client.py` | `SECClient`: httpx with SEC-compliant User-Agent, gzip; `RateLimiter` (min-interval, thread-safe, injectable sleep/clock); bounded retries with exponential backoff + `Retry-After` on 429/5xx/network errors; fail-fast on 403/404/400; `resolve_company` (exact ticker → name substring, ambiguous → error); `get_company_facts`; `save_raw` |
| `app/ingestion/company_facts.py` | `fetch_and_save` (resolve → fetch → persist); `summarize` (concept/form/fiscal-year counts, key-concept presence); `_is_annual_period` (date-duration heuristic, ~365 days) |
| `scripts/ingest_company.py` | CLI: `python scripts/ingest_company.py MSFT [AAPL ...]`; prints inspection summary; clean error per company; non-zero exit on any failure |
| `tests/test_sec_ingestion.py` | 25 tests, ALL via `httpx.MockTransport` (zero real SEC requests) |

### Key design decisions

1. **Asymmetric retry policy.** 429/5xx/timeout/connection → retry (max 3) with
   exponential backoff (0.5s, 1s, 2s), honoring SEC's `Retry-After` header when
   present. 403/404/400 → fail immediately (retrying a client-side error just
   hammers SEC and looks like abuse).
2. **Min-interval rate limiter** (not sliding-window): guarantees average AND
   instantaneous rate stay under 10 req/s; we use 8 to leave retry headroom.
3. **Injectable transport + sleep + clock** in `SECClient`: tests simulate
   429/500/timeout deterministically without quota burn or flaky timing.
4. **Raw storage is byte-faithful**: the audit trail M2+ can always diff
   against a fresh SEC response.

### Real-data findings (Microsoft, CIK 0000789019)

- **9.93 MB** raw payload; **572 concepts** across `us-gaap`, `dei`, `ecd`.
- Forms: 10-Q ×20373, 10-K ×10429, 8-K ×1770, 10-Q/A ×440, DEF 14A ×40.
- **19 fiscal years** of annual facts (2008–2026).
- **Concept drift confirmed live:** legacy `Revenues` tag only covers ~2010-era
  facts; modern revenue is `RevenueFromContractWithCustomerExcludingAssessedTax`
  (ASC 606 adoption). All 11 key concepts for M2 exist under modern tags.
- **Duplicate FY2025 revenue fact explained:** the same value ($281,724M)
  appears in the FY2025 10-K (filed 2025-07-30) AND the FY2026 10-K (filed
  2026-07-29, comparative prior-year). Same value → M2 dedups by
  `(concept, period)`; a *different* value would signal a true restatement to
  surface, not hide.
- Recent annual facts (as filed): FY2025 revenue $281.7B / op income $128.5B /
  net income $101.8B; FY2026 revenue $331.8B / op income $155.2B / net income
  $133.7B.

### Tests performed

1. **Unit (mocked HTTP):** 25/25 pass — resolution, URL zero-padding,
   User-Agent header, retry/backoff/Retry-After, 403/404 fail-fast, timeout
   retry, rate-limiter intervals, raw round-trip, summarize counts, empty
   payload safety.
2. **Integration (live SEC):** `python scripts/ingest_company.py MSFT` →
   exit 0; ticker→CIK resolution, HTTP 200, 9.93 MB saved, summary printed.
3. **Full suite:** 28/28 pass (`pytest -q`).

### Bugs found & fixed (during M1)

All three initial failures were **test-side** (the client code was correct):

1. 403 test regex `User-Agent` (hyphen) didn't match message `SEC_USER_AGENT`
   (underscore) → assert on `403 Forbidden` instead.
2. Backoff test also captured the rate limiter's tiny sleeps (one shared
   injected `sleep`) → filter recorded delays to backoff-sized (`>= 0.5`).
3. `no_sleep_when_interval_elapsed` premise was inverted (frozen clock = zero
   elapsed = sleeping is correct) → advance the fake clock past the interval.

### Current limitations / next steps

- Filing download/parse (M4) will reuse `SECClient` unchanged.
- `summarize` is inspection-only; M2's normalizer parses facts into
  `FinancialFact` rows in PostgreSQL.
- Live SEC fetch is exercised once per milestone (by design); CI/regression
  tests stay on `MockTransport`.

### Next milestone

**M2 — Financial Data Processing:** XBRL concept mapping (per-company fallback
chains handling the drift confirmed above), period detection (annual vs
quarterly vs YTD→standalone split), `financial_engine.py` (growth, margins,
ROA, FCF, health ratios — pure and unit-tested), SQLAlchemy models + Alembic
migration, PostgreSQL repositories.
