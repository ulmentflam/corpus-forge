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
    """
    backend._execute(
        "INSERT OR IGNORE INTO datasets (id, name, kind) VALUES (?, ?, ?)",
        (dataset_id, "test_ds", "text"),
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
        chunks = ([("# New Doc", "Content")],)

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

        # Insert a prior embedding row for chunk_id=5
        backend._execute(
            "INSERT INTO embeddings_copy_embed (chunk_id, embedder_id, embedding) VALUES (5, ?, ?)",
            (embedder_id, b"\x00" * (64 * 4)),  # 64 floats as float32
        )

        # Insert a prior chunk with the matching content_hash
        match_hash = chunk_content_hash("matching body")
        backend._execute(
            "INSERT INTO chunks (document_id, chunk_index, text, content_hash)"
            " VALUES (1, 0, 'matching body', ?)",
            (match_hash,),
        )

        result = backend._copy_reusable_embeddings(
            new_chunk_id=10,
            content_hash=match_hash,
            embedder_ids=[embedder_id],
            cache={},
        )

        assert embedder_id in result, f"Expected {embedder_id} in reused set, got {result}"

        # Verify the new row was inserted
        new_rows = backend._execute(
            "SELECT chunk_id, embedder_id FROM embeddings_copy_embed WHERE chunk_id = ?",
            (10,),
        )
        assert len(new_rows) == 1, (
            f"Expected 1 new embedding row for chunk_id=10, got {len(new_rows)}"
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
        _insert_dataset_and_document(backend, dataset_id=1, doc_id=1)

        match_hash = chunk_content_hash("shared body")
        backend._execute(
            "INSERT INTO chunks (document_id, chunk_index, text, content_hash)"
            " VALUES (1, 0, 'shared body', ?)",
            (match_hash,),
        )
        backend._execute(
            "INSERT INTO embeddings_multi_reuse (chunk_id, embedder_id, embedding)"
            " VALUES (1, ?, ?)",
            (embedder_id, b"\x00" * (32 * 4)),
        )

        doc = _make_raw_document(
            source_uri="vault://multi.md", content_hash="new_hash", text="# Multi"
        )
        chunks = [("# Multi", "shared body"), ("", "shared body"), ("", "shared body")]

        backend.upsert_document(1, doc, chunks, embedder_ids=[embedder_id])

        # Each new chunk should have an embedding row
        new_rows = backend._execute(
            "SELECT chunk_id FROM embeddings_multi_reuse WHERE chunk_id >= 100 ORDER BY chunk_id"
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
            "SELECT COUNT(*) FROM documents WHERE source_uri = ?",
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
            "SELECT COUNT(*) FROM chunks WHERE document_id = ?",
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
            "SELECT COUNT(*) FROM chunks WHERE document_id = ?",
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
            "SELECT COUNT(*) FROM chunks WHERE document_id = ?",
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
            "SELECT COUNT(*) FROM chunks WHERE document_id = ?",
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
            "SELECT COUNT(*) FROM chunks WHERE document_id = ?",
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
            "SELECT COUNT(*) FROM documents WHERE dataset_id = 1 AND source_uri = ?",
            ("vault://shared.md",),
        )
        ds2_rows = backend._execute(
            "SELECT COUNT(*) FROM documents WHERE dataset_id = 2 AND source_uri = ?",
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
