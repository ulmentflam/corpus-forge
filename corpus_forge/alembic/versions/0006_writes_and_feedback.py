"""writes and feedback (description columns, mcp_audit, feedback)

Revision ID: 0006_writes_and_feedback
Revises: 0005_fts
Create Date: 2026-05-13 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_writes_and_feedback"
down_revision: str | None = "0005_fts"
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
    pass  # Phase F is forward-only


def _upgrade_postgres() -> None:
    # Add description column to documents, conversations, and chunks.
    op.execute("ALTER TABLE corpus.documents ADD COLUMN IF NOT EXISTS description TEXT")
    op.execute("ALTER TABLE corpus.conversations ADD COLUMN IF NOT EXISTS description TEXT")
    op.execute("ALTER TABLE corpus.chunks ADD COLUMN IF NOT EXISTS description TEXT")

    # Append-only audit log for MCP tool invocations.
    # No foreign keys — intentional; FKs would block soft-deletes on entities.
    op.execute("""
CREATE TABLE IF NOT EXISTS corpus.mcp_audit (
  id           BIGSERIAL PRIMARY KEY,
  ts           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  host         TEXT NOT NULL,
  client       TEXT,
  session_id   TEXT,
  tool         TEXT NOT NULL,
  entity_type  TEXT NOT NULL,
  entity_id    BIGINT NOT NULL,
  before       JSONB,
  after        JSONB,
  dry_run      BOOLEAN NOT NULL DEFAULT FALSE
)
""")

    op.execute(
        "CREATE INDEX IF NOT EXISTS mcp_audit_entity_idx"
        " ON corpus.mcp_audit(entity_type, entity_id)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS mcp_audit_session_idx ON corpus.mcp_audit(session_id)")

    # User-facing feedback / ratings on corpus entities.
    # No foreign keys — intentional; append-only log immune to entity soft-deletes.
    op.execute("""
CREATE TABLE IF NOT EXISTS corpus.feedback (
  id           BIGSERIAL PRIMARY KEY,
  ts           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  host         TEXT NOT NULL,
  client       TEXT,
  session_id   TEXT,
  entity_type  TEXT NOT NULL,
  entity_id    BIGINT NOT NULL,
  kind         TEXT NOT NULL,
  rating       INTEGER,
  text         TEXT,
  metadata     JSONB NOT NULL DEFAULT '{}'::jsonb
)
""")

    op.execute(
        "CREATE INDEX IF NOT EXISTS feedback_entity_idx ON corpus.feedback(entity_type, entity_id)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS feedback_session_idx ON corpus.feedback(session_id)")


def _upgrade_sqlite() -> None:
    # SQLAlchemy SQLite dialect rejects `IF NOT EXISTS` on `ALTER TABLE ADD COLUMN`
    # (established in D-03). Strip `IF NOT EXISTS` from ALTER statements.
    # Use unqualified table names (no corpus. prefix).
    # BIGSERIAL -> INTEGER PRIMARY KEY, TIMESTAMPTZ -> TEXT, JSONB -> TEXT, BOOLEAN -> INTEGER.

    op.execute("ALTER TABLE documents ADD COLUMN description TEXT")
    op.execute("ALTER TABLE conversations ADD COLUMN description TEXT")
    op.execute("ALTER TABLE chunks ADD COLUMN description TEXT")

    op.execute("""
CREATE TABLE IF NOT EXISTS mcp_audit (
  id           INTEGER PRIMARY KEY,
  ts           TEXT NOT NULL DEFAULT (datetime('now')),
  host         TEXT NOT NULL,
  client       TEXT,
  session_id   TEXT,
  tool         TEXT NOT NULL,
  entity_type  TEXT NOT NULL,
  entity_id    INTEGER NOT NULL,
  before       TEXT,
  after        TEXT,
  dry_run      INTEGER NOT NULL DEFAULT 0
)
""")

    op.execute(
        "CREATE INDEX IF NOT EXISTS mcp_audit_entity_idx ON mcp_audit(entity_type, entity_id)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS mcp_audit_session_idx ON mcp_audit(session_id)")

    op.execute("""
CREATE TABLE IF NOT EXISTS feedback (
  id           INTEGER PRIMARY KEY,
  ts           TEXT NOT NULL DEFAULT (datetime('now')),
  host         TEXT NOT NULL,
  client       TEXT,
  session_id   TEXT,
  entity_type  TEXT NOT NULL,
  entity_id    INTEGER NOT NULL,
  kind         TEXT NOT NULL,
  rating       INTEGER,
  text         TEXT,
  metadata     TEXT NOT NULL DEFAULT '{}'
)
""")

    op.execute("CREATE INDEX IF NOT EXISTS feedback_entity_idx ON feedback(entity_type, entity_id)")
    op.execute("CREATE INDEX IF NOT EXISTS feedback_session_idx ON feedback(session_id)")
