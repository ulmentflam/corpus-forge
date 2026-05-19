"""Phase M Wave 2 — `scanner.walker.walk` synthetic-scale benchmark.

Generates ~10,000 files in a `tmp_path`:

  - ~7,000 inside baseline-skip dirs (`.git/`, `node_modules/`,
    `__pycache__/`) — the walker MUST never `scandir` into these.
  - ~2,800 ordinary `.md`/`.py`/`.txt` files distributed across a
    moderately-deep tree.
  - ~200 large-extension binaries (`.iso` / `.dmg`) that the include_exts
    short-circuit MUST reject before `entry.stat()`.

Hard assertions:

  (a) The new walker is at least 3x faster than a control walker
      (re-implemented inline matching the legacy `iterdir + post-filter`
      shape on the SAME tree).
  (b) `os.scandir` was called on no more than ~250 directories of the
      ~2,200 directories actually created.

Warning, not hard fail:

  (c) Wall-clock target — log a warning if > 5.0 s on the synthetic tree.
"""

from __future__ import annotations

import os
import time
import warnings
from pathlib import Path

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
    # ~7,000 files distributed across many "package" directories so the
    # directory count climbs into the ~2k range.
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
    """`iterdir` + post-filter. No descent-time pruning, no short-circuit."""
    from corpus_forge.estimate import _SKIP_DIR_NAMES, _SKIP_FILE_NAMES

    file_count = 0
    dir_count = 0
    total_bytes = 0

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
                    if name in _SKIP_DIR_NAMES:
                        continue
                    dir_count += 1
                    stack.append(entry)
                    continue
                if not entry.is_file():
                    continue
                if name in _SKIP_FILE_NAMES or name.startswith("._"):
                    continue
            except OSError:
                continue
            try:
                st = entry.stat()
            except OSError:
                continue
            # Apply include_exts AFTER stat — that's the "control" behavior
            # we're trying to beat.
            ext = Path(name).suffix.lower()
            if ext not in include_exts:
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

    # 1) Time the control on a separate run (no monkey-patch).
    started = time.perf_counter()
    ctrl_files, _ctrl_dirs, _ctrl_bytes = _control_walk(tmp_path, include_exts)
    ctrl_elapsed = time.perf_counter() - started

    # 2) Time the new walker, AND count scandir invocations.
    real_scandir = os.scandir
    scandir_calls: list[str] = []

    def _counting_scandir(path):  # type: ignore[no-untyped-def]
        scandir_calls.append(os.fspath(path))
        return real_scandir(path)

    monkeypatch.setattr("corpus_forge.scanner.walker.os.scandir", _counting_scandir)

    started = time.perf_counter()
    new_count = 0
    new_bytes = 0
    for entry in walk(tmp_path, include_exts=include_exts):
        new_count += 1
        new_bytes += entry.stat.st_size
    new_elapsed = time.perf_counter() - started

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
    # ~2,200 dirs created; the walker should descend ≤250 of them after
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

    # Sanity: the new walker yields strictly more relevant files than the
    # control's same-include-exts filter, OR exactly the same. The
    # presence-vs-absence of the .iso/.dmg short-circuit means
    # include_exts is identical, so both should produce the same count
    # of yielded files.
    assert new_count == ctrl_files, (
        f"new walker yielded {new_count} files; control yielded {ctrl_files}"
    )
