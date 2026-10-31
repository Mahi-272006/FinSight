"""M4 ingestion: download filings, extract text, embed, store in ChromaDB.

Usage:
    python scripts/ingest_filings.py MSFT [AAPL ...] [--limit N] [--forms 10-K,10-Q]

Steps per company:
1. Resolve ticker -> CIK (M1 client).
2. Fetch the submissions index; select the newest `--limit` filings of the
   requested forms (default: last 5 of 10-K/10-Q).
3. For each filing: download (or re-use cached) primary HTML, extract
   paragraphs, segment into Items, chunk, embed (local MiniLM), upsert into
   ChromaDB with deterministic ids.

Re-running is idempotent: cached HTML is re-used (no SEC traffic) and chunk
upserts replace rather than duplicate. First run downloads the
all-MiniLM-L6-v2 model (~90 MB) from HuggingFace once; fully offline after.

Exit code: 0 when every selected filing succeeded; 1 when any failed
(failures are recorded per filing and reported; the run continues).
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.ingestion.filing_downloader import DEFAULT_FORMS, parse_recent_filings, select_filings
from app.ingestion.rag_pipeline import process_filing
from app.ingestion.sec_client import SECClient
from app.services.embeddings import SentenceTransformerEmbedder
from app.services.vector_store import VectorStore
from app.utils.logging import configure_logging


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Ingest SEC filings into the vector store")
    parser.add_argument("tickers", nargs="+", help="Tickers to ingest, e.g. MSFT")
    parser.add_argument(
        "--limit", type=int, default=5,
        help="Newest N filings per company (default 5)",
    )
    parser.add_argument(
        "--forms", default=",".join(DEFAULT_FORMS),
        help=f"Comma-separated forms (default {','.join(DEFAULT_FORMS)})",
    )
    args = parser.parse_args(argv)

    configure_logging("INFO")
    settings = get_settings()
    forms = tuple(f.strip() for f in args.forms.split(",") if f.strip())

    # The real embedder loads the model once; every company below shares it.
    print("Loading embedding model (first run downloads ~90 MB, then offline)...")
    embedder = SentenceTransformerEmbedder()

    total_ok, total_failed, total_chunks = 0, 0, 0

    with SECClient() as client, VectorStore(
        persist_dir=settings.chroma_persist_directory
    ) as store:
        for ticker in args.tickers:
            try:
                identity = client.resolve_company(ticker)
            except Exception as exc:  # noqa: BLE001 -- one bad ticker, keep going
                print(f"  {ticker}: RESOLUTION FAILED - {exc}")
                total_failed += 1
                continue

            cik = identity["cik"]
            print(f"\n{identity['ticker']} (CIK {cik:010d}):")
            submissions = client.get_submissions(cik)
            refs = select_filings(parse_recent_filings(submissions, cik), forms, args.limit)
            if not refs:
                print(f"  no filings matching forms {forms}")
                continue
            print(f"  {len(refs)} filing(s) selected "
                  f"({refs[-1].filing_date} .. {refs[0].filing_date})")

            for ref in refs:
                stats = process_filing(client, store, embedder, ref, identity["ticker"], settings)
                tag = f"{ref.form} {ref.accession} ({ref.filing_date})"
                if stats.ok:
                    total_ok += 1
                    total_chunks += stats.chunks
                    cache_tag = " [cached HTML]" if stats.cached else ""
                    print(f"    OK   {tag}: {stats.paragraphs} paras -> "
                          f"{stats.sections_found} sections -> {stats.chunks} chunks{cache_tag}")
                else:
                    total_failed += 1
                    print(f"    FAIL {tag}: {stats.error}")

    print(f"\nDone: {total_ok} filing(s) ingested, {total_failed} failed, "
          f"{total_chunks} chunks total; store now holds {store.count()} vectors.")
    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
