"""Cross-process advisory file lock helper — SR-G3.

Provides a single public function:

    acquire(path, *, wait=False, timeout=None) -> ContextManager[bool]

On POSIX this uses ``fcntl.flock``; on Windows it falls back to
``msvcrt.locking``.  The context manager yields ``True`` when the lock
is successfully acquired, ``False`` when ``wait=False`` and the lock is
already held.

Cross-process behaviour (POSIX):
    - ``fcntl.flock`` is a *per-(process, open-file-description)* lock.
    - Within the same process an in-process ``threading.Lock`` per
      canonical path serialises concurrent threads so the helper behaves
      identically from multiple threads.
    - On exit (normal or exception) ``flock(LOCK_UN)`` is called and the
      fd is closed, releasing the OS lock.
"""

from __future__ import annotations

import contextlib
import errno
import os
import sys
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

# ---------------------------------------------------------------------------
# Platform-dispatch top-level imports
# fcntl is POSIX-only; msvcrt is Windows-only.  Both imports are gated so
# that the non-relevant one is never executed on its excluded platform.
# pyrefly will flag the Windows branch as unknown-name on macOS/Linux; those
# errors live in the pragma: no cover block and are accepted.
# ---------------------------------------------------------------------------

if sys.platform != "win32":
    import fcntl
else:  # pragma: no cover
    import msvcrt  # pyrefly: ignore[missing-import]  # Windows-only module, not present on POSIX

# ---------------------------------------------------------------------------
# Per-path in-process threading locks (prevent two threads in the same
# process from racing on the flock itself).
# ---------------------------------------------------------------------------

_path_locks: dict[str, threading.Lock] = {}
_path_locks_mu: threading.Lock = threading.Lock()


def _get_path_lock(canonical: str) -> threading.Lock:
    with _path_locks_mu:
        if canonical not in _path_locks:
            _path_locks[canonical] = threading.Lock()
        return _path_locks[canonical]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@contextmanager
def acquire(
    path: Path | str,
    *,
    wait: bool = False,
    timeout: float | None = None,
) -> Generator[bool, None, None]:
    """Acquire an advisory cross-process file lock on *path*.

    Args:
        path:    Path to the lock file.  The file is created if it does not
                 exist.  The parent directory must already exist.
        wait:    If ``False`` (default), tries once and returns ``False``
                 (without blocking) if the lock is already held.
                 If ``True``, blocks until the lock is available or *timeout*
                 expires.
        timeout: Maximum seconds to wait when ``wait=True``.  ``None`` means
                 wait indefinitely.  ``0.0`` is a single non-blocking poll.
                 Negative values raise ``ValueError``.

    Yields:
        ``True`` if the lock was acquired, ``False`` if not (only when
        ``wait=False`` or *timeout* expires).

    Raises:
        ValueError: If ``timeout`` is negative.
        OSError: For genuine I/O failures (e.g. permission denied).
    """
    if timeout is not None and timeout < 0:
        raise ValueError(f"timeout must be >= 0, got {timeout!r}")

    path = Path(path)
    # Compute canonical key; path may not exist yet.
    try:
        canonical = str(path.resolve())
    except OSError:  # pragma: no cover — fallback for broken FS
        canonical = str(path.absolute())

    path_lock = _get_path_lock(canonical)

    # --- Step 1: acquire the in-process threading lock ---
    if wait:
        # Blocking acquire with optional timeout.
        if timeout is None:
            thread_acquired = path_lock.acquire(blocking=True)
        else:
            deadline = time.monotonic() + timeout
            thread_acquired = False
            while True:
                thread_acquired = path_lock.acquire(blocking=False)
                if thread_acquired:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(0.01, remaining))
    else:
        thread_acquired = path_lock.acquire(blocking=False)

    if not thread_acquired:
        yield False
        return

    # We now hold the in-process lock.
    try:
        # --- Step 2: acquire the cross-process OS lock ---
        if sys.platform != "win32":
            held = _posix_acquire(path, wait=wait, timeout=timeout)
            try:
                yield held
            finally:
                if held:
                    _posix_release(path)
        else:  # pragma: no cover
            held = _win_acquire(path, wait=wait, timeout=timeout)
            try:
                yield held
            finally:
                if held:
                    _win_release(path)
    finally:
        path_lock.release()


# ---------------------------------------------------------------------------
# POSIX implementation (fcntl.flock)
# ---------------------------------------------------------------------------

# Key: canonical path str → open fd int.
_posix_fds: dict[str, int] = {}
_posix_fds_mu: threading.Lock = threading.Lock()


def _posix_acquire(path: Path, *, wait: bool, timeout: float | None) -> bool:
    """Acquire an exclusive flock on *path*.  Returns True on success."""
    path.touch(exist_ok=True)
    try:
        canonical = str(path.resolve())
    except OSError:  # pragma: no cover — fallback for broken FS
        canonical = str(path.absolute())

    fd = os.open(str(path), os.O_RDWR | os.O_CREAT)
    try:
        if wait:
            if timeout is None:
                fcntl.flock(fd, fcntl.LOCK_EX)
            else:
                deadline = time.monotonic() + timeout
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError as _e:
                        # Only swallow "lock held by another process" errors;
                        # re-raise permission errors and other genuine failures.
                        if _e.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                            os.close(fd)
                            raise
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            os.close(fd)
                            return False
                        time.sleep(min(0.01, remaining))
        else:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as _e:
                # Only swallow "lock held by another process" errors;
                # re-raise permission errors and other genuine failures.
                if _e.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                    os.close(fd)
                    raise
                os.close(fd)
                return False

        with _posix_fds_mu:
            _posix_fds[canonical] = fd
        return True

    except BaseException:
        os.close(fd)
        raise


def _posix_release(path: Path) -> None:
    """Release the flock and close the stored fd."""
    try:
        canonical = str(path.resolve())
    except OSError:  # pragma: no cover — fallback for broken FS
        canonical = str(path.absolute())

    with _posix_fds_mu:
        fd = _posix_fds.pop(canonical, None)

    if fd is None:
        return
    with contextlib.suppress(OSError):
        fcntl.flock(fd, fcntl.LOCK_UN)
    with contextlib.suppress(OSError):
        os.close(fd)


# ---------------------------------------------------------------------------
# Windows implementation (msvcrt.locking)
# All functions in this section are pragma: no cover — never executed on POSIX.
# The ``import msvcrt`` and all attribute accesses on it are pyrefly: ignore'd
# because pyrefly runs on macOS/Linux where the module doesn't exist.
# ---------------------------------------------------------------------------

# Key: canonical path str → open file object.
_win_files: dict[str, object] = {}
_win_files_mu: threading.Lock = threading.Lock()


def _win_acquire(path: Path, *, wait: bool, timeout: float | None) -> bool:  # pragma: no cover
    """Acquire a Windows msvcrt lock on *path*.  Returns True on success."""
    _LOCK_SIZE = 1
    path.touch(exist_ok=True)
    try:
        canonical = str(path.resolve())
    except OSError:  # pragma: no cover — fallback for broken FS
        canonical = str(path.absolute())

    f = path.open("wb+")  # SIM115: must keep file open for lock lifetime; no context manager
    # On Windows, msvcrt.locking raises OSError(EACCES) when the lock is held
    # by another process.  Only swallow that specific errno; re-raise others.
    _WIN_LOCK_CONTENTION_ERRNOS = frozenset({errno.EACCES})
    try:
        if wait:
            deadline = (time.monotonic() + timeout) if timeout is not None else None
            while True:
                try:
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, _LOCK_SIZE)  # pyrefly: ignore[unknown-name]  # msvcrt is Windows-only
                    break
                except OSError as _e:
                    # Only swallow "lock held" errors; re-raise genuine failures.
                    if _e.errno not in _WIN_LOCK_CONTENTION_ERRNOS:
                        f.close()
                        raise
                    if deadline is not None and time.monotonic() >= deadline:
                        f.close()
                        return False
                    time.sleep(0.01)
        else:
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, _LOCK_SIZE)  # pyrefly: ignore[unknown-name]  # msvcrt is Windows-only
            except OSError as _e:
                # Only swallow "lock held" errors; re-raise genuine failures.
                if _e.errno not in _WIN_LOCK_CONTENTION_ERRNOS:
                    f.close()
                    raise
                f.close()
                return False

        with _win_files_mu:
            _win_files[canonical] = f
        return True

    except BaseException:
        f.close()
        raise


def _win_release(path: Path) -> None:  # pragma: no cover
    """Release the msvcrt lock and close the stored file object."""
    try:
        canonical = str(path.resolve())
    except OSError:  # pragma: no cover — fallback for broken FS
        canonical = str(path.absolute())

    with _win_files_mu:
        f = _win_files.pop(canonical, None)

    if f is None:
        return
    with contextlib.suppress(OSError):
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[union-attr]  # pyrefly: ignore[unknown-name]  # msvcrt is Windows-only
    with contextlib.suppress(OSError):
        f.close()  # type: ignore[union-attr]
