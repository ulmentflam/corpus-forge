"""R1-02 — pin the shape of ``corpus_forge/schema/sqlite/004_fts.sql``.

The migration must create:

- ``chunks_fts`` FTS5 virtual table content-linked to ``chunks`` with
  ``tokenize='porter unicode61'``.
- AFTER INSERT / DELETE / UPDATE triggers (``chunks_ai`` / ``chunks_ad`` /
  ``chunks_au``) that mirror rowid + text into ``chunks_fts``.

All ``CREATE ... IF NOT EXISTS`` for idempotent re-application.
"""

from __future__ import annotations

from pathlib import Path

import pytest

SQLITE_SCHEMA_DIR = Path(__file__).resolve().parents[2] / "corpus_forge" / "schema" / "sqlite"
MIGRATION_FILE = SQLITE_SCHEMA_DIR / "004_fts.sql"


class TestFileExists:
    def test_file_exists(self):
        assert MIGRATION_FILE.exists(), f"Expected migration at {MIGRATION_FILE}"

    def test_naming_convention(self):
        assert MIGRATION_FILE.stem.startswith("004_")
        assert MIGRATION_FILE.suffix == ".sql"

    def test_glob_discovers_file(self):
        files = list(SQLITE_SCHEMA_DIR.glob("[0-9]*.sql"))
        names = [f.name for f in files]
        assert "004_fts.sql" in names, names


class TestVirtualTable:
    @pytest.fixture(autouse=True)
    def sql(self):
        return MIGRATION_FILE.read_text()

    def test_create_virtual_table_chunks_fts(self, sql):
        assert "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts" in sql, (
            "Expected 'CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts'"
        )

    def test_uses_fts5(self, sql):
        assert "USING fts5" in sql or "USING FTS5" in sql, "Virtual table must use fts5 module"

    def test_columns_include_text(self, sql):
        # FTS5 virtual table column list: text, content='chunks', content_rowid='id', tokenize=...
        lower = sql.lower()
        assert "text" in lower

    def test_content_links_to_chunks(self, sql):
        lower = sql.lower()
        assert "content='chunks'" in lower or "content = 'chunks'" in lower, (
            "FTS5 must content-link to the chunks table"
        )
        assert "content_rowid='id'" in lower or "content_rowid = 'id'" in lower, (
            "FTS5 must use chunks.id as the content_rowid"
        )

    def test_tokenize_porter_unicode61(self, sql):
        lower = sql.lower()
        assert "tokenize='porter unicode61'" in lower or 'tokenize="porter unicode61"' in lower, (
            "tokenize must be 'porter unicode61'"
        )


class TestTriggers:
    @pytest.fixture(autouse=True)
    def sql(self):
        return MIGRATION_FILE.read_text()

    def test_chunks_ai_trigger(self, sql):
        assert "CREATE TRIGGER IF NOT EXISTS chunks_ai" in sql, (
            "Expected AFTER INSERT trigger 'chunks_ai'"
        )
        assert "AFTER INSERT ON chunks" in sql, "chunks_ai must fire AFTER INSERT ON chunks"

    def test_chunks_ad_trigger(self, sql):
        assert "CREATE TRIGGER IF NOT EXISTS chunks_ad" in sql, (
            "Expected AFTER DELETE trigger 'chunks_ad'"
        )
        assert "AFTER DELETE ON chunks" in sql, "chunks_ad must fire AFTER DELETE ON chunks"

    def test_chunks_au_trigger(self, sql):
        assert "CREATE TRIGGER IF NOT EXISTS chunks_au" in sql, (
            "Expected AFTER UPDATE trigger 'chunks_au'"
        )
        assert "AFTER UPDATE ON chunks" in sql, "chunks_au must fire AFTER UPDATE ON chunks"

    def test_ai_inserts_into_fts(self, sql):
        # INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text)
        assert "INSERT INTO chunks_fts" in sql, "chunks_ai must mirror new rows into chunks_fts"
        assert "new.id" in sql and "new.text" in sql, "chunks_ai must reference new.id, new.text"

    def test_ad_emits_delete_command(self, sql):
        # FTS5 contentless / external-content delete syntax:
        #   INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', old.id, old.text)
        assert "'delete'" in sql, "chunks_ad must emit the 'delete' command to chunks_fts"
        assert "old.id" in sql and "old.text" in sql, "chunks_ad must reference old.id, old.text"

    def test_au_emits_delete_and_insert(self, sql):
        # chunks_au must do BOTH the delete-old and insert-new
        assert sql.count("'delete'") >= 2, (
            "chunks_au + chunks_ad together must emit two 'delete' commands (one in ad, one in au)"
        )


class TestIdempotencyAndSafety:
    @pytest.fixture(autouse=True)
    def sql(self):
        return MIGRATION_FILE.read_text()

    def test_all_create_have_if_not_exists(self, sql):
        for line in sql.splitlines():
            stripped = line.strip().upper()
            if stripped.startswith("CREATE TRIGGER") or stripped.startswith("CREATE VIRTUAL TABLE"):
                assert "IF NOT EXISTS" in stripped, f"Missing IF NOT EXISTS: {line.strip()}"

    def test_no_postgres_constructs(self, sql):
        upper = sql.upper()
        assert "BIGSERIAL" not in upper
        assert "TSVECTOR" not in upper, "tsvector is Postgres-only — keep in schema/004_fts.sql"
        assert "GIN" not in upper, "GIN is Postgres-only"
        assert "GENERATED ALWAYS" not in upper, "Stored-generated columns are Postgres-only"

    def test_no_schema_prefix(self, sql):
        lower = sql.lower()
        assert "corpus.chunks" not in lower, "SQLite has no schemas — use unqualified 'chunks'"

    def test_no_drop_or_truncate(self, sql):
        upper = sql.upper()
        assert "DROP TABLE" not in upper
        assert "DROP TRIGGER" not in upper
        assert "TRUNCATE" not in upper
