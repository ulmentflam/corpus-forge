"""Integration test for the Phase G (G-13) image_embeddings migration.

Asserts:

- After ``migrate()`` the ``embedders.image`` column exists.
- The column defaults to FALSE/0 so pre-G rows remain valid.
- ``register_multimodal_embedder`` flips it to TRUE/1 and creates a
  parallel ``image_embeddings_<name>`` table.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from corpus_forge.backends.postgres import PostgresBackend
from corpus_forge.backends.sqlite import SQLiteBackend

pytestmark = [pytest.mark.integration]


# ── Postgres ────────────────────────────────────────────────────────────


@pytest.mark.requires_docker
class TestPostgresImageEmbeddings:
    def test_image_column_exists_and_defaults_false(self, pg_dsn: str) -> None:
        backend = PostgresBackend(dsn=pg_dsn, schema="corpus")
        backend.migrate()

        rows = backend._execute(
            """
            SELECT column_name, data_type, column_default
            FROM information_schema.columns
            WHERE table_schema = 'corpus'
              AND table_name = 'embedders'
              AND column_name = 'image'
            """,
        )
        assert len(rows) == 1, "embedders.image missing after migrate()"
        assert rows[0]["data_type"].lower() == "boolean"
        assert "false" in str(rows[0]["column_default"]).lower()

    def test_register_multimodal_provisions_image_table(self, pg_dsn: str) -> None:
        backend = PostgresBackend(dsn=pg_dsn, schema="corpus")
        backend.migrate()
        emb_id = backend.register_multimodal_embedder(
            name="clip_local", model_id="clip-ViT-B-32", dimension=4
        )
        assert emb_id > 0
        rows = backend._execute(
            "SELECT name, image, table_name FROM corpus.embedders WHERE id = %s",
            (emb_id,),
        )
        assert rows[0]["image"] is True
        assert rows[0]["table_name"] == "image_embeddings_clip_local"

        # Table exists.
        table_rows = backend._execute(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'corpus' AND table_name = 'image_embeddings_clip_local'
            """,
        )
        assert len(table_rows) == 1


# ── SQLite ──────────────────────────────────────────────────────────────


class TestSQLiteImageEmbeddings:
    def test_image_column_exists_and_defaults_zero(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = SQLiteBackend(path=str(db_path), schema="corpus")
        backend.migrate()
        with sqlite3.connect(str(db_path)) as conn:
            rows = conn.execute("PRAGMA table_info(embedders)").fetchall()
        cols = {r[1]: r for r in rows}
        assert "image" in cols, f"embedders.image missing; columns: {list(cols)}"
        # PRAGMA columns: (cid, name, type, notnull, dflt, pk)
        assert cols["image"][3] == 1  # notnull
        assert str(cols["image"][4]) == "0"

    def test_register_multimodal_provisions_image_table(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = SQLiteBackend(path=str(db_path), schema="corpus")
        backend.migrate()
        emb_id = backend.register_multimodal_embedder(
            name="clip_local", model_id="clip-ViT-B-32", dimension=4
        )
        rows = backend._execute(
            "SELECT name, image, table_name FROM embedders WHERE id = ?", (emb_id,)
        )
        assert rows[0]["image"] == 1
        assert rows[0]["table_name"] == "image_embeddings_clip_local"
        # Table exists.
        with sqlite3.connect(str(db_path)) as conn:
            existing = conn.execute(
                "SELECT name FROM sqlite_master WHERE name = 'image_embeddings_clip_local'"
            ).fetchall()
        assert len(existing) == 1
