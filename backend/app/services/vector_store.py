"""ChromaDB vector store for filing text chunks.

A thin, opinionated wrapper. Design decisions:

- SINGLE COLLECTION ("filing_text") with per-chunk METADATA (cik, ticker,
  form, accession, section, dates) rather than one collection per company.
  Metadata filters answer "only MSFT 10-K MD&A" just as well as separate
  collections would, while keeping cross-company queries trivial and the
  schema fixed as companies are added.

- DETERMINISTIC IDs: `{accession}:{chunk_index}`. Re-ingesting a filing
  UPSERTS (replaces) its chunks instead of duplicating them -- the same
  idempotency rule as fact ingestion in M2. A re-parse with different
  chunking parameters replaces cleanly because the whole filing's chunks
  are re-embedded together.

- COSINE SPACE: set at collection creation (hnsw:space=cosine). Must match
  the embeddings being unit-normalized (see embeddings.py); mixing L2
  storage with unnormalized vectors silently degrades retrieval quality,
  which is why the pairing is asserted in tests.

- MULTI-KEY FILTERS: Chroma requires explicit $and for >1 condition;
  `where=` accepts a plain equality dict and builds that for the caller.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


@dataclass(frozen=True)
class SearchHit:
    """One retrieval result: the chunk text, its provenance, and distance."""

    id: str
    text: str
    metadata: dict[str, Any]
    distance: float  # cosine distance: 0.0 identical, ~2.0 opposite


class VectorStore:
    def __init__(
        self,
        persist_dir: Path | str | None = None,
        collection_name: str = "filing_text",
        client: Any = None,
    ):
        if client is None:
            import chromadb  # local import: keeps test collection fast

            client = chromadb.PersistentClient(path=str(persist_dir))
        self._client = client
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def upsert_chunks(
        self,
        ids: Sequence[str],
        embeddings: Sequence[Sequence[float]],
        documents: Sequence[str],
        metadatas: Sequence[dict[str, Any]],
    ) -> None:
        """Insert or replace chunks (idempotent by id)."""
        if not (len(ids) == len(embeddings) == len(documents) == len(metadatas)):
            raise ValueError(
                "ids/embeddings/documents/metadatas must have equal lengths: "
                f"{len(ids)}/{len(embeddings)}/{len(documents)}/{len(metadatas)}"
            )
        if not ids:
            return
        self._collection.upsert(
            ids=list(ids),
            embeddings=[list(map(float, e)) for e in embeddings],
            documents=list(documents),
            metadatas=[dict(m) for m in metadatas],
        )

    def search(
        self,
        embedding: Sequence[float],
        k: int = 5,
        where: dict[str, Any] | None = None,
    ) -> list[SearchHit]:
        """k nearest chunks (cosine), optionally filtered by metadata equality.

        `where` is a flat dict of equality conditions, e.g.
        {"ticker": "MSFT", "section": "mdna"} -- converted to Chroma's
        explicit $and form when it has more than one key.
        """
        if k <= 0:
            raise ValueError(f"k must be positive, got {k}")
        chroma_where = _build_where(where)
        raw = self._collection.query(
            query_embeddings=[list(map(float, embedding))],
            n_results=k,
            where=chroma_where,
            include=["documents", "metadatas", "distances"],
        )
        hits: list[SearchHit] = []
        # Chroma returns per-query nested lists; we issue one query at a time.
        ids = raw.get("ids", [[]])[0] or []
        docs = raw.get("documents", [[]])[0] or []
        metas = raw.get("metadatas", [[]])[0] or []
        dists = raw.get("distances", [[]])[0] or []
        for i, chunk_id in enumerate(ids):
            hits.append(SearchHit(
                id=chunk_id,
                text=docs[i] if i < len(docs) else "",
                metadata=metas[i] if i < len(metas) else {},
                distance=dists[i] if i < len(dists) else float("inf"),
            ))
        return hits

    def count(self) -> int:
        return int(self._collection.count())

    def __enter__(self) -> "VectorStore":
        return self

    def __exit__(self, *exc: object) -> None:
        # Chroma's PersistentClient owns its own lifecycle; nothing to close.
        # The context-manager protocol is supported so scripts can scope the
        # store lexically like SECClient.
        return None

    def delete_filing(self, accession: str) -> int:
        """Remove every chunk of one filing (by accession metadata)."""
        existing = self._collection.get(where={"accession": {"$eq": accession}})
        ids = existing.get("ids", [])
        if ids:
            self._collection.delete(ids=ids)
        return len(ids)


def _build_where(where: dict[str, Any] | None) -> dict[str, Any] | None:
    """Flat equality dict -> Chroma where-clause (explicit $and if >1 key)."""
    if not where:
        return None
    conditions = [{key: {"$eq": value}} for key, value in where.items()]
    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions}
