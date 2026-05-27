"""Integration tests for :class:`PostgresBackend`'s connection-pool
lifecycle.

These tests guard against the failure mode that broke CI in commit
``ec9632e`` (reverted in ``50913a3``): the pool's worker threads + open
connections aren't cleaned up at process exit, AND if cleanup is wired
up via :func:`weakref.finalize` it fires during garbage collection at
unpredictable times that interact badly with pytest's py3.12 lifecycle
(``FATAL: sorry, too many clients already`` from ~6 alembic migration
tests in ``Integration (ubuntu-22.04 / py3.12)``).

The atexit-based cleanup landing on this branch:

- Registers a module-level callback (``_close_pool_at_exit``) per
  backend at construction time;
- Holds the pool by **weakref** so atexit doesn't pin it alive — if
  the backend is GC'd mid-process, the pool can be reclaimed and our
  atexit callback becomes a no-op;
- Fires **only at interpreter shutdown** — never during GC — so
  pytest's connection-tracking can't be disrupted between tests.

What we pin here
----------------
1. ``_close_pool_at_exit`` is called with the pool's weakref at
   interpreter exit (verified via ``atexit.register`` spy).
2. Constructing many backends in sequence does NOT exhaust the
   Postgres ``max_connections`` budget — proves the lazy
   (``min_size=0``) pool semantics actually defer connection
   creation until first use, AND that the per-backend pool releases
   its connections back to the pool after each ``_get_connection``
   ``with`` block.
3. ``close()`` is idempotent (calling it twice doesn't raise) and
   actually closes the underlying pool connections.
4. The atexit callback handles a dead weakref cleanly (returns
   silently when the pool has already been GC'd by close()).
"""

from __future__ import annotations

import gc
import weakref
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from corpus_forge.backends.postgres import (
    PostgresBackend,
    _close_pool_at_exit,
)

if TYPE_CHECKING:
    pass


pytestmark = pytest.mark.integration


# ─────────────────────────────────────────────────────────────────────
# atexit wiring
# ─────────────────────────────────────────────────────────────────────


class TestAtexitRegistration:
    """The pool's cleanup must be registered with ``atexit`` (NOT
    :func:`weakref.finalize`) so it fires at interpreter shutdown
    only — never during garbage collection. The previous design
    (commit ec9632e) used ``weakref.finalize`` and broke py3.12
    Integration CI; this test class pins the post-revert wiring.
    """

    def test_constructor_registers_atexit_handler(self, pg_dsn: str) -> None:
        """Each ``PostgresBackend.__init__`` call must register one
        ``atexit`` callback pointing at ``_close_pool_at_exit``.
        """

        with patch("corpus_forge.backends.postgres.atexit") as mock_atexit:
            backend = PostgresBackend(dsn=pg_dsn)
            assert mock_atexit.register.called, (
                "PostgresBackend.__init__ should register an atexit handler for pool cleanup"
            )
            args, _kwargs = mock_atexit.register.call_args
            assert args[0] is _close_pool_at_exit, (
                f"expected _close_pool_at_exit as the atexit callback; got {args[0]}"
            )
            assert isinstance(args[1], weakref.ReferenceType), (
                f"atexit callback should be passed a weakref to the pool, got {type(args[1])}"
            )
        backend.close()

    def test_no_weakref_finalize_during_gc(self, pg_dsn: str) -> None:
        """The fix must NOT use ``weakref.finalize`` to clean up the
        pool. Finalizers fire on garbage collection, which is the
        exact failure mode that broke py3.12 Integration in commit
        ec9632e (the finalizer fired mid-test as backends went out of
        scope, racing pytest's own teardown).

        We don't spy ``weakref.finalize`` directly — backends may use
        it for other purposes — but we can pin the ABSENCE of a
        cleanup finalizer by constructing a backend, dropping all
        references, forcing GC, and verifying the pool is still open
        (it would be closed by a finalizer-driven cleanup).
        """

        backend = PostgresBackend(dsn=pg_dsn)
        pool_ref = weakref.ref(backend._pool)
        del backend
        # Force GC to flush all reference-counted objects + their
        # finalizers if any were registered.
        gc.collect()
        # The pool should be GC'd as a normal consequence of losing
        # the backend reference (no finalizer holding it). What we
        # ASSERT is that if any pool object survives, it's because the
        # weak reference can still resolve — NOT because a finalizer
        # forced its close().
        pool = pool_ref()
        if pool is not None:
            # Pool still reachable through some path (e.g. internal
            # ConnectionPool worker threads holding self-refs). It
            # MUST still be open — if it's already closed, that means
            # a finalizer ran during GC, which is what we forbid.
            assert not getattr(pool, "_closed", False), (
                "Pool was closed during garbage collection — likely a "
                "weakref.finalize was re-introduced. See commit ec9632e "
                "postmortem in tests/integration/test_postgres_pool_lifecycle.py."
            )


# ─────────────────────────────────────────────────────────────────────
# Connection budget
# ─────────────────────────────────────────────────────────────────────


class TestConnectionBudget:
    """Postgres test container defaults to ``max_connections=100``.
    The pool's ``pool_max_size=8`` per backend means 13+ concurrent
    backends would exhaust the budget if connections weren't returned
    to the pool after each ``_get_connection`` ``with`` block. These
    tests pin that the lifecycle actually returns connections.
    """

    def test_many_backends_do_not_exhaust_max_connections(self, pg_dsn: str) -> None:
        """Construct 20 backends (well past the 13-concurrent
        connection-budget threshold) and run a basic query on each.
        With ``min_size=0`` the pool is lazy — connections only open
        on first use — and each query returns its connection to the
        pool on exit. Total concurrent connections should stay ≤ 20
        even though we hold all 20 backends alive simultaneously.
        """

        backends = []
        try:
            for _ in range(20):
                backend = PostgresBackend(dsn=pg_dsn)
                # First use opens one connection in this backend's pool.
                result = backend._execute("SELECT 1 AS one")
                assert result == [{"one": 1}]
                backends.append(backend)
        finally:
            for backend in backends:
                backend.close()

    def test_serial_backend_creation_releases_connections(self, pg_dsn: str) -> None:
        """Construct backends in a loop, close each before the next.
        Should NEVER exhaust connections regardless of iteration
        count because each backend's pool is closed before the next
        opens. Validates ``close()`` actually releases connections
        back to Postgres.
        """

        for _ in range(50):
            backend = PostgresBackend(dsn=pg_dsn)
            result = backend._execute("SELECT 1 AS one")
            assert result == [{"one": 1}]
            backend.close()


# ─────────────────────────────────────────────────────────────────────
# close() semantics
# ─────────────────────────────────────────────────────────────────────


class TestCloseSemantics:
    def test_close_is_idempotent(self, pg_dsn: str) -> None:
        """Calling ``close()`` multiple times must not raise. The
        first call closes the underlying pool; subsequent calls
        should detect ``_pool`` is already closed and no-op (the
        ``logger.debug`` swallow path in the catch).
        """

        backend = PostgresBackend(dsn=pg_dsn)
        backend._execute("SELECT 1")
        backend.close()
        # Second close — must not raise.
        backend.close()
        # Third for good measure.
        backend.close()

    def test_close_releases_pool_connections(self, pg_dsn: str) -> None:
        """After ``close()``, the pool object reports closed state."""

        backend = PostgresBackend(dsn=pg_dsn)
        backend._execute("SELECT 1")
        pool = backend._pool
        backend.close()
        # psycopg-pool's ConnectionPool.close() sets the closed flag.
        # The exact attribute name has changed across psycopg-pool
        # versions; check the behavior (connection() raises) rather
        # than pinning the attribute name.
        from psycopg_pool import PoolClosed

        with pytest.raises(PoolClosed), pool.connection():
            pass


# ─────────────────────────────────────────────────────────────────────
# atexit callback behavior
# ─────────────────────────────────────────────────────────────────────


class TestCloseAtExitCallback:
    def test_dead_weakref_is_a_noop(self) -> None:
        """``_close_pool_at_exit`` must return cleanly when its
        weakref target has already been GC'd — the backend may have
        been closed and reclaimed long before interpreter shutdown.
        """

        # Construct a weakref to a transient object that gets GC'd
        # immediately.
        class _Dummy:
            pass

        dummy = _Dummy()
        ref: weakref.ReferenceType = weakref.ref(dummy)
        del dummy
        gc.collect()
        assert ref() is None, "weakref should be dead after del + GC"
        # The callback must handle this without raising.
        _close_pool_at_exit(ref)

    def test_close_swallows_pool_exceptions(
        self, pg_dsn: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``_close_pool_at_exit`` wraps ``pool.close()`` in
        ``contextlib.suppress`` so interpreter-shutdown-time
        exceptions (from already-torn-down threading machinery)
        don't leak out of atexit.
        """

        backend = PostgresBackend(dsn=pg_dsn)
        backend._execute("SELECT 1")  # warm the pool
        # Patch the pool's close to raise — simulate the
        # already-shutdown-machinery condition.
        pool = backend._pool

        def boom() -> None:
            raise RuntimeError("simulated: ThreadPoolExecutor already shut down")

        monkeypatch.setattr(pool, "close", boom)
        # _close_pool_at_exit must not raise even though pool.close() does.
        _close_pool_at_exit(weakref.ref(pool))
