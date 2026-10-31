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

### 2.2 Normalization (`app/ingestion/data_cleaner.py` + M2 work)

**Input:** raw XBRL facts.
**Output:** clean `FinancialFact` rows in PostgreSQL.

Real-world XBRL is messy, and this is where most of the subtle correctness work
lives:

- **Concept drift across companies.** Microsoft reports revenue as
  `RevenueFromContractWithCustomerExcludingAssessedTax`; older filings used
  `Revenues`; some companies use `SalesRevenueNet`. We map many tags → one
  canonical metric, per company, with a documented mapping table.
- **Period confusion.** Every fact has a *form* (`10-K` annual vs `10-Q`
  quarterly) and a *frame* (start/end date). A 10-Q for Q3 reports
  **year-to-date** numbers (9 months). To get the *standalone* Q3 quarter we
  compute `9M_YTD − 6M_YTD`. Doing this wrongly is the classic double-counting
  bug; we detect YTD facts by comparing period lengths, not by trusting labels.
- **Amendments and restatements.** The same concept/period can appear in
  multiple filings (original 10-K, then a 10-K/A amendment). Rule: *latest
  filed* fact wins, but every version is kept with its accession number so the
  change history is visible.
- **Units.** Facts arrive in USD, shares, or per-share units. We normalize to
  canonical units and reject mismatches loudly.

### 2.3 Financial Engine (`app/services/financial_engine.py`)

**Input:** normalized facts for one company (or several, for peers).
**Output:** `FinancialMetric` records, each carrying **provenance**.

Every metric record stores: company, period, the *formula name*, the *input
values*, the unit, the source (accession number), and a calculation timestamp.
This means any number shown in the UI can be expanded to show exactly how it
was computed — from raw SEC fact to final ratio.

Metrics implemented (growth, profitability, health):
revenue/operating-income/net-income/OCF growth · gross/operating/net margins ·
ROA · current ratio · debt ratios · free cash flow · OCF-vs-net-income
divergence.

All functions are pure (same input → same output, no I/O) so they are trivially
unit-testable — tests compare against values we hand-verified from actual SEC
filings, with a numeric tolerance for float rounding.

### 2.4 RAG Pipeline (`app/ingestion/` → ChromaDB)

**Input:** a filing URL (10-K / 10-Q / selected 8-K).
**Output:** searchable chunks with full metadata.

```
download HTML → parse (lxml) → locate sections (MD&A, Risk Factors, ...)
→ clean text → chunk (~500 tokens, 50 overlap) → embed locally
→ store in ChromaDB with metadata → semantic search at query time
```

- **Embeddings run locally** (`all-MiniLM-L6-v2`, 384 dimensions, ~90 MB).
  No text ever leaves the machine for embedding — a deliberate privacy and
  cost choice.
- **Metadata on every chunk:** company, CIK, filing type, filing date,
  reporting period, accession number, section heading, source URL, chunk ID.
  This is what makes citations possible: a retrieved passage *is* its own
  citation.
- **Untrusted content.** Filing text is data, never instructions. Retrieval
  results are wrapped and delimited when passed to the LLM, and the system
  prompt explicitly forbids following instructions found inside documents
  (prompt-injection defense).

### 2.5 Financial Anomaly Detection (`app/models/`)

**Input:** per-company feature matrix built from historical metrics.
**Output:** anomaly scores + human-readable explanations.

- Model: **Isolation Forest** (scikit-learn). Chosen because it is unsupervised
  (fraud labels don't exist), fast, robust to high dimensions, and its
  contamination parameter maps naturally to "flag the top N% of odd cases."
- Features: revenue growth, margin changes, debt growth, receivables vs revenue
  growth, net-income vs OCF divergence, etc. — each computed by the same
  deterministic engine, never by the LLM.
- **Interpretability:** the detector reports *which* features contributed most
  to isolation (per-feature deviation from the company's own history), so the
  alert says "OCF fell 34% while net income rose 12% versus your 5-year
  pattern," not just "score = 0.71."
- **Honesty about limits:** an anomaly is an *investigation signal*. The score
  is not a calibrated probability of fraud. This caveat appears in the UI, the
  API, and the docs.

### 2.6 LLM Manager (`app/services/llm_manager.py`)

**Input:** messages + tool schemas.
**Output:** normalized provider response + metadata (which provider answered,
latency, failures).

A multi-provider abstraction so the rest of the app never touches Groq/Gemini/
Ollama directly:

- **Primary:** Groq (fast, free tier, tool-calling capable models).
- **Fallback:** Google Gemini — on *transient* errors only (429, 5xx,
  timeout, connection error). Auth errors (bad key) do **not** trigger fallback;
  they fail fast with a clear message.
- **Optional local:** Ollama, if configured and reachable.
- **Resilience:** bounded retries, exponential backoff, a circuit breaker
  (after N consecutive failures a provider is skipped for a cool-down window),
  and per-request logging of provider/latency/errors to a `ProviderEvent` table.
- **Tool-calling normalization:** each provider has slightly different tool-call
  message formats; the manager converts them to one internal shape so the agent
  code is provider-agnostic.
- **Testing without quota burn:** fallback behavior is tested with *mocked*
  failures (injected 429/500/timeouts), so we never need to exhaust real API
  limits to prove resilience.

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
