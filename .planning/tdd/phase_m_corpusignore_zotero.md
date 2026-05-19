# Phase M — Corpusignore lifecycle, scan perf, Zotero, semble spike

**Motivation:** Phase K shipped a `.corpusignore` parser; nothing helps users *create* or *maintain* one. Setup writes `config.toml` only; the `.corpusignore.example` at repo root is never copied. Doctor doesn't validate `.corpusignore`. The two file walkers (`corpus_forge/estimate.py:_walk` and `corpus_forge/sources/filesystem.py:discover`) diverge — the estimator at least applies ignore + baseline-skip; the FS source uses bare `rglob("*")` with no pruning at all. Adjacent gaps: no Zotero connector for academic users with curated PDF + metadata libraries; open question about whether MinishLab/semble could improve our code-search quality.

**Target release:** `0.1.0b5`.

**Status:** planning → execution. Workflow: tdd-principal owns it; orchestrator (this session) commits on workers' behalf.

**Approved master plan:** `/Users/evanowen/.claude/plans/let-s-work-on-a-prancy-pancake.md`.

## Decisions locked with the user

- One phase document, multi-wave.
- Zotero: both modes (local SQLite + Web API), user-configurable, defaults to local.
- semble: investigation spike only — no production wiring.
- Default ignore patterns: **conservative**. Always-on lockfiles/build/sourcemap/Apple-metadata/archives. Feature-gated: audio/video when `whisper.backend == "none"`; raw images when no image extractor. Never auto-ignore PDFs, notebooks, or source code.

## Wave overview

| Wave | Scope | Critical files |
|---|---|---|
| 1 | Ignore lifecycle: setup writes managed `.corpusignore`; doctor validates | new `ignore_defaults.py`, `ignore_lifecycle.py`; modify `setup/wizard.py`, `setup/questions.toml`, `doctor/checks.py` |
| 2 | Unified `os.scandir` walker with descent-time pruning + ext short-circuit | new `scanner/walker.py`; modify `ignore.py` (add `directory_pruned`), `estimate.py`, `sources/filesystem.py`, `config.py` |
| 3 | `corpus-forge ignore` CLI + MCP tools | new `admin/ignore.py`; modify `cli.py`, `mcp/server.py` |
| 4 | Zotero source plugin (`mode = local|web|both`) + MCP tool | new `zotero/{local,web_client,types}.py`, `sources/zotero.py`; modify `config.py`, `ingest.py`, `admin/source.py`, `mcp/server.py`, `doctor/checks.py`; commit fixture sqlite |
| 5 | semble spike (time-boxed ≤2 dev-days) | `experiments/semble_adapter.py`, `tests/perf/test_semble_bench.py`, decision doc `phase_m_wave5_semble.md` |

Each wave: RED → GREEN → wave gate (orchestrator stages + commits per `feedback_tdd_worker_commits.md`).

---

## Wave 1 — Ignore Lifecycle

### Red (failing tests first)

- `tests/unit/test_ignore_defaults.py` — `default_managed_lines(features)` truth table: empty features → only always-on; `{"whisper": False}` → audio/video patterns present; `{"image_extractor": False}` → raw-image patterns present; PDFs/notebooks/source code never present; output stable-sorted across calls.
- `tests/unit/test_ignore_lifecycle.py` — `splice_managed_block`: preserves text outside sentinels; idempotent; missing closing sentinel raises `ManagedBlockCorrupted`; corrupted rewrite leaves `.corpusignore.bak.<ts>`; `atomic_write_text` uses tempfile + `os.replace`.
- `tests/unit/test_setup_wizard.py` (additions) — `CF_CREATE_CORPUSIGNORE=yes` + populated `scan_root` → `<scan_root>/.corpusignore` with sentinels; `whisper_transcription=no` adds audio patterns; flipped to `yes` removes them; blank `scan_root` → only global at `~/.config/corpus-forge/ignore` is written; honored by `run_quick` and `run_non_interactive` likewise.
- `tests/integration/test_setup_corpusignore_resync.py` — flip a feature, rerun setup, managed block updates, user lines below the closing sentinel survive; deleted file gets recreated; corrupted sentinels → `.bak.<ts>` + rewrite.
- `tests/unit/test_doctor.py` (additions) — new `corpusignore` check: `FAIL` on unparseable line, `WARN` on missing file at a configured FS root, `WARN` on managed-block drift vs current features, `OK` when synced, `SKIP` when no FS-style data root. Existing JSON `checks[]` keys unchanged (additive only).

### Green (implementation)

- New `corpus_forge/ignore_defaults.py` (pure module, no I/O):
  - `MANAGED_START = "# >>> corpus-forge managed (do not edit between sentinels) >>>"`
  - `MANAGED_END = "# <<< corpus-forge managed <<<"`
  - `_ALWAYS_ON` — sorted: lockfiles (`*.lock`, `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `Cargo.lock`, `uv.lock`, `poetry.lock`), minified (`*.min.js`, `*.min.css`), sourcemaps (`*.map`), build (`dist/`, `build/`, `out/`, `.next/`, `.nuxt/`, `coverage/`, `target/`), Apple metadata (`.DS_Store`, `._*`, `.Spotlight-V100/`), iCloud (`*.icloud`), archives (`*.zip`, `*.tar*`, `*.7z`, `*.dmg`, `*.iso`).
  - `_AUDIO_VIDEO`, `_RAW_IMAGES` — sorted feature-gated tuples.
  - `feature_flags_from_config(cfg) -> dict[str, bool]` — `{whisper, image_extractor, code_enricher, vlm}` from `cfg.whisper.backend != "none"` etc.
  - `default_managed_lines(features) -> list[str]` — composes deterministically.
  - `render_managed_block(features, *, include_timestamp=True) -> str`.
  - `parse_managed_lines(text) -> list[str] | None`.
- New `corpus_forge/ignore_lifecycle.py`:
  - `class ManagedBlockCorrupted(Exception)`.
  - `splice_managed_block(existing_text, new_block) -> str`.
  - `atomic_write_text(path, text)` — tempfile + `fsync` + `os.replace` (Windows-safe).
  - `write_corpusignore(root, features, *, backup_corrupted=True) -> CorpusignoreWriteResult`.
  - `discover_data_roots(cfg) -> list[Path]` — FS-plugin sources only.
  - `resync_all(cfg, *, also_global=False) -> list[Path]`.
- Modify `corpus_forge/setup/wizard.py`: `_apply_corpusignore(answers, config_dir)` runs after answer collection; wired into `run_wizard`, `run_quick`, `run_non_interactive`. Always resyncs the global at `<config_dir>/ignore` so global tracks features regardless of per-root question.
- Modify `corpus_forge/setup/questions.toml`: add `create_corpusignore` yes/no (default `yes`, env `CF_CREATE_CORPUSIGNORE`).
- Modify `corpus_forge/doctor/checks.py`: add `_check_corpusignore(cfg)`; call from `run_doctor` alongside `_check_config_present` (parameterless `_CHECKS` tuple won't fit it).

---

## Wave 2 — Scan & Estimate Performance

### Red

- `tests/unit/test_walker.py` — baseline `_SKIP_DIR_NAMES` never descended (`os.scandir` monkey-patched to count visits); `IgnoreStack` dir-pattern prunes descent; negation `!build/keep.txt` re-includes — walker descends `build/`; `include_exts={".md"}` short-circuits before `stat()`; `follow_symlinks=False` matches current behavior; `sort=True` deterministic POSIX order; scandir handles closed via context manager.
- `tests/unit/test_ignore_directory_pruned.py` — `IgnoreStack.directory_pruned(rel_path) -> bool`: empty → False; non-negated dir match → True; **any** negation anywhere in the stack → conservative False; baseline `_SKIP_DIR_NAMES` still pruned independently.
- `tests/integration/test_scan_parity.py` — old `_walk` vs new walker on five fixtures (flat; mixed; deep `.git`-like; corpusignore with negation; broken symlinks/cycle). Multiset equality on `(path, size, kind)` + total bytes + bucket counts.
- `tests/integration/test_filesystem_source_parity.py` — legacy `_is_excluded` + `rglob` vs new walker driven by `IgnoreStack` synthesized from `exclude_globs`.
- `tests/perf/test_scan_bench.py` (`@pytest.mark.slow`) — synthetic 10k-file tree (≥70% in baseline-skip dirs). Hard: (a) ≥3× faster than inline control; (b) `os.scandir` called on ≤250 dirs of ~2,200. Wall-clock target as warning, not hard fail.
- `tests/unit/test_extension_index.py` — union of `ExtractorRegistry().extensions()` + heuristic extensions contains `.md`/`.py`/`.pdf`; absent `.iso`/`.dmg`; filename-only set contains `Makefile`/`Dockerfile`.

### Green

- New `corpus_forge/scanner/__init__.py` — re-exports `walk`, `WalkEntry`, `ScanConfig`.
- New `corpus_forge/scanner/walker.py`:
  - `@dataclass(frozen=True) class WalkEntry: path, stat, is_dir`.
  - `walk(root, *, ignore=None, baseline_dirs=_SKIP_DIR_NAMES, baseline_files=_SKIP_FILE_NAMES, include_exts=None, include_filenames=None, follow_symlinks=False, sort=True, scan_root=None, workers=1) -> Iterator[WalkEntry]`.
  - `os.scandir` in context manager; iterative stack of `(abs_path, rel_posix)`. Symlinks skipped unless `follow_symlinks`. Dirs: baseline-name skip → `ignore.directory_pruned(rel)` → existing `ignore.matches(..., is_dir=True)`. Files: baseline + macOS `._*` skip → `include_exts`/`include_filenames` short-circuit *before stat* → `ignore.matches(..., is_dir=False)` → yield. `sort=True` per-dir sort.
  - `workers > 1` raises `NotImplementedError` (API plumbed for follow-up).
- Modify `corpus_forge/ignore.py`: add `IgnoreStack.directory_pruned(rel_path) -> bool` after `matches`. Algorithm: any negation in stack → False; else True iff some non-negated pattern matches.
- Modify `corpus_forge/estimate.py:_walk` (lines 403–509): body becomes single loop over `walker.walk(...)`. Keep `_ClassBucket` accumulators, `make_progress` plumbing, `_LAST_SCAN_STATS` write. Add module-level `_ext_to_class` / `_filename_to_class` dicts to replace the linear `for h in _heuristics()` scan; add `_full_ext_index()`.
- Modify `corpus_forge/sources/filesystem.py:discover` (lines 126–137): body becomes a `walker.walk(...)` loop. Delete `_is_excluded`. Add `_ignore_from_globs` adapter.
- Modify `corpus_forge/config.py`: `ScanConfig` near `EstimateConfig` (~line 495) with `extra_skip_dirs: list[str] = []`, `follow_symlinks: bool = False`, `workers: int = 1`. Wire `scan: ScanConfig = Field(default_factory=ScanConfig)` on top-level `Config`. Document in `config.example.toml`.

---

## Wave 3 — Ignore Browse/Edit Tool

### Red

- `tests/admin/test_ignore_crud.py`: list (local/global/all with `[scope]` + `[scope:managed]` provenance); add (appends below closing sentinel; duplicate no-op exit 0); remove (managed-block touch → exit 3 `managed_block_protected`); validate (exit 0 clean, exit 1 with `(line, pattern, reason)`); sync (regenerates managed block; idempotent); init (creates starter; refuses on existing without `--force`); ambiguous scope on TTY → confirm prompt, off-TTY/agent → `ambiguous_scope` exit 2; atomic-write contract (mid-write crash leaves original intact).
- `tests/mcp/test_ignore_tools.py`: `list_ignore` returns `{patterns: [{source, pattern, managed, line}]}`; `add_ignore_pattern`/`remove_ignore_pattern`/`sync_ignore` are `writes_enabled`-gated; `validate_ignore`/`list_ignore` always-available; managed-block removal returns `isError: true, kind: "managed_block_protected"`.

### Green

- New `corpus_forge/admin/ignore.py`: Typer sub-app + reusable functions (`resolve_local_path`, `resolve_global_path`, `list_patterns`, `add_pattern`, `remove_pattern`, `edit_file`, `validate_file`, `sync_managed`, `init_file`). Reuses `corpus_forge.ignore._compile_pattern` for validation; `corpus_forge.admin.config._resolve_editor` for `$EDITOR`; `ignore_lifecycle.atomic_write_text` for writes; `ignore_lifecycle.write_corpusignore` for sync.
- Modify `corpus_forge/cli.py` (line 640 area): register `ignore_app` alongside other admin sub-apps.
- Modify `corpus_forge/mcp/server.py`: five new schemas (`_LIST_IGNORE_INPUT_SCHEMA`, `_VALIDATE_IGNORE_INPUT_SCHEMA`, `_ADD_IGNORE_PATTERN_INPUT_SCHEMA`, `_REMOVE_IGNORE_PATTERN_INPUT_SCHEMA`, `_SYNC_IGNORE_INPUT_SCHEMA`); register two always-available + three `writes_enabled`-gated; dispatch delegates to `admin/ignore.py`.

---

## Wave 4 — Zotero Library Connector

### Red

- `tests/unit/test_zotero_config.py` — mode-conditional Pydantic validators (`local` allows missing creds; `web` requires `user_id` + `api_key_env`; `both` requires both; `library_type="group"` requires `group_id`).
- `tests/unit/test_zotero_local.py` — committed fixture `tests/fixtures/zotero/zotero.sqlite` (5 items); `iter_attachments()` yields 5; on-disk paths resolve to `<storage_root>/<key>/<filename>`; authors/year/DOI/tags/collection-path lifted; excluded collections drop; `mode=ro&immutable=1` open succeeds with sibling `.wal`.
- `tests/unit/test_zotero_web.py` — `respx`-mocked `api.zotero.org`: pagination, `If-Modified-Since` 304, attachment caching, user/group URL shape, 429+Retry-After, 5xx exponential backoff.
- `tests/unit/test_zotero_source.py` — one `RawDocument` per PDF attachment + abstract-only doc for attachment-less items with non-empty `abstractNote`; metadata carries Zotero fields; labels carry `(zotero_tag, …)` + `(zotero_collection, …)`; `source_uri = zotero://<library_id>/<item_key>/<attachment_key>`; in `both` mode, dedupe on `zotero_item_key` (local wins unless web `dateModified` newer).
- `tests/integration/test_zotero_ingest_pdf.py` — end-to-end Zotero → ingest → `PdfDigitalExtractor`; per-chunk metadata retains Zotero fields.
- `tests/smoke/test_mcp_zotero_sync.py` — `zotero_sync` MCP tool: `writes_enabled`-gated; `dry_run=true` returns `{would_ingest, by_mode}` without touching backend.
- `tests/unit/test_doctor_zotero.py` — `_check_zotero`: SKIP / OK / WARN / FAIL by mode + path/credential presence.

### Green

- New `corpus_forge/zotero/{__init__,local,web_client,types}.py`.
  - `local.py`: read-only sqlite via `sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)`. SQL against `items`, `itemDataValues`, `itemData`, `fields`, `creators`, `itemCreators`, `tags`, `itemTags`, `collections`, `collectionItems`, `itemAttachments`. Schema-version best-effort check. `default_library_path()` for macOS/Linux/Windows.
  - `web_client.py`: `httpx.Client`-based. Honors `Last-Modified-Version`, `Retry-After`. Configurable `base_url` (defaults to `https://api.zotero.org`).
  - `types.py`: `ZoteroItem`, `ZoteroAttachment`, `ZoteroReconciled` frozen dataclasses.
- New `corpus_forge/sources/zotero.py`: `ZoteroSource(WatchedSource)`, `plugin name = "zotero"`. `discover()` yields PDF attachment paths; `parse(path)` joins parent-item metadata from a cache built at construction. Watch mode polls `dateModified` at `debounce` interval.
- Modify `corpus_forge/config.py`: `ZoteroSourceConfig` Pydantic block; added as nested `zotero: ZoteroSourceConfig | None` on `DatasetSourceConfig`. Fields: `mode`, `library_path`, `user_id`, `api_key_env`, `library_type`, `group_id`, `base_url`, `include_attachments`, `include_collections`, `exclude_collections`, `cache_dir`.
- Modify `corpus_forge/ingest.py` around line 564: new `elif source_config.plugin == "zotero":` branch; thread `vlm`/`whisper` from `Config` exactly like the `filesystem` branch.
- Modify `corpus_forge/admin/source.py`: `zotero` in `add` wizard.
- Modify `corpus_forge/mcp/server.py`: `_ZOTERO_SYNC_INPUT_SCHEMA`; register `zotero_sync` under `writes_enabled`; `_dispatch_zotero_sync` returns `{ingested, skipped, by_mode, audit_id}`.
- Modify `corpus_forge/doctor/checks.py`: `_check_zotero`.
- Fixtures: `tests/fixtures/zotero/{zotero.sqlite, build_fixture.py}` + `tests/fixtures/zotero/storage/<KEY>/<filename>` symlinks to existing tiny PDFs under `tests/fixtures/extractors/pdf/`.
- Docs: `docs/sources/zotero.md` (setup, mode trade-offs, "database is locked" troubleshooting). `config.example.toml` gains commented zotero example.

---

## Wave 5 — semble Investigation Spike (≤2 dev-days)

Hard limits: no MCP wiring, no changes to `corpus_forge/embedders/`, no changes to `corpus_forge/retrieval/`, no new top-level deps in `pyproject.toml`.

### Deliverables

- `experiments/semble_adapter.py` — research `SembleRetriever` conforming to `Retriever` protocol; in-memory.
- `experiments/README.md` — "not shipped" notice. Excluded from wheels + Docker via `pyproject.toml`.
- `tests/perf/test_semble_bench.py` — gated `CF_SEMBLE_BENCH=1`. Dumps `tests/perf/out/semble_bench_<ISO>.json`.
- `tests/perf/metrics.py` + `tests/perf/test_metrics.py` — MRR@10, Recall@5, p50/p95 latency helpers (unit-tested, ungated).
- `tests/perf/data/semble_queries.jsonl` — 25 hand-crafted queries against this repo at a pinned commit; ground-truth chunk ids authored manually.
- `.planning/tdd/phase_m_wave5_semble.md` — methodology, results table from JSON output, qualitative win-types, recommendation ∈ {**productionize**, **extract techniques**, **defer**, **drop**} tied to concrete numbers.

---

## Verification

Pass criteria for the phase as a whole:

- `uv run pytest tests/unit tests/integration tests/admin tests/mcp tests/smoke -x` green.
- `uv run pytest tests/perf/test_scan_bench.py -m slow` meets the ≥3× ratio + bounded scandir-count assertions.
- `uv run corpus-forge doctor --json` shows `corpusignore` + `zotero` checks; OK on synced fixture; WARN after flipping a feature flag.
- `uv run corpus-forge ignore list/add/remove/validate/sync/init` round-trips per Wave 3 contract.
- `uv run corpus-forge setup --non-interactive CF_CREATE_CORPUSIGNORE=yes CF_WHISPER_TRANSCRIPTION=no` writes `.corpusignore` with audio patterns; flipped to `yes` removes them; user-added lines below the closing sentinel persist.
- `uv run corpus-forge ingest --once` on the Zotero fixture lands Zotero metadata into `chunks.metadata`.
- Wave 5 decision doc carries concrete numbers + a chosen recommendation.

## Risks & open questions

- **Conservative directory pruning** disables itself when any negation pattern is present in the ignore stack. Acceptable for Wave 2 (still big wins on baseline-only trees); follow-up could add per-pattern reachability.
- **`exclude_globs → IgnoreStack` adapter** is the highest-behavioral-risk piece of Wave 2 — parity test #4 is the safety net.
- **Zotero schema drift** — tests pin against the committed fixture; `_validate_schema_compatibility(conn)` helper fast-fails on unsupported `userdata` version.
- **Concurrent Zotero process locks** — mitigated by `immutable=1`. Document the WAL-checkpoint ingest-lag tradeoff.
- **semble install on Apple Silicon** may pull binary deps we don't have — if so, run bench in Docker and document.

## Sequencing

1. Wave 1 RED → GREEN → wave gate.
2. Wave 2 RED → GREEN → wave gate (independent of Wave 1 but parity fixtures use the managed-block sentinels).
3. Wave 3 RED → GREEN → wave gate (depends on Wave 1's `ignore_lifecycle.write_corpusignore`).
4. Wave 4 RED → GREEN → wave gate (independent; benefits from Wave 2 speed for integration tests).
5. Wave 5 spike → decision doc (single commit).
