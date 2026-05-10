"""Tests for corpus_forge/schema/sqlite/002_chunk_content_hash.sql — B-02.

Asserts that the SQLite translation of the Postgres 002_chunk_content_hash.sql:
- Exists at the expected path in the sqlite/ subdirectory.
- Contains ALTER TABLE chunks ADD COLUMN IF NOT EXISTS content_hash TEXT.
- Contains CREATE INDEX IF NOT EXISTS chunks_content_hash_idx ON chunks(content_hash).
- Does NOT reference the Postgres-style 'corpus.chunks' schema-qualified name
  (SQLite has no schemas; the table is just 'chunks').
- Has exactly 2 executable DDL statements.
- Contains no destructive DDL.
"""

from pathlib import Path

import pytest

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "corpus_forge" / "schema"
SQLITE_SCHEMA_DIR = SCHEMA_DIR / "sqlite"
MIGRATION_FILE = SQLITE_SCHEMA_DIR / "002_chunk_content_hash.sql"


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
        stem = MIGRATION_FILE.stem  # '002_chunk_content_hash'
        number_part = stem.split("_")[0]
        assert number_part.isdigit(), (
            f"Migration filename stem '{stem}' does not start with a digit"
        )
        assert len(number_part) == 3, f"Migration number '{number_part}' is not 3 digits"

    def test_file_ordering_after_001(self):
        """Ordering: numeric prefix must be > 001."""
        stem = MIGRATION_FILE.stem
        number_part = int(stem.split("_")[0])
        assert number_part > 1, f"Migration number {number_part} must be > 1 (after 001_core)"


# ── DDL content validation ───────────────────────────────────────────────


class TestDDLContent:
    """Validate the SQL content matches the SQLite-translated acceptance spec."""

    @pytest.fixture(autouse=True)
    def sql(self):
        return MIGRATION_FILE.read_text()

    def test_contains_alter_table_chunks(self, sql):
        """DDL: ALTER TABLE chunks (not corpus.chunks — no schema prefix in SQLite)."""
        upper = sql.upper()
        assert "ALTER TABLE CHUNKS" in upper or "ALTER TABLE chunks" in sql, (
            "Expected 'ALTER TABLE chunks' (unqualified, no corpus. prefix)"
        )

    def test_no_schema_qualified_table_name(self, sql):
        """SQLite has no schemas: corpus.chunks must NOT appear (use bare 'chunks')."""
        assert "corpus.chunks" not in sql.lower(), (
            "SQLite migration must use unqualified table name 'chunks', not 'corpus.chunks'"
        )

    def test_add_column_content_hash_text(self, sql):
        """DDL: ADD COLUMN IF NOT EXISTS content_hash TEXT."""
        assert "ADD COLUMN IF NOT EXISTS content_hash TEXT" in sql, (
            "Expected 'ADD COLUMN IF NOT EXISTS content_hash TEXT'"
        )

    def test_contains_create_index(self, sql):
        """DDL: CREATE INDEX IF NOT EXISTS chunks_content_hash_idx ON chunks(content_hash)."""
        assert "CREATE INDEX IF NOT EXISTS chunks_content_hash_idx" in sql, (
            "Expected 'CREATE INDEX IF NOT EXISTS chunks_content_hash_idx'"
        )
        assert "ON chunks(content_hash)" in sql, (
            "Expected 'ON chunks(content_hash)' (unqualified, no corpus. prefix)"
        )

    def test_no_schema_qualified_index_target(self, sql):
        """Index target must be unqualified 'chunks', not 'corpus.chunks'."""
        assert "ON corpus.chunks" not in sql.lower(), (
            "SQLite migration must use unqualified table 'chunks' in CREATE INDEX target"
        )

    def test_idempotent_alter(self, sql):
        """Idempotent: ALTER uses IF NOT EXISTS guard."""
        for line in sql.splitlines():
            stripped = line.strip().upper()
            if stripped.startswith("ALTER TABLE"):
                assert "IF NOT EXISTS" in stripped, (
                    "ALTER TABLE must use IF NOT EXISTS for idempotency"
                )
                break
        else:
            pytest.fail("No ALTER TABLE statement found in SQLite migration file")

    def test_idempotent_index(self, sql):
        """Idempotent: CREATE INDEX uses IF NOT EXISTS guard."""
        for line in sql.splitlines():
            stripped = line.strip().upper()
            if stripped.startswith("CREATE INDEX"):
                assert "IF NOT EXISTS" in stripped, (
                    "CREATE INDEX must use IF NOT EXISTS for idempotency"
                )
                break
        else:
            pytest.fail("No CREATE INDEX statement found in SQLite migration file")

    def test_no_drop_or_truncate(self, sql):
        """Safety: migration must not contain destructive DDL."""
        upper_sql = sql.upper()
        assert "DROP TABLE" not in upper_sql
        assert "DROP COLUMN" not in upper_sql
        assert "TRUNCATE" not in upper_sql

    def test_no_postgres_only_types(self, sql):
        """No Postgres-only types must appear in this migration."""
        upper = sql.upper()
        assert "BIGSERIAL" not in upper
        assert "JSONB" not in upper
        assert "TIMESTAMPTZ" not in upper

    def test_statements_count(self, sql):
        """Structure: exactly 2 executable statements (ALTER + CREATE INDEX)."""
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
        assert len(executable) == 2, (
            f"Expected 2 executable SQL statements, found {len(executable)}: {executable}"
        )


# ── Migration runner integration ─────────────────────────────────────────


class TestMigrationLoaderIntegration:
    """The file must be discoverable by the numeric-prefix glob pattern."""

    def test_glob_discovers_file(self):
        """The glob [0-9]*.sql in sqlite/ subdir discovers 002_chunk_content_hash.sql."""
        sql_files = list(SQLITE_SCHEMA_DIR.glob("[0-9]*.sql"))
        file_names = {f.name for f in sql_files}
        assert "002_chunk_content_hash.sql" in file_names, (
            f"002_chunk_content_hash.sql not found via glob. Found: {file_names}"
        )

    def test_sort_key_extraction_is_numeric(self):
        """Numeric prefix is extractable as int(2)."""
        stem = MIGRATION_FILE.stem  # '002_chunk_content_hash'
        number_part = stem.split("_")[0]  # '002'
        assert number_part.isdigit(), f"Cannot extract numeric prefix from '{stem}'"
        assert int(number_part) == 2, f"Expected prefix 2, got {int(number_part)}"

    def test_file_includes_if_not_exists_guards(self):
        """Both DDL statements include IF NOT EXISTS."""
        content = MIGRATION_FILE.read_text()
        count = content.upper().count("IF NOT EXISTS")
        assert count >= 2, f"Expected at least 2 'IF NOT EXISTS' guards in migration, found {count}"
