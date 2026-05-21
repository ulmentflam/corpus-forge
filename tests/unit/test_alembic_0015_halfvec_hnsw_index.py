"""Unit coverage for the 0015 halfvec-index alembic revision.

The migration is mostly a runtime side-effect (DROP INDEX / CREATE INDEX
against an actual Postgres backend), so we don't try to spin up
testcontainers for it here — that path is covered by the integration
matrix. What we DO test in-process:

1. The revision imports cleanly and chains onto 0014.
2. ``_target_index_spec`` agrees with the canonical
   :func:`corpus_forge.backends.postgres._dense_index_strategy` for
   every dim regime we care about (1024, 2000, 2001, 4000, 4096, 8192).
3. The revision file declares ``downgrade`` as a no-op (forward-only
   per project convention).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REVISION_PATH = (
    Path(__file__).resolve().parents[2]
    / "corpus_forge"
    / "alembic"
    / "versions"
    / "0015_halfvec_hnsw_index.py"
)


def _load_revision_module():
    spec = importlib.util.spec_from_file_location("_revision_0015", REVISION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_revision_imports_and_chains_onto_0014() -> None:
    mod = _load_revision_module()
    assert mod.revision == "0015_halfvec_hnsw_index"
    assert mod.down_revision == "0014_sdft_demonstrations"

    # alembic's default ``alembic_version.version_num`` is VARCHAR(32).
    # Anything longer raises ``StringDataRightTruncation`` at the very
    # end of every ``alembic upgrade`` (after the migration's own
    # ``upgrade()`` succeeds — the failure shows up only on the
    # post-migration ``UPDATE alembic_version`` and rolls the whole
    # thing back). The original id for this revision was 37 chars
    # and broke ``corpus-forge migrate`` against live Postgres.
    assert len(mod.revision) <= 32, (
        f"revision id {mod.revision!r} is {len(mod.revision)} chars; "
        "alembic's version_num column is VARCHAR(32). The original "
        "0015 id was 37 chars and exploded mid-upgrade — see the "
        "module docstring."
    )


@pytest.mark.parametrize(
    ("dim", "expected_expr", "expected_ops"),
    [
        (1024, "embedding", "vector_cosine_ops"),
        (2000, "embedding", "vector_cosine_ops"),
        (2001, "(embedding::halfvec(2001))", "halfvec_cosine_ops"),
        (3000, "(embedding::halfvec(3000))", "halfvec_cosine_ops"),
        (4000, "(embedding::halfvec(4000))", "halfvec_cosine_ops"),
        # Native Qwen3-Embedding-8B width — must clamp the index
        # projection at 4000 because pgvector's halfvec HNSW caps there.
        (4096, "(embedding::halfvec(4000))", "halfvec_cosine_ops"),
        # Very wide hypothetical model — same clamp applies.
        (8192, "(embedding::halfvec(4000))", "halfvec_cosine_ops"),
    ],
)
def test_target_index_spec_matches_canonical_strategy(
    dim: int, expected_expr: str, expected_ops: str
) -> None:
    mod = _load_revision_module()
    expr, ops = mod._target_index_spec(dim)
    assert expr == expected_expr
    assert ops == expected_ops

    # Cross-check against the canonical helper. The migration is a
    # snapshot of today's strategy — they MUST agree at every dim
    # regime, otherwise the migration would rebuild indexes
    # incorrectly.
    from corpus_forge.backends.postgres import _dense_index_strategy

    canonical_expr, _search_expr, canonical_ops = _dense_index_strategy(dim)
    assert expr == canonical_expr
    assert ops == canonical_ops


def test_downgrade_is_a_no_op() -> None:
    """Forward-only per project convention — downgrade is empty so
    operators can't accidentally roll back into a state that
    pgvector refuses to materialise (HNSW over vector(>2000)).
    """
    mod = _load_revision_module()
    assert mod.downgrade() is None
