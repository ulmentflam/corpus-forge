"""Integration tests — Fleet-2 distributed claim-based embedding backfill.

Exercise the three PostgresBackend claim methods against a real
testcontainers Postgres:

- ``claim_chunks_for_embedding`` — reserves missing-embedding chunks,
  excludes already-claimed chunks, returns the ``chunks_missing_embedding``
  tuple shape, applies the same missing-set definition + extension filter,
  honours ``batch``, sets ``lease_until = claimed_at + lease_ttl``.
- ``release_claims`` — host-scoped delete of claim rows.
- ``expire_stale_claims`` — sweeps rows past ``lease_until`` (per-embedder
  and global); the sweep also runs opportunistically inside a claim call so
  a dead worker's claims self-heal (crash-recovery path).
- Uniqueness race safety — ON CONFLICT DO NOTHING means a pre-existing
  claim is never re-returned.

Gated on ``requires_docker``; uses the session-scoped ``pg_dsn`` fixture.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from corpus_forge.backends.postgres import PostgresBackend
from corpus_forge.embedders.base import BaseEmbedder
from corpus_forge.sources.base import RawDocument

pytestmark = [pytest.mark.integration, pytest.mark.requires_docker]


# ── fixtures / helpers ────────────────────────────────────────────────────────


@pytest.fixture
def backend(pg_dsn: str) -> PostgresBackend:  # type: ignore[return]
    b = PostgresBackend(dsn=pg_dsn)
    b.migrate()
    # embed_claims.host_id FKs corpus.hosts — in production the embed worker
    # heartbeats (upsert_host) before claiming. Pre-register every host id the
    # tests below use so the FK is satisfied.
    for host_id in (
        "h1",
        "h2",
        "h3",
        "h-live",
        "worker-A",
        "worker-B",
        "other",
        "me",
        "d1",
        "d2",
        "dead-host",
    ):
        b.upsert_host(host_id=host_id, hostname=host_id, os="test", accelerator=None)
    yield b
    b.close()


def _register_embedder(b: PostgresBackend, name: str = "claim-embed", dim: int = 8) -> int:
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


def _seed_chunks(b: PostgresBackend, suffix: str = ".md", n: int = 5) -> list[int]:
    """Create a dataset + document with ``n`` chunks; return chunk ids in order."""
    dataset_id = b.get_or_create_dataset(name=f"ds{suffix}{n}", kind="text", description="")
    doc = RawDocument(
        source_uri=f"vault://file{suffix}",
        content_hash=f"hash-{suffix}-{n}",
        text="body",
        title="t",
        modified_at=1000.0,
        metadata={},
        labels=[],
    )
    chunks = [(f"h{i}", f"chunk text number {i}") for i in range(n)]
    b.upsert_document(dataset_id, doc, chunks)
    rows = b._execute("SELECT id FROM corpus.chunks ORDER BY id")
    return [r["id"] for r in rows]


def _claim_count(b: PostgresBackend) -> int:
    rows = b._execute("SELECT COUNT(*) AS n FROM corpus.embed_claims")
    return int(rows[0]["n"])


# ── claim_chunks_for_embedding ────────────────────────────────────────────────


def test_claim_returns_missing_chunks_in_tuple_shape(backend: PostgresBackend) -> None:
    emb = _register_embedder(backend)
    chunk_ids = _seed_chunks(backend, n=3)

    claimed = backend.claim_chunks_for_embedding(emb, host_id="h1", batch=10, lease_ttl=600)
    assert {c[0] for c in claimed} == set(chunk_ids)
    # Same (chunk_id, text, source_uri) shape as chunks_missing_embedding.
    for cid, text, source_uri in claimed:
        assert isinstance(cid, int)
        assert text.startswith("chunk text number")
        assert source_uri == "vault://file.md"
    assert _claim_count(backend) == 3


def test_claim_excludes_already_claimed(backend: PostgresBackend) -> None:
    emb = _register_embedder(backend)
    _seed_chunks(backend, n=4)

    first = backend.claim_chunks_for_embedding(emb, host_id="h1", batch=2, lease_ttl=600)
    assert len(first) == 2
    # A second claimer gets the *other* two — disjoint, no overlap.
    second = backend.claim_chunks_for_embedding(emb, host_id="h2", batch=10, lease_ttl=600)
    assert len(second) == 2
    assert {c[0] for c in first}.isdisjoint({c[0] for c in second})
    # Nothing left to claim.
    third = backend.claim_chunks_for_embedding(emb, host_id="h3", batch=10, lease_ttl=600)
    assert third == []


def test_claim_excludes_already_embedded(backend: PostgresBackend) -> None:
    emb = _register_embedder(backend, dim=4)
    chunk_ids = _seed_chunks(backend, n=3)
    # Embed the first chunk — it must drop out of the missing set entirely.
    backend.write_embeddings(emb, [(chunk_ids[0], np.ones(4, dtype=np.float32))])

    claimed = backend.claim_chunks_for_embedding(emb, host_id="h1", batch=10, lease_ttl=600)
    assert {c[0] for c in claimed} == set(chunk_ids[1:])


def test_claim_honours_batch_limit(backend: PostgresBackend) -> None:
    emb = _register_embedder(backend)
    _seed_chunks(backend, n=5)
    claimed = backend.claim_chunks_for_embedding(emb, host_id="h1", batch=2, lease_ttl=600)
    assert len(claimed) == 2


def test_claim_sets_lease_until_from_ttl(backend: PostgresBackend) -> None:
    emb = _register_embedder(backend)
    _seed_chunks(backend, n=1)
    before = datetime.now(tz=UTC)
    backend.claim_chunks_for_embedding(emb, host_id="h1", batch=10, lease_ttl=600)
    rows = backend._execute("SELECT claimed_at, lease_until, host_id FROM corpus.embed_claims")
    assert len(rows) == 1
    row = rows[0]
    assert row["host_id"] == "h1"
    delta = row["lease_until"] - row["claimed_at"]
    assert abs(delta.total_seconds() - 600) < 2
    assert row["lease_until"] > before


def test_claim_respects_extension_filter(backend: PostgresBackend) -> None:
    emb = _register_embedder(backend)
    _seed_chunks(backend, suffix=".md", n=2)
    _seed_chunks(backend, suffix=".py", n=3)

    py_claimed = backend.claim_chunks_for_embedding(
        emb, host_id="h1", batch=10, lease_ttl=600, extensions=[".py"]
    )
    assert len(py_claimed) == 3
    assert all(c[2] == "vault://file.py" for c in py_claimed)


def test_claim_unknown_embedder_returns_empty(backend: PostgresBackend) -> None:
    _seed_chunks(backend, n=2)
    assert backend.claim_chunks_for_embedding(99999, host_id="h1", batch=10, lease_ttl=600) == []


def test_claim_no_missing_chunks_returns_empty(backend: PostgresBackend) -> None:
    emb = _register_embedder(backend)
    # No chunks seeded.
    assert backend.claim_chunks_for_embedding(emb, host_id="h1", batch=10, lease_ttl=600) == []


# ── release_claims ────────────────────────────────────────────────────────────


def test_release_deletes_own_claims(backend: PostgresBackend) -> None:
    emb = _register_embedder(backend)
    _seed_chunks(backend, n=3)
    claimed = backend.claim_chunks_for_embedding(emb, host_id="h1", batch=10, lease_ttl=600)
    ids = [c[0] for c in claimed]

    deleted = backend.release_claims(emb, host_id="h1", chunk_ids=ids)
    assert deleted == 3
    assert _claim_count(backend) == 0


def test_release_is_host_scoped(backend: PostgresBackend) -> None:
    emb = _register_embedder(backend)
    _seed_chunks(backend, n=2)
    claimed = backend.claim_chunks_for_embedding(emb, host_id="h1", batch=10, lease_ttl=600)
    ids = [c[0] for c in claimed]
    # h2 cannot release h1's claims.
    assert backend.release_claims(emb, host_id="h2", chunk_ids=ids) == 0
    assert _claim_count(backend) == 2


def test_release_empty_list_is_noop(backend: PostgresBackend) -> None:
    emb = _register_embedder(backend)
    assert backend.release_claims(emb, host_id="h1", chunk_ids=[]) == 0


# ── expire_stale_claims + crash recovery ──────────────────────────────────────


def _insert_stale_claim(b: PostgresBackend, emb: int, chunk_id: int, host: str) -> None:
    past = datetime.now(tz=UTC) - timedelta(seconds=10)
    b._execute(
        "INSERT INTO corpus.embed_claims "
        "(embedder_id, chunk_id, host_id, claimed_at, lease_until) "
        "VALUES (%s, %s, %s, %s, %s)",
        (emb, chunk_id, host, past, past),
    )


def test_expire_stale_claims_per_embedder(backend: PostgresBackend) -> None:
    emb = _register_embedder(backend)
    chunk_ids = _seed_chunks(backend, n=2)
    # One stale claim + one live claim, inserted directly so the
    # opportunistic sweep inside a claim call doesn't pre-empt the test.
    _insert_stale_claim(backend, emb, chunk_ids[0], "dead-host")
    future = datetime.now(tz=UTC) + timedelta(seconds=600)
    backend._execute(
        "INSERT INTO corpus.embed_claims "
        "(embedder_id, chunk_id, host_id, claimed_at, lease_until) "
        "VALUES (%s, %s, %s, %s, %s)",
        (emb, chunk_ids[1], "h-live", datetime.now(tz=UTC), future),
    )

    # A stale claim under a *different* embedder must NOT be swept by the
    # per-embedder call (scoping check).
    other_emb = _register_embedder(backend, name="claim-other")
    _insert_stale_claim(backend, other_emb, chunk_ids[0], "dead-host")

    n = backend.expire_stale_claims(embedder_id=emb)
    assert n == 1  # only the stale claim under `emb` is swept
    surviving = {
        (r["embedder_id"], r["host_id"])
        for r in backend._execute("SELECT embedder_id, host_id FROM corpus.embed_claims")
    }
    assert surviving == {(emb, "h-live"), (other_emb, "dead-host")}


def test_expire_stale_claims_global(backend: PostgresBackend) -> None:
    emb1 = _register_embedder(backend, name="claim-e1")
    emb2 = _register_embedder(backend, name="claim-e2")
    chunk_ids = _seed_chunks(backend, n=2)
    _insert_stale_claim(backend, emb1, chunk_ids[0], "d1")
    _insert_stale_claim(backend, emb2, chunk_ids[1], "d2")

    n = backend.expire_stale_claims()  # global sweep
    assert n == 2
    assert _claim_count(backend) == 0


def test_count_stale_claims_does_not_delete(backend: PostgresBackend) -> None:
    """``count_stale_claims`` is the read-only sibling of ``expire_stale_claims``."""
    emb = _register_embedder(backend)
    chunk_ids = _seed_chunks(backend, n=2)
    _insert_stale_claim(backend, emb, chunk_ids[0], "dead-host")
    future = datetime.now(tz=UTC) + timedelta(seconds=600)
    backend._execute(
        "INSERT INTO corpus.embed_claims "
        "(embedder_id, chunk_id, host_id, claimed_at, lease_until) "
        "VALUES (%s, %s, %s, %s, %s)",
        (emb, chunk_ids[1], "h-live", datetime.now(tz=UTC), future),
    )

    assert backend.count_stale_claims() == 1
    # The count must NOT mutate state — both claims still present.
    assert _claim_count(backend) == 2


def test_count_stale_claims_per_embedder_scope(backend: PostgresBackend) -> None:
    emb1 = _register_embedder(backend, name="count-e1")
    emb2 = _register_embedder(backend, name="count-e2")
    chunk_ids = _seed_chunks(backend, n=2)
    _insert_stale_claim(backend, emb1, chunk_ids[0], "d1")
    _insert_stale_claim(backend, emb2, chunk_ids[1], "d2")

    assert backend.count_stale_claims(embedder_id=emb1) == 1
    assert backend.count_stale_claims() == 2  # global
    assert _claim_count(backend) == 2  # still no deletion


def test_count_stale_claims_empty_is_zero(backend: PostgresBackend) -> None:
    emb = _register_embedder(backend)
    chunk_ids = _seed_chunks(backend, n=1)
    future = datetime.now(tz=UTC) + timedelta(seconds=600)
    backend._execute(
        "INSERT INTO corpus.embed_claims "
        "(embedder_id, chunk_id, host_id, claimed_at, lease_until) "
        "VALUES (%s, %s, %s, %s, %s)",
        (emb, chunk_ids[0], "h-live", datetime.now(tz=UTC), future),
    )
    assert backend.count_stale_claims() == 0


def test_crash_recovery_via_opportunistic_expiry(backend: PostgresBackend) -> None:
    """Worker A claims a chunk and dies; its lease expires; worker B reclaims it.

    The expiry sweep runs at the top of every claim call, so B does not need
    to call expire_stale_claims explicitly — the abandoned work self-heals.
    """
    emb = _register_embedder(backend)
    chunk_ids = _seed_chunks(backend, n=1)
    # Simulate worker A's dead claim (lease already in the past).
    _insert_stale_claim(backend, emb, chunk_ids[0], "worker-A")
    assert _claim_count(backend) == 1

    reclaimed = backend.claim_chunks_for_embedding(emb, host_id="worker-B", batch=10, lease_ttl=600)
    assert [c[0] for c in reclaimed] == chunk_ids
    rows = backend._execute("SELECT host_id FROM corpus.embed_claims")
    assert len(rows) == 1
    assert rows[0]["host_id"] == "worker-B"


def test_on_conflict_do_nothing_keeps_live_claim(backend: PostgresBackend) -> None:
    """A live (non-expired) claim is never re-returned even on a re-claim attempt.

    The missing-set anti-join already excludes claimed chunks, but this pins
    the ON CONFLICT DO NOTHING safety net: insert a live claim directly, then
    a claim call must skip that chunk entirely.
    """
    emb = _register_embedder(backend)
    chunk_ids = _seed_chunks(backend, n=2)
    future = datetime.now(tz=UTC) + timedelta(seconds=600)
    backend._execute(
        "INSERT INTO corpus.embed_claims "
        "(embedder_id, chunk_id, host_id, claimed_at, lease_until) "
        "VALUES (%s, %s, %s, %s, %s)",
        (emb, chunk_ids[0], "other", datetime.now(tz=UTC), future),
    )
    claimed = backend.claim_chunks_for_embedding(emb, host_id="me", batch=10, lease_ttl=600)
    assert [c[0] for c in claimed] == [chunk_ids[1]]


def test_claim_count_matches_missing_minus_claimed(backend: PostgresBackend) -> None:
    """The claim path and chunks_missing_embedding agree on the missing set."""
    emb = _register_embedder(backend)
    chunk_ids = _seed_chunks(backend, n=4)
    missing_before = {c[0] for c in backend.chunks_missing_embedding(emb, limit=100)}
    assert missing_before == set(chunk_ids)

    claimed = backend.claim_chunks_for_embedding(emb, host_id="h1", batch=2, lease_ttl=600)
    # chunks_missing_embedding still reports all (it ignores claims) — the
    # claim path narrows via the live-claim anti-join, returning the rest.
    remaining = backend.claim_chunks_for_embedding(emb, host_id="h2", batch=100, lease_ttl=600)
    assert {c[0] for c in claimed} | {c[0] for c in remaining} == set(chunk_ids)


# ── SKIP LOCKED under live contention (reviewer-major follow-through) ────────
#
# The tests above prove disjointness via the live-claim anti-join
# (sequential calls). These two prove the FOR UPDATE OF c SKIP LOCKED
# half: candidate rows locked by a *concurrent open transaction* are
# skipped — not blocked on, not double-claimed.


def test_held_row_locks_are_skipped_not_blocked(backend: PostgresBackend, pg_dsn: str) -> None:
    """Host B claims around rows a concurrent transaction has locked.

    Simulates host A mid-claim: a raw connection holds ``FOR UPDATE``
    locks on the first two candidate chunk rows (the same locks the
    claim CTE's ``cand`` takes) without committing. Host B's claim via
    the real API must return the *other* chunks promptly — SKIP LOCKED,
    not lock-wait — and the locked rows become claimable again once A's
    transaction ends.
    """
    import psycopg

    emb = _register_embedder(backend)
    chunk_ids = _seed_chunks(backend, n=5)
    locked, free = chunk_ids[:2], chunk_ids[2:]

    with psycopg.connect(pg_dsn) as held:
        with held.cursor() as cur:
            # Fail loudly instead of deadlocking the test if SKIP LOCKED
            # were ever lost from the claim CTE.
            cur.execute("SET statement_timeout = '5s'")
            cur.execute(
                "SELECT id FROM corpus.chunks WHERE id = ANY(%s) FOR UPDATE",
                (locked,),
            )
            assert {r[0] for r in cur.fetchall()} == set(locked)

            claimed = backend.claim_chunks_for_embedding(
                emb, host_id="worker-B", batch=10, lease_ttl=600
            )
            assert {c[0] for c in claimed} == set(free), (
                "host B must claim exactly the unlocked candidates while "
                "host A's transaction holds the first two rows"
            )
        held.rollback()

    # A's locks are gone (no claims were written by the raw transaction) —
    # the two skipped chunks are claimable now.
    leftovers = backend.claim_chunks_for_embedding(emb, host_id="worker-A", batch=10, lease_ttl=600)
    assert {c[0] for c in leftovers} == set(locked)


def test_simultaneous_same_lane_claims_are_disjoint(backend: PostgresBackend) -> None:
    """Two workers drain one lane at the same moment: no overlap, no loss.

    Both transactions order candidates by ``c.id``, so they contend on
    the *same* leading rows — the winner locks them, SKIP LOCKED routes
    the loser to the next free rows. A barrier maximises the overlap
    window; each claim runs on its own connection via a thread-local
    backend.
    """
    import threading

    emb = _register_embedder(backend)
    chunk_ids = _seed_chunks(backend, n=8)

    barrier = threading.Barrier(2)
    results: dict[str, list[tuple[int, str, str]]] = {}
    errors: list[BaseException] = []

    def worker(host_id: str) -> None:
        b = PostgresBackend(dsn=backend.dsn)
        try:
            barrier.wait(timeout=10)
            results[host_id] = b.claim_chunks_for_embedding(
                emb, host_id=host_id, batch=4, lease_ttl=600
            )
        except BaseException as exc:  # surfaced below — don't swallow in thread
            errors.append(exc)
        finally:
            b.close()

    threads = [threading.Thread(target=worker, args=(h,)) for h in ("worker-A", "worker-B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, f"claim worker raised: {errors!r}"

    got_a = {c[0] for c in results["worker-A"]}
    got_b = {c[0] for c in results["worker-B"]}
    assert got_a.isdisjoint(got_b), "two hosts claimed overlapping chunks"
    assert got_a | got_b == set(chunk_ids), "chunks lost between concurrent claimers"
    assert _claim_count(backend) == len(chunk_ids)
