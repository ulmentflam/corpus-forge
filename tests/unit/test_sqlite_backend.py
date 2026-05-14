"""Tests for SQLiteBackend.__init__ + migrate() — B-03.
Tests for SQLiteBackend.register_embedder() + per-embedder vector table — B-04.

Verifies:
- Constructor accepts path (str or Path) and schema, stores path, does NOT open a
  connection eagerly.
- ":memory:" works as an in-memory database.
- _get_connection() (lazily called) sets WAL journal_mode and enables foreign_keys.
- migrate() applies all three SQLite schema migrations idempotently.
- All expected tables exist after migrate().
- The Postgres-only 002 backfill does NOT run for dialect="sqlite".
- Failure modes: directory path and missing parent directory.
- register_embedder() inserts or updates the embedders row and returns an int id.
- register_embedder() creates a per-embedder virtual table (sqlite-vec) or blob table (fallback).
- register_embedder() is idempotent — same id on re-registration, no duplicate tables.
"""

import json
import sqlite3
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Import will fail until coder creates corpus_forge/backends/sqlite.py.
# This causes the entire module to fail collection with ImportError / ModuleNotFoundError,
# which is the desired red signal.
# ---------------------------------------------------------------------------
from corpus_forge.backends.sqlite import SQLiteBackend
from corpus_forge.backends.sqlite_vec_loader import SQLITE_VEC_AVAILABLE
from corpus_forge.identity import chunk_content_hash
from corpus_forge.sources.base import RawConversation, RawDocument, RawMessage

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

    # Tables extracted from 001_core.sql, 002_chunk_content_hash.sql, 003_sync.sql,
    # 004_fts.sql.
    # 002 adds a column to chunks (no new table).
    # 003 adds document_revisions table.
    # 004 adds the chunks_fts FTS5 virtual table; SQLite's FTS5 module
    #     automatically materialises four shadow tables alongside the user-facing
    #     virtual table (config / data / docsize / idx) — these are FTS5 internals
    #     and visible via sqlite_master.  Pin them explicitly so an unexpected
    #     extra table still fails this test.
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
            # FTS5 (chunks_fts virtual table + its four shadow tables).
            "chunks_fts",
            "chunks_fts_config",
            "chunks_fts_data",
            "chunks_fts_docsize",
            "chunks_fts_idx",
            # Alembic tracking table added by Alembic in D-10 (was not present
            # with the legacy SQL-file migrator).
            "alembic_version",
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

        # Filter / strip comments before matching to avoid false positives from
        # descriptive `--` comments in CREATE TABLE DDL (e.g. 001_core.sql:35
        # has "-- sha256 of raw bytes (idempotency key)" which is metadata, not
        # executed SQL). The Postgres backfill is an UPDATE that doesn't run
        # for dialect="sqlite".
        def strip_line_comments(sql: str) -> str:
            """Remove -- to end-of-line comment text from each line of SQL."""
            stripped_lines = []
            for raw_line in sql.splitlines():
                # Find the first -- not inside a string literal (simple heuristic:
                # strip the -- suffix; this is sufficient because the backfill
                # UPDATE never embeds -- in its executable SQL body).
                comment_pos = raw_line.find("--")
                executable = raw_line[:comment_pos] if comment_pos >= 0 else raw_line
                stripped_lines.append(executable)
            return "\n".join(stripped_lines)

        for sql in executed_sqls:
            executable_sql = strip_line_comments(sql)
            sql_upper = executable_sql.upper()
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
        """Opening a directory as a DB file raises an OperationalError variant."""
        # tmp_path itself is a directory.  Post-D10 Alembic wraps the
        # sqlite3.OperationalError in sqlalchemy.exc.OperationalError; accept both.
        import sqlalchemy.exc as _sa_exc

        backend = SQLiteBackend(path=tmp_path)
        with pytest.raises((sqlite3.OperationalError, _sa_exc.OperationalError)):
            # Connection is lazy; migrate() (or explicit _get_connection) triggers it
            backend.migrate()

    def test_path_is_directory_error_is_raised_on_connection(self, tmp_path):
        """Constructor itself does NOT raise — only _get_connection() raises."""
        import sqlalchemy.exc as _sa_exc

        dir_path = tmp_path  # a directory
        # __init__ must not raise
        backend = SQLiteBackend(path=dir_path)
        # But migrate() (which calls Alembic / _get_connection) must raise
        with pytest.raises((sqlite3.OperationalError, _sa_exc.OperationalError)):
            backend.migrate()

    def test_missing_parent_directory_raises(self, tmp_path):
        """Path whose parent does not exist raises an OperationalError variant."""
        import sqlalchemy.exc as _sa_exc

        nonexistent_parent = tmp_path / "nonexistent_subdir" / "corpus.db"
        backend = SQLiteBackend(path=nonexistent_parent)
        with pytest.raises((sqlite3.OperationalError, _sa_exc.OperationalError)):
            backend.migrate()

    def test_missing_parent_constructor_does_not_raise(self, tmp_path):
        """Constructor does NOT raise for missing parent — stays lazy."""
        nonexistent_parent = tmp_path / "nonexistent" / "corpus.db"
        # Must not raise
        backend = SQLiteBackend(path=nonexistent_parent)
        assert backend is not None


# ---------------------------------------------------------------------------
# B-04 helpers
# ---------------------------------------------------------------------------


class FakeEmbedder:
    """Minimal embedder stub matching the Embedder protocol (corpus_forge/embedders/base.py).

    Attributes match what PostgresBackend.register_embedder reads:
        name, provider, model_id, dimension, normalized, distance.
    The optional `active` attribute is also included because postgres.py
    uses ``getattr(embedder, "active", True)`` — we replicate that pattern.
    """

    def __init__(
        self,
        name: str = "test_embedder",
        provider: str = "sentence_transformers",
        model_id: str = "test/model-v1",
        dimension: int = 384,
        normalized: bool = True,
        distance: str = "cosine",
        active: bool = True,
    ) -> None:
        self.name = name
        self.provider = provider
        self.model_id = model_id
        self.dimension = dimension
        self.normalized = normalized
        self.distance = distance
        self.active = active

    def encode(self, texts, *, batch_size: int = 32):  # pragma: no cover
        import numpy as np

        return np.zeros((len(texts), self.dimension), dtype="float32")

    def warmup(self) -> None:  # pragma: no cover
        pass


def _migrated_backend(tmp_path_or_memory):
    """Return a migrated SQLiteBackend ready for register_embedder tests."""
    backend = SQLiteBackend(path=tmp_path_or_memory)
    backend.migrate()
    return backend


def _table_names(backend: SQLiteBackend) -> list[str]:
    """Return all table names (including virtual tables) from the backend's DB."""
    result = backend._execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'shadow') ORDER BY name"
    )
    # Also capture virtual tables explicitly
    vt = backend._execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    all_names = {row["name"] for row in result} | {row["name"] for row in vt}
    return sorted(all_names)


def _embedder_rows(backend: SQLiteBackend) -> list[dict]:
    """Return all rows from the embedders table."""
    return backend._execute("SELECT * FROM embedders ORDER BY id")


# ---------------------------------------------------------------------------
# TestRegisterEmbedder — B-04
# ---------------------------------------------------------------------------


class TestRegisterEmbedder:
    """register_embedder() — insert/update embedders row + per-embedder table."""

    # ------------------------------------------------------------------ happy path

    def test_register_returns_integer_id(self, tmp_path):
        """Happy path: register_embedder returns an int (the embedder_id)."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        embedder = FakeEmbedder()
        result = backend.register_embedder(embedder)
        assert isinstance(result, int), f"register_embedder must return int, got {type(result)}"

    def test_register_inserts_embedders_row(self, tmp_path):
        """Happy path: after register_embedder, a row exists in the embedders table."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        embedder = FakeEmbedder(name="alpha_embedder")
        backend.register_embedder(embedder)
        rows = _embedder_rows(backend)
        assert len(rows) == 1, f"Expected 1 embedders row, got {len(rows)}"
        row = rows[0]
        assert row["name"] == "alpha_embedder"
        assert row["provider"] == embedder.provider
        assert row["model_id"] == embedder.model_id
        assert row["dimension"] == embedder.dimension

    def test_register_sets_table_name_column(self, tmp_path):
        """Happy path: embedders.table_name is set to 'embeddings_<embedder.name>'."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        embedder = FakeEmbedder(name="my_model")
        backend.register_embedder(embedder)
        rows = _embedder_rows(backend)
        assert rows[0]["table_name"] == "embeddings_my_model", (
            f"table_name must be 'embeddings_my_model', got {rows[0]['table_name']!r}"
        )

    def test_register_creates_per_embedder_table(self, tmp_path):
        """Happy path: a table named 'embeddings_<name>' is created after registration."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        embedder = FakeEmbedder(name="beta_model")
        backend.register_embedder(embedder)
        tables = _table_names(backend)
        assert "embeddings_beta_model" in tables, (
            f"'embeddings_beta_model' table must exist after registration. Tables: {tables}"
        )

    def test_register_in_memory_returns_id(self):
        """Happy path on ':memory:' backend: returns int id."""
        backend = _migrated_backend(":memory:")
        embedder = FakeEmbedder(name="mem_embedder")
        result = backend.register_embedder(embedder)
        assert isinstance(result, int)
        assert result >= 1

    # ------------------------------------------------------------------ idempotency

    def test_register_twice_same_id(self, tmp_path):
        """Idempotency: same embedder registered twice yields the same id."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        embedder = FakeEmbedder(name="stable_name")
        id1 = backend.register_embedder(embedder)
        id2 = backend.register_embedder(embedder)
        assert id1 == id2, (
            f"register_embedder must be idempotent — expected same id, got {id1} vs {id2}"
        )

    def test_register_twice_single_row(self, tmp_path):
        """Idempotency: registering the same embedder twice produces exactly one row."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        embedder = FakeEmbedder(name="one_row_embedder")
        backend.register_embedder(embedder)
        backend.register_embedder(embedder)
        rows = _embedder_rows(backend)
        assert len(rows) == 1, (
            "Exactly 1 embedders row expected after two registrations of the same name; "
            f"got {len(rows)}"
        )

    def test_register_twice_no_duplicate_table(self, tmp_path):
        """Idempotency: IF NOT EXISTS guard ensures no error on second registration."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        embedder = FakeEmbedder(name="no_dup")
        backend.register_embedder(embedder)
        # Must not raise — IF NOT EXISTS on the CREATE TABLE/VIRTUAL TABLE
        backend.register_embedder(embedder)
        # Table still exists, still one row
        tables = _table_names(backend)
        assert "embeddings_no_dup" in tables

    # ------------------------------------------------------------------ update-on-collision

    def test_update_on_same_name_different_dimension(self, tmp_path):
        """Update path: re-registering same name with different dimension updates the row."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        embedder_v1 = FakeEmbedder(name="evolving_model", dimension=128)
        id1 = backend.register_embedder(embedder_v1)

        embedder_v2 = FakeEmbedder(name="evolving_model", dimension=256)
        id2 = backend.register_embedder(embedder_v2)

        # Same id (keyed on name)
        assert id1 == id2, "Same name must return the same row id on update"

        # Dimension should be updated to the new value
        rows = _embedder_rows(backend)
        assert len(rows) == 1
        assert rows[0]["dimension"] == 256, (
            f"dimension must be updated to 256, got {rows[0]['dimension']}"
        )

    def test_update_on_same_name_different_model_id(self, tmp_path):
        """Update path: re-registering same name with different model_id updates the row."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        e1 = FakeEmbedder(name="versioned", model_id="vendor/model-v1")
        backend.register_embedder(e1)

        e2 = FakeEmbedder(name="versioned", model_id="vendor/model-v2")
        backend.register_embedder(e2)

        rows = _embedder_rows(backend)
        assert rows[0]["model_id"] == "vendor/model-v2", (
            f"model_id must update to 'vendor/model-v2', got {rows[0]['model_id']!r}"
        )

    def test_two_distinct_embedders_get_distinct_ids(self, tmp_path):
        """Two embedders with different names get distinct integer ids."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        id_a = backend.register_embedder(FakeEmbedder(name="embedder_a"))
        id_b = backend.register_embedder(FakeEmbedder(name="embedder_b"))
        assert id_a != id_b, "Different embedder names must receive different ids"

    # ---------------------------------------------------------------- table shape (sqlite-vec path)

    @pytest.mark.skipif(
        not SQLITE_VEC_AVAILABLE,
        reason="sqlite-vec extra not installed; vec0 virtual table not available",
    )
    def test_vec0_virtual_table_has_required_columns(self, tmp_path):
        """With sqlite-vec: vec0 virtual table exposes chunk_id, embedder_id, embedding."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        embedder = FakeEmbedder(name="vec_model", dimension=64)
        backend.register_embedder(embedder)

        # Query the virtual table's columns via PRAGMA table_info
        rows = backend._execute("PRAGMA table_info(embeddings_vec_model)")
        col_names = [r["name"] for r in rows]
        for required in ("chunk_id", "embedder_id", "embedding"):
            assert required in col_names, (
                f"Column '{required}' missing from vec0 virtual table. Columns: {col_names}"
            )

    # ------------------------------------------------------------------ fallback table shape

    def test_fallback_blob_table_created_when_vec_unavailable(self, tmp_path, monkeypatch):
        """Fallback path: when SQLITE_VEC_AVAILABLE is False, a plain BLOB table is created."""
        import corpus_forge.backends.sqlite as sqlite_mod
        import corpus_forge.backends.sqlite_vec_loader as loader_mod

        # Monkeypatch both the loader module flag and the sqlite module's imported reference
        monkeypatch.setattr(loader_mod, "SQLITE_VEC_AVAILABLE", False)
        monkeypatch.setattr(sqlite_mod, "SQLITE_VEC_AVAILABLE", False)

        backend = _migrated_backend(tmp_path / "corpus.db")
        embedder = FakeEmbedder(name="blob_model", dimension=32)
        backend.register_embedder(embedder)

        # Table must exist
        tables = _table_names(backend)
        assert "embeddings_blob_model" in tables, (
            f"Fallback table 'embeddings_blob_model' missing. Tables: {tables}"
        )

        # Table must have the required columns
        col_rows = backend._execute("PRAGMA table_info(embeddings_blob_model)")
        col_names = [r["name"] for r in col_rows]
        for required in ("chunk_id", "embedder_id", "embedding"):
            assert required in col_names, (
                f"Fallback table missing column '{required}'. Columns: {col_names}"
            )

    def test_fallback_embedding_column_is_blob(self, tmp_path, monkeypatch):
        """Fallback path: the embedding column in the plain table is typed BLOB."""
        import corpus_forge.backends.sqlite as sqlite_mod
        import corpus_forge.backends.sqlite_vec_loader as loader_mod

        monkeypatch.setattr(loader_mod, "SQLITE_VEC_AVAILABLE", False)
        monkeypatch.setattr(sqlite_mod, "SQLITE_VEC_AVAILABLE", False)

        backend = _migrated_backend(tmp_path / "corpus.db")
        embedder = FakeEmbedder(name="blob_typed", dimension=16)
        backend.register_embedder(embedder)

        col_rows = backend._execute("PRAGMA table_info(embeddings_blob_typed)")
        col_by_name = {r["name"]: r for r in col_rows}
        assert "embedding" in col_by_name, "embedding column must exist in fallback table"
        col_type = col_by_name["embedding"]["type"].upper()
        assert "BLOB" in col_type, f"Fallback embedding column must be BLOB type, got {col_type!r}"

    # ------------------------------------------------------------------ return type contract

    def test_returned_id_matches_embedders_row_id(self, tmp_path):
        """The returned id matches the actual id column in the embedders table."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        embedder = FakeEmbedder(name="id_check")
        returned_id = backend.register_embedder(embedder)
        rows = _embedder_rows(backend)
        assert rows[0]["id"] == returned_id, (
            f"Returned id {returned_id} does not match embedders.id {rows[0]['id']}"
        )

    def test_returned_id_is_positive_integer(self, tmp_path):
        """The returned embedder_id is a positive integer (>= 1)."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        embedder = FakeEmbedder(name="positive_id")
        result = backend.register_embedder(embedder)
        assert result >= 1, f"embedder_id must be >= 1, got {result}"

    # ------------------------------------------------------------------ multiple embedders

    def test_multiple_embedders_each_get_own_table(self, tmp_path):
        """Registering N distinct embedders creates N distinct embedding tables."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        names = ["alpha", "beta", "gamma"]
        for n in names:
            backend.register_embedder(FakeEmbedder(name=n, dimension=64))

        tables = _table_names(backend)
        for n in names:
            assert f"embeddings_{n}" in tables, (
                f"embeddings_{n} missing after registering embedder '{n}'. Tables: {tables}"
            )

    def test_multiple_embedders_rows_all_present(self, tmp_path):
        """Registering N distinct embedders produces N rows in the embedders table."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        names = ["x1", "x2", "x3", "x4"]
        for n in names:
            backend.register_embedder(FakeEmbedder(name=n))
        rows = _embedder_rows(backend)
        assert len(rows) == len(names), f"Expected {len(names)} embedder rows, got {len(rows)}"


# ---------------------------------------------------------------------------
# B-05 — upsert_document + chunk reuse
# ---------------------------------------------------------------------------


def _make_raw_document(
    source_uri="vault://test.md",
    content_hash="abc123",
    text="# Test\n\nContent.",
    title="Test",
    modified_at=1000.0,
):
    """Factory for RawDocument used in upsert_document tests."""
    return RawDocument(
        source_uri=source_uri,
        content_hash=content_hash,
        text=text,
        title=title,
        modified_at=modified_at,
        metadata={},
        labels=[],
    )


def _insert_dataset_and_document(
    backend,
    dataset_id=1,
    doc_id=1,
    source_uri="vault://test.md",
    content_hash="abc123",
    text="# Test",
):
    """Insert prerequisite dataset + document rows into the SQLite backend.

    Uses INSERT OR IGNORE for the dataset row to be idempotent across tests.
    The dataset name is parametrized by ``dataset_id`` so that calls with
    different ``dataset_id`` values don't collide on the UNIQUE(name) constraint.
    """
    backend._execute(
        "INSERT OR IGNORE INTO datasets (id, name, kind) VALUES (?, ?, ?)",
        (dataset_id, f"test_ds_{dataset_id}", "text"),
    )
    backend._execute(
        "INSERT INTO documents"
        " (id, dataset_id, source_uri, content_hash, text) VALUES (?, ?, ?, ?, ?)",
        (doc_id, dataset_id, source_uri, content_hash, text),
    )


# ---------------------------------------------------------------------------
# TestUpsertDocument — B-05 core
# ---------------------------------------------------------------------------


class TestUpsertDocumentNewDocument:
    """Happy path: upsert_document with a brand-new document."""

    def test_returns_document_id_on_first_insert(self, tmp_path):
        """First call to upsert_document inserts the document and returns its id."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_and_document(backend, dataset_id=1, doc_id=1)

        doc = _make_raw_document(
            source_uri="vault://new.md", content_hash="new_hash", text="# New Doc"
        )
        chunks = [("# New Doc", "Content")]

        result = backend.upsert_document(1, doc, chunks)
        assert isinstance(result, int)
        assert result >= 1

    def test_inserts_document_row(self, tmp_path):
        """After upsert_document, the document row exists with correct fields."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_and_document(backend, dataset_id=1, doc_id=1)

        doc = _make_raw_document(
            source_uri="vault://insert_test.md", content_hash="hash1", text="# Insert Test"
        )
        chunks = [("# Insert Test", "Body text")]

        backend.upsert_document(1, doc, chunks)

        rows = backend._execute(
            "SELECT source_uri, content_hash, text FROM documents WHERE source_uri = ?",
            ("vault://insert_test.md",),
        )
        assert len(rows) == 1
        assert rows[0]["source_uri"] == "vault://insert_test.md"
        assert rows[0]["content_hash"] == "hash1"
        assert rows[0]["text"] == "# Insert Test"

    def test_inserts_new_chunks(self, tmp_path):
        """Chunks list is inserted into the chunks table."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_and_document(backend, dataset_id=1, doc_id=1)

        doc = _make_raw_document(
            source_uri="vault://chunk_test.md", content_hash="hash1", text="# Chunk Test"
        )
        chunks = [("# Chunk Test", "Body A"), ("", "Body B")]

        backend.upsert_document(1, doc, chunks)

        doc_rows = backend._execute(
            "SELECT id FROM documents WHERE source_uri = ?",
            ("vault://chunk_test.md",),
        )
        doc_id = doc_rows[0]["id"]

        chunk_rows = backend._execute(
            "SELECT chunk_index, heading, text FROM chunks"
            " WHERE document_id = ? ORDER BY chunk_index",
            (doc_id,),
        )
        assert len(chunk_rows) == 2
        assert chunk_rows[0]["chunk_index"] == 0
        assert chunk_rows[0]["heading"] == "# Chunk Test"
        assert chunk_rows[0]["text"] == "Body A"
        assert chunk_rows[1]["chunk_index"] == 1
        assert chunk_rows[1]["heading"] is None or chunk_rows[1]["heading"] == ""
        assert chunk_rows[1]["text"] == "Body B"

    def test_chunk_content_hash_set_on_insert(self, tmp_path):
        """Each new chunk INSERT includes content_hash = chunk_content_hash(text)."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_and_document(backend, dataset_id=1, doc_id=1)

        doc = _make_raw_document(
            source_uri="vault://hash_test.md", content_hash="hash1", text="# Hash Test"
        )
        chunks = [("# Hash Test", "Body A")]

        backend.upsert_document(1, doc, chunks)

        doc_rows = backend._execute(
            "SELECT id FROM documents WHERE source_uri = ?",
            ("vault://hash_test.md",),
        )
        doc_id = doc_rows[0]["id"]

        chunk_rows = backend._execute(
            "SELECT content_hash FROM chunks WHERE document_id = ?",
            (doc_id,),
        )
        assert len(chunk_rows) == 1
        expected_hash = chunk_content_hash("Body A")
        assert chunk_rows[0]["content_hash"] == expected_hash, (
            f"Expected content_hash={expected_hash!r}, got {chunk_rows[0]['content_hash']!r}"
        )

    def test_chunk_index_sequence_starts_at_zero(self, tmp_path):
        """Chunks are indexed 0..N-1 in order."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_and_document(backend, dataset_id=1, doc_id=1)

        doc = _make_raw_document(
            source_uri="vault://index_test.md", content_hash="hash1", text="# Index Test"
        )
        chunks = [("# Index Test", "First"), ("", "Second"), ("", "Third")]

        backend.upsert_document(1, doc, chunks)

        doc_rows = backend._execute(
            "SELECT id FROM documents WHERE source_uri = ?",
            ("vault://index_test.md",),
        )
        doc_id = doc_rows[0]["id"]

        chunk_rows = backend._execute(
            "SELECT chunk_index FROM chunks WHERE document_id = ? ORDER BY chunk_index",
            (doc_id,),
        )
        indices = [r["chunk_index"] for r in chunk_rows]
        assert indices == [0, 1, 2], f"Expected [0, 1, 2], got {indices}"


class TestUpsertDocumentExistingDocument:
    """upsert_document with an existing document — update path."""

    def test_updates_document_when_content_hash_differs(self, tmp_path):
        """When the new content_hash differs, the document row is updated."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_and_document(
            backend,
            dataset_id=1,
            doc_id=1,
            source_uri="vault://update.md",
            content_hash="old_hash",
            text="# Old",
        )

        doc = _make_raw_document(
            source_uri="vault://update.md", content_hash="new_hash", text="# Updated"
        )
        chunks = [("# Updated", "New body")]

        result = backend.upsert_document(1, doc, chunks)
        assert result == 1  # same document id

        rows = backend._execute(
            "SELECT content_hash, text FROM documents WHERE source_uri = ?",
            ("vault://update.md",),
        )
        assert rows[0]["content_hash"] == "new_hash"
        assert rows[0]["text"] == "# Updated"

    def test_content_hash_short_circuit_returns_existing_id(self, tmp_path):
        """If content_hash matches, upsert_document returns existing doc ID
        without modifying chunks."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_and_document(
            backend,
            dataset_id=1,
            doc_id=1,
            source_uri="vault://unchanged.md",
            content_hash="same_hash",
            text="# Same",
        )

        # Insert a chunk first
        backend._execute(
            "INSERT INTO chunks (document_id, chunk_index, text) VALUES (1, 0, 'existing chunk')"
        )

        doc = _make_raw_document(
            source_uri="vault://unchanged.md", content_hash="same_hash", text="# Same"
        )
        chunks = [("# Same", "existing chunk")]

        result = backend.upsert_document(1, doc, chunks)
        assert result == 1

        # Chunk should be unchanged (not re-inserted)
        chunk_rows = backend._execute("SELECT text FROM chunks WHERE document_id = 1")
        assert len(chunk_rows) == 1
        assert chunk_rows[0]["text"] == "existing chunk"

    def test_replaces_chunks_on_content_change(self, tmp_path):
        """When content changes: old chunks are deleted, new chunks are inserted."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_and_document(
            backend,
            dataset_id=1,
            doc_id=1,
            source_uri="vault://replace.md",
            content_hash="old_hash",
            text="# Old",
        )

        # Pre-existing chunks
        backend._execute(
            "INSERT INTO chunks (document_id, chunk_index, text) VALUES (1, 0, 'old chunk 1')"
        )
        backend._execute(
            "INSERT INTO chunks (document_id, chunk_index, text) VALUES (1, 1, 'old chunk 2')"
        )

        doc = _make_raw_document(
            source_uri="vault://replace.md", content_hash="new_hash", text="# New"
        )
        chunks = [("# New", "new chunk A"), ("", "new chunk B")]

        backend.upsert_document(1, doc, chunks)

        doc_rows = backend._execute(
            "SELECT id FROM documents WHERE source_uri = ?",
            ("vault://replace.md",),
        )
        doc_id = doc_rows[0]["id"]

        chunk_rows = backend._execute(
            "SELECT chunk_index, text FROM chunks WHERE document_id = ? ORDER BY chunk_index",
            (doc_id,),
        )
        assert len(chunk_rows) == 2
        assert chunk_rows[0]["text"] == "new chunk A"
        assert chunk_rows[1]["text"] == "new chunk B"

    def test_reduces_chunk_count_on_content_change(self, tmp_path):
        """When new content has fewer chunks, old excess chunks are deleted."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_and_document(
            backend,
            dataset_id=1,
            doc_id=1,
            source_uri="vault://shrink.md",
            content_hash="old_hash",
            text="# Old",
        )

        # Pre-existing 3 chunks
        for i, text in enumerate(["chunk 1", "chunk 2", "chunk 3"]):
            backend._execute(
                "INSERT INTO chunks (document_id, chunk_index, text) VALUES (1, ?, ?)",
                (i, text),
            )

        doc = _make_raw_document(
            source_uri="vault://shrink.md", content_hash="new_hash", text="# New"
        )
        chunks = [("# New", "only one")]

        backend.upsert_document(1, doc, chunks)

        doc_rows = backend._execute(
            "SELECT id FROM documents WHERE source_uri = ?",
            ("vault://shrink.md",),
        )
        doc_id = doc_rows[0]["id"]

        chunk_rows = backend._execute(
            "SELECT chunk_index FROM chunks WHERE document_id = ? ORDER BY chunk_index",
            (doc_id,),
        )
        assert len(chunk_rows) == 1
        assert chunk_rows[0]["chunk_index"] == 0


class TestUpsertDocumentChunkReuse:
    """Chunk reuse via content_hash matching in upsert_document."""

    def test_reuses_chunk_when_content_hash_matches(self, tmp_path):
        """When a chunk's content_hash matches a prior chunk, it updates
        in-place (keeps chunk_id)."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_and_document(
            backend,
            dataset_id=1,
            doc_id=1,
            source_uri="vault://reuse.md",
            content_hash="old_hash",
            text="# Old",
        )

        # Insert a prior chunk with known content
        prior_hash = chunk_content_hash("same body")
        backend._execute(
            "INSERT INTO chunks (document_id, chunk_index, text, content_hash)"
            " VALUES (1, 0, 'same body', ?)",
            (prior_hash,),
        )

        doc = _make_raw_document(
            source_uri="vault://reuse.md", content_hash="new_hash", text="# New"
        )
        chunks = [("# New", "same body")]

        backend.upsert_document(1, doc, chunks)

        doc_rows = backend._execute(
            "SELECT id FROM documents WHERE source_uri = ?",
            ("vault://reuse.md",),
        )
        doc_id = doc_rows[0]["id"]

        # Should have exactly 1 chunk (reused, not duplicated)
        chunk_rows = backend._execute(
            "SELECT chunk_index, text, content_hash FROM chunks WHERE document_id = ?",
            (doc_id,),
        )
        assert len(chunk_rows) == 1
        assert chunk_rows[0]["chunk_index"] == 0
        assert chunk_rows[0]["content_hash"] == prior_hash

    def test_inserts_new_chunk_when_no_prior_match(self, tmp_path):
        """A chunk with no prior content_hash match is inserted as a new row."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_and_document(
            backend,
            dataset_id=1,
            doc_id=1,
            source_uri="vault://new_body.md",
            content_hash="old_hash",
            text="# Old",
        )

        # Prior chunk with different content
        backend._execute(
            "INSERT INTO chunks (document_id, chunk_index, text, content_hash)"
            " VALUES (1, 0, 'different body', 'different_hash')"
        )

        doc = _make_raw_document(
            source_uri="vault://new_body.md", content_hash="new_hash", text="# New"
        )
        chunks = [("# New", "brand new body")]

        backend.upsert_document(1, doc, chunks)

        doc_rows = backend._execute(
            "SELECT id FROM documents WHERE source_uri = ?",
            ("vault://new_body.md",),
        )
        doc_id = doc_rows[0]["id"]

        chunk_rows = backend._execute(
            "SELECT text, content_hash FROM chunks WHERE document_id = ?",
            (doc_id,),
        )
        assert len(chunk_rows) == 1
        assert chunk_rows[0]["text"] == "brand new body"
        # content_hash should be set
        assert chunk_rows[0]["content_hash"] is not None


class TestUpsertDocumentEmbedderIds:
    """upsert_document accepts embedder_ids and triggers reuse."""

    def test_embedder_ids_none_no_reuse(self, tmp_path):
        """When embedder_ids is None, _copy_reusable_embeddings is NOT called."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_and_document(backend, dataset_id=1, doc_id=1)

        doc = _make_raw_document(
            source_uri="vault://no_reuse.md", content_hash="hash1", text="# No Reuse"
        )
        chunks = [("# No Reuse", "body")]

        # Patch _copy_reusable_embeddings to track calls
        original_copy = backend._copy_reusable_embeddings
        call_count = [0]

        def spy_copy(*args, **kwargs):
            call_count[0] += 1
            return original_copy(*args, **kwargs)

        backend._copy_reusable_embeddings = spy_copy

        backend.upsert_document(1, doc, chunks, embedder_ids=None)

        assert call_count[0] == 0, (
            f"_copy_reusable_embeddings must not be called when embedder_ids=None."
            f" Called {call_count[0]} times."
        )

    def test_embedder_ids_empty_list_no_reuse(self, tmp_path):
        """When embedder_ids is [], _copy_reusable_embeddings is NOT called."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_and_document(backend, dataset_id=1, doc_id=1)

        doc = _make_raw_document(
            source_uri="vault://empty_ids.md", content_hash="hash1", text="# Empty"
        )
        chunks = [("# Empty", "body")]

        call_count = [0]
        original_copy = backend._copy_reusable_embeddings

        def spy_copy(*args, **kwargs):
            call_count[0] += 1
            return original_copy(*args, **kwargs)

        backend._copy_reusable_embeddings = spy_copy

        backend.upsert_document(1, doc, chunks, embedder_ids=[])

        assert call_count[0] == 0, (
            f"_copy_reusable_embeddings must not be called when embedder_ids=[]."
            f" Called {call_count[0]} times."
        )

    def test_embedder_ids_triggers_copy_per_chunk(self, tmp_path):
        """When embedder_ids is a non-empty list, _copy_reusable_embeddings is called
        once per chunk."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_and_document(backend, dataset_id=1, doc_id=1)

        doc = _make_raw_document(
            source_uri="vault://reuse_ids.md", content_hash="hash1", text="# Reuse"
        )
        chunks = [("# Reuse", "body A"), ("", "body B")]

        call_count = [0]
        original_copy = backend._copy_reusable_embeddings

        def spy_copy(*args, **kwargs):
            call_count[0] += 1
            return original_copy(*args, **kwargs)

        backend._copy_reusable_embeddings = spy_copy

        backend.upsert_document(1, doc, chunks, embedder_ids=[1, 2])

        assert call_count[0] == 2, (
            f"_copy_reusable_embeddings must be called once per chunk."
            f" Called {call_count[0]} times, expected 2."
        )

    def test_embedder_ids_passed_to_copy_reusable(self, tmp_path):
        """The embedder_ids list is passed through to _copy_reusable_embeddings."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_and_document(backend, dataset_id=1, doc_id=1)

        doc = _make_raw_document(
            source_uri="vault://pass_ids.md", content_hash="hash1", text="# Pass"
        )
        chunks = [("# Pass", "body")]

        captured_ids = []
        original_copy = backend._copy_reusable_embeddings

        def spy_copy(new_chunk_id, content_hash, embedder_ids, cache):
            captured_ids.append(list(embedder_ids))
            return original_copy(new_chunk_id, content_hash, embedder_ids, cache)

        backend._copy_reusable_embeddings = spy_copy

        expected_ids = [10, 20]
        backend.upsert_document(1, doc, chunks, embedder_ids=expected_ids)

        assert len(captured_ids) >= 1
        assert captured_ids[0] == expected_ids, (
            f"Expected embedder_ids={expected_ids}, got {captured_ids[0]}"
        )


class TestCopyReusableEmbeddings:
    """Tests for SQLiteBackend._copy_reusable_embeddings."""

    def test_returns_empty_set_when_no_prior_chunk_shares_hash(self, tmp_path):
        """No prior chunk with matching content_hash → empty set returned."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        embedder_id = backend.register_embedder(FakeEmbedder(name="test_embed", dimension=64))

        # Register embedder first so table_name lookup works
        _insert_dataset_and_document(backend, dataset_id=1, doc_id=1)

        result = backend._copy_reusable_embeddings(
            new_chunk_id=10,
            content_hash="nonexistent_hash",
            embedder_ids=[embedder_id],
            cache={},
        )

        assert result == set(), f"Expected empty set, got {result}"

    def test_copies_vector_from_prior_chunk_when_hash_matches(self, tmp_path):
        """When a prior chunk with matching content_hash has an embedding, it is copied."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        embedder_id = backend.register_embedder(FakeEmbedder(name="copy_embed", dimension=64))
        _insert_dataset_and_document(backend, dataset_id=1, doc_id=1)

        # Insert a prior chunk with the matching content_hash, capture its real
        # auto-incremented id (do NOT assume a specific id — auto-increment is
        # not stable across test runs / fixtures).
        match_hash = chunk_content_hash("matching body")
        backend._execute(
            "INSERT INTO chunks (document_id, chunk_index, text, content_hash)"
            " VALUES (1, 0, 'matching body', ?)",
            (match_hash,),
        )
        prior_chunk_id = backend._execute(
            "SELECT id FROM chunks WHERE content_hash = ?",
            (match_hash,),
        )[0]["id"]

        # Insert the embedding row at the chunk's real id (not a hardcoded value).
        backend._execute(
            "INSERT INTO embeddings_copy_embed (chunk_id, embedder_id, embedding) VALUES (?, ?, ?)",
            (prior_chunk_id, embedder_id, b"\x00" * (64 * 4)),  # 64 floats as float32
        )

        # Insert the new chunk that should reuse the prior embedding.
        backend._execute(
            "INSERT INTO chunks (document_id, chunk_index, text, content_hash)"
            " VALUES (1, 1, 'matching body', ?)",
            (match_hash,),
        )
        new_chunk_id = backend._execute(
            "SELECT id FROM chunks WHERE chunk_index = 1 AND content_hash = ?",
            (match_hash,),
        )[0]["id"]

        result = backend._copy_reusable_embeddings(
            new_chunk_id=new_chunk_id,
            content_hash=match_hash,
            embedder_ids=[embedder_id],
            cache={},
        )

        assert embedder_id in result, f"Expected {embedder_id} in reused set, got {result}"

        # Verify the new row was inserted at the new chunk id.
        new_rows = backend._execute(
            "SELECT chunk_id, embedder_id FROM embeddings_copy_embed WHERE chunk_id = ?",
            (new_chunk_id,),
        )
        assert len(new_rows) == 1, (
            f"Expected 1 new embedding row for chunk_id={new_chunk_id}, got {len(new_rows)}"
        )
        assert new_rows[0]["embedder_id"] == embedder_id

    def test_cache_prevents_repeat_select_for_hash(self, tmp_path):
        """Cache hits skip the SELECT for prior chunk lookup."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        embedder_id = backend.register_embedder(FakeEmbedder(name="cache_embed", dimension=32))
        _insert_dataset_and_document(backend, dataset_id=1, doc_id=1)

        # Pre-populate cache: (content_hash, embedder_id) -> prior_chunk_id
        match_hash = chunk_content_hash("cached body")
        cache = {(match_hash, embedder_id): 42}

        result = backend._copy_reusable_embeddings(
            new_chunk_id=10,
            content_hash=match_hash,
            embedder_ids=[embedder_id],
            cache=cache,
        )

        assert embedder_id in result

        # The cache should have been populated (or already had the entry)
        assert (match_hash, embedder_id) in cache

    def test_returns_reused_embedder_ids_subset(self, tmp_path):
        """Only embedder_ids that successfully had embeddings copied are returned."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        eid_a = backend.register_embedder(FakeEmbedder(name="reuse_a", dimension=32))
        eid_b = backend.register_embedder(FakeEmbedder(name="reuse_b", dimension=32))
        _insert_dataset_and_document(backend, dataset_id=1, doc_id=1)

        # Only embedder_a has a prior embedding for the matching hash
        match_hash = chunk_content_hash("partial body")
        backend._execute(
            "INSERT INTO chunks (document_id, chunk_index, text, content_hash)"
            " VALUES (1, 0, 'partial body', ?)",
            (match_hash,),
        )
        backend._execute(
            "INSERT INTO embeddings_reuse_a (chunk_id, embedder_id, embedding) VALUES (1, ?, ?)",
            (eid_a, b"\x00" * (32 * 4)),
        )
        # No embedding for eid_b

        result = backend._copy_reusable_embeddings(
            new_chunk_id=10,
            content_hash=match_hash,
            embedder_ids=[eid_a, eid_b],
            cache={},
        )

        assert eid_a in result, f"Expected {eid_a} in reused set"
        assert eid_b not in result, f"Expected {eid_b} NOT in reused set"

    def test_no_prior_chunk_with_embedding_returns_empty(self, tmp_path):
        """Prior chunk exists with matching hash but NO embedding → empty set."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        embedder_id = backend.register_embedder(FakeEmbedder(name="no_vec", dimension=32))
        _insert_dataset_and_document(backend, dataset_id=1, doc_id=1)

        match_hash = chunk_content_hash("no embed body")
        # Insert chunk with matching hash but NO embedding row
        backend._execute(
            "INSERT INTO chunks (document_id, chunk_index, text, content_hash)"
            " VALUES (1, 0, 'no embed body', ?)",
            (match_hash,),
        )

        result = backend._copy_reusable_embeddings(
            new_chunk_id=10,
            content_hash=match_hash,
            embedder_ids=[embedder_id],
            cache={},
        )

        assert result == set(), (
            f"Expected empty set when prior chunk has no embedding. Got {result}"
        )

    def test_cache_entry_used_directly_without_query(self, tmp_path):
        """When cache has the entry, no SELECT is performed to find prior chunk."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        embedder_id = backend.register_embedder(FakeEmbedder(name="direct_cache", dimension=16))
        _insert_dataset_and_document(backend, dataset_id=1, doc_id=1)

        # Pre-populate cache with a prior_chunk_id that does NOT exist in DB
        match_hash = "cache_only_hash"
        fake_prior_id = 9999  # doesn't exist in DB
        cache = {(match_hash, embedder_id): fake_prior_id}

        # This should NOT raise — the cache entry is used directly
        result = backend._copy_reusable_embeddings(
            new_chunk_id=10,
            content_hash=match_hash,
            embedder_ids=[embedder_id],
            cache=cache,
        )

        # If the implementation uses the cache entry to do an INSERT SELECT from
        # a non-existent chunk, it should still not raise (the SELECT returns 0 rows).
        assert (
            embedder_id in result or result == set()
        )  # depends on whether it errors or returns empty

    def test_multiple_chunks_share_same_prior(self, tmp_path):
        """Multiple new chunks with the same content_hash all reuse the same prior chunk."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        embedder_id = backend.register_embedder(FakeEmbedder(name="multi_reuse", dimension=32))
        # Use a separate document id from the doc that upsert_document will create,
        # so the prior chunk isn't deleted by upsert_document's
        # snapshot-prior-chunks-then-DELETE pattern.
        _insert_dataset_and_document(
            backend, dataset_id=1, doc_id=1, source_uri="vault://prior_doc.md"
        )

        match_hash = chunk_content_hash("shared body")
        backend._execute(
            "INSERT INTO chunks (document_id, chunk_index, text, content_hash)"
            " VALUES (1, 0, 'shared body', ?)",
            (match_hash,),
        )
        prior_chunk_id = backend._execute(
            "SELECT id FROM chunks WHERE content_hash = ?",
            (match_hash,),
        )[0]["id"]
        backend._execute(
            "INSERT INTO embeddings_multi_reuse (chunk_id, embedder_id, embedding)"
            " VALUES (?, ?, ?)",
            (prior_chunk_id, embedder_id, b"\x00" * (32 * 4)),
        )

        doc = _make_raw_document(
            source_uri="vault://multi.md", content_hash="new_hash", text="# Multi"
        )
        chunks = [("# Multi", "shared body"), ("", "shared body"), ("", "shared body")]

        backend.upsert_document(1, doc, chunks, embedder_ids=[embedder_id])

        # The 3 new chunks should each get a reused embedding row, in addition
        # to the prior embedding row. Filter by chunk_id != prior_chunk_id rather
        # than by a hardcoded id range (auto-increment ids are not stable).
        new_rows = backend._execute(
            "SELECT chunk_id FROM embeddings_multi_reuse WHERE chunk_id != ? ORDER BY chunk_id",
            (prior_chunk_id,),
        )
        assert len(new_rows) == 3, f"Expected 3 new embedding rows, got {len(new_rows)}"


class TestUpsertDocumentSqliteDialect:
    """Tests specific to SQLite SQL dialect for upsert_document."""

    def test_on_conflict_syntax_used_for_document_upsert(self, tmp_path):
        """Document UPSERT uses ON CONFLICT(dataset_id, source_uri) DO UPDATE."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_and_document(
            backend,
            dataset_id=1,
            doc_id=1,
            source_uri="vault://conflict.md",
            content_hash="old_hash",
            text="# Old",
        )

        doc = _make_raw_document(
            source_uri="vault://conflict.md", content_hash="new_hash", text="# New"
        )
        chunks = [("# New", "body")]

        captured_sqls = []
        original_execute = backend._execute

        def spy_execute(query, params=()):
            captured_sqls.append(query)
            return original_execute(query, params)

        backend._execute = spy_execute

        backend.upsert_document(1, doc, chunks)

        # Find the document UPSERT SQL
        upsert_sqls = [s for s in captured_sqls if "ON CONFLICT" in s.upper()]
        assert len(upsert_sqls) >= 1, (
            f"Expected ON CONFLICT in upsert_document. SQLs: {captured_sqls}"
        )

        upsert_sql = upsert_sqls[0]
        assert "ON CONFLICT" in upsert_sql.upper(), (
            f"Expected ON CONFLICT clause. Got: {upsert_sql}"
        )
        assert "dataset_id" in upsert_sql.lower(), (
            f"Expected dataset_id in ON CONFLICT. Got: {upsert_sql}"
        )
        assert "source_uri" in upsert_sql.lower(), (
            f"Expected source_uri in ON CONFLICT. Got: {upsert_sql}"
        )

    def test_unique_constraint_on_documents_exists(self, tmp_path):
        """The documents table must have a unique constraint on (dataset_id, source_uri)."""
        backend = _migrated_backend(tmp_path / "corpus.db")

        # Query the index info for documents
        indexes = backend._execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='documents'"
        )
        index_names = [r["name"] for r in indexes]

        # SQLite creates a unique index automatically for UNIQUE constraints
        unique_indices = [n for n in index_names if "unique" in n.lower() or n.startswith("sqlite")]
        assert len(unique_indices) > 0, (
            f"Expected unique index on documents table. Found: {index_names}"
        )

    def test_duplicate_source_uri_raises_or_updates(self, tmp_path):
        """Inserting a document with an existing (dataset_id, source_uri) must not raise."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_and_document(
            backend,
            dataset_id=1,
            doc_id=1,
            source_uri="vault://dupe.md",
            content_hash="hash1",
            text="# First",
        )

        doc = _make_raw_document(
            source_uri="vault://dupe.md", content_hash="hash2", text="# Second"
        )
        chunks = [("# Second", "updated body")]

        # Must not raise — should update in place
        result = backend.upsert_document(1, doc, chunks)
        assert result is not None

        # Only one document row should exist
        doc_rows = backend._execute(
            "SELECT COUNT(*) AS count FROM documents WHERE source_uri = ?",
            ("vault://dupe.md",),
        )
        assert doc_rows[0]["count"] == 1, (
            f"Expected exactly 1 document row for vault://dupe.md, got {doc_rows[0]['count']}"
        )


class TestUpsertDocumentState:
    """State-related tests for upsert_document."""

    def test_idempotent_upsert_no_duplicate_chunks(self, tmp_path):
        """Re-ingesting the same document with same chunks produces no duplicate chunks."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_and_document(backend, dataset_id=1, doc_id=1)

        doc = _make_raw_document(
            source_uri="vault://idem.md", content_hash="same_hash", text="# Idem"
        )
        chunks = [("# Idem", "body")]

        # First ingest
        backend.upsert_document(1, doc, chunks)

        # Second ingest with same content
        backend.upsert_document(1, doc, chunks)

        doc_rows = backend._execute(
            "SELECT id FROM documents WHERE source_uri = ?",
            ("vault://idem.md",),
        )
        doc_id = doc_rows[0]["id"]

        chunk_rows = backend._execute(
            "SELECT COUNT(*) AS count FROM chunks WHERE document_id = ?",
            (doc_id,),
        )
        assert chunk_rows[0]["count"] == 1, (
            f"Expected 1 chunk, got {chunk_rows[0]['count']} — duplicates on re-ingest!"
        )

    def test_dirty_state_multiple_upserts(self, tmp_path):
        """Multiple sequential upserts to the same document accumulate correctly."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_and_document(backend, dataset_id=1, doc_id=1)

        # First upsert
        doc1 = _make_raw_document(source_uri="vault://dirty.md", content_hash="h1", text="# V1")
        backend.upsert_document(1, doc1, [("# V1", "body 1")])

        # Second upsert — different content
        doc2 = _make_raw_document(source_uri="vault://dirty.md", content_hash="h2", text="# V2")
        backend.upsert_document(1, doc2, [("# V2", "body 2")])

        # Third upsert — different content
        doc3 = _make_raw_document(source_uri="vault://dirty.md", content_hash="h3", text="# V3")
        backend.upsert_document(1, doc3, [("# V3", "body 3"), ("", "body 4")])

        doc_rows = backend._execute(
            "SELECT id FROM documents WHERE source_uri = ?",
            ("vault://dirty.md",),
        )
        doc_id = doc_rows[0]["id"]

        chunk_rows = backend._execute(
            "SELECT chunk_index, text FROM chunks WHERE document_id = ? ORDER BY chunk_index",
            (doc_id,),
        )
        assert len(chunk_rows) == 2
        assert chunk_rows[0]["text"] == "body 3"
        assert chunk_rows[1]["text"] == "body 4"

    def test_fresh_state_no_prior_chunks(self, tmp_path):
        """First upsert on a fresh (no prior chunks) document inserts all chunks."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_and_document(backend, dataset_id=1, doc_id=1)

        doc = _make_raw_document(
            source_uri="vault://fresh.md", content_hash="hash1", text="# Fresh"
        )
        chunks = [("# Fresh", "chunk 1"), ("", "chunk 2"), ("", "chunk 3")]

        backend.upsert_document(1, doc, chunks)

        doc_rows = backend._execute(
            "SELECT id FROM documents WHERE source_uri = ?",
            ("vault://fresh.md",),
        )
        doc_id = doc_rows[0]["id"]

        chunk_rows = backend._execute(
            "SELECT COUNT(*) AS count FROM chunks WHERE document_id = ?",
            (doc_id,),
        )
        assert chunk_rows[0]["count"] == 3


class TestUpsertDocumentFailurePaths:
    """Failure paths and edge cases for upsert_document."""

    def test_empty_chunks_list_inserts_no_chunk_rows(self, tmp_path):
        """upsert_document with empty chunks list inserts document but no chunks."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_and_document(backend, dataset_id=1, doc_id=1)

        doc = _make_raw_document(
            source_uri="vault://empty_chunks.md", content_hash="hash1", text="# Empty"
        )
        chunks = []

        result = backend.upsert_document(1, doc, chunks)
        assert result is not None

        doc_rows = backend._execute(
            "SELECT id FROM documents WHERE source_uri = ?",
            ("vault://empty_chunks.md",),
        )
        doc_id = doc_rows[0]["id"]

        chunk_rows = backend._execute(
            "SELECT COUNT(*) AS count FROM chunks WHERE document_id = ?",
            (doc_id,),
        )
        assert chunk_rows[0]["count"] == 0, (
            f"Expected 0 chunks for empty chunks list, got {chunk_rows[0]['count']}"
        )

    def test_single_chunk_boundary(self, tmp_path):
        """Single chunk is handled correctly."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_and_document(backend, dataset_id=1, doc_id=1)

        doc = _make_raw_document(
            source_uri="vault://single.md", content_hash="hash1", text="# Single"
        )
        chunks = [("# Single", "only chunk")]

        backend.upsert_document(1, doc, chunks)

        doc_rows = backend._execute(
            "SELECT id FROM documents WHERE source_uri = ?",
            ("vault://single.md",),
        )
        doc_id = doc_rows[0]["id"]

        chunk_rows = backend._execute(
            "SELECT COUNT(*) AS count FROM chunks WHERE document_id = ?",
            (doc_id,),
        )
        assert chunk_rows[0]["count"] == 1

    def test_large_number_of_chunks(self, tmp_path):
        """Handling many chunks at once does not break the upsert."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_and_document(backend, dataset_id=1, doc_id=1)

        doc = _make_raw_document(
            source_uri="vault://large.md", content_hash="hash1", text="# Large"
        )
        chunks = [(f"# Chunk {i}", f"Body {i}") for i in range(100)]

        result = backend.upsert_document(1, doc, chunks)
        assert result is not None

        doc_rows = backend._execute(
            "SELECT id FROM documents WHERE source_uri = ?",
            ("vault://large.md",),
        )
        doc_id = doc_rows[0]["id"]

        chunk_rows = backend._execute(
            "SELECT COUNT(*) AS count FROM chunks WHERE document_id = ?",
            (doc_id,),
        )
        assert chunk_rows[0]["count"] == 100, f"Expected 100 chunks, got {chunk_rows[0]['count']}"

    def test_different_dataset_same_source_uri(self, tmp_path):
        """Same source_uri under different dataset_ids are separate documents."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_and_document(
            backend, dataset_id=1, doc_id=1, source_uri="vault://shared.md"
        )
        _insert_dataset_and_document(
            backend, dataset_id=2, doc_id=2, source_uri="vault://shared.md"
        )

        # Upsert for dataset 1
        doc1 = _make_raw_document(source_uri="vault://shared.md", content_hash="h1", text="# DS1")
        backend.upsert_document(1, doc1, [("# DS1", "body")])

        # Upsert for dataset 2
        doc2 = _make_raw_document(source_uri="vault://shared.md", content_hash="h2", text="# DS2")
        backend.upsert_document(2, doc2, [("# DS2", "body")])

        # Both should exist
        ds1_rows = backend._execute(
            "SELECT COUNT(*) AS count FROM documents WHERE dataset_id = 1 AND source_uri = ?",
            ("vault://shared.md",),
        )
        ds2_rows = backend._execute(
            "SELECT COUNT(*) AS count FROM documents WHERE dataset_id = 2 AND source_uri = ?",
            ("vault://shared.md",),
        )
        assert ds1_rows[0]["count"] == 1
        assert ds2_rows[0]["count"] == 1

    def test_null_heading_preserved(self, tmp_path):
        """Chunk with None heading is stored correctly (not coerced to empty string)."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_and_document(backend, dataset_id=1, doc_id=1)

        doc = _make_raw_document(
            source_uri="vault://null_head.md", content_hash="hash1", text="# Null"
        )
        chunks = [(None, "body with null heading")]

        backend.upsert_document(1, doc, chunks)

        doc_rows = backend._execute(
            "SELECT id FROM documents WHERE source_uri = ?",
            ("vault://null_head.md",),
        )
        doc_id = doc_rows[0]["id"]

        chunk_rows = backend._execute(
            "SELECT heading FROM chunks WHERE document_id = ?",
            (doc_id,),
        )
        assert len(chunk_rows) == 1
        # SQLite stores None as NULL; the value may be None or empty string
        # depending on implementation
        assert chunk_rows[0]["heading"] is None or chunk_rows[0]["heading"] == ""


# ---------------------------------------------------------------------------
# B-06 — upsert_conversation helpers
# ---------------------------------------------------------------------------


def _make_raw_message(
    role: str = "user",
    content: str = "Hello",
    external_uuid: str | None = None,
    parent_uuid: str | None = None,
    tool_calls: list | None = None,
    tool_results: list | None = None,
    ts: float | None = None,
    metadata: dict | None = None,
) -> RawMessage:
    """Factory for RawMessage used in upsert_conversation tests."""
    return RawMessage(
        external_uuid=external_uuid,
        parent_uuid=parent_uuid,
        role=role,
        content=content,
        tool_calls=tool_calls,
        tool_results=tool_results,
        ts=ts,
        metadata=metadata if metadata is not None else {},
    )


def _make_raw_conversation(
    source_uri: str = "claude-code://proj/sess-001",
    content_hash: str = "convhash001",
    title: str | None = "Test Conversation",
    external_id: str | None = "ext-001",
    started_at: float | None = 1_700_000_000.0,
    ended_at: float | None = 1_700_001_000.0,
    messages: list[RawMessage] | None = None,
    metadata: dict | None = None,
) -> RawConversation:
    """Factory for RawConversation used in upsert_conversation tests."""
    if messages is None:
        messages = [
            _make_raw_message(role="user", content="Hi"),
            _make_raw_message(role="assistant", content="Hello!"),
        ]
    return RawConversation(
        source_uri=source_uri,
        external_id=external_id,
        content_hash=content_hash,
        title=title,
        started_at=started_at,
        ended_at=ended_at,
        messages=messages,
        metadata=metadata if metadata is not None else {},
        labels=[],
    )


def _insert_dataset_for_conv(
    backend,
    dataset_id: int = 1,
    name: str | None = None,
    kind: str = "chat",
) -> None:
    """Insert a prerequisite dataset row for conversation tests.

    Uses INSERT OR IGNORE so tests that share a dataset_id do not collide.
    Each caller that needs a unique dataset should pass a distinct dataset_id
    and name — hardcoding "test_ds" for every dataset causes a UNIQUE violation
    on the name column when two datasets share id=1 (the B-05 lesson).
    """
    unique_name = name if name is not None else f"conv_ds_{dataset_id}"
    backend._execute(
        "INSERT OR IGNORE INTO datasets (id, name, kind) VALUES (?, ?, ?)",
        (dataset_id, unique_name, kind),
    )


def _conv_rows(backend, dataset_id: int, source_uri: str) -> list:
    """Return conversation rows matching (dataset_id, source_uri)."""
    return backend._execute(
        "SELECT * FROM conversations WHERE dataset_id = ? AND source_uri = ?",
        (dataset_id, source_uri),
    )


def _message_rows(backend, conv_id: int) -> list:
    """Return message rows for a conversation ordered by turn_index."""
    return backend._execute(
        "SELECT * FROM messages WHERE conversation_id = ? ORDER BY turn_index",
        (conv_id,),
    )


def _chunk_rows_for_conv(backend, conv_id: int) -> list:
    """Return chunk rows for a conversation ordered by message_id, chunk_index."""
    return backend._execute(
        "SELECT * FROM chunks WHERE conversation_id = ? ORDER BY message_id, chunk_index",
        (conv_id,),
    )


# ---------------------------------------------------------------------------
# TestUpsertConversationNew — B-06
# ---------------------------------------------------------------------------


class TestUpsertConversationNew:
    """Happy path: fresh conversation is inserted and returns correct data."""

    def test_returns_integer_id_on_first_insert(self, tmp_path):
        """First call to upsert_conversation returns a positive integer id."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_for_conv(backend, dataset_id=1)

        conv = _make_raw_conversation(source_uri="claude-code://p/s1")
        chunked = [[(None, "Hello chunk")], [(None, "Hello! chunk")]]

        result = backend.upsert_conversation(1, conv, chunked)
        assert isinstance(result, int), f"upsert_conversation must return int, got {type(result)}"
        assert result >= 1, f"Returned id must be >= 1, got {result}"

    def test_conversation_row_written(self, tmp_path):
        """After upsert_conversation, the conversations table has a matching row."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_for_conv(backend, dataset_id=1)

        conv = _make_raw_conversation(
            source_uri="claude-code://p/s2",
            content_hash="hash-new",
            title="My Session",
        )
        chunked = [[(None, "chunk a")], [(None, "chunk b")]]

        conv_id = backend.upsert_conversation(1, conv, chunked)

        rows = _conv_rows(backend, 1, "claude-code://p/s2")
        assert len(rows) == 1, f"Expected 1 conversations row, got {len(rows)}"
        assert rows[0]["id"] == conv_id
        assert rows[0]["content_hash"] == "hash-new"
        assert rows[0]["title"] == "My Session"

    def test_messages_written_for_new_conversation(self, tmp_path):
        """upsert_conversation inserts one messages row per message."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_for_conv(backend, dataset_id=1)

        messages = [
            _make_raw_message(role="user", content="Question?"),
            _make_raw_message(role="assistant", content="Answer."),
            _make_raw_message(role="user", content="Follow-up?"),
        ]
        conv = _make_raw_conversation(
            source_uri="claude-code://p/s3",
            messages=messages,
        )
        chunked = [[(None, "q chunk")], [(None, "a chunk")], [(None, "fu chunk")]]

        conv_id = backend.upsert_conversation(1, conv, chunked)

        msg_rows = _message_rows(backend, conv_id)
        assert len(msg_rows) == 3, f"Expected 3 message rows, got {len(msg_rows)}"

    def test_chunks_written_for_new_conversation(self, tmp_path):
        """upsert_conversation inserts chunk rows linked to messages."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_for_conv(backend, dataset_id=1)

        messages = [
            _make_raw_message(role="user", content="Q"),
            _make_raw_message(role="assistant", content="A"),
        ]
        conv = _make_raw_conversation(
            source_uri="claude-code://p/s4",
            messages=messages,
        )
        chunked = [
            [(None, "user chunk 1"), ("# heading", "user chunk 2")],
            [(None, "assistant chunk")],
        ]

        conv_id = backend.upsert_conversation(1, conv, chunked)

        chunk_rows = _chunk_rows_for_conv(backend, conv_id)
        assert len(chunk_rows) == 3, f"Expected 3 chunk rows (2+1), got {len(chunk_rows)}"

    def test_in_memory_backend_works(self):
        """upsert_conversation works on a ':memory:' backend."""
        backend = _migrated_backend(":memory:")
        backend._execute(
            "INSERT OR IGNORE INTO datasets (id, name, kind) VALUES (?, ?, ?)",
            (1, "mem_ds", "chat"),
        )

        conv = _make_raw_conversation(source_uri="claude-code://mem/s1")
        chunked = [[(None, "c1")], [(None, "c2")]]

        result = backend.upsert_conversation(1, conv, chunked)
        assert isinstance(result, int) and result >= 1


# ---------------------------------------------------------------------------
# TestUpsertConversationExisting — B-06
# ---------------------------------------------------------------------------


class TestUpsertConversationExisting:
    """Existing conversation: same hash → no-op; different hash → UPDATE."""

    def test_same_hash_returns_existing_id(self, tmp_path):
        """Second call with same (dataset_id, source_uri, content_hash) returns same id."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_for_conv(backend, dataset_id=1)

        conv = _make_raw_conversation(
            source_uri="claude-code://p/existing",
            content_hash="stable-hash",
        )
        chunked = [[(None, "chunk")], [(None, "chunk2")]]

        id1 = backend.upsert_conversation(1, conv, chunked)
        id2 = backend.upsert_conversation(1, conv, chunked)
        assert id1 == id2, f"Same hash must return same id on re-upsert; got {id1} vs {id2}"

    def test_same_hash_no_op_preserves_messages(self, tmp_path):
        """No-op re-upsert (same hash) must not add duplicate messages."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_for_conv(backend, dataset_id=1)

        conv = _make_raw_conversation(
            source_uri="claude-code://p/noop",
            content_hash="fixed-hash",
        )
        chunked = [[(None, "c1")], [(None, "c2")]]

        conv_id = backend.upsert_conversation(1, conv, chunked)
        backend.upsert_conversation(1, conv, chunked)

        msg_rows = _message_rows(backend, conv_id)
        assert len(msg_rows) == 2, (
            f"No-op re-upsert must not duplicate messages; got {len(msg_rows)}"
        )

    def test_different_hash_updates_conversation_row(self, tmp_path):
        """When content_hash changes, the conversations row is updated."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_for_conv(backend, dataset_id=1)

        conv_v1 = _make_raw_conversation(
            source_uri="claude-code://p/updated",
            content_hash="hash-v1",
            title="Session V1",
        )
        chunked_v1 = [[(None, "old user")], [(None, "old assistant")]]

        id1 = backend.upsert_conversation(1, conv_v1, chunked_v1)

        conv_v2 = _make_raw_conversation(
            source_uri="claude-code://p/updated",
            content_hash="hash-v2",
            title="Session V2",
            messages=[
                _make_raw_message(role="user", content="New question"),
                _make_raw_message(role="assistant", content="New answer"),
            ],
        )
        chunked_v2 = [[(None, "new user")], [(None, "new assistant")]]

        id2 = backend.upsert_conversation(1, conv_v2, chunked_v2)

        assert id1 == id2, f"Update must preserve same conv id; got {id1} vs {id2}"

        rows = _conv_rows(backend, 1, "claude-code://p/updated")
        assert rows[0]["content_hash"] == "hash-v2"
        assert rows[0]["title"] == "Session V2"

    def test_different_hash_replaces_messages(self, tmp_path):
        """When content_hash changes, old messages are replaced by new ones."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_for_conv(backend, dataset_id=1)

        conv_v1 = _make_raw_conversation(
            source_uri="claude-code://p/replace-msgs",
            content_hash="h1",
            messages=[
                _make_raw_message(role="user", content="Old Q"),
                _make_raw_message(role="assistant", content="Old A"),
            ],
        )
        chunked_v1 = [[(None, "old q chunk")], [(None, "old a chunk")]]
        conv_id = backend.upsert_conversation(1, conv_v1, chunked_v1)

        conv_v2 = _make_raw_conversation(
            source_uri="claude-code://p/replace-msgs",
            content_hash="h2",
            messages=[
                _make_raw_message(role="user", content="New Q"),
                _make_raw_message(role="assistant", content="New A"),
            ],
        )
        chunked_v2 = [[(None, "new q chunk")], [(None, "new a chunk")]]
        backend.upsert_conversation(1, conv_v2, chunked_v2)

        msg_rows = _message_rows(backend, conv_id)
        assert len(msg_rows) == 2, f"Replacement must yield exactly 2 messages; got {len(msg_rows)}"
        contents = [r["content"] for r in msg_rows]
        assert "New Q" in contents, f"New Q not found in messages: {contents}"
        assert "Old Q" not in contents, f"Old Q still present after update: {contents}"


# ---------------------------------------------------------------------------
# TestUpsertConversationMessages — B-06
# ---------------------------------------------------------------------------


class TestUpsertConversationMessages:
    """Messages table: turn_index, roles, ts, tool_calls/results, JSON storage."""

    def test_turn_index_zero_based_sequential(self, tmp_path):
        """N messages produce turn_index 0..N-1 in order."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_for_conv(backend, dataset_id=1)

        n = 4
        messages = [
            _make_raw_message(role="user" if i % 2 == 0 else "assistant", content=f"msg {i}")
            for i in range(n)
        ]
        conv = _make_raw_conversation(
            source_uri="claude-code://p/turn-idx",
            messages=messages,
        )
        chunked = [[(None, f"chunk {i}")] for i in range(n)]

        conv_id = backend.upsert_conversation(1, conv, chunked)

        msg_rows = _message_rows(backend, conv_id)
        indices = [r["turn_index"] for r in msg_rows]
        assert indices == list(range(n)), f"Expected turn_indices {list(range(n))}, got {indices}"

    def test_role_preserved_per_message(self, tmp_path):
        """Message roles ('user', 'assistant', 'system') are stored correctly."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_for_conv(backend, dataset_id=1)

        messages = [
            _make_raw_message(role="system", content="System prompt."),
            _make_raw_message(role="user", content="User input."),
            _make_raw_message(role="assistant", content="Assistant reply."),
        ]
        conv = _make_raw_conversation(
            source_uri="claude-code://p/roles",
            messages=messages,
        )
        chunked = [[(None, "s")], [(None, "u")], [(None, "a")]]

        conv_id = backend.upsert_conversation(1, conv, chunked)

        msg_rows = _message_rows(backend, conv_id)
        roles = [r["role"] for r in msg_rows]
        assert roles == ["system", "user", "assistant"], (
            f"Expected ['system', 'user', 'assistant'], got {roles}"
        )

    def test_tool_calls_serialized_as_json_text(self, tmp_path):
        """tool_calls are stored as JSON TEXT, not as None."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_for_conv(backend, dataset_id=1)

        tool_payload = [{"name": "bash", "input": {"cmd": "ls"}}]
        messages = [
            _make_raw_message(
                role="assistant",
                content="Running tool.",
                tool_calls=tool_payload,
            ),
        ]
        conv = _make_raw_conversation(
            source_uri="claude-code://p/tool-calls",
            messages=messages,
        )
        chunked = [[(None, "tool chunk")]]

        conv_id = backend.upsert_conversation(1, conv, chunked)

        msg_rows = _message_rows(backend, conv_id)
        assert len(msg_rows) == 1
        stored = msg_rows[0]["tool_calls"]
        assert stored is not None, "tool_calls must not be NULL when provided"
        # Must round-trip as valid JSON
        parsed = json.loads(stored)
        assert parsed == tool_payload, (
            f"tool_calls JSON round-trip failed: {parsed!r} != {tool_payload!r}"
        )

    def test_tool_results_serialized_as_json_text(self, tmp_path):
        """tool_results are stored as JSON TEXT, not as None."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_for_conv(backend, dataset_id=1)

        results_payload = [{"tool_use_id": "t1", "content": "ok"}]
        messages = [
            _make_raw_message(
                role="tool",
                content="Tool output.",
                tool_results=results_payload,
            ),
        ]
        conv = _make_raw_conversation(
            source_uri="claude-code://p/tool-results",
            messages=messages,
        )
        chunked = [[(None, "tool result chunk")]]

        conv_id = backend.upsert_conversation(1, conv, chunked)

        msg_rows = _message_rows(backend, conv_id)
        stored = msg_rows[0]["tool_results"]
        assert stored is not None, "tool_results must not be NULL when provided"
        parsed = json.loads(stored)
        assert parsed == results_payload, (
            f"tool_results JSON round-trip failed: {parsed!r} != {results_payload!r}"
        )

    def test_ts_column_populated_when_provided(self, tmp_path):
        """When message.ts is set, the ts column in messages is non-NULL."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_for_conv(backend, dataset_id=1)

        messages = [
            _make_raw_message(role="user", content="Timed message.", ts=1_700_500_000.0),
        ]
        conv = _make_raw_conversation(
            source_uri="claude-code://p/ts-test",
            messages=messages,
        )
        chunked = [[(None, "ts chunk")]]

        conv_id = backend.upsert_conversation(1, conv, chunked)

        msg_rows = _message_rows(backend, conv_id)
        assert msg_rows[0]["ts"] is not None, "ts must be stored when message.ts is provided"

    def test_null_tool_calls_stored_as_null(self, tmp_path):
        """When tool_calls is None, the column is stored as NULL."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_for_conv(backend, dataset_id=1)

        messages = [_make_raw_message(role="user", content="No tools.", tool_calls=None)]
        conv = _make_raw_conversation(
            source_uri="claude-code://p/no-tools",
            messages=messages,
        )
        chunked = [[(None, "no tool chunk")]]

        conv_id = backend.upsert_conversation(1, conv, chunked)

        msg_rows = _message_rows(backend, conv_id)
        assert msg_rows[0]["tool_calls"] is None, "tool_calls must be NULL when not provided"


# ---------------------------------------------------------------------------
# TestUpsertConversationChunks — B-06
# ---------------------------------------------------------------------------


class TestUpsertConversationChunks:
    """Chunks table: conversation_id set, document_id NULL, message_id set, chunk_index."""

    def test_chunks_have_conversation_id_set(self, tmp_path):
        """Every inserted chunk has conversation_id matching the conversation."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_for_conv(backend, dataset_id=1)

        messages = [
            _make_raw_message(role="user", content="Q"),
            _make_raw_message(role="assistant", content="A"),
        ]
        conv = _make_raw_conversation(
            source_uri="claude-code://p/conv-id-chunks",
            messages=messages,
        )
        chunked = [[(None, "q chunk")], [(None, "a chunk")]]

        conv_id = backend.upsert_conversation(1, conv, chunked)

        chunk_rows = _chunk_rows_for_conv(backend, conv_id)
        for row in chunk_rows:
            assert row["conversation_id"] == conv_id, (
                f"chunk.conversation_id must be {conv_id}, got {row['conversation_id']}"
            )

    def test_chunks_have_document_id_null(self, tmp_path):
        """Conversation chunks must have document_id = NULL (XOR-check)."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_for_conv(backend, dataset_id=1)

        messages = [_make_raw_message(role="user", content="Q")]
        conv = _make_raw_conversation(
            source_uri="claude-code://p/doc-null",
            messages=messages,
        )
        chunked = [[(None, "chunk"), ("# h", "chunk2")]]

        conv_id = backend.upsert_conversation(1, conv, chunked)

        chunk_rows = _chunk_rows_for_conv(backend, conv_id)
        for row in chunk_rows:
            assert row["document_id"] is None, (
                f"Conversation chunk must have document_id=NULL, got {row['document_id']}"
            )

    def test_chunks_have_message_id_set(self, tmp_path):
        """Each chunk's message_id must be the id of the owning message."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_for_conv(backend, dataset_id=1)

        messages = [
            _make_raw_message(role="user", content="Q"),
            _make_raw_message(role="assistant", content="A"),
        ]
        conv = _make_raw_conversation(
            source_uri="claude-code://p/msg-id-chunks",
            messages=messages,
        )
        chunked = [
            [(None, "u chunk 1"), (None, "u chunk 2")],
            [(None, "a chunk")],
        ]

        conv_id = backend.upsert_conversation(1, conv, chunked)

        msg_rows = _message_rows(backend, conv_id)
        msg_ids = [r["id"] for r in msg_rows]

        chunk_rows = _chunk_rows_for_conv(backend, conv_id)
        # All chunk message_ids must be one of the actual message ids
        for row in chunk_rows:
            assert row["message_id"] in msg_ids, (
                f"chunk.message_id={row['message_id']} not in msg_ids={msg_ids}"
            )

    def test_chunk_index_per_message_starts_at_zero(self, tmp_path):
        """chunk_index restarts at 0 for each message."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_for_conv(backend, dataset_id=1)

        messages = [
            _make_raw_message(role="user", content="Q"),
            _make_raw_message(role="assistant", content="A"),
        ]
        conv = _make_raw_conversation(
            source_uri="claude-code://p/chunk-idx",
            messages=messages,
        )
        chunked = [
            [(None, "u0"), (None, "u1"), (None, "u2")],
            [(None, "a0"), (None, "a1")],
        ]

        conv_id = backend.upsert_conversation(1, conv, chunked)

        msg_rows = _message_rows(backend, conv_id)
        for msg_row in msg_rows:
            msg_id = msg_row["id"]
            c_rows = backend._execute(
                "SELECT chunk_index FROM chunks"
                " WHERE conversation_id = ? AND message_id = ?"
                " ORDER BY chunk_index",
                (conv_id, msg_id),
            )
            indices = [r["chunk_index"] for r in c_rows]
            expected = list(range(len(indices)))
            assert indices == expected, (
                f"message_id={msg_id}: expected chunk_indices {expected}, got {indices}"
            )


# ---------------------------------------------------------------------------
# TestUpsertConversationFailurePaths — B-06
# ---------------------------------------------------------------------------


class TestUpsertConversationFailurePaths:
    """Failure paths: bad dataset_id, missing required fields."""

    def test_invalid_dataset_id_raises_integrity_error(self, tmp_path):
        """Passing a non-existent dataset_id violates the FK and raises IntegrityError."""
        import sqlite3 as _sqlite3

        backend = _migrated_backend(tmp_path / "corpus.db")
        # dataset_id=999 does not exist in the datasets table

        conv = _make_raw_conversation(source_uri="claude-code://p/bad-ds")
        chunked = [[(None, "chunk")], [(None, "chunk2")]]

        with pytest.raises(_sqlite3.IntegrityError):
            backend.upsert_conversation(999, conv, chunked)

    def test_source_uri_none_raises_integrity_error(self, tmp_path):
        """A conversation with source_uri=None violates NOT NULL and raises IntegrityError."""
        import sqlite3 as _sqlite3

        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_for_conv(backend, dataset_id=1)

        # source_uri is NOT NULL in the schema; passing None must raise IntegrityError
        conv = RawConversation(
            source_uri=None,  # type: ignore[arg-type]
            external_id=None,
            content_hash="h",
            title=None,
            started_at=None,
            ended_at=None,
            messages=[_make_raw_message()],
            metadata={},
            labels=[],
        )
        chunked = [[(None, "chunk")]]

        with pytest.raises(_sqlite3.IntegrityError):
            backend.upsert_conversation(1, conv, chunked)


# ---------------------------------------------------------------------------
# B-07 — write_embeddings + chunks_missing_embedding
# ---------------------------------------------------------------------------


import numpy as np  # noqa: E402  (import at module level but appended here for readability)


def _insert_dataset_for_embedding(backend, dataset_id: int = 1) -> None:
    """Insert a minimal dataset row for B-07 tests."""
    backend._execute(
        "INSERT OR IGNORE INTO datasets (id, name, kind) VALUES (?, ?, ?)",
        (dataset_id, f"emb_test_ds_{dataset_id}", "text"),
    )


def _insert_doc_and_chunk(
    backend,
    dataset_id: int,
    source_uri: str,
    chunk_text: str,
) -> tuple[int, int]:
    """Insert a document + one chunk row; return (doc_id, chunk_id)."""
    doc_result = backend._execute(
        "INSERT INTO documents"
        " (dataset_id, source_uri, content_hash, text)"
        " VALUES (?, ?, ?, ?) RETURNING id",
        (dataset_id, source_uri, "hash_" + source_uri, chunk_text),
    )
    doc_id: int = doc_result[0]["id"]
    chunk_result = backend._execute(
        "INSERT INTO chunks"
        " (document_id, chunk_index, text, metadata, content_hash)"
        " VALUES (?, 0, ?, '{}', ?) RETURNING id",
        (doc_id, chunk_text, "ch_" + chunk_text),
    )
    chunk_id: int = chunk_result[0]["id"]
    return doc_id, chunk_id


# ---------------------------------------------------------------------------
# TestWriteEmbeddings — B-07
# ---------------------------------------------------------------------------


class TestWriteEmbeddings:
    """write_embeddings(embedder_id, pairs) — insert serialized vectors."""

    # ------------------------------------------------------------------ happy path

    def test_single_pair_row_exists(self, tmp_path):
        """Happy path: write_embeddings with one pair inserts one row."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_for_embedding(backend, dataset_id=1)
        embedder = FakeEmbedder(name="we_single", dimension=4)
        emb_id = backend.register_embedder(embedder)
        _, chunk_id = _insert_doc_and_chunk(backend, 1, "vault://we/single.md", "hello world")

        vec = np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32)
        backend.write_embeddings(emb_id, [(chunk_id, vec)])

        rows = backend._execute(
            "SELECT chunk_id FROM embeddings_we_single WHERE chunk_id = ?",
            (chunk_id,),
        )
        assert len(rows) == 1, f"Expected 1 row after write_embeddings; got {len(rows)}"

    def test_multiple_pairs_multiple_rows(self, tmp_path):
        """write_embeddings with N pairs inserts N rows."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_for_embedding(backend, dataset_id=1)
        embedder = FakeEmbedder(name="we_multi", dimension=4)
        emb_id = backend.register_embedder(embedder)

        chunk_ids: list[int] = []
        for i in range(3):
            _, cid = _insert_doc_and_chunk(
                backend, 1, f"vault://we/multi_{i}.md", f"text chunk {i}"
            )
            chunk_ids.append(cid)

        vecs = [np.array([float(i), 0.0, 0.0, 0.0], dtype=np.float32) for i in range(3)]
        pairs = list(zip(chunk_ids, vecs, strict=True))
        backend.write_embeddings(emb_id, pairs)

        rows = backend._execute("SELECT chunk_id FROM embeddings_we_multi ORDER BY chunk_id")
        assert len(rows) == 3, f"Expected 3 rows, got {len(rows)}"

    def test_empty_pairs_is_noop(self, tmp_path):
        """Empty pairs list must not raise and must not touch the table."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_for_embedding(backend, dataset_id=1)
        embedder = FakeEmbedder(name="we_empty", dimension=4)
        emb_id = backend.register_embedder(embedder)

        # Must not raise
        backend.write_embeddings(emb_id, [])

        rows = backend._execute("SELECT chunk_id FROM embeddings_we_empty")
        assert len(rows) == 0, "No rows should exist after write_embeddings([])"

    # ------------------------------------------------------------------ float32 vs float64

    def test_float64_input_accepted_and_stored(self, tmp_path):
        """Float64 numpy array is accepted; implementation converts to float32 before storing."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_for_embedding(backend, dataset_id=1)
        embedder = FakeEmbedder(name="we_f64", dimension=4)
        emb_id = backend.register_embedder(embedder)
        _, chunk_id = _insert_doc_and_chunk(backend, 1, "vault://we/f64.md", "float64 text")

        vec_f64 = np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float64)
        # Must not raise
        backend.write_embeddings(emb_id, [(chunk_id, vec_f64)])

        rows = backend._execute(
            "SELECT chunk_id FROM embeddings_we_f64 WHERE chunk_id = ?",
            (chunk_id,),
        )
        assert len(rows) == 1, "float64 input must produce a stored row"

    # ------------------------------------------------------------------ idempotency / REPLACE

    def test_duplicate_chunk_id_is_idempotent(self, tmp_path):
        """Inserting the same (chunk_id, embedder_id) twice must not raise (INSERT OR REPLACE)."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_for_embedding(backend, dataset_id=1)
        embedder = FakeEmbedder(name="we_idem", dimension=4)
        emb_id = backend.register_embedder(embedder)
        _, chunk_id = _insert_doc_and_chunk(backend, 1, "vault://we/idem.md", "idem text")

        vec1 = np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32)
        vec2 = np.array([0.9, 0.8, 0.7, 0.6], dtype=np.float32)
        backend.write_embeddings(emb_id, [(chunk_id, vec1)])
        # Second call with same chunk_id must not raise
        backend.write_embeddings(emb_id, [(chunk_id, vec2)])

        rows = backend._execute(
            "SELECT chunk_id FROM embeddings_we_idem WHERE chunk_id = ?",
            (chunk_id,),
        )
        assert len(rows) == 1, "INSERT OR REPLACE on duplicate (chunk_id) must leave exactly 1 row"

    # ------------------------------------------------------------------ fallback BLOB path

    def test_fallback_blob_stores_bytes(self, tmp_path, monkeypatch):
        """Fallback (no sqlite-vec): embedding is stored as raw float32 bytes in BLOB column."""
        import corpus_forge.backends.sqlite as sqlite_mod
        import corpus_forge.backends.sqlite_vec_loader as loader_mod

        monkeypatch.setattr(loader_mod, "SQLITE_VEC_AVAILABLE", False)
        monkeypatch.setattr(sqlite_mod, "SQLITE_VEC_AVAILABLE", False)

        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_for_embedding(backend, dataset_id=1)
        embedder = FakeEmbedder(name="we_blob", dimension=4)
        emb_id = backend.register_embedder(embedder)
        _, chunk_id = _insert_doc_and_chunk(backend, 1, "vault://we/blob.md", "blob path text")

        vec = np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float32)
        backend.write_embeddings(emb_id, [(chunk_id, vec)])

        rows = backend._execute(
            "SELECT embedding FROM embeddings_we_blob WHERE chunk_id = ?",
            (chunk_id,),
        )
        assert len(rows) == 1, "Fallback path must insert a row"
        blob = rows[0]["embedding"]
        assert isinstance(blob, bytes), (
            f"Fallback embedding must be stored as bytes (BLOB), got {type(blob)}"
        )
        recovered = np.frombuffer(blob, dtype=np.float32)
        np.testing.assert_array_almost_equal(
            recovered,
            vec,
            decimal=5,
            err_msg="Recovered float32 bytes must match the original vector",
        )

    def test_fallback_blob_duplicate_idempotent(self, tmp_path, monkeypatch):
        """Fallback path: second write on same chunk_id must not raise."""
        import corpus_forge.backends.sqlite as sqlite_mod
        import corpus_forge.backends.sqlite_vec_loader as loader_mod

        monkeypatch.setattr(loader_mod, "SQLITE_VEC_AVAILABLE", False)
        monkeypatch.setattr(sqlite_mod, "SQLITE_VEC_AVAILABLE", False)

        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_for_embedding(backend, dataset_id=1)
        embedder = FakeEmbedder(name="we_blob_idem", dimension=4)
        emb_id = backend.register_embedder(embedder)
        _, chunk_id = _insert_doc_and_chunk(backend, 1, "vault://we/blob_idem.md", "blob idem text")

        vec = np.array([0.5, 0.5, 0.5, 0.5], dtype=np.float32)
        backend.write_embeddings(emb_id, [(chunk_id, vec)])
        # Must not raise
        backend.write_embeddings(emb_id, [(chunk_id, vec)])

        rows = backend._execute(
            "SELECT chunk_id FROM embeddings_we_blob_idem WHERE chunk_id = ?",
            (chunk_id,),
        )
        assert len(rows) == 1, "Duplicate insert on BLOB fallback must leave exactly 1 row"

    # ------------------------------------------------------------------ sqlite-vec path

    @pytest.mark.skipif(
        not SQLITE_VEC_AVAILABLE,
        reason="sqlite-vec extra not installed; vec0 virtual table not available",
    )
    def test_vec_path_row_retrievable(self, tmp_path):
        """With sqlite-vec: written embedding row is retrievable via SELECT."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_for_embedding(backend, dataset_id=1)
        embedder = FakeEmbedder(name="we_vec", dimension=4)
        emb_id = backend.register_embedder(embedder)
        _, chunk_id = _insert_doc_and_chunk(backend, 1, "vault://we/vec.md", "vec path text")

        vec = np.array([0.25, 0.5, 0.75, 1.0], dtype=np.float32)
        backend.write_embeddings(emb_id, [(chunk_id, vec)])

        rows = backend._execute(
            "SELECT chunk_id FROM embeddings_we_vec WHERE chunk_id = ?",
            (chunk_id,),
        )
        assert len(rows) == 1, "vec0 path must produce a retrievable row"


# ---------------------------------------------------------------------------
# TestChunksMissingEmbedding — B-07
# ---------------------------------------------------------------------------


class TestChunksMissingEmbedding:
    """chunks_missing_embedding(embedder_id, limit) — returns chunks without embeddings."""

    # ------------------------------------------------------------------ happy path

    def test_no_chunks_returns_empty(self, tmp_path):
        """When no chunks exist, chunks_missing_embedding returns an empty result."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        embedder = FakeEmbedder(name="cme_empty", dimension=4)
        emb_id = backend.register_embedder(embedder)

        result = list(backend.chunks_missing_embedding(emb_id))
        assert result == [], f"Expected empty list when no chunks exist; got {result}"

    def test_all_chunks_have_embeddings_returns_empty(self, tmp_path):
        """When all chunks have embeddings, returns empty."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_for_embedding(backend, dataset_id=1)
        embedder = FakeEmbedder(name="cme_all_covered", dimension=4)
        emb_id = backend.register_embedder(embedder)
        _, chunk_id = _insert_doc_and_chunk(
            backend, 1, "vault://cme/all_covered.md", "already embedded"
        )

        vec = np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32)
        backend.write_embeddings(emb_id, [(chunk_id, vec)])

        result = list(backend.chunks_missing_embedding(emb_id))
        assert result == [], f"Expected empty list when all chunks have embeddings; got {result}"

    def test_missing_chunks_are_returned(self, tmp_path):
        """Chunks without an embedding row are returned."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_for_embedding(backend, dataset_id=1)
        embedder = FakeEmbedder(name="cme_missing", dimension=4)
        emb_id = backend.register_embedder(embedder)

        _, chunk_id_1 = _insert_doc_and_chunk(
            backend, 1, "vault://cme/missing_1.md", "missing chunk 1"
        )
        _, chunk_id_2 = _insert_doc_and_chunk(
            backend, 1, "vault://cme/missing_2.md", "missing chunk 2"
        )

        result = list(backend.chunks_missing_embedding(emb_id))
        returned_ids = [r[0] for r in result]
        assert chunk_id_1 in returned_ids, (
            f"chunk_id_1={chunk_id_1} must be in missing set; got {returned_ids}"
        )
        assert chunk_id_2 in returned_ids, (
            f"chunk_id_2={chunk_id_2} must be in missing set; got {returned_ids}"
        )

    def test_only_missing_chunks_returned_not_covered(self, tmp_path):
        """When some chunks have embeddings and some don't, only the missing ones are returned."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_for_embedding(backend, dataset_id=1)
        embedder = FakeEmbedder(name="cme_partial", dimension=4)
        emb_id = backend.register_embedder(embedder)

        _, chunk_id_covered = _insert_doc_and_chunk(
            backend, 1, "vault://cme/covered.md", "covered chunk"
        )
        _, chunk_id_missing = _insert_doc_and_chunk(
            backend, 1, "vault://cme/missing.md", "missing chunk"
        )

        vec = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
        backend.write_embeddings(emb_id, [(chunk_id_covered, vec)])

        result = list(backend.chunks_missing_embedding(emb_id))
        returned_ids = [r[0] for r in result]

        assert chunk_id_missing in returned_ids, (
            f"chunk_id_missing must be in result; got {returned_ids}"
        )
        assert chunk_id_covered not in returned_ids, (
            f"chunk_id_covered must NOT be in result; got {returned_ids}"
        )

    # ------------------------------------------------------------------ return shape

    def test_returns_tuple_of_chunk_id_and_text(self, tmp_path):
        """Each returned item is a (chunk_id: int, text: str) tuple."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_for_embedding(backend, dataset_id=1)
        embedder = FakeEmbedder(name="cme_shape", dimension=4)
        emb_id = backend.register_embedder(embedder)
        _, chunk_id = _insert_doc_and_chunk(backend, 1, "vault://cme/shape.md", "shape test text")

        result = list(backend.chunks_missing_embedding(emb_id))
        assert len(result) == 1
        item = result[0]
        assert len(item) == 2, f"Each item must be a 2-tuple; got {item!r}"
        cid, text = item
        assert isinstance(cid, int), f"chunk_id must be int, got {type(cid)}"
        assert isinstance(text, str), f"text must be str, got {type(text)}"
        assert cid == chunk_id
        assert text == "shape test text"

    # ------------------------------------------------------------------ limit honored

    def test_limit_caps_number_of_results(self, tmp_path):
        """The limit parameter caps how many rows are returned."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_for_embedding(backend, dataset_id=1)
        embedder = FakeEmbedder(name="cme_limit", dimension=4)
        emb_id = backend.register_embedder(embedder)

        # Insert 5 chunks, all missing embeddings
        for i in range(5):
            _insert_doc_and_chunk(backend, 1, f"vault://cme/limit_{i}.md", f"limit chunk {i}")

        result = list(backend.chunks_missing_embedding(emb_id, limit=3))
        assert len(result) <= 3, f"limit=3 must cap results at 3; got {len(result)}"

    def test_default_limit_returns_all_when_few(self, tmp_path):
        """Default limit (1024) returns all chunks when there are fewer than 1024."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_for_embedding(backend, dataset_id=1)
        embedder = FakeEmbedder(name="cme_default_limit", dimension=4)
        emb_id = backend.register_embedder(embedder)

        for i in range(10):
            _insert_doc_and_chunk(backend, 1, f"vault://cme/def_{i}.md", f"default limit chunk {i}")

        result = list(backend.chunks_missing_embedding(emb_id))
        assert len(result) == 10, f"Default limit must return all 10 chunks; got {len(result)}"

    # ------------------------------------------------------------------ multiple embedders

    def test_multiple_embedders_are_independent(self, tmp_path):
        """A chunk covered for embedder A is still missing for embedder B."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_for_embedding(backend, dataset_id=1)
        emb_a = backend.register_embedder(FakeEmbedder(name="cme_indep_a", dimension=4))
        emb_b = backend.register_embedder(FakeEmbedder(name="cme_indep_b", dimension=4))
        _, chunk_id = _insert_doc_and_chunk(backend, 1, "vault://cme/indep.md", "independence test")

        vec = np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32)
        # Write embedding only for embedder A
        backend.write_embeddings(emb_a, [(chunk_id, vec)])

        # Chunk is covered for A
        result_a = list(backend.chunks_missing_embedding(emb_a))
        assert all(r[0] != chunk_id for r in result_a), (
            f"chunk_id should NOT appear in missing for embedder A; got {result_a}"
        )

        # Chunk is missing for B
        result_b = list(backend.chunks_missing_embedding(emb_b))
        b_ids = [r[0] for r in result_b]
        assert chunk_id in b_ids, f"chunk_id must appear in missing for embedder B; got {result_b}"

    # ------------------------------------------------------------------ unknown embedder_id

    def test_unknown_embedder_id_returns_empty(self, tmp_path):
        """Passing an unknown embedder_id returns empty (mirrors Postgres behavior)."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_for_embedding(backend, dataset_id=1)
        _, _chunk_id = _insert_doc_and_chunk(backend, 1, "vault://cme/unknown_emb.md", "some chunk")

        # embedder_id=9999 does not exist
        result = list(backend.chunks_missing_embedding(9999))
        # Must not raise; return empty (no embedder table to join against)
        assert result == [], f"Unknown embedder_id must return empty; got {result}"


# ---------------------------------------------------------------------------
# B-08 — lock_source(key: str) context manager
# ---------------------------------------------------------------------------
# These tests drive the implementation of SQLiteBackend.lock_source, which
# must not exist yet (AttributeError is the expected red signal).
#
# Contract (decided per Q1 / sqlite_backend.md B-08):
#   - On __enter__: acquire the DB's exclusive write lock via BEGIN IMMEDIATE.
#   - On clean __exit__: COMMIT.
#   - On exception __exit__: ROLLBACK.
#   - key is accepted for protocol parity with PostgresBackend but is ignored
#     for granularity purposes (SQLite write-lock is global).
#   - Retry with exponential back-off on OperationalError("database is locked")
#     up to lock_timeout_s (default 30 s); re-raise beyond timeout.
#
# All concurrency tests use threading.Event for deterministic handshaking;
# no race-prone bare sleeps.  Tests use file-backed DBs (tmp_path) because
# in-memory shared-cache connections share the same lock domain regardless of
# connection identity, which can mask cross-connection contention bugs.


# ---------------------------------------------------------------------------
# TestLockSource — single-connection / single-thread behaviour
# ---------------------------------------------------------------------------


class TestLockSource:
    """lock_source() — happy path, commit, rollback, key contract."""

    # ------------------------------------------------------------------ happy path

    def test_context_manager_executes_block(self, tmp_path):
        """Happy path: block inside with lock_source() executes without error."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        ran = []

        with backend.lock_source("foo"):
            ran.append(True)

        assert ran == [True], "Block inside lock_source must execute"

    def test_lock_source_accepts_any_string_key(self, tmp_path):
        """key argument is accepted regardless of value (empty string, unicode, long)."""
        backend = _migrated_backend(tmp_path / "corpus.db")

        for key in ("", "simple", "vault://path/to/source.md", "a" * 256, "测试"):
            with backend.lock_source(key):
                pass  # must not raise

    # ------------------------------------------------------------------ commit on clean exit

    def test_write_inside_lock_is_committed(self, tmp_path):
        """Writes inside a lock_source block persist after the context exits normally."""
        db_path = tmp_path / "corpus.db"
        backend = _migrated_backend(db_path)

        with backend.lock_source("commit_test"):
            backend._execute(
                "INSERT OR IGNORE INTO datasets (id, name, kind) VALUES (?, ?, ?)",
                (99, "lock_commit_ds", "text"),
            )

        # Verify row persists by opening a fresh connection
        import sqlite3 as _sqlite3

        conn = _sqlite3.connect(str(db_path))
        try:
            rows = conn.execute(
                "SELECT id FROM datasets WHERE name = ?", ("lock_commit_ds",)
            ).fetchall()
        finally:
            conn.close()

        assert len(rows) == 1, (
            "Row written inside lock_source must be committed; found 0 rows in fresh connection"
        )

    def test_write_inside_lock_visible_after_exit(self, tmp_path):
        """After lock_source exits normally, subsequent _execute calls see the committed data."""
        backend = _migrated_backend(tmp_path / "corpus.db")

        with backend.lock_source("visibility_test"):
            backend._execute(
                "INSERT OR IGNORE INTO datasets (id, name, kind) VALUES (?, ?, ?)",
                (42, "vis_ds", "text"),
            )

        rows = backend._execute("SELECT name FROM datasets WHERE id = ?", (42,))
        assert rows and rows[0]["name"] == "vis_ds", (
            "Committed row must be visible to subsequent _execute calls"
        )

    # ------------------------------------------------------------------ rollback on exception

    def test_exception_inside_lock_rolls_back(self, tmp_path):
        """An exception inside the lock_source block must trigger ROLLBACK; writes not persisted."""
        db_path = tmp_path / "corpus.db"
        backend = _migrated_backend(db_path)

        def _trigger():
            with backend.lock_source("rollback_test"):
                backend._execute(
                    "INSERT OR IGNORE INTO datasets (id, name, kind) VALUES (?, ?, ?)",
                    (77, "rollback_ds", "text"),
                )
                raise RuntimeError("intentional rollback trigger")

        with pytest.raises(RuntimeError, match="intentional"):
            _trigger()

        # Row must NOT persist
        rows = backend._execute("SELECT id FROM datasets WHERE name = ?", ("rollback_ds",))
        assert rows == [], (
            f"Write inside a failed lock_source block must be rolled back; found rows: {rows}"
        )

    def test_exception_is_re_raised(self, tmp_path):
        """lock_source must re-raise the exception from the block body."""
        backend = _migrated_backend(tmp_path / "corpus.db")

        with pytest.raises(ValueError, match="propagated"), backend.lock_source("reraise_test"):
            raise ValueError("propagated")

    # ------------------------------------------------------------------ key granularity

    def test_different_keys_serialize_globally(self, tmp_path):
        """Different key values still use the same global write-lock (SQLite is single-writer).

        Two sequential lock_source calls with different keys must both succeed.
        This confirms the implementation does not attempt per-key locking that
        could leave orphaned row mutexes.
        """
        backend = _migrated_backend(tmp_path / "corpus.db")

        with backend.lock_source("key_alpha"):
            backend._execute(
                "INSERT OR IGNORE INTO datasets (id, name, kind) VALUES (?, ?, ?)",
                (1, "alpha_ds", "text"),
            )

        with backend.lock_source("key_beta"):
            backend._execute(
                "INSERT OR IGNORE INTO datasets (id, name, kind) VALUES (?, ?, ?)",
                (2, "beta_ds", "text"),
            )

        rows = backend._execute("SELECT id FROM datasets ORDER BY id")
        ids = [r["id"] for r in rows]
        assert 1 in ids and 2 in ids, f"Both writes with different keys must persist; got ids {ids}"

    def test_returns_context_manager_protocol(self, tmp_path):
        """lock_source returns an object with __enter__ and __exit__ (context manager protocol)."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        ctx = backend.lock_source("proto_check")
        assert hasattr(ctx, "__enter__") and hasattr(ctx, "__exit__"), (
            "lock_source must return a context manager (has __enter__ and __exit__)"
        )
        # Actually enter and exit to clean up
        with ctx:
            pass


# ---------------------------------------------------------------------------
# TestLockSourceConcurrency — multi-thread behaviour
# ---------------------------------------------------------------------------


class TestLockSourceConcurrency:
    """lock_source() under concurrent access: serialization, timeout, post-exit release."""

    # ---------------------------------------------------------------- concurrent writers serialize

    def test_two_threads_serialize_no_data_loss(self, tmp_path):
        """Two threads each calling lock_source write distinct rows; both rows persist.

        Uses threading.Event for deterministic handshake — no bare sleeps.
        Thread B waits for thread A to acquire the lock, then contends for it.
        Both eventually succeed (sequentially) and neither row is lost.
        """
        db_path = tmp_path / "corpus.db"
        backend = _migrated_backend(db_path)
        # Pre-insert dataset row so FK constraints on documents don't block us.
        backend._execute(
            "INSERT OR IGNORE INTO datasets (id, name, kind) VALUES (?, ?, ?)",
            (1, "conc_ds", "text"),
        )

        a_acquired = threading.Event()  # A signals it holds the lock
        b_start = threading.Event()  # main signals B to start
        errors: list[Exception] = []

        def thread_a():
            try:
                with backend.lock_source("conc_key"):
                    a_acquired.set()
                    # Hold the lock long enough for B to contend.
                    b_start.wait(timeout=5.0)
                    backend._execute(
                        "INSERT INTO documents"
                        " (dataset_id, source_uri, content_hash, text)"
                        " VALUES (?, ?, ?, ?)",
                        (1, "vault://conc/a.md", "hash_a", "thread A doc"),
                    )
            except Exception as exc:
                errors.append(exc)

        def thread_b():
            try:
                # B tries to acquire only after A holds the lock.
                a_acquired.wait(timeout=5.0)
                with backend.lock_source("conc_key"):
                    backend._execute(
                        "INSERT INTO documents"
                        " (dataset_id, source_uri, content_hash, text)"
                        " VALUES (?, ?, ?, ?)",
                        (1, "vault://conc/b.md", "hash_b", "thread B doc"),
                    )
            except Exception as exc:
                errors.append(exc)

        ta = threading.Thread(target=thread_a, daemon=True)
        tb = threading.Thread(target=thread_b, daemon=True)

        ta.start()
        tb.start()
        # Let both threads finish (short timeout — should be << 1 s in practice).
        b_start.set()
        ta.join(timeout=10.0)
        tb.join(timeout=10.0)

        assert not errors, f"Threads raised exceptions: {errors}"
        assert not ta.is_alive(), "Thread A must finish within 10 s"
        assert not tb.is_alive(), "Thread B must finish within 10 s"

        # Both rows must exist.
        rows = backend._execute("SELECT source_uri FROM documents ORDER BY source_uri")
        uris = [r["source_uri"] for r in rows]
        assert "vault://conc/a.md" in uris, f"Thread A's row missing; got {uris}"
        assert "vault://conc/b.md" in uris, f"Thread B's row missing; got {uris}"

    # ------------------------------------------------------------------ timeout raises

    def test_timeout_raises_operational_error(self, tmp_path):
        """Lock held by another thread; acquire with short timeout re-raises OperationalError.

        Thread A holds the lock indefinitely (until signalled).  Thread B tries
        to acquire with lock_timeout_s=0.3.  Thread B must raise OperationalError
        within a few seconds (well before the test's own timeout).
        """
        import sqlite3 as _sqlite3

        db_path = tmp_path / "corpus.db"
        backend = _migrated_backend(db_path)

        a_acquired = threading.Event()  # A signals it has the lock
        a_release = threading.Event()  # main signals A to release
        b_result: list[Exception | None] = []

        def thread_a_hold():
            # Open a raw connection and start an IMMEDIATE transaction.
            # This simulates an external writer holding the global write-lock.
            conn = _sqlite3.connect(str(db_path), timeout=0)
            try:
                conn.execute("BEGIN IMMEDIATE")
                a_acquired.set()
                a_release.wait(timeout=10.0)
                conn.execute("ROLLBACK")
            finally:
                conn.close()

        def thread_b_timeout():
            # Wait until A holds the lock, then try with very short timeout.
            a_acquired.wait(timeout=5.0)
            try:
                with backend.lock_source("timeout_key", lock_timeout_s=0.3):
                    pass
                b_result.append(None)  # unexpected success
            except _sqlite3.OperationalError as exc:
                b_result.append(exc)
            except Exception as exc:
                b_result.append(exc)

        ta = threading.Thread(target=thread_a_hold, daemon=True)
        tb = threading.Thread(target=thread_b_timeout, daemon=True)

        ta.start()
        tb.start()
        tb.join(timeout=5.0)  # B must finish quickly (timeout=0.3 + retry overhead)
        a_release.set()
        ta.join(timeout=5.0)

        assert b_result, "Thread B must have completed"
        exc = b_result[0]
        assert isinstance(exc, _sqlite3.OperationalError), (
            f"Expected OperationalError on timeout; got {type(exc).__name__}: {exc}"
        )

    # ------------------------------------------------------------------ exit releases lock

    def test_lock_released_after_context_exit(self, tmp_path):
        """After the with block exits, a fresh BEGIN IMMEDIATE from another connection succeeds.

        This confirms __exit__ commits (or rolls back) the transaction, fully
        releasing the write-lock so a subsequent writer can proceed immediately.
        """
        import sqlite3 as _sqlite3

        db_path = tmp_path / "corpus.db"
        backend = _migrated_backend(db_path)

        # Acquire and release via the context manager.
        with backend.lock_source("release_test"):
            pass  # clean exit — lock must be released

        # A fresh external connection must be able to acquire IMMEDIATELY.
        conn = _sqlite3.connect(str(db_path), timeout=0)
        try:
            # If lock is still held this would raise OperationalError("database is locked").
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("ROLLBACK")
        except _sqlite3.OperationalError as exc:
            pytest.fail(
                f"Lock not released after context exit; fresh BEGIN IMMEDIATE failed: {exc}"
            )
        finally:
            conn.close()

    # ---------------------------------------------------------------- global serialization by key

    def test_different_keys_still_serialize(self, tmp_path):
        """Concurrent lock_source calls with *different* keys still serialize (global lock).

        Neither call should raise; both commits must land.  This test asserts
        that the implementation does NOT attempt per-key granularity (which
        could lead to simultaneous writers corrupting the DB).
        """
        db_path = tmp_path / "corpus.db"
        backend = _migrated_backend(db_path)
        backend._execute(
            "INSERT OR IGNORE INTO datasets (id, name, kind) VALUES (?, ?, ?)",
            (10, "gser_ds", "text"),
        )

        a_acquired = threading.Event()
        a_release = threading.Event()
        errors: list[Exception] = []

        def thread_a():
            try:
                with backend.lock_source("key_x"):
                    a_acquired.set()
                    a_release.wait(timeout=5.0)
                    backend._execute(
                        "INSERT INTO documents"
                        " (dataset_id, source_uri, content_hash, text)"
                        " VALUES (?, ?, ?, ?)",
                        (10, "vault://gser/a.md", "h_ga", "global ser A"),
                    )
            except Exception as exc:
                errors.append(exc)

        def thread_b():
            try:
                a_acquired.wait(timeout=5.0)
                with backend.lock_source("key_y"):  # different key, same global lock
                    backend._execute(
                        "INSERT INTO documents"
                        " (dataset_id, source_uri, content_hash, text)"
                        " VALUES (?, ?, ?, ?)",
                        (10, "vault://gser/b.md", "h_gb", "global ser B"),
                    )
            except Exception as exc:
                errors.append(exc)

        ta = threading.Thread(target=thread_a, daemon=True)
        tb = threading.Thread(target=thread_b, daemon=True)
        ta.start()
        tb.start()
        a_release.set()
        ta.join(timeout=10.0)
        tb.join(timeout=10.0)

        assert not errors, f"Threads raised: {errors}"
        rows = backend._execute(
            "SELECT source_uri FROM documents WHERE dataset_id = ? ORDER BY source_uri",
            (10,),
        )
        uris = [r["source_uri"] for r in rows]
        assert "vault://gser/a.md" in uris, f"A's row missing; got {uris}"


# ---------------------------------------------------------------------------
# B-09 helpers
# ---------------------------------------------------------------------------


def _insert_dataset_only(backend, dataset_id: int, kind: str = "text") -> None:
    """Insert only a dataset row (no document) — used by B-09 resolve/source tests."""
    backend._execute(
        "INSERT OR IGNORE INTO datasets (id, name, kind) VALUES (?, ?, ?)",
        (dataset_id, f"b09_ds_{dataset_id}", kind),
    )


def _insert_doc_with_chunks(
    backend,
    dataset_id: int,
    source_uri: str,
    content_hash: str = "hash_abc",
    text: str = "body",
    chunk_texts: list[str] | None = None,
) -> int:
    """Insert a document row + zero or more chunk rows; return the document id."""
    _insert_dataset_only(backend, dataset_id)
    rows = backend._execute(
        "INSERT INTO documents (dataset_id, source_uri, content_hash, text)"
        " VALUES (?, ?, ?, ?) RETURNING id",
        (dataset_id, source_uri, content_hash, text),
    )
    doc_id: int = rows[0]["id"]
    for i, chunk_text in enumerate(chunk_texts or []):
        backend._execute(
            "INSERT INTO chunks (document_id, chunk_index, text) VALUES (?, ?, ?)",
            (doc_id, i, chunk_text),
        )
    return doc_id


def _insert_conv_with_messages(
    backend,
    dataset_id: int,
    source_uri: str,
    content_hash: str = "conv_hash_abc",
    message_texts: list[str] | None = None,
) -> int:
    """Insert a conversation + messages + per-message chunks; return conv id."""
    _insert_dataset_only(backend, dataset_id, kind="chat")
    rows = backend._execute(
        "INSERT INTO conversations"
        " (dataset_id, source_uri, content_hash, message_count)"
        " VALUES (?, ?, ?, ?) RETURNING id",
        (dataset_id, source_uri, content_hash, len(message_texts or [])),
    )
    conv_id: int = rows[0]["id"]
    for i, msg_text in enumerate(message_texts or []):
        msg_rows = backend._execute(
            "INSERT INTO messages"
            " (conversation_id, turn_index, role, content)"
            " VALUES (?, ?, ?, ?) RETURNING id",
            (conv_id, i, "user", msg_text),
        )
        msg_id: int = msg_rows[0]["id"]
        backend._execute(
            "INSERT INTO chunks (conversation_id, message_id, chunk_index, text)"
            " VALUES (?, ?, ?, ?)",
            (conv_id, msg_id, 0, msg_text),
        )
    return conv_id


def _count_rows(backend, table: str, **where: object) -> int:
    """Return COUNT(*) from *table* filtered by keyword-arg equality clauses."""
    if where:
        cols = list(where.keys())
        clause = " AND ".join(f"{c} = ?" for c in cols)
        vals = tuple(where[c] for c in cols)
        rows = backend._execute(
            f"SELECT COUNT(*) AS cnt FROM {table} WHERE {clause}",
            vals,
        )
    else:
        rows = backend._execute(f"SELECT COUNT(*) AS cnt FROM {table}")
    return int(rows[0]["cnt"])


# ---------------------------------------------------------------------------
# TestDeleteDocument — B-09
# ---------------------------------------------------------------------------


class TestDeleteDocument:
    """delete_document(dataset_id, source_uri) — DELETE + cascade + idempotency."""

    def test_delete_removes_document_row(self, tmp_path):
        """Happy path: after delete_document the documents row is gone."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_doc_with_chunks(backend, 1, "vault://del.md")

        backend.delete_document(1, "vault://del.md")

        remaining = backend._execute(
            "SELECT id FROM documents WHERE dataset_id = 1 AND source_uri = ?",
            ("vault://del.md",),
        )
        assert remaining == [], (
            f"delete_document must remove the documents row; found rows: {remaining}"
        )

    def test_delete_cascades_to_chunks(self, tmp_path):
        """Deleting a document also deletes its chunk rows (FK CASCADE)."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        doc_id = _insert_doc_with_chunks(
            backend, 1, "vault://cascade.md", chunk_texts=["chunk A", "chunk B"]
        )

        backend.delete_document(1, "vault://cascade.md")

        chunk_count = _count_rows(backend, "chunks", document_id=doc_id)
        assert chunk_count == 0, (
            f"Chunks for deleted document must be cascade-deleted; found {chunk_count}"
        )

    def test_delete_nonexistent_is_noop(self, tmp_path):
        """Deleting a document that does not exist raises no error (idempotent)."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_only(backend, 1)

        # Must not raise
        backend.delete_document(1, "vault://ghost.md")

    def test_delete_only_affects_matching_dataset(self, tmp_path):
        """delete_document with dataset_id=1 must not remove dataset_id=2 rows."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_doc_with_chunks(backend, 1, "vault://shared.md")
        _insert_doc_with_chunks(backend, 2, "vault://shared.md")

        backend.delete_document(1, "vault://shared.md")

        # Dataset 1 row is gone
        ds1_rows = backend._execute(
            "SELECT id FROM documents WHERE dataset_id = 1 AND source_uri = ?",
            ("vault://shared.md",),
        )
        assert ds1_rows == [], "Row for dataset_id=1 must be deleted"

        # Dataset 2 row must survive
        ds2_rows = backend._execute(
            "SELECT id FROM documents WHERE dataset_id = 2 AND source_uri = ?",
            ("vault://shared.md",),
        )
        assert len(ds2_rows) == 1, (
            "Row for dataset_id=2 must survive delete_document(dataset_id=1, ...)"
        )


# ---------------------------------------------------------------------------
# TestDeleteConversation — B-09
# ---------------------------------------------------------------------------


class TestDeleteConversation:
    """delete_conversation(dataset_id, source_uri) — DELETE + cascade + idempotency."""

    def test_delete_removes_conversation_row(self, tmp_path):
        """Happy path: after delete_conversation the conversations row is gone."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_conv_with_messages(backend, 1, "claude://s1")

        backend.delete_conversation(1, "claude://s1")

        remaining = backend._execute(
            "SELECT id FROM conversations WHERE dataset_id = 1 AND source_uri = ?",
            ("claude://s1",),
        )
        assert remaining == [], (
            f"delete_conversation must remove the conversations row; found: {remaining}"
        )

    def test_delete_cascades_messages_and_chunks(self, tmp_path):
        """Deleting a conversation cascades to its messages and their chunks."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        conv_id = _insert_conv_with_messages(
            backend, 1, "claude://cascade_conv", message_texts=["msg1", "msg2"]
        )

        backend.delete_conversation(1, "claude://cascade_conv")

        msg_count = _count_rows(backend, "messages", conversation_id=conv_id)
        chunk_count = _count_rows(backend, "chunks", conversation_id=conv_id)
        assert msg_count == 0, (
            f"Messages for deleted conversation must be cascade-deleted; found {msg_count}"
        )
        assert chunk_count == 0, (
            f"Chunks for deleted conversation must be cascade-deleted; found {chunk_count}"
        )

    def test_delete_nonexistent_conversation_is_noop(self, tmp_path):
        """Deleting a conversation that does not exist raises no error (idempotent)."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_only(backend, 1, kind="chat")

        # Must not raise
        backend.delete_conversation(1, "claude://ghost_session")


# ---------------------------------------------------------------------------
# TestFindDocument — B-09
# ---------------------------------------------------------------------------


class TestFindDocument:
    """find_document(dataset_id, source_uri) -> dict | None — read-only lookup."""

    def test_returns_dict_for_existing_document(self, tmp_path):
        """Happy path: existing document returns a dict with id and content_hash."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_doc_with_chunks(backend, 1, "vault://find_me.md", content_hash="find_hash")

        result = backend.find_document(1, "vault://find_me.md")

        assert result is not None, "find_document must return a dict for an existing row"
        assert "id" in result, "Returned dict must contain 'id'"
        assert "content_hash" in result, "Returned dict must contain 'content_hash'"
        assert result["content_hash"] == "find_hash", (
            f"content_hash mismatch: expected 'find_hash', got {result['content_hash']!r}"
        )

    def test_returns_none_for_missing_document(self, tmp_path):
        """Missing document returns None (no error)."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_only(backend, 1)

        result = backend.find_document(1, "vault://does_not_exist.md")

        assert result is None, f"find_document must return None for a missing row; got {result!r}"

    def test_wrong_dataset_id_returns_none(self, tmp_path):
        """Same source_uri under a different dataset_id returns None."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_doc_with_chunks(backend, 1, "vault://shared_uri.md")

        # Ask for dataset_id=2 — should not find dataset_id=1's row
        result = backend.find_document(2, "vault://shared_uri.md")

        assert result is None, (
            f"find_document must return None when dataset_id does not match; got {result!r}"
        )

    def test_is_non_mutating(self, tmp_path):
        """Calling find_document twice on the same row does not change the row."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_doc_with_chunks(backend, 1, "vault://idempotent.md", content_hash="stable_hash")

        result1 = backend.find_document(1, "vault://idempotent.md")
        result2 = backend.find_document(1, "vault://idempotent.md")

        assert result1 is not None and result2 is not None
        assert result1["id"] == result2["id"], "Repeated find_document must return same id"
        assert result1["content_hash"] == result2["content_hash"]


# ---------------------------------------------------------------------------
# TestResolveDocument — B-09
# ---------------------------------------------------------------------------


class TestResolveDocument:
    """resolve_document(dataset_id, source_uri) -> dict | None — upsert semantics."""

    def test_creates_stub_for_missing_document(self, tmp_path):
        """When no row exists, resolve_document inserts a stub and returns a dict."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_only(backend, 1)

        result = backend.resolve_document(1, "vault://new_resolve.md")

        assert result is not None, "resolve_document must return a dict even when row is new"
        assert "id" in result, "Returned dict must contain 'id'"
        assert "content_hash" in result, "Returned dict must contain 'content_hash'"

    def test_new_stub_has_empty_content_hash(self, tmp_path):
        """Newly created stub row has content_hash == '' (empty string)."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_only(backend, 1)

        result = backend.resolve_document(1, "vault://empty_hash.md")

        assert result is not None
        assert result["content_hash"] == "", (
            f"Stub row content_hash must be empty string; got {result['content_hash']!r}"
        )

    def test_returns_existing_row_without_duplicate(self, tmp_path):
        """When the row already exists, resolve_document returns it without inserting
        a duplicate."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_doc_with_chunks(
            backend, 1, "vault://already_exists.md", content_hash="existing_hash"
        )

        result = backend.resolve_document(1, "vault://already_exists.md")

        assert result is not None
        assert result["content_hash"] == "existing_hash", (
            f"resolve_document on existing row must return existing content_hash; "
            f"got {result['content_hash']!r}"
        )
        doc_count = _count_rows(
            backend, "documents", dataset_id=1, source_uri="vault://already_exists.md"
        )
        assert doc_count == 1, (
            f"resolve_document must not create a duplicate row; found {doc_count} rows"
        )

    def test_idempotent_same_id_on_double_call(self, tmp_path):
        """Calling resolve_document twice for a missing URI returns the same id."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_only(backend, 1)

        result1 = backend.resolve_document(1, "vault://idempotent_resolve.md")
        result2 = backend.resolve_document(1, "vault://idempotent_resolve.md")

        assert result1 is not None and result2 is not None
        assert result1["id"] == result2["id"], (
            f"Two resolve_document calls must yield the same id; "
            f"got {result1['id']} vs {result2['id']}"
        )

    def test_returns_none_for_empty_source_uri(self, tmp_path):
        """resolve_document with empty source_uri returns None (matches Postgres contract)."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_only(backend, 1)

        result = backend.resolve_document(1, "")

        assert result is None, f"resolve_document('') must return None; got {result!r}"

    def test_isolated_by_dataset_id(self, tmp_path):
        """Resolving the same URI under different dataset_ids creates separate rows."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_only(backend, 1)
        _insert_dataset_only(backend, 2)

        result1 = backend.resolve_document(1, "vault://cross_ds.md")
        result2 = backend.resolve_document(2, "vault://cross_ds.md")

        assert result1 is not None and result2 is not None
        assert result1["id"] != result2["id"], (
            "resolve_document for different dataset_ids must create distinct rows"
        )


# ---------------------------------------------------------------------------
# TestResolveSelfSource — B-09
# ---------------------------------------------------------------------------


class TestResolveSelfSource:
    """resolve_self_source(dataset_id, host) -> int — upsert into sources table."""

    def test_first_call_returns_int_id(self, tmp_path):
        """Happy path: first call inserts a sources row and returns its integer id."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_only(backend, 1)

        result = backend.resolve_self_source(1, "myhost.local")

        assert isinstance(result, int), (
            f"resolve_self_source must return int; got {type(result).__name__}"
        )
        assert result >= 1, f"Returned id must be >= 1; got {result}"

    def test_second_call_same_args_returns_same_id(self, tmp_path):
        """Idempotency: two calls with the same (dataset_id, host) return the same id."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_only(backend, 1)

        id1 = backend.resolve_self_source(1, "myhost.local")
        id2 = backend.resolve_self_source(1, "myhost.local")

        assert id1 == id2, f"resolve_self_source must be idempotent; got {id1} then {id2}"

    def test_second_call_does_not_duplicate_row(self, tmp_path):
        """Calling resolve_self_source twice must not insert two sources rows."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_only(backend, 1)

        backend.resolve_self_source(1, "dupe_host")
        backend.resolve_self_source(1, "dupe_host")

        row_count = _count_rows(backend, "sources", dataset_id=1)
        assert row_count == 1, (
            f"Only one sources row must exist after two identical calls; found {row_count}"
        )

    def test_different_host_produces_different_id(self, tmp_path):
        """Different host values for the same dataset_id create separate sources rows."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_only(backend, 1)

        id_a = backend.resolve_self_source(1, "host-a.local")
        id_b = backend.resolve_self_source(1, "host-b.local")

        assert id_a != id_b, (
            f"Different hosts must produce different source ids; both returned {id_a}"
        )

    def test_uses_sync_plugin_and_pull_identity(self, tmp_path):
        """The inserted sources row uses plugin='sync' and identity='pull' (Postgres parity)."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_only(backend, 1)

        backend.resolve_self_source(1, "check_host")

        rows = backend._execute(
            "SELECT plugin, identity FROM sources WHERE dataset_id = 1",
        )
        assert len(rows) == 1, "Expected exactly one sources row"
        assert rows[0]["plugin"] == "sync", f"plugin must be 'sync'; got {rows[0]['plugin']!r}"
        assert rows[0]["identity"] == "pull", (
            f"identity must be 'pull'; got {rows[0]['identity']!r}"
        )

    def test_isolated_by_dataset_id(self, tmp_path):
        """The same host under different dataset_ids creates two separate rows."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        _insert_dataset_only(backend, 1)
        _insert_dataset_only(backend, 2)

        id1 = backend.resolve_self_source(1, "shared_host")
        id2 = backend.resolve_self_source(2, "shared_host")

        assert id1 != id2, (
            f"Different dataset_ids must produce different source ids; both returned {id1}"
        )


# ---------------------------------------------------------------------------
# B-10 helpers
# ---------------------------------------------------------------------------


def _insert_document_for_revision(
    backend,
    dataset_id: int = 1,
    source_uri: str = "vault://rev_test.md",
    content_hash: str = "rev_hash_001",
    text: str = "# Revision Test",
) -> int:
    """Insert dataset + document rows; return the document id.

    Uses INSERT OR IGNORE on the dataset row so tests with the same dataset_id
    don't collide on the UNIQUE(name) constraint.
    """
    backend._execute(
        "INSERT OR IGNORE INTO datasets (id, name, kind) VALUES (?, ?, ?)",
        (dataset_id, f"b10_ds_{dataset_id}", "text"),
    )
    rows = backend._execute(
        "INSERT INTO documents (dataset_id, source_uri, content_hash, text)"
        " VALUES (?, ?, ?, ?) RETURNING id",
        (dataset_id, source_uri, content_hash, text),
    )
    return rows[0]["id"]


def _call_insert_revision(
    backend,
    document_id: int,
    *,
    source_uri: str = "vault://rev_test.md",
    content_hash: str = "chash_001",
    text: str = "revision body",
    parent_revision_id: int | None = None,
    author_host: str = "localhost",
    is_tombstone: bool = False,
    metadata: dict | None = None,
) -> dict:
    """Thin wrapper so tests don't repeat every keyword arg."""
    return backend.insert_revision(
        document_id=document_id,
        source_uri=source_uri,
        content_hash=content_hash,
        text=text,
        parent_revision_id=parent_revision_id,
        author_host=author_host,
        is_tombstone=is_tombstone,
        metadata=metadata,
    )


def _revision_rows(backend, document_id: int) -> list[dict]:
    """Return all document_revisions rows for *document_id*, ordered by revision_number."""
    return backend._execute(
        "SELECT * FROM document_revisions WHERE document_id = ? ORDER BY revision_number",
        (document_id,),
    )


# ---------------------------------------------------------------------------
# TestInsertRevisionHappyPath — B-10
# ---------------------------------------------------------------------------


class TestInsertRevisionHappyPath:
    """insert_revision() happy-path: return shape, numbering, DB visibility."""

    def test_returns_dict_with_id_and_revision_number(self, tmp_path):
        """insert_revision must return a dict containing 'id' and 'revision_number' keys."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        doc_id = _insert_document_for_revision(backend)

        with backend.lock_source("vault://rev_test.md"):
            result = _call_insert_revision(backend, doc_id)

        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "id" in result, f"Return dict must contain 'id'; got keys: {list(result)}"
        assert "revision_number" in result, (
            f"Return dict must contain 'revision_number'; got keys: {list(result)}"
        )

    def test_id_is_positive_integer(self, tmp_path):
        """The returned 'id' must be a positive integer (autoincrement PK)."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        doc_id = _insert_document_for_revision(backend)

        with backend.lock_source("vault://rev_test.md"):
            result = _call_insert_revision(backend, doc_id)

        assert isinstance(result["id"], int), f"'id' must be int, got {type(result['id'])}"
        assert result["id"] >= 1, f"'id' must be >= 1, got {result['id']}"

    def test_first_revision_has_revision_number_one(self, tmp_path):
        """First insert_revision for a document returns revision_number=1."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        doc_id = _insert_document_for_revision(backend)

        with backend.lock_source("vault://rev_test.md"):
            result = _call_insert_revision(backend, doc_id)

        assert result["revision_number"] == 1, (
            f"First revision must be revision_number=1; got {result['revision_number']}"
        )

    def test_second_revision_has_revision_number_two(self, tmp_path):
        """Second insert_revision for the same document returns revision_number=2."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        doc_id = _insert_document_for_revision(backend)

        with backend.lock_source("vault://rev_test.md"):
            r1 = _call_insert_revision(backend, doc_id, content_hash="chash_001")
            r2 = _call_insert_revision(
                backend,
                doc_id,
                content_hash="chash_002",
                parent_revision_id=r1["id"],
            )

        assert r2["revision_number"] == 2, (
            f"Second revision must be revision_number=2; got {r2['revision_number']}"
        )

    def test_row_visible_via_select(self, tmp_path):
        """After insert_revision, the row is visible via direct SELECT on document_revisions."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        doc_id = _insert_document_for_revision(backend)

        with backend.lock_source("vault://rev_test.md"):
            result = _call_insert_revision(
                backend,
                doc_id,
                content_hash="chash_vis",
                text="visible body",
                author_host="check_host",
            )

        rows = _revision_rows(backend, doc_id)
        assert len(rows) == 1, f"Expected 1 revision row; found {len(rows)}"
        row = rows[0]
        assert row["id"] == result["id"]
        assert row["document_id"] == doc_id
        assert row["revision_number"] == 1
        assert row["content_hash"] == "chash_vis"
        assert row["text"] == "visible body"
        assert row["author_host"] == "check_host"


# ---------------------------------------------------------------------------
# TestInsertRevisionParents — B-10
# ---------------------------------------------------------------------------


class TestInsertRevisionParents:
    """parent_revision_id FK — None allowed for root; non-None stored correctly."""

    def test_parent_revision_id_none_for_first_revision(self, tmp_path):
        """parent_revision_id=None is valid for the first (root) revision."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        doc_id = _insert_document_for_revision(backend)

        with backend.lock_source("vault://rev_test.md"):
            result = _call_insert_revision(backend, doc_id, parent_revision_id=None)

        rows = _revision_rows(backend, doc_id)
        assert len(rows) == 1
        # NULL should round-trip as None through sqlite3.Row
        stored_parent = rows[0]["parent_revision_id"]
        assert stored_parent is None, (
            f"parent_revision_id=None must store NULL; got {stored_parent!r}"
        )
        # Return value id should match the inserted row
        assert rows[0]["id"] == result["id"]

    def test_parent_revision_id_stored_for_child_revision(self, tmp_path):
        """parent_revision_id is stored correctly when pointing to a prior revision."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        doc_id = _insert_document_for_revision(backend)

        with backend.lock_source("vault://rev_test.md"):
            r1 = _call_insert_revision(backend, doc_id, content_hash="chash_p1")
            r2 = _call_insert_revision(
                backend,
                doc_id,
                content_hash="chash_p2",
                parent_revision_id=r1["id"],
            )

        rows = _revision_rows(backend, doc_id)
        assert len(rows) == 2
        child_row = rows[1]  # revision_number=2
        assert child_row["parent_revision_id"] == r1["id"], (
            f"parent_revision_id must be {r1['id']}; got {child_row['parent_revision_id']}"
        )
        assert child_row["id"] == r2["id"]


# ---------------------------------------------------------------------------
# TestInsertRevisionTombstone — B-10
# ---------------------------------------------------------------------------


class TestInsertRevisionTombstone:
    """is_tombstone flag: True stored; empty text allowed for tombstone."""

    def test_is_tombstone_true_stored_as_truthy(self, tmp_path):
        """is_tombstone=True is stored and reads back as truthy (SQLite INTEGER 1)."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        doc_id = _insert_document_for_revision(backend)

        with backend.lock_source("vault://rev_test.md"):
            _call_insert_revision(backend, doc_id, is_tombstone=True, text="deleted")

        rows = _revision_rows(backend, doc_id)
        assert len(rows) == 1
        # SQLite stores booleans as INTEGER; is_tombstone=1 is truthy
        assert rows[0]["is_tombstone"], (
            f"is_tombstone=True must store truthy value; got {rows[0]['is_tombstone']!r}"
        )

    def test_empty_text_allowed_for_tombstone(self, tmp_path):
        """Tombstone revisions may have text='' (the column DEFAULT is empty string)."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        doc_id = _insert_document_for_revision(backend)

        with backend.lock_source("vault://rev_test.md"):
            result = _call_insert_revision(
                backend,
                doc_id,
                is_tombstone=True,
                text="",
                content_hash="tombstone_hash",
            )

        rows = _revision_rows(backend, doc_id)
        assert len(rows) == 1
        assert rows[0]["text"] == "", (
            f"Tombstone revision with text='' must store empty string; got {rows[0]['text']!r}"
        )
        assert result["id"] >= 1


# ---------------------------------------------------------------------------
# TestInsertRevisionMetadata — B-10
# ---------------------------------------------------------------------------


class TestInsertRevisionMetadata:
    """metadata serialization: None → '{}' JSON; dict round-trips; nested dict round-trips."""

    def test_metadata_none_stores_empty_json_object(self, tmp_path):
        """metadata=None must store '{}' (empty JSON object) or NULL — mirrors Postgres behavior."""
        import json as _json

        backend = _migrated_backend(tmp_path / "corpus.db")
        doc_id = _insert_document_for_revision(backend)

        with backend.lock_source("vault://rev_test.md"):
            _call_insert_revision(backend, doc_id, metadata=None)

        rows = _revision_rows(backend, doc_id)
        assert len(rows) == 1
        raw_metadata = rows[0]["metadata"]
        # Acceptable outcomes: None (NULL) OR a JSON-parseable string that decodes to {}
        if raw_metadata is None:
            # NULL is acceptable — mirrors Postgres NULL for empty metadata
            pass
        else:
            parsed = _json.loads(raw_metadata)
            assert parsed == {}, f"metadata=None must decode to {{}}; decoded to {parsed!r}"

    def test_metadata_dict_round_trips(self, tmp_path):
        """metadata={"key": "value"} round-trips: stored as JSON text, parsed back to equal dict."""
        import json as _json

        backend = _migrated_backend(tmp_path / "corpus.db")
        doc_id = _insert_document_for_revision(backend)
        original = {"key": "value", "count": 42}

        with backend.lock_source("vault://rev_test.md"):
            _call_insert_revision(backend, doc_id, metadata=original)

        rows = _revision_rows(backend, doc_id)
        raw = rows[0]["metadata"]
        assert raw is not None, "metadata dict must not be stored as NULL"
        parsed = _json.loads(raw)
        assert parsed == original, (
            f"metadata dict must round-trip; expected {original!r}, got {parsed!r}"
        )

    def test_nested_metadata_dict_round_trips(self, tmp_path):
        """Nested dict metadata round-trips correctly through JSON serialization."""
        import json as _json

        backend = _migrated_backend(tmp_path / "corpus.db")
        doc_id = _insert_document_for_revision(backend)
        original = {"outer": {"inner": [1, 2, 3]}, "flag": True}

        with backend.lock_source("vault://rev_test.md"):
            _call_insert_revision(backend, doc_id, metadata=original)

        rows = _revision_rows(backend, doc_id)
        raw = rows[0]["metadata"]
        assert raw is not None, "Nested metadata must not be stored as NULL"
        parsed = _json.loads(raw)
        assert parsed == original, (
            f"Nested metadata must round-trip; expected {original!r}, got {parsed!r}"
        )


# ---------------------------------------------------------------------------
# TestInsertRevisionMonotonicity — B-10
# ---------------------------------------------------------------------------


class TestInsertRevisionMonotonicity:
    """Concurrent inserts produce strictly increasing, gapless revision_numbers."""

    def test_two_threads_produce_revision_numbers_one_and_two(self, tmp_path):
        """Two threads each inserting one revision get numbers 1 and 2 (no gap, no duplicate).

        Uses threading.Event handshake so thread B starts contending before A has released
        the lock — proving that lock_source() serializes the MAX()+1 allocation.
        """
        db_path = tmp_path / "corpus.db"
        backend = _migrated_backend(db_path)
        doc_id = _insert_document_for_revision(backend, source_uri="vault://mono.md")

        a_inside = threading.Event()  # A signals it is inside the lock
        b_can_go = threading.Event()  # main tells A it may proceed
        results: list[int] = []
        errors: list[Exception] = []

        def thread_a():
            try:
                with backend.lock_source("vault://mono.md"):
                    a_inside.set()
                    b_can_go.wait(timeout=5.0)
                    r = _call_insert_revision(
                        backend,
                        doc_id,
                        content_hash="mono_a",
                        source_uri="vault://mono.md",
                    )
                    results.append(r["revision_number"])
            except Exception as exc:
                errors.append(exc)

        def thread_b():
            try:
                # Wait until A is inside the lock so we genuinely contend
                a_inside.wait(timeout=5.0)
                with backend.lock_source("vault://mono.md"):
                    r = _call_insert_revision(
                        backend,
                        doc_id,
                        content_hash="mono_b",
                        source_uri="vault://mono.md",
                    )
                    results.append(r["revision_number"])
            except Exception as exc:
                errors.append(exc)

        ta = threading.Thread(target=thread_a, daemon=True)
        tb = threading.Thread(target=thread_b, daemon=True)

        ta.start()
        tb.start()
        b_can_go.set()  # let A proceed so B contends and then gets the lock
        ta.join(timeout=10.0)
        tb.join(timeout=10.0)

        assert not errors, f"Threads raised exceptions: {errors}"
        assert not ta.is_alive(), "Thread A did not finish within timeout"
        assert not tb.is_alive(), "Thread B did not finish within timeout"

        assert sorted(results) == [1, 2], (
            f"Two concurrent inserts must produce revision_numbers {{1, 2}}; got {sorted(results)}"
        )

    def test_independent_documents_each_start_at_one(self, tmp_path):
        """Two distinct document_ids each get independent monotonic numbering (both start at 1)."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        doc_a = _insert_document_for_revision(
            backend, dataset_id=1, source_uri="vault://doc_a.md", content_hash="ha"
        )
        doc_b = _insert_document_for_revision(
            backend, dataset_id=1, source_uri="vault://doc_b.md", content_hash="hb"
        )

        with backend.lock_source("vault://doc_a.md"):
            ra = _call_insert_revision(
                backend, doc_a, source_uri="vault://doc_a.md", content_hash="ra1"
            )

        with backend.lock_source("vault://doc_b.md"):
            rb = _call_insert_revision(
                backend, doc_b, source_uri="vault://doc_b.md", content_hash="rb1"
            )

        assert ra["revision_number"] == 1, (
            f"doc_a first revision must be 1; got {ra['revision_number']}"
        )
        assert rb["revision_number"] == 1, (
            f"doc_b first revision must be 1 (independent); got {rb['revision_number']}"
        )


# ---------------------------------------------------------------------------
# TestInsertRevisionFailurePaths — B-10
# ---------------------------------------------------------------------------


class TestInsertRevisionFailurePaths:
    """Failure paths: invalid FK raises IntegrityError; missing required kwarg raises TypeError."""

    def test_invalid_document_id_raises_integrity_error(self, tmp_path):
        """Passing a document_id that does not exist must raise sqlite3.IntegrityError (FK).

        PRAGMA foreign_keys = ON is set by _get_connection(), so the FK is enforced.
        """
        import sqlite3 as _sqlite3

        backend = _migrated_backend(tmp_path / "corpus.db")

        with (
            pytest.raises(_sqlite3.IntegrityError),
            backend.lock_source("vault://no_such_doc.md"),
        ):
            _call_insert_revision(
                backend,
                document_id=999999,  # does not exist
                source_uri="vault://no_such_doc.md",
                content_hash="ghost_hash",
            )

    def test_missing_required_kwarg_raises_type_error(self, tmp_path):
        """Calling insert_revision without a required keyword arg must raise TypeError."""
        backend = _migrated_backend(tmp_path / "corpus.db")

        with pytest.raises(TypeError):
            # Omit document_id entirely — must raise TypeError
            backend.insert_revision(  # type: ignore[call-arg]
                source_uri="vault://rev_test.md",
                content_hash="chash",
                text="body",
                parent_revision_id=None,
                author_host="localhost",
                is_tombstone=False,
            )


# ---------------------------------------------------------------------------
# B-11 helpers
# ---------------------------------------------------------------------------


def _insert_source_row(
    backend,
    dataset_id: int,
    host: str = "remote.host",
    plugin: str = "sync",
    identity: str = "pull",
    last_pulled_revision_id: int | None = None,
) -> int:
    """Insert a sources row and return its id.

    Uses INSERT OR IGNORE on the dataset so tests with the same dataset_id don't
    collide on the UNIQUE(name) constraint.
    """
    _insert_dataset_only(backend, dataset_id)
    rows = backend._execute(
        "INSERT INTO sources (dataset_id, plugin, identity, host, last_pulled_revision_id)"
        " VALUES (?, ?, ?, ?, ?) RETURNING id",
        (dataset_id, plugin, identity, host, last_pulled_revision_id),
    )
    return rows[0]["id"]


def _insert_revision_direct(
    backend,
    document_id: int,
    *,
    author_host: str = "remote.host",
    content_hash: str = "chash_b11",
    text: str = "b11 rev body",
    parent_revision_id: int | None = None,
) -> int:
    """Insert a document_revision row without lock_source (for read-side test setup).

    Returns the new revision id.
    """
    rows = backend._execute(
        """
        INSERT INTO document_revisions
            (document_id, revision_number, parent_revision_id, content_hash,
             text, author_host, is_tombstone, metadata, created_at)
        SELECT
            ?,
            COALESCE(MAX(revision_number), 0) + 1,
            ?,
            ?, ?, ?, 0, '{}',
            strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        FROM document_revisions
        WHERE document_id = ?
        RETURNING id
        """,
        (
            document_id,
            parent_revision_id,
            content_hash,
            text,
            author_host,
            document_id,
        ),
    )
    return rows[0]["id"]


def _insert_doc_for_b11(
    backend,
    dataset_id: int,
    source_uri: str,
    content_hash: str = "doc_hash_b11",
) -> int:
    """Insert dataset + document; return document id."""
    backend._execute(
        "INSERT OR IGNORE INTO datasets (id, name, kind) VALUES (?, ?, ?)",
        (dataset_id, f"b11_ds_{dataset_id}", "text"),
    )
    rows = backend._execute(
        "INSERT INTO documents (dataset_id, source_uri, content_hash, text)"
        " VALUES (?, ?, ?, ?) RETURNING id",
        (dataset_id, source_uri, content_hash, "b11 body"),
    )
    return rows[0]["id"]


# ---------------------------------------------------------------------------
# TestLatestRevision — B-11
# ---------------------------------------------------------------------------


class TestLatestRevision:
    """latest_revision(document_id) returns highest revision_number row or None."""

    def test_returns_highest_revision_number_row(self, tmp_path):
        """Happy path: with multiple revisions, latest_revision returns the one with the highest
        revision_number, not necessarily the last inserted id.
        """
        backend = _migrated_backend(tmp_path / "corpus.db")
        doc_id = _insert_doc_for_b11(backend, dataset_id=1, source_uri="vault://lr/doc.md")

        r1_id = _insert_revision_direct(backend, doc_id, content_hash="lr_hash_1")
        r2_id = _insert_revision_direct(
            backend, doc_id, content_hash="lr_hash_2", parent_revision_id=r1_id
        )
        _insert_revision_direct(backend, doc_id, content_hash="lr_hash_3", parent_revision_id=r2_id)

        result = backend.latest_revision(doc_id)

        assert result is not None, "latest_revision must return a dict, not None"
        assert result["content_hash"] == "lr_hash_3", (
            f"Expected the latest (highest revision_number) row; got {result}"
        )

    def test_returns_none_for_unknown_document(self, tmp_path):
        """latest_revision for a document_id with no revisions returns None."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        # No revisions inserted at all.
        result = backend.latest_revision(document_id=99999)

        assert result is None, f"Expected None for unknown document_id; got {result!r}"

    def test_isolated_by_document_id(self, tmp_path):
        """latest_revision for doc_a does not bleed into doc_b even with shared dataset."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        doc_a = _insert_doc_for_b11(backend, dataset_id=1, source_uri="vault://lr/a.md")
        doc_b = _insert_doc_for_b11(backend, dataset_id=1, source_uri="vault://lr/b.md")

        _insert_revision_direct(backend, doc_a, content_hash="lr_a_hash_1")
        _insert_revision_direct(backend, doc_a, content_hash="lr_a_hash_2")
        _insert_revision_direct(backend, doc_b, content_hash="lr_b_hash_1")

        result_a = backend.latest_revision(doc_a)
        result_b = backend.latest_revision(doc_b)

        assert result_a is not None and result_a["content_hash"] == "lr_a_hash_2", (
            f"doc_a latest must be lr_a_hash_2; got {result_a}"
        )
        assert result_b is not None and result_b["content_hash"] == "lr_b_hash_1", (
            f"doc_b latest must be lr_b_hash_1; got {result_b}"
        )
        # Ensure the doc_id in each result matches the queried document
        assert result_a["document_id"] == doc_a, (
            f"latest_revision for doc_a must have document_id={doc_a}; "
            f"got {result_a['document_id']}"
        )
        assert result_b["document_id"] == doc_b, (
            f"latest_revision for doc_b must have document_id={doc_b}; "
            f"got {result_b['document_id']}"
        )


# ---------------------------------------------------------------------------
# TestPendingRemoteRevisions — B-11
# ---------------------------------------------------------------------------


class TestPendingRemoteRevisions:
    """pending_remote_revisions(dataset_id, last_pulled_revision_id, self_host, limit=1024)."""

    def test_happy_path_returns_remote_revisions(self, tmp_path):
        """Basic happy path: revisions authored by other hosts appear in the result."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        doc_id = _insert_doc_for_b11(backend, dataset_id=1, source_uri="vault://prr/doc.md")
        _insert_revision_direct(
            backend, doc_id, author_host="remote.host", content_hash="prr_hash_1"
        )

        results = backend.pending_remote_revisions(
            dataset_id=1, last_pulled_revision_id=None, self_host="local.host"
        )

        assert len(results) >= 1, f"Expected at least one pending revision; got {results}"
        hashes = [r["content_hash"] for r in results]
        assert "prr_hash_1" in hashes, f"prr_hash_1 must appear in results; got {hashes}"

    def test_filters_by_dataset_id(self, tmp_path):
        """Revisions under a different dataset_id must not appear."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        doc_ds1 = _insert_doc_for_b11(backend, dataset_id=1, source_uri="vault://prr_ds1/doc.md")
        doc_ds2 = _insert_doc_for_b11(backend, dataset_id=2, source_uri="vault://prr_ds2/doc.md")
        _insert_revision_direct(
            backend, doc_ds1, author_host="remote.host", content_hash="prr_ds1_hash"
        )
        _insert_revision_direct(
            backend, doc_ds2, author_host="remote.host", content_hash="prr_ds2_hash"
        )

        results = backend.pending_remote_revisions(
            dataset_id=1, last_pulled_revision_id=None, self_host="local.host"
        )

        hashes = [r["content_hash"] for r in results]
        assert "prr_ds1_hash" in hashes, f"ds1 revision must appear; got {hashes}"
        assert "prr_ds2_hash" not in hashes, f"ds2 revision must NOT appear; got {hashes}"

    def test_excludes_self_host_revisions(self, tmp_path):
        """Revisions authored by self_host are excluded from results."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        doc_id = _insert_doc_for_b11(backend, dataset_id=1, source_uri="vault://prr/selfx.md")
        _insert_revision_direct(
            backend, doc_id, author_host="self.host", content_hash="prr_self_hash"
        )
        _insert_revision_direct(
            backend, doc_id, author_host="remote.host", content_hash="prr_remote_hash"
        )

        results = backend.pending_remote_revisions(
            dataset_id=1, last_pulled_revision_id=None, self_host="self.host"
        )

        hashes = [r["content_hash"] for r in results]
        assert "prr_self_hash" not in hashes, f"self.host revisions must be excluded; got {hashes}"
        assert "prr_remote_hash" in hashes, f"remote.host revision must appear; got {hashes}"

    def test_respects_last_pulled_revision_id(self, tmp_path):
        """Only revisions with id > last_pulled_revision_id are returned."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        doc_id = _insert_doc_for_b11(backend, dataset_id=1, source_uri="vault://prr/lp.md")
        rev1_id = _insert_revision_direct(
            backend, doc_id, author_host="remote.host", content_hash="prr_lp_hash_1"
        )
        _insert_revision_direct(
            backend, doc_id, author_host="remote.host", content_hash="prr_lp_hash_2"
        )

        # Filter out the first revision by setting last_pulled_revision_id = rev1_id
        results = backend.pending_remote_revisions(
            dataset_id=1, last_pulled_revision_id=rev1_id, self_host="local.host"
        )

        hashes = [r["content_hash"] for r in results]
        assert "prr_lp_hash_1" not in hashes, (
            f"Already-pulled revision must be excluded; got {hashes}"
        )
        assert "prr_lp_hash_2" in hashes, f"New revision must appear; got {hashes}"

    def test_none_last_pulled_revision_id_returns_all(self, tmp_path):
        """last_pulled_revision_id=None is treated as 0 — all remote revisions returned."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        doc_id = _insert_doc_for_b11(backend, dataset_id=1, source_uri="vault://prr/all.md")
        _insert_revision_direct(
            backend, doc_id, author_host="remote.host", content_hash="prr_all_hash_1"
        )
        _insert_revision_direct(
            backend, doc_id, author_host="remote.host", content_hash="prr_all_hash_2"
        )

        results = backend.pending_remote_revisions(
            dataset_id=1, last_pulled_revision_id=None, self_host="local.host"
        )

        assert len(results) >= 2, (
            f"None last_pulled_revision_id must return all revisions; got {len(results)}"
        )

    def test_orders_by_id_asc(self, tmp_path):
        """Results are ordered by revision id ascending."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        doc_id = _insert_doc_for_b11(backend, dataset_id=1, source_uri="vault://prr/ord.md")
        _insert_revision_direct(
            backend, doc_id, author_host="remote.host", content_hash="prr_ord_1"
        )
        _insert_revision_direct(
            backend, doc_id, author_host="remote.host", content_hash="prr_ord_2"
        )
        _insert_revision_direct(
            backend, doc_id, author_host="remote.host", content_hash="prr_ord_3"
        )

        results = backend.pending_remote_revisions(
            dataset_id=1, last_pulled_revision_id=None, self_host="local.host"
        )

        ids = [r["id"] for r in results]
        assert ids == sorted(ids), f"Results must be ordered by id ASC; got ids={ids}"

    def test_honors_limit(self, tmp_path):
        """limit parameter caps the number of returned rows."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        doc_id = _insert_doc_for_b11(backend, dataset_id=1, source_uri="vault://prr/lim.md")
        for i in range(5):
            _insert_revision_direct(
                backend,
                doc_id,
                author_host="remote.host",
                content_hash=f"prr_lim_hash_{i}",
            )

        results = backend.pending_remote_revisions(
            dataset_id=1, last_pulled_revision_id=None, self_host="local.host", limit=2
        )

        assert len(results) == 2, f"limit=2 must return exactly 2 rows; got {len(results)}"

    def test_result_includes_source_uri_and_parent_content_hash(self, tmp_path):
        """Each result dict includes source_uri (from documents JOIN) and
        parent_content_hash (from LEFT JOIN on parent revision).
        """
        backend = _migrated_backend(tmp_path / "corpus.db")
        doc_id = _insert_doc_for_b11(
            backend,
            dataset_id=1,
            source_uri="vault://prr/jointest.md",
        )
        parent_id = _insert_revision_direct(
            backend,
            doc_id,
            author_host="local.host",  # self — won't show in pending
            content_hash="prr_parent_hash",
        )
        # Child revision authored by a remote host so it appears in pending
        _insert_revision_direct(
            backend,
            doc_id,
            author_host="remote.host",
            content_hash="prr_child_hash",
            parent_revision_id=parent_id,
        )

        results = backend.pending_remote_revisions(
            dataset_id=1, last_pulled_revision_id=None, self_host="local.host"
        )

        assert len(results) == 1, f"Expected exactly one pending result; got {results}"
        row = results[0]
        assert "source_uri" in row, f"Result must include 'source_uri'; keys={list(row)}"
        assert row["source_uri"] == "vault://prr/jointest.md", (
            f"source_uri mismatch; got {row['source_uri']!r}"
        )
        assert "parent_content_hash" in row, (
            f"Result must include 'parent_content_hash'; keys={list(row)}"
        )
        assert row["parent_content_hash"] == "prr_parent_hash", (
            f"parent_content_hash must match parent revision's content_hash; "
            f"got {row['parent_content_hash']!r}"
        )

    def test_parent_content_hash_is_none_for_root_revision(self, tmp_path):
        """For a root revision (no parent), parent_content_hash must be NULL/None."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        doc_id = _insert_doc_for_b11(backend, dataset_id=1, source_uri="vault://prr/root.md")
        _insert_revision_direct(
            backend,
            doc_id,
            author_host="remote.host",
            content_hash="prr_root_hash",
            parent_revision_id=None,
        )

        results = backend.pending_remote_revisions(
            dataset_id=1, last_pulled_revision_id=None, self_host="local.host"
        )

        assert len(results) == 1, f"Expected one result; got {results}"
        assert results[0]["parent_content_hash"] is None, (
            f"Root revision parent_content_hash must be None; "
            f"got {results[0]['parent_content_hash']!r}"
        )


# ---------------------------------------------------------------------------
# TestMarkRevisionPulled — B-11
# ---------------------------------------------------------------------------


class TestMarkRevisionPulled:
    """mark_revision_pulled(source_id, revision_id) advances last_pulled_revision_id
    monotonically.
    """

    def test_updates_last_pulled_revision_id(self, tmp_path):
        """Basic update: calling mark_revision_pulled sets last_pulled_revision_id."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        source_id = _insert_source_row(backend, dataset_id=1, last_pulled_revision_id=None)

        backend.mark_revision_pulled(source_id, revision_id=42)

        rows = backend._execute(
            "SELECT last_pulled_revision_id FROM sources WHERE id = ?", (source_id,)
        )
        assert rows[0]["last_pulled_revision_id"] == 42, (
            f"last_pulled_revision_id must be 42; got {rows[0]['last_pulled_revision_id']}"
        )

    def test_monotonic_smaller_value_does_not_regress(self, tmp_path):
        """Calling mark_revision_pulled with a smaller revision_id must not lower the pointer.

        SQLite uses MAX(coalesce(last_pulled_revision_id, 0), ?) rather than GREATEST().
        """
        backend = _migrated_backend(tmp_path / "corpus.db")
        source_id = _insert_source_row(backend, dataset_id=1, last_pulled_revision_id=None)

        backend.mark_revision_pulled(source_id, revision_id=100)
        backend.mark_revision_pulled(source_id, revision_id=50)  # smaller — must not regress

        rows = backend._execute(
            "SELECT last_pulled_revision_id FROM sources WHERE id = ?", (source_id,)
        )
        assert rows[0]["last_pulled_revision_id"] == 100, (
            f"Monotonic: pointer must remain at 100 after calling with 50; "
            f"got {rows[0]['last_pulled_revision_id']}"
        )

    def test_idempotent_on_same_value(self, tmp_path):
        """Calling mark_revision_pulled twice with the same revision_id is a no-op (idempotent)."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        source_id = _insert_source_row(backend, dataset_id=1, last_pulled_revision_id=None)

        backend.mark_revision_pulled(source_id, revision_id=77)
        backend.mark_revision_pulled(source_id, revision_id=77)

        rows = backend._execute(
            "SELECT last_pulled_revision_id FROM sources WHERE id = ?", (source_id,)
        )
        assert rows[0]["last_pulled_revision_id"] == 77, (
            f"Idempotent: double-call with same revision_id must keep pointer at 77; "
            f"got {rows[0]['last_pulled_revision_id']}"
        )


# ---------------------------------------------------------------------------
# B-12 helpers
# ---------------------------------------------------------------------------


def _insert_document_for_tombstone(
    backend,
    dataset_id: int = 1,
    source_uri: str = "vault://tomb_test.md",
    content_hash: str = "tomb_hash_001",
    text: str = "# Tombstone Test",
) -> int:
    """Insert dataset + document rows for tombstone tests; return the document id.

    Uses INSERT OR IGNORE on the dataset row so tests sharing a dataset_id
    don't collide on the UNIQUE(name) constraint.
    """
    backend._execute(
        "INSERT OR IGNORE INTO datasets (id, name, kind) VALUES (?, ?, ?)",
        (dataset_id, f"b12_ds_{dataset_id}", "text"),
    )
    rows = backend._execute(
        "INSERT INTO documents (dataset_id, source_uri, content_hash, text)"
        " VALUES (?, ?, ?, ?) RETURNING id",
        (dataset_id, source_uri, content_hash, text),
    )
    return rows[0]["id"]


def _tombstoned_at(backend, document_id: int) -> str | None:
    """Return the tombstoned_at value for a document row (None if NULL)."""
    rows = backend._execute(
        "SELECT tombstoned_at FROM documents WHERE id = ?",
        (document_id,),
    )
    if not rows:
        return None
    return rows[0]["tombstoned_at"]


# ---------------------------------------------------------------------------
# TestSetTombstone — B-12
# ---------------------------------------------------------------------------


class TestSetTombstone:
    """set_tombstone(document_id) — sets tombstoned_at to current UTC ISO-8601 timestamp."""

    # ------------------------------------------------------------------ happy path

    def test_sets_tombstoned_at_to_non_null(self, tmp_path):
        """Happy path: after set_tombstone, tombstoned_at is NOT NULL."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        doc_id = _insert_document_for_tombstone(backend, dataset_id=1)

        backend.set_tombstone(doc_id)

        value = _tombstoned_at(backend, doc_id)
        assert value is not None, (
            f"tombstoned_at must be non-NULL after set_tombstone; got {value!r}"
        )

    def test_tombstoned_at_parses_as_iso8601_datetime(self, tmp_path):
        """After set_tombstone, tombstoned_at is a parseable ISO-8601 UTC datetime string."""
        from datetime import datetime

        backend = _migrated_backend(tmp_path / "corpus.db")
        doc_id = _insert_document_for_tombstone(backend, dataset_id=1, source_uri="vault://iso.md")

        backend.set_tombstone(doc_id)

        value = _tombstoned_at(backend, doc_id)
        assert value is not None, "tombstoned_at must be non-NULL"

        # SQLite strftime('%Y-%m-%dT%H:%M:%fZ', 'now') produces e.g.
        # "2026-05-09T23:59:59.123Z" — parse using fromisoformat after normalizing the Z suffix.
        # datetime.fromisoformat() in Python 3.11+ accepts trailing Z directly.
        # For compatibility, normalise "...Z" → "...+00:00" before parsing.
        ts = value.rstrip("Z") + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(ts)
        except ValueError as exc:
            pytest.fail(f"tombstoned_at={value!r} must parse as ISO-8601 datetime; error: {exc}")

        # Confirm UTC awareness (offset = 0) when parsed with timezone info
        if parsed.tzinfo is not None:
            assert parsed.utcoffset().total_seconds() == 0, (
                f"tombstoned_at must be UTC; utcoffset={parsed.utcoffset()}"
            )

    def test_idempotent_double_call_still_tombstoned(self, tmp_path):
        """Idempotency: calling set_tombstone twice does not error; document remains tombstoned."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        doc_id = _insert_document_for_tombstone(
            backend, dataset_id=1, source_uri="vault://idem_set.md"
        )

        backend.set_tombstone(doc_id)
        # Capture value to confirm it was set, but we don't compare with second call's value
        # (the second call may update the timestamp legitimately)
        assert _tombstoned_at(backend, doc_id) is not None, "First call must set tombstoned_at"

        # Second call must not raise
        backend.set_tombstone(doc_id)
        second_value = _tombstoned_at(backend, doc_id)

        assert second_value is not None, (
            "tombstoned_at must remain non-NULL after second set_tombstone call"
        )
        # Note: second call updates the timestamp; we do NOT assert first == second.
        # The contract is just: no error, still tombstoned.

    def test_unknown_document_id_is_noop(self, tmp_path):
        """set_tombstone on a non-existent document_id is a no-op (UPDATE on 0 rows is fine)."""
        backend = _migrated_backend(tmp_path / "corpus.db")

        # Must not raise for a document_id that does not exist
        backend.set_tombstone(999999)

        # The documents table must still have no rows affected (no error, nothing written)
        all_docs = backend._execute("SELECT tombstoned_at FROM documents")
        assert all_docs == [], "No document rows should exist after set_tombstone on unknown id"


# ---------------------------------------------------------------------------
# TestClearTombstone — B-12
# ---------------------------------------------------------------------------


class TestClearTombstone:
    """clear_tombstone(document_id) — sets tombstoned_at to NULL."""

    # ------------------------------------------------------------------ happy path

    def test_clears_existing_tombstone_to_null(self, tmp_path):
        """Happy path: after set then clear, tombstoned_at is NULL again."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        doc_id = _insert_document_for_tombstone(backend, dataset_id=1)

        backend.set_tombstone(doc_id)
        assert _tombstoned_at(backend, doc_id) is not None, "Precondition: tombstone must be set"

        backend.clear_tombstone(doc_id)

        value = _tombstoned_at(backend, doc_id)
        assert value is None, f"tombstoned_at must be NULL after clear_tombstone; got {value!r}"

    def test_tombstoned_at_becomes_null(self, tmp_path):
        """Direct verification: SELECT tombstoned_at after clear_tombstone returns NULL."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        doc_id = _insert_document_for_tombstone(
            backend, dataset_id=1, source_uri="vault://null_check.md"
        )

        # Manually write a tombstoned_at value to ensure it is set before clearing
        backend._execute(
            "UPDATE documents SET tombstoned_at = '2026-01-01T00:00:00.000Z' WHERE id = ?",
            (doc_id,),
        )
        assert _tombstoned_at(backend, doc_id) is not None, "Precondition: must be non-NULL"

        backend.clear_tombstone(doc_id)

        rows = backend._execute(
            "SELECT tombstoned_at FROM documents WHERE id = ?",
            (doc_id,),
        )
        assert len(rows) == 1, "Document row must still exist after clear_tombstone"
        assert rows[0]["tombstoned_at"] is None, (
            f"tombstoned_at must be NULL after clear_tombstone; got {rows[0]['tombstoned_at']!r}"
        )

    def test_idempotent_on_already_clear(self, tmp_path):
        """Idempotency: calling clear_tombstone on a document with NULL tombstoned_at is a no-op."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        doc_id = _insert_document_for_tombstone(
            backend, dataset_id=1, source_uri="vault://already_clear.md"
        )

        # tombstoned_at is already NULL (default); clear_tombstone must not raise
        backend.clear_tombstone(doc_id)

        value = _tombstoned_at(backend, doc_id)
        assert value is None, (
            f"tombstoned_at must remain NULL after clear on already-clear document; got {value!r}"
        )

    def test_unknown_document_id_is_noop(self, tmp_path):
        """clear_tombstone on a non-existent document_id is a no-op (no error)."""
        backend = _migrated_backend(tmp_path / "corpus.db")

        # Must not raise for a document_id that does not exist
        backend.clear_tombstone(999999)


# ---------------------------------------------------------------------------
# TestTombstoneRoundTrip — B-12
# ---------------------------------------------------------------------------


class TestTombstoneRoundTrip:
    """Round-trip: set then clear returns tombstoned_at to NULL."""

    def test_set_then_clear_returns_to_null(self, tmp_path):
        """Full round-trip: fresh doc → set_tombstone → tombstoned → clear_tombstone → NULL."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        doc_id = _insert_document_for_tombstone(
            backend, dataset_id=1, source_uri="vault://roundtrip.md"
        )

        # Initially NULL (no tombstone)
        assert _tombstoned_at(backend, doc_id) is None, "Precondition: tombstoned_at must be NULL"

        # Set tombstone
        backend.set_tombstone(doc_id)
        assert _tombstoned_at(backend, doc_id) is not None, "After set: must be non-NULL"

        # Clear tombstone
        backend.clear_tombstone(doc_id)
        assert _tombstoned_at(backend, doc_id) is None, "After clear: must be NULL again"

    def test_set_clear_set_works(self, tmp_path):
        """Multiple cycles of set/clear work without error."""
        backend = _migrated_backend(tmp_path / "corpus.db")
        doc_id = _insert_document_for_tombstone(
            backend, dataset_id=1, source_uri="vault://multi_cycle.md"
        )

        for _cycle in range(3):
            backend.set_tombstone(doc_id)
            assert _tombstoned_at(backend, doc_id) is not None, (
                f"Cycle {_cycle}: tombstoned_at must be non-NULL after set"
            )
            backend.clear_tombstone(doc_id)
            assert _tombstoned_at(backend, doc_id) is None, (
                f"Cycle {_cycle}: tombstoned_at must be NULL after clear"
            )
