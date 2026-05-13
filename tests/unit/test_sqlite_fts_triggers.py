"""R1-02 — execute the SQLite 004_fts.sql against an in-memory db and verify
trigger semantics (INSERT mirrors, UPDATE re-mirrors, DELETE removes).

This is a standalone trigger test: it builds a minimal ``chunks`` table with
just the columns FTS5 references (``id`` + ``text``), then applies
``004_fts.sql`` via a permissive statement splitter that respects ``BEGIN ...
END`` blocks for the trigger bodies. After the migration, INSERTs / UPDATEs /
DELETEs on ``chunks`` must be visible in ``chunks_fts`` MATCH queries.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

SQLITE_SCHEMA_DIR = Path(__file__).resolve().parents[2] / "corpus_forge" / "schema" / "sqlite"
MIGRATION_FILE = SQLITE_SCHEMA_DIR / "004_fts.sql"


def _has_fts5() -> bool:
    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        conn.close()
        return True
    except sqlite3.OperationalError:
        return False


pytestmark = pytest.mark.skipif(not _has_fts5(), reason="sqlite3 not built with FTS5")


@pytest.fixture
def db_with_chunks() -> sqlite3.Connection:
    """An in-memory db with a minimal ``chunks`` table.

    We don't need the full schema — only the columns the FTS5 triggers read
    (``id`` and ``text``).
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL
        )
        """
    )
    return conn


def _apply_migration(conn: sqlite3.Connection, sql_path: Path) -> None:
    """Apply the migration file using ``executescript``, which handles
    multi-statement files (including BEGIN/END trigger bodies) correctly."""
    conn.executescript(sql_path.read_text())


class TestSchemaApplies:
    def test_migration_executes_without_error(self, db_with_chunks):
        _apply_migration(db_with_chunks, MIGRATION_FILE)
        # chunks_fts virtual table is visible in sqlite_master
        row = db_with_chunks.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='chunks_fts'"
        ).fetchone()
        assert row is not None, "chunks_fts virtual table was not created"

    def test_migration_is_idempotent(self, db_with_chunks):
        _apply_migration(db_with_chunks, MIGRATION_FILE)
        # Re-apply — must not error (IF NOT EXISTS everywhere)
        _apply_migration(db_with_chunks, MIGRATION_FILE)

    def test_three_triggers_exist(self, db_with_chunks):
        _apply_migration(db_with_chunks, MIGRATION_FILE)
        rows = db_with_chunks.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY name"
        ).fetchall()
        names = sorted(r["name"] for r in rows)
        assert names == ["chunks_ad", "chunks_ai", "chunks_au"], names


class TestInsertTrigger:
    def test_chunks_ai_mirrors_into_fts(self, db_with_chunks):
        _apply_migration(db_with_chunks, MIGRATION_FILE)
        db_with_chunks.execute("INSERT INTO chunks (text) VALUES (?)", ("the quick brown fox",))
        db_with_chunks.commit()

        hits = db_with_chunks.execute(
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?", ("brown",)
        ).fetchall()
        assert len(hits) == 1, f"Expected one FTS hit for 'brown', got {len(hits)}"

    def test_multiple_inserts_all_indexed(self, db_with_chunks):
        _apply_migration(db_with_chunks, MIGRATION_FILE)
        for text in ("alpha beta gamma", "delta epsilon zeta", "alpha omega"):
            db_with_chunks.execute("INSERT INTO chunks (text) VALUES (?)", (text,))
        db_with_chunks.commit()
        hits = db_with_chunks.execute(
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY rowid", ("alpha",)
        ).fetchall()
        assert len(hits) == 2, f"Expected 2 FTS hits for 'alpha', got {len(hits)}"


class TestDeleteTrigger:
    def test_chunks_ad_removes_from_fts(self, db_with_chunks):
        _apply_migration(db_with_chunks, MIGRATION_FILE)
        db_with_chunks.execute("INSERT INTO chunks (text) VALUES (?)", ("hello world",))
        db_with_chunks.commit()
        # Confirm indexed
        hits_before = db_with_chunks.execute(
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?", ("hello",)
        ).fetchall()
        assert len(hits_before) == 1

        # Delete
        db_with_chunks.execute("DELETE FROM chunks WHERE text = ?", ("hello world",))
        db_with_chunks.commit()
        hits_after = db_with_chunks.execute(
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?", ("hello",)
        ).fetchall()
        assert len(hits_after) == 0, "DELETE trigger must remove the row from chunks_fts"


class TestUpdateTrigger:
    def test_chunks_au_remirrors(self, db_with_chunks):
        _apply_migration(db_with_chunks, MIGRATION_FILE)
        cur = db_with_chunks.execute("INSERT INTO chunks (text) VALUES (?)", ("old content",))
        chunk_id = cur.lastrowid
        db_with_chunks.commit()

        # Search for old content — should find one
        hits_old = db_with_chunks.execute(
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?", ("old",)
        ).fetchall()
        assert len(hits_old) == 1

        # Update
        db_with_chunks.execute("UPDATE chunks SET text = ? WHERE id = ?", ("new content", chunk_id))
        db_with_chunks.commit()

        # Old token gone
        hits_old_after = db_with_chunks.execute(
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?", ("old",)
        ).fetchall()
        assert len(hits_old_after) == 0, "UPDATE trigger must remove the old token"

        # New token present
        hits_new = db_with_chunks.execute(
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?", ("new",)
        ).fetchall()
        assert len(hits_new) == 1, "UPDATE trigger must mirror the new text"


class TestPorterStemmer:
    def test_porter_unicode61_tokenizer(self, db_with_chunks):
        """The 'porter' stemmer makes plural / singular forms match each other."""
        _apply_migration(db_with_chunks, MIGRATION_FILE)
        db_with_chunks.execute("INSERT INTO chunks (text) VALUES (?)", ("running quickly",))
        db_with_chunks.commit()
        # Porter stemmer should match 'run' to 'running'
        hits = db_with_chunks.execute(
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?", ("run",)
        ).fetchall()
        assert len(hits) == 1, "Porter stemmer must match 'run' to 'running'"
