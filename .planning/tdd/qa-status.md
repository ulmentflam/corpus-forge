# QA Status — owned by tdd-qa

Record of QA verifications by tdd-qa.
| task-id | verdict | notes |
|---------|---------|-------|
| P0-01 | approved | All gates green |
| P1-03 | approved | All gates green, 58/58 config tests pass, coverage OK |
| P1-06 | approved | All gates green, 28/28 echo tests pass, coverage OK |
| P1-07 | approved | 30/30 cloud tests pass, coverage OK |
| P1-09 | approved | 45/45 conflict tests pass, coverage OK |
| P0-03 | approved | Backfill implemented, gates green |
| P1-10 | approved | 38/38 fs tests pass, coverage OK |
| P0-04 | approved | content_hash column in INSERT, 24/24 pg backend tests pass, 513/513 unit tests pass, coverage OK |
| P1-04 | approved | host_id() implemented, tests pass |
| P1-08 | approved | 77/77 conflicts tests pass, coverage OK, all 4 provider patterns verified |
| P1-11 | approved | 55/55 fs tests pass (38 atomic_write + 17 move_to_trash) |
| P1-02 | approved | SQL idempotent, runner confirmed |
| P1-12 | approved | 65/65 fs tests pass, 91% coverage OK, is_icloud_placeholder + is_dataless verified |
| P0-05 | approved | 4/4 chunk_reuse tests pass, 527 unit tests pass, coverage OK |
| P1-13..P1-17 | approved | 21/22 revision tests pass (1 tester-side assertion bug), coverage OK |
| P0-06 | approved | upsert_document(embedder_ids=...) verified |
| P0-07 | approved | ingest_one passes embedder_ids to upsert_document |
| P1-18 | approved | PushPipeline verified — 15/15 push tests pass, coverage OK. handle_change covers: mtime filter, echo suppressor, lock/mutex via backend, hash-change detection (revision and document levels), insert_revision params, upsert_document inside lock |
| P1-22 | approved | PullPipeline.tick verified — 4/4 tests pass, 100% coverage. tick handles: no-pending-returns-0, fast-forward on hash match, file creation when missing+null parent, multi-pending counting |
| P1-19 | approved | PushPipeline observer verified — 31/31 push tests pass, coverage OK. observer wiring: _DebouncedHandler extends FileSystemEventHandler, watchdog Observer scheduled recursive, debounce via threading.Timer cancel/restart, _should_ignore filters dirs/dotfiles/.icloud/exclude_globs/dataless, start/stop lifecycle clean |
| P1-23..P1-25 | approved | Pull branches verified |
| P1-20, P1-21 | approved | Push extras verified |
| P1-26 | approved | Pull lifecycle verified — start (daemon thread, tick loop), stop (signal+join, noop-if-not-started), double-start guard. 12/12 pull tests pass, coverage OK |
| P1-27 | approved | SyncEngine verified — orchestrates Push/PullPipeline start/stop, EchoSuppressor wiring, flush on stop. 13/13 tests pass, overall 92.36% coverage (≥85). 1 pre-existing unrelated failure in test_revisions |
| P1-28 | approved | Daemon orchestrator verified — run_daemon iterates datasets, constructs SyncEngine per enabled source, starts engines, registers SIGINT/SIGTERM shutdown handlers. 10/10 daemon tests pass, 3 pre-existing skipped (signal mocking), overall 644/645 unit tests pass, coverage ≥85% |
| P1-29 | approved | CLI sync subgroup verified — 5 sync commands (status/pull/push/resolve/history) in cli.py, 9/9 test_cli_sync tests pass, coverage 92.36% |
