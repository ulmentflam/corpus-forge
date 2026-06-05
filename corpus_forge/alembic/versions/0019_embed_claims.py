"""Add corpus.embed_claims table for distributed claim-based embedding backfill.

Revision ID: 0019_embed_claims
Revises: 0018_model_telemetry
Create Date: 2026-06-05 14:40:00.000000

First task of RFC ``rfc-fleet-2-distributed-embedding`` (P0).  The RFC
lets N machines drain the *same* embedder's backlog concurrently with
zero duplicated GPU compute, coordinating only through the shared
Postgres the fleet already points at — no broker, no new service.

The claim primitive is a single table:

* ``embed_claims`` — one row per ``(embedder_id, chunk_id)`` a host has
  reserved for embedding.  ``host_id`` references ``corpus.hosts`` (the
  fleet-1 registry, revision 0018).  ``claimed_at`` records when the
  reservation was taken and ``lease_until`` when it expires — a worker
  that dies simply stops renewing, and the next claim call sweeps the
  abandoned rows (``expire_stale_claims``).  The ``UNIQUE
  (embedder_id, chunk_id)`` constraint makes the
  ``ON CONFLICT DO NOTHING`` insert in
  :meth:`PostgresBackend.claim_chunks_for_embedding` a benign no-op when
  two hosts race for the same chunk; only the host whose insert lands
  actually works it.  The ``lease_until`` index serves the
  expiry-sweep ``DELETE ... WHERE lease_until < now()``.

Distributed claiming is a Postgres-only feature; the SQLite path raises
:class:`corpus_forge.backends.base.FederationUnsupported` at the
backend layer.  This migration still creates a parallel ``embed_claims``
table on SQLite so the schema introspection / migration tests stay
dialect-symmetric (``corpus.`` prefix dropped, ``TIMESTAMPTZ`` → ``TEXT``
ISO-8601), mirroring the dialect split established by 0017 / 0018.

Unlike the forward-only 0008+ chain, this revision carries a real
``downgrade()`` that drops the table (``DROP TABLE IF EXISTS`` — safe to
re-run): the claim table holds only ephemeral coordination state (no
durable corpus data — the embedding rows are the durable record), so
dropping it on a rollback loses nothing.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from alembic import op

revision: str = "0019_embed_claims"
down_revision: str | None = "0018_model_telemetry"
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
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        op.execute("DROP TABLE IF EXISTS corpus.embed_claims")
    elif dialect == "sqlite":
        op.execute("DROP TABLE IF EXISTS embed_claims")
    else:
        raise NotImplementedError(f"unsupported dialect: {dialect}")
    logger.info("0019_embed_claims: dropped embed_claims (%s)", dialect)


# ---------------------------------------------------------------------------
# Postgres
# ---------------------------------------------------------------------------


def _upgrade_postgres() -> None:
    """Postgres path — CREATE TABLE IF NOT EXISTS is fully idempotent."""
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS corpus.embed_claims (
            claim_id    BIGSERIAL PRIMARY KEY,
            embedder_id INTEGER NOT NULL,
            chunk_id    BIGINT NOT NULL,
            host_id     TEXT REFERENCES corpus.hosts(host_id),
            claimed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            lease_until TIMESTAMPTZ NOT NULL,
            UNIQUE (embedder_id, chunk_id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS embed_claims_lease_until_idx
            ON corpus.embed_claims(lease_until)
        """
    )
    logger.info("0019_embed_claims: created embed_claims (postgres)")


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------


def _upgrade_sqlite() -> None:
    """SQLite path — CREATE TABLE IF NOT EXISTS is idempotent; no TIMESTAMPTZ.

    The claim table is never *used* on SQLite (the backend raises
    ``FederationUnsupported``); it exists only so the dialect-symmetric
    migration tests can introspect the same table on both backends.
    ``claimed_at`` / ``lease_until`` are ``TEXT`` ISO-8601, matching the
    timestamp idiom 0017 / 0018 established.
    """
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS embed_claims (
            claim_id    INTEGER PRIMARY KEY,
            embedder_id INTEGER NOT NULL,
            chunk_id    INTEGER NOT NULL,
            host_id     TEXT REFERENCES hosts(host_id),
            claimed_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            lease_until TEXT NOT NULL,
            UNIQUE (embedder_id, chunk_id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS embed_claims_lease_until_idx
            ON embed_claims(lease_until)
        """
    )
    logger.info("0019_embed_claims: created embed_claims (sqlite)")
