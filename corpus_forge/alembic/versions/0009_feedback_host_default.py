"""feedback.host default for SQLite (makes bare INSERT viable without host)

Revision ID: 0009_feedback_host_default
Revises: 0008_feedback_sessions
Create Date: 2026-05-14 00:00:00.000000

SQLite does not support ALTER TABLE ... ALTER COLUMN, so we recreate the
feedback table with host TEXT NOT NULL DEFAULT 'localhost'.  Postgres already
handles host at the application layer (socket.gethostname() in add_feedback),
so no schema change is needed there.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009_feedback_host_default"
down_revision: str | None = "0008_feedback_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "sqlite":
        _upgrade_sqlite()
    # Postgres: no change needed — host is always supplied by the application.


def downgrade() -> None:
    pass


def _upgrade_sqlite() -> None:
    """Recreate feedback table with DEFAULT 'localhost' on host column."""
    op.execute("PRAGMA foreign_keys=OFF")

    op.execute("""
CREATE TABLE IF NOT EXISTS feedback_v2 (
  id           INTEGER PRIMARY KEY,
  ts           TEXT NOT NULL DEFAULT (datetime('now')),
  host         TEXT NOT NULL DEFAULT 'localhost',
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

    # Copy all existing rows.
    op.execute("""
INSERT OR IGNORE INTO feedback_v2
  (id, ts, host, client, session_id, entity_type, entity_id, kind, rating, text, metadata)
SELECT id, ts, host, client, session_id, entity_type, entity_id, kind, rating, text, metadata
FROM feedback
""")

    op.execute("DROP TABLE feedback")
    op.execute("ALTER TABLE feedback_v2 RENAME TO feedback")

    # Recreate indexes.
    op.execute("CREATE INDEX IF NOT EXISTS feedback_entity_idx ON feedback(entity_type, entity_id)")
    op.execute("CREATE INDEX IF NOT EXISTS feedback_session_idx ON feedback(session_id)")

    op.execute("PRAGMA foreign_keys=ON")
