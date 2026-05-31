# Test Status — owned by tdd-tester (feat/routing-sql-push)
_Append-only per task._

| task-id | status | notes |
|---------|--------|-------|
| T1 | red→green | Added 12 unit tests for `chunks_missing_embedding(..., extensions=)` and 5 for `count_chunks_missing_embedding(..., extensions=)` on Postgres (mock-execute); 8 unit tests for SQLite (real migrated DB). Pre-coder run was RED at expected `TypeError: ... unexpected keyword argument 'extensions'`. Post-coder: 25/25 passing. |
| T2 | red→green | Added 5 new unit tests to `tests/unit/test_embed_routing_filter.py` covering (a) `backfill_embedder` passes `extensions=` kwarg to both backend methods, (b) catchall sends `extensions=None` not `[]`, (c) in-memory `route_for` filter is a no-op when SQL filter is correct, (d) regression: a "broken" backend that ignores `extensions=` causes the loop to `continue` not `break` on empty in-memory pages, (e) genuine empty `raw_rows` still exits cleanly. Existing 4 PR-#81 tests in same file kept green. |
| T3 | red→green | New `tests/integration/test_postgres_backend_routing_filter.py` (5 tests) and `tests/integration/test_sqlite_backend_routing_filter.py` (5 tests). Each seeds 4 chunks (.py/.ts/.md + conversation), verifies SQL filter excludes md+chat for `[".py", ".ts"]`, verifies `None` returns all 4, verifies case normalisation `[".PY"]` matches `.py`, count helper returns filtered totals, and an end-to-end backfill smoke that proves the in-memory filter dropped zero rows (encoded count == written count). |
