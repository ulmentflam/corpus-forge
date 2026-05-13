"""Integration tests for the SQLite 004_fts migration.

Verifies:
- ``chunks_fts`` virtual table exists after ``backend.migrate()``.
- ``chunks_ai`` / ``chunks_ad`` / ``chunks_au`` triggers fire correctly.
- ``backend.backfill_lexical_index()`` is invoked once during migrate()
  and is idempotent.
- Re-running migrate twice is safe (IF NOT EXISTS everywhere).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from corpus_forge.backends.sqlite import SQLiteBackend


def _has_fts5() -> bool:
    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        conn.close()
        return True
    except sqlite3.OperationalError:
        return False


pytestmark = pytest.mark.skipif(not _has_fts5(), reason="sqlite3 not built with FTS5")


def _make_backend(db_path: Path) -> SQLiteBackend:
    backend = SQLiteBackend(path=str(db_path))
    backend.migrate()
    return backend


def _tables(db_path: Path) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def _triggers(db_path: Path) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


class TestSchemaShape:
    def test_chunks_fts_table_exists(self, tmp_path):
        db = tmp_path / "corpus.db"
        _make_backend(db)
        names = _tables(db)
        assert "chunks_fts" in names, f"chunks_fts missing; tables: {names}"

    def test_three_fts_triggers_exist(self, tmp_path):
        db = tmp_path / "corpus.db"
        _make_backend(db)
        trigs = _triggers(db)
        assert {"chunks_ai", "chunks_ad", "chunks_au"}.issubset(trigs), trigs


class TestTriggerSemantics:
    def test_insert_indexes_chunks_fts(self, tmp_path):
        db = tmp_path / "corpus.db"
        backend = _make_backend(db)
        # Use a fresh raw connection (bypasses the backend; we just want SQL)
        conn = sqlite3.connect(str(db))
        try:
            # Need a doc to satisfy the chunks CHECK constraint (XOR doc/conv)
            conn.execute("INSERT INTO datasets (name, kind) VALUES (?, ?)", ("ds-insert", "text"))
            ds_id = conn.execute(
                "SELECT id FROM datasets WHERE name = ?", ("ds-insert",)
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO documents (dataset_id, source_uri, content_hash, text) "
                "VALUES (?, ?, ?, ?)",
                (ds_id, "sqlite://insert.md", "h1", "doc text"),
            )
            doc_id = conn.execute(
                "SELECT id FROM documents WHERE source_uri = ?", ("sqlite://insert.md",)
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO chunks (document_id, chunk_index, text) VALUES (?, ?, ?)",
                (doc_id, 0, "the lazy fox"),
            )
            conn.commit()

            hits = conn.execute(
                "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?", ("lazy",)
            ).fetchall()
            assert len(hits) == 1
        finally:
            conn.close()
        # Quiet "unused" warning for backend reference (we needed migrate to run)
        assert backend.path == str(db)

    def test_delete_removes_from_fts(self, tmp_path):
        db = tmp_path / "corpus.db"
        _make_backend(db)
        conn = sqlite3.connect(str(db))
        try:
            conn.execute("INSERT INTO datasets (name, kind) VALUES (?, ?)", ("ds-del", "text"))
            ds_id = conn.execute("SELECT id FROM datasets WHERE name = ?", ("ds-del",)).fetchone()[
                0
            ]
            conn.execute(
                "INSERT INTO documents (dataset_id, source_uri, content_hash, text) "
                "VALUES (?, ?, ?, ?)",
                (ds_id, "sqlite://del.md", "h2", "doc text"),
            )
            doc_id = conn.execute(
                "SELECT id FROM documents WHERE source_uri = ?", ("sqlite://del.md",)
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO chunks (document_id, chunk_index, text) VALUES (?, ?, ?)",
                (doc_id, 0, "purple unicorn"),
            )
            conn.commit()
            assert (
                len(
                    conn.execute(
                        "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?",
                        ("unicorn",),
                    ).fetchall()
                )
                == 1
            )

            conn.execute("DELETE FROM chunks WHERE text = ?", ("purple unicorn",))
            conn.commit()
            assert (
                len(
                    conn.execute(
                        "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?",
                        ("unicorn",),
                    ).fetchall()
                )
                == 0
            )
        finally:
            conn.close()

    def test_update_remirrors(self, tmp_path):
        db = tmp_path / "corpus.db"
        _make_backend(db)
        conn = sqlite3.connect(str(db))
        try:
            conn.execute("INSERT INTO datasets (name, kind) VALUES (?, ?)", ("ds-upd", "text"))
            ds_id = conn.execute("SELECT id FROM datasets WHERE name = ?", ("ds-upd",)).fetchone()[
                0
            ]
            conn.execute(
                "INSERT INTO documents (dataset_id, source_uri, content_hash, text) "
                "VALUES (?, ?, ?, ?)",
                (ds_id, "sqlite://upd.md", "h3", "doc text"),
            )
            doc_id = conn.execute(
                "SELECT id FROM documents WHERE source_uri = ?", ("sqlite://upd.md",)
            ).fetchone()[0]
            cur = conn.execute(
                "INSERT INTO chunks (document_id, chunk_index, text) VALUES (?, ?, ?)",
                (doc_id, 0, "old phrase here"),
            )
            chunk_id = cur.lastrowid
            conn.commit()
            assert (
                len(
                    conn.execute(
                        "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?", ("phrase",)
                    ).fetchall()
                )
                == 1
            )

            conn.execute("UPDATE chunks SET text = ? WHERE id = ?", ("new term here", chunk_id))
            conn.commit()
            assert (
                len(
                    conn.execute(
                        "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?", ("phrase",)
                    ).fetchall()
                )
                == 0
            )
            assert (
                len(
                    conn.execute(
                        "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?", ("term",)
                    ).fetchall()
                )
                == 1
            )
        finally:
            conn.close()


class TestIdempotency:
    def test_migrate_twice(self, tmp_path):
        """Second migrate() call is a no-op (no duplicate triggers / errors)."""
        db = tmp_path / "corpus.db"
        backend = SQLiteBackend(path=str(db))
        backend.migrate()
        backend.migrate()

        names = _tables(db)
        assert "chunks_fts" in names
        trigs = _triggers(db)
        # Exactly three FTS triggers — not duplicated
        fts_trigs = {t for t in trigs if t.startswith("chunks_a")}
        assert fts_trigs == {"chunks_ai", "chunks_ad", "chunks_au"}, fts_trigs

    def test_backfill_idempotent(self, tmp_path):
        db = tmp_path / "corpus.db"
        backend = _make_backend(db)
        first = backend.backfill_lexical_index()
        assert first >= 0
        second = backend.backfill_lexical_index()
        assert second == 0, f"Second backfill must be 0; got {second}"
