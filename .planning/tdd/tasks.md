# TDD Task Board — Active Directory Sync

_Owner: tdd-principal. Workers: read freely. Edit only your claimed row's `status` and `claimed_by`._

Source plan: `/Users/evanowen/Library/Mobile Documents/com~apple~CloudDocs/Workspace/playground/corpus-forge/.planning/active_directory_sync.md`

**Phase A/C status**: Active Directory Sync (Waves 0–13) — **complete**. 668 unit + 102 integration tests green; ≥85% coverage; ruff/format/pyrefly all clean.

**Phase B (active, separate board)**: SQLite backend at `.planning/tdd/sqlite_backend.md` (task ids `B-01..B-18`). Worker entries cross-linked into `code-status.md`, `test-status.md`, `qa-status.md` Phase B sections. Do NOT add `B-*` rows to the table below.

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
| P0-03 | Migration runner backfills `chunks.content_hash` | P0-01, P0-02 | `corpus_forge/schema/migrate.py`, `tests/integration/test_migrate_002.py` | med | done | tdd-qa | Backfill implemented. QA approved. |
| P0-04 | `upsert_document` writes `content_hash` on chunk insert | P0-01, P0-02 | `corpus_forge/backends/postgres.py`, `tests/unit/test_postgres_backend.py` (or fixture in test_chunk_reuse) | low | done | tdd-qa | content_hash in INSERT. QA approved. |
| P0-05 | `_copy_reusable_embeddings` helper on `PostgresBackend` | P0-04 | `corpus_forge/backends/postgres.py`, `tests/unit/test_chunk_reuse.py` | med | done | tdd-qa | Per-call cache `(content_hash, embedder_id) → chunk_id`. Returns set of reused embedder_ids. QA approved. |
| P0-06 | `upsert_document` accepts `embedder_ids` and triggers reuse | P0-05 | `corpus_forge/backends/postgres.py`, `corpus_forge/backends/base.py`, `tests/unit/test_chunk_reuse.py` | med | done | tdd-qa | Signature change: `upsert_document(..., embedder_ids: list[int] \| None = None)`. Backwards-compat default keeps None = no reuse. QA approved. |
| P0-07 | `ingest_one` passes active embedder ids into `upsert_document` | P0-06 | `corpus_forge/ingest.py`, `tests/unit/test_ingest_core.py` (or test_ingest_helpers) | low | done | tdd-qa | Resolve embedder_id via `backend.register_embedder(e)` once per call, pass list. QA approved. |
| P0-08 | E2E reuse test (testcontainers) | P0-03..P0-07, INT-01 | `tests/integration/test_chunk_reuse_e2e.py` | med | done | tdd-coder | Wave 13b: BUG-2 (_copy_reusable_embeddings INSERT missing embedder_id) + BUG-3 (UPDATE-in-place chunk reuse) fixed in postgres.py. 668 unit tests green. |
| P1-01 | `003_sync.sql` migration file (DDL only) | P0-02 (file ordering) | `corpus_forge/schema/003_sync.sql` | low | done | tdd-tester | DDL created. 31 tests green. |
| P1-02 | Migration runner applies `003_sync.sql` cleanly + idempotent | P1-01 | `corpus_forge/schema/migrate.py`, `tests/integration/test_migrate_003.py` | low | done | tdd-qa | Existing runner already loops numbered files; just confirm. QA approved. |
| P1-03 | Pydantic config sync fields + validators | — | `corpus_forge/config.py`, `tests/unit/test_config.py` (and/or test_config_extended) | low | done | tdd-qa | 58/58 pass. QA approved. |
| P1-04 | `Config.host_id()` resolution + first-run persistence | P1-03 | `corpus_forge/config.py`, `tests/unit/test_config_extended.py` | med | done | tdd-qa | host_id() implemented. QA approved. |
| P1-05 | `config.example.toml` + default exclude_globs | P1-03 | `config.example.toml` | low | done | tdd-tester | TOML valid. |
| P1-06 | `EchoSuppressor` in `sync/echo.py` | — | `corpus_forge/sync/echo.py`, `tests/unit/test_sync_echo.py` | low | done | tdd-qa | 28/28 pass. QA approved. |
| P1-07 | `sync/cloud.py::detect_cloud_provider` | — | `corpus_forge/sync/cloud.py`, `tests/unit/test_sync_cloud.py` | low | done | tdd-qa | 30/30 pass. QA approved. |
| P1-08 | `sync/conflicts.py::is_cloud_duplicate` | P1-07 | `corpus_forge/sync/conflicts.py`, `tests/unit/test_sync_conflicts.py` | med | done | tdd-qa | 77/77 pass. QA approved. |
| P1-09 | `sync/conflicts.py::conflict_filename` | — | `corpus_forge/sync/conflicts.py`, `tests/unit/test_sync_conflicts.py` | low | done | tdd-qa | 45/45 pass. QA approved. |
| P1-10 | `sync/fs.py::atomic_write_text` | — | `corpus_forge/sync/fs.py`, `tests/unit/test_sync_fs.py` | low | done | tdd-qa | 38/38 pass. QA approved. |
| P1-11 | `sync/fs.py::move_to_trash` | P1-03 | `corpus_forge/sync/fs.py`, `tests/unit/test_sync_fs.py` | low | done | tdd-qa | 55/55 pass. QA approved. |
| P1-12 | `sync/fs.py::is_icloud_placeholder` and dataless guards | — | `corpus_forge/sync/fs.py`, `tests/unit/test_sync_fs.py` | med | done | tdd-qa | Detect `*.icloud` 0-byte stubs and `com.apple.fileprovider.materialized` xattr. QA approved. |
| P1-13 | `PostgresBackend.insert_revision` | P1-02 | `corpus_forge/backends/postgres.py`, `corpus_forge/backends/base.py`, `tests/integration/test_backend.py` (revision section) or new `tests/integration/test_revisions.py` | med | done | tdd-qa | Allocates `revision_number = MAX+1` under `lock_source`. Sets `parent_revision_id` from caller-provided latest. Collapsed with P1-14..P1-17 as Wave 3. QA approved. |
| P1-14 | `PostgresBackend.latest_revision` | P1-02 | `corpus_forge/backends/postgres.py`, `tests/integration/test_revisions.py` | low | done | tdd-qa | `SELECT … ORDER BY revision_number DESC LIMIT 1` for a `document_id` (or by `source_uri` lookup). QA approved. |
| P1-15 | `PostgresBackend.pending_remote_revisions` | P1-02 | `corpus_forge/backends/postgres.py`, `tests/integration/test_revisions.py` | med | done | tdd-qa | `WHERE r.id > $last AND r.author_host <> $self ORDER BY r.id ASC`. Joins documents to filter by `dataset_id`. QA approved. |
| P1-16 | `PostgresBackend.mark_revision_pulled` | P1-15 | `corpus_forge/backends/postgres.py`, `tests/integration/test_revisions.py` | low | done | tdd-qa | `UPDATE sources SET last_pulled_revision_id = GREATEST(coalesce(last_pulled_revision_id,0), $1)`. QA approved. |
| P1-17 | `PostgresBackend.set_tombstone` (and clear on resurrect) | P1-13 | `corpus_forge/backends/postgres.py`, `tests/integration/test_revisions.py` | low | done | tdd-qa | Sets/clears `documents.tombstoned_at`. Called by pull pipeline. QA approved. |
| P1-18 | `sync/push.py::PushPipeline.handle_event` core (mtime cache + hash + echo + lock + insert) | P0-07, P1-04, P1-06, P1-13, P1-14 | `corpus_forge/sync/push.py`, `tests/unit/test_sync_push.py` | high | done | tdd-qa | One handler call per event. Tests use a fake backend + temp file; watchdog observer not required at this layer. QA approved. |
| P1-19 | `sync/push.py` watchdog observer wiring + debounce | P1-18 | `corpus_forge/sync/push.py`, `tests/unit/test_sync_push.py` | med | done | tdd-qa | `watchdog.Observer`, exclude_globs, hidden-file filter, `*.icloud` filter, dataless guard, debounce per-path. QA approved. |
| P1-20 | `sync/push.py` cloud-duplicate cleanup branch | P1-08, P1-09, P1-19 | `corpus_forge/sync/push.py`, `tests/unit/test_sync_push.py` | med | done | tdd-qa | When `is_cloud_duplicate` matches: same hash → delete; different hash → rename to `conflict_filename(provider=…)` + ingest as conflict revision. QA approved. |
| P1-21 | `sync/push.py` tombstone-on-delete handler | P1-12, P1-13 | `corpus_forge/sync/push.py`, `tests/unit/test_sync_push.py` | med | done | tdd-qa | Watchdog delete event → tombstone revision. Suppress when `*.icloud` placeholder remains (eviction, not delete). QA approved. |
| P1-22 | `sync/pull.py::PullPipeline.tick` (single poll cycle, fast-forward branch) | P1-06, P1-10, P1-14, P1-15, P1-16 | `corpus_forge/sync/pull.py`, `tests/unit/test_sync_pull.py` | high | done | tdd-qa | Pulls pending, fast-forward-writes when local hash matches parent, registers echo, advances `last_pulled_revision_id`. QA approved. |
| P1-23 | `sync/pull.py` already-in-sync branch | P1-22 | `corpus_forge/sync/pull.py`, `tests/unit/test_sync_pull.py` | low | done | tdd-qa | Local hash already == new revision hash → register echo only, advance pointer. QA approved. |
| P1-24 | `sync/pull.py` conflict branch (non-destructive LWW) | P1-09, P1-22 | `corpus_forge/sync/pull.py`, `tests/unit/test_sync_pull.py` | high | done | tdd-qa | Local matches neither parent nor new → write incoming canonical, save local as `<stem>.conflict-<host>-<ts><suffix>`. Conflict file gets ingested next push cycle (do not insert revision here). QA approved. |
| P1-25 | `sync/pull.py` tombstone branch | P1-11, P1-17, P1-22 | `corpus_forge/sync/pull.py`, `tests/unit/test_sync_pull.py` | med | done | tdd-qa | Tombstone revision → `move_to_trash`, set `documents.tombstoned_at`. QA approved. |
| P1-26 | `sync/pull.py` poll-loop / lifecycle | P1-22..P1-25 | `corpus_forge/sync/pull.py`, `tests/unit/test_sync_pull.py` | med | done | tdd-qa | Thread-driven loop with `sync_poll_interval_s`. Stop event for clean shutdown. QA approved. |
| P1-27 | `sync/engine.py::SyncEngine` lifecycle (start/stop both halves per dataset) | P1-19, P1-26 | `corpus_forge/sync/engine.py`, `corpus_forge/sync/__init__.py` (export), `tests/unit/test_sync_engine.py` | med | done | tdd-qa | Owns push pipeline + pull pipeline. `start()` non-blocking, `stop()` flushes. QA approved. |
| P1-28 | `daemon.py` orchestrator: per-dataset SyncEngine vs ingest_main | P1-27, P1-04 | `corpus_forge/daemon.py`, `tests/unit/test_daemon.py` | med | done | tdd-qa | Replaces 39-line stub. Block on signals, call `engine.stop()` on shutdown. QA approved. |
| P1-29 | CLI `sync` Typer subgroup | P1-04, P1-13..P1-17, P1-09 | `corpus_forge/cli.py`, `tests/unit/test_cli_sync.py` | med | done | tdd-qa | Commands: `status`, `pull --once/--continuous -d DATASET`, `push`, `resolve CONFLICT_FILE --strategy keep-local\|keep-remote`, `history SOURCE_URI [--limit N]`. P2 strategies (`merge`) raise NotImplemented with friendly error. QA approved. |
| P1-30 | E2E push→pull integration test (testcontainers) | P1-27, P1-28, INT-01 | `tests/integration/test_sync_push_pull.py` | high | done | tdd-coder | Wave 13b: All 4 bugs fixed. resolve_document + find_document added to PostgresBackend; insert_revision source_uri kwarg; pending_remote_revisions JOIN documents; pull._resolve_path cross-host fix. 668 unit tests green. |
| P1-31 | E2E tombstone integration test | P1-25, P1-27, INT-01 | `tests/integration/test_sync_tombstone.py` | med | done | tdd-coder | Wave 13b: All bugs fixed. psycopg3 LIKE %% escaping fixed in tests; resolve_document/find_document/mark_revision_pulled production bugs fixed; 668 unit tests green. |
| P1-32 | E2E iCloud-duplicate integration test | P1-08, P1-20, INT-01 | `tests/integration/test_sync_icloud_dupe.py` | med | done | tdd-coder | Wave 13b: BUG-7 (cloud-duplicate early exit wired into handle_change) + resolve_document production bug fixed. 668 unit tests green. |
| INT-01 | DSN fixture refactor (libpq DSN) | — | `tests/conftest.py`, `tests/integration/test_backend.py`, `tests/integration/test_embedder_contract.py`, `tests/integration/test_ingest.py`, `tests/integration/test_migrate_002.py`, `tests/integration/test_migrate_003.py` | low | done | tdd-coder | Wave 13. testcontainers' `pg.get_connection_url()` returns SQLAlchemy-style `postgresql+psycopg2://…` which `psycopg.connect` rejects. Centralized `postgres_container` (session-scoped) + `pg_dsn` (function-scoped) + `pg` (alias) fixtures in conftest; refactored 5 files. 5/5 test_dsn_fixture pass. No DSN-format errors remain. Residual integration failures are pre-existing production bugs (INT-02). |
| INT-02 | Triage residual integration failures | INT-01 | `tests/integration/*`, `corpus_forge/backends/postgres.py`, `corpus_forge/embedders/registry.py`, `corpus_forge/embedders/sentence_transformers.py`, `corpus_forge/chunkers/base.py`, `corpus_forge/sources/markdown_vault.py` | med | done | tdd-coder | 73/73 integration + 668/668 unit pass. Fixed: (1) SQL comment semicolon in migrate; (2) pg.get_connection() → psycopg.connect(pg_dsn) in all 3 integration test files; (3) EmbedderRegistry.register() in-place overwrite; (4) encode([]) empty-list guard; (5) vault fixture dotfile rename + default excludes; (6) MarkdownChunker heading extraction. Also fixed: postgres.py TIMESTAMPTZ conversion, dataset name collisions in session-scoped container, doc-id vs chunk-id mix-up. Format: clean. Typecheck: 19 errors (all pre-existing). |
| DOC-01 | Doc cleanup (stale revisions claim + blocked→done transitions) | INT-01, P0-08, P1-30, P1-31, P1-32 | `.planning/tdd/tasks.md`, `.planning/tdd/code-status.md` | low | done | tdd-principal | Wave 13 closeout. Stale "1 failed test" line removed. Wave 6 + 12 flipped to DONE. Wave 13 summary appended. |

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

### INT-01 — DSN fixture refactor
- Add a `postgres_container` (or `pg`, conftest-scoped) fixture in `tests/conftest.py` that wraps `PostgresContainer("pgvector/pgvector:pg17", port=5432)` and yields the container.
- Add a `pg_dsn` fixture that takes the container and returns a libpq DSN: `f"postgresql://{c.username}:{c.password}@{c.get_container_host_ip()}:{c.get_exposed_port(5432)}/{c.dbname}"`. (Stripping `+psycopg2` from `get_connection_url()` is acceptable as long as the test asserts the resulting string parses with `psycopg.conninfo.conninfo_to_dict`.)
- Refactor each of the five existing integration test files to consume the new fixtures. The old per-file `pg` fixture and `_make_backend(pg)` helpers go away or become thin wrappers.
- Centralize the pgvector extension creation in the conftest fixture (today only `pgvector_container` does it; the per-file `pg` fixtures rely on `backend.migrate()` doing CREATE EXTENSION via `001_core.sql`).
- Failing micro-test (red signal for tester): a smoke test `tests/integration/test_dsn_fixture.py` (or a `tests/unit/test_conftest_dsn_smoke.py`) asserting `pg_dsn` parses cleanly via `psycopg.conninfo.conninfo_to_dict` and starts with `postgresql://` (no `+psycopg2`).
- Acceptance: after the refactor, `PYTHONPATH=. uv run pytest tests/integration -v` shows 0 failures attributable to `psycopg.ProgrammingError: missing "="`. Container start cost reduced (one fixture, not five).

### INT-02 — Triage residual integration failures
- Only runs if INT-01 leaves any failure. tdd-qa enumerates remaining failures from the INT-01 sign-off run.
- Triage each: real production bug → file follow-up under a new task id; flaky test setup → fix in place.
- Known suspects (pre-DSN-fix): `test_advisory_lock_context`, `test_advisory_lock_conflict` (unclear if DSN-only), `test_chunks_missing_embedding` (state leakage across module-scoped container), `test_write_embeddings_empty_does_not_raise` (likely embedder_id 1 not actually registered), `test_embedder_contract::test_duplicate_register_overwrites` and `test_encode_empty_list` (may not need container at all).
- Suspected real bug to verify: `corpus_forge/sync/pull.py:71` does `path = self._source_root / rev["source_uri"]`. If push records `source_uri = str(path.resolve())` (absolute), `Path("/x") / "/y"` returns `Path("/y")` so `source_root` is silently dropped. Confirm via P1-30 first; if reproducing, decide whether to (a) record relative `source_uri` from push, or (b) compute relative on pull. **Do not pre-judge — let the failing E2E test surface it.**

### DOC-01 — Doc cleanup
- In `tasks.md`:
  - Remove the line "One pre-existing test assertion bug in `test_revisions.py`" from the Summary's Known deferred items.
  - Update the Summary's `test:` line: re-run unit suite and report current pass count (no longer "644 passed, 1 failed").
  - Update the Summary's `integration / E2E smoke:` line to "all green" with counts.
  - Re-flag DAG entries: Wave 6 (P0-08) and Wave 12 (P1-30..P1-32) move from blocked to ✅ DONE.
- In `code-status.md`:
  - The `P1-13..P1-17` row's "1 test has bug …" claim is stale (test now passes); update note to reflect green.
- Append a Wave 13 summary block below the existing Summary, listing files changed, gates run, and integration pass counts.

## DAG (waves at-a-glance — see waves.md for parallelism rationale)

- **Wave 0 (✅ DONE):** P0-01, P0-02, P1-01, P1-03, P1-05, P1-06, P1-07, P1-09, P1-10.
- **Wave 1 (✅ DONE):** P0-03, P0-04, P1-04, P1-08, P1-11.
- **Wave 2 (✅ DONE):** P1-02, P1-12, P0-05.
- **Wave 3 (✅ DONE):** P1-13..P1-17 (revision API, collapsed cycle). Fixed sort-key bug.
- **Wave 4 (✅ DONE):** P0-06.
- **Wave 5 (✅ DONE):** P0-07.
- **Wave 6 (✅ DONE in Wave 13b):** P0-08. Originally blocked on Docker; landed once Docker came online.
- **Wave 7 (✅ DONE):** P1-18, P1-22.
- **Wave 8 (✅ DONE):** P1-19, P1-23, P1-24, P1-25.
- **Wave 9 (✅ DONE):** P1-20, P1-21, P1-26.
- **Wave 10 (✅ DONE):** P1-27.
- **Wave 11 (✅ DONE):** P1-28, P1-29.
- **Wave 12 (✅ DONE in Wave 13b):** P1-30, P1-31, P1-32.
- **Wave 13 (✅ DONE) — Integration test rehab:** INT-01 → INT-02 → {P0-08, P1-30, P1-31, P1-32} parallel → bug-fix coder → DOC-01.

## Out of scope

P2 features are explicitly deferred per plan §P2: `LISTEN/NOTIFY` low-latency channel, `sync resolve --strategy merge`, `sync history` 3-way merge, section-level merge, tombstone retention sweeper, revision compaction, content-addressed `chunk_texts` table.

## Summary

Implementation of the Active Directory Sync feature is complete across 12 waves (32 tasks).

### Files changed (new + modified)

| File | Role |
|------|------|
| `corpus_forge/identity.py` | `chunk_content_hash()` helper |
| `corpus_forge/config.py` | Sync fields on DaemonConfig/DatasetConfig + `host_id()` |
| `corpus_forge/schema/002_chunk_content_hash.sql` | Content hash DDL |
| `corpus_forge/schema/003_sync.sql` | Document revisions DDL |
| `corpus_forge/schema/migrate.py` | Migration runner + backfill (**fixed sort-key extraction**) |
| `corpus_forge/backends/postgres.py` | Reusable embeddings, revision API, upsert_document embedder_ids |
| `corpus_forge/sync/echo.py` | EchoSuppressor |
| `corpus_forge/sync/cloud.py` | `detect_cloud_provider` |
| `corpus_forge/sync/conflicts.py` | `is_cloud_duplicate`, `conflict_filename` |
| `corpus_forge/sync/fs.py` | `atomic_write_text`, `move_to_trash`, `is_icloud_placeholder`, `is_dataless` |
| `corpus_forge/sync/push.py` | PushPipeline (handler core + watchdog observer + cloud-dupe + tombstone) |
| `corpus_forge/sync/pull.py` | PullPipeline (tick + all 4 branches + lifecycle) |
| `corpus_forge/sync/engine.py` | SyncEngine orchestration |
| `corpus_forge/daemon.py` | run_daemon with signal handling |
| `corpus_forge/cli.py` | sync CLI subgroup (5 commands) |
| `corpus_forge/ingest.py` | embedder_ids pass-through in ingest_one |
| `config.example.toml` | Sync config example |

### Gates run (post-Wave 13)
- **lint**: `uv run ruff check corpus_forge tests` — pre-existing 437 warnings (PLR2004/PLC0415 patterns from before Wave 13); 0 new errors introduced.
- **format**: `uv run ruff format --check corpus_forge tests` — clean (83 files).
- **typecheck**: `uv run pyrefly check corpus_forge` — 17 pre-existing errors (down from 19).
- **test (unit)**: 668 passed, 8 skipped (OpenAI deps), 0 failed.
- **coverage**: ≥85% threshold maintained.
- **test (integration)**: 102 passed, 9 warnings, 0 failed.

### Known deferred items
- `resolve --strategy merge` is a P2 feature
- `LISTEN/NOTIFY` low-latency channel is a P2 feature

## Wave 13 Summary — Integration test rehab

Triggered when Docker Desktop came online. Surface: the four "blocked" E2E test files (P0-08, P1-30, P1-31, P1-32) were never actually written; the existing 5 integration files used a SQLAlchemy-flavoured DSN that psycopg rejects.

### Files changed in Wave 13

| File | Role |
|------|------|
| `tests/conftest.py` | Centralized session-scoped `postgres_container` + `pg_dsn` (libpq) + `pg` alias. Removed unused `pgvector_container`, fixed duplicate `temp_dir`. |
| `tests/integration/test_dsn_fixture.py` | New — pins libpq DSN contract. |
| `tests/integration/test_backend.py` | Refactored to use `pg_dsn` + `psycopg.connect(pg_dsn)`. |
| `tests/integration/test_ingest.py` | Same; vault dotfile fixture; unique source_uri per test. |
| `tests/integration/test_migrate_002.py`, `test_migrate_003.py` | Same DSN refactor. |
| `tests/integration/test_chunk_reuse_e2e.py` | New — P0-08 reuse pin (≥7/10 + encoder spy ≤3). |
| `tests/integration/test_sync_push_pull.py` | New — P1-30 cross-host push/pull pin. |
| `tests/integration/test_sync_tombstone.py` | New — P1-31 delete-and-resurrect pin. |
| `tests/integration/test_sync_icloud_dupe.py` | New — P1-32 iCloud-dupe cleanup pin. |
| `corpus_forge/backends/postgres.py` | Added `resolve_document`, `find_document`. Fixed `_copy_reusable_embeddings` (embedder_id), `upsert_document` reuse-before-delete semantics, `pending_remote_revisions` JOIN-and-select source_uri/source_id/parent_content_hash, `mark_revision_pulled` correctness. |
| `corpus_forge/sync/push.py` | source_uri now relative to source_root; `insert_revision` source_uri kwarg passed; `_handle_cloud_duplicate` wired into `handle_change`; `upsert_document` call uses real RawDocument. |
| `corpus_forge/sync/pull.py` | reads `source_uri` (relative) and `source_id` from joined query; resolves local path correctly. |
| `corpus_forge/sync/engine.py`, `corpus_forge/sync/__init__.py` | Wire source_root + chunker + embedders into PushPipeline construction. |
| `corpus_forge/embedders/registry.py`, `embedders/sentence_transformers.py`, `chunkers/base.py`, `sources/markdown_vault.py`, `schema/001_core.sql`, `schema/migrate.py` | INT-02 surgical fixes (overwrite-in-place register; encode([]) guard; chunker heading; markdown_vault excludes; SQL comment semicolon). |

### Bugs surfaced and fixed

| Bug | File | Symptom | Fix |
|-----|------|---------|-----|
| Migration SQL comment wrap | `postgres.py:77-78` | `--` only comments first line; line 78 parsed as SQL → syntax error | Collapse comment to one line |
| testcontainers `get_connection_url()` | 5 integration files | Returns `postgresql+psycopg2://...`; psycopg rejects | Centralized `pg_dsn` fixture |
| `pg.get_connection()` removed | testcontainers 4.x | AttributeError | `psycopg.connect(pg_dsn)` |
| Embedder registry overwrite | `registry.py` | new instance instead of in-place | overwrite-in-place |
| `encode([])` shape | `sentence_transformers.py` | shape mismatch | early return `np.empty((0, dim))` |
| Markdown chunker heading | `chunkers/base.py` | heading not preserved | `_extract_heading` + `_create_chunk` |
| `markdown_vault` defaults | `sources/markdown_vault.py` | wrong excludes/casing | `[".obsidian/**", ".trash/**"]` + Path normalize |
| `_copy_reusable_embeddings` INSERT | `postgres.py` | NOT NULL on embedder_id | added column to SELECT |
| Reuse race in `upsert_document` | `postgres.py` | DELETE before lookup → 0% reuse | UPDATE-in-place (or snapshot before delete) |
| Push: `resolve_document` missing | `push.py:84` | AttributeError per file event | added `resolve_document` to backend |
| Push: `insert_revision` missing source_uri | `push.py:96` | TypeError on call | pass `source_uri=source_uri` |
| Push: absolute source_uri | `push.py:82` | pull on different host can't reconstruct path | record relative to source_root |
| Push: `upsert_document(None, [])` | `push.py:105` | crash on `doc.source_uri` | build real RawDocument from text |
| Push: dead `_handle_cloud_duplicate` | `push.py` | iCloud dupes never cleaned up | wire into `handle_change` |
| Pull: missing `source_uri` row field | `pull.py:69` | KeyError on `rev["source_uri"]` | JOIN documents in `pending_remote_revisions` |
| Pull: missing `source_id` row field | `pull.py:82` | KeyError on `rev["source_id"]` | JOIN sources or self-resolve |
| Pull: missing `parent_content_hash` | `pull.py:71` | always None | LEFT JOIN parent revision |

### Verification

`PYTHONPATH=. uv run pytest tests/integration -v` → **102 passed, 9 warnings, 0 failed**.
`PYTHONPATH=. uv run pytest tests/unit --cov-fail-under=85` → **668 passed, 8 skipped, 0 failed**.

### Stale claims removed

- "1 pre-existing test assertion bug in `test_revisions.py`" — verified 22/22 green; no casing bug present.
- Wave 6 (P0-08) and Wave 12 (P1-30..P1-32) flipped from `blocked` to ✅ DONE.
- "6 integration tests blocked" claim updated: 102 integration tests passing.

## Phase CI-1 Summary — CI foundation + stability harness

Plan: `/Users/evanowen/.claude/plans/crispy-yawning-crescent.md` (first phase
of beta-release milestone).

### Dispatch mode

**Option B (fused principal+coder)** — Agent tool not available in this
session; principal absorbed tester+coder roles. Four atomic commits with
`[tdd-principal] phase-ci-1:` prefix.

### Slices landed

| slice | commit | gist |
|-------|--------|------|
| 1 | `25c54d9` | RED tests for stability harness wiring (5 new unit test files, 30 cases) |
| 2 | `7ef5d5c` | GREEN impl: tests/fuzz/profiles.py + pyproject.toml + Makefile + tests/conftest.py |
| 3 | `f33dcc9` | YAML workflows: .github/workflows/ci.yml + .github/actions/setup-uv/action.yml |
| 4 | `5bfd03b` | Hygiene: pythonpath in pytest ini + ruff format/lint cleanup (incl. pre-existing ingest.py drift) |

### New unit tests

- `tests/unit/test_phase_ci1_pyproject.py` — 15 cases (dev deps, addopts, xfail_strict, markers, coverage gate)
- `tests/unit/test_hypothesis_profiles.py` — 11 cases (registration, semantics, conftest activation)
- `tests/unit/test_markers_and_xfail.py` — 3 cases (marker registration + xfail_strict pytester verification)
- `tests/unit/test_timeout_wired.py` — 3 cases (signal-method failure parsing, thread-method exit-code, plugin importability)
- `tests/unit/test_ci_workflow_yaml.py` — 12 cases (CI YAML + composite action structural validation, Make-target dereferencing)

**44 new unit tests added** all green after slice 2/3.

### Gate output (final)

| gate | result |
|------|--------|
| `make format-check` | `106 files already formatted` |
| `make lint` | `All checks passed!` |
| `make typecheck` | `0 errors (14 suppressed, 15 warnings not shown)` |
| `make test-unit` | `1168 passed, 1 xfailed`, **91.94% coverage** (gate 85%), 28.24s with `-n auto` |
| `make test-fuzz` (default dev profile) | `15 passed in 0.33s` |
| `HYPOTHESIS_PROFILE=ci make test-fuzz` | `15 passed`, profile shows `deadline=800ms` |
| `make test-smoke` | `10 passed in 3.59s` |
| YAML parse (`yaml.safe_load`) | both files parse cleanly |
| `actionlint` | **unavailable on local machine** (not in nix or homebrew or uvx registry) — defer to CI-2 |

### What CI-1 surfaced (non-blocking for close-out)

- **`test_chunk_reuse_e2e` (postgres) order-dependence**: pytest-randomly's
  random seeding intermittently exposes a per-seed failure in
  `tests/integration/test_backend_dual.py::TestChunkReuseE2E::test_chunk_reuse_e2e[postgres]`
  when run as part of the full `make test-integration` suite. In isolation
  the test passes; with the seed shown in `make ci` (auto-randomized) the
  test fails. The test passes again with `--randomly-seed=3642869480`.
  Likely root cause: shared session-scoped postgres container state isn't
  fully reset between the standalone `test_chunk_reuse_e2e.py` module and
  the parametrized `test_backend_dual.py::TestChunkReuseE2E` suite when
  ordered back-to-back. This is a Phase B integration test that CI-1
  intentionally did not touch; the order-dep is the *signal* pytest-randomly
  was added to surface. Triage owner: B-tail / CI-2 (the latter introduces a
  dedicated `integration.yml` workflow that will run integration in its own
  job).
- **Pre-existing ingest.py format drift**: corpus_forge/ingest.py was
  committed with a long-line wrap that ruff format -check would reject;
  hidden until CI-1 wired the gate. Folded into slice 4.

### Acceptance status

1. ☑ `make ci`-style six-gate gauntlet (format-check + lint + typecheck + test-unit + test-fuzz + test-smoke) green locally.
2. ☑ Coverage 91.94% on unit suite (≥ 85% required).
3. ☑ pyproject.toml verified via `tomllib.load(...)` — `fail_under == 85`, all five new dev deps declared, addopts carry `--timeout=60 --timeout-method=thread`, `xfail_strict == True`, markers table declares `requires_unix` + `requires_docker`.
4. ☑ `tests/fuzz/profiles.py` exists; `HYPOTHESIS_PROFILE=ci uv run pytest tests/fuzz -v --hypothesis-show-statistics` confirms `ci` profile active.
5. ☑ Workflow YAMLs parse via `yaml.safe_load`; `actionlint` skipped (unavailable locally).
6. ☑ Working tree clean (only `.claude/` untracked, user-private).
7. ☑ All four commits carry SSH signatures (1Password). `[tdd-principal] phase-ci-1:` prefix on every commit.

### Files changed

| file | role |
|------|------|
| `.github/workflows/ci.yml` | NEW — PR gate, single-OS × 3-Python matrix, two jobs (quality + test), workflow_call-able |
| `.github/actions/setup-uv/action.yml` | NEW — composite action: install uv, cache uv + Hugging Face, sync dev deps |
| `tests/fuzz/profiles.py` | NEW — dev/ci/nightly hypothesis profiles |
| `tests/conftest.py` | register_hypothesis_profiles() at module import; activate via HYPOTHESIS_PROFILE env |
| `pyproject.toml` | new dev deps, pytest addopts + xfail_strict + markers, coverage 85, pythonpath = ["."] |
| `Makefile` | test-unit gains `-n auto --timeout=60`; test-fuzz reads HYPOTHESIS_PROFILE |
| `corpus_forge/ingest.py` | pre-existing ruff-format drift reconciled |
| `tests/unit/test_phase_ci1_pyproject.py` | NEW — pyproject pin verification |
| `tests/unit/test_hypothesis_profiles.py` | NEW — profile registration + env semantics |
| `tests/unit/test_markers_and_xfail.py` | NEW — marker registration + xfail_strict pin |
| `tests/unit/test_timeout_wired.py` | NEW — pytest-timeout signal + thread method verification |
| `tests/unit/test_ci_workflow_yaml.py` | NEW — workflow YAML structural validation |

### Handoff to CI-2

- The 3-OS matrix (`ubuntu-22.04`, `macos-14`, `windows-2022`) is the
  next axis to expand. CI-2 will also add a separate `integration.yml`
  (Postgres via services on Linux, docker setup on macOS) — that's the
  natural home for the `test_chunk_reuse_e2e[postgres]` order-dep
  triage.
- `actionlint` should be wired into either the `quality` job or a
  separate pre-commit hook in CI-2. The Nix flake at
  `~/dotfiles/nixos-config` could grow an `actionlint` package; the
  CI runner has it preinstalled.
- `requires_unix` is declared but no real test consumes it yet —
  CI-2 needs to actually mark Windows-unfriendly tests (subprocess
  signal handling, advisory locks, etc.) with it when the windows-2022
  matrix cell goes live.

## Phase CI-2 Summary — cross-OS matrix + integration + nightly + flake fix

Plan: `/Users/evanowen/.claude/plans/crispy-yawning-crescent.md` (second
phase of beta-release milestone).

### Dispatch mode

**Option B (fused principal+tester+coder)** — Agent tool not available in
this session; principal absorbed tester+coder+QA roles. Four atomic commits
with `[tdd-principal] phase-ci-2:` prefix.

### Slices landed

| slice | commit  | gist |
|-------|---------|------|
| 1 RED | `2d9f1a2` | Three RED test modules: flake reproducer (3 integration tests), CI_NO_DOCKER hook pin (8 unit tests), workflow YAML shape pin (20 unit tests). Board entries CI2-1..CI2-9 added. |
| 2 GREEN | `0788813` | tests/conftest.py: pg_dsn drops corpus schema (CASCADE) per test; _ci_no_docker() helper + extended pytest_collection_modifyitems. Isolation reproducer rewritten to use a tri-test class so the reset hook is provably exercised. |
| 3 GREEN | `27321d5` | Workflow YAMLs: ci.yml expanded to 3×3 matrix + actionlint job + Windows CI_NO_DOCKER + caches; integration.yml NEW (Linux services + macOS docker); nightly.yml NEW (cron + nightly hypothesis + summary). 32 YAML pin tests green. |
| 4 GREEN | `101d41b` | requires_unix wired (gates on sys.platform == 'win32'); test_symlink_resolved marked; 4 gate tests added; pyproject CI-3 TODO comment; ruff --fix cleanup. |

### Flake root cause and fix (carry-over #1)

**Root cause**: ``upsert_document(..., embedder_ids=...)`` calls
``_copy_reusable_embeddings`` for every new chunk insert. That helper
SELECTs from ``corpus.embeddings_<name>`` looking for any chunk with the
same ``content_hash`` and an existing embedding row, then INSERTs the
vector for the new chunk. Two tests in two files both ingested
``_build_doc(12)`` markdown under the same embedder name. With the
session-scoped Postgres container and no per-test reset, the SECOND test's
ingest pre-filled embeddings for all 12 chunks from the FIRST test's
residue. ``chunks_missing_embedding`` returned 0 → ``encoder.encode()`` was
never called → assertion ``first_pass_arg_count >= 10`` saw 0 and failed.

**Fix**: ``tests/conftest.py::pg_dsn`` now executes ``DROP SCHEMA IF EXISTS
corpus CASCADE`` at fixture entry. Each test's ``backend.migrate()`` then
re-creates the schema from scratch; there is no prior-test residue for
``_copy_reusable_embeddings`` to find.

**Validation**: Ran the previously-flaky pair under 10 deterministic seeds
{1, 2, 3, 4, 5, 7, 11, 13, 17, 23}: 41/41 tests pass in every run.
Previously: 1–2 failures per seed for seeds {1, 2, 3, 5, 7, 11, 13, 23, 42,
999, 1234}.

### Test counts

| suite | before CI-2 | after CI-2 | delta |
|-------|------------:|-----------:|------:|
| unit  | 1168        | 1200       | +32   |
| integration | 240   | 246        | +6 (the 3 isolation tests + 3 from previous churn already landed) |
| fuzz  | 15          | 15         | 0     |
| smoke | 10          | 10         | 0     |

New unit test modules:
- `tests/unit/test_phase_ci2_yaml.py` — 20 cases pinning the new + modified
  workflow YAMLs.
- `tests/unit/test_ci_no_docker.py` — 8 cases pinning the env var + hook.
- `tests/unit/test_requires_unix_gate.py` — 4 cases pinning the marker.

New integration test module:
- `tests/integration/test_chunk_reuse_isolation.py` — 3 cases pinning
  per-test schema reset.

### Gate output (final)

| gate | result |
|------|--------|
| `make format-check` | `110 files already formatted` |
| `make lint` | `All checks passed!` |
| `make typecheck` | `0 errors (14 suppressed, 15 warnings not shown)` |
| `make test-unit` | `1200 passed, 1 xfailed`, **coverage 91.94%** (gate 85%), 40s with `-n auto` |
| `make test-fuzz` (ci profile) | `15 passed in 0.32s` |
| `make test-smoke` | `10 passed, 1 warning in 3.93s` |
| `make test-integration` | `246 passed, 9 warnings in 111.73s` |
| 10-seed flake sweep | `41 passed per seed × 10 seeds — 0 failures` |
| YAML parse (`yaml.safe_load`) | ci.yml, integration.yml, nightly.yml, setup-uv/action.yml — all parse |
| actionlint (via rhysd/actionlint:latest container) | clean on all 3 workflows |

### YAML validation

All four workflow YAMLs parse via `yaml.safe_load`:
- `.github/workflows/ci.yml` — 3 jobs (actionlint, quality, test). `test`
  job: 3 OS × 3 Python, fail-fast: false, top-level
  `defaults.run.shell: bash`, Windows cell sets `CI_NO_DOCKER=1`, 3.13 cells
  on macos/windows get `continue-on-error: true` until upstream wheels
  stabilize. Caches: `~/.cache/uv` keyed on uv.lock+pyproject.toml;
  `~/.cache/huggingface` keyed on pyproject.toml+uv.lock.
- `.github/workflows/integration.yml` — 2 jobs (integration-linux,
  integration-macos). Linux uses `services: { postgres: pgvector/pgvector:pg16 }`
  with `pg_isready` healthcheck. macOS uses `docker/setup-docker-action@v3`
  + pre-pull of pgvector image. Both: Python 3.11/3.12 only (3.13 deferred
  per plan; TODO comment marks the re-add condition).
- `.github/workflows/nightly.yml` — 4 jobs. Cron `0 7 * * *` +
  `workflow_dispatch`. Sets `HYPOTHESIS_PROFILE=nightly` (10× examples).
  Full 3-OS matrix on unit/fuzz; linux+macos on integration. Summary job
  appends results to `$GITHUB_STEP_SUMMARY`.

### requires_unix application

Marked **1 test** with `@pytest.mark.requires_unix`:
- `tests/unit/test_sync_cloud.py::TestDetectCloudProviderTypeHandling::
  test_symlink_resolved` — creates a real symlink via `Path.symlink_to`,
  which on Windows needs admin or developer-mode.

Survey of other candidates:
- `tests/unit/test_daemon.py` — all signal-handling tests mock
  `signal.signal`; SIGINT/SIGTERM exist on Windows too. No mark needed.
- `tests/unit/test_sync_fs.py::TestIsDataless` — uses `patch(..., create=True)`
  for `os.getxattr`, so works on Windows. No mark needed.
- `tests/unit/test_sync_fs.py` EXDEV / move_to_trash — uses `os.replace`
  + mocked OSError; cross-platform. No mark needed.
- Integration tests — already skipped on Windows via the CI_NO_DOCKER hook.

Anything else missed will surface on the windows-2022 matrix cell; the
3.13 cells have `continue-on-error: true` already, but 3.11/3.12 on
Windows are real gates.

### actionlint integration

Added as a separate `actionlint` job in `ci.yml` (ubuntu-latest, ~5min
timeout) using `raven-actions/actionlint@v2`. Both `quality` and `test`
jobs declare `needs: [actionlint]` so shell-syntax errors block the matrix
before burning runner minutes.

### Handoff to CI-3

- `pythonpath = ["."]` in pyproject.toml is now commented with
  `# CI-3: replace with editable install via hatchling build-system`. The
  hack stays until CI-3 introduces `[build-system]`.
- Version bump (`0.1.0` → `0.1.0a1` or similar pre-release tag), project
  classifiers, keywords, and urls are deferred per plan.
- The Windows 3.13 / macOS 3.13 matrix cells run with `continue-on-error:
  true`. Monitor weekly; flip to hard-fail once
  `sentence-transformers` ships stable wheels on py3.13 for those arches.
- `integration.yml` paths filter currently excludes pure-doc edits.
  Adjust if `docs/**` changes start needing a Postgres validation run.

### Acceptance status

1. ☑ `make ci`-style six-gate gauntlet green locally. Coverage 91.94%
   (≥ 85% gate).
2. ☑ Flake fix: 10 deterministic seeds × 41 tests = **0 failures**.
3. ☑ All four YAML files parse via `yaml.safe_load`. Matrix dimensions
   match plan exactly (3-OS × 3-Python on ci.yml; 2-OS × 2-Python on
   integration.yml; full matrix + cron on nightly.yml). 32 pin tests cover
   the structural contract.
4. ☑ `CI_NO_DOCKER=1 uv run pytest tests/integration -v` skips every
   integration item with reason "CI_NO_DOCKER set — integration tests
   skipped on Docker-less runner" (unit-test pinned via mock-call to the
   hook).
5. ☑ Working tree clean (only `.claude/` untracked, user-private).
6. ☑ All four commits carry SSH signatures (1Password) and the
   `[tdd-principal] phase-ci-2:` prefix.

| id | title | depends_on | surface | risk | status | claimed_by | notes |
|----|-------|------------|---------|------|--------|------------|-------|
| CI2-1 | Reproducer + RED tests for flake, CI_NO_DOCKER, YAML shape | — | tests/unit/test_phase_ci2_yaml.py, tests/unit/test_ci_no_docker.py, tests/unit/test_chunk_reuse_isolation.py | med | done | tdd-principal | RED slice — pins the contracts before code changes. |
| CI2-2 | GREEN — per-test schema reset in pg_dsn fixture | CI2-1 | tests/conftest.py | high | done | tdd-principal | TRUNCATE/DROP-and-recreate corpus.* per test; isolate the embedding-reuse cross-talk. |
| CI2-3 | GREEN — extend pytest_collection_modifyitems for CI_NO_DOCKER | CI2-1 | tests/conftest.py | low | done | tdd-principal | Skip integration tests when CI_NO_DOCKER is set. |
| CI2-4 | GREEN — ci.yml matrix expansion + actionlint job + caches | CI2-1 | .github/workflows/ci.yml | med | done | tdd-principal | 3 OS × 3 Python; integration skip on Windows via CI_NO_DOCKER. |
| CI2-5 | GREEN — integration.yml (Linux services + macOS docker) | CI2-1 | .github/workflows/integration.yml | med | done | tdd-principal | NEW file. |
| CI2-6 | GREEN — nightly.yml (cron + nightly hypothesis profile) | CI2-1 | .github/workflows/nightly.yml | low | done | tdd-principal | NEW file. |
| CI2-7 | GREEN — requires_unix marker application | CI2-1 | tests/unit/test_daemon.py, others as surveyed | low | done | tdd-principal | Mark POSIX-only tests. |
| CI2-8 | GREEN — pythonpath TODO comment (CI-3 handoff) | CI2-1 | pyproject.toml | low | done | tdd-principal | Comment only; don't replace yet. |
| CI2-9 | QA — full gauntlet + 10-seed flake sweep + YAML parse | CI2-2..CI2-8 | n/a (verification only) | high | done | tdd-principal | Acceptance check. |

---

## Phase CI-3: Packaging hardening + cross-platform install + LICENSE (Apache-2.0)

_Owner: tdd-principal (Option B fused; Agent tool unavailable)._
_Plan reference: `/Users/evanowen/.claude/plans/crispy-yawning-crescent.md` (CI-3); user override locks license to **Apache-2.0** everywhere._

### Project gates (CI-3 specific additions)

- Wheel build: `python -m build --wheel --outdir dist-test .`
- Editable install check: `uv sync` then `uv run pytest tests/unit -q -k test_dummy_import_corpus_forge`
- `bash -n` syntax check on every `scripts/{macos,linux}/*.sh`

### Tasks

| id | title | depends_on | surface | risk | status | claimed_by | notes |
|----|-------|------------|---------|------|--------|------------|-------|
| CI3-1 | RED — pyproject metadata + wheel METADATA characterization | — | tests/unit/test_phase_ci3_pyproject.py, tests/unit/test_phase_ci3_wheel_metadata.py | high | done | tdd-principal | 70-test pin block; commit `f8fd84e`. |
| CI3-2 | RED — LICENSE content (Apache-2.0) + py.typed + governance/README license | CI3-1 | tests/unit/test_phase_ci3_packaging.py | med | done | tdd-principal | 18 tests + 2 governance-skip; commit `f8fd84e`. |
| CI3-3 | RED — script split + Makefile dispatch | CI3-1 | tests/unit/test_phase_ci3_scripts.py | low | done | tdd-principal | 39 tests covering moves, bash -n, systemd anchors, make -n stop; commit `f8fd84e`. |
| CI3-4 | RED — editable install dummy test | CI3-1 | tests/unit/test_dummy_editable_install.py | low | done | tdd-principal | `import corpus_forge` smoke; commit `f8fd84e`. |
| CI3-5 | GREEN — pyproject rewrite + version bump + remove `pythonpath` | CI3-1, CI3-4 | pyproject.toml, corpus_forge/__init__.py | high | done | tdd-principal | hatchling build-system, Apache-2.0 SPDX, 0.1.0b1, `>=3.11,<3.14`, 14 classifiers, 11 keywords, 5 urls, hatch wheel/sdist targets; commit `d7a39d7`. |
| CI3-6 | GREEN — LICENSE file + py.typed + README license footer | CI3-2 | LICENSE, corpus_forge/py.typed, README.md | med | done | tdd-principal | Canonical Apache 2.0 (11.3kB, 202 lines, 2026/Evan Owen), py.typed marker, README Linux/macOS/Windows install matrix + license footer; commit `15f6e7c` (with macOS `git mv`). |
| CI3-7 | GREEN — script split (git mv) + linux scripts + service template + Makefile dispatch | CI3-3 | scripts/, packaging/corpus-forge.service.template, Makefile | med | done | tdd-principal | macOS scripts moved via `git mv` (blame preserved), Linux installers using `systemctl --user`, systemd unit template, Makefile uname-S dispatch + `_unhide-pth` workaround for iCloud UF_HIDDEN flag stripping; commit `9c8a426`. |
| CI3-8 | QA — full gauntlet + `python -m build --wheel` METADATA inspection + editable install | CI3-5..CI3-7 | n/a (verification only) | high | done | tdd-principal | All gates green; wheel built + METADATA verified; pip-install round-trip from clean venv → `corpus-forge --help` rc=0; commits `1af797d` (lint cleanup) closes the slice. |

### Summary

**Mode**: Option B (fused) — Agent tool unavailable; principal executed RED then GREEN slices in-process.

**Slices landed** (five commits, all SSH-signed, all on `main`):

| commit | role | scope |
|--------|------|-------|
| `f8fd84e` | tdd-principal | RED — full CI-3 test pin (5 files, 930 LOC, 80 failures pre-GREEN) |
| `d7a39d7` | tdd-principal | GREEN — pyproject rewrite (hatchling, Apache-2.0, 0.1.0b1, classifiers, keywords, urls, requires-python `>=3.11,<3.14`, pythonpath removed) |
| `15f6e7c` | tdd-principal | GREEN — LICENSE + py.typed + README OS install matrix (macOS scripts git-mv'd in same commit) |
| `9c8a426` | tdd-principal | GREEN — Linux scripts + systemd template + Makefile uname dispatch + `_unhide-pth` helper |
| `1af797d` | tdd-principal | GREEN — ruff lint+format cleanup on the 4 new test files |

**Wheel build output**:
- `dist-test/corpus_forge-0.1.0b1-py3-none-any.whl` (81 276 bytes, py3-none-any).
- `Metadata-Version: 2.4`, `Name: corpus-forge`, `Version: 0.1.0b1`.
- `License-Expression: Apache-2.0` (PEP 639 modern, not legacy `License:`).
- `License-File: LICENSE`.
- `Requires-Python: <3.14,>=3.11` (hatchling re-ordered; semantically identical).
- 14 classifiers, 11 keywords, 5 Project-URLs all match expected.

**Editable install verification (local, iCloud)**: `uv sync` writes `_editable_impl_corpus_forge.pth` containing the repo root; macOS iCloud Drive marks every new .venv file `UF_HIDDEN`, which makes `site.py` silently skip the `.pth` (returns at site.py:179 after the `lstat.st_flags & UF_HIDDEN` check). Workaround added: `make _unhide-pth` (chained into `make install` / `make dev`, no-op on non-Darwin) runs `chflags nohidden .venv/lib/**/*.pth`. After it runs, `uv run --no-sync python -c "import corpus_forge"` resolves from /tmp (verifying the .pth is honoured) and the dummy test passes. CI runners are not on iCloud, so this is purely a local-dev quirk. **R1 should know**: any future fresh `uv sync` re-applies the iCloud hidden flag; the Makefile compensates, but ad-hoc `uv run pytest …` outside the Makefile will fail unless preceded by `make _unhide-pth`.

**Pip-install round-trip**: clean venv → `pip install 'corpus-forge[sqlite,hf] @ file://…/corpus_forge-0.1.0b1-py3-none-any.whl'` → `corpus-forge --help` returns rc=0 with the full Typer banner (six subcommands: migrate, ingest, embed, daemon, version, sync).

**Test counts**:
- New: 90 CI-3 unit tests (40 pyproject pins, 31 wheel METADATA, 18 LICENSE/py.typed/README, 39 scripts/Makefile, 2 dummy import — minus 2 governance skips + 1 full-install skip).
- Unit suite total: 1319 passed / 3 skipped / 1 xfailed (was 1229 at CI-2 close; +90 new, no regressions).
- Coverage: 91.94% (≥ 85% gate).

**Gate output**:
- `ruff format --check` — 115 files already formatted.
- `ruff check` — All checks passed.
- `pyrefly check corpus_forge` — 0 errors (14 suppressed).
- `pytest tests/unit -n auto --cov-fail-under=85` — 1319 passed in 42.23s, 91.94% coverage.
- `python -m build --wheel` — Successfully built corpus_forge-0.1.0b1-py3-none-any.whl.
- Wheel pip-install + `corpus-forge --help` — rc=0.
- `make -n stop` on darwin — `./scripts/macos/stop.sh`. `make -n logs` — `tail -f ~/Library/Logs/corpus-forge.err.log`.

**For Phase R1 (Release tagging)**:
- Wheel builds clean; ready for PyPI publish via `twine upload`.
- LICENSE is canonical Apache-2.0 verbatim, only the appendix boilerplate substituted; verify with `diff` against `https://www.apache.org/licenses/LICENSE-2.0.txt` modulo that one line.
- README `## License` footer says Apache-2.0; no MIT-license claims remain anywhere in the tree (verified by `test_readme_has_no_mit_license_claim` and `test_no_mit_anywhere` in wheel METADATA).
- The wheel build process is idempotent; rerun `python -m build --wheel --outdir dist .` to refresh.
- Editable-install / iCloud caveat documented above — does not affect CI or downstream consumers.
- `pythonpath = ["."]` is **gone** from `pyproject.toml`; the test `TestPythonPathRemoved::test_pythonpath_absent` pins this so it can't sneak back.

### Acceptance status

1. ☑ `make ci`-style six-gate gauntlet green locally. Coverage 91.94%.
2. ☑ `python -m build --wheel` produces `corpus_forge-0.1.0b1-py3-none-any.whl`; METADATA carries `License-Expression: Apache-2.0`, `License-File: LICENSE`, `Requires-Python: <3.14,>=3.11`, all 14 classifiers, 11 keywords.
3. ☑ Fresh-venv `pip install` of the wheel works; `corpus-forge --help` produces output.
4. ☑ `LICENSE` is canonical Apache-2.0 text with year 2026 and holder "Evan Owen / corpus-forge contributors".
5. ☑ `scripts/macos/`, `scripts/linux/`, `packaging/corpus-forge.service.template` all exist; macOS scripts have full git-mv blame; Linux scripts are POSIX-shell parseable (bash -n clean).
6. ☑ `Makefile` `stop` / `logs` dispatch on `uname -s`; `make -n stop` on darwin yields the macOS path.
7. ☑ `pythonpath = ["."]` removed; full unit suite passes via the hatchling editable install (with macOS iCloud hidden-flag workaround via `make _unhide-pth`).
8. ☑ Working tree clean (only `.claude/` untracked, user-private). All commits SSH-signed.

---

## Phase R1 — Lexical index + protocol lift

_Source plan: `/Users/evanowen/.claude/plans/crispy-yawning-crescent.md` § Phase R1._

**Dispatch mode**: Option B (fused) — Agent tool not present in current toolset; tdd-principal executes RED then GREEN slices in-process and atomically commits each slice prefixed `[tdd-principal] phase-r1: <slice>`.

**Headline**: introduce the first retrieval surface on `StorageBackend`. 5 new methods: `search_dense`, `search_lexical`, `get_chunk`, `list_datasets`, `backfill_lexical_index`. New `corpus_forge/retrieval/{__init__.py, types.py}` module exporting `Hit`, `SearchOptions`, `RetrievalMetrics`. New `004_fts.sql` migrations for both dialects: Postgres `text_tsv` GENERATED column + GIN index; SQLite `chunks_fts` FTS5 virtual table + ai/ad/au triggers + idempotent `backfill_lexical_index()` invocation.

### Tasks (R1)

| id | title | depends_on | surface | risk | status | claimed_by | notes |
|----|-------|------------|---------|------|--------|------------|-------|
| R1-01 | RED — `retrieval/types.py` + protocol-surface signature pins (unit) | — | `tests/unit/test_retrieval_types.py`, `tests/unit/test_protocol_retrieval_surface.py` | low | done | tdd-principal | — |
| R1-02 | RED — migration 004 parser + FTS5 trigger semantics (unit) | — | `tests/unit/test_migration_004_postgres.py`, `tests/unit/test_migration_004_sqlite.py`, `tests/unit/test_sqlite_fts_triggers.py` | low | done | tdd-principal | — |
| R1-03 | RED — dual-backend integration: search_dense + search_lexical + get_chunk + list_datasets + backfill | — | `tests/integration/test_backend_dual.py` (additions), `tests/integration/test_migrate_004_sqlite.py`, `tests/integration/test_migrate_004_postgres.py` | med | done | tdd-principal | — |
| R1-04 | GREEN — `004_fts.sql` (postgres + sqlite) + `migrate.py` backfill wiring | R1-02 | `corpus_forge/schema/004_fts.sql`, `corpus_forge/schema/sqlite/004_fts.sql`, `corpus_forge/schema/migrate.py` | low | done | tdd-principal | — |
| R1-05 | GREEN — `corpus_forge/retrieval/{__init__.py, types.py}` | R1-01 | `corpus_forge/retrieval/__init__.py`, `corpus_forge/retrieval/types.py` | low | done | tdd-principal | — |
| R1-06 | GREEN — protocol surface in `backends/base.py` | R1-01, R1-05 | `corpus_forge/backends/base.py` | low | done | tdd-principal | — |
| R1-07 | GREEN — sqlite backend impls + inline `migrate()` 004 application | R1-04, R1-06 | `corpus_forge/backends/sqlite.py` | med | done | tdd-principal | search_dense lifted from `scripts/query_repo_sqlite.py`. |
| R1-08 | GREEN — postgres backend impls (parity) | R1-04, R1-06 | `corpus_forge/backends/postgres.py` | med | done | tdd-principal | inline `migrate()` already runs numbered files via `apply_migrations`. |
| R1-09 | QA — `make ci` + integration + idempotency + dogfood `scripts/query_repo_sqlite.py` | R1-04..R1-08 | n/a | high | done | tdd-principal | All gates green; idempotency verified; dogfood script + backend.search_lexical("phase", k=3) both produce sensible hits. |

### Waves

- Wave 0 (RED, all parallel-safe by file surface): R1-01, R1-02, R1-03.
- Wave 1 (GREEN, parallel-safe): R1-04, R1-05.
- Wave 2 (GREEN, depends on Wave 1): R1-06 (then R1-07 + R1-08 fan out).
- Wave 3 (QA): R1-09.

### Acceptance details

#### R1-01 — retrieval types
- `Hit` is `@dataclass(frozen=True)` with exactly: `chunk_id: int`, `score: float`, `text: str`, `document_id: int | None`, `source_uri: str | None`, `title: str | None`, `dataset_id: int`, `metadata: dict[str, Any]`, `source: Literal["dense","lexical","fused","reranked"]`.
- `SearchOptions` defaults: `k=10`, `dataset=None`, `fusion="rrf"`, `alpha=0.5`, `rerank=False`, `rerank_top_n=50`.
- `RetrievalMetrics` fields: `ndcg`, `mrr`, `recall`, each `dict[int, float]`.
- `Hit.source` literal MUST include all four values (forward-compat for R2/R4).
- Protocol pin: `StorageBackend` has 5 new methods with the spec signatures; `Hit` is only imported under `TYPE_CHECKING` so no runtime circular import.

#### R1-02 — migrations
- `004_fts.sql` (postgres): `ALTER TABLE corpus.chunks ADD COLUMN IF NOT EXISTS text_tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED;` + `CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON corpus.chunks USING GIN (text_tsv);`.
- `sqlite/004_fts.sql`: `CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, content='chunks', content_rowid='id', tokenize='porter unicode61');` + `chunks_ai`, `chunks_ad`, `chunks_au` triggers; each trigger guarded `CREATE TRIGGER IF NOT EXISTS`.
- Triggers verified via in-memory `sqlite3.connect(":memory:")` execution of the migration against a tiny seed schema (chunks table only) and observing rowid mirroring on INSERT/UPDATE/DELETE.

#### R1-03 — dual-backend integration
- `test_search_dense_returns_topk`: deterministic vectors, top-k ordering, `Hit.source == "dense"`, score = 1.0 - distance for sqlite normalized cosine.
- `test_search_lexical_matches_phrase`: insert chunks, search a phrase, verify Hit (`source == "lexical"`).
- `test_search_lexical_excludes_other_datasets`: dataset filter excludes other dataset.
- `test_get_chunk_joins_document`: returns dict with `source_uri` + `title`.
- `test_list_datasets_counts`: two datasets, correct document/chunk counts.
- `test_backfill_lexical_index_idempotent_sqlite` (sqlite-only): N then 0.
- `test_migrate_004_sqlite`: fresh tmp_path db, migrate twice → no error, `chunks_fts` exists, INSERT/UPDATE/DELETE mirror.
- `test_migrate_004_postgres`: testcontainer Postgres; `text_tsv` GENERATED column present, GIN index in `pg_indexes`, EXPLAIN ANALYZE on a `text_tsv @@ websearch_to_tsquery(...)` query uses the GIN index.

#### R1-04 — migration files + backfill wiring
- File `corpus_forge/schema/004_fts.sql` — exactly the Postgres ALTER + CREATE INDEX.
- File `corpus_forge/schema/sqlite/004_fts.sql` — virtual table + 3 triggers.
- `apply_migrations(...)` invokes `backend.backfill_lexical_index()` once after applying `004_fts` on the SQLite dialect. Backend whose `backfill_lexical_index` returns int. Idempotent on re-run.

#### R1-07 / R1-08 — backend impls
- `search_dense` (sqlite): lift SQL from `scripts/query_repo_sqlite.py:67–92`. `MATCH ... AND k = ?` against `embeddings_<safe>` vec0 table. LEFT JOIN documents (chunk may have NULL `document_id` for message chunks). Filter by `dataset_id` if provided. `Hit.score = 1.0 - distance`.
- `search_dense` (postgres): `ORDER BY embedding <=> %s LIMIT %s` on `corpus.embeddings_<safe>`. `Hit.score = 1.0 - cosine_distance`.
- `search_lexical` (sqlite): `chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT ?`, normalized `1/(1+bm25)`.
- `search_lexical` (postgres): `ts_rank_cd(text_tsv, websearch_to_tsquery('english', %s)) AS rank`. Clip to `[0,1]` if needed.
- `get_chunk(chunk_id)`: returns row dict joined to documents (LEFT JOIN) or `None`.
- `list_datasets()`: returns list of dicts `{name, kind, description, document_count, chunk_count}` ordered by `name`.
- `backfill_lexical_index()`: sqlite returns rowcount of inserted rows; postgres returns 0.

#### R1-09 — QA acceptance
- `make ci` green: format-check, lint, pyrefly strict, unit tests + ≥ 85% coverage.
- `make test-integration` green: new dual-backend + migrate_004 tests pass on both backends.
- `corpus-forge migrate` twice → no errors / no duplicate triggers / no duplicate columns.
- `scripts/query_repo_sqlite.py` still runs end-to-end against `/tmp/corpus-forge-test.db` (sample query produces results).
- `backend.search_lexical("how does lock_source work", k=5)` returns plausible hits on the seeded corpus on both backends.
- All commits prefixed `[tdd-principal] phase-r1: <slice>`; SSH-signed; tree clean at end (only `.claude/` untracked).

### Phase R1 — close-out

**Dispatch mode**: Option B (fused). Agent subagent tool not present in toolset; tdd-principal drove RED → GREEN → QA in-process.

**Commits (all SSH-signed, in order)**:
| commit | slice |
|--------|-------|
| `a0e6175` | seed task board (R1-01..R1-09) |
| `1d88453` | RED — retrieval types + protocol surface + 004_fts migrations + dual-backend integration |
| `27a331f` | GREEN — 004_fts migrations + retrieval types module + sqlite backfill_lexical_index |
| `4e538a3` | GREEN — StorageBackend protocol gains retrieval surface |
| `7986e38` | GREEN — backend impls of search_dense / search_lexical / get_chunk / list_datasets |
| `ebd9e1f` | GREEN polish — BM25 sign + external-content FTS5 rebuild + table-count pin |
| _next_   | this close-out |

**Surface landed**:
- 5 protocol methods on `StorageBackend` — `search_dense`, `search_lexical`, `get_chunk`, `list_datasets`, `backfill_lexical_index`.
- `corpus_forge/retrieval/{__init__.py, types.py}` — `Hit`, `SearchOptions`, `RetrievalMetrics`.
- Migrations: `corpus_forge/schema/004_fts.sql` (Postgres GENERATED tsvector + GIN), `corpus_forge/schema/sqlite/004_fts.sql` (FTS5 + ai/ad/au triggers).
- `corpus_forge/schema/migrate.py` — handles SQLite trigger bodies via the new `SQLiteBackend._executescript`; invokes `backfill_lexical_index()` once after applying `004_fts` on the SQLite dialect.

**Tasks closed**:
| id | status |
|----|--------|
| R1-01..R1-03 | done (RED suite landed at `1d88453`) |
| R1-04, R1-05 | done (`27a331f`) |
| R1-06 | done (`4e538a3`) |
| R1-07, R1-08 | done (`7986e38`) |
| R1-09 | done (this slice; gates all green; idempotency + dogfood verified) |

**Gates** (Wave 3 QA):
- `ruff format --check corpus_forge tests` — 124 files already formatted.
- `ruff check corpus_forge tests` — All checks passed.
- `pyrefly check corpus_forge` — 0 errors (15 suppressed).
- `pytest tests/unit -n auto --cov-fail-under=85` — 1403 passed / 3 skipped / 1 xfailed; **coverage 87.60%**.
- `pytest tests/integration` — 280/281 first run; the lone fail (`test_sync_icloud_dupe.py::TestICloudDupeSameHashDeleted`) is a pre-existing flake on the iCloud-Drive watcher; re-run = 5/5 green. **No R1 regressions.**
- `corpus-forge migrate` twice on a fresh `tmp_path/idem.db` — no errors; tables identical (17 rows including the 5 FTS5 family); confirms `IF NOT EXISTS` discipline.
- `scripts/query_repo_sqlite.py "how does lock_source work" --k 3` — runs end-to-end against `/tmp/corpus-forge-test.db`; returns the expected ranked output (lock_source-related hits from open-questions.md / sqlite_backend.md). Script left un-refactored (still uses its own ad-hoc SQL; refactoring to call `backend.search_dense(...)` deferred to a follow-up if desired — current code keeps doing what it has always done).
- `backend.search_lexical("phase", k=3)` on the seeded corpus → 3 hits at score 0.82–0.83. `backend.search_dense(eid, qvec, k=3)` → 3 hits at score 0.06–0.07 (cosine-distance-derived; consistent with the prototype query).

**Surprises / notes for Phase R2**:
- **SQLite FTS5 `bm25()` returns *non-positive* values** where 0 = perfect match.  The first GREEN pass used `1/(1+bm25)` which produced scores > 1 (and is what the plan suggested verbatim); the polish commit replaced it with `relevance = max(-bm25, bm25); score = relevance/(1+relevance)` so scores stay in `[0, 1)` and remain higher-is-better. **R2 should normalise both backends' scores again before fusion** (RRF or alpha) — Postgres's `ts_rank_cd` is clipped to `[0,1]` already.
- **External-content FTS5 backfill**: `INSERT INTO chunks_fts(rowid, text) SELECT … FROM chunks` (the plan's literal SQL) silently produces *delete markers*, not index entries.  The correct backfill is `INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')`.  This bit me on the dogfood db — the corpus already had 421 chunks pre-mirrored by the broken pattern; after switching to `'rebuild'`, `chunks_fts MATCH 'phase'` returns 30 hits as expected.  Phase R2's HybridRetriever does not need to know about this — `backfill_lexical_index()` hides the detail — but if any future migration ships its own backfill it must use `'rebuild'`.
- **Triggers vs bulk INSERT**: the `chunks_ai`/`chunks_au` triggers continue to use the per-row pattern (`INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text)`), which **does** work inside triggers for external-content tables.  The silent no-op only applies to bulk INSERTs from outside trigger context.  This asymmetry is documented in the SQLite source but not the FTS5 manual; flag it in the R2 design doc if relevant.
- **Migrate-time trigger bodies**: the legacy `";".split(...)` migration applier mangles `BEGIN ... END;` blocks.  Added `SQLiteBackend._executescript(sql)` which calls `sqlite3.Connection.executescript()` directly.  `apply_migrations` routes SQLite migrations containing `CREATE TRIGGER` through this helper; everything else still uses the per-statement path so per-statement IF-NOT-EXISTS rewriting (ADD COLUMN) is preserved.
- **`chunks` has no direct `dataset_id`** column. Dataset attribution is via `documents.dataset_id` OR `conversations.dataset_id`. All R1 retrieval queries `COALESCE(d.dataset_id, cv.dataset_id)` to resolve this. R2's HybridRetriever should pass `dataset_id` straight through to the backend; do NOT add a `chunks.dataset_id` shortcut column.
- **iCloud `.pth` hidden flag** still affects local dev — running `pytest` directly without `make _unhide-pth` will fail with `ModuleNotFoundError: No module named 'corpus_forge'`. Documented in CI-3 close-out; nothing new here.
- **`backend._execute` leak ban**: every retrieval SQL stays inside the backend module.  The `corpus_forge/retrieval/` module imports types only; consumers go through the protocol.  Verified by inspection of new code.

**Working tree**: clean modulo `.claude/` (user-private, untracked).

**Acceptance status**:
1. ☑ `make ci`-style gauntlet (format-check, lint, pyrefly, unit + coverage) all green; coverage 87.60% (gate 85%).
2. ☑ `make test-integration` 280/281 first attempt; the 1 fail is pre-existing iCloud flake; 5/5 on rerun.
3. ☑ Migrations idempotent; verified by running `backend.migrate()` twice on a fresh SQLite db + by the `test_migrate_004_postgres::TestIdempotency::test_migrate_twice_no_error` integration test.
4. ☑ `scripts/query_repo_sqlite.py` runs end-to-end against `/tmp/corpus-forge-test.db`; output matches prior dogfooding (left un-refactored — same SQL it always used).
5. ☑ `backend.search_lexical(...)` returns plausible hits on both backends (sqlite verified locally; postgres verified in integration).
6. ☑ All 6 R1 commits + this close-out commit follow the `[tdd-principal] phase-r1: <slice>` prefix, SSH-signed by 1Password.
7. ☑ Tree clean at end.


---

## Phase R2 — Hybrid retriever + asymmetric `encode_query`

_Source plan: `/Users/evanowen/.claude/plans/crispy-yawning-crescent.md`._

**Dispatch mode**: Option B — atomic per-slice commits by tdd-principal (worker-tool unavailable; mirrors R1 close pattern).
**Carry-overs from R1 honored**:
- Backend score scales differ (sqlite `score = relevance/(1+relevance)` ∈ [0,1); postgres `ts_rank_cd` clipped to [0,1]) → R2 normalises **per-list** before alpha fusion; RRF stays rank-only.
- `Hit.source` already includes `"fused"` / `"reranked"` — no widening.
- `chunks` has no direct `dataset_id` — pass dataset filter through to backend calls.
- No `[retrieval]` extra (R3 territory); numpy is transitive.

### Tasks

| id | title | depends_on | surface | risk | status | claimed_by | notes |
|----|-------|------------|---------|------|--------|------------|-------|
| R2-01 | RED — fusion / normalize / retriever / encode_query / config unit pins + dual-integration hybrid test | — | `tests/unit/test_retrieval_fusion.py`, `tests/unit/test_retrieval_normalize.py`, `tests/unit/test_retrieval_retriever.py`, `tests/unit/test_embedder_encode_query.py`, `tests/unit/test_config_retrieval.py`, `tests/integration/test_backend_dual.py` | med | done | tdd-principal | commit `9850a58` — 89 failing tests; pins full R2 surface. |
| R2-02 | GREEN — `RetrievalConfig` model + attach to `Config` | R2-01 | `corpus_forge/config.py` | low | done | tdd-principal | commit `ac40e4a` — 13/13 unit tests green. |
| R2-03 | GREEN — `Embedder.encode_query` protocol + `BaseEmbedder` default + Qwen3 override on `SentenceTransformersEmbedder` | R2-01 | `corpus_forge/embedders/base.py`, `corpus_forge/embedders/sentence_transformers.py` | low | done | tdd-principal | commit `b4c961c` — 19/19 + 52 existing embedder tests green. |
| R2-04 | GREEN — `retrieval/normalize.py` (min-max) + `retrieval/fusion.py` (RRF + alpha_blend) | R2-01 | `corpus_forge/retrieval/normalize.py`, `corpus_forge/retrieval/fusion.py`, `corpus_forge/retrieval/__init__.py` | low | done | tdd-principal | commit `66f6e6e` — 30/30 unit tests green. |
| R2-05 | GREEN — `retrieval/retriever.py` (`Retriever` protocol + `HybridRetriever`) | R2-03, R2-04 | `corpus_forge/retrieval/retriever.py`, `corpus_forge/retrieval/__init__.py` | med | done | tdd-principal | commit `12cef88` — 28/28 retriever tests green; reranker stored but never called. |
| R2-06 | GREEN — dual-backend integration: `test_hybrid_search_*[*]` passes; gate-clean polish | R2-05 | `corpus_forge/retrieval/normalize.py`, `tests/*` | low | done | tdd-principal | commit `34338ae` — single-element normalise contract corrected to `[1.0]`; 6/6 hybrid integration; 287/287 full integration; ruff/format/pyrefly clean. |
| R2-Z | Close-out summary + acceptance check | R2-02..R2-06 | `.planning/tdd/tasks.md` | low | done | tdd-principal | This block. |

### DAG
- Wave 0: R2-01 (RED ratchet)
- Wave 1: R2-02, R2-03, R2-04 (independent; disjoint surfaces)
- Wave 2: R2-05 (HybridRetriever uses everything above)
- Wave 3: R2-06 (integration goes green; no new code, just sanity-check)
- Wave 4: R2-Z (close-out)

### Phase R2 close-out

**Commits (7)**:
1. `e40a029` — seed task board (R2-01..R2-Z).
2. `9850a58` — RED ratchet: 5 new unit test files + extension of dual-integration suite.
3. `ac40e4a` — GREEN: `RetrievalConfig` + attach to `Config`.
4. `b4c961c` — GREEN: `Embedder.encode_query` + Qwen3 override.
5. `66f6e6e` — GREEN: `retrieval.normalize.min_max` + `retrieval.fusion` (RRF + alpha_blend).
6. `12cef88` — GREEN: `Retriever` protocol + `HybridRetriever`.
7. `34338ae` — GREEN polish: single-element normalise fix; lint / format / pyrefly clean.

**Files added (5)**:
- `corpus_forge/retrieval/normalize.py` (44 lines)
- `corpus_forge/retrieval/fusion.py` (84 lines)
- `corpus_forge/retrieval/retriever.py` (175 lines)
- `tests/unit/test_retrieval_normalize.py` (95 lines, 9 tests)
- `tests/unit/test_retrieval_fusion.py` (157 lines, 21 tests)
- `tests/unit/test_retrieval_retriever.py` (475 lines, 28 tests)
- `tests/unit/test_embedder_encode_query.py` (305 lines, 19 tests)
- `tests/unit/test_config_retrieval.py` (178 lines, 13 tests)

**Files modified (4)**:
- `corpus_forge/config.py` — `RetrievalConfig` model + attach to `Config`.
- `corpus_forge/embedders/base.py` — `Embedder.encode_query` protocol + `BaseEmbedder.encode_query` default delegate.
- `corpus_forge/embedders/sentence_transformers.py` — Qwen3 detection + instruct-prefix override on `encode_query`.
- `corpus_forge/retrieval/__init__.py` — re-export `min_max`, `reciprocal_rank_fusion`, `alpha_blend`, `Retriever`, `HybridRetriever`.
- `tests/integration/test_backend_dual.py` — `TestHybridSearch` (3 tests × {postgres, sqlite}).

**Fusion strategy implemented**:
- **RRF (default)** — rank-only.  Sidesteps the R1 score-scale mismatch entirely (no normalisation needed).
- **alpha-weighted** — `per-list min_max → alpha_blend`.  R1 carry-over #1 honoured.

**Score normalisation discoveries / surprises**:
- **`min_max([x])` must return `[1.0]`, not `[0.0]`.**  Discovered when the dual-backend `TestHybridSearch::test_hybrid_search_alpha_fusion_blends_scores[postgres/sqlite]` case with `alpha=0.0` failed: the lexical search for the unique keyword `"baobab"` returned exactly one hit; normalising that singleton to `0.0` silently erased it from the blend.  All-equal multi-element lists still emit zeros (no "best" among equals).
- **No additional scale-mismatch issues beyond the R1 flag.**  RRF doesn't care; alpha path is normalise-then-blend; backend score scales (sqlite `relevance/(1+relevance)` ∈ [0,1); postgres `ts_rank_cd` clipped to [0,1]) survive the round-trip cleanly once each list is min-maxed.

**Qwen3 prefix detection**:
- Two `model_id` prefix variants (case-insensitive): `Qwen/Qwen3-Embedding` (HF canonical) and `qwen3-embedding` (Ollama-style alias).
- Tested against the parametrised set `{Qwen/Qwen3-Embedding-{0.6,4,8}B, qwen3-embedding-{0.6,4,8}b}` — 6 variants, all green.
- The `encode` (document-side) path is NEVER prefixed — only `encode_query` is.

**Gates** (final QA):
- `ruff check corpus_forge tests` — All checks passed.
- `ruff format --check corpus_forge tests` — 132 files already formatted.
- `pyrefly check corpus_forge` — 0 errors (16 suppressed).
- `pytest tests/unit -n auto --cov-fail-under=85` — **1493 passed / 3 skipped / 1 xfailed; coverage 88.10%.**
- `pytest tests/integration` — **287 passed in 2m29s** (no R1/R2 regressions; the iCloud-watcher flake noted in R1 close-out did not re-surface this run).
- `pytest tests/integration/test_backend_dual.py::TestHybridSearch` — **6/6 green** (3 tests × {postgres, sqlite}).

**Working tree**: clean modulo `.claude/` (user-private, untracked).

**Acceptance status**:
1. ☑ `make ci`-style gauntlet (format-check, lint, pyrefly, unit + coverage 88.10% ≥ 85%) all green.
2. ☑ `make test-integration` 287/287 green.
3. ☑ `HybridRetriever(backend, embedder, embedder_id).search(...)` returns `list[Hit]` of correct length with `source="fused"` on both backends.
4. ☑ With `fusion="alpha"`, scores blend linearly with `alpha` — verified end-to-end in both unit + dual-backend integration.
5. ☑ `SentenceTransformersEmbedder.encode_query` for Qwen3 prepends the instruction prompt (6 variants); non-Qwen passes through.
6. ☑ All 7 R2 commits follow `[tdd-principal] phase-r2: <slice>` prefix; signed by 1Password SSH.
7. ☑ Tree clean at end.

**Hand-off to Phase R3 (eval harness)**:
- The retriever is ready to be evaluated.  R3 imports `HybridRetriever` + `SearchOptions` + `Hit` from `corpus_forge.retrieval` (all re-exported).
- The `RetrievalConfig` model is in place; R3 can read `cfg.retrieval.default_k` etc. directly.
- Pyproject `[retrieval]` extra is **NOT** added in R2 (reserved for R3 per the plan).  Add when wiring the `eval` CLI.
- Note for the eval harness: when scoring with a Qwen3 model, the harness must call `embedder.encode_query(...)` for the query side and `embedder.encode(...)` for the corpus side — asymmetric is now the contract.

---

## Phase R3 — Eval harness + bundled gold set + `eval` CLI

_Owner: tdd-principal.  Plan ref: `/Users/evanowen/.claude/plans/crispy-yawning-crescent.md` § "Phase R3"._

**Carry-overs from R2 (load-bearing)**:
1. Asymmetric `embedder.encode_query(...)` for queries; `embedder.encode(...)` for corpus.  Qwen3-Embedding silently degrades otherwise.
2. `HybridRetriever`, `Retriever`, `SearchOptions`, `Hit`, `RetrievalMetrics` re-exported from `corpus_forge.retrieval`.
3. `Config.retrieval` exposes `default_k`, `fusion`, `alpha`, `rerank_top_n`, `rerank_enabled` — wire CLI defaults to these.
4. R3 owns the `[retrieval]` and `[eval]` extras in `pyproject.toml`.

**Hard rules** (re-asserted for every R3 worker):
- No reranker code — R4 owns it.  `--rerank` flag is a no-op that prints a friendly "lands in R4" message when explicitly passed.
- No MCP code — R5 owns it.
- `--rerank` must NOT silently misbehave; an explicit friendly stderr/stdout notice is mandatory.
- Pinned baseline NDCG@10 floor is REAL — hard-fail CI.  Pick a floor that catches regressions but survives innocuous changes.
- Tolerate chunk-id drift via optional `content_hash` fallback in the gold-set loader (and runner).
- Do not touch README — Phase BR owns the rewrite.
- Atomic commits prefixed `[role] phase-r3: <slice>`; HEREDOC body; Co-Authored-By trailer; signed (do NOT pass `--no-gpg-sign`).

### R3 tasks

| id | title | depends_on | surface | risk | status | claimed_by | notes |
|----|-------|------------|---------|------|--------|------------|-------|
| R3-01 | Add `[retrieval]` + `[eval]` extras to pyproject | — | `pyproject.toml`, `tests/unit/test_pyproject_eval_extras.py` | low | green | tdd-coder | numpy>=1.26 floor for both extras. R4/R5 extras explicitly NOT added (scope-guard tests pin this). |
| R3-02 | `eval/metrics.py` (ndcg_at_k, mrr_at_k, recall_at_k) | — | `corpus_forge/eval/metrics.py`, `tests/unit/test_eval_metrics.py` | low | green | tdd-coder | 29/29 tests pass. Pure NumPy; gain=2**g-1; discount=1/log2(rank+1). Coverage 98%. |
| R3-03 | `eval/dataset.py` — JSONL loader + `GoldQuery` | — | `corpus_forge/eval/dataset.py`, `tests/unit/test_eval_dataset.py` | low | green | tdd-coder | 21/21 tests pass. GoldQuery frozen dataclass; `{path}:{lineno}: <reason>` ValueErrors; content_hashes parallel-length invariant. Coverage 91%. |
| R3-04 | `eval/runner.py` — `evaluate_retriever` + `report` + JSON dump + pinned baseline test | R3-02, R3-03 | `corpus_forge/eval/runner.py`, `tests/unit/test_eval_runner.py` | med | pending | — | End-to-end with in-memory SQLite + FakeEmbedder + toy gold set.  Pinned NDCG@10 floor (>= 0.55 baseline; tester picks exact value, document it).  Hard-fail CI on regression. |
| R3-05 | `eval/__init__.py` + content_hash drift fallback in runner | R3-02, R3-03, R3-04 | `corpus_forge/eval/__init__.py`, `corpus_forge/eval/runner.py`, `corpus_forge/eval/dataset.py`, `tests/unit/test_eval_runner.py` (drift case) | med | pending | — | Re-export `RetrievalMetrics`, public eval fns.  Runner falls back to `content_hash` lookup when configured `chunk_id` missing.  Tolerant fallback discoverable but not noisy. |
| R3-06 | Bundled gold set `forge_self.jsonl` + provenance md | R3-04, R3-05 | `corpus_forge/eval/datasets/forge_self.jsonl`, `corpus_forge/eval/datasets/forge_self.corpus.md` | med | pending | — | ≥20 hand-curated queries against `/tmp/corpus-forge-test.db` seeded from this repo via `scripts/vectorize_repo_sqlite.py`.  1–5 relevant chunk_ids per query.  Pin corpus provenance: chunker (max_chars=1500, overlap=200), default embedder, file set.  Use `scripts/query_repo_sqlite.py` to assist curation; record each chunk_id's content_hash for drift tolerance. |
| R3-07 | `eval` CLI subcommand group (`retrieval`, `corpus-quality`) | R3-04, R3-05 | `corpus_forge/cli.py`, `tests/unit/test_cli_eval.py` | med | pending | — | Two subcommands.  Dual-use docstrings (training-corpus quality FIRST, retrieval correctness SECOND).  `--rerank` prints friendly "lands in R4" message and no-ops.  `--json` writes metrics JSON.  Defaults pulled from `Config.retrieval`. |
| R3-08 | Smoke test for `eval retrieval` CLI | R3-06, R3-07 | `tests/smoke/test_eval_smoke.py` | low | pending | — | `CliRunner` invokes `corpus-forge eval retrieval --dataset forge_self --k 10 --json <tmp>` against a seeded db (or skip-on-missing seed); asserts exit 0, table prints, JSON parseable.  Skip / xfail if seeded db absent — never silently pass without verification. |

### Acceptance details

#### R3-01
- `pyproject.toml` `[project.optional-dependencies]`: `retrieval = ["numpy>=1.26"]` and `eval = ["numpy>=1.26"]`.
- Keep R4 (`rerank`) and R5 (`mcp`) extras OUT of this PR.
- `uv sync --extra retrieval --extra eval` succeeds; no transitive collisions.

#### R3-02
- Signatures:
  - `ndcg_at_k(ranked_ids: list[int], relevant_ids: set[int] | list[int], k: int, *, graded: dict[int, int] | None = None) -> float`
  - `mrr_at_k(ranked_ids: list[int], relevant_ids: set[int] | list[int], k: int) -> float`
  - `recall_at_k(ranked_ids: list[int], relevant_ids: set[int] | list[int], k: int) -> float`
- Known-answer tests:
  - NDCG binary: `ndcg([1,2,3], {1,3}, k=3)` = computed-by-hand value (DCG = 1/log2(2) + 0 + 1/log2(4); ideal DCG = 1/log2(2) + 1/log2(3); normalise).
  - NDCG graded: `ndcg([1,2,3], {1,2,3}, k=3, graded={1:3,2:1,3:2})` = computed value.
  - MRR@10: first hit at rank 1 → 1.0; rank 5 → 0.2; no hit → 0.0.
  - Recall@k: set-overlap math.
- Edge cases: empty rankings → 0.0; empty relevant → 0.0; k > len(ranked) — use what's there, don't IndexError.

#### R3-03
- `@dataclass(frozen=True) class GoldQuery: query_id: str; query: str; relevant_chunk_ids: list[int]; graded: dict[int, int] | None = None; content_hashes: list[str] | None = None`.
- `load_gold(path: Path) -> list[GoldQuery]`.
- Schema validation: missing `query_id`/`query`/`relevant_chunk_ids` → `ValueError` with line + path.
- Graded keys may be str or int; normalise to int internally.
- Mixed binary + graded rows accepted in the same file.

#### R3-04
- `evaluate_retriever(retriever, gold_path, k_values, *, max_queries=None) -> RetrievalMetrics`.
- `report(metrics: RetrievalMetrics) -> str` — formatted table (k vs ndcg/mrr/recall).
- `dump_json(metrics: RetrievalMetrics, out: Path) -> None`.
- Calls `retriever.search(q.query, SearchOptions(k=max(k_values)))` per query; computes per-query then averages.
- Pinned NDCG@10 floor test: builds toy gold set + in-memory SQLite + FakeEmbedder, runs `evaluate_retriever`, asserts `metrics.ndcg[10] >= <pinned floor>` AND `<= 1.0`.  Document the exact pinned floor in test docstring and in `notes` after green.

#### R3-05
- `corpus_forge/eval/__init__.py` re-exports: `RetrievalMetrics`, `GoldQuery`, `load_gold`, `evaluate_retriever`, `report`, `dump_json`, the three metric fns.
- Content-hash drift fallback:
  - `GoldQuery.content_hashes` is `list[str] | None`, parallel to `relevant_chunk_ids`.
  - Runner: when a gold `chunk_id` is missing from the corpus, look up by `content_hash` via `backend.get_chunk_by_content_hash(hash)` (or equivalent — coordinate with R2 surface; if not present, use a thin `backend._execute(...)` shim in the runner with a TODO for R4/R5 to lift cleanly).
  - Drift-fallback test: verify a stale chunk_id resolves via content_hash and the metric is unchanged.

#### R3-06
- Build script-assisted: wipe `/tmp/corpus-forge-test.db`, run `scripts/vectorize_repo_sqlite.py`, then `scripts/query_repo_sqlite.py` per candidate query.
- ≥20 queries spanning: architecture, schema, sync, sqlite-backend, retrieval, eval (meta), licensing, install, embedders, chunkers.
- Each query: 1–5 relevant chunk_ids.  Record content_hashes alongside chunk_ids for drift tolerance.
- `forge_self.corpus.md`: which embedder (`sentence-transformers/all-MiniLM-L6-v2` default), chunker config (`max_chars=1500, overlap=200`), source file set, curation date, how to rebuild.

#### R3-07
- `corpus-forge eval retrieval` options: `--dataset` (name or path), `--k 10,20`, `--metric ndcg,mrr,recall`, `--fusion rrf|alpha`, `--alpha float`, `--rerank/--no-rerank`, `--json PATH`.
- `corpus-forge eval corpus-quality` mirror options, `--dataset` is required path to user JSONL.
- Docstrings frame training-corpus quality as primary use; retrieval-eval as secondary.
- `--rerank=True` → print `"Reranker lands in Phase R4 — running without rerank."` to stderr, proceed without rerank.

#### R3-08
- `tests/smoke/test_eval_smoke.py` uses `typer.testing.CliRunner` (or invokes via subprocess if CliRunner can't reach the lazy imports).
- If `/tmp/corpus-forge-test.db` is absent OR the bundled gold set's chunk_ids no longer resolve (no fallback hits either), `pytest.skip` with a clear message — do NOT silently pass.

### DAG (R3 waves)

- **Wave 0** (parallel, 3 tasks): R3-01, R3-02, R3-03 — all independent.
- **Wave 1** (1 task): R3-04 — needs metrics + dataset.
- **Wave 2** (1 task): R3-05 — needs runner + dataset; touches runner so sequential after R3-04.
- **Wave 3** (parallel, 2 tasks): R3-06 (gold set), R3-07 (CLI) — both depend on R3-04/R3-05, surface disjoint.
- **Wave 4** (1 task): R3-08 (smoke) — needs CLI + gold set.
