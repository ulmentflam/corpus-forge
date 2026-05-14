"""R1-02 — pin the shape of ``corpus_forge/schema/004_fts.sql`` (Postgres).

The migration must:

- ``ALTER TABLE corpus.chunks ADD COLUMN IF NOT EXISTS text_tsv tsvector
  GENERATED ALWAYS AS (to_tsvector('english', text)) STORED;``
- ``CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON corpus.chunks
  USING GIN (text_tsv);``

Notes:
- ``GENERATED ALWAYS AS ... STORED`` auto-populates on ``ADD COLUMN`` for
  existing rows, so no explicit backfill is required on Postgres.
- Both statements must be idempotent (the migration may be re-applied).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.skip(
    reason="legacy migration test — pins pre-Alembic file-globbing; deleted in D-10"
)

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "corpus_forge" / "schema"
MIGRATION_FILE = SCHEMA_DIR / "004_fts.sql"


class TestFileExists:
    def test_file_exists(self):
        assert MIGRATION_FILE.exists(), f"Expected migration at {MIGRATION_FILE}"

    def test_naming_convention(self):
        assert MIGRATION_FILE.stem.startswith("004_")
        assert MIGRATION_FILE.suffix == ".sql"

    def test_glob_discovers_file(self):
        files = list(SCHEMA_DIR.glob("[0-9]*.sql"))
        assert any(f.name == "004_fts.sql" for f in files), [f.name for f in files]


class TestStatements:
    @pytest.fixture(autouse=True)
    def sql(self):
        return MIGRATION_FILE.read_text()

    def test_alter_table_corpus_chunks(self, sql):
        assert "ALTER TABLE corpus.chunks" in sql, (
            "Expected schema-qualified 'ALTER TABLE corpus.chunks'"
        )

    def test_add_column_if_not_exists_text_tsv(self, sql):
        assert "ADD COLUMN IF NOT EXISTS text_tsv" in sql, (
            "Expected 'ADD COLUMN IF NOT EXISTS text_tsv' for idempotency"
        )

    def test_column_type_is_tsvector(self, sql):
        assert "tsvector" in sql.lower(), "text_tsv must be of tsvector type"

    def test_generated_always_as_stored(self, sql):
        # All three keywords must appear (case-insensitive substring scan)
        lower = sql.lower()
        assert "generated always as" in lower, "Expected GENERATED ALWAYS AS"
        assert "stored" in lower, "Expected STORED for the GENERATED column"

    def test_to_tsvector_english(self, sql):
        assert "to_tsvector(" in sql.lower(), "Expected to_tsvector(...)"
        assert "'english'" in sql.lower(), "Expected 'english' tsvector config"

    def test_to_tsvector_on_text_column(self, sql):
        # to_tsvector('english', text) — the column reference must be "text"
        lower = sql.lower()
        # Either "to_tsvector('english', text)" or with explicit casts
        assert "to_tsvector('english', text)" in lower or "to_tsvector('english',  text)" in lower

    def test_create_index_chunks_tsv_idx(self, sql):
        assert "CREATE INDEX IF NOT EXISTS chunks_tsv_idx" in sql, (
            "Expected 'CREATE INDEX IF NOT EXISTS chunks_tsv_idx'"
        )

    def test_index_uses_gin(self, sql):
        assert "USING GIN" in sql.upper(), "Index must use GIN"

    def test_index_on_text_tsv(self, sql):
        assert "(text_tsv)" in sql, "GIN index must target (text_tsv)"

    def test_index_on_corpus_chunks(self, sql):
        # CREATE INDEX ... ON corpus.chunks USING GIN (text_tsv)
        assert "ON corpus.chunks" in sql, "Index must target corpus.chunks"


class TestIdempotencyAndSafety:
    @pytest.fixture(autouse=True)
    def sql(self):
        return MIGRATION_FILE.read_text()

    def test_no_drop(self, sql):
        upper = sql.upper()
        assert "DROP TABLE" not in upper
        assert "DROP COLUMN" not in upper
        assert "DROP INDEX" not in upper

    def test_no_truncate(self, sql):
        assert "TRUNCATE" not in sql.upper()

    def test_no_grant_revoke(self, sql):
        upper = sql.upper()
        assert "GRANT " not in upper
        assert "REVOKE " not in upper

    def test_all_alter_have_if_not_exists(self, sql):
        for line in sql.splitlines():
            stripped = line.strip().upper()
            if stripped.startswith("ALTER TABLE") and "ADD COLUMN" in stripped:
                assert "IF NOT EXISTS" in stripped, (
                    f"ALTER TABLE ADD COLUMN missing IF NOT EXISTS: {line.strip()}"
                )

    def test_all_create_have_if_not_exists(self, sql):
        for line in sql.splitlines():
            stripped = line.strip().upper()
            if stripped.startswith("CREATE INDEX") or stripped.startswith("CREATE TABLE"):
                assert "IF NOT EXISTS" in stripped, f"Missing IF NOT EXISTS: {line.strip()}"

    def test_no_sqlite_dialect(self, sql):
        """The Postgres file must NOT contain FTS5 virtual table syntax."""
        upper = sql.upper()
        assert "USING FTS5" not in upper, "FTS5 is SQLite-only — keep that in sqlite/004_fts.sql"
        assert "VIRTUAL TABLE" not in upper, "Virtual tables are SQLite-only"
