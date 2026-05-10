"""Tests for SQLiteBackend.__init__ + migrate() — B-03.

Verifies:
- Constructor accepts path (str or Path) and schema, stores path, does NOT open a
  connection eagerly.
- ":memory:" works as an in-memory database.
- _get_connection() (lazily called) sets WAL journal_mode and enables foreign_keys.
- migrate() applies all three SQLite schema migrations idempotently.
- All expected tables exist after migrate().
- The Postgres-only 002 backfill does NOT run for dialect="sqlite".
- Failure modes: directory path and missing parent directory.
"""

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Import will fail until coder creates corpus_forge/backends/sqlite.py.
# This causes the entire module to fail collection with ImportError / ModuleNotFoundError,
# which is the desired red signal.
# ---------------------------------------------------------------------------
from corpus_forge.backends.sqlite import SQLiteBackend

# ---------------------------------------------------------------------------
# Helpers shared across test classes
# ---------------------------------------------------------------------------

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "corpus_forge" / "schema"


def _tables(db_path: str | Path) -> list[str]:
    """Return sorted table names from a database file or ':memory:' path via a raw connection."""
    path = str(db_path)
    conn = sqlite3.connect(path)
    try:
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        return [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# TestConstruction
# ---------------------------------------------------------------------------


class TestConstruction:
    """Constructor stores path, is lazy, accepts str/Path/':memory:'."""

    def test_accepts_str_path(self, tmp_path):
        """Happy path: str path is accepted without error."""
        db_path = str(tmp_path / "test.db")
        backend = SQLiteBackend(path=db_path)
        assert backend is not None

    def test_accepts_pathlib_path(self, tmp_path):
        """Happy path: pathlib.Path object is accepted."""
        db_path = tmp_path / "test.db"
        backend = SQLiteBackend(path=db_path)
        assert backend is not None

    def test_accepts_in_memory_string(self):
        """Special case: ':memory:' is accepted as path."""
        backend = SQLiteBackend(path=":memory:")
        assert backend is not None

    def test_stores_path_attribute(self, tmp_path):
        """Constructor stores the path for later use."""
        db_path = tmp_path / "test.db"
        backend = SQLiteBackend(path=db_path)
        # The stored path should resolve to the given path (str or Path form)
        stored = backend.path if hasattr(backend, "path") else backend.db_path
        assert str(stored) == str(db_path)

    def test_schema_parameter_accepted(self, tmp_path):
        """schema param is accepted (protocol symmetry, unused for SQLite)."""
        db_path = tmp_path / "test.db"
        backend = SQLiteBackend(path=db_path, schema="my_schema")
        assert backend is not None

    def test_schema_defaults_to_corpus(self, tmp_path):
        """schema defaults to 'corpus' (matches PostgresBackend convention)."""
        db_path = tmp_path / "test.db"
        backend = SQLiteBackend(path=db_path)
        assert backend.schema == "corpus"

    def test_no_file_created_on_construction(self, tmp_path):
        """Constructor does NOT create the DB file — connection is lazy."""
        db_path = tmp_path / "lazy.db"
        assert not db_path.exists(), "File must not exist before construction"
        _backend = SQLiteBackend(path=db_path)
        # File should still not exist — no eager open occurred
        assert not db_path.exists(), (
            "SQLiteBackend must not open a connection in __init__. Use lazy _get_connection()."
        )

    def test_in_memory_get_connection_returns_connection(self):
        """_get_connection() on ':memory:' yields a sqlite3.Connection."""
        backend = SQLiteBackend(path=":memory:")
        with backend._get_connection() as conn:
            assert isinstance(conn, sqlite3.Connection)

    def test_get_connection_returns_fresh_connection_each_call(self):
        """Each _get_connection() call yields an independent connection object."""
        backend = SQLiteBackend(path=":memory:")
        with backend._get_connection() as conn1:
            pass
        with backend._get_connection() as conn2:
            pass
        # Two separate context manager invocations must yield different objects
        assert conn1 is not conn2


# ---------------------------------------------------------------------------
# TestPragmasAfterMigrate
# ---------------------------------------------------------------------------


class TestPragmasAfterMigrate:
    """After migrate(), the connection PRAGMAs must be correctly set."""

    def test_journal_mode_is_wal(self, tmp_path):
        """PRAGMA journal_mode must be 'wal' after migrate()."""
        db_path = tmp_path / "test.db"
        backend = SQLiteBackend(path=db_path)
        backend.migrate()
        with backend._get_connection() as conn:
            row = conn.execute("PRAGMA journal_mode").fetchone()
            # row[0] or row['journal_mode'] depending on row_factory
            mode = row[0] if not hasattr(row, "keys") else row["journal_mode"]
            assert mode == "wal", f"Expected journal_mode='wal', got {mode!r}"

    def test_foreign_keys_enabled(self, tmp_path):
        """PRAGMA foreign_keys must be 1 (ON) after migrate()."""
        db_path = tmp_path / "test.db"
        backend = SQLiteBackend(path=db_path)
        backend.migrate()
        with backend._get_connection() as conn:
            row = conn.execute("PRAGMA foreign_keys").fetchone()
            fk = row[0] if not hasattr(row, "keys") else row["foreign_keys"]
            assert fk == 1, f"Expected foreign_keys=1, got {fk!r}"

    def test_in_memory_journal_mode(self):
        """':memory:' DB also sets WAL (or memory mode, which is acceptable)."""
        backend = SQLiteBackend(path=":memory:")
        backend.migrate()
        with backend._get_connection() as conn:
            row = conn.execute("PRAGMA journal_mode").fetchone()
            mode = row[0] if not hasattr(row, "keys") else row["journal_mode"]
            # SQLite in-memory DBs report 'memory' for WAL requests — both are fine.
            assert mode in ("wal", "memory"), (
                f"Expected journal_mode 'wal' or 'memory', got {mode!r}"
            )

    def test_row_factory_is_set(self, tmp_path):
        """_get_connection() uses sqlite3.Row as row_factory."""
        db_path = tmp_path / "test.db"
        backend = SQLiteBackend(path=db_path)
        backend.migrate()
        with backend._get_connection() as conn:
            assert conn.row_factory is sqlite3.Row, "Connection must use sqlite3.Row as row_factory"


# ---------------------------------------------------------------------------
# TestSchemaTablePresence
# ---------------------------------------------------------------------------


class TestSchemaTablePresence:
    """After migrate(), all expected tables must exist."""

    # Tables extracted from 001_core.sql, 002_chunk_content_hash.sql, 003_sync.sql.
    # 002 adds a column to chunks (no new table).
    # 003 adds document_revisions table.
    EXPECTED_TABLES = sorted(
        [
            "datasets",
            "sources",
            "documents",
            "conversations",
            "messages",
            "chunks",
            "embedders",
            "labels",
            "chunk_labels",
            "document_labels",
            "conversation_labels",
            "document_revisions",
        ]
    )

    def test_all_expected_tables_present(self, tmp_path):
        """All 12 tables from 001+003 migrations exist after migrate()."""
        db_path = tmp_path / "test.db"
        backend = SQLiteBackend(path=db_path)
        backend.migrate()
        tables = _tables(db_path)
        for expected in self.EXPECTED_TABLES:
            assert expected in tables, (
                f"Table '{expected}' missing after migrate(). Found: {tables}"
            )

    def test_exact_table_count(self, tmp_path):
        """Exactly the expected set of tables exists — no extras, no gaps."""
        db_path = tmp_path / "test.db"
        backend = SQLiteBackend(path=db_path)
        backend.migrate()
        tables = sorted(_tables(db_path))
        assert tables == self.EXPECTED_TABLES, (
            f"Table list mismatch.\n  Expected: {self.EXPECTED_TABLES}\n  Got: {tables}"
        )

    def test_chunks_content_hash_column_added(self, tmp_path):
        """002 migration adds content_hash column to chunks table."""
        db_path = tmp_path / "test.db"
        backend = SQLiteBackend(path=db_path)
        backend.migrate()
        conn = sqlite3.connect(str(db_path))
        try:
            cursor = conn.execute("PRAGMA table_info(chunks)")
            column_names = [row[1] for row in cursor.fetchall()]
        finally:
            conn.close()
        assert "content_hash" in column_names, (
            "002 migration must add content_hash column to chunks"
        )

    def test_document_revisions_has_correct_columns(self, tmp_path):
        """003 migration: document_revisions table has expected columns."""
        db_path = tmp_path / "test.db"
        backend = SQLiteBackend(path=db_path)
        backend.migrate()
        conn = sqlite3.connect(str(db_path))
        try:
            cursor = conn.execute("PRAGMA table_info(document_revisions)")
            column_names = [row[1] for row in cursor.fetchall()]
        finally:
            conn.close()
        for expected_col in (
            "id",
            "document_id",
            "revision_number",
            "parent_revision_id",
            "content_hash",
            "text",
            "author_host",
            "is_tombstone",
            "metadata",
            "created_at",
        ):
            assert expected_col in column_names, (
                f"Column '{expected_col}' missing from document_revisions. Got: {column_names}"
            )

    def test_in_memory_tables_present(self):
        """All expected tables present after migrate() on ':memory:' DB."""
        backend = SQLiteBackend(path=":memory:")
        backend.migrate()
        with backend._get_connection() as conn:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            tables = [row[0] for row in cursor.fetchall()]
        for expected in self.EXPECTED_TABLES:
            assert expected in tables, (
                f"Table '{expected}' missing after in-memory migrate(). Found: {tables}"
            )


# ---------------------------------------------------------------------------
# TestMigrateIdempotency
# ---------------------------------------------------------------------------


class TestMigrateIdempotency:
    """Calling migrate() twice must be a no-op (no errors, no duplicate tables)."""

    def test_double_migrate_no_error(self, tmp_path):
        """migrate() called twice must not raise any exception."""
        db_path = tmp_path / "test.db"
        backend = SQLiteBackend(path=db_path)
        backend.migrate()
        # Second call must not raise
        backend.migrate()

    def test_double_migrate_same_table_set(self, tmp_path):
        """Table set after two migrate() calls is identical to after one."""
        db_path = tmp_path / "test.db"
        backend = SQLiteBackend(path=db_path)
        backend.migrate()
        tables_after_first = sorted(_tables(db_path))
        backend.migrate()
        tables_after_second = sorted(_tables(db_path))
        assert tables_after_first == tables_after_second, (
            "migrate() must be idempotent — table list must not change on second call"
        )

    def test_double_migrate_in_memory_no_error(self):
        """migrate() twice on ':memory:' backend must not raise."""
        backend = SQLiteBackend(path=":memory:")
        backend.migrate()
        backend.migrate()

    def test_migrate_from_separate_backend_instance(self, tmp_path):
        """Two separate backend instances pointing at same file — both migrate() succeed."""
        db_path = tmp_path / "test.db"
        backend_a = SQLiteBackend(path=db_path)
        backend_b = SQLiteBackend(path=db_path)
        backend_a.migrate()
        # Second instance migrating same already-migrated file must be a no-op
        backend_b.migrate()
        tables = sorted(_tables(db_path))
        assert sorted(TestSchemaTablePresence.EXPECTED_TABLES) == tables


# ---------------------------------------------------------------------------
# TestBackfillGating
# ---------------------------------------------------------------------------


class TestBackfillGating:
    """The 002 Postgres-only backfill must NOT run when dialect='sqlite'."""

    def test_apply_migrations_called_with_sqlite_dialect(self, tmp_path):
        """migrate() calls apply_migrations with dialect='sqlite'."""
        from corpus_forge.schema import migrate as migrate_module

        db_path = tmp_path / "test.db"
        backend = SQLiteBackend(path=db_path)

        captured_kwargs: list[dict] = []

        original_apply = migrate_module.apply_migrations

        def spy_apply(backend_arg, schema_dir, dialect="postgres"):
            captured_kwargs.append({"schema_dir": schema_dir, "dialect": dialect})
            return original_apply(backend_arg, schema_dir, dialect=dialect)

        with patch.object(migrate_module, "apply_migrations", side_effect=spy_apply):
            backend.migrate()

        assert len(captured_kwargs) >= 1, "migrate() must call apply_migrations"
        used_dialect = captured_kwargs[0]["dialect"]
        assert used_dialect == "sqlite", (
            f"migrate() must call apply_migrations with dialect='sqlite', got {used_dialect!r}"
        )

    def test_content_hash_null_not_backfilled(self, tmp_path):
        """002 backfill (UPDATE chunks SET content_hash=... WHERE content_hash IS NULL)
        must NOT run for SQLite dialect.

        Strategy: after first migrate(), insert a dataset + document + chunk with
        NULL content_hash (the column is nullable in SQLite's 002 schema).
        Then call migrate() again. If the backfill ran, content_hash would be
        updated. Assert it is still NULL.
        """
        db_path = tmp_path / "test.db"
        backend = SQLiteBackend(path=db_path)
        backend.migrate()

        # Insert prerequisite rows (FK chain: datasets → documents → chunks)
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA foreign_keys = OFF")  # relax FK for test setup
        try:
            conn.execute("INSERT INTO datasets (name, kind) VALUES ('test_ds', 'text')")
            dataset_id = conn.execute("SELECT id FROM datasets WHERE name='test_ds'").fetchone()[0]
            conn.execute(
                "INSERT INTO documents (dataset_id, source_uri, content_hash, text)"
                " VALUES (?, 'src://x', 'hash0', 'body')",
                (dataset_id,),
            )
            doc_id = conn.execute("SELECT id FROM documents LIMIT 1").fetchone()[0]
            conn.execute(
                "INSERT INTO chunks (document_id, chunk_index, text, content_hash)"
                " VALUES (?, 0, 'chunk text', NULL)",
                (doc_id,),
            )
            chunk_id = conn.execute("SELECT id FROM chunks LIMIT 1").fetchone()[0]
            conn.commit()
        finally:
            conn.close()

        # Second migrate() — backfill must NOT update the NULL content_hash
        backend.migrate()

        conn2 = sqlite3.connect(str(db_path))
        try:
            row = conn2.execute(
                "SELECT content_hash FROM chunks WHERE id=?", (chunk_id,)
            ).fetchone()
        finally:
            conn2.close()

        assert row is not None, "Chunk row must still exist after second migrate()"
        assert row[0] is None, (
            "content_hash must remain NULL — Postgres-only backfill must not run for SQLite. "
            f"Got content_hash={row[0]!r}"
        )

    def test_no_postgres_backfill_sql_executed(self, tmp_path):
        """No sha256 / encode UPDATE SQL must be executed against the SQLite backend."""
        db_path = tmp_path / "test.db"
        backend = SQLiteBackend(path=db_path)

        executed_sqls: list[str] = []
        original_execute = backend._execute

        def spy_execute(sql, *args, **kwargs):
            executed_sqls.append(sql.strip())
            return original_execute(sql, *args, **kwargs)

        backend._execute = spy_execute
        backend.migrate()

        # The Postgres backfill uses sha256 / encode — these should not appear
        for sql in executed_sqls:
            sql_upper = sql.upper()
            assert "SHA256" not in sql_upper, (
                f"Postgres sha256 backfill SQL must not run for SQLite. Got: {sql!r}"
            )
            assert "ENCODE(" not in sql_upper, (
                f"Postgres encode() backfill SQL must not run for SQLite. Got: {sql!r}"
            )


# ---------------------------------------------------------------------------
# TestFailureModes
# ---------------------------------------------------------------------------


class TestFailureModes:
    """Failure paths: directory path, missing parent."""

    def test_path_is_directory_raises_operational_error(self, tmp_path):
        """Opening a directory as a DB file raises sqlite3.OperationalError."""
        # tmp_path itself is a directory
        backend = SQLiteBackend(path=tmp_path)
        with pytest.raises(sqlite3.OperationalError):
            # Connection is lazy; migrate() (or explicit _get_connection) triggers it
            backend.migrate()

    def test_path_is_directory_error_is_raised_on_connection(self, tmp_path):
        """Constructor itself does NOT raise — only _get_connection() raises."""
        # This verifies the lazy-connect contract
        dir_path = tmp_path  # a directory
        # __init__ must not raise
        backend = SQLiteBackend(path=dir_path)
        # But migrate() (which calls _get_connection) must raise
        with pytest.raises(sqlite3.OperationalError):
            backend.migrate()

    def test_missing_parent_directory_raises(self, tmp_path):
        """Path whose parent does not exist raises sqlite3.OperationalError."""
        nonexistent_parent = tmp_path / "nonexistent_subdir" / "corpus.db"
        backend = SQLiteBackend(path=nonexistent_parent)
        with pytest.raises(sqlite3.OperationalError):
            backend.migrate()

    def test_missing_parent_constructor_does_not_raise(self, tmp_path):
        """Constructor does NOT raise for missing parent — stays lazy."""
        nonexistent_parent = tmp_path / "nonexistent" / "corpus.db"
        # Must not raise
        backend = SQLiteBackend(path=nonexistent_parent)
        assert backend is not None
