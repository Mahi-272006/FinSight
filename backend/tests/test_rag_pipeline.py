"""Tests for the RAG pipeline wiring (M4) and the vector store.

The pipeline runs end-to-end on fixture HTML with FakeEmbedder (no model
download) and an in-memory Chroma client (no disk). These tests prove the
WIRING: cache behavior, provenance metadata, deterministic ids,
idempotent re-runs, and retrieval returning the right chunk.
"""

import httpx
import pytest

from app.config import Settings
from app.ingestion.filing_downloader import FilingRef
from app.ingestion.rag_pipeline import filing_cache_path, process_filing
from app.ingestion.sec_client import SECClient
from app.services.embeddings import FakeEmbedder
from app.services.vector_store import VectorStore, _build_where

FIXTURE_HTML = b"""
<html><body>
<h2>Item 1. Business</h2>
<p>We design and sell graphics processors and accelerated computing systems.</p>
<h2>Item 1A. Risk Factors</h2>
<p>Our results depend on demand for data center infrastructure and on
export control compliance with respect to international sales.</p>
<h2>Item 7. Management's Discussion and Analysis</h2>
<p>Data center revenue grew sharply this fiscal year driven by accelerator
demand for large language models.</p>
</body></html>
"""

REF = FilingRef(
    cik=1045810, accession="0001045810-26-000021", form="10-K",
    filing_date="2026-02-25", primary_document="nvda-20260125.htm",
    report_date="2026-01-25",
)


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(
        # Note: the validator rejects "example.com" substrings; tests never
        # contact SEC (MockTransport intercepts all requests), so a .local
        # address satisfies the validator honestly.
        sec_user_agent="FinSight Test Suite finsight-tests@noreply.local",
        data_filings_dir=tmp_path / "filings",
        data_raw_dir=tmp_path / "raw",
        data_processed_dir=tmp_path / "processed",
        logs_dir=tmp_path / "logs",
        chroma_persist_directory=tmp_path / "chroma",
    )


@pytest.fixture()
def store(tmp_path) -> VectorStore:
    # PersistentClient over a per-test tmp_path: EphemeralClient instances
    # share one in-memory database within a process (data leaked between
    # tests in the first run of this suite), while a unique tmp dir gives
    # true isolation -- and exercises the real on-disk code path.
    return VectorStore(persist_dir=tmp_path / "chroma_store")


def _cached_client() -> SECClient:
    """A client that FAILS if contacted -- proves the cache path is used."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"SEC should not be contacted, got {request.url}")

    return SECClient(transport=httpx.MockTransport(handler), sleep=lambda _s: None)


def _downloading_client(content: bytes = FIXTURE_HTML) -> SECClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content)

    return SECClient(transport=httpx.MockTransport(handler), sleep=lambda _s: None)


class TestProcessFiling:
    def test_download_then_cache(self, settings, store):
        # First run: downloads via client, writes cache file.
        stats = process_filing(
            _downloading_client(), store, FakeEmbedder(), REF, "NVDA", settings
        )
        assert stats.ok and not stats.cached
        cache = filing_cache_path(settings, REF.cik, REF.accession, REF.primary_document)
        assert cache.exists()
        assert cache.read_bytes() == FIXTURE_HTML  # byte-faithful audit trail

        # Second run: same client raises if contacted -- cache must serve it.
        stats2 = process_filing(
            _cached_client(), store, FakeEmbedder(), REF, "NVDA", settings
        )
        assert stats2.ok and stats2.cached

    def test_chunks_stored_with_full_provenance(self, settings, store):
        process_filing(
            _downloading_client(), store, FakeEmbedder(), REF, "NVDA", settings
        )
        assert store.count() > 0
        hits = store.search(FakeEmbedder().embed(["data center revenue literature"])[0], k=1)
        meta = hits[0].metadata
        assert meta["cik"] == 1045810
        assert meta["ticker"] == "NVDA"
        assert meta["form"] == "10-K"
        assert meta["accession"] == REF.accession
        assert meta["filing_date"] == "2026-02-25"
        assert meta["report_date"] == "2026-01-25"
        assert "chunk_index" in meta

    def test_section_metadata_distinguishes_risk_factors(self, settings, store):
        process_filing(
            _downloading_client(), store, FakeEmbedder(), REF, "NVDA", settings
        )
        hits = store.search(
            FakeEmbedder().embed(["export control compliance international sales"])[0],
            k=1,
            where={"section": "risk_factors"},
        )
        assert hits and hits[0].metadata["section"] == "risk_factors"
        assert "export control" in hits[0].text

    def test_rerun_is_idempotent(self, settings, store):
        process_filing(
            _downloading_client(), store, FakeEmbedder(), REF, "NVDA", settings
        )
        count_after_first = store.count()
        process_filing(
            _cached_client(), store, FakeEmbedder(), REF, "NVDA", settings
        )
        assert store.count() == count_after_first  # upsert, not append

    def test_rerun_replaces_stale_chunks_from_older_parse(self, settings, store):
        """A code change that alters chunking leaves old ids behind; the
        pipeline must delete-then-insert so stale chunks never survive."""
        process_filing(
            _downloading_client(), store, FakeEmbedder(), REF, "NVDA", settings
        )
        # Simulate an older parse's orphan chunk (same accession, id the
        # current parse would never regenerate).
        embedder = FakeEmbedder()
        store.upsert_chunks(
            ids=[f"{REF.accession}:9999"],
            embeddings=embedder.embed(["stale text from an older chunker"])[0:1],
            documents=["stale text from an older chunker"],
            metadatas=[{"accession": REF.accession, "chunk_index": 9999}],
        )
        count_with_orphan = store.count()

        process_filing(
            _cached_client(), store, FakeEmbedder(), REF, "NVDA", settings
        )
        assert store.count() == count_with_orphan - 1  # orphan removed
        leftover = store.search(
            embedder.embed(["stale text from an older chunker"])[0],
            k=5, where={"accession": REF.accession},
        )
        assert all(h.id != f"{REF.accession}:9999" for h in leftover)

    def test_download_failure_recorded_not_raised(self, settings, store):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        client = SECClient(transport=httpx.MockTransport(handler), sleep=lambda _s: None)
        stats = process_filing(client, store, FakeEmbedder(), REF, "NVDA", settings)
        assert not stats.ok
        assert "download" in stats.error
        assert store.count() == 0

    def test_empty_html_yields_zero_chunks_not_crash(self, settings, store):
        stats = process_filing(
            _downloading_client(content=b""), store, FakeEmbedder(), REF, "NVDA", settings
        )
        assert stats.ok
        assert stats.chunks == 0


class TestVectorStore:
    def test_search_orders_by_cosine_distance(self, store):
        embedder = FakeEmbedder()
        texts = [
            "graphics processors and accelerated computing",
            "recipe for tomato pasta sauce with basil",
            "data center demand for accelerators",
        ]
        vectors = embedder.embed(texts)
        store.upsert_chunks(
            ids=["a", "b", "c"],
            embeddings=vectors,
            documents=texts,
            metadatas=[{"topic": t.split()[0]} for t in texts],
        )
        query = embedder.embed(["computing hardware for data centers"])[0]
        hits = store.search(query, k=2)
        # Nearest two are the computing texts; the pasta text is last.
        assert {h.id for h in hits} == {"a", "c"}
        assert hits[0].distance <= hits[1].distance

    def test_metadata_filter_restricts_candidates(self, store):
        embedder = FakeEmbedder()
        store.upsert_chunks(
            ids=["msft", "nvda"],
            embeddings=embedder.embed(["cloud revenue growth", "gpu revenue growth"]),
            documents=["MSFT text", "NVDA text"],
            metadatas=[{"ticker": "MSFT"}, {"ticker": "NVDA"}],
        )
        query = embedder.embed(["revenue growth"])[0]
        hits = store.search(query, k=5, where={"ticker": "NVDA"})
        assert [h.id for h in hits] == ["nvda"]

    def test_multi_key_where_becomes_and(self, store):
        embedder = FakeEmbedder()
        store.upsert_chunks(
            ids=["x", "y"],
            embeddings=embedder.embed(["risk factors text one", "risk factors text two"]),
            documents=["doc x", "doc y"],
            metadatas=[{"ticker": "MSFT", "section": "risk_factors"},
                       {"ticker": "NVDA", "section": "risk_factors"}],
        )
        query = embedder.embed(["risk factors"])[0]
        hits = store.search(query, k=5, where={"ticker": "MSFT", "section": "risk_factors"})
        assert [h.id for h in hits] == ["x"]

    def test_upsert_length_mismatch_raises(self, store):
        with pytest.raises(ValueError, match="equal lengths"):
            store.upsert_chunks(ids=["a"], embeddings=[[0.1]], documents=[], metadatas=[])

    def test_invalid_k_raises(self, store):
        with pytest.raises(ValueError):
            store.search([0.1] * 32, k=0)

    def test_build_where_shapes(self):
        assert _build_where(None) is None
        assert _build_where({}) is None
        assert _build_where({"a": 1}) == {"a": {"$eq": 1}}
        assert _build_where({"a": 1, "b": "x"}) == {"$and": [{"a": {"$eq": 1}}, {"b": {"$eq": "x"}}]}

    def test_delete_filing_removes_only_that_accession(self, store):
        embedder = FakeEmbedder()
        store.upsert_chunks(
            ids=["acc1:0", "acc2:0"],
            embeddings=embedder.embed(["one", "two"]),
            documents=["one", "two"],
            metadatas=[{"accession": "acc1"}, {"accession": "acc2"}],
        )
        removed = store.delete_filing("acc1")
        assert removed == 1
        assert store.count() == 1


class TestFakeEmbedder:
    def test_deterministic_across_instances(self):
        a = FakeEmbedder().embed(["hello world"])[0]
        b = FakeEmbedder().embed(["hello world"])[0]
        assert a == b  # crc32 hashing: stable across instances AND processes

    def test_unit_norm(self):
        vec = FakeEmbedder().embed(["some words here for testing"])[0]
        norm = sum(x * x for x in vec) ** 0.5
        assert abs(norm - 1.0) < 1e-9

    def test_shared_tokens_increase_similarity(self):
        import math

        embedder = FakeEmbedder()
        q, near, far = embedder.embed([
            "data center revenue growth",
            "data center demand growth",
            "recipe tomato basil pasta",
        ])
        cos = lambda a, b: sum(x * y for x, y in zip(a, b))
        assert cos(q, near) > cos(q, far)

    def test_empty_text_returns_unit_vector(self):
        vec = FakeEmbedder().embed([""])[0]
        assert abs(sum(x * x for x in vec) - 1.0) < 1e-9
