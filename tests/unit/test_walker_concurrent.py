"""CW1-T1 — Concurrent walker parity / safety suite (RED).

All tests here MUST fail until `walk(workers=N)` is implemented.
They currently fail because `workers > 1` raises `NotImplementedError`.

Contract (from .planning/tdd/tasks.md § Concurrent Scanner Walk):
  - `walk(root, workers=N)` yields the SAME set of files as `workers=1`.
  - With `sort=True`, the yield ORDER is identical to serial (per-dir
    POSIX-sorted DFS).  Concurrency parallelises `os.scandir`, NOT order.
  - `IgnoreStack` pruning is honored — no files from pruned dirs leak.
  - `WalkStats` counters (`files_yielded`, `dirs_descended`) are
    thread-safe and equal between serial and concurrent runs.
  - `workers=1` takes the existing serial path (no regression).
  - `follow_symlinks=False` (default) is preserved under concurrency.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# ── helpers ───────────────────────────────────────────────────────────────


def _make_tree(root: Path, layout: dict[str, str | None]) -> None:
    """Materialise a sparse tree under ``root``."""
    for rel, content in layout.items():
        p = root / rel
        if content is None:
            p.mkdir(parents=True, exist_ok=True)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")


def _walk_paths(root: Path, **kwargs: object) -> list[str]:
    """Return sorted relative POSIX paths yielded by `walk(root, **kwargs)`."""
    from corpus_forge.scanner import walk

    return sorted(
        str(Path(e.path).relative_to(root).as_posix())
        for e in walk(root, **kwargs)  # type: ignore[arg-type]
    )


def _walk_ordered(root: Path, **kwargs: object) -> list[str]:
    """Return ORDERED (not sorted) relative POSIX paths from `walk(root, **kwargs)`."""
    from corpus_forge.scanner import walk

    return [
        str(Path(e.path).relative_to(root).as_posix())
        for e in walk(root, **kwargs)  # type: ignore[arg-type]
    ]


# ── fixture tree ──────────────────────────────────────────────────────────

_NESTED_LAYOUT: dict[str, str | None] = {
    "root.md": "top",
    "a/a1.md": "x",
    "a/a2.py": "x",
    "a/sub/a_sub1.txt": "x",
    "a/sub/a_sub2.txt": "x",
    "b/b1.md": "x",
    "b/b2.py": "x",
    "b/sub/b_sub1.txt": "x",
    "c/c1.md": "x",
    "c/d/d1.md": "x",
    "c/d/e/deep.md": "x",
    "z/z1.md": "x",
    "z/z2.md": "x",
}


# ─────────────────────────────────────────────────────────────────────────
# File-set parity
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("workers", [4, 8])
def test_file_set_parity_workers_vs_serial(tmp_path: Path, workers: int) -> None:
    """Concurrent walk yields exactly the same file set as serial walk."""
    _make_tree(tmp_path, _NESTED_LAYOUT)

    serial = _walk_paths(tmp_path, workers=1)
    concurrent = _walk_paths(tmp_path, workers=workers)

    assert concurrent == serial, (
        f"workers={workers}: concurrent set differs from serial.\n"
        f"  serial={serial}\n"
        f"  concurrent={concurrent}"
    )


def test_file_set_parity_workers_4_deep_tree(tmp_path: Path) -> None:
    """File-set parity on a deep tree with many nested subdirs."""
    layout: dict[str, str | None] = {}
    # Build a 4-level deep tree: 3 branches x 3 dirs per level x 5 files per dir.
    for branch in ("alpha", "beta", "gamma"):
        for lvl1 in ("x", "y", "z"):
            for lvl2 in ("p", "q"):
                for lvl3 in ("m", "n"):
                    for i in range(5):
                        key = f"{branch}/{lvl1}/{lvl2}/{lvl3}/file_{i}.md"
                        layout[key] = "content"
    _make_tree(tmp_path, layout)

    serial = _walk_paths(tmp_path, workers=1)
    concurrent = _walk_paths(tmp_path, workers=4)

    assert set(concurrent) == set(serial), (
        f"Concurrent walk missing/extra files vs serial. "
        f"Extra: {set(concurrent) - set(serial)}, "
        f"Missing: {set(serial) - set(concurrent)}"
    )
    assert len(concurrent) == len(serial), (
        f"Concurrent yielded {len(concurrent)} files; serial {len(serial)}"
    )


def test_file_set_parity_workers_8_wide_tree(tmp_path: Path) -> None:
    """File-set parity on a wide tree (many files per dir, few nesting levels)."""
    layout: dict[str, str | None] = {}
    for subdir in range(20):
        for fnum in range(15):
            layout[f"dir_{subdir:02d}/file_{fnum:02d}.md"] = "x"
    _make_tree(tmp_path, layout)

    serial = _walk_paths(tmp_path, workers=1)
    concurrent = _walk_paths(tmp_path, workers=8)

    assert set(concurrent) == set(serial)
    assert len(concurrent) == len(serial)


# ─────────────────────────────────────────────────────────────────────────
# Deterministic order (sort=True)
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("workers", [4, 8])
def test_sort_true_concurrent_order_equals_serial(tmp_path: Path, workers: int) -> None:
    """With sort=True, concurrent yield order must equal serial yield order.

    The contract: concurrency parallelises os.scandir; it does NOT
    re-order the output.  Output is per-directory POSIX-sorted DFS,
    identical to workers=1.
    """
    _make_tree(tmp_path, _NESTED_LAYOUT)

    serial_order = _walk_ordered(tmp_path, sort=True, workers=1)
    concurrent_order = _walk_ordered(tmp_path, sort=True, workers=workers)

    assert concurrent_order == serial_order, (
        f"workers={workers} sort=True: order differs from serial.\n"
        f"  serial={serial_order}\n"
        f"  concurrent={concurrent_order}"
    )


def test_sort_false_concurrent_set_equals_serial_set(tmp_path: Path) -> None:
    """With sort=False, the file-set is still identical (order may differ)."""
    _make_tree(tmp_path, _NESTED_LAYOUT)

    serial = _walk_paths(tmp_path, sort=False, workers=1)
    concurrent = _walk_paths(tmp_path, sort=False, workers=4)

    # sort=False: order is non-deterministic in both modes; only set must match.
    assert set(concurrent) == set(serial)
    assert len(concurrent) == len(serial)


# ─────────────────────────────────────────────────────────────────────────
# Ignore-pruning under concurrency
# ─────────────────────────────────────────────────────────────────────────


def test_ignore_prune_venv_concurrent(tmp_path: Path) -> None:
    """IgnoreStack prunes .venv/ — no files from pruned dirs appear with workers>1."""
    from corpus_forge.ignore import CorpusIgnore, IgnoreStack

    _make_tree(
        tmp_path,
        {
            "keep.md": "x",
            "src/main.py": "x",
            # .venv — should be pruned by ignore stack
            ".venv/lib/python3.11/site-packages/foo.py": "x",
            ".venv/bin/activate": "x",
        },
    )

    ig = IgnoreStack(sets=(CorpusIgnore.from_lines([".venv/"], root=tmp_path),))

    serial = _walk_paths(tmp_path, ignore=ig, workers=1)
    concurrent = _walk_paths(tmp_path, ignore=ig, workers=4)

    # No .venv files in either run.
    assert all(".venv" not in p for p in serial), f"serial leaked .venv: {serial}"
    assert all(".venv" not in p for p in concurrent), f"concurrent leaked .venv: {concurrent}"
    # Sets match.
    assert set(concurrent) == set(serial)


def test_ignore_prune_node_modules_concurrent(tmp_path: Path) -> None:
    """IgnoreStack prunes node_modules/ — verified with workers=4."""
    from corpus_forge.ignore import CorpusIgnore, IgnoreStack

    _make_tree(
        tmp_path,
        {
            "index.js": "x",
            "src/app.js": "x",
            # node_modules — pruned by ignore stack (NOT baseline — we put it
            # OUTSIDE baseline_dirs by using a name not in _SKIP_DIR_NAMES)
            "vendor/foo/bar.js": "x",
            "vendor/baz/qux.js": "x",
        },
    )

    # Prune "vendor/" via ignore stack (simulates node_modules-style exclusion).
    ig = IgnoreStack(sets=(CorpusIgnore.from_lines(["vendor/"], root=tmp_path),))

    serial = _walk_paths(tmp_path, ignore=ig, workers=1)
    concurrent = _walk_paths(tmp_path, ignore=ig, workers=4)

    assert all("vendor" not in p for p in concurrent), (
        f"concurrent leaked vendor files: {[p for p in concurrent if 'vendor' in p]}"
    )
    assert set(concurrent) == set(serial)


def test_ignore_pruning_no_files_from_pruned_dirs_concurrent(tmp_path: Path) -> None:
    """Comprehensive: multiple ignored dirs, workers=8, NO leakage."""
    from corpus_forge.ignore import CorpusIgnore, IgnoreStack

    _make_tree(
        tmp_path,
        {
            "real/a.md": "x",
            "real/b.md": "x",
            "real/sub/c.md": "x",
            "ignored_a/x.md": "pruned",
            "ignored_a/sub/y.md": "pruned",
            "ignored_b/z.md": "pruned",
        },
    )

    ig = IgnoreStack(sets=(CorpusIgnore.from_lines(["ignored_a/", "ignored_b/"], root=tmp_path),))

    serial = _walk_paths(tmp_path, ignore=ig, workers=1)
    concurrent = _walk_paths(tmp_path, ignore=ig, workers=8)

    assert all("ignored_" not in p for p in concurrent), (
        f"Concurrent leaked ignored files: {[p for p in concurrent if 'ignored_' in p]}"
    )
    assert set(concurrent) == set(serial)
    assert len(concurrent) == 3  # only real/a.md, real/b.md, real/sub/c.md


# ─────────────────────────────────────────────────────────────────────────
# WalkStats correctness under concurrency
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("workers", [1, 4, 8])
def test_walk_stats_files_yielded_matches_len_concurrent(tmp_path: Path, workers: int) -> None:
    """WalkStats.files_yielded equals the number of files actually yielded."""
    from corpus_forge.scanner import walk
    from corpus_forge.scanner.walker import WalkStats

    _make_tree(tmp_path, _NESTED_LAYOUT)

    stats = WalkStats()
    files = list(walk(tmp_path, workers=workers, stats=stats))

    assert stats.files_yielded == len(files), (
        f"workers={workers}: stats.files_yielded={stats.files_yielded} but len(files)={len(files)}"
    )


def test_walk_stats_counts_identical_serial_vs_concurrent(tmp_path: Path) -> None:
    """WalkStats dirs_descended and files_yielded equal between workers=1 and workers=4."""
    from corpus_forge.scanner import walk
    from corpus_forge.scanner.walker import WalkStats

    _make_tree(tmp_path, _NESTED_LAYOUT)

    serial_stats = WalkStats()
    list(walk(tmp_path, workers=1, stats=serial_stats))

    concurrent_stats = WalkStats()
    list(walk(tmp_path, workers=4, stats=concurrent_stats))

    assert concurrent_stats.files_yielded == serial_stats.files_yielded, (
        f"files_yielded: serial={serial_stats.files_yielded} "
        f"concurrent={concurrent_stats.files_yielded}"
    )
    assert concurrent_stats.dirs_descended == serial_stats.dirs_descended, (
        f"dirs_descended: serial={serial_stats.dirs_descended} "
        f"concurrent={concurrent_stats.dirs_descended}"
    )


def test_walk_stats_no_double_count_under_concurrency(tmp_path: Path) -> None:
    """Thread-safe increments: files_yielded must not exceed actual file count."""
    from corpus_forge.scanner import walk
    from corpus_forge.scanner.walker import WalkStats

    # Build a tree with an exact known count.
    layout: dict[str, str | None] = {}
    expected_count = 0
    for d in range(10):
        for f in range(10):
            layout[f"dir_{d}/file_{f}.txt"] = "x"
            expected_count += 1
    _make_tree(tmp_path, layout)

    stats = WalkStats()
    yielded = list(walk(tmp_path, workers=8, stats=stats))

    assert stats.files_yielded == expected_count, (
        f"Expected {expected_count} files, stats.files_yielded={stats.files_yielded}"
    )
    assert len(yielded) == expected_count, f"Expected {expected_count} yielded, got {len(yielded)}"
    # No double-count: the two counts must agree.
    assert stats.files_yielded == len(yielded)


# ─────────────────────────────────────────────────────────────────────────
# workers=1 serial path — no regression
# ─────────────────────────────────────────────────────────────────────────


def test_workers_1_serial_path_unchanged(tmp_path: Path) -> None:
    """workers=1 must behave identically to before (no pool, same output)."""
    _make_tree(tmp_path, _NESTED_LAYOUT)

    result = _walk_paths(tmp_path, workers=1)
    # Must include all real files.
    assert "root.md" in result
    assert "a/a1.md" in result
    assert "c/d/e/deep.md" in result
    assert len(result) == len(_NESTED_LAYOUT)


def test_workers_1_does_not_raise(tmp_path: Path) -> None:
    """workers=1 must never raise (existing behavior)."""
    from corpus_forge.scanner import walk

    (tmp_path / "x.md").write_text("hi")
    # Must not raise anything.
    files = list(walk(tmp_path, workers=1))
    assert len(files) == 1


def test_workers_greater_than_one_is_now_supported(tmp_path: Path) -> None:
    """workers > 1 must NOT raise NotImplementedError (NEW contract).

    This test replaces test_workers_greater_than_one_raises in test_walker.py
    which pinned the OLD contract.
    """
    from corpus_forge.scanner import walk

    (tmp_path / "x.md").write_text("hi")
    # Must succeed — no NotImplementedError.
    files = list(walk(tmp_path, workers=2))
    assert len(files) == 1


# ─────────────────────────────────────────────────────────────────────────
# Symlink safety under concurrency
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks require admin on Windows CI")
def test_follow_symlinks_false_skips_symlinked_dir_concurrent(tmp_path: Path) -> None:
    """Symlinked dirs are still skipped with workers>1 when follow_symlinks=False."""
    real = tmp_path / "real"
    real.mkdir()
    (real / "inside.md").write_text("x")
    (tmp_path / "linked").symlink_to(real, target_is_directory=True)
    (tmp_path / "top.md").write_text("y")

    serial = _walk_paths(tmp_path, follow_symlinks=False, workers=1)
    concurrent = _walk_paths(tmp_path, follow_symlinks=False, workers=4)

    # Symlinked dir must not be descended in either run.
    leaked = [p for p in concurrent if p.startswith("linked/")]
    assert all(not p.startswith("linked/") for p in concurrent), (
        f"Concurrent descended into symlinked dir: {leaked}"
    )
    assert set(concurrent) == set(serial)


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks require admin on Windows CI")
def test_follow_symlinks_false_skips_symlinked_file_concurrent(tmp_path: Path) -> None:
    """Symlinked files are skipped with workers>1 when follow_symlinks=False."""
    (tmp_path / "real.md").write_text("x")
    (tmp_path / "sym.md").symlink_to(tmp_path / "real.md")

    concurrent = _walk_paths(tmp_path, follow_symlinks=False, workers=4)

    # Only real.md yielded; sym.md skipped.
    assert "real.md" in concurrent
    assert "sym.md" not in concurrent


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks require admin on Windows CI")
def test_follow_symlinks_false_matches_serial_with_symlinks_concurrent(tmp_path: Path) -> None:
    """With symlinks in tree, concurrent and serial produce identical sets."""
    real_dir = tmp_path / "real_dir"
    real_dir.mkdir()
    (real_dir / "file.md").write_text("x")
    (tmp_path / "linked_dir").symlink_to(real_dir, target_is_directory=True)
    (tmp_path / "top.md").write_text("y")

    serial = _walk_paths(tmp_path, follow_symlinks=False, workers=1)
    concurrent = _walk_paths(tmp_path, follow_symlinks=False, workers=4)

    assert set(concurrent) == set(serial)
