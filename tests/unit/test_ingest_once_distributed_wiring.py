"""DR-T6 (RED) — ingest_once distributed-resumability wiring tests.

Pins:
1. ``mark_stale_runs(threshold_seconds, host=hostname)`` is called AFTER
   ``migrate()`` and BEFORE ``latest_unfinished_ingest_run`` (C6 order).
2. The host string passed to BOTH calls is ``socket.gethostname()``.
3. ``latest_unfinished_ingest_run(host=hostname)`` is called on the resume
   path (``resume=True``) and NOT called on the non-resume path
   (``resume=False``).
4. ``stale_run_threshold=0.0`` → ``mark_stale_runs`` is still called (the
   short-circuit lives inside the backend method, not in ``ingest_once``).
   Decision locked: ingest_once always calls mark_stale_runs; threshold ≤ 0
   no-op semantics are the backend's responsibility.  See tasks.md DR-T6 notes.
5. Cross-machine simulation: patching ``socket.gethostname`` to "machine-a"
   then "machine-b" passes the correct host string to both calls each time.
6. INFO log is emitted when ``mark_stale_runs`` returns a count > 0.

All tests MUST FAIL now because:
- ``ingest_once`` does not call ``mark_stale_runs`` at all.
- ``latest_unfinished_ingest_run`` does not accept a ``host=`` keyword.
- ``ScanConfig.stale_run_threshold`` does not yet exist (attribute falls back
  to MagicMock auto-attribute, so the threshold value is unchecked, but the
  call assertion still fails).

Expected RED reasons:
- AssertionError: mark_stale_runs was not called / called with wrong args
- TypeError: latest_unfinished_ingest_run() got an unexpected keyword argument 'host'
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from corpus_forge.ingest import ingest_once

# ---------------------------------------------------------------------------
# Config factory
# ---------------------------------------------------------------------------


def _make_config(*, kind: str = "postgres", stale_run_threshold: float = 900.0) -> MagicMock:
    """Return a minimal mock Config with postgres backend and stale threshold."""
    config = MagicMock()
    config.backend.kind = kind
    config.backend.dsn = "postgresql://user:pass@localhost/forge_test"
    config.backend.schema = "corpus"
    config.daemon.log_level = "INFO"
    config.datasets = []
    config.embedders = []
    # Wire ScanConfig.stale_run_threshold
    config.scan.stale_run_threshold = stale_run_threshold
    return config


def _make_backend_mock(*, mark_stale_returns: int = 0) -> MagicMock:
    """Return a MagicMock backend with standard ingest_once support wired."""
    backend = MagicMock()

    # lock_source returns a trivial no-op context manager
    @contextmanager
    def _lock(*args, **kwargs):
        yield

    backend.lock_source.side_effect = _lock
    backend.migrate.return_value = None
    backend.mark_stale_runs.return_value = mark_stale_returns
    backend.latest_unfinished_ingest_run.return_value = None
    backend.start_ingest_run.return_value = None
    return backend


# ---------------------------------------------------------------------------
# Class 1: mark_stale_runs is called
# ---------------------------------------------------------------------------


class TestMarkStaleRunsIsCalled:
    """ingest_once calls backend.mark_stale_runs on every invocation."""

    def test_mark_stale_runs_called_once(self, tmp_path):
        """mark_stale_runs is called exactly once per ingest_once invocation."""
        config = _make_config(stale_run_threshold=900.0)
        backend = _make_backend_mock()

        with (
            patch("corpus_forge.ingest.PostgresBackend", return_value=backend),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
        ):
            ingest_once(config)

        backend.mark_stale_runs.assert_called_once()

    def test_mark_stale_runs_called_with_threshold_from_scan_config(self, tmp_path):
        """mark_stale_runs receives threshold_seconds from config.scan.stale_run_threshold."""
        config = _make_config(stale_run_threshold=600.0)
        backend = _make_backend_mock()

        with (
            patch("corpus_forge.ingest.PostgresBackend", return_value=backend),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
            patch("corpus_forge.ingest.socket.gethostname", return_value="test-host"),
        ):
            ingest_once(config)

        backend.mark_stale_runs.assert_called_once_with(600.0, host="test-host")

    def test_mark_stale_runs_called_with_host_keyword(self, tmp_path):
        """mark_stale_runs host= is socket.gethostname() (keyword-only per C5)."""
        fake_host = "corpus-node-42"
        config = _make_config(stale_run_threshold=900.0)
        backend = _make_backend_mock()

        with (
            patch("corpus_forge.ingest.PostgresBackend", return_value=backend),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
            patch("corpus_forge.ingest.socket.gethostname", return_value=fake_host),
        ):
            ingest_once(config)

        call_kwargs = backend.mark_stale_runs.call_args.kwargs
        assert call_kwargs.get("host") == fake_host, (
            f"mark_stale_runs must be called with host={fake_host!r}; got kwargs: {call_kwargs}"
        )

    def test_mark_stale_runs_called_even_when_threshold_is_zero(self, tmp_path):
        """stale_run_threshold=0.0 still triggers the call; backend no-ops internally.

        Decision: ingest_once ALWAYS calls mark_stale_runs. The threshold ≤ 0
        short-circuit is the backend's responsibility (C5: 'threshold_seconds <= 0
        → no-op, returns 0'). This test locks that policy: the call must not be
        skipped by ingest_once itself.
        """
        config = _make_config(stale_run_threshold=0.0)
        backend = _make_backend_mock(mark_stale_returns=0)

        with (
            patch("corpus_forge.ingest.PostgresBackend", return_value=backend),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
        ):
            ingest_once(config)

        backend.mark_stale_runs.assert_called_once()


# ---------------------------------------------------------------------------
# Class 2: order of operations (C6)
# ---------------------------------------------------------------------------


class TestOrderOfOperations:
    """mark_stale_runs → lock_source → migrate → mark_stale_runs → latest_unfinished.

    C6 binding:
      1. host + lock_key built.
      2. lock_source(lock_key, ...) acquired.
      3. migrate().
      4. mark_stale_runs(threshold, host=host).
      5. config_digest computed.
      6. If resume: latest_unfinished_ingest_run(host=host).
    """

    def test_mark_stale_runs_called_after_migrate(self, tmp_path):
        """mark_stale_runs fires AFTER migrate() (columns must exist first)."""
        call_order: list[str] = []

        backend = MagicMock()

        @contextmanager
        def _lock(*args, **kwargs):
            yield

        backend.lock_source.side_effect = _lock
        backend.migrate.side_effect = lambda: call_order.append("migrate")
        backend.mark_stale_runs.side_effect = lambda *a, **kw: call_order.append("mark_stale")
        backend.latest_unfinished_ingest_run.return_value = None
        backend.start_ingest_run.return_value = None

        config = _make_config()

        with (
            patch("corpus_forge.ingest.PostgresBackend", return_value=backend),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
        ):
            ingest_once(config)

        assert "migrate" in call_order, "migrate() was never called"
        assert "mark_stale" in call_order, "mark_stale_runs was never called"
        assert call_order.index("migrate") < call_order.index("mark_stale"), (
            f"migrate must precede mark_stale_runs; got order: {call_order}"
        )

    def test_mark_stale_runs_called_before_latest_unfinished(self, tmp_path):
        """mark_stale_runs fires BEFORE latest_unfinished_ingest_run (resume path).

        A stale run on this host must be flushed to 'failed' before the resume
        lookup so the resume path doesn't pick up its own ghost.
        """
        call_order: list[str] = []

        backend = MagicMock()

        @contextmanager
        def _lock(*args, **kwargs):
            yield

        backend.lock_source.side_effect = _lock
        backend.migrate.return_value = None
        backend.mark_stale_runs.side_effect = lambda *a, **kw: call_order.append("mark_stale") or 0
        backend.latest_unfinished_ingest_run.side_effect = lambda **kw: (
            call_order.append("latest_unfinished") or None
        )
        backend.start_ingest_run.return_value = None

        config = _make_config()

        with (
            patch("corpus_forge.ingest.PostgresBackend", return_value=backend),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
        ):
            ingest_once(config, resume=True)

        assert "mark_stale" in call_order, "mark_stale_runs was never called"
        assert "latest_unfinished" in call_order, "latest_unfinished_ingest_run was never called"
        assert call_order.index("mark_stale") < call_order.index("latest_unfinished"), (
            f"mark_stale_runs must precede latest_unfinished_ingest_run; got: {call_order}"
        )

    def test_lock_source_called_before_mark_stale(self, tmp_path):
        """lock_source context is entered before mark_stale_runs fires.

        The lock serialises startup: mark_stale inside the lock prevents two
        simultaneous ingest_once calls from both marking the same run stale.
        """
        call_order: list[str] = []

        backend = MagicMock()

        @contextmanager
        def _tracking_lock(*args, **kwargs):
            call_order.append("lock_acquired")
            yield
            call_order.append("lock_released")

        backend.lock_source.side_effect = _tracking_lock
        backend.migrate.return_value = None
        backend.mark_stale_runs.side_effect = lambda *a, **kw: call_order.append("mark_stale") or 0
        backend.latest_unfinished_ingest_run.return_value = None
        backend.start_ingest_run.return_value = None

        config = _make_config()

        with (
            patch("corpus_forge.ingest.PostgresBackend", return_value=backend),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
        ):
            ingest_once(config)

        assert "lock_acquired" in call_order, "lock_source was never acquired"
        assert "mark_stale" in call_order, "mark_stale_runs was never called"
        assert call_order.index("lock_acquired") < call_order.index("mark_stale"), (
            f"lock must be acquired before mark_stale_runs; got: {call_order}"
        )


# ---------------------------------------------------------------------------
# Class 3: host-scoped resume (C4 + C6)
# ---------------------------------------------------------------------------


class TestHostScopedResume:
    """latest_unfinished_ingest_run is called with host= on resume=True path."""

    def test_resume_true_passes_host_to_latest_unfinished(self, tmp_path):
        """With resume=True, latest_unfinished_ingest_run receives host=hostname."""
        fake_host = "resume-host-01"
        config = _make_config()
        backend = _make_backend_mock()

        with (
            patch("corpus_forge.ingest.PostgresBackend", return_value=backend),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
            patch("corpus_forge.ingest.socket.gethostname", return_value=fake_host),
        ):
            ingest_once(config, resume=True)

        backend.latest_unfinished_ingest_run.assert_called_once_with(host=fake_host)

    def test_resume_false_does_not_call_latest_unfinished(self, tmp_path):
        """With resume=False (default), latest_unfinished_ingest_run is never called."""
        config = _make_config()
        backend = _make_backend_mock()

        with (
            patch("corpus_forge.ingest.PostgresBackend", return_value=backend),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
        ):
            ingest_once(config, resume=False)

        backend.latest_unfinished_ingest_run.assert_not_called()

    def test_resume_true_host_matches_gethostname(self, tmp_path):
        """The host string in latest_unfinished_ingest_run matches socket.gethostname()."""
        fake_host = "verify-hostname-match"
        config = _make_config()
        captured_host: list[str] = []

        backend = _make_backend_mock()
        backend.latest_unfinished_ingest_run.side_effect = lambda *, host=None: (
            captured_host.append(host) or None
        )

        with (
            patch("corpus_forge.ingest.PostgresBackend", return_value=backend),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
            patch("corpus_forge.ingest.socket.gethostname", return_value=fake_host),
        ):
            ingest_once(config, resume=True)

        assert captured_host == [fake_host], (
            f"latest_unfinished_ingest_run received host={captured_host!r}, "
            f"expected [{fake_host!r}]"
        )


# ---------------------------------------------------------------------------
# Class 4: cross-machine simulation
# ---------------------------------------------------------------------------


class TestCrossMachineSimulation:
    """Patching socket.gethostname to different values propagates to both calls."""

    def _run_ingest(self, fake_host: str, stale_run_threshold: float = 900.0) -> MagicMock:
        """Run ingest_once with a patched hostname and return the backend mock."""
        config = _make_config(stale_run_threshold=stale_run_threshold)
        backend = _make_backend_mock()

        with (
            patch("corpus_forge.ingest.PostgresBackend", return_value=backend),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
            patch("corpus_forge.ingest.socket.gethostname", return_value=fake_host),
        ):
            ingest_once(config, resume=True)

        return backend

    def test_machine_a_wires_correct_host_to_mark_stale(self):
        """machine-a → mark_stale_runs called with host='machine-a'."""
        backend = self._run_ingest("machine-a")
        kwargs = backend.mark_stale_runs.call_args.kwargs
        assert kwargs.get("host") == "machine-a", (
            f"Expected host='machine-a' in mark_stale_runs; got {kwargs}"
        )

    def test_machine_b_wires_correct_host_to_mark_stale(self):
        """machine-b → mark_stale_runs called with host='machine-b'."""
        backend = self._run_ingest("machine-b")
        kwargs = backend.mark_stale_runs.call_args.kwargs
        assert kwargs.get("host") == "machine-b", (
            f"Expected host='machine-b' in mark_stale_runs; got {kwargs}"
        )

    def test_machine_a_wires_correct_host_to_latest_unfinished(self):
        """machine-a → latest_unfinished_ingest_run called with host='machine-a'."""
        backend = self._run_ingest("machine-a")
        kwargs = backend.latest_unfinished_ingest_run.call_args.kwargs
        assert kwargs.get("host") == "machine-a", (
            f"Expected host='machine-a' in latest_unfinished_ingest_run; got {kwargs}"
        )

    def test_machine_b_wires_correct_host_to_latest_unfinished(self):
        """machine-b → latest_unfinished_ingest_run called with host='machine-b'."""
        backend = self._run_ingest("machine-b")
        kwargs = backend.latest_unfinished_ingest_run.call_args.kwargs
        assert kwargs.get("host") == "machine-b", (
            f"Expected host='machine-b' in latest_unfinished_ingest_run; got {kwargs}"
        )

    def test_both_calls_use_same_host_string(self):
        """mark_stale_runs and latest_unfinished_ingest_run see the same host value."""
        for fake_host in ("alpha-laptop", "beta-laptop", "gamma-server"):
            backend = self._run_ingest(fake_host)
            stale_kwargs = backend.mark_stale_runs.call_args.kwargs
            resume_kwargs = backend.latest_unfinished_ingest_run.call_args.kwargs
            assert stale_kwargs.get("host") == resume_kwargs.get("host") == fake_host, (
                f"Host mismatch for {fake_host!r}: "
                f"mark_stale_runs got {stale_kwargs.get('host')!r}, "
                f"latest_unfinished got {resume_kwargs.get('host')!r}"
            )


# ---------------------------------------------------------------------------
# Class 5: INFO log emitted when stale count > 0
# ---------------------------------------------------------------------------


class TestStaleCountLogging:
    """When mark_stale_runs returns > 0, an INFO log is emitted."""

    def test_info_log_emitted_when_stale_count_positive(self, tmp_path, caplog):
        """mark_stale_runs returning 3 causes an INFO log mentioning the count."""
        config = _make_config(stale_run_threshold=900.0)
        backend = _make_backend_mock(mark_stale_returns=3)

        with (
            patch("corpus_forge.ingest.PostgresBackend", return_value=backend),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
            caplog.at_level(logging.INFO, logger="corpus_forge.ingest"),
        ):
            ingest_once(config)

        # At least one INFO record must contain the stale count (3 or "3") and
        # something indicating it relates to stale runs.
        stale_logs = [
            r for r in caplog.records if r.levelno == logging.INFO and "stale" in r.message.lower()
        ]
        assert stale_logs, (
            "No INFO log mentioning 'stale' was emitted after mark_stale_runs returned 3. "
            "Expected a log like 'marked 3 stale ingest run(s) as failed'."
        )
        # Also verify the count appears in at least one of those records
        count_present = any("3" in r.message for r in stale_logs)
        assert count_present, (
            f"Stale log records exist but none contain the count '3': "
            f"{[r.message for r in stale_logs]}"
        )

    def test_no_stale_log_when_count_is_zero(self, tmp_path, caplog):
        """mark_stale_runs returning 0 does NOT produce a stale INFO log (no noise)."""
        config = _make_config(stale_run_threshold=900.0)
        backend = _make_backend_mock(mark_stale_returns=0)

        with (
            patch("corpus_forge.ingest.PostgresBackend", return_value=backend),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
            caplog.at_level(logging.INFO, logger="corpus_forge.ingest"),
        ):
            ingest_once(config)

        # No log should say "marked N stale" when N == 0
        noise_logs = [
            r
            for r in caplog.records
            if "stale" in r.message.lower() and "marked" in r.message.lower()
        ]
        assert not noise_logs, (
            f"Unexpected stale log when count=0: {[r.message for r in noise_logs]}"
        )

    def test_info_log_emitted_when_stale_count_is_one(self, tmp_path, caplog):
        """Edge case: count=1 still triggers the log (not just plural counts)."""
        config = _make_config(stale_run_threshold=900.0)
        backend = _make_backend_mock(mark_stale_returns=1)

        with (
            patch("corpus_forge.ingest.PostgresBackend", return_value=backend),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
            caplog.at_level(logging.INFO, logger="corpus_forge.ingest"),
        ):
            ingest_once(config)

        stale_logs = [
            r for r in caplog.records if r.levelno == logging.INFO and "stale" in r.message.lower()
        ]
        assert stale_logs, "No INFO log mentioning 'stale' emitted when mark_stale_runs returned 1."


# ---------------------------------------------------------------------------
# Class 6: stale_run_threshold=0.0 policy (decision locked)
# ---------------------------------------------------------------------------


class TestThresholdZeroPolicy:
    """threshold=0.0 → mark_stale_runs is still called (backend owns the no-op)."""

    def test_mark_stale_called_with_zero_threshold(self, tmp_path):
        """When stale_run_threshold=0.0, call passes 0.0 to mark_stale_runs."""
        config = _make_config(stale_run_threshold=0.0)
        backend = _make_backend_mock(mark_stale_returns=0)

        with (
            patch("corpus_forge.ingest.PostgresBackend", return_value=backend),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
            patch("corpus_forge.ingest.socket.gethostname", return_value="any-host"),
        ):
            ingest_once(config)

        backend.mark_stale_runs.assert_called_once()
        positional_args = backend.mark_stale_runs.call_args.args
        kwargs = backend.mark_stale_runs.call_args.kwargs
        # threshold_seconds is the first positional or keyword arg
        threshold_received = (
            positional_args[0] if positional_args else kwargs.get("threshold_seconds")
        )
        assert threshold_received == 0.0, (
            f"Expected threshold_seconds=0.0 to be forwarded; got {threshold_received!r}"
        )

    def test_no_stale_log_when_threshold_zero_and_count_zero(self, tmp_path, caplog):
        """threshold=0.0 + count=0 → no stale log emitted."""
        config = _make_config(stale_run_threshold=0.0)
        backend = _make_backend_mock(mark_stale_returns=0)

        with (
            patch("corpus_forge.ingest.PostgresBackend", return_value=backend),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
            caplog.at_level(logging.INFO, logger="corpus_forge.ingest"),
        ):
            ingest_once(config)

        noise_logs = [
            r
            for r in caplog.records
            if "stale" in r.message.lower() and "marked" in r.message.lower()
        ]
        assert not noise_logs, (
            f"Unexpected stale log with threshold=0 + count=0: {[r.message for r in noise_logs]}"
        )


# ---------------------------------------------------------------------------
# Class 7: SQLite backend path (same wiring, different backend class)
# ---------------------------------------------------------------------------


class TestSQLiteBackendWiring:
    """mark_stale_runs + latest_unfinished host-scope work the same on SQLite backend."""

    def test_mark_stale_called_on_sqlite_path(self, tmp_path):
        """SQLite path: mark_stale_runs is called with correct host."""
        fake_host = "sqlite-host"
        config = _make_config(kind="sqlite", stale_run_threshold=900.0)
        config.backend.dsn = str(tmp_path / "test.db")
        backend = _make_backend_mock()

        with (
            patch("corpus_forge.ingest.SQLiteBackend", return_value=backend),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
            patch("corpus_forge.ingest.socket.gethostname", return_value=fake_host),
        ):
            ingest_once(config)

        backend.mark_stale_runs.assert_called_once()
        kwargs = backend.mark_stale_runs.call_args.kwargs
        assert kwargs.get("host") == fake_host

    def test_latest_unfinished_called_with_host_on_sqlite_resume(self, tmp_path):
        """SQLite + resume=True: latest_unfinished_ingest_run called with host=."""
        fake_host = "sqlite-resume-host"
        config = _make_config(kind="sqlite", stale_run_threshold=900.0)
        config.backend.dsn = str(tmp_path / "test.db")
        backend = _make_backend_mock()

        with (
            patch("corpus_forge.ingest.SQLiteBackend", return_value=backend),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
            patch("corpus_forge.ingest.socket.gethostname", return_value=fake_host),
        ):
            ingest_once(config, resume=True)

        backend.latest_unfinished_ingest_run.assert_called_once_with(host=fake_host)
