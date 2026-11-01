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

---

## M6 — LangGraph Research Agent (typed state, retrieval tools, deterministic validation)

### What was built

- `app/graph/` package:
  - `state.py` — `ResearchState` (TypedDict + reducers): `messages`
    (`add_messages`), `tool_observations` / `trace` / `limitations`
    (`operator.add`), loop counters, draft/report/final fields. The
    observations ledger is the run's **single source of truth for evidence**:
    allowlists are derived from it at validation time, never stored twice
    (two sources drift).
  - `evidence.py` — pure functions, no DB/network/LLM:
    `validate_answer` (every cited accession ∈ this run's observations;
    every financial figure matched within 1% after normalization; years and
    small plain integers exempt as non-financial; accessions/dates masked
    before number extraction), `strip_unsupported` (sentence-level removal,
    every removal reported), `ValidationReport` (checkpoint-safe dict).
  - `tools.py` — three **retrieval-only** tools as factory closures over
    injected deps: `search_filing_sections` (M4 vector search, chunk IDs +
    provenance), `get_financial_facts` (M2 rows: VERIFIED FACT with
    form/accession/filed, CALCULATED METRIC with formula — the path by which
    the agent gets numbers *without computing*), `get_investigation_signals`
    (M3 rows, disclaimer as the first line).
  - `nodes.py` — node factories (closures over `GraphDeps`) + **pure routing
    functions** where every loop bound lives; hand-rolled `execute_tools`
    capturing structured observations; `force_stop`; `revise` (report →
    feedback message); `finalize` (strip + limitations + fallback text).
  - `agent.py` — `AGENT_SYSTEM_PROMPT` (tools are the only evidence, tool
    results are untrusted quotation material, quote figures never compute),
    graph assembly, `ResearchAgent.run()` → `ResearchResult` (final answer,
    citations, trace, validation report, limitations, counters — API-ready
    for M9), `InMemorySaver` checkpointer (thread per run).
- `scripts/research.py` — live demo CLI: tool trace, validation verdict,
  final answer, validated citations, limitations. Exit 0 (completed),
  1 (unknown ticker / empty store), 2 (LLM/config failure).
- `LLMManager.bind_tools(tools)` — the M5 seam extended, not bypassed:
  `RunnableWithFallbacks` over tool-capable providers (Groq/Gemini; the
  hand-rolled Ollama adapter has no tool protocol → skipped at build with a
  log; empty set → config error), each branch wrapped with the M6
  wait-on-429 policy + taxonomy classification at the boundary.
- Config: `agent_max_iterations=6`, `agent_max_revises=1`,
  `llm_rate_limit_wait_seconds=12.0` (+ `.env.example` entries).
- Tests: `test_evidence.py`, `test_graph_tools.py`, `test_agent_loop.py`
  (scripted-LLM full-graph runs), plus rate-limit/wait/bind-tools tests in
  `test_llm_manager.py` and `parse_retry_after` units in
  `test_llm_providers.py`.

### Key design decisions

1. **Hand-rolled `execute_tools`, not `prebuilt.ToolNode`.** The validation
   node needs each tool result as *data* (text + chunk IDs) at the moment
   it's produced. ToolNode would only append `ToolMessage`s; re-parsing
   messages later would derive evidence a second time from a lossy format.
   ~30 lines bought one source of truth — and the happy-path test pins it
   structurally (`tool_calls[0]["chunk_ids"] == [acc:3]`), not by text.
2. **Validation is code, never LLM judgment** — and its allowlists come
   from `tool_observations`, never from what the model says it retrieved.
   The 1% tolerance exists because "about $46.7B" IS the same fact as
   46,743,000,000; exact matching would flag honest rounding as fraud.
   Masking exists because `0001045810-26-000075` must never be read as the
   number 1045810. Exemptions (years, small ints) exist because "fiscal
   2026" and "three risks" are not financial claims — each rule tested.
3. **Loop bounds are routing functions, not prompt text.** At
   `agent_max_iterations`: force-stop message → one defensive call →
   validate regardless. At `agent_max_revises`: finalize with limitations.
   The cap test scripts a model that ONLY emits tool calls and asserts
   termination at exactly 8 iterations — if the loop wanted a 9th, the
   scripted manager would raise inside the graph.
4. **Failover inside the graph, taxonomy at the boundary.**
   `bind_tools` keeps M5's failover semantics working *within* a node; the
   wrapper classifies raw SDK errors into our taxonomy because langchain's
   fallbacks wrapper re-raises the FIRST branch's raw error when every
   branch fails (observed live) — without this the CLI printed a groq
   traceback instead of exiting 2 with a readable cause.
5. **Wait-once-on-429 (M5 policy amendment, evidence-driven).** M5
   documented "no in-provider retry… revisited in hardening"; the first live
   agent run *was* the hardening trigger (see bugs #5). New policy: on a
   rate-limit error only — one wait honoring the provider's own Retry-After
   hint (parsed from the message, bounded [1, 60] s, default
   `llm_rate_limit_wait_seconds`), one retry, then failover. All other
   errors still fail over immediately: waiting cannot fix auth or a timeout,
   and generation has no side effects. No SDK backoff — the wait is ours,
   logged, and bounded by policy.
6. **Numbers-without-arithmetic survives adding tools.** The model got
   retrieval powers only: `get_financial_facts` makes quoting a provenance'd
   figure easier than deriving one, so M5's no-arithmetic architecture holds
   with tools in the loop (M8's trap tests will measure it again here).
7. **Injection defense extended to tool results.** Filing text now arrives
   in `ToolMessage`s; the system prompt marks tool-result content untrusted
   quotation material — same `<document>` discipline, new carrier.
8. **`InMemorySaver` now, durable checkpointing in M9.**
   `langgraph-checkpoint-sqlite` is not installed (verified by probe);
   adding a dependency for a feature no milestone needs yet would violate
   the zero-cost-just-in-time rule. The checkpointer slot takes the durable
   implementation unchanged when the API needs cross-process resume.

### Real-data bugs found & fixed (during M6)

1. **Revise node read a key that doesn't exist.** Feedback was built from
   `report["summary"]` — but `summary()` is a method and `to_dict()`
   doesn't serialize it (checkpoint safety). Feedback now builds from the
   dict fields directly.
2. **`f"{v:,.4g}"` rendered `1e+12`** for trillion-scale figures in
   limitations/traces. Added `_display()`: integral values get thousands
   separators (`999,999,999,999`), fractions keep 4 significant digits.
3. **Fabrication-test expectation vs graph behavior** — the test expected
   the "insufficient evidence" fallback, but the graph correctly kept the
   *revised honest* text ("No further evidence.") after stripping the
   hallucination. The graph was right; the test was corrected to assert the
   better behavior: fabrication never reaches the final answer.
4. **Edit accident:** an `oldString` without a trailing newline joined a
   `def` line with its docstring in `llm_providers.py` — caught by reading
   the file right after the edit, repaired immediately (Rule: verify edits).
5. **Live: the first research run died on a free-tier 429** (the milestone's
   signature failure). Chain of causes: Groq's 8000-TPM tier vs a ~5.5k-token
   agent request → 429 ("Please try again in 13.8075s") → M5's no-retry
   policy failed over instantly → Gemini also failed → langchain re-raised
   groq's raw error with Gemini's cause swallowed (`fallbacks.py` logs
   nothing — verified by reading the installed source) → raw traceback,
   exit 1. Fixed in four parts: (a) wait-once honoring the parsed hint
   (first implementation capped at 30 s — a second live run showed the
   window needed longer, cap raised to 60 s with the evidence recorded in
   the test comment); (b) our wrapper now logs EVERY branch failure
   (first attempt and retry), closing the swallowed-cause gap; (c)
   `_classify` at the graph boundary → the CLI exits 2 with
   `LLM failure: groq rate limited: …`; (d) unit tests for
   hint-parsing, wait-once-success, wait-once-failover, no-wait-on-auth,
   the cap, and whole-chain-failure-raises-our-taxonomy. The subsequent
   live run logged `waiting 21.8s once before retrying` (parsed from
   "21.8025s"), retried, and completed end-to-end.
6. **Warning-filter gotcha (why the fix took two tries).**
   `warnings.filterwarnings("ignore", message=…)` defaults to
   `category=UserWarning` — `LangChainPendingDeprecationWarning` derives
   from `PendingDeprecationWarning`, so the filter never matched (proven
   with a minimal repro). And filters are checked newest-first, so it must
   be set *after* `langchain_core` installs its own visibility filter for
   LangChain warnings. Fixed with explicit category + placement, comment
   explains both — the filter also self-expires when the deprecation lands
   (message changes), which is exactly when we should see it again.
7. **Probe findings before writing product code** (cheap probes, expensive
   lessons): `with_fallbacks(..., exception_key="*")` requires dict input —
   omit it for message lists; `tool_calls` DO survive the fallbacks wrapper
   (live-proven for both Groq and Gemini); `openai/gpt-oss-20b` emits
   proper tool calls through `bind_tools` — the milestone's main technical
   risk cleared before any graph code existed.
8. **Gemini aliasing:** requested `gemini-3.5-flash`, response metadata
   reported `gemini-3.6-flash` — Google resolves aliases server-side; the
   response's `model_name` is the provenance truth source, not our config.

### Tests performed (all executed, outputs shown in session)

- `python -m pytest -q` → **334 passed** (284 at M5 close → 316 after the
  32 M6 state/evidence/tools/nodes/graph tests → 334 after the 18
  rate-limit/wait/taxonomy hardening tests). ~8 s wall, no real sleeps
  (the manager takes an injectable `sleep`; tests record waits instead).
- Offline capability probes (Rule 4 before coding): langgraph 0.6.11 with
  `InMemorySaver` + `ToolNode` present, `langgraph-checkpoint-sqlite`
  absent (→ M9); gpt-oss-20b tool-call emission; fallbacks wrapper
  preserving `tool_calls`; the `exception_key` constraint.
- **Live research run (post-hardening):** `research.py "What was NVDA's
  most recent quarterly revenue, and what risks do the filings cite around
  export controls?" --ticker NVDA` → exit 0. Trace: resolve NVIDIA CORP
  (CIK 1045810) → llm#1 tool call (search: quarterly revenue,
  financial_statements) → llm#2 tool call (search: export controls,
  risk_factors) → llm#3 final answer (2611 chars) → `validate: PASS;
  2 citation(s), 0 unsupported` → `finalize: 2 citations, 0 limitation(s)`.
  The answer gave Q2 FY2026 revenue **$96,221M** cited to
  `0001045810-26-000075` and six export-control risk points as *direct
  quotes* from risk factors; both accessions validated against this run's
  retrieved evidence. One rate-limit wait (21.8 s, hint honored) occurred
  mid-run and the retry succeeded.
- Live failure runs (kept as evidence): the pre-hardening 429 death
  (exit 1, traceback) and the post-hardening taxonomy exit (exit 2,
  `LLM failure: groq rate limited: …`) — both documented in bug #5.

### Current limitations / next steps

- **Free-tier throughput:** a single agent request (~5.5k tokens incl.
  gpt-oss reasoning) vs Groq's 8000 TPM means calls stacked inside one
  minute trigger 429 → wait → retry. Runs complete but can take minutes.
  Levers for M11 demo hardening: smaller tool schemas, a leaner model via
  config, or accepting the waits (they're logged and bounded).
- **Fallback causes visibility:** our wrapper logs every branch failure,
  but the exception raised when the whole chain dies carries only the
  first branch's cause (langchain behavior) — full story in logs, first
  chapter in the exception.
- **No durable checkpointing yet** (InMemorySaver only) — M9 adds
  `langgraph-checkpoint-sqlite` when the API needs resume across processes.
- **CLI requires `--ticker`**; natural-language question→company parsing
  is M9/API scope (resolve node is deterministic by design).
- **Grounding path nuance:** the live run quoted the revenue figure from
  filing text instead of `get_financial_facts` — still tool-grounded and
  validated, but the prompt could prefer the facts tool for figures so
  numbers always carry engine provenance (candidate prompt tweak, M8
  measures it).
- **Model citation rendering** is inconsistent (the live answer used
  `【3†chunk_id:…】` style). Extraction is programmatic (regex over the
  answer text), so validation never depends on the model's formatting —
  display polish belongs to M10.
- **M0's empty graph placeholders** (`edges.py`, `workflow.py`,
  `monitoring_workflow.py`) remain untouched: assembly lives in `agent.py`
  and `monitoring_workflow.py` is M7's slot — fill or remove when M7 lands.

### Next milestone

**M7 — Investment thesis monitoring:** structured theses (statement +
measurable conditions over M2 facts), append-only assessment history,
condition evaluation with VERIFIED/CALCULATED provenance, and change
detection — the product's differentiating feature (ARCHITECTURE 2.8).

---

### M6 — Technical interview corner (5 questions + answers)

**Q1. Why LangGraph at all — isn't a research loop just a while-loop with
tool calls?**
A while-loop works for the happy path, but then every guarantee lives in
loop-local variables: iteration counts, validation results, revision
rounds. After the run they're gone, and during it they're untestable in
isolation. LangGraph's typed state makes the entire run *data* — my loop
tests assert on `iterations`, `revises`, `trace`, and the validation report
pulled from final state, and the same structure is what M9 will checkpoint.
I also deliberately did NOT use the prebuilt `create_react_agent`: the
whole point of this project is the deterministic validate → revise →
finalize gates *around* the loop, and a prebuilt agent buries exactly the
internals I need to route through my own gates. Explicit nodes, pure
routing functions, typed state — every step inspectable. It's the same
instinct as keeping the arithmetic in M2's engine: the interesting logic
stays in code you own.

**Q2. How do you actually know a citation or number in the answer is
real — especially when the model renders citations in whatever format it
likes?**
Two deterministic gates, and neither one trusts the model's formatting.
Citations: I regex-extract accession patterns from the answer *text* — the
live run cited `【3†chunk_id:0001045810-26-000075:29】`, a format I never
parsed at all; the accession inside it either appears in this run's tool
observations or the sentence is unsupported. Numbers: figures are
normalized (`$96.2B` ≡ `96,221,000,000`) and must match an observed figure
within 1% — that tolerance is there because "about $46.7B" *is* the same
fact as 46,743,000,000, and exact matching would punish honest rounding.
Years and small plain integers are exempt as non-financial; accessions and
dates are masked before extraction so a filing number can't masquerade as
a figure. On failure the run revises once with the report as feedback; if
it still fails, sentences carrying unsupported claims are stripped and
every removal is listed under Limitations — the answer's omissions are
auditable rather than silent. All pure functions, full test matrix.

**Q3. LangGraph ships a `ToolNode`. Why hand-roll your own?**
Because `ToolNode` gives me message text, and validation needs evidence as
*data*. If I used it, the observation ledger would have to be re-derived by
re-parsing `ToolMessage` contents later — a second, lossy translation of
the one thing the whole verification story rests on. My node executes each
tool call and records `{tool, args, full text, chunk_ids}` at the exact
moment the evidence enters the system, so the allowlists validation checks
against are literally what the tools returned. It's ~30 lines, fully
inspectable, and the happy-path test pins the structural property
(`chunk_ids == ["0001045810-26-000075:3"]`). When the thing your
correctness depends on is cheap to capture at the source, capturing it at
the source beats reconstructing it later — even if the framework offers a
default.

**Q4. Your first live run died on a free-tier 429. You had documented
"no in-provider retries" as an M5 decision — changing it mid-project
smells like policy drift.**
The M5 entry said, verbatim, "revisited in hardening" — what changed was
evidence, not conviction: a live agent loop on an 8000-TPM tier is precisely
the hardening trigger the note anticipated. The failure chain taught more
than the fix: a 429 that says "try again in 13.8s" was being answered by
spending flaky Gemini quota instead of waiting seconds, and when Gemini
also failed, langchain re-raised the *first* raw error with the fallback's
cause invisible (`fallbacks.py` logs nothing — I read the installed source
to confirm). So the amendment is bounded policy, not SDK magic: one wait
honoring the provider's own Retry-After hint (parsed, capped at 60 s — 30 s
was tried live and demonstrably didn't clear the window), one retry, then
failover; non-rate errors still fail over immediately because waiting can't
fix auth. Plus visibility: our wrapper logs every branch failure, and the
graph boundary classifies into our taxonomy so the CLI exits 2 with a
readable cause instead of a traceback. All of it unit-tested — the policy
change came *with* its tests, which is what separates a revision from
drift.

**Q5. What happens when a model won't stop calling tools?**
Three nested bounds, all in routing code, none in the prompt. At
`agent_max_iterations` (6) the graph injects a force-stop message demanding
an answer with no further tools. One defensive extra LLM call follows — if
the model *still* emits tool calls, the router sends it straight to
validation regardless. Validation then fails an empty or ungrounded draft,
`agent_max_revises` (1) caps the feedback loop, and `finalize` assembles an
honest "insufficient evidence" answer with limitations instead of looping.
The cap test scripts a model that ONLY ever returns tool calls and asserts
the graph terminates at exactly 8 iterations with limitations recorded —
and if the loop had wanted a 9th call, the scripted manager would have
raised an AssertionError inside the graph, failing the test. Bounded by
construction, and the bound is asserted, not promised.

---

## M7 — Investment Thesis Monitoring (two-layer assessment, append-only history)

### What was built

The product's differentiating feature (ARCHITECTURE 2.8, written as-built):
an investor states a thesis in prose, pins measurable conditions to it
(`revenue_growth_yoy >= 0.10` for FY2026), and the platform assesses it in
two strictly separated layers, keeping an append-only history.

- **Schema** (`app/database/models.py` + Alembic migration `7cee87c1bedf`,
  chained on `d872fcb82e52`): `theses` (status NULL = Unassessed),
  `thesis_conditions` (immutable after creation), `thesis_assessments`
  (append-only — no `updated_at`, no mutation path; `status_changed_from`
  is NULL unless the status actually moved). Applied to live PostgreSQL;
  verified via `information_schema`/`pg_indexes` (3 tables, 6 indexes).
- **Verified layer** (`app/services/thesis_service.py`, pure functions):
  `parse_condition` (regex `metric OP threshold`, `≥/≤/=` normalized),
  `compare` (5 operators, `==` gets a 1e-9 epsilon for float noise),
  `select_row` (exact period → pinned-year FY row → latest; a missing row
  is `incomplete` with a reason listing what *does* exist — never a guess,
  never a quarter silently standing in for a year),
  `evaluate_condition` (verdict met/not_met/incomplete with full
  provenance), `derive_status` (the verdict-count matrix).
- **Lookup precedence:** FinancialMetric first (CALCULATED, formula +
  source accessions) → FinancialFact fallback (VERIFIED, accession). If
  the metric *name* exists but the pinned period doesn't, that is
  `incomplete` — falling back to a fact row would swap metric semantics
  (growth → raw dollars) and answer a different question than asked.
- **Interpretive layer:** `build_thesis_interpretation_messages` in
  `prompting.py` (M7 policy: numbers verbatim, no arithmetic, signals are
  context not verdicts, gaps stated plainly) + best-effort
  `LLMManager.generate()`. LLM failure/degradation → assessment still
  saves with the deterministic explanation; `explanation_source` records
  which layer wrote the prose (`llm:provider/model` vs
  `deterministic:condition-counts`).
- **Service** (`ThesisService`): `create_thesis` (snapshots each
  condition's current value into `evidence_json` at creation — including
  honest absence: `"value": null` when the metric doesn't exist yet),
  `assess` (two layers, appends one immutable row, updates the thesis's
  cached status), `get_thesis` (full history), `list_theses`.
- **CLI** (`scripts/thesis.py`): `create|assess|show|list`, exit 0/1/2
  (2 only when `--require-interpret` and no LLM is configured — assess
  otherwise degrades to deterministic prose by design).
- **Signal neutrality:** M3 signals are fetched, trimmed, and attached to
  the assessment as `signals_json` context; `derive_status`'s ONLY input
  is condition verdicts — an anomaly alone can never move a thesis toward
  Contradicted. Enforced structurally (function signature) and pinned by
  a test (all conditions met + elevated/high signals → still Supported).

### Key design decisions

- **Statuses in code, not prompts:** {Supported, Weakened, Contradicted,
  Insufficient evidence} via matrix: any `not_met` → Contradicted if zero
  met else Weakened; no `not_met` but any `incomplete` → Insufficient
  evidence; else Supported. The LLM is never asked for a status — it
  explains one that Python already computed.
- **Evidence at creation:** each condition snapshots the metric's value,
  tier, period, formula and accessions when the thesis is written, so
  later assessments can show drift between "what I saw" and "what is
  now" instead of silently re-basing the thesis on restated data.
- **Append-only is structural:** assessments have no `updated_at` and no
  service method mutates an existing row; `status_changed_from` is NULL
  unless the status actually moved (fixed after the first live run showed
  a misleading "changed from Weakened" on a repeat assessment of the same
  world — rows recorded under the buggy semantics were left untouched,
  which is the invariant proving itself).
- **The service owns its transaction:** `create_thesis`/`assess` commit
  internally, so behavior is identical under production's
  `get_session_factory()` and a bare test `sessionmaker`; both close without
  auto-committing, while the service explicitly commits its own transaction.
- **No arithmetic anywhere:** conditions compare thresholds against values
  the M2 engine already computed; growth/margin formulas exist only in
  the engine, only with source accessions.
- **`_fmt`/`_year_period` single formatters:** one place decides how a
  number (ratio 4 decimals vs dollar separators) and a period ("FY2026"
  vs "Q1 FY2026") read — in the CLI, the deterministic explanation, and
  the model prompt alike, so the same value reads identically everywhere.

### Real-data bugs found & fixed (during M7)

1. **My own verification query was wrong, not the migration.** After
   `alembic upgrade head` I verified with `table_name LIKE 'thesis%'` and
   saw only 2 of 3 tables — `theses` doesn't match that pattern (spelling:
   "theses" ≠ "thesis*"). Re-ran with `LIKE 'thes%'`: all 3 tables + 6
   indexes present. Worth logging because "the test said it's missing"
   was, this time, the *test* that was broken.
2. **"FY FY2026".** Period labels were built as
   `f"{fiscal_period} FY{fiscal_year}"`, which for annual rows renders
   "FY FY2026". Collapsed into one `_year_period` helper used by the
   explanation, the snapshot, the signal context and the CLI.
3. **Misleading `status_changed_from`.** The first live `show` printed
   "Weakened (changed from Weakened)" — the column recorded the previous
   status even when nothing changed. Fixed to NULL unless the status
   actually moved; the append-only test updated to pin both directions
   (same world → NULL; real flip → prior status named, change note in the
   explanation).
4. **Test fixture vs M3's uniqueness constraint.** The signal-neutrality
   test inserted two `InvestigationSignal` rows with the same
   (company, type, period) differing only in severity — SQLite's UNIQUE
   constraint (deliberate, M3) rejected it. Two distinct signal types
   instead; the constraint was doing its job.
5. **Naive local timestamp** in the creation snapshot — the project
   convention is the timezone-aware `utcnow` from `models.py`; fixed
   before anything stored a naive datetime.

### Tests performed (all executed, outputs shown in session)

- `python -m pytest tests/test_thesis_service.py -q` → **76 passed**
  (parse/compare/select/evaluate/derive matrices incl. boundary cases;
  signal-neutrality structural + end-to-end; creation snapshots incl.
  honest-null; append-only invariant; transition explanation; LLM
  present/absent/failing/skipped paths; prompt contract).
- `python -m pytest -q` (full suite) → **410 passed** (334 at M6 close →
  410). ~16 s wall.
- **Schema applied live:** `alembic revision --autogenerate -m "add thesis
  tables"` detected exactly 3 tables + 3 indexes and zero drift on
  existing schema; `alembic upgrade head` ran `d872fcb82e52 → 7cee87c1bedf`;
  verified via `information_schema` + `pg_indexes`.
- **Live CLI demo (real MSFT data, CIK 789019):** thesis with three
  FY2026 conditions → creation snapshots
  (`revenue_growth_yoy` 0.1779, `operating_income_margin` 0.4678,
  `net_income_margin` 0.4031, each with formula + accession
  `0001193125-26-323660`). `assess 1` → **Weakened** (2/3 met) with live
  Groq interpretation (`llm:groq/openai/gpt-oss-20b`) restating all three
  numbers verbatim. `assess --no-interpret` → deterministic explanation
  with the same verdicts. `show 1` → 3-row append-only timeline (the
  row recorded under the pre-fix semantics preserved as written).
  Second thesis (single `net_income_margin>=0.45` condition) →
  **Contradicted** (0/1 met) from the same real data.
- Cold-start observation: the first `thesis.py create` invocation took
  >60 s (timeout) on Windows — first-run `.pyc` compilation + langchain
  import; the immediately following run took ~3 s. Not a defect, but
  noted so a demo always does a warm-up import first.

### Current limitations / next steps

- **No scheduled reassessment:** assessments run on demand (CLI); M9's
  API + a scheduler (or M11 demo script) close that loop.
- **Condition periods are thesis-wide:** `--fiscal-year`/`--fiscal-period`
  pin ALL conditions of one thesis to the same period. Mixed-period
  theses ("FY2026 growth AND FY2027 margin") need per-condition period
  syntax — a parser extension, deliberately deferred.
- **No natural-language condition drafting** (LLM proposes, human
  confirms): structured-only creation keeps M7 deterministic; the
  drafting layer can be added on top without touching evaluation.
- **`evidence_json` drift is captured but not surfaced:** assessments
  don't yet compare creation snapshot vs assessed value (M10 can show
  "the metric moved from X to Y since you wrote this").
- **`InMemorySaver`-like ephemerality for theses is NOT a thing** — rows
  are in PostgreSQL from day one; but multi-user ownership/access control
  is M9+ scope.
- **M0 placeholders resolved:** `monitoring_workflow.py` (M7's slot) is
  filled by `thesis_service.py` + `scripts/thesis.py` — the "monitoring
  workflow" is a service + CLI, not a langgraph graph (no loop to
  orchestrate; assessment is one deterministic pass + one optional LLM
  call). The file is now removed as an empty placeholder; `edges.py` /
  `workflow.py` remain empty and are candidates for removal at M8 if M9
  doesn't claim them.

### Next milestone

**M8 — Golden dataset + evaluation framework:** a hand-built golden set
over real ingested data (questions with known answers across all four
evidence tiers), an offline evaluation harness that scores RAG answers
(citation precision/recall, number fidelity, refusal honesty), and the
first measured regression numbers — the difference between "the tests
pass" and "the system is good".

---

### M7 — Technical interview corner (5 questions + answers)

**Q1. The two-layer assessment is the headline. How do you stop the LLM
from ever influencing the status?**
Structurally, not by prompt. `derive_status(evaluations)` takes exactly
one parameter — the condition verdicts — and there is a test that asserts
the signature has exactly one parameter, so adding a `signals=` or
`llm_says=` argument later fails the suite and forces a conversation. The
LLM's input is the finished verdict table; its output is stored in
`explanation` with `explanation_source` saying which layer wrote it; the
status column is written from Python's return value in the line above.
When the LLM is down, the assessment still saves — that degraded path
exists precisely because the verified layer never depended on it. The
four statuses are computed by a verdict-count matrix that lives in one
pure function with a full test matrix, so "what Supported means" is
reviewable in ten lines of code, not scattered across prompts.

**Q2. Why snapshot each condition's value at thesis CREATION?**
Because a thesis is a claim made at a point in time, and financial data
mutates underneath it — SEC restatements are real (M2 found them in
MSFT's own history). If assessment only ever read "current" values, a
restatement would silently re-base the investor's original thesis and the
history would show a status change with no visible cause. The creation
snapshot in `evidence_json` (value, tier, formula, accessions,
captured_at) makes drift *inspectable*: "the metric said 0.1779 when you
wrote this; it says 0.1650 now." It's the same reasoning as M2 storing
`value_original` beside restated facts — provenance at write time is
cheap, reconstruction later is impossible.

**Q3. Your lookup prefers CALCULATED metrics over VERIFIED facts, but a
missing period doesn't fall through to the fact table. Isn't that less
complete?**
It's less *convenient* and more *correct*. If `revenue_growth_yoy` has no
FY2025 row but `revenue` does, evaluating the condition against raw
revenue would answer a different question than the one asked — the
threshold 0.10 means "10% growth", not "10 dollars". So the row is
missing → verdict `incomplete` → status "Insufficient evidence", with a
reason that names the periods that DO exist. "I don't know, and here's
what I would need" is the honest answer, and it's the same discipline as
M6 stripping unsupported sentences rather than letting them stand. The
incompleteness surfaces to the investor; they can re-pin the condition.

**Q4. `thesis_assessments` has no updated_at and no update path. Isn't
that artificial rigidity?**
It's rigidity in service of the audit trail, and it's already paid for
itself. The first live run recorded a misleading "(changed from
Weakened)" row — after fixing the code, the correct behavior appeared on
row 3 while rows 1–2 remained exactly as written. An editable history
would have invited "just fix the bad rows," destroying the evidence that
the bug ever happened — which is the same evidence that let me verify the
fix. If the schema ever genuinely needs to change, that's an Alembic
migration plus a documented backfill decision, not a quiet UPDATE. The
cost of immutability is paid once, in a migration; the cost of mutable
history is paid every time someone trusts it.

**Q5. Anomaly signals exist in the DB. Why not let them count toward the
status — "supported but with red flags" sounds useful?**
Because the moment a signal can move the verdict, the verdict means
"the model was worried" instead of "the conditions were checked," and the
two claims have completely different evidentiary weight. M3 signals are
derived from statistical divergence — they are prompts for a human to
look, exactly as the project rule says: anomalies are investigation
signals, never fraud verdicts. So signals ride along in `signals_json`,
the deterministic explanation names them as context, the prompt tells the
LLM they are never verdicts, and the status stays what the numbers say.
The end-to-end test pins it: every condition met plus elevated AND
high-severity signals → status still Supported, signals still attached.
Useful and honest, without conflating "worth reviewing" with "contradicted".

---

## M9 Stage 1 — FastAPI application foundation

**Status:** ✅ STAGE 1 COMPLETE; Stage 2 is not started
**Date:** 2026-11-01

### What was built

- Added `create_app()` and a module-level ASGI `app` in
  `backend/app/main.py`. The factory configures lifespan startup/shutdown,
  CORS, middleware, exception handlers, and versioned API routing.
- Added an optional `components` mapping stored on `app.state.components` so
  later API routes can receive prebuilt services without module-level service
  singletons.
- Added configurable `CORS_ORIGINS` with defaults for both Vite development
  origins (`localhost:5173` and `127.0.0.1:5173`), credentials disabled, and
  comma-separated origin parsing in `Settings`.
- Added `GET /api/v1/health`, returning status, application version, and
  environment without contacting downstream services.
- Added per-request IDs, response `X-Request-ID` headers, and method/path/
  status/duration logging.
- Added centralized mappings for thesis not-found (404), thesis input (400),
  LLM configuration/provider failures (sanitized 503), and unexpected errors
  (sanitized 500). Diagnostic exceptions are logged server-side; error
  responses do not include exception details or tracebacks.
- Corrected the unhandled-error middleware boundary: Starlette's
  `ServerErrorMiddleware` wraps user middleware, so a generic exception can
  escape `call_next()` before the logging middleware sees the generated 500.
  The middleware now logs the failed request before re-raising, and the outer
  sanitized handler returns the same request ID in `X-Request-ID`.
- Added synchronous TestClient and settings-cache fixtures. Added Stage 1
  tests for health responses, request IDs, CORS, exception mappings and
  sanitization, factory isolation/component injection, and unhandled-error
  response/log correlation.
- Added `httpx2` to backend requirements for the installed Starlette
  TestClient implementation.

### Verification

Freshly executed by the user in Windows PowerShell:

- Focused unhandled-exception tests: **4 passed, 25 deselected**.
- Full backend regression suite: **439 passed in 35.38s** (previous verified
  baseline: 438 passed).

### Scope remaining

M9 Stage 1 is verified. M9 Stage 2 has not started. M8 remains postponed.

---

## M9 Stage 2, Step 1 — Database session dependency correction

**Status:** ✅ STEP 1 VERIFIED; Steps 2 and 3 are not started
**Date:** 2026-11-01

### What changed

- Converted `get_session()` into a genuine generator dependency for FastAPI.
  It yields one SQLAlchemy `Session`, commits after normal completion, rolls
  back if the request raises an exception, and always closes the session.
- Added `get_session_factory()` to return the cached `sessionmaker`, creating
  the existing PostgreSQL engine lazily when needed. Services such as
  `ThesisService` use this factory and continue to own their write commits.
- Added `session_scope()` as a context-manager wrapper for scripts and CLIs.
  Updated the three existing script call sites to use it, preserving their
  commit-on-success, rollback-on-error, and close behavior.
- Added focused lifecycle, lazy factory, compatibility, and ThesisService
  transaction tests using an isolated SQLite database.

### Verification

Freshly executed by the user in Windows PowerShell:

- `tests/test_postgres_dependency.py`: **5 passed**.
- Full backend regression suite: **444 passed in 10.04s** (previous verified
  baseline: 439 passed).

M9 Stage 2, Steps 2 and 3 remain unstarted.

### M9 Stage 2, Steps 2–3 — Component dependencies and API test harness

**Status:** ✅ STAGE 2 COMPLETE; awaiting Checkpoint 1 review
**Date:** 2026-11-01

#### What was built

- Added typed component accessors for the injected ResearchAgent, vector
  store, embedder, and LLM manager. Accessors read only from
  `request.app.state.components`; they do not construct live components.
- Added a cached `get_thesis_service` accessor. It returns an injected service
  when present or constructs one with the injected/lazily obtained
  `sessionmaker` and optional manager, preserving ThesisService's independent
  transaction ownership.
- Added an API SQLite session-factory fixture using `StaticPool` and
  `check_same_thread=False` so setup data is visible to TestClient worker
  threads.
- Added a scoped client factory accepting injected components, dependency
  overrides, and optional test route configuration. It clears overrides in
  `finally` and uses a context-managed TestClient; the existing `client`
  fixture keeps its original interface.
- Added API dependency tests for fake component identity, ThesisService
  construction and caching, a real session shared across TestClient threads,
  and dependency override use and cleanup. Tests inject fakes and do not
  instantiate real ML, ChromaDB, or LLM components; a focused guard fails if
  the API test path attempts to construct the real agent, store, embedder, or
  manager.

#### Verification

Executed in the project virtual environment:

- Focused: `tests/test_api_dependencies.py`, `tests/test_api_stage1.py`, and
  `tests/test_postgres_dependency.py` — **38 passed**.
- Full regression: **448 passed in 7.87s**.

### M9 Stage 3 — Companies API

**Status:** ✅ IMPLEMENTED; awaiting Checkpoint 1.5 review
**Date:** 2026-11-01

#### What was built

- Mounted the Companies router under `/api/v1` while preserving the application
  factory and injected component state.
- Added paginated company listing and company detail endpoints backed by
  `CompanyRepository`. Tickers are validated and looked up case-insensitively.
- Added paginated stored calculated-metric and SEC fact endpoints, plus
  paginated chronological metric history. Metric and period filters are
  validated against the existing domain enums and calculated-metric names
  emitted by the existing financial engine.
- Extended the existing repositories with count and page queries. Route
  handlers use the request-scoped `Depends(get_session)` session and do not
  calculate or synthesize financial values.
- Added response schemas that preserve stored metric formula/input/accession
  provenance and fact filing/restatement provenance. Missing companies return
  404; a supported but explicitly filtered metric/fact with no matching rows
  returns 404. Unfiltered empty collections return an empty page.
- Added API tests using the shared SQLite `StaticPool` fixture and a scoped
  `get_session` override; no live research, ML, vector, or LLM component is
  constructed.
- Added explicit valid and invalid period-filter coverage, including a check
  that the filtered count matches the returned fact rows.

#### Verification

Executed in the project virtual environment:

- Focused Companies API tests: **20 passed**.
- Companies API and related database/API regression tests: **69 passed**
  before the additional period-filter tests.
- Full backend regression after the review update: **468 passed in 8.65s**.
- `git diff --check`: clean.

Stage 3 was committed as `adcf95a80ba9266d4fab15252859a973f99534c6`
(`M9 Stage 3: Companies API`).

### M9 Stage 4 — Anomalies API

**Status:** ✅ IMPLEMENTED; awaiting Checkpoint 2 review
**Date:** 2026-11-01

#### What was built

- Added `GET /api/v1/anomalies/{ticker}` to retrieve already-persisted
  investigation signals for a company. This endpoint is read-only and does
  not invoke the detector or recompute signals.
- Added an optional validated `signal_type` filter and limit/offset
  pagination. Signal identifiers are defined once in the shared domain schema
  and used by both the detector and API validation, preventing the filter
  list from drifting away from the emitted types.
- Extended `SignalRepository` with filtered count and stable page queries;
  counts and rows use the same company/type filters. Existing repository
  methods remain available to preserve current callers.
- Added response schemas exposing the stored signal identity, severity,
  fiscal period, detector headline/explanation, complete evidence values and
  thresholds, source accession numbers, and detection timestamp.
- Empty optional evidence/accession JSON is serialized as an empty object/list;
  valid stored values are returned unchanged. Response `signal_type` remains a
  string because the persisted model allows future types; the query filter is
  constrained to the detector's current shared enum.
- The route uses the existing company repository and request-scoped
  `Depends(get_session)` session. Tests use the API SQLite `StaticPool`
  fixture and scoped session override; no research, ML, vector-store, or LLM
  components are initialized.
- Missing companies return 404, syntactically invalid tickers and unsupported
  signal types return 422, explicitly filtered supported types with no rows
  return 404, and an unfiltered company with no signals returns an empty page.

#### Verification

Executed in the project virtual environment:

- Focused Anomalies API tests: **13 passed**.
- Anomaly, repository, and related API regressions after review fixes:
  **109 passed**.
- Full backend regression after review fixes: **481 passed in 9.22s**.

Anomaly recomputation remains Stage 8 and has not started. Stage 6 and later
stages remain unstarted.

### M9 Stage 5 — Thesis API

**Status:** ✅ IMPLEMENTED; awaiting Checkpoint 2 review
**Date:** 2026-11-01

#### What was built

- Mounted the thesis router under `/api/v1` with create, paginated list,
  detail, assess, and paginated assessment-history endpoints.
- Added request/response schemas for thesis creation, immutable condition
  snapshots, deterministic condition evaluations, assessments, and paged
  results. Input ticker, condition text, horizon, fiscal period, IDs, and
  pagination values are validated.
- Routes depend on `get_thesis_service` and do not use request-scoped
  `get_session`; `ThesisService` continues to open, commit, and close its own
  sessions for reads and writes. Added service-owned page queries for thesis
  listings and assessment history while preserving existing unpaged service
  methods for M7 callers.
- Assessment status and per-condition verdicts continue to come from the
  deterministic service. Optional LLM output supplies wording only. The
  service's existing behavior of appending assessments and falling back to a
  deterministic explanation on LLM errors is preserved.
- Added API tests using injected `ThesisService` instances and fake managers;
  no real providers or ML components are constructed. Tests cover committed
  creation, validation, missing theses, paging, assessment status and history,
  append-only behavior, LLM fallback, 503 mappings for failures that escape
  the service, and rollback/session cleanup when creation fails after flush.
- Thesis input and request-schema validation errors now return static messages;
  validation diagnostics omit submitted values, and thesis-input diagnostics
  remain server-side only.

#### Verification

Executed in the project virtual environment:

- Focused Thesis API tests: **17 passed**.
- Thesis API and Stage 1 exception-handler tests after error sanitization:
  **46 passed**.
- Thesis, service, dependency, exception-handler, and database regressions:
  **144 passed**.
- Full backend regression after review fixes: **498 passed in 9.34s**.

Stage 6 (Research API) has not started.
