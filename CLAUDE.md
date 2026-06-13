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
# Postgres + pgvector are core deps (no extra needed); `[hf]` is for the
# HuggingFace export. Add other opt-in extras (`code`, `ocr`, `whisper`,
# `mcp`, …) as needed.
uv tool install 'corpus-forge[hf]'

# Or the guided one-liner (asks the user a short prompt-tree).
curl -sSf https://raw.githubusercontent.com/ulmentflam/corpus-forge/main/install.sh | bash

# macOS via Homebrew tap.
brew install ulmentflam/tap/corpus-forge

# Windows via Scoop bucket.
scoop bucket add corpus-forge https://github.com/ulmentflam/scoop-corpus-forge
scoop install corpus-forge

# Windows / PowerShell one-liner (run elevated if you also want the service).
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force; iwr -useb https://raw.githubusercontent.com/ulmentflam/corpus-forge/main/install.ps1 -OutFile $env:TEMP\install.ps1; & $env:TEMP\install.ps1
```

Extras worth knowing:
- Postgres + pgvector are in the **core** deps — no extra needed. It's the
  first-class, multi-host backend.
- `[sqlite]` — single-machine fallback via `sqlite-vec`.
- `[hf]` — HuggingFace Datasets export.
- `[mcp]` — pulls the MCP-server transport deps if you're wiring corpus-forge
  into Claude Code / Claude Desktop / Gemini CLI / etc.
- `[ocr]`, `[whisper]`, `[code]`, `[multi-format]` — opt-in heavy stacks
  (Tesseract, faster-whisper, tree-sitter language pack, FastCDC). Tell the
  user before you pull them.
- `[rerank]`, `[retrieval]`, `[eval]`, `[analyze]`, `[fast-tier]`, `[tokens]`,
  `[openai]` — niche extras; see `pyproject.toml` for the full matrix.
- `[llama-cpp]` — in-process llama.cpp embeddings. The installers
  **auto-select the accelerated wheel**: `install.sh` / `install.ps1`
  detect the host accelerator (`nvidia-smi` → CUDA, Apple Silicon →
  Metal, else CPU) and install the matching prebuilt `llama-cpp-python`
  wheel, so a CUDA box gets GPU offload by default (no hand-edited
  `CMAKE_ARGS`). Override with `--llama-backend
  {auto|cuda|cudaNNN|metal|cpu|none}` (`-LlamaBackend` on PowerShell) or
  `CF_LLAMA_BACKEND`; `none` skips the extra. The `setup` `embedder=auto`
  choice pulls `[llama-cpp]` in automatically (its recommended lanes are
  all `provider="llama-cpp"`).

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

Brings the database schema up to date (idempotent). `install.sh` and `install.ps1` already run this for you on first install (failures are warned and ignored so an unreachable Postgres at install time doesn't break the installer); re-running it is a safe no-op. Run it again manually after every upgrade.

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

Reload Claude Code. The tools `mcp__corpus-forge__search`, `…__get_chunk`, `…__list_datasets`, `…__estimate_sync_size`, `…__check_update`, `…__next_curation_target`, `…__next_curation_batch`, and `…__commit_curation` (plus the existing write tools) should appear in `/mcp`.

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
- `corpus-forge-update` — update-awareness. Use when the user asks about updating corpus-forge or the server's MCP instructions advertise a newer version; calls `check_update` and recommends (never runs) the upgrade command. See `.claude/skills/corpus-forge-update/SKILL.md`.

Both skills are auto-discovered if this repo is on the Claude Code skill search path. If the user is wiring them into a different project, copy or symlink `.claude/skills/corpus-forge-search/` and `.claude/skills/corpus-curate/` into the target repo.

There's also a research-librarian subagent at `.claude/agents/corpus-forge-researcher.md` — invoke it via the `Agent` tool with `subagent_type: corpus-forge-researcher` when the parent agent needs grounded citations without spending its own context.

## 6. First-run sanity

Run in this exact order — each step relies on the previous one:

```bash
corpus-forge migrate                             # apply alembic revisions (creates the corpus.* schema +
                                                 #   the per-embedder HNSW indexes at the right strategy
                                                 #   for each embedder's dim — see revision 0015).
                                                 # Idempotent: re-runnable on every upgrade.
corpus-forge doctor                              # checks Python, backend, embedders, model endpoints
                                                 # AND HNSW index drift per embedder (will WARN if the
                                                 # index strategy doesn't match the configured dim and
                                                 # suggest `embedder repair-indexes` as the fix).
corpus-forge estimate ~/Notes                    # Postgres footprint estimate, no sync
                                                 # Honors <root>/.corpusignore AND ~/.config/corpus-forge/ignore
                                                 # (gitignore syntax; NEW in 0.1.0b3)
corpus-forge ingest --once                       # one-shot sync of the configured roots; on first hit
                                                 # creates each `embeddings_<name>` table with the right
                                                 # HNSW strategy (vector_cosine_ops for dim<=2000,
                                                 # halfvec projection for dim>2000)
corpus-forge embed -e qwen3_8b                   # backfill embeddings
corpus-forge bench embed --all                   # record this machine's embedder throughput
                                                 #   (model telemetry tables; see `models list`)
corpus-forge search "what does the daemon log on startup" --k 5
```

`install.sh` runs `setup` + `migrate` automatically; the manual sequence above is for incremental setups (e.g., adding a Postgres backend after originally running on SQLite) and for **upgrades** (revision 0015 needs `migrate` to fire so existing `vector_cosine_ops` indexes get rebuilt to `halfvec` for any embedder with `dim > 2000`).

Ask the user to run all of these on a small directory before pointing corpus-forge at their full vault. The `estimate` step is cheap (no network, no model calls) and will tell them roughly how many GB Postgres needs.

### Recovering from HNSW index drift

If `doctor` reports `embedder_indexes: WARN`, the per-embedder HNSW index strategy doesn't match the configured `dimension`. Two fixes — both safe to run repeatedly:

```bash
corpus-forge migrate                             # rebuilds every drifted index (revision 0015)
corpus-forge embedder repair-indexes             # audit + per-table diff; --apply rebuilds drifted ones
corpus-forge embedder repair-indexes --apply     # actually drop + recreate the drifted indexes
```

`migrate` does the same work as `repair-indexes --apply` but applied to every embedder atomically; `repair-indexes` is the targeted diagnostic.

### Add a second machine (one-command fleet onboarding)

When the user wants to add another host to an existing fleet (a new GPU
box draining the embedding backlog, a laptop joining for search, etc.),
**do not** walk them through `setup` interactively. The installer takes
a `--join <dsn>` / `-Join <dsn>` pass-through that does it in one
command:

```bash
# macOS / Linux / WSL
curl -sSf https://raw.githubusercontent.com/ulmentflam/corpus-forge/main/install.sh \
  | bash -s -- --join 'postgresql://primary.fleet:5432/corpus'
```

```powershell
# Windows — env-var form, single line, always paste-safe:
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force; $env:CF_JOIN_DSN = 'postgresql://primary.fleet:5432/corpus'; iwr -useb https://raw.githubusercontent.com/ulmentflam/corpus-forge/main/install.ps1 -OutFile $env:TEMP\install.ps1; & $env:TEMP\install.ps1
```

`CF_JOIN_DSN=<dsn>` is the env-var equivalent of `--join` / `-Join`.
The `-Join` parameter form is also supported, chained the same way:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force; iwr -useb https://raw.githubusercontent.com/ulmentflam/corpus-forge/main/install.ps1 -OutFile $env:TEMP\install.ps1; & $env:TEMP\install.ps1 -Join 'postgresql://primary.fleet:5432/corpus'
```

**Don't recommend `iwr | iex`** — `Invoke-Expression` doesn't
reliably handle scripts with a top-level `param()` block (its parser
stumbles on the preceding `<# #>` comment-based-help block). The
`-OutFile + &` form routes through PowerShell's normal `.ps1`
loader, which handles `param()` correctly.

A `ts://...` DSN (Tailscale-resolved) is also accepted — useful for
tailnet-native fleets.

**GPU joiner → CUDA llama-cpp in one command.** A GPU box joins the
fleet to drain the embedding backlog on its GPU, so the joiner
auto-detects its accelerator and installs the matching
`llama-cpp-python` wheel (CUDA / Metal) for in-process GPU embedding —
the `--llama-backend` / `-LlamaBackend` flag threads through the same
one-liner. Force it explicitly when detection can't see the GPU at
install time (driver not yet on PATH):

```bash
curl -sSf https://raw.githubusercontent.com/ulmentflam/corpus-forge/main/install.sh \
  | bash -s -- --join 'postgresql://primary.fleet:5432/corpus' --llama-backend cuda
```

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force; iwr -useb https://raw.githubusercontent.com/ulmentflam/corpus-forge/main/install.ps1 -OutFile $env:TEMP\install.ps1; & $env:TEMP\install.ps1 -Join 'postgresql://primary.fleet:5432/corpus' -LlamaBackend cuda
```

The installer **skips the question tree** (the fleet's primary publishes
the shared scope — embedders, retrieval tuning, classifier chains, even
dataset names/kinds — and the joiner pulls all of it via `setup --join`)
and **explicitly does NOT run `migrate`** — only the primary owns the
schema lifecycle. After the one-liner finishes:

- `doctor` already ran (smoke check). If it warned, re-run once the
  primary is reachable.
- `corpus-forge bench embed --all` — record the new host's per-lane
  throughput so the fleet's claim loop knows what it's best at.
- `corpus-forge service install` — install the daemon as a managed
  service so the host drains backlog continuously.

#### The `[service]` block — what the managed daemon does (RFC fleet-5)

`service install` now *actually* drains the backlog: the daemon runs a
continuous fleet-2 claim-based embed-drain loop, honoring `[embed] lanes`.
Two independent toggles under `[service]` select its role:

```toml
[service]
embed_drain  = true     # run the backlog drain loop in the daemon
ingest_watch = true      # also run the filesystem source watcher (default)
```

- **`embed_drain`** (default `false`) — when on (and the backend is
  Postgres), the daemon continuously claims + embeds the standing backlog
  via `corpus.embed_claims` (`FOR UPDATE SKIP LOCKED`), so N hosts drain one
  lane with zero duplicate compute. Backs off (`[embed] drain_idle_min` ..
  `drain_idle_max`, default 5 s → 5 min) when the backlog is empty.
- **`ingest_watch`** (default `true`) — run the source watcher. A
  **pure-drain GPU box** sets this `false`: it only embeds, never walks
  source roots.

`setup --join` seeds `embed_drain = true` for a joined host automatically,
and a capable-GPU probe defaults `ingest_watch = false` (pure drain). Override
either way with `--embed-drain/--no-embed-drain` and
`--ingest-watch/--no-ingest-watch` (env `CF_EMBED_DRAIN` / `CF_INGEST_WATCH`).
A plain local (non-`--join`) setup is unchanged — `embed_drain` stays off, so
there's no surprise background GPU loop on a laptop; ingest-time embedding
already covers a single-machine corpus.

`corpus-forge doctor` reports an `embed_drain` row (WARN if it's on but the
service isn't running), and `service status` shows the drain lanes. The
detached drift-prompt re-embed worker automatically yields any lane the
managed service already owns, so the two never double-embed.

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
| `TailscaleUnavailable` / `ts://` won't resolve | `corpus-forge doctor` names the failing endpoint. "daemon" reason → install/start Tailscale (`tailscale status` should report `Running`); "name" reason → the peer name isn't in this tailnet (check spelling). `ts://` in config while `[tailscale] enabled = false` fails at load — flip it on. |
| Second machine: no shared config after `setup --join` | `corpus-forge config pull` (dry-run) then `--apply`. "nothing published yet" means no host has run `config publish` — do it on the primary. Federation needs the `postgres` backend (SQLite WARNs in `doctor`). |
| Joined a fleet, backlog isn't draining | Is the drain loop on? `corpus-forge doctor` → the `embed_drain` row WARNs if `[service] embed_drain = true` but the managed service isn't running — `corpus-forge service install` then `service start`. Then `corpus-forge service status` shows the drain lanes ticking. Drain needs the `postgres` backend (no `corpus.embed_claims` coordination on SQLite). |
| In-process embedding slow / GPU idle on a CUDA box | The installed `llama-cpp-python` is the CPU-only wheel (the host had no driver on PATH at install time, or `pip`/`uv install` was used directly instead of the installer). Reinstall with `install.sh --llama-backend cuda` (`-LlamaBackend cuda` on PowerShell), or set `CF_LLAMA_BACKEND=cuda`. Force a specific CUDA variant with `--llama-backend cuda124` if auto-detection picks the wrong one. |

Full docs live under `docs/` in this repo. The `corpus-forge doctor` command's `--json` output is the quickest way to ship a diagnosis to the user.

---

## See also

- `AGENTS.md` — generic recipe for any MCP-speaking client.
- `GEMINI.md` — Gemini CLI / Gemini Code Assist setup.
- `README.md` — product overview and full feature list.
- `docs/architecture.md` — internals.
