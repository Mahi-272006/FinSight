"""M4 retrieval demo: semantic search over ingested filing text.

Usage:
    python scripts/search_filings.py "query text" [--k 5] [--ticker MSFT]
                                     [--section mdna] [--form 10-K]

Embeds the query with the same local model used at ingestion, searches the
vector store (cosine), and prints hits with full provenance: company,
form, accession, section, filing date, and the source text. This is the
retrieval substrate the LLM analyst (M5/M6) will call -- kept as a script
so retrieval quality can be inspected without the API layer.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.services.embeddings import SentenceTransformerEmbedder
from app.services.vector_store import VectorStore
from app.utils.logging import configure_logging


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Search ingested filing text")
    parser.add_argument("query", help="Natural-language query")
    parser.add_argument("--k", type=int, default=5, help="Number of results")
    parser.add_argument("--ticker", default=None, help="Restrict to one ticker")
    parser.add_argument("--section", default=None,
                        help="Restrict to a section (mdna, risk_factors, ...)")
    parser.add_argument("--form", default=None, help="Restrict to a form (10-K, 10-Q)")
    args = parser.parse_args(argv)

    configure_logging("WARNING")
    settings = get_settings()

    store = VectorStore(persist_dir=settings.chroma_persist_directory)
    if store.count() == 0:
        print("Vector store is empty. Run scripts/ingest_filings.py first.")
        return 1

    where = {
        key: value
        for key, value in (
            ("ticker", args.ticker.upper() if args.ticker else None),
            ("section", args.section),
            ("form", args.form),
        )
        if value
    }

    embedder = SentenceTransformerEmbedder()
    query_vec = embedder.embed([args.query])[0]
    hits = store.search(query_vec, k=args.k, where=where or None)

    if not hits:
        print("No results (check filters).")
        return 1

    print(f"Query: {args.query!r}")
    if where:
        print(f"Filter: {where}")
    print(f"{len(hits)} result(s) from {store.count()} stored chunks:\n")

    for rank, hit in enumerate(hits, start=1):
        m = hit.metadata
        print(f"[{rank}] {m.get('ticker', '?')} {m.get('form', '?')} "
              f"{m.get('accession', '?')} - section: {m.get('section', '?')} "
              f"(filed {m.get('filing_date', '?')}, cosine distance {hit.distance:.3f})")
        snippet = hit.text.replace("\n", " ")
        if len(snippet) > 300:
            snippet = snippet[:300] + "..."
        print(f"    {snippet}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
