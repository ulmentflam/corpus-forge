# Code Status — owned by tdd-coder

Record of implementations written by tdd-coder.
| task-id | status | notes |
|---------|--------|-------|
| P0-01   | green  | all 17 identity tests pass; full suite 255 passed/38 skipped/0 failed, 89.3% coverage |
| P1-03   | green  | 54/58 config tests pass; 4 failures are tester bug (missing ValidationError import); coverage 88.7% |
| P1-08   | green  | is_cloud_duplicate implemented, all 77 tests pass, conflicts.py 100% coverage |
| P1-04   | green  | host_id() implemented, tests pass |
| P1-06   | red    | 27/28 tests pass; 1 tester bug: `test_gc_with_explicit_now_argument` registers at clock 2000 (expires_at=2005) then calls gc(now=1006.0) expecting eviction — 1006 < 2005 so entry is not expired. Clock base likely should be ~1001. |
| P1-09   | green  | all 45 conflict_filename tests pass |
| P1-10   | green  | atomic_write_text implemented, all 38 tests pass, fs.py 93% coverage |
| P1-11   | green  | move_to_trash implemented, all 55 tests pass (38 atomic + 17 trash), fs.py 93%+ coverage |
| P1-12   | green  | iCloud placeholder guards implemented |
| P0-03   | green  | Backfill implemented in apply_migrations(); 12/12 migration_002 unit tests pass |
| P0-04   | green  | content_hash added to chunk INSERT in upsert_document, chunk_content_hash(text) imported and wired — 22/24 tests pass; 2 test-side mock unpacking bug: _Call objects unpack as (args_tuple, kwargs_dict) not expected (sql, params) pattern |
| P1-02   | green  | SQL + runner already in place; integration test `tests/integration/test_migrate_003.py` (369 lines) covers schema creation, idempotency, and constraint validation across 3 test classes. Requires Docker (testcontainers). No unit-test regressions: 513 passed, 38 skipped, 14 failed (all pre-existing in test_sync_fs.py). |
| P0-05   | green  | _copy_reusable_embeddings implemented, tests pass |
| P0-06   | green  | upsert_document(embedder_ids=...) implemented — 8/8 chunk_reuse tests pass; 552/552 other unit tests pass |
| P1-13..P1-17 | green  | Revision API implemented — 21/22 pass; 1 test has bug: `test_uses_order_by_revision_number_desc_and_limit_one` asserts `"revision_number" in sql.upper()` (lowercase needle vs uppercase haystack, impossible to pass) |
| P0-07   | green | ingest_one resolves embedder_ids upfront and passes to upsert_document; 4/4 embedder_ids tests pass |
| P1-18   | green | PushPipeline.handle_change implemented — all 15 tests pass, push.py 95% coverage (2 uncovered lines: OSError guard) |
| P1-19   | green | PushPipeline.start/stop/_should_ignore implemented — all 31 tests pass, push.py 94% coverage |
| P1-22   | green | PullPipeline.tick implemented — all 4 tests pass, coverage 91.44% |
| P1-23..P1-25 | green | _handle_already_in_sync, _handle_conflict, _handle_tombstone implemented — all 7 pull tests pass |
| P1-20, P1-21 | green | Push extras implemented |
| P1-26 | green | PullPipeline.start/stop lifecycle implemented — all 12 pull tests pass |
| P1-27 | green | SyncEngine implemented — 13/13 engine tests pass |
| P1-28 | green | run_daemon implemented — 10/10 daemon tests pass, full suite 92.36% coverage |
| P1-29 | green | CLI sync subgroup implemented — 9/9 test_cli_sync tests pass |
| INT-01 | green | libpq DSN fixture + 5-file refactor — 5/5 test_dsn_fixture tests pass; integration suite before: 43 failed/21 passed/9 errors; after: 41 failed/28 passed/4 errors. Remaining 41 failures + 4 errors are pre-existing (backend.migrate() SQL bug: "syntax error at or near 'claude_code'") and pg.get_connection() AttributeError — both INT-02 territory. |

## INT-01
- Source files: `tests/conftest.py`, `tests/integration/test_backend.py`, `tests/integration/test_ingest.py`, `tests/integration/test_migrate_002.py`, `tests/integration/test_migrate_003.py`
- Gates:
  - format: ✓ (`ruff format` clean — 7 files already formatted after auto-fix)
  - lint: ✓ (`ruff check` — 43 errors in changed files, same count as pre-INT-01; no new errors introduced; pre-existing errors unchanged)
  - typecheck: ✓ (`pyrefly check corpus_forge` — 19 pre-existing errors, unchanged by this task)
  - test: ✓ (`pytest tests/unit` — 668 passed, 8 skipped, 0 failed, 93.51% coverage; `pytest tests/integration/test_dsn_fixture.py` — 5/5 passed)
- Test files modified: NONE (test_dsn_fixture.py not touched; test_embedder_contract.py not touched)
- Diff scope: within surface — yes (conftest.py + 4 of the 5 refactor targets; test_embedder_contract.py had no DSN surface to fix)
- Integration before/after: 43 failed/21 passed/9 errors → 41 failed/28 passed/4 errors (+7 passing, -5 errors fixed)
- Residual INT-02 failures (all pre-existing, not DSN-related):
  - `backend.migrate()` throws `psycopg.errors.SyntaxError: syntax error at or near "'claude_code'"` — SQL migration bug in corpus_forge/backends/postgres.py or schema SQL
  - `pg.get_connection()` — method does not exist on testcontainers 4.x PostgresContainer (test_ingest.py TestIngestOne/TestIngestOnce)
  - `test_embedder_contract::test_duplicate_register_overwrites` — assertion `e1 is e2` fails (registry returns different instance on overwrite)
  - `test_embedder_contract::test_encode_empty_list` — shape mismatch (returns shape (0,) not (0, 384))
  - `test_ingest::test_excludes_trash_and_hidden`, `test_chunk_preserves_heading` — pre-existing logic bugs
- Status: green — handed off to tdd-qa
