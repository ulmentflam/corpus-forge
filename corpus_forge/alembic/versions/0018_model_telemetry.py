"""Add hosts, models, and model_benchmarks tables for fleet telemetry.

Revision ID: 0018_model_telemetry
Revises: 0017_ingest_runs
Create Date: 2026-06-05 03:10:00.000000

First task of RFC ``rfc-fleet-1-model-telemetry-and-bench`` (P0). The
RFC turns the multi-host fleet's model story from operator folklore
into queryable data: which host can run which model, and how fast.
Three tables back that:

* ``hosts`` — one row per machine (keyed by the per-host
  ``host_id``); records hostname, OS, the
  :func:`corpus_forge.acceleration.detect_accelerator` probe output
  (as JSON), an optional Tailscale name (fleet-4 fills it properly),
  and a ``last_seen`` heartbeat.
* ``models`` — registry of every model a host has run or has
  available, keyed by ``"<provider>:<model_id>"``.  ``kind`` reserves
  room for ``llm``/``vlm``/``whisper`` but v1 only writes ``embedder``
  (plus best-effort ``ollama list`` rows).
* ``model_benchmarks`` — one row per measured throughput sample
  ``(host, model, device, transport, batch)``; written passively by
  every ``embed`` run and actively by ``bench embed`` (fleet-1 tasks
  4-5).

The index ``model_benchmarks(host_id, model_key, measured_at DESC)``
serves the "latest per host+model" reads the ``models list`` /
``hosts list`` verbs need.

Both Postgres and SQLite paths create the tables with ``CREATE TABLE
IF NOT EXISTS`` so the upgrade is fully idempotent.  Postgres uses
``JSONB`` for ``hosts.accelerator``; SQLite has no JSONB type, so the
SQLite path stores the same JSON document as ``TEXT`` (the heartbeat
helper serialises with :func:`json.dumps` for both backends).  The
SQLite path also drops the ``corpus.`` schema prefix and uses ``TEXT``
in place of ``TIMESTAMPTZ`` / ``NUMERIC`` (ISO-8601 strings + REAL),
mirroring the dialect split established by 0017_ingest_runs.

Forward-only per project convention (0008+): ``downgrade()`` is a
no-op ``pass``.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from alembic import op

revision: str = "0018_model_telemetry"
down_revision: str | None = "0017_ingest_runs"
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
    # Forward-only — matches the rest of the chain (0008+).
    pass


# ---------------------------------------------------------------------------
# Postgres
# ---------------------------------------------------------------------------


def _upgrade_postgres() -> None:
    """Postgres path — CREATE TABLE IF NOT EXISTS is fully idempotent."""
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS corpus.hosts (
            host_id        TEXT PRIMARY KEY,
            hostname       TEXT,
            os             TEXT,
            accelerator    JSONB,
            tailscale_name TEXT,
            last_seen      TIMESTAMPTZ
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS corpus.models (
            model_key  TEXT PRIMARY KEY,
            kind       TEXT,
            provider   TEXT,
            model_id   TEXT,
            dimension  INTEGER,
            first_seen TIMESTAMPTZ
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS corpus.model_benchmarks (
            id             BIGSERIAL PRIMARY KEY,
            host_id        TEXT REFERENCES corpus.hosts(host_id),
            model_key      TEXT REFERENCES corpus.models(model_key),
            source         TEXT,
            transport      TEXT,
            device         TEXT,
            batch_size     INTEGER,
            sample_chunks  INTEGER,
            chunks_per_s   NUMERIC,
            tokens_per_s   NUMERIC,
            latency_p50_ms NUMERIC,
            latency_p95_ms NUMERIC,
            measured_at    TIMESTAMPTZ
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS model_benchmarks_host_model_measured_idx
            ON corpus.model_benchmarks(host_id, model_key, measured_at DESC)
        """
    )
    logger.info("0018_model_telemetry: created hosts, models, model_benchmarks (postgres)")


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------


def _upgrade_sqlite() -> None:
    """SQLite path — CREATE TABLE IF NOT EXISTS is idempotent; no JSONB/TIMESTAMPTZ.

    ``hosts.accelerator`` is stored as ``TEXT`` (a JSON document); the
    timestamp columns are ``TEXT`` ISO-8601 strings; the ``NUMERIC``
    throughput columns map to ``REAL``.  Same end-state contract as the
    Postgres path, no special casing in the application layer.
    """
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS hosts (
            host_id        TEXT PRIMARY KEY,
            hostname       TEXT,
            os             TEXT,
            accelerator    TEXT,
            tailscale_name TEXT,
            last_seen      TEXT
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS models (
            model_key  TEXT PRIMARY KEY,
            kind       TEXT,
            provider   TEXT,
            model_id   TEXT,
            dimension  INTEGER,
            first_seen TEXT
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS model_benchmarks (
            id             INTEGER PRIMARY KEY,
            host_id        TEXT REFERENCES hosts(host_id),
            model_key      TEXT REFERENCES models(model_key),
            source         TEXT,
            transport      TEXT,
            device         TEXT,
            batch_size     INTEGER,
            sample_chunks  INTEGER,
            chunks_per_s   REAL,
            tokens_per_s   REAL,
            latency_p50_ms REAL,
            latency_p95_ms REAL,
            measured_at    TEXT
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS model_benchmarks_host_model_measured_idx
            ON model_benchmarks(host_id, model_key, measured_at DESC)
        """
    )
    logger.info("0018_model_telemetry: created hosts, models, model_benchmarks (sqlite)")
