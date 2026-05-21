# Changelog

All notable changes to **corpus-forge** are recorded here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [PEP 440](https://peps.python.org/pep-0440/)
version numbers (so `0.1.0b1` is the first beta of the `0.1.0` line).

## [Unreleased]

### Fixed

- Phase M Wave 4 source-nesting bug: doctor's Zotero check no longer
  silently SKIPs sources declared as `plugin = "zotero"` without an
  explicit `[datasets.sources.zotero]` block. `DatasetSourceConfig`
  now default-instantiates `ZoteroSourceConfig()` (local mode, platform-
  default library path) when `plugin == "zotero"` and the nested block
  is absent. Three regression tests in
  `TestZoteroSourceDefault` lock the contract.

## [0.1.0b7] - 2026-05-20

### Added

#### Phase O — EDA + corpus cleaning (alembic `0012_analyze_signals`)

- `[analyze]` optional extra: `scikit-learn`, `hdbscan`, `umap-learn`,
  `bertopic`, `datasketch`, `fasttext-langdetect` (POSIX) +
  `langdetect` (cross-platform fallback). All heavy modules are
  lazy-imported inside function bodies — `corpus-forge --help` cold
  start stays at ~34 ms.
- `corpus_forge/analyze/` modules:
  - `stats` — p50/p95/mean/min/max token counts, length histograms.
  - `dedup` — exact (content-hash) + near-duplicate (MinHash LSH).
  - `language` — ISO-code + confidence via fasttext/langdetect dispatch.
  - `drift` — KS over token length, JS over embedding centroids.
  - `topics` — BERTopic with raw-HDBSCAN fallback + c-TF-IDF top terms.
  - `quality` — heuristic by default, joblib model when present at
    `~/.cache/corpus-forge/models/quality.joblib`.
- Curation selector extension: per-chunk dual-weight scheme. 5-weight
  (`learned_quality` added) when `chunk_quality_signals` has a row;
  4-weight (unchanged) otherwise. **The 47 pre-existing
  `test_curation_selector` tests stay byte-identical**, so MCP callers
  depending on `next_curation_target` ordering see no flip until
  `analyze quality` has run.
- New `corpus-forge analyze` CLI subgroup —
  `stats|duplicates|topics|distribution|drift|quality`. Reports land
  at `~/.cache/corpus-forge/reports/<ts>/` and respect
  `CORPUS_FORGE_REPORT_DIR`.
- Four new **read-only** MCP tools above the `writes_enabled` gate:
  `analyze_corpus`, `find_duplicates`, `cluster_topics`,
  `score_quality`.

#### Phase P — RAG/CAG retrieval feedback (alembic `0013_search_sessions`)

- New schema: `search_sessions(id, query, dataset_id, started_at)` and
  `search_result_events(id, session_id FK, chunk_id, signal, value,
  source, created_at)`.
- `HybridRetriever.search()` returns a `SearchResponse` that
  **subclasses `list`** — existing callers iterating/indexing the
  return value still work; new callers can read `.query_id` and
  `.results`. MCP `search` surfaces `query_id` in the response.
- `rate_search_result` MCP write tool — auto-creates a session for
  unknown `query_id`, persists `replacement_chunk_id` as a preference
  signal for the learned reranker.
- `LearnedReranker` — sklearn LogisticRegression trained on rated
  events; conforms to the existing `Reranker` protocol. Train via
  `corpus-forge analyze quality --train-reranker`.
- `corpus_forge/cag/` — precomputed cache builder + hybrid CAG/RAG
  selector. Cache key =
  `sha256((dataset_id, sorted(content_hash_set), template_name))`,
  so `commit_curation` triggers deterministic targeted invalidation
  (best-effort; failure does not fail the commit). corpus-forge ships
  CAG as a corpus-side cache builder, **not** an inference server.
- `corpus-forge eval rag` + `eval cag` CLI subcommands with
  configurable LLM-judge endpoint (local Ollama default; OpenAI-compat
  remote supported). `corpus_forge/eval/judge_mock.py` ships a
  SHA-256-keyed deterministic mock judge for CI byte-stability.

#### Phase Q — Explicit feedback capture + SDFT-format preprocessing (alembic `0014_sdft_demonstrations`)

Grounded in [Shenfeld et al., arXiv:2601.19897](https://arxiv.org/abs/2601.19897).
**corpus-forge does NOT train, fine-tune, or sample models** — it
captures feedback and emits training-ready data; downstream consumers
train. A static-analysis test enforces the boundary
(`tests/unit/test_sdft_no_inference.py`).

- New schema: `sdft_demonstrations(id, dataset_id FK, query,
  student_messages, teacher_messages, target, source, trace_id,
  content_hash UNIQUE, created_at)` with indexes on
  `(dataset_id, source)` and `(trace_id)`.
- `record_demonstration` MCP write tool with content-hash dedup
  (sha256 over a canonical-JSON payload). Idempotent — re-issued
  identical writes return the existing id with `deduped=True`.
- Capture hooks: `commit_curation` description corrections write a
  demo with `source=curation_commit`; `rate_search_result` negative
  signal + replacement_chunk_id writes with `source=rate_search_result`.
  Pure metadata fixes do NOT fire the hook (low-signal filter).
- Per-chat-client skill packs — all four reference the same MCP tool
  set; `test_skill_pack_consistency.py` rot-detects drift:
  - `.claude/skills/corpus-curate/SKILL.md` — extended
  - `.gemini/extensions/corpus-curate.toml` + `PROMPT.md` (Gemini CLI)
  - `opencode/commands/corpus-curate.md` (OpenCode)
  - `codex/agents/corpus-curate.md` (Codex)
- `corpus-forge feedback` CLI subgroup — `start|resume|list-sessions
  |export-session`. `prompt_toolkit`-based TUI for offline curators
  plus a scripted `--no-tui` mode for headless / CI usage. Session
  state persists to `~/.cache/corpus-forge/feedback/session-<id>.json`
  with idempotent resume.
- `corpus-forge export sdft` — chat-templated JSONL/Parquet artifact
  loadable via `datasets.load_dataset(...)`. Deterministic train /
  held-out split via `sha256(content_hash) % 100` bucketing.
  `--include-sources` filters by `SDFTSource` enum (covers
  `curation_commit`, `rate_search_result`, `record_demonstration`,
  `cli_feedback`, `claude_code`, `gemini`, `opencode`, `codex`).
  Golden-file regression locks `export_chat` and
  `export_feedback_pairs` schemas — no row-shape drift.
- `corpus-forge eval distill` — preprocessing-health metrics only:
  coverage, source mix histogram, template fidelity, p50/p95 token
  stats. **Not** a training-quality metric (no judge calls, no model
  sampling).

### Fixed

- `corpus_forge/sdft/capture.py` Postgres branch: the
  `INSERT ... ON CONFLICT DO NOTHING RETURNING id` path never called
  `conn.commit()`. The row was rolled back when
  `backend._get_connection`'s context manager closed the connection,
  so callers received a phantom `demonstration_id` that didn't exist
  on disk. Added the commit; pinned with four regression tests at
  `tests/integration/test_sdft_capture_pg_commit_regression.py`
  (durability across connection close, dedup-branch round trip,
  white-box commit-counter spy, full PostgresBackend round trip).
- Bumped `astral-sh/setup-uv@v5 → v6` and `actions/cache@v4 → v5` in
  `.github/actions/setup-uv/action.yml` to clear the Node.js 20
  deprecation warning ahead of GitHub's 2026-09-16 forced removal.

### Migration order

`0012_analyze_signals` → `0013_search_sessions` →
`0014_sdft_demonstrations`. Downgrade functions are `pass` (matches
the project's forward-only convention from `0008` / `0010` / `0011`).

## [0.1.0b6] - 2026-05-19

### Added

#### Phase N — Retrieval Quality (semble technique extraction)

Carries out Phase M Wave 5's recommendation to extract three
techniques from MinishLab/semble rather than swap retrievers. All
three features default OFF — deployments opt in explicitly via
`RetrievalConfig` flags.

- **Wave 0 — broadened bench.** Vendored a `pallets/flask` snapshot
  (`tests/fixtures/external/flask-snapshot/`, BSD-3-Clause, pinned
  to commit `954f5684`) as a second bench corpus alongside this repo.
  Grew `tests/perf/data/semble_queries.jsonl` from 25 → 61 hand-
  authored queries with byte-offset ground truth. Captured the
  Phase N baseline at `tests/perf/out/phase_n_baseline.json`. New
  gated bench at `tests/perf/test_phase_n_bench.py`
  (`CF_PHASE_N_BENCH=1`).
- **Wave 1 — adaptive lexical-weight bump on symbol queries.** New
  `corpus_forge.retrieval.query_shape.is_symbol_shaped(query)`
  heuristic (catches `Foo.bar`, `Foo::bar`, `_private`,
  `setUp`, `MyClass`, `snake_case_name`; rejects natural language).
  `HybridRetriever.search` lowers the effective alpha to
  `RetrievalConfig.symbol_query_alpha` (default 0.3) when
  `adaptive_lexical_weight=True` and the query is symbol-shaped on
  alpha fusion. Reranker downstream washes out the fusion-stage
  signal in practice — Wave 1 ships the lever, the lift materialises
  via composition with Wave 2.
- **Wave 2 — definition boosts on retrieval.** Code chunker now tags
  every AST-walk chunk with `metadata.is_definition = True` and
  `metadata.definition_kind` (`Function` / `Class` / `Method` /
  `Block`). HybridRetriever applies a score multiplier to definition
  chunks whose `metadata.name` matches a query token, BOTH
  pre-rerank (`definition_boost_factor_pre_rerank`, default 1.5) AND
  post-rerank (`definition_boost_factor_post_rerank`, default 1.2).
  Boost is gated on `is_symbol_shaped(query)` to avoid collateral
  damage on natural-language queries that happen to contain
  identifier-like words. Composite result with Wave 1: **identifier
  MRR@10 +0.1225** (0.466 → 0.588), zero per-category regression vs
  control on the broadened bench.
- **Wave 3 — static-tier fast path.** New `model2vec` embedder
  provider (`corpus_forge/embedders/model2vec.py`) for
  `minishlab/potion-code-16M` (256-dim, MIT, ~16 MB, CPU-fast). New
  `SearchOptions.fast_tier_mode ∈ {skip, shortcut, only}`. Shortcut
  mode uses the fast embedder as a candidate generator
  (`fast_tier_top_n`, default 200) for the main embedder's dense +
  lexical + rerank pipeline; only mode bypasses lexical + rerank
  entirely for latency-sensitive paths. Backend `search_dense` and
  `search_lexical` gained a `chunk_ids: frozenset[int] | None`
  keyword arg to support the candidate-pool restriction (on both
  Postgres and SQLite). Bench result: **shortcut +0.07 concept
  MRR@10**, **only mode 24.6 ms p50** (50× drop from 1.2 s) with
  quality within the looser Pareto floor. The cross-encoder reranker
  dominates p50, so shortcut-mode's value is quality preservation
  under candidate restriction, not latency — documented in the
  retriever's docstring.

### Changed

- **Embedder fingerprint drift detection** unchanged in code but
  re-verified during Wave 3 pre-flight: silently skips embedders the
  backend has not yet seen, so adding the new `model2vec` provider
  does NOT false-positive on a user's main embedder.

### Deps

- `model2vec>=0.5` under the NEW optional extra `[fast-tier]`. Core
  install size unchanged.

## [0.1.0b5] - 2026-05-19

### Added

#### Phase M — Corpusignore lifecycle, scan perf, Zotero, semble spike

- **Managed `.corpusignore` lifecycle** — `corpus-forge setup` now
  offers to create a feature-aware `.corpusignore` at each data root.
  The file carries a sentinel-delimited managed block whose patterns
  derive from active features: always-on lockfiles / build artifacts /
  sourcemaps / Apple metadata / archives; audio + video patterns
  added when `whisper.backend == "none"`; raw-image patterns added
  when no image extractor is configured. Conservative — PDFs,
  notebooks, and source code are never auto-ignored. User edits
  outside the sentinels survive resync.
  (`corpus_forge/ignore_defaults.py`, `corpus_forge/ignore_lifecycle.py`)
- **`corpus-forge doctor`** — new `corpusignore` check validates
  syntax, detects managed-block drift vs current features, warns on
  missing files at configured FS roots.
- **`corpus-forge ignore` subcommand** — list, add, remove, edit,
  validate, sync, and init `.corpusignore` files at local or global
  scope. Refuses to mutate the managed region (instructs the user to
  flip the underlying feature). Atomic writes; backup-and-rollback
  on `$EDITOR` syntax errors.
  (`corpus_forge/admin/ignore.py`)
- **Five new MCP tools** wrapping the same surface: `list_ignore`,
  `validate_ignore` (always available, read-only); `add_ignore_pattern`,
  `remove_ignore_pattern`, `sync_ignore` (`writes_enabled`-gated).
- **Zotero library source plugin** — local `zotero.sqlite` (read-only
  via `mode=ro&immutable=1`, safe with Zotero running), Zotero Web API
  at `api.zotero.org`, or both with local-wins reconciliation. PDF
  attachments flow through `PdfDigitalExtractor`; Zotero metadata
  (authors, year, DOI, collection, tags, abstract) propagates into
  chunk metadata. Doctor and MCP gain Zotero-aware tools.
  (`corpus_forge/zotero/`, `corpus_forge/sources/zotero.py`,
  `mcp__corpus-forge__zotero_sync`)
- **`[scan]` config block** — `extra_skip_dirs`, `follow_symlinks`,
  `workers` (concurrency reserved for a follow-up wave).

### Changed

- **Unified file walker** (`corpus_forge/scanner/walker.py`) replaces
  the two divergent slow walkers (`estimate._walk` and
  `FilesystemSource.discover`). `os.scandir`-based with descent-time
  directory pruning and extension short-circuit *before* statting.
  Synthetic-tree bench measured **3.29× speedup** with 99% of
  baseline-skip subtrees never entered (144 of ~2,200 dirs scanned on
  a 10k-file fixture).
- `IgnoreStack` gains `directory_pruned(rel_path)` — conservative
  algorithm: any negation anywhere in the stack disables directory
  pruning, otherwise prune iff a non-negated pattern matches. Strict
  gitignore parent-exclusion semantics preserved (a `!parent/child`
  negation cannot re-include children when `parent/` is ignored).
- `corpus-forge estimate` and ingest of the `filesystem` source plugin
  both delegate to the new walker; size/count behavior is unchanged
  (parity-tested across five fixture trees including negation-heavy
  ignore stacks).

### Research

- **semble investigation spike** — time-boxed measurement of
  MinishLab/semble against corpus-forge's `HybridRetriever` on this
  repo with 25 hand-authored queries. semble crushes identifier
  searches (MRR@10 0.85 vs 0.40) at ~880× lower p50 latency, but
  loses on concept, error, and call-site queries. Decision:
  **extract techniques** (adaptive lexical-weight bump on symbol
  queries, definition boosts, optional model2vec static-embedding
  fast tier) in a follow-up phase. semble is not added as a
  dependency. (`.planning/tdd/phase_m_wave5_semble.md`,
  `experiments/semble_adapter.py`)

### Deps

- `httpx>=0.27` (core, for Zotero Web API)
- `respx>=0.21` (dev, for Zotero web-client tests)

## [0.1.0b4] - 2026-05-18

(Reissue of `0.1.0b3` — that tag's release pipeline failed on a missed
test version-string pin. No code differences vs the failed b3 tag other
than the version bump and the corrected wheel-metadata test.)

### Added

#### Phase L — CLI beautification & diagnostics

- **`corpus_forge/ui/` package** (theme, console, banner, progress,
  prompts, agent). Brand palette pinned to the logo's ember (`#ff8a3d`)
  / deep ember (`#b83205`) with ANSI named state colors. Rounded-box
  banner on `setup` and `doctor`. `--no-color`, `--light` flags.
- **Centralized rotating logging** (`corpus_forge/logging_config.py`)
  — file at `~/.cache/corpus-forge/logs/<component>.log` (10 MB × 5) +
  themed stderr `RichHandler` + 200-entry in-memory ring buffer for
  bug-reports. New global flags: `--verbose/-v`, `--quiet/-q`,
  `--agent`, `--background/-b`.
- **`corpus-forge setup --quick`** — minimal-prompt wizard (backend,
  Ollama URL probe, embedder, first dataset).
- **`corpus-forge doctor --json`** — structured doctor output for
  agents / scripts. Adds a new `daemon_activity` check.
- **`corpus-forge estimate`** now reports wall-clock scan time + scan
  rate + pending-files breakdown (documents-not-chunked,
  chunks-missing-embedding).
- **Progress bars on every long op** (`ingest --once`, `embed`,
  `sync pull --once`, `sync push`, `estimate`) via a shared
  `ui.progress.make_progress` factory with bookending logger lines.
- **Embedder fingerprint drift detection**
  (`corpus_forge/embedders/fingerprint.py`) with a 3-way prompt
  (now/later/skip) on setup/ingest/embed; daemon emits a warning only.
  Drift state persisted to `~/.cache/corpus-forge/state/`.
- **`corpus-forge bug-report`** — zipped diagnostics bundle
  (manifest.json, doctor.json, redacted config.toml, log tails,
  recent-events ring buffer flush, env, deps, db summary,
  service status). Pre-fills a GitHub issue URL. Redactor module
  (`corpus_forge/diagnostics/redact.py`) covers DSN, API keys, Bearer
  tokens, password fields.
- **`corpus-forge logs path|tail|clear`** — sibling diagnostics
  surface; `tail --follow` polls at 250 ms, themed by log level.
- **Admin CRUD command groups**: `config`, `embedder`, `ollama`,
  `dataset`, `source`. Dotted-path config get/set/show/unset/edit via
  `tomlkit`. Ollama `list/get/pull/set-url/test` (streamed pull progress).
  Embedder `list/get/add/remove/set-active/test`. Dataset / source CRUD.
- **`corpus-forge service` lifecycle group**:
  `status/start/stop/restart/logs/install/uninstall`. Generates user-scope
  systemd unit (Linux) / launchd plist (macOS) / `schtasks` argv
  (Windows). The bare `daemon` command is now a deprecated alias for
  `service start`.
- **Project-wide "stay attached, unless `-b`" convention**: every
  long-running side-effect command (rerun-embed, daemon start,
  ollama pull, source ingest) defaults foreground with live progress
  and SIGINT forwarding; `-b` / `--background` detaches via
  `subprocess.Popen` and writes a pid file.
- **Agent-mode detection + JSONL emission** (`corpus_forge/ui/agent.py`)
  mirrors `cli/cli`'s `internal/agents/detect.go`. Recognised signals:
  `AI_AGENT`, `AGENT=amp`, `CODEX_*`, `GEMINI_CLI`, `COPILOT_CLI`,
  `OPENCODE`, `CLAUDECODE`, plus MCP stdio carve-out and explicit
  `--agent <type>` / `CF_AGENT`. When active: every command emits one
  `command.start` and one terminal `result|error` JSONL event on
  stdout; banners/progress/prompts suppress or emit structured events;
  logs route through an `AgentLogHandler`. `corpus-forge capabilities`
  introspects the registered Typer commands for agent discovery.

#### Phase K — .corpusignore

- `corpus-forge estimate` now honors a gitignore-subset `.corpusignore`
  file at the scan root (auto-detect) or at a path passed via the new
  `--ignore-file PATH`. New CLI flags: `--ignore-file`, `--no-ignore-file`
  (disable local), `--no-global-ignore` (disable global). `--ignore-file`
  and `--no-ignore-file` are mutually exclusive.
- The MCP `estimate_sync_size` tool gains `ignore_file` (string; empty
  string disables local; absent → auto-detect) and `disable_global_ignore`
  (boolean) args. Same semantics as the CLI flags.
- Global ignore file at `~/.config/corpus-forge/ignore` (mirrors
  git's `~/.config/git/ignore` convention). Overridable via the
  `CF_GLOBAL_IGNORE_FILE` env var; empty-string value disables the
  global lookup.
- Hard-coded `_SKIP_DIR_NAMES` (`.git`, `node_modules`, `__pycache__`,
  `.venv`, …) remain absolute — `.corpusignore` negations cannot un-skip
  a baseline entry.
- `.corpusignore.example` ships at the repo root with sensible
  defaults (Apple metadata, Photos libraries, large media, common
  backup dirs).
- New module `corpus_forge/ignore.py` exposes `CorpusIgnore`,
  `IgnoreStack`, `load_global_ignore`, `load_local_ignore` for callers
  that want the same matcher (K2 will wire this into `FilesystemSource`
  and `MarkdownVaultSource` so estimate and ingest agree).

### Changed

#### Phase L — CLI beautification & diagnostics

- Every `typer.echo` / `typer.secho` call site in `corpus_forge/cli.py`
  routes through `corpus_forge.ui.*` helpers. Static test
  (`tests/cli/test_no_typer_echo.py`) locks the refactor against drift.
- `corpus-forge sync status` shows the embed-worker pid status.
- Backends gain `find_embedder_row_by_name`, `count_existing_embeddings`,
  `update_embedder_config_blob` for the fingerprint flow, and
  `count_chunks_missing_embedding`, `pending_documents` for the
  estimate-pending breakdown.
- `corpus-forge daemon` deprecation alias forwards to `service start`.

#### Phase K — .corpusignore

- README "Install" section moved above "Quickstart" so users land on
  install before being asked to run shell commands. No content edits to
  either section — just an order swap and a "drop a `.corpusignore`"
  one-liner inserted into the Quickstart numbered list.

### Fixed

- Windows portability: `signal.SIGKILL` fallback to `SIGTERM` +
  `TerminateProcess` for the service-stop escalation path; atomic
  marker writes use linear backoff against Windows' file-replace deny;
  redactor reads/writes use explicit `encoding="utf-8"` so the
  `«redacted»` guillemets round-trip on cp1252 hosts.

## [0.1.0b2] - 2026-05-17

### Added

#### Phase J — Living Corpus
- `corpus-forge estimate <path>` CLI (new `corpus_forge/estimate.py`
  module + Typer command) — predicts the Postgres storage footprint of
  syncing a folder without touching the database. Per-extractor file
  counts and per-embedder embedding-row sizing including pgvector HNSW
  overhead (35 %) and btree-index overhead (~80 B / row). Human output
  by default; `--json` emits the `SyncEstimate` dataclass under stable
  `schema_version = 1`. `--compression-ratio` models LZ4-toasted text
  columns. New `[estimate]` config block with `compression_ratio`
  (default `1.0`; lower it to model TOAST compression on
  `documents.text` / `chunks.text`).
- `estimate_sync_size` MCP tool — same surface as the CLI, available to
  any MCP-connected assistant. Read-only; no `writes_enabled` flag
  required. Args: `{path, dataset?, embedders?, compression_ratio?}`.
  Returns the same `SyncEstimate` JSON shape with `schema_version = 1`.
- `CLAUDE.md`, `GEMINI.md`, and `AGENTS.md` at the repo root — vendor-
  specific (Claude Code / Desktop / API; Gemini CLI / Code Assist) and
  vendor-neutral (OpenCode, Cursor, Zed, Continue, Cline, any MCP-
  speaking client) setup guides covering install → configure →
  migrate → wire MCP → register skills → first-run sanity →
  curation-loop playbook → troubleshooting. README cross-links via a
  new "For AI assistants" section (J3).
- Data-curation chat skill (Claude / Gemini / OpenCode / AGENTS.md
  generic recipe) — pulls low-confidence or metadata-poor entries,
  facilitates a chat to improve them, and commits changes via MCP. New
  module `corpus_forge/curation/` (selector + shared chat-loop prompt
  template). New MCP tools: `next_curation_target` /
  `next_curation_batch` (read-only; both available regardless of the
  `writes_enabled` gate) and `commit_curation` (gated by
  `writes_enabled`; composes the existing
  `add_label`/`remove_label`/`set_metadata`/`set_description`/`add_feedback`
  write surface in one call). Skill assets land under
  `.claude/skills/corpus-curate/`, `.opencode/command/corpus-curate.md`,
  and the greenfield `.gemini/agents/corpus-curate.md`. Selector score
  formula: classifier_confidence_deficit × 0.35 + missing_metadata ×
  0.30 + ranker_elevation × 0.25 + freshness × 0.10, normalised to
  [0, 1]; the reranker leg reuses the existing `Reranker` protocol
  (cross_encoder or ollama) so the local-or-remote URL invariant
  carries through unchanged.

### Changed

#### Phase J — Living Corpus
- README reframed around "Chat with your data. Forge a living, trainable
  corpus." Training-data export stays the headline deliverable, framed
  as the outcome of an active corpus rather than a one-shot ETL job.
  New "Human-in-the-loop curation" bullet under "Why corpus-forge."
  Quickstart now shows `corpus-forge estimate <path>` between `migrate`
  and `ingest`, and the curation skill flow before `export`. New "For
  AI assistants" H2 cross-linking to `CLAUDE.md` / `GEMINI.md` /
  `AGENTS.md`. The MCP tool table now lists every tool with its
  `writes_enabled` gate. Banner alt-text updated to match.

#### Phase D — Universal multi-format ingest (waves 0–6)
- `Extractor` protocol (`corpus_forge/extractors/base.py`) + `ExtractorRegistry`
  with a per-extension lookup table and a second-pass `supported_filenames`
  fallback for the extension-less long tail (`Makefile`, `Dockerfile`, …).
- Seven document extractors landed under `corpus_forge/extractors/`:
  `PdfDigitalExtractor` (pymupdf4llm rag-helper), `HtmlExtractor`
  (readability-lxml + markdownify), `EpubExtractor` (ebooklib), `OfficeExtractor`
  (Docling for `.docx`/`.pptx`/`.xlsx`), `NotebookExtractor` (jupytext),
  `CsvExtractor` (pandas → markdown table, row-capped), and a 45+ extension
  `CodeExtractor` (tree-sitter-language-pack).
- `PassthroughMarkdownExtractor`, `PlainTextExtractor`, `StructuredDataExtractor`
  (`.json`/`.yaml`/`.toml`), `SubtitleExtractor` (`.srt`/`.vtt`).
- `FilesystemSource` — heterogeneous-tree walker that dispatches every file
  through the extractor registry. New `[[datasets.sources]]` plugin `filesystem`.
- `ChunkerDispatcher` — picks the per-document chunker from each
  `ExtractedDocument.metadata.chunker_hint`. `CodeChunker` (`chunkers/code.py`):
  tree-sitter AST walk with size-bounding + overlap, falling back to a brace-/
  blank-line byte chunker when the grammar is unavailable.
- New `[code]`, `[multi-format]`, and `[ocr]` optional extras. License posture
  documented in the README's "Distribution / licensing" section — `[multi-format]`
  AGPL-binds; `[code]` and `[ocr]` stay permissive.
- **P1 — Vision/OCR (waves 4–6).** `VLMBackend` protocol + `OllamaVLM`
  (local, `qwen2.5vl:7b` default) + `MistralOCR` (remote, `mistral-ocr-2503`).
  `PdfDigitalExtractor` Tier-1 → Tier-2 escalation on sparse text layers;
  `ImageExtractor` for `.png`/`.jpg`/`.tif`/`.bmp`/`.webp`/`.heic`. Failure
  ladder: missing poppler → ERROR + Tier-1 fallback; VLM timeout on a page →
  placeholder, remaining pages continue. Documented in `docs/architecture.md`.

#### Phase E — Document classification (rule → LLM chain)
- New `Classifier` protocol (`corpus_forge/classifiers/`) + `ClassifierRegistry`
  with ordered dispatch and the `tuple[str, ClassLabel] | None` return shape
  that distinguishes `classifier:rule` from `classifier:llm` on
  `document_labels.source`.
- `RuleBasedClassifier` (stdlib, microseconds/doc) — fast path covering the
  9-value taxonomy (`code`, `chat`, `book`, `textbook`, `paper`, `article`,
  `reference`, `note`, `other`) via format-label + path + body heuristics.
- `LLMClassifier` (Ollama `qwen2.5:7b-instruct` default; `POST /api/generate`
  with `format=json`, head+tail excerpt). Local-or-remote URL via
  `classifier.llm_url`; same swap-the-URL principle as `vlm.ollama_url`.
- New `corpus-forge classify` CLI with cost-guard preflight, `--dry-run`,
  `--limit`, `--json`, `--reclassify`, and `--classifier <name>` (bypass
  the chain).
- Alembic `0010_document_label_confidence` adds the `confidence REAL`
  column to `document_labels`.

#### Phase F — Content-defined chunking (FastCDC)
- New `CDCChunker` (`corpus_forge/chunkers/cdc.py`) — pure-Python FastCDC
  rolling-hash boundaries (MIT). Replaces positional slicing for prose classes
  (`book`/`textbook`/`paper`/`article`/`note`/`other`). Small edits ripple ≤ 2-3
  chunks, proven via Hypothesis property tests.
- `ChunkerDispatcher.for_class` — class-mapped chunker resolution
  (`code → CodeChunker`, `chat → ConversationChunker`,
  `reference → PassthroughChunker`, everything-else → `CDCChunker`).
- New `corpus-forge rechunk` CLI — walks classified documents and re-runs the
  chunker pass. Idempotent on chunk-text + metadata signature; preserves
  embeddings on identical chunks via `StorageBackend.replace_document_chunks`.
- New `StorageBackend.get_document_chunk_texts` + `get_document_chunk_metadatas`
  helpers powering the rechunk idempotency check.
- `[multi-format]` extra picks up `fastcdc>=1.6`.

#### Phase G — Whisper transcription + multi-modal embeddings
- **P0 — Whisper.** `WhisperBackend` protocol + `LocalWhisper` (faster-whisper
  in-process, tiny/base/small/medium/large) + `RemoteWhisper` (OpenAI-compatible
  `/audio/transcriptions`; works against OpenAI, Groq, Replicate, self-hosted
  whisper.cpp via HTTP). `AudioExtractor` for `.mp3`/`.wav`/`.m4a`/`.ogg`/`.flac`;
  `VideoExtractor` for `.mp4`/`.mov`/`.webm`/`.mkv`/`.avi` (uses imageio-ffmpeg).
  `[whisper]` extra; defaults to `backend = "none"` so audio/video files are
  silently skipped pre-opt-in.
- **P1 — Multi-modal embeddings.** New `MultiModalEmbedder` protocol
  (`corpus_forge/embedders/multimodal.py`) — distinct seam from the text
  `Embedder` so both keep clean APIs. `ClipLocalEmbedder` (sentence-transformers
  `clip-ViT-B-32`, 512 d; `jina-clip-v2` 1024 d also accepted) and
  `ClipRemoteEmbedder` (OpenAI-compatible `/v1/embeddings` with base64 data-URL
  image input).
- `corpus-forge embed --image` routes through `backfill_image_embedder`.
  Resolves image bytes from `metadata.image_b64` → `metadata.image_path` →
  the document's `filesystem://` URI in order.
- Alembic `0011_image_embeddings` adds `embedders.image BOOLEAN`; the dynamic
  `image_embeddings_<name>` per-embedder family mirrors the text
  `embeddings_<name>` family.

#### Phase H — Qwen3.6-35B-A3B code-chunk enrichment
- New `CodeEnricher` protocol (`corpus_forge/enrichers/`) + `CodeChunkEnrichment`
  dataclass (`{docstring, summary, symbols[], model, confidence}`) + `EnricherRegistry`.
- Two concrete backends to satisfy the local-or-remote URL principle:
  `QwenCoderLocal` (local Ollama `/api/generate`) and `QwenCoderRemote` —
  speaks either the Ollama shape OR OpenAI chat-completions
  (`response_format=json_object`) via `remote_api_shape`. Bearer auth
  optional on Ollama, required on OpenAI.
- New `corpus-forge enrich` CLI — walks `class=code` chunks only,
  cost-guard preflight, idempotent on `chunks.metadata.enrichment.model`.
  `--backend qwen-local|qwen-remote`, `--reclassify-on-model-change`,
  `--dataset`, `--limit`, `--dry-run`, `--json`.
- `iter_code_chunks_for_enrichment(model_tag)` + `update_chunk_enrichment`
  on both Postgres (`jsonb_set`) and SQLite (read-modify-write) backends.
- Default `[code_enricher].backend = "none"` keeps existing configs untouched.

### Changed

- Configuration: every model-client block (`[vlm]`, `[classifier]`, `[whisper]`,
  `[code_enricher]`) now carries explicit local + remote URL fields and rich
  one-comment-per-field documentation in `config.example.toml`. The
  local-or-remote URL principle is documented as a cross-cutting concern in
  `docs/architecture.md` and the README.
- `Config.backend.kind == "sqlite"` is now validated against multi-host sync
  (`Config.validate_sync_gate`) — `sync_enabled = true` on any dataset is
  rejected at config-load time so the failure is at startup, not on the first
  write.

## [0.1.0b1] - 2026-05-12

First beta release. The project is now feature-complete for the
single-host single-developer workflow described in the README and is
ready for external review.

### Added

#### Phase B — SQLite backend
- `corpus_forge/backends/sqlite_backend.py` — full SQLite + `sqlite-vec`
  storage backend, single-host only (no advisory locks, no cross-host
  sync).
- New `[sqlite]` optional install extra.
- Config-load validation rejects `sync_enabled = true` when
  `backend.kind = "sqlite"` so the failure surface is at startup, not
  on the first write.
- Schema migrations apply identically on PostgreSQL and SQLite (per-
  embedder vector tables, JSON metadata columns, content-hash dedup).

#### Phases CI-1 / CI-2 / CI-3 — release-ready CI/CD
- `.github/workflows/ci.yml` — `workflow_call`-able, `actionlint`
  gate, full lint + format + typecheck + parallel pytest with
  per-test timeout + coverage gate ≥ 85%.
- 3-OS × 3-Python matrix (ubuntu-22.04 / macos-14 / windows-2022 × py
  3.11 / 3.12 / 3.13) with `continue-on-error` on the still-landing
  py3.13 macOS-arm64 + Windows cells.
- `.github/workflows/integration.yml` — Linux + macOS Docker-backed
  pgvector integration runs.
- `.github/workflows/nightly.yml` — full matrix + `HYPOTHESIS_PROFILE=
  nightly` on a cron.
- Apache-2.0 license, PyPI classifiers, `py.typed` marker, per-OS
  installer scripts under `scripts/{linux,macos}/`.

#### Phases R1..R5 — retrieval + MCP surface
- `corpus_forge/retrieval/` — vector search + reranker over
  `chunks.text` (BGE reranker v2-m3 default).
- New `[retrieval]`, `[rerank]`, `[mcp]`, and `[eval]` extras.
- `corpus-forge search` and `corpus-forge mcp serve` CLI commands.
- In-process MCP server (`corpus_forge/mcp/server.py`) exposes
  `search`, `get_chunk`, `list_datasets` tools over stdio.
- Bundled retrieval-evaluation harness (`corpus-forge eval retrieval`)
  with a self-curated gold set under
  `corpus_forge/eval/datasets/forge_self.*`.

#### Phase CS — Claude integration drop-ins
- `examples/mcp-config/` — drop-in `.mcp.json` for Claude Code and
  `claude-desktop.json` for Claude Desktop.
- `.claude/skills/corpus-forge-search/SKILL.md` — Claude Code skill
  that surfaces `mcp__corpus-forge__*` tools with a citation-disciplined
  playbook.
- `.claude/agents/corpus-forge-researcher.md` — Agent SDK subagent
  scoped to the three MCP tools.
- `docs/claude-integration.md` — end-to-end walkthrough.
- Contract test (`tests/smoke/test_skill_tool_contract.py`) pins the
  `mcp__corpus-forge__<tool>` prefix against the server's live
  `tools/list` reply.

#### Phase BR — beta packaging
- `assets/banner.svg`, `assets/banner-dark.svg`, `assets/logo.svg` —
  anvil/forge + dataflow brand assets used in the README banner block.
- `CHANGELOG.md` (this file), `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`
  (Contributor Covenant 2.1), `SECURITY.md`.
- `.github/ISSUE_TEMPLATE/{bug_report,feature_request,config}.yml`,
  `.github/PULL_REQUEST_TEMPLATE.md`, `.github/dependabot.yml`
  (pip + github-actions weekly), `.github/FUNDING.yml`.
- `cliff.toml` — `git-cliff` config used by the release workflow.
- `.github/workflows/release.yml` — tag-triggered release pipeline
  (`gate` → `build` → `publish`); `gate` reuses `ci.yml` via
  `workflow_call`; `publish` uses `softprops/action-gh-release@v2`
  with `prerelease` auto-derived from beta / RC tags.
- Full README rewrite — banner block, shields.io badge row, expanded
  Agent integration (MCP) section, and a slimmer install / quickstart
  flow.

### Changed

- README condensed and reorganised from ~430 lines to ~250 lines; the
  three install scripts are in collapsible `<details>` blocks; the
  HF-export "what you get" section is promoted toward the top.
- The compact 3-bullet MCP pointer landed in CS is replaced by a full
  Agent-integration section with Prerequisites + Wire-up snippets.

### Security

- `SECURITY.md` lists `0.1.x` as the supported beta line and
  `evan@jwo3.io` as the vulnerability-reporting contact.

[Unreleased]: https://github.com/ulmentflam/corpus-forge/compare/v0.1.0b1...HEAD
[0.1.0b1]: https://github.com/ulmentflam/corpus-forge/releases/tag/v0.1.0b1
