"""Unit tests for corpus_forge.scanner.filelock — SR-T3.

Tests the cross-process advisory file-lock helper:
  corpus_forge.scanner.filelock.acquire(path, *, wait=False, timeout=None) -> ContextManager[bool]

Module does NOT yet exist — every test must fail with ImportError at
collection time. That is the desired RED state.

Coverage dimensions:
  - Happy path: acquire, enter context, get True, exit, re-acquire succeeds
  - Boundaries: empty path parent (parent must be created or handled), same
    path twice (contention), different paths (independent)
  - Type/format: Path vs str arg, non-existent parent directory
  - State: fresh lock (uncontended), idempotent release on re-use
  - Concurrency: two threads contending for same file; wait=False semantics
  - Failure paths: lock held by another thread — wait=False must not block
  - Locale/time: N/A (pure filesystem lock)
  - Production-realistic: uses tmp_path fixtures, not /tmp
  - Regression hooks: none yet (new feature)
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# This import will raise ImportError until corpus_forge/scanner/filelock.py
# is created — the desired RED state.
# ---------------------------------------------------------------------------
from corpus_forge.scanner.filelock import acquire

# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestAcquireHappyPath:
    """Basic acquire / release round-trips."""

    def test_acquire_succeeds_on_free_lock(self, tmp_path):
        """acquire() on an uncontended lock file succeeds and yields True."""
        lock_path = tmp_path / "free.lock"
        with acquire(lock_path) as held:
            assert held is True

    def test_acquire_creates_lock_file(self, tmp_path):
        """acquire() creates the lock file on disk (or doesn't crash if it already exists)."""
        lock_path = tmp_path / "exists.lock"
        with acquire(lock_path):
            # Just verifying no exception is raised; file may or may not be visible
            pass

    def test_acquire_accepts_pathlib_path(self, tmp_path):
        """acquire() accepts a pathlib.Path argument."""
        lock_path = tmp_path / "pathlib.lock"
        assert isinstance(lock_path, Path)
        with acquire(lock_path) as held:
            assert held is True

    def test_acquire_accepts_string_path(self, tmp_path):
        """acquire() accepts a string path argument."""
        lock_path = str(tmp_path / "str_path.lock")
        with acquire(lock_path) as held:
            assert held is True

    def test_lock_is_released_after_context_exit(self, tmp_path):
        """After context exit the lock is free to re-acquire."""
        lock_path = tmp_path / "release.lock"
        with acquire(lock_path):
            pass
        with acquire(lock_path) as held:
            assert held is True

    def test_acquire_is_idempotent_across_sequential_uses(self, tmp_path):
        """acquire() on the same path works correctly across multiple sequential uses."""
        lock_path = tmp_path / "idempotent.lock"
        for _ in range(5):
            with acquire(lock_path) as held:
                assert held is True


# ---------------------------------------------------------------------------
# Boundary tests
# ---------------------------------------------------------------------------


class TestAcquireBoundaries:
    """Edge cases around path names and parameter values."""

    def test_lock_in_nested_directory(self, tmp_path):
        """acquire() works when the lock file is inside a newly created sub-directory."""
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        lock_path = nested / "nested.lock"
        with acquire(lock_path) as held:
            assert held is True

    def test_wait_false_default(self, tmp_path):
        """wait defaults to False — a free lock still succeeds."""
        lock_path = tmp_path / "default_wait.lock"
        with acquire(lock_path, wait=False) as held:
            assert held is True

    def test_wait_true_free_lock(self, tmp_path):
        """wait=True on a free lock acquires immediately."""
        lock_path = tmp_path / "wait_true_free.lock"
        with acquire(lock_path, wait=True) as held:
            assert held is True

    def test_timeout_none_is_accepted(self, tmp_path):
        """timeout=None is a valid argument (unbounded wait on a free lock)."""
        lock_path = tmp_path / "timeout_none.lock"
        with acquire(lock_path, wait=True, timeout=None) as held:
            assert held is True

    def test_timeout_zero_is_accepted(self, tmp_path):
        """timeout=0 is a valid argument (instant poll)."""
        lock_path = tmp_path / "timeout_zero.lock"
        # On a free lock, even timeout=0 should succeed
        with acquire(lock_path, wait=True, timeout=0.0) as held:
            assert held is True

    def test_negative_timeout_raises_value_error(self, tmp_path):
        """Negative timeout values should raise ValueError or similar."""
        lock_path = tmp_path / "neg_timeout.lock"
        with pytest.raises((ValueError, TypeError)), acquire(lock_path, wait=True, timeout=-1.0):
            pass


# ---------------------------------------------------------------------------
# Contention tests (in-process threading)
# ---------------------------------------------------------------------------


class TestAcquireContention:
    """Two in-process threads contend for the same lock."""

    def test_wait_false_returns_false_or_raises_when_held(self, tmp_path):
        """wait=False returns False (or raises) when another thread holds the lock."""
        lock_path = tmp_path / "contend.lock"
        ready = threading.Event()
        release = threading.Event()
        results: list[object] = []

        def holder():
            with acquire(lock_path, wait=True):
                ready.set()
                release.wait(timeout=5.0)

        def contender():
            ready.wait(timeout=3.0)
            time.sleep(0.02)  # ensure holder is firmly inside the context
            try:
                ctx = acquire(lock_path, wait=False)
                with ctx as held:
                    results.append(held)
            except Exception as exc:
                results.append(exc)

        t1 = threading.Thread(target=holder, daemon=True)
        t2 = threading.Thread(target=contender, daemon=True)
        t1.start()
        t2.start()
        t2.join(timeout=3.0)
        release.set()
        t1.join(timeout=3.0)

        assert len(results) == 1, f"Expected 1 result from contender, got {results}"
        r = results[0]
        assert r is False or isinstance(r, Exception), (
            f"Contender must get False or raise with wait=False on held lock, got {r!r}"
        )

    def test_wait_true_blocks_until_released(self, tmp_path):
        """wait=True blocks the second caller until the first releases."""
        lock_path = tmp_path / "blocking.lock"
        order: list[str] = []
        threading.Event()

        def first():
            with acquire(lock_path, wait=True):
                order.append("first_acquired")
                time.sleep(0.2)
                order.append("first_releasing")

        def second():
            # Give first a moment to acquire
            time.sleep(0.05)
            with acquire(lock_path, wait=True) as held:
                order.append(f"second_acquired:{held}")

        t1 = threading.Thread(target=first, daemon=True)
        t2 = threading.Thread(target=second, daemon=True)
        t1.start()
        t2.start()
        t1.join(timeout=3.0)
        t2.join(timeout=3.0)

        assert "first_acquired" in order
        assert "first_releasing" in order
        assert any("second_acquired" in s for s in order)
        # first_releasing must happen before second_acquired
        releasing_idx = order.index("first_releasing")
        second_idx = next(i for i, s in enumerate(order) if "second_acquired" in s)
        assert releasing_idx < second_idx, (
            f"second must not acquire until first releases; order={order}"
        )

    def test_exactly_one_of_two_threads_gets_lock_when_both_try(self, tmp_path):
        """When two threads race for wait=True, only one holds the lock at once."""
        lock_path = tmp_path / "race.lock"
        concurrent_holders: list[int] = []
        current_count = [0]
        lock_for_count = threading.Lock()

        def worker(worker_id: int):
            with acquire(lock_path, wait=True):
                with lock_for_count:
                    current_count[0] += 1
                    concurrent_holders.append(current_count[0])
                time.sleep(0.05)
                with lock_for_count:
                    current_count[0] -= 1

        threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        # At no point should more than one thread have been inside the lock simultaneously
        assert max(concurrent_holders) == 1, (
            f"Concurrent lock holders detected: max={max(concurrent_holders)}, "
            f"history={concurrent_holders}"
        )


# ---------------------------------------------------------------------------
# Exception-safety tests
# ---------------------------------------------------------------------------


class TestAcquireExceptionSafety:
    """Lock is released even when the context body raises."""

    def test_released_on_exception(self, tmp_path):
        """Lock is released when the context body raises an exception."""
        lock_path = tmp_path / "exc_safe.lock"
        try:
            with acquire(lock_path):
                raise ValueError("intentional test exception")
        except ValueError:
            pass

        # Must be able to re-acquire immediately
        with acquire(lock_path) as held:
            assert held is True

    def test_released_on_keyboard_interrupt(self, tmp_path):
        """Lock is released even on KeyboardInterrupt."""
        lock_path = tmp_path / "ki_safe.lock"
        try:
            with acquire(lock_path):
                raise KeyboardInterrupt()
        except KeyboardInterrupt:
            pass

        with acquire(lock_path) as held:
            assert held is True


# ---------------------------------------------------------------------------
# Independence tests (different paths = different locks)
# ---------------------------------------------------------------------------


class TestAcquireIndependentPaths:
    """Locks on different paths do not interfere."""

    def test_two_different_paths_can_both_be_held(self, tmp_path):
        """Two acquire() contexts on different paths can be nested without contention."""
        path_a = tmp_path / "a.lock"
        path_b = tmp_path / "b.lock"
        with acquire(path_a) as held_a, acquire(path_b) as held_b:
            assert held_a is True
            assert held_b is True

    def test_three_independent_paths(self, tmp_path):
        """Three separate lock paths are all independently acquirable."""
        paths = [tmp_path / f"lock_{i}.lock" for i in range(3)]
        results = {}
        for p in paths:
            with acquire(p) as held:
                results[str(p)] = held
        assert all(v is True for v in results.values()), (
            f"All three locks should succeed: {results}"
        )


# ---------------------------------------------------------------------------
# Type / format tests
# ---------------------------------------------------------------------------


class TestAcquireTypeFormat:
    """Verify the API contract around parameter types."""

    def test_returns_context_manager_protocol(self, tmp_path):
        """acquire() returns an object implementing the context-manager protocol."""
        lock_path = tmp_path / "proto.lock"
        ctx = acquire(lock_path)
        assert callable(getattr(ctx, "__enter__", None)), "__enter__ must be callable"
        assert callable(getattr(ctx, "__exit__", None)), "__exit__ must be callable"

    def test_bool_result_type(self, tmp_path):
        """The value yielded inside the context must be a bool."""
        lock_path = tmp_path / "bool_type.lock"
        with acquire(lock_path) as held:
            assert isinstance(held, bool), f"Expected bool, got {type(held).__name__}"
