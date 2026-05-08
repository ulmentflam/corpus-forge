# Definition of Done — Active Directory Sync

Two phases, two gates. Both gates tie back to `active_directory_sync.md` §Verification.

## P0 — Chunk-level embedding reuse

### Required artifacts shipped

- [ ] `corpus_forge/identity.py::chunk_content_hash` exists and is exported.
- [ ] `corpus_forge/schema/002_chunk_content_hash.sql` exists and is picked up by `get_migration_files`.
- [ ] Backfill in migration runner is idempotent.
- [ ] `corpus.chunks.content_hash` column populated for all rows after migration.
- [ ] `PostgresBackend.upsert_document` writes `content_hash` on every chunk INSERT.
- [ ] `PostgresBackend._copy_reusable_embeddings` exists with per-call cache.
- [ ] `PostgresBackend.upsert_document(..., embedder_ids=...)` accepts and honors the new arg.
- [ ] `corpus_forge.ingest.ingest_one` resolves and forwards active embedder ids.

### Required tests green

- [ ] `tests/unit/test_identity.py` — new cases for `chunk_content_hash`.
- [ ] `tests/unit/test_chunk_reuse.py` — all behaviors per test-matrix.md.
- [ ] `tests/integration/test_migrate_002.py` — column added + backfill idempotent.
- [ ] `tests/integration/test_chunk_reuse_e2e.py` — **≥70% reuse** on small append (this is the plan's verification metric).

### Required gates green

- [ ] `make lint` (ruff check) — clean.
- [ ] `make format-check` (ruff format --check) — clean.
- [ ] `make typecheck` (pyrefly strict) — clean.
- [ ] `make test-unit` — clean and `--cov-fail-under=85` passing.
- [ ] `make test-integration` — `test_migrate_002.py` and `test_chunk_reuse_e2e.py` pass.
- [ ] `make ci` — full pipeline green.

### Manual verification (per plan §Verification P0.3)

- [ ] On a real ~50-note vault, time second ingest pass after appending one chunk to one note. Confirm only that document's tail chunk(s) hit the encoder. (Manual; principal logs result in `tasks.md` summary.)

### P0 stop condition

All P0 boxes checked AND no P1 work has begun. P0 is the hard gate before any P1 push/pull task is dispatched.

## P1 — Cross-host sync engine

### Required artifacts shipped

- [ ] `corpus_forge/schema/003_sync.sql` applied; tables and columns present.
- [ ] `PostgresBackend` methods: `insert_revision`, `latest_revision`, `pending_remote_revisions`, `mark_revision_pulled`, `set_tombstone`, `clear_tombstone`.
- [ ] `corpus_forge/sync/` package exists with: `__init__.py`, `engine.py`, `push.py`, `pull.py`, `echo.py`, `conflicts.py`, `fs.py`, `cloud.py`. `SyncEngine` exported from `__init__`.
- [ ] `corpus_forge/config.py`: `DaemonConfig` sync fields, `DatasetConfig.sync_enabled`, validator, `Config.host_id()` resolver, first-run persistence to `~/.config/corpus-forge/host_id`.
- [ ] `config.example.toml`: sync example block; `*.icloud` in default exclude_globs.
- [ ] `corpus_forge/daemon.py`: rewritten orchestrator wires `SyncEngine` per sync-enabled dataset and `ingest_main` for the rest; SIGINT/SIGTERM clean shutdown.
- [ ] `corpus_forge/cli.py`: `sync` Typer subgroup with `status`, `pull`, `push`, `resolve`, `history`. `merge` strategy stubbed as P2.

### Required tests green

- [ ] `tests/integration/test_migrate_003.py`.
- [ ] `tests/unit/test_config.py` / `test_config_extended.py` — sync-related additions.
- [ ] `tests/unit/test_sync_echo.py`.
- [ ] `tests/unit/test_sync_cloud.py`.
- [ ] `tests/unit/test_sync_conflicts.py`.
- [ ] `tests/unit/test_sync_fs.py`.
- [ ] `tests/integration/test_revisions.py` — including monotonicity-under-concurrency case.
- [ ] `tests/unit/test_sync_push.py`.
- [ ] `tests/unit/test_sync_pull.py`.
- [ ] `tests/unit/test_sync_engine.py`.
- [ ] `tests/unit/test_daemon.py` (rewritten).
- [ ] `tests/unit/test_cli_sync.py`.
- [ ] `tests/integration/test_sync_push_pull.py`.
- [ ] `tests/integration/test_sync_tombstone.py`.
- [ ] `tests/integration/test_sync_icloud_dupe.py`.

### Required gates green

- [ ] `make lint`.
- [ ] `make format-check`.
- [ ] `make typecheck` (pyrefly strict — must accept the new `sync/` package).
- [ ] `make test-unit` with `--cov-fail-under=85`.
- [ ] `make test-integration`.
- [ ] `make test-fuzz` — no regressions (no new fuzz tests required by plan, but existing must still pass).
- [ ] `make test-smoke` — no regressions.
- [ ] `make ci`.

### Manual cross-Mac smoke (per plan §Verification P1.3)

- [ ] Both Macs configured with `sync_enabled = true` against shared Postgres, pointed at a non-iCloud dir on each.
- [ ] Edit a markdown file on Mac A → appears on Mac B within `sync_poll_interval_s`.
- [ ] Delete on Mac A → moves to trash dir on Mac B.
- [ ] Concurrent edit on both → exactly one canonical winner per side, plus a `.conflict-<host>-<ts>.md` of the loser, also synced to both sides.

### Manual iCloud smoke (per plan §Verification P1.4)

- [ ] Same as above but vault under iCloud.
- [ ] `.icloud` placeholder files are not treated as tombstones.
- [ ] Trigger an iCloud-generated `Foo 2.md` (e.g., edit offline on both Macs while iCloud paused, then resume) and confirm corpus-forge either deletes the noise dupe (when content matches) or renames it to its own conflict naming and ingests it (when it doesn't).

### CLI sanity

- [ ] `corpus-forge sync status` reports sane numbers on each Mac.
- [ ] `corpus-forge sync history <source_uri>` shows revisions in order.
- [ ] `corpus-forge sync resolve <conflict_file> --strategy keep-local|keep-remote` works.

### P1 stop condition

All P1 boxes checked. The final summary in `tasks.md` lists files changed, gates run, coverage delta vs baseline, and the cross-Mac smoke verdict.

## Out of scope at done time (must not be checked)

- LISTEN/NOTIFY low-latency channel.
- `sync resolve --strategy merge`.
- Section-level 3-way merge.
- Tombstone retention sweeper.
- Revision compaction / checkpointing.
- Content-addressed `chunk_texts` table.

These remain in the P2 backlog (`active_directory_sync.md` §P2).
