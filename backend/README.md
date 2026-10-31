# FinSight Backend

Python backend for the FinSight financial research platform.

See the repository root for full documentation:
- [`../README.md`](../README.md) — overview and setup
- [`../ARCHITECTURE.md`](../ARCHITECTURE.md) — how the system works internally
- [`../IMPLEMENTATION_LOG.md`](../IMPLEMENTATION_LOG.md) — build progress

## Quick commands

```powershell
# Activate the virtual environment
venv\Scripts\Activate.ps1

# Install dependencies (torch CPU first — see root README)
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt

# Run tests
pytest

# Run the API (once implemented, M9)
uvicorn app.main:app --reload
```

## Layout

- `app/` — application code (api, graph, ingestion, services, models, ...)
- `data/` — raw/processed SEC data and filings (git-ignored)
- `scripts/` — CLI entry points (ingest, train, evaluate)
- `tests/` — pytest suite
- `logs/` — application logs (git-ignored)
