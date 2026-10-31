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

**Input:** a natural-language research question (+ optional thesis context).
**Output:** an evidence-backed answer, tool trace, citations, saved report.

A single well-designed agent (not a multi-agent swarm — extra agents add
complexity, not capability, at this scale) with a typed state object and a
conditional tool-calling loop:

```
START → initialize → parse request → resolve company → plan
     → research agent ⇄ tool execution (loop, max N iterations)
     → evidence validation → (revise plan | continue)
     → analyze findings → evaluate thesis (if applicable)
     → generate report → save → END
```

- **Typed state** (`ResearchState`, a Pydantic/TypedDict schema) carries the
  question, resolved company, plan, message history, tool outputs, evidence,
  citations, and final answer. Because state is explicit, every step is
  inspectable and debuggable — this is the main reason LangGraph over a bare
  chat loop.
- **Tool-calling loop with a cap.** The agent may call tools repeatedly, but a
  hard iteration limit (plus a repeat-detection check) prevents infinite loops.
- **Evidence validation** is a *deterministic node*, not an LLM judgment: it
  checks that every cited chunk ID exists in the retrieved set and that every
  number in the answer appears in a tool output. Unsupported claims are
  stripped and reported as limitations.
- **Checkpointing:** graph state can be persisted so long-running research can
  resume and be audited.

### 2.8 Investment Thesis Monitoring (`app/services/thesis_service.py` + M7)

The differentiating feature. A thesis is a structured object:

```
thesis statement + company + time horizon
  → measurable conditions (e.g. "FY revenue growth ≥ 10%")
  → relevant metrics + supporting evidence at creation time
  → assessment history (append-only)
```

Assessment separates two layers:

1. **Verified layer (Python):** evaluate measurable conditions against actual
   data — true/false/incomplete, with the numbers shown.
2. **Interpretive layer (LLM):** summarize what the verified changes mean for
   the thesis *in words*, constrained to the verified facts.

Status ∈ {Supported, Weakened, Contradicted, Insufficient evidence}. Key rule:
an ML anomaly **alone** never flips a thesis to Contradicted — anomalies are
signals that trigger *investigation*, not verdicts. Every status change stores
an explanation and the evidence that caused it.

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
