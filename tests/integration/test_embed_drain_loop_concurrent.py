"""Integration test — RFC fleet-5: concurrent ``EmbedDrainLoop`` drain.

Two ``EmbedDrainLoop`` instances (distinct ``host_id``s, separate Postgres
connections) drain a shared backlog to zero *concurrently*. The test asserts
the fleet-2 claim guarantees hold end-to-end through the drain loop:

- **Full coverage** — every seeded chunk ends up embedded
  (``count_chunks_missing_embedding`` → 0).
- **No double-embedding** — no chunk gets more than one embedding row for
  the lane's embedder (the ``corpus.embed_claims`` ``FOR UPDATE SKIP
  LOCKED`` reservation kept the two hosts on disjoint work).

This rides the merged ``EmbedDrainLoop`` (#123) directly — it does **not**
need the daemon-lifecycle wiring (fleet-5 item 2b), so it runs on a clean
``origin/main`` base. Gated on ``requires_docker``; uses the ``pg_dsn``
fixture.
"""

from __future__ import annotations

import threading

import numpy as np
import pytest

from corpus_forge.backends.postgres import PostgresBackend
from corpus_forge.config import Config
from corpus_forge.embed_drain import EmbedDrainLoop
from corpus_forge.embedders.base import BaseEmbedder
from corpus_forge.sources.base import RawDocument

pytestmark = [pytest.mark.integration, pytest.mark.requires_docker]

_DIM = 8
_EMBEDDER_NAME = "drain-stub"


class _StubEmbedder(BaseEmbedder):
    """Deterministic in-process embedder — no model download.

    Optionally records every text it encodes into a shared ``_recorder``
    list (guarded by ``_lock``) so the test can assert, table-agnostically,
    that no chunk was ever encoded twice across the two drain hosts — the
    direct observable of the fleet-2 claim guarantee.
    """

    _recorder: list[str] | None = None
    _lock: threading.Lock | None = None

    def encode(self, texts, *, batch_size: int = 32) -> np.ndarray:  # type: ignore[override]
        if self._recorder is not None and self._lock is not None:
            with self._lock:
                self._recorder.extend(texts)
        out = np.zeros((len(texts), self.dimension), dtype=np.float32)
        for i, t in enumerate(texts):
            seed = sum(ord(c) for c in t) or 1
            out[i, seed % self.dimension] = 1.0
        return out


@pytest.fixture
def backend(pg_dsn: str) -> PostgresBackend:  # type: ignore[return]
    b = PostgresBackend(dsn=pg_dsn)
    b.migrate()
    # embed_claims.host_id FKs corpus.hosts — register the two drain hosts.
    for host_id in ("d1", "d2"):
        b.upsert_host(host_id=host_id, hostname=host_id, os="test", accelerator=None)
    yield b
    b.close()


def _config(pg_dsn: str) -> Config:
    return Config.model_validate(
        {
            "backend": {"kind": "postgres", "dsn": pg_dsn, "schema": "corpus"},
            "daemon": {},
            "datasets": [],
            "embedders": [
                {
                    "name": _EMBEDDER_NAME,
                    "provider": "sentence_transformers",
                    "model_id": "test/stub",
                    "dimension": _DIM,
                    "normalized": True,
                    "distance": "cosine",
                    "active": True,
                }
            ],
            # Small batches so both hosts get multiple sweeps and genuinely
            # interleave on the claim table.
            "embed": {"claim_batch_size": 8, "claim_lease_ttl": 600},
        }
    )


def _seed(b: PostgresBackend, n: int) -> int:
    """Seed one document with ``n`` chunks needing embedding."""
    dataset_id = b.get_or_create_dataset(name="drain-ds", kind="text", description="")
    doc = RawDocument(
        source_uri="vault://drain-file.md",
        content_hash="hash-drain",
        text="body",
        title="t",
        modified_at=1000.0,
        metadata={},
        labels=[],
    )
    chunks = [(f"h{i}", f"chunk text number {i}") for i in range(n)]
    b.upsert_document(dataset_id, doc, chunks)
    return dataset_id


def test_two_drain_loops_drain_disjoint_no_double_embedding(
    pg_dsn: str, backend: PostgresBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    n_chunks = 60
    _seed(backend, n_chunks)

    # ``resolve_lanes`` calls ``register_from_config(registry, ec)`` to build
    # each lane's embedder. Stub it so we get our in-process embedder instead
    # of trying to load a real sentence-transformers model.
    encoded: list[str] = []
    encoded_lock = threading.Lock()

    def _fake_register_from_config(_registry, ec):
        stub = _StubEmbedder(
            name=ec.name,
            provider=ec.provider,
            model_id=ec.model_id,
            dimension=ec.dimension,
        )
        stub._recorder = encoded
        stub._lock = encoded_lock
        return stub

    monkeypatch.setattr(
        "corpus_forge.embedders.registry.register_from_config",
        _fake_register_from_config,
    )

    config = _config(pg_dsn)
    embedder_id = backend.register_embedder(
        _StubEmbedder(name=_EMBEDDER_NAME, provider="test", model_id="stub", dimension=_DIM)
    )
    assert backend.count_chunks_missing_embedding(embedder_id) == n_chunks

    # Each loop gets its own backend connection (psycopg connections are not
    # shared across threads). Resolve lanes up front (main thread) so the
    # registry monkeypatch applies; the threads only call drain_once.
    b2 = PostgresBackend(dsn=pg_dsn)
    loops = [
        EmbedDrainLoop(backend, config, host_id="d1", batch=8),
        EmbedDrainLoop(b2, config, host_id="d2", batch=8),
    ]
    for loop in loops:
        loop.resolve_lanes()

    errors: dict[str, BaseException] = {}
    embedded: dict[str, int] = {}
    barrier = threading.Barrier(len(loops))

    def _worker(name: str, loop: EmbedDrainLoop) -> None:
        try:
            barrier.wait(timeout=10)
            total = 0
            # Drain greedily until the shared backlog is exhausted.
            for _ in range(1000):  # generous cap; guards against a runaway loop
                got = loop.drain_once()
                if got == 0:
                    break
                total += got
            embedded[name] = total
        except BaseException as exc:
            errors[name] = exc

    threads = [
        threading.Thread(target=_worker, args=(f"d{i + 1}", loop), name=f"drain-{i}")
        for i, loop in enumerate(loops)
    ]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
            assert not t.is_alive(), "drain worker did not finish within timeout"
    finally:
        b2.close()

    assert not errors, f"drain workers raised: {errors}"

    # Full coverage: the backlog drained to zero.
    assert backend.count_chunks_missing_embedding(embedder_id) == 0

    # No double-embedding (the fleet-2 claim guarantee, observed directly):
    # each chunk's text was encoded exactly once across BOTH hosts.
    assert len(encoded) == n_chunks, (
        f"expected {n_chunks} encode(s) total, got {len(encoded)} "
        f"(duplicates ⇒ two hosts claimed the same chunk)"
    )
    assert len(set(encoded)) == n_chunks, "a chunk was encoded by both hosts (claim race)"

    # Sum of per-host drained counts equals the backlog exactly — a second,
    # independent dedup proof (a double-claim would push the sum past
    # n_chunks). Per-host *fairness* isn't asserted: it's scheduler-dependent
    # and not what this test guards.
    assert embedded["d1"] + embedded["d2"] == n_chunks, (
        f"per-host drained counts {embedded} should sum to {n_chunks}"
    )

    # Claim rows are released after each batch (no orphaned claims left).
    remaining = backend._execute("SELECT COUNT(*) AS n FROM corpus.embed_claims")
    assert int(remaining[0]["n"]) == 0, "embed_claims not fully released after drain"
