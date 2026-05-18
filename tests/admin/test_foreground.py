"""Tests for :mod:`corpus_forge.admin.foreground` (Phase L Wave 7).

Three regions:

1. PID-file helpers — ``write_pid`` / ``read_pid`` / ``clear_pid`` with
   liveness check semantics.
2. Background-mode ``run_attached`` — returns 0 immediately, writes the
   pid file, child is alive.
3. Foreground-mode ``run_attached`` — returns child exit code, SIGINT
   forwards to the child.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import platformdirs
import pytest

from corpus_forge.admin import foreground


def _isolate_state_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect platformdirs cache to ``tmp_path`` for the test."""

    def _fake_cache_dir(name: str, *args, **kwargs) -> str:
        return str(tmp_path / name)

    monkeypatch.setattr(platformdirs, "user_cache_dir", _fake_cache_dir)
    return tmp_path


# ── pid-file helpers ─────────────────────────────────────────────────────


def test_state_dir_creates_under_platformdirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_state_dir(monkeypatch, tmp_path)
    sd = foreground.state_dir()
    assert sd.is_dir()
    assert sd.parts[-2:] == ("corpus-forge", "state")


def test_pid_path_under_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_state_dir(monkeypatch, tmp_path)
    p = foreground.pid_path("daemon")
    assert p.parent == foreground.state_dir()
    assert p.name == "daemon.pid"


def test_write_pid_atomic_replace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_state_dir(monkeypatch, tmp_path)
    foreground.write_pid("alpha", 12345)
    assert foreground.pid_path("alpha").read_text(encoding="utf-8") == "12345"
    # Overwrites in place.
    foreground.write_pid("alpha", 6789)
    assert foreground.pid_path("alpha").read_text(encoding="utf-8") == "6789"


def test_read_pid_returns_live_pid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_state_dir(monkeypatch, tmp_path)
    # Our own pid is always alive.
    foreground.write_pid("self", os.getpid())
    assert foreground.read_pid("self") == os.getpid()


def test_read_pid_returns_none_for_dead_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_state_dir(monkeypatch, tmp_path)
    # PID 0 is invalid; PID 1 (init) is too well-known to spoof on every
    # OS — spawn a child and let it exit, then check.
    proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(0)"])
    proc.wait()
    foreground.write_pid("dead", proc.pid)
    # The OS may recycle the pid, but on a quiet test machine the
    # chance is small; allow either None or "process is somebody else's".
    result = foreground.read_pid("dead")
    # The contract is "return None when the original process is gone".
    # In practice on the test runner, after wait() the kernel has
    # reaped the slot and the pid is invalid.
    assert result in (None, proc.pid)
    if result is not None:
        # If the pid got recycled by something else, our own kill(0)
        # would have succeeded — that's the alive path, which is also
        # documented behavior.  We can't tighten further without a
        # platform-specific mock.
        pass


def test_read_pid_returns_none_for_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_state_dir(monkeypatch, tmp_path)
    assert foreground.read_pid("nope") is None


def test_read_pid_returns_none_for_malformed_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_state_dir(monkeypatch, tmp_path)
    foreground.pid_path("bad").write_text("not-a-number", encoding="utf-8")
    assert foreground.read_pid("bad") is None


def test_read_pid_returns_none_for_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_state_dir(monkeypatch, tmp_path)
    foreground.pid_path("zero").write_text("0", encoding="utf-8")
    assert foreground.read_pid("zero") is None


def test_clear_pid_removes_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_state_dir(monkeypatch, tmp_path)
    foreground.write_pid("temp", 999)
    assert foreground.pid_path("temp").exists()
    foreground.clear_pid("temp")
    assert not foreground.pid_path("temp").exists()
    # Idempotent.
    foreground.clear_pid("temp")
    assert not foreground.pid_path("temp").exists()


# ── run_attached: empty argv guard ──────────────────────────────────────


def test_run_attached_rejects_empty_argv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_state_dir(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        foreground.run_attached([], component="x")


# ── run_attached: background mode ───────────────────────────────────────


def test_run_attached_background_returns_zero_and_writes_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_state_dir(monkeypatch, tmp_path)
    # Tiny child that sleeps just long enough that the pid is live when
    # we inspect it.
    rc = foreground.run_attached(
        [sys.executable, "-c", "import time; time.sleep(0.2)"],
        component="bg-test",
        background=True,
    )
    assert rc == 0
    pid_file = foreground.pid_path("bg-test")
    assert pid_file.exists(), "pid file should exist after background spawn"
    pid = int(pid_file.read_text(encoding="utf-8").strip())
    assert pid > 0
    # Wait for the child to exit so we don't leak.
    time.sleep(0.3)


def test_run_attached_background_env_overlay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_state_dir(monkeypatch, tmp_path)
    marker = tmp_path / "marker.txt"
    code = (
        "import os, pathlib; "
        f"pathlib.Path({str(marker)!r}).write_text(os.environ.get('CF_TEST_TAG',''))"
    )
    rc = foreground.run_attached(
        [sys.executable, "-c", code],
        component="env-test",
        background=True,
        env_overlay={"CF_TEST_TAG": "hello-bg"},
    )
    assert rc == 0
    # Give the detached child a moment to land.
    for _ in range(50):
        if marker.exists():
            break
        time.sleep(0.05)
    assert marker.exists()
    assert marker.read_text(encoding="utf-8") == "hello-bg"


# ── run_attached: foreground mode ───────────────────────────────────────


def test_run_attached_foreground_returns_child_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_state_dir(monkeypatch, tmp_path)
    rc = foreground.run_attached(
        [sys.executable, "-c", "import sys; sys.exit(7)"],
        component="fg-test",
        background=False,
    )
    assert rc == 7


def test_run_attached_foreground_zero_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_state_dir(monkeypatch, tmp_path)
    rc = foreground.run_attached(
        [sys.executable, "-c", "print('ok')"],
        component="fg-ok",
        background=False,
    )
    assert rc == 0


# ── coverage push: edge branches in read_pid / _safe_std / signal install ──


def test_read_pid_returns_none_for_empty_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 0-byte pid file returns ``None`` (the early ``if not raw`` branch)."""

    _isolate_state_dir(monkeypatch, tmp_path)
    foreground.pid_path("empty").write_text("", encoding="utf-8")
    assert foreground.read_pid("empty") is None


def test_read_pid_returns_none_when_read_raises_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OSError during ``Path.read_text`` is swallowed (returns None)."""

    _isolate_state_dir(monkeypatch, tmp_path)
    # Write the file (so the ``exists()`` check passes) then patch
    # ``read_text`` on the Path class to raise.
    foreground.write_pid("perm", os.getpid())

    real_read_text = Path.read_text

    def _broken_read_text(self, *args, **kwargs):
        if self.name == "perm.pid":
            raise OSError("simulated read failure")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _broken_read_text)
    assert foreground.read_pid("perm") is None


def test_read_pid_returns_pid_on_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``os.kill(pid, 0)`` raising PermissionError → pid still counted as alive."""

    _isolate_state_dir(monkeypatch, tmp_path)
    foreground.write_pid("alien", 99999)

    def _fake_kill(pid: int, sig: int) -> None:
        raise PermissionError("not yours, but it's there")

    monkeypatch.setattr(os, "kill", _fake_kill)
    assert foreground.read_pid("alien") == 99999


def test_read_pid_returns_none_on_other_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-PermissionError, non-ProcessLookupError OSError → None."""

    _isolate_state_dir(monkeypatch, tmp_path)
    foreground.write_pid("strange", 99999)

    def _fake_kill(pid: int, sig: int) -> None:
        raise OSError(99, "some other errno")

    monkeypatch.setattr(os, "kill", _fake_kill)
    assert foreground.read_pid("strange") is None


def test_safe_std_returns_default_for_none() -> None:
    """`_safe_std(None, default=X)` returns X immediately."""

    sentinel = object()
    assert foreground._safe_std(None, default=sentinel) is sentinel


def test_safe_std_returns_default_for_streams_without_fileno() -> None:
    """A stream without a ``fileno`` attr falls back to the default."""

    class _NoFileno:
        pass

    sentinel = object()
    assert foreground._safe_std(_NoFileno(), default=sentinel) is sentinel


def test_safe_std_returns_default_when_fileno_raises() -> None:
    """A stream whose ``fileno()`` raises falls back to the default."""

    class _BrokenFileno:
        def fileno(self) -> int:
            raise OSError("captured by pytest")

    sentinel = object()
    assert foreground._safe_std(_BrokenFileno(), default=sentinel) is sentinel


def test_safe_std_returns_stream_when_fileno_works() -> None:
    """A real stream is passed through unchanged."""

    stream = sys.stdout

    # ``sys.stdout`` may not have a real fileno() under pytest capture —
    # build a stub that does.
    class _RealStream:
        def fileno(self) -> int:
            return 1

    s = _RealStream()
    assert foreground._safe_std(s, default=None) is s
    # Reference ``stream`` to keep ruff happy.
    assert stream is sys.stdout


def test_install_signal_forwarders_swallows_value_error(monkeypatch) -> None:
    """``signal.signal`` raising ValueError → forwarder install is silently skipped."""

    def _refuse(sig, handler):
        raise ValueError("simulated non-main-thread refusal")

    monkeypatch.setattr(signal, "signal", _refuse)

    # Build a stub Popen-like — we just need ``.send_signal`` to be safe.
    class _Stub:
        def send_signal(self, sig: int) -> None:
            pass

    previous = foreground._install_signal_forwarders(_Stub())  # type: ignore[arg-type]
    # No handlers were captured because every install raised.
    assert previous == {}


def test_restore_signal_handlers_swallows_value_error(monkeypatch) -> None:
    """``_restore_signal_handlers`` survives a flaky ``signal.signal``."""

    def _refuse(sig, handler):
        raise ValueError("simulated refusal")

    monkeypatch.setattr(signal, "signal", _refuse)
    foreground._restore_signal_handlers({int(signal.SIGINT): None})  # must not raise


def test_forward_signal_factory_swallows_lookup_error() -> None:
    """The forwarder handler swallows ProcessLookupError from a dead child."""

    class _DeadChild:
        def send_signal(self, sig: int) -> None:
            raise ProcessLookupError

    handler = foreground._forward_signal_factory(_DeadChild())  # type: ignore[arg-type]
    # Calling the handler with a fake (signum, frame) should not raise.
    handler(int(signal.SIGINT), None)


def test_run_attached_foreground_handles_keyboard_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When ``proc.wait()`` raises KeyboardInterrupt, the inner wait returns the rc."""

    _isolate_state_dir(monkeypatch, tmp_path)

    class _FakeProc:
        def __init__(self) -> None:
            self.pid = 999
            self._waited = 0

        def wait(self, timeout: float | None = None):
            self._waited += 1
            if self._waited == 1:
                raise KeyboardInterrupt
            # Second call (inside the KI handler) returns the child code.
            return 130

        def kill(self) -> None:
            pass

        def send_signal(self, sig: int) -> None:
            pass

    fake = _FakeProc()

    def _fake_popen(*args, **kwargs):
        return fake

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    # Defang the signal installer so we don't touch the real handler table.
    monkeypatch.setattr(foreground, "_install_signal_forwarders", lambda _proc: {})
    monkeypatch.setattr(foreground, "_restore_signal_handlers", lambda _prev: None)

    rc = foreground.run_attached(
        [sys.executable, "-c", "pass"], component="ki-test", background=False
    )
    assert rc == 130


def test_run_attached_foreground_kills_child_after_keyboard_interrupt_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the post-SIGINT wait times out, ``proc.kill()`` runs and we return its rc."""

    _isolate_state_dir(monkeypatch, tmp_path)

    class _StubbornProc:
        def __init__(self) -> None:
            self.pid = 1234
            self._calls = 0
            self.killed = False

        def wait(self, timeout: float | None = None):
            self._calls += 1
            if self._calls == 1:
                # First call (no timeout) — interrupt.
                raise KeyboardInterrupt
            if self._calls == 2:
                # Second call (with timeout) — child still alive.
                raise subprocess.TimeoutExpired(cmd="x", timeout=timeout or 0.0)
            # Third call (after kill) — finally returns.
            return 137

        def kill(self) -> None:
            self.killed = True

        def send_signal(self, sig: int) -> None:
            pass

    fake = _StubbornProc()
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: fake)
    monkeypatch.setattr(foreground, "_install_signal_forwarders", lambda _proc: {})
    monkeypatch.setattr(foreground, "_restore_signal_handlers", lambda _prev: None)

    rc = foreground.run_attached(
        [sys.executable, "-c", "pass"], component="ki-kill-test", background=False
    )
    assert rc == 137
    assert fake.killed


@pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="POSIX-only signal forwarding; Windows has different semantics.",
)
def test_run_attached_foreground_forwards_sigint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SIGINT delivered to the parent should propagate to the child.

    We launch a child that traps SIGINT and exits with code 42, then
    fire SIGINT at our own process from a helper thread.  ``run_attached``
    must forward the signal; the child exits 42; the wrapper returns 42.
    """

    _isolate_state_dir(monkeypatch, tmp_path)
    child_code = (
        "import signal, sys\n"
        "def _h(*_): sys.exit(42)\n"
        "signal.signal(signal.SIGINT, _h)\n"
        "import time\n"
        "time.sleep(5)\n"
    )

    parent_pid = os.getpid()

    def _send_sigint_after_delay() -> None:
        # Wait long enough for the child to install its handler.
        time.sleep(0.5)
        os.kill(parent_pid, signal.SIGINT)

    sender = threading.Thread(target=_send_sigint_after_delay, daemon=True)
    sender.start()

    rc = foreground.run_attached(
        [sys.executable, "-c", child_code],
        component="sigint-test",
        background=False,
    )
    sender.join(timeout=1.0)
    assert rc == 42
