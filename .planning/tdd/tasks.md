# TDD Task Board — Phase M Wave 4 (Zotero Library Connector)

_Owner: tdd-principal (inline execution). Workers: n/a — single inline RED→GREEN wave._
_Date: 2026-05-19._

Brief: Ship a Zotero library source plugin (local SQLite + Web API + reconciled `both` mode) that flows attachments through `PdfDigitalExtractor`, enriches per-item metadata onto each `RawDocument`, and exposes a `zotero_sync` MCP tool.

## Project gates
- format: `uv run ruff format --check corpus_forge tests`   ✅ clean
- lint:   `uv run ruff check corpus_forge tests`            ✅ clean
- typecheck: `uv run pyrefly check corpus_forge`            ✅ 9 errors (all pre-existing optional-import noise; ≤ 27-baseline)
- test:   `uv run python -m pytest tests/unit/test_zotero_*.py tests/unit/test_mcp_zotero_sync.py tests/unit/test_doctor_zotero.py tests/integration/test_zotero_ingest_pdf.py`  ✅ 60 passed
- tool-count pin tests: `tests/smoke/test_mcp_writes_disabled_by_default.py`, `tests/smoke/test_skill_tool_contract.py`, `tests/unit/test_mcp_server_enrichment.py`  ✅ updated + passing

## Tasks
| id | title | depends_on | surface | risk | status |
|----|-------|------------|---------|------|--------|
| M4-F0 | Fixture: build_fixture.py + commit zotero.sqlite + storage tree | — | tests/fixtures/zotero/{build_fixture.py, zotero.sqlite, storage/...} | med | done |
| M4-T1 | RED: ZoteroSourceConfig validators | — | tests/unit/test_zotero_config.py | low | done |
| M4-T2 | RED: ZoteroLocalReader unit | M4-F0 | tests/unit/test_zotero_local.py | med | done |
| M4-T3 | RED: ZoteroWebClient unit (respx) | — | tests/unit/test_zotero_web.py | med | done |
| M4-T4 | RED: ZoteroSource unit | M4-F0 | tests/unit/test_zotero_source.py | med | done |
| M4-T5 | RED: ingest+PdfDigitalExtractor integration | M4-F0 | tests/integration/test_zotero_ingest_pdf.py | med | done |
| M4-T6 | RED: MCP zotero_sync tool | — | tests/unit/test_mcp_zotero_sync.py | low | done |
| M4-T7 | RED: doctor _check_zotero | — | tests/unit/test_doctor_zotero.py | low | done |
| M4-G1 | GREEN: zotero/types.py + __init__ | M4-T2 | corpus_forge/zotero/{__init__,types}.py | low | done |
| M4-G2 | GREEN: zotero/local.py | M4-G1, M4-F0 | corpus_forge/zotero/local.py | med | done |
| M4-G3 | GREEN: zotero/web_client.py | M4-G1 | corpus_forge/zotero/web_client.py | med | done |
| M4-G4 | GREEN: sources/zotero.py | M4-G2, M4-G3 | corpus_forge/sources/zotero.py | med | done |
| M4-G5 | GREEN: ZoteroSourceConfig | M4-T1 | corpus_forge/config.py | low | done |
| M4-G6 | GREEN: ingest dispatch branch | M4-G4, M4-G5 | corpus_forge/ingest.py | low | done |
| M4-G7 | GREEN: admin/source.py zotero wizard | M4-G5 | corpus_forge/admin/source.py | low | done |
| M4-G8 | GREEN: MCP zotero_sync | M4-G4, M4-G6 | corpus_forge/mcp/server.py | med | done |
| M4-G9 | GREEN: doctor _check_zotero | M4-G5 | corpus_forge/doctor/checks.py | low | done |
| M4-G10 | GREEN: docs + pyproject deps + tool-count test backfills | M4-G3 | docs/sources/zotero.md, config.example.toml, pyproject.toml, tests/smoke/test_mcp_writes_disabled_by_default.py, tests/smoke/test_skill_tool_contract.py, tests/unit/test_mcp_server_enrichment.py | low | done |

## DAG
- Wave 0: M4-F0 (fixture must land first). DONE
- Wave 1 (RED): T1, T3, T6, T7 in parallel; T2, T4, T5 after F0. DONE
- Wave 2 (GREEN): G1 → G2/G3 → G4 → {G5..G10}. DONE

## Summary

Files changed (Wave 4 scope):

### New (production)
- `corpus_forge/zotero/__init__.py`
- `corpus_forge/zotero/types.py` — `ZoteroItem`, `ZoteroAttachment`, `ZoteroReconciled` frozen dataclasses.
- `corpus_forge/zotero/local.py` — `ZoteroLocalReader` over `zotero.sqlite` (`mode=ro&immutable=1`).
- `corpus_forge/zotero/web_client.py` — sync `httpx`-backed Zotero v3 REST client.
- `corpus_forge/sources/zotero.py` — `ZoteroSource(WatchedSource)` + `reconcile_items(...)`.

### New (tests + fixtures + docs)
- `tests/fixtures/zotero/build_fixture.py` (run-once, committed alongside the binary).
- `tests/fixtures/zotero/zotero.sqlite` (5 items, 5 attachments).
- `tests/fixtures/zotero/storage/<KEY>/<filename>` (4 attachment files: 3 PDFs + 1 HTML).
- `tests/unit/test_zotero_config.py` (12 tests).
- `tests/unit/test_zotero_local.py` (20 tests).
- `tests/unit/test_zotero_web.py` (6 tests).
- `tests/unit/test_zotero_source.py` (10 tests).
- `tests/unit/test_doctor_zotero.py` (6 tests).
- `tests/unit/test_mcp_zotero_sync.py` (4 tests).
- `tests/integration/test_zotero_ingest_pdf.py` (2 tests).
- `docs/sources/zotero.md` — setup, mode trade-offs, "database is locked" troubleshooting.

### Modified
- `corpus_forge/config.py` — `ZoteroSourceConfig` Pydantic block + nested `zotero` field on `DatasetSourceConfig`.
- `corpus_forge/ingest.py` — `elif source_config.plugin == "zotero":` branch threading VLM + Whisper.
- `corpus_forge/admin/source.py` — `zotero` in the `add` wizard with mode-aware prompts.
- `corpus_forge/mcp/server.py` — `_ZOTERO_SYNC_INPUT_SCHEMA`, `zotero_sync` tool registration under `writes_enabled`, `_dispatch_zotero_sync`, plus module-level seams `_zotero_dry_run_count` / `_zotero_real_sync`.
- `corpus_forge/doctor/checks.py` — `_check_zotero` (SKIP / OK / WARN / FAIL per mode).
- `config.example.toml` — commented `[[datasets.sources]] plugin = "zotero"` example.
- `pyproject.toml` — `httpx>=0.27` (new core dep, Zotero web client), `respx>=0.21` (dev dep, route-mock for tests).
- `tests/smoke/test_mcp_writes_disabled_by_default.py` — added `zotero_sync` to `_WRITE_TOOL_NAMES`.
- `tests/smoke/test_skill_tool_contract.py` — added `zotero_sync` to `_WRITE_TOOLS`.
- `tests/unit/test_mcp_server_enrichment.py` — added `zotero_sync` to the writes-enabled expected set (25 tools total).

### Net delta
- 30 source/test/doc files added or modified.
- ~3,750 lines added, 88 removed.
- 60 new tests; full Wave 4 + tool-count backfill suite is 118 tests passing.

### Verification
- `uv run ruff check corpus_forge tests`         → All checks passed!
- `uv run ruff format --check corpus_forge tests` → 498 files already formatted
- `uv run pyrefly check corpus_forge`            → 9 errors (all pre-existing optional-import noise; well below the 27 baseline)
- Wave 4 + tool-count backfill suite             → 118 passed
- Pre-existing failures unrelated to Wave 4: confirmed `test_sqlite_backend::TestCopyReusableEmbeddings::test_returns_reused_embedder_ids_subset` and friends fail on the pre-Wave-4 tree too (sqlite_vec / search_dense / OCR-extra paths).

### Deviations from spec
- Architecture decision says fixture should use **symlinks** to existing PDFs; I used `shutil.copyfile` instead because symlinks under macOS iCloud sync are unreliable (the iCloud daemon dereferences and uploads the target which can break under multi-machine sync), and the PDFs are tiny (~50 KB each). Same total bytes either way; safer for the committed-fixture story. The Master Plan called this out as a tolerated alternative ("if symlinks are risky for git portability, copy the bytes instead.").
- Zotero schema test added a tiny `_AUTHOR_CREATOR_TYPES = ("author", "editor")` constant since the master plan called for "author-equivalent creatorType". Translator is intentionally excluded.
- `chunker = "markdown"` is required on `DatasetSourceConfig` (TOML schema regex); the Zotero source carries it through but the per-document `chunker_hint = "markdown"` from `PdfDigitalExtractor` is what `_DISPATCHER` actually picks. The required field is a no-op for the Zotero path but preserves backwards-compat with the existing `DatasetSourceConfig` regex.
- I had to backfill three "tool count" tests (smoke + unit) that pin the registered MCP tool set — these were not enumerated in the spec but are blocking regressions if not updated, so they're listed under M4-G10.
