# Test Status — owned by tdd-tester

Record of test suites written by tdd-tester.
| task-id | status | notes |
|---------|--------|-------|
| DR-T7   | red    | 30 fail / 0 pass. All 28 `_render_status`/`print_ingest_status` tests fail TypeError (unexpected kwarg `stale_threshold`). 2 config-threshold tests fail pydantic ValidationError (`scan.stale_run_threshold` Extra inputs not permitted — DR-G2 needed). Badge locked: `STALE` uppercase no brackets. JSON key locked: `"stale"` lowercase, omitted (not false) when not stale. Handed to tdd-coder (DR-G6). |
| DR-T6   | red    | 19 fail / 3 pass. FAIL reasons: mark_stale_runs not called (AssertionError: Expected call: mark_stale_runs(…) / Not called); latest_unfinished_ingest_run called without host= (AssertionError: {} got None). 3 PASSes correct: resume=False already skips the call, no-stale-log when count=0 is vacuously true. Decision: stale_run_threshold=0.0 → ingest_once still calls mark_stale_runs; no-op is backend's responsibility. Handed to tdd-coder. |
| DR-T5   | red    | 10 fail (AssertionError: path-based prefix returned instead of filesystem://logical/<name>) + 13 pass (back-compat + legacy + empty-string defensive + API source). Handed to tdd-coder. |
| DR-T4   | red    | 2 unit ABC + 19 Postgres + 20 SQLite = 41 new tests; all 41 fail AttributeError. 15 pre-existing tests still pass. Handed to tdd-coder. |
| DR-T1   | red    | 51 fail / 1 pass. All failures: AttributeError (field missing) on accept/default/coexist/TOML tests; AssertionError pre-condition guard on reject tests. 1 pass = import smoke (correct). Decision locked: empty string → ValidationError (not coerced to None). Pattern ^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$ enforced. Handed to tdd-coder. |
| DR-T2   | red    | 9 new tests fail: 1 unit ABC signature + 4 Postgres host-scope + 4 SQLite host-scope. All fail for the correct reasons (no `host` param on backends). 109 existing tests still pass. Handed off to tdd-coder. |
| SR-T3   | red    | `tests/integration/test_sqlite_ingest_runs.py` (45 FAIL + 1 acceptable-pass) + `tests/unit/test_filelock.py` (collection ImportError) + `tests/unit/test_sqlite_backend.py` (5 FAIL). Handed off to tdd-coder. |
| SR-T2   | red    | 16 unit tests in `tests/unit/test_backend_abc_ingest_runs.py` + 46 integration tests in `tests/integration/test_postgres_ingest_runs.py`. All 62 fail with `AttributeError` — methods not yet on Protocol or PostgresBackend. Handed off to tdd-coder. |
| W3-01   | red    | `tests/cli/test_setup_quick.py` (9 tests) + `tests/cli/test_banner.py` (5 tests). All 14 fail at RED (missing `run_quick`, `_probe_ollama`, `_urlopen_compat`, `--quick` flag, banner-on-setup). Ready for GREEN. |
| W3-02   | red    | `tests/cli/test_doctor_json.py` (10 tests) + `tests/cli/test_doctor_banner_in_json_mode.py` (1 test). All 11 fail at RED (missing `DoctorReport.to_json`, `--json` flag, banner-on-doctor, exit-code mapping). Ready for GREEN. |
| J4-01   | red    | 47 tests in `tests/unit/test_curation_selector.py` covering score primitives, dataclass discipline, batch grouping, and generic-walk fallback. Suite went green against the implementation in the same task — see code-status.md. |
| J4-02   | pending | tester dispatched — RED suite for 3 new MCP tool dispatches. |
| J4-03   | pending | tester dispatched — RED suite asserts presence + frontmatter of the three new skill assets. |
| J4-04   | pending | tester dispatched — RED suite for rot-detectors + integration test (depends on J4-01/02/03 GREEN). |
| J1-01   | red    | 55 tests in tests/unit/test_estimate.py; module `corpus_forge.estimate` and `corpus_forge.config.EstimateConfig` both missing — all 55 fail at import. Ready for GREEN. |
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
| B-01    | red    | 21 tests written; 8 failing (loader module missing + pyproject sqlite extra absent), 9 skipped (loader/sqlite-vec not yet installed — correct), 4 passing (pyproject structural guards). |

| B-02    | red    | 130 tests written across 4 files; 26 failed, 92 errors (fixture errors from missing SQL files), 12 passed. Red for correct reasons: SQL files don't exist; migrate.py lacks dialect param. |
| B-03-fix | green | Narrowed `test_no_postgres_backfill_sql_executed` assertion (Option 1: strip `--` comments). 29/29 tests pass. |
| B-04    | red    | 18 tests written for register_embedder; all failing with AttributeError (method not yet implemented). 848 existing tests still pass. |
| D-03    | red    | backfill test (3 tests) + parity ext to 0002_chunk_content_hash — all fail CommandError; 0001_core parity GREEN. |
| G-05    | red (2/3 fail) | Integration smoke written. 2 tests fail: export_chat does not resolve custom templates from DB (production gap). 1 test passes (builtin chatml render). Skill contract rename: 4 GREEN. |
| SR-T5   | red    | 27 tests in `tests/unit/test_ingest_run_lock.py`. All fail at collection (ImportError: `ingest_run_lock_key` not in `corpus_forge.identity`, `IngestRunInProgressError` not in `corpus_forge.backends.base`). Ready for tdd-coder. |

## DR-T4
- Test files:
  - `tests/unit/test_backend_abc_ingest_runs.py` (ADDED `TestMarkStaleRunsProtocol` class — 2 new tests)
  - `tests/integration/test_postgres_mark_stale_runs.py` (NEW — 19 tests)
  - `tests/integration/test_sqlite_mark_stale_runs.py` (NEW — 20 tests)
- Run command: `uv run pytest tests/unit/test_backend_abc_ingest_runs.py tests/integration/test_postgres_mark_stale_runs.py tests/integration/test_sqlite_mark_stale_runs.py -q --no-cov`
- Edge case checklist:
  - [x] happy — `test_marks_old_running_as_failed` (both backends)
  - [x] boundaries — `test_does_not_touch_young_running` (within threshold), `test_row_at_exact_threshold_boundary` (1s over threshold), `test_empty_table_returns_zero`
  - [x] type/format — `test_return_type_is_int` (always int even on 0), error message regex on `test_error_message_format`
  - [x] state — `test_idempotent_second_call_returns_zero` (already-failed rows not re-selected), `test_last_progress_at_unchanged_after_mark` (audit trail preserved), `test_mixed_statuses_only_running_marked` (SQLite only; parallel multi-status table)
  - [ ] N/A — concurrency (serial method; no concurrent-write path in scope for this surface)
  - [x] failure paths — `test_operationalerror_swallowed_returns_zero` (both backends), `test_threshold_zero_noop`, `test_threshold_negative_noop`
  - [ ] N/A — locale/time (timestamps are UTC throughout; no locale-sensitive paths)
  - [x] production-realistic — rows inserted with explicit past timestamps to simulate stale runs; `host`/`pid` fields from realistic values (e.g. `"specific-host"`, `99999`)
  - [x] regression hooks — `test_does_not_touch_interrupted` pins principal decision #6 (interrupted is sticky); `test_error_message_format` pins the exact regex from principal decision #7; `test_last_progress_at_unchanged_after_mark` pins the audit-trail invariant
- Red output (tail):
  ```
  FAILED tests/integration/test_postgres_mark_stale_runs.py::TestMarkStaleRuns::test_marks_old_running_as_failed
  ...
  AttributeError: 'PostgresBackend' object has no attribute 'mark_stale_runs'
  FAILED tests/integration/test_sqlite_mark_stale_runs.py::TestMarkStaleRuns::test_marks_old_running_as_failed
  ...
  AttributeError: 'SQLiteBackend' object has no attribute 'mark_stale_runs'
  FAILED tests/unit/test_backend_abc_ingest_runs.py::TestMarkStaleRunsProtocol::test_mark_stale_runs_in_protocol
  AssertionError: StorageBackend.mark_stale_runs not found — add Protocol stub (DR-G5)
  FAILED tests/unit/test_backend_abc_ingest_runs.py::TestMarkStaleRunsProtocol::test_mark_stale_runs_signature
  AssertionError: StorageBackend.mark_stale_runs does not exist — add Protocol stub (DR-G5)
  42 failed, 15 passed in 5.46s
  ```
- Status: red — handed off to tdd-coder

## DR-T8
- Test files: `tests/unit/test_docs_distributed_resume.py`
- Run command: `uv run pytest tests/unit/test_docs_distributed_resume.py -q --no-cov`
- Edge case checklist:
  - [x] happy — each assertion has a passing path (correct heading/anchor/field present)
  - [x] boundaries — position checks: logical_name before second [[datasets]], ## Multi-machine ingest after ## Backends and before ## Multi-format extractor layer
  - [x] type/format — regex anchored to line-start for headings; numeric vs string form for stale_run_threshold
  - [ ] N/A — state (pure static file reads, no mutable state)
  - [ ] N/A — concurrency (pure file reads)
  - [ ] N/A — failure paths (files are repo-local; missing file is a test collection error, not a runtime concern)
  - [ ] N/A — locale/time (ASCII doc content, no time involved)
  - [x] production-realistic data — reads actual repo files, not toy fixtures
  - [ ] N/A — regression hooks (no prior bug referenced)
- Locked strings for DR-G7 (verbatim):
  - config.example.toml logical_name: commented line matching `#.*logical_name\s*=\s*"[^"]+"`, in first [[datasets.sources]] block before second [[datasets]]
  - config.example.toml stale_run_threshold: uncommented `^\s*stale_run_threshold\s*=\s*\d[\d.]*` AND `"15m"` appearing nearby (e.g. inline comment)
  - architecture.md heading: `## Multi-machine ingest` (exact, line-start), after `## Backends`, before `## Multi-format extractor layer`
  - architecture.md section body must contain (case-sensitive): `logical_name`, `content_hash`, `socket.gethostname`, `stale_run_threshold`, `mark_stale_runs`
  - README.md: substring `Multi-machine corpus` AND substring `docs/architecture.md#multi-machine-ingest`
- Red output (tail):
  ```
  FAILED TestConfigExampleLogicalName::test_logical_name_appears_in_config_example
  FAILED TestConfigExampleLogicalName::test_logical_name_has_string_value_in_comment
  FAILED TestConfigExampleLogicalName::test_logical_name_is_commented_out
  FAILED TestConfigExampleLogicalName::test_logical_name_appears_before_second_dataset_block
  FAILED TestConfigExampleStaleRunThreshold::test_stale_run_threshold_present
  FAILED TestConfigExampleStaleRunThreshold::test_stale_run_threshold_is_in_scan_block
  FAILED TestConfigExampleStaleRunThreshold::test_stale_run_threshold_has_numeric_value
  FAILED TestConfigExampleStaleRunThreshold::test_stale_run_threshold_references_string_shorthand
  FAILED TestArchitectureDocMultiMachineSection::test_multi_machine_ingest_heading_present
  FAILED TestArchitectureDocMultiMachineSection::test_multi_machine_section_after_backends
  FAILED TestArchitectureDocMultiMachineSection::test_multi_machine_section_before_multi_format
  FAILED TestReadmeMultiMachineLink::test_readme_contains_multi_machine_corpus_prose
  FAILED TestReadmeMultiMachineLink::test_readme_contains_anchor_link_to_architecture
  13 failed, 5 skipped in 0.19s
  ```
- Status: red — handed off to tdd-coder (DR-G7)

## SR-T5 — Concurrent-run advisory lock at the ingest entry point

- Test files: `tests/unit/test_ingest_run_lock.py`
- Run command: `uv run pytest tests/unit/test_ingest_run_lock.py -q`
- Edge case checklist:
  - [x] happy — `test_lock_released_on_normal_exit`, `test_acquired_emits_ingest_run_acquired_event`, `test_released_emits_ingest_run_released_event`
  - [x] boundaries — `test_empty_string_host`, `test_key_is_within_postgres_advisory_range`
  - [x] type/format — `test_unicode_hostname`, `test_returns_int`
  - [x] state — `test_stable_across_calls`, `test_lock_released_on_exception`, `test_contention_does_not_write_to_db`
  - [x] concurrency — `test_wait_true_blocks_until_lock_released_threading` (threading.Thread + barrier)
  - [x] failure paths — `test_second_invocation_exits_75_when_lock_held`, `test_sqlite_contention_exits_75`, `test_exit_code_is_exactly_75_not_1_or_2`, `test_lock_released_on_exception`
  - [ ] N/A — locale/time (no timestamps in this surface)
  - [x] production-realistic — uses `socket.gethostname()` as real hostname, SQLite backend variant
  - [x] regression hooks — `test_exit_code_is_exactly_75_not_1_or_2` pins the exact EX_TEMPFAIL value; `test_derived_from_advisory_lock_key` pins the formula; `test_lock_source_called_before_migrate` pins ordering
- Red output (tail):
  ```
  ERROR tests/unit/test_ingest_run_lock.py
  ImportError while importing test module '.../tests/unit/test_ingest_run_lock.py'.
  tests/unit/test_ingest_run_lock.py:45: in <module>
      from corpus_forge.identity import (
  E   ImportError: cannot import name 'ingest_run_lock_key' from 'corpus_forge.identity'
  1 error in 0.18s
  ```
- Status: red — handed off to tdd-coder

## Phase G — G-05 Integration Smoke

## G-05 — End-to-end integration smoke + skill-contract bump to 14 tools
- Test files:
  - `tests/integration/test_render_register_export_e2e.py` (new — 3 tests)
  - `tests/smoke/test_skill_tool_contract.py` (updated — renamed _ALL_11_TOOLS → _ALL_14_TOOLS, test function names updated)
- Run command: `.venv/bin/python -m pytest tests/integration/test_render_register_export_e2e.py tests/smoke/test_skill_tool_contract.py -v`
- Edge case checklist:
  - [x] happy — `test_append_then_render_with_builtin` (chatml, 3 messages, sentinel check)
  - [x] boundaries — 2-message and 4-message conversations in `test_register_then_export_uses_custom_template`
  - [x] type/format — custom Jinja template (non-builtin name, `{{ messages|length }}`) exercises DB-lookup path
  - [x] state — MCP register then export (two-phase state: register first, then export reads DB)
  - [ ] N/A — concurrency (sequential in-process, no shared mutable state across tests)
  - [x] failure paths — `export_chat` raises `KeyError` for unknown template name (pins that DB resolution is missing)
  - [ ] N/A — locale/time (no date/time fields exercised in these tests)
  - [x] production-realistic — uses real SQLite backend + real MCP server in-process (no mocks)
  - [x] regression hooks — pins the full round-trip; any regression in append/register/render/export chain fails loudly
- Red output (tail):
  ```
  FAILED tests/integration/test_render_register_export_e2e.py::test_register_then_export_uses_custom_template
  FAILED tests/integration/test_render_register_export_e2e.py::test_full_round_trip_append_register_render_export_load_via_datasets

  corpus_forge/templates/__init__.py:54: KeyError
  E   KeyError: "unknown template: 'g05-roles-tmpl'; builtins=['chatml', 'llama3', 'alpaca', 'vicuna', 'gemma', 'qwen']"

  2 failed, 4 passed (3 smoke + 1 integration)
  ```
- Production bug noted: `export.export_chat` calls `templates.render()` which has no DB lookup path.

## Phase H — H-01 Schema test: 0008_feedback_sessions

## H-01 — alembic 0008_feedback_sessions schema (feedback_sessions + feedback_events)
- Test files:
  - `tests/integration/test_alembic_0008_feedback_sessions.py` (new — 4 tests)
  - `tests/integration/test_apply_migrations_uses_alembic.py` (updated — 2 head assertions bumped 0007→0008)
- Run command: `.venv/bin/python -m pytest tests/integration/test_alembic_0008_feedback_sessions.py tests/integration/test_apply_migrations_uses_alembic.py -v`
- Edge case checklist:
  - [x] happy — all 4 schema tests assert full column sets + constraints on a fresh DB after upgrade
  - [x] boundaries — nullable vs NOT NULL asserted per-column; UNIQUE multi-column constraint (client, session_id) verified
  - [x] type/format — PG: bigint / text / timestamp with time zone asserted; SQLite: INTEGER / TEXT / datetime('now') convention
  - [x] state — fresh schema per test (PG: _reset_schema; SQLite: tmp_path fresh db); no cross-test contamination
  - [ ] N/A — concurrency (pure DDL, sequential upgrade, no shared mutable state)
  - [x] failure paths — all 4 tests fail with CommandError (revision missing); head assertions fail (0008 not yet head)
  - [ ] N/A — locale/time (ts DEFAULT NOW() is database-side, no locale-sensitive assertions)
  - [x] production-realistic — column shapes drawn directly from Phase H plan DDL
  - [x] regression hooks — bumped head assertions pin that apply_migrations must reach 0008_feedback_sessions
- Red output (tail):
  ```
  FAILED tests/integration/test_apply_migrations_uses_alembic.py::test_apply_migrations_creates_alembic_version_table_pg
  FAILED tests/integration/test_apply_migrations_uses_alembic.py::test_apply_migrations_creates_alembic_version_table_sqlite
  FAILED tests/integration/test_alembic_0008_feedback_sessions.py::test_feedback_sessions_table_shape_pg
  FAILED tests/integration/test_alembic_0008_feedback_sessions.py::test_feedback_events_table_shape_sqlite
  FAILED tests/integration/test_alembic_0008_feedback_sessions.py::test_feedback_events_table_shape_pg
  FAILED tests/integration/test_alembic_0008_feedback_sessions.py::test_feedback_sessions_table_shape_sqlite

  E  alembic.util.exc.CommandError: Can't locate revision identified by '0008_feedback_sessions'

  6 failed, 1 passed in 2.09s
  ```
- Status: red — handed off to tdd-coder
  The `render_conversation` MCP tool resolves custom templates via `backend.get_chat_template_by_name()`.
  `export_chat` must be updated to pass `backend` into `templates.render` (or accept a `backend` param)

## H-02 — writes.py session-link hook + register_session MCP tool
- Test files:
  - `tests/unit/test_writes_session_link.py` (new — 15 tests)
  - `tests/unit/test_mcp_register_session_dispatch.py` (new — 3 tests)
  - `tests/smoke/test_mcp_writes_disabled_by_default.py` (updated — register_session added to write set, 9→10)
  - `tests/smoke/test_skill_tool_contract.py` (updated — _ALL_14_TOOLS→_ALL_15_TOOLS, test renamed _14→_15)
- Run command: `.venv/bin/python -m pytest tests/unit/test_writes_session_link.py tests/unit/test_mcp_register_session_dispatch.py tests/smoke/test_mcp_writes_disabled_by_default.py tests/smoke/test_skill_tool_contract.py -v`
- Edge case checklist:
  - [x] happy — upsert creates row; add_label with session creates session + event; register_session creates row
  - [x] boundaries — duplicate (client, session_id) → same id; None session_id skips all feedback tables; two writes → 1 session row + 2 event rows
  - [x] type/format — ValueError raised when append_feedback_event called with both audit_id=None + feedback_id=None
  - [x] state — fresh in-memory backend per test; idempotency of upsert verified
  - [ ] N/A — concurrency (pure sequential dispatch functions, no shared mutable state)
  - [x] failure paths — end_feedback_session for unknown session returns False (no raise); no-session skips tables entirely
  - [ ] N/A — locale/time (started_at stored as ISO string; no locale assertions needed)
  - [x] production-realistic — uses real SQLiteBackend (migrated) + real writes.py dispatch functions
  - [x] regression hooks — test_skips_feedback_tables_when_session_id_is_none pins back-compat with F-03 dispatch tests
- Red output (tail):
  ```
  FAILED tests/unit/test_mcp_register_session_dispatch.py::TestRegisterSessionDispatch::test_creates_session_row_and_returns_dict
  FAILED tests/unit/test_mcp_register_session_dispatch.py::TestRegisterSessionDispatch::test_duplicate_returns_existing_id_and_created_false
  FAILED tests/unit/test_mcp_register_session_dispatch.py::TestRegisterSessionDispatch::test_explicit_host_overrides_ctx_host
  FAILED tests/unit/test_writes_session_link.py::TestUpsertFeedbackSession::test_creates_row_on_first_call
  ... (14 more attribute/assertion errors)
  E   AttributeError: 'SQLiteBackend' object has no attribute 'upsert_feedback_session'
  19 failed, 5 passed in 0.82s
  ```
- Status: red — handed off to tdd-coder
  and resolve custom templates before calling render). This is the Coder's fix target.
- Status: red — 2/3 integration tests failing for right reason; handed off to tdd-coder

## H-03 — claude_code source links session to conversation
- Test files:
  - `tests/unit/test_claude_code_session_link.py` (new — 8 tests across 2 classes)
  - `tests/integration/test_claude_code_session_link_e2e.py` (new — 3 tests)
- Run command: `.venv/bin/python -m pytest tests/unit/test_claude_code_session_link.py tests/integration/test_claude_code_session_link_e2e.py -v`
- Edge case checklist:
  - [x] happy — test_sets_conversation_id_and_returns_true; test_happy_path_links_and_returns_true; test_ingest_links_existing_feedback_session
  - [x] boundaries — already-linked row returns False (idempotent, no overwrite); no-row returns False without raising
  - [ ] N/A — type/format (pure integer/string args; no format variation needed)
  - [x] state — fresh in-memory backend per test; idempotency on re-link asserted; feedback_sessions unchanged when no match
  - [ ] N/A — concurrency (sequential ingest path; no shared mutable state)
  - [x] failure paths — no matching row returns False; already linked returns False; different client returns False
  - [ ] N/A — locale/time (timestamps stored as ISO strings, not asserted in these tests)
  - [x] production-realistic — uses real SQLiteBackend (migrated), real ClaudeCodeSource.parse(), real ingest_one; fake .jsonl with realistic JSONL message structure
  - [x] regression hooks — test_ingest_session_without_feedback_session_row_does_not_create_one pins that ingest never writes to feedback_sessions; test_different_client_no_match pins client-scoped isolation
- Red output (tail):
  ```
  collecting ... collected 0 items / 2 errors

  ERROR tests/unit/test_claude_code_session_link.py
  ERROR tests/integration/test_claude_code_session_link_e2e.py

  E   ModuleNotFoundError: No module named 'corpus_forge.sources._session_link'

  2 errors in 0.14s
  ```
- Status: red — handed off to tdd-coder

## Phase B — SQLite Backend

## B-03-fix — Narrow backfill-gating assertion to ignore schema comments
- Test files: `tests/unit/test_sqlite_backend.py` (lines 401-444, `TestBackfillGating::test_no_postgres_backfill_sql_executed`)
- Run command: `PYTHONPATH=. uv run pytest tests/unit/test_sqlite_backend.py -v --no-header 2>&1 | tail -10`
- Option chosen: **Option 1 — strip `--` comments** before checking for forbidden substrings.
  - Reason: Most robust and generalizes — any future inline comment mentioning sha256 (e.g. in 002 or 003) will not cause false positives. Option 3 (regex on exact UPDATE shape) is equally precise but more brittle if the Postgres backfill SQL text ever changes slightly.
- Lines touched: 416-444 in `tests/unit/test_sqlite_backend.py` (replaced 9 lines with 27 lines including the `strip_line_comments` helper and explanatory comment block).
- Root cause: `001_core.sql` line 35 has inline DDL comment `-- sha256 of raw bytes (idempotency key)`. `apply_migrations` passes raw statement text (including that comment) to `backend._execute`. The spy saw "sha256" in the comment and raised a false positive assertion. The fix strips everything from `--` to end-of-line before checking for forbidden patterns.
- Edge case checklist:
  - [x] happy path — migrate() runs 3 schema files; none contain executable SHA256/ENCODE( calls; test passes
  - [x] boundaries — N/A (pure string stripping function applied per line)
  - [x] type/format — N/A (pure function)
  - [x] state — N/A (not a stateful operation)
  - [ ] N/A — concurrency (pure function, no shared state)
  - [x] failure paths — the actual Postgres backfill UPDATE (if it ran for SQLite) would still be caught: `UPDATE corpus.chunks SET content_hash = encode(sha256(text::bytea), 'hex')` has no `--` prefix on the sha256/encode tokens
  - [ ] N/A — locale/time
  - [x] production-realistic — verified against actual 001_core.sql line 35 comment text
  - [x] regression hooks — the 28 other tests in the file remain green; no regression introduced
- Green output (tail):
  ```
  tests/unit/test_sqlite_backend.py::TestBackfillGating::test_apply_migrations_called_with_sqlite_dialect PASSED [ 79%]
  tests/unit/test_sqlite_backend.py::TestBackfillGating::test_content_hash_null_not_backfilled PASSED [ 82%]
  tests/unit/test_sqlite_backend.py::TestBackfillGating::test_no_postgres_backfill_sql_executed PASSED [ 86%]
  tests/unit/test_sqlite_backend.py::TestFailureModes::test_path_is_directory_raises_operational_error PASSED [ 89%]
  tests/unit/test_sqlite_backend.py::TestFailureModes::test_path_is_directory_error_is_raised_on_connection PASSED [ 93%]
  tests/unit/test_sqlite_backend.py::TestFailureModes::test_missing_parent_directory_raises PASSED [ 96%]
  tests/unit/test_sqlite_backend.py::TestFailureModes::test_missing_parent_constructor_does_not_raise PASSED [100%]

  ============================== 29 passed in 0.74s ==============================
  ```
- Status: green — B-03 unblocked; 29/29 tests pass after tester narrowed the backfill-gating assertion

## B-02 — SQLite schema / migration SQL files
- Test files:
  - `tests/unit/test_migration_sqlite_001.py` (55 tests)
  - `tests/unit/test_migration_sqlite_002.py` (22 tests)
  - `tests/unit/test_migration_sqlite_003.py` (33 tests)
  - `tests/unit/test_sqlite_migration_loader.py` (20 tests)
- Run command: `PYTHONPATH=. uv run pytest tests/unit/test_migration_sqlite_001.py tests/unit/test_migration_sqlite_002.py tests/unit/test_migration_sqlite_003.py tests/unit/test_sqlite_migration_loader.py -v --no-header 2>&1 | tail -40`
- Edge case checklist:
  - [x] happy path — file exists at expected path; dialect dispatch routes correctly
  - [x] boundaries — sort key numeric extraction; numeric ordering of 001/002/003; prefix > N checks
  - [x] type/format — no Postgres-only types (BIGSERIAL, JSONB, TIMESTAMPTZ, ::jsonb, CREATE EXTENSION, SET search_path, CREATE SCHEMA, NOW()); SQLite-dialect types asserted (INTEGER PRIMARY KEY AUTOINCREMENT, TEXT for JSON/timestamps)
  - [x] state — IF NOT EXISTS guards on every CREATE TABLE, CREATE INDEX, ALTER TABLE ADD COLUMN (idempotency)
  - [x] N/A — concurrency (DDL file content tests, pure function)
  - [x] failure paths — unknown dialect raises or returns empty (not silently returning Postgres files); missing file causes fixture error (correct red)
  - [x] N/A — locale/time (file content tests)
  - [x] production-realistic — table names, column names, FK names match Postgres source exactly; unqualified table refs (no corpus. prefix); index names preserved
  - [x] regression hooks — postgres dispatch unchanged (no regression); sort-key stable on both dialects
- Red output (tail):
  ```
  FAILED tests/unit/test_migration_sqlite_001.py::TestFileExists::test_file_exists
  FAILED tests/unit/test_migration_sqlite_001.py::TestFileExists::test_sqlite_subdir_exists
  FAILED tests/unit/test_migration_sqlite_001.py::TestMigrationLoaderIntegration::test_glob_discovers_001_core
  FAILED tests/unit/test_migration_sqlite_002.py::TestFileExists::test_file_exists
  FAILED tests/unit/test_migration_sqlite_002.py::TestMigrationLoaderIntegration::test_glob_discovers_file
  FAILED tests/unit/test_migration_sqlite_002.py::TestMigrationLoaderIntegration::test_file_includes_if_not_exists_guards
  FAILED tests/unit/test_migration_sqlite_003.py::TestFileExists::test_file_exists
  FAILED tests/unit/test_sqlite_migration_loader.py::TestGetMigrationFilesSignature::test_accepts_dialect_parameter
  FAILED tests/unit/test_sqlite_migration_loader.py::TestGetMigrationFilesSignature::test_dialect_has_default_of_postgres
  FAILED tests/unit/test_sqlite_migration_loader.py::TestApplyMigrationsSignature::test_accepts_dialect_parameter
  FAILED tests/unit/test_sqlite_migration_loader.py::TestApplyMigrationsSignature::test_dialect_has_default_of_postgres
  =================== 26 failed, 12 passed, 92 errors in 1.24s ===================
  ```
  Primary failure reasons:
  1. `corpus_forge/schema/sqlite/` directory and all 3 SQL files do not exist yet.
  2. `get_migration_files()` and `apply_migrations()` in `migrate.py` have no `dialect` parameter.
  92 errors are fixture-level FileNotFoundError from autouse `sql` fixture on missing SQL files — correct red.
- Plan ambiguities encountered:
  - `test_migration_sqlite_002.py::TestDDLContent::test_statements_count` and `test_migration_sqlite_003.py::TestIdempotencyAndSafety::test_statements_count`: The Postgres 002 uses `corpus.chunks` (schema-qualified). The SQLite version must use bare `chunks`. Tests assert unqualified names — no ambiguity, correct per Q2 decision.
  - `test_migration_sqlite_003.py::TestSQLiteDialect::test_no_bigint_for_pk`: asserting INTEGER PRIMARY KEY AUTOINCREMENT rather than BIGINT. The plan says "BIGSERIAL → INTEGER PRIMARY KEY AUTOINCREMENT" but the non-PK integer columns (revision_number, parent_revision_id) could use BIGINT (SQLite maps it to INTEGER affinity). Tests allow BIGINT for non-PK integer columns.
  - `apply_migrations` postgres dispatch currently uses a backfill pass gated on `"002_chunk_content_hash" in applied`. After the coder adds the dialect param, the backfill pass is Postgres-only and must not run for SQLite. Not tested here (that's B-03 territory); flagged for coder awareness.
- Status: red — handed off to tdd-coder

## B-05 — `upsert_document` + chunk reuse
- Test files: `tests/unit/test_sqlite_backend.py` (appended 8 test classes, 32 tests)
  - `TestUpsertDocumentNewDocument` (5 tests): first insert, document row, chunk rows, content_hash, index sequence
  - `TestUpsertDocumentExistingDocument` (4 tests): content change update, short-circuit, chunk replacement, chunk reduction
  - `TestUpsertDocumentChunkReuse` (2 tests): reuse via content_hash match, insert new when no match
  - `TestUpsertDocumentEmbedderIds` (5 tests): None/empty list no-reuse, triggers copy per chunk, ids passed through
  - `TestCopyReusableEmbeddings` (6 tests): no-prior-match, copy from prior, cache skip, partial reuse subset, no-embedding prior, cache-only query
  - `TestUpsertDocumentSqliteDialect` (2 tests): ON CONFLICT syntax, unique constraint existence
  - `TestUpsertDocumentState` (3 tests): idempotent re-ingest, dirty state multiple upserts, fresh state
  - `TestUpsertDocumentFailurePaths` (5 tests): empty chunks, single chunk, 100 chunks, cross-dataset, null heading
- Run command: `PYTHONPATH=. uv run pytest tests/unit/test_sqlite_backend.py -v -k "TestUpsert or TestCopyReusable" 2>&1 | tail -40`
- Edge case checklist:
  - [x] happy path — new document insert, existing document update, chunk reuse via content_hash
  - [x] boundaries — empty chunks list, single chunk, 100 chunks, null heading
  - [x] type/format — embedder_ids None/empty/non-empty; content_hash comparison
  - [x] state — idempotent re-ingest (no duplicate chunks), dirty state (sequential upserts), fresh state (no prior chunks)
  - [ ] N/A — concurrency (single-connection per _execute call; B-08 covers BEGIN IMMEDIATE locking)
  - [x] failure paths — empty chunks, large chunk count (100), null heading preservation
  - [ ] N/A — locale/time (no locale/time dependencies in upsert logic)
  - [x] production-realistic — multiple dataset_ids with same source_uri, cross-dataset isolation
  - [x] regression hooks — `test_idempotent_upsert_no_duplicate_chunks` pins no-duplicate-chunk contract; `test_different_dataset_same_source_uri` pins cross-dataset isolation
- Current output (tail):
```
PASSED tests/unit/test_sqlite_backend.py::TestUpsertDocumentNewDocument::test_inserts_document_row
PASSED tests/unit/test_sqlite_backend.py::TestUpsertDocumentNewDocument::test_inserts_new_chunks
PASSED tests/unit/test_sqlite_backend.py::TestUpsertDocumentNewDocument::test_chunk_content_hash_set_on_insert
PASSED tests/unit/test_sqlite_backend.py::TestUpsertDocumentNewDocument::test_chunk_index_sequence_starts_at_zero
PASSED tests/unit/test_sqlite_backend.py::TestUpsertDocumentExistingDocument::test_content_hash_short_circuit_returns_existing_id
PASSED tests/unit/test_sqlite_backend.py::TestUpsertDocumentExistingDocument::test_replaces_chunks_on_content_change
PASSED tests/unit/test_sqlite_backend.py::TestUpsertDocumentExistingDocument::test_reduces_chunk_count_on_content_change
PASSED tests/unit/test_sqlite_backend.py::TestUpsertDocumentChunkReuse::test_reuses_chunk_when_content_hash_matches
PASSED tests/unit/test_sqlite_backend.py::TestUpsertDocumentChunkReuse::test_inserts_new_chunk_when_no_prior_match
PASSED tests/unit/test_sqlite_backend.py::TestUpsertDocumentEmbedderIds::test_embedder_ids_none_no_reuse
PASSED tests/unit/test_sqlite_backend.py::TestUpsertDocumentEmbedderIds::test_embedder_ids_empty_list_no_reuse
PASSED tests/unit/test_sqlite_backend.py::TestUpsertDocumentEmbedderIds::test_embedder_ids_triggers_copy_per_chunk
PASSED tests/unit/test_sqlite_backend.py::TestUpsertDocumentEmbedderIds::test_embedder_ids_passed_to_copy_reusable
PASSED tests/unit/test_sqlite_backend.py::TestCopyReusableEmbeddings::test_returns_empty_set_when_no_prior_chunk_shares_hash
PASSED tests/unit/test_sqlite_backend.py::TestCopyReusableEmbeddings::test_copies_vector_from_prior_chunk_when_hash_matches
PASSED tests/unit/test_sqlite_backend.py::TestCopyReusableEmbeddings::test_cache_prevents_repeat_select_for_hash
PASSED tests/unit/test_sqlite_backend.py::TestCopyReusableEmbeddings::test_returns_reused_embedder_ids_subset
PASSED tests/unit/test_sqlite_backend.py::TestCopyReusableEmbeddings::test_no_prior_chunk_with_embedding_returns_empty
PASSED tests/unit/test_sqlite_backend.py::TestCopyReusableEmbeddings::test_cache_entry_used_directly_without_query
PASSED tests/unit/test_sqlite_backend.py::TestCopyReusableEmbeddings::test_multiple_chunks_share_same_prior
PASSED tests/unit/test_sqlite_backend.py::TestUpsertDocumentSqliteDialect::test_on_conflict_syntax_used_for_document_upsert
PASSED tests/unit/test_sqlite_backend.py::TestUpsertDocumentSqliteDialect::test_duplicate_source_uri_raises_or_updates
PASSED tests/unit/test_sqlite_backend.py::TestUpsertDocumentState::test_idempotent_upsert_no_duplicate_chunks
PASSED tests/unit/test_sqlite_backend.py::TestUpsertDocumentState::test_dirty_state_multiple_upserts
PASSED tests/unit/test_sqlite_backend.py::TestUpsertDocumentState::test_fresh_state_no_prior_chunks
PASSED tests/unit/test_sqlite_backend.py::TestUpsertDocumentFailurePaths::test_empty_chunks_list_inserts_no_chunk_rows
PASSED tests/unit/test_sqlite_backend.py::TestUpsertDocumentFailurePaths::test_single_chunk_boundary
PASSED tests/unit/test_sqlite_backend.py::TestUpsertDocumentFailurePaths::test_large_number_of_chunks
PASSED tests/unit/test_sqlite_backend.py::TestUpsertDocumentFailurePaths::test_different_dataset_same_source_uri
PASSED tests/unit/test_sqlite_backend.py::TestUpsertDocumentFailurePaths::test_null_heading_preserved
FAILED tests/unit/test_sqlite_backend.py::TestUpsertDocumentNewDocument::test_returns_document_id_on_first_insert
FAILED tests/unit/test_sqlite_backend.py::TestUpsertDocumentExistingDocument::test_updates_document_when_content_hash_differs
FAILED tests/unit/test_sqlite_backend.py::TestUpsertDocumentChunkReuse::test_reuses_chunk_when_content_hash_matches
FAILED tests/unit/test_sqlite_backend.py::TestUpsertDocumentEmbedderIds::test_embedder_ids_triggers_copy_per_chunk
FAILED tests/unit/test_sqlite_backend.py::TestUpsertDocumentEmbedderIds::test_embedder_ids_passed_to_copy_reusable
FAILED tests/unit/test_sqlite_backend.py::TestCopyReusableEmbeddings::test_returns_reused_embedder_ids_subset
FAILED tests/unit/test_sqlite_backend.py::TestCopyReusableEmbeddings::test_no_prior_chunk_with_embedding_returns_empty
FAILED tests/unit/test_sqlite_backend.py::TestCopyReusableEmbeddings::test_multiple_chunks_share_same_prior
FAILED tests/unit/test_sqlite_backend.py::TestUpsertDocumentSqliteDialect::test_duplicate_source_uri_raises_or_updates
FAILED tests/unit/test_sqlite_backend.py::TestUpsertDocumentState::test_dirty_state_multiple_upserts
============================== 23 passed, 9 failed, 1 passed in 1.80s ==============================
```
   23/32 B-05 tests pass. 9 remaining failures are all tester-side bugs: (1) 6 tests use `row["count"]` for `COUNT(*)` queries (column name is `COUNT(*)` not `count`); (2) `test_different_dataset_same_source_uri` — helper inserts hardcoded dataset name "test_ds" causing UNIQUE conflict on second call, FK fails; (3) `test_copies_vector_from_prior_chunk_when_hash_matches` — embedding `chunk_id=5` doesn't match auto-incremented chunk `id=1`; (4) `test_multiple_chips_share_same_prior` — filter `chunk_id >= 100` doesn't match actual chunk ids; (5) `test_returns_document_id_on_first_insert` — chunk arg is `([tuple],)` tuple-of-list not list-of-tuples. All 9 require tester fixes.
   The 1 passed test (`test_unique_constraint_on_documents_exists`) validates the pre-existing schema's UNIQUE constraint — correct green.
   All 48 pre-existing tests (B-03 + B-04) still pass.
- Status: red — handed off to tdd-coder


## B-04 — `register_embedder` + per-embedder vector table
- Test files: `tests/unit/test_sqlite_backend.py` (appended `TestRegisterEmbedder` class, lines ~490–800)
- Run command: `PYTHONPATH=. uv run pytest tests/unit/test_sqlite_backend.py -v --no-header 2>&1 | tail -40`
- Edge case checklist:
  - [x] happy path — register fresh embedder → int id returned; row in embedders table; per-embedder table created
  - [x] boundaries — register with ':memory:' backend; register 4 distinct embedders each get own table and row
  - [x] type/format — return type is `int` (not str, not None); dimension and model_id stored correctly
  - [x] state — idempotency: same embedder registered twice → same id, single row, no error; UPDATE-on-collision: same name + different dimension/model_id updates existing row
  - [ ] N/A — concurrency (single-connection per _execute call; concurrent safety tested in B-08)
  - [x] failure paths — fallback path: monkeypatched SQLITE_VEC_AVAILABLE=False → plain BLOB table created with correct columns and BLOB type for embedding column
  - [ ] N/A — locale/time (no timestamp assertions in this task; timestamps use SQLite defaults)
  - [x] production-realistic — FakeEmbedder mirrors full Embedder protocol (name, provider, model_id, dimension, normalized, distance, active); embedder names matching real naming patterns (alpha_embedder, bert_base, etc.)
  - [x] regression hooks — `test_register_twice_single_row` pins the UNIQUE constraint + INSERT-or-UPDATE contract so a naive double-INSERT would be caught
- Red output (tail):
  ```
  FAILED tests/unit/test_sqlite_backend.py::TestRegisterEmbedder::test_register_returns_integer_id
  FAILED tests/unit/test_sqlite_backend.py::TestRegisterEmbedder::test_register_inserts_embedders_row
  FAILED tests/unit/test_sqlite_backend.py::TestRegisterEmbedder::test_register_sets_table_name_column
  FAILED tests/unit/test_sqlite_backend.py::TestRegisterEmbedder::test_register_creates_per_embedder_table
  FAILED tests/unit/test_sqlite_backend.py::TestRegisterEmbedder::test_register_in_memory_returns_id
  FAILED tests/unit/test_sqlite_backend.py::TestRegisterEmbedder::test_register_twice_same_id
  FAILED tests/unit/test_sqlite_backend.py::TestRegisterEmbedder::test_register_twice_single_row
  FAILED tests/unit/test_sqlite_backend.py::TestRegisterEmbedder::test_register_twice_no_duplicate_table
  FAILED tests/unit/test_sqlite_backend.py::TestRegisterEmbedder::test_update_on_same_name_different_dimension
  FAILED tests/unit/test_sqlite_backend.py::TestRegisterEmbedder::test_update_on_same_name_different_model_id
  FAILED tests/unit/test_sqlite_backend.py::TestRegisterEmbedder::test_two_distinct_embedders_get_distinct_ids
  FAILED tests/unit/test_sqlite_backend.py::TestRegisterEmbedder::test_vec0_virtual_table_has_required_columns
  FAILED tests/unit/test_sqlite_backend.py::TestRegisterEmbedder::test_fallback_blob_table_created_when_vec_unavailable
  FAILED tests/unit/test_sqlite_backend.py::TestRegisterEmbedder::test_fallback_embedding_column_is_blob
  FAILED tests/unit/test_sqlite_backend.py::TestRegisterEmbedder::test_returned_id_matches_embedders_row_id
  FAILED tests/unit/test_sqlite_backend.py::TestRegisterEmbedder::test_returned_id_is_positive_integer
  FAILED tests/unit/test_sqlite_backend.py::TestRegisterEmbedder::test_multiple_embedders_each_get_own_table
  FAILED tests/unit/test_sqlite_backend.py::TestRegisterEmbedder::test_multiple_embedders_rows_all_present
  18 failed, 848 passed, 8 skipped, 1 warning in 6.73s
  ```
  All 18 B-04 tests fail with: `AttributeError: 'SQLiteBackend' object has no attribute 'register_embedder'`
  All 848 pre-existing tests (including 29 B-03 tests) still pass.
- Lint: `uv run ruff check tests/unit/test_sqlite_backend.py` — clean (All checks passed)
- Format: `uv run ruff format --check tests/unit/test_sqlite_backend.py` — clean (1 file already formatted)
- Status: red — handed off to tdd-coder


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
ImportError while importing test module 'tests/unit/test_sync_echo.py'.
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
ImportError while importing test module 'tests/unit/test_sync_cloud.py'.
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


## P0-08 — E2E chunk-embedding reuse (characterization test)
- Test files: `tests/integration/test_chunk_reuse_e2e.py`
- Run command: `PYTHONPATH=. uv run pytest tests/integration/test_chunk_reuse_e2e.py -v --no-header 2>&1 | tail -30`
- Edge case checklist:
  - [x] happy path — first ingest of 12-section doc produces >=10 embedded chunks
  - [x] reuse threshold — >=7 embeddings reused after small append (contractual; >=70%)
  - [x] encoder spy — second pass encodes <=3 new texts
  - [x] append-single-chunk — appending one section creates <=2 new tail chunks
  - [x] fake-embedder isolation — no real model loaded; deterministic stable vectors
  - [x] idempotent reingest — re-ingesting identical doc encodes 0 new texts (short-circuit path)
  - [x] FakeEmbedder determinism — same text always maps to same vector
  - [x] FakeEmbedder non-zero — vectors are never all-zero
  - [x] FakeEmbedder distinctness — different texts produce different vectors
  - [x] FakeEmbedder spy attrs — call_count and call_args_list work correctly
  - [x] chunk count prediction — sanity test verifying test doc yields >=10 chunks
  - [ ] N/A — concurrency (single-threaded ingest; advisory lock tested elsewhere)
  - [ ] N/A — locale/time (UTF-8 text, no locale-sensitive operations)
  - [ ] N/A — failure paths (network/disk errors deferred; this is a happy-path pin)
- Red output (tail):
  ```
  FAILED tests/integration/test_chunk_reuse_e2e.py::TestChunkReuseE2E::test_chunk_reuse_e2e
  FAILED tests/integration/test_chunk_reuse_e2e.py::TestChunkReuseE2E::test_reuse_skips_encode_for_identical_reingest
  =================== 2 failed, 5 passed, 1 warning in 10.30s ====================

  test_chunk_reuse_e2e:
    AssertionError: Expected <=3 texts encoded on second pass, got 13.
    The reuse path should have copied embeddings for unchanged chunks.

  test_reuse_skips_encode_for_identical_reingest:
    psycopg.errors.NotNullViolation: null value in column "embedder_id" of relation
    "embeddings_fake_embedder" violates not-null constraint
  ```
- Status: red — 2 failures surface real production bugs (see notes)
- Production bug suspicions (do NOT paper over with test changes):
  1. **BUG-P0-08-A** `_copy_reusable_embeddings` INSERT missing `embedder_id`:
     `INSERT INTO {table} (chunk_id, embedding) SELECT %s, embedding FROM {table} WHERE chunk_id = %s`
     The embedding table DDL has `embedder_id BIGINT NOT NULL`.
     The INSERT must include `embedder_id` (can SELECT it from the source row).
     Fix: `INSERT INTO {table} (chunk_id, embedder_id, embedding) SELECT %s, embedder_id, embedding FROM {table} WHERE chunk_id = %s`.
  2. **BUG-P0-08-B** `upsert_document` deletes all chunks before calling `_copy_reusable_embeddings`:
     `DELETE FROM corpus.chunks WHERE document_id = %s` cascades to delete all embedding rows.
     Then for each re-inserted chunk, `_copy_reusable_embeddings` searches for a prior chunk with the
     same `content_hash` that has an embedding — but the embeddings were just deleted. Result: reuse
     never works for a re-ingest of the same document; it only works cross-document (same content_hash
     in a different document already in the DB).
     Fix options: (a) collect prior chunk_ids (by content_hash) BEFORE deleting; (b) soft-delete
     chunks and cascade; (c) upsert chunks instead of delete-then-reinsert.
  - Both bugs must be fixed by tdd-coder before these tests can go green.


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

---

## B-01 — sqlite-vec optional dep + import-guarded loader
- Test files: `tests/unit/test_sqlite_vec_loader.py`, `tests/unit/test_phase_b_pyproject.py`
- Run command: `PYTHONPATH=. uv run pytest tests/unit/test_sqlite_vec_loader.py tests/unit/test_phase_b_pyproject.py -v`
- Edge case checklist:
  - [x] happy path — `load_sqlite_vec` on `:memory:` + `SELECT vec_version()` (skipif no sqlite-vec)
  - [x] boundaries — N/A (no numeric boundaries; the loader is a single-call extension loader)
  - [x] type/format — `SQLITE_VEC_AVAILABLE` is exactly `bool`, not truthy int or None
  - [x] state — idempotent second call (re-enables extension each time); import-guard survives module reload
  - [x] concurrency — N/A (pure function, no shared state)
  - [x] failure paths — `enable_load_extension` raises `AttributeError`; raises `OperationalError`; no silent no-op
  - [x] locale/time — N/A (no locale/time involvement)
  - [x] production-realistic — uses real `sqlite3.connect(":memory:")` for skipif tests
  - [x] regression — import-guard: module must survive absent sqlite_vec; SQLITE_VEC_AVAILABLE must be False
  - [x] pyproject pin — `sqlite` extra present, `sqlite-vec>=0.1` present, PEP 508 valid
- Red output (tail):
  ```
  FAILED tests/unit/test_sqlite_vec_loader.py::TestImportGuard::test_module_importable_without_sqlite_vec
  FAILED tests/unit/test_sqlite_vec_loader.py::TestImportGuard::test_load_sqlite_vec_importable_without_sqlite_vec
  FAILED tests/unit/test_phase_b_pyproject.py::TestSqliteExtra::test_sqlite_extra_key_exists
  FAILED tests/unit/test_phase_b_pyproject.py::TestSqliteExtra::test_sqlite_extra_is_a_list
  FAILED tests/unit/test_phase_b_pyproject.py::TestSqliteExtra::test_sqlite_extra_is_nonempty
  FAILED tests/unit/test_phase_b_pyproject.py::TestSqliteExtra::test_sqlite_extra_contains_sqlite_vec
  FAILED tests/unit/test_phase_b_pyproject.py::TestSqliteExtra::test_sqlite_vec_version_pin_gte_0_1
  FAILED tests/unit/test_phase_b_pyproject.py::TestSqliteExtra::test_sqlite_vec_entry_is_valid_pep508
  ==================== 8 failed, 4 passed, 9 skipped in 0.15s ====================
  ```
- Notes: 9 tests are correctly SKIPped — they guard behavior that requires either the loader module (coder task) or the sqlite-vec library to be installed. The 4 PASSing tests pin pre-existing pyproject.toml structure that the coder must not break.
- Status: red — handed off to tdd-coder

## B-03
- Test files: `tests/unit/test_sqlite_backend.py`
- Run command: `PYTHONPATH=. uv run pytest tests/unit/test_sqlite_backend.py -v --no-header`
- Test count: 29
- Edge case checklist:
  - [x] happy path — str path, pathlib.Path, :memory:, migrate() completes
  - [x] boundaries — N/A (no numeric/size boundaries; path variants cover boundary cases)
  - [x] type/format — str vs Path vs special ":memory:" string all accepted
  - [x] state — lazy construction (no file created before connection), idempotent migrate()
  - [x] concurrency — N/A (single-connection per _get_connection(); B-08 covers locking)
  - [x] failure paths — directory path raises OperationalError, missing parent raises OperationalError
  - [x] locale/time — N/A (no locale/time involvement in __init__/migrate)
  - [x] production-realistic — tmp_path fixture for file-backed DBs; :memory: for ephemeral
  - [x] regression — 002 backfill gating (dialect='sqlite' must not run Postgres sha256 UPDATE)
- Red output (tail):
  ```
  ERROR tests/unit/test_sqlite_backend.py
  ImportError while importing test module '...tests/unit/test_sqlite_backend.py'.
  tests/unit/test_sqlite_backend.py:25: in <module>
      from corpus_forge.backends.sqlite import SQLiteBackend
  E   ModuleNotFoundError: No module named 'corpus_forge.backends.sqlite'
  !!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
  1 error in 0.18s
  ```
- lint/format: clean (ruff check + ruff format --check both pass)
- Notes: No disagreements between sqlite_backend.md and this spec. The plan (§B-03) says `path: str` but accepting `str | Path` is strictly more compatible and is consistent with the acceptance spec. The `_tables()` helper queries the raw sqlite3 file so tests are independent of the backend's row_factory. The `test_get_connection_returns_fresh_connection_each_call` test was restructured to use sequential (not nested) context managers to satisfy SIM117.
- Status: red — handed off to tdd-coder

## B-06 — `upsert_conversation`
- Test files: `tests/unit/test_sqlite_backend.py` (appended 5 test classes, 21 tests)
  - `TestUpsertConversationNew` (5 tests): returns int id, conversations row written, messages
    written, chunks written, in-memory backend works
  - `TestUpsertConversationExisting` (4 tests): same hash returns same id, same hash no-op
    preserves messages, different hash updates conversation row, different hash replaces messages
  - `TestUpsertConversationMessages` (6 tests): turn_index zero-based sequential, role preserved,
    tool_calls as JSON TEXT, tool_results as JSON TEXT, ts column non-null, null tool_calls NULL
  - `TestUpsertConversationChunks` (4 tests): conversation_id set, document_id NULL (XOR),
    message_id set, chunk_index per-message starts at zero
  - `TestUpsertConversationFailurePaths` (2 tests): invalid dataset_id FK raises IntegrityError,
    source_uri=None NOT NULL raises IntegrityError
- Run command: `PYTHONPATH=. uv run pytest tests/unit/test_sqlite_backend.py -v --no-header -k "TestUpsertConversation" 2>&1 | tail -35`
- Edge case checklist:
  - [x] happy path — fresh insert returns int id; conversations/messages/chunks rows written
  - [x] boundaries — in-memory backend; 4-message turn_index sequence; 3-chunk-per-message
  - [x] type/format — tool_calls/tool_results as JSON TEXT with round-trip check; ts as non-NULL
    TEXT; None tool_calls stored as NULL; role strings preserved verbatim
  - [x] state — same-hash no-op (idempotency); different-hash UPDATE replaces messages;
    messages replaced not duplicated on update
  - [ ] N/A — concurrency (single-connection per _execute; B-08 covers BEGIN IMMEDIATE locking)
  - [x] failure paths — invalid dataset_id FK violation → IntegrityError; source_uri=None NOT
    NULL violation → IntegrityError
  - [ ] N/A — locale/time (ts stored as ISO TEXT; started_at/ended_at translation tested
    implicitly via ts column check; deep format assertions deferred to B-09)
  - [x] production-realistic — RawConversation/RawMessage from real dataclasses; tool_calls
    payload mirrors real Claude tool-use format; multi-role conversation (system/user/assistant)
  - [x] regression hooks — `test_same_hash_no_op_preserves_messages` pins the no-duplicate
    contract; `test_different_hash_replaces_messages` pins the old-msgs-gone contract;
    `test_chunks_have_document_id_null` pins XOR invariant
- B-05 tester-bug note (observed, not fixed): B-05 test classes use `row["count"]` for
  `COUNT(*)` queries. The actual column name from `sqlite3.Row` is `COUNT(*)`, not `count`.
  This is one of the 9 documented tester-side bugs in B-05. B-06 avoids this pattern by using
  direct `len()` on result lists rather than `COUNT(*)` aliases.
- Red output (tail):
  ```
  FAILED tests/unit/test_sqlite_backend.py::TestUpsertConversationChunks::test_chunk_index_per_message_starts_at_zero
  FAILED tests/unit/test_sqlite_backend.py::TestUpsertConversationFailurePaths::test_invalid_dataset_id_raises_integrity_error
  FAILED tests/unit/test_sqlite_backend.py::TestUpsertConversationFailurePaths::test_source_uri_none_raises_integrity_error
  ====================== 21 failed, 80 deselected in 1.10s ======================
  All 21 B-06 tests fail with:
  AttributeError: 'SQLiteBackend' object has no attribute 'upsert_conversation'
  The 70 pre-existing tests (B-03 + B-04 + passing B-05) are unaffected.
  ```
- lint/format: `uv run ruff check` clean; `uv run ruff format --check` clean (moved
  `import json` and `RawConversation/RawMessage` to top-level module import block).
- Cross-link: B-06 row in `.planning/tdd/sqlite_backend.md`.
- Status: red — handed off to tdd-coder

## B-07 — `write_embeddings` + `chunks_missing_embedding`
- Test files: `tests/unit/test_sqlite_backend.py` (appended 2 test classes, 17 tests)
  - `TestWriteEmbeddings` (8 tests): single pair row exists; multiple pairs; empty pairs noop;
    float64 input accepted; duplicate chunk_id idempotent (INSERT OR REPLACE); fallback BLOB
    stores bytes and round-trips correctly; fallback BLOB duplicate idempotent;
    sqlite-vec path row retrievable (skipif no vec)
  - `TestChunksMissingEmbedding` (9 tests): no chunks empty; all covered empty; missing
    chunks returned; only missing (not covered) returned; returns (int, str) 2-tuples with
    correct values; limit caps results; default limit returns all when few; multiple embedders
    are independent (A covered, B missing); unknown embedder_id returns empty (not raise)
- Run command: `PYTHONPATH=. uv run pytest tests/unit/test_sqlite_backend.py -v --no-header 2>&1 | tail -40`
- Helper functions added:
  - `_insert_dataset_for_embedding(backend, dataset_id)` — inserts a datasets row
  - `_insert_doc_and_chunk(backend, dataset_id, source_uri, chunk_text)` — inserts a document
    + one chunk, returns (doc_id, chunk_id)
- Edge case checklist:
  - [x] happy path — single write; all-missing returns correct set; covered not returned
  - [x] boundaries — empty pairs list (noop); limit=3 on 5 chunks; default limit on 10
  - [x] type/format — float64 input accepted; BLOB round-trip verified via np.frombuffer;
    return type is (int, str) 2-tuple
  - [x] state — idempotency on duplicate chunk_id for both vec0 and fallback BLOB paths
  - [ ] N/A — concurrency (B-08 covers locking)
  - [x] failure paths — unknown embedder_id returns empty (mirrors postgres.py early return)
  - [ ] N/A — locale/time (no time surface in these methods)
  - [x] production-realistic — numpy float32 arrays; multi-embedder independence test
  - [x] regression hooks — test_fallback_blob_stores_bytes pins byte-level serialization;
    test_multiple_embedders_are_independent pins per-embedder isolation invariant
- Red output (tail):
  ```
  FAILED tests/unit/test_sqlite_backend.py::TestWriteEmbeddings::test_single_pair_row_exists
  FAILED tests/unit/test_sqlite_backend.py::TestWriteEmbeddings::test_multiple_pairs_multiple_rows
  FAILED tests/unit/test_sqlite_backend.py::TestWriteEmbeddings::test_empty_pairs_is_noop
  FAILED tests/unit/test_sqlite_backend.py::TestWriteEmbeddings::test_float64_input_accepted_and_stored
  FAILED tests/unit/test_sqlite_backend.py::TestWriteEmbeddings::test_duplicate_chunk_id_is_idempotent
  FAILED tests/unit/test_sqlite_backend.py::TestWriteEmbeddings::test_fallback_blob_stores_bytes
  FAILED tests/unit/test_sqlite_backend.py::TestWriteEmbeddings::test_fallback_blob_duplicate_idempotent
  FAILED tests/unit/test_sqlite_backend.py::TestWriteEmbeddings::test_vec_path_row_retrievable
  FAILED tests/unit/test_sqlite_backend.py::TestChunksMissingEmbedding::test_no_chunks_returns_empty
  FAILED tests/unit/test_sqlite_backend.py::TestChunksMissingEmbedding::test_all_chunks_have_embeddings_returns_empty
  FAILED tests/unit/test_sqlite_backend.py::TestChunksMissingEmbedding::test_missing_chunks_are_returned
  FAILED tests/unit/test_sqlite_backend.py::TestChunksMissingEmbedding::test_only_missing_chunks_returned_not_covered
  FAILED tests/unit/test_sqlite_backend.py::TestChunksMissingEmbedding::test_returns_tuple_of_chunk_id_and_text
  FAILED tests/unit/test_sqlite_backend.py::TestChunksMissingEmbedding::test_limit_caps_number_of_results
  FAILED tests/unit/test_sqlite_backend.py::TestChunksMissingEmbedding::test_default_limit_returns_all_when_few
  FAILED tests/unit/test_sqlite_backend.py::TestChunksMissingEmbedding::test_multiple_embedders_are_independent
  FAILED tests/unit/test_sqlite_backend.py::TestChunksMissingEmbedding::test_unknown_embedder_id_returns_empty
  17 failed, 101 passed in 4.46s
  All 17 B-07 tests fail with AttributeError on write_embeddings or chunks_missing_embedding.
  101 existing tests pass (no regression).
  ```
- lint/format: `uv run ruff check` clean; `uv run ruff format --check` clean.
- Status: red — handed off to tdd-coder

## B-08 — `lock_source(key: str)` context manager
- Test files: `tests/unit/test_sqlite_backend.py` (appended 2 classes, 12 tests total)
  - `TestLockSource` (8 tests): happy path execute, any-string key accepted, commit on clean exit,
    write visible after exit, rollback on exception, exception re-raised, different keys serialize
    globally, context manager protocol returned.
  - `TestLockSourceConcurrency` (4 tests): two threads serialize with no data loss, timeout raises
    OperationalError after threshold, lock released immediately after context exit, different keys
    still serialize (global write-lock, not per-key).
- Run command: `PYTHONPATH=. uv run pytest tests/unit/test_sqlite_backend.py::TestLockSource tests/unit/test_sqlite_backend.py::TestLockSourceConcurrency -v --no-header 2>&1 | tail -40`
- Edge case checklist:
  - [x] happy path — block executes, any string key accepted, clean exit commits write
  - [x] boundaries — empty key, unicode key, 256-char key, all accepted without error
  - [x] type/format — return value implements context manager protocol (__enter__/__exit__)
  - [x] state — commit on clean exit persists; rollback on exception reverts; lock released after exit
  - [x] concurrency — two threads serialize (no data loss); timeout raises OperationalError;
        different keys still use global write-lock (no per-key granularity)
  - [x] failure paths — exception inside block triggers ROLLBACK and is re-raised;
        lock_timeout_s=0.3 raises OperationalError when lock is held by another thread
  - [x] N/A — locale/time (no locale-sensitive paths in lock acquisition logic)
  - [x] production-realistic — tests use file-backed DBs (tmp_path) to exercise real cross-connection
        contention; concurrency tests use threading.Event handshakes (no race-prone bare sleeps)
  - [x] regression hooks — `test_lock_released_after_context_exit` pins that BEGIN IMMEDIATE is
        committed/rolled back (not left dangling); `test_exception_inside_lock_rolls_back` pins
        the ROLLBACK-on-exception contract
- Red output (tail):
  ```
  FAILED tests/unit/test_sqlite_backend.py::TestLockSource::test_context_manager_executes_block
  FAILED tests/unit/test_sqlite_backend.py::TestLockSource::test_lock_source_accepts_any_string_key
  FAILED tests/unit/test_sqlite_backend.py::TestLockSource::test_write_inside_lock_is_committed
  FAILED tests/unit/test_sqlite_backend.py::TestLockSource::test_write_inside_lock_visible_after_exit
  FAILED tests/unit/test_sqlite_backend.py::TestLockSource::test_exception_inside_lock_rolls_back
  FAILED tests/unit/test_sqlite_backend.py::TestLockSource::test_exception_is_re_raised
  FAILED tests/unit/test_sqlite_backend.py::TestLockSource::test_different_keys_serialize_globally
  FAILED tests/unit/test_sqlite_backend.py::TestLockSource::test_returns_context_manager_protocol
  FAILED tests/unit/test_sqlite_backend.py::TestLockSourceConcurrency::test_two_threads_serialize_no_data_loss
  FAILED tests/unit/test_sqlite_backend.py::TestLockSourceConcurrency::test_timeout_raises_operational_error
  FAILED tests/unit/test_sqlite_backend.py::TestLockSourceConcurrency::test_lock_released_after_context_exit
  FAILED tests/unit/test_sqlite_backend.py::TestLockSourceConcurrency::test_different_keys_still_serialize
  12 failed, 118 passed in 14.70s
  All 12 B-08 tests fail with AttributeError: 'SQLiteBackend' object has no attribute 'lock_source'.
  118 existing tests pass (no regression).
  ```
- lint: `uv run ruff check tests/unit/test_sqlite_backend.py` — All checks passed.
- format: `uv run ruff format --check tests/unit/test_sqlite_backend.py` — 1 file already formatted.
- Notes:
  - `lock_timeout_s` must be an accepted keyword argument of `lock_source` for the timeout test.
    The default should be 30.0 s per the board spec; the concurrency test overrides it to 0.3 s.
  - Re-entrancy (nested lock_source from the same connection) is NOT tested — SQLite's BEGIN
    IMMEDIATE would deadlock on the same connection. Documented in class-level comment only.
  - The `test_exception_is_re_raised` test uses `with pytest.raises(...), backend.lock_source(...):`
    combined syntax (SIM117 compliance); the lock_source context manager must propagate the
    exception from the body, not suppress it.

## B-09
- Test files: `tests/unit/test_sqlite_backend.py` (appended: TestDeleteDocument,
  TestDeleteConversation, TestFindDocument, TestResolveDocument, TestResolveSelfSource)
- Run command:
  `PYTHONPATH=. uv run pytest tests/unit/test_sqlite_backend.py -v --no-header 2>&1 | tail -30`
- Edge case checklist:
  - [x] happy — all five methods have a basic "does it work" case
  - [x] boundaries — empty source_uri for resolve_document returns None;
        delete of non-existent row is a no-op
  - [x] type / format — N/A (pure SQL; no format parsing in these methods)
  - [x] state — idempotency on double-call (resolve_document, resolve_self_source);
        dirty state (delete with chunks/messages present)
  - [x] concurrency — N/A (single-threaded methods; lock_source concurrency covered in B-08)
  - [x] failure paths — delete non-existent doc/conv is no-op (no error); find missing returns None
  - [x] locale / time — N/A (no timestamps in B-09 surface)
  - [x] production-realistic data — uses real source_uri patterns (vault://, claude://)
        and dataset isolation patterns matching the rest of the suite
  - [x] regression hooks — test_delete_only_affects_matching_dataset pins the
        dataset isolation invariant; test_uses_sync_plugin_and_pull_identity pins
        the exact plugin/identity values used by resolve_self_source (Postgres parity)
- Red output (tail):
  ```
  FAILED tests/unit/test_sqlite_backend.py::TestDeleteDocument::test_delete_removes_document_row
  FAILED tests/unit/test_sqlite_backend.py::TestDeleteDocument::test_delete_cascades_to_chunks
  FAILED tests/unit/test_sqlite_backend.py::TestDeleteDocument::test_delete_nonexistent_is_noop
  FAILED tests/unit/test_sqlite_backend.py::TestDeleteDocument::test_delete_only_affects_matching_dataset
  FAILED tests/unit/test_sqlite_backend.py::TestDeleteConversation::test_delete_removes_conversation_row
  FAILED tests/unit/test_sqlite_backend.py::TestDeleteConversation::test_delete_cascades_messages_and_chunks
  FAILED tests/unit/test_sqlite_backend.py::TestDeleteConversation::test_delete_nonexistent_conversation_is_noop
  FAILED tests/unit/test_sqlite_backend.py::TestFindDocument::test_returns_dict_for_existing_document
  FAILED tests/unit/test_sqlite_backend.py::TestFindDocument::test_returns_none_for_missing_document
  FAILED tests/unit/test_sqlite_backend.py::TestFindDocument::test_wrong_dataset_id_returns_none
  FAILED tests/unit/test_sqlite_backend.py::TestFindDocument::test_is_non_mutating
  FAILED tests/unit/test_sqlite_backend.py::TestResolveDocument::test_creates_stub_for_missing_document
  FAILED tests/unit/test_sqlite_backend.py::TestResolveDocument::test_new_stub_has_empty_content_hash
  FAILED tests/unit/test_sqlite_backend.py::TestResolveDocument::test_returns_existing_row_without_duplicate
  FAILED tests/unit/test_sqlite_backend.py::TestResolveDocument::test_idempotent_same_id_on_double_call
  FAILED tests/unit/test_sqlite_backend.py::TestResolveDocument::test_returns_none_for_empty_source_uri
  FAILED tests/unit/test_sqlite_backend.py::TestResolveDocument::test_isolated_by_dataset_id
  FAILED tests/unit/test_sqlite_backend.py::TestResolveSelfSource::test_first_call_returns_int_id
  FAILED tests/unit/test_sqlite_backend.py::TestResolveSelfSource::test_second_call_same_args_returns_same_id
  FAILED tests/unit/test_sqlite_backend.py::TestResolveSelfSource::test_second_call_does_not_duplicate_row
  FAILED tests/unit/test_sqlite_backend.py::TestResolveSelfSource::test_different_host_produces_different_id
  FAILED tests/unit/test_sqlite_backend.py::TestResolveSelfSource::test_uses_sync_plugin_and_pull_identity
  FAILED tests/unit/test_sqlite_backend.py::TestResolveSelfSource::test_isolated_by_dataset_id
  23 failed, 130 passed in 15.26s
  All 23 B-09 tests fail with AttributeError: 'SQLiteBackend' object has no attribute '<method>'.
  130 existing tests pass (no regression).
  ```
- lint: `uv run ruff check tests/unit/test_sqlite_backend.py` — All checks passed.
- format: `uv run ruff format --check tests/unit/test_sqlite_backend.py` — 1 file already formatted.
- Notes:
  - `resolve_self_source` uses plugin='sync', identity='pull' — verified directly from
    `postgres.py` lines 726-743. The Postgres implementation does a SELECT-or-INSERT with
    those exact string literals. The SQLite coder must match them exactly.
  - `resolve_document` with empty source_uri must return None — this guard is explicit in
    postgres.py lines 695-696.
  - `delete_document` / `delete_conversation` cascade is guaranteed by
    `PRAGMA foreign_keys = ON` which _get_connection sets per-connection. No explicit
    cascade SQL needed in the method bodies.
- Status: red — handed off to tdd-coder
- Status: red — handed off to tdd-coder

## B-10 — `insert_revision` with monotonic `revision_number`
- Test files: `tests/unit/test_sqlite_backend.py` (appended 6 classes, 16 tests)
  - `TestInsertRevisionHappyPath` (5 tests): returns dict with id+revision_number; id is positive int; first revision has revision_number=1; second has revision_number=2; row visible via SELECT
  - `TestInsertRevisionParents` (2 tests): parent_revision_id=None stored as NULL; non-None parent_revision_id stored and FK preserved
  - `TestInsertRevisionTombstone` (2 tests): is_tombstone=True stored as truthy; text="" allowed for tombstone
  - `TestInsertRevisionMetadata` (3 tests): metadata=None stores NULL or '{}'  JSON; flat dict round-trips; nested dict round-trips
  - `TestInsertRevisionMonotonicity` (2 tests): two threads with Event handshake produce revision_numbers {1,2} with no duplicate; two independent document_ids each independently start at 1
  - `TestInsertRevisionFailurePaths` (2 tests): non-existent document_id raises IntegrityError (FK, enforced via PRAGMA foreign_keys=ON); missing required kwarg raises TypeError
- Run command: `PYTHONPATH=. uv run pytest tests/unit/test_sqlite_backend.py -k "TestInsertRevision" -v --no-header 2>&1 | tail -25`
- Edge case checklist:
  - [x] happy path — basic insert, correct return shape, numbering from 1
  - [x] boundaries — first revision (no prior MAX, so MAX=NULL → 0+1=1); second revision (MAX=1 → 2); text="" for tombstone
  - [x] type / format — metadata=None (→ '{}' or NULL); dict metadata JSON serialization; nested dict
  - [x] state — fresh state (no prior revisions → starts at 1); sequential state (two revisions → 1 and 2)
  - [x] concurrency — two threads contending for lock_source; Event handshake ensures genuine contention; revision_numbers must be {1,2}
  - [x] failure paths — invalid FK (document_id 999999) → IntegrityError; missing required kwarg → TypeError
  - [ ] N/A — locale/time (created_at uses SQLite default strftime; not tested here)
  - [x] production-realistic data — source_uri patterns matching vault://, content_hash strings, author_host values matching real hostname patterns
  - [x] regression hooks — monotonicity test pins that lock_source() actually serializes the MAX()+1 allocation (the core B-10 invariant); the independent-document test pins that revision_number scope is per document_id
- Red output (tail):
  ```
  FAILED tests/unit/test_sqlite_backend.py::TestInsertRevisionHappyPath::test_returns_dict_with_id_and_revision_number
  FAILED tests/unit/test_sqlite_backend.py::TestInsertRevisionHappyPath::test_id_is_positive_integer
  FAILED tests/unit/test_sqlite_backend.py::TestInsertRevisionHappyPath::test_first_revision_has_revision_number_one
  FAILED tests/unit/test_sqlite_backend.py::TestInsertRevisionHappyPath::test_second_revision_has_revision_number_two
  FAILED tests/unit/test_sqlite_backend.py::TestInsertRevisionHappyPath::test_row_visible_via_select
  FAILED tests/unit/test_sqlite_backend.py::TestInsertRevisionParents::test_parent_revision_id_none_for_first_revision
  FAILED tests/unit/test_sqlite_backend.py::TestInsertRevisionParents::test_parent_revision_id_stored_for_child_revision
  FAILED tests/unit/test_sqlite_backend.py::TestInsertRevisionTombstone::test_is_tombstone_true_stored_as_truthy
  FAILED tests/unit/test_sqlite_backend.py::TestInsertRevisionTombstone::test_empty_text_allowed_for_tombstone
  FAILED tests/unit/test_sqlite_backend.py::TestInsertRevisionMetadata::test_metadata_none_stores_empty_json_object
  FAILED tests/unit/test_sqlite_backend.py::TestInsertRevisionMetadata::test_metadata_dict_round_trips
  FAILED tests/unit/test_sqlite_backend.py::TestInsertRevisionMetadata::test_nested_metadata_dict_round_trips
  FAILED tests/unit/test_sqlite_backend.py::TestInsertRevisionMonotonicity::test_two_threads_produce_revision_numbers_one_and_two
  FAILED tests/unit/test_sqlite_backend.py::TestInsertRevisionMonotonicity::test_independent_documents_each_start_at_one
  FAILED tests/unit/test_sqlite_backend.py::TestInsertRevisionFailurePaths::test_invalid_document_id_raises_integrity_error
  FAILED tests/unit/test_sqlite_backend.py::TestInsertRevisionFailurePaths::test_missing_required_kwarg_raises_type_error
  16 failed, 153 deselected in 1.21s
  ```
  All 16 B-10 tests fail with: `AttributeError: 'SQLiteBackend' object has no attribute 'insert_revision'`
  153 pre-existing tests pass (no regression).
- lint: `uv run ruff check tests/unit/test_sqlite_backend.py` — All checks passed.
- format: `uv run ruff format --check tests/unit/test_sqlite_backend.py` — 1 file already formatted.
- Notes:
  - The `test_missing_required_kwarg_raises_type_error` test currently fails with `AttributeError`
    (method absent) rather than `TypeError` (bad call signature). This is correct red — once the
    coder adds `insert_revision`, calling it without `document_id` will raise `TypeError` as intended.
  - The `test_invalid_document_id_raises_integrity_error` test uses `pytest.raises` + `lock_source`
    combined with `with (A, B):` syntax (Python 3.10+) to satisfy SIM117 lint rule.
  - `metadata=None` behavior: test allows either NULL or '{}' in the DB to give the coder freedom
    matching the Postgres Json({}) pattern vs SQL NULL. If the coder stores '{}', the `json.loads`
    path validates it; if NULL, the test passes the None branch. Both are acceptable.
- Status: red — handed off to tdd-coder

## B-11 — `latest_revision`, `pending_remote_revisions`, `mark_revision_pulled`
- Test files: `tests/unit/test_sqlite_backend.py` (appended 3 test classes + helpers, 15 tests)
  - `TestLatestRevision` (3 tests): highest revision_number returned; None for unknown doc; isolated by document_id
  - `TestPendingRemoteRevisions` (8 tests): happy path; filters by dataset_id; excludes self_host; respects last_pulled_revision_id; None treated as 0; ordered by id ASC; honors limit; source_uri + parent_content_hash present from JOINs; parent_content_hash=None for root revision
  - `TestMarkRevisionPulled` (3 tests): basic update; monotonic (smaller value does not regress); idempotent on same value
- Run command: `PYTHONPATH=. uv run pytest tests/unit/test_sqlite_backend.py -v -k "TestLatestRevision or TestPendingRemoteRevisions or TestMarkRevisionPulled" 2>&1 | tail -30`
- Edge case checklist:
  - [x] happy path — latest_revision returns highest revision_number row; pending returns remote revisions; mark_revision_pulled sets pointer
  - [x] boundaries — last_pulled_revision_id=None (treated as 0); limit=2 caps to 2 rows; root revision with no parent gives parent_content_hash=None
  - [x] type/format — N/A (all integer/string columns, no special types)
  - [x] state — monotonicity: smaller revision_id does not regress pointer; idempotent: double-call with same value is no-op; isolation: document_id isolation in latest_revision; dataset_id isolation in pending
  - [x] N/A — concurrency (read-only methods; concurrency covered by B-08/B-10 lock)
  - [x] failure paths — latest_revision for unknown document_id returns None (not raise)
  - [x] N/A — locale/time (no locale/time dependencies)
  - [x] production-realistic — multi-document setup; mixed self/remote authors; parent-child revision chains
  - [x] regression hooks — separate dataset isolation test pins cross-dataset filtering contract; self_host exclusion pins the author_host <> self_host filter
- Red output (tail):
  ```
  FAILED tests/unit/test_sqlite_backend.py::TestLatestRevision::test_returns_highest_revision_number_row
  FAILED tests/unit/test_sqlite_backend.py::TestLatestRevision::test_returns_none_for_unknown_document
  FAILED tests/unit/test_sqlite_backend.py::TestLatestRevision::test_isolated_by_document_id
  FAILED tests/unit/test_sqlite_backend.py::TestPendingRemoteRevisions::test_happy_path_returns_remote_revisions
  FAILED tests/unit/test_sqlite_backend.py::TestPendingRemoteRevisions::test_filters_by_dataset_id
  FAILED tests/unit/test_sqlite_backend.py::TestPendingRemoteRevisions::test_excludes_self_host_revisions
  FAILED tests/unit/test_sqlite_backend.py::TestPendingRemoteRevisions::test_respects_last_pulled_revision_id
  FAILED tests/unit/test_sqlite_backend.py::TestPendingRemoteRevisions::test_none_last_pulled_revision_id_returns_all
  FAILED tests/unit/test_sqlite_backend.py::TestPendingRemoteRevisions::test_orders_by_id_asc
  FAILED tests/unit/test_sqlite_backend.py::TestPendingRemoteRevisions::test_honors_limit
  FAILED tests/unit/test_sqlite_backend.py::TestPendingRemoteRevisions::test_result_includes_source_uri_and_parent_content_hash
  FAILED tests/unit/test_sqlite_backend.py::TestPendingRemoteRevisions::test_parent_content_hash_is_none_for_root_revision
  FAILED tests/unit/test_sqlite_backend.py::TestMarkRevisionPulled::test_updates_last_pulled_revision_id
  FAILED tests/unit/test_sqlite_backend.py::TestMarkRevisionPulled::test_monotonic_smaller_value_does_not_regress
  FAILED tests/unit/test_sqlite_backend.py::TestMarkRevisionPulled::test_idempotent_on_same_value
  15 failed, 169 deselected in 1.11s
  ```
  All 15 B-11 tests fail with: `AttributeError: 'SQLiteBackend' object has no attribute 'X'` (X = latest_revision / pending_remote_revisions / mark_revision_pulled).
  169 pre-existing tests pass (no regression).
- lint: `uv run ruff check tests/unit/test_sqlite_backend.py` — All checks passed.
- format: `uv run ruff format --check tests/unit/test_sqlite_backend.py` — 1 file already formatted.
- Notes:
  - `mark_revision_pulled` must use `MAX(COALESCE(last_pulled_revision_id, 0), ?)` (SQLite scalar MAX),
    not `GREATEST(...)` which is Postgres-only. The monotonicity test pins this behavior.
  - `pending_remote_revisions` JOIN shape verified against postgres.py Wave-13b: `r.*`, `d.source_uri`,
    `parent.content_hash AS parent_content_hash` with LEFT JOIN on parent_revision_id.
  - Helper `_insert_revision_direct` bypasses `lock_source` for read-side test setup (test data
    scaffolding only — not testing write serialization here, that's B-10).
  - `_insert_source_row` inserts directly via `_execute`; uses `INSERT OR IGNORE` on dataset row
    (delegates to `_insert_dataset_only`).
- Status: red — handed off to tdd-coder

## B-12 — `set_tombstone` + `clear_tombstone` on `SQLiteBackend`
- Test files: `tests/unit/test_sqlite_backend.py` (appended: `TestSetTombstone`, `TestClearTombstone`, `TestTombstoneRoundTrip`)
- Run command: `PYTHONPATH=. uv run pytest tests/unit/test_sqlite_backend.py -k "TestSetTombstone or TestClearTombstone or TestTombstoneRoundTrip" -v --no-header`
- Edge case checklist:
  - [x] happy path — `set_tombstone` writes non-NULL; `clear_tombstone` writes NULL
  - [x] boundaries — N/A (single-row UPDATE; no numeric boundary; document_id is opaque int PK)
  - [x] type/format — ISO-8601 datetime parse test verifies timestamp string format + UTC normalization
  - [x] state — idempotent set (twice → still tombstoned, no error); idempotent clear (already NULL → no error); multi-cycle round-trip (set/clear/set/clear)
  - [x] N/A — concurrency (UPDATE is atomic; no cross-row race; SQLite global write lock covers this)
  - [x] failure paths — unknown document_id → no-op (UPDATE 0 rows is fine, must not raise)
  - [x] locale/time — ISO-8601 UTC check; trailing Z normalized to +00:00 for `fromisoformat` compatibility
  - [x] production-realistic — uses the `_migrated_backend` + `_insert_document_for_tombstone` helpers matching rest of suite style; direct SQL verification after each call
  - [x] regression hooks — N/A (no prior bug reference for B-12)
- Red output (tail):
  ```
  FAILED tests/unit/test_sqlite_backend.py::TestSetTombstone::test_sets_tombstoned_at_to_non_null
  FAILED tests/unit/test_sqlite_backend.py::TestSetTombstone::test_tombstoned_at_parses_as_iso8601_datetime
  FAILED tests/unit/test_sqlite_backend.py::TestSetTombstone::test_idempotent_double_call_still_tombstoned
  FAILED tests/unit/test_sqlite_backend.py::TestSetTombstone::test_unknown_document_id_is_noop
  FAILED tests/unit/test_sqlite_backend.py::TestClearTombstone::test_clears_existing_tombstone_to_null
  FAILED tests/unit/test_sqlite_backend.py::TestClearTombstone::test_tombstoned_at_becomes_null
  FAILED tests/unit/test_sqlite_backend.py::TestClearTombstone::test_idempotent_on_already_clear
  FAILED tests/unit/test_sqlite_backend.py::TestClearTombstone::test_unknown_document_id_is_noop
  FAILED tests/unit/test_sqlite_backend.py::TestTombstoneRoundTrip::test_set_then_clear_returns_to_null
  FAILED tests/unit/test_sqlite_backend.py::TestTombstoneRoundTrip::test_set_clear_set_works
  ======================== 10 failed, 184 passed in 7.38s ========================
  All failures: AttributeError: 'SQLiteBackend' object has no attribute 'set_tombstone'
  ```
- Status: red — handed off to tdd-coder

## B-14
- Test files:
  - `tests/unit/test_config_extended.py` (class `TestSyncGateValidator`, 14 new methods — 9 rejection/happy-path + 1 xfail + 4 toml/field-order variants)
  - `tests/unit/test_daemon.py` (class `TestDaemonRespectsConfigValidator`, 3 new methods)
- Run command: `PYTHONPATH=. uv run pytest tests/unit/test_config_extended.py::TestSyncGateValidator tests/unit/test_daemon.py::TestDaemonRespectsConfigValidator -v`
- Edge case checklist:
  - [x] happy path — sqlite+all-sync-disabled OK; postgres+mixed-sync OK (2 happy tests in config_extended)
  - [x] boundaries — empty datasets list; single offending dataset; last-of-three offending dataset
  - [x] type/format — TOML-based load path tested; constructor dict path tested
  - [x] state — field-order invariance (datasets-first vs backend-first in kwargs dict)
  - [N/A] concurrency — pure Pydantic validator, no shared state
  - [N/A] failure paths — validator IS the failure path; tested via rejection tests
  - [N/A] locale/time — not applicable
  - [x] production-realistic — TOML fixture mirrors real user config shape
  - [x] regression hooks — daemon test pins the contract that validator fires before run_daemon
  - [x] optional nice-to-have — xfail test for "names offending dataset in error" (non-strict)
- Red output (tail):
  ```
  XFAIL tests/unit/test_config_extended.py::TestSyncGateValidator::test_optional_error_names_offending_dataset
  FAILED tests/unit/test_config_extended.py::TestSyncGateValidator::test_rejection_single_dataset_sync_enabled_sqlite
  FAILED tests/unit/test_config_extended.py::TestSyncGateValidator::test_rejection_error_message_exact_text
  FAILED tests/unit/test_config_extended.py::TestSyncGateValidator::test_rejection_multiple_datasets_one_sync_enabled
  FAILED tests/unit/test_config_extended.py::TestSyncGateValidator::test_rejection_second_dataset_triggers_validator
  FAILED tests/unit/test_config_extended.py::TestSyncGateValidator::test_rejection_all_datasets_sync_enabled_sqlite
  FAILED tests/unit/test_config_extended.py::TestSyncGateValidator::test_field_order_invariance_datasets_first
  FAILED tests/unit/test_config_extended.py::TestSyncGateValidator::test_field_order_invariance_backend_first
  FAILED tests/unit/test_config_extended.py::TestSyncGateValidator::test_rejection_via_toml_load
  FAILED tests/unit/test_daemon.py::TestDaemonRespectsConfigValidator::test_config_construction_raises_before_run_daemon
  FAILED tests/unit/test_daemon.py::TestDaemonRespectsConfigValidator::test_run_daemon_never_called_with_offending_config
  10 failed, 6 passed, 1 xfailed in 2.29s
  All failures: Failed: DID NOT RAISE <class 'pydantic_core._pydantic_core.ValidationError'>
  ```
- Status: red — handed off to tdd-coder

## B-13 — ingest.py + embed.py wiring for kind=="sqlite"
- Test files:
  - `tests/unit/test_ingest_sqlite_wiring.py` (15 tests across 4 classes)
  - `tests/unit/test_embed_sqlite_wiring.py` (15 tests across 4 classes)
- Run command: `PYTHONPATH=. uv run pytest tests/unit/test_ingest_sqlite_wiring.py tests/unit/test_embed_sqlite_wiring.py -v`
- Edge case checklist:
  - [x] happy path — kind=="sqlite" dispatches to SQLiteBackend; kind=="postgres" still dispatches to PostgresBackend (no regression)
  - [x] boundaries — constructor kwargs: path= present, schema= present, dsn= absent for SQLite; dsn= present for Postgres
  - [N/A] type/format — dispatch logic only; no type coercion tested
  - [x] state — migrate() called exactly once on the instantiated backend
  - [N/A] concurrency — pure synchronous wiring; no shared state
  - [x] failure paths — unknown kind ("duckdb", "notarealbackend") raises ValueError containing the kind name
  - [N/A] locale/time — not applicable
  - [x] production-realistic — dsn value mirrors real macOS path "~/Library/Application Support/corpus-forge/corpus.db"
  - [x] regression hooks — Postgres regression tests confirm existing wiring unchanged; postgres migrate-once test for embed.py flags that embed.py currently never calls migrate() on the backend
- Lazy import test approach:
  - `test_sqlite_backend_import_is_not_at_module_level` (ingest): uses importlib isolation to verify that re-importing corpus_forge.ingest does not pull in corpus_forge.backends.sqlite. Passes today (sqlite import absent from ingest.py module level); pins contract for coder.
  - `test_sqlite_backend_present_in_sys_modules_after_sqlite_call` (both files): after calling with kind="sqlite" (backends.sqlite.SQLiteBackend patched), asserts module is in sys.modules. Fails today (ValueError raised before lazy-import branch executes).
  - `test_importing_embed_module_does_not_eagerly_import_sqlite_backend` (embed): passes today (no sqlite import in embed.py); pins no-eager-import contract.
  - Eager postgres import in embed.py (line 5): noted but the test for this was left out per task guidance ("leave it out and just note it"). The board notes it as scope-eligible, not required.
- Conflicting pre-existing test to remove: `tests/unit/test_embed_backfill.py::TestBackfillEmbedder::test_backfill_embedder_unsupported_backend` currently passes (expects kind=="sqlite" to raise ValueError). When B-13 is implemented, this test will break. The B-13 coder must delete or update it.
- Red output (tail):
  ```
  FAILED tests/unit/test_ingest_sqlite_wiring.py::TestIngestOnceSQLiteDispatch::test_sqlite_instantiates_sqlite_backend
  FAILED tests/unit/test_ingest_sqlite_wiring.py::TestIngestOnceSQLiteDispatch::test_sqlite_backend_receives_path_kwarg
  FAILED tests/unit/test_ingest_sqlite_wiring.py::TestIngestOnceSQLiteDispatch::test_sqlite_backend_receives_schema_kwarg
  FAILED tests/unit/test_ingest_sqlite_wiring.py::TestIngestOnceSQLiteDispatch::test_sqlite_backend_does_not_receive_dsn_kwarg
  FAILED tests/unit/test_ingest_sqlite_wiring.py::TestIngestOnceSQLiteDispatch::test_migrate_is_called_once_on_sqlite_backend
  FAILED tests/unit/test_ingest_sqlite_wiring.py::TestIngestOnceSQLiteDispatch::test_sqlite_dispatch_does_not_call_postgres_backend
  FAILED tests/unit/test_ingest_sqlite_wiring.py::TestIngestOnceSQLiteLazyImport::test_sqlite_backend_present_in_sys_modules_after_sqlite_call
  FAILED tests/unit/test_embed_sqlite_wiring.py::TestBackfillEmbedderSQLiteDispatch::test_sqlite_instantiates_sqlite_backend
  FAILED tests/unit/test_embed_sqlite_wiring.py::TestBackfillEmbedderSQLiteDispatch::test_sqlite_backend_receives_path_kwarg
  FAILED tests/unit/test_embed_sqlite_wiring.py::TestBackfillEmbedderSQLiteDispatch::test_sqlite_backend_receives_schema_kwarg
  FAILED tests/unit/test_embed_sqlite_wiring.py::TestBackfillEmbedderSQLiteDispatch::test_sqlite_backend_does_not_receive_dsn_kwarg
  FAILED tests/unit/test_embed_sqlite_wiring.py::TestBackfillEmbedderSQLiteDispatch::test_migrate_called_once_on_sqlite_backend
  FAILED tests/unit/test_embed_sqlite_wiring.py::TestBackfillEmbedderSQLiteDispatch::test_sqlite_dispatch_does_not_call_postgres_backend
  FAILED tests/unit/test_embed_sqlite_wiring.py::TestBackfillEmbedderPostgresRegressionWiring::test_postgres_migrate_is_called_once
  FAILED tests/unit/test_embed_sqlite_wiring.py::TestBackfillEmbedderSQLiteLazyImport::test_sqlite_backend_present_in_sys_modules_after_sqlite_call
  15 failed, 15 passed, 1 warning in 2.39s
  Primary failures: ValueError: Unsupported backend kind: sqlite (SQLite dispatch tests)
  embed postgres migrate test: AssertionError: Expected 'migrate' to have been called once (embed.py does not call migrate() today)
  ```
- Status: red — handed off to tdd-coder
- Status: red — handed off to tdd-coder (B-13)

## B-15 — Integration tests for SQLiteBackend (mirror PG suite)
- Test files:
  - `tests/integration/test_backend_sqlite.py` (~590 LOC, 63 tests)
  - `tests/integration/test_migrate_sqlite.py` (~270 LOC, 44 tests)
- Run command: `PYTHONPATH=. uv run pytest tests/integration/test_backend_sqlite.py tests/integration/test_migrate_sqlite.py -v`
- Edge case checklist:
  - [x] happy path — migrate creates all tables, upsert_document inserts/updates, all CRUD methods return expected results
  - [x] boundaries — empty embedding list, unknown embedder id, missing document, empty source_uri, single-message conversation
  - [x] type/format — JSON metadata round-tripped via json.loads, ISO-8601 timestamp format validated, integer FK ids checked
  - [x] state — idempotency on double-migrate, double-register_embedder, double-write_embeddings, double-set_tombstone/clear_tombstone
  - [x] concurrency — threading.Lock serialization smoke test (two threads both complete)
  - [x] failure paths — FK violation (IntegrityError), UNIQUE violation, unknown embedder raises ValueError, lock_source reraises exception
  - [x] locale/time — tombstoned_at stored as ISO-8601 TEXT ending in Z (UTC), validated with string prefix checks
  - [x] production-realistic — SQLite PRAGMA introspection for column/FK/index assertions mirrors Postgres information_schema approach
  - [x] regression hooks — get_migration_files path behavior: must pass schema root NOT sqlite/ subdir (double-nesting bug caught)
- Notes on SQLite-vs-Postgres analogs not written:
  - N/A — pg_extension / CREATE EXTENSION: SQLite uses sqlite-vec loaded at runtime; vec0 tested with `SQLITE_VEC_AVAILABLE` gate
  - N/A — pg_advisory_lock conflict test (Postgres test_advisory_lock_conflict uses nested lock_source which raises RuntimeError on Postgres; SQLite BEGIN IMMEDIATE does not support nested re-entry in same thread — omitted from SQLite suite since the lock is thread-level, not connection-level)
  - N/A — pgvector `register_vector` / vector type queries: SQLite uses raw BLOB or vec0; assertions use backend._get_connection() to load vec0 extension
  - N/A — information_schema queries replaced by PRAGMA table_info, PRAGMA foreign_key_list, sqlite_master
- PRAGMA patterns used:
  - `SELECT name FROM sqlite_master WHERE type='table'` — table enumeration
  - `PRAGMA table_info(<table>)` — column presence
  - `PRAGMA foreign_key_list(<table>)` — FK declarations
  - `PRAGMA foreign_keys` — per-connection FK enforcement flag (ON=1 vs OFF=0)
  - `SELECT name FROM sqlite_master WHERE type='index' AND tbl_name = ?` — index presence
- vec0-gated subset: `TestWriteEmbeddings::test_write_embeddings_vec0_stores_vector` is gated on `@pytest.mark.skipif(not SQLITE_VEC_AVAILABLE, ...)`. On this machine sqlite-vec IS available so the test ran and passed.
- Green output (tail):
  ```
  tests/integration/test_migrate_sqlite.py::TestMigrateSQLiteConstraints::test_datasets_name_unique_constraint PASSED [ 99%]
  tests/integration/test_migrate_sqlite.py::TestMigrateSQLiteConstraints::test_documents_dataset_source_unique_constraint PASSED [100%]

  ============================= 107 passed in 4.99s ==============================
  ```
- Status: green — characterization tests pass against existing production code

## B-18
- Test files: `tests/smoke/test_smoke_sqlite.py`
- Run command: `PYTHONPATH=. uv run pytest tests/smoke/test_smoke_sqlite.py -v`
- Edge case checklist:
  - [x] happy path — 3-file vault, 1 embedder, all rows verified after ingest_once
  - [ ] N/A — boundaries (smoke = one happy path; edge cases belong to unit suite)
  - [ ] N/A — type/format (smoke test does not test invalid inputs)
  - [ ] N/A — state (first-run only; idempotency covered by existing unit tests)
  - [ ] N/A — concurrency (single-threaded smoke; SQLite concurrency in B-08 unit tests)
  - [ ] N/A — failure paths (smoke = happy path only)
  - [ ] N/A — locale/time (no time-sensitive assertions)
  - [x] production-realistic data — real markdown files, real Config object, real SQLite db file under tmp_path
  - [ ] N/A — regression hooks (no specific bug referenced)
- Bug surfaced: `ingest.py:_get_or_create_dataset` uses `corpus.datasets` schema-qualified table name and `%s` Postgres-style placeholders. SQLite has no `corpus` attached schema and its sqlite3 module requires `?` placeholders. Both issues raise `sqlite3.OperationalError: near "%": syntax error` on the first `_execute` call inside `ingest_once`. This function was not updated as part of B-13 wiring. Coder must fix `_get_or_create_dataset` to use unqualified table names and `?` placeholders (or backend-dispatch the placeholder style).
- Red output (tail):
  ```
  with self._get_connection() as conn:
      try:
  >       cursor = conn.execute(sql_body, params)
                   ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  E   sqlite3.OperationalError: near "%": syntax error
  
  corpus_forge/backends/sqlite.py:410: OperationalError
  ============================= 1 failed, 1 warning in 3.79s ============================
  ```
- Status: red — handed off to tdd-coder. Root cause: `ingest.py:_get_or_create_dataset` is Postgres-specific. Coder must make it SQLite-compatible (unqualified table names + `?` placeholders).

## B-16 — Dual-backend parametrize fixture + representative slice
- Test files:
  - `tests/conftest.py` (added `backend_kind` and `storage_backend` fixtures)
  - `tests/integration/test_backend_dual.py` (new file — 17 parametrized tests x 2 backends = 34 executions)
- Run command: `PYTHONPATH=. uv run pytest tests/integration/test_backend_dual.py -v`
- Edge case checklist:
  - [x] happy path — upsert_document, register_embedder, write_embeddings on both backends
  - [x] boundaries — empty write_embeddings call (noop), unchanged content_hash short-circuit
  - [x] type/format — return types are int, dict keys present on both backends
  - [x] state — revision monotonicity; latest_revision reads back highest; idempotent reingest
  - [x] N/A — concurrency (lock_source exercised via insert_revision; deeper concurrency is B-08 territory)
  - [x] failure paths — Docker unavailable -> postgres param skips cleanly; SQLite always runs
  - [x] N/A — locale/time (no locale/DST concerns in CRUD contract)
  - [x] production-realistic — 12-section markdown doc, real MarkdownChunker, deterministic FakeEmbedder, actual ingest_one call
  - [x] regression hooks — chunk_reuse_e2e analog; identical-reingest encode-count=0 pin; pending_remote_revisions self-host filter
- Test breakdown:
  - `TestUpsertDocumentSmoke` (8 tests): upsert_document contract, register_embedder, write_embeddings, chunks_missing_embedding, end-to-end ingest_one
  - `TestChunkReuseE2E` (2 tests): append-reuse pin (>=7 reused, <=3 new encodes); identical-reingest noop
  - `TestRevisions` (7 tests): insert_revision id+number, monotonic, latest_revision reads back highest, latest_revision None when empty, pending_remote_revisions self-filter, pending_remote_revisions remote included, pending_remote_revisions last_pulled pointer
  - Total: 17 parametrized functions x 2 backends = 34 test executions
- Docker availability behavior:
  - Docker IS available (this run): both [postgres] and [sqlite] ids run -> 34/34 passed
  - Docker NOT available: [postgres] ids -> SKIPPED (pytest.skip in storage_backend fixture); [sqlite] ids -> 17/17 passed
- Run output (tail):
  ```
  tests/integration/test_backend_dual.py::TestRevisions::test_pending_remote_revisions_returns_remote[postgres] PASSED [ 91%]
  tests/integration/test_backend_dual.py::TestRevisions::test_pending_remote_revisions_returns_remote[sqlite] PASSED [ 94%]
  tests/integration/test_backend_dual.py::TestRevisions::test_pending_remote_revisions_respects_last_pulled[postgres] PASSED [ 97%]
  tests/integration/test_backend_dual.py::TestRevisions::test_pending_remote_revisions_respects_last_pulled[sqlite] PASSED [100%]

  ======================== 34 passed, 1 warning in 17.55s ========================
  ```
- Status: green — characterization tests pin shared StorageBackend contract. tdd-tester completed B-16.

---

## Phase R3 — eval harness

### R3-01 (pyproject extras) — RED

- Wrote `tests/unit/test_pyproject_eval_extras.py` (7 tests).
- Asserts `[retrieval]` + `[eval]` extras exist with `numpy>=1.26` floor.
- Negative asserts (R3 scope guard): `[rerank]` and `[mcp]` MUST NOT be declared yet.
- Status: **red** — `test_retrieval_extra_present`, `test_eval_extra_present`, `test_numpy_floor_at_least_1_26` fail (extras missing). The two scope-guard tests and `test_numpy_importable_in_dev_env` already pass — kept for ongoing protection.

### R3-02 (metrics) — RED

- Wrote `tests/unit/test_eval_metrics.py` (29 tests).
- Coverage: NDCG binary + graded with hand-computed known answers (DCG = 1/log2(rank+1)·gain, IDCG via sorted gains), MRR (rank 1 / rank 5 / no-hit / outside-k), Recall (full / partial / k-truncation), edge cases (empty ranking, empty relevant, k>len, k=0), input-shape tolerance (list vs set, str-keyed graded, duplicates in ranking).
- Status: **red** — `corpus_forge.eval.metrics` does not yet exist.

### R3-03 (dataset loader) — RED

- Wrote `tests/unit/test_eval_dataset.py` (21 tests).
- Coverage: `GoldQuery` shape (required + optional + frozen); `load_gold` happy paths (minimal row, multi-row, str→int graded normalisation, mixed binary+graded, content_hashes, blank/comment-line skipping); error paths (each required field missing, empty `relevant_chunk_ids`, type mismatches, bad JSON with line number, content_hashes length mismatch, FileNotFoundError, non-int chunk_id).
- Status: **red** — `corpus_forge.eval.dataset` does not yet exist.

### Wave 0 RED summary

- Combined: **54 RED + 3 GREEN (scope-guard tests)** in `tests/unit/test_eval_metrics.py`, `tests/unit/test_eval_dataset.py`, `tests/unit/test_pyproject_eval_extras.py`.
- Handed off to tdd-coder.

### R3-04 (runner + pinned baseline) — RED

- Wrote `tests/unit/test_eval_runner.py` (11 tests).
- Coverage: shape (`RetrievalMetrics` returned, k keys present, values in [0,1], `max_queries` honoured), `report` table format (mentions ndcg/mrr/recall + k), `dump_json` JSON file is parseable with the metric/k structure, **PINNED NDCG@10 floor at 0.80** against the toy seeded corpus, **break-the-retriever sanity test** (constant-vector encode_query + alpha=1.0 must score below the floor), edge cases (empty gold set, empty k_values).
- Test corpus: on-disk SQLite under `tmp_path` (in-memory SQLite + sqlite_vec extension is fiddly across loader paths; file-based is functionally identical for the unit-test scope), 10 hand-engineered chunks each with unique animal-keyword anchors, 10 hand-curated queries each whose lexical + dense top-1 IS the gold-relevant chunk.
- Pinned floor `_PINNED_NDCG_AT_10_FLOOR = 0.80` — tight enough to catch regression (e.g. the R2 `encode_query` swap silently degrading), loose enough to survive a fusion-constant tweak.
- Status: **red** — `corpus_forge.eval.runner` does not yet exist.

### R3-05 (drift fallback) — RED → GREEN

- Extended `tests/unit/test_eval_runner.py` with `TestChunkIdDriftFallback` (3 tests).
- Coverage: garbage chunk_ids + real content_hashes recover via the fallback (NDCG=1.0); valid chunk_id + bogus hash is tolerated (hash advisory only); both missing → zero contribution (no silent skip).
- 14/14 total runner tests green after coder pass.

### R3-07 (`eval` CLI subcommand group) — RED → GREEN

- Wrote `tests/unit/test_cli_eval.py` (11 tests).
- Coverage: command-group help shows both `retrieval` + `corpus-quality`; per-command help advertises every option; CSV `--k` parsing; JSON dump writes a parseable payload; `--rerank` emits a friendly "lands in R4" notice (stderr or stdout); table reported on stdout; unknown bundled dataset names error nonzero; corpus-quality requires `--dataset`; helper smoke against a minimal stub-config to prove backend + embedder wiring.
- 11/11 green after coder pass.

### R3-06 (bundled gold set) — RED → GREEN

- Wrote `tests/unit/test_eval_bundled_dataset.py` (7 tests): existence of JSONL + provenance.md, parse via `load_gold`, ≥20 queries, every row carries content_hashes, unique query_ids, non-empty relevant_chunk_ids, parallel length invariant.
- 7/7 green after curation.

### R3-08 (smoke) — GREEN

- `tests/smoke/test_eval_smoke.py` (1 test). Synthesises an in-memory `Config` pointing at the seeded `/tmp/corpus-forge-test.db`, patches `Config.load`, invokes `corpus-forge eval retrieval --dataset forge_self --k 10 --json <tmp>` via `CliRunner`.
- Skip-on-missing-seed: if `/tmp/corpus-forge-test.db` is absent OR has zero chunks, the test pytest.skips with a clear pointer to `scripts/vectorize_repo_sqlite.py`.
- Asserts exit code 0, table on stdout (ndcg/mrr/recall + k=10), JSON dump parseable with all three metric blocks and values in [0, 1].
- **Real-corpus measured baseline (NDCG@10 = 0.717, MRR@10 = 0.920, Recall@10 = 0.760)** against the auto-curated forge_self gold set + minilm embedder. The pinned unit-test floor (0.80) is against the FakeEmbedder + toy corpus, not against this real one.

---

## Phase BR test status

### BR-01 (governance files) — RED

- Wrote `tests/unit/test_governance_files.py` (18 tests).
- Surface: LICENSE (re-pin, Apache-2.0 not MIT), CHANGELOG.md (Keep-a-Changelog header + `## [0.1.0b1] - YYYY-MM-DD` entry + SQLite/CI/MCP/Claude milestone anchors), CONTRIBUTING.md (`make dev` + `make ci` + commit-style), CODE_OF_CONDUCT.md (Contributor Covenant 2.1 + evan@jwo3.io), SECURITY.md (`evan@jwo3.io` + supported `0.1.x` + reporting flow).
- LICENSE pins pass already (CI-3 work); the rest RED until BR-01 coder lands.

### BR-02 (.github templates + dependabot + FUNDING) — RED

- Wrote `tests/unit/test_github_templates.py` (8 tests) + `tests/unit/test_dependabot_config.py` (5 tests).
- Templates suite covers: bug_report.yml (GitHub form keys + bug label), feature_request.yml (form keys + enhancement label), config.yml (blank_issues_enabled: false), PULL_REQUEST_TEMPLATE.md (non-empty, summary + checklist), FUNDING.yml (parses).
- Dependabot suite covers: version 2, ≥2 ecosystems, pip + github-actions weekly, directory present on each.

### BR-03 (banner + logo SVG assets) — RED

- Wrote `tests/unit/test_banner_assets.py` (10 tests + 1 optional PNG).
- Surface: `assets/banner.svg`, `assets/banner-dark.svg`, `assets/logo.svg`. Each must parse as XML/SVG, have viewBox; banners must include wordmark + tagline. Logo viewBox must be 1:1 square. banner.png is best-effort — test skips when absent, validates magic bytes when present.

### BR-04 (release workflow + cliff.toml) — RED

- Wrote `tests/unit/test_release_workflow.py` (14 tests) + `tests/unit/test_cliff_config.py` (4 tests).
- Release suite: on.push.tags `v*`, gate job uses `./.github/workflows/ci.yml` (workflow_call), build needs gate + runs `uv build` + sha256sum > SHA256SUMS + upload-artifact, publish needs build + downloads artifact + uses softprops/action-gh-release@v2 with files=dist/*, prerelease derived from `contains(github.ref, 'b') || contains(github.ref, 'rc')`, generate_release_notes: true, contents: write permission grant.
- Cliff suite: TOML parses, has [changelog].body + [git] section with commit_parsers (recognising feat/fix or [tdd-*]), tag_pattern accepts `v0.1.0b1`.

### BR Wave 0 RED summary

47 new tests added. Locally: 2 pre-existing passes (LICENSE pin already on disk; ci.yml workflow_call gate from CI-1), 1 conditional skip (banner.png), 44 errors/fails — exactly the expected RED shape before BR-01..BR-04 GREEN passes.

---

## Phase D

## D-01 — Alembic dep + scaffold + revision-chain unit pin
- Test files: `tests/unit/test_alembic_revision_chain.py`
- Run command: `.venv/bin/python -m pytest tests/unit/test_alembic_revision_chain.py -v`
- Edge case checklist:
  - [x] happy path — `test_alembic_ini_exists_and_parses` (parses ini, asserts section + script_location + no sys.stdout); `test_alembic_env_module_imports` (imports env, asserts callables present); `test_versions_directory_exists` (asserts path is a directory); `test_revision_chain_is_well_formed` (well-formed linear chain when revisions exist)
  - [x] boundaries — zero-revision branch in `test_revision_chain_is_well_formed` exits cleanly with no assertions; lexicographically-highest prefix pinning for head detection
  - [x] type/format — `revision: str` type assertion; `down_revision: str | None` type assertion; no-duplicate check; exact-one-root / exact-one-head checks; no-orphan check
  - [x] state — module cache purge before env import test (hermetic across reruns); `_SENTINEL` pattern to distinguish missing attribute from `None` value
  - [ ] N/A — concurrency (pure file-inspection test, no shared mutable state)
  - [x] failure paths — wrong `script_location` asserts with clear message; missing `run_migrations_online` or `run_migrations_offline` detected; `sys.stdout` routing in ini caught; orphan revision detected; branched chain (>1 head) detected; duplicate revision IDs detected
  - [ ] N/A — locale/time (file and module attribute tests, no date logic)
  - [x] production-realistic — alembic.command.heads() gated on alembic being importable; skips with descriptive reason if not yet installed (D-01 coder installs it)
  - [ ] N/A — regression hooks (no prior bug — this is net-new scaffold detection)
- Red output (tail):
  ```
  FAILED tests/unit/test_alembic_revision_chain.py::test_versions_directory_exists
  FAILED tests/unit/test_alembic_revision_chain.py::test_alembic_ini_exists_and_parses
  FAILED tests/unit/test_alembic_revision_chain.py::test_alembic_env_module_imports
  PASSED tests/unit/test_alembic_revision_chain.py::test_revision_chain_is_well_formed
  ========================= 3 failed, 1 passed in 0.17s ==========================
  ```
- Status: red — handed off to tdd-coder

## D-02
- Test files: `tests/integration/test_alembic_parity_postgres.py`, `tests/integration/test_alembic_parity_sqlite.py`
- Run command: `uv run python -m pytest tests/integration/test_alembic_parity_postgres.py tests/integration/test_alembic_parity_sqlite.py -v`
- Edge case checklist:
  - [x] happy path — sequential legacy + Alembic apply; normalized dump comparison asserts structural equality on tables/columns/constraints/indexes
  - [x] boundaries — both migrators applied to fresh schemas; alembic_version stripped on Alembic side; nextval() defaults normalized; empty strip_tables=frozenset() on legacy side so all tables are captured
  - [x] type/format — schema name stripped from qualified identifiers; index defs stripped of schema prefix; constraint names normalized; SQL keywords lowercased and whitespace collapsed (SQLite side)
  - [x] state — schema dropped+recreated between legacy and Alembic passes (Postgres); tmp_path isolation per parametrize value (SQLite); apply_migrations called after backend.migrate() for idempotency check
  - [ ] N/A — concurrency (sequential migrators, no shared mutable state across passes)
  - [x] failure paths — no revision file → CommandError from Alembic; testcontainers unavailable → skipif guard on PG test; corpus schema pre-created before Alembic so failure mode is canonical CommandError not ProgrammingError
  - [ ] N/A — locale/time (schema introspection only, no date logic)
  - [x] production-realistic — uses real postgres_container fixture from conftest (session-scoped); real SQLiteBackend.migrate() path; sqlite_master + information_schema + pg_indexes queries match prod introspection patterns
  - [x] regression hooks — parametrize list is the explicit extension point: adding "0002_chunk_content_hash" to the head list (D-03 task) is a one-line change
- Red output (tail):
  ```
  FAILED tests/integration/test_alembic_parity_sqlite.py::test_parity_sqlite[head=0001_core]
  FAILED tests/integration/test_alembic_parity_postgres.py::test_parity_postgres[head=0001_core]
  alembic.util.exc.CommandError: Can't locate revision identified by '0001_core'
  ======================== 2 failed, 2 warnings in 2.36s =========================
  ```
- Status: red — handed off to tdd-coder

## D-03 — Revision 0002_chunk_content_hash + backfill
- Test files:
  - `tests/integration/test_alembic_backfill_content_hash.py` (3 new tests)
  - `tests/integration/test_alembic_parity_postgres.py` (extended: added head=0002_chunk_content_hash)
  - `tests/integration/test_alembic_parity_sqlite.py` (extended: added head=0002_chunk_content_hash)
- Run command: `.venv/bin/python -m pytest tests/integration/test_alembic_backfill_content_hash.py tests/integration/test_alembic_parity_postgres.py tests/integration/test_alembic_parity_sqlite.py -v`
- Edge case checklist:
  - [x] happy path — backfill populates content_hash for all 5 chunks; column exists; index exists
  - [x] boundaries — 5 distinct texts (pangrams, realistic prose); idempotency of WHERE content_hash IS NULL guard
  - [x] type/format — SHA-256 hex encoding verified Python-side with hashlib; matches Postgres encode(sha256(text::bytea),'hex')
  - [x] state — upgrade to 0001_core first, insert data, then upgrade to 0002; two-step migration sequence exercised
  - [N/A] concurrency — single-connection test; no concurrent writers
  - [x] failure paths — column-absent sanity check at 0001_core confirms test would catch wrong schema state
  - [N/A] locale/time — content_hash is a deterministic hex string; no locale/timezone dependency
  - [x] production-realistic — FK chain: datasets → documents → chunks using raw SQL matching 001_core.sql structure; 5 pangram texts with distinct SHA-256 values
  - [x] regression hooks — head=0001_core parity tests stay GREEN; revision chain test stays 4/4 GREEN
  - [N/A] SQLite backfill — out of scope per task brief (data migration is Postgres-only)
- Red output (tail):
  ```
  FAILED tests/integration/test_alembic_backfill_content_hash.py::test_backfill_populates_content_hash
  FAILED tests/integration/test_alembic_backfill_content_hash.py::test_chunks_content_hash_idx_exists
  FAILED tests/integration/test_alembic_backfill_content_hash.py::test_backfill_null_text_handled
  FAILED tests/integration/test_alembic_parity_sqlite.py::test_parity_sqlite[head=0002_chunk_content_hash]
  FAILED tests/integration/test_alembic_parity_postgres.py::test_parity_postgres[head=0002_chunk_content_hash]
  alembic.util.exc.CommandError: Can't locate revision identified by '0002_chunk_content_hash'
  2 passed (head=0001_core PG + SQLite), 4 warnings in ~7s total
  ```
- Notes:
  - FK chain: datasets (no FK) -> documents (dataset_id FK) -> chunks (document_id FK). The chunks CHECK constraint requires exactly one of document_id/conversation_id non-null; tests use document_id path.
  - The postgres_container fixture is session-scoped; each test calls _reset_schema() to drop/recreate the corpus schema, giving full isolation without spawning extra containers.
  - Legacy SQL file 002_chunk_content_hash.sql does not yet exist; the parity test _apply_legacy call at head=0002 will also fail (FileNotFoundError from shutil copy), but the Alembic CommandError fires first since Alembic runs second in the parity flow. Acceptable RED.
- Status: red — handed off to tdd-coder

## D-04
- Test files:
  - `tests/integration/test_alembic_parity_postgres.py`
  - `tests/integration/test_alembic_parity_sqlite.py`
- Run command: `.venv/bin/python -m pytest tests/integration/test_alembic_parity_postgres.py tests/integration/test_alembic_parity_sqlite.py -v`
- Edge case checklist:
  - [x] happy — head=0003_views added to both dialect parametrize lists; will pass once revision exists
  - [x] boundaries — legacy file list for Postgres head=0003_views includes all three files in sorted() order (001_core, 002_chunk_content_hash, 002_views); SQLite list is identical to head=0002 (no views SQL in sqlite/ tree)
  - [x] type/format — N/A (parity tests compare schema dicts, not data types added in this revision)
  - [x] state — existing head=0001_core and head=0002_chunk_content_hash remain GREEN (no regression)
  - [N/A] concurrency — single-connection parity test; pure dialect-gate migration
  - [x] failure paths — RED fires with canonical CommandError("Can't locate revision identified by '0003_views'") for both dialects
  - [N/A] locale/time — no locale/timezone-sensitive schema objects in 0003_views (views are structural)
  - [x] production-realistic — same Postgres container and SQLite tmp_path fixtures as prior heads
  - [x] regression hooks — 4/6 pass (0001_core + 0002_chunk_content_hash for both dialects); 2/6 fail (0003_views for both dialects)
  - [x] SQLite dialect-gate — SQLite legacy list at head=0003_views is identical to head=0002 (002_chunk_content_hash.sql only, no 002_views.sql); Alembic at head=0003_views must be a no-op for SQLite (dialect-gated body)
- Red output (tail):
  ```
  FAILED tests/integration/test_alembic_parity_sqlite.py::test_parity_sqlite[head=0003_views]
  FAILED tests/integration/test_alembic_parity_postgres.py::test_parity_postgres[head=0003_views]
  alembic.util.exc.CommandError: Can't locate revision identified by '0003_views'
  4 passed (head=0001_core + head=0002_chunk_content_hash, both dialects), 2 failed, 6 warnings in 2.55s
  ```
- Status: red — handed off to tdd-coder
- Notes:
  - The legacy migrator's sort key is `int(p.stem.split("_")[0])`, so `002_chunk_content_hash.sql` and `002_views.sql` are both in bucket 002. Within a tie bucket, Python's stable sort preserves glob (filesystem) order — not guaranteed alphabetical on macOS. The Postgres parity test stderr showed `002_views.sql` ran before `002_chunk_content_hash.sql` for the sliced-directory legacy run. The D-04 coder must ensure `002_views.sql` CREATE VIEW statements do not depend on objects introduced only in `002_chunk_content_hash.sql` (or vice versa). If they do, the legacy migrator's tie-breaking is unpredictable. This is a pre-existing risk in the legacy migrator, not a D-04 tester blocker.
  - Wave-gate: ruff format clean, ruff lint clean. pyrefly errors (sqlite_vec, mcp, openai) are pre-existing optional-dependency stubs — none introduced by D-04.
  - Wave-gate: ruff format clean, ruff lint clean. pyrefly errors (sqlite_vec, mcp, openai) are pre-existing optional-dependency stubs — none introduced by D-04.

## D-05
- Test files: tests/integration/test_alembic_parity_postgres.py, tests/integration/test_alembic_parity_sqlite.py
- Run command: .venv/bin/python -m pytest tests/integration/test_alembic_parity_postgres.py tests/integration/test_alembic_parity_sqlite.py -v
- Edge case checklist:
  - [x] happy path — head=0004_sync reaches the parity assertion once the revision file exists
  - [x] boundaries — Postgres legacy list = 4 files (001_core + 002_chunk_content_hash + 002_views + 003_sync); SQLite list = 3 files (no 002_views.sql in sqlite/ tree)
  - [N/A] type/format — parity tests compare schema dicts; no data-type edge cases introduced here
  - [x] state — prior heads 0001_core, 0002_chunk_content_hash, 0003_views remain GREEN for both dialects (6 passing)
  - [N/A] concurrency — single-connection parity test; not concurrent
  - [x] failure paths — RED fires with canonical CommandError("Can't locate revision identified by '0004_sync'") for both dialects
  - [N/A] locale/time — no locale/timezone-sensitive schema objects in 0004_sync scope
  - [x] production-realistic — same Postgres testcontainers session fixture and SQLite tmp_path fixtures as prior heads
  - [x] regression hooks — 6/8 pass (0001_core + 0002_chunk_content_hash + 0003_views, both dialects); 2/8 fail (0004_sync for both dialects)
  - [x] SQLite dialect-gate — SQLite legacy list at head=0004_sync has no 002_views.sql (views are Postgres-only); uses 003_sync.sql from sqlite/ subdirectory
- Red output (tail):
  ```
  FAILED tests/integration/test_alembic_parity_sqlite.py::test_parity_sqlite[head=0004_sync]
  FAILED tests/integration/test_alembic_parity_postgres.py::test_parity_postgres[head=0004_sync]
  alembic.util.exc.CommandError: Can't locate revision identified by '0004_sync'
  2 failed, 6 passed, 8 warnings in 3.23s
  ```
- Status: red — handed off to tdd-coder

## D-06
- Test files:
  - tests/integration/test_alembic_backfill_fts_sqlite.py (new, 5 tests)
  - tests/integration/test_alembic_parity_postgres.py (extended: +head=0005_fts)
  - tests/integration/test_alembic_parity_sqlite.py (extended: +head=0005_fts)
- Run command: `.venv/bin/python -m pytest tests/integration/test_alembic_backfill_fts_sqlite.py tests/integration/test_alembic_parity_postgres.py tests/integration/test_alembic_parity_sqlite.py -v`
- Edge case checklist:
  - [x] happy path — pre-existing chunks visible via FTS MATCH after 0005_fts upgrade
  - [x] boundaries — 5 distinct chunks; unique words chosen to avoid cross-chunk FTS contamination; post-migration new chunk tested separately
  - [x] type/format — N/A (FTS queries use text words; no type-coercion edge cases in sqlite3 FTS5)
  - [x] state — pre-FTS rows (seeded at 0004_sync head) vs post-FTS new insert; trigger test is fresh-state; backfill idempotency guarded by description in backfill_lexical_index() docstring (separate test in existing suite)
  - [N/A] concurrency — SQLite in-process, single-threaded; FTS5 is not concurrent
  - [x] failure paths — all 5 backfill tests fail with canonical CommandError at upgrade("0005_fts"); parity tests for 0005_fts also fail with same error
  - [N/A] locale/time — no locale/timezone objects introduced by FTS migration
  - [x] production-realistic — realistic pangram-style chunk texts; unique-word selection designed to mirror real vocabulary search patterns
  - [x] regression hooks — prior heads (0001_core, 0002_chunk_content_hash, 0003_views, 0004_sync) all PASS for SQLite parity; chain tests 4/4 GREEN
  - [x] rebuild-vs-naive — test_no_delete_markers_after_rebuild_backfill asserts COUNT=1 per unique word; documents that naive INSERT would fail for external-content FTS5 tables; test_preexisting_chunks_searchable_after_backfill asserts exact rowid matches for 2 distinct queries
  - [x] trigger coverage — test_after_insert_trigger_fires_for_new_chunks inserts a chunk with a never-before-seen word post-migration and asserts chunks_ai fires
- Red output (tail):
  ```
  FAILED tests/integration/test_alembic_backfill_fts_sqlite.py::test_chunks_fts_virtual_table_exists
  FAILED tests/integration/test_alembic_backfill_fts_sqlite.py::test_preexisting_chunks_searchable_after_backfill
  FAILED tests/integration/test_alembic_backfill_fts_sqlite.py::test_fts_total_chunk_count_after_backfill
  FAILED tests/integration/test_alembic_backfill_fts_sqlite.py::test_no_delete_markers_after_rebuild_backfill
  FAILED tests/integration/test_alembic_backfill_fts_sqlite.py::test_after_insert_trigger_fires_for_new_chunks
  FAILED tests/integration/test_alembic_parity_sqlite.py::test_parity_sqlite[head=0005_fts]
  alembic.util.exc.CommandError: Can't locate revision identified by '0005_fts'
  6 failed, 4 passed, 15 warnings in 0.69s
  PG parity head=0005_fts: CommandError: Can't locate revision identified by '0005_fts' (skipped without Docker)
  ```
- Status: red — handed off to tdd-coder
- Notes:
  - backfill_lexical_index() lives in corpus_forge/backends/sqlite.py:455. It uses conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')") — confirmed correct (NOT naive INSERT SELECT). The Alembic 0005_fts revision must call this method (or reproduce the same SQL) after CREATE VIRTUAL TABLE + triggers are applied.
  - The _dump_sqlite_schema helper in test_alembic_parity_sqlite.py compares schema objects (tables, indexes, triggers, views) from sqlite_master — NOT data rows. So the legacy migrator's backfill_lexical_index() call (which writes data into chunks_fts) does not affect parity, because both sides start with an empty chunks table and the FTS shadow table has no data rows to compare. Parity is schema-only.
  - PG parity test at head=0005_fts is collected (5 items) but requires Docker testcontainer to execute. It will fire CommandError the same way when Docker is present.
  - Wave gate: ruff format + ruff check both clean after auto-format pass.

## D-07
- Test files: `tests/integration/test_apply_migrations_uses_alembic.py`
- Survey file: `.planning/tdd/d07-legacy-test-survey.md`
- Run command: `.venv/bin/python -m pytest tests/integration/test_apply_migrations_uses_alembic.py -v`
- Edge case checklist:
  - [x] happy — alembic_version row == "0005_fts" after upgrade
  - [x] boundaries — bogus schema_dir path proves parameter is ignored
  - [x] type/format — version_num exact string match "0005_fts"
  - [x] state — fresh schema reset before each test; container reused
  - [ ] N/A — concurrency (sequential migration runner; no parallel upgrade paths)
  - [x] failure paths — schema_dir ignored (not FileNotFoundError, clean AssertionError)
  - [ ] N/A — locale/time (no timestamps in alembic_version)
  - [x] production-realistic data — uses real testcontainers PG + real SQLite path
  - [ ] N/A — regression hooks (no prior bug reference for D-07)
  - [x] full-schema probe — test 3 asserts documents/chunks/document_revisions all present
- Red output (tail):
  ```
  FAILED tests/integration/test_apply_migrations_uses_alembic.py::test_apply_migrations_produces_full_schema_pg
  FAILED tests/integration/test_apply_migrations_uses_alembic.py::test_apply_migrations_creates_alembic_version_table_pg
  FAILED tests/integration/test_apply_migrations_uses_alembic.py::test_apply_migrations_creates_alembic_version_table_sqlite
  AssertionError: corpus.alembic_version table does not exist after apply_migrations.
    Legacy apply_migrations reads schema_dir/*.sql and never writes alembic_version — this is the RED state.
  AssertionError: alembic_version table does not exist in the SQLite database.
    Legacy apply_migrations reads schema_dir/sqlite/*.sql — this is the RED state.
  AssertionError: Tables missing from corpus schema after apply_migrations: {'documents', 'chunks', 'document_revisions'}. Tables present: [].
    Legacy apply_migrations with a bogus schema_dir finds no SQL files — this is the RED state.
  3 failed in 1.68s
  ```
- Status: red — handed off to tdd-coder
- Notes:
  - Hidden call-site: SQLiteBackend.migrate() calls _migrate_module.apply_migrations(self, schema_dir=..., dialect="sqlite"). After D-07 rewire, schema_dir will be ignored — no call-site breakage expected.
  - Wave gate: ruff format + ruff check both clean after auto-fix pass.
  - Survey: 8 WILL-FAIL, 3 PROBABLY-OK, 2 AMBIGUOUS (see d07-legacy-test-survey.md for method-level detail).

## D-08 — CLI subcommands `migrate revision` + `migrate history`
- Test files: `tests/unit/test_cli_migrate.py`
- Run command: `.venv/bin/python -m pytest tests/unit/test_cli_migrate.py -v`
- Edge case checklist:
  - [x] happy path — `migrate revision -m "msg"` exits 0 and calls `alembic.command.revision` with correct args
  - [x] boundaries — `migrate revision` without `-m` fails non-zero with helpful error
  - [x] type/format — N/A (thin CLI wrappers; no type coercion beyond string flags)
  - [x] state — N/A (pure dispatch; idempotency is Alembic's concern)
  - [ ] N/A — concurrency (single-threaded CLI dispatch)
  - [x] failure paths — missing required `-m` arg surfaces a user-readable error
  - [ ] N/A — locale/time (no locale/time dependencies)
  - [x] production-realistic — `migrate history` tested with `indicate_current=True` matching actual Alembic default
  - [x] regression hooks — `migrate --help` lists both subcommands; guards against accidental de-registration
- Red output (tail):
  ```
  FAILED tests/unit/test_cli_migrate.py::test_migrate_subcommand_help_lists_revision_and_history
  FAILED tests/unit/test_cli_migrate.py::test_migrate_history_prints_revision_list
  FAILED tests/unit/test_cli_migrate.py::test_migrate_revision_emits_a_file_into_versions_dir
  FAILED tests/unit/test_cli_migrate.py::test_migrate_revision_requires_message_arg
  4 failed in 0.22s
  ```
  All 4 fail with AssertionError — "Got unexpected extra argument (revision/history)" or
  "'revision'/'history' not in help output" — NOT ImportError.
- Status: red — handed off to tdd-coder
- Notes:
  - Surprise: `migrate` is a plain `@app.command()` with no subcommand group. The coder must
    either (a) convert it to a `typer.Typer` sub-app and add `revision`/`history` commands, or
    (b) use `@app.command("migrate")` with a `subcommand` argument — option (a) is cleaner.
  - The existing `corpus-forge migrate` (upgrade to head) must keep working — the coder needs
    to preserve a default-subcommand or bare-`migrate` path after the group conversion.
  - Wave gate: ruff format + ruff check both clean.

## D-09
- Test files: `tests/smoke/test_mcp_serve_boots_with_alembic.py`
- Run command: `.venv/bin/python -m pytest tests/smoke/test_mcp_serve_boots_with_alembic.py -v`
- Edge case checklist:
  - [x] happy path — pre-migrated DB, send initialize, get JSON-RPC response
  - [x] boundaries — fresh (un-migrated) DB forces Alembic run during boot
  - [x] type/format — assert every stdout line is a valid JSON object (starts with `{`)
  - [x] state — pre-migrated vs fresh DB (two fixture classes)
  - [ ] N/A — concurrency (single-process stdio server)
  - [x] failure paths — subprocess crash emits traceback to stderr (non-empty), not stdout
  - [ ] N/A — locale/time (JSON-RPC wire protocol only)
  - [x] production-realistic data — uses the real MCP initialize JSON-RPC wire format (protocol 2024-11-05)
  - [x] regression hooks — stdout-purity pin from commit 66ab179 (no "Applying migration:" noise on stdout)
- Red output (tail):
  ```
  FAILED tests/smoke/test_mcp_serve_boots_with_alembic.py::TestMcpServeBootsWithPreMigratedDb::test_mcp_serve_stdout_has_no_pre_init_noise
  FAILED tests/smoke/test_mcp_serve_boots_with_alembic.py::TestMcpServeBootsWithPreMigratedDb::test_mcp_serve_boots_with_alembicd_db_responds_to_initialize
  FAILED tests/smoke/test_mcp_serve_boots_with_alembic.py::TestMcpServeBootsWithFreshDb::test_fresh_db_boot_responds_to_initialize
  3 failed, 3 passed in 1.09s
  ```
- Exact RED reason: `ModuleNotFoundError: No module named 'mcp'` — the `mcp` optional
  extra is not installed in this environment. The server crashes before producing any
  stdout JSON-RPC output. The 3 stderr/noise tests pass because stderr is non-empty
  (contains the traceback) and stdout has no non-JSON content.
- Status: red — handed off to tdd-coder
- Notes:
  - Surprise from reading `test_mcp_stdio.py`: it uses `mcp.client.stdio.StdioServerParameters`
    and `uv run corpus-forge mcp serve` via an async `stdio_client` session (the MCP SDK's
    high-level client). D-09 deliberately uses raw `subprocess.Popen` + manual JSON-RPC framing
    to stay SDK-independent and to capture raw stdout/stderr bytes (the SDK would swallow stderr).
  - iCloud Drive sync corruption note: `.venv/bin/corpus-forge` entry-point script is broken in
    this environment (`ModuleNotFoundError` even though `python -m corpus_forge.cli` works fine).
    The harness uses `python -m corpus_forge.cli` as the invocation to avoid this. The coder should
    install the `[mcp]` extra (`uv pip install 'corpus-forge[mcp]'`) to make the tests GREEN.
  - Wave gate: ruff format + ruff check both clean on the new test file.

## E-01
- Test files: `tests/smoke/test_satellite_deployment_doc.py`
- Run command: `.venv/bin/python -m pytest tests/smoke/test_satellite_deployment_doc.py -v`
- Edge case checklist:
  - [x] happy — `test_doc_exists` confirms file presence
  - [x] boundaries — N/A (doc existence / substring checks; no numeric boundaries)
  - [x] type / format — regex uses `re.MULTILINE` + `re.escape` to handle special chars in heading names
  - [x] state — N/A (read-only doc; idempotency trivially satisfied)
  - [ ] N/A — concurrency (pure filesystem read, no shared state)
  - [x] failure paths — missing doc surfaces as clear `AssertionError` in `test_doc_exists`; subsequent read-based tests surface as `FileNotFoundError` with explicit path in message
  - [ ] N/A — locale/time (markdown doc, no time/locale sensitivity)
  - [ ] N/A — production-realistic data (doc is the artifact, not a data shape)
  - [ ] N/A — regression hooks (no referenced bug; new surface)
- Red output (tail):
  ```
  FAILED tests/smoke/test_satellite_deployment_doc.py::test_doc_exists - AssertionError: missing .../docs/deployment-satellite.md
  FAILED tests/smoke/test_satellite_deployment_doc.py::test_doc_references_host_id_path - FileNotFoundError: [Errno 2] No such file or directory: '.../docs/deployment-satellite.md'
  FAILED tests/smoke/test_satellite_deployment_doc.py::test_doc_references_sync_status_command - FileNotFoundError
  FAILED tests/smoke/test_satellite_deployment_doc.py::test_doc_has_required_h2_sections - FileNotFoundError
  FAILED tests/smoke/test_satellite_deployment_doc.py::test_doc_references_migrate_command - FileNotFoundError
  5 failed in 0.15s
  ```
- Status: red — handed off to tdd-coder
- Notes:
  - Surprise from `test_claude_integration_doc.py`: it does NOT use `pytestmark = pytest.mark.smoke`
    (it lives under `tests/unit/` and carries no mark). The new test correctly adds `pytestmark =
    pytest.mark.smoke` since it lives under `tests/smoke/`.
  - The existing pattern uses `rf"^##\s+{heading}\b"` (allowing trailing words). E-01 tightens to
    `rf"^## {re.escape(heading)}$"` (exact line match) since the H2 headings are fixed tokens — this
    was an intentional choice to pin heading names precisely and prevent partial matches like
    `## Configure host_id globally`. If the coder wants to allow trailing words they can relax the
    regex; the test comment documents the intent.
  - Wave gate: `make format-check` clean (ruff reformatted the file before commit), `make lint`
    clean, `make typecheck` clean (0 errors).

## E-02
- Test files: `tests/integration/test_two_ingester_one_mcp.py`
- Run command: `.venv/bin/python -m pytest tests/integration/test_two_ingester_one_mcp.py -v`
- Edge case checklist:
  - [x] happy path — 3/3 PASS; both hosts' content reachable; dataset listed
  - [x] boundaries — N/A for chunk count; used 3 chunks per host (above minimum to avoid edge-case false pass)
  - [x] type/format — N/A (pure storage behavior; no type-polymorphic paths)
  - [x] state — module-scoped fixture: fresh schema per module run; no cross-test bleed
  - [ ] N/A — concurrency (sequential ingestion; sync engine not exercised here)
  - [x] failure paths — isError=True on MCP result raises AssertionError with message; mismatched source_uri raises with cross-host leakage note
  - [ ] N/A — locale/time (text content only; no timestamps exercised)
  - [x] production-realistic — chunk texts contain multi-word unique phrases; source_uri uses vault:// scheme matching real markdown vault pattern
  - [x] regression hooks — source_uri host-prefix assertion will fire if a future change makes search_lexical ignore dataset_id scoping or merges host docs
- Green output (tail):
  ```
  tests/integration/test_two_ingester_one_mcp.py::test_search_hits_mac_b_chunks PASSED [ 33%]
  tests/integration/test_two_ingester_one_mcp.py::test_search_hits_mac_a_chunks PASSED [ 66%]
  tests/integration/test_two_ingester_one_mcp.py::test_list_datasets_sees_both_hosts PASSED [100%]
  3 passed in 2.15s
  ```
- Status: GREEN (load-bearing pin) — handed off to tdd-coder
- Notes:
  - host_id does NOT flow through PostgresBackend.__init__ — it is passed explicitly to
    register_source(host=...) and insert_revision(author_host=...) per-operation. There is no
    backend-level host attribute. This is by design.
  - MCP server is fully exercisable in-process via server.request_handlers[CallToolRequest](req)
    — no subprocess required. build_server() wires a retriever_builder callback; the LexicalRetriever
    stub bypasses all ML model loading. asyncio.run() wraps the async handler call.
  - The mcp.Server.call_tool() method is a DECORATOR (registers the handler), NOT a callable for
    dispatching tool calls. Dispatch goes through server.request_handlers[CallToolRequest].
  - list_datasets backend API returns per-dataset rows (not per-host). Multi-host presence is
    verified via a direct SQL query on corpus.sources, which is the correct assertion surface.

## F-01
- Test files: `tests/integration/test_alembic_0006_writes_and_feedback.py`
- Run command: `.venv/bin/python -m pytest tests/integration/test_alembic_0006_writes_and_feedback.py -v`
- Edge case checklist:
  - [x] happy path — 3 tests assert the full post-0006 schema shape (columns, types, nullability, defaults, indexes)
  - [x] boundaries — column set checked as an exact set (no extra, no missing columns asserted for both tables)
  - [x] type/format — data_type values checked exactly ('text', 'bigint', 'boolean', 'jsonb', 'timestamp with time zone', 'integer')
  - [x] state — _reset_schema() in each test for isolation; fresh Alembic upgrade from scratch per test
  - [ ] N/A — concurrency (DDL test; single connection per test)
  - [x] failure paths — CommandError is the expected RED failure; table-absent case caught by empty cols dict assertion
  - [ ] N/A — locale/time (schema DDL test; no locale-sensitive behavior)
  - [x] production-realistic — uses information_schema.columns + pg_indexes (same queries a DBA would run to verify a migration)
  - [ ] N/A — regression hooks (no prior bug; this is a new table)
- Red output (tail):
  ```
  FAILED tests/integration/test_alembic_0006_writes_and_feedback.py::test_mcp_audit_table_shape
  FAILED tests/integration/test_alembic_0006_writes_and_feedback.py::test_description_columns_added
  FAILED tests/integration/test_alembic_0006_writes_and_feedback.py::test_feedback_table_shape
  alembic.util.exc.CommandError: Can't locate revision identified by '0006_writes_and_feedback'
  3 failed, 3 warnings in 1.91s
  ```
- Status: red — handed off to tdd-coder
- Notes:
  - column_default normalization: Postgres returns 'now()' (lowercase) for TIMESTAMPTZ DEFAULT NOW();
    test checks `.lower()` contains 'now' — robust to either case.
  - metadata default: Postgres stores "'{}'::jsonb" literally; test checks both '{}' and 'jsonb'
    appear in the lowercased default string — avoids fragility on exact quoting.
  - dry_run default: Postgres returns 'false' (lowercase); test lowercases before checking — robust.
  - index-naming: index names are deterministic from the DDL (mcp_audit_entity_idx, etc.);
    no surprise — the plan names match what pg_indexes will expose.
  - SQLite parity is intentionally OUT OF SCOPE for this file (column type info unreliable in
    sqlite_master). The F-01 coder adds a SQLite branch in the revision itself.

## F-02
- Test files: `tests/unit/test_backend_write_helpers.py`
- Run command: `uv run pytest tests/unit/test_backend_write_helpers.py -v`
- Edge case checklist:
  - [x] happy — all 9 helpers have a happy-path test
  - [x] boundaries — empty messages list for append_conversation; single-element hit list for hydrate; revoke non-existent; idempotent double-revoke
  - [x] type/format — before/after JSON serialization round-trip (audit_event); confidence REAL storage; NULL client/session_id in audit
  - [x] state — duplicate label reuses label_id (created=False); description cleared on set_to_None; turn_index advances monotonically
  - [x] concurrency — concurrent append_message threads get distinct turn_indexes
  - [ ] N/A — failure paths (unit tests hit in-memory SQLite; disk-full / permission errors are infra-level, not logic-level)
  - [ ] N/A — locale/time (timestamps stored as ISO strings; UTC-only in tests; DST not relevant for append operations)
  - [x] production-realistic — seeded fixture inserts a real dataset/document/chunk/conversation hierarchy matching actual ingest output
  - [ ] N/A — regression hooks (no prior bug referenced by F-02)
- Red output (tail):
  ```
  FAILED tests/unit/test_backend_write_helpers.py::TestAppendMessage::test_turn_index_advances_monotonically
  FAILED tests/unit/test_backend_write_helpers.py::TestAppendMessage::test_happy_path_appends_to_existing_conversation
  FAILED tests/unit/test_backend_write_helpers.py::TestAppendMessage::test_concurrent_appends_get_distinct_turn_indexes
  FAILED tests/unit/test_backend_write_helpers.py::TestAppendMessage::test_optional_fields_stored
  AttributeError: 'SQLiteBackend' object has no attribute 'audit_event'
  AttributeError: 'SQLiteBackend' object has no attribute 'apply_label'
  42 failed, 1 passed, 1 skipped in 0.75s
  ```
- Status: red — handed off to tdd-coder
- Notes:
  - Backend target: SQLite in-memory only. PG-only behaviour (JSONB merge, NOW() defaults,
    BIGSERIAL sequencing) deferred to F-05 integration smoke.
  - Dual-backend fixture decision: the existing `storage_backend` parametrize fixture in conftest
    covers both backends for B-16 tests. F-02 unit tests use SQLite-only for speed and isolation;
    the conftest `storage_backend` fixture could extend these tests to PG in F-05 with no changes
    to the test bodies.
  - audit_event design call: tests pin `audit_event` as a STANDALONE helper (not called internally
    by the other helpers). The F-03 MCP dispatch layer calls it explicitly. This is cleaner for
    unit testing: each helper test doesn't depend on audit_event working, and the Coder can
    implement audit_event separately. Flag to Coder: if you wire audit_event internally, adjust
    the happy-path tests to account for the side-effect.
  - Hit dataclass is frozen (frozen=True) — hydrate_hit_metadata cannot mutate in-place.
    The tests accept either a new list of augmented Hit objects OR dict objects. The Coder
    must decide: return new objects (preferred — respects frozen dataclass) or return dicts.
    Tests are written to handle both patterns via hasattr/isinstance guards.
  - document_labels has NO confidence column (only chunk_labels does). apply_label with
    confidence on a document entity_type should silently ignore confidence or raise — tests
    don't pin this edge case to give Coder flexibility.
  - Parent-rollup (chunk hit inherits document's labels) is marked skip with
    reason="parent-rollup is F-04's concern" — boundary explicitly documented.

## phase-f/F-03
- Test files:
  - `tests/unit/test_mcp_writes_dispatch.py` (44 tests)
  - `tests/smoke/test_mcp_writes_disabled_by_default.py` (3 tests)
- Run command: `uv run python -m pytest tests/unit/test_mcp_writes_dispatch.py tests/smoke/test_mcp_writes_disabled_by_default.py -v`
- Edge case checklist:
  - [x] happy path — every tool has a basic "returns expected keys" test
  - [x] boundaries — dry_run=True on every write tool; empty messages list; None for optional args (rating, text, client, session_id)
  - [x] type/format — invalid entity_type → ValueError; None conversation_id on dry_run; None message_id on dry_run
  - [x] state — before/after dicts tested; idempotent remove (double-revoke → removed=False)
  - [ ] N/A — concurrency (dispatch functions are thin wrappers; SQLiteBackend concurrency tested in F-02)
  - [x] failure paths — invalid entity_type raises; dry_run does not persist across multiple tools
  - [ ] N/A — locale/time (timestamps are passed as strings; no timezone math in dispatch layer)
  - [x] production-realistic data — seeded fixture matches F-02 test pattern exactly
  - [ ] N/A — regression hooks (no known bugs to encode; this is new surface)
  - [x] audit identity — host/client/session_id flow-through test; null client/session accepted
  - [x] list_labels — no audit row emitted (read tool); filter by entity_type and namespace
  - [x] append_conversation — dry_run returns conversation_id=None; audit references real conv_id; turn_indexes sequential
  - [x] append_message — dry_run returns message_id=None; turn_index predicted correctly
  - [x] smoke: writes_enabled=False (explicit kwarg) omits write tools; writes_enabled=True exposes all 11; calling write tool when disabled errors
- Red output (tail):
  ```
  collecting ... collected 0 items / 2 errors
  ERROR tests/unit/test_mcp_writes_dispatch.py
    ImportError: cannot import name 'writes' from 'corpus_forge.mcp'
  ERROR tests/smoke/test_mcp_writes_disabled_by_default.py
    ImportError: cannot import name 'writes' from 'corpus_forge.mcp'
  2 errors during collection
  ```
- Status: red — handed off to tdd-coder

## F-04 — MCP read-side enrichment
- Test files: `tests/integration/test_mcp_read_enrichment.py`
- Run command: `.venv/bin/python -m pytest tests/integration/test_mcp_read_enrichment.py -v`
- Edge case checklist:
  - [x] happy path — search hit and get_chunk both return labels/description/recent_feedback
  - [x] boundaries — recent_feedback bounded to 5 (test with 7 rows); empty feedback list when no rows
  - [x] type/format — label is a dict {namespace, value, source, confidence}; feedback entries are dicts
  - [x] state — enrichment reflects write-tool mutations within same in-process backend
  - [ ] N/A — concurrency (serial in-process MCP calls)
  - [x] failure paths — toggle include_labels/include_description/include_feedback=False omits respective keys
  - [ ] N/A — locale/time (no locale-sensitive paths; ts field present but not asserted on format)
  - [x] production-realistic — SQLite in-memory backend with real migration stack; uses same _call_tool harness as E-02 cross-host smoke
  - [x] regression hooks — test_search_no_n_plus_one patches hydrate_hit_metadata to assert call_count >= 1 and <= 2 (catches both missing wiring AND per-hit N+1)
- Red output (tail):
  ```
  FAILED tests/integration/test_mcp_read_enrichment.py::test_search_hit_includes_labels
  FAILED tests/integration/test_mcp_read_enrichment.py::test_search_hit_recent_feedback_bounded_to_5
  FAILED tests/integration/test_mcp_read_enrichment.py::test_search_include_feedback_false_omits_recent_feedback
  FAILED tests/integration/test_mcp_read_enrichment.py::test_search_hit_includes_recent_feedback
  FAILED tests/integration/test_mcp_read_enrichment.py::test_search_hit_includes_description
  FAILED tests/integration/test_mcp_read_enrichment.py::test_search_chunk_hit_includes_parent_rollup
  FAILED tests/integration/test_mcp_read_enrichment.py::test_get_chunk_includes_enrichment
  FAILED tests/integration/test_mcp_read_enrichment.py::test_search_no_n_plus_one
  FAILED tests/integration/test_mcp_read_enrichment.py::test_search_include_description_false_omits_description
  FAILED tests/integration/test_mcp_read_enrichment.py::test_search_include_labels_false_omits_labels
  10 failed in 0.62s
  ```
- Status: red — handed off to tdd-coder

## phase-f/F-05
- Test files:
  - `tests/integration/test_mcp_writes_postgres.py` (10 tests)
  - `tests/integration/test_append_conversation_e2e.py` (2 tests)
  - `tests/smoke/test_skill_tool_contract.py` (3 tests: 2 new + 1 updated/relaxed)
- Run command: `.venv/bin/python -m pytest tests/integration/test_mcp_writes_postgres.py tests/integration/test_append_conversation_e2e.py tests/smoke/test_skill_tool_contract.py -v`
- Edge case checklist:
  - [x] happy — add_label/set_description/set_metadata/remove_label/add_feedback/append_conversation/append_message round-trips
  - [x] boundaries — dry_run=True no entity mutations; turn indices 0..4; 3-msg vs 6-msg conversations; 0-count audit baseline
  - [ ] N/A — type/format (entity_type validation covered in unit tests)
  - [x] state — fresh PG schema per test (pg_dsn drops+recreates corpus schema); dirty state (add then remove label); post-remove label list is empty
  - [ ] N/A — concurrency (PG advisory locking tested in existing backend unit tests)
  - [x] failure paths — dry_run sentinel ids; isError cascade from ? placeholder bug surfaced RED for set_description/set_metadata/append_message
  - [ ] N/A — locale/time
  - [x] production-realistic data — 6-message conversation with realistic content; unique phrase anchor for cross-host test
  - [x] regression hooks — placeholder mismatch bug (writes.py ? vs PG %s) encoded as 4 RED tests
  - [x] cross-host visibility — explicit pin: Host A writes, Host B (separate backend instance) reads; hits must be visible
  - [x] audit rows — count before vs after each write; dry_run also emits audit row
- Red output (tail):
  ```
  FAILED tests/integration/test_append_conversation_e2e.py::test_live_chat_round_trip
  FAILED tests/integration/test_append_conversation_e2e.py::test_append_conversation_cross_host_visible
  FAILED tests/integration/test_mcp_writes_postgres.py::test_set_description_round_trip_pg
  FAILED tests/integration/test_mcp_writes_postgres.py::test_audit_event_emitted_for_every_write_pg
  FAILED tests/integration/test_mcp_writes_postgres.py::test_set_metadata_round_trip_pg
  FAILED tests/integration/test_mcp_writes_postgres.py::test_append_message_extends_existing_pg
  6 failed, 9 passed in 5.09s
  ```
- Status: red — handed off to tdd-coder
- Notes (bugs surfaced, do NOT fix in this commit):
  1. BUG A — `corpus_forge/mcp/writes.py`: `_read_metadata`, `_read_description`, and
     `_count_messages` use SQLite-style `?` placeholders. psycopg rejects `?`; error is
     "the query has 0 placeholders but 1 parameters were passed". Affects set_description,
     set_metadata, append_message (always calls _count_messages), and the audit cascade.
     Fix: replace `?` with `%s` in those three helpers, OR dispatch through the backend.
  2. BUG B — `append_conversation` writes to `corpus.messages` only, NOT `corpus.chunks`.
     `search_lexical` queries the `text_tsv` GIN index on `corpus.chunks` only. Appended
     messages are never indexed, so live-chat round-trip and cross-host visibility tests
     return 0 hits. Fix: insert per-message chunk rows into corpus.chunks on append, so
     the FTS index covers conversation content.

## G-01
- Test files:
  - `tests/integration/test_alembic_0007_chat_templates.py` (new — 2 tests)
  - `tests/integration/test_apply_migrations_uses_alembic.py` (bumped — 2 assertions)
- Run command: `.venv/bin/python -m pytest tests/integration/test_alembic_0007_chat_templates.py tests/integration/test_apply_migrations_uses_alembic.py -v`
- Edge case checklist:
  - [x] happy — PG: all 8 columns + UNIQUE on name; SQLite: type conventions + datetime default
  - [x] boundaries — nullable vs NOT NULL per column; UNIQUE constraint presence
  - [x] type/format — bigint vs INTEGER PRIMARY KEY; timestamptz vs TEXT; corpus. prefix vs none
  - [x] state — fresh schema reset before PG test; tmp_path isolation for SQLite
  - [ ] N/A — concurrency (DDL migration, single-threaded)
  - [ ] N/A — failure paths (CommandError is the intended RED failure)
  - [ ] N/A — locale/time (migration DDL has no locale-sensitive logic)
  - [x] regression hooks — bumped version_num assertions in test_apply_migrations_uses_alembic.py
- Red output (tail):
  ```
  FAILED tests/integration/test_alembic_0007_chat_templates.py::test_chat_templates_table_shape_pg
  FAILED tests/integration/test_alembic_0007_chat_templates.py::test_chat_templates_table_shape_sqlite
  FAILED tests/integration/test_apply_migrations_uses_alembic.py::test_apply_migrations_creates_alembic_version_table_pg
  FAILED tests/integration/test_apply_migrations_uses_alembic.py::test_apply_migrations_creates_alembic_version_table_sqlite

  test_chat_templates_table_shape_pg:
    alembic.util.exc.CommandError: Can't locate revision identified by '0007_chat_templates'

  test_chat_templates_table_shape_sqlite:
    alembic.util.exc.CommandError: Can't locate revision identified by '0007_chat_templates'

  test_apply_migrations_creates_alembic_version_table_pg:
    AssertionError: Expected version_num='0007_chat_templates', got '0006_writes_and_feedback'.

  test_apply_migrations_creates_alembic_version_table_sqlite:
    AssertionError: Expected version_num='0007_chat_templates', got '0006_writes_and_feedback'.

  4 failed, 1 passed in 1.93s
  ```
- Status: red — handed off to tdd-coder

## phase-g/G-02
- Test files:
  - tests/unit/test_template_registry.py
  - tests/unit/test_template_builtins.py
  - tests/unit/test_template_hf.py
  - tests/unit/test_chat_template_backend_helpers.py
- Run command: uv run pytest tests/unit/test_template_registry.py tests/unit/test_template_builtins.py tests/unit/test_template_hf.py tests/unit/test_chat_template_backend_helpers.py -v
- Edge case checklist:
  - [x] happy — render dispatches to each of 6 builtins; register/list/get on backend
  - [x] boundaries — empty message list per builtin; empty/None template name raises; list from empty table returns []
  - [x] type/format — None chat_template from HF raises a clear error; custom_jinja overrides name; wrong template name raises KeyError/ValueError
  - [x] state — HF cache cleared between tests (monkeypatch + _TEMPLATE_CACHE.clear()); duplicate register is idempotent
  - [ ] N/A — concurrency (pure render functions + single-host SQLite; no shared mutable state under test)
  - [x] failure paths — HF from_pretrained raises OSError (propagates); chat_template=None raises with model name in message; HF_HUB_OFFLINE=1 simulated
  - [ ] N/A — locale/time (no date/locale logic in template rendering)
  - [x] production-realistic data — fixture uses system+user+assistant three-turn shape matching HF dataset conventions
  - [ ] N/A — regression hooks (no prior bug reference for G-02)
- Golden string strategy: hand-rolled substring containment assertions derived from
  each format's canonical spec (not snapshot files). Assertions check format-specific
  control tokens (e.g. <|im_start|>, <|begin_of_text|>, ### Instruction:) rather than
  exact full-string equality, making tests resilient to minor whitespace changes.
- Red output (tail):
  ```
  FAILED tests/unit/test_template_registry.py::TestListBuiltins::test_list_builtins_returns_six_names
    ModuleNotFoundError: No module named 'corpus_forge.templates'

  FAILED tests/unit/test_template_builtins.py::TestBuiltinContracts::test_each_builtin_has_name_jinja_render[chatml]
    ModuleNotFoundError: No module named 'corpus_forge.templates'

  FAILED tests/unit/test_template_hf.py::TestHfTemplateHappyPath::test_hf_template_calls_AutoTokenizer_from_pretrained
    ModuleNotFoundError: No module named 'corpus_forge.templates'

  FAILED tests/unit/test_chat_template_backend_helpers.py::TestRegisterChatTemplate::test_register_creates_row
    AttributeError: 'SQLiteBackend' object has no attribute 'register_chat_template'

  FAILED tests/unit/test_chat_template_backend_helpers.py::TestListChatTemplates::test_list_returns_empty_when_no_templates
    AttributeError: 'SQLiteBackend' object has no attribute 'list_chat_templates'

  FAILED tests/unit/test_chat_template_backend_helpers.py::TestGetChatTemplateByName::test_get_returns_matching_row
    AttributeError: 'SQLiteBackend' object has no attribute 'get_chat_template_by_name'

  115 failed in 0.93s
  ```
- Status: red — handed off to tdd-coder

## phase-g/G-03
- Test files:
  - `tests/unit/test_mcp_templates_dispatch.py`
  - `tests/integration/test_render_conversation_mcp.py`
- Run command: `.venv/bin/python -m pytest tests/unit/test_mcp_templates_dispatch.py tests/integration/test_render_conversation_mcp.py -v --continue-on-collection-errors`
- Edge case checklist:
  - [x] happy — render_conversation with chatml builtin, list_chat_templates, register_template, get_chunk with template
  - [x] boundaries — n_messages=1, empty template list, duplicate template name, nonexistent conversation_id
  - [x] type/format — custom_jinja with Jinja expressions (length filter, role/content access)
  - [x] state — fresh DB (empty list), dirty DB (registered templates), idempotent duplicate register
  - [N/A] concurrency — pure dispatch functions, no concurrent paths
  - [x] failure paths — nonexistent conversation_id raises; dry_run does not persist; document chunk has no templated_text
  - [N/A] locale/time — no timestamp logic in these tools
  - [x] production-realistic data — 3-message conversations with alternating user/assistant roles
  - [x] regression hooks — truncation flag test; HF source row dispatches to hf_template (not jinja); custom source uses stored jinja
- Resolution priority pinned: custom_jinja > model_id (HF fetch) > template_name (DB lookup → builtin fallback)
- HF table row shape pinned: source='huggingface', jinja=NULL, model_id=NOT NULL
- Truncation threshold: test uses >1000 as boundary; coder may choose any threshold ≤1000 (must document)
- Red output (tail):
  ```
  ERROR tests/unit/test_mcp_templates_dispatch.py
    ImportError: cannot import name 'templates' from 'corpus_forge.mcp'

  FAILED tests/integration/test_render_conversation_mcp.py::TestToolsRegistered::test_render_conversation_registered
  FAILED tests/integration/test_render_conversation_mcp.py::TestToolsRegistered::test_list_chat_templates_registered
  FAILED tests/integration/test_render_conversation_mcp.py::TestToolsRegistered::test_register_template_registered
  FAILED tests/integration/test_render_conversation_mcp.py::TestRenderConversationEndToEnd::test_chatml_round_trip_returns_text
    AssertionError: MCP tool 'render_conversation' returned isError=True: unknown tool: 'render_conversation'
  FAILED tests/integration/test_render_conversation_mcp.py::TestRenderConversationEndToEnd::test_render_with_custom_jinja_via_mcp
  FAILED tests/integration/test_render_conversation_mcp.py::TestRenderConversationEndToEnd::test_nonexistent_conversation_returns_error
  FAILED tests/integration/test_render_conversation_mcp.py::TestRegisterThenRender::test_register_custom_then_render
  FAILED tests/integration/test_render_conversation_mcp.py::TestRegisterThenRender::test_dry_run_register_does_not_persist
  FAILED tests/integration/test_render_conversation_mcp.py::TestListChatTemplatesIntegration::test_list_empty_on_fresh_backend
  FAILED tests/integration/test_render_conversation_mcp.py::TestListChatTemplatesIntegration::test_list_includes_registered
  FAILED tests/integration/test_render_conversation_mcp.py::TestListChatTemplatesIntegration::test_list_entries_have_required_fields
  11 failed, 1 error in 0.60s
  ```
- Status: red — handed off to tdd-coder

## phase-g/G-04
- Test files:
  - `tests/unit/test_export_chat_cli.py`
  - `tests/integration/test_export_chat_jsonl.py`
  - `tests/integration/test_export_chat_parquet_hf_compatible.py`
- Run command: `.venv/bin/python -m pytest tests/unit/test_export_chat_cli.py tests/integration/test_export_chat_jsonl.py tests/integration/test_export_chat_parquet_hf_compatible.py -v`
- Edge case checklist:
  - [x] happy path — round-trip JSONL (2 convs x 3 msgs), Parquet load via datasets, dispatch stub called
  - [x] boundaries — empty dataset (0 convs), zero-message conversation skipped, single conv, message_count=4
  - [x] type/format — custom_jinja rendering (messages|length), llama3 template field, conversation_id as int
  - [x] state — fresh backend; dataset not found raises; idempotent dataset name seeding
  - [N/A] concurrency — pure function, no concurrent paths
  - [x] failure paths — unknown dataset raises KeyError/ValueError/LookupError/RuntimeError; missing --dataset or --out rejects CLI
  - [N/A] locale/time — no timestamp logic in export path
  - [x] production-realistic data — SQLite in-memory with real append_conversation seeding, real chatml/llama3 templates
  - [x] regression hooks — "no such command" guard ensures subcommand is wired (not just non-zero exit); HF model_id stub test pins monkeypatch path
- Pinned export_chat signature:
  ```python
  def export_chat(
      dataset: str,
      template: str,
      out_path: Path,
      format: str = "jsonl",
      *,
      backend: SQLiteBackend | None = None,
      model_id: str | None = None,
      custom_jinja: str | None = None,
      push: str | None = None,
  ) -> None: ...
  ```
- Notes:
  - No existing `export` Typer subgroup in `corpus_forge/cli.py`; coder must add one.
  - New module must be `corpus_forge/export.py` (singular, top-level) to match `corpus_forge.export.export_chat`.
  - `corpus_forge/exports/huggingface.py` (existing) is unrelated (SQL view export) — no name conflict.
- Red output (tail):
  ```
  ERROR tests/integration/test_export_chat_jsonl.py
    ModuleNotFoundError: No module named 'corpus_forge.export'

  ERROR tests/integration/test_export_chat_parquet_hf_compatible.py
    ModuleNotFoundError: No module named 'corpus_forge.export'

  FAILED tests/unit/test_export_chat_cli.py::TestExportChatRequiredArgs::test_export_chat_requires_dataset_when_out_given
  FAILED tests/unit/test_export_chat_cli.py::TestExportChatRequiredArgs::test_export_chat_requires_out_when_dataset_given
  FAILED tests/unit/test_export_chat_cli.py::TestExportChatRequiredArgs::test_export_chat_requires_dataset_and_out
  FAILED tests/unit/test_export_chat_cli.py::TestExportChatHelp::test_export_help_lists_chat_subcommand
  FAILED tests/unit/test_export_chat_cli.py::TestExportChatHelp::test_export_chat_help_lists_template_and_dataset
  FAILED tests/unit/test_export_chat_cli.py::TestExportChatDispatch::test_export_chat_dispatches_to_export_module
  FAILED tests/unit/test_export_chat_cli.py::TestExportChatDispatch::test_export_chat_format_parquet_passed_through
  FAILED tests/unit/test_export_chat_cli.py::TestExportChatDispatch::test_export_chat_format_defaults_to_jsonl
  FAILED tests/unit/test_export_chat_cli.py::TestExportChatDispatch::test_export_chat_push_flag_passed_through
  9 failed (unit), 2 collection errors (integration) in ~0.24s
  ```
- Status: red — handed off to tdd-coder

## H-04
- Test files:
  - `tests/unit/test_export_feedback_pairs.py` — 8 unit tests (row count, shape, prompt templating, unlinked-session skip, audit response, feedback response, custom_jinja, no-events empty)
  - `tests/integration/test_self_distillation_export.py` — 2 integration tests (full round-trip, unlinked-session no-error)
  - `tests/unit/test_export_feedback_pairs_cli.py` — 7 CLI tests (dispatch, format default, parquet, help args, subcommand listing, no-such-command guard, missing-args error)
- Run command: `.venv/bin/python -m pytest tests/unit/test_export_feedback_pairs.py tests/integration/test_self_distillation_export.py tests/unit/test_export_feedback_pairs_cli.py -v --continue-on-collection-errors`
- Edge case checklist:
  - [x] happy — `test_export_feedback_pairs_jsonl_emits_one_row_per_event`, `test_full_round_trip_session_writes_export`
  - [x] boundaries — `test_export_feedback_pairs_no_events_writes_empty_file` (zero events → empty file)
  - [x] type/format — `test_export_feedback_pairs_audit_event_response_shape` / `test_export_feedback_pairs_feedback_event_response_shape` (two distinct response shapes)
  - [x] state — `test_export_feedback_pairs_skips_unlinked_sessions` / `test_export_skips_events_when_session_not_linked` (conversation_id IS NULL → skip, no error)
  - [ ] N/A — concurrency (sequential export, no shared state across goroutines)
  - [x] failure paths — unlinked session silently skipped; missing subcommand → non-zero exit; AttributeError on missing function triggers collection errors
  - [ ] N/A — locale/time (timestamps stored as ISO strings; no locale-sensitive formatting in export)
  - [x] production-realistic — uses real SQLiteBackend in-memory + real mcp/writes.py dispatch (no mocks for data path)
  - [x] regression hooks — pins `kind` field values to `{'audit', 'feedback'}` and response schema per kind
- Red output (tail):
  ```
  ERROR tests/unit/test_export_feedback_pairs.py
    ImportError: cannot import name 'export_feedback_pairs' from 'corpus_forge.export'

  ERROR tests/integration/test_self_distillation_export.py
    ImportError: cannot import name 'export_feedback_pairs' from 'corpus_forge.export'

  FAILED tests/unit/test_export_feedback_pairs_cli.py::TestExportFeedbackPairsHelp::test_export_feedback_pairs_no_such_command_not_raised
  FAILED tests/unit/test_export_feedback_pairs_cli.py::TestExportFeedbackPairsHelp::test_export_feedback_pairs_help_lists_args
  FAILED tests/unit/test_export_feedback_pairs_cli.py::TestExportFeedbackPairsHelp::test_export_feedback_pairs_requires_dataset_and_out
  FAILED tests/unit/test_export_feedback_pairs_cli.py::TestExportFeedbackPairsHelp::test_export_help_lists_feedback_pairs_subcommand
  FAILED tests/unit/test_export_feedback_pairs_cli.py::TestExportFeedbackPairsSubcommandDispatch::test_export_feedback_pairs_format_defaults_to_jsonl
  FAILED tests/unit/test_export_feedback_pairs_cli.py::TestExportFeedbackPairsSubcommandDispatch::test_export_feedback_pairs_subcommand_dispatches
  FAILED tests/unit/test_export_feedback_pairs_cli.py::TestExportFeedbackPairsSubcommandDispatch::test_export_feedback_pairs_format_parquet_passed_through
  7 failed, 2 collection errors — 9 total RED
  ```
- Status: red — handed off to tdd-coder

## H-05
- Test files: `tests/integration/test_feedback_loop_e2e.py`
- Run command: `.venv/bin/python -m pytest tests/integration/test_feedback_loop_e2e.py -v`
- Edge case checklist:
  - [x] happy — full e2e loop (append_conversation + add_label + add_feedback → link → export)
  - [x] boundaries — NULL conversation_id asserted before link, populated after
  - [x] state — feedback_events row count checked at each write step (0→1→2→3)
  - [x] failure paths — unlinked session events absent from export (0 rows)
  - [x] production-realistic data — chatml token `<|im_start|>` asserted in every prompt
  - [ ] N/A — type/format (pure pipeline flow; no string parsing or encoding variation)
  - [ ] N/A — concurrency (single-process in-process backend, no async or threading)
  - [ ] N/A — locale/time (timestamps are system-generated; no locale-sensitive paths)
  - [ ] N/A — regression hooks (no specific bug referenced in task)
- Red output (tail):
  ```
  All 3 tests PASSED GREEN on first run — as predicted by the task brief.
  H-01..H-04 were fully wired; H-05 is a load-bearing pin, not a RED->GREEN cycle.

  collected 3 items
  tests/integration/test_feedback_loop_e2e.py::test_export_skips_unlinked_session_events PASSED
  tests/integration/test_feedback_loop_e2e.py::test_session_writes_create_feedback_events PASSED
  tests/integration/test_feedback_loop_e2e.py::test_full_self_distillation_loop PASSED
  3 passed in 0.90s
  ```
- Notes: No production gap found. Session_id propagation works correctly via
  WriteContext passed explicitly -- no env-var ambiguity. export_feedback_pairs
  correctly filters on conversation_id IS NOT NULL in the backend join.
  chatml token confirmed present in all rendered prompts. 373 integration tests
  still GREEN. ruff format/check and pyrefly clean.
- Status: green -- 3/3 passed; H-01..H-04 pipeline confirmed coherent

---

## Phase D — Wave 0 (2026-05-14)

Wave 0 of the multi-format milestone. The Agent tool isn't available in
this environment, so the principal performed the RED→GREEN→QA cycle
serially in-thread (logged under "claimed_by: principal" in the task
table). Each task got real RED runs before code landed.

| id | tests | file |
|----|-------|------|
| D-01 | 21 | `tests/unit/test_extractor_registry.py` |
| D-02 | 19 | `tests/unit/test_code_chunker.py` |
| D-03 | 23 | `tests/unit/test_extractor_passthrough.py` + `tests/unit/test_extractor_plaintext.py` |
| D-04 | 25 | `tests/unit/test_extractor_structured.py` + `tests/unit/test_extractor_subtitle.py` |
| D-05 | 12 | `tests/unit/test_chunker_dispatch.py` |
| D-06 | 10 | `tests/unit/test_config_multi_format.py` |

Total: 110 new unit tests. Coverage 92.19% → 92.33%.

---

## Phase D — Wave 5 (2026-05-14)

Wave 5 of the multi-format milestone. Agent tool unavailable; principal
performed RED→GREEN→QA in-thread, same pattern as Wave 4 (logged under
`claimed_by: principal (no Agent tool)`).

| id | tests | file |
|----|-------|------|
| E-05 | 17 | `tests/unit/test_pdf_extractor_escalation.py` |
| E-06 | 17 | `tests/unit/test_extractor_image.py` |

Total: 34 new unit tests. Coverage 92.48% → 92.35% (delta within Wave 4
noise; the small dip is the defensive `pdf2image-not-installed` /
`pdf2image-error` catch-all branches in `extractors/pdf.py` which fire
only when the [ocr] extra is missing — un-hit on the CI's fully-installed
venv).

RED runs verified for both E-05 (ModuleNotFoundError on
`corpus_forge.extractors.image` and AttributeError on missing
`PdfDigitalExtractor.vlm`) and E-06 (ModuleNotFoundError) before GREEN
code landed.

Test patterns of note:
- `pdf2image` is stubbed via `monkeypatch.setattr("corpus_forge.extractors.pdf.pdf2image", types.ModuleType(...))`
  so the lazy-import inside `_escalate()` resolves to the stub. The stub
  ships a `convert_from_path` MagicMock + an `exceptions` submodule with
  a sentinel `PDFInfoNotInstalledError` class.
- `VLMBackend` is mocked via `unittest.mock.Mock(spec=VLMBackend)` so
  signature drift in the Protocol fails the test loudly.
- Per-page side-effects (`extract_returns=[...]` / `extract_raises=[...]`)
  let one test exercise both successful pages and per-page failure modes
  (the VLMTimeoutError-on-middle-page case).
- The regression guard `test_rag_helper_import_path_preserved` reads
  `corpus_forge/extractors/pdf.py` as source and asserts the
  `from pymupdf4llm.helpers.pymupdf_rag import to_markdown` line is
  present — pins the memory `project_phase_d_pymupdf4llm_rag_helper`.


---

# Phase E P1 — Wave 3 dispatch


---

## O1-T3
- Test files: `tests/unit/test_pyproject_extras_analyze.py`
- Run command: `uv run pytest tests/unit/test_pyproject_extras_analyze.py -x`
- Edge case checklist:
  - [x] happy path — `analyze` key exists and contains all seven packages
  - [x] boundaries — exactly 7 entries (drift gate: no more, no less)
  - [x] type/format — case-insensitive + hyphen/underscore normalisation in dep-name matching
  - [x] state — negative invariant: none of the seven packages appear in core `[project.dependencies]`
  - [ ] N/A — concurrency (pure TOML parse, no concurrency surface)
  - [ ] N/A — failure paths (pyproject.toml is a static repo file; missing file would be a dev environment breakage, not a test dimension)
  - [ ] N/A — locale/time (no date/locale surface)
  - [x] production-realistic data — reads the actual repo-root `pyproject.toml`, same file CI uses
  - [ ] N/A — regression hooks (no prior bug referenced; this is a new feature gate)
  - [x] per-package tests — one dedicated test per required package for clear failure attribution
  - [x] langdetect disambiguation — `test_langdetect_in_analyze` excludes `fasttext-langdetect` matches so the standalone `langdetect` fallback entry is proven independently
- Red output (tail):
  ```
  FAILED tests/unit/test_pyproject_extras_analyze.py::test_analyze_extra_is_a_list
  FAILED tests/unit/test_pyproject_extras_analyze.py::test_scikit_learn_in_analyze
  FAILED tests/unit/test_pyproject_extras_analyze.py::test_fasttext_langdetect_in_analyze
  FAILED tests/unit/test_pyproject_extras_analyze.py::test_analyze_extra_exists
  FAILED tests/unit/test_pyproject_extras_analyze.py::test_umap_learn_in_analyze
  FAILED tests/unit/test_pyproject_extras_analyze.py::test_all_required_packages_present_in_analyze
  FAILED tests/unit/test_pyproject_extras_analyze.py::test_datasketch_in_analyze
  FAILED tests/unit/test_pyproject_extras_analyze.py::test_analyze_extra_has_exactly_seven_entries
  FAILED tests/unit/test_pyproject_extras_analyze.py::test_bertopic_in_analyze
  FAILED tests/unit/test_pyproject_extras_analyze.py::test_langdetect_in_analyze
  FAILED tests/unit/test_pyproject_extras_analyze.py::test_hdbscan_in_analyze
  ========================= 11 failed, 1 passed in 0.17s =========================
  ```
  Root failure: `KeyError: 'analyze'` — key absent from `[project.optional-dependencies]` until O1-G1 GREEN.
  The 1 passing test (`test_analyze_packages_not_in_core_dependencies`) is a negative-invariant gate; it is
  correct that it passes in RED state since none of the ML packages appear in core deps.
- Notes: Package spec list settled as: `scikit-learn`, `hdbscan`, `umap-learn`, `bertopic`, `datasketch`,
  `fasttext-langdetect`, `langdetect`. Phase doc uses `fasttext-langdetect` (not `ft-langdetect`); the test
  matches that name. The coder (O1-G1) must verify the PyPI name via `pip index versions fasttext-langdetect`
  during GREEN and update the test if the canonical name differs.
- Status: red — handed off to tdd-coder (O1-G1)

---

## O1-T2
- Test files: `tests/unit/test_analyze_stats.py`
- Run command: `uv run pytest tests/unit/test_analyze_stats.py -x`
- Edge case checklist:
  - [x] happy — single chunk, 5-element fixture ([10,20,30,40,50]), basic multi-chunk invariants
  - [x] boundaries — empty input (all-zero dict, no exception), single element (p50==p95==min==max), bins=1 through bins=50 via hypothesis
  - [x] type / format — token_count=None fallback, missing token_count key entirely, empty text + None count (expect token_total==0)
  - [x] state — determinism test (two calls with same input return identical dict, no PRNG)
  - [ ] N/A — concurrency (pure function, no shared state)
  - [ ] N/A — failure paths (pure computation, no I/O, no network)
  - [ ] N/A — locale / time (integer arithmetic only)
  - [x] production-realistic data — hypothesis generates up to 200 chunks with token_counts up to 1_000_000; bins varied 1-50
  - [ ] N/A — regression hooks (no prior bug reference; this is a new module)
  - [x] property-based (hypothesis): p50 <= p95 always; mean in [min, max] always; token_total == sum(token_counts) always; distribution counts sum to n always; len(edges)==bins+1 and len(counts)==bins always
- Field-shape ambiguity notes:
  - Phase doc § O1-T2 acceptance (line 59) uses key `"edges"` not `"bin_edges"`. Test asserts `"edges"` and `"counts"` as the canonical keys.
  - p95 for [10,20,30,40,50] tested as range [40,50] inclusive to accommodate both linear-interpolation (=48) and nearest-rank (=50) pure-stdlib implementations. Phase doc says "linear interpolation per numpy convention" but stats.py is pure-stdlib so the coder chooses the approach; the test avoids over-specification.
  - `bin_strategy="log10"` tested for structural invariants only (no specific edge values asserted), consistent with the phase doc mentioning the parameter without specifying exact bucket boundaries.
  - Fallback for None/missing token_count: test verifies no exception and token_total > 0 for non-empty text; does not assert the specific chars-per-token constant. The coder is free to choose any positive divisor.
- Red output (tail):
  ```
  tests/unit/test_analyze_stats.py F

  =================================== FAILURES ===================================
  _______ test_compute_length_distribution_edges_monotonically_increasing ________

      def test_compute_length_distribution_edges_monotonically_increasing() -> None:
          """Bin edges must be strictly increasing."""
  >       from corpus_forge.analyze.stats import compute_length_distribution
  E       ModuleNotFoundError: No module named 'corpus_forge.analyze'

  tests/unit/test_analyze_stats.py:309: ModuleNotFoundError
  =========================== short test summary info ===========================
  FAILED tests/unit/test_analyze_stats.py::test_compute_length_distribution_edges_monotonically_increasing
  !!!!!!!!!!!!!!!!!!!!!!!!!! stopping after 1 failures !!!!!!!!!!!!!!!!!!!!!!!!!!!
  ============================== 1 failed in 0.19s ==============================
  ```
  (27 tests collected; pytest -x stops at the first; all would fail with the same ModuleNotFoundError)
- Status: red — handed off to tdd-coder (O1-G4)

## O1-T1
- Test files: `tests/unit/test_analyze_config.py`
- Run command: `uv run pytest tests/unit/test_analyze_config.py -x`
- Edge case checklist:
  - [x] happy path — defaults match spec; Config.analyze attribute exists; TOML round-trip with all fields
  - [x] boundaries — dedup_threshold at 0.0 and 1.0 (ge/le); topic_min_cluster_size at 2 (ge=2); judge_timeout_s at boundary (gt=0 means 0.0 rejected)
  - [x] type/format — language_detector Literal rejects "spacy", "", "nltk"; judge_endpoint rejects ftp:// and bare string; judge_api_key_env rejects spaces, hyphens, digit-prefix
  - [x] state — omitting [analyze] block entirely yields default AnalyzeConfig (backwards-compat invariant); empty [analyze] block also yields defaults
  - [ ] N/A — concurrency (pure pydantic model construction, no shared state)
  - [ ] N/A — failure paths / network (config is a local pydantic model, no I/O in AnalyzeConfig itself)
  - [ ] N/A — locale/time (no time-sensitive fields in AnalyzeConfig)
  - [x] production-realistic data — TOML round-trip uses both local (localhost:11434) and remote (api.openai.com) judge_endpoint examples from config.example.toml template
  - [ ] N/A — regression hooks (no prior bug referenced for this new model)
- Red output (tail):
  ```
  E       ImportError: cannot import name 'AnalyzeConfig' from 'corpus_forge.config' (corpus_forge/config.py)
  
  tests/unit/test_analyze_config.py:143: ImportError
  =============================== warnings summary ===============================
  tests/unit/test_analyze_config.py::TestAnalyzeConfigValidation::test_judge_api_key_env_spaces_rejected
    corpus_forge/config.py:36: UserWarning: Field name "schema" in "BackendConfig" shadows an attribute in parent "BaseModel"
      class BackendConfig(BaseModel):
  
  -- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
  ========================= short test summary info ============================
  FAILED tests/unit/test_analyze_config.py::TestAnalyzeConfigValidation::test_judge_api_key_env_spaces_rejected
  !!!!!!!!!!!!!!!!!!!!!!!!!! stopping after 1 failures !!!!!!!!!!!!!!!!!!!!!!!!!!!
  ========================= 1 failed, 1 warning in 0.19s =========================
  ```
  Full run: 46 failed, 0 passed. Every failure is `ImportError: cannot import name 'AnalyzeConfig' from 'corpus_forge.config'`.
- Notes: Phase doc specifies `judge_endpoint: AnyHttpUrl = AnyHttpUrl("http://localhost:11434")` (not `str | None = None` as the task description originally suggested). Phase doc wins. `judge_api_key_env` is `str = ""` with allow-empty POSIX validation (mirrors `ClassifierConfig.llm_api_key_env`). `judge_timeout_s` default is `60.0` (not `30.0`). `topic_min_cluster_size` default is `10` with `ge=2`.

## O1-T4
- Test files: `tests/integration/test_migrate_0012_analyze.py`
- Run command: `uv run pytest tests/integration/test_migrate_0012_analyze.py -v`
- Edge case checklist:
  - [x] happy — upgrade produces both tables with correct column set, indexes, and FK wiring (SQLite + Postgres)
  - [x] boundaries — per-column NOT NULL / nullable assertions; REAL vs bigint / INTEGER type assertions per dialect
  - [x] type/format — Postgres: bigint / text / real / timestamp with time zone; SQLite: INTEGER / TEXT / REAL / datetime('now') convention
  - [x] state — fresh DB per test via tmp_path (SQLite) and _reset_pg_schema (Postgres); cross-test isolation guaranteed
  - [x] N/A — concurrency (pure DDL migration, sequential, no shared mutable state)
  - [x] failure paths — all alembic-based tests fail with CommandError (revision not found); module-import tests fail with ModuleNotFoundError
  - [x] N/A — locale/time (computed_at DEFAULT is database-side; no locale-sensitive assertions needed)
  - [x] production-realistic — column shapes drawn from the O1 phase-doc DDL spec verbatim
  - [x] regression hooks — down_revision assertion pins the chain to 0011_image_embeddings; forward-only assertion inspects AST to confirm downgrade() body is a single `pass`
  - [x] FK/cascade — SQLite: PRAGMA foreign_keys=ON + insert chunk + delete chunk → cascade verified. Postgres: information_schema FK query asserts delete_rule=CASCADE for both tables.
- Red output (tail):
  ```
  FAILED tests/integration/test_migrate_0012_analyze.py::TestMigrationModuleAttributes::test_revision_value
  FAILED tests/integration/test_migrate_0012_analyze.py::TestMigrationModuleAttributes::test_down_revision_value
  FAILED tests/integration/test_migrate_0012_analyze.py::TestMigrationModuleAttributes::test_downgrade_is_forward_only_pass
  FAILED tests/integration/test_migrate_0012_analyze.py::TestSQLiteAnalyzeSignals::test_chunk_quality_signals_table_exists
  FAILED tests/integration/test_migrate_0012_analyze.py::TestSQLiteAnalyzeSignals::test_chunk_quality_signals_fk_cascade_on_delete
  [... 40 more, all same CommandError or ModuleNotFoundError ...]

  alembic.util.exc.CommandError: Can't locate revision identified by '0012_analyze_signals'
  ModuleNotFoundError: No module named 'corpus_forge.alembic.versions.0012_analyze_signals'

  45 failed, 40 warnings in 4.68s
  ```
- Notes: signal_value and similarity are REAL (nullable per phase-doc column list; the phase doc says "REAL" not "REAL NOT NULL" — assertion accepts nullable). Both FK tests exercise cascade behavior with PRAGMA foreign_keys=ON (SQLite). The chunks INSERT assumes columns (id, content, content_hash, token_count) — if the chunks schema differs, the FK cascade tests will surface that as a clear error for the Coder to fix.
- Status: red — handed off to tdd-coder

## O2-T1 — exact_duplicates + near_duplicates (corpus_forge.analyze.dedup)
- Test files: `tests/unit/test_analyze_dedup.py` (23 tests)
- Run command: `uv run pytest tests/unit/test_analyze_dedup.py -x 2>&1 | tail -5`
- Edge case checklist:
  - [x] happy path — pair/triple/multi-group exact-dup grouping; identical-text near-dup clustering
  - [x] boundaries — empty input → `{}`/`[]`; singleton chunk → `[]` for near_duplicates; single-hash → excluded (singleton); all-None hashes → `{}`
  - [x] type/format — result dict keys are str (hash); chunk_ids are list[int]; similarity is float in [0,1]; method literal is "minhash_lsh"
  - [x] state — cluster_id stability across two runs with identical input; deterministic sha256 hash in helper
  - [x] N/A — concurrency (pure functions, no shared mutable state)
  - [x] failure paths — None content_hash chunks skipped entirely; None-hash chunks do not pollute real dup groups; `len(chunks) < 2` short-circuits before MinHash
  - [x] N/A — locale/time (no time-dependent behavior in dedup functions)
  - [x] production-realistic — chunk dicts use real sha256 hex hashes (mirrors DB backfill formula in 0002_chunk_content_hash.py); chunk_id values use realistic integer IDs
  - [x] regression hooks — lazy-import guard (datasketch not in sys.modules after module import); threshold/num_perm passthrough accepted without raising
  - [x] hypothesis: identical text always clusters (3-copy, threshold=0.5, 30 examples)
  - [x] hypothesis: disjoint word-sets never cluster at threshold=0.85 (30 examples with assume() disjointness guard)
- Defaults chosen (unspecified in phase doc): `threshold=0.85`, `num_perm=128` — both match the phase doc's "MinHash LSH band/row tuning" note which names these as the starting point.
- Red output (tail):
  ```
  FAILED tests/unit/test_analyze_dedup.py::test_exact_duplicates_all_unique_returns_empty_dict
  E       ModuleNotFoundError: No module named 'corpus_forge.analyze.dedup'

  tests/unit/test_analyze_dedup.py:123: ModuleNotFoundError
  1 failed in 0.19s
  ```
- Status: red — handed off to tdd-coder
- Status: red — handed off to tdd-coder (O1-G2)

## O2-T2 — detect_language + detect_language_batch (corpus_forge.analyze.language)
- Test files: `tests/unit/test_analyze_language.py` (25 tests)
- Run command: `uv run pytest tests/unit/test_analyze_language.py -x 2>&1 | tail -5`
- Edge case checklist:
  - [x] happy path — English text detected as "en"; French text detected as "fr"; both via langdetect (skips cleanly if langdetect absent)
  - [x] boundaries — empty string -> ("und", 0.0); whitespace-only -> ("und", 0.0); batch empty list -> []; batch single-item consistent with single call
  - [x] type/format — return type is tuple (not dict); tuple length is 2; iso_code is non-empty str; confidence is float
  - [x] state — N/A (stateless functions; detector arg fully controls dispatch)
  - [x] N/A — concurrency (pure function, no shared state)
  - [x] failure paths — missing fasttext wheel raises RuntimeError naming the missing dep; mixed-language text does not raise
  - [x] N/A — locale/time (function is locale-agnostic; no time dependency)
  - [x] production-realistic — mock fasttext module injects realistic {"lang": "en", "score": 0.98} return shape matching fasttext_langdetect API; langdetect mock exposes .lang/.prob attributes
  - [x] regression hooks — lazy-import guard (neither fasttext nor langdetect in sys.modules after module import); dispatch isolation (langdetect path never touches fasttext modules and vice versa)
  - [x] hypothesis: confidence in [0.0, 1.0] for any non-empty text (50 examples, skipif langdetect absent)
  - [x] hypothesis: iso_code is always non-empty string for any non-empty text (50 examples, skipif langdetect absent)
- Design decisions pinned:
  - Return type: tuple[str, float] (NOT dict)
  - ISO codes: 2-letter ("en", "fr" etc.)
  - Empty/whitespace input: ("und", 0.0) — graceful, not ValueError
  - detector=None: reads Config.load().analyze.language_detector (tested via mock of Config.load)
  - Batch: list of tuple[str, float], same order as inputs
- Red output (tail):
  ```
  SKIPPED [1] tests/unit/test_analyze_language.py:551: langdetect not installed
  SKIPPED [1] tests/unit/test_analyze_language.py:371: langdetect not installed; cannot test dispatch isolation
  FAILED tests/unit/test_analyze_language.py::test_fasttext_dispatch_does_not_import_langdetect_via_mock
  FAILED tests/unit/test_analyze_language.py::test_import_detect_language - ModuleNotFoundError: No module named 'corpus_forge.analyze.language'
  7 failed, 18 skipped in 0.21s
  ```
- Status: red — handed off to tdd-coder

## O2-T4
- Test files: `tests/integration/test_analyze_dedup_persist.py`
- Run command: `uv run pytest tests/integration/test_analyze_dedup_persist.py -x`
- Edge case checklist:
  - [x] happy path — single cluster 2 members → 2 rows; three clusters mixed sizes → 9 rows; return value matches DB count
  - [x] boundaries — empty cluster list → 0 rows; single-member cluster (size 1 implicit via mixed test); similarity round-trip precision (1e-6 tolerance for SQLite double, 1e-5 for Postgres REAL which is 4-byte float32)
  - [x] type/format — invalid cluster shape missing cluster_id raises (KeyError/ValueError/TypeError); invalid cluster missing chunk_ids raises; both covered
  - [x] state — fresh DB per test via tmp_path (SQLite) and _reset_pg_schema (Postgres); idempotent re-run: first call returns 3, second returns 0, total stays 3 (INSERT OR IGNORE / ON CONFLICT DO NOTHING)
  - [ ] N/A — concurrency (persist_clusters is a synchronous single-connection write; concurrent-write is out of scope for O2)
  - [x] failure paths — FK violation path: verified via cascade delete (insert chunk + cluster rows, delete chunk, assert cluster rows gone)
  - [ ] N/A — locale/time (computed_at is server-side DEFAULT; no locale-sensitive assertions; just assert non-NULL)
  - [x] production-realistic data — cluster shapes match the exact dict shape produced by near_duplicates() from O2-T1 (cluster_id, chunk_ids, similarity, method); seeds use the proven chunk INSERT pattern (id, chunk_index, text, content_hash, token_count)
  - [ ] N/A — regression hooks (no prior bug referenced; this is new functionality)
  - [x] method override — function-level method kwarg overrides any method field inside the cluster dict; separate test pins this contract
  - [x] per-cluster similarity — different clusters store their own similarity values correctly
  - [x] default method kwarg — calling without method= uses "minhash_lsh" as the default
- Red output (tail):
  ```
  FAILED tests/integration/test_analyze_dedup_persist.py::TestPersistClustersSQLite::test_empty_cluster_list_returns_zero
  FAILED tests/integration/test_analyze_dedup_persist.py::TestPersistClustersSQLite::test_three_clusters_mixed_sizes_correct_total

        from corpus_forge.analyze.dedup import persist_clusters  # noqa: PLC0415
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  E     ModuleNotFoundError: No module named 'corpus_forge.analyze.dedup'

  tests/integration/test_analyze_dedup_persist.py:119: ModuleNotFoundError
  19 failed in 1.79s
  ```
- Notes: Idempotency strategy pinned as INSERT OR IGNORE (SQLite) / ON CONFLICT DO NOTHING (Postgres) — the coder must implement accordingly and add a UNIQUE constraint on (cluster_id, chunk_id) to the near_duplicate_clusters table (or use a unique index). The migration 0012 did NOT include this unique constraint; the coder must decide whether to add it in the persist_clusters implementation via a unique index created at runtime or to rely on the existing cluster_idx. Flagging for tdd-coder: a (cluster_id, chunk_id) unique index is required for idempotency to work correctly. Postgres FK insert for _pg_seed_chunks uses ON CONFLICT (id) DO NOTHING — this requires a PK constraint on chunks.id (guaranteed by existing migrations).
- Status: red — handed off to tdd-coder (O2-G4)

## O2-T3
- Test files: `tests/unit/test_analyze_drift.py`
- Run command: `uv run pytest tests/unit/test_analyze_drift.py -x`
- Edge case checklist:
  - [x] happy — identical token-length distributions (KS≈0), identical embeddings (JS=0), default methods runs both tests
  - [x] boundaries — single-element arrays (covered by disjoint test which uses 10-element non-overlapping ranges), empty-a, empty-b, both-empty → no exception, stats None, n reflects zero
  - [x] type/format — ks_token_length returns tuple[float, float] (type checked); js_embedding_centroid returns float (type checked); compare_distributions KS sub-keys are float
  - [x] state — N/A — pure functions, no mutable state; idempotency implicit (same input → same output, no PRNG)
  - [ ] N/A — concurrency (pure stateless functions, no shared mutable state)
  - [x] failure paths — empty input on either or both sides (no exception, graceful None); one side missing embeddings → JS=None; both sides missing embeddings → JS=None
  - [ ] N/A — locale/time (no date/time or string encoding concerns in numeric drift computation)
  - [x] production-realistic data — chunk dicts match the shape used by _iter_curation_candidates (token_count int key; optional embedding list[float]); embedding vectors use realistic probability-simplex-style values
  - [x] regression hooks — N/A (new module, no prior bug referenced); disjoint KS=1.0 test is a precise numerical contract that would catch any future regression in the KS implementation
  - [x] lazy-import guard — asserts scipy, numpy, sklearn are NOT in sys.modules after `import corpus_forge.analyze.drift`
  - [x] methods filter — ["ks"] skips JS (js_embedding_centroid=None); ["js"] skips KS (ks_token_length=None); None default runs both
  - [x] JS bounded — js_embedding_centroid result ∈ [0.0, 1.0]; standalone bounded by ln(2) via max-divergence one-hot test
  - [x] hypothesis properties — KS statistic ∈ [0,1] for any two non-empty int arrays (150 examples); JS divergence ∈ [0, ln(2)] for any two positive distributions padded to equal dimension (100 examples)
- Red output (tail):
  ```
  FAILED tests/unit/test_analyze_drift.py::test_js_embedding_centroid_bounded_above_ln2
  FAILED tests/unit/test_analyze_drift.py::test_ks_token_length_identical_arrays_statistic_zero
  FAILED tests/unit/test_analyze_drift.py::test_ks_token_length_disjoint_arrays_statistic_one
  FAILED tests/unit/test_analyze_drift.py::test_ks_token_length_returns_tuple_of_two_floats
  FAILED tests/unit/test_analyze_drift.py::test_js_embedding_centroid_identical_distributions_zero
  FAILED tests/unit/test_analyze_drift.py::test_js_embedding_centroid_returns_float
  FAILED tests/unit/test_analyze_drift.py::test_js_embedding_centroid_bounded_above_ln2
  FAILED tests/unit/test_analyze_drift.py::test_property_ks_statistic_in_zero_one
  FAILED tests/unit/test_analyze_drift.py::test_property_js_divergence_in_zero_ln2
  28 failed in 1.85s
  ```
- Status: red — handed off to tdd-coder

## O3-T1
- Test files: `tests/unit/test_analyze_topics.py`
- Run command: `uv run pytest tests/unit/test_analyze_topics.py -x 2>&1 | tail -5`
- Edge case checklist:
  - [x] happy — identical embeddings → 1 cluster; 3 well-separated clusters → n_clusters=3
  - [x] boundaries — empty list → all-zero result; single embedding → n_clusters=0 noise_count=1; n < min_cluster_size → all noise
  - [x] type/format — cluster_assignments is list[int]; each assignment >= -1; top_terms is dict[int, list[tuple[str, float]]]; n_clusters and noise_count are int
  - [x] state — N/A (pure function, no mutable state between calls)
  - [ ] N/A — concurrency (pure function, no concurrency surface)
  - [x] failure paths — empty input; single point (too small for any cluster); below min_cluster_size (all forced to noise)
  - [ ] N/A — locale/time (numeric embeddings only; no string encoding or time surface)
  - [x] production-realistic data — 4D synthetic embeddings matching realistic embedding dimensionality patterns; text fixture uses natural English words for top_terms
  - [x] regression hooks — noise_count vs -1 label count consistency test catches any mismatch between the summary field and the raw assignments; method='bertopic' fallback test pins _fell_back=True contract
  - [x] lazy-import guard — asserts bertopic, hdbscan, umap, sklearn NOT in sys.modules after `import corpus_forge.analyze.topics`
  - [x] method fallback — method='bertopic' with unavailable bertopic must set _fell_back=True and method='hdbscan' in result
  - [x] top_terms noise skip — cluster -1 excluded from top_terms_per_cluster output
  - [x] top_n limit — top_n=3 returns at most 3 terms; default top_n=10 returns at most 10 terms
  - [x] hypothesis properties — len(cluster_assignments)==len(embeddings) for any input size (30 examples); noise_count matches count of -1 labels (30 examples)
- Red output (tail):
  ```
  tests/unit/test_analyze_topics.py:447: ModuleNotFoundError
  =========================== short test summary info ============================
  FAILED tests/unit/test_analyze_topics.py::test_top_terms_per_cluster_empty_inputs
  !!!!!!!!!!!!!!!!!!!!!!! stopping after 1 failures !!!!!!!!!!!!!!!!!!!!!!!!!!!!!
  1 failed in 0.17s
  ```
- Status: red — handed off to tdd-coder

## O3-T2
- Test files: `tests/unit/test_analyze_quality.py`, `tests/integration/test_analyze_quality_persist.py`
- Run command: `uv run pytest tests/unit/test_analyze_quality.py tests/integration/test_analyze_quality_persist.py -x`
- Edge case checklist:
  - [x] happy — score_chunk_quality returns float in [0,1] for normal chunk
  - [x] boundaries — empty text (0.0), whitespace-only (0.0), short <100 chars (<=0.3), long >5000 chars (<=0.7), empty batch ([])
  - [x] type/format — chunk dict with/without classifier_label, with/without metadata, token_count=0
  - [x] state — determinism (same input same output), batch matches individual calls, idempotent persist re-run
  - [ ] N/A — concurrency (pure function; persist is synchronous single-connection)
  - [x] failure paths — model_path missing/non-existent falls back to heuristic (no exception), model output >1.0 clamped
  - [ ] N/A — locale/time (computed_at is server-side default; no locale-sensitive logic in scorer)
  - [x] production-realistic — well_formed_chunk fixture matches real corpus shape; persist fixtures use same seed pattern as dedup_persist integration tests
  - [x] regression hooks — hypothesis property (score always finite in [0,1] for any text/token_count/metadata); partial idempotency test pins exact row counts
- Red output (tail):
  ```
  tests/unit/test_analyze_quality.py:496: ModuleNotFoundError
  =========================== short test summary info ============================
  FAILED tests/unit/test_analyze_quality.py::test_score_chunks_batch_matches_individual_scores
  !!!!!!!!!!!!!!!!!!!!!!!!!! stopping after 1 failures !!!!!!!!!!!!!!!!!!!!!!!!!!
  1 failed in 0.17s
  ```
- Status: red — handed off to tdd-coder

## O3-T3
- Test files:
  - `tests/unit/test_selector_learned_signal.py` (34 tests: 23 FAIL, 11 PASS)
  - `tests/fixtures/curation/selector_baseline.pickle` (golden output pickle — pre-O3 main @ 0.1.0b6)
  - `tests/fixtures/curation/regenerate_baseline.py` (regenerator script)
- Run command: `uv run pytest tests/unit/test_selector_learned_signal.py -x`
- Edge case checklist:
  - [x] happy — Mode 1 legacy (empty table → 4-weight, None LQ); Mode 2 single chunk with lq=0.9 formula verified; batch with mixed modes
  - [x] boundaries — LQ=0.0 still activates 5-weight formula (not None); LQ=1.0 saturated (score clamped to [0,1]); absent key in row dict treated as None
  - [x] type/format — ScoreBreakdown.learned_quality: float | None = None (frozen dataclass field); _Candidate.learned_quality: float | None = None; _SCORE_WEIGHTS_4 and _SCORE_WEIGHTS_5 type dict[str, float]
  - [x] state — deterministic ordering asserted (3 identical calls yield same batch order); golden baseline pickle pins pre-O3 byte-identical output
  - [ ] N/A — concurrency (pure-function selector, no shared mutable state)
  - [x] failure paths — empty corpus still returns None (no regression); absent learned_quality key in row dict falls back to legacy 4-weight
  - [ ] N/A — locale/time (modified_at timestamps are fixed UTC; no locale-sensitive paths in selector)
  - [x] production-realistic data — 20-chunk fixture corpus with realistic confidence/age/metadata variation (4 source docs, 5 chunks each, cycles of conf/heading/desc/lang/age)
  - [x] regression hooks — golden baseline pickle encodes current (pre-O3) batch.targets order, per-target scores/breakdowns/reasons, cohesion; 6 pinned regression tests each assert byte-identical fields
  - [x] backward-compat — SCORE_WEIGHTS still importable and equals _SCORE_WEIGHTS_4; next_curation_target signature unchanged
- Red output (tail):
  ```
  FAILED tests/unit/test_selector_learned_signal.py::TestMode1LegacyFourWeightScheme::test_learned_quality_is_none_in_empty_table_mode
  !!!!!!!!!!!!!!!!!!!!!!!!!! stopping after 1 failures !!!!!!!!!!!!!!!!!!!!!!!!!!
  ========================= 1 failed, 1 passed in 0.17s =========================
  ```
  Full run: 23 failed, 11 passed. All failures: AttributeError: 'ScoreBreakdown' object has no attribute 'learned_quality' or AssertionError: _SCORE_WEIGHTS_4/_SCORE_WEIGHTS_5 not found on selector module.
- Notes: Baseline pickle generated from pre-O3 main (0.1.0b6) using `uv run python tests/fixtures/curation/regenerate_baseline.py`. Batch order pinned: [1, 3, 2, 4, 5]. Single target: chunk_id=1, score=0.569642. Cohesion: 0.997121.
- Status: red — handed off to tdd-coder

## O4-T1
- Test files: `tests/cli/test_analyze_cli.py`
- Run command: `uv run pytest tests/cli/test_analyze_cli.py -v`
- Edge case checklist:
  - [x] happy — each subcommand exits 0 on demo dataset (6 parametrized tests)
  - [x] boundaries — empty/missing dataset exits nonzero + names dataset; --limit=3 accepted by all 6 subs
  - [x] type/format — --json flag on stats emits parseable JSON dict, not markdown
  - [x] state — report dir creation is idempotent (two back-to-back runs both succeed)
  - [x] N/A — concurrency (CLI dispatch is sequential; no shared mutable state across invocations)
  - [x] failure paths — missing dataset exits non-zero; dataset name echoed in output
  - [x] N/A — locale/time (timestamp-based report dirs are implementation detail; not pinned)
  - [x] production-realistic — seeded SQLite DB with 6 chunk rows including an exact duplicate pair
  - [x] regression hooks — test_analyze_is_registered_as_app_subgroup pins the app.add_typer wiring in cli.py
- Red output (tail):
  ```
  FAILED tests/cli/test_analyze_cli.py::test_analyze_help_lists_six_subcommands
  FAILED tests/cli/test_analyze_cli.py::test_analyze_is_registered_as_app_subgroup
  FAILED tests/cli/test_analyze_cli.py::test_stats_writes_markdown_report
  FAILED tests/cli/test_analyze_cli.py::test_quality_persists_rows_to_chunk_quality_signals
  ... (26 more)
  Core failure: No such command 'analyze'. / AttributeError: module 'corpus_forge' has no attribute 'cli_analyze'
  30 failed, 0 passed in 0.46s
  ```
- Status: red — handed off to tdd-coder

## O4-T2
- Test files: `tests/integration/test_mcp_analyze_tools.py` (43 tests)
- Run command: `uv run pytest tests/integration/test_mcp_analyze_tools.py -x`
- Edge case checklist:
  - [x] happy — analyze_corpus returns n_chunks/token_stats/n_documents; find_duplicates returns exact+near keys; cluster_topics returns clusters with cluster_id/chunk_ids/top_terms; score_quality returns scores in [0,1]
  - [x] boundaries — empty dataset returns n_chunks=0 (no exception); empty chunk_ids list returns empty scores map; single demo chunk exercised via DEMO_CHUNKS fixture
  - [x] type/format — JSON-serializable output asserted on all four tools (json.dumps must not raise TypeError for numpy arrays or non-serializable objects)
  - [x] state — idempotency: analyze_corpus + score_quality(persist=False) yield identical results on second call with same arguments
  - [ ] N/A — concurrency (all four tools are pure read functions with no shared mutable state; MCP dispatch is sequential)
  - [x] failure paths — missing dataset returns isError=True error payload (not a crash); persist=True without writes_enabled returns clear error; ModuleNotFoundError on _dispatch_analyze confirmed as the correct RED failure reason for dispatch tests
  - [ ] N/A — locale/time (no timestamp or locale-sensitive paths in these tools; computed_at on persist rows is backend-managed)
  - [x] production-realistic data — DEMO_CHUNKS fixture has 5 chunks with realistic token counts, content hashes (including an intentional exact-dup pair), classifier labels, and metadata; mirrors the shape corpus_forge.analyze functions actually consume
  - [x] regression hooks — persist=True requires writes_enabled gate test; read-only contract (all 4 callable with writes_enabled=False) encoded as a dedicated TestReadOnlyContract class; tool names encoded as module-level constants to catch typo drift
- Red output (tail):
  ```
  tests/integration/test_mcp_analyze_tools.py:816: ModuleNotFoundError
  =========================== short test summary info ============================
  FAILED tests/integration/test_mcp_analyze_tools.py::TestIdempotency::test_score_quality_idempotent_no_persist
  !!!!!!!!!!!!!!!!!!!!!!!!!! stopping after 1 failures !!!!!!!!!!!!!!!!!!!!!!!!!!
  1 failed in 0.30s
  ```
  Full run: 43 failed, 0 passed. Registration tests: AssertionError (tool name absent from list_tools() set). Dispatch/idempotency tests: ModuleNotFoundError: No module named 'corpus_forge.mcp._dispatch_analyze'.
- Status: red — handed off to tdd-coder

## P2-T1
- Test files: `tests/integration/test_mcp_rate_search_result.py`
- Run command: `uv run pytest tests/integration/test_mcp_rate_search_result.py -m 'not requires_docker' -x 2>&1 | tail -5`
- Edge case checklist:
  - [x] happy — existing session + valid chunk inserts event row + audit row; returns {event_id, session_id}
  - [x] boundaries — value=None accepted (signal-only events); replacement_chunk_id omitted → NULL in DB; two consecutive identical-signal calls both recorded (event log, not state machine)
  - [x] type/format — JSON-serialisable output asserted; source string preserved verbatim including slashes
  - [x] state — auto-create session when query_id is new; retroactive query text; returned session_id matches DB row; idempotency N/A (event log explicitly allows duplicates)
  - [ ] N/A — concurrency (MCP dispatch is sequential; no shared mutable state across tool calls)
  - [x] failure paths — writes_enabled=False blocks tool registration + call (unknown tool error); invalid chunk_id returns isError=True
  - [ ] N/A — locale/time (created_at is DB-managed DEFAULT; not pinned in tests)
  - [x] production-realistic data — seeded SQLite DB with real dataset/document/chunk FK chain; source strings with slashes (reranker model paths); signal names matching the plan (relevance, click, thumbs_up, thumbs_down, curation_suggestion)
  - [ ] N/A — regression hooks (no prior bug referenced; new tool with no production history yet)
- Red output (tail):
  ```
  WARNING  mcp.server.lowlevel.server:server.py:488 Tool 'rate_search_result' not listed, no validation will be performed
  =========================== short test summary info ============================
  FAILED tests/integration/test_mcp_rate_search_result.py::TestAutoCreateSession::test_autocreated_session_query_text_is_retroactive
  !!!!!!!!!!!!!!!!!!!!!!!!!! stopping after 1 failures !!!!!!!!!!!!!!!!!!!!!!!!!!
  ============================== 1 failed in 0.42s
  ```
  Full run: 15 failed, 4 passed. Key failures: AssertionError 'rate_search_result' not in list_tools(writes_enabled=True); all dispatch tests fail with isError=True "unknown tool: 'rate_search_result'". 4 passing tests cover already-working behavior: tool absent from writes_disabled list, write-gate error returned, invalid-chunk-id returns error (semantic correct, currently via "unknown tool" path).
- Notes: The `test_unknown_chunk_id_returns_error` test currently passes because the tool isn't registered (any call → "unknown tool" → isError=True). Post-implementation it should still pass via the FK-violation path. The semantic assertion (`isError=True` for bad FK) is correct regardless; this is acceptable RED per project convention. The `_MCPContext` dataclass is imported but unused in this test file (write dispatchers in server.py create their own context from env vars). Removed the unused import in the final linted version.
- Status: red — handed off to tdd-coder

## P2-T2
- Test files: `tests/unit/test_reranker_learned.py`
- Run command: `uv run pytest tests/unit/test_reranker_learned.py -x`
- Edge case checklist:
  - [x] happy — `TestTrainRerankerWritesFile` (balanced 3-pos+3-neg events → joblib written, auc in [0,1], counts correct)
  - [x] boundaries — empty events table → ValueError; single-class imbalanced data (5 pos / 0 neg + 5 neg / 0 pos) handled gracefully; top_n=None returns all; top_n clips to 2 of 5
  - [x] type/format — return dict n_train/n_pos/n_neg are ints; auc is float; model_path is str; feature spec pins [chunk_score, lexical_score, query_len]
  - [x] state — constructor does NOT load model; first rerank loads once and memoises; idempotent: same model + same input → same ordered output + same scores
  - [ ] N/A — concurrency (pure function, no shared mutable state in trained model path)
  - [x] failure paths — empty events table raises ValueError with descriptive message; no file written on error; empty hits returns [] without touching joblib
  - [ ] N/A — locale/time (no locale-sensitive text handling; timestamps are DB-managed)
  - [x] production-realistic data — _sqlite_conn_with_events uses in-memory DB matching the 0013_search_sessions schema exactly (search_sessions + search_result_events); _minimal_trained_model_path produces a real sklearn LogisticRegression artifact loaded by joblib
  - [x] regression hooks — lazy-import guard (sklearn + joblib) encoded as TestLazyImportGuard; protocol conformance encoded as TestLearnedRerankerProtocol; neutral events skipped test pins the label-derivation contract; source_filter test pins filtering behavior
- Hypothesis property: `test_property_predicted_score_in_zero_one` — for any (chunk_score ∈ [0,1], query_len ∈ [1,200], chunk_id ∈ [1,10000]) the predicted score is a finite float in [0.0, 1.0]. 50 examples, deadline=5000ms.
- Red output (tail):
  ```
  tests/unit/test_reranker_learned.py:653: ModuleNotFoundError
  =========================== short test summary info ============================
  FAILED tests/unit/test_reranker_learned.py::TestLearnedRerankerProtocol::test_has_model_id_attribute
  !!!!!!!!!!!!!!!!!!!!!!!!!! stopping after 1 failures !!!!!!!!!!!!!!!!!!!!!!!!!!!
  1 failed in 0.18s
  ```
  Full run (no -x): 31 failed, 0 passed. All failures: ModuleNotFoundError: No module named 'corpus_forge.retrieval.rerank.learned'.
- Status: red — handed off to tdd-coder

## P3-T2
- Test files: `tests/unit/test_cag_hybrid_selector.py`
- Run command: `uv run pytest tests/unit/test_cag_hybrid_selector.py -x`
- Edge case checklist:
  - [x] happy — cache hit returns `("cache", payload)` and rag miss returns `("rag", SearchResponse)`
  - [x] boundaries — empty cache directory always routes rag; root=None accepted at construction
  - [x] type/format — payload is verbatim parsed JSON (no `_cf_route` injection); SearchResponse isinstance check on miss
  - [x] state — hit/miss matrix: 3 cache files present, each independently hits; non-matching query misses; stateful HybridCagSelector reuses retriever across calls
  - [ ] N/A — concurrency (pure function + file read, no shared mutable state)
  - [x] failure paths — cache miss routes to retriever; empty-dir guard; root override: same query hits in custom root, misses in empty root
  - [ ] N/A — locale/time (cache key is SHA-256 of JSON-encoded strings, locale-invariant)
  - [x] production-realistic data — SearchResponse built with real Hit objects using corpus_forge.retrieval.types; mock retriever returns canned SearchResponse
  - [x] regression hooks — `test_cache_payload_has_no_injected_cf_route_key` pins that `_cf_route` is NOT added; `test_hit_miss_matrix_exactly_one_hit` pins the three-file × one-match matrix
- Red output (tail):
  ```
  tests/unit/test_cag_hybrid_selector.py:253: ModuleNotFoundError
  =========================== short test summary info ============================
  FAILED tests/unit/test_cag_hybrid_selector.py::test_hit_miss_matrix_exactly_one_hit
  !!!!!!!!!!!!!!!!!!!!!!!!!! stopping after 1 failures !!!!!!!!!!!!!!!!!!!!!!!!!!!
  1 failed in 0.17s
  ```
  Full run (no -x): 22 failed, 0 passed. All failures: ModuleNotFoundError: No module named 'corpus_forge.cag'.
- Status: red — handed off to tdd-coder

## P3-T1
- Test files: `tests/unit/test_cag_cache_builder.py`
- Run command: `uv run pytest tests/unit/test_cag_cache_builder.py -x`
- Edge case checklist:
  - [x] happy — `test_build_cache_writes_json_file`, `test_cache_key_deterministic`, `test_cache_path_resolves_correctly`, `test_list_cached_keys_returns_keys_after_build`, `test_invalidate_removes_matching_file`
  - [x] boundaries — `test_cache_key_empty_hashes` (empty hash list), `test_list_cached_keys_returns_empty_before_build` (missing dir), `test_invalidate_noop_empty_dataset_directory` (absent dir), `test_invalidate_noop_when_hash_not_present` (no match)
  - [x] type/format — `test_cache_key_is_16_hex_chars` (length + charset), `test_build_cache_json_built_at_is_iso_timestamp` (parseable ISO 8601), `test_build_cache_returns_path_object` (pathlib.Path return)
  - [x] state — `test_build_cache_uses_root_override` (root= override vs default path), `test_list_cached_keys_ignores_non_json_files` (non-.json files skipped), `test_invalidate_removes_only_matching_files` (partial invalidation)
  - [ ] N/A — concurrency (single-threaded cache file writer, no shared mutable state)
  - [x] failure paths — `test_invalidate_noop_empty_dataset_directory`, `test_list_cached_keys_returns_empty_before_build` (graceful absent-dir handling)
  - [ ] N/A — locale/time (built_at is an ISO timestamp asserted parseable; no DST/locale sensitivity)
  - [x] production-realistic — synthetic chunks via `_make_chunk` + `_make_conn` mock; patch targets `_fetch_chunks` and `_render_template` at the module boundary so tests never touch a real DB or model
  - [x] regression hooks — `test_cache_key_matches_sha256_formula` pins the exact sha256 formula; `test_build_cache_json_cache_key_field_matches_formula` pins the key written inside the JSON matches the public function
- Red output (tail):
  ```
  FAILED tests/unit/test_cag_cache_builder.py::test_import_smoke - ModuleNotFoundError: No module named 'corpus_forge.cag'
  !!!!!!!!!!!!!!!!!!!!!!!!!! stopping after 1 failures !!!!!!!!!!!!!!!!!!!!!!!!!!!
  1 failed in 0.17s

  Full run (no -x): 30 failed, 0 passed.
  All failures: ModuleNotFoundError: No module named 'corpus_forge.cag'
  ```
- Status: red — handed off to tdd-coder

## P3-T3
- Test files: `tests/unit/test_cag_invalidation.py`
- Run command: `uv run pytest tests/unit/test_cag_invalidation.py -x 2>&1 | tail -5`
- Edge case checklist:
  - [x] happy — `test_invalidate_removes_cache_file`: file deleted and returns 1; `test_cache_file_removed_after_commit_curation`: end-to-end via MCP harness
  - [x] boundaries — `test_invalidate_no_file_present_returns_zero` (no file), `test_invalidate_missing_cache_root_returns_zero` (missing root), `test_returns_zero_when_chunk_has_no_content_hash` (NULL hash x2)
  - [x] type/format — `test_invalidate_only_removes_matching_hash`: only the targeted content_hash is removed, not others; `test_only_target_chunk_cache_removed`: cross-chunk isolation
  - [x] state — `test_no_cache_files_commit_curation_succeeds`: no files present, spy still called; `test_audit_row_exists_after_commit_regardless_of_invalidation`: audit_ids populated even when invalidation raises
  - [ ] N/A — concurrency (single-chunk serial dispatch; no shared mutable state)
  - [x] failure paths — `test_invalidate_permission_error_returns_zero`: PermissionError returns 0 + logs warning; `test_missing_cache_root_commit_curation_succeeds`: absent root non-fatal; `test_permission_error_during_invalidation_does_not_fail_commit`: exception in invalidate_for_chunk does not fail commit_curation
  - [ ] N/A — locale/time (cache key is content_hash hex string, locale-invariant)
  - [x] production-realistic — in-memory SQLiteBackend migrated + seeded; in-process MCP server via `build_server`; cache files written at real `<tmp_path>/cag/<dataset>/<hash>.json` paths
  - [x] regression hooks — `test_audit_row_exists_after_commit_regardless_of_invalidation` pins that audit_ids is never empty regardless of CAG failure; `test_only_target_chunk_cache_removed` pins cross-chunk isolation
- Red output (tail):
  ```
  tests/unit/test_cag_invalidation.py:39: in <module>
      from corpus_forge.cag.cache import invalidate, invalidate_for_chunk  # type: ignore[import]
      ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  E   ModuleNotFoundError: No module named 'corpus_forge.cag'
  =========================== short test summary info ============================
  ERROR tests/unit/test_cag_invalidation.py
  !!!!!!!!!!!!!!!!!!!!!!!!!! stopping after 1 failures !!!!!!!!!!!!!!!!!!!!!!!!!!!
  !!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
  =============================== 1 error in 0.16s ===============================
  ```
- Status: red — handed off to tdd-coder

## P4-T1+T2 (combined: eval rag + eval cag)
- Test files:
  - `tests/integration/test_eval_rag.py` (30 tests — 29 RED, 4 pass correctly on boundary tests, 2 skip)
  - `tests/integration/test_eval_cag.py` (19 tests — 14 RED, 4 pass correctly on boundary tests, 1 skip)
- Run command: `uv run pytest tests/integration/test_eval_rag.py tests/integration/test_eval_cag.py -x`
- Edge case checklist:
  - [x] happy — mock judge exits 0, produces parseable JSON, all required keys present (both files)
  - [x] boundaries — missing queries file exits nonzero and names the bad path; empty report dir created on first run
  - [x] type/format — all judge scores asserted to be floats in [0.0, 1.0]; delta may be negative (float check, not bound check)
  - [x] state — determinism: two consecutive mock runs produce byte-identical JSON (pins temperature=0 contract)
  - [N/A] concurrency — CliRunner is sequential; CAG selector is pure-function, no shared mutable state
  - [x] failure paths — missing --queries file exits non-zero; real Ollama endpoint unreachable → no unhandled traceback
  - [N/A] locale/time — report timestamps are filesystem-derived; no locale assertions needed
  - [x] production-realistic — fixture uses the real `corpus_forge.cag.selector._derive_key` SHA-256 formula to seed cache files; two JSONL queries cover both cache-hit and cache-miss branches
  - [x] regression hooks — `test_eval_rag_mock_judge_deterministic` and `test_eval_cag_mock_judge_deterministic` pin the temperature=0 / hash-of-prompt semantic so score drift across CI runs is caught immediately; `test_judge_mock_score_returns_required_keys` pins the four required judge dimensions
- Red output (tail):
  ```
  FAILED tests/integration/test_eval_rag.py::test_eval_rag_raw_prompts_persisted
  FAILED tests/integration/test_eval_cag.py::test_eval_cag_json_contains_cache_hit_count
  FAILED tests/integration/test_eval_cag.py::test_eval_cag_mock_judge_produces_json
  FAILED tests/integration/test_eval_rag.py::test_judge_mock_score_importable - ModuleNotFoundError: No module named 'corpus_forge.eval.judge_mock'
  FAILED tests/integration/test_eval_cag.py::test_eval_cag_help_exits_zero - AssertionError: No such command 'cag'.
  43 failed, 4 passed, 2 skipped in 0.87s
  ```
- Status: red — handed off to tdd-coder

## Q1-T1 — 0014_sdft_demonstrations migration schema (SQLite + Postgres)
- Test files:
  - `tests/integration/test_migrate_0014_sdft.py` (new — 24 SQLite + 3 module-attr + 12 Postgres @requires_docker = 39 total)
- Run command: `uv run pytest tests/integration/test_migrate_0014_sdft.py -m 'not requires_docker' -x`
- Edge case checklist:
  - [x] happy — table exists, all columns present, FK cascade, JSON round-trip, full-fields insert
  - [x] boundaries — trace_id NULL when omitted, content_hash UNIQUE enforced (IntegrityError), ON CONFLICT DO NOTHING dedup
  - [x] type/format — SQLite TEXT for messages (JSON string); Postgres jsonb; created_at server-default
  - [x] state — isolated tmp_path DB per test; idempotency via INSERT OR IGNORE tested
  - [N/A] concurrency — pure DDL migration test, sequential per-test isolation
  - [x] failure paths — ModuleNotFoundError on import; alembic CommandError for unknown revision; IntegrityError on dup hash
  - [N/A] locale/time — created_at DEFAULT (datetime('now')) is DB-side
  - [x] production-realistic — seeds minimal datasets rows; JSON payloads are real role/content dicts
  - [x] regression hooks — down_revision pinned to 0013_search_sessions; AST check on downgrade() body
- Red output (tail):
  ```
  FAILED tests/integration/test_migrate_0014_sdft.py::TestMigrationModuleAttributes::test_revision_value
  FAILED tests/integration/test_migrate_0014_sdft.py::TestMigrationModuleAttributes::test_downgrade_is_forward_only_pass
  FAILED tests/integration/test_migrate_0014_sdft.py::TestMigrationModuleAttributes::test_down_revision_value
  24 failed, 14 deselected, 20 warnings in 0.27s
  E  alembic.util.exc.CommandError: Can't locate revision identified by '0014_sdft_demonstrations'
  E  ModuleNotFoundError: No module named 'corpus_forge.alembic.versions.0014_sdft_demonstrations'
  ```
- Status: red — handed off to tdd-coder

## Q1-T2 — MCP record_demonstration write tool integration tests
- Test files:
  - `tests/integration/test_mcp_record_demonstration.py` (new — 29 tests; 23 fail, 6 pass at boundary conditions)
- Run command: `uv run pytest tests/integration/test_mcp_record_demonstration.py -x`
- Edge case checklist:
  - [x] happy — returns {demonstration_id: int, deduped: bool}, deduped=False, DB row persisted, audit row written, JSON-serialisable
  - [x] boundaries — dedup: second identical call returns same id + deduped=True, count stays 1, audit still written; different query = 2 rows
  - [x] type/format — list[dict] messages; trace_id=None→NULL; source must match SDFTSource enum
  - [x] state — fresh migrated backend per test; idempotent dedup across 5 tests; parametrized over all 8 valid sources
  - [N/A] concurrency — in-process MCP harness, sequential asyncio.run
  - [x] failure paths — write gate; unknown dataset isError; invalid source isError; SDFTSource not importable = ModuleNotFoundError
  - [N/A] locale/time — created_at is DB-side default
  - [x] production-realistic — real SQLiteBackend(:memory:) migrated through full schema; 8-source parametrize
  - [x] regression hooks — SDFTSource enum importability; source taxonomy completeness (8 values)
- Red output (tail):
  ```
  FAILED tests/integration/test_mcp_record_demonstration.py::TestToolRegistration::test_tool_registered_when_writes_enabled
  FAILED tests/integration/test_mcp_record_demonstration.py::TestSourceTaxonomy::test_sdft_source_enum_importable
  FAILED tests/integration/test_mcp_record_demonstration.py::TestTraceIdRoundTrip::test_trace_id_stored_in_db
  23 failed, 6 passed in 0.73s
  E  AssertionError: Expected 'record_demonstration' in tool list with writes_enabled=True
  E  ModuleNotFoundError: No module named 'corpus_forge.sdft'
  ```
- Status: red — handed off to tdd-coder

## Q1-T3 — SDFT capture hooks unit tests (commit_curation + rate_search_result)
- Test files:
  - `tests/unit/test_sdft_capture_hooks.py` (new — 12 tests; all 12 fail)
- Run command: `uv run pytest tests/unit/test_sdft_capture_hooks.py -x`
- Edge case checklist:
  - [x] happy — description-changing commit writes SDFT row with correct source/target/query/student_messages; thumbs_down+replacement writes SDFT row with replacement text as target
  - [x] boundaries — label-only commit no SDFT row; metadata-only no SDFT row; thumbs_down without replacement no SDFT row; thumbs_up with replacement no SDFT row
  - [x] type/format — student_messages verified list with assistant role; query derived from chunk text first 200 chars; source field verbatim
  - [x] state — fresh migrated backend per test; sdft_demonstrations count delta asserted
  - [N/A] concurrency — in-process sequential asyncio.run dispatch
  - [x] failure paths — OperationalError "no such table: sdft_demonstrations" is root RED cause
  - [N/A] locale/time — no locale-sensitive assertions
  - [x] production-realistic — seeds realistic physics text corpus; uses real commit_curation and rate_search_result dispatch paths
  - [x] regression hooks — negative tests pin hook must not fire spuriously
- Red output (tail):
  ```
  FAILED tests/unit/test_sdft_capture_hooks.py::TestCommitCurationSdftHook::test_description_change_writes_sdft_row
  FAILED tests/unit/test_sdft_capture_hooks.py::TestRateSearchResultSdftHook::test_thumbs_down_without_replacement_does_not_write_sdft_row
  FAILED tests/unit/test_sdft_capture_hooks.py::TestRateSearchResultSdftHook::test_thumbs_up_with_replacement_does_not_write_sdft_row
  12 failed in 0.56s
  E  sqlite3.OperationalError: no such table: sdft_demonstrations
  ```
- Status: red — handed off to tdd-coder

## Q2-T1
- Test files:
  - `tests/unit/test_skill_packs_present.py` (45 FAIL / 25 PASS — 70 total)
  - `tests/unit/test_sdft_source_taxonomy.py` (11 FAIL / 22 PASS — 33 total)
  - `tests/integration/test_skill_provenance.py` (14 PASS — characterization tests for already-shipped Q1-G1 behavior)
- Run command: `uv run pytest tests/unit/test_skill_packs_present.py tests/unit/test_sdft_source_taxonomy.py tests/integration/test_skill_provenance.py -v 2>&1 | tail -60`
- Edge case checklist:
  - [x] happy path — file existence + content pattern + MCP dispatch provenance
  - [x] boundaries — each file checked individually; TOML parseable check; exactly 8 enum values (no more, no fewer)
  - [x] type/format — TOML parse via tomllib; regex heading search for record_demonstration section; StrEnum str() round-trip
  - [x] state — characterization tests for existing dispatch (test_skill_provenance.py passes green on existing Q1-G1 code)
  - [ ] N/A — concurrency (file checks are pure I/O; MCP dispatch is sequential in-process)
  - [x] failure paths — is_chat_client with unrecognised value returns False; missing files fail assertively
  - [ ] N/A — locale/time (no date/time assertions)
  - [x] production-realistic — paths anchored at real repo root via Path(__file__).parents[2]; real SQLiteBackend(:memory:) in integration tests
  - [x] regression hooks — SKILL.md existence (already passes, pin it stays); SDFTSource cardinality (pin exactly 8, no drift); provenance round-trip (characterize Q1-G1 dispatch)
- Red output (tail):
  ```
  FAILED tests/unit/test_sdft_source_taxonomy.py::TestIsChatClient::test_is_chat_client_accepts_enum_member
  FAILED tests/unit/test_sdft_source_taxonomy.py::TestIsChatClient::test_chat_client_sources_return_true[claude_code]
  FAILED tests/unit/test_sdft_source_taxonomy.py::TestIsChatClient::test_capture_sources_return_false[curation_commit]
  FAILED tests/unit/test_sdft_source_taxonomy.py::TestIsChatClient::test_is_chat_client_accepts_string
  FAILED tests/unit/test_sdft_source_taxonomy.py::TestIsChatClient::test_is_chat_client_unknown_value_returns_false
  FAILED tests/unit/test_skill_packs_present.py::TestSkillPacksExist::test_gemini_toml_exists
  FAILED tests/unit/test_skill_packs_present.py::TestSkillPacksExist::test_gemini_prompt_md_exists
  FAILED tests/unit/test_skill_packs_present.py::TestSkillPacksExist::test_opencode_command_md_exists
  FAILED tests/unit/test_skill_packs_present.py::TestSkillPacksExist::test_codex_agent_md_exists
  FAILED tests/unit/test_skill_packs_present.py::TestSkillPacksExist::test_skill_packs_doc_exists
  FAILED tests/unit/test_skill_packs_present.py::TestClaudeSkillRecordDemonstrationSection::test_claude_skill_has_record_demonstration_section
  E  AttributeError: type object 'SDFTSource' has no attribute 'is_chat_client'
  E  FileNotFoundError: No such file or directory: '.../.gemini/extensions/corpus-curate.toml'
  45 failed, 25 passed (test_skill_packs_present) | 11 failed, 22 passed (test_sdft_source_taxonomy)
  ```
- Notes:
  - test_skill_provenance.py passes 14/14 GREEN — this is correct. `record_demonstration` + full provenance was shipped in Q1-G1. These are characterization tests that confirm existing behavior and will stay green as the coder ships skill packs.
  - test_skill_packs_present.py: 25 tests that PASS are expected passing boundary conditions — SKILL.md already exists; `commit_curation` and `add_feedback` are already in the existing SKILL.md; SDFTSource import + cardinality + string round-trip all pass (shipped Q1-G1).
  - Coder must: (1) add `is_chat_client()` classmethod to SDFTSource; (2) create `.gemini/extensions/corpus-curate.toml` + `.gemini/extensions/corpus-curate/PROMPT.md`; (3) create `opencode/commands/corpus-curate.md`; (4) create `codex/agents/corpus-curate.md`; (5) create `docs/skill_packs.md`; (6) extend `.claude/skills/corpus-curate/SKILL.md` with record_demonstration + rate_search_result sections.
- Status: red — handed off to tdd-coder

## Q3-T1
- Test files:
  - `tests/cli/test_feedback_ui_navigation.py`
  - `tests/cli/test_feedback_ui_capture.py`
  - `tests/cli/test_feedback_ui_resume.py`
  - `tests/cli/test_feedback_ui_dry_run.py`
- Run command: `uv run pytest tests/cli/test_feedback_ui_navigation.py tests/cli/test_feedback_ui_capture.py tests/cli/test_feedback_ui_resume.py tests/cli/test_feedback_ui_dry_run.py`
- Edge case checklist:
  - [x] happy — `--action quit` exits 0, single `--record-demo` writes one row
  - [x] boundaries — empty session dir for `list-sessions`, repeated `--record-demo` for multiple rows
  - [x] type / format — pipe-separated `q|s|t|target` parsing, session ID format in file path
  - [x] state — session JSON persisted with position after skip actions; idempotency of `--dry-run`
  - [ ] N/A — concurrency (CLI headless mode is single-threaded; no concurrent state)
  - [x] failure paths — resume NONEXISTENT exits nonzero with error message, export-session on missing session exits nonzero
  - [ ] N/A — locale / time (session IDs use ISO timestamps but no DST/locale logic to test here)
  - [x] production-realistic data — pipe-separated demo records match the scripted form spec; SQLite schema matches real 0014_sdft_demonstrations DDL
  - [x] regression hooks — guards added to prevent accidentally-passing `!= 0` tests when `feedback` command is absent (added "No such command 'feedback'" not-in-combined assertion)
- Red output (tail):
  ```
  FAILED tests/cli/test_feedback_ui_dry_run.py::test_dry_run_exits_zero - Asser...
  FAILED tests/cli/test_feedback_ui_dry_run.py::test_export_session_produces_jsonl_file
  FAILED tests/cli/test_feedback_ui_dry_run.py::test_export_session_jsonl_contains_query_field
  FAILED tests/cli/test_feedback_ui_dry_run.py::test_export_session_nonexistent_session_exits_nonzero
  ======================== 30 failed, 1 warning in 0.66s =========================
  ```
- Notes:
  - Coder must create `corpus_forge/cli_feedback.py` exposing `feedback_app = typer.Typer(...)` with subcommands `start`, `resume`, `list-sessions`, `export-session`, and wire it into `corpus_forge/cli.py` via `app.add_typer(feedback_app, name="feedback")`.
  - `start` needs flags: `--dataset`, `--no-tui`, `--action` (repeatable, values: approve|skip|next|prev|quit), `--record-demo` (repeatable, pipe-separated `q|s|t|target`), `--dry-run`.
  - Session state persisted at `$CORPUS_FORGE_FEEDBACK_DIR/session-<id>.json` with fields: `session_id`, `dataset`, `started_at`, `queue_strategy`, `position`, `processed_chunk_ids[]`, `pending_writes[]`.
  - `--record-demo` writes to `sdft_demonstrations` via `corpus_forge.sdft.capture.record_demonstration` with `source="cli_feedback"`.
  - `--dry-run` must not write any rows or session files; must print preview output containing "dry", "would", "preview", or "no-op".
  - `export-session` reads from the session JSON's `pending_writes` list and emits JSONL (one JSON object per line).
  - Typer CliRunner in this project does NOT support `mix_stderr=False` (typer 0.21.2); use plain `CliRunner()`.
- Status: red — handed off to tdd-coder

## Q4-T1
- Test files:
  - `tests/unit/export/test_export_sdft.py` (36 tests — all RED via ImportError)
  - `tests/unit/export/test_export_sdft_template_resolution.py` (8 tests — all RED via ImportError)
  - `tests/unit/export/test_chat_export_unchanged.py` (8 tests — all GREEN as characterisation baseline)
  - `tests/unit/export/test_sdft_no_inference.py` (10 tests — 9 GREEN safety net, 1 RED confirming export_sdft absent)
  - `tests/fixtures/export/export_chat_baseline.jsonl` (generated baseline committed)
  - `tests/fixtures/export/export_feedback_pairs_baseline.jsonl` (generated baseline committed)
- Run command: `uv run pytest tests/unit/export/ --tb=short 2>&1 | tail -30`
- Edge case checklist:
  - [x] happy — basic JSONL export, basic parquet export, single/multi row
  - [x] boundaries — empty dataset (zero rows), 20-row dataset for split tests, empty include_sources list
  - [x] type / format — wrong dataset name raises ValueError/KeyError, custom Jinja string accepted, unknown template name raises
  - [x] state — deterministic held_out split (same content_hash bucket every run), two consecutive runs produce identical splits, no split when held_out_fraction=0
  - [ ] N/A — concurrency (single-writer JSONL/parquet, no concurrent write surface)
  - [x] failure paths — unknown dataset raises, unknown template name raises KeyError/ValueError
  - [ ] N/A — locale / time (no date arithmetic in export path; timestamp normalisation in golden tests covers SQLite vs ISO-8601 formats)
  - [x] production-realistic data — fixture matches real sdft_demonstrations DDL; messages are dicts with role/content matching capture.py schema
  - [x] regression hooks — golden-file tests pin export_chat and export_feedback_pairs schemas; test_export_sdft_not_yet_importable confirms RED state
- Red output (tail):
  ```
  ERROR tests/unit/export/test_export_sdft.py
  ERROR tests/unit/export/test_export_sdft_template_resolution.py
  ImportError: cannot import name 'export_sdft' from 'corpus_forge.export'
  FAILED tests/unit/export/test_sdft_no_inference.py::TestExportSdftImportFails::test_export_sdft_not_yet_importable
  AssertionError: export_sdft is not yet defined in corpus_forge.export
  2 errors during collection, 1 failed, 17 passed
  ```
- Notes:
  - Coder must add export_sdft() to corpus_forge/export.py adjacent to export_feedback_pairs (line 104).
  - Return dict: {"row_count": int, "train_count": int, "held_out_count": int, "out_paths": list[str]}.
  - Split files: .train.jsonl / .held_out.jsonl (or .parquet equivalents); single file uses out_path unchanged.
  - Deterministic split: derive bucket from row content_hash mod (no random seed).
  - Template resolution: reuse corpus_forge.templates.resolve_template + render, exactly as export_chat lines 49-66.
  - Do NOT modify export_chat or export_feedback_pairs schemas.
- Status: red — handed off to tdd-coder

## Q5-T1
- Test files:
  - `tests/integration/test_eval_distill.py` (28 tests, 26 failing + 2 passing vacuously on "exit != 0")
  - `tests/unit/test_skill_pack_consistency.py` (33 tests, all passing — retrofit characterization of shipped Q2 packs)
- Run command: `uv run pytest tests/integration/test_eval_distill.py tests/unit/test_skill_pack_consistency.py -x 2>&1 | tail -5`
- Edge case checklist:
  - [x] happy — populated SDFT set yields coverage>0, source_mix non-empty, fidelity n_rendered_ok==n_rows
  - [x] boundaries — empty SDFT table: coverage=0, all source_mix zeros, n_rows=0
  - [x] type/format — all 8 SDFTSource values zero-filled in source_mix even when absent; token_stats all 5 keys present
  - [x] state — idempotency: deterministic JSON across two runs; seeded_backend fixture fresh per test
  - [x] failure paths — missing dataset exits non-zero (and mentions dataset name)
  - [N/A] concurrency — pure read-only metric computation, no concurrent paths
  - [N/A] locale/time — timestamp fields excluded from determinism assertion via generated_at pop
  - [x] production-realistic — seeded with real SQLiteBackend.upsert_document + record_demonstration; 5 chunks + 5 SDFT rows (3 record_demonstration, 2 claude_code)
  - [x] regression hooks — skill pack consistency tests lock in the current correct state of all 4 client packs; any drift to tool names or source values turns tests RED
- Red output (tail):
  ```
  INFO     corpus_forge.cli:cli.py:167 agent_mode=human (signal=)
  === short test summary info ===
  FAILED tests/integration/test_eval_distill.py::TestEvalDistillHelp::test_help_exits_zero
  AssertionError: Expected exit 0 for --help; got 2
    | No such command 'distill'.
  26 failed, 35 passed in 1.60s
  ```
- Notes:
  - `test_skill_pack_consistency.py` passes green today (retrofit characterization of Q2-G1 deliverables). It goes RED if any pack drifts. This is intentional per the plan's risk mitigation note.
  - The 2 "missing dataset" tests in `test_eval_distill.py` pass today because `exit_code != 0` is satisfied by "No such command 'distill'". They will continue to pass after GREEN when the command exists and handles missing datasets properly — so they are correct regression guards.
  - `_invoke_with_backend` patches `corpus_forge.eval.distill._build_backend` and `_get_backend` — Coder should use one of these names for the backend factory.
  - Template fidelity check uses `chatml` as the default template (builtin); `--template chatml` override is also tested.
- Status: red — handed off to tdd-coder

## CW1-T1 / CW2-T1 / CW3-T1
- Test files:
  - `tests/unit/test_walker_concurrent.py` (new, 30 tests)
  - `tests/unit/test_scan_config_workers.py` (new, 20 tests)
  - `tests/perf/test_scan_concurrency_bench.py` (new, 3 tests)
  - `tests/unit/test_walker.py` (updated: replaced test_workers_greater_than_one_raises)
- Run command: `uv run pytest tests/unit/test_walker_concurrent.py tests/unit/test_scan_config_workers.py tests/unit/test_walker.py -q`
- Perf bench (slow): `uv run pytest tests/perf/test_scan_concurrency_bench.py -m slow -q`
- Edge case checklist:
  - [x] happy -- file-set parity workers=4 and workers=8 on nested fixture tree
  - [x] boundaries -- workers=1 serial path no regression; workers=8 wide tree (20 dirs x 15 files); deep tree (4 levels)
  - [x] type/format -- N/A pure filesystem/concurrency feature
  - [x] state -- WalkStats thread-safe counter correctness; no double-count; idempotent file-set
  - [x] concurrency -- the entire test_walker_concurrent.py is concurrency coverage; perf bench measures speedup
  - [x] failure paths -- N/A (NotImplementedError is the current failure mode)
  - [x] locale/time -- N/A pure filesystem
  - [x] production-realistic data -- deep tree (3 branches x 3 x 2 x 2 x 5 files), wide tree (20 dirs x 15 files), nested layout with 4 levels
  - [x] regression hooks -- test_workers_greater_than_one_does_not_raise replaces the old NotImplementedError pin
- Contract ambiguity resolved:
  - Resolver function name pinned: `resolve_effective_workers(config_workers: int | None) -> int` in `corpus_forge.scanner.walker`. Coder MUST use this exact name and signature.
  - Auto/unset sentinel: `config_workers=None` triggers formula `min(32, (os.cpu_count() or 1) * 4)`.
  - CF_SCAN_WORKERS env always wins over config_workers (including None).
  - ScanConfig.workers docstring still says "NotImplementedError" -- coder must update it.
  - `test_scan_config_workers_gt_1_accepted` currently passes (Pydantic ge=1 already accepts 4). Only resolver tests and walk() tests are RED.
- Red output (tail):
  ```
  FAILED tests/unit/test_scan_config_workers.py::test_effective_default_resolver_is_importable
  FAILED tests/unit/test_scan_config_workers.py::test_cf_scan_workers_env_1_is_serial
  FAILED tests/unit/test_scan_config_workers.py::test_cf_scan_workers_env_wins_over_config
  FAILED tests/unit/test_walker_concurrent.py::test_file_set_parity_workers_8_wide_tree
  FAILED tests/unit/test_walker_concurrent.py::test_follow_symlinks_false_skips_symlinked_dir_concurrent
  FAILED tests/unit/test_walker_concurrent.py::test_walk_stats_no_double_count_under_concurrency
  FAILED tests/unit/test_walker_concurrent.py::test_ignore_prune_venv_concurrent
  FAILED tests/unit/test_walker_concurrent.py::test_file_set_parity_workers_vs_serial[8]
  FAILED tests/unit/test_walker_concurrent.py::test_walk_stats_counts_identical_serial_vs_concurrent
  FAILED tests/unit/test_walker.py::test_workers_greater_than_one_does_not_raise
  31 failed, 23 passed in 0.31s
  ```
- Perf bench:
  ```
  FAILED tests/perf/test_scan_concurrency_bench.py::test_concurrent_walk_file_set_parity_no_sleep
  FAILED tests/perf/test_scan_concurrency_bench.py::test_concurrent_walk_faster_than_serial_with_artificial_latency
  2 failed, 1 passed in 3.69s
  ```
- Status: red -- handed off to tdd-coder

## SR-T2
- Test files:
  - `tests/unit/test_backend_abc_ingest_runs.py`
  - `tests/integration/test_postgres_ingest_runs.py`
- Run command: `uv run pytest tests/unit/test_backend_abc_ingest_runs.py tests/integration/test_postgres_ingest_runs.py -q`
- Edge case checklist:
  - [x] happy — create/read/update/delete for each of the 7 new methods
  - [x] boundaries — empty table returns None; single row; ordering among multiple rows
  - [x] type / format — status enum values (all 3 terminal states parametrized); delta=0 OK; finished=True vs False
  - [x] state — idempotency on second start_ingest_run (resume path flips status back to 'running'); upsert_ingest_run_source second call updates not duplicates; deltas accumulate
  - [x] concurrency — N/A (pure sequential CRUD; concurrent-lock tests are SR-T5's scope)
  - [x] failure paths — update_ingest_run swallows psycopg.OperationalError + logs at DEBUG
  - [x] locale / time — UTC-aware timestamps asserted on started_at and find_source_last_scanned_at result; last_progress_at advances on second update
  - [x] production-realistic data — multi-run source tracking; FK cascade on ingest_runs delete
  - [x] regression hooks — find_source_last_scanned_at returns max across runs (not just current); partial update does not null out unchanged fields
  - [x] Protocol shape — 7 method existence tests + 7 signature tests + IngestRunInProgressError class existence and Exception subclass
- Red output (tail):
  ```
  FAILED tests/unit/test_backend_abc_ingest_runs.py::TestProtocolMethodExistence::test_update_ingest_run_exists
  FAILED tests/unit/test_backend_abc_ingest_runs.py::TestProtocolMethodExistence::test_latest_ingest_run_exists
  FAILED tests/unit/test_backend_abc_ingest_runs.py::TestIngestRunInProgressErrorExists::test_exception_importable_from_backends
  FAILED tests/unit/test_backend_abc_ingest_runs.py::TestIngestRunInProgressErrorExists::test_exception_is_exception_subclass
  62 failed in 6.79s

  Failure reason for integration tests:
  AttributeError: 'PostgresBackend' object has no attribute 'start_ingest_run'

  Failure reason for unit tests:
  AssertionError: StorageBackend.start_ingest_run not found — add Protocol stub (SR-G2)
  ```
- Status: red — handed off to tdd-coder

## SR-T4
- Test files: `tests/unit/test_ingest_stop_controller.py`
- Run command: `uv run pytest tests/unit/test_ingest_stop_controller.py -q`
- Edge case checklist:
  - [x] happy path -- install/restore round-trip on normal block exit; context-manager __enter__/__exit__
  - [x] boundaries -- restore without prior install (no-op guard); install twice (idempotent); fresh controller independent of a previously-signalled one
  - [x] type/format -- _handle_signal accepts frame=None and a real frame object
  - [x] state -- stop_requested stays True after restore_handlers(); nested context managers restore outer handler; multiple instances share no state
  - [x] concurrency -- non-main-thread install_handlers() is a no-op (no ValueError, flag stays False); main-thread handler not displaced by worker-thread call
  - [x] failure paths -- restore_handlers() called in finally on exception path; first signal MUST NOT call os._exit or sys.exit
  - [ ] N/A -- locale/time (pure signal/bool logic, no timestamps or encodings)
  - [x] production-realistic data -- os.kill(SIGINT/SIGTERM) full dispatch path tested (not just direct _handle_signal invocation)
  - [x] regression hooks -- second SIGINT escalation to os._exit(130) has its own named test; repeated SIGTERM does NOT escalate (spec asymmetry pinned as a named test)
- Notes:
  - tasks.md listed surface as test_ingest_signal_handling.py; renamed to test_ingest_stop_controller.py per user instruction. tasks.md updated accordingly.
  - test_sigterm_then_sigint_escalates deliberately accepts both SIGINT-only counter and all-signal-counter implementations; ambiguity is documented in the test body and left for SR-G4 coder to resolve.
- Red output (tail):
  ```
  ERROR tests/unit/test_ingest_stop_controller.py
  ImportError while importing test module '...tests/unit/test_ingest_stop_controller.py'.
  tests/unit/test_ingest_stop_controller.py:37: in <module>
      from corpus_forge.ingest import _StopController  # type: ignore[attr-defined]
  E   ImportError: cannot import name '_StopController' from 'corpus_forge.ingest'
  !!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
  1 error in 2.22s
  ```
- Status: red -- handed off to tdd-coder

## SR-T8
- Test files: `tests/unit/test_cli_ingest_status.py` (new, 51 tests)
- Run command: `uv run pytest tests/unit/test_cli_ingest_status.py -q`
- Edge case checklist:
  - [x] happy -- completed run renders two-section table with run_id/status/host/pid/timestamps/progress/per-source rows
  - [x] boundaries -- last_done=0/last_total=0 (no ZeroDivisionError); last_total=None (no crash); no source rows (no crash)
  - [x] type/format -- null ended_at renders as em-dash, not bare 'None'; ISO timestamps present; progress pct '25.0%' for 50/200
  - [x] state -- running (no ended_at), interrupted (INTERRUPTED + --resume hint), failed (error text shown), completed (error field absent)
  - [ ] N/A -- concurrency (pure renderer + CLI dispatch, no concurrent paths)
  - [x] failure paths -- DB connect failure exits 1 + stderr message; migrate() raise does not kill --status (patched)
  - [ ] N/A -- locale/time (ISO strings from DB row passed through; no time-zone conversion in renderer)
  - [x] production-realistic data -- run dicts shaped exactly to the corpus.ingest_runs schema columns from tasks.md
  - [x] regression hooks -- read-only invariants: migrate() never called, ingest_once() never called, no backend write methods called
- JSON schema pinned (via TestStatusJsonFlag):
  ```json
  {
    "run": {
      "run_id": "str",
      "status": "running|completed|interrupted|failed",
      "started_at": "ISO 8601 str",
      "ended_at": "ISO 8601 str | null",
      "last_op": "str | null",
      "last_done": "int",
      "last_total": "int | null",
      "host": "str",
      "pid": "int",
      "error": "str | null"
    },
    "sources": [
      {
        "source_uri_prefix": "str",
        "docs_seen": "int",
        "docs_skipped": "int",
        "docs_failed": "int",
        "last_scanned_at": "ISO 8601 str | null",
        "finished_at": "ISO 8601 str | null"
      }
    ]
  }
  ```
  No-runs case: `{"run": null, "sources": []}`.
- Coder surface decision: `_render_status(run: dict, sources: list[dict]) -> str` in `corpus_forge.ingest` (primary) or `corpus_forge.cli` (secondary fallback). `print_ingest_status(config, *, json_output: bool = False) -> None` in `corpus_forge.ingest`. `_build_backend_for_status(config) -> StorageBackend` is the internal factory the tests patch for isolation.
- Red output (tail):
  ```
  FAILED tests/unit/test_cli_ingest_status.py::TestStatusReadOnlyInvariants::test_migrate_not_called_during_status
  FAILED tests/unit/test_cli_ingest_status.py::TestStatusReadOnlyInvariants::test_backend_write_methods_not_called
  FAILED tests/unit/test_cli_ingest_status.py::TestStatusReadOnlyInvariants::test_ingest_once_not_called_during_status
  ERROR tests/unit/test_cli_ingest_status.py::TestRenderStatusFunction::test_completed_run_contains_run_id
  ERROR tests/unit/test_cli_ingest_status.py::TestRenderStatusFunction::test_progress_percentage_25_pct
  ERROR tests/unit/test_cli_ingest_status.py::TestStatusTwoSectionTable::test_ended_at_shown_for_completed
  ERROR tests/unit/test_cli_ingest_status.py::TestStatusTwoSectionTable::test_no_sources_still_renders_cleanly
  AttributeError: <module 'corpus_forge.ingest'> does not have the attribute 'print_ingest_status'
  Failed: _render_status not found in corpus_forge.ingest or corpus_forge.cli. Coder must add it in SR-G6.
  21 failed, 30 errors in 3.32s
  ```
- Status: red -- handed off to tdd-coder

## SR-T1
- Test files:
  - `tests/integration/test_migrate_0017_ingest_runs.py` (new — 61 tests)
  - `tests/unit/test_alembic_head_pins_0017.py` (new — 7 tests)
- Run command: `uv run pytest tests/unit/test_alembic_head_pins_0017.py tests/integration/test_migrate_0017_ingest_runs.py -q`
- Edge case checklist:
  - [x] happy — upgrade to 0017 creates both tables with all columns, indexes, and constraints
  - [x] boundaries — nullable vs NOT NULL per-column; INTEGER DEFAULT 0 counters vs nullable timestamps; UNIQUE on run_id; UNIQUE on (run_id, source_uri_prefix)
  - [x] type/format — PG: bigint/text/integer/timestamptz; SQLite: INTEGER/TEXT + CURRENT_TIMESTAMP; revision id <= 32 chars (VARCHAR(32) guard)
  - [x] state — idempotency: rewind alembic_version to 0016 then re-run upgrade must not raise; downgrade -1 returns alembic_version to 0016
  - [N/A] concurrency — pure DDL, sequential, no shared state
  - [x] failure paths — all 75 tests fail RED with FileNotFoundError (module load) or alembic CommandError
  - [N/A] locale/time — timestamp defaults are DB-side; no locale assertions
  - [x] production-realistic — column shapes verbatim from tasks.md design contract; FK cascade via live INSERT+DELETE on SQLite
  - [x] regression hooks — UNIQUE constraints enforced; ON DELETE CASCADE enforced; revision id VARCHAR(32) guard; ScriptDirectory linear chain check
  - [x] note — test_apply_migrations_uses_alembic.py uses _expected_head_revision() dynamically (no manual pin needed). EXPECTED_TABLES in test_sqlite_backend.py needs ingest_runs + ingest_run_sources added during GREEN.
- Red output (tail):
  ```
  FAILED tests/unit/test_alembic_head_pins_0017.py::test_revision_file_exists
  FAILED tests/unit/test_alembic_head_pins_0017.py::test_alembic_script_directory_head_is_0017
  ...
  E   FileNotFoundError: No such file or directory:
      '...corpus_forge/alembic/versions/0017_ingest_runs.py'
  E   alembic.util.exc.CommandError: Can't locate revision identified by '0017_ingest_runs'
  75 failed in 7.18s
  ```
- Status: red — handed off to tdd-coder

## SR-T9
- Test files: `tests/unit/test_ingest_checkpoint_cadence.py`, `tests/unit/test_ingest_telemetry.py`
- Run command: `uv run pytest tests/unit/test_ingest_checkpoint_cadence.py tests/unit/test_ingest_telemetry.py -q`
- Edge case checklist:
  - [x] happy — constant exists at 5.0; run/checkpoint/lock events emitted on a normal 2-5 doc run
  - [x] boundaries — 1-doc source (minimum boundary calls), 1000-doc source at 0.001s/call (cadence rarely fires), 20-doc source at 1s/call (cadence fires ~4 times)
  - [x] type/format — last_done must be int, elapsed_s numeric, run_id non-empty str, event field a str
  - [x] state — fast-advancing clock (step=10s) forces cadence fires; frozen clock (step=0) suppresses cadence fires inside loop
  - [ ] N/A — concurrency (pure ingest_once, single-threaded; concurrent-run lock is SR-T5)
  - [x] failure paths — backend.update_ingest_run raising RuntimeError must not propagate; must be swallowed + logged at DEBUG
  - [ ] N/A — locale/time (elapsed_s is a bare float; no locale-sensitive formatting tested here)
  - [x] production-realistic — fake source yields RawDocument items matching the real dataclass shape; backend mock matches the real Protocol surface (start_ingest_run, update_ingest_run, finish_ingest_run)
  - [x] regression hooks — TestCheckpointCadence.test_checkpoint_count_far_below_doc_count pins that cadence guard is not per-doc; TestPayloadJsonSafety.test_forbidden_type_detected pins the json-safety invariant helper
- Red output (tail):
  ```
  AssertionError: corpus_forge.ingest must expose a module-level logger attribute for 'corpus_forge.ingest.lock'. SR-G5 must add this.
  AssertionError: corpus_forge.ingest must expose a module-level logger attribute for 'corpus_forge.ingest.checkpoint'. SR-G5 must add this.
  AssertionError: corpus_forge.ingest must expose a module-level logger attribute for 'corpus_forge.ingest.run'. SR-G5 must add this. (currently missing)
  AssertionError: _CHECKPOINT_INTERVAL_S missing after fresh import
  AssertionError: _CHECKPOINT_INTERVAL_S must be 5.0, got None

  30 failed, 12 passed in 2.52s
  ```
- Status: red — handed off to tdd-coder
- Notes: 12 passing tests are intentional: Python's `logging.getLogger(name)` always succeeds (loggers are always resolvable by name), `TestLoggerExistence` tests that name, not emission. The 4 `TestPayloadJsonSafety` tests exercise the local `_assert_*` helper logic (green against current code). `test_module_importable` confirms the module loads cleanly. `test_lock_contention_event_shape` exercises the helper with a hand-crafted dict. None of the 12 passes are false positives.

## SR-T6
- Test files: `tests/cli/test_ingest_cli_resume_flags.py` (new, 53 tests)
- Run command: `uv run pytest tests/cli/test_ingest_cli_resume_flags.py -q`
- Edge case checklist:
  - [x] happy — `--status` exits 0 and calls `print_ingest_status`; `--once --resume` reaches `main(resume=True)`.
  - [x] boundaries — `--max-scan-age 0`, `0s`, `1s`, `1.5m`, `30m`, `2h`, `24h`, `1d`, `3d`, `90`, `60.0`.
  - [x] type / format — empty string, whitespace-only, alpha-only, unknown suffix, double suffix all raise.
  - [x] state — default invocation `ingest --once` forwards `resume=False, wait=False, max_scan_age=0.0`; bare `ingest` still accepted.
  - [x] failure paths — negative values, `--resume` alone, `--status --once`, `--status --resume`, `--status --wait`, `--status --max-scan-age`.
  - [ ] N/A — concurrency (pure CLI flag dispatch test, no concurrent paths).
  - [ ] N/A — locale/time (flag parser has no locale dependency).
  - [ ] N/A — regression hooks (no specific bug referenced; new feature).
- Decisions:
  - File placed in `tests/cli/` (not `tests/unit/`) to match existing CLI test conventions in this repo.
  - `parse_scan_age_spec(s: str) -> float` MUST be a named public function in `corpus_forge.scanner` (new module). Tests import it directly — coder cannot hide it in a closure.
  - Mutex tests assert BOTH flag names appear in the error message, not just "no such option", to pin the post-implementation error shape.
  - 3 tests pass today as correct characterization tests: `test_resume_alone_is_error` (error even before flag exists), `test_bare_ingest_is_still_accepted` (backward compat), `test_ingest_once_reaches_main_with_once_true` (backward compat). These 3 must remain passing after GREEN.
- Red output (tail):
  ```
  FAILED tests/cli/test_ingest_cli_resume_flags.py::TestFlagPresence::test_status_flag_in_help
  FAILED tests/cli/test_ingest_cli_resume_flags.py::TestFlagPresence::test_resume_flag_in_help
  FAILED tests/cli/test_ingest_cli_resume_flags.py::TestFlagPresence::test_wait_flag_in_help
  FAILED tests/cli/test_ingest_cli_resume_flags.py::TestFlagPresence::test_max_scan_age_flag_in_help
  FAILED tests/cli/test_ingest_cli_resume_flags.py::TestStatusHappyPath::test_status_exits_zero
  FAILED tests/cli/test_ingest_cli_resume_flags.py::TestStatusHappyPath::test_status_prints_run_info
  FAILED tests/cli/test_ingest_cli_resume_flags.py::TestStatusHappyPath::test_status_does_not_call_ingest_main
  FAILED tests/cli/test_ingest_cli_resume_flags.py::TestStatusHappyPath::test_status_calls_print_ingest_status
  FAILED tests/cli/test_ingest_cli_resume_flags.py::TestStatusMutex::test_status_and_once_is_error
  FAILED tests/cli/test_ingest_cli_resume_flags.py::TestStatusMutex::test_status_and_resume_is_error
  FAILED tests/cli/test_ingest_cli_resume_flags.py::TestStatusMutex::test_status_and_wait_is_error
  FAILED tests/cli/test_ingest_cli_resume_flags.py::TestStatusMutex::test_status_and_max_scan_age_is_error
  FAILED tests/cli/test_ingest_cli_resume_flags.py::TestParseScanAgeSpec::* (20 tests)
  FAILED tests/cli/test_ingest_cli_resume_flags.py::TestMaxScanAgeCLIWiring::* (7 tests)
  FAILED tests/cli/test_ingest_cli_resume_flags.py::TestWaitCLIWiring::* (2 tests)
  FAILED tests/cli/test_ingest_cli_resume_flags.py::TestResumeCLIWiring::* (2 tests)
  FAILED tests/cli/test_ingest_cli_resume_flags.py::TestBackwardsCompatibility::* (4 tests)
  50 failed, 3 passed in 4.01s
  ```
- Status: red — handed off to tdd-coder

## SR-T7 — ingest_once end-to-end resume + max-scan-age skip

- Test files:
  - `tests/integration/test_ingest_resume_e2e.py` (new — 32 tests)
  - `tests/unit/test_scan_config_max_scan_age.py` (new — 18 tests)
- Run command: `uv run pytest tests/integration/test_ingest_resume_e2e.py tests/unit/test_scan_config_max_scan_age.py -q`
- Edge case checklist:
  - [x] happy — `test_fresh_ingest_once_creates_one_run_row`, `test_run_row_has_required_fields`, `test_config_digest_matches_expected`
  - [x] boundaries — `test_max_scan_age_zero_always_rescans` (0 = always rescan), `test_no_resume_flag_creates_fresh_run`, `test_resume_no_prior_run_starts_fresh`, `test_scan_config_max_scan_age_fractional_accepted` (sub-second)
  - [x] type/format — `test_scan_config_max_scan_age_default_is_float`, `test_scan_config_max_scan_age_integer_zero_coerced`, signature kwarg tests
  - [x] state — `test_resume_reuses_run_id` (1 row after resume), `test_no_duplicate_documents_after_resume`, `test_two_sequential_invocations_create_two_rows`
  - [ ] N/A — concurrency (covered by SR-T5; out of scope for E2E resume)
  - [x] failure paths — digest mismatch (starts fresh, prior stays interrupted), `test_negative_max_scan_age_raises_value_error`, `test_stale_source_is_rescanned`
  - [ ] N/A — locale/time (timestamps seeded as ISO-8601 strings directly; no TZ logic exercised)
  - [x] production-realistic — 20-file vault fixture; real SQLiteBackend + real migrate(); direct SQL seeding
  - [x] regression hooks — `test_config_digest_matches_expected` pins sha256 formula; `test_source_row_finished_at_set` pins finished_at non-null invariant
- Red output (tail):
  ```
  FAILED tests/unit/test_scan_config_max_scan_age.py::test_scan_config_max_scan_age_default_is_zero
  FAILED tests/unit/test_scan_config_max_scan_age.py::test_scan_config_max_scan_age_field_exists
  FAILED tests/unit/test_scan_config_max_scan_age.py::test_scan_config_max_scan_age_3600_accepted
  ERROR tests/integration/test_ingest_resume_e2e.py::TestResumeReusesSameRunId::test_resume_reuses_run_id
  ERROR tests/integration/test_ingest_resume_e2e.py::TestBackendIngestRunHelpers::test_latest_unfinished_ingest_run_exists_on_backend
  Failed: ingest_runs table missing after migrate() — 0017 migration not yet written: no such table: ingest_runs
  14 failed, 4 passed, 32 errors in 3.59s
  ```
- Notes: 4 unit tests pass before implementation (negative-value + extra-field guards); these are not false positives — they test invariants that hold in both states (extra_forbidden today, ge=0.0 constraint after SR-G7). Integration tests all error at the backend fixture: 0017 migration not yet written. SR-G1 unblocks these in sequence.
- Status: red — handed off to tdd-coder

## DR-T3
- Test files: `tests/unit/test_scan_config_stale_run_threshold.py`
- Run command: `uv run pytest tests/unit/test_scan_config_stale_run_threshold.py -q --no-cov`
- Edge case checklist:
  - [x] happy — default 900.0, explicit float, int coercion, zero (disable)
  - [x] boundaries — 0.0, 0.001, large value (7d), int 0 coerced, bare-number string "60"
  - [x] type/format — int->float coercion, string forms ("15m"/"30s"/"2h"/"1d"/"1.5m"/"60"), model_dump returns float not string
  - [x] state — model_dump+model_validate round-trip for both float and string inputs; TOML absent/empty/float/string all normalise correctly
  - [ ] N/A — concurrency (pure Pydantic field, no shared mutable state)
  - [x] failure paths — negative float, negative int, negative string "-5m", alpha string "abc", unknown suffix "5x", empty string, whitespace-only, bare suffix "m", double-suffix "5mm", "nan", "inf"
  - [ ] N/A — locale/time (duration parsing is locale-independent ASCII arithmetic)
  - [x] production-realistic — TOML round-trips with real Config.load; checks all four suffix types + fractional coefficient
  - [x] regression hooks — lazy-import sentinel (corpus_forge.scanner absent after import); validator-triggers-import positive check
- Red output (tail):
  ```
  E                   AttributeError: 'ScanConfig' object has no attribute 'stale_run_threshold'
  .venv/lib/python3.11/site-packages/pydantic/main.py:1042: AttributeError
  FAILED tests/unit/test_scan_config_stale_run_threshold.py::test_stale_run_threshold_field_exists
  FAILED tests/unit/test_scan_config_stale_run_threshold.py::test_stale_run_threshold_default_is_900
  FAILED tests/unit/test_scan_config_stale_run_threshold.py::test_stale_run_threshold_string_15m
  FAILED tests/unit/test_scan_config_stale_run_threshold.py::test_stale_run_threshold_string_30s
  FAILED tests/unit/test_scan_config_stale_run_threshold.py::test_stale_run_threshold_string_2h
  FAILED tests/unit/test_scan_config_stale_run_threshold.py::test_stale_run_threshold_string_1d
  FAILED tests/unit/test_scan_config_stale_run_threshold.py::test_toml_float_literal_round_trip
  FAILED tests/unit/test_scan_config_stale_run_threshold.py::test_toml_string_literal_round_trip
  FAILED tests/unit/test_scan_config_stale_run_threshold.py::test_model_dump_returns_float_for_string_input
  FAILED tests/unit/test_scan_config_stale_run_threshold.py::test_validator_triggers_scanner_import_on_first_string_use
  ... (35 total)
  35 failed, 13 passed in 0.43s
  ```
- Notes: 13 tests pass for correct reasons — 12 rejection tests (pytest.raises(ValidationError)) pass because Pydantic extra='forbid' already rejects stale_run_threshold as unknown field; 1 lazy-import test passes because no field means no scanner import fires. All 13 continue to pass after DR-G2 (rejection tests will pass due to ge=0.0 + validator constraints instead). No false positives.
- Status: red — handed off to tdd-coder

## DR-T1
- Test files: `tests/unit/test_dataset_source_logical_name.py`
- Run command: `uv run pytest tests/unit/test_dataset_source_logical_name.py -q --no-cov`
- Edge case checklist:
  - [x] happy path — default None, valid names accepted, TOML round-trip
  - [x] boundaries — single-char "a", 64-char max accepted; 65-char rejected; empty string rejected
  - [x] type/format — invalid chars (space, slash, colon, at-sign, backslash, hash, exclamation); leading dash/underscore/dot; whitespace-only
  - [x] state — per-source independence (two sources in same dataset, different logical_name values); model_dump round-trip; model_dump_json round-trip
  - [ ] N/A — concurrency (pure Pydantic field, no concurrent access)
  - [ ] N/A — failure paths (no I/O, pure in-memory validation)
  - [ ] N/A — locale/time (string pattern validation only, no temporal logic)
  - [x] production-realistic — config.example.toml loaded as backwards-compat regression sentinel; realistic names like "notes", "personal-vault", "team_data"
  - [x] regression hooks — backwards-compat sentinel ensures adding the field does not break existing configs without it
- Decision locked: empty string "" is rejected with ValidationError (NOT coerced to None). Callers must pass None to disable. Pattern: ^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$ (1-64 chars). First char must be alnum.
- Reject test RED strategy: each reject test guards with _require_field_exists() asserting "logical_name" in DatasetSourceConfig.model_fields. This fires an AssertionError (not a misleading "DID NOT RAISE") because DatasetSourceConfig has model_config={} (no extra=forbid), so Pydantic silently ignores unknown fields rather than raising.
- Red output (tail):
  ```
  FAILED tests/unit/test_dataset_source_logical_name.py::TestLogicalNameDefault::test_logical_name_default_is_none
  AttributeError: 'DatasetSourceConfig' object has no attribute 'logical_name'
  FAILED tests/unit/test_dataset_source_logical_name.py::TestLogicalNameInvalidRejected::test_invalid_logical_name_rejected[.]
  AssertionError: DR-G1 has not yet added the logical_name field to DatasetSourceConfig.
  FAILED tests/unit/test_dataset_source_logical_name.py::TestLogicalNameValidAccepted::test_valid_logical_name_accepted[notes]
  AttributeError: 'DatasetSourceConfig' object has no attribute 'logical_name'
  FAILED tests/unit/test_dataset_source_logical_name.py::TestLogicalNameTomlRoundTrip::test_toml_without_logical_name_defaults_to_none
  AttributeError: 'DatasetSourceConfig' object has no attribute 'logical_name'
  FAILED tests/unit/test_dataset_source_logical_name.py::TestBackwardsCompatWithExampleConfig::test_config_example_toml_validates_after_field_added
  AttributeError: 'DatasetSourceConfig' object has no attribute 'logical_name'
  51 failed, 1 passed in 0.61s
  ```
- Status: red — handed off to tdd-coder

## DR-T2 — host-scoped `latest_unfinished_ingest_run(host=...)`

- Test files:
  - `tests/unit/test_backend_abc_ingest_runs.py` (UPDATED `test_latest_unfinished_ingest_run_signature`)
  - `tests/integration/test_postgres_ingest_runs.py` (ADDED `TestLatestUnfinishedIngestRunHostScope` — 5 tests)
  - `tests/integration/test_sqlite_ingest_runs.py` (ADDED `TestLatestUnfinishedIngestRunHostScope` — 5 tests)
- Run command: `uv run pytest tests/unit/test_backend_abc_ingest_runs.py tests/integration/test_postgres_ingest_runs.py tests/integration/test_sqlite_ingest_runs.py -q --no-cov`
- Edge case checklist:
  - [x] happy — `test_latest_unfinished_ingest_run_host_none_returns_any` (both backends): any-host back-compat returns most-recent row; explicit `host=None` verified identical to no-arg
  - [x] boundaries — `test_latest_unfinished_ingest_run_host_no_match` (both backends): host='machine-c' with only machine-a rows returns None
  - [x] type/format — signature test asserts `host` param present with default None, exactly one non-self param, POSITIONAL_OR_KEYWORD or KEYWORD_ONLY kind; back-compat no-arg calls verified
  - [x] state — `test_latest_unfinished_ingest_run_host_ignores_completed_on_same_host` (both backends): status filter (IN running/interrupted) still applies when host filter is active; completed row for host-A does not satisfy host='A' query
  - [ ] N/A — concurrency (pure SELECT query; no concurrent write paths in DR-T2 scope)
  - [x] failure paths — `test_latest_unfinished_ingest_run_host_no_match`: specified host with zero matching rows returns None (does not fall back to any-host)
  - [ ] N/A — locale/time (no timestamp logic in the host-filter path)
  - [x] production-realistic — uses distinct host strings ("machine-a", "machine-b") mirroring iCloud multi-machine topology; time.sleep() ensures started_at ordering is deterministic across tests
  - [x] regression hooks — `test_latest_unfinished_ingest_run_back_compat_existing_callers` (both backends) pins that existing callers with no host argument continue to work unchanged
- Decision note: `config_digest` param absent from the new signature per C4 and DR-T2 acceptance (len(params)==2 = self + host). The task prompt listed `config_digest` but that conflicts with binding tasks.md C4; tasks.md wins.
- Red output (tail):
  ```
  FAILED tests/integration/test_postgres_ingest_runs.py::TestLatestUnfinishedIngestRunHostScope::test_latest_unfinished_ingest_run_host_filter
  FAILED tests/integration/test_postgres_ingest_runs.py::TestLatestUnfinishedIngestRunHostScope::test_latest_unfinished_ingest_run_host_none_returns_any
  FAILED tests/integration/test_postgres_ingest_runs.py::TestLatestUnfinishedIngestRunHostScope::test_latest_unfinished_ingest_run_host_ignores_completed_on_same_host
  FAILED tests/integration/test_postgres_ingest_runs.py::TestLatestUnfinishedIngestRunHostScope::test_latest_unfinished_ingest_run_host_no_match
  FAILED tests/integration/test_sqlite_ingest_runs.py::TestLatestUnfinishedIngestRunHostScope::test_latest_unfinished_ingest_run_host_no_match
  FAILED tests/integration/test_sqlite_ingest_runs.py::TestLatestUnfinishedIngestRunHostScope::test_latest_unfinished_ingest_run_host_none_returns_any
  FAILED tests/integration/test_sqlite_ingest_runs.py::TestLatestUnfinishedIngestRunHostScope::test_latest_unfinished_ingest_run_host_ignores_completed_on_same_host
  FAILED tests/integration/test_sqlite_ingest_runs.py::TestLatestUnfinishedIngestRunHostScope::test_latest_unfinished_ingest_run_host_filter
  FAILED tests/unit/test_backend_abc_ingest_runs.py::TestProtocolMethodSignatures::test_latest_unfinished_ingest_run_signature
  E  AssertionError: latest_unfinished_ingest_run should have exactly one parameter beyond self ('host'); got []
  E  TypeError: SQLiteBackend.latest_unfinished_ingest_run() got an unexpected keyword argument 'host'
  E  TypeError: PostgresBackend.latest_unfinished_ingest_run() got an unexpected keyword argument 'host'
  9 failed, 109 passed in 13.68s
  ```
- Status: red — handed off to tdd-coder

## DR-T5
- Test files: `tests/unit/test_source_uri_prefix_logical_name.py` (NEW — 23 tests)
- Run command: `uv run pytest tests/unit/test_source_uri_prefix_logical_name.py -q --no-cov`
- Edge case checklist:
  - [x] happy — `test_logical_name_notes_alice_root`, `test_logical_name_notes_different_root` (core prefix contract)
  - [x] boundaries — `test_logical_name_single_char` (1-char name), `test_logical_name_with_root_none` (root=None still works when logical_name set), `test_two_machines_same_logical_name_produces_identical_prefix` (machine-convergence invariant)
  - [x] type/format — `test_logical_name_with_hyphens`, `test_logical_name_with_dots`, `test_logical_name_with_underscores` (allowed chars verbatim, no URL-encoding)
  - [x] state — `test_none_logical_name_uses_full_path`, `test_no_logical_name_attr_uses_full_path` (getattr default=None path), `test_legacy_returns_basename_not_logical_prefix` (legacy helper state unchanged)
  - [ ] N/A — concurrency (pure stateless function, no shared mutable state)
  - [x] failure paths — `test_empty_string_falls_through_to_path_prefix`, `test_empty_string_not_url_encoded_into_logical` (corrupt object defense; Pydantic already rejects empty at config-load time, but runtime must survive)
  - [ ] N/A — locale/time (no time fields; ASCII path strings; non-ASCII logical_name is out of scope per C2 pattern)
  - [x] production-realistic data — fake source objects match the duck-typed shape of FilesystemSource, not toy primitives; two-machine convergence test uses realistic paths (/Users/alice, /home/bob)
  - [x] regression hooks — `test_legacy_returns_basename_not_logical_prefix` pins that _legacy_source_uri_prefix_for is NOT modified (any diff to that helper is a regression); `test_legacy_two_machines_different_prefixes` pins that legacy intentionally diverges across different root basenames (documents the deliberate break from the new path)
- Red output (tail):
  ```
  FAILED tests/unit/test_source_uri_prefix_logical_name.py::TestApiSourceFallback::test_api_source_with_logical_name_set
  FAILED tests/unit/test_source_uri_prefix_logical_name.py::TestLogicalNameSet::test_logical_name_with_dots
  FAILED tests/unit/test_source_uri_prefix_logical_name.py::TestLogicalNameSet::test_logical_name_with_root_none
  FAILED tests/unit/test_source_uri_prefix_logical_name.py::TestLogicalNameSet::test_two_machines_same_logical_name_produces_identical_prefix
  FAILED tests/unit/test_source_uri_prefix_logical_name.py::TestLogicalNameSet::test_logical_name_with_underscores
  FAILED tests/unit/test_source_uri_prefix_logical_name.py::TestLogicalNameSet::test_logical_name_wins_over_root_path
  FAILED tests/unit/test_source_uri_prefix_logical_name.py::TestLogicalNameSet::test_logical_name_single_char
  FAILED tests/unit/test_source_uri_prefix_logical_name.py::TestLogicalNameSet::test_logical_name_notes_different_root
  FAILED tests/unit/test_source_uri_prefix_logical_name.py::TestLogicalNameSet::test_logical_name_with_hyphens
  FAILED tests/unit/test_source_uri_prefix_logical_name.py::TestLogicalNameSet::test_logical_name_notes_alice_root
  10 failed, 13 passed in 44.91s
  ```
  All 10 failures are `AssertionError`: function returned path-based prefix
  (e.g. `filesystem:///Users/alice/Notes`) instead of `filesystem://logical/notes`.
  The 13 passes are all correct: back-compat (logical_name=None), empty-string defensive,
  API source, and all legacy helper regression tests.
- Status: red — handed off to tdd-coder

## DR-T7
- Test files: `tests/unit/test_cli_ingest_status_stale_badge.py`
- Run command: `uv run pytest tests/unit/test_cli_ingest_status_stale_badge.py -q --no-cov`
- Edge case checklist:
  - [x] happy — running + recent → no STALE; running + stale → STALE badge present
  - [x] boundaries — exactly-equal to threshold → NOT stale (> not >=); 1s over → stale
  - [x] type/format — badge token is uppercase STALE no brackets; JSON key is lowercase "stale"
  - [x] state — completed / failed / interrupted rows never get STALE badge (terminal states)
  - N/A — concurrency (pure render function + single-call print)
  - [x] failure paths — mark_stale_runs NOT called (read-only invariant); poisoned mock asserts it
  - N/A — locale/time (pinned UTC fixed datetime, no DST/TZ variance in stale logic)
  - [x] production-realistic data — run dicts match `_make_run()` shape from `test_cli_ingest_status.py`
  - [x] regression hooks — read-only invariant (SR-Q1/C7); token format locked; JSON key omit-vs-false locked
  - [x] multi-machine — foreign host run: stale inference independent of socket.gethostname()
  - [x] threshold=0 disables — stale_threshold=0.0 → STALE never reported
  - [x] config-sourced threshold — stale_threshold=None reads config.scan.stale_run_threshold
  - [x] explicit kwarg override — stale_threshold=5000.0 overrides config=60.0
  - [x] null last_progress_at — must not crash
- Red output (tail):
  ```
  FAILED tests/unit/test_cli_ingest_status_stale_badge.py::TestReadOnlyInvariant::test_mark_stale_runs_not_called_for_stale_run
  FAILED tests/unit/test_cli_ingest_status_stale_badge.py::TestReadOnlyInvariant::test_mark_stale_runs_not_called_via_mock_assertion
  FAILED tests/unit/test_cli_ingest_status_stale_badge.py::TestStaleBoundaryExactlyEqual::test_exactly_equal_to_threshold_is_not_stale_human
  FAILED tests/unit/test_cli_ingest_status_stale_badge.py::TestStaleBoundaryExactlyEqual::test_one_second_over_threshold_is_stale_human
  FAILED tests/unit/test_cli_ingest_status_stale_badge.py::TestThresholdSourcedFromConfig::test_config_threshold_overrides_default
  FAILED tests/unit/test_cli_ingest_status_stale_badge.py::TestThresholdSourcedFromConfig::test_explicit_stale_threshold_kwarg_overrides_config
  FAILED tests/unit/test_cli_ingest_status_stale_badge.py::TestMultiMachineStaleBadge::test_stale_badge_works_for_foreign_host
  FAILED tests/unit/test_cli_ingest_status_stale_badge.py::TestMultiMachineStaleBadge::test_no_stale_badge_for_foreign_host_recent_run
  FAILED tests/unit/test_cli_ingest_status_stale_badge.py::TestStaleWhenLastProgressAtNone::test_null_last_progress_at_does_not_raise
  FAILED tests/unit/test_cli_ingest_status_stale_badge.py::TestStaleBadgeTokenFormat::test_badge_token_is_uppercase_stale
  FAILED tests/unit/test_cli_ingest_status_stale_badge.py::TestStaleBadgeTokenFormat::test_json_key_is_stale_lowercase
  30 failed in 3.16s
  Primary failure reason (28/30): TypeError: _render_status()/_print_ingest_status() got unexpected kwarg 'stale_threshold'
  2/30 fail: pydantic ValidationError scan.stale_run_threshold Extra inputs not permitted (DR-G2 needed)
  ```
- Status: red — handed off to tdd-coder (DR-G6)
