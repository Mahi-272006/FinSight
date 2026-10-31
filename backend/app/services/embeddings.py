"""Local text embeddings for the filing RAG pipeline.

WHY LOCAL: the zero-cost policy forbids paid embedding APIs. The model is
sentence-transformers/all-MiniLM-L6-v2 (~90 MB, runs on CPU in seconds for
a filing's worth of chunks). First run downloads it from HuggingFace once;
after that it is fully offline.

WHY AN `Embedder` ABSTRACTS THE MODEL: unit tests must never load a
torch model (slow, downloads weights, makes pytest require network). Tests
inject `FakeEmbedder` -- a deterministic, dependency-free stand-in whose
similarity behavior is "texts sharing tokens are closer", enough to test
pipeline wiring and retrieval end-to-end. The live scripts use the real
embedder.

NORMALIZATION: `SentenceTransformerEmbedder` L2-normalizes vectors, which
makes COSINE similarity equivalent to dot product -- ChromaDB is then
configured with hnsw:space=cosine (see vector_store). Normalizing here (not
relying on the store) keeps the invariant local to the thing that produces
vectors: any consumer -- Chroma, a numpy dot product, a test -- sees unit
vectors.

DETERMINISM: the real model is deterministic for a fixed model version on
CPU in practice; FakeEmbedder is deterministic BY CONSTRUCTION (it hashes
tokens with crc32, never Python's salted `hash()`, so vectors are identical
across processes -- a subtlety that would otherwise make tests flaky).
"""

import math
import zlib
from typing import Protocol, Sequence

MINILM_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


class Embedder(Protocol):
    """Anything that turns texts into fixed-dimension vectors."""

    dimension: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one vector per input text, same order."""
        ...


class SentenceTransformerEmbedder:
    """Real embedder backed by sentence-transformers (CPU by default).

    The model is loaded lazily on first use, not at import time, so
    importing this module (e.g. from tests) never triggers a download.
    """

    def __init__(
        self,
        model_name: str = MINILM_MODEL_NAME,
        device: str = "cpu",
        batch_size: int = 32,
    ):
        # Local import: torch import costs ~2-5s; keep it out of module
        # scope so test collection stays fast.
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name, device=device)
        self._batch_size = batch_size
        self.dimension = int(self._model.get_sentence_embedding_dimension())

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._model.encode(
            list(texts),
            batch_size=self._batch_size,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [[float(x) for x in row] for row in vectors]


class FakeEmbedder:
    """Deterministic stand-in for tests: bag-of-token-hashes vector.

    Properties that make it useful for wiring tests:
    - identical text -> identical vector (crc32 hashing, process-stable);
    - texts sharing tokens -> higher cosine similarity than disjoint ones,
      so retrieval-order tests are meaningful;
    - always L2-normalized (like the real embedder), so cosine-space
      assumptions hold in tests too.
    """

    def __init__(self, dimension: int = 32):
        self.dimension = dimension

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dimension
        for token in text.lower().split():
            # crc32 is stable across processes; Python's hash() is salted
            # per process and would make vectors differ between test runs.
            vec[zlib.crc32(token.encode("utf-8")) % self.dimension] += 1.0
        norm = math.sqrt(sum(x * x for x in vec))
        if norm == 0:
            # Empty/whitespace-only text: return a fixed unit vector rather
            # than all zeros (zero vectors have undefined cosine similarity).
            vec[0] = 1.0
            return vec
        return [x / norm for x in vec]
