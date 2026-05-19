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

Concurrency note: `workers > 1` is API-plumbed but raises
`NotImplementedError` for now. A future revision may add a thread pool
to overlap stat calls; the contract here is single-threaded.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

# Re-use the baseline tables from `estimate.py` so there is exactly one
# source of truth for "skip wholesale".
from corpus_forge.estimate import _SKIP_DIR_NAMES, _SKIP_FILE_NAMES

if TYPE_CHECKING:  # pragma: no cover — typing only
    from corpus_forge.ignore import IgnoreStack

__all__ = ["WalkEntry", "WalkStats", "walk"]

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
      workers: API-plumbed but not yet implemented. ``> 1`` raises
        :class:`NotImplementedError`.
      stats: Optional :class:`WalkStats` mutated as the walk progresses.
        Counters are incremented in-place; the caller reads them after
        the iterator drains. Pass ``None`` (default) to skip the cost.

    Yields:
      :class:`WalkEntry` for every file that passes every filter.
    """
    if workers > 1:
        raise NotImplementedError("walker concurrency is a follow-up — pass workers=1")

    root_path = Path(root)
    scan_root_path = scan_root if scan_root is not None else root_path

    # Iterative DFS stack: (absolute_path, posix-relative_string_to_scan_root)
    # We carry the rel-string so we can call ``ignore.directory_pruned``
    # without re-deriving it for each candidate.
    stack: list[tuple[Path, str]] = [(root_path, "")]

    while stack:
        current, current_rel = stack.pop()
        # Buffer entries for sort and to release the scandir handle
        # before we descend (limits open-FD pressure on deep trees).
        try:
            with os.scandir(current) as it:
                entries = list(it)
        except OSError as exc:
            logger.debug("walker: cannot scandir %s: %s", current, exc)
            continue

        if sort:
            entries.sort(key=lambda e: e.name)

        # Buffer subdirectories so we can push them in reverse order
        # (so DFS pops in sorted order when sort=True).
        subdirs_to_push: list[tuple[Path, str]] = []

        for entry in entries:
            name = entry.name

            # Symlink gate. We do this BEFORE is_dir/is_file because
            # `entry.is_dir(follow_symlinks=False)` is False for
            # symlinked directories — but we want to explicitly bypass
            # them regardless of target type when `follow_symlinks=False`.
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
                # ── Directory: baseline → ignore.directory_pruned → ignore.matches
                if name in baseline_dirs:
                    continue
                child_rel = f"{current_rel}/{name}" if current_rel else name
                # `directory_pruned` is the fast path — conservative-negation
                # algorithm; when it returns True the dir is definitively
                # excluded by the ignore stack and we can skip the scandir.
                if ignore is not None and ignore.directory_pruned(child_rel):
                    continue
                # `matches(is_dir=True)` is the legacy fallback — applies
                # the full gitignore last-match-wins semantics, including
                # the gitignore rule that an excluded parent dir cannot
                # be re-included by a negation pointing inside it (which
                # is why we still skip when this returns True even in
                # the presence of negations elsewhere in the stack).
                if ignore is not None:
                    try:
                        if ignore.matches(Path(entry.path), is_dir=True, scan_root=scan_root_path):
                            continue
                    except ValueError:
                        # Path not under scan_root — defensive; fall through.
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

            # Short-circuit on extension/filename BEFORE stat.
            if include_exts is not None or include_filenames is not None:
                # Compute suffix the way `pathlib.Path.suffix` does:
                # everything from the last '.' (inclusive) if there is
                # one past position 0, else ''.
                last_dot = name.rfind(".")
                suffix = name[last_dot:].lower() if last_dot > 0 else ""
                ext_ok = include_exts is not None and suffix in include_exts
                name_ok = include_filenames is not None and name in include_filenames
                if not ext_ok and not name_ok:
                    continue

            # Stat for size + regular-file check. Caller is responsible
            # for using ``WalkEntry.stat`` (cached) rather than re-stat'ing.
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError as exc:
                logger.debug("walker: stat failed on %s: %s", entry.path, exc)
                continue

            # Regular-file check — `is_file` was already implicit via
            # the `is_dir` branch, but we still need to reject sockets,
            # FIFOs, block devices, etc.
            try:
                if not entry.is_file(follow_symlinks=follow_symlinks):
                    continue
            except OSError:
                continue

            # Final ignore-stack consultation.
            if ignore is not None:
                try:
                    if ignore.matches(Path(entry.path), is_dir=False, scan_root=scan_root_path):
                        continue
                except ValueError:
                    pass

            if stats is not None:
                stats.files_yielded += 1
            yield WalkEntry(path=Path(entry.path), stat=st, is_dir=False)

        # Push subdirs in reverse so DFS pops them in sorted order.
        for child in reversed(subdirs_to_push):
            stack.append(child)
