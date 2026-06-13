"""Unit tests for the RFC fleet-2 claim/release backfill loop.

Covers the loop-level half of ``backfill_embedder``'s distributed
embedding path:

* **Path selection** — the claim path is taken only for a genuine
  Postgres backend (``isinstance(backend, _RealPostgresBackend)``); a
  MagicMock backend (the unit-suite shape) takes the byte-identical
  ``chunks_missing_embedding`` fallback.
* **Fallback on FederationUnsupported** — when the first claim call
  raises, the run demotes to ``chunks_missing_embedding`` and behaves
  identically (same chunks embedded, no claim calls thereafter).
* **Release-on-success** — after a page is written, its claims are
  released.
* **Release-on-failure** — a poison chunk (every chunk bisected out)
  still releases its claims so it's immediately retryable elsewhere.
* **Release-via-finally-on-exception** — an exception mid-page still
  releases the page's claims (no leaked reservations).

The claim path is gated on a real ``PostgresBackend`` instance, so these
tests stand up a tiny concrete subclass and patch
``corpus_forge.embed._RealPostgresBackend`` to it — that makes
``use_claims`` true without a live Postgres.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from corpus_forge import embed as embed_mod
from corpus_forge.backends.base import FederationUnsupported
from corpus_forge.config import Config


class _NullTask:
    """No-op stand-in for a rich progress task handle."""

    def update(self, *args: object, **kwargs: object) -> None:
        return None


class _ProgressShim:
    """Minimal ``make_progress`` yield surface: ``add_task`` + ``update``."""

    def add_task(self, *args: object, **kwargs: object) -> _NullTask:
        return _NullTask()

    def update(self, *args: object, **kwargs: object) -> None:
        return None


@contextlib.contextmanager
def _null_progress(*args: object, **kwargs: object) -> Iterator[_ProgressShim]:
    """Drop-in for ``make_progress`` that spawns NO rich ``Live`` thread.

    The real ``make_progress`` enters a ``rich.progress.Progress`` whose
    background refresh thread corrupts ``coverage`` data collection under
    ``pytest-xdist`` (the trace function races with the dying worker). The
    loop only uses ``.add_task`` / ``.update`` on the yielded object, so a
    null context with that surface keeps these unit tests honest while
    eliminating the thread.
    """
    yield _ProgressShim()


def _make_embedder_config_mock() -> MagicMock:
    cfg = MagicMock()
    cfg.name = "test-embedder"
    cfg.provider = "sentence_transformers"
    cfg.model_id = "test-model"
    cfg.dimension = 4
    cfg.normalize = True
    cfg.distance = "cosine"
    cfg.active = True
    cfg.batch_size = 32
    cfg.device = "auto"
    cfg.api_key_env = "OPENAI_API_KEY"
    cfg.extensions = []
    return cfg


def _make_config_mock(*, lease_ttl: int = 600) -> MagicMock:
    cfg = MagicMock()
    cfg.backend.kind = "postgres"
    cfg.backend.dsn = "postgresql://test@test/memory"
    cfg.backend.schema = "corpus"
    cfg.embedders = [_make_embedder_config_mock()]
    cfg.embed.claim_lease_ttl = lease_ttl
    cfg.host_id.return_value = "host-under-test"
    return cfg


def _make_embedder_mock() -> MagicMock:
    emb = MagicMock()
    emb.name = "test-embedder"
    emb.extensions = []  # catchall — routing keeps every chunk
    emb.last_failed_indices = []
    return emb


class _ClaimBackend:
    """Concrete (non-Mock) class so ``isinstance(backend, _RealPostgresBackend)``
    is True once we patch the module's class reference to this.

    Method bodies delegate to attached MagicMocks so tests can assert on
    call args while the loop still sees a real instance.
    """

    def __init__(self) -> None:
        self.migrate = MagicMock()
        self.register_embedder = MagicMock(return_value=1)
        self.count_chunks_missing_embedding = MagicMock(return_value=0)
        self.count_live_claims = MagicMock(return_value=0)
        self.claim_chunks_for_embedding = MagicMock(return_value=[])
        self.chunks_missing_embedding = MagicMock(return_value=[])
        self.release_claims = MagicMock(return_value=0)
        self.write_embeddings = MagicMock()
        self.insert_model_benchmark = MagicMock()
        self.upsert_host = MagicMock()
        self.upsert_models = MagicMock()


def _run_backfill(config: MagicMock, backend: _ClaimBackend, embedder: MagicMock) -> None:
    """Drive ``backfill_embedder`` with the claim path enabled.

    Patches: Config.load → our config mock; the registry register fns →
    our embedder; the PostgresBackend constructor → our backend; the
    telemetry heartbeat to a no-op; and ``_RealPostgresBackend`` to our
    concrete class so the isinstance gate selects the claim path.
    """
    with (
        patch.object(Config, "load", return_value=config),
        patch("corpus_forge.embed.register_from_config", return_value=embedder),
        patch("corpus_forge.embed.registry.register", return_value=embedder),
        patch("corpus_forge.embed.PostgresBackend", return_value=backend),
        patch("corpus_forge.embed._RealPostgresBackend", _ClaimBackend),
        patch("corpus_forge.embed.make_progress", _null_progress),
        patch("corpus_forge.telemetry_registry.heartbeat"),
    ):
        embed_mod.backfill_embedder("test-embedder")


class TestPathSelection:
    """The claim path is gated on a real Postgres backend instance."""

    def test_magicmock_backend_takes_fallback_path(self) -> None:
        """A MagicMock backend (not a real PostgresBackend) must NOT claim."""
        config = _make_config_mock()
        embedder = _make_embedder_mock()
        embedder.encode.return_value = [[0.1] * 4]

        backend = MagicMock()
        backend.register_embedder.return_value = 1
        backend.count_chunks_missing_embedding.return_value = 1
        # One non-empty page then an empty page terminates the loop (the
        # MagicMock ignores ``after_id``, so a fixed return_value would
        # re-yield the same row forever).
        backend.chunks_missing_embedding.side_effect = [[(1, "t", "")], []]

        with (
            patch.object(Config, "load", return_value=config),
            patch("corpus_forge.embed.register_from_config", return_value=embedder),
            patch("corpus_forge.embed.registry.register", return_value=embedder),
            patch("corpus_forge.embed.PostgresBackend", return_value=backend),
            patch("corpus_forge.embed.make_progress", _null_progress),
            patch("corpus_forge.telemetry_registry.heartbeat"),
        ):
            embed_mod.backfill_embedder("test-embedder")

        # Fallback path: chunks_missing_embedding used; claims never touched.
        backend.chunks_missing_embedding.assert_called()
        backend.claim_chunks_for_embedding.assert_not_called()
        backend.release_claims.assert_not_called()


class TestFallbackOnFederationUnsupported:
    """A FederationUnsupported on the first claim demotes to the old path."""

    def test_falls_back_and_behaves_identically(self) -> None:
        config = _make_config_mock()
        embedder = _make_embedder_mock()
        embedder.encode.return_value = [[0.1] * 4, [0.2] * 4]

        backend = _ClaimBackend()
        backend.count_chunks_missing_embedding.return_value = 2
        # count_live_claims succeeds (returns 0) so the pre-loop progress
        # total does NOT demote — the demotion must happen on the first
        # in-loop claim call, which is the path under test.
        backend.count_live_claims.return_value = 0
        # Claim raises → loop must fall back to chunks_missing_embedding.
        backend.claim_chunks_for_embedding.side_effect = FederationUnsupported("nope")
        backend.chunks_missing_embedding.side_effect = [
            [(1, "a", ""), (2, "b", "")],
            [],
        ]

        _run_backfill(config, backend, embedder)

        # The first claim was attempted exactly once, then never again.
        assert backend.claim_chunks_for_embedding.call_count == 1
        # The fallback fetch path drained the pool.
        backend.chunks_missing_embedding.assert_called()
        # The two chunks were embedded via the fallback (identical behavior).
        backend.write_embeddings.assert_called_once()
        written = backend.write_embeddings.call_args.args[1]
        assert {cid for cid, _ in written} == {1, 2}
        # No release on the fallback path (nothing was claimed).
        backend.release_claims.assert_not_called()

    def test_count_live_claims_unsupported_demotes_before_loop(self) -> None:
        """A FederationUnsupported from the pre-loop progress total demotes too.

        ``count_live_claims`` is queried once up-front to net out other
        hosts' claims from the progress total. If that raises (e.g. a
        backend kind that registers as a real Postgres class but doesn't
        actually federate), the run demotes to the fallback path *before*
        the loop and never attempts a claim.
        """
        config = _make_config_mock()
        embedder = _make_embedder_mock()
        embedder.encode.return_value = [[0.1] * 4, [0.2] * 4]

        backend = _ClaimBackend()
        backend.count_chunks_missing_embedding.return_value = 2
        backend.count_live_claims.side_effect = FederationUnsupported("nope")
        backend.chunks_missing_embedding.side_effect = [
            [(1, "a", ""), (2, "b", "")],
            [],
        ]

        _run_backfill(config, backend, embedder)

        # Demoted before the loop → claim never attempted.
        backend.claim_chunks_for_embedding.assert_not_called()
        # Fallback drained the pool and embedded both chunks.
        backend.write_embeddings.assert_called_once()
        written = backend.write_embeddings.call_args.args[1]
        assert {cid for cid, _ in written} == {1, 2}
        backend.release_claims.assert_not_called()


class TestReleaseOnSuccess:
    """After a page is written, its claims are released."""

    def test_release_called_with_written_ids(self) -> None:
        config = _make_config_mock()
        embedder = _make_embedder_mock()
        embedder.encode.return_value = [[0.1] * 4, [0.2] * 4]

        backend = _ClaimBackend()
        backend.count_chunks_missing_embedding.return_value = 2
        backend.count_live_claims.return_value = 0
        backend.claim_chunks_for_embedding.side_effect = [
            [(1, "a", ""), (2, "b", "")],
            [],
        ]

        _run_backfill(config, backend, embedder)

        backend.write_embeddings.assert_called_once()
        # release_claims called once for the worked page, with the page ids.
        backend.release_claims.assert_called_once()
        emb_id, host_id, ids = backend.release_claims.call_args.args
        assert emb_id == 1
        assert host_id == "host-under-test"
        assert set(ids) == {1, 2}


class TestReleaseOnFailure:
    """A poison page (all chunks bisected out) still releases its claims."""

    def test_release_called_when_all_chunks_skipped(self) -> None:
        config = _make_config_mock()
        embedder = _make_embedder_mock()

        # Bisecting embedder: returns nothing and flags every input failed.
        def fake_encode(texts):  # type: ignore[no-untyped-def]
            embedder.last_failed_indices = list(range(len(texts)))
            return []

        embedder.encode.side_effect = fake_encode

        backend = _ClaimBackend()
        backend.count_chunks_missing_embedding.return_value = 2
        backend.count_live_claims.return_value = 0
        # One non-empty claimed page; the all-skipped guard breaks the loop.
        backend.claim_chunks_for_embedding.return_value = [(1, "a", ""), (2, "b", "")]

        _run_backfill(config, backend, embedder)

        # Nothing was written (all bisected out)...
        backend.write_embeddings.assert_not_called()
        # ...but the claims for the poison page were released so they're
        # immediately retryable on another host.
        backend.release_claims.assert_called_once()
        _, host_id, ids = backend.release_claims.call_args.args
        assert host_id == "host-under-test"
        assert set(ids) == {1, 2}


class TestReleaseViaFinallyOnException:
    """An exception mid-page still releases the page's claims (no leak)."""

    def test_release_called_in_finally_on_write_error(self) -> None:
        config = _make_config_mock()
        embedder = _make_embedder_mock()
        embedder.encode.return_value = [[0.1] * 4, [0.2] * 4]

        backend = _ClaimBackend()
        backend.count_chunks_missing_embedding.return_value = 2
        backend.count_live_claims.return_value = 0
        backend.claim_chunks_for_embedding.return_value = [(1, "a", ""), (2, "b", "")]
        # write_embeddings blows up mid-page.
        backend.write_embeddings.side_effect = RuntimeError("db down")

        with pytest.raises(RuntimeError, match="db down"):
            _run_backfill(config, backend, embedder)

        # The finally released the page's claims despite the exception.
        backend.release_claims.assert_called_once()
        _, host_id, ids = backend.release_claims.call_args.args
        assert host_id == "host-under-test"
        assert set(ids) == {1, 2}


class TestProgressTotalSubtractsOtherHostClaims:
    """On the claim path the progress total nets out other hosts' claims."""

    def test_count_live_claims_excludes_self(self) -> None:
        config = _make_config_mock()
        embedder = _make_embedder_mock()

        backend = _ClaimBackend()
        backend.count_chunks_missing_embedding.return_value = 10
        backend.count_live_claims.return_value = 3
        backend.claim_chunks_for_embedding.return_value = []  # nothing left to do

        _run_backfill(config, backend, embedder)

        # count_live_claims queried with this host excluded.
        backend.count_live_claims.assert_called_once_with(1, exclude_host_id="host-under-test")


class _FakeFKViolation(Exception):
    """Mimics psycopg's ``ForeignKeyViolation`` by class name + sqlstate."""

    sqlstate = "23503"


class _NamedFKViolation(Exception):
    """Carries the class name but no ``sqlstate`` attribute."""


# Give the name-only variant the recognised class name without a sqlstate.
_NamedFKViolation.__name__ = "ForeignKeyViolation"


class TestIsFkViolation:
    """``_is_fk_violation`` recognises a Postgres FK violation two ways."""

    def test_sqlstate_match(self) -> None:
        assert embed_mod._is_fk_violation(_FakeFKViolation()) is True

    def test_class_name_match(self) -> None:
        assert embed_mod._is_fk_violation(_NamedFKViolation()) is True

    def test_unrelated_exception_is_not_fk(self) -> None:
        assert embed_mod._is_fk_violation(ValueError("nope")) is False


class TestFirstClaimFKViolationSelfHeals:
    """A FK violation on the FIRST claim re-heartbeats + retries the claim.

    RFC fleet-2 live bug (2026-06-08): a silent heartbeat failure leaves
    no ``corpus.hosts`` row, so the first claim insert trips the
    ``embed_claims.host_id`` FK. The old loop PERMANENTLY demoted the
    worker to the un-deduped ``chunks_missing_embedding`` fallback for its
    whole lifetime (racing other hosts). The fix re-heartbeats once and
    retries the claim instead — a missing-row FK is transient and
    self-heals; only ``FederationUnsupported`` earns a permanent demotion.
    """

    def test_first_claim_fk_reheartbeats_and_retries(self) -> None:
        config = _make_config_mock()
        embedder = _make_embedder_mock()
        embedder.encode.return_value = [[0.1] * 4, [0.2] * 4]

        backend = _ClaimBackend()
        backend.count_chunks_missing_embedding.return_value = 2
        backend.count_live_claims.return_value = 0
        # First claim trips the host_id FK; the re-heartbeat creates the
        # host row so the RETRY succeeds and the worker stays on the claim
        # path (no fallback). A trailing empty claim ends the loop.
        backend.claim_chunks_for_embedding.side_effect = [
            _FakeFKViolation(),
            [(1, "a", ""), (2, "b", "")],
            [],
        ]

        _run_backfill(config, backend, embedder)

        # Claim called 3x: initial FK, the successful retry, then the
        # loop-terminating empty page. Crucially the worker NEVER fell back.
        assert backend.claim_chunks_for_embedding.call_count == 3
        backend.chunks_missing_embedding.assert_not_called()
        backend.write_embeddings.assert_called_once()
        written = backend.write_embeddings.call_args.args[1]
        assert {cid for cid, _ in written} == {1, 2}
        # The claimed page was released after it was written.
        assert backend.release_claims.call_count >= 1

    def test_first_claim_fk_retry_also_fails_propagates(self) -> None:
        """If the re-heartbeat doesn't help, the retry's FK propagates.

        A host row that is STILL missing after an explicit re-heartbeat is
        a genuine error, not the transient startup race — it must surface,
        never silently latch the worker onto the fallback path.
        """
        config = _make_config_mock()
        embedder = _make_embedder_mock()

        backend = _ClaimBackend()
        backend.count_chunks_missing_embedding.return_value = 2
        backend.count_live_claims.return_value = 0
        # Both the first claim and the post-heartbeat retry FK-violate.
        backend.claim_chunks_for_embedding.side_effect = [
            _FakeFKViolation(),
            _FakeFKViolation(),
        ]

        with pytest.raises(_FakeFKViolation):
            _run_backfill(config, backend, embedder)

        # Initial claim + one retry, then it propagated — no silent fallback.
        assert backend.claim_chunks_for_embedding.call_count == 2
        backend.chunks_missing_embedding.assert_not_called()

    def test_later_fk_violation_propagates(self) -> None:
        """An FK violation after the first successful claim is a real bug.

        Only the *first* claim attempt forgives a FK violation (heartbeat
        race). A second-page FK violation propagates so the operator sees it.
        """
        config = _make_config_mock()
        embedder = _make_embedder_mock()
        embedder.encode.return_value = [[0.1] * 4]

        backend = _ClaimBackend()
        backend.count_chunks_missing_embedding.return_value = 3
        backend.count_live_claims.return_value = 0
        # First claim succeeds (one chunk), second claim trips the FK.
        backend.claim_chunks_for_embedding.side_effect = [
            [(1, "a", "")],
            _FakeFKViolation(),
        ]

        with pytest.raises(_FakeFKViolation):
            _run_backfill(config, backend, embedder)

        # The first page's claim was released before the second claim raised.
        assert backend.release_claims.call_count == 1
        _, host_id, ids = backend.release_claims.call_args.args
        assert host_id == "host-under-test"
        assert set(ids) == {1}
