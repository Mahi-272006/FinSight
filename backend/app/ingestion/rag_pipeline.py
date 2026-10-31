"""Filing RAG pipeline: download -> extract -> segment -> chunk -> embed -> store.

Orchestration only; every heavy step is a pure, separately tested function
(filing_text) or an injected dependency (SECClient, Embedder, VectorStore).
That keeps this module thin and lets tests run the WHOLE pipeline on a
fixture HTML file with a FakeEmbedder and an in-memory Chroma client --
no network, no model download, no database.

KEY POLICIES ENFORCED HERE:

- RAW HTML IS THE AUDIT TRAIL (M1 rule, applied to documents now): filings
  are saved byte-for-byte under data/filings/{cik}/{accession}/ before any
  parsing. If extracted text ever looks wrong, the source bytes are on disk
  to diff against.

- DISK CACHE = IDEMPOTENCY: if the raw HTML already exists it is re-used
  and the SEC is not contacted again. Re-running the pipeline converges to
  the same vector store state (deterministic chunk ids upsert, never
  duplicate), same as fact ingestion in M2.

- ONE FILING FAILS, THE RUN CONTINUES: a 404 or a parse error on one filing
  is logged and reported in its FilingStats; the caller decides policy
  (the CLI keeps going, aggregating failures into the exit code).

- CHUNK METADATA CARRIES FULL PROVENANCE: cik/ticker/form/accession/
  filing_date/section/chunk_index. Retrieval results can always name the
  exact filing and section they came from -- the citation requirement that
  M5/M6's LLM layer will rely on. Fiscal-year alignment is NOT done here:
  chunks cite accessions, and accessions already join to FinancialFact
  rows (M2), so fiscal context comes from the facts table rather than a
  duplicated label.
"""

from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings, get_settings
from app.ingestion.filing_downloader import FilingRef
from app.ingestion.filing_text import (
    chunk_document,
    extract_paragraphs,
    segment_sections,
)
from app.ingestion.sec_client import SECClient
from app.services.embeddings import Embedder
from app.services.vector_store import VectorStore
from app.utils.logging import get_logger

logger = get_logger(__name__)

# Embed this many chunks per embedder call. Batching matters for CPU
# throughput (MiniLM encodes ~32-64 texts in parallel efficiently) and caps
# peak memory on big 10-Ks (~400 chunks).
EMBED_BATCH_CHARS = 8_000  # batch by cumulative chars, not count


@dataclass
class FilingStats:
    """Outcome of processing one filing (success or failure record)."""

    ref: FilingRef
    ok: bool
    cached: bool = False
    paragraphs: int = 0
    sections_found: int = 0
    chunks: int = 0
    error: str | None = None


def filing_cache_path(settings: Settings, cik: int, accession: str, document: str) -> Path:
    """Where the raw filing HTML lives: data/filings/{cik}/{accession}/{doc}."""
    return settings.data_filings_dir / str(cik) / accession.replace("-", "") / document


def _iter_batches(items: list[str], max_chars: int) -> list[list[int]]:
    """Group indices into batches bounded by cumulative text length."""
    batches: list[list[int]] = []
    current: list[int] = []
    chars = 0
    for i, text in enumerate(items):
        if current and chars + len(text) > max_chars:
            batches.append(current)
            current, chars = [], 0
        current.append(i)
        chars += len(text)
    if current:
        batches.append(current)
    return batches


def process_filing(
    client: SECClient,
    store: VectorStore,
    embedder: Embedder,
    ref: FilingRef,
    ticker: str,
    settings: Settings | None = None,
) -> FilingStats:
    """Run one filing through the full pipeline.

    Downloads (or re-uses cached) HTML, extracts paragraphs, segments
    sections, chunks, embeds in bounded batches, and upserts into the
    vector store with deterministic ids `{accession}:{chunk_index}`.
    """
    settings = settings or get_settings()

    # 1. Raw HTML: cache-first, byte-faithful.
    cached = False
    cache_path = filing_cache_path(settings, ref.cik, ref.accession, ref.primary_document)
    try:
        if cache_path.exists():
            html = cache_path.read_bytes()
            cached = True
        else:
            url = ref.document_url(settings.sec_files_url)
            logger.info("Downloading %s %s (%s)", ticker, ref.form, ref.accession)
            html = client.get_bytes(url)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(html)
    except Exception as exc:  # noqa: BLE001 -- one filing must not kill the run
        logger.error("Download failed for %s %s: %s", ticker, ref.accession, exc)
        return FilingStats(ref=ref, ok=False, error=f"download: {exc}")

    # 2-4. Extract, segment, chunk (pure functions).
    try:
        paragraphs = extract_paragraphs(html)
        sections = segment_sections(paragraphs)
        chunks = chunk_document(paragraphs, sections)
    except Exception as exc:  # noqa: BLE001 -- malformed HTML must not kill the run
        logger.error("Parse failed for %s %s: %s", ticker, ref.accession, exc)
        return FilingStats(ref=ref, ok=False, cached=cached, error=f"parse: {exc}")

    if not chunks:
        logger.warning("No text extracted from %s %s", ticker, ref.accession)
        return FilingStats(
            ref=ref, ok=True, cached=cached,
            paragraphs=len(paragraphs), sections_found=len(sections), chunks=0,
        )

    # 5. Embed in batches bounded by text length (CPU-friendly, memory-safe).
    texts = [c.text for c in chunks]
    vectors: list[list[float] | None] = [None] * len(chunks)
    for batch in _iter_batches(texts, EMBED_BATCH_CHARS):
        embedded = embedder.embed([texts[i] for i in batch])
        for slot, vec in zip(batch, embedded):
            vectors[slot] = vec

    # 6. Upsert with full provenance metadata + deterministic ids.
    ids, embeddings, documents, metadatas = [], [], [], []
    for chunk, vec in zip(chunks, vectors):
        ids.append(f"{ref.accession}:{chunk.chunk_index}")
        embeddings.append(vec or [])
        documents.append(chunk.text)
        metadatas.append({
            "cik": ref.cik,
            "ticker": ticker,
            "form": ref.form,
            "accession": ref.accession,
            "filing_date": ref.filing_date,
            "report_date": ref.report_date or "",
            "section": chunk.section,
            "item_number": chunk.item_number or "",
            "chunk_index": chunk.chunk_index,
        })
    # 6. Replace any chunks from a PREVIOUS parse of this filing, then
    # upsert. Upsert-by-id alone would orphan stale chunks when a code
    # change alters chunking (old ids that no longer get regenerated would
    # survive) -- found while re-ingesting MSFT after a segmentation fix.
    # Delete-then-insert makes re-processing a filing a true replace.
    store.delete_filing(ref.accession)
    store.upsert_chunks(ids, embeddings, documents, metadatas)

    logger.info(
        "%s %s %s: %d paragraphs -> %d sections -> %d chunks%s",
        ticker, ref.form, ref.accession,
        len(paragraphs), len(sections), len(chunks),
        " (cached HTML)" if cached else "",
    )
    return FilingStats(
        ref=ref, ok=True, cached=cached,
        paragraphs=len(paragraphs), sections_found=len(sections),
        chunks=len(chunks),
    )
