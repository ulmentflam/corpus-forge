"""Phase M Wave 2 — `scanner.walker.walk` synthetic-scale benchmark.

Generates ~10,000 files in a `tmp_path`:

  - ~6,200 inside baseline-skip dirs (`.git/`, `node_modules/`,
    `__pycache__/`) — the walker MUST never `scandir` into these.
  - ~2,800 ordinary `.md`/`.py`/`.txt` files distributed across a
    moderately-deep tree.
  - 1,000 large-extension binaries (`.iso` / `.dmg`) that the include_exts
    short-circuit MUST reject before `entry.stat()`.

Hard assertions:

  (a) The new walker is at least 3x faster than a control walker
      (re-implemented inline matching the legacy `rglob + post-filter`
      shape on the SAME tree).
  (b) `os.scandir` was called on no more than ~250 directories of the
      ~380 directories actually created.

Warning, not hard fail:

  (c) Wall-clock target — log a warning if > 5.0 s on the synthetic tree.
"""

from __future__ import annotations

import os
import time
import warnings
from pathlib import Path
from statistics import median

import pytest

pytestmark = pytest.mark.slow


# ─────────────────────────────────────────────────────────────────────────
# Tree generation
# ─────────────────────────────────────────────────────────────────────────


def _build_synthetic_tree(root: Path) -> tuple[int, int]:
    """Materialise the ~10k-file tree. Returns (n_files, n_dirs)."""
    n_files = 0
    n_dirs = 0

    # Baseline-skip noise: simulate node_modules / .git / __pycache__.
    # About 6,200 files distributed across ignored package/cache directories.
    pkgs = (
        "alpha",
        "beta",
        "gamma",
        "delta",
        "epsilon",
        "zeta",
        "eta",
        "theta",
        "iota",
        "kappa",
        "lambda",
        "mu",
        "nu",
        "xi",
        "omicron",
        "pi",
        "rho",
        "sigma",
        "tau",
        "upsilon",
    )
    for shard in pkgs:
        for sub in ("a", "b", "c", "d", "e", "f", "g"):
            d = root / "node_modules" / shard / sub
            d.mkdir(parents=True, exist_ok=True)
            n_dirs += 1
            for i in range(25):
                (d / f"f{i:02d}.js").write_text("x")
                n_files += 1
    git = root / ".git"
    git.mkdir(parents=True, exist_ok=True)
    n_dirs += 1
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    n_files += 1
    git_objects = git / "objects"
    git_objects.mkdir()
    n_dirs += 1
    for prefix in (
        "aa",
        "bb",
        "cc",
        "dd",
        "ee",
        "ff",
        "gg",
        "hh",
        "ii",
        "jj",
        "kk",
        "ll",
        "mm",
        "nn",
        "oo",
        "pp",
        "qq",
        "rr",
        "ss",
        "tt",
    ):
        sub = git_objects / prefix
        sub.mkdir()
        n_dirs += 1
        for j in range(60):
            (sub / f"obj-{j:03d}").write_bytes(b"x" * 32)
            n_files += 1
    for sub_name in ("refs", "hooks", "logs", "info"):
        d = git / sub_name
        d.mkdir()
        n_dirs += 1
        for k in range(30):
            (d / f"file-{k}").write_text("x")
            n_files += 1

    py_caches = root / "src"
    py_caches.mkdir(parents=True, exist_ok=True)
    n_dirs += 1
    for s in range(70):
        sd = py_caches / f"pkg{s:02d}"
        sd.mkdir()
        n_dirs += 1
        for m in range(20):
            (sd / f"m{m:02d}.py").write_text("x = 1\n")
            n_files += 1
        cache = sd / "__pycache__"
        cache.mkdir()
        n_dirs += 1
        for m in range(20):
            (cache / f"m{m:02d}.cpython-313.pyc").write_text("x")
            n_files += 1

    # Real prose / data — distributed across many shallow dirs.
    docs = root / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    n_dirs += 1
    for s in range(70):
        d = docs / f"chapter{s:02d}"
        d.mkdir()
        n_dirs += 1
        for m in range(20):
            (d / f"sec{m:02d}.md").write_text(f"# section {m}\n\nbody\n")
            n_files += 1

    # 1,000 large-extension binaries that include_exts must reject
    # pre-stat. Bigger pile here = bigger pre-stat-short-circuit win.
    blobs = root / "blobs"
    blobs.mkdir()
    n_dirs += 1
    for i in range(500):
        (blobs / f"image{i:03d}.iso").write_bytes(b"\x00" * 16)
        n_files += 1
    for i in range(500):
        (blobs / f"disk{i:03d}.dmg").write_bytes(b"\x00" * 16)
        n_files += 1

    return n_files, n_dirs


# ─────────────────────────────────────────────────────────────────────────
# Control walker — pre-Wave-2 shape with no short-circuiting
# ─────────────────────────────────────────────────────────────────────────


def _control_walk(root: Path, include_exts: frozenset[str]) -> tuple[int, int, int]:
    """Pre-Wave-2 filesystem discovery, with output filters applied afterward.

    ``FilesystemSource.discover`` at ``d226c7c^`` used ``rglob("*")``
    followed by per-file exclusion. Unlike the old estimator, it did not
    prune directories. Match the new walker's output without giving the
    control the descent-time optimization this benchmark measures.
    """
    from corpus_forge.estimate import _SKIP_DIR_NAMES, _SKIP_FILE_NAMES

    file_count = 0
    dir_count = 0
    total_bytes = 0
    for entry in root.rglob("*"):
        if entry.is_dir():
            dir_count += 1
            continue
        if not entry.is_file() or entry.is_symlink():
            continue
        rel = entry.relative_to(root)
        if any(part in _SKIP_DIR_NAMES for part in rel.parts[:-1]):
            continue
        if entry.name in _SKIP_FILE_NAMES or entry.name.startswith("._"):
            continue
        st = entry.stat()
        if entry.suffix.lower() not in include_exts:
            continue
        file_count += 1
        total_bytes += st.st_size
    return file_count, dir_count, total_bytes


# ─────────────────────────────────────────────────────────────────────────
# Bench
# ─────────────────────────────────────────────────────────────────────────


def test_walker_perf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from corpus_forge.estimate import _full_ext_index
    from corpus_forge.scanner import walk

    n_files, n_dirs = _build_synthetic_tree(tmp_path)

    include_exts = _full_ext_index()

    def consume_new() -> tuple[int, int]:
        count = 0
        size = 0
        for entry in walk(tmp_path, include_exts=include_exts):
            count += 1
            size += entry.stat.st_size
        return count, size

    # Warm both paths, then alternate order to limit cache/order bias.
    ctrl_files, ctrl_dirs, ctrl_bytes = _control_walk(tmp_path, include_exts)
    new_count, new_bytes = consume_new()
    assert (new_count, new_bytes) == (ctrl_files, ctrl_bytes)
    assert ctrl_dirs == sum(p.is_dir() for p in tmp_path.rglob("*"))

    control_samples: list[float] = []
    new_samples: list[float] = []
    for sample in range(7):
        operations = [
            (lambda: _control_walk(tmp_path, include_exts), control_samples),
            (consume_new, new_samples),
        ]
        if sample % 2:
            operations.reverse()
        for operation, timings in operations:
            started = time.perf_counter()
            operation()
            timings.append(time.perf_counter() - started)
    ctrl_elapsed = median(control_samples)
    new_elapsed = median(new_samples)

    # Count visits separately so instrumentation cannot penalize one timer.
    real_scandir = os.scandir
    scandir_calls: list[Path] = []

    def _counting_scandir(path):  # type: ignore[no-untyped-def]
        scandir_calls.append(Path(path))
        return real_scandir(path)

    with monkeypatch.context() as patch:
        patch.setattr("corpus_forge.scanner.walker.os.scandir", _counting_scandir)
        assert consume_new() == (ctrl_files, ctrl_bytes)

    from corpus_forge.estimate import _SKIP_DIR_NAMES

    assert all(
        not any(part in _SKIP_DIR_NAMES for part in path.relative_to(tmp_path).parts)
        for path in scandir_calls
    )
    assert len(scandir_calls) < ctrl_dirs

    # ── (a) speedup ─────────────────────────────────────────────────────
    # Ratio is control_elapsed / new_elapsed. Hard floor at 3x.
    ratio = ctrl_elapsed / new_elapsed if new_elapsed > 0 else float("inf")
    print(
        f"\n[scan-bench] n_files={n_files} n_dirs={n_dirs}"
        f"\n[scan-bench] new_walker: {new_elapsed:.3f}s  files_yielded={new_count}"
        f"\n[scan-bench] control:    {ctrl_elapsed:.3f}s  files_yielded={ctrl_files}"
        f"\n[scan-bench] speedup:    {ratio:.2f}x"
        f"\n[scan-bench] scandir_calls: {len(scandir_calls)}"
    )
    assert ratio >= 3.0, (
        f"new walker only {ratio:.2f}x faster than control "
        f"(new={new_elapsed:.3f}s ctrl={ctrl_elapsed:.3f}s); target ≥3x"
    )

    # ── (b) scandir call budget ────────────────────────────────────────
    # ~380 dirs created; the walker should descend ≤250 of them after
    # baseline + include_exts pruning. Generous margin for env variance.
    # The real distribution: root + src + 70 pkg + docs + 70 chapter +
    # blobs ≈ 144 expected — well under the 250 budget.
    assert len(scandir_calls) <= 250, (
        f"scandir called {len(scandir_calls)} times — expected ≤ 250 (of {n_dirs} created)"
    )

    # ── (c) wall-clock warning ─────────────────────────────────────────
    if new_elapsed > 5.0:
        warnings.warn(
            f"scanner.walker.walk took {new_elapsed:.2f}s on synthetic 10k-file tree "
            "(soft target: <5.0s)",
            stacklevel=1,
        )

    # Both algorithms must return the same files and byte totals.
    assert new_count == ctrl_files, (
        f"new walker yielded {new_count} files; control yielded {ctrl_files}"
    )
    assert new_bytes == ctrl_bytes
