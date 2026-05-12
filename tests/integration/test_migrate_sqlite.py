"""Integration tests for SQLiteBackend schema creation and idempotency.

B-15 — characterization tests.  Mirrors the spirit of test_migrate_002.py and
test_migrate_003.py but exercises the SQLite dialect.  No Docker required —
sqlite3 is in Python stdlib.

Schema introspection uses:
- ``SELECT name FROM sqlite_master WHERE type='table'`` for table enumeration.
- ``PRAGMA table_info(<table>)`` for column checks.
- ``PRAGMA foreign_key_list(<table>)`` for FK checks.
- ``PRAGMA foreign_keys`` to verify the per-connection FK enforcement flag.
"""

import sqlite3
from pathlib import Path

import pytest

from corpus_forge.backends.sqlite import SQLiteBackend
from corpus_forge.schema.migrate import apply_migrations, get_migration_files

# ---------------------------------------------------------------------------
# No pytestmark = pytest.mark.integration — these run on any machine with
# sqlite3 (stdlib), no Docker needed.  The conftest's
# pytest_collection_modifyitems hook does skip tests.integration.* when
# Docker is absent, so we rely on Docker being present in CI.  Locally
# the tests can also be run as: PYTHONPATH=. uv run pytest <this file> -v
# ---------------------------------------------------------------------------

# The schema root (parent of the sqlite/ subdir).
# get_migration_files(schema_dir, dialect="sqlite") appends "/sqlite" internally,
# so pass the parent here — NOT the sqlite/ subdir directly.
_SQLITE_SCHEMA_DIR = Path(__file__).resolve().parents[2] / "corpus_forge" / "schema"


def _make_backend(db_path: Path) -> SQLiteBackend:
    """Create a fully-migrated SQLiteBackend at *db_path*."""
    backend = SQLiteBackend(path=str(db_path))
    backend.migrate()
    return backend


def _tables(db_path: Path) -> set[str]:
    """Return the set of user-visible table names in the database."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def _columns(db_path: Path, table: str) -> set[str]:
    """Return the set of column names for *table* via PRAGMA table_info."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        # PRAGMA table_info: (cid, name, type, notnull, dflt_value, pk)
        return {r[1] for r in rows}
    finally:
        conn.close()


def _fk_list(db_path: Path, table: str) -> list[dict]:
    """Return foreign-key descriptors for *table* via PRAGMA foreign_key_list."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
        # (id, seq, table, from, to, on_update, on_delete, match)
        return [
            {
                "id": r[0],
                "seq": r[1],
                "ref_table": r[2],
                "from_col": r[3],
                "to_col": r[4],
                "on_update": r[5],
                "on_delete": r[6],
            }
            for r in rows
        ]
    finally:
        conn.close()


def _indexes(db_path: Path, table: str | None = None) -> set[str]:
    """Return index names from sqlite_master, optionally filtered by table."""
    conn = sqlite3.connect(str(db_path))
    try:
        if table:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name = ?",
                (table,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# TestMigrateSQLiteSchema — basic table + column presence
# ---------------------------------------------------------------------------


class TestMigrateSQLiteSchema:
    """Verify that migrate() creates all expected tables and columns."""

    def test_all_core_tables_exist(self, tmp_path: Path) -> None:
        """After migrate(), all 12 user-visible tables are present."""
        db_path = tmp_path / "corpus.db"
        _make_backend(db_path)

        tables = _tables(db_path)
        expected = {
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
        }
        assert expected.issubset(tables), f"Missing tables: {expected - tables}"

    def test_datasets_columns(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        _make_backend(db_path)
        cols = _columns(db_path, "datasets")
        assert {"id", "name", "kind", "created_at"}.issubset(cols)

    def test_sources_columns(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        _make_backend(db_path)
        cols = _columns(db_path, "sources")
        expected = {
            "id",
            "dataset_id",
            "plugin",
            "identity",
            "host",
            "config",
            "last_seen_at",
            "last_pulled_revision_id",
            "sync_enabled",
        }
        assert expected.issubset(cols), f"Missing columns: {expected - cols}"

    def test_documents_columns(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        _make_backend(db_path)
        cols = _columns(db_path, "documents")
        expected = {
            "id",
            "dataset_id",
            "source_uri",
            "content_hash",
            "title",
            "text",
            "modified_at",
            "metadata",
            "tombstoned_at",
        }
        assert expected.issubset(cols), f"Missing columns: {expected - cols}"

    def test_chunks_has_content_hash_column(self, tmp_path: Path) -> None:
        """002 migration adds content_hash to chunks."""
        db_path = tmp_path / "corpus.db"
        _make_backend(db_path)
        cols = _columns(db_path, "chunks")
        assert "content_hash" in cols

    def test_document_revisions_columns(self, tmp_path: Path) -> None:
        """003 migration adds document_revisions table with all required columns."""
        db_path = tmp_path / "corpus.db"
        _make_backend(db_path)
        cols = _columns(db_path, "document_revisions")
        expected = {
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
        }
        assert expected.issubset(cols), f"Missing columns: {expected - cols}"

    def test_tombstoned_at_column_on_documents(self, tmp_path: Path) -> None:
        """003 migration adds tombstoned_at to documents."""
        db_path = tmp_path / "corpus.db"
        _make_backend(db_path)
        cols = _columns(db_path, "documents")
        assert "tombstoned_at" in cols

    def test_sources_last_pulled_revision_id(self, tmp_path: Path) -> None:
        """003 migration adds last_pulled_revision_id to sources."""
        db_path = tmp_path / "corpus.db"
        _make_backend(db_path)
        cols = _columns(db_path, "sources")
        assert "last_pulled_revision_id" in cols

    def test_sources_sync_enabled(self, tmp_path: Path) -> None:
        """003 migration adds sync_enabled to sources."""
        db_path = tmp_path / "corpus.db"
        _make_backend(db_path)
        cols = _columns(db_path, "sources")
        assert "sync_enabled" in cols


# ---------------------------------------------------------------------------
# TestMigrateSQLiteIndexes — index presence
# ---------------------------------------------------------------------------


class TestMigrateSQLiteIndexes:
    """Verify that expected indexes are created."""

    def test_documents_hash_idx(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        _make_backend(db_path)
        idxs = _indexes(db_path, "documents")
        assert "documents_hash_idx" in idxs

    def test_chunks_doc_idx(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        _make_backend(db_path)
        idxs = _indexes(db_path, "chunks")
        assert "chunks_doc_idx" in idxs

    def test_chunks_content_hash_idx(self, tmp_path: Path) -> None:
        """002 migration adds chunks_content_hash_idx."""
        db_path = tmp_path / "corpus.db"
        _make_backend(db_path)
        idxs = _indexes(db_path, "chunks")
        assert "chunks_content_hash_idx" in idxs

    def test_document_revisions_doc_idx(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        _make_backend(db_path)
        idxs = _indexes(db_path, "document_revisions")
        assert any("doc_idx" in n for n in idxs), f"No doc_idx found in {idxs}"

    def test_document_revisions_parent_idx(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        _make_backend(db_path)
        idxs = _indexes(db_path, "document_revisions")
        assert any("parent_idx" in n for n in idxs), f"No parent_idx found in {idxs}"


# ---------------------------------------------------------------------------
# TestMigrateSQLiteForeignKeys — FK declarations via PRAGMA
# ---------------------------------------------------------------------------


class TestMigrateSQLiteForeignKeys:
    """Verify FK declarations using PRAGMA foreign_key_list."""

    def test_sources_fk_to_datasets(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        _make_backend(db_path)
        fks = _fk_list(db_path, "sources")
        ref_tables = {fk["ref_table"] for fk in fks}
        assert "datasets" in ref_tables

    def test_documents_fk_to_datasets(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        _make_backend(db_path)
        fks = _fk_list(db_path, "documents")
        ref_tables = {fk["ref_table"] for fk in fks}
        assert "datasets" in ref_tables

    def test_chunks_fk_to_documents(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        _make_backend(db_path)
        fks = _fk_list(db_path, "chunks")
        ref_tables = {fk["ref_table"] for fk in fks}
        assert "documents" in ref_tables

    def test_document_revisions_fk_to_documents(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        _make_backend(db_path)
        fks = _fk_list(db_path, "document_revisions")
        ref_tables = {fk["ref_table"] for fk in fks}
        assert "documents" in ref_tables

    def test_document_revisions_self_ref_fk(self, tmp_path: Path) -> None:
        """parent_revision_id references document_revisions itself."""
        db_path = tmp_path / "corpus.db"
        _make_backend(db_path)
        fks = _fk_list(db_path, "document_revisions")
        ref_tables = {fk["ref_table"] for fk in fks}
        assert "document_revisions" in ref_tables

    def test_messages_fk_to_conversations(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        _make_backend(db_path)
        fks = _fk_list(db_path, "messages")
        ref_tables = {fk["ref_table"] for fk in fks}
        assert "conversations" in ref_tables

    def test_chunk_labels_fks(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        _make_backend(db_path)
        fks = _fk_list(db_path, "chunk_labels")
        ref_tables = {fk["ref_table"] for fk in fks}
        assert "chunks" in ref_tables
        assert "labels" in ref_tables


# ---------------------------------------------------------------------------
# TestMigrateSQLiteFKEnforcement — PRAGMA foreign_keys = ON per connection
# ---------------------------------------------------------------------------


class TestMigrateSQLiteFKEnforcement:
    """Verify that backend connections have foreign_keys enabled."""

    def test_backend_connection_has_foreign_keys_on(self, tmp_path: Path) -> None:
        """Connections opened by _get_connection() have PRAGMA foreign_keys = ON."""
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        with backend._get_connection() as conn:
            fk = conn.execute("PRAGMA foreign_keys").fetchone()
            assert fk[0] == 1, "PRAGMA foreign_keys is OFF on backend connection"

    def test_raw_connection_has_foreign_keys_off_by_default(self, tmp_path: Path) -> None:
        """Direct sqlite3.connect() does NOT enable FK enforcement — backend must do it."""
        db_path = tmp_path / "corpus.db"
        _make_backend(db_path)
        raw = sqlite3.connect(str(db_path))
        try:
            fk = raw.execute("PRAGMA foreign_keys").fetchone()
            assert fk[0] == 0, "Expected raw connection to have foreign_keys OFF"
        finally:
            raw.close()

    def test_fk_violation_rejected_by_backend(self, tmp_path: Path) -> None:
        """Insert into document_revisions with bogus document_id raises OperationalError."""
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        with pytest.raises(sqlite3.IntegrityError):
            backend._execute(
                "INSERT INTO document_revisions"
                " (document_id, revision_number, content_hash, text, author_host)"
                " VALUES (99999, 1, 'abc', 'text', 'host1')"
            )


# ---------------------------------------------------------------------------
# TestMigrateSQLiteIdempotency — safe to call migrate() twice
# ---------------------------------------------------------------------------


class TestMigrateSQLiteIdempotency:
    """migrate() is safe to call multiple times — no duplicate-table errors."""

    def test_migrate_twice_does_not_raise(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = SQLiteBackend(path=str(db_path))
        backend.migrate()
        backend.migrate()  # must not raise

    def test_tables_still_present_after_second_migrate(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = SQLiteBackend(path=str(db_path))
        backend.migrate()
        backend.migrate()

        tables = _tables(db_path)
        assert "document_revisions" in tables
        assert "documents" in tables
        assert "chunks" in tables

    def test_apply_migrations_explicitly_idempotent(self, tmp_path: Path) -> None:
        """apply_migrations() called directly twice is also idempotent."""
        db_path = tmp_path / "corpus.db"
        backend = SQLiteBackend(path=str(db_path))
        apply_migrations(backend, _SQLITE_SCHEMA_DIR, dialect="sqlite")
        apply_migrations(backend, _SQLITE_SCHEMA_DIR, dialect="sqlite")  # no error


# ---------------------------------------------------------------------------
# TestMigrateSQLiteFileOrder — migration files applied 001 → 002 → 003
# ---------------------------------------------------------------------------


class TestMigrateSQLiteFileOrder:
    """Migration runner loads files in numeric order."""

    def test_get_migration_files_includes_all_three(self) -> None:
        # Pass the schema root + dialect="sqlite"; the function appends "/sqlite" internally.
        files = get_migration_files(_SQLITE_SCHEMA_DIR, dialect="sqlite")
        names = [f.name for f in files]
        assert "001_core.sql" in names
        assert "002_chunk_content_hash.sql" in names
        assert "003_sync.sql" in names

    def test_migration_files_numeric_order(self) -> None:
        files = get_migration_files(_SQLITE_SCHEMA_DIR, dialect="sqlite")
        names = [f.name for f in files]
        idx_001 = names.index("001_core.sql")
        idx_002 = names.index("002_chunk_content_hash.sql")
        idx_003 = names.index("003_sync.sql")
        assert idx_001 < idx_002 < idx_003

    def test_002_columns_present_from_second_file(self, tmp_path: Path) -> None:
        """content_hash column (added by 002) is present after full migrate."""
        db_path = tmp_path / "corpus.db"
        _make_backend(db_path)
        cols = _columns(db_path, "chunks")
        assert "content_hash" in cols

    def test_003_table_present_from_third_file(self, tmp_path: Path) -> None:
        """document_revisions (added by 003) is present after full migrate."""
        db_path = tmp_path / "corpus.db"
        _make_backend(db_path)
        tables = _tables(db_path)
        assert "document_revisions" in tables


# ---------------------------------------------------------------------------
# TestMigrateSQLiteConstraints — unique + FK constraints fire correctly
# ---------------------------------------------------------------------------


class TestMigrateSQLiteConstraints:
    """Constraint violations produce IntegrityError as expected."""

    def test_insert_valid_revision_succeeds(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)

        backend._execute("INSERT INTO datasets (name, kind) VALUES ('ds_valid_rev', 'text')")
        ds_rows = backend._execute("SELECT id FROM datasets WHERE name = 'ds_valid_rev'")
        ds_id = ds_rows[0]["id"]

        backend._execute(
            "INSERT INTO documents (dataset_id, source_uri, content_hash, text)"
            " VALUES (?, 'test://rev_ok', 'h1', 'hello')",
            (ds_id,),
        )
        doc_rows = backend._execute("SELECT id FROM documents WHERE source_uri = 'test://rev_ok'")
        doc_id = doc_rows[0]["id"]

        backend._execute(
            "INSERT INTO document_revisions"
            " (document_id, revision_number, content_hash, text, author_host)"
            " VALUES (?, 1, 'abc', 'rev text', 'host1')",
            (doc_id,),
        )
        rev_rows = backend._execute(
            "SELECT id FROM document_revisions WHERE document_id = ?",
            (doc_id,),
        )
        assert rev_rows, "Expected revision row to exist"

    def test_fk_rejects_invalid_document_id(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        with pytest.raises(sqlite3.IntegrityError):
            backend._execute(
                "INSERT INTO document_revisions"
                " (document_id, revision_number, content_hash, text, author_host)"
                " VALUES (99999, 1, 'abc', 'text', 'host1')"
            )

    def test_unique_revision_number_per_document(self, tmp_path: Path) -> None:
        """Same (document_id, revision_number) pair raises IntegrityError."""
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)

        backend._execute("INSERT INTO datasets (name, kind) VALUES ('ds_uniq_rev', 'text')")
        ds_rows = backend._execute("SELECT id FROM datasets WHERE name = 'ds_uniq_rev'")
        ds_id = ds_rows[0]["id"]

        backend._execute(
            "INSERT INTO documents (dataset_id, source_uri, content_hash, text)"
            " VALUES (?, 'test://uniq', 'h2', 'content')",
            (ds_id,),
        )
        doc_rows = backend._execute("SELECT id FROM documents WHERE source_uri = 'test://uniq'")
        doc_id = doc_rows[0]["id"]

        backend._execute(
            "INSERT INTO document_revisions"
            " (document_id, revision_number, content_hash, text, author_host)"
            " VALUES (?, 1, 'abc', 'first', 'host1')",
            (doc_id,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            backend._execute(
                "INSERT INTO document_revisions"
                " (document_id, revision_number, content_hash, text, author_host)"
                " VALUES (?, 1, 'def', 'second', 'host2')",
                (doc_id,),
            )

    def test_datasets_name_unique_constraint(self, tmp_path: Path) -> None:
        """datasets.name has UNIQUE constraint."""
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        backend._execute("INSERT INTO datasets (name, kind) VALUES ('unique_ds', 'text')")
        with pytest.raises(sqlite3.IntegrityError):
            backend._execute("INSERT INTO datasets (name, kind) VALUES ('unique_ds', 'text')")

    def test_documents_dataset_source_unique_constraint(self, tmp_path: Path) -> None:
        """documents(dataset_id, source_uri) is UNIQUE."""
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        backend._execute("INSERT INTO datasets (name, kind) VALUES ('ds_docuniq', 'text')")
        ds_rows = backend._execute("SELECT id FROM datasets WHERE name = 'ds_docuniq'")
        ds_id = ds_rows[0]["id"]

        backend._execute(
            "INSERT INTO documents (dataset_id, source_uri, content_hash, text)"
            " VALUES (?, 'test://dup', 'h1', 'content')",
            (ds_id,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            backend._execute(
                "INSERT INTO documents (dataset_id, source_uri, content_hash, text)"
                " VALUES (?, 'test://dup', 'h2', 'content2')",
                (ds_id,),
            )
