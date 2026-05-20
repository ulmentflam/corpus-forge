"""Phase N Wave 3 — backend ``chunk_ids`` filter on ``search_dense`` / ``search_lexical``.

The fast-tier "shortcut" mode needs the storage backends to accept a
``chunk_ids: frozenset[int] | None = None`` keyword on both
``search_dense`` and ``search_lexical`` so the main embedder + lexical
backend run only against the candidate pool surfaced by the fast tier.

Pinned here against the dual-backend fixture so both Postgres + SQLite
implementations get the same contract.

The pre-Wave-3 (no-filter) call site continues to work — the new kwarg
is keyword-only with a ``None`` default.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

import numpy as np

from corpus_forge.backends.base import StorageBackend
from corpus_forge.sources.base import RawDocument

# Mirror test_backend_dual.py — runs SQLite always; Postgres only when Docker is up.

_FAKE_DIM = 8


class _FakeEmbedder:
    """Deterministic embedder.  Same shape as the dual-backend fixture's
    fake embedder so the helpers stay portable.
    """

    name = "fast-filter-fake"
    provider = "fake"
    model_id = "fake-v1"
    dimension = _FAKE_DIM
    normalized = True
    distance = "cosine"

    def _encode_one(self, text: str) -> np.ndarray:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        vec = np.frombuffer(digest[:_FAKE_DIM], dtype=np.uint8).astype(np.float32)
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
    title: str,
    chunk_texts: list[str],
) -> int:
    text = "\n\n".join(chunk_texts)
    raw = RawDocument(
        source_uri=source_uri,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
        text=text,
        title=title,
        modified_at=0.0,
        metadata={},
        labels=[],
    )
    chunks = [(None, ct) for ct in chunk_texts]
    return backend.upsert_document(dataset_id, raw, chunks)


class TestSearchDenseChunkIdsFilter:
    """``search_dense(chunk_ids=...)`` restricts the result set."""

    def test_filter_returns_only_subset(self, storage_backend: StorageBackend) -> None:
        ds_id = _insert_dataset(storage_backend, "fastfilter-dense")
        embedder = _FakeEmbedder()
        eid = storage_backend.register_embedder(embedder)
        _seed_doc(
            storage_backend,
            ds_id,
            "fast://dense.md",
            "fast-dense",
            ["alpha alpha alpha", "beta beta beta", "gamma gamma gamma"],
        )

        missing = list(storage_backend.chunks_missing_embedding(eid))
        # Use distinct vectors so ordering is deterministic.
        vecs = [embedder._encode_one(f"v{i}") for i, _ in enumerate(missing)]
        pairs = [(missing[i][0], vecs[i]) for i in range(len(missing))]
        storage_backend.write_embeddings(eid, pairs)

        # Pick two of the three chunk_ids as the candidate pool.
        keep_ids = {missing[0][0], missing[1][0]}
        excluded_id = missing[2][0]

        hits = storage_backend.search_dense(
            eid,
            vecs[0],
            k=10,
            chunk_ids=frozenset(keep_ids),
        )
        returned_ids = {h.chunk_id for h in hits}
        assert returned_ids.issubset(keep_ids)
        assert excluded_id not in returned_ids

    def test_filter_none_means_no_restriction(self, storage_backend: StorageBackend) -> None:
        """``chunk_ids=None`` (default) preserves pre-Wave-3 behaviour."""
        ds_id = _insert_dataset(storage_backend, "fastfilter-dense-noop")
        embedder = _FakeEmbedder()
        eid = storage_backend.register_embedder(embedder)
        _seed_doc(
            storage_backend,
            ds_id,
            "fast://dense-noop.md",
            "fast-dense-noop",
            ["alpha alpha", "beta beta", "gamma gamma"],
        )

        missing = list(storage_backend.chunks_missing_embedding(eid))
        vecs = [embedder._encode_one(f"v{i}") for i, _ in enumerate(missing)]
        storage_backend.write_embeddings(
            eid, [(missing[i][0], vecs[i]) for i in range(len(missing))]
        )

        hits = storage_backend.search_dense(eid, vecs[0], k=10, chunk_ids=None)
        assert len(hits) == 3

    def test_empty_filter_returns_empty(self, storage_backend: StorageBackend) -> None:
        """An empty frozenset means "nothing matches" — not "no filter"."""
        ds_id = _insert_dataset(storage_backend, "fastfilter-dense-empty")
        embedder = _FakeEmbedder()
        eid = storage_backend.register_embedder(embedder)
        _seed_doc(
            storage_backend,
            ds_id,
            "fast://dense-empty.md",
            "fast-dense-empty",
            ["alpha alpha"],
        )
        missing = list(storage_backend.chunks_missing_embedding(eid))
        vec = embedder._encode_one("v0")
        storage_backend.write_embeddings(eid, [(missing[0][0], vec)])

        hits = storage_backend.search_dense(eid, vec, k=10, chunk_ids=frozenset())
        assert hits == []


class TestSearchLexicalChunkIdsFilter:
    """``search_lexical(chunk_ids=...)`` restricts the FTS result set."""

    def test_filter_returns_only_subset(self, storage_backend: StorageBackend) -> None:
        ds_id = _insert_dataset(storage_backend, "fastfilter-lex")
        _seed_doc(
            storage_backend,
            ds_id,
            "fast://lex.md",
            "fast-lex",
            [
                "the quick brown fox jumps over the lazy dog",
                "another brown fox sighting on the trail",
                "elephants never forget their friends",
            ],
        )

        # Get all chunk_ids from a no-filter search to identify the IDs.
        baseline = storage_backend.search_lexical("brown fox", k=10)
        baseline_ids = sorted({h.chunk_id for h in baseline})
        assert len(baseline_ids) >= 2  # both brown-fox chunks

        # Keep only the first chunk_id.
        keep = {baseline_ids[0]}
        hits = storage_backend.search_lexical("brown fox", k=10, chunk_ids=frozenset(keep))
        returned_ids = {h.chunk_id for h in hits}
        assert returned_ids.issubset(keep)

    def test_filter_none_means_no_restriction(self, storage_backend: StorageBackend) -> None:
        ds_id = _insert_dataset(storage_backend, "fastfilter-lex-noop")
        _seed_doc(
            storage_backend,
            ds_id,
            "fast://lex-noop.md",
            "fast-lex-noop",
            ["the quick brown fox jumps", "another brown fox"],
        )
        hits = storage_backend.search_lexical("brown fox", k=10, chunk_ids=None)
        assert len(hits) >= 1

    def test_empty_filter_returns_empty(self, storage_backend: StorageBackend) -> None:
        ds_id = _insert_dataset(storage_backend, "fastfilter-lex-empty")
        _seed_doc(
            storage_backend,
            ds_id,
            "fast://lex-empty.md",
            "fast-lex-empty",
            ["the quick brown fox"],
        )
        hits = storage_backend.search_lexical("brown fox", k=10, chunk_ids=frozenset())
        assert hits == []
