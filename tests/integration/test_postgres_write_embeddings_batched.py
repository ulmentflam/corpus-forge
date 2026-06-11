"""Integration test — ``PostgresBackend.write_embeddings`` batches.

RED → GREEN anchor for the fleet-2 claim-path deadlock fix
(``.planning/tdd/fleet2_claim_deadlock_investigation.md``).

The bug, in one sentence: the pre-fix ``write_embeddings`` issued one
``_execute`` per ``(chunk_id, embedding)`` pair, which translated to one
pool checkout + BEGIN/INSERT/COMMIT round-trip per pair. On the real
Mac → LXC-Postgres-over-Tailscale topology that surfaced as the
fleet-2 claim deadlock (1000 pool checkouts per 1000-pair page,
contention storm, ``Connection [ACTIVE]`` warnings, claims leaking in
``corpus.embed_claims``).

This test pins the **structural** contract that ``write_embeddings``
uses a SINGLE batched transaction regardless of the number of pairs.
Three assertions:

1. **Pool-checkout count.** Instrument the backend's pool with a
   counter wrapper around ``connection()`` and assert that a 500-pair
   write checks out a SMALL constant number of connections (1 for the
   embedder-info SELECT + 1 for the batched executemany = 2), not
   501. The pre-fix per-pair loop drives this to ~501 — wall-clock
   budgets aren't reliable on localhost-Docker, but the checkout count
   is structurally tied to the bug.
2. **All rows land.** The conflict-free side of the contract — every
   pair we pass in shows up in ``embeddings_<name>`` — is what makes
   the batched ``executemany`` a behaviour-preserving refactor.
3. **ON CONFLICT DO NOTHING preserved.** Re-writing the same
   chunk_id with a different vector is a no-op (not a clobber). This
   matters because two racing hosts on the claim path each write
   to (possibly) the same row if their `expire_stale_claims` lets the
   pre-stale row through; the conflict guard is what keeps concurrent
   writes idempotent.

Gated on ``requires_docker``; uses the session-scoped ``pg_dsn``
fixture.
"""

from __future__ import annotations

from contextlib import contextmanager

import numpy as np
import pytest

from corpus_forge.backends.postgres import PostgresBackend
from corpus_forge.embedders.base import BaseEmbedder
from corpus_forge.sources.base import RawDocument

pytestmark = [pytest.mark.integration, pytest.mark.requires_docker]


@pytest.fixture
def backend(pg_dsn: str) -> PostgresBackend:  # type: ignore[return]
    b = PostgresBackend(dsn=pg_dsn)
    b.migrate()
    yield b
    b.close()


def _register_embedder(b: PostgresBackend, name: str = "wbatch-embed", dim: int = 4) -> int:
    return b.register_embedder(
        BaseEmbedder(
            name=name,
            provider="sentence_transformers",
            model_id="test/model",
            dimension=dim,
            normalized=True,
            distance="cosine",
        )
    )


def _seed_chunks(b: PostgresBackend, n: int) -> list[int]:
    dataset_id = b.get_or_create_dataset(name=f"ds-wbatch-{n}", kind="text", description="")
    doc = RawDocument(
        source_uri=f"vault://wbatch-{n}.md",
        content_hash=f"hash-wbatch-{n}",
        text="body",
        title="t",
        modified_at=1000.0,
        metadata={},
        labels=[],
    )
    chunks: list[tuple[str | None, str]] = [(f"h{i}", f"chunk text number {i}") for i in range(n)]
    b.upsert_document(dataset_id, doc, chunks)
    rows = b._execute("SELECT id FROM corpus.chunks ORDER BY id")
    return [r["id"] for r in rows]


def _embedder_table(name: str) -> str:
    return f"embeddings_{name.replace('-', '_')}"


@contextmanager
def _count_pool_checkouts(backend: PostgresBackend):
    """Yield a ``list`` whose length tracks ``pool.connection()`` checkouts.

    Wraps ``backend._pool.connection`` so every ``with pool.connection()``
    in the call body appends a sentinel to the list. ``len(captured)``
    is the structural round-trip count. The pre-fix per-pair INSERT
    loop drives this to N+1 for an N-pair write; the post-fix batched
    form keeps it at 2 (embedder-info SELECT + batched executemany).
    """
    original = backend._pool.connection
    captured: list[None] = []

    @contextmanager
    def _wrapped(*args, **kwargs):
        captured.append(None)
        with original(*args, **kwargs) as conn:
            yield conn

    backend._pool.connection = _wrapped  # type: ignore[method-assign]
    try:
        yield captured
    finally:
        backend._pool.connection = original  # type: ignore[method-assign]


# ── Round-trip / transaction count contract ───────────────────────────────────


def test_write_embeddings_uses_constant_pool_checkouts(backend: PostgresBackend) -> None:
    """500 pairs must check out the pool a SMALL constant number of times.

    Pre-fix (per-pair ``_execute`` loop): 1 embedder-info SELECT + 500
    INSERT checkouts = 501 pool checkouts per page.
    Post-fix (batched ``executemany``): 1 embedder-info SELECT + 1
    ``executemany`` checkout = 2 pool checkouts per page.

    The structural assertion is ``len(checkouts) <= 4`` — a loose
    upper bound that catches the regression (501 ≫ 4) without being
    so tight that an implementation tweak that adds a second SELECT
    has to update the test.
    """
    emb_id = _register_embedder(backend)
    chunk_ids = _seed_chunks(backend, n=500)

    rng = np.random.default_rng(seed=42)
    pairs: list[tuple[int, np.ndarray]] = [
        (cid, rng.standard_normal(4).astype(np.float32)) for cid in chunk_ids
    ]

    with _count_pool_checkouts(backend) as checkouts:
        backend.write_embeddings(emb_id, pairs)

    # The smoking-gun structural pin: the pre-fix per-pair INSERT loop
    # produces 501 checkouts; the post-fix batched form produces 2.
    # A ceiling of 4 is loose enough that an implementation tweak
    # adding one more lookup doesn't have to update the test, but
    # still catches the per-pair regression definitively.
    assert len(checkouts) <= 4, (
        f"write_embeddings(500 pairs) took {len(checkouts)} pool checkouts — "
        "the per-pair _execute loop has reappeared; restore the executemany "
        "batch (see .planning/tdd/fleet2_claim_deadlock_investigation.md)."
    )

    # All 500 rows landed (correctness-preservation check).
    table = _embedder_table("wbatch-embed")
    rows = backend._execute(f"SELECT COUNT(*) AS n FROM corpus.{table}")
    assert int(rows[0]["n"]) == 500


def test_write_embeddings_round_trips_scale_o1_not_on(backend: PostgresBackend) -> None:
    """``len(checkouts)`` is INDEPENDENT of pairs count — O(1), not O(N)."""
    emb_id = _register_embedder(backend, name="wbatch-embed-2")
    chunk_ids = _seed_chunks(backend, n=400)
    first, second = chunk_ids[:200], chunk_ids[200:]

    rng = np.random.default_rng(seed=7)

    def _pairs(ids: list[int]) -> list[tuple[int, np.ndarray]]:
        return [(cid, rng.standard_normal(4).astype(np.float32)) for cid in ids]

    with _count_pool_checkouts(backend) as checkouts_a:
        backend.write_embeddings(emb_id, _pairs(first))
    with _count_pool_checkouts(backend) as checkouts_b:
        backend.write_embeddings(emb_id, _pairs(second))

    # Two calls of the same shape must produce the same checkout count
    # (it's a function of the call, not the pair-count). And both must
    # land in the same small constant — the structural O(1) pin.
    assert len(checkouts_a) == len(checkouts_b), (
        f"checkouts vary between identically-shaped calls: "
        f"{len(checkouts_a)} vs {len(checkouts_b)} — non-deterministic "
        "or pair-count-dependent path (bug)."
    )
    assert len(checkouts_a) <= 4, (
        f"write_embeddings(200 pairs) took {len(checkouts_a)} pool "
        "checkouts — should be O(1) (1 embedder-info SELECT + 1 batched "
        "executemany). The per-pair loop is the regression."
    )


# ── ON CONFLICT DO NOTHING preserved ─────────────────────────────────────────


def test_write_embeddings_preserves_on_conflict_do_nothing(backend: PostgresBackend) -> None:
    """Re-writing the same chunk_id with a different vector must be a no-op."""
    emb_id = _register_embedder(backend, name="wbatch-conf")
    chunk_ids = _seed_chunks(backend, n=3)

    table = _embedder_table("wbatch-conf")

    # First write: distinct vectors per chunk.
    first = [(cid, np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float32)) for cid in chunk_ids]
    backend.write_embeddings(emb_id, first)

    # Second write: SAME chunk_ids with DIFFERENT vectors. Under
    # ON CONFLICT (chunk_id) DO NOTHING the existing rows must stand;
    # nothing about ``executemany`` batching may turn this into an
    # upsert (which would be a silent correctness regression — two
    # racing hosts could each "win" different chunks of a single page).
    second = [(cid, np.array([9.0, 9.0, 9.0, 9.0], dtype=np.float32)) for cid in chunk_ids]
    backend.write_embeddings(emb_id, second)

    rows = backend._execute(
        f"SELECT chunk_id, embedding::text AS vec FROM corpus.{table} ORDER BY chunk_id"
    )
    assert len(rows) == 3
    for row in rows:
        # ``[1,2,3,4]`` survives unchanged — the second write was a no-op.
        assert row["vec"].startswith("[1") and "9" not in row["vec"], (
            f"ON CONFLICT DO NOTHING did not hold; row {row['chunk_id']} → {row['vec']!r}"
        )


# ── Empty-pairs early return preserved ───────────────────────────────────────


def test_write_embeddings_empty_pairs_is_noop(backend: PostgresBackend) -> None:
    """``pairs=[]`` must not even resolve the embedder row — pure early-return."""
    # No embedder registered → if write_embeddings tried to look up the
    # embedder_id with an empty pairs list it'd ValueError. The early
    # return at the top of the function is what prevents that.
    backend.write_embeddings(embedder_id=9999, pairs=[])  # no exception → contract holds
