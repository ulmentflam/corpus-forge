# Code Status — owned by tdd-coder

Record of implementations written by tdd-coder.
| task-id | status | notes |
|---------|--------|-------|
| P0-01   | green  | all 17 identity tests pass; full suite 255 passed/38 skipped/0 failed, 89.3% coverage |
| P1-03   | green  | 54/58 config tests pass; 4 failures are tester bug (missing ValidationError import); coverage 88.7% |
| P1-06   | red    | 27/28 tests pass; 1 tester bug: `test_gc_with_explicit_now_argument` registers at clock 2000 (expires_at=2005) then calls gc(now=1006.0) expecting eviction — 1006 < 2005 so entry is not expired. Clock base likely should be ~1001. |
| P1-09   | green  | all 45 conflict_filename tests pass |
| P1-10   | green  | atomic_write_text implemented, all 38 tests pass, fs.py 93% coverage |
