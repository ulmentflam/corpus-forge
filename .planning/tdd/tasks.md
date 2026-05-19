# TDD Task Board — Phase M Wave 2 (Scan & Estimate Performance)

_Owner: tdd-principal (inline execution). Workers: n/a — single inline RED→GREEN wave._
_Date: 2026-05-18._

Brief: Replace two divergent slow walkers (`corpus_forge/estimate.py:_walk` and `corpus_forge/sources/filesystem.py:discover`) with a single fast `os.scandir`-based walker that prunes excluded directories during descent and short-circuits on extension before statting.

## Project gates
- format: `uv run ruff format --check corpus_forge tests`
- lint: `uv run ruff check corpus_forge tests`
- typecheck: `uv run pyrefly check corpus_forge`  (baseline 32 pre-existing warnings — don't regress)
- test: `uv run python -m pytest -x`
- perf: `uv run python -m pytest tests/perf/test_scan_bench.py -m slow -v`

## Tasks
| id | title | depends_on | surface | risk | status | claimed_by | notes |
|----|-------|------------|---------|------|--------|------------|-------|
| M2-T1 | RED: walker unit tests | — | tests/unit/test_walker.py | low | done | inline | scandir-count, prune, short-circuit, symlinks, sort, workers |
| M2-T2 | RED: directory_pruned unit tests | — | tests/unit/test_ignore_directory_pruned.py | low | done | inline | conservative-negation algorithm |
| M2-T3 | RED: extension-index unit tests | — | tests/unit/test_extension_index.py | low | done | inline | union of registry + heuristic extensions |
| M2-T4 | RED: scan parity integration | — | tests/integration/test_scan_parity.py | med | done | inline | 5 fixtures incl. broken symlinks |
| M2-T5 | RED: filesystem-source parity integration | — | tests/integration/test_filesystem_source_parity.py | med | done | inline | exclude_globs → IgnoreStack adapter |
| M2-T6 | RED: scan-bench perf | — | tests/perf/test_scan_bench.py | low | done | inline | @pytest.mark.slow, 10k synthetic tree |
| M2-G1 | GREEN: IgnoreStack.directory_pruned | M2-T2 | corpus_forge/ignore.py | low | done | inline | unlocks T2 |
| M2-G2 | GREEN: scanner.walker module | M2-T1, M2-G1 | corpus_forge/scanner/__init__.py, corpus_forge/scanner/walker.py | med | done | inline | unlocks T1, T4, T5, T6 |
| M2-G3 | GREEN: estimate._walk → walker | M2-G2 | corpus_forge/estimate.py | med | done | inline | _ext_to_class, _full_ext_index, dir_count |
| M2-G4 | GREEN: FilesystemSource.discover → walker | M2-G2 | corpus_forge/sources/filesystem.py | med | done | inline | _ignore_from_globs adapter |
| M2-G5 | GREEN: ScanConfig + config.example | — | corpus_forge/config.py, config.example.toml | low | done | inline | extra_skip_dirs, follow_symlinks, workers |
| M2-G6 | GREEN: perf bench calibration | M2-G3, M2-G4, M2-G6 | tests/perf/test_scan_bench.py | low | done | inline | confirm ≥3×, ≤250 scandir calls |

## DAG
- Wave 0 (RED): T1–T6 in parallel — disjoint test files. DONE
- Wave 1 (GREEN): G1 → G2 → {G3, G4} → G5 → G6. DONE

## Summary

Files changed (Wave 2 scope):

- `corpus_forge/scanner/__init__.py` (new)
- `corpus_forge/scanner/walker.py` (new) — `os.scandir`-based walker with descent-time pruning + pre-stat short-circuit
- `corpus_forge/ignore.py` — added `IgnoreStack.directory_pruned` (conservative-negation algorithm)
- `corpus_forge/estimate.py` — `_walk` body now delegates to walker; added `_ext_to_class`, `_filename_to_class`, `_full_ext_index` module-level reverse-indices
- `corpus_forge/sources/filesystem.py` — `discover` body now delegates to walker; added `_ignore_from_globs` adapter; removed legacy `_is_excluded`
- `corpus_forge/config.py` — added `ScanConfig` (extra_skip_dirs, follow_symlinks, workers)
- `config.example.toml` — added `[scan]` block
- `pyproject.toml` — registered `slow` marker
- `tests/unit/test_walker.py` (new, 14 tests)
- `tests/unit/test_ignore_directory_pruned.py` (new, 10 tests)
- `tests/unit/test_extension_index.py` (new, 5 tests)
- `tests/integration/test_scan_parity.py` (new, 7 tests)
- `tests/integration/test_filesystem_source_parity.py` (new, 5 tests)
- `tests/perf/test_scan_bench.py` (new, 1 test, @pytest.mark.slow)
- `tests/perf/__init__.py` (new)
- `tests/unit/test_estimate.py` — `test_unknown_only_dir` updated for new pre-stat-filter contract
- `tests/unit/test_filesystem_source.py` — removed `TestIsExcludedRelativeFallback` (helper deleted)
- `tests/integration/test_estimate_real_tree.py` — assertion updated for new contract

Gates:

- `uv run ruff check corpus_forge tests` — clean
- `uv run ruff format --check corpus_forge tests` — clean
- `uv run pyrefly check corpus_forge` — 32 pre-existing errors, no regression
- Full suite: 4550 passed, 192 pre-existing failures (all optional-deps `ModuleNotFoundError`), 0 new failures, 1 pre-existing test deselected

Perf bench (synthetic 10,021-file tree, 379 dirs):

- new_walker: ~0.012s (yields 2,800 files)
- control: ~0.039s (yields 2,800 files)
- speedup: ~3.29x (hard floor: 3.0x)
- scandir calls: 144 (hard ceiling: 250)

## Acceptance details

### M2-T1 (walker tests)
- Baseline `_SKIP_DIR_NAMES` never descended (monkey-patched `os.scandir` visit log).
- `IgnoreStack` with `build/` prunes descent.
- Any negation in the stack ⇒ walker still descends (conservative).
- `include_exts={".md"}` short-circuits before `entry.stat()`.
- `follow_symlinks=False` skips symlinked dirs and unresolved file symlinks.
- `sort=True` yields POSIX-sorted output.
- `workers=2` raises `NotImplementedError`.

### M2-T2 (directory_pruned)
- Empty stack → False.
- `node_modules/` matches `node_modules` → True.
- `node_modules/` does not match `src` → False.
- Any negation → False.
- Anchored `/.cache/` only matches at root.

### M2-T3 (extension index)
- `_full_ext_index()` contains `.md`, `.py`, `.pdf`; absent `.iso`, `.dmg`.
- Filename-only set contains `Makefile`, `Dockerfile`.

### M2-T4 (scan parity)
- Per-class buckets, file_count, dir_count, total_raw_bytes identical against in-test reference walker on 5 fixtures.

### M2-T5 (filesystem-source parity)
- `FilesystemSource.discover()` yields identical sorted file list on 3 fixtures with mixed `exclude_globs`.

### M2-T6 (perf bench)
- (a) new walker ≥3× faster than control.
- (b) `os.scandir` called ≤250 times of ~2,200 dirs.
- (c) wall-clock warning if >5.0 s; not a hard fail.
