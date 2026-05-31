# TDD Task Board — feat/routing-sql-push (post-PR #81 bugfix)

_Owner: tdd-principal. Workers: read freely. Edit only your claimed row's `status` and `claimed_by`._

Worktree: `/Users/evanowen/dev/cf-worktrees/feat-routing-sql-push`
Branch: `feat/routing-sql-push`
Off: `main` @ 982787a (PR #81 — extension routing landed; this PR fixes the prod regression it caused)

## The bug we're fixing (concise)

PR #81 added per-embedder `extensions` allow-list routing but only filtered in **Python** after fetching a 1000-row page from `chunks_missing_embedding`. The SQL paging is non-cursored (`WHERE e.chunk_id IS NULL ORDER BY c.id LIMIT 1000` returns the same first 1000 rows every call). If that first page has zero rows whose `source_uri` extension matches the specialist, `backfill_embedder` hits `if not chunks_needing: break` and gives up — even when tens of thousands of matching code chunks exist deeper in the table.

Prod symptom (user's box tonight): 1.88 M chunks "pending" against `nomic-code`, first 1000 are .md / chat / non-code, loop breaks, only 13 chunks ever embed.

A wheel-only hotfix is currently keeping the user's backfill running at ~3 chunks/sec. This PR lands the proper fix: push the extension allow-list into SQL (both backends, both `chunks_missing_embedding` AND `count_chunks_missing_embedding`), drop the broken `break`, keep `route_for` as defense-in-depth.

## Project gates (discovered)

- lint:        `ruff check`
- format:      `ruff format --check`
- typecheck:   `./scripts/check-pyrefly.sh corpus_forge`
- test:        `pytest -q`
- focused:     `pytest tests/unit/test_embed_routing_filter.py tests/unit/test_embedder_routing.py tests/unit/test_postgres_backend.py tests/unit/test_sqlite_backend.py`
- integration: `pytest -m integration tests/integration/test_postgres_backend_routing_filter.py tests/integration/test_sqlite_backend_routing_filter.py` (new files)
- coverage-min: 80
- smoke: (deferred — user will exercise `corpus-forge embed -e <specialist>` after reinstall)

## Hard constraints (from requirements)

- Don't touch JOIN structure of `chunks_missing_embedding` — PR #81 wired `COALESCE(d.source_uri, cv.source_uri, '')` correctly.
- Don't bump backfill page size (stay at 1000).
- Don't change `route_for` / `claims` / `EmbedderRoutingError` public API.
- Don't add the wheel hotfix's `TypeError` fallback in source — source already passes the `extensions=` kwarg directly.
- Don't touch the inline ingest writer's route filter — chunks come from the pipeline, not a paged query. Only add a one-line comment explaining why.
- All workers leave changes staged but uncommitted (1Password SSH signing needs TTY; orchestrator commits).

## Tasks

| id | title | depends_on | surface | risk | status | claimed_by | notes |
|----|-------|------------|---------|------|--------|------------|-------|
| T1 | Backend interface + SQL push for `extensions` kwarg (both backends, both methods) | — | corpus_forge/backends/base.py, corpus_forge/backends/postgres.py, corpus_forge/backends/sqlite.py, tests/unit/test_postgres_backend.py, tests/unit/test_sqlite_backend.py | med | done | principal | 25 unit tests added (17 Postgres + 8 SQLite); shared `normalize_extensions_filter` helper. |
| T2 | embed.backfill_embedder passes `extensions=` and drops the broken `break` | T1 | corpus_forge/embed.py, corpus_forge/ingest.py, tests/unit/test_embed_routing_filter.py | med | done | principal | 5 new unit tests; `break` → `continue`; ingest.py doc comment only. |
| T3 | Integration tests against real Postgres + SQLite | T1 | tests/integration/test_postgres_backend_routing_filter.py, tests/integration/test_sqlite_backend_routing_filter.py | med | done | principal | 10/10 passing (5 Postgres via testcontainers, 5 SQLite via tmp_path). E2E smokes prove SQL filter + no-op route_for. |

## Acceptance details

### T1 — Backend SQL push (the actual bug fix)

**Surface:**
- `corpus_forge/backends/base.py` — extend `StorageBackend` Protocol:
  - `chunks_missing_embedding(self, embedder_id, limit=1024, *, extensions: list[str] | None = None) -> Iterator[tuple[int, str, str]]`
  - `count_chunks_missing_embedding(self, embedder_id, *, extensions: list[str] | None = None) -> int`
- `corpus_forge/backends/postgres.py` — implement both with SQL filter.
- `corpus_forge/backends/sqlite.py` — implement both with SQL filter (mirror Postgres semantics).
- `corpus_forge/backends/postgres.py` `image_chunks_missing_embedding` — do NOT add `extensions` (image lane not in scope per requirements).

**Filter semantics (locked):**
- `None` or empty `[]` → unfiltered (preserves existing behaviour exactly — same SQL emitted).
- Non-empty → normalise each ext: `.lower()`, ensure leading dot (`"PY"` and `".PY"` and `".py"` all become `".py"`).
- Emit one SQL clause: `AND (lower(COALESCE(d.source_uri, cv.source_uri, '')) LIKE %s OR lower(COALESCE(d.source_uri, cv.source_uri, '')) LIKE %s OR ...)` with parameter values `f"%{ext}"`.
- Patterns are suffix matches (`%.py` matches `filesystem://a/foo.py`).
- COALESCE is reused exactly as PR #81 wired it — don't reorder operands.
- SQLite uses `lower()` and `LIKE`. Use `?` placeholders, not `%s`.

**Reject:** non-string entries / empty strings in the extension list — raise `ValueError("extension must be a non-empty string")`. (Defense in depth; config-layer already validates.)

**Tests (unit, mock-execute):**
- `chunks_missing_embedding(eid, extensions=None)` — SQL emitted has NO `LIKE` clause; current behaviour unchanged.
- `chunks_missing_embedding(eid, extensions=[])` — same: no LIKE.
- `chunks_missing_embedding(eid, extensions=[".py", ".ts"])` — SQL contains 2 `LIKE` clauses joined by `OR`, params include `'%.py'` and `'%.ts'`.
- Case normalisation: `extensions=["PY", ".TS", ".Md"]` → patterns `'%.py'`, `'%.ts'`, `'%.md'`.
- `extensions=["", None, 5]` → `ValueError`.
- `count_chunks_missing_embedding(eid, extensions=[".py"])` — SQL has 1 LIKE clause, param `'%.py'`.
- Existing tests at `tests/unit/test_postgres_backend.py:482` and `tests/unit/test_sqlite_backend.py:2761` MUST stay green (the `extensions=None` default must not change SQL shape).

### T2 — `embed.backfill_embedder` plumbing

**Surface:**
- `corpus_forge/embed.py` — `backfill_embedder`:
  - Compute `_ext_filter = embedder.extensions or None` once before the loop.
  - Pass `extensions=_ext_filter` to both `count_chunks_missing_embedding` (around line 155) and `chunks_missing_embedding` (around line 179).
  - **Delete the `if not chunks_needing: break` block (lines ~207-221).** Replace with `if not chunks_needing: continue`. The real end-of-stream is still `if not raw_rows: break` (line 181) — keep it.
  - Keep the in-memory `route_for(...)` check as defense-in-depth.
- `corpus_forge/ingest.py` — `_write_embeddings_for_chunks`:
  - Add a 2-3 line comment explaining why this site does NOT need the SQL filter (chunks come from per-file ingest pipeline, not a paged scan; in-memory `route_for` is sufficient at batch granularity). No code change.

**Tests (unit):**
- Add to `tests/unit/test_embed_routing_filter.py`:
  - `test_backfill_passes_extensions_kwarg_to_backend` — assert `backend.chunks_missing_embedding` and `backend.count_chunks_missing_embedding` were called with `extensions=embedder.extensions or None`.
  - `test_backfill_no_extensions_passes_none` — catchall with no `extensions` → both backend calls receive `extensions=None`.
  - `test_backfill_with_extensions_route_for_filters_nothing` — when the backend already filters (mock returns only-matching rows), `route_for` filter is a no-op (no chunk removed); processed count equals fetched count.
  - **Regression test for the bug**: `test_backfill_does_not_break_when_in_memory_filter_would_have_emptied_page` — simulate a backend that (incorrectly) ignores `extensions=` and returns a page of all-non-matching rows on iter 1, then matching rows on iter 2, then empty on iter 3. Verify backfill calls `chunks_missing_embedding` AT LEAST 3 times (does not break on iter 1) — proves the `continue` semantics.

### T3 — Integration tests against real Postgres + SQLite

**Surface:**
- `tests/integration/test_postgres_backend_routing_filter.py` (new)
- `tests/integration/test_sqlite_backend_routing_filter.py` (new)

**Per backend — seed and assert:**
1. Register a dataset; ingest 4 chunks via the backend's normal upsert path:
   - chunk 1: source `filesystem:///x/a.py`, text `"py code"`
   - chunk 2: source `filesystem:///x/a.ts`, text `"ts code"`
   - chunk 3: source `filesystem:///x/a.md`, text `"md text"`
   - chunk 4: from a conversation upsert (source `claude-code://session-1`), text `"chat"`
2. Register an embedder `nomic-code` (dim=4, fake — no model calls).
3. Assertions:
   - `chunks_missing_embedding(eid, extensions=[".py", ".ts"])` → exactly 2 rows; chunk_ids = py + ts.
   - `chunks_missing_embedding(eid, extensions=None)` → all 4.
   - `chunks_missing_embedding(eid, extensions=[".PY"])` (case normalisation) → 1 row (the .py one).
   - `count_chunks_missing_embedding(eid, extensions=[".py"])` → 1.
   - `count_chunks_missing_embedding(eid, extensions=None)` → 4.
4. **End-to-end smoke** (Postgres only — SQLite mirror optional but encouraged):
   - Register both `nomic` (catchall) and `nomic-code` (specialist `.py`/`.ts`) embedders.
   - Stub the embedder runtime (no model calls) — patch `register_from_config` to hand back a fake that returns 4-d vectors.
   - Call `backfill_embedder("nomic-code")` end-to-end.
   - Assert `embeddings_nomic_code` table contains rows ONLY for chunk_ids of `.py` + `.ts` (no `.md`, no conversation).
   - Assert the in-memory `route_for` filter dropped zero rows (instrument via spy on `route_for` or assert `len(fetched) == len(written)`).

**Use existing fixtures:**
- Postgres: see `tests/integration/conftest.py`, `test_postgres_backend_helpers.py` for the live-DSN fixture pattern.
- SQLite: in-memory or tmp file via `tmp_path`; see `test_backend_sqlite.py`.

## DAG (waves)

- **Wave 0 (parallel)**: T1-tester, T2-tester, T3-tester (the test files don't overlap; T2/T3 tests can be written against the planned (not-yet-existing) signature).
- **Wave 1**: T1-coder (implements backend SQL push to green its tests + the existing PR #81 baseline tests).
- **Wave 2 (parallel)**: T2-coder, T3-coder (T2 needs T1's signature; T3 needs T1's SQL filter to actually run). Both can run together — disjoint surfaces.
- **Wave 3 (parallel)**: T1-qa, T2-qa, T3-qa.

## Status

All tasks `done`. See `qa-status.md` for the gate-by-gate verdict.

## Summary

Files changed (source):
- `corpus_forge/backends/base.py` — added `normalize_extensions_filter()` helper + extended Protocol signatures for `chunks_missing_embedding` (kwarg) and `count_chunks_missing_embedding` (new method on Protocol).
- `corpus_forge/backends/postgres.py` — SQL push for both methods.
- `corpus_forge/backends/sqlite.py` — SQL push for both methods (added LEFT JOIN documents/conversations to the count query so its LIKE can reference COALESCE).
- `corpus_forge/embed.py` — `_ext_filter` plumbing + `break` → `continue`.
- `corpus_forge/ingest.py` — doc-only comment.

Files changed (tests):
- `tests/unit/test_postgres_backend.py` — added `TestChunksMissingEmbeddingExtensionsFilter` (7) + `TestCountChunksMissingEmbeddingExtensionsFilter` (5).
- `tests/unit/test_sqlite_backend.py` — added `TestChunksMissingEmbeddingExtensionsFilter` (8) + `TestCountChunksMissingEmbeddingExtensionsFilter` (5).
- `tests/unit/test_embed_routing_filter.py` — added 5 tests across 3 new test classes.
- `tests/integration/test_postgres_backend_routing_filter.py` — new file (5 tests).
- `tests/integration/test_sqlite_backend_routing_filter.py` — new file (5 tests).

Gates: lint ✓ format ✓ pyrefly ✓ focused suite ✓ (1 pre-existing failure on `main`, unrelated) integration ✓.

Smoke (deferred per scope): user will reinstall the wheel and run `corpus-forge embed -e nomic-code` against the live 1.88M-row corpus after this PR merges.
