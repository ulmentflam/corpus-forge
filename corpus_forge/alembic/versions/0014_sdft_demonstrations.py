"""Phase Q Wave 1 — SDFT (Supervised Demo Fine-Tuning) demonstrations table.

Revision ID: 0014_sdft_demonstrations
Revises: 0013_search_sessions
Create Date: 2026-05-20 00:00:00.000000

Adds one table that stores demonstration pairs for SDFT fine-tuning:

- ``sdft_demonstrations`` — one row per captured teacher→student demonstration.
  Stores the query context, prior model output (student_messages), the curated
  correction prompt (teacher_messages), the corrected target text, the source
  signal type (SDFTSource enum), an optional cross-system trace_id, and a
  content_hash for deduplication.

``sdft_demonstrations.dataset_id`` FKs to ``datasets(id)`` ON DELETE CASCADE.
``sdft_demonstrations.content_hash`` has a UNIQUE constraint so identical
demonstrations are deduplicated via INSERT ... ON CONFLICT DO NOTHING.

Indexes:
- ``(dataset_id, source)`` — per-dataset source-type queries.
- ``(trace_id)`` — cross-system trace lookup.

Forward-only per project convention (0008, 0010, 0011, 0012, 0013).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0014_sdft_demonstrations"
down_revision: str | None = "0013_search_sessions"
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
CREATE TABLE corpus.sdft_demonstrations (
  id               BIGSERIAL PRIMARY KEY,
  dataset_id       BIGINT NOT NULL REFERENCES corpus.datasets(id) ON DELETE CASCADE,
  query            TEXT NOT NULL,
  student_messages JSONB NOT NULL,
  teacher_messages JSONB NOT NULL,
  target           TEXT NOT NULL,
  source           TEXT NOT NULL,
  trace_id         TEXT,
  content_hash     TEXT NOT NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (content_hash)
)
""")
    op.execute(
        "CREATE INDEX sdft_demonstrations_dataset_source_idx"
        " ON corpus.sdft_demonstrations(dataset_id, source)"
    )
    op.execute(
        "CREATE INDEX sdft_demonstrations_trace_id_idx ON corpus.sdft_demonstrations(trace_id)"
    )


def _upgrade_sqlite() -> None:
    op.execute("""
CREATE TABLE sdft_demonstrations (
  id               INTEGER PRIMARY KEY,
  dataset_id       INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
  query            TEXT NOT NULL,
  student_messages TEXT NOT NULL,
  teacher_messages TEXT NOT NULL,
  target           TEXT NOT NULL,
  source           TEXT NOT NULL,
  trace_id         TEXT,
  content_hash     TEXT NOT NULL,
  created_at       TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (content_hash)
)
""")
    op.execute(
        "CREATE INDEX sdft_demonstrations_dataset_source_idx"
        " ON sdft_demonstrations(dataset_id, source)"
    )
    op.execute("CREATE INDEX sdft_demonstrations_trace_id_idx ON sdft_demonstrations(trace_id)")
