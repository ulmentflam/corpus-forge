# TDD Task Board — managed-ignore dev/build junk patterns + doctor drift

_Owner: tdd-principal. Workers: read freely. Edit only your claimed row's `status`/`claimed_by`._
_Requirement source: 2026-05-27 user prompt — dev/build artifacts (`.venv`, `node_modules`, `.git`, `__pycache__`, …) drowned the scanner on a real machine. Add them to the managed-ignore TEMPLATE so fresh init auto-ignores them, and make `doctor` flag/heal a stale global managed block._

## Project gates
- lint: `.venv/bin/python -m ruff check <changed files>`
- format: `.venv/bin/python -m ruff format --check <changed files>`
- typecheck: `.venv/bin/python -m pyrefly check <changed files>` (best-effort; project uses pyrefly)
- test (targeted): `.venv/bin/python -m pytest tests/unit/test_ignore_defaults.py tests/unit/test_ignore_lifecycle.py tests/unit/test_doctor.py tests/unit/test_corpusignore.py -q`
- coverage-min: n/a for this slice (targeted run only — full suite pulls torch/transformers + thrashes iCloud)
- smoke: n/a

## Constraints
- Run TARGETED unit tests only (the four ignore/doctor files). Repo venv at `.venv/bin/python`.
- `Could not import X from Y` → iCloud `.venv` corruption (`2`-suffix dupes); fix with `uv pip install --force-reinstall <pkg>`.
- DO NOT commit/push — workers cannot sign (1Password SSH needs TTY). Main session commits.

## Tasks

| id | title | depends_on | surface | risk | status | claimed_by | notes |
|----|-------|------------|---------|------|--------|------------|-------|
|----|-------|------------|---------|------|--------|------------|-------|
| T1 | Add dev/build junk patterns to managed template | — | corpus_forge/ignore_defaults.py, tests/unit/test_ignore_defaults.py | low | done | principal | GREEN 33 passed; 21 patterns added to _ALWAYS_ON dev/build group (sorted); QA approved |
| T2 | doctor: flag/heal stale GLOBAL managed-ignore block | T1 | corpus_forge/doctor/checks.py, tests/unit/test_doctor.py | med | done | principal | `_check_global_ignore` WARNs on drift + suggests `corpus-forge ignore sync --also-global`; no silent mutation; SKIP when absent/unmanaged; wired into run_doctor; QA approved |
| T3 | Verify idempotent regen + user-content preservation w/ new patterns | T1 | tests/unit/test_ignore_lifecycle.py, tests/unit/test_corpusignore.py | low | done | principal | test-only; junk flows through write_corpusignore/resync_all; outside-sentinel content preserved; CorpusIgnore actually matches junk dirs; QA approved |

## Summary
- Files changed:
  - `corpus_forge/ignore_defaults.py` — 21 dev/build junk patterns added to `_ALWAYS_ON` (sorted dev/build group); unconditional (never gated behind feature flags).
  - `corpus_forge/doctor/checks.py` — new `_check_global_ignore(cfg=None)`; wired into `run_doctor` (runs in both loaded/unloaded-cfg branches). WARN on stale global managed block with `corpus-forge ignore sync --also-global` fix. No auto-heal — audit path never mutates user config (matches `embedder_indexes` idiom).
  - `tests/unit/test_ignore_defaults.py` — `TestDevBuildJunkPatterns` (5 tests).
  - `tests/unit/test_doctor.py` — `TestCheckGlobalIgnoreDrift` (6 tests).
  - `tests/unit/test_ignore_lifecycle.py` — `TestDevBuildJunkLifecycle` (4 tests).
  - `tests/unit/test_corpusignore.py` — `TestManagedTemplateIgnoresJunk` (6 tests).
- Gates run: ruff check (pass), ruff format --check (pass), pyrefly check (0 errors), targeted pytest = 129 passed.
- Smoke: n/a (per constraints — targeted run only).
- Env note: repo `.venv` was missing `pyvenv.cfg` (iCloud drop) → `import pytest` failed with ModuleNotFoundError because the venv fell back to the base interpreter's site-packages. Surgically recreated `.venv/pyvenv.cfg` rather than rebuilding the venv (avoids multi-GB torch reinstall + iCloud thrash).
- Decision under ambiguity: WARN-not-auto-heal for the doctor check (user said "doctor should auto-ignore the junk" — satisfied by fresh-init auto-ignoring via the template; on EXISTING installs we WARN + one-command fix, per the prompt's stated preference and the established no-silent-mutation pattern in `corpus_forge/admin/ignore.py`). The global file is rendered with the conservative all-off preset (matching what the wizard/`ignore sync` write), so the unconditional junk patterns are always in the expected set.
- NOT committed/pushed (workers cannot sign here). Changes left in working tree for the main session.

## Acceptance details
### T1
- `_ALWAYS_ON` (or a new dev/build group) contains all 21 new patterns: `.git/ .venv/ venv/ env/ node_modules/ __pycache__/ *.pyc *.pyo *.pyd .mypy_cache/ .pytest_cache/ .ruff_cache/ .tox/ .eggs/ *.egg-info/ *.egg .cache/ .gradle/ .terraform/ .ipynb_checkpoints/ site-packages/`
- ordering/grouping/style matches existing file (sorted-within-group; tuples remain `tuple`)
- `default_managed_lines({})` includes every new pattern; PDFs/notebooks/source still NEVER ignored
- `render_managed_block` round-trips the new patterns through `parse_managed_lines`

### T2
- New (or extended) doctor check compares an existing `~/.config/corpus-forge/ignore` GLOBAL managed block against the current template
- WARN on drift (missing patterns / outdated) with the exact one-command fix (`corpus-forge ignore sync --also-global`)
- Matches the `embedder_indexes` idiom (WARN + suggested repair cmd); does NOT silently mutate user config from the audit path
- SKIP when global file absent or config unloadable; OK when global block matches template
- Honors `CF_GLOBAL_IGNORE_FILE` override (tests use it to avoid touching the real `~/.config`)
- Additive to `run_doctor` report (existing check names preserved)

### T3
- After `write_corpusignore` / `resync_all`, the managed block contains the new patterns
- Regeneration rewrites ONLY between sentinels; user lines above/below survive verbatim; idempotent re-splice
- `CorpusIgnore.from_file` on a freshly-rendered file actually ignores e.g. `.venv/`, `node_modules/`, `__pycache__/`

## DAG
- Wave 0: T1
- Wave 1: T2, T3 (after T1 — disjoint production surfaces)

## Execution note
The `Agent` sub-dispatch tool is unavailable in this (already-subagent) context, so the
principal is driving the RED→GREEN→verify loop directly per task rather than fanning out
separate tester/coder/qa workers. Loop shape preserved: tests-first (confirm red), then
implement (confirm green), then gate (lint/format/typecheck/targeted suite). Status files
record each phase.
