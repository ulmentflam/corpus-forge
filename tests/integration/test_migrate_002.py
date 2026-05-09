"""Integration tests for 002_chunk_content_hash migration and backfill."""

from pathlib import Path

import pytest

from corpus_forge.backends.postgres import PostgresBackend
from corpus_forge.identity import chunk_content_hash
from corpus_forge.schema.migrate import apply_migrations

pytestmark = pytest.mark.integration


def _schema_dir() -> Path:
    return Path(__file__).parent.parent.parent / "corpus_forge" / "schema"


def _make_backend(pg_dsn: str) -> PostgresBackend:
    return PostgresBackend(dsn=pg_dsn, schema="corpus")


class TestChunkContentHashMigration:
    """Tests for 002_chunk_content_hash migration and backfill."""

    def test_content_hash_column_exists(self, pg, pg_dsn):
        """After apply_migrations, content_hash column exists on chunks table."""
        backend = _make_backend(pg_dsn)
        backend.migrate()
        apply_migrations(backend, _schema_dir())

        with pg.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'corpus'
                  AND table_name = 'chunks'
                  AND column_name = 'content_hash'
                """
            )
            assert cur.fetchone() is not None

    def test_backfill_populates_content_hash(self, pg, pg_dsn):
        """Insert chunks with NULL content_hash, re-run migration, expect backfill."""
        backend = _make_backend(pg_dsn)
        backend.migrate()
        apply_migrations(backend, _schema_dir())

        with pg.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO corpus.datasets (name, kind) VALUES ('test', 'text') RETURNING id"
            )
            dataset_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO corpus.documents (dataset_id, source_uri, content_hash, title, text) "
                "VALUES (%s, 'test://doc1', 'hash1', 'Doc 1', 'Hello world') RETURNING id",
                (dataset_id,),
            )
            doc_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO corpus.chunks (document_id, chunk_index, text) "
                "VALUES (%s, 0, 'Hello world')",
                (doc_id,),
            )

        apply_migrations(backend, _schema_dir())

        with pg.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT content_hash FROM corpus.chunks WHERE document_id = %s",
                (doc_id,),
            )
            row = cur.fetchone()
        assert row is not None
        assert row[0] is not None
        assert row[0] == chunk_content_hash("Hello world")

    def test_backfill_idempotent(self, pg_dsn):
        """Re-running migration after backfill is a no-op."""
        backend = _make_backend(pg_dsn)
        backend.migrate()
        apply_migrations(backend, _schema_dir())
        apply_migrations(backend, _schema_dir())
