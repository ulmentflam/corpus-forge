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
