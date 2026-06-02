# QA Status — owned by tdd-qa (feat/corpus-agents-init)
_Append-only per task._

## Schema per entry
```
### T<id> — <title>
- reviewed_at: <ISO>
- verdict: approved | rework
- gate_replay: { ruff_format, ruff_check, pyrefly, pytest_focused, pytest_suite_shape }
- findings: [bullets]
- notes: short
```

### Wave 0–4 (principal #1) — T1, T2, T3, T5(orig), T9(orig)
- verdict: superseded
- notes: superseded by principal #3's redirect spec for T4/T5/T6/T7/T8/T9; T1/T2/T3 retained intact.

### Wave 5 (final, principal #3 self-QA) — T4..T8 + T9
- reviewed_at: 2026-06-02T01:00:00Z
- verdict: approved
- gate_replay:
  - ruff_format: PASS (806 files clean — `ruff format --check corpus_forge tests`)
  - ruff_check: PASS (`ruff check corpus_forge tests`)
  - pyrefly: PASS (`./scripts/check-pyrefly.sh corpus_forge` — 0 errors)
  - pytest_focused: 65/65 PASS (`pytest tests/unit/test_agents_* tests/unit/test_cli_agents_init.py tests/integration/test_agents_init_e2e.py`)
  - pytest_suite_shape: 5840 pass / 130 fail / 34 skip / 1 xfail (matches main baseline 5777 pass / 130 fail; +63 new tests from this branch; same fail shape — all 130 are pre-existing missing-extras failures on this venv: pymupdf, jupyter, tree_sitter, sklearn/hdbscan)
- findings:
  - Two-pass synthesizer + sanitization clauses verified.
  - Shareable result.citations gated empty (tested twice — once directly, once via E2E).
  - Root AGENTS.md never overwritten — tested 3 ways: writer-unit (`test_force_never_overwrites_root_agents_md`), cli-unit (`test_force_does_not_overwrite_root_agents_md`), E2E (`test_e2e_existing_root_agents_md_untouched_with_force`).
  - `.corpus-agents/` is appended to .gitignore once, idempotently.
  - `--no-root-write` and `--no-gitignore` both honored.
  - `--output-dir` redirects the four-file write target.
  - Smoke test: `corpus-forge agents init --help` exits 0 with all 9 documented flags visible.
  - Found + fixed: prior principal's `corpus_forge.embedders.registry.build_embedder` import was hallucinated — switched to the real `register_from_config(EmbedderRegistry(), embedder_cfg)` pattern that `cli._build_eval_retriever` uses.
- notes: feature complete; all gates green; safety contract holds.
