# AGENTS.md — Generic setup recipe for any MCP-speaking assistant

This is the vendor-neutral guide for wiring **corpus-forge** into a coding assistant. Use it if your client isn't Claude (see [`CLAUDE.md`](CLAUDE.md)) or Gemini (see [`GEMINI.md`](GEMINI.md)) — for example **OpenCode**, **Cursor**, **Zed**, **Continue**, **Cline**, or anything else that speaks the [Model Context Protocol](https://modelcontextprotocol.io).

---

## What corpus-forge is

**Chat with your data.** corpus-forge turns a directory tree of notes, docs, code, PDFs, chat history, audio, and video into a *living, trainable corpus*: a searchable index you can query with citations, a curation loop you can use to fortify weak entries with the help of an assistant, and a HuggingFace-format export that's the deliverable for fine-tuning.

Connected to an MCP-speaking assistant, corpus-forge exposes:

| Tool | Purpose |
|------|---------|
| `search` | Hybrid (dense + lexical) retrieval over the indexed corpus. Optional cross-encoder rerank. |
| `get_chunk` | Full record of a single chunk by id. |
| `list_datasets` | Catalogue of indexed datasets with row/chunk counts. |
| `estimate_sync_size` | Predicts Postgres footprint of syncing a directory tree — pure prediction, no I/O. |
| `next_curation_target` / `next_curation_batch` | Ranker-driven "what entry most needs my help right now?" |
| `commit_curation` | Atomic multi-write covering label adds/removes, metadata, description, feedback. |
| `add_label` / `remove_label` / `set_metadata` / `set_description` / `add_feedback` | Direct write surface, used internally by `commit_curation` but available stand-alone. |
| `list_labels` | Enumerate labels on a chunk / document / conversation. |
| `append_conversation` / `append_message` / `render_conversation` / `list_chat_templates` / `register_template` / `register_session` | Chat-corpus authoring + templated rendering. |

---

## 1. Install corpus-forge

Pick one. The tool runs the same way regardless of how you installed it.

```bash
# Recommended on macOS / Linux / WSL — Astral's uv, ships in a tool venv.
# Postgres + pgvector are core deps; `[hf]` is for the HuggingFace export.
uv tool install 'corpus-forge[hf]'

# Or the guided one-liner (asks a short prompt-tree).
curl -sSf https://raw.githubusercontent.com/ulmentflam/corpus-forge/main/install.sh | bash

# macOS via Homebrew tap.
brew install ulmentflam/tap/corpus-forge

# Windows via Scoop bucket.
scoop bucket add corpus-forge https://github.com/ulmentflam/scoop-corpus-forge
scoop install corpus-forge

# Windows / PowerShell one-liner (run elevated if you also want the service).
iwr -useb https://raw.githubusercontent.com/ulmentflam/corpus-forge/main/install.ps1 | iex
```

Extras:
- Postgres + pgvector are in the **core** deps — no extra needed.
- `[sqlite]` — single-machine fallback via `sqlite-vec`.
- `[hf]` — HuggingFace Datasets export.
- `[mcp]` — MCP-server transport (Claude Code / Desktop / Gemini CLI / etc.).
- `[ocr]`, `[whisper]`, `[code]`, `[multi-format]` — opt-in heavy stacks
  (Tesseract, faster-whisper, tree-sitter language pack, FastCDC). Decide
  before installing.

Then:

```bash
corpus-forge setup           # interactive config wizard
corpus-forge migrate         # schema up to date
```

## 2. The canonical MCP block

Every supported client speaks the same launch contract. Drop this into whatever your client uses for MCP server definitions (file paths vary; keys are the standard ones from the MCP spec).

```json
{
  "mcpServers": {
    "corpus-forge": {
      "command": "corpus-forge",
      "args": ["mcp", "serve", "--transport", "stdio"],
      "env": { "CF_CONFIG": "~/.config/corpus-forge/config.toml" }
    }
  }
}
```

Optional knobs:
- `--dataset <name>` — pin a default dataset so callers can omit it.
- Replace `corpus-forge` with an absolute path if the binary is not on the client's `PATH`.
- Set `CF_LOG=info` (or `debug`) in `env` to send structured logs to the client's stderr pane.

## 3. Per-client placement

| Client | Where the MCP block goes | Notes |
|--------|--------------------------|-------|
| **Claude Code** | `.mcp.json` in the project, or `~/.claude/mcp.json` user-scoped. See `CLAUDE.md`. | Hot-reloads on save in most builds; otherwise `/mcp reconnect`. |
| **Claude Desktop** | `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) / `%APPDATA%\Claude\claude_desktop_config.json` (Windows). | Quit and relaunch. |
| **Gemini CLI** | `~/.gemini/settings.json`. See `GEMINI.md`. | Re-launch `gemini`. |
| **OpenCode** | `.opencode/config.json` (project-scoped) or `~/.config/opencode/config.json` (user-scoped). | The repo ships `.opencode/command/corpus-forge-search.md` and `.opencode/command/corpus-curate.md` as slash commands. |
| **Cursor** | Settings → "MCP Servers" → paste the block (Cursor stores it under `~/.cursor/mcp.json`). | Restart the Cursor agent panel. |
| **Zed** | `~/.config/zed/settings.json`, key `experimental.context_servers` (Zed's MCP wiring is preview at time of writing — check Zed's docs for the current key). | Reload window. |
| **Continue** | `.continue/config.json`, under `experimental.modelContextProtocolServers`. | Run "Continue: Reload Window" from the command palette. |
| **Cline** (VS Code) | Cline settings UI → "MCP Servers" → paste the block. | Reload VS Code. |
| **Anything else** | Anywhere your client takes a `{command, args, env}` MCP definition. | If it doesn't, run `corpus-forge mcp serve --transport stdio` manually and ask the user to point their client at the resulting stdio. |

## 4. Skill / slash-command files

The repo ships skill assets that work across clients with minimal porting:

```
.claude/skills/corpus-forge-search/SKILL.md
.claude/skills/corpus-curate/SKILL.md
.opencode/command/corpus-forge-search.md
.opencode/command/corpus-curate.md
.gemini/agents/corpus-forge-search.md
.gemini/agents/corpus-curate.md
```

For clients without a native slash-command system, copy the prose of `SKILL.md` into the assistant's system prompt or "instructions" panel — the prompts are intentionally vendor-neutral and reference only the MCP tool names.

## 5. First-run sanity

Ask the user to run these in order. The `estimate` step is cheap (no network, no model calls) — use it to size up before sync.

```bash
corpus-forge migrate                             # apply alembic revisions (creates corpus.* + adapts per-
                                                 #   embedder HNSW indexes; revision 0015 picks vector
                                                 #   vs. halfvec ops based on each embedder's dim).
corpus-forge doctor                              # checks Python, backend, embedders, model endpoints,
                                                 # AND HNSW index drift per embedder (WARN with a
                                                 # repair recommendation when drift is detected).
corpus-forge estimate ~/Notes                    # Postgres footprint, no sync
                                                 # Honors <root>/.corpusignore AND ~/.config/corpus-forge/ignore
                                                 # (gitignore syntax; NEW in 0.1.0b3)
corpus-forge ingest --once                       # one-shot sync of the configured roots
corpus-forge embed -e qwen3_8b                   # backfill embeddings
corpus-forge search "what does the daemon log on startup" --k 5
```

If `doctor` reports `embedder_indexes: WARN`, rerun `corpus-forge migrate` (rebuilds every drifted index) or `corpus-forge embedder repair-indexes --apply` (targeted rebuild). Both are idempotent.

## 6. Curation loop (vendor-neutral playbook)

When the user wants to improve corpus quality, follow this prompt skeleton — it works for any MCP-speaking assistant:

1. **Pick** — call `next_curation_target(dataset=<name>)` (or `next_curation_batch(limit=N)` if the user said "let's batch"). The response includes `chunk_id`, `text`, `current_labels`, `current_metadata`, `missing_fields`, and a `score_breakdown` showing why this entry was selected.
2. **Present** — show the user a tight summary (≤ 6 lines): preview of the text, current labels, and the one most-improvable dimension (missing description? wrong label? thin metadata?). Don't list every gap.
3. **Ask** — at most three focused questions targeting the highest-value gap.
4. **Commit** — call `commit_curation(chunk_id=..., add_labels=[...], remove_labels=[...], set_metadata={...}, set_description="...", feedback="...")`. The server applies the writes atomically.
5. **Loop** — offer "next one?" — yes → step 1, no → session summary.

In bulk mode, `next_curation_batch` groups candidates by `(source stem, classifier label)` so one chat can ratify a coherent group of similar entries.

## 7. Troubleshooting

| Symptom | First step |
|---------|------------|
| Tools don't appear | Run `corpus-forge mcp serve --transport stdio` from a terminal. If it crashes, the stderr names the missing extra. |
| `corpus-forge` not found | `which corpus-forge` (POSIX) / `Get-Command corpus-forge` (PowerShell). If installed via `uv tool`, `uv tool list` confirms the venv path. |
| Client doesn't see the new MCP block | Confirm the JSON parses (`jq . <config>`); confirm the client supports MCP at all in your current version; restart the client. |
| Slow `search`, no rerank | Reranker is opt-in (`rerank=true`). First call triggers a one-time ~600 MB BGE download. Skip on latency-sensitive paths. |
| "no embeddings for dataset" | Run `corpus-forge embed -e <embedder_name>` once after `ingest`. |
| Postgres ENOSPC | `corpus-forge estimate <path>` *before* sync. Tune the `[estimate]` block in config to model your TOAST compression ratio. |

Full docs live under `docs/` in this repo. The `corpus-forge doctor --json` output is the quickest way to ship a diagnosis to the user.

---

## See also

- `CLAUDE.md` — Claude Code / Claude Desktop setup.
- `GEMINI.md` — Gemini CLI / Gemini Code Assist setup.
- `README.md` — product overview and full feature list.
- `docs/architecture.md` — internals.
