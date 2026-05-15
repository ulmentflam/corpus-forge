"""document_labels.confidence (optional REAL column for classifier output)

Revision ID: 0010_document_label_confidence
Revises: 0009_feedback_host_default
Create Date: 2026-05-15 00:00:00.000000

Phase E (C-04) — adds an optional ``confidence REAL`` column to
``document_labels`` so the document-level classifier output mirrors the
existing ``chunk_labels.confidence`` shape. NULL default keeps every
pre-existing ``document_labels`` row valid (the column is purely
additive).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_document_label_confidence"
down_revision: str | None = "0009_feedback_host_default"
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
    # Forward-only — Phase F convention. The column is additive and
    # NULL-defaulted so a downgrade would be a no-op for production
    # rows anyway.
    pass


def _upgrade_postgres() -> None:
    op.execute("ALTER TABLE corpus.document_labels ADD COLUMN IF NOT EXISTS confidence REAL")


def _upgrade_sqlite() -> None:
    # SQLite supports ALTER TABLE ... ADD COLUMN for nullable columns
    # without rewriting the table. ``op.batch_alter_table`` would also
    # work but isn't needed for a simple add.
    bind = op.get_bind()
    cols = {
        row[1] for row in bind.execute(sa.text("PRAGMA table_info(document_labels)")).fetchall()
    }
    if "confidence" in cols:
        return
    op.execute("ALTER TABLE document_labels ADD COLUMN confidence REAL")
