"""Phase O Wave 1 — analyze signals tables.

Revision ID: 0012_analyze_signals
Revises: 0011_image_embeddings
Create Date: 2026-05-19 00:00:00.000000

Adds two tables that underpin the Phase O EDA + corpus-cleaning pipeline:

- ``chunk_quality_signals`` — per-chunk learned quality scores from
  various signal sources (e.g. LLM judge, heuristic, classifier).
- ``near_duplicate_clusters`` — MinHash LSH near-duplicate groups that
  link chunks to a shared cluster identifier.

Both tables carry a ``chunk_id`` FK to ``chunks(id)`` with ON DELETE CASCADE
so quality data is cleaned up automatically when a chunk is removed.
Forward-only per project convention (0008, 0010, 0011).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0012_analyze_signals"
down_revision: str | None = "0011_image_embeddings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        _upgrade_postgres()
    elif dialect == "sqlite":
        _upgrade_sqlite()
    else:
        raise NotImplementedError(f"unsupported dialect: {dialect}")


def downgrade() -> None:
    pass


def _upgrade_postgres() -> None:
    op.execute("""
CREATE TABLE corpus.chunk_quality_signals (
  id           BIGSERIAL PRIMARY KEY,
  chunk_id     BIGINT NOT NULL REFERENCES corpus.chunks(id) ON DELETE CASCADE,
  signal_name  TEXT NOT NULL,
  signal_value REAL,
  source       TEXT NOT NULL,
  computed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
""")
    op.execute(
        "CREATE INDEX chunk_quality_signals_chunk_signal_idx"
        " ON corpus.chunk_quality_signals(chunk_id, signal_name)"
    )
    op.execute("""
CREATE TABLE corpus.near_duplicate_clusters (
  id          BIGSERIAL PRIMARY KEY,
  cluster_id  TEXT NOT NULL,
  chunk_id    BIGINT NOT NULL REFERENCES corpus.chunks(id) ON DELETE CASCADE,
  similarity  REAL,
  method      TEXT NOT NULL,
  computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
""")
    op.execute(
        "CREATE INDEX near_duplicate_clusters_cluster_idx"
        " ON corpus.near_duplicate_clusters(cluster_id)"
    )


def _upgrade_sqlite() -> None:
    op.execute("""
CREATE TABLE chunk_quality_signals (
  id           INTEGER PRIMARY KEY,
  chunk_id     INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
  signal_name  TEXT NOT NULL,
  signal_value REAL,
  source       TEXT NOT NULL,
  computed_at  TEXT NOT NULL DEFAULT (datetime('now'))
)
""")
    op.execute(
        "CREATE INDEX chunk_quality_signals_chunk_signal_idx"
        " ON chunk_quality_signals(chunk_id, signal_name)"
    )
    op.execute("""
CREATE TABLE near_duplicate_clusters (
  id          INTEGER PRIMARY KEY,
  cluster_id  TEXT NOT NULL,
  chunk_id    INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
  similarity  REAL,
  method      TEXT NOT NULL,
  computed_at TEXT NOT NULL DEFAULT (datetime('now'))
)
""")
    op.execute(
        "CREATE INDEX near_duplicate_clusters_cluster_idx ON near_duplicate_clusters(cluster_id)"
    )
