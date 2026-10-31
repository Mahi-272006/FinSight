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

---

## M3 — Peer Comparison & Anomaly Detection

**Status:** ✅ COMPLETE
**Date:** 2026-10-31

### What was built

| File | Purpose |
|---|---|
| `app/services/peer_comparison.py` | Cross-company comparison: `CompanyMetricPoint`, `PeerRanking` (rank, mean/median/std, **leave-one-out z-scores** with per-company reference counts), `compare_all_periods` grouping. `is_z_statistically_meaningful()` returns False below 8 reference points so the UI can never present a 2-point z-score as a calibrated statistic |
| `app/services/trend.py` | Pure math: `linear_slope` (hand-rolled least squares, no numpy dependency for 3-line formula), `trailing_streak` (consecutive up/down runs at the end of a series) |
| `app/services/anomaly_detection.py` | 4 deterministic rules, each with documented + unit-tested thresholds and sign-aware guards: `check_earnings_cashflow_divergence` (elevated ≥20pp, high ≥50pp, requires positive NI growth), `check_receivables_outpacing_revenue` (≥25pp/≥50pp over revenue growth), `check_margin_compression_streak` (≥3/≥5 consecutive FY declines), `check_growth_deceleration` (≥15pp/≥30pp drop, prior growth must be ≥10%). Every signal carries headline, plain-language explanation ending in "NOT a finding of misstatement", full evidence dict (inputs, thresholds, formula), and source accessions |
| `app/services/anomaly_orchestrator.py` | `CompanySeries` (metric → year → value series assembled by caller) + `detect_signals` wiring all rules across all available years; missing data never fires a signal |
| `app/database/models.py` (extended) | `InvestigationSignal` table: unique on (cik, signal_type, fiscal_year, fiscal_period), severity, evidence_json, source_accessions_json |
| `app/database/repositories.py` (extended) | `SignalRepository` (upsert, full-refresh-per-company, filtered queries); `MetricRepository.upsert_metrics` now **refuses None-valued metrics** (see bug 4) |
| `migrations/versions/d872fcb82e52_...` | Alembic migration: `investigation_signals` table + `ix_signals_company` index; chained from `b33bfba96b16`, applied, at head |
| `scripts/analyze_peers.py` | M3 CLI: loads metrics from DB → prints annual-only comparison table (ranks + z-scores + "indicative only" labels) → runs all rules → full-refreshes signals per company → prints report with signal-vs-verdict framing |
| `app/services/financial_engine.py` (extended) | `accounts_receivable_growth_yoy` added to instant-slot loop (FY slots only): the AR rule needed this metric WITH provenance rather than ad-hoc arithmetic in the detector |
| `tests/test_peer_comparison.py` | 10 tests: hand-verified leave-one-out z-scores (A=0.10/B=0.20/C=0.30 → z = ∓2.1213/0), zero-std guard (z=None, never inf), small-sample flag, singleton periods skipped |
| `tests/test_trend.py` | 13 tests: slope golden values (±2.0 exact lines, 0.0 flat, None for <2 points), trailing-streak behavior incl. broken runs and flat tails |
| `tests/test_anomaly_detection.py` | 21 tests: **two kinds per rule** — trigger cases (fires with right severity/evidence) AND silent cases (below threshold, wrong direction, missing data). False-positive guards are as mandatory as triggers |
| `tests/test_anomaly_orchestrator.py` | 6 tests: quiet-company silence, multi-rule years, chronological sorting, partial-data graceful degradation |
| `tests/test_database.py` (extended) | 5 SignalRepository tests (upsert no-duplicates, full-refresh wipes stale, JSON round-trip, type filter) + None-metric-refusal regression test |

### Key design decisions

1. **Leave-one-out z-scores.** A company is scored against the *other*
   peers only. Including the subject in its own reference mean/std pulls the
   mean toward it and shrinks every z toward zero — subtle bias that flatters
   the largest company. Golden test pins the math by hand (values 10/20/30 →
   z = −3.0/0.0/+3.0 against 2-point references).
2. **Small-sample honesty as a first-class feature.** With n=3 companies the
   leave-one-out reference has 2 points — statistically meaningless as a
   z-score. `is_z_statistically_meaningful()` (False below 8 reference
   points) gates the report header: "Z-SCORES ARE INDICATIVE ONLY … ranks and
   raw values are the trustworthy comparison at n=3." The machinery is built
   correctly for larger peer sets; the *label* is what keeps n=3 honest.
3. **Anomalies are investigation signals, never verdicts.** Each rule
   documents its threshold and rationale; each signal's explanation names
   legitimate alternative causes (working-capital swings, mix shift, one-time
   boosts) and ends with "NOT a finding of misstatement". Severity is a
   triage label (elevated/high), not a probability.
4. **Sign-aware rules.** Divergence fires only when income growth outruns
   cash flow AND income is actually growing; AR-gap fires only when AR grows
   faster than revenue AND AR itself grew. The reverse directions are
   conservative accounting or collection improvements — no signal. Guarded
   by dedicated silent-case tests.
5. **Missing data never fires.** Every rule returns None/skips on absent
   inputs. A gap is not evidence; firing on gaps would train users to ignore
   the tool.
6. **Peer stats computed on demand, signals persisted.** Peer statistics
   depend on the peer set — storing them makes them stale when peers are
   added (and stale stats are *less* reproducible than recomputed ones).
   Discrete signals (severity, evidence, timestamp) are worth persisting.
7. **Annual-only comparison table.** Quarterly cross-company comparison is
   misleading with Jun/Sep/Jan fiscal year-ends (different calendar windows,
   seasonal quarters) — and caught live (bug 2 below).
8. **AR growth lives in the deterministic engine, not the detector.**
   Computing it ad-hoc in the orchestrator would bypass the provenance
   trail; the engine's instant-slot loop emits it with formula + inputs +
   accessions like every other metric.

### Rule scope trimmed by evidence (restatement clustering — CUT)

The planned 5th rule (restatement-count clustering) was cut after live
measurement showed it would be calibrated on noise:

- Restatement events per company are **stable at ~21/year** across all three
  companies (17 years of history; per-year range 6–51 but recent 3 years
  ~21/21/21 per company). This is *routine comparative reclassification*
  (gross-profit restatements dominate), not unusual clustering.
- A count threshold would fire always or never — pure noise either way.
- A materiality filter (>5% value change) was tested next and **also rejected**:
  the largest "restatements" (cash $426M → $1.8B, +315%) are era differences
  in concept *definitions* (restricted-cash inclusion in early-2000s
  filings), not accounting revisions. Calibrating on them would produce
  confident false signals.

Documenting *why a rule was excluded* is as valuable as the rules included —
it is the same evidence-first discipline that cut CUDA wheels (M0) and
reversed the concept-priority direction (M2).

### Real-data bugs found & fixed (during M3)

1. **`trailing_streak` zip arguments reversed (failing golden tests).**
   `zip(reversed(values[1:]), reversed(values[:-1]))` paired (later, earlier)
   so every direction inverted — a declining series returned down-streak 0,
   a *rising* series returned down-streak 2. Caught by 9 failing tests
   before any live run. Fixed by swapping arguments; hand-traced
   [30,28,26,24]→3, [10,20,30]→0, [30,28,29,27]→1 after the fix.
2. **FY2027 partial periods displayed as annual (caught by reading live
   output).** The comparison table originally didn't filter by period type;
   incomplete-year quarterly slots appeared beside complete years with no
   label — a partial-year number masquerading as annual, the exact failure a
   financial tool must never have. Fixed: `period_type == "annual"` +
   `fiscal_period == "FY"` filter, "Only complete ANNUAL (FY) periods shown"
   in the header.
3. **Console mojibake (caught by reading live output).** Unicode
   em-dashes/σ rendered as `â€”`/`â€" under the Windows console codepage.
   Data unaffected (display-only), but a financial report that garbles its
   own headers undermines confidence in its numbers. All CLI output now
   ASCII-only (z-scores print as `(z=+17.21)`).
4. **460 NULL-value metric rows = 14% of the table (found while verifying
   AR-growth storage).** The engine legitimately emits `value=None` +
   `reason` when inputs are missing — good in-memory design — but the
   repository persisted those rows anyway, and the schema has no `reason`
   column, so the rows carried zero information while forcing every consumer
   to defensively filter (a throwaway verification script tripped on one).
   Fixed at the persistence boundary: `upsert_metrics` drops None-valued
   metrics (documented in the docstring); the 460 live rows were deleted
   (3324 → 2864); regression test pins the refusal.
5. **Test expectation wrong, code right (tied values).** A test expected
   z-scores for all three companies when two were tied; the tied subject's
   reference std is exactly 0, so z=None by design (never inf). Corrected
   the test to assert the divide-by-zero guard it was actually demonstrating.

### Tests performed (all executed, outputs shown in session)

1. **Unit suites:** `pytest -q` → **135 passed** (was 76 at M2 close; +59).
2. **Live analysis run:** `python scripts/analyze_peers.py` → exit 0.
   Comparison table FY2025/FY2026 with honest z labels; **8 signals
   persisted** (full-refresh per company, idempotent re-runs verified).
3. **Hand-verified sanity checks on live output:**
   - MSFT: **0 signals** across 19 years — genuinely stable metrics, the
     correct false-positive-free result.
   - NVDA FY2023 growth deceleration 61.4%→0.2% (the crypto/demand crash)
     and FY2026 normalization 114.2%→65.5% both flagged HIGH.
   - AAPL FY2025 divergence (NI +19.5% vs OCF −5.7%, gap 25.2pp) — matches
     the real FY2025 working-capital story.
   - AR rule correctly silent: NVDA FY2025 AR +130.7% vs revenue +114.2%
     (gap 16.5pp < 25pp threshold) — the rule sees the data and *chooses*
     not to fire.
   - FY2025 leave-one-out z: NVDA revenue growth z=+17.21 (extreme outlier
     vs MSFT/AAPL single digits) — extreme value, honest label.
4. **DB verification:** `investigation_signals` = 8 rows, each with complete
   evidence JSON (thresholds + formula + inputs) and source accession
   provenance; Alembic at head `d872fcb82e52`.
5. **Engine extension verified live:** `accounts_receivable_growth_yoy`
   rows regenerated (MSFT 1234→1255, AAPL 1223→1244, NVDA 1200→1221 metrics
   stored; 17 non-null FY rows per company).

### Current limitations / next steps

- Peer set n=3: z-scores indicative only by design; adding GOOGL/AMZN (M7+)
  begins to make them meaningful (still <8 reference points at n=5).
- Anomaly rules run on ANNUAL series only; quarterly seasonality needs
  separate handling (rule inputs already accept any period slot).
- Thresholds are hand-set from domain knowledge, not calibrated on
  distributions — defensible at n=3 (calibrating on 3 companies would be
  overfitting); revisit when the peer set grows.
- `check_margin_compression_streak` emits a signal per qualifying year
  (timeline visibility) — the UI (M10) must group them per streak episode.
- Trend module has slope/streak only; inflection-point detection and
  trailing-window regression are future extensions.

### Next milestone

**M4 — Filing RAG pipeline:** download 10-K/10-Q filing documents via the M1
`SECClient`, extract/segment text (MD&A, risk factors), chunk + embed with
local sentence-transformers, persist to ChromaDB — the retrieval substrate
the LLM analyst (M5/M6) will ground its interpretations in.

---

## M4 — Filing RAG Pipeline (download → extract → segment → chunk → embed → store)

**Status:** ✅ COMPLETE

### What was built

| File | Role |
|---|---|
| `app/ingestion/sec_client.py` (extended) | `get_submissions()` (filing index API) + `get_bytes()` (byte-faithful document download); retry loop refactored into `_get_response()` — the single choke point for ALL SEC traffic (M1's 25 tests re-verified green after the refactor) |
| `app/ingestion/filing_downloader.py` | `FilingRef` dataclass; `parse_recent_filings()` (submissions parallel-arrays → typed refs, ragged arrays tolerated); `select_filings()` (form filter, newest-first, limit after filter); archive URL builder (accession dashes stripped) |
| `app/ingestion/filing_text.py` | The heart: `extract_paragraphs()` (lxml/bs4; strips script/style/noscript/`ix:header`/`display:none` XBRL blocks) → `segment_sections()` (Item headers with 4 guards + same-item absorption) → `chunk_document()`/`chunk_section()` (2400-char chunks, 300-char overlap, giant-paragraph sentence split) |
| `app/services/embeddings.py` | `Embedder` protocol; `SentenceTransformerEmbedder` (all-MiniLM-L6-v2, 384-dim, L2-normalized, lazy load); `FakeEmbedder` (crc32 bag-of-tokens, process-stable, unit-norm) |
| `app/services/vector_store.py` | ChromaDB wrapper: single `filing_text` collection, cosine space, deterministic ids `{accession}:{chunk_index}`, flat-dict `where` → explicit `$and`, `delete_filing()` |
| `app/ingestion/rag_pipeline.py` | `process_filing()` orchestration: cache-first download → extract → segment → chunk → batched embed → delete-then-insert; `FilingStats` per-filing outcome (one filing failing never kills the run) |
| `scripts/ingest_filings.py` | CLI: `python scripts/ingest_filings.py MSFT [..] [--limit N] [--forms ...]`, per-filing OK/FAIL lines, exit code aggregates failures |
| `scripts/search_filings.py` | Retrieval demo CLI with `--k/--ticker/--section/--form` filters, prints provenance per hit |

### Key design decisions

- **One choke point for SEC traffic.** The retry/backoff/rate-limit logic
  that lived in `_get_json` was refactored into `_get_response(url, accept)`
  before adding document downloads — JSON APIs and binary documents share the
  exact same policy, so rate limiting cannot drift between endpoints.
- **Disk cache = idempotency + audit trail.** Raw HTML saved byte-for-byte
  under `data/filings/{cik}/{accession}/{doc}` before parsing (M1's rule,
  applied to documents). Re-running re-uses cached bytes → zero SEC traffic,
  and any questionable extraction is diffable against its source bytes.
- **Delete-then-insert per filing.** Upsert-by-id alone cannot remove chunks
  whose ids a *changed chunker* would no longer regenerate (orphans). Found
  the hard way while re-ingesting after segmentation fixes; now
  `store.delete_filing(accession)` runs before every upsert.
- **Deterministic ids `{accession}:{chunk_index}`** → re-ingest replaces, never
  duplicates (same idempotency contract as M2 fact ingestion).
- **Segmentation is best-effort with an honest fallback**, biased toward NOT
  matching: a missed header loses a label (text merges, retrieval still
  works); a wrong header lies about every chunk after it. Four guards, all
  derived from inspecting a real MSFT 10-K: TOC dot leaders, trailing page
  number, empty/digit-only title, 200-char line cap; plus same-item repeat
  absorption (per-page running headers) with re-entry allowed after a
  different item.
- **Section names come from the header's TITLE, not the item number** (see
  bugs below — item numbers are form-dependent and the number-based map
  demonstrably lied about live 10-Q content). Fallback is generic `item_N`:
  we know which item it is and invent no content claim we can't support.
- **Single collection + per-chunk metadata** rather than per-company
  collections: metadata filters answer "only MSFT MD&A" just as well, while
  keeping the schema fixed as companies are added.
- **L2-normalized embeddings ↔ cosine space**, set at collection creation.
  Normalization lives with the producer (embedder), so any consumer — Chroma,
  numpy, a test — sees unit vectors; mixing storage space and normalization
  would silently degrade retrieval.
- **Chunk overlap of ~300 chars** for boundary continuity; cost is duplicate
  retrieval at seams — accepted, documented, visible in results.
- **No fiscal-year labeling on chunks.** Chunks cite accession numbers, and
  accessions already join to `FinancialFact` (M2) — fiscal context comes from
  the facts table instead of a duplicated label that could drift.
- **Amendments (`10-K/A`, `10-Q/A`) excluded** — they restate the original
  document and would duplicate sections in retrieval.
- **`filings.recent` only** (≈1000 most recent); older filings in
  `filings.files` not paginated — 20 years of 10-K/10-Q is ~100 filings per
  company, far beyond demo needs. Documented limitation.
- **Tests never load torch.** `SentenceTransformerEmbedder` loads lazily on
  first use; unit tests inject `FakeEmbedder` (crc32, not salted `hash()` —
  salted hashing would make vectors differ per process and tests flaky).
  The live run exercises the real model.

### Real-data bugs found & fixed (during M4)

1. **`chunk_section` walked the FULL paragraph list instead of the section's
   slice.** Every section would have chunked the *entire document*
   (duplicated text across sections, negative `para_start`). Caught by
   `test_para_indices_reference_filing_wide_list` before a single live
   ingestion — pure-function testing paid for itself here.
2. **Over-segmentation on the live 10-K: 145 sections** (17 `mdna`,
   13 `business`, 41 `financial_statements` for a document that has one of
   each). Diagnosed by printing every offending header line:
   `line 60: 'Item 1.'` = TOC cells (title in a separate table cell),
   `line 159/181/200: 'Item 1'` = per-page running headers. Fixes: (a) a
   header must carry a non-empty non-digit title; (b) the trailing-page-number
   regex `_TOC_PAGE_RE` existed but was **never wired in** — now applied;
   (c) same-item repeats absorbed while inside that item's section. Result:
   10-K 145 → **24 sections**, 10-Q 77 → **10 sections**.
3. **Number-based section naming lied about every 10-Q.** Live header lines
   vs. labels: `ITEM 1. FINANCIAL STATEMENTS` → labeled `business`;
   `ITEM 2. MANAGEMENT'S DISCUSSION...` → labeled `properties`;
   `ITEM 3. QUANTITATIVE AND QUALITATIVE...` → labeled `legal_proceedings`.
   Root cause: item numbers are form-dependent (10-K Item 2 = Properties,
   10-Q Part I Item 2 = MD&A, 10-Q Part II Item 2 = Unregistered Sales).
   Fix: `_TITLE_RULES` keyword mapping over the header's own title, matched
   lowercase, apostrophe-free patterns (extracted titles carry varying
   apostrophe encodings), generic `item_N` fallback. Re-verified live across
   all three companies' 10-Qs: zero mislabels.
4. **Orphan chunks after a chunking-code change** — upsert-by-id leaves stale
   ids behind when the new parse generates fewer/different ids. Fixed with
   delete-then-insert; test simulates an orphan id and asserts it's removed.
5. **Chroma `EphemeralClient` shares one in-memory DB per process** — test
   data leaked between tests (count 8 instead of 1). Fixed with per-test
   `tmp_path` PersistentClient, which also exercises the real disk path.
6. **`Settings` validator rejected the test fixture's `example.com` email.**
   Tests now use a `.local` address — honest, because MockTransport
   intercepts every request and no SEC traffic ever happens.
7. **A "bug" that was actually the test's fault (twice):** a 181-char
   cross-reference line sat *under* the 200-char guard so the parser matched
   it correctly; and a rejected TOC line at index 0 correctly yields a
   leading `unknown` section. Both fixed by correcting expectations and
   adding explicit boundary tests — the guard was never broken.
8. **`XMLParsedAsHTMLWarning` noise** — filings are XHTML-flavored
   (`<?xml?>` declaration over HTML that only the HTML parser recovers
   from). That *specific* warning is filtered with justification; all other
   warnings stay visible.

### Tests performed (all executed, outputs shown in session)

- `python -m pytest -q` → **202 passed** (135 at M3 close → 202; +67 M4
  tests: extraction guards, segmentation guards incl. boundary cases,
  chunking invariants, submissions parsing, client extensions over
  MockTransport, pipeline wiring, vector store filters/idempotency).
- Live `ingest_filings.py MSFT --limit 2` (first run): model downloaded
  (~90 MB one-time), 387 chunks stored, exit 0.
- After segmentation fixes, re-run: both filings `[cached HTML]` (zero SEC
  document traffic), 10-K 145→24 sections, store exactly **283** — old 387
  replaced, proving delete-then-insert live.
- Live `ingest_filings.py MSFT AAPL NVDA --limit 2` → **6 filings, 542
  chunks, 0 failed**, exit 0 (MSFT replaced in place; AAPL/NVDA downloaded).
- Live `search_filings.py` demos, all correct with provenance:
  - "export controls and international sales restrictions" → top-3 NVDA
    `risk_factors` passages (distances 0.225/0.262/0.286).
  - same query with `--section mdna` → only MD&A chunks across NVDA/AAPL.
  - "cybersecurity and data protection risks" → MSFT `risk_factors` +
    `cybersecurity` (Item 1C, named from its title) — no cross-section bleed.
- Post-fix 10-Q label audit across all three companies: `financial_statements`,
  `mdna`, `market_risk`, `controls_and_procedures`,
  `unregistered_sales_of_equity`, `mine_safety_disclosures` — correct.

### Current limitations / next steps

- Section detection is header-regex based; filings with unusual formatting
  degrade to larger `unknown`/merged sections (never wrong labels by design,
  but a heading-styled `<h*>` heuristic could help someday).
- One collection, no per-filing *score* recalibration: raw cosine distances
  are comparable within a query but not across queries.
- `search_filings.py` is a script, not an API — M9 wraps retrieval behind
  FastAPI; the LLM agent (M5/M6) will call `VectorStore.search` directly.
- Chunk overlap duplicates boundary text — retrieval can return two chunks
  containing the same sentence; dedup/reranking is a future (M8-era) concern.

### Next milestone

**M5 — Multi-provider LLM manager:** Groq primary / Gemini fallback / Ollama
optional behind one interface, with timeouts, provider failover, and prompt
construction that keeps LLMs out of arithmetic entirely (all financial math
stays in the deterministic, provenance-carrying engine).

---

### M4 — Technical interview corner (5 questions + answers)

**Q1. Your section segmentation looked right in tests but produced 145
sections on a real 10-K. What actually happened, and what does that teach?**
The fixtures reflected *my* mental model of a filing, not filings themselves.
Real documents carry two things no fixture had: TOC rows where "Item 7." sits
alone in a table cell (title in the next cell), and running page headers
repeating "Item 1" at the top of every page. I diagnosed it by printing every
offending header line rather than guessing, then fixed it with guards derived
from that evidence (title-required, trailing page number, same-item
absorption). The lesson: **unit tests prove your rules are implemented;
only live data proves your rules are right.** Also, one guard
(`_TOC_PAGE_RE`) existed in code but was never wired in — dead code that
looked like protection.

**Q2. Item numbers are the obvious key for naming sections. Why did you
throw that away?**
Because the same number means different things per form: 10-K Item 2 is
Properties, 10-Q Part I Item 2 is MD&A, 10-Q Part II Item 2 is Unregistered
Sales. The number-based map labeled a live 10-Q's MD&A as "properties" and
its financial statements as "business" — a *wrong label* is worse than no
label, because every consumer downstream (filters, citations, the LLM) trusts
it. Naming now reads the header's own title text — the evidence the filer
itself wrote — and falls back to a generic `item_N` when the title carries no
recognized keyword. That fallback is deliberate honesty: we know which item
it is; we don't invent a content claim we can't support.

**Q3. Why does every SEC request — JSON and document bytes — flow through
one `_get_response` method?**
Because the SEC's rules (identified User-Agent, ≤10 req/s, retry etiquette)
are properties of the *traffic*, not of individual endpoints. Two code paths
means two chances for drift: rate limiting tuned in one, not the other; a
retry policy updated in one, forgotten in the other. One choke point makes
the policy a single point of truth, and it's exactly where tests hook in via
`httpx.MockTransport`. The refactor also kept M1's 25 tests green — behavior
had to be provably unchanged, so the tests doubled as a safety net for the
refactor.

**Q4. You use idempotent upserts everywhere — why did filings still need
delete-then-insert?**
Upsert is idempotent only when regeneration is *deterministic*: same input,
same ids. But chunking is code, and when I fixed segmentation (145 → 24
sections), the new parse generated different, fewer ids — upsert would have
replaced the shared ids while leaving every stale id the new code no longer
produces. Those orphans would surface as retrieval hits from an old parse
with no way to tell them apart. So "re-process this filing" is now defined as
**replace the filing's entire chunk set**: `delete_filing(accession)` before
upsert. A test plants an orphan id and asserts it's gone. The deeper lesson:
idempotency contracts must state *what happens when the producer changes*,
not just when it runs twice.

**Q5. Embeddings are behind an interface with a fake implementation. Isn't a
fake testing nothing valuable?**
The fake tests the *wiring*, which is where the real bugs were: a fake that
hashes tokens with crc32 (never Python's salted `hash()`, which differs per
process) gives deterministic, unit-length vectors where identical texts score
distance 0 and shared-token texts rank above disjoint ones — enough to prove
download → extract → chunk → embed → store → search end-to-end, including
metadata filters and the failure paths, in milliseconds with no model
download. What the fake *cannot* test — retrieval quality, model behavior —
goes to the golden dataset (M8), not to unit tests. Separately, the real
embedder is exercised live: 542 chunks from 6 real filings, and the query
demos returned the right sections at sensible distances. Fake for wiring,
golden set for quality, live run for integration — three layers, each
answering a different question.

---

## M5 — Multi-provider LLM Manager (Groq / Gemini / Ollama + grounded prompting)

### What was built

- `app/services/llm_providers.py` — the contract and the three adapters:
  - Error taxonomy: `LLMError` → `LLMConfigurationError` (broken chain,
    propagates immediately) / `LLMProviderError` (+ `LLMRateLimitError`,
    `LLMAuthError`, `LLMTimeoutError`, `LLMNetworkError`, each carrying
    `.provider` and `.status_code`) / `LLMAllProvidersFailed` (carries every
    `(provider, cause)` from the whole chain).
  - `LLMResponse` provenance dataclass (text, provider, model, in/out tokens,
    latency_ms) — the model that produced an LLM INTERPRETATION travels with
    it, the same provenance discipline M2 applied to numbers.
  - `LLMProvider` protocol (the failover seam); `GroqProvider` /
    `GeminiProvider` over the installed `langchain-groq` /
    `langchain-google-genai` runnables (injectable for tests);
    `OllamaProvider` hand-rolled over injectable httpx.
  - `_classify()` normalizes any provider exception into the taxonomy via
    duck-typed status attributes (`status_code`, `code`, and
    `.response.status_code` for httpx HTTPStatusError) plus class-name
    heuristics — no isinstance chains across four libraries.
  - `build_providers()` — the zero-cost guard: skips providers whose key is
    missing/unusable (placeholder markers count as unset), and an emptied
    chain raises `LLMConfigurationError` with free-tier setup URLs. No code
    path exists to any paid API.
- `app/services/llm_manager.py` — `LLMManager.generate()` walks the chain in
  order; any provider failure is logged with its cause and fails over; all
  failures aggregate into `LLMAllProvidersFailed`. Context-manager closes
  providers. `build_manager(settings)` wires settings → chain → manager
  entirely offline.
- `app/services/prompting.py` — `SYSTEM_POLICY` (untrusted `<document>`
  content, NO ARITHMETIC, the four evidence tiers, citation form, honesty
  rules) and `build_grounded_messages()`: delimited context blocks with
  provenance attributes, budget truncation from the lowest-ranked hit up,
  embedded `</document>` escaped, and `used_hits` returned so callers can
  only cite what was actually provided.
- `scripts/ask_filings.py` — the M5 integration demo: M4 retrieval → grounded
  prompt → failover generate → answer + provider/model provenance + the
  exact citations the model saw.
- Config: `llm_provider_chain`, `llm_timeout_seconds`, `llm_max_tokens`,
  `llm_temperature`, `prompt_context_chars`; plus a field validator that
  anchors all relative data/log paths at `BACKEND_ROOT` (see bugs below).

### Key design decisions

1. **LangChain integrations behind our own narrow manager** — a mid-design
   revision, made *after reading requirements.txt*: `langchain-groq` and
   `langchain-google-genai` were already installed in M0 because M6's
   LangGraph consumes LangChain message objects natively. So the message
   type in this layer IS `langchain_core` messages (no conversion layer),
   provider quirks stay in the SDKs, and *our* failover policy, error
   taxonomy, zero-cost guard, and provenance stay ours. Ollama is the
   exception: `langchain-ollama` is not installed, its `/api/chat` is one
   stable JSON POST, and a hand-rolled adapter gets the cleanest
   MockTransport tests — zero new dependencies.
2. **`max_retries=0` on both SDK clients.** SDK-internal retries hide rate
   limits behind silent backoff and make failover timing nondeterministic.
   Failover IS the retry strategy: generation has no side effects, so
   trying the next provider is always safe. Accepted cost: a Groq 429
   spends Gemini quota instead of waiting — both are free, revisited in
   hardening.
3. **Failover on ANY provider failure, aggregated.** Even unexpected
   exceptions fail over (recorded with class name); configuration errors
   propagate immediately (the next provider cannot fix a broken chain).
   If the whole chain fails, one readable error lists every cause — all
   auth errors ⇒ misconfiguration; all timeouts ⇒ outage.
4. **Constructor-time generation knobs, not per-call.** Probing the real
   classes offline showed `temperature`/`max_tokens`/`timeout` are
   constructor fields (ChatGroq: `request_timeout`, `groq_api_key`,
   injectable `http_client`; ChatGoogleGenerativeAI: `timeout`,
   `max_output_tokens`, `google_api_key`), so `generate(messages)` is the
   entire call API — the narrowest seam that still works.
5. **No-arithmetic is architectural, not guaranteed.** The LLM gets no
   tools and is never asked to compute; numbers arrive pre-computed from
   the M2 engine with provenance. A prompt policy backs it up; M8's
   arithmetic-trap tests measure leaks (a live trap probe in this
   milestone already showed the model quoting figures and explicitly
   deferring the calculation to the deterministic engine).
6. **Citation honesty is a return-value property.** `build_grounded_messages`
   returns `used_hits`; the CLI prints exactly those — an answer may only
   cite context that was actually inside the prompt, which no prompt text
   can enforce by itself.

### Real-data bugs found & fixed (during M5)

1. **UnicodeEncodeError after a successful completion** — the model's answer
   contained U+2011 (non-breaking hyphen); the cp1252 Windows console
   crashed the script *after* paying 1.8 s of latency. Fixed at the stream
   level (`reconfigure(encoding="utf-8", errors="replace")`) — model output
   is data, garbled glyphs beat dead runs (same lesson as M4's ASCII policy,
   opposite tactic, because the content isn't ours to filter upstream).
2. **Silent truncation at 1024 output tokens** — `gpt-oss` spends completion
   budget on its reasoning channel too; the live answer was cut
   mid-citation at exactly 1024. Default raised to 2048 with the reason
   recorded in config.
3. **Citation example-leak** — the policy's concrete example
   (`[MSFT 10-K ... section:mdna]`) was *copied into* citations of
   `risk_factors` documents (model wrote `risk_factors:mdna`). Rewritten
   with placeholder form + "never values copied from any example".
4. **Retired model ID, twice, in one direction** — `gemini-2.5-flash` →
   404 "no longer available to new users". This failure *proved the
   machinery*: the all-fail run aggregated Groq's 401 and Gemini's 404
   into one perfect error. Google's suggested replacement
   `gemini-3.8-flash` then **hung** on generateContent from this network —
   a timeout-scaling probe (died at both the 30 s and the 90 s client
   deadline ⇒ server-side hold, not a short client timeout; the
   models-list endpoint answered instantly, so the endpoint is reachable
   and the key is authorized for that model) plus a 3-model sweep found
   `gemini-3.5-flash` answering in 7 s → verified default. Model lists
   drift AND availability varies by network: config-not-hardcode
   vindicated twice in one session.
5. **Classifier gaps exposed by live errors** — `httpx.HTTPStatusError`
   stores the status at `.response.status_code` (and *our own* Ollama
   adapter raises exactly that shape — a test now covers it);
   `google.api_core.DeadlineExceeded` contains no "timeout" substring →
   "deadline" added to the name heuristics.
6. **Relative Chroma path resolved against the CWD** (M0 leftover) —
   `.env` ships `CHROMA_PERSIST_DIRECTORY=./data/chroma_db`; from
   `backend/` the store showed all 542 chunks, from the repo root the same
   script saw an EMPTY store and created a stray `chroma.sqlite3` outside
   `backend/` (removed). Fixed with a `Settings` field validator anchoring
   all five Path fields at `BACKEND_ROOT`, plus regression tests including
   an explicit cwd-independence test. Found because an arithmetic-trap
   probe mysteriously retrieved 0 chunks.
7. Test-side errors, fixed honestly: langchain-core's `UsageMetadata`
   requires `total_tokens`; a Gemini-shaped test key contained the literal
   word "example" — which our own placeholder detector rejects.

### Tests performed (all executed, outputs shown in session)

- `python -m pytest -q` → **284 passed** (202 at M4 close → 274 after the
  72 M5 provider/manager/prompting tests → 284 after the 10 new Settings
  path-anchoring tests).
- Offline constructor probes (Rule 4 before coding): inspected real
  `ChatGroq`/`ChatGoogleGenerativeAI` field lists and built both with
  dummy keys — no network — to pin exact kwargs (`request_timeout`,
  `max_retries=0`, `max_output_tokens`, injectable `http_client`).
- Live happy path: `ask_filings.py "export controls..." --ticker NVDA
  --section risk_factors` → served by `groq/openai/gpt-oss-20b`, 1929 ms,
  complete answer with per-claim citations using actual document
  attributes, clean stderr (google FutureWarning filtered narrowly).
- Live failover: corrupted Groq key in memory only (`.env` untouched) →
  401 classified as auth → logged → `gemini-3.5-flash` served "OK"
  (22.2 s), exit 0.
- Live all-fail aggregation: both providers broken → single
  `LLMAllProvidersFailed` listing groq's 401 and gemini's cause.
- Live arithmetic trap: retrieved real NVDA financial-statement chunks,
  the model quoted both revenue figures verbatim, wrote the formula, and
  stated "The deterministic engine must perform this calculation; the
  result is not provided here."
- Gemini diagnostics: models-list probe (50 models authorized for the
  key), timeout-scaling probe (30 s/90 s), 3-model sweep — all recorded
  in the bug log above.

### Current limitations / next steps

- No in-provider retry/backoff: a Groq 429 fails over immediately instead
  of waiting out seconds; deliberate (determinism) but a hardening item.
- Gemini's live latency from this network (7–22 s) makes it a fallback of
  last resort in practice; Groq serves the happy path.
- No streaming — M10's UI may want SSE; the manager returns one response.
- The injection defense is structural (delimiters + policy text), not
  adversarially tested; M8's golden set should include injection probes.
- Model citation *rendering* is inconsistent (CJK brackets, occasional
  attribute mixups) — M6 must extract and verify citations
  programmatically, never trust the model's formatting.
- google.api_core warns that Python 3.10 is EOL (functional today; the
  warning is filtered only at the gemini import site). A venv upgrade to
  3.11+ is a project-level to-do, not an M5 change.

### Next milestone

**M6 — LangGraph research agent:** a stateful research loop over the M5
seams (retrieve → answer → follow-up retrieval on gaps), with citations
extracted and verified against the store, ready to sit behind the M9 API.

---

### M5 — Technical interview corner (5 questions + answers)

**Q1. You designed hand-rolled httpx adapters, then threw it away for
LangChain classes mid-mileage. Isn't that a wasted design?**
It was a design invalidated by evidence: I had written the plan before
reading `requirements.txt`, where M0 had already installed
`langchain-groq`/`langchain-google-genai` because M6's LangGraph needs
LangChain message objects anyway. Keeping my plan would have meant a
conversion layer, hand-maintained wire formats for two providers, and
duplication of what installed, maintained code already does. The part of
the design that mattered — failover policy, error taxonomy, zero-cost
guard, provenance — survived untouched, because it lived behind the
`LLMProvider` protocol, not in the transports. The lesson I'd defend:
**commit to interfaces, not implementations, and let newly-read evidence
change the implementation for free.**

**Q2. You fail over on *any* error, including 401 auth failures. Doesn't
that hide misconfiguration behind "it works, sort of"?**
Failover hides nothing because failures are never dropped: every attempt
is logged with provider + cause, and if the chain is exhausted the single
raised error lists *every* cause. A 401 is visible twice — in the warning
log the moment it happens, and in the aggregated error if nothing else
served. What failover buys is availability for the *user* while the
operator still sees the truth; what it deliberately does not do is retry
the same broken provider. And configuration errors that failover can't
fix (empty chain, unknown provider name, missing OLLAMA_MODEL) raise
immediately instead — the taxonomy separates "try the next one" from
"stop and fix your config" by testing what the next call could possibly
change.

**Q3. How do you guarantee the LLM never performs financial arithmetic?**
You don't guarantee a probabilistic system with words — you make the
answer not require it. The architecture never gives the model a computing
tool, and every number it could quote arrives pre-computed from the
deterministic engine with provenance (formula, inputs, accession), so a
correct answer never needs to calculate. The prompt states the rule, and
we *measure* compliance: an arithmetic-trap probe asked the model to
compute a YoY percentage change; it quoted both figures verbatim, wrote
the formula, and said the deterministic engine must produce the result.
That's the honest three-layer story — architectural prevention, prompt
policy, empirical measurement (M8's golden set) — not "we trust the
prompt."

**Q4. Filing text goes into your prompt. How do you defend against prompt
injection hidden in a document?**
Structurally, then empirically. Structurally: retrieved text only ever
appears inside `<document>` tags carrying provenance attributes, before
the question, and the system prompt declares document content untrusted
quotation material whose instructions are never followed — the question,
the only thing phrased as an instruction, sits outside the tags. I also
escape an embedded literal `</document>` in chunk text, because untrusted
data must not be able to break the delimiter the defense rests on (tested).
Empirically: this is prompt-level mitigation, not proof, so M8's golden
set needs injection probes (documents that say "ignore previous
instructions"), and M6's citation verification adds a second net —
answers citing documents not actually retrieved are detectable
programmatically regardless of what the text tried to do.

**Q5. A provider's model ID retired mid-project, and the replacement the
vendor recommended hung from your network. What's the systemic lesson?**
Three, escalating. First, hardcoding provider model IDs is a time bomb —
both IDs involved (`gemini-2.5-flash` at M0, `gemini-3.8-flash` as the
vendor's own suggestion) were wrong within hours, which is why the model
is config with a code default in exactly one documented place. Second,
vendor error text is a diagnostic gift: the 404 named the replacement,
and I used Google's own models-list endpoint to separate "key not
authorized" (it was) from "model hangs" (it did) before a
timeout-scaling probe proved the hold was server-side, not my deadline —
each probe designed to kill one hypothesis. Third, failover earns its
keep precisely here: while Gemini flaked, Groq kept serving; while Groq's
key was deliberately corrupted, the chain degraded to a working fallback —
and when both broke, the aggregated error told the whole story in one
line. Nondeterministic vendors are exactly why the boring parts —
classification, aggregation, provenance — must be ours.
