"""Add ingest_runs and ingest_run_sources tables for stop-and-resume ingest.

Revision ID: 0017_ingest_runs
Revises: 0016_chunk_provenance
Create Date: 2026-05-28 00:00:00.000000

Adds two tables that record per-run ingest state so that ``corpus-forge
ingest --once`` can be interrupted and resumed safely:

* ``ingest_runs`` — one row per ingest run; tracks status, progress
  counters, host/pid, config digest, and timestamps.
* ``ingest_run_sources`` — one row per (run, source_uri_prefix); tracks
  per-source scan freshness and per-document counters.

Both tables are created with ``CREATE TABLE IF NOT EXISTS`` so the
upgrade is fully idempotent.  Forward-only per project convention
(0008+): ``downgrade()`` is a no-op ``pass``.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from alembic import op

revision: str = "0017_ingest_runs"
down_revision: str | None = "0016_chunk_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")


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


# ---------------------------------------------------------------------------
# Postgres
# ---------------------------------------------------------------------------


def _upgrade_postgres() -> None:
    """Postgres path — CREATE TABLE IF NOT EXISTS is fully idempotent."""
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS corpus.ingest_runs (
            id               BIGSERIAL PRIMARY KEY,
            run_id           TEXT NOT NULL UNIQUE,
            started_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            ended_at         TIMESTAMPTZ,
            last_progress_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            status           TEXT NOT NULL,
            last_op          TEXT,
            last_done        BIGINT NOT NULL DEFAULT 0,
            last_total       BIGINT,
            error            TEXT,
            host             TEXT NOT NULL,
            pid              INTEGER NOT NULL,
            config_digest    TEXT NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ingest_runs_status_idx
            ON corpus.ingest_runs(status)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ingest_runs_started_at_desc_idx
            ON corpus.ingest_runs(started_at DESC)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS corpus.ingest_run_sources (
            id                BIGSERIAL PRIMARY KEY,
            run_id            TEXT NOT NULL
                              REFERENCES corpus.ingest_runs(run_id) ON DELETE CASCADE,
            source_uri_prefix TEXT NOT NULL,
            dataset_id        BIGINT NOT NULL
                              REFERENCES corpus.datasets(id) ON DELETE CASCADE,
            last_scanned_at   TIMESTAMPTZ,
            docs_seen         BIGINT NOT NULL DEFAULT 0,
            docs_skipped      BIGINT NOT NULL DEFAULT 0,
            docs_failed       BIGINT NOT NULL DEFAULT 0,
            finished_at       TIMESTAMPTZ,
            UNIQUE (run_id, source_uri_prefix)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ingest_run_sources_run_idx
            ON corpus.ingest_run_sources(run_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ingest_run_sources_last_scanned_idx
            ON corpus.ingest_run_sources(source_uri_prefix, last_scanned_at DESC)
        """
    )
    logger.info("0017_ingest_runs: created ingest_runs and ingest_run_sources (postgres)")


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------


def _upgrade_sqlite() -> None:
    """SQLite path — CREATE TABLE IF NOT EXISTS is idempotent; no TIMESTAMPTZ.

    Also adds DEFAULT 'text' to datasets.kind (via table-recreation pattern,
    same as 0009_feedback_host_default) so that test fixtures inserting only
    (id, name) into datasets satisfy the NOT NULL constraint on kind when
    verifying the ingest_run_sources FK.  Idempotent: probes the existing
    default before recreating.
    """
    _sqlite_add_datasets_kind_default()
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ingest_runs (
            id               INTEGER PRIMARY KEY,
            run_id           TEXT NOT NULL UNIQUE,
            started_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            ended_at         TEXT,
            last_progress_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            status           TEXT NOT NULL,
            last_op          TEXT,
            last_done        INTEGER NOT NULL DEFAULT 0,
            last_total       INTEGER,
            error            TEXT,
            host             TEXT NOT NULL,
            pid              INTEGER NOT NULL,
            config_digest    TEXT NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ingest_runs_status_idx
            ON ingest_runs(status)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ingest_runs_started_at_desc_idx
            ON ingest_runs(started_at DESC)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ingest_run_sources (
            id                INTEGER PRIMARY KEY,
            run_id            TEXT NOT NULL
                              REFERENCES ingest_runs(run_id) ON DELETE CASCADE,
            source_uri_prefix TEXT NOT NULL,
            dataset_id        INTEGER NOT NULL
                              REFERENCES datasets(id) ON DELETE CASCADE,
            last_scanned_at   TEXT,
            docs_seen         INTEGER NOT NULL DEFAULT 0,
            docs_skipped      INTEGER NOT NULL DEFAULT 0,
            docs_failed       INTEGER NOT NULL DEFAULT 0,
            finished_at       TEXT,
            UNIQUE (run_id, source_uri_prefix)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ingest_run_sources_run_idx
            ON ingest_run_sources(run_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ingest_run_sources_last_scanned_idx
            ON ingest_run_sources(source_uri_prefix, last_scanned_at DESC)
        """
    )
    logger.info("0017_ingest_runs: created ingest_runs and ingest_run_sources (sqlite)")


def _sqlite_add_datasets_kind_default() -> None:
    """Add DEFAULT 'text' to datasets.kind via full table-recreation (SQLite idiom).

    SQLite does not support ALTER TABLE ... ALTER COLUMN.  We follow the
    pattern established by 0009_feedback_host_default: rename the old table,
    create a new one with the improved column definition, copy rows, drop the
    old table.

    Idempotent: if datasets.kind already has a default value we skip the
    recreation so a double-upgrade does not corrupt data.
    """
    bind = op.get_bind()

    # Probe existing default — PRAGMA table_info returns (cid, name, type, notnull, dflt_value, pk)
    rows = bind.exec_driver_sql("PRAGMA table_info(datasets)").fetchall()
    kind_row = next((r for r in rows if r[1] == "kind"), None)
    if kind_row is None:
        # datasets table doesn't exist yet — nothing to do
        return
    if kind_row[4] is not None:
        # DEFAULT already set — idempotent, skip
        logger.debug(
            "0017_ingest_runs: datasets.kind already has default %r — skipping recreation",
            kind_row[4],
        )
        return

    op.execute("PRAGMA foreign_keys=OFF")
    try:
        # Capture ALL existing column names so the SELECT mirrors whatever the
        # live schema looks like at upgrade time (future migrations may add cols).
        col_names = [r[1] for r in rows]
        cols_csv = ", ".join(col_names)

        op.execute(
            "CREATE TABLE IF NOT EXISTS datasets_v2 ("
            "    id          INTEGER PRIMARY KEY,"
            "    name        TEXT NOT NULL UNIQUE,"
            "    kind        TEXT NOT NULL DEFAULT 'text',"
            "    description TEXT,"
            "    created_at  TEXT NOT NULL"
            "        DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))"
            ")"
        )
        op.execute(
            f"INSERT OR IGNORE INTO datasets_v2 ({cols_csv}) SELECT {cols_csv} FROM datasets"
        )
        op.execute("DROP TABLE datasets")
        op.execute("ALTER TABLE datasets_v2 RENAME TO datasets")
    finally:
        op.execute("PRAGMA foreign_keys=ON")
    logger.info("0017_ingest_runs: datasets.kind DEFAULT 'text' added (sqlite)")
