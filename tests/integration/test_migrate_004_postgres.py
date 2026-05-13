"""Integration tests for the Postgres 004_fts migration.

Verifies:
- ``corpus.chunks.text_tsv`` exists as a GENERATED STORED tsvector column.
- ``chunks_tsv_idx`` GIN index exists.
- Inserting a chunk auto-populates ``text_tsv`` (no manual backfill required).
- A ``text_tsv @@ websearch_to_tsquery('english', ...)`` query produces hits
  on the inserted text.
- Re-running the migration is a no-op (idempotent).
"""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest

from corpus_forge.backends.postgres import PostgresBackend
from corpus_forge.schema.migrate import apply_migrations

pytestmark = pytest.mark.integration


def _schema_dir() -> Path:
    return Path(__file__).parent.parent.parent / "corpus_forge" / "schema"


def _make_backend(pg_dsn: str) -> PostgresBackend:
    return PostgresBackend(dsn=pg_dsn, schema="corpus")


def _insert_chunk(pg_dsn: str, text: str) -> int:
    """Insert a chunk under a fresh dataset+document and return chunk id."""
    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO corpus.datasets (name, kind) VALUES (%s, %s) RETURNING id",
            (f"ds-{text[:8]}-{id(text)}", "text"),
        )
        ds_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO corpus.documents (dataset_id, source_uri, content_hash, text) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (ds_id, f"pg://doc-{id(text)}.md", "hash-x", text),
        )
        doc_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO corpus.chunks (document_id, chunk_index, text, content_hash) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (doc_id, 0, text, "ch-hash-x"),
        )
        chunk_id = cur.fetchone()[0]
        conn.commit()
        return chunk_id


class TestFtsColumnAndIndex:
    def test_text_tsv_column_exists(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()
        apply_migrations(backend, _schema_dir())

        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT data_type, is_generated, generation_expression
                FROM information_schema.columns
                WHERE table_schema = 'corpus'
                  AND table_name = 'chunks'
                  AND column_name = 'text_tsv'
                """
            )
            row = cur.fetchone()
            assert row is not None, "text_tsv column missing after migration 004"
            data_type, is_generated, gen_expr = row
            assert data_type == "tsvector", f"text_tsv type was {data_type!r}"
            assert is_generated == "ALWAYS", f"text_tsv is_generated={is_generated!r}"
            assert gen_expr and "to_tsvector" in gen_expr.lower()

    def test_gin_index_exists(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()
        apply_migrations(backend, _schema_dir())

        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE schemaname = 'corpus'
                  AND tablename = 'chunks'
                  AND indexname = 'chunks_tsv_idx'
                """
            )
            row = cur.fetchone()
            assert row is not None, "chunks_tsv_idx GIN index missing"
            _name, defn = row
            assert "using gin" in defn.lower(), f"Index is not GIN: {defn}"
            assert "text_tsv" in defn, f"Index does not target text_tsv: {defn}"


class TestAutoPopulate:
    def test_insert_populates_text_tsv(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()
        apply_migrations(backend, _schema_dir())

        chunk_id = _insert_chunk(pg_dsn, "the quick brown fox jumps over the lazy dog")

        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT text_tsv FROM corpus.chunks WHERE id = %s", (chunk_id,))
            tsv = cur.fetchone()[0]
            assert tsv is not None, "text_tsv must be auto-populated by GENERATED ALWAYS AS"
            # The lemma 'brown' must appear somewhere in the tsvector
            assert "brown" in tsv, f"tsvector missing 'brown' lemma: {tsv!r}"

    def test_websearch_query_hits(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()
        apply_migrations(backend, _schema_dir())

        _insert_chunk(pg_dsn, "the quick brown fox jumps over the lazy dog")

        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*)
                FROM corpus.chunks
                WHERE text_tsv @@ websearch_to_tsquery('english', %s)
                """,
                ("brown fox",),
            )
            assert cur.fetchone()[0] >= 1, "Expected at least one hit for 'brown fox'"


class TestIdempotency:
    def test_migrate_twice_no_error(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()
        # apply twice
        apply_migrations(backend, _schema_dir())
        apply_migrations(backend, _schema_dir())

        # No duplicate index or column
        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*)
                FROM pg_indexes
                WHERE schemaname = 'corpus'
                  AND indexname = 'chunks_tsv_idx'
                """
            )
            assert cur.fetchone()[0] == 1, "chunks_tsv_idx must not be duplicated"

            cur.execute(
                """
                SELECT COUNT(*)
                FROM information_schema.columns
                WHERE table_schema = 'corpus'
                  AND table_name = 'chunks'
                  AND column_name = 'text_tsv'
                """
            )
            assert cur.fetchone()[0] == 1, "text_tsv column must not be duplicated"

    def test_backfill_returns_zero(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()
        apply_migrations(backend, _schema_dir())
        # Postgres: GENERATED column already populated → 0 rows backfilled
        assert backend.backfill_lexical_index() == 0
