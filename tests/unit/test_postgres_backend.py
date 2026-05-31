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
        """The chunk INSERT must include the ``content_hash`` column.

        Updated 2026-05-27 for the batched-INSERT path: ``upsert_document``
        now issues ONE multi-row INSERT covering all chunks rather than
        N per-chunk INSERTs. The test was previously asserting on the
        per-chunk call count; we now assert on the single batched call
        and check that it still references ``content_hash``.
        """

        with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
            backend = PostgresBackend.__new__(PostgresBackend)
            backend._execute = MagicMock(
                side_effect=[
                    [],  # SELECT existing document (none)
                    [{"id": 5}],  # INSERT documents RETURNING id
                    # Batched chunk INSERT: returns one row per chunk in
                    # the same order as the input list.
                    [{"id": 10, "content_hash": "hashA"}, {"id": 11, "content_hash": "hashB"}],
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
            # Single batched INSERT (was 2 per-chunk INSERTs before the
            # 2026-05-27 batching refactor).
            assert len(chunk_calls) == 1, (
                f"Expected 1 batched chunk INSERT call, got {len(chunk_calls)}"
            )
            sql = chunk_calls[0][0]
            assert "content_hash" in sql, f"content_hash missing from chunk INSERT: {sql}"
            # Both chunk parameter sets are crammed into one VALUES
            # clause — count the tuples robustly via the gaps between
            # ``)`` and ``(`` rather than matching an exact placeholder
            # string (the latter breaks under harmless formatting
            # changes like extra whitespace, casts, or psycopg version
            # bumps that subtly retokenize the literal).
            import re

            values_clause = sql.split("VALUES", 1)[1] if "VALUES" in sql else ""
            tuple_gaps = re.findall(r"\)\s*,\s*\(", values_clause)
            # N tuples in a VALUES list have N-1 separators between them.
            tuple_count = len(tuple_gaps) + 1 if values_clause else 0
            assert tuple_count == 2, (
                f"Expected 2 VALUES tuples in the batched INSERT, found {tuple_count}: {sql}"
            )

    def test_upsert_document_chunks_have_correct_content_hash_value(self):
        """The batched chunk INSERT must include the right hash per chunk.

        Updated 2026-05-27 for the batched-INSERT path: ``upsert_document``
        now bundles all chunk hashes into one multi-row INSERT's flat
        param list. We pull the params for the batched call and confirm
        each chunk's expected hash appears in the flat sequence.
        """

        from corpus_forge.identity import chunk_content_hash

        with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
            backend = PostgresBackend.__new__(PostgresBackend)
            backend._execute = MagicMock(
                side_effect=[
                    [],
                    [{"id": 5}],
                    [{"id": 10, "content_hash": "hashA"}, {"id": 11, "content_hash": "hashB"}],
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
            assert len(chunk_calls) == 1, f"Expected 1 batched chunk INSERT, got {len(chunk_calls)}"
            _sql, params = chunk_calls[0]
            params_list = (
                list(params) if isinstance(params, (tuple, list)) else list(params.values())
            )
            # Each chunk's expected hash must appear in the flat
            # batched-INSERT param list.
            for _, text in chunks_input:
                expected_hash = chunk_content_hash(text)
                assert expected_hash in params_list, (
                    f"Expected hash {expected_hash} for text {text!r} not in {params_list}"
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
                    # PR #81 — chunks_missing_embedding now SELECTs source_uri
                    # from the parent documents row via LEFT JOIN.
                    [
                        {"id": 1, "text": "chunk1", "source_uri": "filesystem://v/a.md"},
                        {"id": 2, "text": "chunk2", "source_uri": ""},
                    ],
                ]
            )

            results = list(backend.chunks_missing_embedding(1))
            assert len(results) == 2
            assert results[0] == (1, "chunk1", "filesystem://v/a.md")
            assert results[1] == (2, "chunk2", "")

    def test_returns_empty_generator_when_embedder_not_found(self):
        with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
            backend = PostgresBackend.__new__(PostgresBackend)
            backend._execute = MagicMock(return_value=[])  # embedder not found

            results = list(backend.chunks_missing_embedding(99))
            assert results == []


class TestChunksMissingEmbeddingExtensionsFilter:
    """Post-PR #81 bugfix — SQL-side extension allow-list push.

    The Python-side ``route_for`` filter in ``embed.backfill_embedder`` was
    applied AFTER the backend fetched a 1000-row page. The first page is
    deterministic (``ORDER BY c.id``) so a specialist whose extensions
    matched zero rows in the first 1000 would loop-break and skip the
    rest of the corpus. These tests pin the SQL filter that pushes the
    allow-list into the backend so every fetched page is dense with
    matches.
    """

    def _backend_with_capturing_execute(self) -> tuple[PostgresBackend, MagicMock]:
        with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
            backend = PostgresBackend.__new__(PostgresBackend)
            backend._execute = MagicMock(
                side_effect=[
                    [{"name": "nomic-code"}],  # embedder info lookup
                    [],  # second call returns the empty result set
                ]
            )
            return backend, backend._execute

    def test_extensions_none_emits_no_like_clause(self) -> None:
        """Back-compat: ``extensions=None`` must NOT add a LIKE clause."""
        backend, exec_mock = self._backend_with_capturing_execute()
        list(backend.chunks_missing_embedding(1, extensions=None))
        # exec_mock.call_args_list[0] is the embedder lookup; [1] is the chunks query
        chunks_query_sql = exec_mock.call_args_list[1][0][0]
        assert "LIKE" not in chunks_query_sql.upper(), (
            f"extensions=None must not emit a LIKE clause; got SQL:\n{chunks_query_sql}"
        )

    def test_extensions_empty_list_emits_no_like_clause(self) -> None:
        """Back-compat: ``extensions=[]`` must behave like ``extensions=None``."""
        backend, exec_mock = self._backend_with_capturing_execute()
        list(backend.chunks_missing_embedding(1, extensions=[]))
        chunks_query_sql = exec_mock.call_args_list[1][0][0]
        assert "LIKE" not in chunks_query_sql.upper(), (
            f"extensions=[] must not emit a LIKE clause; got SQL:\n{chunks_query_sql}"
        )

    def test_extensions_non_empty_emits_one_like_per_extension(self) -> None:
        """Two extensions → two ``LIKE`` clauses joined by OR.

        Params end with ``%.py`` / ``%.ts`` for suffix-match semantics.
        """
        backend, exec_mock = self._backend_with_capturing_execute()
        list(backend.chunks_missing_embedding(1, extensions=[".py", ".ts"]))
        chunks_query_sql = exec_mock.call_args_list[1][0][0]
        chunks_query_params = exec_mock.call_args_list[1][0][1]
        # Two LIKE %s clauses, joined by OR
        assert chunks_query_sql.upper().count(" LIKE ") == 2, (
            f"Expected exactly 2 LIKE clauses; SQL:\n{chunks_query_sql}"
        )
        assert " OR " in chunks_query_sql.upper(), (
            f"Multiple extensions must be OR-joined; SQL:\n{chunks_query_sql}"
        )
        # Params contain the suffix patterns (plus limit)
        assert "%.py" in chunks_query_params, (
            f"params must include '%.py'; got {chunks_query_params!r}"
        )
        assert "%.ts" in chunks_query_params, (
            f"params must include '%.ts'; got {chunks_query_params!r}"
        )

    def test_extensions_case_normalised_and_leading_dot_added(self) -> None:
        """``["PY", ".TS", ".Md"]`` → patterns ``'%.py'``, ``'%.ts'``, ``'%.md'``."""
        backend, exec_mock = self._backend_with_capturing_execute()
        list(backend.chunks_missing_embedding(1, extensions=["PY", ".TS", ".Md"]))
        chunks_query_params = exec_mock.call_args_list[1][0][1]
        assert "%.py" in chunks_query_params, f"params: {chunks_query_params!r}"
        assert "%.ts" in chunks_query_params, f"params: {chunks_query_params!r}"
        assert "%.md" in chunks_query_params, f"params: {chunks_query_params!r}"

    def test_extensions_reuses_coalesce_source_uri(self) -> None:
        """The LIKE clause must reference the same COALESCE expression PR #81 wired up
        (``COALESCE(d.source_uri, cv.source_uri, '')``) — don't re-derive it from
        documents.source_uri alone, or chat chunks won't match."""
        backend, exec_mock = self._backend_with_capturing_execute()
        list(backend.chunks_missing_embedding(1, extensions=[".py"]))
        chunks_query_sql = exec_mock.call_args_list[1][0][0]
        # The LIKE comparator must be lower(COALESCE(...)) so case-insensitive
        # match works against uppercase URIs.
        assert "COALESCE" in chunks_query_sql, (
            f"LIKE must compare COALESCE source uri; SQL:\n{chunks_query_sql}"
        )
        assert "LOWER" in chunks_query_sql.upper(), (
            f"LIKE must be case-insensitive (lower(COALESCE(...))); SQL:\n{chunks_query_sql}"
        )

    def test_extensions_rejects_empty_string_entry(self) -> None:
        """Defense in depth: empty-string extensions blow up — they would otherwise
        match every row (``LIKE '%'``) which silently defeats the filter."""
        backend, _exec_mock = self._backend_with_capturing_execute()
        with pytest.raises(ValueError, match="extension"):
            list(backend.chunks_missing_embedding(1, extensions=[""]))

    def test_extensions_rejects_non_string_entry(self) -> None:
        backend, _exec_mock = self._backend_with_capturing_execute()
        with pytest.raises((TypeError, ValueError)):
            list(backend.chunks_missing_embedding(1, extensions=[5]))  # type: ignore[list-item]


class TestCountChunksMissingEmbeddingExtensionsFilter:
    """Same SQL-push contract for the count helper. Without it, the progress
    bar lies (1.88 M chunks "pending" for nomic-code when only a few thousand
    .py / .ts rows actually exist — see PR description)."""

    def _backend_with_capturing_execute(
        self, count_result: int = 0
    ) -> tuple[PostgresBackend, MagicMock]:
        with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
            backend = PostgresBackend.__new__(PostgresBackend)
            backend._execute = MagicMock(
                side_effect=[
                    [{"name": "nomic-code"}],  # embedder info lookup
                    [{"n": count_result}],  # COUNT(*) result
                ]
            )
            return backend, backend._execute

    def test_count_extensions_none_emits_no_like(self) -> None:
        backend, exec_mock = self._backend_with_capturing_execute()
        backend.count_chunks_missing_embedding(1, extensions=None)
        count_sql = exec_mock.call_args_list[1][0][0]
        assert "LIKE" not in count_sql.upper(), (
            f"count extensions=None must not emit LIKE; SQL:\n{count_sql}"
        )

    def test_count_extensions_emits_like_per_extension(self) -> None:
        backend, exec_mock = self._backend_with_capturing_execute()
        backend.count_chunks_missing_embedding(1, extensions=[".py", ".ts"])
        call = exec_mock.call_args_list[1]
        count_sql = call[0][0]
        count_params = call[0][1] if len(call[0]) > 1 else ()
        assert count_sql.upper().count(" LIKE ") == 2, f"Expected 2 LIKE clauses; SQL:\n{count_sql}"
        assert "%.py" in count_params, f"params: {count_params!r}"
        assert "%.ts" in count_params, f"params: {count_params!r}"

    def test_count_extensions_case_normalised(self) -> None:
        backend, exec_mock = self._backend_with_capturing_execute()
        backend.count_chunks_missing_embedding(1, extensions=["PY"])
        call = exec_mock.call_args_list[1]
        count_params = call[0][1] if len(call[0]) > 1 else ()
        assert "%.py" in count_params, f"params: {count_params!r}"

    def test_count_extensions_reuses_coalesce(self) -> None:
        """COUNT(*) must JOIN documents + conversations and apply the LIKE
        against the COALESCE'd source_uri — same as ``chunks_missing_embedding``.

        Without the JOIN the count query can't reference source_uri at all,
        so the SQL push fails silently and the progress bar over-reports.
        """
        backend, exec_mock = self._backend_with_capturing_execute()
        backend.count_chunks_missing_embedding(1, extensions=[".py"])
        count_sql = exec_mock.call_args_list[1][0][0]
        assert "COALESCE" in count_sql, (
            f"count LIKE must reference COALESCE(d.source_uri, cv.source_uri, ''); "
            f"SQL:\n{count_sql}"
        )

    def test_count_extensions_rejects_empty_string(self) -> None:
        backend, _exec_mock = self._backend_with_capturing_execute()
        with pytest.raises(ValueError, match="extension"):
            backend.count_chunks_missing_embedding(1, extensions=[""])


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


# ─────────────────────────────────────────────────────────────────────
# Coverage for the 2026-05-27 batched-upsert refactor — unit-level
# tests for the helpers that are otherwise exercised only by the
# integration suite (which doesn't run on the unit-test CI job).
# ─────────────────────────────────────────────────────────────────────


class TestEnsureEmbedderCaches:
    """``_ensure_embedder_caches`` initialises the per-instance caches
    when ``__init__`` was bypassed (production code initialises them
    eagerly). Idempotent across calls.
    """

    def test_initialises_missing_attrs(self):
        backend = PostgresBackend.__new__(PostgresBackend)
        # No attrs set yet.
        assert not hasattr(backend, "_embedder_id_cache")
        backend._ensure_embedder_caches()
        assert backend._embedder_id_cache == {}
        assert backend._embedder_info_cache == {}
        assert backend._tables_created == set()

    def test_idempotent_preserves_existing_state(self):
        backend = PostgresBackend.__new__(PostgresBackend)
        backend._ensure_embedder_caches()
        # Seed with state — re-init must NOT clobber.
        backend._embedder_id_cache["x"] = 42
        backend._tables_created.add("x")
        backend._ensure_embedder_caches()
        assert backend._embedder_id_cache == {"x": 42}
        assert backend._tables_created == {"x"}


class TestRegisterEmbedderCache:
    """Process-lifetime caching on ``register_embedder``: first call
    hits the DB (3 round-trips); second call with the same embedder
    name returns the cached id with zero ``_execute`` calls.
    """

    def test_second_call_is_zero_round_trips(self):
        with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
            backend = PostgresBackend.__new__(PostgresBackend)
            # First call: no existing row → INSERT path → 2 _execute
            # calls + 1 CREATE TABLE inside _create_embedder_table.
            backend._execute = MagicMock(
                side_effect=[
                    [],  # SELECT existing
                    [{"id": 7}],  # INSERT RETURNING id
                    [],  # _create_embedder_table CREATE TABLE
                ]
            )

            embedder = MagicMock()
            embedder.name = "cache-test"
            embedder.provider = "test"
            embedder.model_id = "model"
            embedder.dimension = 4
            embedder.normalized = False
            embedder.distance = "cosine"
            embedder.active = True

            first_id = backend.register_embedder(embedder)
            assert first_id == 7
            first_call_count = backend._execute.call_count
            assert first_call_count >= 2

            # Second call: cached → ZERO _execute calls.
            second_id = backend.register_embedder(embedder)
            assert second_id == 7
            assert backend._execute.call_count == first_call_count


class TestCopyReusableEmbeddingsBatch:
    """Batch reuse-embedding helper: 2 round-trips per embedder
    regardless of chunk count, with empty-input short-circuits and
    sub-batching past the PG bind-parameter cap.
    """

    def test_empty_new_chunks_short_circuits(self):
        backend = PostgresBackend.__new__(PostgresBackend)
        backend._ensure_embedder_caches()
        backend._execute = MagicMock()
        backend._copy_reusable_embeddings_batch([], [1, 2])
        assert backend._execute.call_count == 0

    def test_empty_embedder_ids_short_circuits(self):
        backend = PostgresBackend.__new__(PostgresBackend)
        backend._ensure_embedder_caches()
        backend._execute = MagicMock()
        backend._copy_reusable_embeddings_batch([(1, "h1")], [])
        assert backend._execute.call_count == 0

    def test_single_embedder_two_round_trips(self):
        """One SELECT (prior_chunk_id per hash) + one INSERT (bulk
        copy) for the whole document, regardless of chunk count.
        """

        backend = PostgresBackend.__new__(PostgresBackend)
        backend._ensure_embedder_caches()
        # Pre-seed embedder_info cache so we skip the defensive
        # SELECT name, table_name fallback path.
        backend._embedder_info_cache[42] = {
            "name": "test-emb",
            "table_name": "embeddings_test_emb",
        }
        backend._execute = MagicMock(
            side_effect=[
                # First _execute: SELECT DISTINCT ON (content_hash) ...
                [
                    {"content_hash": "hA", "prior_chunk_id": 100},
                    {"content_hash": "hB", "prior_chunk_id": 101},
                ],
                # Second _execute: INSERT ... SELECT FROM VALUES JOIN
                [],
            ]
        )
        backend._copy_reusable_embeddings_batch([(200, "hA"), (201, "hB")], [42])
        # Exactly 2 calls: one SELECT + one INSERT.
        assert backend._execute.call_count == 2

    def test_no_prior_matches_skips_insert(self):
        """If no chunk in the SELECT result has a matching prior,
        the helper skips the INSERT (no zero-row INSERT). Saves the
        second round-trip when reuse-opportunity is absent.
        """

        backend = PostgresBackend.__new__(PostgresBackend)
        backend._ensure_embedder_caches()
        backend._embedder_info_cache[42] = {
            "name": "test-emb",
            "table_name": "embeddings_test_emb",
        }
        backend._execute = MagicMock(
            side_effect=[
                [],  # SELECT returns no priors
            ]
        )
        backend._copy_reusable_embeddings_batch([(200, "h-novel")], [42])
        # Just the SELECT — INSERT was skipped because copy_pairs is empty.
        assert backend._execute.call_count == 1

    def test_skips_self_copy(self):
        """When the prior_chunk_id equals the new chunk_id (re-ingest
        of the SAME chunk under the same id), don't add a self-copy
        pair — would write a duplicate row that violates the
        ``(chunk_id, embedder_id)`` uniqueness invariant.
        """

        backend = PostgresBackend.__new__(PostgresBackend)
        backend._ensure_embedder_caches()
        backend._embedder_info_cache[42] = {
            "name": "test-emb",
            "table_name": "embeddings_test_emb",
        }
        backend._execute = MagicMock(
            side_effect=[
                [{"content_hash": "h1", "prior_chunk_id": 200}],
                # No INSERT expected: the only copy_pair would be
                # (200, 200) which is filtered out.
            ]
        )
        # New chunk_id == prior_chunk_id for the same hash.
        backend._copy_reusable_embeddings_batch([(200, "h1")], [42])
        assert backend._execute.call_count == 1  # Only the SELECT.

    def test_falls_back_to_db_lookup_when_info_cache_missing(self):
        """If a caller passes an embedder_id not in the cache (tests
        that bypass register_embedder), the helper does a defensive
        SELECT to populate the info, then proceeds with the batched
        SELECT + INSERT — 3 _execute calls total instead of 2.
        """

        backend = PostgresBackend.__new__(PostgresBackend)
        backend._ensure_embedder_caches()
        # Don't pre-populate _embedder_info_cache.
        backend._execute = MagicMock(
            side_effect=[
                # Defensive SELECT name, table_name
                [{"name": "test-emb", "table_name": "embeddings_test_emb"}],
                # SELECT prior_chunk_id per hash
                [{"content_hash": "hA", "prior_chunk_id": 100}],
                # INSERT bulk copy
                [],
            ]
        )
        backend._copy_reusable_embeddings_batch([(200, "hA")], [42])
        assert backend._execute.call_count == 3
        # Cache is now populated for next time.
        assert 42 in backend._embedder_info_cache


class TestInsertChunksBatchSubBatching:
    """The PG bind-parameter cap (65,535) limits how many chunks fit
    in a single multi-row INSERT. ``_insert_chunks_batch`` splits
    larger inputs into sub-batches that each stay under the cap.
    These tests pin the sub-batching math without actually creating
    8k+ chunks.
    """

    def test_empty_chunks_returns_empty_list(self):
        backend = PostgresBackend.__new__(PostgresBackend)
        backend._execute = MagicMock()
        result = backend._insert_chunks_batch(doc_id=1, chunks=[])
        assert result == []
        assert backend._execute.call_count == 0

    def test_single_batch_for_small_input(self):
        """N=2 chunks fit comfortably in one batch — only one
        ``_execute`` call needed.
        """

        from corpus_forge.chunkers.base import TextChunk

        backend = PostgresBackend.__new__(PostgresBackend)
        backend._execute = MagicMock(
            return_value=[
                {"id": 10, "content_hash": "h0"},
                {"id": 11, "content_hash": "h1"},
            ]
        )
        chunks = [
            TextChunk(text="a", heading="h0", metadata={}, role=None, token_count=1),
            TextChunk(text="b", heading="h1", metadata={}, role=None, token_count=1),
        ]
        result = backend._insert_chunks_batch(doc_id=1, chunks=chunks)
        assert len(result) == 2
        assert backend._execute.call_count == 1

    def test_sub_batches_when_input_exceeds_cap(self, monkeypatch):
        """Override ``_PG_MAX_BIND_PARAMS`` to a tiny value so we
        can exercise the sub-batching loop without constructing
        8k+ real chunks. With cap=16 and 8 params/chunk, the
        max is 2 chunks per batch — so 5 chunks splits into
        3 batches (2, 2, 1).
        """

        import corpus_forge.backends.postgres as pg_mod
        from corpus_forge.chunkers.base import TextChunk

        monkeypatch.setattr(pg_mod, "_PG_MAX_BIND_PARAMS", 16)

        backend = PostgresBackend.__new__(PostgresBackend)
        # Each _execute call returns however many rows are in that
        # sub-batch's INSERT (mock that exactly).
        backend._execute = MagicMock(
            side_effect=[
                [{"id": 10, "content_hash": "h0"}, {"id": 11, "content_hash": "h1"}],
                [{"id": 12, "content_hash": "h2"}, {"id": 13, "content_hash": "h3"}],
                [{"id": 14, "content_hash": "h4"}],
            ]
        )
        chunks = [
            TextChunk(text=f"c{i}", heading=f"h{i}", metadata={}, role=None, token_count=1)
            for i in range(5)
        ]
        result = backend._insert_chunks_batch(doc_id=1, chunks=chunks)
        assert len(result) == 5
        # 5 chunks / 2 per batch (16 // 8) = 3 batches → 3 _execute calls.
        assert backend._execute.call_count == 3
        # chunk_index keeps incrementing across sub-batches: pull the
        # raw param tuples and confirm indices 0..4 appear in order.
        all_params = []
        for call in backend._execute.call_args_list:
            all_params.extend(call[0][1])
        # chunk_index is the second param in each 8-tuple, so positions
        # 1, 9, 17, ... in the flat tuple list.
        chunk_indices = [all_params[i] for i in range(1, len(all_params), 8)]
        assert chunk_indices == [0, 1, 2, 3, 4]
