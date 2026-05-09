"""Tests for the 002_chunk_content_hash.sql migration file."""

from pathlib import Path

import pytest

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "corpus_forge" / "schema"
MIGRATION_FILE = SCHEMA_DIR / "002_chunk_content_hash.sql"


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
        stem = MIGRATION_FILE.stem  # e.g. '002_chunk_content_hash'
        number_part = stem.split("_")[0]
        assert number_part.isdigit(), (
            f"Migration filename stem '{stem}' does not start with a digit"
        )
        assert len(number_part) == 3, f"Migration number '{number_part}' is not 3 digits"


# ── DDL content validation ──────────────────────────────────────────────


class TestDDLContent:
    """Validate the SQL content matches the acceptance spec."""

    @pytest.fixture(autouse=True)
    def sql(self):
        """Load the migration file content once per test class."""
        return MIGRATION_FILE.read_text()

    def test_contains_alter_table(self, sql):
        """DDL: ALTER TABLE corpus.chunks ADD COLUMN IF NOT EXISTS content_hash TEXT."""
        assert "ALTER TABLE corpus.chunks" in sql
        assert "ADD COLUMN IF NOT EXISTS content_hash TEXT" in sql

    def test_contains_create_index(self, sql):
        """DDL: CREATE INDEX IF NOT EXISTS chunks_content_hash_idx ON corpus.chunks(content_hash)."""
        assert "CREATE INDEX IF NOT EXISTS chunks_content_hash_idx" in sql
        assert "ON corpus.chunks(content_hash)" in sql

    def test_idempotent_alter(self, sql):
        """Idempotent: ALTER uses IF NOT EXISTS guard."""
        # The ALTER line must contain IF NOT EXISTS
        for line in sql.splitlines():
            stripped = line.strip().upper()
            if stripped.startswith("ALTER TABLE"):
                assert "IF NOT EXISTS" in stripped, (
                    "ALTER TABLE must use IF NOT EXISTS for idempotency"
                )
                break
        else:
            pytest.fail("No ALTER TABLE statement found in migration file")

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
            pytest.fail("No CREATE INDEX statement found in migration file")

    def test_no_drop_or_truncate(self, sql):
        """Safety: migration should not contain destructive DDL."""
        upper_sql = sql.upper()
        assert "DROP TABLE" not in upper_sql
        assert "DROP COLUMN" not in upper_sql
        assert "TRUNCATE" not in upper_sql
        assert "ALTER TABLE ... DROP" not in upper_sql

    def test_statements_count(self, sql):
        """Structure: exactly 2 executable statements (ALTER + CREATE INDEX)."""
        # Parse statements by splitting on ';'. Handle the case where
        # a semicolon-separated block starts with a comment but contains
        # a DDL statement (e.g., header comments before the first ALTER).
        raw_blocks = sql.split(";")
        executable = []
        for block in raw_blocks:
            stripped = block.strip()
            if not stripped:
                continue
            # Remove comment-only lines within a block
            lines = [
                l.strip()
                for l in stripped.splitlines()
                if l.strip() and not l.strip().startswith("--")
            ]
            if lines:
                executable.append(" ".join(lines))
        assert len(executable) == 2, (
            f"Expected 2 executable SQL statements, found {len(executable)}: {executable}"
        )


# ── Migration runner integration ─────────────────────────────────────────


class TestGetMigrationFiles:
    """Verify the file is discoverable by the migration loader.

    NOTE: `get_migration_files()` had a pre-existing bug —
    `p.name.split(".")[0]` on filenames like `002_views.sql` yields
    `'002_views'` which cannot be converted to `int()`. Fixed in Wave 2
    to use `p.stem.split("_")[0]` instead.
    """

    def test_glob_pattern_discovers_file(self):
        """The glob `[0-9]*.sql` picks up our migration file."""
        sql_files = list(SCHEMA_DIR.glob("[0-9]*.sql"))
        file_names = {f.name for f in sql_files}
        assert "002_chunk_content_hash.sql" in file_names, (
            f"Migration file not discovered by glob. Found: {file_names}"
        )

    def test_sort_key_extraction_is_numeric(self):
        """The numeric prefix can be extracted as an int.

        This verifies our filename follows the convention consumed by
        `get_migration_files()` (uses `stem.split("_")[0]` for numeric sort).
        """
        stem = MIGRATION_FILE.stem  # '002_chunk_content_hash'
        number_part = stem.split("_")[0]  # '002'
        assert number_part.isdigit(), f"Cannot extract numeric prefix from '{stem}'"
        numeric = int(number_part)
        assert numeric == 2, f"Expected numeric prefix 2, got {numeric}"

    def test_file_includes_if_not_exists_guards(self):
        """Content: both DDL statements include IF NOT EXISTS."""
        content = MIGRATION_FILE.read_text()
        # Count occurrences of IF NOT EXISTS (case-insensitive)
        count = content.upper().count("IF NOT EXISTS")
        assert count >= 2, f"Expected at least 2 'IF NOT EXISTS' guards in migration, found {count}"
