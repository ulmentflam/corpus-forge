"""Add corpus.shared_config table for federated config publish/pull.

Revision ID: 0020_shared_config
Revises: 0019_embed_claims
Create Date: 2026-06-06 03:10:00.000000

Storage half of RFC ``rfc-fleet-3-federated-config-and-setup`` (item 3).
The RFC lets a fleet share one canonical config blob through the
Postgres the hosts already point at: a host *publishes* its config and
peers *pull* it, with an optimistic-concurrency version guard that
refuses a blind clobber ("pull first").

The shared state is a single one-row table:

* ``shared_config`` — the canonical config blob for *this* corpus.
  ``corpus_id`` is the PK, defaulting to ``1``: there is exactly one
  corpus per database (RFC non-goal: no multi-corpus federation in one
  DB), but the PK keeps forward room rather than baking the singleton
  into a constraint we'd have to migrate away from. ``version`` is a
  monotonically-increasing integer bumped on every publish; a publisher
  passes the version it last pulled and the write only lands if the DB
  is still at that version (optimistic concurrency — see
  :meth:`PostgresBackend.put_shared_config`). ``body`` is the config
  blob as ``JSONB`` (``TEXT`` on SQLite per the house dialect split).
  ``published_by`` references ``corpus.hosts(host_id)`` (the fleet-1
  registry, revision 0018) so a published config always names the host
  that wrote it; ``published_at`` records when.

Federation is a Postgres-only feature; the SQLite path raises
:class:`corpus_forge.backends.base.FederationUnsupported` at the backend
layer. This migration still creates a parallel ``shared_config`` table
on SQLite so schema-introspection / migration tests stay
dialect-symmetric (``corpus.`` prefix dropped, ``JSONB`` → ``TEXT``,
``TIMESTAMPTZ`` → ``TEXT`` ISO-8601), mirroring the dialect split 0017 /
0018 / 0019 established.

Like 0019 (and unlike the forward-only 0008+ chain), this revision
carries a real ``downgrade()`` that drops the table
(``DROP TABLE IF EXISTS`` — safe to re-run): the shared-config table
holds only federated coordination state that each host also keeps in its
local ``config.toml``, so dropping it on a rollback loses nothing
durable — the same reasoning 0019 applied to ``embed_claims``.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from alembic import op

revision: str = "0020_shared_config"
down_revision: str | None = "0019_embed_claims"
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
        op.execute("DROP TABLE IF EXISTS corpus.shared_config")
    elif dialect == "sqlite":
        op.execute("DROP TABLE IF EXISTS shared_config")
    else:
        raise NotImplementedError(f"unsupported dialect: {dialect}")
    logger.info("0020_shared_config: dropped shared_config (%s)", dialect)


# ---------------------------------------------------------------------------
# Postgres
# ---------------------------------------------------------------------------


def _upgrade_postgres() -> None:
    """Postgres path — CREATE TABLE IF NOT EXISTS is fully idempotent."""
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS corpus.shared_config (
            corpus_id    INTEGER PRIMARY KEY DEFAULT 1,
            version      INTEGER NOT NULL,
            body         JSONB NOT NULL,
            published_by TEXT REFERENCES corpus.hosts(host_id),
            published_at TIMESTAMPTZ
        )
        """
    )
    logger.info("0020_shared_config: created shared_config (postgres)")


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------


def _upgrade_sqlite() -> None:
    """SQLite path — CREATE TABLE IF NOT EXISTS is idempotent; no JSONB/TIMESTAMPTZ.

    The shared-config table is never *used* on SQLite (the backend raises
    ``FederationUnsupported``); it exists only so the dialect-symmetric
    migration tests can introspect the same table on both backends.
    ``body`` is ``TEXT`` (``json.dumps`` on the Python side) and
    ``published_at`` is ``TEXT`` ISO-8601, matching the dialect idiom 0017
    / 0018 / 0019 established.
    """
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS shared_config (
            corpus_id    INTEGER PRIMARY KEY DEFAULT 1,
            version      INTEGER NOT NULL,
            body         TEXT NOT NULL,
            published_by TEXT REFERENCES hosts(host_id),
            published_at TEXT
        )
        """
    )
    logger.info("0020_shared_config: created shared_config (sqlite)")
