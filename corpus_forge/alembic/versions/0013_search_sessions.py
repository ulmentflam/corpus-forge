"""Phase P Wave 1 — search sessions tables.

Revision ID: 0013_search_sessions
Revises: 0012_analyze_signals
Create Date: 2026-05-20 00:00:00.000000

Adds two tables that underpin the Phase P search-session telemetry pipeline:

- ``search_sessions`` — one row per search call, keyed on the originating
  dataset.  Records the raw query text, an optional client identifier, and
  the host that served the request.
- ``search_result_events`` — one row per result item returned within a
  session.  Carries the signal name (e.g. ``relevance``, ``click``,
  ``thumbs_up``), an optional numeric value, a source tag, and an optional
  pointer to a replacement chunk when the signal records a curation
  suggestion.

``search_sessions.dataset_id`` FKs to ``datasets(id)`` ON DELETE CASCADE.
``search_result_events.session_id`` FKs to ``search_sessions(id)`` ON DELETE CASCADE.
``search_result_events.chunk_id`` FKs to ``chunks(id)`` ON DELETE CASCADE.
``search_result_events.replacement_chunk_id`` is a nullable weak FK to
``chunks(id)``; no cascade is enforced on this column (the referenced chunk
may be removed without invalidating the event record).

Forward-only per project convention (0008, 0010, 0011, 0012).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0013_search_sessions"
down_revision: str | None = "0012_analyze_signals"
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
CREATE TABLE corpus.search_sessions (
  id          BIGSERIAL PRIMARY KEY,
  query       TEXT NOT NULL,
  dataset_id  BIGINT NOT NULL REFERENCES corpus.datasets(id) ON DELETE CASCADE,
  started_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  client      TEXT,
  host        TEXT
)
""")
    op.execute(
        "CREATE INDEX search_sessions_dataset_started_idx"
        " ON corpus.search_sessions(dataset_id, started_at)"
    )
    op.execute("""
CREATE TABLE corpus.search_result_events (
  id                   BIGSERIAL PRIMARY KEY,
  session_id           BIGINT NOT NULL REFERENCES corpus.search_sessions(id) ON DELETE CASCADE,
  chunk_id             BIGINT NOT NULL REFERENCES corpus.chunks(id) ON DELETE CASCADE,
  signal               TEXT NOT NULL,
  value                REAL,
  source               TEXT NOT NULL,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  replacement_chunk_id BIGINT REFERENCES corpus.chunks(id) ON DELETE SET NULL
)
""")
    op.execute(
        "CREATE INDEX search_result_events_session_chunk_idx"
        " ON corpus.search_result_events(session_id, chunk_id)"
    )


def _upgrade_sqlite() -> None:
    op.execute("""
CREATE TABLE search_sessions (
  id          INTEGER PRIMARY KEY,
  query       TEXT NOT NULL,
  dataset_id  INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
  started_at  TEXT NOT NULL DEFAULT (datetime('now')),
  client      TEXT,
  host        TEXT
)
""")
    op.execute(
        "CREATE INDEX search_sessions_dataset_started_idx"
        " ON search_sessions(dataset_id, started_at)"
    )
    op.execute("""
CREATE TABLE search_result_events (
  id                   INTEGER PRIMARY KEY,
  session_id           INTEGER NOT NULL REFERENCES search_sessions(id) ON DELETE CASCADE,
  chunk_id             INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
  signal               TEXT NOT NULL,
  value                REAL,
  source               TEXT NOT NULL,
  created_at           TEXT NOT NULL DEFAULT (datetime('now')),
  replacement_chunk_id INTEGER REFERENCES chunks(id) ON DELETE SET NULL
)
""")
    op.execute(
        "CREATE INDEX search_result_events_session_chunk_idx"
        " ON search_result_events(session_id, chunk_id)"
    )
