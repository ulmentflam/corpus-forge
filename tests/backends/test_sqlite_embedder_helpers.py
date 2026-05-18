"""Phase L Wave 6 — SQLiteBackend embedder helpers (W6-01).

Mirrors :mod:`tests.backends.test_postgres_embedder_helpers` but uses
the real in-memory SQLite backend via the project's existing migrate
helpers.  No mocks — these are end-to-end checks against the live
sqlite schema.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from corpus_forge.backends.sqlite import SQLiteBackend


@pytest.fixture
def backend(tmp_path: Path) -> SQLiteBackend:
    """Migrated SQLite backend on disk in a tmp_path."""

    b = SQLiteBackend(path=str(tmp_path / "corpus.db"))
    b.migrate()
    return b


def _register_embedder(backend: SQLiteBackend, *, name: str = "qwen3_8b") -> int:
    """Insert a row via :meth:`register_embedder` and return its id."""

    embedder = SimpleNamespace(
        name=name,
        provider="sentence_transformers",
        model_id="Qwen/Qwen3-Embedding-8B",
        dimension=1024,
        normalized=True,
        distance="cosine",
        active=True,
    )
    return backend.register_embedder(embedder)


# ─── find_embedder_row_by_name ────────────────────────────────────────────


class TestFindEmbedderRowByName:
    def test_returns_row_dict_for_known(self, backend: SQLiteBackend) -> None:
        embedder_id = _register_embedder(backend)

        row = backend.find_embedder_row_by_name("qwen3_8b")

        assert row is not None
        assert row["id"] == embedder_id
        assert row["name"] == "qwen3_8b"
        assert row["provider"] == "sentence_transformers"
        assert row["model_id"] == "Qwen/Qwen3-Embedding-8B"
        assert row["dimension"] == 1024
        # SQLite stores INTEGER 0/1; the helper coerces to bool.
        assert row["normalized"] is True
        assert row["active"] is True
        assert row["distance"] == "cosine"
        assert row["table_name"] == "embeddings_qwen3_8b"
        # JSON-string config is decoded to a dict for callers.
        assert isinstance(row["config"], dict)
        assert row["config"]["provider"] == "sentence_transformers"

    def test_returns_none_for_unknown(self, backend: SQLiteBackend) -> None:
        assert backend.find_embedder_row_by_name("does_not_exist") is None

    def test_round_trip_with_update_config_blob(self, backend: SQLiteBackend) -> None:
        _register_embedder(backend)
        backend.update_embedder_config_blob(
            "qwen3_8b",
            {
                "provider": "sentence_transformers",
                "model_id": "Qwen/Qwen3-Embedding-8B",
                "dimension": 1024,
                "normalize": True,
                "distance": "cosine",
                "fingerprint": "abc123",
            },
        )

        row = backend.find_embedder_row_by_name("qwen3_8b")
        assert row is not None
        assert row["config"]["fingerprint"] == "abc123"


# ─── count_existing_embeddings ────────────────────────────────────────────


class TestCountExistingEmbeddings:
    def test_zero_when_no_embeddings(self, backend: SQLiteBackend) -> None:
        _register_embedder(backend)
        # No embeddings written yet → 0.
        assert backend.count_existing_embeddings(1) == 0
        assert backend.count_existing_embeddings("qwen3_8b") == 0

    def test_returns_zero_for_unknown(self, backend: SQLiteBackend) -> None:
        # No backend at all yet — still 0, never raises.
        assert backend.count_existing_embeddings(99_999) == 0
        assert backend.count_existing_embeddings("ghost") == 0

    def test_int_and_string_resolve_same_count(self, backend: SQLiteBackend) -> None:
        embedder_id = _register_embedder(backend)
        by_id = backend.count_existing_embeddings(embedder_id)
        by_name = backend.count_existing_embeddings("qwen3_8b")
        assert by_id == by_name == 0


# ─── update_embedder_config_blob ──────────────────────────────────────────


class TestUpdateEmbedderConfigBlob:
    def test_update_by_int_id(self, backend: SQLiteBackend) -> None:
        embedder_id = _register_embedder(backend)
        blob = {"provider": "sentence_transformers", "model_id": "x", "fingerprint": "deadbeef"}

        backend.update_embedder_config_blob(embedder_id, blob)

        row = backend.find_embedder_row_by_name("qwen3_8b")
        assert row is not None
        assert row["config"] == blob

    def test_update_by_string_name(self, backend: SQLiteBackend) -> None:
        _register_embedder(backend)
        blob = {"k": "v", "n": 1}

        backend.update_embedder_config_blob("qwen3_8b", blob)

        row = backend.find_embedder_row_by_name("qwen3_8b")
        assert row is not None
        assert row["config"] == blob

    def test_update_unknown_name_does_not_raise(self, backend: SQLiteBackend) -> None:
        # UPDATE matches zero rows — no exception, no row created.
        backend.update_embedder_config_blob("ghost", {"k": "v"})
        assert backend.find_embedder_row_by_name("ghost") is None

    def test_blob_serialised_as_json(self, backend: SQLiteBackend) -> None:
        """Stored value is valid JSON (so external readers can decode)."""

        _register_embedder(backend)
        blob = {"nested": {"a": 1}, "list": [1, 2]}
        backend.update_embedder_config_blob("qwen3_8b", blob)

        # Direct query (bypassing the helper) returns the raw JSON string.
        rows = backend._execute("SELECT config FROM embedders WHERE name = ?", ("qwen3_8b",))
        assert rows
        raw = rows[0]["config"]
        assert isinstance(raw, str)
        assert json.loads(raw) == blob


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
