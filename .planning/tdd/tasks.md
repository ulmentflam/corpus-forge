# TDD Task Board — Active Directory Sync

_Owner: tdd-principal. Workers: read freely. Edit only your claimed row's `status` and `claimed_by`._

Source plan: `/Users/evanowen/Library/Mobile Documents/com~apple~CloudDocs/Workspace/playground/corpus-forge/.planning/active_directory_sync.md`

## Project gates

- lint: `uv run ruff check corpus_forge tests`
- format: `uv run ruff format --check corpus_forge tests`
- typecheck: `uv run pyrefly check corpus_forge` (strict mode)
- test (unit, fast, gated): `uv run pytest tests/unit -v --cov=corpus_forge --cov-report=term-missing --cov-fail-under=85`
- test (integration, testcontainers Postgres): `uv run pytest tests/integration -v`
- test (fuzz): `uv run pytest tests/fuzz -v --hypothesis-show-statistics`
- test (smoke): `uv run pytest tests/smoke -v`
- coverage-min: 85 (this is what `make test-unit` enforces today; current coverage 75% per active_tasks.md — flag if global coverage falls below 85% after introducing untested watchdog code)
- ci: `make ci` = `format-check lint typecheck test`
- smoke: cross-Mac smoke for P1 only (manual; see done-criteria.md)

## Reused primitives (do not reinvent — direct imports allowed by all tasks)

- `corpus_forge.identity.file_content_hash` (file path → sha256)
- `corpus_forge.identity.content_hash` (bytes → sha256) — **use for `chunk_content_hash`**
- `corpus_forge.identity.advisory_lock_key`
- `corpus_forge.backends.postgres.PostgresBackend.lock_source` (advisory lock per source_uri)
- `corpus_forge.backends.postgres.PostgresBackend.chunks_missing_embedding`
- `corpus_forge.backends.postgres.PostgresBackend.upsert_document` (already has `documents.content_hash` short-circuit at line 294)
- `corpus_forge.sources.base.WatchedSource.file_content_hash`, `identity`

## Tasks

| id | title | depends_on | surface | risk | status | claimed_by | notes |
|----|-------|------------|---------|------|--------|------------|-------|
| P0-01 | `chunk_content_hash` helper in `identity.py` | — | `corpus_forge/identity.py`, `tests/unit/test_identity.py` | low | done | tdd-qa | All gates green: 17/17 tests pass, 100% coverage on identity.py, 89% overall (threshold 85%), lint/format/typecheck clean on changed files, 57 regression tests pass. |
| P0-02 | `002_chunk_content_hash.sql` migration file (DDL only) | — | `corpus_forge/schema/002_chunk_content_hash.sql` | low | done | tdd-tester | DDL created (ALTER + CREATE INDEX, both IF NOT EXISTS). 12 tests green. Surfaced: `get_migration_files()` sort-key extraction is broken for all existing files; ruff has no `.sql` exclusion. |
| P0-03 | Migration runner backfills `chunks.content_hash` | P0-01, P0-02 | `corpus_forge/schema/migrate.py`, `tests/integration/test_migrate_002.py` | med | pending | — | Idempotent batched UPDATE; uses sha256 of `chunks.text`. |
| P0-04 | `upsert_document` writes `content_hash` on chunk insert | P0-01, P0-02 | `corpus_forge/backends/postgres.py`, `tests/unit/test_postgres_backend.py` (or fixture in test_chunk_reuse) | low | pending | — | Just append column to chunk INSERT. |
| P0-05 | `_copy_reusable_embeddings` helper on `PostgresBackend` | P0-04 | `corpus_forge/backends/postgres.py`, `tests/unit/test_chunk_reuse.py` | med | pending | — | Per-call cache `(content_hash, embedder_id) → chunk_id`. Returns set of reused embedder_ids. |
| P0-06 | `upsert_document` accepts `embedder_ids` and triggers reuse | P0-05 | `corpus_forge/backends/postgres.py`, `corpus_forge/backends/base.py`, `tests/unit/test_chunk_reuse.py` | med | pending | — | Signature change: `upsert_document(..., embedder_ids: list[int] \| None = None)`. Backwards-compat default keeps None = no reuse. |
| P0-07 | `ingest_one` passes active embedder ids into `upsert_document` | P0-06 | `corpus_forge/ingest.py`, `tests/unit/test_ingest_core.py` (or test_ingest_helpers) | low | pending | — | Resolve embedder_id via `backend.register_embedder(e)` once per call, pass list. |
| P0-08 | E2E reuse test (testcontainers) | P0-03..P0-07 | `tests/integration/test_chunk_reuse_e2e.py` | med | pending | — | Ingest 10 chunks, append 1, assert ≥7 of original 10 embedding rows preserved (same vector). |
| P1-01 | `003_sync.sql` migration file (DDL only) | P0-02 (file ordering) | `corpus_forge/schema/003_sync.sql` | low | done | tdd-tester | DDL created (CREATE TABLE + 2 INDEX + 3 ALTER, all IF NOT EXISTS). 31 tests green. |
| P1-02 | Migration runner applies `003_sync.sql` cleanly + idempotent | P1-01 | `corpus_forge/schema/migrate.py`, `tests/integration/test_migrate_003.py` | low | pending | — | Existing runner already loops numbered files; just confirm. |
| P1-03 | Pydantic config: `DaemonConfig` sync fields + `DatasetConfig.sync_enabled` + validators | — | `corpus_forge/config.py`, `tests/unit/test_config.py` (and/or test_config_extended) | low | done | tdd-tester | 19 tests written, confirmed red (fields/validators not yet implemented). 39 existing tests still pass. |
| P1-04 | `Config.host_id()` resolution + first-run persistence | P1-03 | `corpus_forge/config.py`, `tests/unit/test_config_extended.py` | med | pending | — | Read config value → `socket.gethostname()` fallback → write `~/.config/corpus-forge/host_id` once and prefer that on later loads. Use `tmp_path` + monkeypatch for tests. |
| P1-05 | `config.example.toml` + default exclude_globs include `*.icloud` | P1-03 | `config.example.toml` | low | done | tdd-tester | Added `[daemon]` sync fields (host_id, trash_dir, conflict_dir, sync_poll_interval_s), `*.icloud` to exclude_globs, `sync_enabled = true` on text vault. TOML valid, ruff format clean. |
| P1-06 | `EchoSuppressor` in `sync/echo.py` | — | `corpus_forge/sync/__init__.py`, `corpus_forge/sync/echo.py`, `tests/unit/test_sync_echo.py` | low | done | tdd-tester | 28 tests written, confirmed red (module doesn't exist). Handed off to tdd-coder. |
| P1-07 | `sync/cloud.py::detect_cloud_provider` | — | `corpus_forge/sync/cloud.py`, `tests/unit/test_sync_cloud.py` | low | done | tdd-tester | Substring/prefix match on resolved abs path. Returns `Literal["icloud","dropbox","gdrive","none"]`. Tests written, confirmed red |
| P1-08 | `sync/conflicts.py::is_cloud_duplicate` | P1-07 | `corpus_forge/sync/conflicts.py`, `tests/unit/test_sync_conflicts.py` | med | pending | — | Recognize iCloud (` 2`, ` 3`, ` (n)`), Dropbox (`(host's conflicted copy date)`), Google Drive (`(1)`, `-conflict-date-n`), Finder (` copy`, ` copy 2`). |
| P1-09 | `sync/conflicts.py::conflict_filename` (canonical conflict naming) | — | `corpus_forge/sync/conflicts.py`, `tests/unit/test_sync_conflicts.py` | low | in_progress | tdd-tester | 45 tests written, confirmed red. Handed off to tdd-coder. |
| P1-10 | `sync/fs.py::atomic_write_text` | — | `corpus_forge/sync/fs.py`, `tests/unit/test_sync_fs.py` | low | done | tdd-tester | 38 tests written (37 red, 1 stub-return pass). All dimensions covered: happy, boundaries, failure paths (os.replace crash), encoding, tempfile naming/cleanup, type validation, state/interleaving, RTL/Unicode. Handed off to tdd-coder. |
| P1-11 | `sync/fs.py::move_to_trash` | P1-03 (trash_dir config) | `corpus_forge/sync/fs.py`, `tests/unit/test_sync_fs.py` | low | pending | — | `<trash_dir>/<dataset>/<rel-path>.deleted-<host>-<ts><suffix>` via `os.replace`. Creates parents. |
| P1-12 | `sync/fs.py::is_icloud_placeholder` and dataless guards | — | `corpus_forge/sync/fs.py`, `tests/unit/test_sync_fs.py` | med | pending | — | Detect `*.icloud` 0-byte stubs and `com.apple.fileprovider.materialized` xattr. xattr check should be best-effort and fail closed (treat unknown as real). |
| P1-13 | `PostgresBackend.insert_revision` | P1-02 | `corpus_forge/backends/postgres.py`, `corpus_forge/backends/base.py`, `tests/integration/test_backend.py` (revision section) or new `tests/integration/test_revisions.py` | med | pending | — | Allocates `revision_number = MAX+1` under `lock_source`. Sets `parent_revision_id` from caller-provided latest. |
| P1-14 | `PostgresBackend.latest_revision` | P1-02 | `corpus_forge/backends/postgres.py`, `tests/integration/test_revisions.py` | low | pending | — | `SELECT … ORDER BY revision_number DESC LIMIT 1` for a `document_id` (or by `source_uri` lookup). |
| P1-15 | `PostgresBackend.pending_remote_revisions` | P1-02 | `corpus_forge/backends/postgres.py`, `tests/integration/test_revisions.py` | med | pending | — | `WHERE r.id > $last AND r.author_host <> $self ORDER BY r.id ASC`. Joins documents to filter by `dataset_id`. |
| P1-16 | `PostgresBackend.mark_revision_pulled` | P1-15 | `corpus_forge/backends/postgres.py`, `tests/integration/test_revisions.py` | low | pending | — | `UPDATE sources SET last_pulled_revision_id = GREATEST(coalesce(last_pulled_revision_id,0), $1)`. |
| P1-17 | `PostgresBackend.set_tombstone` (and clear on resurrect) | P1-13 | `corpus_forge/backends/postgres.py`, `tests/integration/test_revisions.py` | low | pending | — | Sets/clears `documents.tombstoned_at`. Called by pull pipeline. |
| P1-18 | `sync/push.py::PushPipeline.handle_event` core (mtime cache + hash + echo + lock + insert) | P0-07, P1-04, P1-06, P1-13, P1-14 | `corpus_forge/sync/push.py`, `tests/unit/test_sync_push.py` | high | pending | — | One handler call per event. Tests use a fake backend + temp file; watchdog observer not required at this layer. |
| P1-19 | `sync/push.py` watchdog observer wiring + debounce | P1-18 | `corpus_forge/sync/push.py`, `tests/unit/test_sync_push.py` | med | pending | — | `watchdog.Observer`, exclude_globs, hidden-file filter, `*.icloud` filter, dataless guard, debounce per-path. |
| P1-20 | `sync/push.py` cloud-duplicate cleanup branch | P1-08, P1-09, P1-19 | `corpus_forge/sync/push.py`, `tests/unit/test_sync_push.py` | med | pending | — | When `is_cloud_duplicate` matches: same hash → delete; different hash → rename to `conflict_filename(provider=…)` + ingest as conflict revision. |
| P1-21 | `sync/push.py` tombstone-on-delete handler | P1-12, P1-13 | `corpus_forge/sync/push.py`, `tests/unit/test_sync_push.py` | med | pending | — | Watchdog delete event → tombstone revision. Suppress when `*.icloud` placeholder remains (eviction, not delete). |
| P1-22 | `sync/pull.py::PullPipeline.tick` (single poll cycle, fast-forward branch) | P1-06, P1-10, P1-14, P1-15, P1-16 | `corpus_forge/sync/pull.py`, `tests/unit/test_sync_pull.py` | high | pending | — | Pulls pending, fast-forward-writes when local hash matches parent, registers echo, advances `last_pulled_revision_id`. |
| P1-23 | `sync/pull.py` already-in-sync branch | P1-22 | `corpus_forge/sync/pull.py`, `tests/unit/test_sync_pull.py` | low | pending | — | Local hash already == new revision hash → register echo only, advance pointer. |
| P1-24 | `sync/pull.py` conflict branch (non-destructive LWW) | P1-09, P1-22 | `corpus_forge/sync/pull.py`, `tests/unit/test_sync_pull.py` | high | pending | — | Local matches neither parent nor new → write incoming canonical, save local as `<stem>.conflict-<host>-<ts><suffix>`. Conflict file gets ingested next push cycle (do not insert revision here). |
| P1-25 | `sync/pull.py` tombstone branch | P1-11, P1-17, P1-22 | `corpus_forge/sync/pull.py`, `tests/unit/test_sync_pull.py` | med | pending | — | Tombstone revision → `move_to_trash`, set `documents.tombstoned_at`. |
| P1-26 | `sync/pull.py` poll-loop / lifecycle | P1-22..P1-25 | `corpus_forge/sync/pull.py`, `tests/unit/test_sync_pull.py` | med | pending | — | Thread-driven loop with `sync_poll_interval_s`. Stop event for clean shutdown. Exact threading model — see open-questions.md. |
| P1-27 | `sync/engine.py::SyncEngine` lifecycle (start/stop both halves per dataset) | P1-19, P1-26 | `corpus_forge/sync/engine.py`, `corpus_forge/sync/__init__.py` (export), `tests/unit/test_sync_engine.py` | med | pending | — | Owns push pipeline + pull pipeline. `start()` non-blocking, `stop()` flushes. |
| P1-28 | `daemon.py` orchestrator: per-dataset SyncEngine vs ingest_main | P1-27, P1-04 | `corpus_forge/daemon.py`, `tests/unit/test_daemon.py` | med | pending | — | Replace 39-line stub. Block on signals, call `engine.stop()` on shutdown. |
| P1-29 | CLI `sync` Typer subgroup | P1-04, P1-13..P1-17, P1-09 | `corpus_forge/cli.py`, `tests/unit/test_cli_sync.py` | med | pending | — | Commands: `status`, `pull --once/--continuous -d DATASET`, `push`, `resolve CONFLICT_FILE --strategy keep-local\|keep-remote`, `history SOURCE_URI [--limit N]`. P2 strategies (`merge`) raise NotImplemented with friendly error. |
| P1-30 | E2E push→pull integration test (testcontainers) | P1-27, P1-28 | `tests/integration/test_sync_push_pull.py` | high | pending | — | Two simulated hosts (different `host_id`) in one process, shared Postgres, two `tmp_path` roots. Edit on A → appears on B. |
| P1-31 | E2E tombstone integration test | P1-25, P1-27 | `tests/integration/test_sync_tombstone.py` | med | pending | — | Delete on A → file appears in trash dir on B; `documents.tombstoned_at` set. Resurrection clears it. |
| P1-32 | E2E iCloud-duplicate integration test | P1-08, P1-20 | `tests/integration/test_sync_icloud_dupe.py` | med | pending | — | Drop a `Foo 2.md` next to `Foo.md` with matching hash → duplicate deleted, no extra document. With differing hash → renamed to corpus-forge conflict naming, ingested as conflict revision. |

## Acceptance details

### P0-01 — `chunk_content_hash`
- New function `corpus_forge.identity.chunk_content_hash(text: str) -> str` returning sha256 hex of `text.encode("utf-8")`.
- Same hashing convention as existing `content_hash(bytes)`.
- Pure function, no I/O.

### P0-02 — `002_chunk_content_hash.sql`
- File `corpus_forge/schema/002_chunk_content_hash.sql` with exact DDL from plan §P0:
  - `ALTER TABLE corpus.chunks ADD COLUMN IF NOT EXISTS content_hash TEXT;`
  - `CREATE INDEX IF NOT EXISTS chunks_content_hash_idx ON corpus.chunks(content_hash);`
- File picks up automatically via `get_migration_files()` numeric sort.

### P0-03 — Backfill in migration runner
- Migration runner runs `002` and then performs an idempotent batched backfill: `UPDATE corpus.chunks SET content_hash = encode(sha256(text::bytea), 'hex') WHERE content_hash IS NULL` (or equivalent done client-side in batches; SQL form preferred to avoid round-trips).
- Re-running migrations is a no-op (zero rows updated on second run).

### P0-04 — `upsert_document` writes `content_hash`
- Chunk INSERT in `upsert_document` includes `content_hash = chunk_content_hash(text)`.
- Backwards compatibility: schema migration applied means column exists; INSERT must list it.

### P0-05 — `_copy_reusable_embeddings`
- Method signature: `_copy_reusable_embeddings(self, new_chunk_id: int, content_hash: str, embedder_ids: list[int], cache: dict[tuple[str, int], int]) -> set[int]`.
- For each `embedder_id`: look up an existing chunk with same `content_hash` whose embedding row exists in `embeddings_<name>`; if found, `INSERT … SELECT` the vector for `new_chunk_id`; mark embedder_id as reused.
- Cache hits skip the SELECT.
- Returns the set of `embedder_id`s for which embeddings were successfully copied.

### P0-06 — `upsert_document(..., embedder_ids=...)`
- New keyword arg `embedder_ids: list[int] | None = None`.
- When None → behaves exactly like today (no reuse pass).
- When provided → after each chunk INSERT, calls `_copy_reusable_embeddings`. Reused embedder rows mean `chunks_missing_embedding` returns nothing for those embedders.

### P0-07 — `ingest_one` passes embedder_ids
- Resolve list of `embedder_id` (call `backend.register_embedder(e)` for each active embedder) once at the top of the function.
- Pass to `upsert_document(..., embedder_ids=ids)`.
- Conversation path may keep current behavior; reuse is a P0 concern for documents (markdown), not chats.

### P0-08 — E2E reuse test
- Use `tests/conftest.py`'s testcontainers Postgres fixture.
- Ingest a doc that chunks to ~10 chunks (plan: ≥7 reuse expected on small append).
- Capture set of `(chunk_id, vector)` rows from the active embedder table.
- Append a paragraph that creates a single new tail chunk; re-ingest.
- Assert: ≥7 of the original 10 chunks' embeddings survived (same `content_hash`, vector preserved by reuse path; specifically: `embeddings_<name>` has at least 7 rows whose chunk_ids correspond to chunks whose `content_hash` was present in the pre-append snapshot).

### P1-01 — `003_sync.sql`
- Exact DDL from plan §P1 schema block. All `IF NOT EXISTS` guards.

### P1-02 — Migrate `003_sync.sql` cleanly
- Re-running migrations is a no-op.
- Indexes named exactly as in the plan.

### P1-03 — Sync config fields
- `DaemonConfig`:
  - `host_id: str = ""` (blank → derived later)
  - `trash_dir: ExpandedPath = "~/.local/share/corpus-forge/trash"`
  - `conflict_dir: ExpandedPath = ""` (blank → next to original)
  - `sync_poll_interval_s: float = Field(default=5.0, gt=0)`
  - `sync_use_listen_notify: bool = False` (P2)
- `DatasetConfig`:
  - `sync_enabled: bool = False`
  - Validator: raises if `sync_enabled and kind != "text"`.
- Existing tests must continue to pass.

### P1-04 — `Config.host_id()`
- Method: `Config.host_id() -> str`.
- Resolution order: explicit `daemon.host_id` if non-empty → file at `~/.config/corpus-forge/host_id` if present → `socket.gethostname()`. Last branch also writes the result to the host_id file (first-run persistence). Subsequent calls read from the file.
- Tests use `tmp_path` + monkeypatch of `Path.home`/HOME and `socket.gethostname` to verify persistence.

### P1-05 — `config.example.toml`
- Add a sync example block matching plan §P1 config example.
- `*.icloud` must be in default `exclude_globs` example for markdown_vault.

### P1-06 — `EchoSuppressor`
- Class in `corpus_forge/sync/echo.py`.
- API:
  - `EchoSuppressor(default_ttl_s: float = 5.0, clock: Callable[[], float] = time.monotonic)`
  - `register(path: Path, content_hash: str, ttl_s: float | None = None) -> None`
  - `was_just_written(path: Path, content_hash: str) -> bool` — also clears the entry when matched
  - `gc(now: float | None = None) -> None`
- Keys: `str(path.resolve())`. Values: `(content_hash, expires_at)`.
- Injectable clock for tests. Thread-safety policy → see open-questions.md.

### P1-07 — `detect_cloud_provider`
- `detect_cloud_provider(path: Path) -> Literal["icloud","dropbox","gdrive","none"]`.
- Substring match on `str(path.resolve())` against:
  - iCloud: `Library/Mobile Documents/com~apple~CloudDocs`, `Library/Mobile Documents/iCloud~`
  - Dropbox: `Dropbox`
  - Google Drive: `Google Drive`, `GoogleDrive`, `My Drive`
- Returns first match in plan-defined precedence; `none` if no match.

### P1-08 — `is_cloud_duplicate`
- `is_cloud_duplicate(path: Path) -> tuple[bool, str | None, Path | None]` returning `(matched, provider, canonical_path)`.
- Patterns:
  - iCloud: `<stem> 2<suffix>`, `<stem> 3<suffix>`, `<stem> (n)<suffix>`
  - Dropbox: `<stem> (<host>'s conflicted copy <date>)<suffix>`
  - Google Drive: `<stem> (1)<suffix>`, `<stem>-conflict-<date>-<n><suffix>`
  - macOS Finder: `<stem> copy<suffix>`, `<stem> copy 2<suffix>`
- `canonical_path` is the inferred original (e.g., `Foo 2.md` → `Foo.md`) to compare hashes against. None if undecidable.

### P1-09 — `conflict_filename`
- `conflict_filename(original: Path, host: str, ts: datetime, provider: str | None = None) -> Path` returning the renamed conflict path.
- Format: `<stem>.conflict-<host>-<isoZ-no-colons><suffix>` (no provider) or `<stem>.conflict-<provider>-<host>-<isoZ-no-colons><suffix>`.
- Timestamp format: stable, sortable, filesystem-safe (e.g., `20260507T223045Z`).

### P1-10 — `atomic_write_text`
- `atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None`.
- Writes to `<path>.tmp.<rand>` in same directory, fsyncs, `os.replace` to target.
- Creates parent directories.

### P1-11 — `move_to_trash`
- `move_to_trash(src: Path, trash_root: Path, dataset_name: str, host: str, rel_path: Path | None = None) -> Path`.
- Destination: `<trash_root>/<dataset>/<rel-path or src.name>.deleted-<host>-<ts><suffix>`.
- Uses `os.replace` (atomic on same filesystem). If cross-device, fall back to copy+unlink.
- Creates parents.

### P1-12 — iCloud / dataless guards
- `is_icloud_placeholder(path: Path) -> bool` — true if `path.suffix == ".icloud"` and size == 0, or any of the well-known placeholder name patterns (`<stem>.icloud`).
- `is_dataless(path: Path) -> bool` — best-effort check via `xattr` (`com.apple.fileprovider.materialized`); on failure return False (do not block real edits).

### P1-13 — `insert_revision`
- `insert_revision(self, *, document_id: int, source_uri: str, content_hash: str, text: str, parent_revision_id: int | None, author_host: str, is_tombstone: bool, metadata: dict | None = None) -> dict`.
- Holds `lock_source(source_uri)` through `MAX(revision_number)+1` allocation and INSERT.
- Returns the inserted row (or at minimum `{"id", "revision_number"}`).

### P1-14 — `latest_revision`
- `latest_revision(self, document_id: int) -> dict | None` returning the highest `revision_number` row for the document, or None.
- Convenience overload `latest_revision_for_uri(self, dataset_id: int, source_uri: str) -> dict | None` welcome but optional.

### P1-15 — `pending_remote_revisions`
- `pending_remote_revisions(self, dataset_id: int, last_pulled_revision_id: int | None, self_host: str, *, limit: int = 1024) -> list[dict]`.
- Joins documents to filter dataset; `r.id > $last AND r.author_host <> $self`; `ORDER BY r.id ASC`; `LIMIT`.

### P1-16 — `mark_revision_pulled`
- `mark_revision_pulled(self, source_id: int, revision_id: int) -> None`.
- `UPDATE sources SET last_pulled_revision_id = GREATEST(coalesce(last_pulled_revision_id, 0), %s) WHERE id = %s`.

### P1-17 — `set_tombstone` / clear
- `set_tombstone(self, document_id: int) -> None` — `UPDATE documents SET tombstoned_at = NOW() WHERE id = %s`.
- `clear_tombstone(self, document_id: int) -> None` — sets to NULL.

### P1-18 — Push event handler core
- Class `PushPipeline` (or `PushHandler`), constructor takes backend, dataset_id, source_uri-resolver, host_id, EchoSuppressor, mtime-cache.
- Method `handle_change(path: Path) -> None`:
  1. mtime pre-filter (skip if unchanged).
  2. Compute content_hash; echo check; drop if matched.
  3. Inside `lock_source`:
     - Read `latest_revision` for the doc (resolve doc by `(dataset_id, source_uri)`; insert minimal document row first if missing — reuse existing `upsert_document` short-circuit semantics).
     - If `local_hash == latest.content_hash`: no-op.
     - Else: `insert_revision(parent=latest.id, content_hash, text, author_host=host_id)` then `upsert_document` (now reuse-aware via P0-06).

### P1-19 — Watchdog observer wiring + debounce
- `PushPipeline.start()` spins up a `watchdog.Observer` rooted at the source's `root`.
- Skips: directories, hidden files, `exclude_globs` matches, `*.icloud` placeholders, dataless entries.
- Debounces per-path with `daemon.debounce_seconds`. Final event after debounce calls `handle_change`.
- `PushPipeline.stop()` joins the observer cleanly.

### P1-20 — Cloud-duplicate cleanup
- In `handle_change` (or a dedicated pre-step):
  - If `is_cloud_duplicate(path)` and `canonical_path` exists: read both, hash both.
  - Same hash → log + `path.unlink()`. No revision inserted.
  - Different hash → rename `path` to `conflict_filename(canonical_path, host=…, provider=…, ts=…)`, then proceed to ingest as a conflict revision (still goes through `insert_revision` with the new path's source_uri).

### P1-21 — Tombstone-on-delete
- Watchdog `on_deleted` calls `PushPipeline.handle_delete(path)`.
- Inside `lock_source`:
  - If `is_icloud_placeholder` for sibling `<path>.icloud` exists OR was the deleted file itself a placeholder → no-op.
  - Else: `insert_revision(text="", content_hash=sha256(b""), is_tombstone=True, parent=latest.id)` and `set_tombstone(document_id)`.

### P1-22 — Pull tick + fast-forward
- `PullPipeline.tick() -> int` — returns count of revisions applied.
- For each pending revision (oldest first):
  - Resolve target path from `documents.source_uri` + dataset's source root.
  - Compute local hash (or treat as None if file missing).
  - **Fast-forward**: local hash == parent's `content_hash` (or local missing and parent NULL) → `atomic_write_text` and `EchoSuppressor.register`. Then `mark_revision_pulled`.

### P1-23 — Already-in-sync branch
- Local hash already == new revision's `content_hash` → skip write, `EchoSuppressor.register`, `mark_revision_pulled`.

### P1-24 — Conflict branch
- Local matches neither parent nor incoming:
  - Rename local file to `conflict_filename(path, host=local_host, ts=now)` (or copy + write).
  - `atomic_write_text` incoming.
  - `EchoSuppressor.register` for the canonical path only.
  - Do **not** insert a new revision for the saved-aside conflict file — the next push cycle will pick it up as a new document/revision.

### P1-25 — Tombstone branch
- Tombstone revision: `move_to_trash(path, trash_root, dataset_name, author_host, rel_path)`; `set_tombstone(document_id)`.
- If a non-tombstone revision arrives later for the same `document_id`, normal flow re-creates the file and `clear_tombstone` should fire (handled by P1-17 + the fast-forward branch).

### P1-26 — Pull lifecycle
- `PullPipeline.start()` launches a thread (or asyncio task — see open-questions) calling `tick` every `sync_poll_interval_s`.
- `PullPipeline.stop()` signals the loop to exit and joins.

### P1-27 — `SyncEngine`
- `SyncEngine(dataset_config, source, backend, embedders, host_id, daemon_config)`.
- `start()`: starts push pipeline + pull pipeline.
- `stop()`: stops both, flushes pending revisions.

### P1-28 — Daemon orchestrator
- For each dataset: if `sync_enabled`, build a `SyncEngine` and `start()` it; else fall through to `ingest_main(once=False)` for that dataset.
- Signal handlers (`SIGINT`/`SIGTERM`) call `stop()` on every engine then exit.
- Test via subprocess or by directly invoking `main()` with monkeypatched engines.

### P1-29 — `sync` CLI subgroup
- `sync_app = typer.Typer(...)`; `app.add_typer(sync_app, name="sync")`.
- `status`: per-dataset summary (last_pulled_revision_id, pending count, conflict file count under `conflict_dir`/source root).
- `pull --once|--continuous -d DATASET`.
- `push -d DATASET`: force rescan + push pending changes.
- `resolve CONFLICT_FILE --strategy keep-local|keep-remote`. `merge` raises `typer.BadParameter("strategy 'merge' is a P2 feature")`.
- `history SOURCE_URI [--limit N]`: lists revisions (read-only).

### P1-30 — E2E push/pull
- Two `SyncEngine`s in one process, different `host_id`, different `tmp_path` roots, shared testcontainers Postgres.
- Edit on A → polled on B within `sync_poll_interval_s` (test uses very small interval, e.g. 0.2s).
- Hash on B equals hash on A.

### P1-31 — E2E tombstone
- Delete on A → tombstone revision → file appears in B's trash dir; `documents.tombstoned_at` set.
- Re-create on A → file re-appears on B; `tombstoned_at` cleared.

### P1-32 — E2E iCloud-dupe
- Drop `Foo 2.md` matching hash next to `Foo.md` → push deletes `Foo 2.md`; only one document exists.
- Drop `Foo 2.md` differing from `Foo.md` → push renames it to `Foo.conflict-icloud-<host>-<ts>.md` and inserts a conflict revision.

## DAG (waves at-a-glance — see waves.md for parallelism rationale)

- **Wave 0 (P0 foundation, fully parallel):** P0-01, P0-02, P1-01, P1-03, P1-05, P1-06, P1-07, P1-09, P1-10, P1-12.
- **Wave 1 (depends on Wave 0):** P0-03, P0-04, P1-02, P1-04, P1-08, P1-11.
- **Wave 2:** P0-05, P1-13, P1-14, P1-15, P1-16, P1-17.
- **Wave 3:** P0-06.
- **Wave 4:** P0-07.
- **Wave 5 (P0 gate):** P0-08. _All P0 work must finish before any subsequent wave starts on P1 push/pull._
- **Wave 6:** P1-18, P1-22.
- **Wave 7:** P1-19, P1-23, P1-24, P1-25.
- **Wave 8:** P1-20, P1-21, P1-26.
- **Wave 9:** P1-27.
- **Wave 10:** P1-28, P1-29.
- **Wave 11 (E2E):** P1-30, P1-31, P1-32.

## Out of scope

P2 features are explicitly deferred per plan §P2: `LISTEN/NOTIFY` low-latency channel, `sync resolve --strategy merge`, `sync history` 3-way merge, section-level merge, tombstone retention sweeper, revision compaction, content-addressed `chunk_texts` table.
