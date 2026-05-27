"""Tests for _copy_reusable_embeddings on PostgresBackend."""

from unittest.mock import MagicMock, patch

from corpus_forge.backends.postgres import PostgresBackend
from corpus_forge.sources.base import RawDocument


class TestCopyReusableEmbeddings:
    """Tests for _copy_reusable_embeddings helper."""

    def test_returns_empty_set_when_no_prior_chunk_shares_hash(self):
        with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
            backend = PostgresBackend.__new__(PostgresBackend)
            backend._execute = MagicMock(
                side_effect=[
                    [{"name": "embed_a", "table_name": "embeddings_embed_a"}],
                    [{"name": "embed_b", "table_name": "embeddings_embed_b"}],
                    [],
                    [],
                ]
            )

            result = backend._copy_reusable_embeddings(
                new_chunk_id=10,
                content_hash="abc123",
                embedder_ids=[1, 2],
                cache={},
            )

            assert result == set()

    def test_copies_vector_row_from_prior_chunk_when_hash_matches(self):
        with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
            backend = PostgresBackend.__new__(PostgresBackend)
            backend._execute = MagicMock(
                side_effect=[
                    [{"name": "embed_a", "table_name": "embeddings_embed_a"}],
                    [{"chunk_id": 5}],
                    [],
                ]
            )

            result = backend._copy_reusable_embeddings(
                new_chunk_id=10,
                content_hash="abc123",
                embedder_ids=[1],
                cache={},
            )

            assert result == {1}
            insert_call = backend._execute.call_args_list[-1]
            sql = insert_call[0][0]
            assert "INSERT" in sql.upper()
            assert "SELECT" in sql.upper()

    def test_cache_prevents_repeat_select(self):
        with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
            backend = PostgresBackend.__new__(PostgresBackend)
            backend._execute = MagicMock(
                side_effect=[
                    [{"name": "embed_a", "table_name": "embeddings_embed_a"}],
                    [],
                ]
            )

            result = backend._copy_reusable_embeddings(
                new_chunk_id=10,
                content_hash="abc123",
                embedder_ids=[1],
                cache={("abc123", 1): 5},
            )

            assert result == {1}
            assert backend._execute.call_count == 2

    def test_returns_reused_embedder_ids_subset(self):
        with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
            backend = PostgresBackend.__new__(PostgresBackend)
            backend._execute = MagicMock(
                side_effect=[
                    [{"name": "embed_a", "table_name": "embeddings_embed_a"}],
                    [{"name": "embed_b", "table_name": "embeddings_embed_b"}],
                    [{"name": "embed_c", "table_name": "embeddings_embed_c"}],
                    [{"chunk_id": 5}],
                    [],
                    [{"chunk_id": 7}],
                    [],
                    [],
                ]
            )

            result = backend._copy_reusable_embeddings(
                new_chunk_id=10,
                content_hash="abc123",
                embedder_ids=[1, 2, 3],
                cache={},
            )

            assert result == {1, 3}
            assert 2 not in result


class TestUpsertDocumentEmbedderIds:
    """Tests for upsert_document embedder_ids kwarg (P0-06)."""

    def test_embedder_ids_none_works_as_before(self):
        with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
            backend = PostgresBackend.__new__(PostgresBackend)
            backend._execute = MagicMock(
                side_effect=[
                    [],
                    [{"id": 5}],
                    [],
                ]
            )

            doc = RawDocument(
                source_uri="vault://test.md",
                content_hash="abc123",
                text="# Test\n\nContent.",
                title="Test",
                modified_at=1000.0,
                metadata={},
                labels=[],
            )
            result = backend.upsert_document(1, doc, [("# Test", "Content.")], embedder_ids=None)
            assert result == 5

    def test_embedder_ids_empty_list_calls_copy_reusable(self):
        """Both ``embedder_ids=None`` and ``embedder_ids=[]`` skip the
        reuse-copy code path entirely.

        Updated 2026-05-27 for the batched-INSERT refactor: chunk
        INSERTs collapse into ONE call returning all rows with
        ``id`` + ``content_hash`` (was N per-chunk INSERTs), and
        the reuse-copy is now ``_copy_reusable_embeddings_batch``
        called at most ONCE per document (was per-chunk).
        ``upsert_document`` guards the call with ``if
        embedder_ids:``, which is falsy for BOTH ``None`` and
        ``[]`` — so ``_copy_reusable_embeddings_batch`` is not
        invoked in either case. This is a deliberate behavior
        change from the old per-chunk path, where an empty list
        still called ``_copy_reusable_embeddings`` once per chunk
        (a no-op loop because the inner ``for embedder_id in
        embedder_ids`` had nothing to iterate over). The new
        behavior is more efficient AND semantically clearer — an
        empty embedder list means "no embedders to copy from", so
        there's nothing for the batch helper to do. The test name
        is kept from the pre-refactor era for git-blame
        continuity; the docstring and assertion below describe
        the actual current contract.
        """

        with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
            backend = PostgresBackend.__new__(PostgresBackend)
            backend._execute = MagicMock(
                side_effect=[
                    [],  # SELECT existing document (none)
                    [{"id": 5}],  # INSERT documents RETURNING id
                    # Batched chunk INSERT: returns one row per chunk
                    # with id + content_hash.
                    [
                        {"id": 101, "content_hash": "hashA"},
                        {"id": 102, "content_hash": "hashB"},
                    ],
                ]
            )
            backend._copy_reusable_embeddings_batch = MagicMock(return_value=None)

            doc = RawDocument(
                source_uri="vault://test.md",
                content_hash="abc123",
                text="# Test\n\nContent.",
                title="Test",
                modified_at=1000.0,
                metadata={},
                labels=[],
            )
            backend.upsert_document(
                1,
                doc,
                [("# Test", "Chunk A"), ("", "Chunk B")],
                embedder_ids=[],
            )

            # Both ``None`` and ``[]`` are falsy, so the batched
            # helper is NOT called in the new path (the ``if
            # embedder_ids:`` guard in upsert_document short-
            # circuits on either). This unifies the two skip cases
            # that used to be subtly different under the old per-
            # chunk path, where an empty list still called
            # ``_copy_reusable_embeddings`` once per chunk (a
            # no-op loop because the inner ``for embedder_id in
            # embedder_ids`` had nothing to iterate over). The
            # new behavior is more efficient AND semantically
            # clearer — an empty embedder list means "no embedders
            # to copy from", so there's nothing for the batch
            # helper to do.
            assert backend._copy_reusable_embeddings_batch.call_count == 0

    def test_reuse_batch_called_once_with_all_chunks(self):
        """The 2026-05-27 batched refactor replaces the per-chunk
        ``_copy_reusable_embeddings`` loop (one call per chunk +
        shared cache dict across calls) with a single
        ``_copy_reusable_embeddings_batch`` call that takes a list
        of ``(chunk_id, content_hash)`` tuples and handles dedup
        internally via a single ``SELECT DISTINCT ON
        (content_hash)``.

        Pin: the batch helper is invoked EXACTLY ONCE per
        ``upsert_document`` call, with all chunks' (id, hash)
        tuples in input order — no per-chunk loop, no shared cache
        object to coordinate.
        """

        with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
            backend = PostgresBackend.__new__(PostgresBackend)
            backend._execute = MagicMock(
                side_effect=[
                    [],
                    [{"id": 5}],
                    [
                        {"id": 101, "content_hash": "hashA"},
                        {"id": 102, "content_hash": "hashB"},
                    ],
                ]
            )
            backend._copy_reusable_embeddings_batch = MagicMock(return_value=None)

            doc = RawDocument(
                source_uri="vault://test.md",
                content_hash="abc123",
                text="# Test\n\nContent.",
                title="Test",
                modified_at=1000.0,
                metadata={},
                labels=[],
            )
            backend.upsert_document(
                1,
                doc,
                [("# Test", "Chunk A"), ("", "Chunk B")],
                embedder_ids=[42],
            )

            # ONE call to the batched helper covers all chunks.
            assert backend._copy_reusable_embeddings_batch.call_count == 1
            args = backend._copy_reusable_embeddings_batch.call_args[0]
            new_chunks, embedder_ids = args
            assert new_chunks == [(101, "hashA"), (102, "hashB")]
            assert embedder_ids == [42]

    def test_embedder_ids_passed_through_to_batch(self):
        """``embedder_ids`` must flow from the caller through to the
        batched reuse-copy helper unmodified. Without this contract,
        ``ingest_one`` couldn't drive which embedders see the
        copied vectors.
        """

        with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
            backend = PostgresBackend.__new__(PostgresBackend)
            backend._execute = MagicMock(
                side_effect=[
                    [],
                    [{"id": 5}],
                    [{"id": 101, "content_hash": "hashA"}],
                ]
            )
            backend._copy_reusable_embeddings_batch = MagicMock(return_value=None)

            doc = RawDocument(
                source_uri="vault://test.md",
                content_hash="abc123",
                text="# Test\n\nContent.",
                title="Test",
                modified_at=1000.0,
                metadata={},
                labels=[],
            )
            expected_ids = [1, 2, 3]
            backend.upsert_document(
                1,
                doc,
                [("# Test", "Chunk A")],
                embedder_ids=expected_ids,
            )

            args = backend._copy_reusable_embeddings_batch.call_args[0]
            # args[0] is new_chunks (list of tuples); args[1] is embedder_ids.
            assert args[1] == expected_ids
