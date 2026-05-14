"""fts (full-text search indexes / virtual tables)

Revision ID: 0005_fts
Revises: 0004_sync
Create Date: 2026-05-13 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_fts"
down_revision: str | None = "0004_sync"
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
    pass  # Phase D is forward-only


def _upgrade_postgres() -> None:
    # Reproduce schema/004_fts.sql exactly.
    # GENERATED ALWAYS AS ... STORED column auto-populates for existing rows;
    # no explicit backfill required on the Postgres side.
    op.execute(
        "ALTER TABLE corpus.chunks ADD COLUMN IF NOT EXISTS text_tsv tsvector"
        " GENERATED ALWAYS AS (to_tsvector('english', text)) STORED"
    )

    op.execute("CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON corpus.chunks USING GIN (text_tsv)")


def _upgrade_sqlite() -> None:
    # Reproduce schema/sqlite/004_fts.sql exactly.
    # FTS5 external-content virtual table mirroring the chunks table.
    op.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  text,
  content='chunks',
  content_rowid='id',
  tokenize='porter unicode61'
)""")

    # AFTER INSERT trigger keeps chunks_fts in sync for new rows.
    op.execute("""CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END""")

    # AFTER DELETE trigger removes stale entries from chunks_fts.
    op.execute("""CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', old.id, old.text);
END""")

    # AFTER UPDATE trigger removes the old entry and inserts the new one.
    op.execute("""CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', old.id, old.text);
  INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END""")

    # Backfill: re-index every existing row from the content table.
    # For external-content FTS5 tables the 'rebuild' command is the only
    # correct approach — naive INSERT … SELECT produces shadow rows without
    # matching content-table entries, yielding 0 hits at query time.
    op.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
