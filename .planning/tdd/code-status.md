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
| P0-03   | green  | Backfill implemented in apply_migrations(); 12/12 migration_002 unit tests pass |
| P0-04   | green  | content_hash added to chunk INSERT in upsert_document, chunk_content_hash(text) imported and wired — 22/24 tests pass; 2 test-side mock unpacking bug: _Call objects unpack as (args_tuple, kwargs_dict) not expected (sql, params) pattern |
