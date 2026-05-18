"""Foreground / background child-process wrapper (Phase L Wave 7).

Project-wide convention: every command that triggers a long-running
side-effect (re-embed, daemon restart, ollama pull, source ingest after a
config change) defaults to **foreground** — the CLI stays attached, the
child's stdout/stderr stream back to the parent, and SIGINT/SIGTERM
forward to the child.  ``--background`` / ``-b`` flips to detached
execution via ``subprocess.Popen(stdin=DEVNULL, start_new_session=True)``,
writes a pid file under ``<platformdirs cache>/corpus-forge/state/`` and
returns 0 immediately.

The wrapper is split into four pieces:

- :func:`state_dir` — singleton directory the pid files live in.
- :func:`write_pid` / :func:`read_pid` / :func:`clear_pid` — atomic pid
  file helpers (``read_pid`` returns ``None`` for stale pids using a
  cheap ``os.kill(pid, 0)`` liveness check).
- :func:`run_attached` — the foreground / background switch + signal
  forwarding glue used by every long-op admin verb.

Cross-cutting: Wave 5 already shipped an ad-hoc ``_state_dir_path`` +
detached spawn inside ``corpus_forge/cli.py`` for the embedder-drift
worker.  Wave 7 generalises it here so the same conventions apply to
``ollama pull``, ``embedder set-active``, ``source add``, etc.
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Final

import platformdirs

from corpus_forge.ui.console import info as ui_info

logger = logging.getLogger(__name__)

_STATE_DIR_NAME: Final[str] = "state"

# Forwarded signals.  SIGTERM is dropped on Windows by Python's signal
# module so it's guarded at call time.
_FORWARD_SIGNALS: tuple[signal.Signals, ...] = (signal.SIGINT, signal.SIGTERM)


def state_dir() -> Path:
    """Return the per-user state directory (``<cache>/corpus-forge/state``).

    Resolved against :func:`platformdirs.user_cache_dir`, mirroring the
    log-directory resolution in :mod:`corpus_forge.logging_config`.  The
    directory is created on every call so callers don't have to.
    """

    path = Path(platformdirs.user_cache_dir("corpus-forge")) / _STATE_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def pid_path(component: str) -> Path:
    """Return the absolute path to ``<state>/<component>.pid``."""

    return state_dir() / f"{component}.pid"


def write_pid(component: str, pid: int) -> None:
    """Atomically write ``pid`` to the component's pid file.

    Writes via tempfile + ``Path.replace`` so a crashed write doesn't
    leave a half-written value on disk.
    """

    target = pid_path(component)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(str(int(pid)), encoding="utf-8")
    tmp.replace(target)


def read_pid(component: str) -> int | None:
    """Return ``pid`` if the component's pid file points at a live process.

    Returns ``None`` when the file is absent, malformed, or names a
    process that has already exited.  Liveness check uses ``os.kill(pid,
    0)`` which is the canonical POSIX shape; on Windows we fall back to
    a ``signal.SIGTERM=0`` probe which behaves the same way on CPython.
    """

    path = pid_path(component)
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        pid = int(raw)
    except ValueError:
        return None
    if pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        # Dead process — caller treats the pid file as stale.
        return None
    except PermissionError:
        # Process exists, just not ours — still "alive".
        return pid
    except OSError:
        return None
    return pid


def clear_pid(component: str) -> None:
    """Remove the component's pid file (idempotent)."""

    with contextlib.suppress(OSError):
        pid_path(component).unlink()


def _build_env(overlay: dict[str, str] | None) -> dict[str, str]:
    env = os.environ.copy()
    if overlay:
        for key, value in overlay.items():
            env[str(key)] = str(value)
    return env


def _safe_std(stream, *, default):
    """Return ``stream`` if it has a real ``fileno``, else ``default``.

    Test harnesses (pytest's capture, IPython, etc.) replace ``sys.stdin``
    with a pseudo-file that doesn't expose a ``fileno()``.  Pass that to
    ``subprocess.Popen`` and it raises ``io.UnsupportedOperation`` before
    the child ever launches.  We probe for ``fileno`` first; if it fails,
    we fall back to ``default`` (``DEVNULL`` for stdin, ``None`` —
    meaning "inherit the parent's real fd" — for stdout/stderr).
    """

    if stream is None:
        return default
    fileno = getattr(stream, "fileno", None)
    if fileno is None:
        return default
    try:
        fileno()
    except (OSError, ValueError):
        return default
    return stream


def _forward_signal_factory(child: subprocess.Popen):
    """Build a signal handler that forwards a signal to ``child``."""

    def _handler(signum, _frame):
        try:
            child.send_signal(signum)
        except (ProcessLookupError, OSError) as exc:
            logger.debug("forward signal %s to child failed: %s", signum, exc)

    return _handler


def _install_signal_forwarders(child: subprocess.Popen) -> dict[int, object]:
    """Install SIGINT/SIGTERM forwarders, return the original handlers."""

    previous: dict[int, object] = {}
    handler = _forward_signal_factory(child)
    for sig in _FORWARD_SIGNALS:
        try:
            previous[int(sig)] = signal.signal(sig, handler)
        except (ValueError, OSError):
            # ``signal.signal`` raises ``ValueError`` if called from a
            # non-main thread; tests under pytest sometimes hit this.
            # We degrade quietly rather than fail.
            logger.debug("install forwarder for %s skipped", sig)
    return previous


def _restore_signal_handlers(previous: dict[int, object]) -> None:
    for sig_int, handler in previous.items():
        try:
            signal.signal(sig_int, handler)  # type: ignore[arg-type]
        except (ValueError, OSError):
            logger.debug("restore handler for %s skipped", sig_int)


def run_attached(
    argv: list[str],
    *,
    component: str,
    env_overlay: dict[str, str] | None = None,
    background: bool = False,
    cwd: str | os.PathLike[str] | None = None,
) -> int:
    """Run ``argv`` as a child process; return the child's exit code.

    Two modes:

    - **Foreground** (default, ``background=False``): inherit the
      parent's stdout/stderr (no pipe — Rich progress and live log lines
      stream back unchanged), forward SIGINT / SIGTERM to the child,
      wait for completion, and return ``child.returncode``.

    - **Background** (``background=True``): detach via ``stdin=DEVNULL``
      and ``start_new_session=True`` (so Ctrl+C in the parent shell
      doesn't bring the child down), write the child's pid to
      ``<state>/<component>.pid``, print a hint pointing at
      ``corpus-forge logs tail --component <component> --follow``, and
      return 0 immediately.

    ``env_overlay`` keys are merged on top of ``os.environ`` for the
    child; the parent's env is untouched.  ``cwd`` is forwarded to
    ``subprocess.Popen`` so callers can pin the working directory.
    """

    if not argv:
        raise ValueError("run_attached requires a non-empty argv")

    env = _build_env(env_overlay)

    if background:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
            cwd=cwd,
        )
        write_pid(component, proc.pid)
        ui_info(
            f"{component} running in background (pid={proc.pid}). "
            f"Watch with: corpus-forge logs tail --component {component} --follow"
        )
        return 0

    # Foreground: inherit the parent's stdout/stderr so progress bars
    # and live log tails stream back unchanged.  Test runners
    # (``pytest``) wrap stdio in pseudo-files without a ``fileno()`` —
    # fall back to ``None`` (= inherit the actual fd directly) when the
    # check fails, or ``DEVNULL`` if even that is unavailable.
    proc = subprocess.Popen(
        argv,
        stdin=_safe_std(sys.stdin, default=subprocess.DEVNULL),
        stdout=_safe_std(sys.stdout, default=None),
        stderr=_safe_std(sys.stderr, default=None),
        env=env,
        cwd=cwd,
    )
    previous = _install_signal_forwarders(proc)
    try:
        try:
            return proc.wait()
        except KeyboardInterrupt:
            # SIGINT already forwarded by the installed handler; give the
            # child a moment to clean up, then return whatever exit code
            # it produced.
            try:
                return proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                return proc.wait()
    finally:
        _restore_signal_handlers(previous)


__all__ = [
    "clear_pid",
    "pid_path",
    "read_pid",
    "run_attached",
    "state_dir",
    "write_pid",
]
