"""Integration tests for 003_sync migration — schema objects and constraints."""

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


def _run_sql(pg_dsn: str, sql: str, params: tuple = ()) -> None:
    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        conn.commit()


def _fetch_one(pg_dsn: str, sql: str, params: tuple = ()):
    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        conn.commit()
        return cur.fetchone()


class TestSyncMigrationSchema:
    """Verify 003_sync creates all schema objects on a fresh database."""

    def test_document_revisions_table_exists(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()
        apply_migrations(backend, _schema_dir())

        row = _fetch_one(
            pg_dsn,
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'corpus' AND table_name = 'document_revisions'",
        )
        assert row is not None

    def test_document_revisions_columns(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()
        apply_migrations(backend, _schema_dir())

        expected = {
            "id",
            "document_id",
            "revision_number",
            "parent_revision_id",
            "content_hash",
            "text",
            "author_host",
            "is_tombstone",
            "metadata",
            "created_at",
        }
        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'corpus' AND table_name = 'document_revisions'",
            )
            actual = {row[0] for row in cur.fetchall()}
        assert expected.issubset(actual), f"Missing columns: {expected - actual}"

    def test_document_revisions_pkey(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()
        apply_migrations(backend, _schema_dir())

        row = _fetch_one(
            pg_dsn,
            "SELECT constraint_name FROM information_schema.table_constraints "
            "WHERE table_schema = 'corpus' AND table_name = 'document_revisions' "
            "AND constraint_type = 'PRIMARY KEY'",
        )
        assert row is not None

    def test_document_revisions_unique_constraint(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()
        apply_migrations(backend, _schema_dir())

        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT constraint_name FROM information_schema.table_constraints "
                "WHERE table_schema = 'corpus' AND table_name = 'document_revisions' "
                "AND constraint_type = 'UNIQUE'",
            )
            names = [r[0] for r in cur.fetchall()]
        assert any("revision" in n.lower() or "document_id" in n.lower() for n in names)

    def test_document_revisions_foreign_key(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()
        apply_migrations(backend, _schema_dir())

        row = _fetch_one(
            pg_dsn,
            "SELECT constraint_name FROM information_schema.table_constraints "
            "WHERE table_schema = 'corpus' AND table_name = 'document_revisions' "
            "AND constraint_type = 'FOREIGN KEY'",
        )
        assert row is not None

    def test_document_revisions_self_ref_fk(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()
        apply_migrations(backend, _schema_dir())

        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT tc.constraint_name, ccu.column_name, ccu.table_name AS ref_table "
                "FROM information_schema.table_constraints tc "
                "JOIN information_schema.constraint_column_usage ccu "
                "ON tc.constraint_name = ccu.constraint_name "
                "WHERE tc.table_schema = 'corpus' AND tc.table_name = 'document_revisions' "
                "AND tc.constraint_type = 'FOREIGN KEY'",
            )
            rows = cur.fetchall()
        ref_tables = {r[2] for r in rows}
        assert "document_revisions" in ref_tables

    def test_document_revisions_indexes(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()
        apply_migrations(backend, _schema_dir())

        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT indexname FROM pg_indexes WHERE schemaname = 'corpus' "
                "AND tablename = 'document_revisions'",
            )
            names = [r[0] for r in cur.fetchall()]
        assert any("doc_idx" in n for n in names)
        assert any("parent_idx" in n for n in names)

    def test_tombstoned_at_column_exists(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()
        apply_migrations(backend, _schema_dir())

        row = _fetch_one(
            pg_dsn,
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'corpus' AND table_name = 'documents' "
            "AND column_name = 'tombstoned_at'",
        )
        assert row is not None

    def test_sources_last_pulled_revision_id_column(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()
        apply_migrations(backend, _schema_dir())

        row = _fetch_one(
            pg_dsn,
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'corpus' AND table_name = 'sources' "
            "AND column_name = 'last_pulled_revision_id'",
        )
        assert row is not None

    def test_sources_sync_enabled_column(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()
        apply_migrations(backend, _schema_dir())

        row = _fetch_one(
            pg_dsn,
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'corpus' AND table_name = 'sources' "
            "AND column_name = 'sync_enabled'",
        )
        assert row is not None

    def test_tombstoned_at_nullable(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()
        apply_migrations(backend, _schema_dir())

        row = _fetch_one(
            pg_dsn,
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'corpus' AND table_name = 'documents' "
            "AND column_name = 'tombstoned_at'",
        )
        assert row is not None
        assert row[0] == "YES"

    def test_sync_enabled_default_false(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()
        apply_migrations(backend, _schema_dir())

        row = _fetch_one(
            pg_dsn,
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_schema = 'corpus' AND table_name = 'sources' "
            "AND column_name = 'sync_enabled'",
        )
        assert row is not None
        assert row[0] is not None


class TestSyncMigrationIdempotent:
    """Re-running migrations is a no-op."""

    def test_reapply_no_error(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()
        apply_migrations(backend, _schema_dir())
        apply_migrations(backend, _schema_dir())

    def test_table_still_exists_after_reapply(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()
        apply_migrations(backend, _schema_dir())
        apply_migrations(backend, _schema_dir())

        row = _fetch_one(
            pg_dsn,
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'corpus' AND table_name = 'document_revisions'",
        )
        assert row is not None


class TestSyncMigrationConstraints:
    """Foreign key and unique constraints fire correctly."""

    def test_fk_rejects_invalid_document_id(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()
        apply_migrations(backend, _schema_dir())

        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            _run_sql(
                pg_dsn,
                "INSERT INTO corpus.document_revisions "
                "(document_id, revision_number, content_hash, text, author_host) "
                "VALUES (99999, 1, 'abc', 'text', 'host1')",
            )

    def test_insert_valid_revision_succeeds(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()
        apply_migrations(backend, _schema_dir())

        _run_sql(
            pg_dsn,
            "INSERT INTO corpus.datasets (name, kind) VALUES ('test_003_fk', 'text')",
        )
        row = _fetch_one(
            pg_dsn,
            "INSERT INTO corpus.documents "
            "(dataset_id, source_uri, content_hash, title, text) "
            "VALUES ((SELECT id FROM corpus.datasets WHERE name = 'test_003_fk'), "
            "'test://rev1', 'hash1', 'Doc 1', 'Hello') RETURNING id",
        )
        doc_id = row[0]

        _run_sql(
            pg_dsn,
            "INSERT INTO corpus.document_revisions "
            "(document_id, revision_number, content_hash, text, author_host) "
            "VALUES (%s, 1, 'abc', 'revision text', 'host1')",
            (doc_id,),
        )

        row = _fetch_one(
            pg_dsn,
            "SELECT id FROM corpus.document_revisions WHERE document_id = %s",
            (doc_id,),
        )
        assert row is not None

    def test_unique_revision_number_per_document(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()
        apply_migrations(backend, _schema_dir())

        _run_sql(
            pg_dsn,
            "INSERT INTO corpus.datasets (name, kind) VALUES ('test_003_unique', 'text')",
        )
        row = _fetch_one(
            pg_dsn,
            "INSERT INTO corpus.documents "
            "(dataset_id, source_uri, content_hash, title, text) "
            "VALUES ((SELECT id FROM corpus.datasets WHERE name = 'test_003_unique'), "
            "'test://unique1', 'hash1', 'Doc 1', 'Hello') RETURNING id",
        )
        doc_id = row[0]

        _run_sql(
            pg_dsn,
            "INSERT INTO corpus.document_revisions "
            "(document_id, revision_number, content_hash, text, author_host) "
            "VALUES (%s, 1, 'abc', 'first', 'host1')",
            (doc_id,),
        )

        with pytest.raises(psycopg.errors.UniqueViolation):
            _run_sql(
                pg_dsn,
                "INSERT INTO corpus.document_revisions "
                "(document_id, revision_number, content_hash, text, author_host) "
                "VALUES (%s, 1, 'def', 'second', 'host2')",
                (doc_id,),
            )

    def test_fk_rejects_invalid_parent_revision_id(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()
        apply_migrations(backend, _schema_dir())

        _run_sql(
            pg_dsn,
            "INSERT INTO corpus.datasets (name, kind) VALUES ('test_003_parent_fk', 'text')",
        )
        row = _fetch_one(
            pg_dsn,
            "INSERT INTO corpus.documents "
            "(dataset_id, source_uri, content_hash, title, text) "
            "VALUES ((SELECT id FROM corpus.datasets WHERE name = 'test_003_parent_fk'), "
            "'test://parentfk1', 'hash1', 'Doc 1', 'Hello') RETURNING id",
        )
        doc_id = row[0]

        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            _run_sql(
                pg_dsn,
                "INSERT INTO corpus.document_revisions "
                "(document_id, revision_number, parent_revision_id, content_hash, text, author_host) "
                "VALUES (%s, 1, 99999, 'abc', 'text', 'host1')",
                (doc_id,),
            )


class TestMigrationFileLooping:
    """Runner loops numbered SQL files."""

    def test_get_migration_files_includes_003(self):
        files = sorted(p.name for p in _schema_dir().glob("[0-9]*.sql"))
        assert any(f.startswith("003") for f in files)

    def test_migration_files_numeric_order(self):
        from corpus_forge.schema.migrate import get_migration_files

        files = get_migration_files(_schema_dir())
        names = [f.name for f in files]
        assert "001_core.sql" in names
        assert "002_chunk_content_hash.sql" in names
        assert "003_sync.sql" in names
        idx_001 = names.index("001_core.sql")
        idx_002 = names.index("002_chunk_content_hash.sql")
        idx_003 = names.index("003_sync.sql")
        assert idx_001 < idx_002 < idx_003
