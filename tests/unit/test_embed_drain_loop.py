"""Unit tests for the RFC fleet-5 daemon embed-drain loop.

Covers the three behaviours the slice's success criteria call out:

* **No-op on a non-Postgres backend.** SQLite / a MagicMock raises
  ``FederationUnsupported`` on the claim primitive, so the drain loop
  must not warm up embedders or issue claims — today's ingest-time embed
  path is left untouched.
* **One drain iteration.** ``_drain_lane_batch`` claims a page, embeds
  the routed-in chunks, writes the embeddings, and releases the claims
  exactly once — even when routing drops rows, even when embedding
  raises mid-batch (release-in-finally), and even when release itself
  raises ``FederationUnsupported`` (swallowed).
* **Idle / backoff.** With an empty backlog the loop backs off with
  bounded exponential cadence (``drain_idle_min`` → ``drain_idle_max``),
  does NOT hot-spin, and resets to the minimum the moment a non-empty
  batch appears.

The claim path is gated on a real ``PostgresBackend`` instance, so the
no-op test patches ``corpus_forge.daemon._RealPostgresBackend`` to a tiny
concrete class (mirroring ``test_embed_claim_loop``); the loop-cadence
tests patch ``_build_drain_lanes`` / ``_drain_lane_batch`` so they
exercise pure control flow without warming a real embedder.
"""

from __future__ import annotations

import contextlib
import signal
import threading
from unittest.mock import MagicMock, patch

import pytest

from corpus_forge import daemon as daemon_mod
from corpus_forge.backends.base import FederationUnsupported
from corpus_forge.config import EmbedConfig, ServiceConfig


@pytest.fixture(autouse=True)
def _restore_signal_handlers():
    """Snapshot + restore SIGINT/SIGTERM around tests that call run_daemon.

    ``run_daemon`` installs an ``os._exit``-based handler into the running
    worker; restoring keeps a leaked handler from killing later tests.
    """
    saved = {
        signal.SIGINT: signal.getsignal(signal.SIGINT),
        signal.SIGTERM: signal.getsignal(signal.SIGTERM),
    }
    yield
    for sig, handler in saved.items():
        if handler is not None:
            with contextlib.suppress(Exception):
                signal.signal(sig, handler)


class _FakeStopEvent:
    """A stop-event WITHOUT a ``wait`` method.

    ``_interruptible_sleep`` falls back to the injected ``sleep`` when the
    event exposes no ``wait`` — exactly what these cadence tests want so
    the backoff durations land in the injected ``sleep`` recorder.
    """

    def __init__(self) -> None:
        self._set = False

    def is_set(self) -> bool:
        return self._set

    def set(self) -> None:
        self._set = True


def _make_lane(name: str = "lane-a", embedder_id: int = 1) -> daemon_mod._DrainLane:
    embedder = MagicMock()
    embedder.name = name
    embedder.extensions = None
    embedder.last_failed_indices = []
    return daemon_mod._DrainLane(
        embedder=embedder,
        embedder_id=embedder_id,
        embedder_config=MagicMock(base_url=None),
        ext_filter=None,
        transport="local",
        device="cpu",
    )


def _drain_config(idle_min: float = 1.0, idle_max: float = 8.0) -> MagicMock:
    config = MagicMock()
    config.host_id.return_value = "test-host"
    config.embed = EmbedConfig(drain_idle_min=idle_min, drain_idle_max=idle_max)
    return config


# ── No-op on a non-Postgres backend ──────────────────────────────────────


class TestNonPostgresNoOp:
    def test_magicmock_backend_is_a_noop(self) -> None:
        """A non-Postgres backend → no warmup, no claim calls."""
        backend = MagicMock()  # not an instance of _RealPostgresBackend
        config = _drain_config()
        with patch.object(daemon_mod, "_build_drain_lanes") as mock_build:
            daemon_mod.run_embed_drain_loop(config, backend, stop_event=_FakeStopEvent())
        mock_build.assert_not_called()
        backend.claim_chunks_for_embedding.assert_not_called()

    def test_no_active_lanes_returns_idle(self) -> None:
        """A Postgres backend with no owned lanes → loop never sweeps."""

        class _PG: ...

        backend = _PG()
        config = _drain_config()
        ev = _FakeStopEvent()
        with (
            patch.object(daemon_mod, "_RealPostgresBackend", _PG),
            patch.object(daemon_mod, "_build_drain_lanes", return_value=([], [])),
            patch.object(daemon_mod, "_drain_lane_batch") as mock_batch,
        ):
            daemon_mod.run_embed_drain_loop(config, backend, stop_event=ev)
        mock_batch.assert_not_called()


# ── Idle / backoff cadence ────────────────────────────────────────────────


class TestBackoffCadence:
    def _run(self, batch_results, idle_min=1.0, idle_max=8.0, stop_after=4):
        """Drive the loop with a scripted ``_drain_lane_batch`` sequence.

        ``batch_results`` is consumed one entry per sweep; a missing entry
        defaults to ``(False, 0)`` (empty). The injected ``sleep`` records
        each backoff duration and stops the loop after ``stop_after`` calls.
        """

        class _PG: ...

        backend = _PG()
        config = _drain_config(idle_min=idle_min, idle_max=idle_max)
        ev = _FakeStopEvent()
        durations: list[float] = []
        seq = iter(batch_results)

        def fake_sleep(seconds: float) -> None:
            durations.append(seconds)
            if len(durations) >= stop_after:
                ev.set()

        def fake_batch(*_args, **_kwargs):
            try:
                return next(seq)
            except StopIteration:
                return (False, 0)

        lane = _make_lane()
        with (
            patch.object(daemon_mod, "_RealPostgresBackend", _PG),
            patch.object(daemon_mod, "_build_drain_lanes", return_value=([lane], [lane.embedder])),
            patch.object(daemon_mod, "_drain_lane_batch", side_effect=fake_batch),
        ):
            daemon_mod.run_embed_drain_loop(config, backend, stop_event=ev, sleep=fake_sleep)
        return durations

    def test_empty_backlog_backs_off_exponentially(self) -> None:
        # Every sweep empty → 1, 2, 4, 8 (doubling, capped at idle_max=8).
        durations = self._run([], idle_min=1.0, idle_max=8.0, stop_after=4)
        assert durations == [1.0, 2.0, 4.0, 8.0]

    def test_backoff_capped_at_max(self) -> None:
        durations = self._run([], idle_min=1.0, idle_max=2.0, stop_after=4)
        # 1 → 2 → capped at 2 → 2 ...
        assert durations == [1.0, 2.0, 2.0, 2.0]

    def test_does_not_hot_spin(self) -> None:
        # Each empty sweep is followed by exactly one sleep — never a tight
        # loop. (4 sweeps → 4 sleeps, none zero.)
        durations = self._run([], stop_after=4)
        assert len(durations) == 4
        assert all(d > 0 for d in durations)

    def test_work_resets_backoff_to_min(self) -> None:
        # empty, empty, WORK, empty, empty → the post-work sleep drops back
        # to idle_min (1), proving the reset.
        results = [(False, 0), (False, 0), (True, 5), (False, 0), (False, 0)]
        durations = self._run(results, idle_min=1.0, idle_max=8.0, stop_after=4)
        assert durations == [1.0, 2.0, 1.0, 2.0]


# ── One drain iteration: _drain_lane_batch ────────────────────────────────


class TestDrainLaneBatch:
    def _backend(self) -> MagicMock:
        backend = MagicMock()
        backend.release_claims.return_value = 0
        return backend

    def test_happy_path_claim_embed_write_release(self) -> None:
        backend = self._backend()
        backend.claim_chunks_for_embedding.return_value = [
            (1, "alpha", "file://a"),
            (2, "beta", "file://b"),
        ]
        lane = _make_lane(embedder_id=7)
        v1, v2 = object(), object()
        lane.embedder.encode.return_value = [v1, v2]
        config = _drain_config()

        with patch("corpus_forge.embedders.routing.route_for", return_value=lane.embedder):
            claimed, embedded = daemon_mod._drain_lane_batch(
                backend, lane, [lane.embedder], "host", 600, config
            )

        assert (claimed, embedded) == (True, 2)
        lane.embedder.encode.assert_called_once_with(["alpha", "beta"])
        backend.write_embeddings.assert_called_once_with(7, [(1, v1), (2, v2)])
        backend.release_claims.assert_called_once_with(7, "host", [1, 2])
        # Cursor advanced to the max claimed id.
        assert lane.after_id == 2

    def test_routing_filters_but_releases_all_claims(self) -> None:
        """Lane pinning: only routed-in chunks embed; ALL claims release."""
        backend = self._backend()
        backend.claim_chunks_for_embedding.return_value = [
            (1, "mine", "file://a"),
            (2, "other", "file://b"),
        ]
        lane = _make_lane(embedder_id=3)
        other = MagicMock(name="other-embedder")
        lane.embedder.encode.return_value = [object()]
        config = _drain_config()

        def route(uri, _embs):
            return lane.embedder if uri == "file://a" else other

        with patch("corpus_forge.embedders.routing.route_for", side_effect=route):
            claimed, embedded = daemon_mod._drain_lane_batch(
                backend, lane, [lane.embedder, other], "host", 600, config
            )

        assert (claimed, embedded) == (True, 1)
        lane.embedder.encode.assert_called_once_with(["mine"])
        # Only the routed-in chunk is written, but BOTH claims are released.
        assert backend.write_embeddings.call_args.args[1] == [
            (1, lane.embedder.encode.return_value[0])
        ]
        backend.release_claims.assert_called_once_with(3, "host", [1, 2])

    def test_empty_page_resets_cursor(self) -> None:
        backend = self._backend()
        backend.claim_chunks_for_embedding.return_value = []
        lane = _make_lane()
        lane.after_id = 99  # a prior sweep advanced it
        config = _drain_config()

        claimed, embedded = daemon_mod._drain_lane_batch(
            backend, lane, [lane.embedder], "host", 600, config
        )

        assert (claimed, embedded) == (False, 0)
        assert lane.after_id is None
        backend.write_embeddings.assert_not_called()
        backend.release_claims.assert_not_called()

    def test_federation_unsupported_on_claim_is_no_work(self) -> None:
        backend = self._backend()
        backend.claim_chunks_for_embedding.side_effect = FederationUnsupported("nope")
        lane = _make_lane()
        config = _drain_config()

        claimed, embedded = daemon_mod._drain_lane_batch(
            backend, lane, [lane.embedder], "host", 600, config
        )

        assert (claimed, embedded) == (False, 0)
        backend.write_embeddings.assert_not_called()

    def test_embedding_error_still_releases_claims(self) -> None:
        backend = self._backend()
        backend.claim_chunks_for_embedding.return_value = [(1, "x", "file://a")]
        lane = _make_lane(embedder_id=5)
        lane.embedder.encode.side_effect = RuntimeError("model wedged")
        config = _drain_config()

        with (
            patch("corpus_forge.embedders.routing.route_for", return_value=lane.embedder),
            pytest.raises(RuntimeError),
        ):
            daemon_mod._drain_lane_batch(backend, lane, [lane.embedder], "host", 600, config)

        # Release-in-finally: the page's claim is freed for retry elsewhere.
        backend.release_claims.assert_called_once_with(5, "host", [1])

    def test_federation_unsupported_on_release_is_swallowed(self) -> None:
        backend = self._backend()
        backend.claim_chunks_for_embedding.return_value = [(1, "x", "file://a")]
        backend.release_claims.side_effect = FederationUnsupported("demoted")
        lane = _make_lane(embedder_id=9)
        lane.embedder.encode.return_value = [object()]
        config = _drain_config()

        with patch("corpus_forge.embedders.routing.route_for", return_value=lane.embedder):
            claimed, embedded = daemon_mod._drain_lane_batch(
                backend, lane, [lane.embedder], "host", 600, config
            )

        # Write succeeded; the release failure is swallowed, not raised.
        assert (claimed, embedded) == (True, 1)
        backend.write_embeddings.assert_called_once()


# ── run_daemon wiring: the drain thread is gated on [service] embed_drain ──


class TestRunDaemonWiring:
    def _config(self, *, embed_drain: bool, ingest_watch: bool = True) -> MagicMock:
        config = MagicMock()
        config.datasets = []  # no sync engines to build
        config.host_id.return_value = "test-host"
        config.service = ServiceConfig(embed_drain=embed_drain, ingest_watch=ingest_watch)
        return config

    def test_drain_thread_started_when_enabled(self) -> None:
        config = self._config(embed_drain=True)
        backend = MagicMock()
        fake_thread = MagicMock()
        with (
            patch("corpus_forge.telemetry_registry.heartbeat"),
            patch.object(daemon_mod, "_get_any_backend", return_value=backend),
            patch.object(daemon_mod.threading, "Thread", return_value=fake_thread) as mk,
            patch.object(daemon_mod.signal, "signal"),
        ):
            daemon_mod.run_daemon(config)

        # A daemon thread targeting the drain loop was created and started.
        assert mk.call_count == 1
        assert mk.call_args.kwargs["target"] is daemon_mod.run_embed_drain_loop
        assert mk.call_args.kwargs["daemon"] is True
        fake_thread.start.assert_called_once()

    def test_drain_thread_not_started_when_disabled(self) -> None:
        config = self._config(embed_drain=False)
        backend = MagicMock()
        with (
            patch("corpus_forge.telemetry_registry.heartbeat"),
            patch.object(daemon_mod, "_get_any_backend", return_value=backend),
            patch.object(daemon_mod.threading, "Thread") as mk,
            patch.object(daemon_mod.signal, "signal"),
        ):
            daemon_mod.run_daemon(config)
        mk.assert_not_called()

    def test_drain_thread_not_started_without_backend(self) -> None:
        config = self._config(embed_drain=True)
        with (
            patch("corpus_forge.telemetry_registry.heartbeat"),
            patch.object(daemon_mod, "_get_any_backend", return_value=None),
            patch.object(daemon_mod.threading, "Thread") as mk,
            patch.object(daemon_mod.signal, "signal"),
        ):
            daemon_mod.run_daemon(config)
        mk.assert_not_called()

    def test_ingest_watch_off_skips_sync_engines(self) -> None:
        dataset = MagicMock(sync_enabled=True, sources=[MagicMock()])
        dataset.name = "vault"
        config = self._config(embed_drain=False, ingest_watch=False)
        config.datasets = [dataset]
        backend = MagicMock()
        with (
            patch("corpus_forge.telemetry_registry.heartbeat"),
            patch.object(daemon_mod, "_get_any_backend", return_value=backend),
            patch.object(daemon_mod, "SyncEngine") as mock_engine,
            patch.object(daemon_mod.signal, "signal"),
        ):
            daemon_mod.run_daemon(config)
        # ingest_watch=false → no sync engine, even for a sync_enabled dataset.
        mock_engine.assert_not_called()

    def test_shutdown_signals_drain_stop(self) -> None:
        config = self._config(embed_drain=True)
        backend = MagicMock()
        fake_thread = MagicMock()
        captured: dict = {}

        def capture_thread(*_args, **kwargs):
            captured["stop_event"] = kwargs["kwargs"]["stop_event"]
            return fake_thread

        with (
            patch("corpus_forge.telemetry_registry.heartbeat"),
            patch.object(daemon_mod, "_get_any_backend", return_value=backend),
            patch.object(daemon_mod.threading, "Thread", side_effect=capture_thread),
            patch.object(daemon_mod.signal, "signal") as mock_signal,
            patch.object(daemon_mod, "_exit_hard"),
            patch("corpus_forge.admin.foreground.clear_pid"),
        ):
            daemon_mod.run_daemon(config)
            handler = mock_signal.call_args_list[0][0][1]
            handler(signal.SIGTERM, None)

        # The shutdown handler set the drain loop's stop event and joined it.
        assert isinstance(captured["stop_event"], threading.Event)
        assert captured["stop_event"].is_set()
        fake_thread.join.assert_called_once()
