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

---

# Phase B — SQLite Backend

Cross-link: board lives at `.planning/tdd/sqlite_backend.md`. Task ids `B-01..B-18`.

**Principal QA-skip override (B-01..B-03)**: Foundation/skeleton tasks (B-01 loader + pyproject extra; B-02 schema files + dialect dispatch; B-03 SQLiteBackend skeleton + migrate) are mechanical surface where coder-run gate matrix is sufficient. Adding an independent QA pass earns nothing the gates didn't already verify. **QA resumes at B-05** (the first high-risk task in the wave plan, per `sqlite_backend.md` planning notes).

| task-id | verdict | notes |
|---------|---------|-------|
| B-01    | qa-skipped | Principal override — gate matrix sufficient. 21/21 tests, format/lint/pyrefly clean, 689 unit + 101/102 integration green. |
| B-02    | qa-skipped | Principal override — gate matrix sufficient. 130/130 tests, 819 unit + 102/102 integration, all gates clean. |
| B-03    | qa-skipped | Principal override — gate matrix sufficient. 29/29 tests green after tester narrowed the backfill-gating assertion to ignore inline schema comments. |
| B-15    | approved | 107/0/0 scoped; 209/0/0 full integration; 1067/1/8skipped unit; 93.04% coverage; format clean; lint clean; pyrefly 0 errors (16 suppressed); stray-file check clean; production code unchanged (zero diff vs 3907c82); vec0 gating verified; assertions substantive; missing-coverage justified. **qa: approved 2026-05-12.** |
| B-16    | approved | 34/0/0 scoped (17 tests x 2 backends, Docker up); 243/0/0 full integration; 1067 passed/1xfailed/0failed unit; 91.86% coverage (threshold 85%); lint clean (0 errors); format clean (98 files); pyrefly 0 errors (14 suppressed); git status clean. Fixture isolation: backend_kind + storage_backend both function-scoped (no explicit scope = default function); sqlite arm uses tmp_path/"corpus.db" (per-test isolation verified at conftest.py:261); postgres arm uses schema="corpus" with unique dataset names per test (consistent with all other integration tests). No pytestmark=integration at module level (confirmed — only in docstring). No sync E2E tests (grep for sync/push/pull/PushPipeline/PullPipeline/SyncEngine returns zero production references in test body). No request.param in test code (only in backend_kind fixture itself at conftest.py:233). Slice coverage: TestUpsertDocumentSmoke (8 tests), TestChunkReuseE2E (2 tests), TestRevisions (7 tests). Docker-off behavior: verified by code path — postgres arm calls pytest.skip() at conftest.py:253 when Docker unavailable; sqlite arm has no Docker dependency. Protocol-lift gap: get_or_create_dataset/find_dataset_id_by_name not exercised by B-16 (uses _execute direct insert); noted, not blocking (B-16 scope is older API surface; protocol lift landed in b978689 post-B-16). Regression sweep: b978689/bb7a3d2 touched base.py + both backends; all 243 integration tests and 1067 unit tests pass at HEAD — no regressions. **qa: approved 2026-05-12.** |
| B-18    | approved | Scoped smoke 1/0/0 (4.46s); all smoke 10/0/0 (3.86s); integration 243/0/0 (87s); unit 1067 passed/1xfailed/0failed (49.8s); 91.86% coverage (threshold 85%); lint clean (0 errors); format clean (98 files); pyrefly 0 errors (14 suppressed — down from 18 pre-fix); git status clean (only qa-status.md modified). register_source assessment: LEGITIMATE — `resolve_self_source` (postgres.py:726) is the only prior sources-table writer and hardcodes plugin="sync",identity="pull" for sync pull tracking; it is NOT called from ingest path. No other call site for `register_source` exists in any pre-b978689 commit (grep confirms); new `register_source` in base.py:90 + both backends is a brand-new protocol method introduced solely by this fix. `import socket` is used exclusively to pass `socket.gethostname()` to `register_source` as the host field — sensible wiring. Test mocks sympathetic: test_ingest_core.py (24→23 assert lines, but dropped `call_count==2` replaced by `.assert_called_once_with(name=..., kind=..., description=...)` — stronger); test_ingest_extended.py (20→19 assert lines, same pattern — stronger); test_embed_extended.py (2→1 `assert` lines, but `assert len(dataset_queries)>=1` replaced by `mock_backend.find_dataset_id_by_name.assert_called_once_with("my-dataset")` — stronger specificity). No assertions were weakened. SQLite RETURNING consistency: grep confirms 14 RETURNING usages in sqlite.py — consistent with project convention. Smoke assertion substantiveness: TIGHT — 4c asserts exact doc count (==3); 4d asserts exact filenames (set equality); 4e asserts exact chunk count (==3); 4f asserts zero NULL content_hash rows; 4b asserts sources>=1 (only loose assertion, justified since it tests the register_source wiring). Pre-existing warning in integration suite (FileNotFoundError in _handle_cloud_duplicate for icloud dupe test) is unrelated to B-18 surface and pre-dates this task. **qa: approved 2026-05-12.** |

---

## Phase R3 — eval harness

| task-id | status | notes |
|---------|--------|-------|
| R3-01   | approved | pyproject extras present (`retrieval`, `eval` with `numpy>=1.26`). Scope guards prevent R4/R5 extras from leaking. 7/7 tests green. Full unit suite **1550 passed / 3 skipped / 1 xfailed**; coverage **88.49%** (threshold 85%). Lint/format/pyrefly clean. **qa: approved 2026-05-12.** |
| R3-02   | approved | 29/29 metric tests green. Hand-computed NDCG values verified against `1.5 / (1.0 + 1/log2(3))` and the graded variant `(7 + 1/log2(3) + 1.5) / (7 + 3/log2(3) + 0.5)` — implementation agrees to rel_tol=1e-9. Coverage 98% (1 unreachable defensive branch — IDCG==0 fallback that the prior effective-relevant check already prevents). **qa: approved 2026-05-12.** |
| R3-03   | approved | 21/21 loader tests green. Error messages include `{path}:{lineno}:` prefix on every failure path; bool/int discriminator prevents `True`/`False` slipping in as chunk_ids; `content_hashes` parallel-length invariant enforced. Coverage 91% (7 misses are belt-and-suspenders error branches overlapping with covered paths). **qa: approved 2026-05-12.** |
| R3-04   | approved | 11/11 runner tests green. Baseline NDCG@10 measured at 1.0 against the toy corpus + FakeEmbedder (sanity-checked via standalone harness execution) — pinned floor 0.80 has 20-point headroom. Break-the-retriever sanity (constant-vector encode_query + alpha=1.0) drops below floor as designed, confirming the gate is non-vacuous. Pyrefly clean (16 suppressed unchanged). Full unit suite 1561 passed / 3 skipped / 1 xfailed; coverage 90.81% (threshold 85%). Pyrefly side-fix (GradedMap alias) is the minimal invariance escape — does not weaken the call-site contract. **qa: approved 2026-05-12.** |
