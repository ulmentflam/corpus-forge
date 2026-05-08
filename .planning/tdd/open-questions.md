# Open Questions for the User

These are gaps, ambiguities, or under-specified details in `active_directory_sync.md` that I am NOT going to commit the project to without your call. Resolve before dispatch.

## High-priority (block dispatch of related tasks)

### Q1. Threading model for push/pull pipelines
The plan says "push + pull threads/tasks" (§Daemon wiring) but does not commit to threads vs asyncio. The watchdog `Observer` is thread-based. The pull poll loop could be either. Mixing introduces complexity (psycopg autocommit + event loop interaction).

**Choices:**
- (a) Pure-threading: pull loop is a `threading.Thread` with a `threading.Event` stop signal; psycopg sync API throughout.
- (b) Asyncio: `asyncio.create_task` for pull, run watchdog in an executor; psycopg async.
- (c) Hybrid: threads for both, asyncio only at the daemon-level signal handler.

**Default if unanswered:** (a) pure threading. Lowest risk against existing sync psycopg usage in `PostgresBackend`. Affected tasks: P1-22..P1-28.

### Q2. `EchoSuppressor` thread-safety
Plan does not say. Push pipeline (watchdog observer thread) reads, pull pipeline (poll thread) writes — they run concurrently per dataset. Without a lock, this is racy.

**Choices:**
- (a) `threading.Lock` around the dict. Simple, safe.
- (b) `threading.local()` keyed by pipeline; principal allocates two suppressors. Loses cross-pipeline coordination.
- (c) Single instance, no lock; rely on GIL for dict atomicity. Unsafe for compound check-then-write.

**Default if unanswered:** (a). Affected tasks: P1-06, P1-18, P1-22.

### Q3. Connection pooling for the new revision API
`PostgresBackend._get_connection` opens a new connection on every `_execute`. The pull poll loop will hammer it (one connection per `pending_remote_revisions` call every `sync_poll_interval_s`). Does the plan implicitly require a connection pool?

**Choices:**
- (a) Stick with one-shot connections (existing pattern). Slow but consistent.
- (b) Introduce `psycopg_pool.ConnectionPool` in `PostgresBackend.__init__`, keep `_get_connection` semantics.
- (c) Defer to P2.

**Default if unanswered:** (c) defer. The current code is one-shot; introducing a pool is a bigger refactor than this plan implies. Plan tasks accordingly: P1-15 keeps the current connection pattern.

## Medium-priority (block specific tasks but have safe defaults)

### Q4. macOS Finder " copy" duplicate provider tag
Plan §iCloud lists `<stem> copy<suffix>` and `<stem> copy 2<suffix>` under "macOS Finder copy-of pattern". `is_cloud_duplicate` returns a provider name. Is Finder its own provider tag, or rolled into `"icloud"` since they coexist on iCloud Drive vaults?

**Choices:**
- (a) Add `"finder"` to the `Literal` union: `"icloud","dropbox","gdrive","finder","none"`.
- (b) Treat as `"icloud"` when path is iCloud, else `"none"`.
- (c) Treat as a separate `is_finder_duplicate` check, leaving `is_cloud_duplicate` strictly cloud-provider.

**Default if unanswered:** (a). Affected tasks: P1-07, P1-08.

### Q5. Conflict timestamp format
Plan says `<stem>.conflict-<host>-<ts><suffix>` but does not specify `<ts>` format. Filesystem-safe options:
- (a) `20260507T223045Z` (ISO 8601 basic, UTC)
- (b) `2026-05-07_22-30-45Z`
- (c) Unix epoch seconds `1746657045`

**Default if unanswered:** (a). Sortable, no colons (Windows-compat), no separators in the host segment that would parse-collide. Affected tasks: P1-09.

### Q6. `sync/__init__.py` — empty or with public API
Plan lists `__init__.py exports SyncEngine`. Should it also export `EchoSuppressor`, `PushPipeline`, `PullPipeline`, `is_cloud_duplicate`, `detect_cloud_provider`?

**Default if unanswered:** Export only `SyncEngine` (per plan literally). Internal modules import from their submodule. Affected tasks: P1-06, P1-27.

### Q7. `documents` row creation timing for new files
Push pipeline pseudocode says "If `local_hash == latest.content_hash`: no-op. Otherwise insert a `document_revisions` row…". But for a brand-new file, there is no `documents` row yet, so `latest.content_hash` is undefined and there is no `document_id` for the revision foreign key.

**Implied flow (please confirm):**
1. Inside `lock_source(source_uri)`:
2. Resolve / create `documents` row first (existing `upsert_document` does this; or call a new lighter `ensure_document` helper).
3. THEN insert the revision with `parent_revision_id = NULL` (because no prior revision exists) and `document_id = <new>`.
4. Then continue with `upsert_document`'s text/chunk update path.

This matters because `upsert_document` ALSO upserts the documents row; calling it inside `lock_source` after `insert_revision` means revision and document upsert race against `documents.content_hash` short-circuit.

**Default if unanswered:** introduce `PostgresBackend.ensure_document(dataset_id, source_uri) -> int` that creates an empty (or placeholder-text) row only if missing, returns its id, then `insert_revision`, then call `upsert_document` for the actual chunk write. This is a small new method; it is **not** itemized in the plan but the alternative (re-ordering inside `upsert_document`) is invasive.

Possible new task **P1-13b**: `ensure_document` helper. **Defaulted to: yes, add it; mark it as a sub-step of P1-13** unless you say otherwise.

### Q8. Pull pipeline writing during a fast-forward race
What if local file changes between the read-and-hash and the `atomic_write_text`? The plan does not mention pull-side locking. A user save during a pull tick could be silently overwritten.

**Choices:**
- (a) Acquire `lock_source` on the pull side too, around the entire revision-application sequence.
- (b) Re-hash inside the lock-equivalent and bail to conflict if it changed mid-flight.
- (c) Accept the race; the next push cycle will re-publish.

**Default if unanswered:** (a). Holding `lock_source` is cheap and matches the push-side guarantees. Affected tasks: P1-22..P1-25.

### Q9. CLI `sync resolve` for the `keep-local` strategy
Plan: `resolve CONFLICT_FILE --strategy keep-local|keep-remote (P2: merge)`. For `keep-local`:
- The conflict file is the *loser* (per pull pipeline writing the incoming as canonical).
- "Keep local" presumably means: replace canonical with the conflict file's contents, then delete the conflict file, then push as a new revision.

Confirm this is the intended semantics. The alternative reading ("keep what's currently local on disk") makes no sense once a conflict file exists.

**Default if unanswered:** the first reading. Affected tasks: P1-29.

## Low-priority (cosmetic / can be resolved during implementation)

### Q10. Revision metadata schema
`document_revisions.metadata JSONB`. The plan does not specify what goes in. Suggested keys: `{"chunker_config_fingerprint": "...", "embedder_ids_at_write": [...]}` for future P2 reuse-gate logic.

**Default if unanswered:** leave empty `{}` unless a worker has a concrete reason. Affected tasks: P1-13.

### Q11. `pending_remote_revisions` `LIMIT`
Plan does not give one. Default 1024 per the `chunks_missing_embedding` precedent. Pull loop iterates until no rows returned.

**Default if unanswered:** 1024. Affected tasks: P1-15.

### Q12. Coverage gate vs new untested daemon code
Current global coverage is 75% (`pyproject.toml` [tool.coverage.report] fail_under = 75) but `make test-unit` enforces 85% via `--cov-fail-under=85`. The active_tasks.md says coverage was lowered to 75. The introduction of watchdog and observer code may push coverage down. Should we:
- (a) Keep `--cov-fail-under=85` and require coverage on all new sync code.
- (b) Lower the gate temporarily to 75 to match `pyproject.toml`.
- (c) Exclude `sync/push.py` and `sync/pull.py` thread-driven paths from coverage as they're integration-tested.

**Default if unanswered:** (a) — hold the line at 85, write fakes/mocks to drive sync code paths in unit tests. Affected: every task that touches sync/.

### Q13. Where the `sync resolve` and `sync push` commands acquire backend
`corpus-forge` CLI today loads `Config.load()` at command time. The `sync` subcommand will need a backend handle. Plan does not specify a context provider.

**Default if unanswered:** load config and instantiate `PostgresBackend` per command, like existing `migrate`/`ingest` commands. No global state. Affected tasks: P1-29.

## Plan items I interpreted rather than followed literally

Listed for transparency; flag any you want to overrule.

1. **Backfill SQL form (P0-03):** Plan says "Backfill (idempotent, batched)". I planned for a SQL `UPDATE … WHERE content_hash IS NULL` with a sha256 expression. If you'd rather have a Python loop in the migration runner that pages 1000 rows at a time and emits progress logs, swap to that — but the SQL form is simpler and equally idempotent.

2. **Revision API split (P1-13..P1-17):** Plan lists four `PostgresBackend` methods (`insert_revision`, `latest_revision`, `pending_remote_revisions`, `mark_revision_pulled`). I added `set_tombstone`/`clear_tombstone` because the pull tombstone branch needs them and the plan implies `documents.tombstoned_at` is mutated from the pull side. Treat `set_tombstone` as a P1-13 dependency rather than a separate task if you want fewer micro-tasks.

3. **`ensure_document` helper (Q7):** Not listed in plan; my interpretation said it's necessary to keep the lock semantics clean for new files.

4. **Cloud-duplicate detection scope:** Plan lists patterns; I extracted them into a single `is_cloud_duplicate(path) -> tuple[bool, provider, canonical_path]` API rather than per-provider helpers. Easier to test in one place.

5. **`PullPipeline.tick()` returning count of revisions applied:** Not specified. I added it for testability — letting the test assert "tick consumed 3 revisions" without snooping internal state.

6. **Test file `tests/integration/test_revisions.py`:** Plan does not enumerate this file by name; I added it because the four backend methods (P1-13..P1-17) need integration coverage and folding them into `test_backend.py` makes that file too large.

7. **Test file `tests/unit/test_sync_engine.py`:** Plan calls out unit tests for echo/conflicts/fs/cloud but not engine. I added it because P1-27 introduces a new class with non-trivial start/stop semantics.

8. **Test file `tests/unit/test_cli_sync.py`:** Same reasoning — plan calls out CLI additions but not their tests; the CLI surface is wide enough to warrant its own unit file.

9. **Wave 3 collapse (P1-13..P1-17):** Plan implies these as separate methods. They share fixtures (testcontainers Postgres + a documents row). I noted the option to dispatch them as a single tester+coder cycle. Pure-procedural alternative: each gets its own tester+coder+qa cycle (5 cycles back-to-back, single-threaded on `postgres.py`). Default to single grouped cycle unless you prefer max granularity.

10. **`debounce_seconds` reuse:** Plan reuses the existing `daemon.debounce_seconds`. I am applying it per-path (one debounce timer per file). Plan says "applied per-path". Confirmed implicitly; no question, just calling out the interpretation.
