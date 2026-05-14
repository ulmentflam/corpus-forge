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
| R3-05   | approved | 14/14 runner tests green (including 3 new drift cases). Advisory-hash semantics confirmed by hand-trace: direct id resolution wins (bogus hash ignored); id-miss falls back to content_hash SQL shim; orphan (both miss) contributes 0 to the metric and logs WARNING. Dialect-aware SQL shim (`SELECT id FROM corpus.chunks WHERE content_hash = %s` for postgres; `FROM chunks WHERE content_hash = ?` for sqlite) sidesteps the protocol-lift requirement; flagged for R4/R5 to lift cleanly into StorageBackend. Full unit 1564 passed, 90.75% coverage. **qa: approved 2026-05-12.** |
| R3-07   | approved | 11/11 CLI tests pass. Hand-traced the `_do_eval → _build_retriever_for_eval → evaluate_retriever` call path; verified mock targets (`corpus_forge.cli._build_retriever_for_eval`, `corpus_forge.eval.runner.evaluate_retriever`) match the import-binding sites. Friendly --rerank notice is emitted to stderr (per the err=True flag) AND captured by typer's CliRunner output buffer. Per-file ruff ignore extension (B008) scoped narrowly to cli.py and motivated by typer's idiomatic defaults. Full unit 1575 passed / 3 skipped / 1 xfailed; coverage 90.75%. **qa: approved 2026-05-13.** |
| R3-06   | approved | 25 hand-curated queries (≥20 required); each row carries parallel `content_hashes` so drift fallback works; provenance doc pins commit / chunker / embedder for reproducible rebuild. Wheel ships the JSONL and the .corpus.md (verified by `python -m build --wheel` + zipfile inspection: `corpus_forge/eval/datasets/forge_self.{jsonl,corpus.md}` both present). Loader parses cleanly. Full unit 1582/3/1 with 7 new bundled-set tests. **qa: approved 2026-05-13.** |
| R3-08   | approved | 1/1 smoke test green; real-corpus measured baseline NDCG@10 = 0.717, MRR@10 = 0.920, Recall@10 = 0.760 (sanity-checked by direct CLI invocation against `/tmp/corpus-forge-test.db`). Side-fix in `SQLiteBackend.search_lexical` (tokenise + OR-join sanitiser) verified by all 26 unit lexical/fts/hybrid tests + 21 integration tests still green; PostgresBackend unaffected. Full unit suite 1582 passed; smoke 11/0/0. **qa: approved 2026-05-13.** |

---

## Phase D — D-11 (close-out QA)

- Suite: 1601 passed, 0 failed, 2 skipped, 1 xfailed (unit, 32.47s); 302 passed, 0 failed (integration, 59.27s); 15 passed (fuzz, 0.61s); 18 passed, 2 failed pre-existing (smoke, 11.74s)
- Coverage: 88.30% on corpus_forge/ (threshold 85%) — PASS
- Smoke (alembic-specific): `test_mcp_serve_boots_with_alembic.py` 6/6 PASS; 2 unrelated pre-existing failures in test_mcp_stdio + test_skill_tool_contract (iCloud Drive path-with-spaces subprocess issue, introduced Phase R5/CS, not Phase D)
- Regression sweep: 302 integration tests pass including test_migrate_002/003/sqlite survivors, test_sync_tombstone, test_apply_migrations_uses_alembic; no regressions found; corpus_forge/schema/ contains only migrate.py + per_embedder.sql.tmpl (no .sql files, no sqlite/ subdir); 0 pytest.mark.skip(reason=".*deleted in D-10.*") markers; apply_migrations body is single-path Alembic-only; alembic chain: 5 revisions, single head 0005_fts; in-memory SQLite migration verified by hand (all 18 tables created); migrate --help + revision subcommand verified; history failure with no DB is known deferred D-08 bug
- Alembic suite double-run: 25/25 run 1 (3.45s seed 1477993657), 25/25 run 2 (3.49s seed 3509799770) — no flakiness
- Issues: none blocking; 1 known deferred defect (migrate history no-DB error); 2 pre-existing smoke failures unrelated to Phase D
- Verdict: approved
- Notes: Phase D milestone goal fully met — Alembic replaces hand-rolled migrate.py; all 5 revisions ported; both dialects work; public apply_migrations signature stable; parity proven across 10 verdicts before legacy deletion; stderr discipline pinned by 6 smoke tests

---

## Phase E — E-03 (close-out QA)

- Suite: 1601 passed, 2 skipped, 1 xfailed (unit, 22.65s); 305 passed, 0 failed (integration, 56.13s); 15 passed (fuzz, 0.30s); 25 passed, 0 failed (smoke, 15.70s)
- Coverage: 88.30% on corpus_forge/ (threshold 85%) — PASS (no production Python modified; doc + test files only)
- Smoke (Phase E specific): `tests/smoke/test_satellite_deployment_doc.py` 5/5 PASS; `tests/integration/test_two_ingester_one_mcp.py` 3/3 PASS (3 consecutive runs, no flake)
- Regression sweep: 77 migrate/alembic integration tests all green (superset of Phase D's 25-test alembic suite); integration total 305 vs Phase D 302 — delta is exactly the 3 new E-02 cross-host tests; unit 1601 unchanged
- Doc accuracy: `sync_enabled = true` placed at `[[datasets]]` level in doc TOML example — matches `DatasetConfig.sync_enabled` (config.py:81); `DatasetSourceConfig` has no sync_enabled field; confirmed correct
- README link: `grep -n "deployment-satellite" README.md` hits lines 273 + 276 in "Multi-host deployment" section — link present and correct
- Issues: none blocking; 1 pre-existing deferred defect (migrate history no-DB ArgumentError, D-08); 1 pre-existing pydantic shadow warning (BackendConfig.schema)
- Verdict: approved
- Notes: Phase E milestone goal fully met — multi-host topology is now documented in `docs/deployment-satellite.md`, pinned by 5 doc-rot tests, and smoke-tested by 3 cross-host integration tests. No production code was modified.

---

## Phase F — F-06 (close-out QA)

- Suite: unit 1730 passed, 3 skipped, 1 xfailed (17.85s); integration 328 passed, 2 failed (65.32s); fuzz 15 passed (0.65s); smoke 30 passed (15.77s)
- Coverage: 85.47% on corpus_forge/ (threshold 85%) — PASS
- Smoke (Phase F specific): `test_skill_tool_contract.py` 3/3 PASS (11-tool pin + 3-read-only pin + subset-check); `test_mcp_writes_disabled_by_default.py` 3/3 PASS; cross-host `test_append_conversation_cross_host_visible` 3/3 consecutive runs GREEN
- Regression sweep: integration full suite — 2 FAILED in `test_apply_migrations_uses_alembic.py` (see Issues below). All 160 Phase F surface tests green. All 30 smoke tests green. All 1730 unit tests green. iCloud dupe thread FileNotFoundError warning pre-existing.
- Issues:
  - **Phase F regression**: `test_apply_migrations_creates_alembic_version_table_pg` and `::test_apply_migrations_creates_alembic_version_table_sqlite` fail because they assert `version_num == "0005_fts"` (written Phase D). Phase F's revision `0006_writes_and_feedback` moved the alembic head. Both assertions need updating to `"0006_writes_and_feedback"`. This is NOT a pre-existing flake — these tests passed in Phase D (302/0) and regressed when Phase F added a new revision. QA is NOT fixing this (read-only role); routing to Coder for a follow-up patch commit. NOT a production defect — purely stale test assertions.
  - The iCloud Drive `.pth` hidden-flag issue: `uv run pytest` fails if `chflags nohidden` has not been run on the editable install `.pth` file since `uv sync`. `make ci-local` and `make install` run `_unhide-pth` automatically, but `make ci` calls `uv run pytest` which re-hides the flag. Workaround: use `.venv/bin/python -m pytest` directly or run `make ci-local`. Pre-existing — not Phase F's introduction.
- Verdict: **rework** — 2 integration tests regressed by Phase F need a follow-up fix before Phase F is fully clean
- Notes: Phase F milestone goal is functionally met (MCP write surface + read-side enrichment + self-distillation loop closed). The 2 failing tests are test-assertion bookkeeping, not production regressions. Phase G may proceed; the fix is a 2-line patch to `test_apply_migrations_uses_alembic.py`.

---

## Phase G — G-06 (close-out QA)

- Suite: unit 1897 passed, 3 skipped, 1 xfailed (45.65s); integration 361 passed, 0 failed (64.21s); smoke 30 passed, 0 failed (15.41s)
- Phase G surface (11 test files): 188 passed, 0 failed, 0 skipped (7.59s)
- Coverage: 85.41% overall (threshold 85%) — PASS
- Smoke (CLI): `python -m corpus_forge export chat --help` — PASS; shows --template, --dataset, --out, --format, --push. Note: shell entrypoint `corpus-forge` fails in iCloud Drive path (spaces in path break sh exec shebang); `python -m corpus_forge` works correctly.
- Smoke (template builtins): all 6 builtins (chatml, llama3, alpaca, vicuna, gemma, qwen) render via `templates.render()` — PASS
- Smoke (MCP tool counts): read-only server 5 tools; writes-enabled server 14 tools — confirmed by test_skill_tool_contract.py 3/3 PASS
- Smoke (Phase F invariant): `test_append_conversation_cross_host_visible` 3 consecutive runs — 3/3 GREEN
- Alembic chain: 7 revisions (0001..0007), head=0007_chat_templates; chain tests 4/4 PASS; apply_migrations tests 3/3 PASS
- Regression sweep: full integration suite 361/0 (up from 328/2 in F-06; the 2 F-06 regressions in test_apply_migrations_uses_alembic were fixed in G-01); all smoke 30/0; no new skips vs F-06 baseline
- Issues: none blocking; shell entrypoint path-with-spaces issue is pre-existing iCloud limitation (not Phase G's introduction); `corpus_forge.templates.tools` 0% coverage (9 lines, all defensive — noted by prior tester, gate still met at 85.41%)
- Verdict: **approved**
- Notes: Phase G milestone goal fully met — dynamic chat templating accessible via MCP retrieval (render_conversation + list_chat_templates) AND CLI export (corpus-forge export chat), with custom template registration round-tripping through both paths via shared resolve_template(). 5 read tools / 14 write-enabled tools match skill contract.
