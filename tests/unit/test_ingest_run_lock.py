"""SR-T5 (RED) — Concurrent-run advisory lock at the ingest entry point.

Tests pin:

1. ``corpus_forge.identity.ingest_run_lock_key(host: str) -> int``
   - Derived from ``advisory_lock_key(f"ingest-run://{host}")``
   - Deterministic and stable across calls
   - Integer type

2. ``ingest_once(config)`` acquires a lock via ``backend.lock_source``
   before any DB writes (the ``advisory_lock_key`` that is keyed on
   the current host).

3. Contention with ``wait=False`` (default): second invocation exits with
   code 75 (POSIX EX_TEMPFAIL) and emits a clear "another run in progress"
   log.

4. Contention with ``wait=True``: the ``lock_source`` call is forwarded
   with ``wait=True`` (backend decides what that means).

5. Lock is released on normal exit, on exception, and the finally block
   always runs.

All tests MUST FAIL now because:
- ``ingest_run_lock_key`` does not exist in ``corpus_forge.identity``.
- ``IngestRunInProgressError`` does not exist in ``corpus_forge.backends.base``.
- ``ingest_once`` does not yet acquire the ingest-run advisory lock.
"""

from __future__ import annotations

import contextlib
import socket
import threading
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

# T2: new exception class
from corpus_forge.backends.base import IngestRunInProgressError  # does not exist yet

# ---------------------------------------------------------------------------
# Target imports — these are the RED triggers.
# ---------------------------------------------------------------------------
# T1: new helper on identity module
from corpus_forge.identity import (
    advisory_lock_key,
    ingest_run_lock_key,  # does not exist yet → ImportError / AttributeError
)

# ---------------------------------------------------------------------------
# Minimal fixtures
# ---------------------------------------------------------------------------

_FAKE_HOST = "test-host.local"


def _make_config(tmp_path, backend_kind="postgres"):
    """Return a minimal corpus_forge Config suitable for ingest_once mocking."""
    from corpus_forge.config import BackendConfig, Config, DaemonConfig

    return Config(
        backend=BackendConfig(kind=backend_kind, dsn="postgresql://localhost/forge_test"),
        daemon=DaemonConfig(),
        datasets=[],
        embedders=[],
    )


# ---------------------------------------------------------------------------
# 1. ingest_run_lock_key — deterministic integer helper
# ---------------------------------------------------------------------------


class TestIngestRunLockKey:
    """``ingest_run_lock_key(host)`` returns a stable, deterministic integer."""

    def test_returns_int(self):
        """Return value is an integer."""
        result = ingest_run_lock_key(_FAKE_HOST)
        assert isinstance(result, int)

    def test_stable_across_calls(self):
        """Same host always produces the same key — no randomness."""
        key1 = ingest_run_lock_key(_FAKE_HOST)
        key2 = ingest_run_lock_key(_FAKE_HOST)
        assert key1 == key2

    def test_different_hosts_produce_different_keys(self):
        """Different hostnames produce different keys (collision resistance)."""
        key_a = ingest_run_lock_key("host-a.local")
        key_b = ingest_run_lock_key("host-b.local")
        assert key_a != key_b

    def test_derived_from_advisory_lock_key(self):
        """Key equals ``advisory_lock_key(f"ingest-run://{host}")``.

        This pins the formula and ensures it doesn't drift from the
        shared ``advisory_lock_key`` primitive.
        """
        host = "corpus-node-01"
        expected = advisory_lock_key(f"ingest-run://{host}")
        assert ingest_run_lock_key(host) == expected

    def test_empty_string_host(self):
        """Edge case: empty string host still returns an int without crashing."""
        result = ingest_run_lock_key("")
        assert isinstance(result, int)

    def test_unicode_hostname(self):
        """Non-ASCII hostname (e.g., IDN) still hashes to an int."""
        result = ingest_run_lock_key("büro-server.example.com")
        assert isinstance(result, int)

    def test_key_is_within_postgres_advisory_range(self):
        """Postgres pg_advisory_lock accepts bigint (signed 64-bit).

        The value must fit in [-2^63, 2^63-1]. Also pin the non-negative
        convention that advisory_lock_key already guarantees.
        """
        key = ingest_run_lock_key("arbitrary-host")
        assert 0 <= key < 2**63

    def test_uses_real_hostname_when_socket_gethostname(self):
        """Key for socket.gethostname() is stable within the same process."""
        host = socket.gethostname()
        key1 = ingest_run_lock_key(host)
        key2 = ingest_run_lock_key(host)
        assert key1 == key2


# ---------------------------------------------------------------------------
# 2. IngestRunInProgressError exception class
# ---------------------------------------------------------------------------


class TestIngestRunInProgressError:
    """``IngestRunInProgressError`` is a proper exception with a message."""

    def test_is_exception_subclass(self):
        """Must be raise-able as an exception."""
        with pytest.raises(IngestRunInProgressError):
            raise IngestRunInProgressError("another ingest run is in progress on this host")

    def test_message_contains_host_phrase(self):
        """Error message should contain enough for the log/stderr."""
        err = IngestRunInProgressError("another ingest run is in progress on this host")
        assert "ingest" in str(err).lower()

    def test_is_base_exception_compatible(self):
        """Should be catch-able as a plain Exception."""
        with pytest.raises(IngestRunInProgressError):
            raise IngestRunInProgressError("contention")


# ---------------------------------------------------------------------------
# 3. ingest_once acquires the advisory lock before DB writes
# ---------------------------------------------------------------------------


class TestIngestOnceLockAcquired:
    """ingest_once calls backend.lock_source with the right key before doing work."""

    def _build_patched_ingest(self, tmp_path, lock_ctx_manager=None):
        """Return (config, mock_backend) with common patches wired up.

        ``lock_ctx_manager`` can be any context manager.  Defaults to a
        trivially no-op one so the happy path proceeds.
        """
        if lock_ctx_manager is None:
            lock_ctx_manager = contextlib.nullcontext()

        mock_backend = MagicMock()
        mock_backend.lock_source.return_value = lock_ctx_manager
        mock_backend.migrate.return_value = None

        return mock_backend

    def test_lock_source_called_with_ingest_run_uri(self, tmp_path):
        """ingest_once calls backend.lock_source("ingest-run://<hostname>")."""
        from corpus_forge.ingest import ingest_once

        mock_backend = self._build_patched_ingest(tmp_path)
        config = _make_config(tmp_path)

        host = socket.gethostname()
        expected_key = f"ingest-run://{host}"

        with (
            patch("corpus_forge.ingest.PostgresBackend", return_value=mock_backend),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
            patch("corpus_forge.ingest.socket.gethostname", return_value=host),
        ):
            ingest_once(config)

        # The first positional arg to lock_source must be the ingest-run URI
        lock_calls = mock_backend.lock_source.call_args_list
        assert len(lock_calls) >= 1
        first_call_key = lock_calls[0][0][0]
        assert first_call_key == expected_key

    def test_lock_source_called_before_migrate(self, tmp_path):
        """The advisory lock must be acquired *before* migrate() runs.

        This prevents a second process from racing to modify the schema
        while the first is still starting.
        """
        from corpus_forge.ingest import ingest_once

        call_order: list[str] = []

        @contextmanager
        def tracking_lock_ctx(*args, **kwargs):
            call_order.append("lock_acquired")
            yield
            call_order.append("lock_released")

        mock_backend = MagicMock()
        mock_backend.lock_source.side_effect = tracking_lock_ctx
        mock_backend.migrate.side_effect = lambda: call_order.append("migrate")

        config = _make_config(tmp_path)

        with (
            patch("corpus_forge.ingest.PostgresBackend", return_value=mock_backend),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
        ):
            ingest_once(config)

        assert "lock_acquired" in call_order
        assert "migrate" in call_order
        assert call_order.index("lock_acquired") < call_order.index("migrate"), (
            f"Expected lock_acquired before migrate; got order: {call_order}"
        )

    def test_lock_released_on_normal_exit(self, tmp_path):
        """Lock context manager __exit__ is called on normal return."""
        from corpus_forge.ingest import ingest_once

        exited = []

        @contextmanager
        def tracking_lock(*args, **kwargs):
            yield
            exited.append(True)

        mock_backend = MagicMock()
        mock_backend.lock_source.side_effect = tracking_lock
        config = _make_config(tmp_path)

        with (
            patch("corpus_forge.ingest.PostgresBackend", return_value=mock_backend),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
        ):
            ingest_once(config)

        assert exited, "Lock context manager __exit__ was never called"

    def test_lock_released_on_exception(self, tmp_path):
        """Lock is released (finally path) even when ingest raises an exception."""
        from corpus_forge.ingest import ingest_once

        exited = []

        @contextmanager
        def tracking_lock(*args, **kwargs):
            yield
            exited.append(True)

        mock_backend = MagicMock()
        mock_backend.lock_source.side_effect = tracking_lock
        mock_backend.migrate.side_effect = RuntimeError("migrate kaboom")
        config = _make_config(tmp_path)

        with (
            patch("corpus_forge.ingest.PostgresBackend", return_value=mock_backend),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
            pytest.raises(RuntimeError, match="migrate kaboom"),
        ):
            ingest_once(config)

        assert exited, "Lock was not released after exception in ingest_once"


# ---------------------------------------------------------------------------
# 4. Contention with wait=False (default) → exit code 75
# ---------------------------------------------------------------------------


class TestIngestOnceLockContention:
    """When the lock is already held and wait=False, ingest_once exits 75."""

    def test_second_invocation_exits_75_when_lock_held(self, tmp_path):
        """Mock the backend so lock_source raises IngestRunInProgressError.

        This simulates a concurrently running ingest on the same host.
        The call to ingest_once MUST catch the error and call sys.exit(75).
        """
        from corpus_forge.ingest import ingest_once

        mock_backend = MagicMock()
        mock_backend.lock_source.side_effect = IngestRunInProgressError(
            "another ingest run is in progress on this host"
        )
        config = _make_config(tmp_path)

        with (
            patch("corpus_forge.ingest.PostgresBackend", return_value=mock_backend),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
            pytest.raises(SystemExit) as exc_info,
        ):
            ingest_once(config)

        assert exc_info.value.code == 75, (
            f"Expected SystemExit(75) for lock contention; got code {exc_info.value.code}"
        )

    def test_contention_message_logged_to_stderr_level(self, tmp_path, caplog):
        """On contention, a WARNING-or-higher log mentioning "ingest run" is emitted."""
        import logging

        from corpus_forge.ingest import ingest_once

        mock_backend = MagicMock()
        mock_backend.lock_source.side_effect = IngestRunInProgressError(
            "another ingest run is in progress on this host"
        )
        config = _make_config(tmp_path)

        with (
            caplog.at_level(logging.WARNING),
            patch("corpus_forge.ingest.PostgresBackend", return_value=mock_backend),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
            pytest.raises(SystemExit),
        ):
            ingest_once(config)

        # At least one record must mention "ingest" or "in progress" or "lock"
        relevant = [
            r
            for r in caplog.records
            if r.levelno >= logging.WARNING
            and any(
                kw in r.getMessage().lower()
                for kw in ("ingest", "in progress", "lock", "contention", "running")
            )
        ]
        assert relevant, (
            "Expected at least one WARNING+ log record about lock contention; got:\n"
            + "\n".join(f"  [{r.levelname}] {r.getMessage()}" for r in caplog.records)
        )

    def test_contention_does_not_write_to_db(self, tmp_path):
        """On lock contention, no DB writes should occur.

        migrate() specifically must NOT run because the lock is acquired
        before migrate(), so raising before yield means migrate is skipped.
        """
        from corpus_forge.ingest import ingest_once

        mock_backend = MagicMock()
        mock_backend.lock_source.side_effect = IngestRunInProgressError(
            "another ingest run is in progress on this host"
        )
        config = _make_config(tmp_path)

        with (
            patch("corpus_forge.ingest.PostgresBackend", return_value=mock_backend),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
            pytest.raises(SystemExit),
        ):
            ingest_once(config)

        mock_backend.migrate.assert_not_called()

    def test_exit_code_is_exactly_75_not_1_or_2(self, tmp_path):
        """POSIX EX_TEMPFAIL = 75. Must not be confused with generic error codes."""
        from corpus_forge.ingest import ingest_once

        mock_backend = MagicMock()
        mock_backend.lock_source.side_effect = IngestRunInProgressError("held")
        config = _make_config(tmp_path)

        with (
            patch("corpus_forge.ingest.PostgresBackend", return_value=mock_backend),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
            pytest.raises(SystemExit) as exc_info,
        ):
            ingest_once(config)

        code = exc_info.value.code
        assert code == 75, f"Exit code MUST be 75 (EX_TEMPFAIL), got {code}"
        assert code not in (1, 2, 130), f"Exit code {code} looks like a generic/signal exit"


# ---------------------------------------------------------------------------
# 4b. Contention via __enter__ (real contextmanager path — D1 regression lock)
# ---------------------------------------------------------------------------


class TestIngestOnceLockContentionViaContextEntry:
    """Lock contention raised DURING ``with lock_ctx:`` entry (the real production path).

    D1 fix: the ``IngestRunInProgressError`` is raised inside the
    ``@contextmanager`` body before the ``yield``, which means it fires
    when Python calls ``lock_ctx.__enter__()``, NOT when the factory
    function is called.  This class exercises that path by returning a
    mock context manager whose ``__enter__`` raises.
    """

    def _make_cm_raising_on_enter(self):
        """Return a mock context manager that raises IngestRunInProgressError
        when entering the ``with`` block (``__enter__``), not when called."""
        mock_cm = MagicMock()
        mock_cm.__enter__.side_effect = IngestRunInProgressError(
            "another ingest run is in progress on this host"
        )
        mock_cm.__exit__.return_value = False
        return mock_cm

    def test_enter_contention_exits_75(self, tmp_path):
        """When __enter__ raises IngestRunInProgressError, ingest_once must exit 75."""
        from corpus_forge.ingest import ingest_once

        mock_backend = MagicMock()
        mock_backend.lock_source.return_value = self._make_cm_raising_on_enter()
        config = _make_config(tmp_path)

        with (
            patch("corpus_forge.ingest.PostgresBackend", return_value=mock_backend),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
            pytest.raises(SystemExit) as exc_info,
        ):
            ingest_once(config)

        assert exc_info.value.code == 75, (
            f"Expected SystemExit(75) for __enter__ contention; got code {exc_info.value.code}"
        )

    def test_enter_contention_does_not_call_migrate(self, tmp_path):
        """When __enter__ raises IngestRunInProgressError, migrate() must NOT be called."""
        from corpus_forge.ingest import ingest_once

        mock_backend = MagicMock()
        mock_backend.lock_source.return_value = self._make_cm_raising_on_enter()
        config = _make_config(tmp_path)

        with (
            patch("corpus_forge.ingest.PostgresBackend", return_value=mock_backend),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
            pytest.raises(SystemExit),
        ):
            ingest_once(config)

        mock_backend.migrate.assert_not_called()


# ---------------------------------------------------------------------------
# 5. Contention with wait=True → lock_source called with wait=True
# ---------------------------------------------------------------------------


class TestIngestOnceLockWait:
    """With wait=True, lock_source is forwarded wait=True to block on contention."""

    def test_wait_true_passed_to_lock_source(self, tmp_path):
        """ingest_once(config, wait=True) passes wait=True to backend.lock_source."""
        from corpus_forge.ingest import ingest_once

        @contextmanager
        def noop_lock(*args, **kwargs):
            yield

        mock_backend = MagicMock()
        mock_backend.lock_source.side_effect = noop_lock
        config = _make_config(tmp_path)

        with (
            patch("corpus_forge.ingest.PostgresBackend", return_value=mock_backend),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
        ):
            ingest_once(config, wait=True)

        lock_calls = mock_backend.lock_source.call_args_list
        assert lock_calls, "lock_source was never called"
        # The first call to lock_source (the ingest-run lock) must have wait=True
        ingest_run_call = lock_calls[0]
        kwargs = ingest_run_call[1]  # keyword args dict
        assert kwargs.get("wait") is True, (
            f"Expected wait=True in lock_source call; got kwargs={kwargs}"
        )

    def test_wait_false_default_not_forwarded_as_true(self, tmp_path):
        """Default (wait not specified or wait=False) must NOT forward wait=True."""
        from corpus_forge.ingest import ingest_once

        @contextmanager
        def noop_lock(*args, **kwargs):
            yield

        mock_backend = MagicMock()
        mock_backend.lock_source.side_effect = noop_lock
        config = _make_config(tmp_path)

        with (
            patch("corpus_forge.ingest.PostgresBackend", return_value=mock_backend),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
        ):
            ingest_once(config)  # no wait= argument

        lock_calls = mock_backend.lock_source.call_args_list
        assert lock_calls, "lock_source was never called"
        ingest_run_call = lock_calls[0]
        kwargs = ingest_run_call[1]
        assert kwargs.get("wait") is not True, (
            f"Default path should NOT pass wait=True; got kwargs={kwargs}"
        )

    def test_wait_true_blocks_until_lock_released_threading(self, tmp_path):
        """Simulate blocking wait via threading — second call waits for first to finish.

        Injects a ``lock_source`` that blocks 0.15 s then yields.  The first
        call returns immediately because no one holds it.  A second thread
        runs ingest_once(wait=True) and must complete only after the first
        context's lock is released.

        This test exercises the *contract* (that wait=True is forwarded and
        that sequential runs succeed), not the actual pg_advisory_lock blocking.
        """
        from corpus_forge.ingest import ingest_once

        # Shared state tracking order of completion
        events: list[str] = []
        barrier = threading.Event()

        @contextmanager
        def blocking_lock(*args, wait=False, **kwargs):
            if not wait:
                # Non-wait holder: take the lock, hold it, then signal after release.
                events.append("first_enter")
                yield
                events.append("first_exit")
                # Signal AFTER first_exit so the second path cannot enter until
                # this lock context has fully exited.
                barrier.set()
            else:
                # Wait path: block until the first holder has released the lock.
                barrier.wait(timeout=2.0)
                events.append("second_enter")
                yield
                events.append("second_exit")

        mock_backend = MagicMock()
        mock_backend.lock_source.side_effect = blocking_lock
        config = _make_config(tmp_path)

        patches = (
            patch("corpus_forge.ingest.PostgresBackend", return_value=mock_backend),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
        )

        with patches[0], patches[1], patches[2]:
            # First call (no wait)
            t1 = threading.Thread(target=ingest_once, args=(config,))
            # Second call (wait=True)
            t2 = threading.Thread(target=ingest_once, args=(config,), kwargs={"wait": True})

            t1.start()
            t2.start()
            t1.join(timeout=3.0)
            t2.join(timeout=3.0)

        # Both completed
        assert "first_enter" in events
        assert "second_enter" in events
        assert "first_exit" in events
        # second_enter must happen AFTER first_exit, proving the second path
        # was blocked (waiting) until the first path released the lock — not
        # merely that it ran after first_enter.
        assert events.index("second_enter") >= events.index("first_exit")


# ---------------------------------------------------------------------------
# 6. lock_source called with the ingest-run URI (not a doc URI)
# ---------------------------------------------------------------------------


class TestIngestRunLockKeyUri:
    """The URI passed to lock_source is specifically "ingest-run://<hostname>"."""

    def test_lock_uri_format_matches_contract(self, tmp_path):
        """ingest_once passes ``"ingest-run://<hostname>"`` as the lock key, not a doc URI."""
        from corpus_forge.ingest import ingest_once

        received_keys: list[str] = []

        @contextmanager
        def capturing_lock(key, *args, **kwargs):
            received_keys.append(key)
            yield

        mock_backend = MagicMock()
        mock_backend.lock_source.side_effect = capturing_lock
        config = _make_config(tmp_path)

        fake_host = "unit-test-host"
        with (
            patch("corpus_forge.ingest.PostgresBackend", return_value=mock_backend),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
            patch("corpus_forge.ingest.socket.gethostname", return_value=fake_host),
        ):
            ingest_once(config)

        # The ingest-run lock key must be the first lock acquired
        assert received_keys, "lock_source was never called"
        assert received_keys[0] == f"ingest-run://{fake_host}", (
            f"Expected first lock key 'ingest-run://{fake_host}'; got {received_keys[0]!r}"
        )

    def test_lock_key_integer_matches_advisory_lock_key(self):
        """ingest_run_lock_key(h) == advisory_lock_key('ingest-run://'+h) for all h."""
        for host in ["", "localhost", "prod-node-01", "büro"]:
            assert ingest_run_lock_key(host) == advisory_lock_key(f"ingest-run://{host}"), (
                f"Mismatch for host={host!r}"
            )


# ---------------------------------------------------------------------------
# 7. SQLite backend path — same lock contract
# ---------------------------------------------------------------------------


class TestIngestOnceLockSQLite:
    """SQLite backend path: ingest_once acquires the same ingest-run lock."""

    def _make_sqlite_config(self, tmp_path):
        from corpus_forge.config import BackendConfig, Config, DaemonConfig

        return Config(
            backend=BackendConfig(kind="sqlite", dsn=str(tmp_path / "test.db")),
            daemon=DaemonConfig(),
            datasets=[],
            embedders=[],
        )

    def test_sqlite_lock_source_called(self, tmp_path):
        """For SQLite backend, backend.lock_source is still called with ingest-run URI."""
        from corpus_forge.ingest import ingest_once

        mock_backend = MagicMock()

        @contextmanager
        def noop(*args, **kwargs):
            yield

        mock_backend.lock_source.side_effect = noop
        config = self._make_sqlite_config(tmp_path)

        with (
            patch("corpus_forge.ingest.SQLiteBackend", return_value=mock_backend),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
        ):
            ingest_once(config)

        lock_calls = mock_backend.lock_source.call_args_list
        assert lock_calls, "lock_source not called for SQLite backend"
        key = lock_calls[0][0][0]
        host = socket.gethostname()
        assert key == f"ingest-run://{host}", f"Expected 'ingest-run://{host}'; got {key!r}"

    def test_sqlite_contention_exits_75(self, tmp_path):
        """SQLite: IngestRunInProgressError from lock_source → SystemExit(75)."""
        from corpus_forge.ingest import ingest_once

        mock_backend = MagicMock()
        mock_backend.lock_source.side_effect = IngestRunInProgressError(
            "another ingest run is in progress on this host"
        )
        config = self._make_sqlite_config(tmp_path)

        with (
            patch("corpus_forge.ingest.SQLiteBackend", return_value=mock_backend),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
            pytest.raises(SystemExit) as exc_info,
        ):
            ingest_once(config)

        assert exc_info.value.code == 75


# ---------------------------------------------------------------------------
# 8. Lock logger emits structured events
# ---------------------------------------------------------------------------


class TestIngestLockLogger:
    """corpus_forge.ingest.lock logger emits JSON-serialisable records."""

    def test_contention_emits_ingest_run_contention_event(self, tmp_path, caplog):
        """On lock contention, the lock logger emits an ``ingest_run_contention`` event."""
        import json
        import logging

        from corpus_forge.ingest import ingest_once

        mock_backend = MagicMock()
        mock_backend.lock_source.side_effect = IngestRunInProgressError("held")
        config = _make_config(tmp_path)

        with (
            caplog.at_level(logging.DEBUG, logger="corpus_forge.ingest.lock"),
            patch("corpus_forge.ingest.PostgresBackend", return_value=mock_backend),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
            pytest.raises(SystemExit),
        ):
            ingest_once(config)

        lock_records = [r for r in caplog.records if r.name == "corpus_forge.ingest.lock"]
        assert lock_records, (
            "Expected at least one record from corpus_forge.ingest.lock; "
            f"got loggers: {[r.name for r in caplog.records]}"
        )
        # Each lock record must be JSON-serialisable
        for rec in lock_records:
            try:
                payload = json.loads(rec.getMessage())
            except (json.JSONDecodeError, TypeError) as exc:
                pytest.fail(
                    f"corpus_forge.ingest.lock record is not valid JSON: "
                    f"{rec.getMessage()!r} — {exc}"
                )
            assert "event" in payload, f"Lock log record missing 'event' key: {payload}"

        # At least one record must carry event=ingest_run_contention
        events = [json.loads(r.getMessage()).get("event") for r in lock_records]
        assert "ingest_run_contention" in events, (
            f"Expected ingest_run_contention event in lock logger; got: {events}"
        )

    def test_acquired_emits_ingest_run_acquired_event(self, tmp_path, caplog):
        """On successful lock acquisition, the lock logger emits ``ingest_run_acquired``."""
        import json
        import logging

        from corpus_forge.ingest import ingest_once

        @contextmanager
        def noop_lock(*args, **kwargs):
            yield

        mock_backend = MagicMock()
        mock_backend.lock_source.side_effect = noop_lock
        config = _make_config(tmp_path)

        with (
            caplog.at_level(logging.DEBUG, logger="corpus_forge.ingest.lock"),
            patch("corpus_forge.ingest.PostgresBackend", return_value=mock_backend),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
        ):
            ingest_once(config)

        lock_records = [r for r in caplog.records if r.name == "corpus_forge.ingest.lock"]
        assert lock_records, "Expected corpus_forge.ingest.lock records on successful lock"

        events = []
        for rec in lock_records:
            try:
                payload = json.loads(rec.getMessage())
                events.append(payload.get("event"))
            except (json.JSONDecodeError, TypeError):
                pytest.fail(f"Non-JSON lock log record: {rec.getMessage()!r}")

        assert "ingest_run_acquired" in events, (
            f"Expected ingest_run_acquired in lock logger events; got {events}"
        )

    def test_released_emits_ingest_run_released_event(self, tmp_path, caplog):
        """After ingest_once returns, the lock logger must have emitted ``ingest_run_released``."""
        import json
        import logging

        from corpus_forge.ingest import ingest_once

        @contextmanager
        def noop_lock(*args, **kwargs):
            yield

        mock_backend = MagicMock()
        mock_backend.lock_source.side_effect = noop_lock
        config = _make_config(tmp_path)

        with (
            caplog.at_level(logging.DEBUG, logger="corpus_forge.ingest.lock"),
            patch("corpus_forge.ingest.PostgresBackend", return_value=mock_backend),
            patch("corpus_forge.ingest.get_active_embedders", return_value=[]),
            patch("corpus_forge.ingest._plan_ingest", return_value={}),
        ):
            ingest_once(config)

        lock_records = [r for r in caplog.records if r.name == "corpus_forge.ingest.lock"]
        events = []
        for rec in lock_records:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                events.append(json.loads(rec.getMessage()).get("event"))

        assert "ingest_run_released" in events, (
            f"Expected ingest_run_released in lock logger events; got {events}"
        )
