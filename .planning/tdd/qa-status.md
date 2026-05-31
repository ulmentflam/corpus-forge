# QA Status — owned by tdd-qa
_Append-only per task._

| task-id | verdict | notes |
|---------|---------|-------|
| T7 | approved | All four QA gates pass on the focused surface. See "Gate runs" below for the exact command output summary. |

## Gate runs (T7)

- `uv run ruff format --check corpus_forge tests` → 778 files clean.
- `uv run ruff check corpus_forge tests` → All checks passed.
- `./scripts/check-pyrefly.sh corpus_forge` → 0 errors (73 suppressed, 105 warnings not shown).
- `uv run pytest tests/unit/test_embedder_llama_cpp.py tests/unit/test_pyproject_llama_cpp_extra.py tests/unit/test_embedder_config_llama_cpp.py tests/unit/test_embedder_register_from_config.py --no-cov` → 71 passed, 1 skipped (gated smoke).
- `uv run pytest tests/unit -k embedder --no-cov` → 415 passed, 10 skipped (optional extras: mcp / tree_sitter_language_pack / sqlite-vec), 1 pre-existing failure in `tests/unit/test_sqlite_backend.py::TestCopyReusableEmbeddings::test_returns_reused_embedder_ids_subset` (FOREIGN KEY constraint failed in corpus_forge/backends/sqlite.py:499) — reproduced on origin/main with this change reverted, NOT caused by this change.

## Out-of-scope failures (NOT introduced by this change)

The broader `pytest tests/unit -n auto` run on this venv shows 166 failures across `analyze/`, `cli_sync/`, `extractor_office/`, `cdc_stability/`, `mcp_server_enrichment/` — all stemming from missing optional extras (`hdbscan`, `bertopic`, `docling`, `mcp`) in this `uv sync --group dev`-only venv. Identical failures reproduce on `origin/main` once the optional-extra modules are imported. The CI matrix exercises these tests with `--all-extras`; this change does NOT regress any of them.
