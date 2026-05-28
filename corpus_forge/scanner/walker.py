"""Phase M Wave 2 — `os.scandir`-based unified walker.

The walker has three perf properties the legacy walkers did not:

1. **Descent-time pruning** — directories matching `baseline_dirs` or
   `IgnoreStack.directory_pruned` are skipped wholesale; we never
   `os.scandir` into them.
2. **Extension short-circuit BEFORE stat** — when `include_exts` /
   `include_filenames` are supplied, files with non-matching extensions
   are rejected via the cheap `entry.name` check before we pay for an
   `entry.stat()` syscall.
3. **Single `scandir` per directory** — context-managed, deterministic
   ordering when `sort=True`.

The walker yields :class:`WalkEntry` for files only. Directory traversal
is implicit. Callers that need per-directory bookkeeping can drive the
underlying `os.scandir` themselves.

Concurrency: when ``workers > 1``, a bounded :class:`ThreadPoolExecutor`
runs blocking ``os.scandir`` calls concurrently (prefetch).  The main
generator thread owns ordering, :class:`WalkStats` increments, and all
yields — so stats are single-writer (no lock needed) and output order is
deterministic (per-dir POSIX-sorted DFS), identical to ``workers=1``.

Helper :func:`resolve_effective_workers` resolves the effective worker
count from the ``CF_SCAN_WORKERS`` env var and/or ``ScanConfig.workers``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

# Re-use the baseline tables from `estimate.py` so there is exactly one
# source of truth for "skip wholesale".
from corpus_forge.estimate import _SKIP_DIR_NAMES, _SKIP_FILE_NAMES

if TYPE_CHECKING:  # pragma: no cover — typing only
    from corpus_forge.ignore import IgnoreStack

__all__ = ["WalkEntry", "WalkStats", "resolve_effective_workers", "walk"]

logger = logging.getLogger(__name__)


@dataclass
class WalkStats:
    """Sidecar counters mutated by :func:`walk` during iteration.

    The walker yields files only; callers that need to count directories
    (the estimator's `dir_count` field is one) supply a `WalkStats`
    instance and read the counters back after the iterator is drained.
    Counters are reset on construction; do not reuse a single instance
    across multiple walks unless that's deliberately what you want.
    """

    dirs_descended: int = 0
    files_yielded: int = 0


@dataclass(frozen=True)
class WalkEntry:
    """One yielded file from :func:`walk`.

    `path` is an absolute :class:`Path`. `stat` is the cached
    `os.stat_result` from the `entry.stat(follow_symlinks=False)` call
    the walker already paid for — callers should reuse it rather than
    re-statting. `is_dir` is always False today (the walker yields files
    only); the field is kept on the dataclass so a future revision could
    surface directory entries too.
    """

    path: Path
    stat: os.stat_result
    is_dir: bool


def resolve_effective_workers(config_workers: int | None) -> int:
    """Resolve the effective worker count for the concurrent walker.

    Priority order (first match wins):

    1. ``CF_SCAN_WORKERS`` environment variable — parsed to ``int``,
       wins unconditionally when set.
    2. ``config_workers=None`` (auto/unset sentinel) — returns the
       formula ``min(32, (os.cpu_count() or 1) * 4)``.
    3. ``config_workers`` — returned as-is (already an ``int``).

    Returns:
        Always an ``int``.
    """
    env_val = os.environ.get("CF_SCAN_WORKERS")
    if env_val is not None:
        return int(env_val)
    if config_workers is None:
        return min(32, (os.cpu_count() or 1) * 4)
    return config_workers


def _scandir_safe(path: Path) -> list[os.DirEntry[str]]:
    """Run ``os.scandir`` and return a list of entries (empty on OSError)."""
    try:
        with os.scandir(path) as it:
            return list(it)
    except OSError as exc:
        logger.debug("walker: cannot scandir %s: %s", path, exc)
        return []


def walk(
    root: Path | str,
    *,
    ignore: IgnoreStack | None = None,
    baseline_dirs: frozenset[str] = _SKIP_DIR_NAMES,
    baseline_files: frozenset[str] = _SKIP_FILE_NAMES,
    include_exts: frozenset[str] | None = None,
    include_filenames: frozenset[str] | None = None,
    follow_symlinks: bool = False,
    sort: bool = True,
    scan_root: Path | None = None,
    workers: int = 1,
    stats: WalkStats | None = None,
) -> Iterator[WalkEntry]:
    """Iterate files under ``root`` honoring baseline + ignore filters.

    Args:
      root: Directory to walk. Resolved to a :class:`Path` if a string is
        passed.
      ignore: Optional :class:`IgnoreStack` consulted twice per entry:
        :meth:`IgnoreStack.directory_pruned` for descent-time pruning,
        and :meth:`IgnoreStack.matches` for the final per-file decision.
      baseline_dirs: Directory NAMES (not paths) skipped wholesale.
        Defaults to ``_SKIP_DIR_NAMES``.
      baseline_files: File NAMES skipped wholesale. Defaults to
        ``_SKIP_FILE_NAMES``. macOS AppleDouble (`._*`) files are also
        skipped unconditionally.
      include_exts: Optional whitelist of lowercase extensions
        (incl. leading ``.``). When set, files with non-matching
        extensions are rejected BEFORE `entry.stat()`.
      include_filenames: Optional whitelist of exact filenames matched
        when `include_exts` misses (covers `Makefile` / `Dockerfile`).
      follow_symlinks: When False (default), symlinked entries (both
        dirs and files) are skipped — neither dereferenced nor yielded.
      sort: When True (default), per-directory entries are sorted by
        name before processing. Cheap (one list-sort per dir) and
        guarantees deterministic output for tests.
      scan_root: Reference root for `IgnoreStack` relative-path
        computation. Defaults to ``root`` — set this to the dataset's
        scan root when the walker is fed a sub-tree.
      workers: Number of threads for concurrent ``os.scandir``
        enumeration.  ``workers=1`` (default) takes the existing serial
        code path byte-for-byte.  ``workers > 1`` runs a bounded
        :class:`ThreadPoolExecutor` that prefetches scandir results
        concurrently while the main generator thread consumes them in
        stable DFS + sorted order.  Use :func:`resolve_effective_workers`
        to derive this from ``ScanConfig.workers`` + the
        ``CF_SCAN_WORKERS`` env override.
      stats: Optional :class:`WalkStats` mutated as the walk progresses.
        Counters are incremented in-place; the caller reads them after
        the iterator drains. Pass ``None`` (default) to skip the cost.

    Yields:
      :class:`WalkEntry` for every file that passes every filter.
    """
    # Resolve to absolute up-front so:
    # - WalkEntry.path is absolute (documented invariant);
    # - ignore.matches' ``relative_to(scan_root)`` never falls into the
    #   ValueError fallback because the candidate path was abs-vs-rel
    #   mismatched.
    root_path = Path(root).expanduser().resolve()
    scan_root_path = Path(scan_root).expanduser().resolve() if scan_root is not None else root_path

    if workers <= 1:
        yield from _walk_serial(
            root_path=root_path,
            scan_root_path=scan_root_path,
            ignore=ignore,
            baseline_dirs=baseline_dirs,
            baseline_files=baseline_files,
            include_exts=include_exts,
            include_filenames=include_filenames,
            follow_symlinks=follow_symlinks,
            sort=sort,
            stats=stats,
        )
    else:
        yield from _walk_concurrent(
            root_path=root_path,
            scan_root_path=scan_root_path,
            ignore=ignore,
            baseline_dirs=baseline_dirs,
            baseline_files=baseline_files,
            include_exts=include_exts,
            include_filenames=include_filenames,
            follow_symlinks=follow_symlinks,
            sort=sort,
            workers=workers,
            stats=stats,
        )


def _process_entries(
    entries: list[os.DirEntry[str]],
    current_rel: str,
    *,
    ignore: IgnoreStack | None,
    baseline_dirs: frozenset[str],
    baseline_files: frozenset[str],
    include_exts: frozenset[str] | None,
    include_filenames: frozenset[str] | None,
    follow_symlinks: bool,
    sort: bool,
    scan_root_path: Path,
    stats: WalkStats | None,
) -> tuple[list[WalkEntry], list[tuple[Path, str]]]:
    """Process a list of scandir entries for one directory.

    Returns:
        A 2-tuple of:
        - ``file_entries``: :class:`WalkEntry` objects for files that
          passed all filters (in stable order when ``sort=True``).
        - ``subdirs``: ``(absolute_path, rel_string)`` pairs for
          directories that should be descended into, in DFS-push order
          (reversed sorted order so a stack pop gives sorted order).

    Side-effects:
        Increments ``stats.dirs_descended`` for each accepted subdir and
        ``stats.files_yielded`` for each yielded file — the caller must
        hold the GIL (i.e. be the main thread) when invoking this.
    """
    if sort:
        entries.sort(key=lambda e: e.name)

    file_entries: list[WalkEntry] = []
    subdirs_to_push: list[tuple[Path, str]] = []

    for entry in entries:
        name = entry.name

        # Symlink gate — before is_dir/is_file.
        try:
            if not follow_symlinks and entry.is_symlink():
                continue
        except OSError as exc:
            logger.debug("walker: is_symlink failed on %s: %s", entry.path, exc)
            continue

        try:
            is_dir = entry.is_dir(follow_symlinks=follow_symlinks)
        except OSError as exc:
            logger.debug("walker: is_dir failed on %s: %s", entry.path, exc)
            continue

        if is_dir:
            if name in baseline_dirs:
                continue
            child_rel = f"{current_rel}/{name}" if current_rel else name
            if ignore is not None and ignore.directory_pruned(child_rel):
                continue
            if ignore is not None:
                try:
                    if ignore.matches(Path(entry.path), is_dir=True, scan_root=scan_root_path):
                        continue
                except ValueError:
                    pass
            subdirs_to_push.append((Path(entry.path), child_rel))
            if stats is not None:
                stats.dirs_descended += 1
            continue

        # ── File ────────────────────────────────────────────────────
        if name in baseline_files:
            continue
        if name.startswith("._"):
            continue

        if include_exts is not None or include_filenames is not None:
            last_dot = name.rfind(".")
            suffix = name[last_dot:].lower() if last_dot > 0 else ""
            ext_ok = include_exts is not None and suffix in include_exts
            name_ok = include_filenames is not None and name in include_filenames
            if not ext_ok and not name_ok:
                continue

        try:
            st = entry.stat(follow_symlinks=False)
        except OSError as exc:
            logger.debug("walker: stat failed on %s: %s", entry.path, exc)
            continue

        try:
            if not entry.is_file(follow_symlinks=follow_symlinks):
                continue
        except OSError:
            continue

        if ignore is not None:
            try:
                if ignore.matches(Path(entry.path), is_dir=False, scan_root=scan_root_path):
                    continue
            except ValueError:
                pass

        if stats is not None:
            stats.files_yielded += 1
        file_entries.append(WalkEntry(path=Path(entry.path), stat=st, is_dir=False))

    return file_entries, subdirs_to_push


def _walk_serial(
    root_path: Path,
    scan_root_path: Path,
    *,
    ignore: IgnoreStack | None,
    baseline_dirs: frozenset[str],
    baseline_files: frozenset[str],
    include_exts: frozenset[str] | None,
    include_filenames: frozenset[str] | None,
    follow_symlinks: bool,
    sort: bool,
    stats: WalkStats | None,
) -> Iterator[WalkEntry]:
    """Serial DFS walk — ``workers=1`` path, unchanged from original."""
    # Iterative DFS stack: (absolute_path, posix-relative_string_to_scan_root)
    stack: list[tuple[Path, str]] = [(root_path, "")]

    while stack:
        current, current_rel = stack.pop()
        try:
            with os.scandir(current) as it:
                entries = list(it)
        except OSError as exc:
            logger.debug("walker: cannot scandir %s: %s", current, exc)
            continue

        if sort:
            entries.sort(key=lambda e: e.name)

        subdirs_to_push: list[tuple[Path, str]] = []

        for entry in entries:
            name = entry.name

            try:
                if not follow_symlinks and entry.is_symlink():
                    continue
            except OSError as exc:
                logger.debug("walker: is_symlink failed on %s: %s", entry.path, exc)
                continue

            try:
                is_dir = entry.is_dir(follow_symlinks=follow_symlinks)
            except OSError as exc:
                logger.debug("walker: is_dir failed on %s: %s", entry.path, exc)
                continue

            if is_dir:
                if name in baseline_dirs:
                    continue
                child_rel = f"{current_rel}/{name}" if current_rel else name
                if ignore is not None and ignore.directory_pruned(child_rel):
                    continue
                if ignore is not None:
                    try:
                        if ignore.matches(Path(entry.path), is_dir=True, scan_root=scan_root_path):
                            continue
                    except ValueError:
                        pass
                subdirs_to_push.append((Path(entry.path), child_rel))
                if stats is not None:
                    stats.dirs_descended += 1
                continue

            if name in baseline_files:
                continue
            if name.startswith("._"):
                continue

            if include_exts is not None or include_filenames is not None:
                last_dot = name.rfind(".")
                suffix = name[last_dot:].lower() if last_dot > 0 else ""
                ext_ok = include_exts is not None and suffix in include_exts
                name_ok = include_filenames is not None and name in include_filenames
                if not ext_ok and not name_ok:
                    continue

            try:
                st = entry.stat(follow_symlinks=False)
            except OSError as exc:
                logger.debug("walker: stat failed on %s: %s", entry.path, exc)
                continue

            try:
                if not entry.is_file(follow_symlinks=follow_symlinks):
                    continue
            except OSError:
                continue

            if ignore is not None:
                try:
                    if ignore.matches(Path(entry.path), is_dir=False, scan_root=scan_root_path):
                        continue
                except ValueError:
                    pass

            if stats is not None:
                stats.files_yielded += 1
            yield WalkEntry(path=Path(entry.path), stat=st, is_dir=False)

        for child in reversed(subdirs_to_push):
            stack.append(child)


def _walk_concurrent(
    root_path: Path,
    scan_root_path: Path,
    *,
    ignore: IgnoreStack | None,
    baseline_dirs: frozenset[str],
    baseline_files: frozenset[str],
    include_exts: frozenset[str] | None,
    include_filenames: frozenset[str] | None,
    follow_symlinks: bool,
    sort: bool,
    workers: int,
    stats: WalkStats | None,
) -> Iterator[WalkEntry]:
    """Concurrent DFS walk — ``workers > 1`` path.

    Design:
    - A :class:`ThreadPoolExecutor` with ``max_workers=workers`` runs the
      blocking ``os.scandir`` calls concurrently (I/O prefetch).
    - The main generator thread owns a DFS stack of
      ``(path, rel, Future | None)`` items:
        - ``Future`` items: the scandir for this dir was already submitted
          to the pool (prefetch); the main thread calls ``.result()`` to
          get the entry list when it reaches this item.
        - ``None`` items: not yet submitted (should not arise in normal
          operation, kept as defensive fallback).
    - Ordering guarantee: the main thread submits the root scandir first,
      then each time it processes a dir's results it pushes the accepted
      subdirs (in reverse sorted order) back onto the stack AND immediately
      submits their scandir futures — so futures are in-flight while the
      main thread is processing the current dir's files.  When the main
      thread pops the next item it just calls ``.result()`` (may block
      briefly if the pool is busy) then continues.  Because items are
      pushed in reverse sorted order onto a LIFO stack, the pop order
      is sorted DFS — identical to ``workers=1``.
    - Thread safety: only the main thread increments ``WalkStats``
      counters and yields entries — no shared mutable state across threads.
    """
    with ThreadPoolExecutor(max_workers=workers) as pool:
        # Stack items: (path, rel, future_or_none)
        # We start by submitting the root scandir immediately.
        root_future: Future[list[os.DirEntry[str]]] = pool.submit(_scandir_safe, root_path)
        stack: list[tuple[Path, str, Future[list[os.DirEntry[str]]]]] = [
            (root_path, "", root_future)
        ]

        while stack:
            _current, current_rel, fut = stack.pop()

            # Block until this directory's scandir result is ready.
            entries = fut.result()

            # Process entries on the main thread (single-writer for stats).
            file_entries, subdirs_to_push = _process_entries(
                entries,
                current_rel,
                ignore=ignore,
                baseline_dirs=baseline_dirs,
                baseline_files=baseline_files,
                include_exts=include_exts,
                include_filenames=include_filenames,
                follow_symlinks=follow_symlinks,
                sort=sort,
                scan_root_path=scan_root_path,
                stats=stats,
            )

            # Eagerly submit scandir futures for all accepted subdirs
            # before yielding any files from this dir.  This overlaps I/O
            # with the main thread's work on files below.
            # We push in reversed order so LIFO pops them in sorted DFS order.
            futures_to_push: list[tuple[Path, str, Future[list[os.DirEntry[str]]]]] = []
            for child_path, child_rel in subdirs_to_push:
                child_future: Future[list[os.DirEntry[str]]] = pool.submit(
                    _scandir_safe, child_path
                )
                futures_to_push.append((child_path, child_rel, child_future))

            for item in reversed(futures_to_push):
                stack.append(item)

            # Yield files from this directory (main thread only).
            yield from file_entries
