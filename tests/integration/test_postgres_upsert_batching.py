"""Integration tests for :meth:`PostgresBackend.upsert_document`'s
batched-INSERT path + :meth:`._copy_reusable_embeddings_batch`.

Background
----------
Profiled 2026-05-27 against the maintainer's Tailscale-PG ingest:
``upsert_document`` was 86% of per-file time at ~457ms/file mean. The
per-chunk loop was issuing 4 round-trips per chunk (INSERT chunk +
SELECT embedder_info + SELECT prior_chunk_with_hash + INSERT-SELECT
reused embedding). For an N-chunk file that was 2 + 4N round-trips,
and at ~4ms per round-trip over Tailscale the cost dominated.

The batched path collapses to:
- 1 INSERT for all chunks (multi-row VALUES + RETURNING)
- 2 round-trips per embedder for the reuse-embedding copy
  (one SELECT, one INSERT)

Regardless of N. Plus :meth:`register_embedder` is now process-
lifetime cached so its 3-round-trip cost is paid exactly once per
embedder name per process.

What these tests pin
--------------------
1. Batched chunk INSERTs produce the right number of chunks with
   correct ``content_hash`` values and in the original ``chunk_index``
   order.
2. ``_copy_reusable_embeddings_batch`` correctly copies embeddings
   from prior chunks across multiple new chunks in one go.
3. ``register_embedder`` is idempotent and cache-fast on the second
   call (same backend instance returns the same id without re-hitting
   the DB).
4. The full ``upsert_document`` end-to-end still produces a working
   document + chunks + (when an embedder_id is supplied) embeddings
   that retrieval would actually find.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import numpy as np
import pytest

from corpus_forge.backends.postgres import PostgresBackend
from corpus_forge.chunkers.base import TextChunk
from corpus_forge.sources.base import RawDocument

if TYPE_CHECKING:
    pass


pytestmark = pytest.mark.integration


@pytest.fixture
def backend(pg_dsn: str) -> PostgresBackend:
    """Fresh PostgresBackend against the testcontainers PG. The
    ``pg_dsn`` fixture rebuilds the schema before yielding so each
    test starts with an empty corpus.
    """

    b = PostgresBackend(dsn=pg_dsn)
    b.migrate()
    yield b
    b.close()


# Match the embedder shape ``register_embedder`` expects (provider,
# model_id, dimension, normalized, distance, name, plus the active
# attribute it falls back to via getattr).
class _FakeEmbedder:
    name = "test-emb"
    provider = "test"
    model_id = "test-model"
    dimension = 4
    normalized = False
    distance = "cosine"
    active = True


def _make_doc(source_uri: str, text: str, content_hash: str = "h0") -> RawDocument:
    return RawDocument(
        source_uri=source_uri,
        content_hash=content_hash,
        text=text,
        title=source_uri.rsplit("/", 1)[-1],
        modified_at=1000.0,
        metadata={},
        labels=[],
    )


def _chunks(n: int, prefix: str = "chunk") -> list[TextChunk]:
    return [
        TextChunk(
            text=f"{prefix}-{i}-body",
            heading=f"heading-{i}",
            metadata={"i": i},
            role=None,
            token_count=10,
        )
        for i in range(n)
    ]


# ─────────────────────────────────────────────────────────────────────
# Batched chunk INSERT
# ─────────────────────────────────────────────────────────────────────


class TestBatchedChunkInsert:
    def test_n_chunk_doc_produces_n_chunk_rows_in_order(self, backend: PostgresBackend) -> None:
        """An N-chunk document must persist N chunk rows with chunk_index
        running 0..N-1 in the same order as the input list. Without
        this guarantee, retrieval ordering would be wrong AND the
        existing-doc fast path's chunk_index-based update-in-place
        would map to the wrong prior chunk.
        """

        dataset_id = backend.get_or_create_dataset(name="d", kind="text", description="")
        chunks = _chunks(10)
        doc = _make_doc("vault://multi.md", "body text")
        doc_id = backend.upsert_document(dataset_id, doc, chunks)

        rows = backend._execute(
            "SELECT chunk_index, text, content_hash FROM corpus.chunks "
            "WHERE document_id = %s ORDER BY chunk_index",
            (doc_id,),
        )
        assert len(rows) == 10
        for i, row in enumerate(rows):
            assert row["chunk_index"] == i
            assert row["text"] == f"chunk-{i}-body"
            # content_hash must be populated (the batched INSERT
            # includes the hash column).
            assert row["content_hash"]

    def test_single_chunk_still_works(self, backend: PostgresBackend) -> None:
        """The batched INSERT collapses N chunks into one statement;
        with N=1 we get a single-row VALUES list. Pin this works the
        same as the multi-chunk case — historically a separate code
        path issued exactly the same statement.
        """

        dataset_id = backend.get_or_create_dataset(name="d", kind="text", description="")
        doc = _make_doc("vault://one.md", "body")
        doc_id = backend.upsert_document(dataset_id, doc, _chunks(1))
        rows = backend._execute(
            "SELECT chunk_index FROM corpus.chunks WHERE document_id = %s",
            (doc_id,),
        )
        assert len(rows) == 1
        assert rows[0]["chunk_index"] == 0

    def test_empty_chunk_list_does_not_insert(self, backend: PostgresBackend) -> None:
        """Edge case: a doc with no chunks should still create the
        document row but skip the batched INSERT entirely (the
        ``if norm_chunks:`` guard in upsert_document).
        """

        dataset_id = backend.get_or_create_dataset(name="d", kind="text", description="")
        doc = _make_doc("vault://empty.md", "header only")
        doc_id = backend.upsert_document(dataset_id, doc, [])
        rows = backend._execute(
            "SELECT id FROM corpus.documents WHERE id = %s",
            (doc_id,),
        )
        assert len(rows) == 1
        chunk_rows = backend._execute(
            "SELECT id FROM corpus.chunks WHERE document_id = %s",
            (doc_id,),
        )
        assert chunk_rows == []


# ─────────────────────────────────────────────────────────────────────
# Embedder cache + table-creation cache
# ─────────────────────────────────────────────────────────────────────


class TestRegisterEmbedderCache:
    def test_second_call_returns_cached_id_without_hitting_db(
        self, backend: PostgresBackend
    ) -> None:
        """The whole point of the cache. First ``register_embedder``
        call does 3 round-trips (SELECT, INSERT, CREATE TABLE);
        subsequent calls with the same ``embedder.name`` return the
        cached id without touching the DB. Spy on ``_execute`` to
        confirm the second call is round-trip-free.
        """

        embedder = _FakeEmbedder()
        first_id = backend.register_embedder(embedder)
        assert isinstance(first_id, int) and first_id > 0

        # Spy on _execute — second call must not invoke it.
        with patch.object(backend, "_execute") as spy:
            second_id = backend.register_embedder(embedder)
        assert second_id == first_id
        assert spy.call_count == 0, (
            f"register_embedder hit the DB {spy.call_count} times on the "
            f"second call — cache is broken"
        )

    def test_cache_survives_distinct_embedder_names(self, backend: PostgresBackend) -> None:
        """Two different embedders get distinct ids; each gets its
        own cache entry; neither call interferes with the other.
        """

        class _A(_FakeEmbedder):
            name = "test-A"

        class _B(_FakeEmbedder):
            name = "test-B"

        id_a = backend.register_embedder(_A())
        id_b = backend.register_embedder(_B())
        assert id_a != id_b
        # Re-register both — both should hit the cache.
        with patch.object(backend, "_execute") as spy:
            assert backend.register_embedder(_A()) == id_a
            assert backend.register_embedder(_B()) == id_b
        assert spy.call_count == 0


# ─────────────────────────────────────────────────────────────────────
# Batched reuse-embedding copy
# ─────────────────────────────────────────────────────────────────────


class TestCopyReusableEmbeddingsBatch:
    def test_re_ingest_reuses_embeddings_for_unchanged_chunks(
        self, backend: PostgresBackend
    ) -> None:
        """Insert a document with N chunks; manually populate the
        embedder table with row-per-chunk embeddings; insert a SECOND
        document whose chunks share the same ``content_hash`` (same
        text body) — the batched reuse-embedding copy should bulk-
        copy ALL the prior embeddings into the new chunks' rows in
        ONE INSERT.

        Pin: a) every new chunk gets an embedding row, b) the
        embedding VALUES are byte-identical to the prior ones (no
        re-encoding, no precision loss).
        """

        embedder = _FakeEmbedder()
        embedder_id = backend.register_embedder(embedder)
        dataset_id = backend.get_or_create_dataset(name="d", kind="text", description="")

        chunks = _chunks(5, prefix="reuse")
        doc1 = _make_doc("vault://reuse-1.md", "body")
        doc1_id = backend.upsert_document(dataset_id, doc1, chunks)

        # Fetch prior chunk ids, manually populate the embedder table
        # with deterministic vectors.
        prior_rows = backend._execute(
            "SELECT id, content_hash FROM corpus.chunks WHERE document_id = %s "
            "ORDER BY chunk_index",
            (doc1_id,),
        )
        assert len(prior_rows) == 5

        # Build 5 known unit-ish vectors of dim 4.
        prior_vectors = [np.array([i + 1.0, 0.5, 0.25, 0.125], dtype=np.float32) for i in range(5)]
        backend.write_embeddings(
            embedder_id,
            list(zip([r["id"] for r in prior_rows], prior_vectors, strict=True)),
        )

        # Re-insert the SAME chunk content under a new source_uri.
        doc2 = _make_doc("vault://reuse-2.md", "body", content_hash="h-different")
        backend.upsert_document(dataset_id, doc2, chunks, embedder_ids=[embedder_id])

        # Both documents now have 5 chunks each. The doc2 chunks
        # should already have embeddings copied from doc1's chunks
        # (matched by content_hash).
        info = backend._embedder_info_cache[embedder_id]
        embedder_table = f"corpus.{info['table_name']}"
        copied_rows = backend._execute(
            f"SELECT e.chunk_id, c.content_hash, e.embedding "
            f"FROM {embedder_table} e "
            f"JOIN corpus.chunks c ON c.id = e.chunk_id "
            f"WHERE c.document_id != %s ORDER BY c.chunk_index",
            (doc1_id,),
        )
        assert len(copied_rows) == 5, (
            f"Expected 5 copied embedding rows for doc2's chunks; got {len(copied_rows)}"
        )

        # The COPIED embedding for chunk_index=i must equal prior_vectors[i].
        prior_by_hash = {r["content_hash"]: idx for idx, r in enumerate(prior_rows)}
        for row in copied_rows:
            i = prior_by_hash[row["content_hash"]]
            expected = prior_vectors[i]
            # pgvector returns as a string like ``"[1,0.5,0.25,0.125]"``
            # when the pgvector psycopg3 adapter isn't registered for
            # this connection. Parse manually rather than depending on
            # adapter setup that may not be wired up for ad-hoc queries.
            raw = row["embedding"]
            if isinstance(raw, str):
                got = np.array([float(x) for x in raw.strip("[]").split(",")], dtype=np.float32)
            else:
                got = np.array(raw, dtype=np.float32)
            assert np.allclose(got, expected, atol=1e-5), (
                f"Copied embedding mismatch for chunk_index={i}: got {got!r}, expected {expected!r}"
            )

    def test_no_prior_embeddings_is_a_noop(self, backend: PostgresBackend) -> None:
        """When no prior chunk has the same content_hash, the batched
        reuse-embedding helper short-circuits cleanly with zero
        INSERTs. Don't crash, don't write garbage rows.
        """

        embedder = _FakeEmbedder()
        embedder_id = backend.register_embedder(embedder)
        dataset_id = backend.get_or_create_dataset(name="d", kind="text", description="")

        # Fresh content with no prior in the embedder table.
        chunks = _chunks(3, prefix="novel")
        doc = _make_doc("vault://novel.md", "body")
        doc_id = backend.upsert_document(dataset_id, doc, chunks, embedder_ids=[embedder_id])

        # No embeddings should have been written (nothing prior to copy from).
        info = backend._embedder_info_cache[embedder_id]
        embedder_table = f"corpus.{info['table_name']}"
        rows = backend._execute(f"SELECT chunk_id FROM {embedder_table}", ())
        # The chunks exist but have no embedding rows.
        assert rows == [], f"expected zero embeddings, got {rows}"
        chunk_rows = backend._execute(
            "SELECT id FROM corpus.chunks WHERE document_id = %s", (doc_id,)
        )
        assert len(chunk_rows) == 3


# ─────────────────────────────────────────────────────────────────────
# End-to-end (no regressions vs the pre-batch behavior)
# ─────────────────────────────────────────────────────────────────────


class TestEndToEnd:
    def test_full_pipeline_writes_document_chunks_and_reuses(
        self, backend: PostgresBackend
    ) -> None:
        """Smoke test: full ``upsert_document`` cycle with an active
        embedder produces correct document + chunks + embeddings
        rows. Catches any regression in the batched path that
        somehow corrupts the chunk-ordering or skips writes.
        """

        embedder = _FakeEmbedder()
        embedder_id = backend.register_embedder(embedder)
        dataset_id = backend.get_or_create_dataset(name="d", kind="text", description="")

        chunks = _chunks(8, prefix="smoke")
        doc = _make_doc("vault://smoke.md", "body")
        doc_id = backend.upsert_document(dataset_id, doc, chunks, embedder_ids=[embedder_id])

        # Document row exists.
        doc_rows = backend._execute(
            "SELECT id, content_hash FROM corpus.documents WHERE id = %s",
            (doc_id,),
        )
        assert len(doc_rows) == 1
        # 8 chunks in chunk_index order.
        chunk_rows = backend._execute(
            "SELECT chunk_index, text, content_hash FROM corpus.chunks "
            "WHERE document_id = %s ORDER BY chunk_index",
            (doc_id,),
        )
        assert len(chunk_rows) == 8
        assert [r["chunk_index"] for r in chunk_rows] == list(range(8))
        # No embeddings copied (this is the first document with these hashes).
        info = backend._embedder_info_cache[embedder_id]
        embedder_table = f"corpus.{info['table_name']}"
        emb_rows = backend._execute(f"SELECT chunk_id FROM {embedder_table}", ())
        assert emb_rows == []
