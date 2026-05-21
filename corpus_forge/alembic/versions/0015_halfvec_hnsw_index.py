"""Adapt HNSW indexes on per-embedder tables to the embedder's dimension.

Revision ID: 0015_halfvec_hnsw_index
Revises: 0014_sdft_demonstrations
Create Date: 2026-05-20 21:00:00.000000

NOTE on the revision id length: alembic's default
``alembic_version.version_num`` column is ``VARCHAR(32)``. The original
id for this revision (``0015_halfvec_index_for_wide_embedders``, 37
chars) tripped ``psycopg.errors.StringDataRightTruncation`` on the
``UPDATE corpus.alembic_version`` that alembic runs at the end of
``upgrade()``. The whole migration rolled back transactionally, so
no data was corrupted — but the id has been shortened to
``0015_halfvec_hnsw_index`` (23 chars) and a regression test pins the
``len(rev_id) <= 32`` constraint going forward.

pgvector's standard ``vector`` HNSW index supports at most 2000 dims;
``halfvec`` HNSW supports up to 4000. Earlier corpus-forge revisions
always created the index as ``USING hnsw (embedding vector_cosine_ops)``
regardless of the embedder's dimension, so any embedder with dim >
2000 either crashed at index-build time or (worse) silently fell back
to a sequential scan if the index never existed.

This migration walks ``corpus.embedders``, looks up the matching
``embeddings_<safe_name>`` table for each row, and rebuilds the HNSW
index using the right strategy for the row's dimension:

- ``dim <= 2000``: ``USING hnsw (embedding vector_cosine_ops)``
  (no change — left in place for back-compat with deployments that
  already have the index)
- ``dim >  2000``: ``USING hnsw ((subvector(embedding, 1, min(dim,4000))
  ::halfvec(min(dim,4000))) halfvec_cosine_ops)`` — the ``subvector``
  truncates the wider underlying ``vector(dim)`` down to the indexable
  width because pgvector's ``::halfvec(N)`` cast does NOT truncate;
  it raises ``DataException: expected N dimensions, not <dim>`` when
  the source is wider.

Idempotent. The migration:

* Skips tables where ``corpus.embeddings_<name>`` doesn't exist (the
  embedder row was inserted but the chunks table was never created —
  e.g. inactive embedder, fresh registration).
* Detects whether the existing index already matches the target
  strategy by name + indexdef and leaves it alone if so (no
  ``DROP INDEX`` thrash on re-runs).
* Wraps the ``DROP INDEX`` + ``CREATE INDEX`` pair in the same
  transaction so a failure rolls back to the previous index. Note
  that during the rebuild window dense search degrades to a
  sequential scan; acceptable because (a) per-embedder tables are
  the unit of rebuild and (b) the new index uses ``CREATE INDEX``
  (not ``CREATE INDEX CONCURRENTLY``) so the rebuild blocks but
  finishes faster.

SQLite is a no-op — the SQLite backend uses ``sqlite-vec`` virtual
tables and doesn't use HNSW at all.

The repair CLI (``corpus-forge admin repair-indexes``) calls the
same shape of statements, so any deployment that lands on a mismatched
index after a hand-rolled change can re-converge without running the
migration again.

Forward-only per project convention (0008, 0010, 0011, 0012, 0013,
0014).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_halfvec_hnsw_index"
down_revision: str | None = "0014_sdft_demonstrations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

# pgvector index ceilings — see corpus_forge/backends/postgres.py for the
# canonical constants. Duplicated here intentionally so the migration is
# self-contained and survives any future refactor that moves the
# helper module.
_PGVECTOR_INDEX_LIMIT = 2000
_HALFVEC_INDEX_LIMIT = 4000


def _target_index_spec(dimension: int) -> tuple[str, str]:
    """Return ``(index_expression, ops_class)`` for ``CREATE INDEX``.

    Mirrors ``corpus_forge.backends.postgres._dense_index_strategy``.
    The ``subvector(embedding, 1, N)::halfvec(N)`` form is the only
    expression pgvector will accept here when the storage column is
    ``vector(dim)`` and ``dim > N``: a plain ``embedding::halfvec(N)``
    cast raises ``expected N dimensions, not <dim>`` at index-build
    time.
    """
    if dimension <= _PGVECTOR_INDEX_LIMIT:
        return ("embedding", "vector_cosine_ops")
    index_dim = min(dimension, _HALFVEC_INDEX_LIMIT)
    return (
        f"(subvector(embedding, 1, {index_dim})::halfvec({index_dim}))",
        "halfvec_cosine_ops",
    )


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect != "postgresql":
        # SQLite backend uses sqlite-vec virtual tables; no HNSW index
        # to rebuild. Left as a no-op for portability.
        logger.info("0015: dialect=%s — no-op (postgres-only migration)", dialect)
        return

    # ``corpus.embedders.name`` is the source of truth; the chunks
    # table name is derived by ``str.replace("-", "_")`` (see
    # ``PostgresBackend._create_embedder_table``). Pull both the
    # original ``name`` and the recorded ``table_name`` so we don't
    # have to re-derive the rule here.
    embedders = bind.execute(
        sa.text("SELECT name, dimension, table_name FROM corpus.embedders ORDER BY id")
    ).fetchall()

    # SQLAlchemy's dialect-aware identifier quoter — used below so
    # ``table_name`` / ``index_name`` get properly escaped before they
    # land in the DDL. ``op.create_index`` doesn't compose cleanly with
    # an expression-based HNSW index + custom ops class (the high-level
    # API expects column references, not SQL expressions with cast
    # operators), so we still build the DDL as a string. Quoting the
    # identifiers via ``preparer.quote`` closes the only injection
    # surface — ``index_expr`` and ``ops_class`` are constants we
    # generate ourselves (``"halfvec_cosine_ops"`` etc.), never from
    # the database.
    quote = bind.dialect.identifier_preparer.quote

    for embedder_name, dimension, table_name in embedders:
        # Defensive: some rows may have been seeded without a
        # table_name (older revisions). Skip silently — the operator
        # can re-run after ``register_embedder`` fills it in.
        if not table_name:
            logger.info("0015: embedder %r has empty table_name — skipping", embedder_name)
            continue

        # The per-embedder table may not actually exist if the row
        # was inserted by a partial-failure path.  Skip non-existent
        # tables — re-registration will create them with the right
        # strategy from scratch.
        exists = bind.execute(
            sa.text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'corpus' AND table_name = :t"
            ),
            {"t": table_name},
        ).fetchone()
        if not exists:
            logger.info(
                "0015: corpus.%s missing — skipping (embedder=%r dim=%s)",
                table_name,
                embedder_name,
                dimension,
            )
            continue

        index_expr, ops_class = _target_index_spec(int(dimension))
        index_name = f"{table_name}_hnsw"

        # Detect the current indexdef. ``pg_get_indexdef`` returns the
        # canonical CREATE INDEX statement; the substring check is
        # tolerant of whitespace + ``USING hnsw`` variants emitted by
        # different pgvector minor versions.
        indexdef_row = bind.execute(
            sa.text(
                "SELECT pg_get_indexdef(c.oid) AS def "
                "FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'corpus' AND c.relname = :i AND c.relkind = 'i'"
            ),
            {"i": index_name},
        ).fetchone()
        current_def = indexdef_row[0] if indexdef_row else ""

        target_fragment = f"hnsw ({index_expr} {ops_class}"
        if target_fragment in current_def:
            logger.info(
                "0015: corpus.%s already on target index strategy — skipping",
                table_name,
            )
            continue

        q_table = quote(table_name)
        q_index = quote(index_name)

        if current_def:
            logger.info(
                "0015: rebuilding corpus.%s.%s (dim=%s) — old=%s, new=USING hnsw (%s %s)",
                table_name,
                index_name,
                dimension,
                current_def,
                index_expr,
                ops_class,
            )
            op.execute(sa.text(f"DROP INDEX IF EXISTS corpus.{q_index}"))
        else:
            logger.info(
                "0015: creating missing index corpus.%s.%s (dim=%s)",
                table_name,
                index_name,
                dimension,
            )

        op.execute(
            sa.text(
                f"CREATE INDEX IF NOT EXISTS {q_index} "
                f"ON corpus.{q_table} "
                f"USING hnsw ({index_expr} {ops_class})"
            )
        )


def downgrade() -> None:
    # Forward-only per project convention. A reversible downgrade
    # would have to recreate the pre-existing ``vector_cosine_ops``
    # index even for embedders that couldn't have had one in the
    # first place (dim > 2000 — pgvector refuses to build that),
    # which would just fail. Operators who need to roll back can
    # drop and recreate the per-embedder tables manually (the
    # chunks/data side of the schema is unchanged by this revision).
    pass
