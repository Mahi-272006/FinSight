"""M6 demo: an auditable research run over one company.

    python scripts/research.py "question" --ticker NVDA

Flow: resolve company -> LLM research loop with tools (filing search,
engine facts, anomaly signals) -> deterministic evidence validation ->
revise-or-finalize. Prints the tool trace, the validation verdict, the
final answer with its citations, and every limitation the validation
enforced -- the audit trail, not just the prose.

Exit codes: 0 = research completed (limitations, if any, are printed);
1 = unknown ticker; 2 = LLM/configuration failure.
"""

import argparse
import sys
from pathlib import Path

# Windows consoles default to cp1252; model-generated text contains Unicode
# that cp1252 cannot encode. UTF-8 + replace: garble glyphs, never the run.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.database.postgres import get_session
from app.graph.agent import ResearchAgent
from app.services.embeddings import SentenceTransformerEmbedder
from app.services.llm_manager import build_manager
from app.services.llm_providers import LLMConfigurationError, LLMError
from app.services.vector_store import VectorStore
from app.utils.logging import configure_logging


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Auditable research run over one company")
    parser.add_argument("question", help="Research question")
    parser.add_argument("--ticker", required=True, help="Company ticker (e.g. NVDA)")
    parser.add_argument("--thread-id", default=None, help="Checkpoint thread id (optional)")
    args = parser.parse_args(argv)

    configure_logging("WARNING")
    settings = get_settings()

    store = VectorStore(persist_dir=settings.chroma_persist_directory)
    if store.count() == 0:
        print("Vector store is empty. Run scripts/ingest_filings.py first.")
        return 1
    embedder = SentenceTransformerEmbedder()
    try:
        manager = build_manager(settings)
    except LLMConfigurationError as exc:
        print(f"Configuration: {exc}")
        return 2

    agent = ResearchAgent(
        session_factory=get_session,
        store=store,
        embedder=embedder,
        manager=manager,
        settings=settings,
    )
    print(f"Chain (tool-capable failover): {' -> '.join(manager.provider_names)}\n")

    try:
        with manager:
            result = agent.run(args.question, args.ticker, thread_id=args.thread_id)
    except LLMError as exc:
        print(f"LLM failure: {exc}")
        return 2
    finally:
        manager.close()

    # --- The audit trail ---
    print("Trace:")
    for line in result.trace:
        print(f"  {line}")

    print(f"\nFinal answer ({result.iterations} llm call(s), {result.revises} revision(s)):")
    print(result.final_answer)

    if result.citations:
        print("\nCitations (validated against this run's retrieved evidence):")
        for accession in result.citations:
            print(f"  {accession}")

    validation = result.validation or {}
    print(f"\nValidation: {'PASS' if validation.get('ok') else 'FAIL'}")
    for key in ("unsupported_citations", "unverified_numbers"):
        if validation.get(key):
            print(f"  {key}: {validation[key]}")
    if result.limitations:
        print("\nLimitations (what validation stripped or refused):")
        for item in result.limitations:
            print(f"  - {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
