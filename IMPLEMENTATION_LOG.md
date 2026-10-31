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

---

## M2 — Financial Data Processing

**Status:** ✅ COMPLETE
**Date:** 2026-10-31

### What was built

| File | Purpose |
|---|---|
| `app/schemas/company.py` | Single source of truth: `Metric` (15 canonical metrics), `PeriodType` (INSTANT/ANNUAL/QUARTERLY/YTD), `FiscalPeriod` (FY/Q1–Q4), `INSTANT_METRICS`/`DURATION_METRICS` frozensets. Imported by normalizer, engine, DB, and (later) API so a string typo can't silently produce empty queries |
| `app/ingestion/data_cleaner.py` | **Correctness heart.** XBRL concept mapping with priority chains (all concepts merged, highest-priority-per-period wins); period classification by date arithmetic (350–380d → annual, 80–100d → quarterly, else YTD); fiscal-year-end-month inference from annual facts; YTD→standalone-quarter splitting (Q2=H1−Q1, Q3=9M−H1, Q4=Annual−9M, missing input → NO output); latest-filed-wins dedup with restatement detection (keeps both values, flags, never hides). Pure functions, no I/O |
| `app/services/financial_engine.py` | Deterministic metric engine: YoY growth (abs() denominator for sign-correct loss recovery), margins, ROA, FCF, current ratio, debt-to-assets, earnings-cashflow divergence. Every metric carries formula name + input values + source accession numbers + timestamp. Division-by-zero → `value=None` with recorded reason, never inf/nan |
| `app/database/models.py` | SQLAlchemy 2.0 typed models: `Company`, `FinancialFact` (as-filed facts with restatement columns `is_restated`/`value_original`), `FinancialMetric` (calculated metrics with `formula`/`inputs_json`/`source_accessions_json`). Only portable column types so tests run on SQLite while production uses PostgreSQL |
| `app/database/postgres.py` | Cached engine (one connection pool per process), context-manager session (commit/rollback/close automatic), `create_tables()` for dev, `reset_engine()` for tests |
| `app/database/repositories.py` | `CompanyRepository` (upsert), `FactRepository` (full-replace per company — idempotent), `MetricRepository` (upsert keyed on company+metric+period). Single place holding DB query knowledge |
| `scripts/process_company.py` | End-to-end CLI: resolve → fetch/load raw → normalize → store facts → calculate metrics → store metrics → print report with recent annual metrics for sanity-checking |
| `scripts/verify_database.py` | DB verification CLI (uses SQLAlchemy, not psql — no password in shell commands): totals, per-company metrics with formulas, restated-fact samples with citation URLs, derived-quarter listing |
| `migrations/` + `alembic.ini` | Alembic initialized; `env.py` reads URL from app `Settings` (never from .ini), wires `Base.metadata` for autogenerate; initial migration `b33bfba96b16` (3 tables, 4 indexes, FKs, unique constraint, working downgrade) |
| `tests/test_data_cleaner.py` | 25 tests: period classification, fiscal-year rules (incl. comparative-mislabel regression), FYE-month inference, concept fallback, restatement detection, YTD splitting (incl. missing-input and negative-value guards), duplicate-quarter prevention |
| `tests/test_financial_engine.py` | 14 tests, all hand-verified golden values (e.g. loss halving −50→−25 = +50% growth, MSFT FY2025 margins) |
| `tests/test_database.py` | 8 tests on SQLite in-memory: idempotent re-ingestion (no duplicate rows), stale-fact removal, restatement persistence, metric upsert overwrite, provenance column survival |

### Key design decisions

1. **Fact vs Metric separation in the database.** `FinancialFact` = what the
   company reported (VERIFIED FACT layer); `FinancialMetric` = what we
   calculated (CALCULATED METRIC layer). This maps directly to the report
   requirement: every number shown in the UI will be tagged with its layer.
2. **All chain concepts merged, priority-per-period wins.** Not "pick the
   first tag with data" — see Bug 3 below, which real data proved wrong.
3. **Period classification by date arithmetic, never labels.** SEC's `fp`/`fy`
   fields describe the *filing*, not the *fact* (comparative columns inherit
   the filing's labels). Date spans are ground truth.
4. **Fiscal-year rules differ by period type** (Bug 2 below):
   annual → end-date year; quarterly/YTD → date arithmetic against inferred
   fiscal-year-end month.
5. **Missing input → no output.** Quarter-splitting emits nothing when an
   input is absent or a derived value fails sign-sanity. Correctly incomplete
   beats confidently wrong.
6. **Restatements are surfaced, not hidden.** Later filing with different
   value → latest wins, `is_restated=True`, `value_original` preserved,
   change logged with both accession numbers.
7. **Provenance on every row.** Facts carry concept/form/accession/URL;
   metrics carry formula/inputs/accessions/timestamp. Any displayed number is
   reproducible by hand — the audit requirement for financial research.
8. **Full-replace fact ingestion (idempotent).** Re-running always converges
   to the same state; trade-off (loses per-run derived-quarter history) is
   documented — raw filings on disk remain the audit trail.

### Real-data bugs found & fixed (the important part)

**Every one of these was caught by running against LIVE SEC data or by a
failing golden test — none would have been found by assumptions alone.**

1. **Instant-metric slot lookup (test failure).** ROA/debt-to-assets mixed
   duration facts (net income) with instant facts (total assets), but looked
   up assets in the annual slot where instants never live → always None.
   Fixed with `_get_instant()` helper forcing `PeriodType.INSTANT` for
   balance-sheet metrics.
2. **Off-by-one fiscal year for quarters (live-data verification).** Before
   the fix, MSFT FY2025 Q4 showed $101.3B instead of the true $76.4B and
   FY2025 Q1 showed FY2024's $56.5B. Root cause: a 10-Q's *comparative*
   prior-year quarters carry the *filing's* `fy` label (Sep-2023 comparative
   inside the FY2025-Q1 10-Q is labelled fy=2025). Fixed by deriving quarterly
   fiscal years from period-end date + inferred fiscal-year-end month
   (`end.year + (1 if end.month > fye_month else 0)`), inferring fye_month
   as the modal month of annual-fact end dates. Verified: MSFT (June FY-end),
   AAPL (September), NVDA (January) all produce quarters summing exactly to
   their annual totals.
3. **Concept-drift direction is per-company (live-data verification).**
   MSFT uses the *modern* ASC-606 tag for recent years (chain order:
   modern-first works). NVDA does the **opposite** — modern tag for older
   years, generic `Revenues` for FY2023–26. The original "first chain tag
   with data wins" hid NVDA's entire recent history (most recent annual
   revenue in DB was FY2022 $26.9B; true FY2026 revenue is $215.9B). Fixed
   by iterating ALL chain concepts in priority order with a per-period
   `claimed` map: highest-priority concept wins each period, later concepts
   fill gaps. After fix: NVDA FY2023 $27.0B / FY2024 $60.9B / FY2025 $130.5B
   / FY2026 $215.9B — all present, MSFT/AAPL unchanged (regression-checked).
4. **Duplicate as-filed + derived quarters (live-data verification).** Q2/Q3
   appeared twice (once filed directly by MSFT, once derived by the
   splitter) — would duplicate points on charts and corrupt time-series
   queries. Fixed by passing existing quarterly facts into the splitter so
   it never derives a period that is already as-filed. Result: 278 → 93
   derived quarters for MSFT, each quarter stored exactly once.
5. **Q2 derivation miss (failing golden test).** A 90-day Q1 fact is
   *correctly* classified QUARTERLY, so the splitter (which only indexed
   YTD facts) couldn't find its Q1 input → Q2 never derived. Fixed by
   letting the splitter use existing quarterly-Q1 facts as inputs.
6. **Repository upsert bugs (failing DB tests).** `upsert_metrics` forgot
   `fiscal_year`/`fiscal_period` (NOT NULL + part of unique identity — would
   crash PostgreSQL) and assigned an ISO *string* to a `DateTime` column.
   Both surfaced by SQLite tests reading the error output.
7. **Annual comparative dedup (failing golden test).** FY2024 period
   re-reported in the FY2025 10-K carries `fy=2025` → two "different" fiscal
   years for one period. Fixed by using end-date year for annual facts.

### Tests performed (all executed, outputs shown in session)

1. **Unit suites:** `pytest -q` → **76 passed** (25 cleaner + 14 engine +
   8 database + 25 M1 + 3 M0 + 2 conftest/smoke).
2. **End-to-end live run:** `python scripts/process_company.py MSFT AAPL NVDA`
   → exit 0; MSFT 1377 facts / AAPL 1377 / NVDA 1300.
3. **DB verification:** `python scripts/verify_database.py` → 3 companies,
   4054 facts, 3261 metrics.
4. **Hand-verified values vs published figures:**
   - MSFT FY2025: revenue growth 14.93%, operating margin 45.62%
     (128,528/281,724), net margin 36.15%, FCF $71.6B ✓
   - MSFT FY2025 quarters: 65,585+69,632+70,066+76,441 = **281,724M = annual
     exactly** ✓ (no double-counting)
   - NVDA FY2026 quarters: 44.1+46.7+57.0+68.1 = **215.9B = annual** ✓
   - AAPL FY2025: revenue growth 6.43%, operating margin 31.97%, FCF $98.8B ✓
5. **Alembic:** initial migration generated (autogenerate detected all 3
   tables + 4 indexes + unique constraint), applied via `upgrade head`,
   `alembic current` → `b33bfba96b16 (head)`; pipeline re-run after migration
   produced identical counts (schema equivalence proven).
6. **Regression checks:** after each fix, re-ran full suite AND re-verified
   MSFT/AAPL values unchanged before accepting the NVDA fix.

### Restatement observations (real accounting history)

- 338–393 restatements per company — mostly **gross profit** restated between
  filings (visible in verify output, e.g. MSFT FY2014 $11.84B → $14.43B in a
  later 10-K/A). Both values retained; every row cites its accession URL.
- NVDA FY2009 operating-cash-flow YTD inputs misaligned → negative derived
  value **rejected with a warning** (correctly incomplete, not wrong).

### Current limitations / next steps

- DB stores USD *full values* as Float; raw JSON on disk remains the exact
  audit trail (documented trade-off: JSON numbers are already lossy).
- Instant (balance-sheet) facts are keyed to fiscal slots from SEC `fp`
  labels; quarterly balance-sheet history is present but M3+ should verify
  slot alignment per company before charting.
- Alembic `script.py.mako` docstring is default; fine for now.
- No incremental ingestion yet — every run re-fetches raw payload (fast, and
  keeps the pipeline idempotent; M4 will add filing-level diffing).

### Next milestone

**M3 — Peer comparison & anomaly detection:** cross-company metric tables,
peer-relative z-scores, trend/acceleration signals, investigation-flag rules
(all labeled as signals, never fraud verdicts).
