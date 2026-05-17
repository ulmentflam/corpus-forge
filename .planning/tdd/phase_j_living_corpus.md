# Phase J — "Living Corpus" beta (0.1.0b2)

**Pivot in one line:** corpus-forge becomes the place you *chat with your data* — a searchable, trainable, living corpus that grows as you (and your models) curate it. Training-data export stays the headline deliverable; it is now framed as the *output* of an active, human-in-the-loop curation loop, not a one-shot batch job.

**Target release:** `0.1.0b2`. Beta line continues; no GA implication.

**Status:** planning → execution. Workers: tdd-tester, tdd-coder, tdd-qa under tdd-principal. Orchestrator (Claude Code session) commits on workers' behalf — workers stage only.

---

## Phase J at a glance

| Slice | Title | Independent? | Surface |
|-------|-------|--------------|---------|
| J1 | Sync storage estimator (CLI + MCP) | yes | new `corpus_forge/estimate.py`, `cli.py` (+ `estimate` command), `mcp/server.py` (+ `estimate_sync_size` tool) |
| J2 | Agent guides at repo root | depends on J1 surfaces being named | new `CLAUDE.md`, `GEMINI.md`, `AGENTS.md` |
| J4 | Data-improvement chat skill | depends on J2 install story | new `corpus_forge/curation/`, MCP additions, `.claude/skills/corpus-curate/`, `.opencode/command/corpus-curate.md`, new `.gemini/agents/corpus-curate.md`, AGENTS.md generic recipe |
| J3 | README + branding reframe | depends on J1+J4 examples being concrete | `README.md`, `CHANGELOG.md` unreleased block, optional banner caption tweak |
| J5 | Beta cut → 0.1.0b2 | depends on all above | `pyproject.toml`, `CHANGELOG.md`, tag |

Execution order: **J1 → J2 → J4 → J3 → J5.** No parallel waves across slices (each slice has a doc/CLI/changelog dependency on its predecessor's named surfaces). Within each slice, tdd-principal builds its own internal RED→GREEN wave plan.

Project gates (unchanged from prior phases — reused as-is):
- `make lint`, `make format-check`, `make typecheck` (pyrefly strict)
- `make test-unit` (coverage ≥90%)
- `make test-integration` (skip-gated on Docker / Ollama)
- `make ci`

Hard constraints (cross-cutting):
1. **Workers stage; orchestrator commits.** Subagents cannot sign commits (1Password SSH needs TTY).
2. **Verify N-files/+X/-Y summary before pushing** (iCloud sync race in this checkout — recover via `git add` + new commit, never `--amend` after a failed verify).
3. **Local-or-remote URL** for every model client (already-load-bearing project invariant — extends to any new LLM call J4 introduces).
4. **MCP write surface already exists.** J4 selects + assembles batches; it does **not** reinvent write paths. Reuse `add_label`, `remove_label`, `set_metadata`, `set_description`, `add_feedback`, `register_template`, `append_message`.

---

## J1 — Sync storage estimator

### Goal
A user (and an MCP-connected assistant) can answer "what will it cost in Postgres to sync this folder?" *without* actually syncing. Pure prediction; ignores existing rows.

### Why this slice first
Smallest blast radius, no new write paths, no agent wiring, no messaging changes. Lands a sizing module and a public CLI surface that J3 (branding) and J4 (curation chat) can both cite.

### Sizing model

A `SyncEstimate` is the sum of five layers, each computed per-file then aggregated:

1. **Document row** — fixed overhead + `len(source_uri)` + `len(title)` + `len(metadata JSON)` + `len(text)` (TOASTed when >2KB).
   - Postgres row overhead per tuple: ~28 bytes (heap header) + per-column nulls bitmap.
   - `content_hash` (64 hex chars) ≈ 64 bytes inline.
2. **Chunk rows** — `est_chunks(file)` × (overhead + `mean_chunk_text_bytes` + 64 (content_hash) + `len(heading)` + `len(metadata JSON)`).
   - `est_chunks(file)`: per-extractor heuristic table (see below). FastCDC prose target ≈ 1024 tokens ≈ 4 KB text → `ceil(text_bytes / 4096)`. Code AST chunker ≈ 1 chunk per 60 LOC. Conversation chunker ≈ 1 chunk per message. Subtitles ≈ 1 chunk per cue group of 30s.
3. **Embedding rows** — per active embedder: `n_chunks × (dim × 4 bytes + 32 byte row overhead)`. For each embedder configured in `[[datasets.embedders]]` with `dim` declared.
4. **HNSW index overhead** — empirical multiplier: `n_chunks × dim × 4 × 1.35` per embedder (pgvector HNSW averages 25–40% over raw vector size for `m=16`).
5. **Btree indexes** — `documents_hash_idx`, `chunks_content_hash_idx`, `conversations_hash_idx`. Roughly `n_rows × 80 bytes` per btree.

### Per-extractor heuristics (initial table)

These are starting points; J1 ships them as a constants file so users can tune.

| Extension class | est_chunks formula | mean_chunk_text_bytes |
|-----------------|--------------------|-----------------------|
| markdown / txt / html / epub / office prose | `ceil(text_bytes / 4096)` | 4096 |
| pdf (digital, no OCR) | `ceil(text_bytes / 4096)` × 1.05 (page-break overhead) | 4096 |
| pdf (assumed-VLM, large bitmap) | `ceil(file_bytes / 12288)` (proxy for pages × extracted text) | 4096 |
| code (tree-sitter languages) | `ceil(loc / 60)` (LOC = `file_bytes / 32`) | 1920 |
| jupyter notebook | `ceil(file_bytes / 8192)` | 4096 |
| csv | `1` (whole table renders to one Markdown chunk, row-capped) | min(file_bytes, 32 KB) |
| structured (json/yaml/toml) | `1` | min(file_bytes, 4 KB) |
| subtitle (srt/vtt) | `ceil(file_bytes / 6144)` | 6144 |
| image (CLIP lane only — no text doc) | `0` text chunks, `1` image-embedding row per active CLIP embedder | n/a |
| audio/video (Whisper) | `ceil(file_size_mb × 60 / 30)` (≈ 30s cues × 60s/MB heuristic) | 6144 |
| unknown / skipped | `0` | 0 |

`mean_chunk_text_bytes` is the *post-extraction* size — what lands in `chunks.text`. The model ignores intermediate extraction overhead since none of it is stored.

Surfacing knobs (config `[estimate]` block, optional):
- `compression_ratio` (default `1.0`) — applied to text-heavy columns to model TOAST compression. Users on `LZ4` toast columns can set `0.5`.
- `embedders_active` — override which configured embedders count (default: all). MCP callers can pass per-call.
- `sampling.enabled` (default `false`) — if true, actually run the extractor on N random files to refine the per-extractor chunk-count multiplier. Off by default; first release is heuristic-only.

### CLI

```bash
corpus-forge estimate <path> [--config PATH] [--dataset NAME] [--embedder NAME ...] [--json] [--verbose]
```

Default human output:
```
corpus-forge estimate ~/Notes

Scanned 4,128 files across 217 directories (3.1 GB raw).

By extractor:
  markdown      1,902 files     412 MB    →  ~94 K chunks
  pdf           218 files       1.6 GB    →  ~108 K chunks
  code          1,712 files     280 MB    →  ~76 K chunks
  unknown       296 files       skipped

Estimated Postgres footprint (purely additive):
  documents          ~120 MB
  chunks             ~640 MB
  embeddings
    qwen3_8b         ~9.2 GB   (278 K × 4096 × 4 B + 35% HNSW)
    bge-small-en     ~1.4 GB   (278 K × 384  × 4 B + 35% HNSW)
  btree indexes      ~110 MB
  ----------------------------
  Total              ~11.6 GB

Assumed compression ratio: 1.0. Pass `--compression-ratio 0.5` to model LZ4-toasted text columns.
```

JSON output mirrors the same structure under a stable schema (`schema_version: 1`).

### MCP tool

`estimate_sync_size`:
- args: `{path: str, dataset?: str, embedders?: list[str], compression_ratio?: float}`
- result: structured JSON identical to `--json` mode.
- registered alongside existing `_dispatch_search` / `_dispatch_get_chunk` in `corpus_forge/mcp/server.py`.

### Tests

- `tests/unit/test_estimate.py` — pure-function tests on the sizing math. ≥30 cases covering every extractor class, edge cases (empty dir, all-unknown, single huge file, mixed), embedder permutations, compression ratios.
- `tests/unit/test_cli_estimate.py` — CLI plumbing: human + JSON modes, dataset/embedder filters, error paths (missing path, bad config).
- `tests/unit/test_mcp_estimate.py` — MCP tool dispatch + arg validation + error shape.
- `tests/integration/test_estimate_real_tree.py` — runs against `tests/fixtures/...` (small heterogeneous tree).

### Done criteria

- [ ] `corpus_forge/estimate.py` exists with `SyncEstimate` dataclass + `estimate_sync(path, config, *, embedders=None, compression_ratio=1.0)` function.
- [ ] `corpus-forge estimate <path>` lands and is registered in `__main__.py`.
- [ ] MCP tool `estimate_sync_size` registered in `_list_tools` + `_call_tool` dispatch.
- [ ] Unit tests ≥30 cases, all green.
- [ ] Integration test runs in <2s.
- [ ] `make ci` green; coverage ≥90%.
- [ ] CHANGELOG `[Unreleased]` section adds the new command + tool under "Added".

---

## J2 — CLAUDE.md / GEMINI.md / AGENTS.md

### Goal
Three repo-root guides that an LLM (Claude Code, Gemini, OpenCode, or any MCP client) can read and act on to: install corpus-forge, wire the MCP server, register the curation skill (J4), and troubleshoot. AGENTS.md doubles as a vendor-neutral MCP recipe.

### Shape (each file)

Each file is ≤200 lines, copy-pasteable, and assumes the reader is an AI assistant directing a human user (or acting via a coding-agent terminal). Sections in the same order across all three so the three files diff cleanly:

1. **What corpus-forge is** — three sentences. Reuses the J3 reframe tagline.
2. **Install** — pick-one block:
   - `uv tool install corpus-forge[postgres,hf]` (recommended; works on macOS/Linux/Windows)
   - `brew install corpus-forge` (macOS, post-J5 Homebrew tap)
   - `scoop install corpus-forge` (Windows, post-J5)
   - `curl …/install.sh | sh` one-liner for completeness
3. **Configure** — run `corpus-forge setup` (existing wizard from Phase I).
4. **Migrate** — `corpus-forge migrate`.
5. **Connect this client (CLAUDE/GEMINI/OPENCODE-specific)**:
   - Claude: drop into `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows), `.mcp.json` for Claude Code:
     ```json
     {
       "mcpServers": {
         "corpus-forge": {
           "command": "corpus-forge",
           "args": ["mcp", "serve"],
           "env": {"CF_CONFIG": "~/.config/corpus-forge/config.toml"}
         }
       }
     }
     ```
   - Gemini: `~/.gemini/settings.json` (or whatever Gemini CLI uses at release time — file MUST cite Gemini docs URL, not invent the path).
   - OpenCode: `.opencode/config.json` (project-scoped). Reuses existing `corpus-forge-researcher` agent pattern.
6. **Register the curation skill** — points at J4 skill files. Tells the LLM/user how to install them client-side and run them.
7. **First-run sanity** — `corpus-forge doctor`, `corpus-forge estimate ~/Notes`, then `corpus-forge ingest --once`.
8. **Troubleshooting** — links to `docs/` + `corpus-forge doctor`.

### AGENTS.md specifically
Generic recipe: "If your client speaks MCP and has a JSON config, here's the canonical block. If your client supports slash-skills, here's the curation-skill payload." Lists the supported clients (Claude Code, Gemini, OpenCode) with one-line gotchas each, and reserves a section for "other clients" with the vendor-neutral block.

### Tests
None (pure docs). QA gate: `make lint` (no executable code) + a markdown-lint check via the existing pre-commit hook if it's wired (else manual proof-read).

### Done criteria

- [ ] `CLAUDE.md`, `GEMINI.md`, `AGENTS.md` exist at repo root.
- [ ] All three reference the *same* version of the install + MCP-config + skill-registration snippets — diffed by hand or by an inline `make docs-check` if cheap.
- [ ] README cross-links to all three under a new "For AI assistants" section.
- [ ] CHANGELOG `[Unreleased]` adds an entry under "Added".

---

## J4 — Data-improvement chat skill ("Forge a stronger entry")

### Goal
An always-available skill the user can invoke from Claude Code / Gemini / OpenCode that:
1. Asks the corpus "what's the next data point that most needs my help?"
2. Pulls that point's full context (chunk text + neighbors + current labels + metadata + missing-metadata diagnostic).
3. Runs a chat with the user to fortify it — labels, descriptions, factual corrections, follow-up notes.
4. Persists every change via existing MCP write tools.
5. Supports bulk mode: when many small records look improvable in one sitting, batch them.

### Selection ranker (the "what's next" engine)

A new module `corpus_forge/curation/selector.py` exposes:

```python
def next_curation_target(
    *,
    backend,
    dataset: str,
    embedder: str,
    limit: int = 1,
    mode: Literal["single", "batch"] = "single",
    seed_query: str | None = None,
) -> list[CurationTarget]
```

Selection score = weighted sum of:
- **Classifier-confidence deficit** — `1.0 - chunks.classifier_confidence` (Phase E surface). Weight 0.35.
- **Missing-metadata score** — count of empty/null among `{title, heading, labels, description, language, source_uri suffix-is-known}`. Weight 0.30.
- **Ranker-elevation potential** — if a `seed_query` is supplied, the chunk's reranker score from the existing `Reranker` (cross-encoder OR ollama). Otherwise, the chunk's mean cosine similarity to its own dataset centroid (low = anomalous = interesting). Weight 0.25.
- **Freshness** — newer chunks ranked higher (recency penalty inverted), so users see what they just imported. Weight 0.10.

The ranker is pluggable: same `Reranker` protocol as `corpus_forge/retrieval/rerank/`. Default = cross-encoder if loaded, else Ollama, else cosine-only.

Batch mode: groups candidates by `(source_uri stem, classifier_label)` and returns the largest group up to `limit`, so one chat can ratify many similar entries.

### MCP tools (new)

Registered alongside existing write tools:

- `next_curation_target` → returns a `CurationTarget` JSON: `{chunk_id, document_id, text, heading, current_labels, current_metadata, missing_fields, classifier_confidence, score_breakdown}`.
- `next_curation_batch` → same but `limit=N`, plus a `cohesion_score` explaining why the batch belongs together.
- `commit_curation` → atomic multi-write that wraps any combination of `add_label`/`remove_label`/`set_metadata`/`set_description`/`add_feedback` for a single `chunk_id` or a list of `chunk_ids` (bulk). Returns the count of writes applied per kind.

### Skill / agent files

| Client | Path | Format |
|--------|------|--------|
| Claude Code | `.claude/skills/corpus-curate/SKILL.md` | Same skill schema as existing `.claude/skills/corpus-forge-search/` |
| OpenCode | `.opencode/command/corpus-curate.md` | Same slash-command schema as existing `corpus-forge-search.md` |
| Gemini | `.gemini/agents/corpus-curate.md` | New `.gemini/` dir; agent file mirrors the Claude SKILL.md but with Gemini front-matter — file MUST cite the Gemini agent-loading docs URL at the top, not invent format |
| Generic | section in `AGENTS.md` | Vendor-neutral prompt + tool list for any MCP client |

Each skill file shares a common chat-loop prompt template (`corpus_forge/curation/prompts.py`) so behavior is consistent across clients. Behavior:

1. Call `next_curation_target` (or `next_curation_batch` if user said "let's batch").
2. Present the entry to the user: text, current labels, missing fields, why-this-was-picked.
3. Ask up to 3 focused questions (label this? rename heading? add description? correct factual error?).
4. On user confirm, call `commit_curation` with the change set.
5. Loop: ask "next one?" Yes → step 1. No → summary of changes this session.

### Tests

- `tests/unit/test_curation_selector.py` — selection math under varied inputs (missing fields, low/high confidence, with/without seed_query, bulk grouping).
- `tests/unit/test_mcp_curation_tools.py` — `next_curation_target`, `next_curation_batch`, `commit_curation` dispatch, arg validation, error shapes.
- `tests/integration/test_curation_e2e.py` — testcontainers Postgres: ingest a tiny fixture corpus, call `next_curation_target`, apply `commit_curation`, verify writes landed.

### Done criteria

- [ ] `corpus_forge/curation/{selector,prompts,__init__}.py` exists with documented API.
- [ ] Three new MCP tools registered and wired through to selector + write surface.
- [ ] Four skill assets (Claude / OpenCode / Gemini / AGENTS recipe) land.
- [ ] Unit + integration tests green; coverage ≥90% on `corpus_forge/curation/`.
- [ ] `make ci` green.
- [ ] CHANGELOG `[Unreleased]` gets an "Added" entry: "Data-curation chat skill (Claude / Gemini / OpenCode) — pulls low-confidence or metadata-poor entries, facilitates a chat to improve them, and commits changes via MCP."

---

## J3 — README + branding reframe

### Goal
Top of the README leads with **"Chat with your data. Build a living, trainable corpus."** Training-data export stays the headline deliverable, *framed as the outcome of an active corpus, not a batch ETL job*. Add curation + estimator examples to Quickstart. Refresh CHANGELOG with the bundled J1+J2+J4 entries.

### Concrete edits

- **Header tagline**: replace `Forge a HuggingFace-format training corpus from your notes and chat history.` with `Chat with your data. Forge a living, trainable corpus that makes any model smarter.`
- **"Why corpus-forge" bullet 1** ("Training data, not search"): keep the "training data is the deliverable" claim but reframe — *"Training data is the deliverable. A living, growing corpus is the way you get there."*
- Add a new "Why corpus-forge" bullet right after the multi-format-ingest one: **"Human-in-the-loop curation."** One paragraph on the J4 chat skill: "Your model finds the weakest data; you fortify it in a conversation; your next training run is stronger."
- **Quickstart**: insert two new steps between current step 2 (`migrate`) and step 3 (`ingest`):
  - `corpus-forge estimate ~/Notes` (J1)
  - … and between steps 7 (`search`) and 8 (`export`): `corpus-forge curate --interactive` or skill-invoked equivalent (J4).
- **New H2**: "For AI assistants" — three-line section pointing at `CLAUDE.md` / `GEMINI.md` / `AGENTS.md` (J2).
- Optional: banner caption tweak from `forge a HuggingFace-format training corpus from your notes and chat history` to `forge a living, trainable corpus from your notes, chat history, and code` (banner image itself unchanged — only the alt-text caption).

### Tests
None — proof-read pass + `markdown-link-check` if available.

### Done criteria

- [ ] Tagline updated.
- [ ] Quickstart shows the two new commands.
- [ ] "For AI assistants" section exists and links to all three J2 files.
- [ ] CHANGELOG `[Unreleased]` consolidates the Phase J additions under a single "Phase J — Living Corpus" subhead.
- [ ] README diff under 250 lines (this is a reframe, not a rewrite).

---

## J5 — Beta cut → 0.1.0b2

### Goal
Ship.

### Steps

1. Bump `pyproject.toml` → `version = "0.1.0b2"`.
2. Move `[Unreleased]` block in CHANGELOG to a `## [0.1.0b2] — 2026-MM-DD` section. Add a new empty `[Unreleased]` header.
3. Write release notes (paste of the new CHANGELOG block) to `docs/release-notes/0.1.0b2.md` if that dir convention exists; else inline in the GitHub release.
4. Tag: `git tag -s v0.1.0b2 -m "0.1.0b2 — Living Corpus beta"`.
5. Verify CI on the tag passes — Phase I CI gates Homebrew tap / Scoop bucket / Docker / PyPI publish.
6. Smoke-test: install from PyPI in a clean venv, run `corpus-forge estimate`, `corpus-forge curate --help`, confirm MCP tool list shows the new tools.

### Done criteria

- [ ] Tag pushed.
- [ ] PyPI release succeeded (TestPyPI first if convention).
- [ ] Homebrew + Scoop manifests bumped (Phase I automation).
- [ ] Release notes published on GitHub.
- [ ] `corpus-forge --version` reports `0.1.0b2`.

---

## Cross-slice risks

| Risk | Mitigation |
|------|------------|
| Estimator heuristics drift from real ingest sizes | J1 ships a `sampling` flag (off by default) for refining; CHANGELOG calls them "first-pass heuristics; tune via `[estimate]` config." |
| `.gemini/` dir convention may not match Gemini CLI at ship time | J2 file MUST cite the live Gemini docs URL and reproduce that path; if Gemini's CLI requires a different layout, J2 carries a `# TODO: Gemini docs URL` placeholder and J4 client coverage drops Gemini until verified. |
| Curation MCP tools collide with existing write tools | New tools are explicit composites (`next_curation_target`, `commit_curation`) — they do not overload existing single-write tools. `commit_curation` internally calls the existing dispatch helpers. |
| iCloud sync race on commit | Per memory: verify `N files changed, +X/-Y` summary on every commit; recover via `git add` + new commit. Orchestrator (Claude Code session) owns this verification. |
| Worker can't sign commits | Per memory: orchestrator commits on workers' behalf at end of each slice. |
