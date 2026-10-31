"""M5 demo: grounded question answering over ingested filing text.

    python scripts/ask_filings.py "question" [--k 4] [--ticker MSFT]
                                  [--section mdna]

Flow: embed the question (M4) -> semantic search -> build delimited prompt
(M5 prompting: policy + <document> tags + budget) -> LLMManager.generate with
failover (Groq -> Gemini -> Ollama as configured) -> print the answer, the
provider/model that produced it (LLM INTERPRETATION provenance), and the
retrieved citations actually included in the prompt.

Exit codes: 0 = answered; 1 = no retrieval results; 2 = LLM failure
(all providers failed or none configured -- messages say which).
"""

import argparse
import sys
from pathlib import Path

# Windows consoles default to cp1252; MODEL-GENERATED text contains Unicode
# (non-breaking hyphens, curly quotes) that cp1252 cannot encode -- crashing
# the script after a successful (paid-in-latency) completion would be absurd.
# UTF-8 with errors="replace" garbles at worst a few glyphs, never the run.
# (Same family of lesson as M4's ASCII-only CLI labels: encoding is policy.)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass  # already-wrapped stream; leave as-is

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.services.embeddings import SentenceTransformerEmbedder
from app.services.llm_manager import build_manager
from app.services.llm_providers import LLMConfigurationError, LLMError
from app.services.prompting import build_grounded_messages
from app.services.vector_store import VectorStore
from app.utils.logging import configure_logging


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Ask a grounded question over filing text")
    parser.add_argument("question", help="Natural-language question")
    parser.add_argument("--k", type=int, default=4, help="Retrieved excerpts to include")
    parser.add_argument("--ticker", default=None, help="Restrict retrieval to one ticker")
    parser.add_argument("--section", default=None, help="Restrict retrieval to a section")
    parser.add_argument("--budget", type=int, default=None,
                        help="Context budget in chars (default: settings.prompt_context_chars)")
    args = parser.parse_args(argv)

    configure_logging("WARNING")
    settings = get_settings()

    store = VectorStore(persist_dir=settings.chroma_persist_directory)
    if store.count() == 0:
        print("Vector store is empty. Run scripts/ingest_filings.py first.")
        return 1

    # 1. Retrieve (M4 substrate)
    where = {
        key: value for key, value in
        (("ticker", args.ticker.upper() if args.ticker else None), ("section", args.section))
        if value
    }
    embedder = SentenceTransformerEmbedder()
    query_vec = embedder.embed([args.question])[0]
    hits = store.search(query_vec, k=args.k, where=where or None)
    if not hits:
        print("No retrieval results (check filters).")
        return 1

    # 2. Build the grounded prompt (policy + delimited context + budget)
    budget = args.budget or settings.prompt_context_chars
    messages, used_hits = build_grounded_messages(args.question, hits, budget_chars=budget)

    # 3. Generate with failover
    try:
        manager = build_manager(settings)
    except LLMConfigurationError as exc:
        print(f"Configuration: {exc}")
        return 2
    print(f"Chain: {' -> '.join(manager.provider_names)}")
    try:
        with manager:
            response = manager.generate(messages)
    except LLMError as exc:
        print(f"LLM failure: {exc}")
        return 2

    # 4. Answer + provenance + citations
    print(f"\nServed by: {response.provider} / {response.model} "
          f"({response.latency_ms:.0f} ms, tokens in/out: "
          f"{response.input_tokens}/{response.output_tokens})")
    print("Tier: LLM INTERPRETATION (grounded in the excerpts below)\n")
    print(response.text)

    print("\nCitations actually provided to the model:")
    for rank, hit in enumerate(used_hits, start=1):
        m = hit.metadata
        print(f"  [{rank}] {m.get('ticker')} {m.get('form')} {m.get('accession')} "
              f"section:{m.get('section')} filed:{m.get('filing_date')} "
              f"(distance {hit.distance:.3f})")
    dropped = len(hits) - len(used_hits)
    if dropped:
        print(f"  ({dropped} retrieved excerpt(s) omitted by the context budget)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
