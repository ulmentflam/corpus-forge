"""T2 — backend chunk navigation APIs.

Pins the additive surface on :class:`StorageBackend`:

- ``get_chunk(chunk_id)`` now includes ``prev_chunk_id`` and
  ``next_chunk_id`` (additive — existing keys untouched).
- ``get_chunk_neighbors(chunk_id, *, before, after)`` returns neighbor
  chunks (NOT including the anchor) in ``chunk_index`` order.
- ``get_document_chunks(document_id)`` returns every chunk of a document
  ordered by ``chunk_index``.

These tests target the SQLite backend with in-memory or tmp-file
databases — the same fixture pattern as ``test_sqlite_backend.py``.
The Postgres mirror is left for an integration suite under
``tests/integration/`` (testcontainers required); the backend ABC
contract assertions below cover the API surface both implementations
must conform to.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from corpus_forge.backends.sqlite import SQLiteBackend
from corpus_forge.sources.base import RawDocument

# ── Helpers — mirror the patterns in test_sqlite_backend.py ─────────────


def _migrated_backend(db_path: str | Path) -> SQLiteBackend:
    backend = SQLiteBackend(path=str(db_path))
    backend.migrate()
    return backend


def _ensure_dataset(backend: SQLiteBackend, *, dataset_id: int = 1) -> None:
    backend._execute(
        "INSERT OR IGNORE INTO datasets (id, name, kind) VALUES (?, ?, ?)",
        (dataset_id, f"ds{dataset_id}", "text"),
    )


def _seed_doc_with_chunks(
    backend: SQLiteBackend,
    *,
    dataset_id: int,
    source_uri: str,
    title: str,
    chunk_texts: list[str],
) -> int:
    """Upsert a document with ``chunk_texts`` as separate chunks.

    Uses the (heading, text) tuple shape that :meth:`upsert_document`
    accepts — guaranteed one chunk per text, no chunker merging.
    """
    doc = RawDocument(
        source_uri=source_uri,
        content_hash=f"hash:{source_uri}",
        text="\n\n".join(chunk_texts),
        title=title,
        modified_at=1000.0,
        metadata={},
        labels=[],
    )
    chunks = [(None, t) for t in chunk_texts]
    return backend.upsert_document(dataset_id, doc, chunks)


# ── get_chunk additive enrichment ────────────────────────────────────────


class TestGetChunkPrevNextIds:
    """``get_chunk`` returns ``prev_chunk_id`` and ``next_chunk_id`` keys."""

    def test_middle_chunk_has_both_neighbors(self, tmp_path: Path) -> None:
        backend = _migrated_backend(tmp_path / "x.db")
        _ensure_dataset(backend)
        doc_id = _seed_doc_with_chunks(
            backend,
            dataset_id=1,
            source_uri="filesystem://x/a.md",
            title="a",
            chunk_texts=["alpha block", "bravo block", "charlie block"],
        )
        chunks = backend.get_document_chunks(doc_id)
        assert len(chunks) == 3
        mid = backend.get_chunk(chunks[1]["id"])
        assert mid is not None
        assert mid["prev_chunk_id"] == chunks[0]["id"]
        assert mid["next_chunk_id"] == chunks[2]["id"]

    def test_first_chunk_prev_is_none(self, tmp_path: Path) -> None:
        backend = _migrated_backend(tmp_path / "x.db")
        _ensure_dataset(backend)
        doc_id = _seed_doc_with_chunks(
            backend,
            dataset_id=1,
            source_uri="filesystem://x/b.md",
            title="b",
            chunk_texts=["one", "two", "three"],
        )
        chunks = backend.get_document_chunks(doc_id)
        first = backend.get_chunk(chunks[0]["id"])
        assert first is not None
        assert first["prev_chunk_id"] is None
        assert first["next_chunk_id"] == chunks[1]["id"]

    def test_last_chunk_next_is_none(self, tmp_path: Path) -> None:
        backend = _migrated_backend(tmp_path / "x.db")
        _ensure_dataset(backend)
        doc_id = _seed_doc_with_chunks(
            backend,
            dataset_id=1,
            source_uri="filesystem://x/c.md",
            title="c",
            chunk_texts=["one", "two", "three"],
        )
        chunks = backend.get_document_chunks(doc_id)
        last = backend.get_chunk(chunks[-1]["id"])
        assert last is not None
        assert last["prev_chunk_id"] == chunks[-2]["id"]
        assert last["next_chunk_id"] is None

    def test_single_chunk_doc_both_none(self, tmp_path: Path) -> None:
        backend = _migrated_backend(tmp_path / "x.db")
        _ensure_dataset(backend)
        doc_id = _seed_doc_with_chunks(
            backend,
            dataset_id=1,
            source_uri="filesystem://x/only.md",
            title="only",
            chunk_texts=["solo"],
        )
        chunks = backend.get_document_chunks(doc_id)
        assert len(chunks) == 1
        c = backend.get_chunk(chunks[0]["id"])
        assert c is not None
        assert c["prev_chunk_id"] is None
        assert c["next_chunk_id"] is None

    def test_existing_keys_preserved(self, tmp_path: Path) -> None:
        """Additive: ``get_chunk`` retains every existing key."""
        backend = _migrated_backend(tmp_path / "x.db")
        _ensure_dataset(backend)
        doc_id = _seed_doc_with_chunks(
            backend,
            dataset_id=1,
            source_uri="filesystem://x/d.md",
            title="d",
            chunk_texts=["alpha"],
        )
        chunks = backend.get_document_chunks(doc_id)
        c = backend.get_chunk(chunks[0]["id"])
        assert c is not None
        for k in (
            "id",
            "document_id",
            "chunk_index",
            "text",
            "source_uri",
            "title",
            "dataset_id",
        ):
            assert k in c, f"existing key {k!r} must still appear in get_chunk()"


# ── get_chunk_neighbors ──────────────────────────────────────────────────


class TestGetChunkNeighbors:
    def test_window_around_middle(self, tmp_path: Path) -> None:
        backend = _migrated_backend(tmp_path / "x.db")
        _ensure_dataset(backend)
        doc_id = _seed_doc_with_chunks(
            backend,
            dataset_id=1,
            source_uri="filesystem://x/win.md",
            title="w",
            chunk_texts=["c0", "c1", "c2", "c3", "c4"],
        )
        chunks = backend.get_document_chunks(doc_id)
        anchor = chunks[2]
        out = backend.get_chunk_neighbors(anchor["id"], before=1, after=2)
        # Returns only neighbors (not the anchor) in chunk_index order.
        assert [c["chunk_index"] for c in out] == [1, 3, 4]

    def test_before_zero_returns_only_after(self, tmp_path: Path) -> None:
        backend = _migrated_backend(tmp_path / "x.db")
        _ensure_dataset(backend)
        doc_id = _seed_doc_with_chunks(
            backend,
            dataset_id=1,
            source_uri="filesystem://x/win2.md",
            title="w2",
            chunk_texts=["c0", "c1", "c2"],
        )
        chunks = backend.get_document_chunks(doc_id)
        out = backend.get_chunk_neighbors(chunks[0]["id"], before=0, after=2)
        assert [c["chunk_index"] for c in out] == [1, 2]

    def test_after_zero_returns_only_before(self, tmp_path: Path) -> None:
        backend = _migrated_backend(tmp_path / "x.db")
        _ensure_dataset(backend)
        doc_id = _seed_doc_with_chunks(
            backend,
            dataset_id=1,
            source_uri="filesystem://x/win3.md",
            title="w3",
            chunk_texts=["c0", "c1", "c2"],
        )
        chunks = backend.get_document_chunks(doc_id)
        out = backend.get_chunk_neighbors(chunks[2]["id"], before=2, after=0)
        assert [c["chunk_index"] for c in out] == [0, 1]

    def test_missing_chunk_returns_empty(self, tmp_path: Path) -> None:
        backend = _migrated_backend(tmp_path / "x.db")
        out = backend.get_chunk_neighbors(99999, before=2, after=2)
        assert out == []

    def test_default_before_after_one_one(self, tmp_path: Path) -> None:
        backend = _migrated_backend(tmp_path / "x.db")
        _ensure_dataset(backend)
        doc_id = _seed_doc_with_chunks(
            backend,
            dataset_id=1,
            source_uri="filesystem://x/win4.md",
            title="w4",
            chunk_texts=["c0", "c1", "c2", "c3", "c4"],
        )
        chunks = backend.get_document_chunks(doc_id)
        out = backend.get_chunk_neighbors(chunks[2]["id"])
        assert [c["chunk_index"] for c in out] == [1, 3]

    def test_window_clamped_at_edges(self, tmp_path: Path) -> None:
        """A larger window than there are chunks just clamps; no error."""
        backend = _migrated_backend(tmp_path / "x.db")
        _ensure_dataset(backend)
        doc_id = _seed_doc_with_chunks(
            backend,
            dataset_id=1,
            source_uri="filesystem://x/edge.md",
            title="e",
            chunk_texts=["c0", "c1", "c2"],
        )
        chunks = backend.get_document_chunks(doc_id)
        out = backend.get_chunk_neighbors(chunks[0]["id"], before=5, after=5)
        # No chunks before; chunks at index 1, 2 after.
        assert [c["chunk_index"] for c in out] == [1, 2]


# ── get_document_chunks ──────────────────────────────────────────────────


class TestGetDocumentChunks:
    def test_returns_chunks_ordered_by_index(self, tmp_path: Path) -> None:
        backend = _migrated_backend(tmp_path / "x.db")
        _ensure_dataset(backend)
        doc_id = _seed_doc_with_chunks(
            backend,
            dataset_id=1,
            source_uri="filesystem://x/order.md",
            title="o",
            chunk_texts=["a", "b", "c"],
        )
        out = backend.get_document_chunks(doc_id)
        assert [c["chunk_index"] for c in out] == [0, 1, 2]
        assert [c["text"] for c in out] == ["a", "b", "c"]

    def test_empty_for_unknown_document(self, tmp_path: Path) -> None:
        backend = _migrated_backend(tmp_path / "x.db")
        assert backend.get_document_chunks(99999) == []


# ── ABC contract — both backends must implement ────────────────────────


class TestBackendABCContract:
    """Both PostgresBackend and SQLiteBackend must expose the new methods."""

    def test_sqlite_implements_get_chunk_neighbors(self) -> None:
        backend = SQLiteBackend(path=":memory:")
        assert hasattr(backend, "get_chunk_neighbors")
        assert callable(backend.get_chunk_neighbors)

    def test_sqlite_implements_get_document_chunks(self) -> None:
        backend = SQLiteBackend(path=":memory:")
        assert hasattr(backend, "get_document_chunks")
        assert callable(backend.get_document_chunks)

    def test_postgres_implements_get_chunk_neighbors(self) -> None:
        pytest.importorskip("psycopg")
        from corpus_forge.backends.postgres import PostgresBackend

        # Defined on the class itself (not inherited Protocol stub) — proves
        # the Postgres backend actually implements it.
        assert "get_chunk_neighbors" in PostgresBackend.__dict__

    def test_postgres_implements_get_document_chunks(self) -> None:
        pytest.importorskip("psycopg")
        from corpus_forge.backends.postgres import PostgresBackend

        assert "get_document_chunks" in PostgresBackend.__dict__
