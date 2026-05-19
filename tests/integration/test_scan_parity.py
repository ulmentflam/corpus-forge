"""Phase M Wave 2 — `estimate._walk` parity vs pre-Wave-2 reference walker.

Captures the legacy `_walk` shape inline as a reference so this test
survives deletion of the original code. Five fixture trees exercise:

  (a) trivial flat (5 files);
  (b) mixed text/code/junk (50 files, 3 dirs);
  (c) deep `.git`-heavy clone-like tree (10 plumbing dirs + 20 real files);
  (d) tree with `.corpusignore` containing dir patterns AND a negation;
  (e) tree with broken symlinks + a cycle
      (only the cycle exercised by `follow_symlinks=False`).

For each fixture we assert per-extractor-class buckets, file_count,
dir_count, and total_raw_bytes match the reference walker exactly.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from corpus_forge.config import (
    BackendConfig,
    Config,
    DaemonConfig,
    DatasetConfig,
    DatasetSourceConfig,
    EmbedderConfig,
)

# ─────────────────────────────────────────────────────────────────────────
# Reference walker — verbatim pre-Wave-2 shape, inlined so test survives
# the in-tree implementation switch.
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class _RefBucket:
    file_count: int = 0
    raw_bytes: int = 0
    files: list[str] = field(default_factory=list)


def _reference_walk(root: Path, ignore=None) -> tuple[dict[str, _RefBucket], int, int, int]:
    """Re-implementation of legacy `_walk` semantics (Path.iterdir loop).

    Phase M Wave 2 also applies a pre-stat extension filter; the
    reference walker emulates that with `_full_ext_index()` so the
    bucket counts compare apples-to-apples. Legacy `_walk` (without
    the pre-stat filter) would yield extra `unknown`-class files
    (`.iso`, `.dmg`, ...). Those files are intentionally dropped by
    the new walker — see the Phase M Wave 2 brief.
    """
    from corpus_forge.estimate import (
        _classify_extension,
        _code_filenames,
        _full_ext_index,
        _should_skip_dir,
        _should_skip_file,
    )

    full_idx = _full_ext_index()
    code_names = _code_filenames()

    buckets: dict[str, _RefBucket] = {}
    file_count = 0
    dir_count = 0
    total_raw_bytes = 0

    stack: list[Path] = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    if _should_skip_dir(name):
                        continue
                    if ignore is not None and ignore.matches(entry, is_dir=True, scan_root=root):
                        continue
                    dir_count += 1
                    stack.append(entry)
                    continue
                if not entry.is_file():
                    continue
                if _should_skip_file(name):
                    continue
                # Phase M Wave 2 — pre-stat extension/filename filter.
                suffix = Path(name).suffix.lower()
                if suffix not in full_idx and name not in code_names:
                    continue
                if ignore is not None and ignore.matches(entry, is_dir=False, scan_root=root):
                    continue
            except OSError:
                continue
            try:
                size = entry.stat().st_size
            except OSError:
                continue

            extractor_class = _classify_extension(suffix)
            if extractor_class is None:
                extractor_class = "code" if name in code_names else "unknown"

            bucket = buckets.setdefault(extractor_class, _RefBucket())
            bucket.file_count += 1
            bucket.raw_bytes += size
            bucket.files.append(str(entry.relative_to(root).as_posix()))
            file_count += 1
            total_raw_bytes += size

    return buckets, file_count, dir_count, total_raw_bytes


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────


def _embedder(name: str = "e1", dim: int = 384) -> EmbedderConfig:
    return EmbedderConfig(
        name=name,
        provider="sentence_transformers",
        model_id=f"fake/{name}",
        dimension=dim,
        active=True,
    )


def _config() -> Config:
    return Config(
        backend=BackendConfig(kind="sqlite", dsn="sqlite:///:memory:"),
        daemon=DaemonConfig(),
        datasets=[
            DatasetConfig(
                name="d1",
                kind="text",
                sources=[
                    DatasetSourceConfig(
                        plugin="filesystem",
                        root="/tmp",
                        chunker="markdown",
                    )
                ],
            )
        ],
        embedders=[_embedder()],
    )


def _compare(tmp_root: Path, ignore=None) -> None:
    """Run both walkers and diff their outputs.

    Note: the reference walker tallies `dir_count` against `iterdir`'s
    discovered directories; the new walker (which yields files only)
    must report the same count. We compare via `estimate_sync().dir_count`
    so the public contract is exercised — the new internal mechanism
    (whether stats tuple or yield) is hidden.
    """
    from corpus_forge.estimate import estimate_sync

    ref_buckets, ref_files, ref_dirs, ref_bytes = _reference_walk(tmp_root, ignore=ignore)

    est = estimate_sync(tmp_root, _config(), ignore=ignore)
    assert est.file_count == ref_files, f"file_count diverged: est={est.file_count} ref={ref_files}"
    assert est.dir_count == ref_dirs, f"dir_count diverged: est={est.dir_count} ref={ref_dirs}"
    assert est.total_raw_bytes == ref_bytes, (
        f"total_raw_bytes diverged: est={est.total_raw_bytes} ref={ref_bytes}"
    )

    # Per-class buckets
    ref_by_class = {cls: (b.file_count, b.raw_bytes) for cls, b in ref_buckets.items()}
    est_by_class = {s.extractor_class: (s.file_count, s.raw_bytes) for s in est.by_extractor}
    assert ref_by_class == est_by_class, (
        f"per-class buckets diverged:\n  est={est_by_class}\n  ref={ref_by_class}"
    )


# ─────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────


def _fixture_flat(root: Path) -> None:
    (root / "a.md").write_text("hello\n")
    (root / "b.py").write_text("print('x')\n")
    (root / "c.txt").write_text("plain\n")
    (root / "d.csv").write_text("a,b\n1,2\n")
    (root / "e.json").write_text("{}")


def _fixture_mixed(root: Path) -> None:
    """50 files across 3 dirs incl. a few junk extensions."""
    (root / "src").mkdir()
    (root / "docs").mkdir()
    (root / "misc").mkdir()
    for i in range(20):
        (root / "src" / f"mod{i}.py").write_text(f"def f{i}(): pass\n")
    for i in range(15):
        (root / "docs" / f"note{i}.md").write_text(f"# note {i}\n\nbody\n")
    for i in range(10):
        (root / "misc" / f"data{i}.csv").write_text("a,b\n1,2\n")
    # Junk that lands in 'unknown'.
    for i in range(5):
        (root / "misc" / f"blob{i}.iso").write_text("noise")


def _fixture_git_heavy(root: Path) -> None:
    """10 git-plumbing dirs + 20 real files in src/."""
    (root / "src").mkdir()
    for i in range(20):
        (root / "src" / f"f{i}.py").write_text(f"x = {i}\n")
    git = root / ".git"
    git.mkdir()
    for d in ("objects", "refs", "hooks", "info", "logs"):
        (git / d).mkdir()
    # Plumbing files (must never be visited or counted).
    for i in range(10):
        (git / "objects" / f"pack-{i:02d}.idx").write_bytes(b"\x00" * 64)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    (git / "config").write_text("[core]\n")
    # Nested plumbing dirs to push depth past trivial.
    for sub in ("aa", "bb", "cc", "dd", "ee"):
        (git / "objects" / sub).mkdir()
        (git / "objects" / sub / "deadbeef").write_bytes(b"x")


def _fixture_corpusignore_with_negation(root: Path) -> None:
    # Use a non-baseline directory name so the .corpusignore is the
    # discriminator (otherwise `build/` would be pruned by the hard-coded
    # baseline before the ignore stack is consulted).
    (root / ".corpusignore").write_text("wasm/\n!wasm/keep.md\n")
    (root / "keep.md").write_text("kept\n")
    (root / "wasm").mkdir()
    (root / "wasm" / "junk.md").write_text("junk\n")
    (root / "wasm" / "keep.md").write_text("re-included by negation\n")
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("print('hi')\n")


def _fixture_broken_symlinks(root: Path) -> None:
    (root / "real.md").write_text("hi\n")
    (root / "sub").mkdir()
    (root / "sub" / "f.py").write_text("x = 1\n")
    # Broken symlink — both walkers must skip silently.
    (root / "broken").symlink_to(root / "does_not_exist")
    # Symlink cycle: a -> b -> a. `follow_symlinks=False` means neither
    # walker dereferences, but `is_symlink()` must short-circuit.
    a = root / "loop_a"
    b = root / "loop_b"
    a.symlink_to(b)
    b.symlink_to(a)


# ─────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────


def test_parity_flat(tmp_path: Path) -> None:
    _fixture_flat(tmp_path)
    _compare(tmp_path)


def test_parity_mixed(tmp_path: Path) -> None:
    _fixture_mixed(tmp_path)
    _compare(tmp_path)


def test_parity_git_heavy(tmp_path: Path) -> None:
    _fixture_git_heavy(tmp_path)
    _compare(tmp_path)


def test_parity_corpusignore_negation(tmp_path: Path) -> None:
    from corpus_forge.ignore import IgnoreStack, load_local_ignore

    _fixture_corpusignore_with_negation(tmp_path)
    local = load_local_ignore(tmp_path)
    stack = IgnoreStack(sets=(local,))
    _compare(tmp_path, ignore=stack)


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks require admin on Windows CI")
def test_parity_broken_symlinks(tmp_path: Path) -> None:
    _fixture_broken_symlinks(tmp_path)
    _compare(tmp_path)


def test_parity_yields_progress_logger_bookends(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The Phase L Wave 4 progress bookends must still fire."""
    import logging

    _fixture_flat(tmp_path)
    from corpus_forge.estimate import estimate_sync

    with caplog.at_level(logging.INFO, logger="corpus_forge.estimate.scan"):
        estimate_sync(tmp_path, _config())
    msgs = " | ".join(r.message for r in caplog.records)
    assert "Scanning started" in msgs
    assert "Scanning complete" in msgs


def test_parity_last_scan_stats_populated(tmp_path: Path) -> None:
    """Phase L Wave 4 — module-level `_LAST_SCAN_STATS` is still set."""
    _fixture_flat(tmp_path)
    from corpus_forge.estimate import estimate_sync, get_last_scan_stats

    started = time.perf_counter()
    estimate_sync(tmp_path, _config())
    elapsed = time.perf_counter() - started
    stats = get_last_scan_stats()
    assert stats is not None
    assert stats.file_count == 5
    assert stats.elapsed_s >= 0.0
    assert stats.elapsed_s <= elapsed + 1.0  # sanity
