# FinSight — Architecture

This document explains *how the system works internally* and *why* each design
choice was made. Written for someone who knows Python and ML basics but is
seeing a production-oriented data + LLM system for the first time.

---

## 1. The core problem and the core rule

Financial analysts need to combine three kinds of information:

1. **Structured numbers** — revenue, margins, cash flow, across years/quarters.
2. **Unstructured text** — hundreds of pages of 10-K/10-Q disclosures.
3. **Continuity** — remembering what they originally believed about a company
   and checking whether new evidence still supports it.

The single most important engineering rule in FinSight follows from this:

> **LLMs interpret; deterministic Python computes.**

Language models are excellent at *understanding a question*, *choosing which
tool to call*, and *summarizing evidence*. They are unreliable at arithmetic.
If an LLM computes a 3-year revenue CAGR, it will sometimes be right, sometimes
wrong — and you cannot tell which. So in FinSight:

- Every number is produced by a pure Python function or read from an SEC fact.
- Every function is unit-tested against hand-verified values.
- The LLM's job is orchestration and language, never math.

This one rule eliminates the largest category of financial AI hallucination.

---

## 2. The seven components and how data flows

### 2.1 Ingestion (`app/ingestion/`)

**Input:** a ticker symbol or company name.
**Output:** raw JSON files on disk + company metadata in PostgreSQL.

- `sec_client.py` — a thin HTTP client for `data.sec.gov` and `www.sec.gov`.
  It sets a descriptive `User-Agent` (required by SEC fair-access policy),
  throttles to ~8 requests/second (limit is 10), and retries transient errors
  with backoff.
- `company_facts.py` — calls the SEC **Company Facts API**, which returns
  essentially every XBRL fact a company has ever filed: revenues, assets,
  liabilities, cash flows... keyed by concept, fiscal period, and filing.
- Raw responses are saved **byte-for-byte** under `data/raw/`. Nothing is
  modified at this stage.

**Why store raw data?** Traceability. When a metric looks wrong in month 6 of
the project, you can diff the stored raw JSON against a fresh SEC response and
prove where the discrepancy came from. Raw data is the audit trail.

### 2.2 Normalization (`app/ingestion/data_cleaner.py` — implemented in M2)

**Input:** raw XBRL facts (one company payload).
**Output:** clean `NormalizedFact` lists → `FinancialFact` rows in PostgreSQL.

Real-world XBRL is messy, and this is where most of the subtle correctness
work lives. Each problem below was confirmed against **live SEC data**:

- **Concept drift — direction differs per company.** Microsoft reports recent
  revenue as `RevenueFromContractWithCustomerExcludingAssessedTax` (modern
  ASC-606 tag) and older revenue as `Revenues`. NVIDIA does the **opposite**:
  older years under the ASC-606 tag, FY2023–26 under generic `Revenues`.
  The normalizer therefore tries *all* tags in a documented priority chain
  and lets the highest-priority tag win **per period**, with later tags
  filling periods earlier tags lack. (Picking a single tag hid NVIDIA's
  entire recent history — caught only by verifying against real data.)
- **Period confusion.** 10-Qs report **year-to-date** figures, not standalone
  quarters. We classify every fact's period by **date arithmetic** (350–380
  days → annual, 80–100 → quarterly, otherwise YTD) — never by trusting
  labels. Standalone quarters are derived as `Q2=H1−Q1`, `Q3=9M−H1`,
  `Q4=Annual−9M`; if any input is missing, no quarter is emitted.
- **Fiscal-year labels lie for comparative columns.** A 10-Q's prior-year
  comparative facts carry the *filing's* `fy` label. Fiscal years are
  therefore derived from period-end dates plus the company's inferred
  fiscal-year-end month (the modal end-month of its annual facts), which
  works for June (MSFT), September (AAPL), and January (NVDA) year-ends.
- **Amendments and restatements.** Same period in multiple filings: the
  *latest filed* fact wins; if the value **changed**, both values are kept
  (`value_original` + `is_restated`) and the change is surfaced, never hidden.
- **Units.** Facts in unexpected units are rejected loudly, never guessed.

### 2.3 Financial Engine (`app/services/financial_engine.py` — implemented in M2)

**Input:** normalized facts for one company.
**Output:** `FinancialMetric` records, each carrying **provenance**.

Growth rates (YoY, sign-correct for loss recovery), margins, ROA, free cash
flow, current ratio, debt-to-assets, and an earnings-vs-cash-flow divergence
signal. Every metric record stores: company, period, the *formula name*, the
*input values*, the source accession numbers, and a calculation timestamp —
so any number in the UI can be expanded to its exact arithmetic. Division by
zero returns `None` with a recorded reason; it never raises and never
produces inf/nan. **The LLM never computes any of this** — all arithmetic is
deterministic Python covered by hand-verified golden-value tests.

Metrics implemented (growth, profitability, health):
revenue/operating-income/net-income/OCF growth · gross/operating/net margins ·
ROA · current ratio · debt ratios · free cash flow · OCF-vs-net-income
divergence.

All functions are pure (same input → same output, no I/O) so they are trivially
unit-testable — tests compare against values we hand-verified from actual SEC
filings, with a numeric tolerance for float rounding.

### 2.4 RAG Pipeline (`app/ingestion/` → ChromaDB — implemented in M4)

**Input:** a filing URL (10-K / 10-Q; amendments excluded).
**Output:** searchable chunks with full metadata.

```
download HTML (cache-first, byte-for-byte) → parse (lxml) → segment at Item
headers (4 anti-false-positive guards; section names from the header TITLE,
not the item number, because numbers are form-dependent) → chunk (2400 chars,
300 overlap) → embed locally → delete-then-insert into ChromaDB (one
collection, cosine space, ids `{accession}:{chunk_index}`) → semantic search
at query time
```

- **Embeddings run locally** (`all-MiniLM-L6-v2`, 384 dimensions, ~90 MB).
  No text ever leaves the machine for embedding — a deliberate privacy and
  cost choice. Vectors are L2-normalized at the producer, matching the
  collection's cosine space.
- **Metadata on every chunk:** CIK, ticker, form, accession, filing date,
  report date, section, item number, chunk index.
  This is what makes citations possible: a retrieved passage *is* its own
  citation. Fiscal-year context is NOT duplicated onto chunks — accessions
  already join to `FinancialFact` (M2), so there is one source of truth.
- **Segmentation is best-effort with an honest fallback**, biased toward
  *not* matching: a missed header loses a label (retrieval still works); a
  wrong header would lie about every chunk after it. Unrecognized text
  becomes `unknown`, never a guessed section.
- **Re-ingestion replaces a filing's whole chunk set** (delete-then-insert):
  upsert-by-id alone would orphan chunks when the chunking code changes.
- **Untrusted content.** Filing text is data, never instructions. Retrieval
  results are wrapped and delimited when passed to the LLM, and the system
  prompt explicitly forbids following instructions found inside documents
  (prompt-injection defense).

### 2.5 Financial Anomaly Detection (`app/services/anomaly_detection.py` — rule layer implemented in M3; ML layer planned)

**Input:** per-company metric time series built from `FinancialMetric` rows.
**Output:** investigation signals (severity + evidence + human-readable
explanation), persisted to `investigation_signals`.

The detector has two layers by design:

- **Deterministic rules (implemented in M3).** Four hand-justified,
  unit-tested rules over engine-computed metrics: earnings–cash-flow
  divergence, receivables outpacing revenue, sustained margin-compression
  streaks, and growth deceleration. Each rule documents its thresholds,
  fires only in the concerning direction (sign-aware), stays silent on
  missing data, and attaches its full evidence (input values, threshold,
  formula, source accession numbers) to every signal. Peer-relative context
  comes from `peer_comparison.py` (leave-one-out z-scores, labeled
  "indicative only" when the peer set is too small for statistical
  significance — honesty is enforced in code, not just prose).
- **ML statistical flags (planned).** **Isolation Forest**
  (scikit-learn) over the feature matrix the same deterministic engine
  already computes (growth, margin deltas, receivables-vs-revenue,
  NI-vs-OCF divergence). Chosen because it is unsupervised (fraud labels
  don't exist), fast, and its contamination parameter maps naturally to
  "flag the top N% of odd cases." The detector will report *which*
  features contributed most to isolation, so the alert says "OCF fell 34%
  while net income rose 12% versus your 5-year pattern," not just
  "score = 0.71."

- **Honesty about limits:** an anomaly is an *investigation signal*. Neither
  a rule firing nor an isolation score is a calibrated probability of fraud.
  Every signal carries the explicit "NOT a finding of misstatement"
  framing; this caveat also appears in the UI, the API, and the docs.

### 2.6 LLM Manager (`app/services/llm_manager.py` + `llm_providers.py` + `prompting.py` — implemented in M5)

**Input:** messages (LangChain `SystemMessage`/`HumanMessage`, which M6's
LangGraph consumes natively).
**Output:** `LLMResponse` — text + provenance (provider, model, in/out
tokens, latency).

A multi-provider abstraction so the rest of the app never touches Groq/
Gemini/Ollama directly. Providers sit behind a small `LLMProvider` protocol;
`LLMManager.generate()` walks the configured chain in order:

- **Primary:** Groq (free tier; `openai/gpt-oss-20b` by default) and
  **fallback:** Google Gemini (free tier; `gemini-3.5-flash`, live-verified)
  via the installed LangChain integrations; **optional local:** Ollama via a
  hand-rolled `/api/chat` client over injectable httpx (no extra dependency).
- **Failover policy (as built):** ANY provider failure — rate limit, auth,
  timeout, connection, malformed response, even unexpected exceptions — is
  logged with its cause and fails over to the next provider; if the whole
  chain fails, one `LLMAllProvidersFailed` error carries every
  `(provider, cause)`. Configuration errors (empty chain, unknown provider,
  missing model) raise immediately instead: failover cannot fix config.
  *Deliberate simplification vs. the original plan:* no per-provider
  retries/backoff or circuit breaker yet (`max_retries=0` on the SDKs so
  failover timing stays deterministic); auth errors fail *over* rather than
  fast because they are visible both in logs and in the aggregated error.
  Hardening adds backoff + a `ProviderEvent` table; the protocol seam means
  none of that touches callers.
- **Zero-cost guard:** only free-tier implementations exist in code;
  providers without keys are skipped (logged), and an emptied chain raises
  `LLMConfigurationError` with setup instructions. No silent paid fallback,
  ever.
- **Grounded prompting (`prompting.py`):** the system prompt carries the
  project's constitution (untrusted `<document>` content, NO ARITHMETIC,
  the four evidence tiers, citation form); retrieved chunks are delimited
  with provenance attributes and truncated to a char budget from the
  lowest-ranked up; `used_hits` is returned so answers can only cite what
  was actually in the prompt.
- **Testing without quota burn:** adapters take injectable runnables/
  transports, and the manager is tested entirely against fake providers —
  injected 429/401/timeout/connection failures prove the failover matrix
  offline. One live test drove the real `ChatGroq` over
  `httpx.MockTransport` to prove the seam holds for the actual SDK.

### 2.7 LangGraph Research Agent (`app/graph/`)

**Input:** a research question + ticker (CLI; the API surface arrives in M9).
**Output:** `ResearchResult` — final answer, validated citations, tool trace,
validation report, limitations, loop counters. Built as-built in M6:

```
START → resolve (deterministic: ticker → company row; unknown → END)
      → research (one LLM call through the tool-bound chain)
      ⇄ execute_tools (hand-rolled; captures STRUCTURED observations)
           [cap: agent_max_iterations → force-stop message → one last call]
      → validate (deterministic) ──pass──→ finalize → END
                └──fail (≤ agent_max_revises)──→ revise → research
```

- **Typed state** (`ResearchState`, TypedDict + reducers): `messages`
  (`add_messages`), `tool_observations` / `trace` / `limitations`
  (`operator.add`), plus counters. Because state is explicit, every step is
  inspectable and debuggable — this is the main reason LangGraph over a bare
  chat loop. The `tool_observations` ledger is the run's single source of
  truth for evidence: citation/number allowlists are derived from it at
  validation time, never stored twice.
- **Three retrieval tools, never computing tools** (factory closures over
  injected deps): `search_filing_sections` (M4 vector search, chunk IDs +
  provenance), `get_financial_facts` (M2 engine rows — VERIFIED FACT with
  form/accession, CALCULATED METRIC with formula; this is how the agent gets
  numbers without computing them), `get_investigation_signals` (M3 rows,
  disclaimer-first: signals are prompts, not verdicts).
- **Hand-rolled `execute_tools` node** (not `prebuilt.ToolNode`): every tool
  call is recorded structurally — full text + chunk IDs — where it is
  produced, because the validation node needs evidence as *data*, not as the
  LLM's rendering of message text.
- **Loop caps are code, not prompt**: `agent_max_iterations` (default 6)
  routes to a force-stop message at the cap, then one defensive call before
  validation regardless; `agent_max_revises` (default 1) bounds the
  validation→revise→research feedback path. Routing functions are pure.
- **Evidence validation** is a *deterministic node* (`app/graph/evidence.py`,
  pure functions): every accession cited must appear in THIS RUN's tool
  observations; every financial figure must match an observed figure within
  1% tolerance (`$96.2B` ≡ `96,221,000,000`); years and small plain integers
  are exempt as non-financial; accessions/dates are masked before number
  extraction so `0001045810-26-000075` is never read as a number. On failure
  the run revises once with the report as feedback; when revisions run out,
  `finalize` strips unsupported sentences and lists every removal under
  Limitations — omissions are auditable, never silent.
- **Failover inside the graph**: `LLMManager.bind_tools(tools)` returns a
  `RunnableWithFallbacks` over tool-capable providers (Groq/Gemini; the
  hand-rolled Ollama adapter has no tool protocol → skipped at build time).
  Each branch carries the M6 one-wait-on-429 policy (Retry-After hint,
  capped 60s, one retry) and classifies errors into the M5 taxonomy at the
  boundary — because langchain re-raises the FIRST branch's raw error when
  every branch fails (observed live), so classification there is what lets
  the CLI exit 2 with a readable cause instead of an SDK traceback.
- **Injection defense extends to tool results**: filing text now arrives in
  `ToolMessage`s; the system prompt marks tool-result content as untrusted
  quotation material (same discipline as M5's `<document>` tags).
- **Checkpointing:** `InMemorySaver` (one thread per run; resumable/auditable
  within the process). Durable checkpointing (`langgraph-checkpoint-sqlite`)
  is intentionally deferred to M9, where the API needs cross-process resume.

### 2.8 Investment Thesis Monitoring (`app/services/thesis_service.py` + M7)

The differentiating feature. A thesis is a structured object:

```
thesis statement + company + time horizon
  → measurable conditions (e.g. "revenue_growth_yoy >= 0.10" for FY2026)
  → relevant metrics + supporting evidence at creation time
  → assessment history (append-only)
```

**Tables** (migration `7cee87c1bedf`): `theses` (status is NULL until first
assessment), `thesis_conditions` (immutable after creation; each snapshots
the metric's value at creation into `evidence_json` so later drift is
visible, not silent), `thesis_assessments` (append-only: no `updated_at`,
no mutation path; `status_changed_from` is NULL unless the status actually
moved).

Assessment separates two layers:

1. **Verified layer (Python, pure functions):** `parse_condition` turns
   `metric OP threshold` into a spec; `evaluate_condition` compares a
   looked-up value against the threshold; `derive_status` maps verdicts to
   a status. Lookup precedence: FinancialMetric first (CALCULATED, with
   formula + accessions) → FinancialFact fallback (VERIFIED). Period
   selection: exact match, else the annual (FY) row of the pinned year,
   else latest available — a missing row is `incomplete`, never a guess
   and never a silent substitution of a quarter for a year.
2. **Interpretive layer (LLM, best-effort):** `build_thesis_interpretation_messages`
   hands the model the pre-computed verdict table; it must restate numbers
   verbatim, never compute. On LLM failure the assessment still saves with
   the deterministic explanation; `explanation_source` records which layer
   wrote it (`llm:provider/model` vs `deterministic:condition-counts`).

Status ∈ {Supported, Weakened, Contradicted, Insufficient evidence}, by
matrix: any not_met → Contradicted (if zero met) else Weakened; no not_met
but any incomplete → Insufficient evidence; else Supported. Key rule: an
ML anomaly **alone** never flips a thesis to Contradicted — enforced
structurally (`derive_status`'s only input is condition verdicts; signals
ride along as `signals_json` context) and pinned by a test. Every status
change stores an explanation and the evidence that caused it.

CLI: `scripts/thesis.py create|assess|show|list`.

---

## 3. Data storage design

| Store | Holds | Why here |
|---|---|---|
| **PostgreSQL** | companies, facts, metrics, filings metadata, theses, assessments, sessions, reports, anomalies, monitoring events, alerts, evaluation runs, provider events, audit logs | Relational data with integrity constraints; Alembic tracks schema evolution |
| **ChromaDB** | filing text chunks + embeddings + metadata | Vector similarity search; wrong tool for financial records, right tool for text |
| **Filesystem (`data/`)** | raw SEC JSON, downloaded filings | Immutable audit trail, cheap, diff-able |

Rule: **structured financial facts never live only in ChromaDB**; chunks live
only in ChromaDB. Each store has one job.

---

## 4. Security posture

- Secrets only in `.env` (git-ignored); `.env.example` documents every key.
- All DB access through SQLAlchemy ORM (parameterized — no SQL injection).
- Filing text treated as **untrusted input**: delimited when shown to the LLM,
  never executed, cannot override system instructions.
- User-visible errors are sanitized: no stack traces, no API keys.
- Auth is added in M9 before any user-specific data (theses, reports) is exposed.

---

## 5. Evaluation (the module that keeps everything honest)

Three layers, all mandatory:

1. **Deterministic checks (Python):** metric values vs hand-verified SEC
   numbers with tolerances; tool-call accuracy vs expected tool sequences;
   retrieval precision@k / recall@k on labeled questions; citation validity
   (does the cited chunk actually exist?); workflow completion; simulated API
   failures.
2. **LLM-as-a-Judge:** an *independent* model scores answers on correctness,
   faithfulness to evidence, completeness, citation support, and reasoning —
   returning structured JSON with explanations. Judge scores are *signals*, not
   ground truth; a small human-reviewed set calibrates the judge.
3. **Provider benchmark:** identical golden cases run on Groq vs Gemini (vs
   Ollama optionally), comparing accuracy, faithfulness, latency, and error
   rate within free-tier limits. Results are stored so trends are visible.

Regression policy: any change to prompts, tools, retrieval, or models reruns
the golden set. A feature is done when measured, not when it "works once."

---

## 6. Why this shape and not something more complex

- **One agent, not many.** Multi-agent systems shine when sub-tasks need
  different models/permissions. Our tasks are tool calls on one state — a
  single agent with good tools is easier to debug, cheaper, and evaluable.
- **Synchronous pipeline first, scheduling later.** Monitoring starts manually
  triggered; cron/scheduling is added only after the pipeline is correct.
  Correctness before automation.
- **Monolith, not microservices.** One FastAPI process + two local data stores.
  Microservices would add network failure modes and deployment cost with zero
  benefit at this scale — and would break the zero-cost policy.
