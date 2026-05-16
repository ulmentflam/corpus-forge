"""image_embeddings_<embedder> tables (Phase G — P1)

Revision ID: 0011_image_embeddings
Revises: 0010_document_label_confidence
Create Date: 2026-05-15 00:00:00.000000

Phase G (P1) — adds the **dynamic** ``image_embeddings_<embedder>``
per-embedder table family alongside the existing
``embeddings_<embedder>`` text family.

The text family is provisioned by
:meth:`PostgresBackend._create_embedder_table` /
:meth:`SQLiteBackend.register_embedder` at runtime — one table per
registered embedder, named after the (sanitised) embedder name. The
image family follows the exact same pattern via the new helpers landed
in :meth:`StorageBackend.register_multimodal_embedder`.

This migration therefore performs no schema-level DDL (per-embedder
tables come from the backend layer, not from the migration history),
but it **does** add an ``image`` boolean column to the existing
``embedders`` registry table so the runtime can discriminate text
embedders from image embedders when listing registrations. The column
is NULL-defaulted-FALSE so every pre-Phase-G row remains valid.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_image_embeddings"
down_revision: str | None = "0010_document_label_confidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        _upgrade_postgres()
    elif dialect == "sqlite":
        _upgrade_sqlite(bind)
    else:
        raise NotImplementedError(f"unsupported dialect: {dialect}")


def downgrade() -> None:
    # Forward-only — Phase F+ convention. The column is additive and
    # FALSE-defaulted so a downgrade would be a no-op for production
    # rows anyway.
    pass


def _upgrade_postgres() -> None:
    op.execute(
        "ALTER TABLE corpus.embedders ADD COLUMN IF NOT EXISTS image BOOLEAN NOT NULL DEFAULT FALSE"
    )


def _upgrade_sqlite(bind) -> None:
    # SQLite supports ALTER TABLE ... ADD COLUMN with a NOT NULL DEFAULT
    # — no table rewrite required.
    cols = {row[1] for row in bind.execute(sa.text("PRAGMA table_info(embedders)")).fetchall()}
    if "image" in cols:
        return
    op.execute("ALTER TABLE embedders ADD COLUMN image INTEGER NOT NULL DEFAULT 0")
