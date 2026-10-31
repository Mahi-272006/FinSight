# FinSight

**Financial Evidence & Investment Thesis Monitoring Platform**

FinSight is an AI-powered financial research platform that retrieves official SEC
company data, calculates financial metrics deterministically, searches company
filings with retrieval-augmented generation (RAG), detects unusual financial
patterns with machine learning, and monitors user-defined investment theses over
time — with verifiable source citations on every claim.

> **Disclaimer:** FinSight is a financial *research* and *evidence-monitoring*
> tool. It does **not** predict returns and does **not** provide buy/sell
> recommendations. An anomaly score is an investigation signal, never proof of
> wrongdoing.

---

## Design Principles

1. **Zero-cost by policy.** SEC EDGAR for data (no key needed), Groq + Gemini
   free tiers for LLMs, Ollama for optional local inference, sentence-transformers
   for local embeddings, PostgreSQL + ChromaDB running locally. No paid service
   is ever introduced silently.
2. **LLMs interpret; Python calculates.** The LLM never performs arithmetic.
   Every number comes from a deterministic, unit-tested Python function or a
   verified SEC fact.
3. **Every claim carries its source.** Raw API responses are preserved
   unmodified; every metric and citation traces back to an accession number and
   a URL.
4. **Separate fact from interpretation.** Reports label content as
   VERIFIED FACT / CALCULATED METRIC / ML SIGNAL / LLM INTERPRETATION.
5. **Measured, not assumed.** A golden evaluation dataset (deterministic checks +
   LLM-as-a-Judge + provider benchmarking) gates every major change.

---

## Architecture (overview)

```
SEC EDGAR ──► Ingestion ──► Raw JSON ──► Normalization ──► PostgreSQL
                                                                    │
       React dashboard ◄── FastAPI ◄── LangGraph agent ◄────────────┤
                                     │        ▲                     │
                                     ▼        │                     ▼
                                  Reports   Tools ◄── Financial Engine (pure Python)
                                     ▲                     │
                                     │                     ▼
                                  ChromaDB ◄── Filing RAG pipeline    ML anomaly detection
```

Detailed explanation: see [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Project Layout

```
FinSight/
├── backend/                 # Python FastAPI application
│   ├── app/
│   │   ├── api/             # HTTP routes
│   │   ├── database/        # SQLAlchemy models, sessions, repositories, ChromaDB
│   │   ├── evaluation/      # Golden dataset runner, metrics, LLM judge
│   │   ├── graph/           # LangGraph research agent (state, nodes, edges)
│   │   ├── ingestion/       # SEC client, filing downloader, parser, chunker
│   │   ├── models/          # ML: feature engineering, anomaly detection
│   │   ├── prompts/         # LLM prompt templates
│   │   ├── schemas/         # Pydantic request/response schemas
│   │   ├── services/        # Business logic (financial engine, LLM manager, ...)
│   │   ├── tools/           # Agent tool definitions
│   │   ├── utils/           # Shared helpers
│   │   ├── config.py        # Environment-based settings
│   │   └── main.py          # FastAPI app entry point
│   ├── data/                # raw/, processed/, filings/ (git-ignored)
│   ├── scripts/             # CLI entry points (ingest, train, evaluate)
│   ├── tests/               # Pytest suite
│   ├── logs/                # Application logs (git-ignored)
│   ├── requirements.txt
│   └── .env.example         # Template for environment variables
└── frontend/                # React + Vite dashboard
    └── src/
        ├── components/  pages/  layouts/
        ├── services/    hooks/  context/  utils/  assets/
```

---

## Getting Started

### Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.10+ | Project uses 3.10.11 (`backend/venv`) |
| Node.js | 18+ | Project developed with Node 23 |
| PostgreSQL | 14+ | Local install, database named `finsight` |
| Git | any | — |

### Backend setup

```powershell
cd backend

# 1. Create / activate virtual environment (already created in this repo)
venv\Scripts\Activate.ps1

# 2. Install CPU-only PyTorch first (avoids ~2.5GB CUDA download on Windows)
pip install torch --index-url https://download.pytorch.org/whl/cpu

# 3. Install remaining dependencies
pip install -r requirements.txt

# 4. Configure environment
copy .env.example .env      # then edit .env: API keys, SEC User-Agent, DB password

# 5. Create the PostgreSQL database (once)
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -c "CREATE DATABASE finsight;"
```

### Frontend setup

```powershell
cd frontend
npm install
npm run dev        # http://localhost:5173
```

---

## Free-Tier Sources

| Need | Source | Key required |
|---|---|---|
| Company financials & filings | [SEC EDGAR](https://www.sec.gov/edgar/search/) (`data.sec.gov` APIs) | No (descriptive User-Agent required) |
| Primary LLM | [Groq](https://console.groq.com/) | Free API key |
| Fallback LLM | [Google Gemini](https://aistudio.google.com/apikey) | Free API key |
| Local LLM (optional) | [Ollama](https://ollama.com/) | No |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` (local) | No |

SEC fair-access policy: max 10 requests/second, identify yourself in
`User-Agent`. FinSight throttles itself to stay well below the limit.

---

## Roadmap (milestones)

| # | Milestone | Status |
|---|---|---|
| M0 | Project setup: git, dependencies, docs, environment | ✅ |
| M1 | SEC ingestion: client, CIK resolution, company facts | ✅ |
| M2 | Normalization + financial engine + PostgreSQL | ✅ |
| M3 | Peer comparison + anomaly detection | ✅ |
| M4 | Filing RAG pipeline | ✅ |
| M5 | Multi-provider LLM manager | ⬜ |
| M6 | LangGraph research agent | ⬜ |
| M7 | Investment thesis monitoring | ⬜ |
| M8 | Golden dataset + evaluation framework | ⬜ |
| M9 | FastAPI endpoints | ⬜ |
| M10 | React dashboard | ⬜ |
| M11 | Integration, hardening, demo | ⬜ |

Progress is logged in [IMPLEMENTATION_LOG.md](IMPLEMENTATION_LOG.md).

---

## License & Data Notice

SEC data is public domain (US Government work). LLM usage is subject to each
provider's free-tier terms. Do not send private or personal data to external
LLM providers.
