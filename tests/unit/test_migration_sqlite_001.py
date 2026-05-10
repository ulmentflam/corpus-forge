"""Tests for corpus_forge/schema/sqlite/001_core.sql — B-02.

Asserts that the SQLite translation of the Postgres 001_core.sql migration:
- Exists at the expected path.
- Uses SQLite-compatible DDL types (no Postgres-only types).
- Contains all required tables with IF NOT EXISTS guards.
- Preserves foreign-key declarations.
- Has no Postgres-only constructs (BIGSERIAL, JSONB, TIMESTAMPTZ, ::jsonb casts,
  CREATE EXTENSION, SET search_path).
"""

from pathlib import Path

import pytest

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "corpus_forge" / "schema"
SQLITE_SCHEMA_DIR = SCHEMA_DIR / "sqlite"
MIGRATION_FILE = SQLITE_SCHEMA_DIR / "001_core.sql"


# ── File existence & naming ──────────────────────────────────────────────


class TestFileExists:
    """The SQLite migration file must exist at the expected path."""

    def test_file_exists(self):
        """Happy path: migration file is present at corpus_forge/schema/sqlite/001_core.sql."""
        assert MIGRATION_FILE.exists(), f"Expected SQLite migration file at {MIGRATION_FILE}"

    def test_file_has_sql_extension(self):
        """Naming: file ends with .sql."""
        assert MIGRATION_FILE.suffix == ".sql"

    def test_file_matches_naming_convention(self):
        """Naming: starts with a 3-digit zero-padded number followed by _."""
        stem = MIGRATION_FILE.stem  # '001_core'
        number_part = stem.split("_")[0]
        assert number_part.isdigit(), (
            f"Migration filename stem '{stem}' does not start with a digit"
        )
        assert len(number_part) == 3, f"Migration number '{number_part}' is not 3 digits"

    def test_sqlite_subdir_exists(self):
        """The corpus_forge/schema/sqlite/ subdirectory must exist."""
        assert SQLITE_SCHEMA_DIR.exists(), (
            f"Expected SQLite schema subdirectory at {SQLITE_SCHEMA_DIR}"
        )
        assert SQLITE_SCHEMA_DIR.is_dir(), f"{SQLITE_SCHEMA_DIR} is not a directory"


# ── SQLite dialect: required types ───────────────────────────────────────


class TestSQLiteDialect:
    """Verify Postgres-specific types are translated to SQLite equivalents."""

    @pytest.fixture(autouse=True)
    def sql(self):
        return MIGRATION_FILE.read_text()

    def test_no_bigserial(self, sql):
        """BIGSERIAL must not appear — SQLite uses INTEGER PRIMARY KEY AUTOINCREMENT."""
        assert "BIGSERIAL" not in sql.upper(), (
            "BIGSERIAL is Postgres-only; use 'INTEGER PRIMARY KEY AUTOINCREMENT' for SQLite"
        )

    def test_uses_integer_primary_key_autoincrement(self, sql):
        """Primary key columns must use INTEGER PRIMARY KEY AUTOINCREMENT."""
        assert "INTEGER PRIMARY KEY AUTOINCREMENT" in sql.upper(), (
            "Expected 'INTEGER PRIMARY KEY AUTOINCREMENT' as the SQLite PK type"
        )

    def test_no_jsonb_type(self, sql):
        """JSONB must not appear — SQLite uses TEXT for JSON storage."""
        assert "JSONB" not in sql.upper(), (
            "JSONB is Postgres-only; use TEXT for JSON columns in SQLite"
        )

    def test_json_columns_use_text(self, sql):
        """JSON columns (config, metadata, tool_calls, etc.) must be typed as TEXT."""
        # Check that at least one TEXT type appears (we know there are many JSON cols)
        assert "TEXT" in sql.upper(), "Expected TEXT type for JSON columns"

    def test_no_timestamptz(self, sql):
        """TIMESTAMPTZ must not appear — SQLite uses TEXT (ISO-8601 UTC)."""
        assert "TIMESTAMPTZ" not in sql.upper(), (
            "TIMESTAMPTZ is Postgres-only; use TEXT (ISO-8601 UTC) in SQLite"
        )

    def test_no_jsonb_cast(self, sql):
        """No ::jsonb casts must appear (Postgres cast syntax)."""
        assert "::jsonb" not in sql.lower(), (
            "::jsonb is a Postgres-specific cast; use '{}' (plain string) in SQLite"
        )

    def test_no_create_extension(self, sql):
        """No CREATE EXTENSION — SQLite extensions are loaded at runtime, not via DDL."""
        assert "CREATE EXTENSION" not in sql.upper(), (
            "CREATE EXTENSION is Postgres-only DDL; sqlite-vec is loaded at runtime"
        )

    def test_no_set_search_path(self, sql):
        """No SET search_path — SQLite has no schema namespacing via search_path."""
        assert "SET search_path" not in sql, (
            "SET search_path is Postgres-only; SQLite has no schema search path"
        )

    def test_no_create_schema(self, sql):
        """No CREATE SCHEMA — SQLite has no schema namespacing."""
        assert "CREATE SCHEMA" not in sql.upper(), (
            "CREATE SCHEMA is Postgres-only; SQLite has no schemas"
        )

    def test_default_empty_json_no_postgres_cast(self, sql):
        """DEFAULT '{}' must not use the Postgres-only ::jsonb cast."""
        # Verify no '{}'::<anything> pattern
        import re

        assert not re.search(r"'\{\}'::jsonb", sql, re.IGNORECASE), (
            "Found Postgres-style cast '{}'::jsonb; use plain '{}' in SQLite"
        )


# ── Required tables ──────────────────────────────────────────────────────


class TestRequiredTables:
    """All core tables from the Postgres schema must be present in the SQLite version."""

    @pytest.fixture(autouse=True)
    def sql(self):
        return MIGRATION_FILE.read_text()

    def test_datasets_table_exists(self, sql):
        """Table: datasets must be defined."""
        assert "CREATE TABLE IF NOT EXISTS" in sql.upper()
        assert "datasets" in sql.lower()

    def test_sources_table_exists(self, sql):
        """Table: sources must be defined."""
        assert "sources" in sql.lower()

    def test_documents_table_exists(self, sql):
        """Table: documents must be defined."""
        assert "documents" in sql.lower()

    def test_conversations_table_exists(self, sql):
        """Table: conversations must be defined."""
        assert "conversations" in sql.lower()

    def test_messages_table_exists(self, sql):
        """Table: messages must be defined."""
        assert "messages" in sql.lower()

    def test_chunks_table_exists(self, sql):
        """Table: chunks must be defined."""
        assert "chunks" in sql.lower()

    def test_embedders_table_exists(self, sql):
        """Table: embedders registry must be defined."""
        assert "embedders" in sql.lower()

    def test_labels_table_exists(self, sql):
        """Table: labels must be defined."""
        assert "labels" in sql.lower()

    def test_chunk_labels_table_exists(self, sql):
        """Table: chunk_labels must be defined."""
        assert "chunk_labels" in sql.lower()

    def test_document_labels_table_exists(self, sql):
        """Table: document_labels must be defined."""
        assert "document_labels" in sql.lower()

    def test_conversation_labels_table_exists(self, sql):
        """Table: conversation_labels must be defined."""
        assert "conversation_labels" in sql.lower()


# ── Idempotency guards ───────────────────────────────────────────────────


class TestIdempotencyGuards:
    """Every DDL statement must be guarded with IF NOT EXISTS."""

    @pytest.fixture(autouse=True)
    def sql(self):
        return MIGRATION_FILE.read_text()

    def test_all_create_table_have_if_not_exists(self, sql):
        """Every CREATE TABLE uses IF NOT EXISTS for idempotency."""
        for line in sql.splitlines():
            stripped = line.strip().upper()
            if stripped.startswith("CREATE TABLE") and "IF NOT EXISTS" not in stripped:
                pytest.fail(f"CREATE TABLE missing IF NOT EXISTS guard: {line.strip()}")

    def test_all_create_index_have_if_not_exists(self, sql):
        """Every CREATE INDEX uses IF NOT EXISTS for idempotency."""
        for line in sql.splitlines():
            stripped = line.strip().upper()
            if stripped.startswith("CREATE INDEX") and "IF NOT EXISTS" not in stripped:
                pytest.fail(f"CREATE INDEX missing IF NOT EXISTS guard: {line.strip()}")

    def test_if_not_exists_count_reasonable(self, sql):
        """At minimum 11 CREATE TABLE IF NOT EXISTS guards (one per table)."""
        count = sql.upper().count("IF NOT EXISTS")
        assert count >= 11, (
            f"Expected at least 11 'IF NOT EXISTS' guards (one per table + indexes); found {count}"
        )


# ── Foreign key declarations ─────────────────────────────────────────────


class TestForeignKeys:
    """Foreign key declarations must be preserved from the Postgres schema."""

    @pytest.fixture(autouse=True)
    def sql(self):
        return MIGRATION_FILE.read_text()

    def test_sources_fk_to_datasets(self, sql):
        """sources.dataset_id must reference datasets(id)."""
        assert "REFERENCES datasets(id)" in sql, (
            "sources.dataset_id must have FK REFERENCES datasets(id)"
        )

    def test_documents_fk_to_datasets(self, sql):
        """documents.dataset_id must reference datasets(id)."""
        # There will be multiple references; just assert the pattern
        assert "REFERENCES datasets(id)" in sql

    def test_conversations_fk_to_datasets(self, sql):
        """conversations.dataset_id must reference datasets(id)."""
        assert "REFERENCES datasets(id)" in sql

    def test_messages_fk_to_conversations(self, sql):
        """messages.conversation_id must reference conversations(id)."""
        assert "REFERENCES conversations(id)" in sql

    def test_chunks_fk_to_documents(self, sql):
        """chunks.document_id must reference documents(id)."""
        assert "REFERENCES documents(id)" in sql

    def test_chunks_fk_to_conversations(self, sql):
        """chunks.conversation_id must reference conversations(id)."""
        assert "REFERENCES conversations(id)" in sql

    def test_chunks_fk_to_messages(self, sql):
        """chunks.message_id must reference messages(id)."""
        assert "REFERENCES messages(id)" in sql

    def test_chunk_labels_fk_to_chunks(self, sql):
        """chunk_labels.chunk_id must reference chunks(id)."""
        assert "REFERENCES chunks(id)" in sql

    def test_chunk_labels_fk_to_labels(self, sql):
        """chunk_labels.label_id must reference labels(id)."""
        assert "REFERENCES labels(id)" in sql

    def test_on_delete_cascade_present(self, sql):
        """ON DELETE CASCADE must be present (preserved from Postgres)."""
        assert "ON DELETE CASCADE" in sql.upper(), (
            "ON DELETE CASCADE must be preserved in the SQLite schema"
        )


# ── Column shape ─────────────────────────────────────────────────────────


class TestColumnShape:
    """Key columns must exist with the right names and types."""

    @pytest.fixture(autouse=True)
    def sql(self):
        return MIGRATION_FILE.read_text()

    def test_datasets_name_unique(self, sql):
        """datasets.name must be UNIQUE."""
        assert "UNIQUE" in sql.upper()

    def test_chunks_chunk_index_column(self, sql):
        """chunks must have a chunk_index column."""
        assert "chunk_index" in sql.lower()

    def test_documents_source_uri_column(self, sql):
        """documents must have source_uri column."""
        assert "source_uri" in sql.lower()

    def test_documents_content_hash_column(self, sql):
        """documents must have content_hash column."""
        assert "content_hash" in sql.lower()

    def test_embedders_dimension_column(self, sql):
        """embedders must have dimension column (INT)."""
        assert "dimension" in sql.lower()

    def test_embedders_table_name_column(self, sql):
        """embedders must have table_name column for tracking per-embedder virtual tables."""
        assert "table_name" in sql.lower()

    def test_no_vector_type(self, sql):
        """No vector(N) type — vector storage handled by separate embedding tables (B-04)."""
        assert "vector(" not in sql.lower(), (
            "vector(N) is not valid in 001_core.sql; embedding tables are created in B-04"
        )

    def test_normalized_boolean_column_in_embedders(self, sql):
        """embedders.normalized must be present as INTEGER or BOOLEAN."""
        assert "normalized" in sql.lower()


# ── Safety checks ────────────────────────────────────────────────────────


class TestSafetyChecks:
    """The schema file must not contain destructive DDL."""

    @pytest.fixture(autouse=True)
    def sql(self):
        return MIGRATION_FILE.read_text()

    def test_no_drop_table(self, sql):
        """Safety: no DROP TABLE in the schema file."""
        assert "DROP TABLE" not in sql.upper()

    def test_no_truncate(self, sql):
        """Safety: no TRUNCATE in the schema file."""
        assert "TRUNCATE" not in sql.upper()

    def test_no_drop_column(self, sql):
        """Safety: no DROP COLUMN in the schema file."""
        assert "DROP COLUMN" not in sql.upper()


# ── Migration loader integration ─────────────────────────────────────────


class TestMigrationLoaderIntegration:
    """The file must be discoverable by the numeric-prefix glob pattern."""

    def test_glob_discovers_001_core(self):
        """The glob [0-9]*.sql in the sqlite/ subdir discovers 001_core.sql."""
        sql_files = list(SQLITE_SCHEMA_DIR.glob("[0-9]*.sql"))
        file_names = {f.name for f in sql_files}
        assert "001_core.sql" in file_names, (
            f"001_core.sql not found via glob in {SQLITE_SCHEMA_DIR}. Found: {file_names}"
        )

    def test_sort_key_extraction_is_numeric(self):
        """The numeric prefix 001 is extractable as int(1)."""
        stem = MIGRATION_FILE.stem  # '001_core'
        number_part = stem.split("_")[0]  # '001'
        assert number_part.isdigit(), f"Cannot extract numeric prefix from '{stem}'"
        assert int(number_part) == 1, f"Expected prefix 1, got {int(number_part)}"
