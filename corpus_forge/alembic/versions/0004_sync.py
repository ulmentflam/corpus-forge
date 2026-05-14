"""sync (document revisions + tombstone + per-source sync state)

Revision ID: 0004_sync
Revises: 0003_views
Create Date: 2026-05-13 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_sync"
down_revision: str | None = "0003_views"
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
    # Reproduce schema/003_sync.sql exactly.
    op.execute("""
CREATE TABLE IF NOT EXISTS corpus.document_revisions (
  id                BIGSERIAL PRIMARY KEY,
  document_id       BIGINT NOT NULL REFERENCES corpus.documents(id) ON DELETE CASCADE,
  revision_number   BIGINT NOT NULL,
  parent_revision_id BIGINT REFERENCES corpus.document_revisions(id) ON DELETE SET NULL,
  content_hash      TEXT NOT NULL,
  text              TEXT NOT NULL DEFAULT '',
  author_host       TEXT NOT NULL,
  is_tombstone      BOOLEAN NOT NULL DEFAULT FALSE,
  metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (document_id, revision_number)
)
""")

    op.execute("""
CREATE INDEX IF NOT EXISTS document_revisions_doc_idx
  ON corpus.document_revisions(document_id)
""")

    op.execute("""
CREATE INDEX IF NOT EXISTS document_revisions_parent_idx
  ON corpus.document_revisions(parent_revision_id)
""")

    op.execute("ALTER TABLE corpus.documents ADD COLUMN IF NOT EXISTS tombstoned_at TIMESTAMPTZ")

    op.execute("ALTER TABLE corpus.sources ADD COLUMN IF NOT EXISTS last_pulled_revision_id BIGINT")

    op.execute(
        "ALTER TABLE corpus.sources ADD COLUMN IF NOT EXISTS sync_enabled BOOLEAN DEFAULT FALSE"
    )


def _upgrade_sqlite() -> None:
    # Reproduce schema/sqlite/003_sync.sql exactly.
    # SQLAlchemy SQLite dialect rejects `IF NOT EXISTS` on `ALTER TABLE ADD COLUMN`
    # (seen in D-03). Strip `IF NOT EXISTS` from ALTER statements on this branch.
    # CREATE INDEX IF NOT EXISTS and CREATE TABLE IF NOT EXISTS pass through fine.
    op.execute("""
CREATE TABLE IF NOT EXISTS document_revisions (
  id                 INTEGER PRIMARY KEY,
  document_id        INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  revision_number    INTEGER NOT NULL,
  parent_revision_id INTEGER REFERENCES document_revisions(id) ON DELETE SET NULL,
  content_hash       TEXT NOT NULL,
  text               TEXT NOT NULL DEFAULT '',
  author_host        TEXT NOT NULL,
  is_tombstone       INTEGER NOT NULL DEFAULT 0,
  metadata           TEXT NOT NULL DEFAULT '{}',
  created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  UNIQUE (document_id, revision_number)
)
""")

    op.execute("""
CREATE INDEX IF NOT EXISTS document_revisions_doc_idx
  ON document_revisions(document_id)
""")

    op.execute("""
CREATE INDEX IF NOT EXISTS document_revisions_parent_idx
  ON document_revisions(parent_revision_id)
""")

    op.execute("ALTER TABLE documents ADD COLUMN tombstoned_at TEXT")

    op.execute("ALTER TABLE sources ADD COLUMN last_pulled_revision_id INTEGER")

    op.execute("ALTER TABLE sources ADD COLUMN sync_enabled INTEGER DEFAULT 0")
