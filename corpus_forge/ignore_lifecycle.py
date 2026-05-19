"""Phase M Wave 1 — managed-block splicer + atomic writer + per-tree
resync helpers for ``.corpusignore``.

This module owns the **side-effecting** half of the ignore lifecycle.
The pure half lives in :mod:`corpus_forge.ignore_defaults`.

Public API
----------

- :class:`ManagedBlockCorrupted` — raised when the existing file has a
  start sentinel without a matching end (or vice versa).
- :func:`splice_managed_block` — replace the managed block in a string
  while preserving all surrounding user lines.
- :func:`atomic_write_text` — tempfile + ``os.replace`` write, Windows-
  safe.
- :func:`write_corpusignore` — high-level "render and write one tree's
  ``.corpusignore``".
- :func:`discover_data_roots` — FS-style plugin discovery from a
  :class:`corpus_forge.config.Config`.
- :func:`resync_all` — write every root + (optionally) the global file.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from corpus_forge.ignore_defaults import (
    MANAGED_END,
    MANAGED_START,
    feature_flags_from_config,
    render_managed_block,
)

if TYPE_CHECKING:
    from corpus_forge.config import Config


__all__ = [
    "CorpusignoreWriteResult",
    "ManagedBlockCorrupted",
    "atomic_write_text",
    "discover_data_roots",
    "resync_all",
    "splice_managed_block",
    "write_corpusignore",
]


# ── exceptions ────────────────────────────────────────────────────────


class ManagedBlockCorrupted(Exception):
    """The existing ``.corpusignore`` has malformed managed-block sentinels.

    Either:

    - ``MANAGED_START`` present but no matching ``MANAGED_END``.
    - ``MANAGED_END`` present but no preceding ``MANAGED_START``.

    Callers can recover by either deleting the file or (the recommended
    path) passing ``backup_corrupted=True`` to
    :func:`write_corpusignore`, which moves the bad file to
    ``.corpusignore.bak.<ts>`` and rewrites from scratch.
    """


# ── splicer ───────────────────────────────────────────────────────────


def _find_sentinel_indices(lines: list[str]) -> tuple[int | None, int | None]:
    """Locate the (start, end) line indices of the managed block.

    The match is **full-line exact** — a substring mention in a user
    comment is not recognised.
    """
    start_idx: int | None = None
    end_idx: int | None = None
    for i, line in enumerate(lines):
        if line == MANAGED_START and start_idx is None:
            start_idx = i
        elif line == MANAGED_END and start_idx is not None and end_idx is None:
            end_idx = i
    # An END with no START still counts as corruption — flag it by
    # walking again with a relaxed sweep.
    if end_idx is None:
        for i, line in enumerate(lines):
            if line == MANAGED_END:
                end_idx = i
                break
    return start_idx, end_idx


def splice_managed_block(existing_text: str, new_block: str) -> str:
    """Replace the managed block inside ``existing_text`` with ``new_block``.

    Behaviour
    ---------
    * Empty ``existing_text`` → just return ``new_block``.
    * No sentinels in ``existing_text`` → append ``new_block`` (preceded
      by a blank line for readability if the existing text doesn't end
      in a newline).
    * Both sentinels present → replace everything between them
      (inclusive of the sentinel lines themselves) with ``new_block``.
    * One sentinel without the other → :class:`ManagedBlockCorrupted`.

    The function is idempotent: ``splice(splice(x, b), b) == splice(x, b)``.
    """
    if not existing_text:
        return new_block

    lines = existing_text.splitlines()
    start_idx, end_idx = _find_sentinel_indices(lines)

    # Corruption: exactly one sentinel.
    if (start_idx is None) != (end_idx is None):
        raise ManagedBlockCorrupted(
            "Existing .corpusignore has unbalanced managed-block sentinels — "
            f"start_idx={start_idx}, end_idx={end_idx}"
        )

    # Also corruption if end_idx < start_idx (end appears before start).
    if start_idx is not None and end_idx is not None and end_idx < start_idx:
        raise ManagedBlockCorrupted(
            "MANAGED_END appears before MANAGED_START in existing .corpusignore"
        )

    new_block_text = new_block if new_block.endswith("\n") else new_block + "\n"

    if start_idx is None and end_idx is None:
        # No managed block yet — append. Preserve a trailing newline
        # on the existing content so the new block starts on its own line.
        prefix = existing_text
        if not prefix.endswith("\n"):
            prefix = prefix + "\n"
        return prefix + new_block_text

    # Both sentinels present — splice.
    assert start_idx is not None
    assert end_idx is not None
    before = lines[:start_idx]
    after = lines[end_idx + 1 :]
    # Rebuild: before-lines (joined with newlines) + new_block (ends in
    # newline) + after-lines.
    parts: list[str] = []
    if before:
        parts.append("\n".join(before) + "\n")
    parts.append(new_block_text)
    if after:
        parts.append("\n".join(after))
        # Preserve a trailing newline if the original had one.
        if existing_text.endswith("\n"):
            parts.append("\n")
    return "".join(parts)


# ── atomic writer ─────────────────────────────────────────────────────


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically.

    Strategy: write to a sibling tempfile, ``fsync``, then ``os.replace``
    (which is atomic on POSIX and best-effort atomic on Windows post-NT).
    The parent directory must already exist — the caller decides whether
    to ``mkdir(parents=True)``.

    On Windows ``os.replace`` can occasionally race against AV scanners;
    we retry once with a short backoff before re-raising.
    """
    parent = path.parent
    # Create the tempfile in the same directory so ``os.replace`` lands
    # on the same filesystem (cross-FS replace can fall back to copy +
    # delete which breaks atomicity).
    fd, tmp_path_str = tempfile.mkstemp(prefix=".cf-tmp-", dir=str(parent))
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fp:
            fp.write(text)
            fp.flush()
            with contextlib.suppress(OSError):
                # Some filesystems (tmpfs, FUSE) reject fsync — swallow;
                # the write is still durable enough for our purposes.
                os.fsync(fp.fileno())
        try:
            tmp_path.replace(path)
        except PermissionError:
            # Windows: AV/Defender briefly holds the file. Retry once.
            time.sleep(0.05)
            tmp_path.replace(path)
    except BaseException:
        # Clean up the tempfile if we never replaced.
        with contextlib.suppress(OSError):
            if tmp_path.exists():
                tmp_path.unlink()
        raise


# ── high-level write_corpusignore ─────────────────────────────────────


@dataclass(frozen=True)
class CorpusignoreWriteResult:
    """Outcome of a single :func:`write_corpusignore` call."""

    path: Path
    created: bool
    corrupted: bool = False
    backup_path: Path | None = None


def _make_backup_path(target: Path) -> Path:
    """Pick a unique ``.corpusignore.bak.<ts>`` path next to ``target``."""
    ts = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    candidate = target.with_name(f"{target.name}.bak.{ts}")
    # Disambiguate across same-second writes by adding a numeric suffix.
    counter = 1
    while candidate.exists():
        candidate = target.with_name(f"{target.name}.bak.{ts}.{counter}")
        counter += 1
    return candidate


def write_corpusignore(
    root: Path,
    features: dict[str, bool],
    *,
    backup_corrupted: bool = True,
) -> CorpusignoreWriteResult:
    """Render the managed block and persist it under ``root/.corpusignore``.

    Args:
        root: Directory that gains a ``.corpusignore`` at its top.
        features: Feature-flag dict (see
            :func:`corpus_forge.ignore_defaults.feature_flags_from_config`).
        backup_corrupted: When True (default) a malformed existing file
            is moved aside as ``.corpusignore.bak.<ts>`` and the file is
            rewritten. When False, the
            :class:`ManagedBlockCorrupted` exception is re-raised.

    Returns:
        :class:`CorpusignoreWriteResult` summarising what happened.
    """
    root.mkdir(parents=True, exist_ok=True)
    target = root / ".corpusignore"
    new_block = render_managed_block(features)

    existed = target.exists()
    existing_text = target.read_text(encoding="utf-8") if existed else ""

    backup_path: Path | None = None
    corrupted = False
    try:
        new_text = splice_managed_block(existing_text, new_block)
    except ManagedBlockCorrupted:
        corrupted = True
        if not backup_corrupted:
            raise
        # Move the broken file aside and rewrite from scratch (no user
        # lines to preserve — they're untrusted now).
        backup_path = _make_backup_path(target)
        target.replace(backup_path)
        new_text = new_block

    atomic_write_text(target, new_text)
    return CorpusignoreWriteResult(
        path=target,
        created=not existed,
        corrupted=corrupted,
        backup_path=backup_path,
    )


# ── data-root discovery + resync_all ──────────────────────────────────


# FS-style plugins whose root corresponds to a directory the user
# actually browses. Chat-style plugins (claude_code, opencode) store
# in daemon log dirs that the user does NOT curate, so skip them. Wave
# 4 will add ``zotero`` to this allow-list once that source ships.
_FS_PLUGINS: frozenset[str] = frozenset({"filesystem", "markdown_vault"})


def _source_root(source) -> Path | None:
    """Return the on-disk root for an FS-style ``DatasetSourceConfig``."""
    if source.plugin not in _FS_PLUGINS:
        return None
    if source.plugin == "filesystem" and source.root:
        return Path(source.root)
    if source.plugin == "markdown_vault" and source.vault_root:
        return Path(source.vault_root)
    return None


def discover_data_roots(cfg: Config) -> list[Path]:
    """Return the deduplicated list of FS-style data roots in ``cfg``.

    Order is preserved (first occurrence wins on dedupe).
    """
    seen: set[Path] = set()
    roots: list[Path] = []
    for dataset in cfg.datasets:
        for source in dataset.sources:
            root = _source_root(source)
            if root is None:
                continue
            if root in seen:
                continue
            seen.add(root)
            roots.append(root)
    return roots


def _resolve_global_path() -> Path:
    """Resolve the on-disk path of the global ignore file.

    Mirrors :mod:`corpus_forge.ignore`'s resolution order: the
    ``CF_GLOBAL_IGNORE_FILE`` env var wins; otherwise default to
    ``~/.config/corpus-forge/ignore``.
    """
    env_val = os.environ.get("CF_GLOBAL_IGNORE_FILE")
    if env_val:
        return Path(env_val).expanduser()
    return Path.home() / ".config" / "corpus-forge" / "ignore"


def resync_all(cfg: Config, *, also_global: bool = False) -> list[Path]:
    """Resync every FS-style root's ``.corpusignore`` (+ optionally global).

    Returns the list of paths actually written. Existing user lines
    outside the sentinels are preserved per :func:`write_corpusignore`.
    """
    features = feature_flags_from_config(cfg)
    written: list[Path] = []
    for root in discover_data_roots(cfg):
        result = write_corpusignore(root, features)
        written.append(result.path)
    if also_global:
        global_path = _resolve_global_path()
        global_path.parent.mkdir(parents=True, exist_ok=True)
        # The global file is treated as a single managed-block file; no
        # user lines to preserve — but we still splice so the user CAN
        # add their own lines around the sentinels manually.
        existing = global_path.read_text(encoding="utf-8") if global_path.exists() else ""
        try:
            new_text = splice_managed_block(existing, render_managed_block(features))
        except ManagedBlockCorrupted:
            backup = _make_backup_path(global_path)
            global_path.replace(backup)
            new_text = render_managed_block(features)
        atomic_write_text(global_path, new_text)
        written.append(global_path)
    return written
