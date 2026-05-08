# Test Matrix — Active Directory Sync

Flat ledger of every test file the plan calls for, plus tests we add along the way for backend revision methods that the plan implies but does not enumerate.

| test file | type | owning task | fixtures / deps | status |
|-----------|------|-------------|-----------------|--------|
| `tests/unit/test_identity.py` (extension) | unit | P0-01 | none | pending |
| `tests/unit/test_chunk_reuse.py` | unit | P0-04, P0-05, P0-06, P0-07 | fake/mock backend or in-memory psycopg fakes; no Docker | pending |
| `tests/integration/test_migrate_002.py` | integration | P0-03 | testcontainers Postgres (`tests/conftest.py`) | pending |
| `tests/integration/test_migrate_003.py` | integration | P1-02 | testcontainers Postgres | pending |
| `tests/integration/test_chunk_reuse_e2e.py` | integration | P0-08 | testcontainers Postgres + active embedder fixture (fake or `FakeEmbedder` if present in conftest) | pending |
| `tests/unit/test_config.py` (extension) | unit | P1-03 | `tmp_path`, monkeypatch | pending |
| `tests/unit/test_config_extended.py` (extension) | unit | P1-04 | `tmp_path`, monkeypatch on `Path.home`/`socket.gethostname` | pending |
| `tests/unit/test_sync_echo.py` | unit | P1-06 | injectable clock | pending |
| `tests/unit/test_sync_cloud.py` | unit | P1-07 | none (string-only) | pending |
| `tests/unit/test_sync_conflicts.py` | unit | P1-08, P1-09 | none | pending |
| `tests/unit/test_sync_fs.py` | unit | P1-10, P1-11, P1-12 | `tmp_path`; xattr availability check (skip if not macOS or unavailable) | pending |
| `tests/integration/test_revisions.py` | integration | P1-13, P1-14, P1-15, P1-16, P1-17 | testcontainers Postgres | pending |
| `tests/unit/test_sync_push.py` | unit | P1-18, P1-19, P1-20, P1-21 | fake backend; `tmp_path`; for observer wiring use watchdog mock or short-running real Observer | pending |
| `tests/unit/test_sync_pull.py` | unit | P1-22, P1-23, P1-24, P1-25, P1-26 | fake backend; `tmp_path`; injectable clock for poll loop | pending |
| `tests/unit/test_sync_engine.py` | unit | P1-27 | mock pipelines; verify start/stop wiring | pending |
| `tests/unit/test_daemon.py` (rewrite) | unit | P1-28 | monkeypatched `SyncEngine` and `ingest_main`; signal injection | pending |
| `tests/unit/test_cli_sync.py` | unit | P1-29 | typer `CliRunner`; mock backend | pending |
| `tests/integration/test_sync_push_pull.py` | integration | P1-30 | testcontainers Postgres; two `tmp_path` roots; tiny `sync_poll_interval_s` (e.g. 0.2s) | pending |
| `tests/integration/test_sync_tombstone.py` | integration | P1-31 | testcontainers Postgres; `tmp_path` root; trash-dir under `tmp_path` | pending |
| `tests/integration/test_sync_icloud_dupe.py` | integration | P1-32 | testcontainers Postgres; `tmp_path` simulating an iCloud-shaped dir | pending |

## Behaviors to pin down (in plain English) — per test file

### `tests/unit/test_identity.py` (P0-01)
- `chunk_content_hash("hello")` is sha256 of `b"hello"`.
- `chunk_content_hash("")` is the well-known sha256 of empty string `e3b0c44...b855`.
- Hashing is stable across calls and platform-independent.
- Unicode round-trips correctly (uses utf-8).

### `tests/unit/test_chunk_reuse.py` (P0-04..P0-07)
- After `upsert_document` writes chunks, every row has a non-null `content_hash` matching `chunk_content_hash(text)`.
- `_copy_reusable_embeddings` returns `set()` when no prior chunk shares the hash.
- `_copy_reusable_embeddings` copies a vector row from prior chunk to new chunk for each embedder when hash matches.
- The per-call cache prevents repeat `(content_hash, embedder_id)` SELECTs (assert via spy/mock counter).
- `upsert_document(..., embedder_ids=None)` does not invoke `_copy_reusable_embeddings`.
- `ingest_one` resolves embedder ids and forwards them to `upsert_document`.

### `tests/integration/test_migrate_002.py` (P0-03)
- Apply migration on a fresh DB → `chunks.content_hash` column exists.
- Insert chunks with `content_hash IS NULL` (simulating pre-002 data) → re-run migration → all rows have `content_hash = sha256(text)`.
- Re-run again → no rows updated (idempotent).

### `tests/integration/test_migrate_003.py` (P1-02)
- Apply on fresh DB → all schema objects exist (`document_revisions`, `documents.tombstoned_at`, `sources.last_pulled_revision_id`, `sources.sync_enabled`).
- Re-run → no errors, no spurious changes.
- Foreign-key constraints fire correctly (deleting a `documents` row cascades into `document_revisions`).

### `tests/integration/test_chunk_reuse_e2e.py` (P0-08)
- Verification metric: ingest a 10-chunk markdown doc, capture all `(chunk_id, embedding)` pairs.
- Append a paragraph (≤ one new chunk's worth of text) and re-ingest.
- After re-ingest, count chunks whose `content_hash` is unchanged from the pre-append snapshot AND whose embedding row is preserved verbatim.
- Assert count ≥ 7 (i.e., ≥70% reuse on a small append).
- Encoder spy: count of texts the embedder was asked to encode is ≤ 3 on the second pass (allowing for boundary chunks shifted by overlap).

### `tests/unit/test_config.py` / `test_config_extended.py` (P1-03, P1-04)
- New `DaemonConfig` fields parse with defaults from a minimal TOML.
- `sync_poll_interval_s <= 0` rejected.
- `sync_enabled = true` rejected when `kind != "text"` (validator).
- `host_id` resolution: explicit value → used; missing value but file present → file wins; neither → `socket.gethostname()` returned and persisted to the host_id file.
- Persisted host_id survives a hostname change (monkeypatch `socket.gethostname` to a different value, expect the persisted value).

### `tests/unit/test_sync_echo.py` (P1-06)
- `register` then `was_just_written` for same path+hash returns True.
- Second `was_just_written` for same key returns False (entry consumed).
- Mismatched hash returns False.
- After TTL elapses (advance injected clock), `was_just_written` returns False.
- `gc` removes expired entries.
- Path normalization: registering with relative or symlinked path matches when called with the resolved path (and vice versa).

### `tests/unit/test_sync_cloud.py` (P1-07)
- iCloud Drive path (`~/Library/Mobile Documents/com~apple~CloudDocs/...`) → `"icloud"`.
- iCloud-app container (`~/Library/Mobile Documents/iCloud~md~obsidian/...`) → `"icloud"`.
- Dropbox path → `"dropbox"`.
- Google Drive variants (`Google Drive`, `GoogleDrive`, `My Drive`) → `"gdrive"`.
- Plain `~/Documents` or `/tmp` → `"none"`.

### `tests/unit/test_sync_conflicts.py` (P1-08, P1-09)
- `is_cloud_duplicate("Foo 2.md")` matches with provider `"icloud"`, canonical `Foo.md`.
- `is_cloud_duplicate("Foo (3).md")` matches with provider `"icloud"`.
- `is_cloud_duplicate("Foo (Bobs-MBP's conflicted copy 2026-05-07).md")` matches with provider `"dropbox"`, canonical `Foo.md`.
- `is_cloud_duplicate("Foo (1).md")` matches with provider `"gdrive"`.
- `is_cloud_duplicate("Foo-conflict-2026-05-07-1.md")` matches with provider `"gdrive"`.
- `is_cloud_duplicate("Foo copy.md")` and `"Foo copy 2.md"` match with provider `"finder"` (or `"icloud"`/`"none"` per resolved policy — see open-questions).
- Non-matches: `"Foo.md"`, `"Foo bar.md"`, `"Foo 2x.md"`.
- `conflict_filename(Path("notes/Foo.md"), host="macA", ts=…)` → `notes/Foo.conflict-macA-20260507T223045Z.md`.
- With provider: `notes/Foo.conflict-icloud-macA-20260507T223045Z.md`.

### `tests/unit/test_sync_fs.py` (P1-10, P1-11, P1-12)
- `atomic_write_text` writes target with expected bytes; tempfile gone afterwards.
- Crash simulation: writing to existing target with mid-flight failure leaves original file intact (use a monkeypatched `os.replace` raising; assert original survives).
- `move_to_trash` produces correct destination path and creates parent dirs.
- `move_to_trash` is atomic on same filesystem; falls back to copy+unlink across devices (mock `os.replace` to raise `OSError(EXDEV)`).
- `is_icloud_placeholder("Foo.md.icloud")` with size 0 → True.
- `is_icloud_placeholder("Foo.md")` (real file) → False.
- `is_dataless` returns False on missing xattr support (no crash).

### `tests/integration/test_revisions.py` (P1-13..P1-17)
- `insert_revision` allocates monotonic `revision_number` per `document_id` (insert two revisions; numbers are 1 then 2).
- `insert_revision` holds the lock through MAX-and-INSERT (test concurrent inserts in two threads against same source_uri; both succeed, no duplicate `revision_number`).
- `latest_revision` returns highest `revision_number` row, or None when document has no revisions.
- `pending_remote_revisions` filters out the caller's own host.
- `pending_remote_revisions` orders by `id ASC`.
- `mark_revision_pulled` is monotonic (`GREATEST`); calling with a smaller id does not regress the pointer.
- `set_tombstone` / `clear_tombstone` toggle `documents.tombstoned_at`.

### `tests/unit/test_sync_push.py` (P1-18..P1-21)
- Unchanged file (mtime cache hit) → handler is a no-op (no hash, no insert).
- Modified file → `insert_revision` called once with correct args; `upsert_document` called with `embedder_ids`.
- Echo match → handler drops without inserting.
- Cloud-dupe matching hash → file deleted, no revision.
- Cloud-dupe differing hash → file renamed to conflict naming, revision inserted.
- Delete event → tombstone revision; `documents.tombstoned_at` set.
- Delete event with sibling `.icloud` placeholder → no tombstone (eviction, not delete).
- Watchdog wiring: simulated event for hidden file (`.foo.md`) is filtered.
- Watchdog wiring: events for files matching `exclude_globs` filtered.
- Debounce: rapid same-path events coalesce to one `handle_change`.

### `tests/unit/test_sync_pull.py` (P1-22..P1-26)
- Fast-forward (parent hash matches local) → file written, echo registered, pointer advanced.
- Already-in-sync (new hash matches local) → no write, echo registered, pointer advanced.
- Conflict (no match) → local renamed to conflict, incoming written, echo registered for canonical only.
- Tombstone → file moved to trash, `set_tombstone` called.
- Resurrection (non-tombstone after tombstone) → fast-forward branch handles it; `clear_tombstone` called via the fast-forward write path or pull-side branch (assert behavior).
- Poll loop: `tick` is invoked at expected interval (use injected clock); stop event halts the loop within one interval.
- `pending_remote_revisions` returning empty → no work, no errors, pointer unchanged.

### `tests/unit/test_sync_engine.py` (P1-27)
- `start()` calls `start()` on both push and pull pipelines.
- `stop()` calls `stop()` on both, regardless of which started first.
- Constructor wires `host_id` and `daemon_config` into both pipelines.
- Idempotent: calling `stop()` twice does not raise.

### `tests/unit/test_daemon.py` (P1-28)
- For a config with one `sync_enabled` dataset and one not, the orchestrator builds exactly one `SyncEngine` and falls through to `ingest_main` for the other.
- SIGINT/SIGTERM triggers `stop()` on the engine and exits cleanly.
- (Existing tests for the 39-line stub are obsolete; this task replaces them.)

### `tests/unit/test_cli_sync.py` (P1-29)
- `corpus-forge sync status` prints per-dataset row (mock backend returns canned stats).
- `sync pull --once -d DATASET` triggers one `tick`.
- `sync push -d DATASET` walks source and pushes pending changes.
- `sync resolve <file> --strategy keep-local` overwrites the canonical with the conflict copy and removes the conflict file.
- `sync resolve <file> --strategy keep-remote` removes the conflict file (canonical was already remote).
- `sync resolve <file> --strategy merge` exits with `BadParameter` (P2 deferred).
- `sync history <source_uri>` prints revision rows in reverse-chronological order.

### `tests/integration/test_sync_push_pull.py` (P1-30)
- Spin up testcontainers Postgres + two `SyncEngine` instances with distinct `host_id` against two `tmp_path` roots.
- Write a markdown file in root A → within ~1s, file appears in root B with same content.
- Edit on B → appears on A.
- Hashes equal on both sides; revision numbers strictly increase.

### `tests/integration/test_sync_tombstone.py` (P1-31)
- Setup: file exists on both A and B (synced).
- Delete on A → tombstone revision visible in DB; file appears in B's `trash_dir/<dataset>/<rel>.deleted-<host>-<ts>.md`.
- `documents.tombstoned_at` is set.
- Recreate file on A → file reappears on B; `documents.tombstoned_at` is NULL.

### `tests/integration/test_sync_icloud_dupe.py` (P1-32)
- Drop `Foo 2.md` matching hash next to `Foo.md` in A's root → push deletes `Foo 2.md`; only one `documents` row, no extra revision.
- Drop `Foo 2.md` differing from `Foo.md` → push renames it to `Foo.conflict-icloud-<host>-<ts>.md` and inserts a conflict revision; B sees both files after pull.

## Coverage / regression watch-list

- Existing unit tests must continue to pass after each task.
- `make test-unit` enforces `--cov-fail-under=85`. Watch coverage on:
  - `corpus_forge/daemon.py` (was untested stub; rewrite must be covered).
  - `corpus_forge/sync/*` (new code; aim for ≥85% on each module).
  - `corpus_forge/backends/postgres.py` (new methods; integration tests count toward coverage only when `make test` is run as a whole).
- `tests/integration/*` is **not** gated by coverage; it produces revision-API confidence and E2E guarantees.
