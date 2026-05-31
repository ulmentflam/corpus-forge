"""Phase N Wave 3 — second embedder table coexistence.

A fast-tier embedder is a regular embedder registered alongside the
main embedder.  The backend protocol already keys per-embedder tables
on the embedder row id (``embedders.id`` → ``embeddings_<name>``), so
no schema migration is required.  This test pins the contract:

- Registering a second embedder under a distinct ``name`` returns a
  distinct ``embedder_id``.
- A per-embedder table is provisioned for each.
- ``search_dense(main_id, ...)`` and ``search_dense(fast_id, ...)``
  return INDEPENDENT result sets keyed off their respective vectors.

Tested against the dual-backend fixture so both Postgres + SQLite
implementations are covered.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

import numpy as np

from corpus_forge.backends.base import StorageBackend
from corpus_forge.sources.base import RawDocument


class _FakeEmbedder:
    """Stable deterministic embedder with configurable dimension."""

    provider = "fake"
    normalized = True
    distance = "cosine"

    def __init__(self, *, name: str, dim: int) -> None:
        self.name = name
        self.model_id = f"fake-{name}"
        self.dimension = dim

    def _encode_one(self, text: str) -> np.ndarray:
        digest = hashlib.sha256((self.name + text).encode("utf-8")).digest()
        # Generate enough bytes for `dim` floats by repeating digest.
        raw = (digest * ((self.dimension // len(digest)) + 1))[: self.dimension]
        vec = np.frombuffer(raw, dtype=np.uint8).astype(np.float32)
        vec = (vec + 1.0) / 256.0
        return vec / float(np.linalg.norm(vec))

    def encode(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        return np.stack([self._encode_one(t) for t in texts])

    def warmup(self) -> None:  # pragma: no cover — protocol only
        pass


def _insert_dataset(backend: StorageBackend, name: str) -> int:
    return backend.get_or_create_dataset(name=name, kind="text", description="")


def _seed_doc(
    backend: StorageBackend,
    dataset_id: int,
    source_uri: str,
    chunk_texts: list[str],
    *,
    embedder_ids: list[int] | None = None,
) -> int:
    text = "\n\n".join(chunk_texts)
    raw = RawDocument(
        source_uri=source_uri,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
        text=text,
        title=source_uri,
        modified_at=0.0,
        metadata={},
        labels=[],
    )
    chunks = [(None, ct) for ct in chunk_texts]
    return backend.upsert_document(dataset_id, raw, chunks, embedder_ids=embedder_ids)


def test_two_embedders_get_distinct_ids(storage_backend: StorageBackend) -> None:
    main = _FakeEmbedder(name="main", dim=384)
    fast = _FakeEmbedder(name="fast", dim=256)
    main_id = storage_backend.register_embedder(main)
    fast_id = storage_backend.register_embedder(fast)
    assert main_id != fast_id


def test_two_embedders_serve_independent_dense_search(
    storage_backend: StorageBackend,
) -> None:
    """Each embedder has its own table; searches return INDEPENDENT hits."""
    ds_id = _insert_dataset(storage_backend, "fast-tier-coexist")

    main = _FakeEmbedder(name="main", dim=8)
    fast = _FakeEmbedder(name="fast", dim=16)
    main_id = storage_backend.register_embedder(main)
    fast_id = storage_backend.register_embedder(fast)

    _seed_doc(
        storage_backend,
        ds_id,
        "fast://coexist.md",
        ["alpha alpha alpha", "beta beta beta", "gamma gamma gamma"],
        embedder_ids=[main_id, fast_id],
    )

    # Embed each chunk in BOTH tables using the embedder's own vectors.
    missing_main = list(storage_backend.chunks_missing_embedding(main_id))
    main_pairs = [(cid, main._encode_one(text)) for cid, text, _ in missing_main]
    storage_backend.write_embeddings(main_id, main_pairs)

    missing_fast = list(storage_backend.chunks_missing_embedding(fast_id))
    fast_pairs = [(cid, fast._encode_one(text)) for cid, text, _ in missing_fast]
    storage_backend.write_embeddings(fast_id, fast_pairs)

    # The first chunk in each.
    target_main = main_pairs[0][1]
    target_fast = fast_pairs[0][1]

    main_hits = storage_backend.search_dense(main_id, target_main, k=3)
    fast_hits = storage_backend.search_dense(fast_id, target_fast, k=3)

    # Both return three results from their respective tables.
    assert len(main_hits) == 3
    assert len(fast_hits) == 3
    # Top-hit chunk_id matches the seeded target in each table.
    assert main_hits[0].chunk_id == main_pairs[0][0]
    assert fast_hits[0].chunk_id == fast_pairs[0][0]
