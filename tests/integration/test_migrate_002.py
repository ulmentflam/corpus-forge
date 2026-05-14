"""Integration tests for 002_chunk_content_hash migration and backfill."""

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


class TestChunkContentHashMigration:
    """Tests for 002_chunk_content_hash migration and backfill."""

    def test_content_hash_column_exists(self, pg_dsn):
        """After apply_migrations, content_hash column exists on chunks table."""
        backend = _make_backend(pg_dsn)
        backend.migrate()
        apply_migrations(backend, _schema_dir())

        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
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

    def test_backfill_idempotent(self, pg_dsn):
        """Re-running migration after backfill is a no-op."""
        backend = _make_backend(pg_dsn)
        backend.migrate()
        apply_migrations(backend, _schema_dir())
        apply_migrations(backend, _schema_dir())
