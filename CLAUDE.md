# CLAUDE.md — Setup guide for Claude (Code, Desktop, and any MCP client)

This file briefs a Claude-family assistant on how to install **corpus-forge**, wire it as an MCP server, register the curation and search skills, and verify the wiring. The user reading over your shoulder may also follow it directly.

---

## What corpus-forge is

**Chat with your data.** corpus-forge turns a directory tree of notes, docs, code, PDFs, chat history, audio, and video into a *living, trainable corpus*: a searchable index you can query with citations, a curation loop you (and Claude) can use to fortify weak entries, and a HuggingFace-format export that's the deliverable for fine-tuning.

Once a corpus-forge MCP server is connected, Claude can:
- **Ground answers** in the user's actual data (`search`, `get_chunk`, `list_datasets`).
- **Estimate cost** before they sync a new folder (`estimate_sync_size`).
- **Curate the corpus** with the user — find weak entries, chat to improve them, commit the edits (`next_curation_target`, `next_curation_batch`, `commit_curation`, plus the existing `add_label` / `set_metadata` / `set_description` / `add_feedback` write tools).

---

## 1. Install

Pick one. Reach for the first option that matches the user's platform unless they tell you otherwise.

```bash
# Recommended on macOS / Linux / WSL — Astral's uv, ships in a tool venv.
uv tool install 'corpus-forge[postgres,hf]'

# Or the guided one-liner (asks the user a short prompt-tree).
curl -sSf https://raw.githubusercontent.com/ulmentflam/corpus-forge/main/install.sh | sh

# macOS via Homebrew tap.
brew install ulmentflam/tap/corpus-forge

# Windows via Scoop bucket.
scoop bucket add corpus-forge https://github.com/ulmentflam/scoop-corpus-forge
scoop install corpus-forge

# Windows / PowerShell one-liner (run elevated if you also want the service).
iwr -useb https://raw.githubusercontent.com/ulmentflam/corpus-forge/main/install.ps1 | iex
```

Extras worth knowing:
- `[postgres]` — first-class backend, multi-host friendly, pgvector required.
- `[sqlite]` — single-machine fallback via `sqlite-vec`.
- `[hf]` — HuggingFace Datasets export.
- `[ocr]`, `[whisper]`, `[clip]`, `[code]` — opt-in heavy stacks. Tell the user before you pull them.

## 2. Configure

```bash
corpus-forge setup            # interactive wizard, renders ~/.config/corpus-forge/config.toml
# or, unattended:
CF_NON_INTERACTIVE=1 corpus-forge setup --backend postgres --embedder qwen3_8b
```

The config lives at `~/.config/corpus-forge/config.toml` on macOS/Linux and `%APPDATA%\corpus-forge\config.toml` on Windows. A fully-commented example ships at `config.example.toml`.

## 3. Migrate

```bash
corpus-forge migrate
```

Brings the database schema up to date (idempotent). Run this once after install and after every upgrade.

## 4. Wire the MCP server into Claude

### Claude Code (the CLI / Claude.ai's coding agent)

Add to project `.mcp.json` (or the user-scoped equivalent):

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

Reload Claude Code. The tools `mcp__corpus-forge__search`, `…__get_chunk`, `…__list_datasets`, `…__estimate_sync_size`, `…__next_curation_target`, `…__next_curation_batch`, and `…__commit_curation` (plus the existing write tools) should appear in `/mcp`.

### Claude Desktop

Edit `claude_desktop_config.json`:
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "corpus-forge": {
      "command": "corpus-forge",
      "args": ["mcp", "serve", "--transport", "stdio", "--dataset", "default"],
      "env": { "CF_CONFIG": "~/.config/corpus-forge/config.toml" }
    }
  }
}
```

Quit and relaunch Claude Desktop. Tools appear under the hammer icon.

### Anthropic API (Managed MCP)

If the user is hosting their own assistant with the Anthropic SDK, register corpus-forge with `client.beta.tools.register_mcp(...)` or, for Managed Agents, drop the same `{command, args, env}` block into the agent's `mcp_servers` config. Pin the `command` to an absolute path if `corpus-forge` is not on the agent's `PATH`.

## 5. Register the corpus-forge skills

corpus-forge ships **two** first-class Claude skills in this repo:

- `corpus-forge-search` — search-and-cite. Use whenever the user asks a question whose answer plausibly lives in the indexed corpus. See `.claude/skills/corpus-forge-search/SKILL.md`.
- `corpus-curate` — the data-improvement chat loop. Use when the user says "let's curate," "improve my data," "fix labels," or when you notice many recently-ingested entries look thin on metadata. See `.claude/skills/corpus-curate/SKILL.md`.

Both skills are auto-discovered if this repo is on the Claude Code skill search path. If the user is wiring them into a different project, copy or symlink `.claude/skills/corpus-forge-search/` and `.claude/skills/corpus-curate/` into the target repo.

There's also a research-librarian subagent at `.claude/agents/corpus-forge-researcher.md` — invoke it via the `Agent` tool with `subagent_type: corpus-forge-researcher` when the parent agent needs grounded citations without spending its own context.

## 6. First-run sanity

```bash
corpus-forge doctor                              # checks Python, backend, embedders, model endpoints
corpus-forge estimate ~/Notes                    # Postgres footprint estimate, no sync
                                                 # Honors <root>/.corpusignore AND ~/.config/corpus-forge/ignore
                                                 # (gitignore syntax; NEW in 0.1.0b3)
corpus-forge ingest --once                       # one-shot sync of the configured roots
corpus-forge embed -e qwen3_8b                   # backfill embeddings
corpus-forge search "what does the daemon log on startup" --k 5
```

Ask the user to run all of these on a small directory before pointing corpus-forge at their full vault. The `estimate` step is cheap (no network, no model calls) and will tell them roughly how many GB Postgres needs.

## 7. Curation loop quickstart (for the assistant)

When the user wants to improve corpus quality:

1. Call `mcp__corpus-forge__next_curation_target(dataset="<name>")`. The response includes `chunk_id`, the text, current labels, missing fields, and a score breakdown explaining why this entry was picked.
2. Present the entry to the user concisely. State the *one* most-improvable dimension first (missing description? wrong label? thin metadata?).
3. Ask up to three focused questions. Don't drag the user through a form — pick the highest-value gap.
4. On confirm, call `mcp__corpus-forge__commit_curation(chunk_id=..., add_labels=[...], set_metadata={...}, set_description="...", feedback="...")`. The MCP server wraps the writes into a single atomic operation.
5. Offer "next one?" — yes loops to step 1, no prints a session summary.

For bulk mode (many similar entries, one chat): call `next_curation_batch(limit=N)` instead. The selector groups by `(source stem, classifier label)` so one ratification ride covers many.

## 8. Troubleshooting

| Symptom | First step |
|---------|------------|
| Tools don't appear in `/mcp` | `corpus-forge mcp serve --transport stdio` manually; if it crashes, the stderr names the missing extra. |
| `corpus-forge` not found | `which corpus-forge` (POSIX) / `Get-Command corpus-forge` (PowerShell). If installed via `uv tool`, `uv tool list` confirms the venv path. |
| Slow `search`, no rerank | Reranker is opt-in (`rerank=true`). First call triggers a one-time ~600 MB BGE download. Skip on latency-sensitive paths. |
| "no embeddings for dataset" | Run `corpus-forge embed -e <embedder_name>` once after `ingest`. |
| Postgres ENOSPC | `corpus-forge estimate <path>` *before* sync. Tune the `[estimate]` block in config to model your TOAST compression ratio. |

Full docs live under `docs/` in this repo. The `corpus-forge doctor` command's `--json` output is the quickest way to ship a diagnosis to the user.

---

## See also

- `AGENTS.md` — generic recipe for any MCP-speaking client.
- `GEMINI.md` — Gemini CLI / Gemini Code Assist setup.
- `README.md` — product overview and full feature list.
- `docs/architecture.md` — internals.
