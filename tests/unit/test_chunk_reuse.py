"""Tests for _copy_reusable_embeddings on PostgresBackend."""

from unittest.mock import MagicMock, patch

import pytest

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
        with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
            backend = PostgresBackend.__new__(PostgresBackend)
            backend._execute = MagicMock(
                side_effect=[
                    [],
                    [{"id": 5}],
                    [{"id": 101}],
                    [{"id": 102}],
                ]
            )
            backend._copy_reusable_embeddings = MagicMock(return_value=set())

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
                1, doc,
                [("# Test", "Chunk A"), ("", "Chunk B")],
                embedder_ids=[],
            )

            assert backend._copy_reusable_embeddings.call_count == 2
            for call_args in backend._copy_reusable_embeddings.call_args_list:
                args = call_args[0]
                assert len(args) >= 4
                assert isinstance(args[3], dict)

    def test_reuse_cache_same_object_across_chunks(self):
        with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
            backend = PostgresBackend.__new__(PostgresBackend)
            backend._execute = MagicMock(
                side_effect=[
                    [],
                    [{"id": 5}],
                    [{"id": 101}],
                    [{"id": 102}],
                ]
            )
            backend._copy_reusable_embeddings = MagicMock(return_value=set())

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
                1, doc,
                [("# Test", "Chunk A"), ("", "Chunk B")],
                embedder_ids=[],
            )

            caches = [call[0][3] for call in backend._copy_reusable_embeddings.call_args_list]
            assert len(caches) >= 2
            assert caches[0] is caches[1], "cache object must be shared across chunks"

    def test_embedder_ids_passed_through_to_copy(self):
        with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
            backend = PostgresBackend.__new__(PostgresBackend)
            backend._execute = MagicMock(
                side_effect=[
                    [],
                    [{"id": 5}],
                    [{"id": 101}],
                ]
            )
            backend._copy_reusable_embeddings = MagicMock(return_value=set())

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
                1, doc,
                [("# Test", "Chunk A")],
                embedder_ids=expected_ids,
            )

            call_args = backend._copy_reusable_embeddings.call_args[0]
            assert call_args[2] == expected_ids
