# QA Status — owned by tdd-qa (feat/routing-sql-push)
_Append-only per task._

| task-id | verdict | notes |
|---------|---------|-------|
| T1 | approved | `pytest tests/unit/test_postgres_backend.py::TestChunksMissingEmbeddingExtensionsFilter tests/unit/test_postgres_backend.py::TestCountChunksMissingEmbeddingExtensionsFilter tests/unit/test_sqlite_backend.py::TestChunksMissingEmbeddingExtensionsFilter tests/unit/test_sqlite_backend.py::TestCountChunksMissingEmbeddingExtensionsFilter` → 25/25 passing. Existing PR-#81 baseline tests at `test_postgres_backend.py::TestChunksMissingEmbedding` and `test_sqlite_backend.py::TestChunksMissingEmbedding` still green — back-compat preserved (`extensions=None` default emits no LIKE clause). |
| T2 | approved | `pytest tests/unit/test_embed_routing_filter.py` → 9/9 passing (4 existing PR-#81 tests + 5 new bugfix tests). Regression test `test_in_memory_filter_empty_page_does_not_break_backfill` confirms a "broken" backend ignoring `extensions=` and returning a page of all-non-matching rows on iter 1 → matching rows on iter 2 → `[]` on iter 3 causes `chunks_missing_embedding` to be called >= 3 times and the iter-2 .py rows to be embedded. The very bug PR #81 introduced is now untestable except by re-introducing the `break`. |
| T3 | approved | `pytest tests/integration/test_postgres_backend_routing_filter.py tests/integration/test_sqlite_backend_routing_filter.py` → 10/10 passing (real Postgres via testcontainers + real SQLite on tmp_path). E2E smoke for both backends verifies `embeddings_<specialist>` table contains rows ONLY for .py + .ts chunk_ids; md and conversation-sourced chunks excluded. Postgres E2E also verifies `total_encoded == len(written_ids)` proving the in-memory `route_for` filter dropped zero rows when the SQL push did its job. |

## Gate summary

| gate | result | command |
|------|--------|---------|
| ruff lint | pass | `ruff check` on all touched files |
| ruff format | pass | `ruff format --check` on all touched files |
| pyrefly | pass | `PATH="$HOME/.asdf/installs/uv/0.9.28/bin:$PATH" ./scripts/check-pyrefly.sh corpus_forge` → `INFO 0 errors` |
| focused suite | pass (1 pre-existing failure) | `pytest tests/unit/test_embed_routing_filter.py tests/unit/test_embedder_routing.py tests/unit/test_postgres_backend.py tests/unit/test_sqlite_backend.py` → 295 passed, 2 skipped, 1 pre-existing failure (`TestCopyReusableEmbeddings::test_returns_reused_embedder_ids_subset` — fails identically on `main`, confirmed unrelated) |
| integration | pass | `pytest tests/integration/test_postgres_backend_routing_filter.py tests/integration/test_sqlite_backend_routing_filter.py` → 10 passed |
| broader sweep | pass for routing surface | `pytest tests/unit -k 'embed or backend or postgres or sqlite or ingest or routing or doctor'` → 1469 passed; the 5 failures all pre-existing on `main` (missing optional extras: bertopic, mcp). |

## Conclusion

This PR replaces the wheel-only hotfix with proper source code:
- SQL filter pushed into both backends, both methods.
- `embed.backfill_embedder` passes `extensions=` through and drops the broken `break`.
- 30+ new tests pin the contract; the original bug is regression-tested.
- All hard constraints from the PR description respected (no JOIN changes, no `TypeError` fallback, no public API churn, no config or ollama-dir mutations).
