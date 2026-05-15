"""Integration test for the alembic revision adding
`document_labels.confidence` (C-04).

Runs against both Postgres (testcontainers) and a tmpfile SQLite.

Asserts:
- The column exists after `migrate()`.
- The column is nullable (legacy `apply_label` calls without
  `confidence` continue to work — backwards-compat).
- A row written with `confidence` round-trips through `apply_label`.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from corpus_forge.backends.postgres import PostgresBackend
from corpus_forge.backends.sqlite import SQLiteBackend

pytestmark = [pytest.mark.integration]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_test_document_pg(backend: PostgresBackend, dataset_id: int) -> int:
    rows = backend._execute(
        """
        INSERT INTO corpus.documents (dataset_id, source_uri, content_hash, text)
        VALUES (%s, %s, %s, %s)
        RETURNING id
        """,
        (dataset_id, "file:///x.md", "deadbeef", "body"),
    )
    return int(rows[0]["id"])


def _ensure_dataset_pg(backend: PostgresBackend) -> int:
    rows = backend._execute(
        "INSERT INTO corpus.datasets (name, kind) VALUES (%s, %s) RETURNING id",
        ("conf-test", "text"),
    )
    return int(rows[0]["id"])


def _ensure_dataset_sqlite(backend: SQLiteBackend) -> int:
    backend._execute(
        "INSERT INTO datasets (name, kind) VALUES (?, ?)",
        ("conf-test", "text"),
    )
    rows = backend._execute("SELECT id FROM datasets WHERE name = ?", ("conf-test",))
    return int(rows[0]["id"])


def _ensure_test_document_sqlite(backend: SQLiteBackend, dataset_id: int) -> int:
    backend._execute(
        """
        INSERT INTO documents (dataset_id, source_uri, content_hash, text)
        VALUES (?, ?, ?, ?)
        """,
        (dataset_id, "file:///x.md", "deadbeef", "body"),
    )
    rows = backend._execute("SELECT id FROM documents WHERE source_uri = ?", ("file:///x.md",))
    return int(rows[0]["id"])


# ---------------------------------------------------------------------------
# Postgres
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker
class TestPostgresConfidenceColumn:
    def test_column_exists_and_nullable(self, pg_dsn: str) -> None:
        backend = PostgresBackend(dsn=pg_dsn, schema="corpus")
        backend.migrate()

        rows = backend._execute(
            """
            SELECT column_name, is_nullable, data_type
            FROM information_schema.columns
            WHERE table_schema = 'corpus'
              AND table_name = 'document_labels'
              AND column_name = 'confidence'
            """,
        )
        assert len(rows) == 1, "document_labels.confidence missing after migrate()"
        assert rows[0]["is_nullable"].upper() == "YES"

    def test_legacy_row_without_confidence_still_works(self, pg_dsn: str) -> None:
        backend = PostgresBackend(dsn=pg_dsn, schema="corpus")
        backend.migrate()
        dataset_id = _ensure_dataset_pg(backend)
        doc_id = _ensure_test_document_pg(backend, dataset_id)

        # Legacy call shape — no confidence.
        backend.apply_label("document", doc_id, "format", "markdown", source="extractor")

        rows = backend._execute(
            """
            SELECT confidence FROM corpus.document_labels
            WHERE document_id = %s
            """,
            (doc_id,),
        )
        assert len(rows) == 1
        assert rows[0]["confidence"] is None

    def test_new_row_persists_confidence(self, pg_dsn: str) -> None:
        backend = PostgresBackend(dsn=pg_dsn, schema="corpus")
        backend.migrate()
        dataset_id = _ensure_dataset_pg(backend)
        doc_id = _ensure_test_document_pg(backend, dataset_id)

        backend.apply_label(
            "document",
            doc_id,
            "class",
            "note",
            source="classifier:rule",
            confidence=0.55,
        )

        rows = backend._execute(
            """
            SELECT dl.confidence, l.namespace, l.value
            FROM corpus.document_labels dl
            JOIN corpus.labels l ON l.id = dl.label_id
            WHERE dl.document_id = %s AND l.namespace = %s
            """,
            (doc_id, "class"),
        )
        assert len(rows) == 1
        assert rows[0]["value"] == "note"
        assert rows[0]["confidence"] == pytest.approx(0.55)


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------


class TestSQLiteConfidenceColumn:
    def test_column_exists_and_nullable(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = SQLiteBackend(path=str(db_path), schema="corpus")
        backend.migrate()

        # PRAGMA table_info returns (cid, name, type, notnull, dflt, pk)
        with sqlite3.connect(str(db_path)) as conn:
            rows = conn.execute("PRAGMA table_info(document_labels)").fetchall()
        cols = {r[1]: r for r in rows}
        assert "confidence" in cols, f"document_labels.confidence missing; columns: {list(cols)}"
        # notnull == 0 means nullable
        assert cols["confidence"][3] == 0

    def test_legacy_row_without_confidence_still_works(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = SQLiteBackend(path=str(db_path), schema="corpus")
        backend.migrate()
        dataset_id = _ensure_dataset_sqlite(backend)
        doc_id = _ensure_test_document_sqlite(backend, dataset_id)

        backend.apply_label("document", doc_id, "format", "markdown", source="extractor")

        rows = backend._execute(
            "SELECT confidence FROM document_labels WHERE document_id = ?",
            (doc_id,),
        )
        assert len(rows) == 1
        assert rows[0]["confidence"] is None

    def test_new_row_persists_confidence(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = SQLiteBackend(path=str(db_path), schema="corpus")
        backend.migrate()
        dataset_id = _ensure_dataset_sqlite(backend)
        doc_id = _ensure_test_document_sqlite(backend, dataset_id)

        backend.apply_label(
            "document",
            doc_id,
            "class",
            "note",
            source="classifier:rule",
            confidence=0.55,
        )

        rows = backend._execute(
            """
            SELECT dl.confidence, l.namespace, l.value
            FROM document_labels dl
            JOIN labels l ON l.id = dl.label_id
            WHERE dl.document_id = ? AND l.namespace = ?
            """,
            (doc_id, "class"),
        )
        assert len(rows) == 1
        assert rows[0]["value"] == "note"
        assert rows[0]["confidence"] == pytest.approx(0.55)
