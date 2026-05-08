# Test Status — owned by tdd-tester

Record of test suites written by tdd-tester.
| task-id | status | notes |
|---------|--------|-------|
| P0-01   | red    | handed off to tdd-coder |
| P0-02   | red    | DDL created, validated |
| P1-01   | red    | handed off to tdd-coder |
| P1-03   | red    | Tests written, confirmed red |
| P1-05   | red    | Documentation task, TOML validated |
| P1-06   | red    | Tests written, confirmed red |
| P1-07   | red    | Tests written, confirmed red |


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
