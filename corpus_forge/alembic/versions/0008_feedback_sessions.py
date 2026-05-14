"""feedback_sessions and feedback_events tables

Revision ID: 0008_feedback_sessions
Revises: 0007_chat_templates
Create Date: 2026-05-14 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008_feedback_sessions"
down_revision: str | None = "0007_chat_templates"
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
CREATE TABLE corpus.feedback_sessions (
  id              BIGSERIAL PRIMARY KEY,
  client          TEXT NOT NULL,
  session_id      TEXT NOT NULL,
  host            TEXT NOT NULL,
  started_at      TIMESTAMPTZ NOT NULL,
  ended_at        TIMESTAMPTZ,
  conversation_id BIGINT REFERENCES corpus.conversations(id) ON DELETE SET NULL,
  UNIQUE (client, session_id)
)
""")
    op.execute("""
CREATE TABLE corpus.feedback_events (
  id                  BIGSERIAL PRIMARY KEY,
  feedback_session_id BIGINT NOT NULL REFERENCES corpus.feedback_sessions(id) ON DELETE CASCADE,
  audit_id            BIGINT REFERENCES corpus.mcp_audit(id) ON DELETE CASCADE,
  feedback_id         BIGINT REFERENCES corpus.feedback(id) ON DELETE CASCADE,
  entity_type         TEXT NOT NULL,
  entity_id           BIGINT NOT NULL,
  ts                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
""")
    op.execute(
        "CREATE INDEX feedback_events_session_idx ON corpus.feedback_events(feedback_session_id)"
    )


def _upgrade_sqlite() -> None:
    op.execute("""
CREATE TABLE feedback_sessions (
  id              INTEGER PRIMARY KEY,
  client          TEXT NOT NULL,
  session_id      TEXT NOT NULL,
  host            TEXT NOT NULL,
  started_at      TEXT NOT NULL,
  ended_at        TEXT,
  conversation_id INTEGER REFERENCES conversations(id) ON DELETE SET NULL,
  UNIQUE (client, session_id)
)
""")
    op.execute("""
CREATE TABLE feedback_events (
  id                  INTEGER PRIMARY KEY,
  feedback_session_id INTEGER NOT NULL REFERENCES feedback_sessions(id) ON DELETE CASCADE,
  audit_id            INTEGER REFERENCES mcp_audit(id) ON DELETE CASCADE,
  feedback_id         INTEGER REFERENCES feedback(id) ON DELETE CASCADE,
  entity_type         TEXT NOT NULL,
  entity_id           INTEGER NOT NULL,
  ts                  TEXT NOT NULL DEFAULT (datetime('now'))
)
""")
    op.execute("CREATE INDEX feedback_events_session_idx ON feedback_events(feedback_session_id)")
