"""Unit tests for PostgresBackend with mocked psycopg."""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from corpus_forge.backends.postgres import PostgresBackend
from corpus_forge.embedders.base import BaseEmbedder
from corpus_forge.sources.base import RawConversation, RawDocument, RawMessage


def _mock_cursor():
    """Return a mock cursor with dict-like rows."""
    cur = MagicMock()

    # SELECT returns list of dicts
    cur.description = [("id",), ("content_hash",), ("name",), ("dimension",)]
    cur.fetchall = MagicMock(
        return_value=[
            {"id": 1, "content_hash": "abc", "name": "test", "dimension": 384},
        ]
    )
    # RETURNING returns single dict
    cur.fetchone = MagicMock(return_value={"id": 42})
    # pg_try_advisory_lock returns True
    cur.fetchone = MagicMock(
        side_effect=[
            {"id": 1},  # SELECT id FROM documents
            {"content_hash": "old"},  # SELECT content_hash
            {"id": 42},  # RETURNING id (INSERT)
            {"id": 43},  # RETURNING id (messages)
            True,  # pg_try_advisory_lock
        ]
    )
    cur.execute = MagicMock()
    return cur


def _mock_connection():
    """Return a mock connection."""
    conn = MagicMock()
    cur = _mock_cursor()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=None)
    conn.cursor.return_value.description = [("id",)]
    conn.cursor.return_value.fetchall.return_value = [{"id": 1}]
    conn.cursor.return_value.fetchone.return_value = (1,)
    return conn


class TestPostgresBackendInit:
    """Tests for PostgresBackend initialization."""

    def test_init_stores_dsn_and_schema(self):
        backend = PostgresBackend(dsn="postgresql://test@localhost/test", schema="my_schema")
        assert backend.dsn == "postgresql://test@localhost/test"
        assert backend.schema == "my_schema"

    def test_init_calls_setup_connection(self):
        with (
            patch("corpus_forge.backends.postgres.PostgresBackend._setup_connection") as mock_setup,
            patch("corpus_forge.backends.postgres.PostgresBackend._execute"),
        ):
            PostgresBackend(dsn="postgresql://test@localhost/test")
            mock_setup.assert_called_once()

    def test_setup_connection_expands_env_vars(self):
        import os

        os.environ["TEST_PG_HOST"] = "expanded-host"
        try:
            backend = PostgresBackend(dsn="postgresql://user:${TEST_PG_HOST}/db")
            assert "expanded-host" in backend.conn_params["dbname"]
        finally:
            del os.environ["TEST_PG_HOST"]


class TestMigrate:
    """Tests for migrate method."""

    def test_migrate_executes_statements(self):
        """migrate() runs inline DDL statements via _execute (core schema bootstrap)."""
        with (
            patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None),
            patch("corpus_forge.schema.migrate.apply_migrations"),
        ):
            real_backend = PostgresBackend.__new__(PostgresBackend)
            real_backend._execute = MagicMock()
            real_backend.migrate()
            assert real_backend._execute.call_count > 0

    def test_migrate_creates_vector_extension(self):
        """migrate() includes CREATE EXTENSION vector in its inline DDL."""
        with (
            patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None),
            patch("corpus_forge.schema.migrate.apply_migrations"),
        ):
            backend = PostgresBackend.__new__(PostgresBackend)
            backend._execute = MagicMock()
            backend.migrate()
            calls = [c[0][0] for c in backend._execute.call_args_list]
            vector_sql = [c for c in calls if "vector" in c.lower()]
            assert len(vector_sql) >= 1


class TestRegisterEmbedder:
    """Tests for register_embedder method."""

    def test_register_new_embedder(self):
        with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
            backend = PostgresBackend.__new__(PostgresBackend)
            backend._execute = MagicMock(
                side_effect=[
                    [],  # existing = [] (not found)
                    [{"id": 1}],  # RETURNING id
                    [],  # _create_embedder_table SQL
                ]
            )

            embedder = BaseEmbedder(
                name="test-embed",
                provider="sentence_transformers",
                model_id="test/model",
                dimension=384,
                normalized=True,
                distance="cosine",
            )
            result = backend.register_embedder(embedder)
            assert result == 1

    def test_register_existing_embedder(self):
        with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
            backend = PostgresBackend.__new__(PostgresBackend)
            backend._execute = MagicMock(
                side_effect=[
                    [{"id": 99}],  # existing found
                    [],  # UPDATE
                    [],  # _create_embedder_table
                ]
            )

            embedder = MagicMock()
            embedder.name = "test-embed"
            embedder.provider = "sentence_transformers"
            embedder.model_id = "test/model"
            embedder.dimension = 384
            embedder.normalized = True
            embedder.distance = "cosine"
            embedder.active = True

            result = backend.register_embedder(embedder)
            assert result == 99


class TestUpsertDocument:
    """Tests for upsert_document method."""

    def test_upsert_new_document(self):
        with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
            backend = PostgresBackend.__new__(PostgresBackend)
            backend._execute = MagicMock(
                side_effect=[
                    [],  # existing = [] (not found)
                    [{"id": 5}],  # RETURNING id
                    [],  # INSERT chunk 1
                ]
            )

            doc = RawDocument(
                source_uri="vault://test.md",
                content_hash="abc123",
                text="# Test\n\nContent.",
                title="Test",
                modified_at=1000.0,
                metadata={"key": "val"},
                labels=[],
            )
            result = backend.upsert_document(1, doc, [("# Test", "Content.")])
            assert result == 5

    def test_upsert_unchanged_document_returns_existing(self):
        with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
            backend = PostgresBackend.__new__(PostgresBackend)
            backend._execute = MagicMock(
                side_effect=[
                    [{"id": 5}],  # existing found
                    [{"content_hash": "abc123"}],  # content_hash matches
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
            result = backend.upsert_document(1, doc, [])
            assert result == 5

    def test_upsert_changed_document_updates(self):
        with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
            backend = PostgresBackend.__new__(PostgresBackend)
            backend._execute = MagicMock(
                side_effect=[
                    [{"id": 5}],  # existing found
                    [{"content_hash": "old_hash"}],  # content_hash differs
                    [],  # UPDATE
                    [],  # DELETE chunks
                    [{"id": 5}],  # RETURNING id (INSERT)
                ]
            )

            doc = RawDocument(
                source_uri="vault://test.md",
                content_hash="new_hash",
                text="# Updated\n\nNew content.",
                title="Updated",
                modified_at=2000.0,
                metadata={},
                labels=[],
            )
            result = backend.upsert_document(1, doc, [("# Updated", "New content.")])
            assert result == 5

    def test_upsert_document_creates_chunks(self):
        with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
            backend = PostgresBackend.__new__(PostgresBackend)
            backend._execute = MagicMock(
                side_effect=[
                    [],  # existing = []
                    [{"id": 5}],  # RETURNING id
                    [],  # INSERT chunk 1
                    [],  # INSERT chunk 2
                ]
            )

            doc = RawDocument(
                source_uri="vault://test.md",
                content_hash="abc",
                text="# Test\n\nContent.",
                title="Test",
                modified_at=1000.0,
                metadata={},
                labels=[],
            )
            backend.upsert_document(1, doc, [("# Test", "Content."), ("", "More text")])
            # Should have called _execute for INSERT doc + 2x INSERT chunk
            assert backend._execute.call_count >= 3

    def test_upsert_document_chunks_include_content_hash_column(self):

        with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
            backend = PostgresBackend.__new__(PostgresBackend)
            backend._execute = MagicMock(
                side_effect=[
                    [],
                    [{"id": 5}],
                    [],
                    [],
                ]
            )

            doc = RawDocument(
                source_uri="vault://test.md",
                content_hash="abc",
                text="# Test\n\nContent.",
                title="Test",
                modified_at=1000.0,
                metadata={},
                labels=[],
            )
            backend.upsert_document(1, doc, [("# Test", "Chunk A"), ("", "Chunk B")])

            chunk_calls = [
                (call[0][0], call[0][1] if len(call[0]) > 1 else {})
                for call in backend._execute.call_args_list
                if "INSERT INTO corpus.chunks" in str(call[0][0])
            ]
            assert len(chunk_calls) == 2, f"Expected 2 chunk INSERT calls, got {len(chunk_calls)}"

            for sql, _ in chunk_calls:
                assert "content_hash" in sql, f"content_hash missing from chunk INSERT: {sql}"

    def test_upsert_document_chunks_have_correct_content_hash_value(self):
        from corpus_forge.identity import chunk_content_hash

        with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
            backend = PostgresBackend.__new__(PostgresBackend)
            backend._execute = MagicMock(
                side_effect=[
                    [],
                    [{"id": 5}],
                    [],
                    [],
                ]
            )

            doc = RawDocument(
                source_uri="vault://test.md",
                content_hash="abc",
                text="# Test\n\nContent.",
                title="Test",
                modified_at=1000.0,
                metadata={},
                labels=[],
            )

            chunks_input = [("# Test", "Chunk A content"), ("", "Chunk B content")]
            backend.upsert_document(1, doc, chunks_input)

            chunk_calls = [
                (call[0][0], call[0][1] if len(call[0]) > 1 else {})
                for call in backend._execute.call_args_list
                if "INSERT INTO corpus.chunks" in str(call[0][0])
            ]
            assert len(chunk_calls) == 2

            for (_sql, params), (_, text) in zip(chunk_calls, chunks_input, strict=False):
                expected_hash = chunk_content_hash(text)
                # params could be a tuple (positional) or dict (keyword)
                params_list = params if isinstance(params, (tuple, list)) else list(params.values())
                assert any(isinstance(p, str) and p == expected_hash for p in params_list), (
                    f"Expected hash {expected_hash} for text {text!r} not in params {params}"
                )


class TestUpsertConversation:
    """Tests for upsert_conversation method."""

    def test_upsert_new_conversation(self):
        with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
            backend = PostgresBackend.__new__(PostgresBackend)
            backend._execute = MagicMock(
                side_effect=[
                    [],  # existing = []
                    [{"id": 10}],  # RETURNING id (conversation)
                    [{"id": 100}],  # RETURNING id (message 1)
                    [{"id": 101}],  # RETURNING id (message 2)
                    [],  # INSERT chunk for msg 1
                    [],  # INSERT chunk for msg 2
                ]
            )

            conv = RawConversation(
                source_uri="claude-code://proj/sess1",
                external_id="sess1",
                content_hash="conv123",
                title="Chat",
                started_at=1000.0,
                ended_at=1005.0,
                messages=[
                    RawMessage(
                        external_uuid="m1",
                        parent_uuid=None,
                        role="user",
                        content="Hello",
                        tool_calls=None,
                        tool_results=None,
                        ts=1000.0,
                        metadata={},
                    ),
                    RawMessage(
                        external_uuid="m2",
                        parent_uuid="m1",
                        role="assistant",
                        content="Hi!",
                        tool_calls=None,
                        tool_results=None,
                        ts=1001.0,
                        metadata={},
                    ),
                ],
                metadata={},
                labels=[],
            )
            result = backend.upsert_conversation(1, conv, [[("Hello", "Hello")], [("Hi!", "Hi!")]])
            assert result == 10

    def test_upsert_conversation_unchanged(self):
        with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
            backend = PostgresBackend.__new__(PostgresBackend)
            backend._execute = MagicMock(
                side_effect=[
                    [{"id": 10}],  # existing found
                    [{"content_hash": "conv123"}],  # hash matches
                ]
            )

            conv = RawConversation(
                source_uri="claude-code://proj/sess1",
                external_id="sess1",
                content_hash="conv123",
                title="Chat",
                started_at=1000.0,
                ended_at=1005.0,
                messages=[],
                metadata={},
                labels=[],
            )
            result = backend.upsert_conversation(1, conv, [])
            assert result == 10


class TestWriteEmbeddings:
    """Tests for write_embeddings method."""

    def test_write_embeddings_empty_does_nothing(self):
        with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
            backend = PostgresBackend.__new__(PostgresBackend)
            backend._execute = MagicMock()
            backend.write_embeddings(1, [])
            backend._execute.assert_not_called()

    def test_write_embeddings_raises_for_missing_embedder(self):
        with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
            backend = PostgresBackend.__new__(PostgresBackend)
            backend._execute = MagicMock(return_value=[])  # embedder not found

            embedding = np.array([0.1, 0.2, 0.3], dtype=np.float32)
            with pytest.raises(ValueError, match="Embedder with ID 1 not found"):
                backend.write_embeddings(1, [(1, embedding)])

    def test_write_embeddings_inserts(self):
        with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
            backend = PostgresBackend.__new__(PostgresBackend)
            backend._execute = MagicMock(
                side_effect=[
                    [{"name": "test-embed", "dimension": 384}],  # embedder info
                    [],  # INSERT embedding 1
                    [],  # INSERT embedding 2
                ]
            )

            embedding = np.zeros((2, 384), dtype=np.float32)
            backend.write_embeddings(1, [(1, embedding[0]), (2, embedding[1])])
            # Should have called _execute for embedder lookup + 2x INSERT
            assert backend._execute.call_count >= 3


class TestChunksMissingEmbedding:
    """Tests for chunks_missing_embedding method."""

    def test_returns_chunks_when_embedder_exists(self):
        with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
            backend = PostgresBackend.__new__(PostgresBackend)
            backend._execute = MagicMock(
                side_effect=[
                    [{"name": "test-embed"}],  # embedder info
                    [{"id": 1, "text": "chunk1"}, {"id": 2, "text": "chunk2"}],  # missing chunks
                ]
            )

            results = list(backend.chunks_missing_embedding(1))
            assert len(results) == 2
            assert results[0] == (1, "chunk1")
            assert results[1] == (2, "chunk2")

    def test_returns_empty_generator_when_embedder_not_found(self):
        with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
            backend = PostgresBackend.__new__(PostgresBackend)
            backend._execute = MagicMock(return_value=[])  # embedder not found

            results = list(backend.chunks_missing_embedding(99))
            assert results == []


class TestLockSource:
    """Tests for lock_source context manager."""

    def test_lock_source_success(self):
        with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
            backend = PostgresBackend.__new__(PostgresBackend)
            backend._get_connection = MagicMock()
            mock_conn = MagicMock()
            mock_cur = MagicMock()
            mock_cur.fetchone.return_value = (True,)  # lock acquired
            mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
            mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=None)
            backend._get_connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
            backend._get_connection.return_value.__exit__ = MagicMock(return_value=None)

            with backend.lock_source("test-key"):
                pass

    def test_lock_source_raises_when_failed(self):
        with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
            backend = PostgresBackend.__new__(PostgresBackend)
            backend._get_connection = MagicMock()
            mock_conn = MagicMock()
            mock_cur = MagicMock()
            mock_cur.fetchone.return_value = (False,)  # lock not acquired
            mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
            mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=None)
            backend._get_connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
            backend._get_connection.return_value.__exit__ = MagicMock(return_value=None)

            with (
                pytest.raises(RuntimeError, match="Could not acquire lock"),
                backend.lock_source("test-key"),
            ):
                pass


class TestDeleteOps:
    """Tests for delete operations."""

    def test_delete_document(self):
        with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
            backend = PostgresBackend.__new__(PostgresBackend)
            backend._execute = MagicMock()
            backend.delete_document(1, "vault://test.md")
            assert backend._execute.call_count == 1

    def test_delete_conversation(self):
        with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
            backend = PostgresBackend.__new__(PostgresBackend)
            backend._execute = MagicMock()
            backend.delete_conversation(1, "claude-code://proj/sess1")
            assert backend._execute.call_count == 1
