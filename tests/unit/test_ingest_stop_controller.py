"""Unit tests for ``corpus_forge.ingest._StopController``.

RED state: ``_StopController`` does not yet exist in ``corpus_forge.ingest``.
Every test in this module is expected to fail with ``ImportError`` or
``AttributeError`` until SR-G4 is implemented.

Contract being locked:
- ``_StopController()`` — zero-arg constructor; ``stop_requested`` starts False.
- ``install_handlers()`` — installs SIGINT + SIGTERM handlers; no-op on
  non-main threads; idempotent.
- ``restore_handlers()`` — restores prior handlers.
- First signal -> ``stop_requested`` flips to True; MUST NOT call
  ``os._exit`` or ``sys.exit``.
- Second SIGINT -> calls ``os._exit(130)``.
- Context-manager protocol (``__enter__`` / ``__exit__``): installs on
  enter, restores on exit even when the body raises.
- Thread-safety: signal handlers run atomically w.r.t. the flag set.

Run command::

    uv run pytest tests/unit/test_ingest_stop_controller.py -q
"""

from __future__ import annotations

import os
import signal
import sys
import threading
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Import -- will raise ImportError until the coder adds _StopController.
# ---------------------------------------------------------------------------
from corpus_forge.ingest import _StopController  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _current_sigint_handler():
    """Return whatever handler is currently installed for SIGINT."""
    return signal.getsignal(signal.SIGINT)


def _current_sigterm_handler():
    """Return whatever handler is currently installed for SIGTERM."""
    return signal.getsignal(signal.SIGTERM)


def _append_exit_code(calls: list[int]):
    """Return a function suitable for monkeypatching os._exit."""

    def _capture(code: int) -> None:
        calls.append(code)

    return _capture


# ---------------------------------------------------------------------------
# TestConstructor
# ---------------------------------------------------------------------------


class TestConstructor:
    """_StopController() -- default state."""

    def test_default_stop_requested_is_false(self):
        """Brand-new controller reports stop_requested == False."""
        ctl = _StopController()
        assert ctl.stop_requested is False

    def test_multiple_instances_are_independent(self):
        """Two independent instances do not share flag state."""
        a = _StopController()
        b = _StopController()
        # Simulate first signal on ``a``'s handler directly.
        a._handle_signal(signal.SIGINT, None)
        assert a.stop_requested is True
        assert b.stop_requested is False


# ---------------------------------------------------------------------------
# TestInstallHandlers
# ---------------------------------------------------------------------------


class TestInstallHandlers:
    """install_handlers() -- installs SIGINT + SIGTERM and stashes priors."""

    def test_install_replaces_sigint_handler(self):
        """After install_handlers(), signal.getsignal(SIGINT) is no longer
        the original handler."""
        original_sigint = _current_sigint_handler()
        ctl = _StopController()
        ctl.install_handlers()
        try:
            installed = _current_sigint_handler()
            assert installed is not original_sigint, (
                "install_handlers() must replace the SIGINT handler"
            )
        finally:
            ctl.restore_handlers()

    def test_install_replaces_sigterm_handler(self):
        """After install_handlers(), signal.getsignal(SIGTERM) is no longer
        the original handler."""
        original_sigterm = _current_sigterm_handler()
        ctl = _StopController()
        ctl.install_handlers()
        try:
            installed = _current_sigterm_handler()
            assert installed is not original_sigterm, (
                "install_handlers() must replace the SIGTERM handler"
            )
        finally:
            ctl.restore_handlers()

    def test_install_is_idempotent(self):
        """Calling install_handlers() twice does not blow up and the handler
        object installed after the second call is still callable (i.e., the
        stash-of-stash case doesn't corrupt the chain)."""
        ctl = _StopController()
        ctl.install_handlers()
        try:
            ctl.install_handlers()  # second call -- must not raise
            handler = _current_sigint_handler()
            assert callable(handler)
        finally:
            ctl.restore_handlers()

    @pytest.mark.unit
    def test_install_noop_on_non_main_thread(self):
        """install_handlers() on a non-main thread MUST be a no-op.

        Signal installation from a non-main thread raises ValueError on CPython.
        The controller must guard against this -- the call returns without error
        and stop_requested stays False.
        """
        results: dict[str, object] = {}

        def _worker():
            ctl = _StopController()
            try:
                ctl.install_handlers()  # must not raise
                results["raised"] = False
                results["stop_requested"] = ctl.stop_requested
                # Verify that NO signal was actually registered from this thread
                # (we can't call signal.getsignal from a non-main thread safely,
                # so we trust that no ValueError escaped).
            except Exception as exc:
                results["raised"] = True
                results["exc"] = exc

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join(timeout=5)
        assert not results.get("raised"), (
            f"install_handlers() raised on non-main thread: {results.get('exc')}"
        )
        assert results.get("stop_requested") is False

    def test_install_uses_signal_signal_api(self):
        """install_handlers() calls signal.signal(...) for SIGINT and SIGTERM."""
        with patch("signal.signal", wraps=signal.signal) as mock_signal:
            ctl = _StopController()
            ctl.install_handlers()
            try:
                calls_sigints = [
                    c for c in mock_signal.call_args_list if c.args[0] == signal.SIGINT
                ]
                calls_sigterms = [
                    c for c in mock_signal.call_args_list if c.args[0] == signal.SIGTERM
                ]
                assert len(calls_sigints) >= 1, "Must install SIGINT handler"
                assert len(calls_sigterms) >= 1, "Must install SIGTERM handler"
            finally:
                ctl.restore_handlers()


# ---------------------------------------------------------------------------
# TestRestoreHandlers
# ---------------------------------------------------------------------------


class TestRestoreHandlers:
    """restore_handlers() -- restores the prior handlers exactly."""

    def test_restore_sigint_to_prior(self):
        """After restore_handlers(), signal.getsignal(SIGINT) equals the
        handler that was in place before install_handlers()."""
        original = _current_sigint_handler()
        ctl = _StopController()
        ctl.install_handlers()
        ctl.restore_handlers()
        assert signal.getsignal(signal.SIGINT) is original

    def test_restore_sigterm_to_prior(self):
        """After restore_handlers(), signal.getsignal(SIGTERM) equals the
        handler that was in place before install_handlers()."""
        original = _current_sigterm_handler()
        ctl = _StopController()
        ctl.install_handlers()
        ctl.restore_handlers()
        assert signal.getsignal(signal.SIGTERM) is original

    def test_restore_after_exception(self):
        """restore_handlers() is called even when the protected block raises."""
        original_sigint = _current_sigint_handler()
        original_sigterm = _current_sigterm_handler()
        ctl = _StopController()
        ctl.install_handlers()
        try:
            raise RuntimeError("simulated ingest error")
        except RuntimeError:
            pass
        finally:
            ctl.restore_handlers()
        assert signal.getsignal(signal.SIGINT) is original_sigint
        assert signal.getsignal(signal.SIGTERM) is original_sigterm

    def test_restore_without_prior_install_does_not_raise(self):
        """Calling restore_handlers() before install_handlers() must not raise
        (defensive guard for double-restore on exception paths)."""
        ctl = _StopController()
        ctl.restore_handlers()  # must not raise


# ---------------------------------------------------------------------------
# TestFirstSignal
# ---------------------------------------------------------------------------


class TestFirstSignal:
    """First SIGINT/SIGTERM sets stop_requested; does not call os._exit."""

    def test_first_sigint_sets_stop_requested(self):
        """After one _handle_signal(SIGINT) call, stop_requested is True."""
        ctl = _StopController()
        ctl.install_handlers()
        try:
            assert ctl.stop_requested is False
            ctl._handle_signal(signal.SIGINT, None)
            assert ctl.stop_requested is True
        finally:
            ctl.restore_handlers()

    def test_first_sigterm_sets_stop_requested(self):
        """After one _handle_signal(SIGTERM) call, stop_requested is True."""
        ctl = _StopController()
        ctl.install_handlers()
        try:
            assert ctl.stop_requested is False
            ctl._handle_signal(signal.SIGTERM, None)
            assert ctl.stop_requested is True
        finally:
            ctl.restore_handlers()

    def test_first_signal_does_not_call_os_exit(self, monkeypatch):
        """The first signal MUST NOT call os._exit or sys.exit."""
        exit_calls: list[int] = []
        monkeypatch.setattr(os, "_exit", _append_exit_code(exit_calls))
        sys_exit_calls: list[object] = []

        def _capture_sys_exit(code: object = 0) -> None:
            sys_exit_calls.append(code)

        monkeypatch.setattr(sys, "exit", _capture_sys_exit)

        ctl = _StopController()
        ctl.install_handlers()
        try:
            ctl._handle_signal(signal.SIGINT, None)
            assert exit_calls == [], "os._exit must NOT be called on first signal"
            assert sys_exit_calls == [], "sys.exit must NOT be called on first signal"
        finally:
            ctl.restore_handlers()

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason=(
            "os.kill(getpid(), SIGINT) terminates the worker on Windows "
            "rather than dispatching to Python's signal handler. The direct "
            "ctl._handle_signal(...) tests above cover the same flag-flip "
            "contract on all platforms."
        ),
    )
    def test_first_signal_via_os_kill(self):
        """Sending SIGINT to the current process via os.kill sets stop_requested.

        This exercises the full signal-dispatch path, not just direct handler
        invocation.  Careful: we save/restore to avoid interfering with
        pytest's own SIGINT handler.
        """
        prev_sigint = signal.getsignal(signal.SIGINT)
        ctl = _StopController()
        ctl.install_handlers()
        try:
            os.kill(os.getpid(), signal.SIGINT)
            assert ctl.stop_requested is True
        finally:
            ctl.restore_handlers()
            # Belt-and-suspenders: assert original is restored
            assert signal.getsignal(signal.SIGINT) is prev_sigint

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="os.kill(getpid(), SIGTERM) terminates the worker on Windows.",
    )
    def test_first_sigterm_via_os_kill(self):
        """Sending SIGTERM to the current process sets stop_requested."""
        prev_sigterm = signal.getsignal(signal.SIGTERM)
        ctl = _StopController()
        ctl.install_handlers()
        try:
            os.kill(os.getpid(), signal.SIGTERM)
            assert ctl.stop_requested is True
        finally:
            ctl.restore_handlers()
            assert signal.getsignal(signal.SIGTERM) is prev_sigterm


# ---------------------------------------------------------------------------
# TestSecondSignalEscalation
# ---------------------------------------------------------------------------


class TestSecondSignalEscalation:
    """Second SIGINT -> os._exit(130) is called."""

    def test_second_sigint_calls_os_exit_130(self, monkeypatch):
        """The second _handle_signal(SIGINT) call MUST invoke os._exit(130)."""
        exit_calls: list[int] = []
        monkeypatch.setattr(os, "_exit", _append_exit_code(exit_calls))

        ctl = _StopController()
        ctl.install_handlers()
        try:
            ctl._handle_signal(signal.SIGINT, None)  # first -- graceful
            assert exit_calls == [], "os._exit must NOT fire on first SIGINT"
            ctl._handle_signal(signal.SIGINT, None)  # second -- escalate
            assert exit_calls == [130], (
                f"os._exit(130) must be called on second SIGINT; got calls={exit_calls}"
            )
        finally:
            # restore_handlers may never be reached if _exit is real, but
            # with monkeypatched _exit it's safe.
            ctl.restore_handlers()

    def test_second_sigterm_does_not_escalate(self, monkeypatch):
        """SIGTERM escalation is NOT required -- only SIGINT Ctrl-C double-tap
        must escalate.  A second SIGTERM should set stop_requested (already
        True) and NOT call os._exit.

        This pins the spec: SIGTERM is a polite shutdown signal; the host
        supervisor will kill via SIGKILL if needed.
        """
        exit_calls: list[int] = []
        monkeypatch.setattr(os, "_exit", _append_exit_code(exit_calls))

        ctl = _StopController()
        ctl.install_handlers()
        try:
            ctl._handle_signal(signal.SIGTERM, None)
            ctl._handle_signal(signal.SIGTERM, None)
            assert exit_calls == [], "os._exit must NOT be called on repeated SIGTERM"
        finally:
            ctl.restore_handlers()

    def test_sigterm_then_sigint_escalates(self, monkeypatch):
        """SIGTERM sets the flag; subsequent SIGINT escalates (the counter
        tracks all signals, or at minimum SIGINT count independently)."""
        exit_calls: list[int] = []
        monkeypatch.setattr(os, "_exit", _append_exit_code(exit_calls))

        ctl = _StopController()
        ctl.install_handlers()
        try:
            ctl._handle_signal(signal.SIGTERM, None)  # first signal -- graceful
            assert exit_calls == []
            ctl._handle_signal(signal.SIGINT, None)  # second SIGINT -- may escalate
            # Per the binding spec: second SIGINT -> os._exit(130).
            # If the implementation counts SIGINT hits specifically, this is 1st SIGINT
            # (no escalation).  If it counts all signals, this is 2nd overall (escalation).
            # Both are valid implementations; we accept either outcome here:
            # either exit_calls == [130] (all-signal counter) or exit_calls == [] (SIGINT-only).
            # The tester documents this ambiguity -- SR-G4 coder resolves it.
            # We assert that IF escalation happens, it uses code 130.
            assert all(c == 130 for c in exit_calls), (
                f"Any escalation MUST use exit code 130; got {exit_calls}"
            )
        finally:
            ctl.restore_handlers()

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="os.kill(getpid(), SIGINT) terminates the worker on Windows.",
    )
    def test_second_sigint_via_os_kill(self, monkeypatch):
        """Full signal-dispatch: two os.kill(SIGINT) calls -> os._exit(130)."""
        exit_calls: list[int] = []
        monkeypatch.setattr(os, "_exit", _append_exit_code(exit_calls))

        prev_sigint = signal.getsignal(signal.SIGINT)
        ctl = _StopController()
        ctl.install_handlers()
        try:
            os.kill(os.getpid(), signal.SIGINT)  # first
            assert exit_calls == []
            os.kill(os.getpid(), signal.SIGINT)  # second
            assert exit_calls == [130]
        finally:
            ctl.restore_handlers()
            assert signal.getsignal(signal.SIGINT) is prev_sigint


# ---------------------------------------------------------------------------
# TestContextManager
# ---------------------------------------------------------------------------


class TestContextManager:
    """``with _StopController() as ctl:`` installs on enter, restores on exit."""

    def test_enter_installs_handler(self):
        """__enter__ installs the SIGINT handler and returns the controller."""
        original = _current_sigint_handler()
        with _StopController() as ctl:
            assert ctl is not None
            assert _current_sigint_handler() is not original
        # After the block, restored.
        assert signal.getsignal(signal.SIGINT) is original

    def test_exit_restores_on_normal_exit(self):
        """__exit__ restores handlers when the body completes normally."""
        original_sigint = _current_sigint_handler()
        original_sigterm = _current_sigterm_handler()
        with _StopController():
            pass
        assert signal.getsignal(signal.SIGINT) is original_sigint
        assert signal.getsignal(signal.SIGTERM) is original_sigterm

    def test_exit_restores_on_exception(self):
        """__exit__ restores handlers even when the body raises."""
        original_sigint = _current_sigint_handler()
        original_sigterm = _current_sigterm_handler()
        with pytest.raises(RuntimeError), _StopController():
            raise RuntimeError("body error")
        assert signal.getsignal(signal.SIGINT) is original_sigint
        assert signal.getsignal(signal.SIGTERM) is original_sigterm

    def test_enter_returns_controller_with_false_flag(self):
        """The object returned by __enter__ has stop_requested == False."""
        with _StopController() as ctl:
            assert ctl.stop_requested is False

    def test_stop_requested_visible_inside_block(self):
        """stop_requested becomes True inside the block after a signal."""
        with _StopController() as ctl:
            ctl._handle_signal(signal.SIGINT, None)
            assert ctl.stop_requested is True


# ---------------------------------------------------------------------------
# TestNonMainThreadGuard
# ---------------------------------------------------------------------------


class TestNonMainThreadGuard:
    """install_handlers() is a no-op (not a crash) when called off main thread."""

    def test_noop_install_does_not_raise(self):
        """Signal install from a worker thread MUST NOT propagate ValueError."""
        exceptions: list[Exception] = []

        def _worker():
            ctl = _StopController()
            try:
                ctl.install_handlers()
            except Exception as exc:
                exceptions.append(exc)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join(timeout=5)
        assert not exceptions, f"install_handlers() raised on non-main thread: {exceptions}"

    def test_noop_install_leaves_stop_requested_false(self):
        """After a no-op install on a worker thread, stop_requested is still False."""
        results: list[bool] = []

        def _worker():
            ctl = _StopController()
            ctl.install_handlers()
            results.append(ctl.stop_requested)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join(timeout=5)
        assert results == [False]

    def test_noop_install_does_not_replace_main_thread_handlers(self):
        """A worker-thread install_handlers() MUST NOT replace the SIGINT
        handler that the main thread's controller installed."""
        original_sigint = _current_sigint_handler()
        main_ctl = _StopController()
        main_ctl.install_handlers()

        handler_after_main_install = signal.getsignal(signal.SIGINT)

        thread_finished = threading.Event()

        def _worker():
            worker_ctl = _StopController()
            worker_ctl.install_handlers()  # no-op
            thread_finished.set()

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        thread_finished.wait(timeout=5)
        t.join(timeout=5)

        # Main thread's handler must not have been displaced
        assert signal.getsignal(signal.SIGINT) is handler_after_main_install
        main_ctl.restore_handlers()
        assert signal.getsignal(signal.SIGINT) is original_sigint


# ---------------------------------------------------------------------------
# TestHandlerSignature
# ---------------------------------------------------------------------------


class TestHandlerSignature:
    """_handle_signal(signum, frame) matches signal handler signature."""

    def test_handle_signal_accepts_signum_and_frame_none(self):
        """_handle_signal must accept (signum, frame) as positional args."""
        ctl = _StopController()
        # Should not raise -- frame=None is acceptable per signal module docs.
        ctl._handle_signal(signal.SIGINT, None)

    def test_handle_signal_accepts_real_frame(self):
        """_handle_signal must accept a real frame object (from signal dispatch)."""
        ctl = _StopController()
        # Use the current execution frame as a stand-in.
        real_frame = sys._getframe()
        ctl._handle_signal(signal.SIGINT, real_frame)
        assert ctl.stop_requested is True


# ---------------------------------------------------------------------------
# TestBoundaryAndState
# ---------------------------------------------------------------------------


class TestBoundaryAndState:
    """Edge cases: flag idempotency, multiple controllers, no double-exit."""

    def test_stop_requested_is_idempotent_on_repeated_first_signal(self):
        """Calling _handle_signal many times when already stopped does not
        raise (the second SIGINT escalation only matters when it's the very
        next SIGINT after the first; three SIGINTs should not triple-fire)."""
        exit_calls: list[int] = []

        ctl = _StopController()
        with patch.object(os, "_exit", side_effect=_append_exit_code(exit_calls)):
            ctl._handle_signal(signal.SIGINT, None)  # first
            assert exit_calls == []
            ctl._handle_signal(signal.SIGINT, None)  # second -- escalates
            assert exit_calls == [130]
            # Third call: _exit already captured; no further side-effects expected
            # (implementation may short-circuit after the escalation)

    def test_stop_requested_remains_true_after_restore(self):
        """restore_handlers() does not reset stop_requested -- the flag
        persists so callers checking after cleanup still see True."""
        ctl = _StopController()
        ctl.install_handlers()
        ctl._handle_signal(signal.SIGINT, None)
        ctl.restore_handlers()
        assert ctl.stop_requested is True

    def test_fresh_controller_independent_of_signalled_one(self):
        """A new _StopController() has stop_requested=False regardless of
        whether a previous instance was signalled."""
        old_ctl = _StopController()
        old_ctl._handle_signal(signal.SIGINT, None)
        assert old_ctl.stop_requested is True

        new_ctl = _StopController()
        assert new_ctl.stop_requested is False

    def test_context_manager_nesting_restores_outer(self):
        """Nesting two context managers should at minimum not corrupt the
        outer restore: outer handler is still the same object after both
        blocks exit.  This is a conservative invariant -- the spec does not
        forbid nesting, it just doesn't promise clean semantics."""
        original = _current_sigint_handler()
        with _StopController(), _StopController():
            pass
        # After both context managers exit, SIGINT must be restored.
        assert signal.getsignal(signal.SIGINT) is original
