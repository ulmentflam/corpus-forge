# Phase B — SQLite Backend (TDD plan)

_Owner: tdd-principal. Workers: read freely. Edit only your claimed row's `status` and `claimed_by`._

## Multi-agent coordination channel

Two parallel principal sessions are running on Phase B. Both write to this file as the source of truth. This section is the **append-only chat log** between them. Sign each message with your principal session id (the prefix of your Claude session UUID) and a UTC timestamp. Reply by appending — do not rewrite history.

### Lane assignments (live)

| lane | session | active task | next task |
|------|---------|-------------|-----------|
| **A** (this principal, lead) | `946880f9` | dispatched B-06 tester (red at `a60186e`); waiting for `sqlite.py` write-lock to dispatch B-06 coder | B-07 or B-08 if A still owns coordination by then |
| **B** (parallel) | unknown — please sign in below | B-05 coder (mid-flight; uncommitted work in `sqlite.py` and `code-status.md`) | tester-fix for the 9 B-05 bugs noted on the B-05 row |

### Messages

#### 2026-05-09T21:25Z — `946880f9` → lane B

**Subject: `sqlite.py` write-lock handoff for B-06**

Hi parallel agent. I'm running the principal lane in session `946880f9`. State as I see it:

- B-04 GREEN at `ef10675` (mine).
- B-05 RED at `eb35539` (yours, 32 tests). Your coder hit 23/32 with 9 tester-side bugs documented; needs your tester pass next.
- B-06 RED at `a60186e` (mine, 21 tests, additive `TestUpsertConversation*` classes).

**Coordination ask**: My B-06 coder needs to edit `corpus_forge/backends/sqlite.py` to add `upsert_conversation`. Your B-05 coder has uncommitted work in that same file (visible in my `git status`). I will **wait** to dispatch B-06 coder until you confirm B-05's `sqlite.py` work is committed (or you tell me you've abandoned the working-tree changes).

**Please reply by appending a message below** with:
1. Your session id.
2. ETA / status of B-05 `sqlite.py` commit.
3. Whether you also plan to claim B-07 (`write_embeddings` + `chunks_missing_embedding`) — if not, I may pick it up next since its surface is also `sqlite.py`.

In the meantime I'll stand by on `sqlite.py` and may pick up B-13 / B-14 (wiring + sync gate; disjoint files: `ingest.py`, `embed.py`, `config.py`, `daemon.py`).

— `946880f9`

#### 2026-05-09T21:45Z — `946880f9` → lane B (followup)

**Subject: takeover of `sqlite.py` lane**

Lane B looks broken (user confirmed in chat). Lane A is taking over the `sqlite.py` write-lock for now. Status:

- Your uncommitted B-05 implementation work in `corpus_forge/backends/sqlite.py` (`upsert_document` + `_copy_reusable_embeddings`) was committed at `7bc37f2` to preserve it. 23/32 B-05 tests still green; the 9 tester-side bugs you documented remain.
- Your `code-status.md` B-05 entry was committed in the same hash.
- I'm dispatching my B-06 coder next to add `upsert_conversation` on top of your B-05 changes.
- B-07 + B-08 are still `pending` — open for whichever lane resumes first. If you come back online, claim them via this file.
- The 9 B-05 tester-side bugs need a tester pass. Either lane can do it; I'll defer to you if you come back. If I see no activity from lane B after my B-06 lands, I'll claim B-05 tester-fix to unblock the count.

If you come back: append a message here with your status. Lane assignment table updated below.

### Lane assignments (live, updated)

| lane | session | active task | next task |
|------|---------|-------------|-----------|
| **A** (this principal, lead — solo) | `946880f9` (W2), new session 2026-05-12 (W3) | claiming W3: B-13 + B-14 in parallel (disjoint surface) | W4 (integration tests + dual-backend + smoke) |
| **B** (parallel) | broken — confirmed offline | (paused) | resume claim via this section when back online |

#### 2026-05-09T21:55Z — `946880f9` → lane B (B-06 green + open lanes)

B-06 GREEN. Implementation committed (forthcoming hash). 21/21 tests pass. Lane A status:
- Done: B-04, B-06.
- Owns: nothing currently active. About to pick up B-04 follow-up issue per user, then B-05 tester-fix or B-07 next.
- `sqlite.py` write-lock: temporarily released — open for either lane to grab.

When you come back online: claim via this section. The 9 B-05 tester-side bugs are still open and the natural starting point for you. B-07 + B-08 also open.

#### 2026-05-09T22:15Z — `946880f9` (lane A, solo)

Lane B confirmed broken (user). Lane A solo from here. Status:

- B-04 fix at `c33152a`: 3 real bugs in B-04 production code (name sanitization, JSON construction, FK + created_at). 18/18 still pass.
- B-05 GREEN: tester-fix pass at upcoming commit. 32/32 tests pass. Implementation untouched.
- B-06 GREEN at `948d347` (21/21).

Now: 920 unit / 102 integration / all gates clean. Next: B-07 (`write_embeddings` + `chunks_missing_embedding`).

`sqlite.py` write-lock claimed by lane A (no other lane).

#### 2026-05-09T22:30Z — `946880f9` (lane A, solo) — B-07 green

B-07 GREEN. 17/17 tests pass. Implementation in `corpus_forge/backends/sqlite.py` (additive, two methods). Commit: uncommitted (signing env issue — changes recoverable in working tree). All gates: format clean (92 files), lint clean, pyrefly 0 errors (14 suppressed), unit 937/937+8skipped/0failed, integration 102/102. Next: B-08 (`lock_source` SQLite mutex strategy).

#### 2026-05-09T23:00Z — tdd-coder — B-08 green

B-08 GREEN. 12/12 tests pass. `lock_source` implemented with `_NoCommitConn` proxy pattern + threading.Lock + `BEGIN IMMEDIATE` with exponential back-off. Key design: `_execute` calls inside lock body use the lock connection via temporary `_get_connection` instance shadowing, with commits suppressed — final COMMIT or ROLLBACK issued by lock_source on exit. All 4 gates clean: format (92 files), lint (0 errors), pyrefly (0 errors, 18 suppressed), unit 949/949+8skipped/0failed, integration 102/102.

#### 2026-05-09T23:30Z — tdd-coder — B-09 green

B-09 GREEN. 23/23 tests pass across TestDeleteDocument (4), TestDeleteConversation (3), TestFindDocument (4), TestResolveDocument (6), TestResolveSelfSource (6). Implementation: 5 additive methods in `corpus_forge/backends/sqlite.py`. All gates clean: format (92 files), lint (0 errors), pyrefly (0 errors, 18 suppressed), unit 972/972+8skipped/0failed, integration 102/102.

#### 2026-05-09T23:50Z — tdd-coder — B-12 green + W2 COMPLETE

B-12 GREEN. 10/10 tests pass (TestSetTombstone 4, TestClearTombstone 4, TestTombstoneRoundTrip 2). Implementation: 2 additive methods in `corpus_forge/backends/sqlite.py`. `set_tombstone` uses `strftime('%Y-%m-%dT%H:%M:%fZ', 'now')` for ISO-8601 with millisecond UTC; `clear_tombstone` sets `tombstoned_at = NULL`. All 4 gates clean: format (92 files), lint (0 errors), pyrefly (0 errors, 18 suppressed), unit 1013/1013+8skipped/0failed, integration 102/102.

**W2 COMPLETE** — all 9 tasks (B-04..B-12) green. sqlite.py is now feature-complete through the core CRUD, lock, lifecycle, revision, and tombstone API surface. Ready for W3 (B-13, B-14 wiring).

#### 2026-05-12T00:00Z — `946880f9` (lane A, solo) — claiming W3 (B-13 + B-14)

W2 closed at `ecdd26e` (B-04..B-12 all green). Picking up W3.

**Dispatch strategy: PARALLEL.** B-13 surface = `corpus_forge/ingest.py`, `corpus_forge/embed.py`, `tests/unit/test_ingest_*.py`, `tests/unit/test_embed_*.py`. B-14 surface = `corpus_forge/daemon.py`, `corpus_forge/config.py`, `tests/unit/test_daemon.py`, `tests/unit/test_config.py`. The two surfaces are fully disjoint at the file level. The plan-doc note "dispatch one after the other since runtime can't parallelize" is stale — we can issue concurrent Agent calls. Going parallel.

Dependency note: the plan row marks B-14 as `depends_on: B-13`, but the dependency is logical-only (validator references `backend.kind` and `dataset.sync_enabled`, both of which already exist in `config.py`). Tests for B-14 do not import anything from `ingest.py`/`embed.py`, so they can be authored and made green independently of B-13.

Plan: tester wave (parallel) → coder wave (parallel) → QA wave (parallel). QA required per board policy (resumed at B-05).

`sqlite.py` write-lock: not relevant for W3 (neither task touches `sqlite.py`).

#### 2026-05-12T00:05Z — `946880f9` (lane A, solo) — DISPATCH BLOCKED

Environment regression: this principal session has **only Read/Write/Edit/Bash tools** — the `Agent` (subagent) tool is **not in my tool inventory**. Cannot spawn `tdd-tester`, `tdd-coder`, or `tdd-qa` workers. The agent definitions exist on disk at `~/.claude/agents/tdd-{tester,coder,qa}.md` but I have no `Agent(...)` call available to invoke them.

Per the principal hard rule "you never write production code or tests yourself", I am stopping here rather than ghost-writing the code myself in violation of the role contract.

State preserved:
- Board reflects W3 claim and parallel-dispatch plan.
- Task rows B-13 and B-14 marked `claimed (dispatch blocked)` (no code touched).
- No production or test files have been modified.

To unblock: the user needs to either (a) start the next principal session in an environment with the Agent tool enabled, or (b) explicitly authorize a fused principal+worker mode for this run. Without (a) or (b), W3 stays at this checkpoint.

#### (lane B reply space — paused)

## Context

corpus-forge currently has one storage backend (`PostgresBackend`, 924 LOC) implementing the full `StorageBackend` protocol. Phase B adds a second backend, `SQLiteBackend`, so single-machine users can run corpus-forge without a Postgres server. SQLite is well-suited for the single-host use case: low setup cost, file-based, fast for sub-million-row corpora.

`Config.BackendConfig.kind` already validates `^(postgres|sqlite)$` (`config.py:38`) but `ingest.py:189` and `embed.py:21` raise `ValueError("Unsupported backend kind: sqlite")`. Phase B is the work that takes that string from validated-but-broken to actually usable.

### Out of scope (intentional)

- **Cross-host sync over SQLite.** The Active Directory Sync engine assumes a network-shared backend (Postgres). SQLite is local-by-design; pointing two `SyncEngine`s at one shared SQLite file over network FS would defeat its concurrency model. Phase B implements the revision/tombstone API on `SQLiteBackend` for protocol completeness and single-host history, but the daemon must reject `sync_enabled = true` for SQLite-backed datasets. Cross-host sync via SQLite is deferred to a hypothetical Phase D ("multi-machine sync over object storage"); Phase B does not block that.
- **HF dataset views (`002_views.sql`).** That migration uses `jsonb_build_object` / `jsonb_agg`. Translating to `json_object` / `json_group_array` is mechanical but the export path (`exports/huggingface.py`) is currently scoped to Postgres only via the `kind == "postgres"` branch. Phase B will add an equivalent SQLite view. **Out of scope** if it grows: defer view creation to Phase B.1.
- **Advanced vector indexing** (HNSW, IVF). sqlite-vec's flat virtual-table is fine for sub-million-vector corpora; we accept linear scan for nearest-neighbour queries. If perf becomes a problem, follow up with a phase to add an external indexer (FAISS, lance) — not a SQLite-internal concern.

### Scope summary

- New file: `corpus_forge/backends/sqlite.py` (~600–800 LOC, slimmer than postgres.py because no pool, no async, no LISTEN/NOTIFY).
- New schema files: `corpus_forge/schema/sqlite/001_core.sql`, `002_chunk_content_hash.sql`, `003_sync.sql` mirroring the Postgres ones.
- New optional dep: `sqlite-vec` (pinned ≥0.1) under `[project.optional-dependencies] sqlite = ["sqlite-vec>=0.1"]`.
- New tests: `tests/integration/test_backend_sqlite.py`, `tests/integration/test_migrate_sqlite.py`, plus parametrized variants of select existing integration tests.
- Updated wiring: `ingest.py:189` + `embed.py:21` route on `kind`.
- Updated docs: `docs/architecture.md`, `docs/schema.md`, `README.md` get a "single-host SQLite" section.

## Project gates (same as Phase A/C)

- lint: `uv run ruff check corpus_forge tests`
- format: `uv run ruff format --check corpus_forge tests`
- typecheck: `uv run pyrefly check corpus_forge`
- unit: `PYTHONPATH=. uv run pytest tests/unit -v --cov=corpus_forge --cov-report=term-missing --cov-fail-under=85`
- integration (Postgres + SQLite): `PYTHONPATH=. uv run pytest tests/integration -v`
- coverage-min: 85

SQLite integration tests **do not require Docker** — sqlite3 ships with Python stdlib. This is a strict win over Postgres-only integration.

## Reused primitives (do not reinvent — direct imports allowed)

- `corpus_forge.identity.chunk_content_hash` (text → sha256)
- `corpus_forge.identity.content_hash` (bytes → sha256)
- `corpus_forge.identity.advisory_lock_key` — currently produces an int for `pg_advisory_lock`; **may not apply to SQLite** (see B-08).
- `corpus_forge.backends.base.StorageBackend` — the protocol that's the contract.
- All `corpus_forge.sources.*`, `corpus_forge.chunkers.*`, `corpus_forge.embedders.*` are backend-agnostic and stay untouched.

## Tasks

| id | title | depends_on | surface | risk | status | claimed_by | notes |
|----|-------|------------|---------|------|--------|------------|-------|
| B-01 | sqlite-vec optional dep + import-guarded loader | — | `pyproject.toml`, `corpus_forge/backends/sqlite.py` (helper) | low | green | tdd-coder | Add `[project.optional-dependencies] sqlite = ["sqlite-vec>=0.1"]`. Loader applies the extension via `sqlite_vec.load(conn)` after `enable_load_extension(True)`. Tests should mark sqlite-vec tests `pytest.mark.skipif(not SQLITE_VEC_AVAILABLE)` so the test suite still works when the extra is not installed. |
| B-02 | SQLite schema files (001, 002, 003) | — | `corpus_forge/schema/sqlite/001_core.sql`, `002_chunk_content_hash.sql`, `003_sync.sql` | med | green | tdd-coder | Translations: `BIGSERIAL` → `INTEGER PRIMARY KEY AUTOINCREMENT`; `JSONB` → `TEXT` (with `json()` validator + `json_object()` for INSERT); `TIMESTAMPTZ` → `TEXT` (ISO-8601, UTC); `'{}'::jsonb` → `'{}'`; `vector(N)` → handled separately via embedding tables (see B-04). Foreign-key declarations must be present BUT SQLite requires `PRAGMA foreign_keys = ON` per connection (loader responsibility). All `IF NOT EXISTS` guards preserved for idempotency. |
| B-03 | `SQLiteBackend` skeleton + `migrate()` | B-01, B-02 | `corpus_forge/backends/sqlite.py`, `tests/unit/test_sqlite_backend.py` | med | green | tdd-coder | 29/29 tests green. Tester narrowed `test_no_postgres_backfill_sql_executed` to strip `--` inline comments before checking for sha256/encode patterns, resolving false positive from `001_core.sql` line 35 descriptive comment. |
| B-04 | `register_embedder` + per-embedder vector table | B-03 | `corpus_forge/backends/sqlite.py`, `tests/unit/test_sqlite_backend.py` | med | green | tdd-coder | 18/18 tests green. SELECT-or-INSERT on `name` UNIQUE key; UPDATE on collision; per-embedder vec0 virtual table (sqlite-vec) or BLOB fallback table. Returns int embedder_id. |
| B-05 | `upsert_document` + chunk reuse | B-04 | `corpus_forge/backends/sqlite.py`, `tests/unit/test_sqlite_backend.py` | high | green | tdd-tester | **32/32 tests green** after lane A solo tester-fix pass. Fixes: (1) `SELECT COUNT(*)` aliased to `AS count` everywhere (6 tests); (2) `test_returns_document_id_on_first_insert` — chunk arg flattened from tuple-of-list to plain list-of-tuples; (3) `test_copies_vector_from_prior_chunk_when_hash_matches` — chunk inserted first, real id captured, embedding inserted at captured id; (4) `test_multiple_chunks_share_same_prior` — filter `chunk_id != prior_chunk_id` instead of hardcoded `>= 100`; (5) `_insert_dataset_and_document` helper now uses `f"test_ds_{dataset_id}"` for UNIQUE-safe parametrization. Implementation untouched (it was correct from the start). |
| B-06 | `upsert_conversation` | B-04 | `corpus_forge/backends/sqlite.py`, `tests/unit/test_sqlite_backend.py` | med | green | tdd-coder | **21/21 tests green.** `upsert_conversation` mirrors `postgres.py` semantics: SELECT-or-UPSERT conversations row keyed on `(dataset_id, source_uri)`; replace messages + chunks on hash mismatch; per-message chunks with `conversation_id`/`message_id` set, `document_id` NULL (XOR); content_hash via `chunk_content_hash`. Coder API-overloaded post-implementation; principal preserved work + finalized bookkeeping. |
| B-07 | `write_embeddings` + `chunks_missing_embedding` | B-04 | `corpus_forge/backends/sqlite.py`, `tests/unit/test_sqlite_backend.py` | med | green | tdd-coder | 17/17 tests green. vec0 path uses DELETE+INSERT (INSERT OR REPLACE rejected by vec0); BLOB fallback uses INSERT OR REPLACE. `chunks_missing_embedding` uses NOT EXISTS subquery. `.tolist()` needed before `serialize_float32` to satisfy type stubs. All 4 gates clean. |
| B-08 | `lock_source(key)` — SQLite mutex strategy | B-03 | `corpus_forge/backends/sqlite.py`, `tests/unit/test_sqlite_backend.py` | high | green | tdd-coder | **12/12 tests green** (tdd-coder). Implementation: Python `threading.Lock` for intra-process serialization + `BEGIN IMMEDIATE` with exponential back-off for inter-process/external-writer detection. Key insight: `_execute` calls within the lock body must share the lock's `BEGIN IMMEDIATE` transaction (so rollback works); achieved via `_NoCommitConn` proxy + temporary instance-level `_get_connection` shadow during the lock body. `timeout=0` on the lock connection avoids SQLite's built-in 5-second busy handler (which would swamp lock_timeout_s); also skips `PRAGMA journal_mode = WAL` in `_open_connection` (WAL pragma requires a write lock and would deadlock if external writer is present). |
| B-09 | `delete_document`, `delete_conversation`, `find_document`, `resolve_document`, `resolve_self_source` | B-05, B-06 | `corpus_forge/backends/sqlite.py`, `tests/unit/test_sqlite_backend.py` | low | green | tdd-coder | 23/23 tests green. All 5 methods additive in sqlite.py. All 4 gates clean: format, lint, pyrefly 0 errors (18 suppressed), unit 972 passed/8 skipped/0 failed, integration 102/102. |
| B-10 | `insert_revision` with monotonic `revision_number` | B-08 | `corpus_forge/backends/sqlite.py`, `tests/unit/test_sqlite_backend.py` | high | green | tdd-coder | Inside `lock_source(source_uri)` context: `MAX(revision_number)+1` then INSERT. SQLite serializes writes via `BEGIN IMMEDIATE`, so monotonicity is guaranteed by the lock. Returns `{"id", "revision_number"}` like Postgres. 16/16 tests green. |
| B-11 | `latest_revision`, `pending_remote_revisions`, `mark_revision_pulled` | B-10 | `corpus_forge/backends/sqlite.py`, `tests/unit/test_sqlite_backend.py` | med | green | tdd-coder | 15/15 tests green. `pending_remote_revisions` JOIN syntax identical to Postgres (no dialect changes needed). `mark_revision_pulled` uses `MAX(COALESCE(..., 0), ?)` instead of `GREATEST(...)`. All 4 gates clean. |
| B-12 | `set_tombstone`, `clear_tombstone` | B-09 | `corpus_forge/backends/sqlite.py`, `tests/unit/test_sqlite_backend.py` | low | green | tdd-coder | 10/10 tests green. `set_tombstone` uses `strftime('%Y-%m-%dT%H:%M:%fZ', 'now')`; `clear_tombstone` sets NULL. Both idempotent; unknown id is no-op. All 4 gates clean: format (92 files), lint (0 errors), pyrefly (0 errors, 18 suppressed), unit 1013/1013+8skipped/0failed, integration 102/102. W2 COMPLETE. |
| B-13 | `ingest.py` + `embed.py` wiring for `kind == "sqlite"` | B-03 | `corpus_forge/ingest.py`, `corpus_forge/embed.py`, `tests/unit/test_ingest_*.py`, `tests/unit/test_embed_*.py` | low | qa-approved | tdd-qa | 30/30 wiring tests + 5/5 backfill regression tests pass. 1067 passed / 1 xfailed unit, 93.04% cov, 11/11 integration, 9/9 smoke. Postgres import in embed.py kept at module level (test patch target requires it); sqlite branch uses lazy import; migrate() added to embed.backfill_embedder (after embedder config lookup to preserve early-exit path); test_backfill_embedder_unsupported_backend + test_ingest_once_unsupported_backend updated to kind="duckdb". All 4 gates clean. **qa: approved 2026-05-12 — both deviations verified, no adjacent regressions, working tree clean.** |
| B-14 | Daemon rejects `sync_enabled = true` for SQLite | B-13 | `corpus_forge/daemon.py`, `corpus_forge/config.py` (validator), `tests/unit/test_daemon.py`, `tests/unit/test_config.py` | low | qa-approved | tdd-qa | 86 passed, 1 xfailed (scoped run); 1059 passed / 8 skipped / 1 xfailed full unit; 91.85% cov; all gates clean. `validate_sync_gate` added to `Config` — 10 previously-red tests now green, 0 regressions. Exact-message contract verified byte-for-byte against `.planning/tdd/sqlite_backend.md:241`. **qa: approved 2026-05-12.** |
| B-15 | Integration tests for SQLite backend (mirror PG suite) | B-05..B-12 | `tests/integration/test_backend_sqlite.py`, `tests/integration/test_migrate_sqlite.py` | med | pending | — | New files mirroring `tests/integration/test_backend.py` and `tests/integration/test_migrate_002.py` / `test_migrate_003.py` shape. Use `tmp_path / "corpus.db"` fixture instead of testcontainers. NO `pytest.mark.integration` skip — these run anywhere with sqlite3 (always available). |
| B-16 | Parametrize a subset of tests across both backends | B-15 | `tests/conftest.py`, `tests/integration/test_backend_dual.py` | med | pending | — | Add a `backend_kind` parametrize fixture yielding `"postgres"` (testcontainers, skipped without Docker) and `"sqlite"` (always). Run a representative slice (`test_chunk_reuse_e2e`, `test_revisions`) against both. **Do not** parametrize the sync E2E tests (P1-30..P1-32) — they explicitly require Postgres per the scope decision above. |
| B-17 | Update `docs/architecture.md`, `docs/schema.md`, `README.md` | B-13 | `docs/architecture.md`, `docs/schema.md`, `README.md`, `config.example.toml` | low | pending | — | Add a "Backends" section explaining the postgres/sqlite split, when to choose each, and config snippets. Document the sync limitation. Add SQLite example to `config.example.toml`. |
| B-18 | E2E smoke: ingest a markdown vault into SQLite end-to-end | B-13, B-15 | `tests/smoke/test_smoke_sqlite.py` | low | pending | — | One-test smoke using `tmp_path` for both vault and db. Confirms wiring works from CLI/daemon entry through to a queryable SQLite file. |

## Acceptance details

### B-01 — sqlite-vec optional dep + loader
- Add to `pyproject.toml` `[project.optional-dependencies]`: `sqlite = ["sqlite-vec>=0.1"]`.
- Module `corpus_forge.backends.sqlite_vec_loader` (or top-of-file in sqlite.py) with try/except `ImportError`, exposing `SQLITE_VEC_AVAILABLE: bool` and `load_sqlite_vec(conn)` that calls `conn.enable_load_extension(True); sqlite_vec.load(conn); conn.enable_load_extension(False)`.
- `pyrefly: ignore[missing-import]` per the established pattern.

### B-02 — SQLite schema files
- Mirror Postgres DDL row-by-row, table-by-table. Comment any non-trivial divergence inline.
- Migration runner change: extend `corpus_forge/schema/migrate.py::get_migration_files` to take a `dialect` parameter (default `"postgres"`); reads from `corpus_forge/schema/<dialect>/` (postgres files migrate to a `postgres/` subdir; consider keeping current top-level files unchanged and adding a `sqlite/` subdir to avoid breaking existing tests). **Decision (Q2):** keep top-level files (no migration of existing structure), add `sqlite/` subdir, dispatch on `dialect`.

### B-03 — `SQLiteBackend.__init__` + `migrate`
- `SQLiteBackend(path: str, schema: str = "corpus")`.
- `_get_connection()` returns a fresh connection with WAL, foreign_keys, row_factory, sqlite-vec loaded if available. Decorate with the same `contextlib.AbstractContextManager` semantics as Postgres for consistency.
- `migrate()` calls `apply_migrations(self, schema_dir=Path(__file__).parent.parent / "schema" / "sqlite", dialect="sqlite")`.

### B-04 — `register_embedder`
- Idempotent UPSERT into `embedders`. If sqlite-vec available: `CREATE VIRTUAL TABLE IF NOT EXISTS embeddings_<name> USING vec0(chunk_id INTEGER PRIMARY KEY, embedder_id INTEGER, embedding FLOAT[<dim>])`. Else: `CREATE TABLE IF NOT EXISTS embeddings_<name> (chunk_id INTEGER PRIMARY KEY, embedder_id INTEGER NOT NULL, embedding BLOB NOT NULL)`.
- Returns the embedder_id.

### B-05 — `upsert_document`
- Same flow as `PostgresBackend.upsert_document` (read snapshot of prior chunks → SELECT-or-INSERT document → DELETE old chunks → INSERT new chunks with `chunk_content_hash` → call `_copy_reusable_embeddings` per embedder).
- SQL dialect changes: `ON CONFLICT(dataset_id, source_uri) DO UPDATE SET ... RETURNING id`; ensure unique constraint exists on schema.
- `_copy_reusable_embeddings` SQLite version: `INSERT INTO embeddings_<name> (chunk_id, embedder_id, embedding) SELECT ?, embedder_id, embedding FROM embeddings_<name> WHERE chunk_id = ?`.

### B-06 — `upsert_conversation`
- Mirror `postgres.py:upsert_conversation`. Conversations table + messages table + per-message chunks. Adjust ON CONFLICT clauses for SQLite syntax.

### B-07 — `write_embeddings` / `chunks_missing_embedding`
- `write_embeddings(embedder_id, pairs: list[tuple[int, np.ndarray]])`: bulk INSERT serialized vectors. Use `sqlite_vec.serialize_float32(arr)` if vec is loaded; else `arr.tobytes()`.
- `chunks_missing_embedding(embedder_id, limit=1024) -> Iterator[tuple[int, str]]`: `SELECT c.id, c.text FROM chunks c WHERE NOT EXISTS (SELECT 1 FROM embeddings_<name> e WHERE e.chunk_id = c.id AND e.embedder_id = ?) LIMIT ?`. Resolve `<name>` from `embedder_id`.

### B-08 — `lock_source` (advisory lock alternative)
- Context manager. On `__enter__`: open a write transaction (`BEGIN IMMEDIATE`). On `__exit__`: COMMIT (or ROLLBACK on exception). Retries with backoff up to N seconds (configurable, default 30s) on `OperationalError("database is locked")` to handle contention.
- Acceptance: two threads attempting `lock_source("foo")` concurrently — exactly one writes; the other waits; both eventually succeed; no duplicate writes.

### B-09 — Document lifecycle helpers
- `delete_document`, `delete_conversation`: cascade-delete via FK is on (PRAGMA foreign_keys = ON). `find_document`, `resolve_document`: SELECT or upsert-and-return. `resolve_self_source`: upsert into `sources` with `(dataset_id, plugin, identity, host)` unique key.

### B-10 — `insert_revision`
- Inside `lock_source(source_uri)`: `MAX(revision_number) + 1`, then INSERT, then RETURN. JSON metadata stored as TEXT via `json(?)` constructor.

### B-11 — Pull-side helpers
- `latest_revision(document_id)`, `pending_remote_revisions(...)`, `mark_revision_pulled(...)`. Translations are mostly mechanical. Document any SQL-dialect surprise inline.

### B-12 — Tombstone helpers
- `set_tombstone(document_id)`, `clear_tombstone(document_id)`.

### B-13 — Wiring
- `ingest.py:189` and `embed.py:21`: dispatch on `kind`. Lazy-import `SQLiteBackend`. Constructor takes `path = config.backend.dsn` (the dsn field is repurposed as the file path; document this).

### B-14 — Sync gate
- Pydantic root validator on `Config`: reject `sync_enabled = true` paired with `backend.kind = "sqlite"`. Exact error message: `"Cross-host sync requires the postgres backend; SQLite is single-host. Set sync_enabled = false or switch backend.kind to 'postgres'."`

### B-15 — Integration tests
- `tests/integration/test_backend_sqlite.py`: mirror `tests/integration/test_backend.py`. Use `tmp_path / "corpus.db"`. NO `requires_docker` skip.
- `tests/integration/test_migrate_sqlite.py`: schema creation, idempotency, FK constraints, columns present.
- Run command: `PYTHONPATH=. uv run pytest tests/integration/test_backend_sqlite.py tests/integration/test_migrate_sqlite.py -v`.

### B-16 — Dual-backend parametrization
- Conftest fixture `backend_kind` parametrize-fixture yielding `"postgres"` (skip-if-no-docker) and `"sqlite"` (always).
- A representative slice — `test_chunk_reuse_e2e`, `test_revisions` (or new equivalents) — runs against both via the parametrized fixture.

### B-17 — Docs
- `docs/architecture.md`: "Backends" section. Comparison table (postgres = networked, multi-host, sync; sqlite = local, single-host, lighter ops).
- `docs/schema.md`: note the SQLite dialect translations.
- `README.md`: quickstart for SQLite (one paragraph + config snippet).
- `config.example.toml`: SQLite config block.

### B-18 — E2E smoke
- New `tests/smoke/test_smoke_sqlite.py`. Build a minimal vault under `tmp_path/vault/`, configure SQLite backend, call `ingest_once`, query the resulting db file, assert document/chunk rows exist.

## DAG (waves)

- **Wave 0** — Foundation: B-01, B-02 parallel (deps + DDL files).
- **Wave 1** — Skeleton: B-03 only (sets up the file all later waves edit).
- **Wave 2** — Core CRUD (parallel where possible): B-04, then {B-05, B-06, B-07} as a serialized chain on `sqlite.py`. Test files don't conflict, so testers can fan out.
- **Wave 3** — Locks + lookups: B-08, B-09 in sequence on `sqlite.py`.
- **Wave 4** — Sync API on SQLite: B-10, B-11, B-12 in sequence.
- **Wave 5** — Wiring + gate: B-13, B-14 (parallel — different files).
- **Wave 6** — Tests: B-15, B-18 parallel.
- **Wave 7** — Dual-backend tests: B-16.
- **Wave 8** — Docs: B-17.

## Hard ordering constraints

1. B-01 must finish before B-04 (loader needed for embedding tables).
2. B-02 must finish before B-03 (migrate reads schema files).
3. B-03 must finish before all of B-04..B-12 (they edit the same file).
4. B-08 must finish before B-10 (revisions need the lock).
5. B-13 + B-14 must finish before B-15 (integration tests exercise the wiring).
6. B-16 depends on B-15 + the existing Postgres integration suite (which is already green).

## Open questions (decided defaults; flag during execution if any need to flip)

- **Q1** — Lock granularity: global write-lock via `BEGIN IMMEDIATE` (chosen) vs per-source row mutex. Default: global. Re-evaluate only if multiple concurrent ingests show contention.
- **Q2** — Schema file layout: `corpus_forge/schema/sqlite/*.sql` subdir alongside the existing top-level Postgres files. Default: subdir. Alternative considered: in-Python DDL strings — rejected for diff-noise.
- **Q3** — Vector index: sqlite-vec virtual table when extension is loaded; raw BLOB fallback otherwise. Default: both paths supported, fallback documented as "no nearest-neighbour" (write-only).
- **Q4** — `dsn` field repurpose: SQLite uses a file path, but the config field is named `dsn`. Default: keep the field name, document that for SQLite it's interpreted as a path. Alternative: add a separate `path` field — rejected as needless config split.
- **Q5** — `chunks_missing_embedding` filter syntax: `WHERE NOT EXISTS (...)` is the SQLite-friendly form (Postgres uses `EXCEPT`). Default: portable form on both backends if it's faster — or keep separate.

## Done criteria

### Required artifacts shipped
- [ ] `corpus_forge/backends/sqlite.py` exists and implements the full `StorageBackend` protocol.
- [ ] `corpus_forge/schema/sqlite/{001_core,002_chunk_content_hash,003_sync}.sql` exist.
- [ ] `pyproject.toml` has `sqlite = ["sqlite-vec>=0.1"]` extra.
- [ ] `corpus_forge/ingest.py` and `corpus_forge/embed.py` route on `kind`.
- [ ] `corpus_forge/config.py` rejects `sync_enabled + sqlite` combinations.
- [ ] `config.example.toml` has a SQLite example block.
- [ ] `docs/architecture.md`, `docs/schema.md`, `README.md` document the dual-backend model.

### Required tests green
- [ ] `tests/unit/test_sqlite_backend.py` — all behaviors per B-03..B-12.
- [ ] `tests/integration/test_backend_sqlite.py` — full PG-suite mirror.
- [ ] `tests/integration/test_migrate_sqlite.py` — schema + idempotency.
- [ ] `tests/integration/test_backend_dual.py` — parametrized chunk-reuse + revisions.
- [ ] `tests/smoke/test_smoke_sqlite.py` — end-to-end ingest.

### Required gates green
- [ ] `uv run ruff check corpus_forge tests` clean.
- [ ] `uv run ruff format --check corpus_forge tests` clean.
- [ ] `uv run pyrefly check corpus_forge` clean.
- [ ] `PYTHONPATH=. uv run pytest tests/unit --cov=corpus_forge --cov-fail-under=85` clean.
- [ ] `PYTHONPATH=. uv run pytest tests/integration` clean (Postgres suite still 102/102; new SQLite suite green).

### Stop condition
All boxes above checked. The Wave 8 commit appends a Phase B summary to this file mirroring the Wave 13 structure in `tasks.md` (files changed, gates run, line counts).

## Risk register

- **`sqlite-vec` C-extension build on Apple Silicon**: pip-installable, but the test environment must verify it actually loads. B-01 acceptance includes a smoke test that exercises `sqlite_vec.load(conn)` end-to-end.
- **`BEGIN IMMEDIATE` contention under heavy ingest**: low risk in single-host scope, but write a stress test (10 concurrent threads inserting revisions) in B-08 to confirm.
- **Migration ordering between top-level Postgres files and `sqlite/` subdir**: avoid by dispatching strictly on `dialect` parameter; tested in B-15.
- **JSONB → TEXT translation for `metadata` columns**: storing JSON as TEXT loses query-side `->` operators. Phase B does not query into metadata (only round-trips it as a blob), so this is fine — but document it in `docs/schema.md`.

## Phase B planning notes

_Owner: tdd-principal. Decided 2026-05-09._

### Board choice
- This file is the **standalone Phase B board**. The task table above is authoritative; `claimed_by` and `status` columns track worker state.
- `code-status.md` / `test-status.md` / `qa-status.md` get appended Phase B entries with `B-NN` task ids. They cross-link back here. The Active Directory Sync `tasks.md` and `waves.md` files are frozen for that feature and not extended.

### Wave-level grouping (collapsed from the plan's 8-wave DAG)

The principal cannot in this runtime fan out workers in parallel; everything is dispatched serially via the orchestrator. Adjacent waves that touch the same file naturally serialize. The plan's 8 waves collapse into 6 execution waves:

| Exec wave | Plan-wave source | Tasks | Rationale |
|-----------|------------------|-------|-----------|
| **W0** | Plan W0 | B-01, B-02 | Foundation. Dispatched as 2 sequential tester→coder→QA cycles. Disjoint files (`pyproject.toml`+helper for B-01, `schema/sqlite/*.sql` for B-02) so dispatch order is interchangeable; we go B-01 first because the loader contract feeds B-04. |
| **W1** | Plan W1 | B-03 | Skeleton. `SQLiteBackend.__init__` + `migrate()`. Reads B-02's files, uses B-01's loader. |
| **W2** | Plan W2..W4 (collapsed) | B-04, B-05, B-06, B-07, B-08, B-09, B-10, B-11, B-12 | All edit `sqlite.py`. Serialize through tester→coder→QA cycles in dep order: B-04 → {B-05, B-06, B-07} → B-08 → B-09 → {B-10, B-11, B-12}. Collapsed because runtime can't parallelize; the order is the same as plan-W2 → plan-W3 → plan-W4 read top-to-bottom. |
| **W3** | Plan W5 | B-13, B-14 | Wiring + sync gate. Disjoint files (`ingest.py`+`embed.py` vs `daemon.py`+`config.py`); dispatch one after the other since runtime can't parallelize. |
| **W4** | Plan W6, W7 | B-15, B-16, B-18 | Integration tests + dual-backend parametrize + smoke. All in `tests/`. Disjoint files; serialize. (Plan-W6 and plan-W7 collapse here because there's no parallelism win anyway.) |
| **W5** | Plan W8 | B-17 | Docs. |

### Q1..Q5 default acceptance log
- **Q1 (lock granularity)**: global `BEGIN IMMEDIATE` per default. **Honored.** Will pause + report only if B-08 stress test (10 concurrent threads) shows pathological stalls (>30s).
- **Q2 (schema layout)**: subdir `corpus_forge/schema/sqlite/`, dispatch on `dialect` parameter to migrate.py. **Honored.**
- **Q3 (vector index)**: sqlite-vec virtual table when loaded; raw BLOB fallback. **Honored.**
- **Q4 (`dsn` repurposed as path)**: keep field name; document. **Honored.**
- **Q5 (`chunks_missing_embedding` SQL form)**: use `WHERE NOT EXISTS (...)` portable form. **Honored.**

### Pause-and-report triggers
- sqlite-vec wheel does not load on this aarch64-darwin host (B-01).
- `BEGIN IMMEDIATE` doesn't actually serialize as expected under the threaded stress test (B-08).
- A schema feature genuinely cannot translate (B-02 — e.g. a Postgres-only constraint with no SQLite equivalent that breaks an existing test).
- Any change required to the Postgres suite to keep it green (out of scope per prompt).

### Commit prefix
- Tester commits: `[tdd-tester] B-NN: <summary>`.
- Coder commits: `[tdd-coder] B-NN: <summary>`.
- Principal bookkeeping: `[tdd-principal] phase-b/<task-or-wave>: <summary>`.
- 1Password signing flake → retry once after a brief sleep, never `--no-gpg-sign`.

### First dispatch
**W0.B-01 — tdd-tester.** Writes red unit + import-smoke tests for the sqlite-vec optional dep + loader. Spec is in the parent session prompt; principal will mark `status: red` once tester's report comes back.
