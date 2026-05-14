"""Tests for corpus_forge/schema/sqlite/003_sync.sql — B-02.

Asserts that the SQLite translation of the Postgres 003_sync.sql migration:
- Exists at the expected path.
- Creates the document_revisions table with correct column types (SQLite-compatible).
- Adds tombstoned_at to documents (TEXT, not TIMESTAMPTZ).
- Adds last_pulled_revision_id and sync_enabled to sources.
- Uses unqualified table names (no 'corpus.' schema prefix).
- Has all IF NOT EXISTS guards preserved.
- Has no Postgres-specific types (BIGSERIAL, JSONB, TIMESTAMPTZ, ::jsonb casts).
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.skip(
    reason="legacy migration test — pins pre-Alembic file-globbing; deleted in D-10"
)

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "corpus_forge" / "schema"
SQLITE_SCHEMA_DIR = SCHEMA_DIR / "sqlite"
MIGRATION_FILE = SQLITE_SCHEMA_DIR / "003_sync.sql"


# ── File existence & naming ──────────────────────────────────────────────


class TestFileExists:
    """The SQLite migration file must exist at the expected path."""

    def test_file_exists(self):
        """Happy path: migration file is present."""
        assert MIGRATION_FILE.exists(), f"Expected SQLite migration file at {MIGRATION_FILE}"

    def test_file_has_sql_extension(self):
        """Naming: file ends with .sql."""
        assert MIGRATION_FILE.suffix == ".sql"

    def test_file_matches_naming_convention(self):
        """Naming: starts with a 3-digit zero-padded number followed by _."""
        stem = MIGRATION_FILE.stem  # '003_sync'
        number_part = stem.split("_")[0]
        assert number_part.isdigit(), (
            f"Migration filename stem '{stem}' does not start with a digit"
        )
        assert len(number_part) == 3, f"Migration number '{number_part}' is not 3 digits"

    def test_file_ordering_after_002(self):
        """Ordering: numeric prefix must be > 002."""
        stem = MIGRATION_FILE.stem
        number_part = int(stem.split("_")[0])
        assert number_part > 2, (
            f"Migration number {number_part} must be > 2 (after 002_chunk_content_hash)"
        )


# ── SQLite dialect: no Postgres-only constructs ──────────────────────────


class TestSQLiteDialect:
    """Verify Postgres-specific constructs do not appear in the SQLite migration."""

    @pytest.fixture(autouse=True)
    def sql(self):
        return MIGRATION_FILE.read_text()

    def test_no_bigserial(self, sql):
        """No BIGSERIAL — SQLite uses INTEGER PRIMARY KEY AUTOINCREMENT."""
        assert "BIGSERIAL" not in sql.upper()

    def test_no_jsonb_type(self, sql):
        """No JSONB type — SQLite uses TEXT for JSON."""
        assert "JSONB" not in sql.upper()

    def test_no_timestamptz(self, sql):
        """No TIMESTAMPTZ — SQLite stores timestamps as TEXT (ISO-8601 UTC)."""
        assert "TIMESTAMPTZ" not in sql.upper()

    def test_no_jsonb_cast(self, sql):
        """No ::jsonb Postgres cast syntax."""
        assert "::jsonb" not in sql.lower()

    def test_no_schema_qualified_table_names(self, sql):
        """No corpus.-prefixed table names — SQLite has no schemas."""
        lower = sql.lower()
        assert "corpus.document_revisions" not in lower, (
            "Use unqualified 'document_revisions', not 'corpus.document_revisions'"
        )
        assert "corpus.documents" not in lower, (
            "Use unqualified 'documents', not 'corpus.documents'"
        )
        assert "corpus.sources" not in lower, "Use unqualified 'sources', not 'corpus.sources'"

    def test_no_bigint_for_pk(self, sql):
        """Primary key must not use BIGINT — SQLite PK is INTEGER."""
        # The id column is the primary key; assert that INTEGER PRIMARY KEY AUTOINCREMENT appears
        assert "INTEGER PRIMARY KEY AUTOINCREMENT" in sql.upper(), (
            "document_revisions.id must use INTEGER PRIMARY KEY AUTOINCREMENT"
        )

    def test_now_function_replaced(self, sql):
        """SQLite has no NOW() — use strftime or CURRENT_TIMESTAMP instead."""
        assert "NOW()" not in sql.upper(), (
            "NOW() is Postgres-only; use strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "or CURRENT_TIMESTAMP for SQLite timestamps"
        )


# ── document_revisions table ─────────────────────────────────────────────


class TestDocumentRevisionsTable:
    """Validate the document_revisions table DDL."""

    @pytest.fixture(autouse=True)
    def sql(self):
        return MIGRATION_FILE.read_text()

    def test_creates_document_revisions_table(self, sql):
        """DDL: CREATE TABLE IF NOT EXISTS document_revisions."""
        assert "CREATE TABLE IF NOT EXISTS document_revisions" in sql, (
            "Expected 'CREATE TABLE IF NOT EXISTS document_revisions' (unqualified)"
        )

    def test_id_column_is_integer_pk_autoincrement(self, sql):
        """Column: id INTEGER PRIMARY KEY AUTOINCREMENT."""
        upper = sql.upper()
        assert "INTEGER PRIMARY KEY AUTOINCREMENT" in upper

    def test_document_id_column(self, sql):
        """Column: document_id INTEGER NOT NULL REFERENCES documents(id)."""
        assert "document_id" in sql.lower()
        assert "REFERENCES documents(id)" in sql

    def test_revision_number_column(self, sql):
        """Column: revision_number INTEGER NOT NULL."""
        lower = sql.lower()
        assert "revision_number" in lower

    def test_parent_revision_id_fk(self, sql):
        """Column: parent_revision_id references document_revisions(id)."""
        assert "parent_revision_id" in sql.lower()
        assert "REFERENCES document_revisions(id)" in sql

    def test_content_hash_column(self, sql):
        """Column: content_hash TEXT NOT NULL."""
        assert "content_hash" in sql.lower()
        assert "TEXT NOT NULL" in sql

    def test_text_column(self, sql):
        """Column: text TEXT NOT NULL DEFAULT ''."""
        lower = sql.lower()
        assert "text" in lower
        assert "DEFAULT ''" in sql

    def test_author_host_column(self, sql):
        """Column: author_host TEXT NOT NULL."""
        assert "author_host" in sql.lower()

    def test_is_tombstone_column(self, sql):
        """Column: is_tombstone INTEGER NOT NULL DEFAULT 0 (SQLite uses 0/1 for booleans)."""
        lower = sql.lower()
        assert "is_tombstone" in lower
        # SQLite booleans are typically INTEGER 0/1
        # Accept either INTEGER or BOOLEAN in the column definition
        upper = sql.upper()
        assert "IS_TOMBSTONE" in upper.replace("is_tombstone", "IS_TOMBSTONE")

    def test_metadata_column_is_text(self, sql):
        """Column: metadata TEXT NOT NULL (JSON stored as TEXT in SQLite)."""
        assert "metadata" in sql.lower()
        # Must be TEXT (not JSONB)
        assert "metadata" in sql.lower()
        # JSONB already checked to be absent by TestSQLiteDialect

    def test_created_at_column_is_text(self, sql):
        """Column: created_at TEXT NOT NULL (ISO-8601 UTC timestamp as TEXT)."""
        assert "created_at" in sql.lower()
        # Must not be TIMESTAMPTZ (already checked); should be TEXT
        # We only assert the column exists; type assertion handled by no-TIMESTAMPTZ check

    def test_unique_constraint_document_id_revision_number(self, sql):
        """Constraint: UNIQUE (document_id, revision_number)."""
        assert "UNIQUE (document_id, revision_number)" in sql, (
            "Expected UNIQUE (document_id, revision_number) constraint on document_revisions"
        )

    def test_document_id_on_delete_cascade(self, sql):
        """FK: document_id → documents(id) ON DELETE CASCADE."""
        assert "ON DELETE CASCADE" in sql.upper()


# ── document_revisions indexes ───────────────────────────────────────────


class TestDocumentRevisionsIndexes:
    """Indexes on document_revisions must be present and IF NOT EXISTS guarded."""

    @pytest.fixture(autouse=True)
    def sql(self):
        return MIGRATION_FILE.read_text()

    def test_index_document_revisions_doc_idx(self, sql):
        """Index: document_revisions_doc_idx on (document_id)."""
        assert "CREATE INDEX IF NOT EXISTS document_revisions_doc_idx" in sql, (
            "Expected 'CREATE INDEX IF NOT EXISTS document_revisions_doc_idx'"
        )
        assert "ON document_revisions(document_id)" in sql, (
            "Index must target unqualified 'document_revisions(document_id)'"
        )

    def test_index_document_revisions_parent_idx(self, sql):
        """Index: document_revisions_parent_idx on (parent_revision_id)."""
        assert "CREATE INDEX IF NOT EXISTS document_revisions_parent_idx" in sql, (
            "Expected 'CREATE INDEX IF NOT EXISTS document_revisions_parent_idx'"
        )
        assert "ON document_revisions(parent_revision_id)" in sql, (
            "Index must target unqualified 'document_revisions(parent_revision_id)'"
        )

    def test_no_schema_prefix_on_indexes(self, sql):
        """Index targets must be unqualified (no corpus. prefix)."""
        lower = sql.lower()
        assert "on corpus.document_revisions" not in lower, (
            "Index target must be unqualified 'document_revisions', not 'corpus.document_revisions'"
        )


# ── ALTER TABLE: documents.tombstoned_at ─────────────────────────────────


class TestTombstonedAtColumn:
    """documents.tombstoned_at must be added as TEXT (not TIMESTAMPTZ)."""

    @pytest.fixture(autouse=True)
    def sql(self):
        return MIGRATION_FILE.read_text()

    def test_alter_documents_add_tombstoned_at(self, sql):
        """DDL: ALTER TABLE documents ADD COLUMN IF NOT EXISTS tombstoned_at."""
        assert "ALTER TABLE documents" in sql, "Expected 'ALTER TABLE documents' (unqualified)"
        assert "ADD COLUMN IF NOT EXISTS tombstoned_at" in sql, (
            "Expected 'ADD COLUMN IF NOT EXISTS tombstoned_at'"
        )

    def test_tombstoned_at_is_text_not_timestamptz(self, sql):
        """tombstoned_at must be TEXT, not TIMESTAMPTZ (SQLite stores timestamps as TEXT)."""
        # Find the tombstoned_at ALTER line and verify it says TEXT
        for line in sql.splitlines():
            if "tombstoned_at" in line.lower():
                assert "TEXT" in line.upper(), (
                    f"tombstoned_at must be TEXT (ISO-8601) in SQLite, got: {line.strip()}"
                )
                assert "TIMESTAMPTZ" not in line.upper(), (
                    f"TIMESTAMPTZ is Postgres-only; use TEXT for tombstoned_at: {line.strip()}"
                )
                break

    def test_no_corpus_schema_prefix_on_documents(self, sql):
        """ALTER TABLE documents must not have corpus. prefix."""
        assert "ALTER TABLE corpus.documents" not in sql, (
            "Use unqualified 'ALTER TABLE documents', not 'ALTER TABLE corpus.documents'"
        )


# ── ALTER TABLE: sources columns ─────────────────────────────────────────


class TestSourcesColumns:
    """sources.last_pulled_revision_id and sources.sync_enabled must be added."""

    @pytest.fixture(autouse=True)
    def sql(self):
        return MIGRATION_FILE.read_text()

    def test_alter_sources_add_last_pulled_revision_id(self, sql):
        """DDL: ALTER TABLE sources ADD COLUMN IF NOT EXISTS last_pulled_revision_id INTEGER."""
        assert "ALTER TABLE sources" in sql, "Expected 'ALTER TABLE sources' (unqualified)"
        assert "ADD COLUMN IF NOT EXISTS last_pulled_revision_id" in sql, (
            "Expected 'ADD COLUMN IF NOT EXISTS last_pulled_revision_id'"
        )

    def test_last_pulled_revision_id_is_integer(self, sql):
        """last_pulled_revision_id must be INTEGER (not BIGINT — SQLite uses INTEGER)."""
        for line in sql.splitlines():
            if "last_pulled_revision_id" in line.lower():
                # SQLite has INTEGER affinity for all integer types
                # Accept INTEGER or BIGINT (SQLite maps BIGINT → INTEGER affinity)
                assert "INTEGER" in line.upper() or "BIGINT" in line.upper(), (
                    f"last_pulled_revision_id should be INTEGER (or BIGINT) in: {line.strip()}"
                )
                break

    def test_alter_sources_add_sync_enabled(self, sql):
        """DDL: ALTER TABLE sources ADD COLUMN IF NOT EXISTS sync_enabled."""
        assert "ADD COLUMN IF NOT EXISTS sync_enabled" in sql, (
            "Expected 'ADD COLUMN IF NOT EXISTS sync_enabled'"
        )

    def test_sync_enabled_has_default_false(self, sql):
        """sync_enabled must have DEFAULT 0 or DEFAULT FALSE."""
        for line in sql.splitlines():
            if "sync_enabled" in line.lower():
                upper = line.upper()
                has_default = "DEFAULT 0" in upper or "DEFAULT FALSE" in upper
                assert has_default, (
                    f"sync_enabled must have DEFAULT 0 or DEFAULT FALSE, got: {line.strip()}"
                )
                break

    def test_no_corpus_schema_prefix_on_sources(self, sql):
        """ALTER TABLE sources must not have corpus. prefix."""
        assert "ALTER TABLE corpus.sources" not in sql, (
            "Use unqualified 'ALTER TABLE sources', not 'ALTER TABLE corpus.sources'"
        )


# ── Idempotency & safety ─────────────────────────────────────────────────


class TestIdempotencyAndSafety:
    """All DDL must be idempotent and non-destructive."""

    @pytest.fixture(autouse=True)
    def sql(self):
        return MIGRATION_FILE.read_text()

    def test_all_alter_statements_have_if_not_exists(self, sql):
        """Every ALTER TABLE ADD COLUMN must use IF NOT EXISTS."""
        for line in sql.splitlines():
            stripped = line.strip().upper()
            if stripped.startswith("ALTER TABLE") and "ADD COLUMN" in stripped:
                assert "IF NOT EXISTS" in stripped, (
                    f"ALTER TABLE ADD COLUMN missing IF NOT EXISTS: {line.strip()}"
                )

    def test_all_create_statements_have_if_not_exists(self, sql):
        """Every CREATE TABLE / CREATE INDEX must use IF NOT EXISTS."""
        for line in sql.splitlines():
            stripped = line.strip().upper()
            if stripped.startswith("CREATE TABLE") or stripped.startswith("CREATE INDEX"):
                assert "IF NOT EXISTS" in stripped, (
                    f"CREATE statement missing IF NOT EXISTS: {line.strip()}"
                )

    def test_no_drop_or_truncate(self, sql):
        """Safety: no destructive DDL."""
        upper = sql.upper()
        assert "DROP TABLE" not in upper
        assert "DROP COLUMN" not in upper
        assert "TRUNCATE" not in upper

    def test_no_dangerous_dcl(self, sql):
        """Safety: no GRANT/REVOKE/DENY."""
        upper = sql.upper()
        assert "GRANT " not in upper
        assert "REVOKE " not in upper
        assert "DENY " not in upper

    def test_if_not_exists_count_at_least_6(self, sql):
        """At least 6 IF NOT EXISTS guards (1 CREATE TABLE + 2 INDEX + 3 ALTER)."""
        count = sql.upper().count("IF NOT EXISTS")
        assert count >= 6, f"Expected at least 6 'IF NOT EXISTS' guards, found {count}"

    def test_statements_count(self, sql):
        """Structure: exactly 6 executable statements (1 CREATE TABLE + 2 INDEX + 3 ALTER)."""
        raw_blocks = sql.split(";")
        executable = []
        for block in raw_blocks:
            stripped = block.strip()
            if not stripped:
                continue
            lines = [
                ln.strip()
                for ln in stripped.splitlines()
                if ln.strip() and not ln.strip().startswith("--")
            ]
            if lines:
                executable.append(" ".join(lines))
        assert len(executable) == 6, (
            f"Expected 6 executable SQL statements, found {len(executable)}: {executable}"
        )


# ── Migration runner integration ─────────────────────────────────────────


class TestMigrationLoaderIntegration:
    """The file must be discoverable by the numeric-prefix glob pattern."""

    def test_glob_discovers_file(self):
        """The glob [0-9]*.sql in sqlite/ discovers 003_sync.sql."""
        sql_files = list(SQLITE_SCHEMA_DIR.glob("[0-9]*.sql"))
        file_names = {f.name for f in sql_files}
        assert "003_sync.sql" in file_names, (
            f"003_sync.sql not found via glob in {SQLITE_SCHEMA_DIR}. Found: {file_names}"
        )

    def test_sort_key_extraction_is_numeric(self):
        """Numeric prefix is extractable as int(3)."""
        stem = MIGRATION_FILE.stem  # '003_sync'
        number_part = stem.split("_")[0]  # '003'
        assert number_part.isdigit(), f"Cannot extract numeric prefix from '{stem}'"
        assert int(number_part) == 3, f"Expected prefix 3, got {int(number_part)}"

    def test_file_includes_if_not_exists_guards(self):
        """All DDL statements include IF NOT EXISTS."""
        content = MIGRATION_FILE.read_text()
        count = content.upper().count("IF NOT EXISTS")
        assert count >= 6, f"Expected at least 6 'IF NOT EXISTS' guards in migration, found {count}"
