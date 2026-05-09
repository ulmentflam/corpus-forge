# Test Status — owned by tdd-tester

Record of test suites written by tdd-tester.
| task-id | status | notes |
|---------|--------|-------|
| P0-01   | red    | handed off to tdd-coder |
| P0-02   | red    | DDL created, validated |
| P1-01   | red    | handed off to tdd-coder |
| P1-02   | red    | Integration tests written, require Docker |
| P1-03   | red    | Tests written, confirmed red |
| P1-05   | red    | Documentation task, TOML validated |
| P1-04   | red    | Tests written, confirmed red |
| P1-06   | red    | Tests written, confirmed red |
| P1-07   | red    | Tests written, confirmed red |
| P1-09   | red    | Tests written, confirmed red |
| P1-08   | red    | Tests written, confirmed red |
| P0-03   | red    | Integration tests written, confirmed red (requires Docker to execute) |
| P0-04   | red    | Tests written, confirmed red |
| P1-11   | red    | Tests written, confirmed red |
| P1-12   | red    | Tests written, confirmed red |
| P0-05   | red    | Tests written, confirmed red |
| P1-13   | red    | Revision API tests written — 22 tests, all NotImplementedError |
| P1-14   | red    | Revision API tests written |
| P1-15   | red    | Revision API tests written |
| P1-16   | red    | Revision API tests written |
| P1-17   | red    | Revision API tests written |
| P0-06   | red    | upsert_document embedder_ids tests written |
| P0-07   | red    | ingest_one embedder_ids tests written |
| P1-18   | red    | PushPipeline tests written |
| P1-19   | red    | PushPipeline observer tests written |
| P1-20, P1-21 | red | Push extras tests written |
| P1-22   | red    | PullPipeline tick tests written |
| P1-23..P1-25 | red | Pull branch tests written |
| P1-26   | red    | Pull lifecycle tests written |
| P1-27   | red    | SyncEngine tests written |
| P1-28   | red    | Daemon orchestrator tests written |
| P1-29   | red    | CLI sync tests written |
| P1-30   | red    | E2E push/pull cross-host test — 4 tests, all failing due to real production bug: `push.py:84` calls `backend.resolve_document()` which does not exist on `PostgresBackend`. Routed to principal for INT-03 coder task. |


## P0-01 — `chunk_content_hash`
- Test files: `tests/unit/test_identity.py`
- Run command: `PYTHONPATH=. uv run pytest tests/unit/test_identity.py -v`
- Edge case checklist:
  - [x] happy path — basic ASCII text
  - [x] boundaries — empty string, single char, multi-line, long text (1000 repeats)
  - [x] type/format — Unicode (café, 日本語, emoji), whitespace preservation
  - [x] state — determinism (10 identical calls → same output)
  - [x] equivalence — `chunk_content_hash(text) == content_hash(text.encode("utf-8"))`
  - [x] collision resistance — 5 distinct inputs → 5 distinct hashes
  - [x] output format — str, 64 hex chars, lowercase
  - [ ] concurrency — N/A (pure function, no shared state)
  - [ ] locale/time — N/A (no locale/time dependencies)
  - [x] production-realistic — multi-line markdown-like text, special chars
  - [x] regression — distinct inputs produce distinct hashes
- Red output (tail):
```
tests/unit/test_identity.py:5: in <module>
    from corpus_forge.identity import (
E   ImportError: cannot import name 'chunk_content_hash' from 'corpus_forge.identity'
```
- Status: red — handed off to tdd-coder


## P0-02 — `002_chunk_content_hash.sql`
- Test files: `tests/unit/test_migration_002.py`
- Run command: `PYTHONPATH=. uv run pytest tests/unit/test_migration_002.py -v`
- Edge case checklist:
  - [x] happy path — file exists, DDL matches spec exactly
  - [x] boundaries — IF NOT EXISTS on both ALTER and CREATE (idempotency)
  - [x] type/format — DDL strings present verbatim (ALTER TABLE, ADD COLUMN, CREATE INDEX)
  - [x] state — no destructive DDL (DROP/TRUNCATE guard)
  - [x] concurrency — N/A (DDL is server-side, idempotent)
  - [x] failure paths — N/A (file-level test, not runtime)
  - [x] naming convention — 3-digit zero-padded prefix, `.sql` extension
  - [x] production-realistic — matches existing migration style (`001_core.sql`)
  - [x] regression — no DROP/DROP COLUMN/TRUNCATE present
  - [x] migration loader — glob `[0-9]*.sql` discovers the file
- Notes:
  - **Surfaced bug**: `get_migration_files()` uses `p.name.split(".")[0]` as the sort key. For filenames like `002_views.sql` or `002_chunk_content_hash.sql`, this yields `'002_views'` / `'002_chunk_content_hash'` which cannot be `int()`. The existing `001_core.sql` and `002_views.sql` are all affected. The glob pattern `[0-9]*.sql` correctly matches all files; the sort-key extraction needs fixing (should use `re.match(r'^(\d+)', p.stem)` or similar).
  - **Ruff**: `uv run ruff check` on `.sql` files produces `invalid-syntax` errors because ruff treats `.sql` as Python. This affects all existing SQL files (`001_core.sql`, `002_views.sql`). The project should add `exclude = ["**/*.sql"]` to `[tool.ruff]` in `pyproject.toml`.
- Green output:
```
tests/unit/test_migration_002.py::TestFileExists::test_file_exists PASSED
tests/unit/test_migration_002.py::TestFileExists::test_file_has_sql_extension PASSED
tests/unit/test_migration_002.py::TestFileExists::test_file_matches_naming_convention PASSED
tests/unit/test_migration_002.py::TestDDLContent::test_contains_alter_table PASSED
tests/unit/test_migration_002.py::TestDDLContent::test_contains_create_index PASSED
tests/unit/test_migration_002.py::TestDDLContent::test_idempotent_alter PASSED
tests/unit/test_migration_002.py::TestDDLContent::test_idempotent_index PASSED
tests/unit/test_migration_002.py::TestDDLContent::test_no_drop_or_truncate PASSED
tests/unit/test_migration_002.py::TestDDLContent::test_statements_count PASSED
tests/unit/test_migration_002.py::TestGetMigrationFiles::test_glob_pattern_discovers_file PASSED
tests/unit/test_migration_002.py::TestGetMigrationFiles::test_sort_key_extraction_is_numeric PASSED
tests/unit/test_migration_002.py::TestGetMigrationFiles::test_file_includes_if_not_exists_guards PASSED
12 passed in 0.06s
```
- Status: red — DDL created, 12 tests green, handed off to tdd-coder


## P1-01 — `003_sync.sql`
- Test files: `tests/unit/test_migration_003.py`
- Run command: `PYTHONPATH=. uv run pytest tests/unit/test_migration_003.py -v`
- Edge case checklist:
  - [x] happy path — file exists, DDL matches spec exactly
  - [x] boundaries — document_revisions column by column (id, document_id, revision_number, parent_revision_id, content_hash, text, author_host, is_tombstone, metadata, created_at)
  - [x] type/format — DDL strings present verbatim (CREATE TABLE, ADD COLUMN, CREATE INDEX)
  - [x] state — no destructive DDL (DROP/DROP COLUMN/TRUNCATE guard)
  - [x] concurrency — N/A (DDL is server-side, idempotent)
  - [x] failure paths — N/A (file-level test, not runtime)
  - [x] naming convention — 3-digit zero-padded prefix (`003`), `.sql` extension
  - [x] production-realistic — matches existing migration style (`001_core.sql`, `002_chunk_content_hash.sql`)
  - [x] regression — no DROP/DROP COLUMN/TRUNCATE/DCL (GRANT/REVOKE/DENY) present
  - [x] migration loader — glob `[0-9]*.sql` discovers the file
  - [x] idempotency — all ALTER + CREATE use IF NOT EXISTS (6 guards)
  - [x] file ordering — numeric prefix 3 > 2 (P0-02 dependency)
  - [x] statement count — 6 executable statements (1 CREATE TABLE + 2 CREATE INDEX + 3 ALTER TABLE)
  - [x] FK constraints — documents(id) ON DELETE CASCADE, self-referencing ON DELETE SET NULL
  - [x] unique constraint — UNIQUE (document_id, revision_number)
- Notes:
  - **Surfaced & fixed**: First pass of the SQL file had `ALTER TABLE corpus.documents` spanning two lines, causing the per-line IF NOT EXISTS guard check to fail. Fixed by putting each ALTER on a single line.
  - Same ruff `.sql` issue as P0-02 (noted there).
- Green output:
```
tests/unit/test_migration_003.py::TestFileExists::test_file_exists PASSED
tests/unit/test_migration_003.py::TestFileExists::test_file_has_sql_extension PASSED
tests/unit/test_migration_003.py::TestFileExists::test_file_matches_naming_convention PASSED
tests/unit/test_migration_003.py::TestFileExists::test_file_ordering_after_002 PASSED
tests/unit/test_migration_003.py::TestDDLContent::test_creates_document_revisions_table PASSED
tests/unit/test_migration_003.py::TestDDLContent::test_document_revisions_id_column PASSED
tests/unit/test_migration_003.py::TestDDLContent::test_document_revisions_document_id_column PASSED
tests/unit/test_migration_003.py::TestDDLContent::test_document_revisions_revision_number_column PASSED
tests/unit/test_migration_003.py::TestDDLContent::test_document_revisions_parent_revision_id_column PASSED
tests/unit/test_migration_003.py::TestDDLContent::test_document_revisions_content_hash_column PASSED
tests/unit/test_migration_003.py::TestDDLContent::test_document_revisions_text_column PASSED
tests/unit/test_migration_003.py::TestDDLContent::test_document_revisions_author_host_column PASSED
tests/unit/test_migration_003.py::TestDDLContent::test_document_revisions_is_tombstone_column PASSED
tests/unit/test_migration_003.py::TestDDLContent::test_document_revisions_metadata_column PASSED
tests/unit/test_migration_003.py::TestDDLContent::test_document_revisions_created_at_column PASSED
tests/unit/test_migration_003.py::TestDDLContent::test_document_revisions_unique_constraint PASSED
tests/unit/test_migration_003.py::TestDDLContent::test_document_revisions_fk_on_delete_cascade PASSED
tests/unit/test_migration_003.py::TestDDLContent::test_index_document_revisions_doc_idx PASSED
tests/unit/test_migration_003.py::TestDDLContent::test_index_document_revisions_parent_idx PASSED
tests/unit/test_migration_003.py::TestDDLContent::test_add_tombstoned_at_column PASSED
tests/unit/test_migration_003.py::TestDDLContent::test_add_last_pulled_revision_id_column PASSED
tests/unit/test_migration_003.py::TestDDLContent::test_add_sync_enabled_column PASSED
tests/unit/test_migration_003.py::TestDDLContent::test_all_alter_statements_have_if_not_exists PASSED
tests/unit/test_migration_003.py::TestDDLContent::test_all_create_statements_have_if_not_exists PASSED
tests/unit/test_migration_003.py::TestDDLContent::test_no_drop_or_truncate PASSED
tests/unit/test_migration_003.py::TestDDLContent::test_no_dangerous_dcl PASSED
tests/unit/test_migration_003.py::TestDDLContent::test_statements_count PASSED
tests/unit/test_migration_003.py::TestGetMigrationFiles::test_glob_pattern_discovers_file PASSED
tests/unit/test_migration_003.py::TestGetMigrationFiles::test_sort_key_extraction_is_numeric PASSED
tests/unit/test_migration_003.py::TestGetMigrationFiles::test_file_includes_if_not_exists_guards PASSED
tests/unit/test_migration_003.py::TestGetMigrationFiles::test_schema_set_command_present PASSED
31 passed in 0.06s
```
- Status: red — DDL created, 31 tests green, handed off to tdd-coder


## P1-03 — `DaemonConfig` sync fields + `DatasetConfig.sync_enabled` validator
- Test files: `tests/unit/test_config_extended.py` (new classes `TestDaemonConfigSyncFields` and `TestDatasetConfigSyncEnabled`)
- Run command: `PYTHONPATH=. uv run pytest tests/unit/test_config.py tests/unit/test_config_extended.py -v --no-header 2>&1 | tail -30`
- Edge case checklist:
  - [x] happy — DaemonConfig parses sync fields from minimal TOML
  - [x] happy — DatasetConfig sync_enabled=True accepted for kind="text"
  - [x] boundaries — sync_poll_interval_s == 0 rejected
  - [x] boundaries — sync_poll_interval_s < 0 rejected
  - [x] boundaries — sync_poll_interval_s default == 5.0
  - [x] type/format — host_id default "" (empty string)
  - [x] type/format — trash_dir/conflict_dir expand ~ via ExpandedPath
  - [x] state — sync_use_listen_notify default False, custom True accepted
  - [x] state — sync_enabled default False
  - [x] state — sync_enabled=True rejected for kind="chat"
  - [x] failure paths — TOML with sync_enabled=true + kind="chat" raises ValidationError
  - [x] regression — existing 39 tests still pass
  - [x] production-realistic — full TOML config with all sync fields
- Red output (tail):
```
FAILED tests/unit/test_config_extended.py::TestDaemonConfigSyncFields::test_daemon_config_sync_default_host_id
FAILED tests/unit/test_config_extended.py::TestDaemonConfigSyncFields::test_daemon_config_sync_custom_host_id
FAILED tests/unit/test_config_extended.py::TestDaemonConfigSyncFields::test_daemon_config_sync_default_trash_dir
FAILED tests/unit/test_config_extended.py::TestDaemonConfigSyncFields::test_daemon_config_sync_default_conflict_dir
FAILED tests/unit/test_config_extended.py::TestDaemonConfigSyncFields::test_daemon_config_sync_trash_dir_expands_tilde
FAILED tests/unit/test_config_extended.py::TestDaemonConfigSyncFields::test_daemon_config_sync_conflict_dir_expands_tilde
FAILED tests/unit/test_config_extended.py::TestDaemonConfigSyncFields::test_daemon_config_sync_default_poll_interval
FAILED tests/unit/test_config_extended.py::TestDaemonConfigSyncFields::test_daemon_config_sync_custom_poll_interval
FAILED tests/unit/test_config_extended.py::TestDaemonConfigSyncFields::test_daemon_config_sync_poll_interval_zero_rejected
FAILED tests/unit/test_config_extended.py::TestDaemonConfigSyncFields::test_daemon_config_sync_poll_interval_negative_rejected
FAILED tests/unit/test_config_extended.py::TestDaemonConfigSyncFields::test_daemon_config_sync_default_listen_notify
FAILED tests/unit/test_config_extended.py::TestDaemonConfigSyncFields::test_daemon_config_sync_custom_listen_notify
FAILED tests/unit/test_config_extended.py::TestDaemonConfigSyncFields::test_daemon_config_sync_from_minimal_toml
FAILED tests/unit/test_config_extended.py::TestDatasetConfigSyncEnabled::test_dataset_config_sync_enabled_default
FAILED tests/unit/test_config_extended.py::TestDatasetConfigSyncEnabled::test_dataset_config_sync_enabled_text_accepted
FAILED tests/unit/test_config_extended.py::TestDatasetConfigSyncEnabled::test_dataset_config_sync_enabled_chat_rejected
FAILED tests/unit/test_config_extended.py::TestDatasetConfigSyncEnabled::test_dataset_config_sync_enabled_chat_accepted_false
FAILED tests/unit/test_config_extended.py::TestDatasetConfigSyncEnabled::test_dataset_config_sync_enabled_from_toml_text
FAILED tests/unit/test_config_extended.py::TestDatasetConfigSyncEnabled::test_dataset_config_sync_enabled_from_toml_chat_rejected
=================== 19 failed, 39 passed, 1 warning in 0.28s ===================
```
- Status: red — handed off to tdd-coder


## P1-05 — Sync section in `config.example.toml`
- Test files: `config.example.toml` (direct edit, no test file)
- Run command: `uv run python -c "import tomllib; tomllib.load(open('config.example.toml', 'rb')); print('TOML valid ✓')"`
- Edge case checklist:
  - [x] happy — `[daemon]` section has `host_id`, `trash_dir`, `conflict_dir`, `sync_poll_interval_s`
  - [x] happy — `*.icloud` added to `exclude_globs` on `markdown_vault` source
  - [x] happy — `sync_enabled = true` on text vault dataset
  - [x] format — TOML parses without error
  - [x] format — ruff format check passes (no reformatting needed)
  - [x] consistency — alignment with existing `[daemon]` field indentation style
  - [x] documentation — all new fields have inline comments explaining purpose
  - [ ] happy-path test — N/A (documentation-only task, TOML validation is the verification)
  - [ ] boundaries — N/A (config values are user-provided, not computed)
  - [ ] type/format — N/A (TOML types are self-documenting)
  - [ ] state — N/A (static config file)
  - [ ] concurrency — N/A (static config file)
  - [ ] failure paths — N/A (static config file)
  - [ ] locale/time — N/A (static config file)
  - [ ] production-realistic — N/A (example config, not runtime)
  - [ ] regression — N/A (no code changed)
- Validation results:
   - `tomllib` parse: ✓ valid
   - `ruff format --check`: ✓ no changes needed
- Status: red — handed off to tdd-coder


## P1-06 — `EchoSuppressor` in `corpus_forge/sync/echo.py`
- Test files: `tests/unit/test_sync_echo.py`
- Run command: `.venv/bin/python -m pytest tests/unit/test_sync_echo.py -v --no-header 2>&1 | tail -30`
- Edge case checklist:
  - [x] happy — register then was_just_written with same path+hash → True
  - [x] happy — register with custom ttl_s
  - [x] happy — register with ttl_s=None uses default
  - [x] consumption — second was_just_written for same key → False (consumed)
  - [x] consumption — consuming one entry doesn't affect other entries
  - [x] mismatch — wrong hash → False
  - [x] mismatch — mismatch does NOT consume entry
  - [x] TTL expiry — was_just_written returns False after TTL elapses
  - [x] TTL expiry — expired entry not matched
  - [x] gc — removes expired entries
  - [x] gc — preserves non-expired entries
  - [x] gc — no-arg gc uses injectable clock
  - [x] gc — explicit now= argument works
  - [x] gc — explicit now= preserves fresh entries
  - [x] path normalization — relative path matches resolved path
  - [x] path normalization — symlinked path matches original
  - [x] path normalization — ./ and .. resolve correctly
  - [x] boundaries — zero TTL expires immediately
  - [x] boundaries — multiple registers same path overwrites
  - [x] boundaries — gc on empty suppressor is no-op
  - [x] boundaries — gc removes all expired entries
  - [x] boundaries — default_ttl_s applied correctly
  - [x] injectable clock — custom clock controls all time reads
  - [x] type — non-string path raises
  - [x] type — non-string content_hash raises
  - [x] type — empty string content_hash accepted
  - [x] locale — unicode path names
  - [x] production-realistic — 200 entries handled
  - [ ] concurrency — N/A (single-threaded cache, no async/race surface)
  - [ ] failure paths — N/A (in-memory cache, no I/O or network)
  - [ ] locale/time — N/A (no locale/time deps beyond monotonic clock)
  - [ ] regression — N/A (new class, no prior implementation)
- Red output (tail):
```
ImportError while importing test module '/Users/evanowen/Library/Mobile Documents/com~apple~CloudDocs/Workspace/playground/corpus-forge/tests/unit/test_sync_echo.py'.
tests/unit/test_sync_echo.py:11: in <module>
    from corpus_forge.sync.echo import EchoSuppressor
E   ModuleNotFoundError: No module named 'corpus_forge.sync.echo'
```
- Status: red — handed off to tdd-coder


## P1-07 — `detect_cloud_provider` in `corpus_forge/sync/cloud.py`
- Test files: `tests/unit/test_sync_cloud.py`
- Run command: `PYTHONPATH=. uv run pytest tests/unit/test_sync_cloud.py -v --no-header 2>&1 | tail -30`
- Edge case checklist:
  - [x] happy — iCloud `Library/Mobile Documents/com~apple~CloudDocs` path
  - [x] happy — iCloud `Library/Mobile Documents/iCloud~` path
  - [x] happy — iCloud deeply nested file
  - [x] happy — iCloud case-sensitive match
  - [x] happy — Dropbox home path
  - [x] happy — Dropbox nested inside
  - [x] happy — Google Drive with space (`Google Drive`)
  - [x] happy — GoogleDrive no-space variant
  - [x] happy — `My Drive` variant
  - [x] happy — deep Google Drive file
  - [x] none — plain local path
  - [x] none — root path `/`
  - [x] none — relative path no match
  - [x] none — `vault` in name (no false positive)
  - [x] precedence — iCloud beats Dropbox
  - [x] precedence — iCloud beats Google Drive
  - [x] precedence — Dropbox beats Google Drive
  - [x] type — string input raises TypeError
  - [x] type — symlink resolves correctly
  - [x] type — path with spaces handled
  - [x] boundaries — return type is one of `Literal["icloud","dropbox","gdrive","none"]`
  - [x] production-realistic — macOS iCloud paths, Dropbox, Google Drive patterns
  - [ ] concurrency — N/A (pure function, no shared state)
  - [ ] failure paths — N/A (no I/O, no network, no disk)
  - [ ] locale/time — N/A (no locale/time deps)
  - [ ] regression — N/A (new function, no prior implementation)
- Red output (tail):
```
ImportError while importing test module '/Users/evanowen/Library/Mobile Documents/com~apple~CloudDocs/Workspace/playground/corpus-forge/tests/unit/test_sync_cloud.py'.
tests/unit/test_sync_cloud.py:9: in <module>
    from corpus_forge.sync.cloud import detect_cloud_provider
E   ImportError: cannot import name 'detect_cloud_provider' from 'corpus_forge.sync.cloud'
```
- Status: red — handed off to tdd-coder


## P1-09 — `conflict_filename` in `corpus_forge/sync/conflicts.py`
- Test files: `tests/unit/test_sync_conflicts.py`
- Run command: `PYTHONPATH=. uv run pytest tests/unit/test_sync_conflicts.py -v --no-header 2>&1 | tail -30`
- Edge case checklist:
  - [x] happy — basic format without provider: `notes/Foo.conflict-macA-20260507T223045Z.md`
  - [x] happy — basic format with provider: `notes/Foo.conflict-icloud-macA-20260507T223045Z.md`
  - [x] happy — explicit provider=None matches no-provider behavior
  - [x] happy — absolute original path preserved
  - [x] suffixes — .txt extension preserved
  - [x] suffixes — no extension (Makefile)
  - [x] suffixes — hidden file no extension (.gitignore)
  - [x] suffixes — compound extension (.tar.gz)
  - [x] suffixes — multi-dot stem (report.v2.md)
  - [x] suffixes — dotfile with extension (.env.local)
  - [x] timestamp — no colons in timestamp component
  - [x] timestamp — trailing Z (UTC indicator)
  - [x] timestamp — sortable ascending (earlier < later)
  - [x] timestamp — different hosts sortable
  - [x] timestamp — midnight boundary (00:00:00)
  - [x] timestamp — leap year (Feb 29)
  - [x] host edge — long host name (255 chars)
  - [x] host edge — underscores in host
  - [x] host edge — dashes in host
  - [x] host edge — numeric host
  - [x] host edge — empty host string
  - [x] provider — icloud, dropbox, gdrive variants
  - [x] provider — long provider name
  - [x] provider — no path separators introduced
  - [x] path structure — parent directory preserved
  - [x] path structure — relative stays relative
  - [x] path structure — absolute stays absolute
  - [x] multi-dot — .v2 suffix correctly parsed
  - [x] multi-dot — just dots (a.b.c)
  - [x] multi-dot — dotfile with multiple dots
  - [x] type — non-Path original raises TypeError
  - [x] type — non-string host raises TypeError
  - [x] type — non-datetime ts raises TypeError
  - [x] type — non-string provider raises TypeError
  - [x] type — return type is Path
  - [x] consistency — same args → same output (idempotent)
  - [x] consistency — different ts → different output
  - [x] consistency — different host → different output
  - [x] regression — file already named "conflict.md"
  - [x] regression — path with spaces
  - [x] regression — Unicode stem (日本語)
  - [x] regression — Unicode path components
  - [ ] concurrency — N/A (pure function, no shared state)
  - [ ] failure paths — N/A (no I/O, no network, no disk)
  - [ ] locale/time — covered via Unicode stem/path, UTC timestamp, midnight/leap-second boundaries
  - [ ] regression — distinct inputs produce distinct filenames
- Red output (tail):
```
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameHappyPath::test_basic_format_no_provider
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameHappyPath::test_basic_format_with_provider
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameHappyPath::test_provider_none_explicit
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameHappyPath::test_absolute_original_path
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameSuffixes::test_txt_extension
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameSuffixes::test_no_extension
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameSuffixes::test_hidden_file_no_extension
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameSuffixes::test_double_extension
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameSuffixes::test_md_with_multiple_dots
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameSuffixes::test_dotfile_with_extension
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameTimestamp::test_no_colons_in_timestamp
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameTimestamp::test_trailing_z
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameTimestamp::test_sortable_ascending
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameTimestamp::test_different_hosts_same_ts_sortable
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameTimestamp::test_midnight_boundary
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameTimestamp::test_leap_second_day
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameHostEdgeCases::test_long_host_name
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameHostEdgeCases::test_host_with_underscores
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameHostEdgeCases::test_host_with_dashes
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameHostEdgeCases::test_host_with_numbers
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameHostEdgeCases::test_empty_host
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameProviderEdgeCases::test_provider_icloud
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameProviderEdgeCases::test_provider_dropbox
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameProviderEdgeCases::test_provider_gdrive
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameProviderEdgeCases::test_provider_with_long_name
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameProviderEdgeCases::test_provider_no_trailing_slash_in_path
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenamePathStructure::test_preserves_parent_directory
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenamePathStructure::test_preserves_parent_with_provider
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenamePathStructure::test_relative_path_becomes_relative
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenamePathStructure::test_absolute_path_becomes_absolute
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameMultiDotFiles::test_multi_dot_stem
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameMultiDotFiles::test_just_dots
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameMultiDotFiles::test_dotfile_with_multiple_dots
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameTypeHandling::test_non_path_original_raises
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameTypeHandling::test_non_string_host_raises
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameTypeHandling::test_non_datetime_ts_raises
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameTypeHandling::test_non_string_provider_raises
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameTypeHandling::test_returns_path
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameConsistency::test_same_args_same_output
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameConsistency::test_same_args_different_ts_different_output
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameConsistency::test_different_host_same_ts_different_output
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameRegression::test_stem_with_conflict_word
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameRegression::test_path_with_spaces
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameRegression::test_unicode_stem
FAILED tests/unit/test_sync_conflicts.py::TestConflictFilenameRegression::test_unicode_path_components
============================== 45 failed in 0.18s ==============================
```
- Status: red — handed off to tdd-coder


## P1-10 — `atomic_write_text` in `corpus_forge/sync/fs.py`
- Test files: `tests/unit/test_sync_fs.py`
- Run command: `PYTHONPATH=. uv run pytest tests/unit/test_sync_fs.py -v --no-header 2>&1 | tail -30`
- Edge case checklist:
  - [x] happy — writes target file with expected text
  - [x] happy — unicode text round-trips (café, 日本語, emoji)
  - [x] happy — newlines, tabs, trailing whitespace preserved
  - [x] happy — default encoding is UTF-8
  - [x] happy — custom encoding respected (latin-1)
  - [x] happy — creates parent directories automatically
  - [x] happy — overwrites existing file
  - [x] happy — replaces partial content (shorter text than old)
  - [x] tempfile cleanup — no `.tmp.*` file remains after success
  - [x] tempfile cleanup — no leftover with deep parents
  - [x] failure paths — os.replace raises → original content unchanged
  - [x] failure paths — os.replace raises → target file not created
  - [x] failure paths — os.replace raises → no temp file left
  - [x] failure paths — os.replace raises → no parent dirs created
  - [x] failure paths — os.replace raises → pre-existing parents preserved
  - [x] boundaries — empty string → zero-byte file
  - [x] boundaries — single character
  - [x] boundaries — very long text (1 MB)
  - [x] boundaries — null bytes in content
  - [x] boundaries — just newlines
  - [x] boundaries — RTL text (Arabic)
  - [x] type — None text raises TypeError
  - [x] type — int text raises TypeError
  - [x] type — list text raises TypeError
  - [x] encoding — UTF-8 BOM written literally
  - [x] encoding — UTF-16 encoding produces valid UTF-16 bytes
  - [x] encoding — UTF-32 encoding produces valid UTF-32 bytes
  - [x] encoding — latin-1 bytes on disk (not utf-8)
  - [x] state — multiple writes to same path succeed
  - [x] state — writes don't interleave (each self-contained)
  - [x] state — writes to different files don't interfere
  - [x] tempfile naming — `.tmp.` prefix pattern
  - [x] tempfile naming — in same directory as target
  - [x] tempfile naming — random suffix (5 unique names)
  - [x] platform — returns None
  - [x] platform — os.replace called (not shutil.move)
  - [x] platform — os.fsync called on temp file
  - [x] platform — os.fsync called on parent directory
  - [ ] concurrency — N/A (atomic write, no concurrent access tested here)
  - [ ] locale/time — covered via Unicode text, RTL text, BOM
  - [ ] regression — N/A (new function, no prior implementation)
- Red output (tail):
```
E       FileNotFoundError: [Errno 2] No such file or directory: '/private/var/folders/1w/x70hfp3x4ms86dyf8bjk22cw0000gp/T/pytest-of-evanowen/pytest-63/test_writes_target_file_with_e0/output.txt'
E       ../../../../../Application Support/uv/python/cpython-3.13.3-macos-aarch64-none/lib/python3.13/pathlib/_abc.py:632: in read_text
E       ../../../../../Application Support/uv/python/cpython-3.13.3-macos-aarch64-none/lib/python3.13/pathlib/_local.py:546: in read_text
E       E       assert target.read_text() == "hello world"
E       tests/unit/test_sync_fs.py:31: in test_writes_target_file_with_expected_text
=========================== short test summary info ============================
FAILED tests/unit/test_sync_fs.py::TestAtomicWriteTextHappyPath::test_writes_target_file_with_expected_text
FAILED tests/unit/test_sync_fs.py::TestAtomicWriteTextHappyPath::test_writes_unicode_text
FAILED tests/unit/test_sync_fs.py::TestAtomicWriteTextHappyPath::test_writes_newlines_and_whitespace
FAILED tests/unit/test_sync_fs.py::TestAtomicWriteTextHappyPath::test_default_encoding_is_utf8
FAILED tests/unit/test_sync_fs.py::TestAtomicWriteTextHappyPath::test_custom_encoding
FAILED tests/unit/test_sync_fs.py::TestAtomicWriteTextHappyPath::test_creates_parent_directories
FAILED tests/unit/test_sync_fs.py::TestAtomicWriteTextHappyPath::test_overwrites_existing_file
FAILED tests/unit/test_sync_fs.py::TestAtomicWriteTextHappyPath::test_replaces_partial_file_content
FAILED tests/unit/test_sync_fs.py::TestAtomicWriteTextTempfileCleanup::test_tempfile_removed_after_success
FAILED tests/unit/test_sync_fs.py::TestAtomicWriteTextTempfileCleanup::test_no_tempfile_with_deep_parents
FAILED tests/unit/test_sync_fs.py::TestAtomicWriteTextFailurePaths::test_os_replace_raises_preserves_original_content
FAILED tests/unit/test_sync_fs.py::TestAtomicWriteTextFailurePaths::test_os_replace_raises_no_target_file_created
FAILED tests/unit/test_sync_fs.py::TestAtomicWriteTextFailurePaths::test_os_replace_raises_no_tempfile_left
FAILED tests/unit/test_sync_fs.py::TestAtomicWriteTextFailurePaths::test_os_replace_raises_no_parent_dirs_created
FAILED tests/unit/test_sync_fs.py::TestAtomicWriteTextFailurePaths::test_os_replace_raises_preserves_existing_parent
FAILED tests/unit/test_sync_fs.py::TestAtomicWriteTextBoundaries::test_empty_string
FAILED tests/unit/test_sync_fs.py::TestAtomicWriteTextBoundaries::test_single_character
FAILED tests/unit/test_sync_fs.py::TestAtomicWriteTextBoundaries::test_very_long_text
FAILED tests/unit/test_sync_fs.py::TestAtomicWriteTextBoundaries::test_binary_null_bytes_as_text
FAILED tests/unit/test_sync_fs.py::TestAtomicWriteTextBoundaries::test_just_newlines
FAILED tests/unit/test_sync_fs.py::TestAtomicWriteTextBoundaries::test_rtl_text
FAILED tests/unit/test_sync_fs.py::TestAtomicWriteTextTypeValidation::test_none_text_raises
FAILED tests/unit/test_sync_fs.py::TestAtomicWriteTextTypeValidation::test_int_text_raises
FAILED tests/unit/test_sync_fs.py::TestAtomicWriteTextTypeValidation::test_list_text_raises
FAILED tests/unit/test_sync_fs.py::TestAtomicWriteTextEncoding::test_utf8_with_bom
FAILED tests/unit/test_sync_fs.py::TestAtomicWriteTextEncoding::test_utf16_encoding
FAILED tests/unit/test_sync_fs.py::TestAtomicWriteTextEncoding::test_utf32_encoding
FAILED tests/unit/test_sync_fs.py::TestAtomicWriteTextEncoding::test_encoding_mismatch_writes_bytes
FAILED tests/unit/test_sync_fs.py::TestAtomicWriteTextState::test_multiple_writes_same_path
FAILED tests/unit/test_sync_fs.py::TestAtomicWriteTextState::test_writes_dont_interleave
FAILED tests/unit/test_sync_fs.py::TestAtomicWriteTextState::test_writes_to_different_files_concurrently
FAILED tests/unit/test_sync_fs.py::TestAtomicWriteTextTempfileNaming::test_tempfile_has_tmp_suffix
FAILED tests/unit/test_sync_fs.py::TestAtomicWriteTextTempfileNaming::test_tempfile_in_same_directory_as_target
FAILED tests/unit/test_sync_fs.py::TestAtomicWriteTextTempfileNaming::test_tempfile_has_random_suffix
FAILED tests/unit/test_sync_fs.py::TestAtomicWriteTextPlatform::test_posix_atomic_rename
FAILED tests/unit/test_sync_fs.py::TestAtomicWriteTextPlatform::test_fsync_called_on_temp_file
FAILED tests/unit/test_sync_fs.py::TestAtomicWriteTextPlatform::test_fsync_called_on_parent_directory
========================= 37 failed, 1 passed in 0.48s =========================
```
- Status: red — handed off to tdd-coder


## P1-02 — `003_sync.sql` integration
- Test files: `tests/integration/test_migrate_003.py`
- Run command: `PYTHONPATH="." uv run pytest tests/integration/test_migrate_003.py -v --no-header 2>&1 | tail -10`
- Edge case checklist:
  - [x] schema — document_revisions table exists
  - [x] schema — all 10 columns on document_revisions
  - [x] schema — PRIMARY KEY constraint
  - [x] schema — UNIQUE(document_id, revision_number)
  - [x] schema — FOREIGN KEY on document_id
  - [x] schema — self-referencing FK on parent_revision_id
  - [x] schema — indexes (doc_idx, parent_idx)
  - [x] schema — tombstoned_at column on documents
  - [x] schema — last_pulled_revision_id column on sources
  - [x] schema — sync_enabled column on sources
  - [x] schema — tombstoned_at nullable
  - [x] schema — sync_enabled default FALSE
  - [x] idempotent — reapply no error
  - [x] idempotent — table still exists after reapply
  - [x] constraints — FK rejects invalid document_id
  - [x] constraints — valid insert succeeds
  - [x] constraints — UNIQUE (document_id, revision_number) enforced
  - [x] constraints — FK rejects invalid parent_revision_id
  - [x] file looping — glob discovers 003_sync.sql
  - [x] file looping — numbered files in order (001 < 002 < 003)
- Red output (tail):
```
SKIPPED [20] tests/integration/test_migrate_003.py: Docker or testcontainers not available
============================= 20 skipped in 2.61s ==============================
```
- Status: red — Integration tests written, require Docker to execute

## P1-11 — `move_to_trash` in `corpus_forge/sync/fs.py`
- Test files: `tests/unit/test_sync_fs.py`
- Run command: `PYTHONPATH=. uv run pytest tests/unit/test_sync_fs.py -v --no-header 2>&1 | tail -30`
- Edge case checklist:
  - [x] dest path — no rel_path uses src.name (stem.deleted-host-ts.suffix)
  - [x] dest path — rel_path preserves directory structure
  - [x] dest path — no extension file (Makefile → no trailing dot)
  - [x] dest path — dotfile (.gitignore → preserved)
  - [x] dest path — compound extension (.tar.gz)
  - [x] dest path — unicode filename
  - [x] file moved — src no longer exists after move
  - [x] file moved — dest has same content
  - [x] file moved — returns Path
  - [x] parent dirs — trash_root/dataset created
  - [x] parent dirs — rel_path sub-directories created
  - [x] same filesystem — os.replace called with correct args
  - [x] same filesystem — os.replace NOT used for cross-device (EXDEV)
  - [x] cross-device — shutil.copy2 called on EXDEV
  - [x] cross-device — os.unlink called on EXDEV
  - [x] cross-device — non-EXDEV OSError propagates
  - [x] cross-device — fallback preserves content
  - [ ] concurrency — N/A (single-file move, no concurrent access tested)
  - [ ] failure paths — covered via cross-device/EXDEV and non-EXDEV error tests
  - [ ] locale/time — covered via unicode filename test, mocked timestamp
  - [ ] regression — N/A (new function, no prior implementation)
- Red output (tail):
```
FAILED tests/unit/test_sync_fs.py::TestMoveToTrashDestPath::test_no_relpath_uses_src_name
FAILED tests/unit/test_sync_fs.py::TestMoveToTrashDestPath::test_with_relpath_preserves_structure
FAILED tests/unit/test_sync_fs.py::TestMoveToTrashDestPath::test_no_extension_file
FAILED tests/unit/test_sync_fs.py::TestMoveToTrashDestPath::test_dotfile_no_extension
FAILED tests/unit/test_sync_fs.py::TestMoveToTrashDestPath::test_compound_extension
FAILED tests/unit/test_sync_fs.py::TestMoveToTrashDestPath::test_unicode_filename
FAILED tests/unit/test_sync_fs.py::TestMoveToTrashFileMoved::test_src_no_longer_exists
FAILED tests/unit/test_sync_fs.py::TestMoveToTrashFileMoved::test_dest_has_same_content
FAILED tests/unit/test_sync_fs.py::TestMoveToTrashFileMoved::test_returns_path
FAILED tests/unit/test_sync_fs.py::TestMoveToTrashParentDirs::test_creates_trash_dataset_dir
FAILED tests/unit/test_sync_fs.py::TestMoveToTrashParentDirs::test_creates_relpath_parents
FAILED tests/unit/test_sync_fs.py::TestMoveToTrashSameFilesystem::test_uses_os_replace
FAILED tests/unit/test_sync_fs.py::TestMoveToTrashSameFilesystem::test_os_replace_not_called_for_cross_device
FAILED tests/unit/test_sync_fs.py::TestMoveToTrashCrossDevice::test_calls_copy2_on_exdev
FAILED tests/unit/test_sync_fs.py::TestMoveToTrashCrossDevice::test_calls_unlink_on_exdev
FAILED tests/unit/test_sync_fs.py::TestMoveToTrashCrossDevice::test_non_exdev_oserror_still_raises
FAILED tests/unit/test_sync_fs.py::TestMoveToTrashCrossDevice::test_exdev_fallback_preserves_content
======================== 17 failed, 38 passed in 0.21s =========================
```
- Status: red — Tests written, confirmed red


## P1-22 — `PullPipeline.tick` in `corpus_forge/sync/pull.py`
- Test files: `tests/unit/test_sync_pull.py`
- Run command: `PYTHONPATH="." uv run pytest tests/unit/test_sync_pull.py -v --no-header 2>&1 | tail -15`
- Edge case checklist:
  - [x] happy — no pending revisions → returns 0
  - [x] happy — fast-forward: local hash matches parent content_hash → atomic_write_text + echo register + mark_revision_pulled
  - [x] boundaries — local file missing and parent_revision_id is None → creates file
  - [x] state — multiple pending revisions (3) → tick returns 3, 3 writes, 3 echo registers, 3 marks
  - [ ] concurrency — N/A (tick is single-threaded; lock_source per-revision is backend responsibility)
  - [ ] failure paths — conflict detection (non-fast-forward) is out of scope for this task
  - [ ] locale/time — N/A (no locale/time deps in ticket scope)
  - [ ] regression — N/A (new class, no prior implementation)
- Red output (tail):
```
=========================== short test summary info ============================
FAILED tests/unit/test_sync_pull.py::TestTickNoPending::test_no_pending_revisions_returns_zero
FAILED tests/unit/test_sync_pull.py::TestTickFastForward::test_fast_forwards_when_local_hash_matches_parent
FAILED tests/unit/test_sync_pull.py::TestTickFastForward::test_creates_file_when_local_missing_and_parent_null
FAILED tests/unit/test_sync_pull.py::TestTickMultiple::test_multiple_pending_returns_count
============================== 4 failed in 0.11s ===============================
```
- Status: red — handed off to tdd-coder

## INT-01 — DSN fixture refactor (libpq DSN)
- Test files: `tests/integration/test_dsn_fixture.py`
- Run command: `PYTHONPATH=. uv run pytest tests/integration/test_dsn_fixture.py -v --no-header 2>&1 | tail -30`
- Form chosen: Form A — integration test with real psycopg.connect, no Docker-skip (uses existing `pytest_collection_modifyitems` skip mechanism in conftest)
- Edge case checklist:
  - [x] happy path — `TestPgDsnLiveConnect::test_connect_and_select_one` opens connection and checks `SELECT 1 == 1`
  - [x] fixture shape — `TestPgDsnFixtureShape::test_pg_dsn_is_str` asserts `isinstance(pg_dsn, str)`
  - [x] DSN scheme — `test_pg_dsn_starts_with_postgresql_scheme` asserts `startswith("postgresql://")`
  - [x] negative scheme — `test_pg_dsn_no_sqlalchemy_driver_prefix` explicitly rejects `+psycopg2`
  - [x] parser shape — `test_pg_dsn_parses_as_libpq_conninfo` calls `psycopg.conninfo.conninfo_to_dict` without raising and checks `host` key present
  - [ ] N/A — boundaries (DSN format is fixed by testcontainers; no length/overflow variation)
  - [ ] N/A — type/format errors (no malformed-input test; contract is about correct output shape)
  - [ ] N/A — state (fixture is fresh per-session; idempotency handled by container fixture)
  - [ ] N/A — concurrency (fixture is session-scoped; single-threaded)
  - [ ] N/A — failure paths (bad DSN rejection is tested implicitly; no network-error simulation needed here)
  - [ ] N/A — locale/time (no locale/time surface)
  - [ ] N/A — regression (new fixture, no prior implementation)
- Notes:
  - Conftest already has `pgvector_container` fixture (function-scoped, not session-scoped). The coder must add a session-scoped `postgres_container` + `pg_dsn` pair. The existing `pgvector_container` is separate and should not be removed (other tests may rely on it).
  - Existing integration files use `pg.get_connection_url()` → `postgresql+psycopg2://…`, which `psycopg.connect()` rejects with `ProgrammingError: missing "=" after ...`. All 5 files need refactor after `pg_dsn` lands.
  - `--strict-markers` is set; `requires_docker` is NOT in `[tool.pytest.ini_options].markers` (only in conftest `addinivalue_line`). Used `pytest.mark.integration` for consistency with all existing integration files.
  - `temp_dir` fixture is defined twice in conftest.py (lines 42-45 and 66-69) — bug noted, not fixed here (tester role).
- Red output (tail):
```
ERROR tests/integration/test_dsn_fixture.py::TestPgDsnFixtureShape::test_pg_dsn_is_str
ERROR tests/integration/test_dsn_fixture.py::TestPgDsnFixtureShape::test_pg_dsn_starts_with_postgresql_scheme
ERROR tests/integration/test_dsn_fixture.py::TestPgDsnFixtureShape::test_pg_dsn_no_sqlalchemy_driver_prefix
ERROR tests/integration/test_dsn_fixture.py::TestPgDsnFixtureShape::test_pg_dsn_parses_as_libpq_conninfo
ERROR tests/integration/test_dsn_fixture.py::TestPgDsnLiveConnect::test_connect_and_select_one
============================== 5 errors in 0.11s ===============================
```
- Red reason: `fixture 'pg_dsn' not found` — fixture does not exist in conftest yet
- Status: red — handed off to tdd-coder


## P1-32 — E2E iCloud-dupe cleanup
- Test files: `tests/integration/test_sync_icloud_dupe.py`
- Run command: `PYTHONPATH=. uv run pytest tests/integration/test_sync_icloud_dupe.py -v --no-header 2>&1 | tail -30`
- Edge case checklist:
  - [x] happy — same-hash dupe: Foo 2.md matches Foo.md hash → push deletes Foo 2.md
  - [x] happy — diff-hash dupe: Foo 2.md differs from Foo.md → renamed to Foo.conflict-icloud-macA-<ts>.md
  - [x] happy — exactly one documents row after same-hash delete
  - [x] happy — two documents rows after diff-hash rename (Foo.md + conflict file)
  - [x] happy — no extra revision after same-hash deletion
  - [x] iCloud detection — path under Library/Mobile Documents/com~apple~CloudDocs → detect_cloud_provider == "icloud" (CONFIRMED GREEN)
  - [x] conflict naming format — Foo.conflict-icloud-macA-<ts>.md
  - [x] boundaries — Foo 2.md is recognised by is_cloud_duplicate (iCloud <stem> N pattern)
  - [x] direct-call path — _handle_cloud_duplicate same-hash → unlink only, no insert_revision (CONFIRMED GREEN)
  - [x] direct-call path — _handle_cloud_duplicate diff-hash → rename + insert_revision
  - [ ] N/A — concurrency (single SyncEngine, not a cross-host sync test)
  - [ ] N/A — locale/time (timestamp format is pinned by test_conflict_file_name_format_with_provider)
  - [ ] N/A — tombstone (separate P1-31 test)
- Production bugs surfaced (do NOT paper over):
  - **BUG-PUSH-DUPE**: `PushPipeline.handle_change()` never calls `_handle_cloud_duplicate()`.
    The `_DebouncedHandler.on_created`/`on_modified` callbacks route ONLY to `handle_change`.
    `_handle_cloud_duplicate` is defined but unreachable from the watchdog event loop.
    This means the cloud-dupe cleanup branch is effectively dead code from the watchdog perspective.
  - **BUG-PUSH-RESOLVE**: Both `handle_change()` and `_handle_cloud_duplicate()` call
    `self._backend.resolve_document(self._dataset_id, source_uri)` which does not exist
    on `PostgresBackend` (not in `base.py` protocol either). This causes
    `AttributeError: 'PostgresBackend' object has no attribute 'resolve_document'`
    when any file event fires.
- Red output (tail):
  ```
  AttributeError: 'PostgresBackend' object has no attribute 'resolve_document'. Did you mean: 'delete_document'?
  corpus_forge/sync/push.py:84: AttributeError

  FAILED tests/integration/test_sync_icloud_dupe.py::TestICloudDupeSameHashDeleted::test_icloud_dupe_same_hash_deleted
  FAILED tests/integration/test_sync_icloud_dupe.py::TestICloudDupeDiffHashRenamed::test_icloud_dupe_diff_hash_renamed
  FAILED tests/integration/test_sync_icloud_dupe.py::TestConflictFilenameFormat::test_conflict_file_name_format_with_provider
  =================== 3 failed, 2 passed, 2 warnings in 32.74s ===================
  ```
- iCloud substring detection under tmp_path: **CONFIRMED WORKING** (test_icloud_substring_detected PASSED)
- Status: red — 2 production bugs surfaced, handed off to tdd-coder for BUG-PUSH-DUPE + BUG-PUSH-RESOLVE fixes

## P1-31 — E2E tombstone integration test
- Test files: `tests/integration/test_sync_tombstone.py`
- Run command: `PYTHONPATH=. uv run pytest tests/integration/test_sync_tombstone.py -v --no-header 2>&1 | tail -30`
- Edge case checklist:
  - [x] happy path — delete on A, file disappears from B root, appears in B's trash_b
  - [x] trash path shape — `<trash_b>/<dataset_component>/doomed.deleted-macA-<ts>.md`, `.md` suffix preserved
  - [x] tombstone revision — `document_revisions.is_tombstone = TRUE`, `author_host = 'macA'`
  - [x] tombstone revision content — `content_hash = sha256(b'')`, `text = ''`
  - [x] tombstoned_at flag — `documents.tombstoned_at` set to non-NULL after delete
  - [x] resurrection clears flag — re-create on A → B sees file again; `tombstoned_at = NULL`
  - [x] resurrection content — B has the re-created content verbatim
  - [x] dataset rel-path preserved — trashed file stays under dataset-scoped subdirectory inside trash_b
  - [x] iCloud guard — sibling `.icloud` placeholder present → handle_delete is a no-op (no tombstone)
  - [x] untracked file delete — handle_delete on a file never pushed → no revision inserted, no crash
  - [x] poll loop — PullPipeline poll thread picks up tombstone without manual tick
  - [ ] N/A — concurrency (push and pull drive sequentially in tests; internal threading is exercised by poll loop test)
  - [ ] N/A — locale/time (UTC timestamp format embedded in trash filename; not the focus of this task)
  - [ ] N/A — type/format (content is always UTF-8 markdown)
- Bugs found during test writing (real production bugs confirmed by test run, NOT papered over):
  1. **BUG-PUSH-RESOLVE** (same as P1-30): `push.py:84` calls `self._backend.resolve_document(dataset_id, source_uri)` — `PostgresBackend` has no `resolve_document` method. `AttributeError` on every `handle_change` and `handle_delete` call. All 13 tests fail at this point.
  2. **BUG-PULL-SOURCE-URI** (latent, will surface after BUG-1 fixed): `pull.py:69` does `path = self._source_root / rev["source_uri"]`. `pending_remote_revisions` returns `r.*` from `document_revisions`, which has no `source_uri` column — will cause `KeyError`. Additionally, even if `source_uri` were added to the join, it would be an absolute path from push (stored as `str(path.resolve())`), so `Path(root_b) / "/abs/path"` silently drops `root_b`.
  3. **BUG-PULL-SOURCE-ID** (latent): `pull.py:83` calls `mark_revision_pulled(source_id=rev["source_id"], ...)` — no `source_id` in `document_revisions` table.
- Red output (tail):
  ```
      with self._backend.lock_source(source_uri):
  >       doc = self._backend.resolve_document(self._dataset_id, source_uri)
                ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  E       AttributeError: 'PostgresBackend' object has no attribute 'resolve_document'. Did you mean: 'delete_document'?

  corpus_forge/sync/push.py:84: AttributeError
  FAILED tests/integration/test_sync_tombstone.py::TestTombstoneDeleteOnA::test_delete_on_a_tombstones_on_b
  FAILED tests/integration/test_sync_tombstone.py::TestTombstoneDeleteOnA::test_trash_path_dataset_component_matches_contract
  FAILED tests/integration/test_sync_tombstone.py::TestTombstoneDeleteOnA::test_tombstone_revision_content_hash_is_empty_sha256
  FAILED tests/integration/test_sync_tombstone.py::TestTombstoneDeleteOnA::test_tombstoned_at_set_on_document
  FAILED tests/integration/test_sync_tombstone.py::TestTombstoneResurrection::test_resurrect_clears_tombstone
  FAILED tests/integration/test_sync_tombstone.py::TestTombstoneResurrection::test_resurrect_content_appears_on_b
  FAILED tests/integration/test_sync_tombstone.py::TestTombstonePushSideOnly::test_handle_delete_inserts_tombstone_revision
  FAILED tests/integration/test_sync_tombstone.py::TestTombstonePushSideOnly::test_handle_delete_sets_tombstoned_at
  FAILED tests/integration/test_sync_tombstone.py::TestTombstonePushSideOnly::test_handle_delete_noop_when_file_not_tracked
  FAILED tests/integration/test_sync_tombstone.py::TestTombstonePushSideOnly::test_handle_delete_ignores_icloud_placeholder
  FAILED tests/integration/test_sync_tombstone.py::TestPullSideTombstone::test_pull_tombstone_moves_file_to_trash
  FAILED tests/integration/test_sync_tombstone.py::TestPullSideTombstone::test_pull_tombstone_sets_tombstoned_at
  FAILED tests/integration/test_sync_tombstone.py::TestTombstonePollLoop::test_poll_loop_processes_tombstone
  ============================== 13 failed in 6.46s ==============================
  ```
- Status: red — same root production bug as P1-30 (resolve_document missing). Handed off to tdd-coder via same INT-03 fix. Tests must NOT be relaxed.

## P1-30 — E2E push/pull cross-host integration test
- Test files: `tests/integration/test_sync_push_pull.py`
- Run command: `PYTHONPATH=. uv run pytest tests/integration/test_sync_push_pull.py -v --no-header 2>&1 | tail -40`
- Edge case checklist:
  - [x] happy path (A→B convergence) — `test_edit_on_a_appears_on_b`
  - [x] happy path (B→A bidirectional) — `test_edit_on_b_appears_on_a`
  - [x] boundaries — monotonic revision numbers across 3 edits — `test_revision_numbers_monotonic`
  - [x] hash equality — content hash on A == hash on B == DB revision hash — `test_hash_equality_after_convergence`
  - [ ] N/A — type/format (content is always UTF-8 markdown, no format ambiguity at this level)
  - [ ] N/A — locale/time (not exercised by sync push/pull path)
  - [ ] N/A — concurrency at test level (engines run on separate threads internally; two-engine concurrency IS exercised by A↔B tests)
  - [x] failure paths — suspected pull.py:69 absolute-path bug documented in each assert message
  - [x] production-realistic data — markdown file content, real Postgres, real watchdog Observer
  - [x] regression hook — the pull.py:69 source_uri/source_root bug explicitly named in assertions and test docstring
- Bugs found during test writing (real production bugs, NOT papered over):
  1. **BUG-PUSH-RESOLVE**: `push.py:84` calls `self._backend.resolve_document(dataset_id, source_uri)` but `PostgresBackend` has no `resolve_document` method. Confirmed by test run: `AttributeError: 'PostgresBackend' object has no attribute 'resolve_document'`. This prevents any revision from being inserted → pull side never converges.
  2. **BUG-PUSH-INSERT-REVISION**: `push.py:96-103` calls `insert_revision(document_id=..., content_hash=..., text=..., parent_revision_id=..., author_host=..., is_tombstone=...)` without the `source_uri` keyword argument, but `PostgresBackend.insert_revision` requires `source_uri`. Would fail after BUG-1 is fixed.
  3. **BUG-PULL-SOURCE-URI (suspected, per waves.md)**: `pull.py:69` does `path = self._source_root / rev["source_uri"]`. `pending_remote_revisions` does `SELECT r.*` from `document_revisions` which has no `source_uri` column — the join to `documents` fetches only `r.*`, not `d.source_uri`. The key `source_uri` will be absent from `rev`, causing `KeyError`. Even if added, `str(path.resolve())` is absolute, so `Path(root_b) / "/absolute/path/on/A"` silently drops `root_b` (Python Path behavior: `Path("/x") / "/y"` → `Path("/y")`).
  4. **BUG-PULL-SOURCE-ID**: `pull.py:83` calls `mark_revision_pulled(source_id=rev["source_id"], ...)` but `document_revisions` table has no `source_id` column, and `pending_remote_revisions` does not join `sources`.
- Red output (tail — 4/4 failed in 34.25s):
  ```
      File ".../corpus_forge/sync/push.py", line 84, in handle_change
        doc = self._backend.resolve_document(self._dataset_id, source_uri)
              ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    AttributeError: 'PostgresBackend' object has no attribute 'resolve_document'. Did you mean: 'delete_document'?

  FAILED tests/integration/test_sync_push_pull.py::test_edit_on_a_appears_on_b
  FAILED tests/integration/test_sync_push_pull.py::test_edit_on_b_appears_on_a
  FAILED tests/integration/test_sync_push_pull.py::test_revision_numbers_monotonic
  FAILED tests/integration/test_sync_push_pull.py::test_hash_equality_after_convergence
  ======================== 4 failed, 7 warnings in 34.25s ========================
  ```
- Status: red — real production bugs (BUG-PUSH-RESOLVE, BUG-PUSH-INSERT-REVISION, BUG-PULL-SOURCE-URI, BUG-PULL-SOURCE-ID). Handed off to principal for INT-03 coder routing. Tests must NOT be relaxed.
