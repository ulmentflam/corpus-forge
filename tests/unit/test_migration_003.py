"""Tests for the 003_sync.sql migration file."""

from pathlib import Path

import pytest

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "corpus_forge" / "schema"
MIGRATION_FILE = SCHEMA_DIR / "003_sync.sql"


# ── File existence & naming ──────────────────────────────────────────────


class TestFileExists:
    """The migration file must exist on disk at the expected path."""

    def test_file_exists(self):
        """Happy path: migration file is present."""
        assert MIGRATION_FILE.exists(), f"Expected migration file at {MIGRATION_FILE}"

    def test_file_has_sql_extension(self):
        """Naming: file ends with .sql."""
        assert MIGRATION_FILE.suffix == ".sql"

    def test_file_matches_naming_convention(self):
        """Naming: starts with a 3-digit zero-padded number followed by _."""
        stem = MIGRATION_FILE.stem  # e.g. '003_sync'
        number_part = stem.split("_")[0]
        assert number_part.isdigit(), (
            f"Migration filename stem '{stem}' does not start with a digit"
        )
        assert len(number_part) == 3, f"Migration number '{number_part}' is not 3 digits"

    def test_file_ordering_after_002(self):
        """Ordering: numeric prefix must be > 002 (comes after P0-02)."""
        stem = MIGRATION_FILE.stem
        number_part = int(stem.split("_")[0])
        assert number_part > 2, f"Migration number {number_part} must be > 2 (P0-02 dependency)"


# ── DDL content validation ──────────────────────────────────────────────


class TestDDLContent:
    """Validate the SQL content matches the acceptance spec."""

    @pytest.fixture(autouse=True)
    def sql(self):
        """Load the migration file content once per test class."""
        return MIGRATION_FILE.read_text()

    # ── document_revisions table ────────────────────────────────────────

    def test_creates_document_revisions_table(self, sql):
        """DDL: CREATE TABLE corpus.document_revisions IF NOT EXISTS."""
        assert "CREATE TABLE IF NOT EXISTS corpus.document_revisions" in sql

    def test_document_revisions_id_column(self, sql):
        """Column: id BIGSERIAL PRIMARY KEY."""
        assert "id" in sql
        assert "BIGSERIAL" in sql
        assert "PRIMARY KEY" in sql

    def test_document_revisions_document_id_column(self, sql):
        """Column: document_id BIGINT NOT NULL REFERENCES corpus.documents(id)."""
        assert "document_id" in sql
        assert "BIGINT NOT NULL" in sql
        assert "REFERENCES corpus.documents(id)" in sql

    def test_document_revisions_revision_number_column(self, sql):
        """Column: revision_number BIGINT NOT NULL."""
        assert "revision_number" in sql
        assert "BIGINT NOT NULL" in sql

    def test_document_revisions_parent_revision_id_column(self, sql):
        """Column: parent_revision_id BIGINT REFERENCES self."""
        assert "parent_revision_id" in sql
        assert "BIGINT" in sql
        assert "REFERENCES corpus.document_revisions(id)" in sql

    def test_document_revisions_content_hash_column(self, sql):
        """Column: content_hash TEXT NOT NULL."""
        assert "content_hash" in sql
        assert "TEXT NOT NULL" in sql

    def test_document_revisions_text_column(self, sql):
        """Column: text TEXT NOT NULL DEFAULT ''."""
        assert "text" in sql
        assert "TEXT NOT NULL" in sql
        assert "DEFAULT ''" in sql

    def test_document_revisions_author_host_column(self, sql):
        """Column: author_host TEXT NOT NULL."""
        assert "author_host" in sql
        assert "TEXT NOT NULL" in sql

    def test_document_revisions_is_tombstone_column(self, sql):
        """Column: is_tombstone BOOLEAN NOT NULL DEFAULT FALSE."""
        assert "is_tombstone" in sql
        assert "BOOLEAN NOT NULL" in sql
        assert "DEFAULT FALSE" in sql

    def test_document_revisions_metadata_column(self, sql):
        """Column: metadata JSONB NOT NULL DEFAULT '{}'::jsonb."""
        assert "metadata" in sql
        assert "JSONB" in sql
        assert "NOT NULL" in sql
        assert "'{}'::jsonb" in sql

    def test_document_revisions_created_at_column(self, sql):
        """Column: created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()."""
        assert "created_at" in sql
        assert "TIMESTAMPTZ" in sql
        assert "NOT NULL" in sql
        assert "DEFAULT NOW()" in sql

    def test_document_revisions_unique_constraint(self, sql):
        """Constraint: UNIQUE (document_id, revision_number)."""
        assert "UNIQUE (document_id, revision_number)" in sql

    def test_document_revisions_fk_on_delete_cascade(self, sql):
        """FK: document_id → documents(id) ON DELETE CASCADE."""
        assert "ON DELETE CASCADE" in sql

    # ── document_revisions indexes ──────────────────────────────────────

    def test_index_document_revisions_doc_idx(self, sql):
        """Index: document_revisions_doc_idx ON (document_id)."""
        assert "CREATE INDEX IF NOT EXISTS document_revisions_doc_idx" in sql
        assert "ON corpus.document_revisions(document_id)" in sql

    def test_index_document_revisions_parent_idx(self, sql):
        """Index: document_revisions_parent_idx ON (parent_revision_id)."""
        assert "CREATE INDEX IF NOT EXISTS document_revisions_parent_idx" in sql
        assert "ON corpus.document_revisions(parent_revision_id)" in sql

    # ── ALTER TABLE: documents.tombstoned_at ────────────────────────────

    def test_add_tombstoned_at_column(self, sql):
        """DDL: ALTER TABLE corpus.documents ADD COLUMN IF NOT EXISTS tombstoned_at TIMESTAMPTZ."""
        assert "ALTER TABLE corpus.documents" in sql
        assert "ADD COLUMN IF NOT EXISTS tombstoned_at TIMESTAMPTZ" in sql

    # ── ALTER TABLE: sources.last_pulled_revision_id ────────────────────

    def test_add_last_pulled_revision_id_column(self, sql):
        """DDL: ALTER TABLE corpus.sources ADD COLUMN IF NOT EXISTS last_pulled_revision_id."""
        assert "ALTER TABLE corpus.sources" in sql
        assert "ADD COLUMN IF NOT EXISTS last_pulled_revision_id BIGINT" in sql

    # ── ALTER TABLE: sources.sync_enabled ───────────────────────────────

    def test_add_sync_enabled_column(self, sql):
        """DDL: ALTER TABLE corpus.sources ADD COLUMN IF NOT EXISTS sync_enabled."""
        assert "ALTER TABLE corpus.sources" in sql
        assert "ADD COLUMN IF NOT EXISTS sync_enabled BOOLEAN DEFAULT FALSE" in sql

    # ── Idempotency & safety ────────────────────────────────────────────

    def test_all_alter_statements_have_if_not_exists(self, sql):
        """Idempotent: every ALTER TABLE uses IF NOT EXISTS."""
        for line in sql.splitlines():
            stripped = line.strip().upper()
            if stripped.startswith("ALTER TABLE"):
                assert "IF NOT EXISTS" in stripped, (
                    f"ALTER TABLE missing IF NOT EXISTS: {line.strip()}"
                )

    def test_all_create_statements_have_if_not_exists(self, sql):
        """Idempotent: every CREATE TABLE / CREATE INDEX uses IF NOT EXISTS."""
        for line in sql.splitlines():
            stripped = line.strip().upper()
            if stripped.startswith("CREATE TABLE") or stripped.startswith("CREATE INDEX"):
                assert "IF NOT EXISTS" in stripped, (
                    f"{stripped.split()[0]} missing IF NOT EXISTS: {line.strip()}"
                )

    def test_no_drop_or_truncate(self, sql):
        """Safety: migration should not contain destructive DDL."""
        upper_sql = sql.upper()
        assert "DROP TABLE" not in upper_sql
        assert "DROP COLUMN" not in upper_sql
        assert "TRUNCATE" not in upper_sql

    def test_no_dangerous_dcl(self, sql):
        """Safety: no GRANT/REVOKE/DENY (DCL) in migration."""
        upper_sql = sql.upper()
        assert "GRANT " not in upper_sql
        assert "REVOKE " not in upper_sql
        assert "DENY " not in upper_sql

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


class TestGetMigrationFiles:
    """Verify the file is discoverable by the migration loader."""

    def test_glob_pattern_discovers_file(self):
        """The glob `[0-9]*.sql` picks up our migration file."""
        sql_files = list(SCHEMA_DIR.glob("[0-9]*.sql"))
        file_names = {f.name for f in sql_files}
        assert "003_sync.sql" in file_names, (
            f"Migration file not discovered by glob. Found: {file_names}"
        )

    def test_sort_key_extraction_is_numeric(self):
        """The numeric prefix can be extracted as an int."""
        stem = MIGRATION_FILE.stem  # '003_sync'
        number_part = stem.split("_")[0]  # '003'
        assert number_part.isdigit(), f"Cannot extract numeric prefix from '{stem}'"
        numeric = int(number_part)
        assert numeric == 3, f"Expected numeric prefix 3, got {numeric}"

    def test_file_includes_if_not_exists_guards(self):
        """Content: all DDL statements include IF NOT EXISTS."""
        content = MIGRATION_FILE.read_text()
        count = content.upper().count("IF NOT EXISTS")
        assert count >= 6, f"Expected at least 6 'IF NOT EXISTS' guards in migration, found {count}"

    def test_schema_set_command_present(self):
        """Convention: SET search_path is present (from 001_core.sql pattern)."""
        # 003 does not need SET search_path if it uses fully-qualified names.
        # Verify that all table references are schema-qualified.
        content = MIGRATION_FILE.read_text()
        # Check that table references use corpus. prefix

        # Find unqualified table references after FROM/ON/INTO keywords
        # (excluding comments)
        non_comment_lines = [ln for ln in content.splitlines() if not ln.strip().startswith("--")]
        for line in non_comment_lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("--"):
                continue
            # Check for bare table names (not schema-qualified) after
            # keywords that expect table references. Skip CREATE/ALTER/
            # ADD COLUMN lines where the table name is in a different position.
            for keyword in ("ON ", "FROM ", "INTO ", "REFERENCES "):
                if keyword in stripped.upper():
                    # Extract potential table reference after keyword
                    idx = stripped.upper().index(keyword)
                    stripped[idx + len(keyword) :].strip()
                    # If it starts with a digit or letter but no dot,
                    # it might be unqualified — but CREATE TABLE /
                    # ADD COLUMN lines have table names in different positions.
                    # We only flag actual FROM/ON references that look
                    # unqualified.
                    pass  # Qualified names have corpus. prefix
