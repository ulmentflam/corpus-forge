"""Phase L Wave 6 — PostgresBackend embedder helpers (W6-01).

Three helpers are added to :class:`corpus_forge.backends.postgres.PostgresBackend`
so the Wave-5 :mod:`corpus_forge.embedders.fingerprint` drift path
activates on real backends instead of the ``getattr`` shim:

1. ``find_embedder_row_by_name(name) -> dict | None`` —
   row dict (with parsed ``config``) or ``None`` when the row is missing.
2. ``count_existing_embeddings(embedder_id_or_name) -> int`` —
   accepts int or str and consults the per-embedder table.
3. ``update_embedder_config_blob(embedder_id_or_name, blob) -> None`` —
   writes the JSONB blob; accepts int (by id) or str (by name).

All psycopg I/O is replaced with mocked ``_execute`` calls so these
tests run without a Postgres server.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from corpus_forge.backends.postgres import PostgresBackend


def _make_backend() -> PostgresBackend:
    """Return a PostgresBackend with __init__ bypassed."""
    with patch.object(PostgresBackend, "__init__", lambda self, dsn, schema="corpus": None):
        backend = PostgresBackend.__new__(PostgresBackend)
        backend.dsn = "postgresql://test/test"
        backend.schema = "corpus"
    return backend


# ─── find_embedder_row_by_name ────────────────────────────────────────────


class TestFindEmbedderRowByName:
    def test_returns_row_dict_when_found(self):
        backend = _make_backend()
        row = {
            "id": 7,
            "name": "qwen3_8b",
            "provider": "sentence_transformers",
            "model_id": "Qwen/Qwen3-Embedding-8B",
            "dimension": 1024,
            "normalized": True,
            "distance": "cosine",
            "active": True,
            "table_name": "embeddings_qwen3_8b",
            "config": {"provider": "sentence_transformers", "model_id": "Qwen/Qwen3-Embedding-8B"},
        }
        backend._execute = MagicMock(return_value=[row])

        result = backend.find_embedder_row_by_name("qwen3_8b")

        assert result is not None
        assert result["id"] == 7
        assert result["name"] == "qwen3_8b"
        assert result["dimension"] == 1024
        assert result["table_name"] == "embeddings_qwen3_8b"
        # config is a dict (parsed; not a JSON string)
        assert isinstance(result["config"], dict)
        assert result["config"]["model_id"] == "Qwen/Qwen3-Embedding-8B"

    def test_returns_none_when_missing(self):
        backend = _make_backend()
        backend._execute = MagicMock(return_value=[])
        assert backend.find_embedder_row_by_name("unknown") is None

    def test_handles_string_config_blob(self):
        """psycopg.types.json should already decode JSONB, but defensive
        handling for downstream paths that hand us a JSON string."""

        backend = _make_backend()
        backend._execute = MagicMock(
            return_value=[
                {
                    "id": 1,
                    "name": "foo",
                    "provider": "sentence_transformers",
                    "model_id": "m",
                    "dimension": 16,
                    "normalized": True,
                    "distance": "cosine",
                    "active": True,
                    "table_name": "embeddings_foo",
                    "config": '{"k": "v"}',  # JSON-as-string (rare but possible)
                }
            ]
        )
        result = backend.find_embedder_row_by_name("foo")
        assert result is not None
        assert isinstance(result["config"], dict)
        assert result["config"]["k"] == "v"

    def test_query_uses_schema_prefix(self):
        backend = _make_backend()
        backend._execute = MagicMock(return_value=[])
        backend.find_embedder_row_by_name("foo")
        # Confirm the SQL references the schema-qualified table.
        sql_called, params = backend._execute.call_args[0]
        assert "corpus.embedders" in sql_called
        assert "name = %s" in sql_called
        assert params == ("foo",)


# ─── count_existing_embeddings ────────────────────────────────────────────


class TestCountExistingEmbeddings:
    def test_count_by_int_id(self):
        backend = _make_backend()
        # First call resolves the table_name; second counts rows.
        backend._execute = MagicMock(
            side_effect=[
                [{"id": 7, "table_name": "embeddings_qwen3_8b"}],
                [{"n": 1234}],
            ]
        )

        result = backend.count_existing_embeddings(7)

        assert result == 1234
        # First SQL: SELECT id, table_name FROM corpus.embedders WHERE id = %s
        first_sql, first_params = backend._execute.call_args_list[0][0]
        assert "id = %s" in first_sql
        assert first_params == (7,)
        # Second SQL: SELECT COUNT(*) FROM corpus.<table> WHERE embedder_id = %s
        second_sql, second_params = backend._execute.call_args_list[1][0]
        assert "embeddings_qwen3_8b" in second_sql
        assert second_params == (7,)

    def test_count_by_string_name(self):
        backend = _make_backend()
        backend._execute = MagicMock(
            side_effect=[
                [{"id": 9, "table_name": "embeddings_bge_m3"}],
                [{"n": 42}],
            ]
        )

        result = backend.count_existing_embeddings("bge_m3")

        assert result == 42
        first_sql, first_params = backend._execute.call_args_list[0][0]
        assert "name = %s" in first_sql
        assert first_params == ("bge_m3",)

    def test_returns_zero_when_unknown(self):
        backend = _make_backend()
        backend._execute = MagicMock(return_value=[])
        assert backend.count_existing_embeddings(99999) == 0
        assert backend.count_existing_embeddings("ghost") == 0


# ─── update_embedder_config_blob ──────────────────────────────────────────


class TestUpdateEmbedderConfigBlob:
    def test_updates_by_int_id(self):
        backend = _make_backend()
        backend._execute = MagicMock(return_value=[])

        backend.update_embedder_config_blob(7, {"k": "v"})

        sql_called, params = backend._execute.call_args[0]
        assert "UPDATE corpus.embedders" in sql_called
        assert "SET config = %s" in sql_called
        assert "WHERE id = %s" in sql_called
        # Blob is wrapped in psycopg.types.json.Json for proper JSONB binding.
        import psycopg

        assert isinstance(params[0], psycopg.types.json.Json)
        assert params[1] == 7

    def test_updates_by_string_name(self):
        backend = _make_backend()
        backend._execute = MagicMock(return_value=[])

        backend.update_embedder_config_blob("qwen3_8b", {"k": "v"})

        sql_called, params = backend._execute.call_args[0]
        assert "UPDATE corpus.embedders" in sql_called
        assert "WHERE name = %s" in sql_called
        assert params[1] == "qwen3_8b"

    def test_returns_none(self):
        backend = _make_backend()
        backend._execute = MagicMock(return_value=[])
        result = backend.update_embedder_config_blob(7, {})
        assert result is None


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
