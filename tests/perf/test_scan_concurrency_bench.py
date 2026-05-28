"""CW3-T1 -- Concurrent scanner walk micro-benchmark (RED).

Synthetic deep tree with artificial per-readdir latency (monkeypatched
`os.scandir` sleep) to prove that concurrent enumeration overlaps I/O
and finishes in meaningfully less wall time than serial.

The two latency-injected timing tests are marked `@pytest.mark.slow`
(excluded from default CI; run explicitly with
`uv run pytest tests/perf/test_scan_concurrency_bench.py -m slow`).
The third test (file-set parity, no sleep) is fast and runs by default
as a lightweight correctness smoke for the concurrent path.

Design notes:
  - Each `os.scandir` call sleeps a fixed delay (SLEEP_PER_DIR seconds)
    to simulate iCloud / NFS per-readdir latency.  With serial execution,
    total time ~= N_DIRS * SLEEP_PER_DIR.  With concurrent execution and
    workers=W, expected time ~= N_DIRS / W * SLEEP_PER_DIR (ideal).
  - We assert concurrent_time < serial_time * SPEEDUP_FRACTION.  We use
    a generous fraction (0.6 = must be at least 40% faster) with the
    generous SLEEP_PER_DIR to make the test robust on slow/loaded machines.
  - File-set parity is also asserted so the benchmark doubles as a
    correctness smoke test.

Tree shape: DEPTH levels of branching with BRANCH_FACTOR dirs per level.
Total dirs ~= BRANCH_FACTOR^DEPTH.  With DEPTH=3, BRANCH_FACTOR=3 that's
27 leaf dirs + intermediate dirs ~= 40 total dirs scanned.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

# -- Tuning constants -------------------------------------------------------

# Artificial sleep per os.scandir call (seconds).
# 40 ms * 40 dirs = 1.6 s serial; with workers=4 target < 1.6 * 0.6 = 0.96 s.
SLEEP_PER_DIR = 0.04

# Tree dimensions.
DEPTH = 3
BRANCH_FACTOR = 3  # 3^3 = 27 leaf dirs; ~40 total dirs with intermediate levels.
FILES_PER_DIR = 5  # files to plant in each leaf dir.

# Assertion: concurrent time must be < serial time * this fraction.
# 0.6 means "concurrent must be at least 40% faster than serial".
# Generous to survive loaded CI machines.
SPEEDUP_FRACTION = 0.6

# Absolute time budget: concurrent must finish within this many seconds.
# Safety net for pathological cases (e.g. workers > dirs -> overhead only).
ABS_CONCURRENT_BUDGET_S = 5.0

# Workers count for the concurrent run.
CONCURRENT_WORKERS = 4


# -- Tree builder -----------------------------------------------------------


def _build_tree(root: Path) -> int:
    """Build a DEPTH-level tree with BRANCH_FACTOR branches each level.

    Returns the total number of dirs created (approx -- includes root).
    """
    n_dirs = 0

    def _recurse(current: Path, level: int) -> None:
        nonlocal n_dirs
        if level >= DEPTH:
            # Leaf: plant files.
            for i in range(FILES_PER_DIR):
                (current / f"file_{i:02d}.md").write_text(f"content {i}")
            return
        for b in range(BRANCH_FACTOR):
            child = current / f"branch_{b}_L{level}"
            child.mkdir()
            n_dirs += 1
            _recurse(child, level + 1)

    _recurse(root, 0)
    return n_dirs


# -- Slowed scandir fixture -------------------------------------------------


def _make_slowed_scandir(real_scandir, sleep_s: float):  # type: ignore[no-untyped-def]
    """Return a wrapped os.scandir that sleeps ``sleep_s`` before returning."""

    def _slowed(path):  # type: ignore[no-untyped-def]
        time.sleep(sleep_s)
        return real_scandir(path)

    return _slowed


# -- Benchmark tests --------------------------------------------------------


@pytest.mark.slow
def test_concurrent_walk_faster_than_serial_with_artificial_latency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent walk must be meaningfully faster than serial when I/O is slow.

    This is the primary evidence that concurrent enumeration works:
    - Serial:     N_dirs * SLEEP_PER_DIR (sequential blocking reads).
    - Concurrent: ~N_dirs / W * SLEEP_PER_DIR (overlapped blocking reads).

    The test will FAIL (RED) until `walk(workers=N)` is implemented because
    `workers > 1` currently raises NotImplementedError.
    """
    from corpus_forge.scanner import walk

    n_dirs = _build_tree(tmp_path)
    real_scandir = os.scandir
    slowed = _make_slowed_scandir(real_scandir, SLEEP_PER_DIR)

    # -- Serial run ----------------------------------------------------------
    monkeypatch.setattr("corpus_forge.scanner.walker.os.scandir", slowed)

    t0 = time.perf_counter()
    serial_files = sorted(
        str(Path(e.path).relative_to(tmp_path).as_posix())
        for e in walk(tmp_path, workers=1, sort=True)
    )
    serial_elapsed = time.perf_counter() - t0

    # -- Concurrent run ------------------------------------------------------
    t0 = time.perf_counter()
    concurrent_files = sorted(
        str(Path(e.path).relative_to(tmp_path).as_posix())
        for e in walk(tmp_path, workers=CONCURRENT_WORKERS, sort=True)
    )
    concurrent_elapsed = time.perf_counter() - t0

    speedup = serial_elapsed / max(concurrent_elapsed, 1e-6)
    print(
        f"\n[concurrency-bench] n_dirs={n_dirs}"
        f"\n[concurrency-bench] serial   : {serial_elapsed:.3f}s"
        f"\n[concurrency-bench] concurrent (workers={CONCURRENT_WORKERS})"
        f": {concurrent_elapsed:.3f}s"
        f"\n[concurrency-bench] speedup  : {speedup:.2f}x"
        f"\n[concurrency-bench] serial_files_count={len(serial_files)}"
        f"\n[concurrency-bench] concurrent_files_count={len(concurrent_files)}"
    )

    # -- Assertions ----------------------------------------------------------

    # (1) File-set parity -- concurrent correctness.
    assert concurrent_files == serial_files, (
        f"File-set mismatch. Extra: {set(concurrent_files) - set(serial_files)}, "
        f"Missing: {set(serial_files) - set(concurrent_files)}"
    )

    # (2) Speedup -- concurrent must be at least SPEEDUP_FRACTION * serial.
    threshold = serial_elapsed * SPEEDUP_FRACTION
    assert concurrent_elapsed < threshold, (
        f"Concurrent walk ({concurrent_elapsed:.3f}s) not fast enough vs serial "
        f"({serial_elapsed:.3f}s). Expected < {threshold:.3f}s "
        f"(= serial * {SPEEDUP_FRACTION}). "
        f"Speedup: {speedup:.2f}x "
        f"(target: > {1 / SPEEDUP_FRACTION:.1f}x)"
    )

    # (3) Absolute wall-clock guard.
    assert concurrent_elapsed < ABS_CONCURRENT_BUDGET_S, (
        f"Concurrent walk took {concurrent_elapsed:.3f}s -- "
        f"exceeds absolute budget of {ABS_CONCURRENT_BUDGET_S}s"
    )


@pytest.mark.slow
def test_serial_baseline_with_artificial_latency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity: serial walk with slowed scandir takes roughly N_dirs * SLEEP_PER_DIR s.

    This establishes that the sleep is actually having an effect.
    The test passes with workers=1 even before the concurrent impl exists,
    confirming the harness is correctly measuring latency.
    """
    from corpus_forge.scanner import walk

    n_dirs = _build_tree(tmp_path)
    real_scandir = os.scandir
    slowed = _make_slowed_scandir(real_scandir, SLEEP_PER_DIR)

    monkeypatch.setattr("corpus_forge.scanner.walker.os.scandir", slowed)

    t0 = time.perf_counter()
    files = list(walk(tmp_path, workers=1))
    elapsed = time.perf_counter() - t0

    # The sleep-injected run must have taken at least N_dirs * SLEEP_PER_DIR.
    # This sanity check ensures the monkeypatch is working.
    # n_dirs does not count root, so actual scandir calls = n_dirs + 1 (root).
    # Allow generous floor at 50% of expected to account for tree shape variance.
    expected_min = (n_dirs + 1) * SLEEP_PER_DIR * 0.5
    assert elapsed >= expected_min, (
        f"Serial run took only {elapsed:.3f}s with n_dirs={n_dirs} "
        f"* SLEEP_PER_DIR={SLEEP_PER_DIR}; expected >= {expected_min:.3f}s. "
        "Monkeypatch may not be slowing scandir correctly."
    )

    print(f"\n[serial-baseline] n_dirs={n_dirs} elapsed={elapsed:.3f}s files={len(files)}")


def test_concurrent_walk_file_set_parity_no_sleep(tmp_path: Path) -> None:
    """Without sleep injection, concurrent walk yields same file set as serial.

    This is a lightweight correctness check that runs as part of the bench
    file even without -m slow.  It will fail RED because workers>1 raises
    NotImplementedError.
    """
    from corpus_forge.scanner import walk

    # Small tree -- no sleep needed for a quick correctness check.
    for d in range(5):
        sub = tmp_path / f"d{d}"
        sub.mkdir()
        for f in range(4):
            (sub / f"f{f}.md").write_text("x")

    serial = sorted(
        str(Path(e.path).relative_to(tmp_path).as_posix())
        for e in walk(tmp_path, workers=1, sort=True)
    )
    concurrent = sorted(
        str(Path(e.path).relative_to(tmp_path).as_posix())
        for e in walk(tmp_path, workers=CONCURRENT_WORKERS, sort=True)
    )

    assert concurrent == serial, (
        f"File-set mismatch. Extra: {set(concurrent) - set(serial)}, "
        f"Missing: {set(serial) - set(concurrent)}"
    )
