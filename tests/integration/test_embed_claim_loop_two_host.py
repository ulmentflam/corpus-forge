"""Integration test — RFC fleet-2 two-host claim/release backfill loop.

Where ``test_postgres_embed_claims.py`` exercises the backend claim
primitives in isolation, this drives the *real*
:func:`corpus_forge.embed.backfill_embedder` loop end-to-end against a
testcontainers Postgres, twice, as two different hosts working the same
embedder lane concurrently. The acceptance bar from the RFC:

* every seeded chunk is embedded **exactly once** (the duplicate-work
  probe ``SELECT chunk_id, count(*) ... GROUP BY 1 HAVING count(*) > 1``
  returns zero rows), and
* **no claims are left behind** once both runs finish (release-on-
  completion drained ``corpus.embed_claims``).

Driving the genuine loop (rather than the backend seam) is what proves
the loop-level half of the feature: the claim fetch, the per-page
release in the ``finally``, and the path gate (``isinstance(backend,
_RealPostgresBackend)``) all run for real. The two runs execute on
worker threads with a barrier so their claim windows overlap; SKIP
LOCKED + the live-claim anti-join keep the work disjoint.

Gated on ``requires_docker``; uses the session-scoped ``pg_dsn`` fixture.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from corpus_forge import embed as embed_mod
from corpus_forge.backends.postgres import PostgresBackend
from corpus_forge.config import Config, EmbedConfig
from corpus_forge.embedders.base import BaseEmbedder
from corpus_forge.sources.base import RawDocument

pytestmark = [pytest.mark.integration, pytest.mark.requires_docker]


class _NoMigratePostgresBackend(PostgresBackend):
    """Postgres backend whose ``migrate`` is a no-op.

    Alembic's ``EnvironmentContext`` uses module-global proxies and is NOT
    thread-safe, so two worker threads calling ``migrate()`` at once race.
    The shared schema is migrated once on the main thread before the
    workers start, so each worker can safely skip it. (Production hosts
    migrate serially at startup, never concurrently.)
    """

    def migrate(self) -> None:
        return None


class _FakeEmbedder(BaseEmbedder):
    """Deterministic, dependency-free embedder for the loop test.

    Catchall (empty ``extensions``) so ``route_for`` keeps every chunk.
    ``encode`` returns a fixed-dim ones-vector per input and never drops
    rows (``last_failed_indices`` stays empty).
    """

    def __init__(self, dim: int = 8) -> None:
        super().__init__(
            name="claim-loop-embed",
            provider="sentence_transformers",
            model_id="test/model",
            dimension=dim,
            normalized=True,
            distance="cosine",
        )

    def warmup(self) -> None:
        return None

    def encode(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        self.last_failed_indices = []
        return np.ones((len(list(texts)), self.dimension), dtype=np.float32)


def _seed_chunks(b: PostgresBackend, n: int) -> list[int]:
    dataset_id = b.get_or_create_dataset(name="ds-loop", kind="text", description="")
    doc = RawDocument(
        source_uri="vault://file.md",
        content_hash="hash-loop",
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


def _make_config(host_id: str) -> MagicMock:
    """Config mock shaped exactly as ``backfill_embedder`` reads it."""
    ec = MagicMock()
    ec.name = "claim-loop-embed"
    ec.provider = "sentence_transformers"
    ec.model_id = "test/model"
    ec.dimension = 8
    ec.normalize = True
    ec.distance = "cosine"
    ec.active = True
    ec.batch_size = 32
    ec.device = "auto"
    ec.api_key_env = "OPENAI_API_KEY"
    ec.extensions = []

    cfg = MagicMock()
    cfg.backend.kind = "postgres"
    cfg.backend.dsn = "postgresql://unused"  # PostgresBackend ctor is patched
    cfg.backend.schema = "corpus"
    cfg.embedders = [ec]
    cfg.embed = EmbedConfig(claim_lease_ttl=600)
    cfg.host_id.return_value = host_id
    return cfg


def test_two_host_backfill_loops_embed_each_chunk_exactly_once(pg_dsn: str) -> None:
    # One shared schema, prepared once; each host's loop opens its own
    # backend connection (real concurrency, not a shared handle).
    prep = PostgresBackend(dsn=pg_dsn)
    prep.migrate()
    for host_id in ("loop-host-A", "loop-host-B"):
        prep.upsert_host(host_id=host_id, hostname=host_id, os="test", accelerator=None)
    chunk_ids = _seed_chunks(prep, n=40)
    # Pre-register the embedder once on the main thread so the two worker
    # loops take ``register_embedder``'s idempotent UPDATE branch instead
    # of racing on a first-time INSERT (the registration is check-then-
    # insert, not atomic — a non-issue in production where hosts register
    # serially at startup, but the test forces a worst-case overlap).
    prep.register_embedder(_FakeEmbedder())

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    # Per-thread state. ``unittest.mock.patch`` mutates *global* module
    # attributes and is NOT thread-safe, so we install the patches ONCE on
    # the main thread and have each patched callable dispatch on the
    # running thread's identity. This lets two genuine ``backfill_embedder``
    # loops run concurrently while each sees its own host_id, backend
    # connection, and embedder instance.
    state: dict[int, dict] = {}

    def run_host(host_id: str) -> None:
        tid = threading.get_ident()
        backend = _NoMigratePostgresBackend(dsn=pg_dsn)
        state[tid] = {
            "config": _make_config(host_id),
            "backend": backend,
            "embedder": _FakeEmbedder(),
        }
        try:
            barrier.wait(timeout=15)
            embed_mod.backfill_embedder("claim-loop-embed")
        except BaseException as exc:  # surface in the main thread
            errors.append(exc)
        finally:
            backend.close()

    def _cur() -> dict:
        return state[threading.get_ident()]

    threads = [threading.Thread(target=run_host, args=(h,)) for h in ("loop-host-A", "loop-host-B")]
    with (
        patch.object(Config, "load", side_effect=lambda *a, **k: _cur()["config"]),
        patch(
            "corpus_forge.embed.register_from_config",
            side_effect=lambda *a, **k: _cur()["embedder"],
        ),
        patch(
            "corpus_forge.embed.registry.register",
            side_effect=lambda *a, **k: _cur()["embedder"],
        ),
        patch(
            "corpus_forge.embed.PostgresBackend",
            side_effect=lambda *a, **k: _cur()["backend"],
        ),
        patch("corpus_forge.telemetry_registry.heartbeat"),
    ):
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)
    assert not errors, f"a host loop raised: {errors!r}"

    emb_row = prep.find_embedder_row_by_name("claim-loop-embed")
    assert emb_row is not None
    emb_id = emb_row["id"]

    # Exactly-once: the duplicate-work probe must return zero rows.
    dupes = prep._execute(
        "SELECT chunk_id, COUNT(*) AS n FROM corpus.embeddings_claim_loop_embed "
        "GROUP BY chunk_id HAVING COUNT(*) > 1"
    )
    assert dupes == [], f"chunks embedded more than once: {dupes!r}"

    # Completeness: every seeded chunk has an embedding row.
    embedded = {
        r["chunk_id"]
        for r in prep._execute("SELECT chunk_id FROM corpus.embeddings_claim_loop_embed")
    }
    assert embedded == set(chunk_ids), "some chunks were never embedded"

    # No claims left behind — release-on-completion drained the table.
    left = prep._execute(
        "SELECT COUNT(*) AS n FROM corpus.embed_claims WHERE embedder_id = %s",
        (emb_id,),
    )
    assert int(left[0]["n"]) == 0, "claims leaked after both hosts finished"

    prep.close()
