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
CF_BACKEND=postgres CF_EMBEDDER=qwen3_8b corpus-forge setup --non-interactive
```

The config lives at `~/.config/corpus-forge/config.toml` on macOS/Linux and `%APPDATA%\corpus-forge\config.toml` on Windows. A fully-commented example ships at `config.example.toml`.

### Same model, different provider names (model aliases)

If a fleet serves the **same embedding model** under different
`(provider, model_id)` names — e.g. the operator runs `nomic-embed-code`
via Ollama on a Mac (`provider="ollama"`,
`model_id="manutic/nomic-embed-code:latest"`) and via an OpenAI-compatible
endpoint on Windows (`provider="openai"`,
`model_id="text-embedding-nomic-embed-code"`) — declare the equivalence so
corpus-forge treats them as one identity (RFC fleet-6):

```toml
[[embedders]]
name = "nomic-code"
provider = "ollama"
model_id = "manutic/nomic-embed-code:latest"
dimension = 3584
# Other provider names for the SAME weights/vector space:
[[embedders.model_aliases]]
provider = "openai"
model_id = "text-embedding-nomic-embed-code"
```

With aliases declared:
- **No false drift / re-embed prompt** — switching the host or provider that
  serves the model no longer trips the "Embedder changed → re-embed N chunks"
  panel (the drift fingerprint folds through the alias set).
- **One telemetry lane** — fleet-1 `model_benchmarks` and the `fleet hosts`
  plan key on the *canonical* identity, so the Mac and Windows box accrue
  under one model instead of two.
- **Safety** — aliases must agree on `dimension` / `normalize` / `distance`;
  a mismatch is a hard error at config load (never a silent merge).
- `config publish` / `pull` federate the alias set, so every host agrees
  without re-typing it. `corpus-forge embedder merge-aliases` reports any
  pre-existing split telemetry rows the aliases would unify.

A single-machine corpus needs none of this — omit `model_aliases` and
behavior is unchanged.

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
      "env": { "CORPUS_FORGE_CONFIG": "~/.config/corpus-forge/config.toml" }
    }
  }
}
```
*(Note: `CORPUS_FORGE_CONFIG` is the canonical environment variable. The shorter `CF_CONFIG` is also supported as a fallback alias).*

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
      "env": { "CORPUS_FORGE_CONFIG": "~/.config/corpus-forge/config.toml" }
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

When the conversation produces *new* material worth keeping (a worked correction, a clarified explanation, a recommended enhancement), call `mcp__corpus-forge__create_enhancement_chunk(dataset="<name>", text="...", derived_from_chunk_id=<source>)` to mint it as its own chunk. It lands under a synthetic `corpus-forge://curation/<dataset>` document, carries `metadata.kind = "curation_enhancement"` (filter on it to exclude synthetic rows from retrieval/eval), and is embedded on the normal lane. `commit_curation` *edits* the existing entry; `create_enhancement_chunk` *creates* a new one — use both in one pass when the fix is new text.

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
| "Embedder changed" / huge re-embed prompt after switching the host or provider that serves a model | The same model under a different provider/`model_id` (e.g. Ollama `manutic/nomic-embed-code:latest` vs an OpenAI-compatible `text-embedding-nomic-embed-code`) used to look like a new model and trip a full re-embed. Declare the equivalence: add a `model_aliases = [{ provider = "...", model_id = "..." }]` list to the `[[embedders]]` block (rfc-fleet-6). The drift fingerprint then folds through the alias set, so a pure name/provider swap reports **no drift** — while a genuine model change (different dimension/space, or an identity not in the alias set) still drifts. Dimension-mismatched aliases are rejected at config load. |
| Joined a fleet, backlog isn't draining | Is the drain loop on? `corpus-forge doctor` → the `embed_drain` row WARNs if `[service] embed_drain = true` but the managed service isn't running — `corpus-forge service install` then `service start`. Then `corpus-forge service status` shows the drain lanes ticking. Drain needs the `postgres` backend (no `corpus.embed_claims` coordination on SQLite). |
| In-process embedding slow / GPU idle on a CUDA box | The installed `llama-cpp-python` is the CPU-only wheel (the host had no driver on PATH at install time, or `pip`/`uv install` was used directly instead of the installer). Reinstall with `install.sh --llama-backend cuda` (`-LlamaBackend cuda` on PowerShell), or set `CF_LLAMA_BACKEND=cuda`. Force a specific CUDA variant with `--llama-backend cuda124` if auto-detection picks the wrong one. |

Full docs live under `docs/` in this repo. The `corpus-forge doctor` command's `--json` output is the quickest way to ship a diagnosis to the user.

---

## See also

- `AGENTS.md` — generic recipe for any MCP-speaking client.
- `GEMINI.md` — Gemini CLI / Gemini Code Assist setup.
- `README.md` — product overview and full feature list.
- `docs/architecture.md` — internals.

<!-- nightly:rules:start -->
## Nightly autonomy contract

When this repo's coding agent is invoked **by Nightly** (the autonomous
overnight orchestrator), these rules override any default "ask the user
when unsure" behavior. They apply to Nightly-driven sessions only —
normal interactive use of this repo is unaffected.

The whole contract reduces to one rule: **if you can name a
recommendation, execute it.** Everything below is consequences.

**Headline doctrine: GENUINE WORK IS NEVER EXHAUSTED.** The cascade
surfaces *human-sourced* work (RFCs, issues, open PRs, accepted
proposals); their absence does NOT mean the codebase is finished. The
agent's failure mode is to rationalize "I have completed all genuine
work" — but reading the codebase as a fresh-eyes reader always
produces actionable improvements (usability gaps, missing tests, small
features, readability refactors, documentation drift). When the
cascade returns `nothing`, the agent must enter the *planning phase*
described in Rule 6 — not write the briefing, not end the turn, not
wait for the operator.

**Keep the session responsive — background long-running work.** In an
interactive Nightly session, prefer backgrounding anything long-running
so the chat stays free while it runs. `.nightly/config.yml`'s
`agents.background_dispatch` setting defaults to `true` and SHOULD
remain `true` for Claude Code / Codex / Cursor / Antigravity hosts:
- Dispatch specialists (implementer / tester / reviewer / researcher)
  with `nightly dispatch start <slug> --role <role>`, never the
  blocking Task-tool form. Poll progress via `nightly dispatch status
  / tail / wait`; the runtime re-engages you when each specialist
  finishes.
- Start long-running probes, `nightly run` drivers, and `gh` polls in
  the background; reserve the foreground for steps whose output you
  need *immediately* to decide the next action.
- Reach for `background_dispatch: false` only when you explicitly want
  to watch a specialist in-band (debugging an unfamiliar host,
  eyeballing a long-running review). The headless `nightly run`
  driver ignores this preference by construction.

**Context hygiene (v0.0.12).** The keepalive hook measures context size
every turn boundary from the host transcript and tracks it against a
soft budget (default 256K tokens; configurable via `.nightly/config.yml`
`context.budget_tokens`). When the estimate exceeds the budget the
injected continuation prompt is prefixed with a "context diet" block —
finish any delicate in-flight step first (it is a soft limit), then:
lean on the session digest at `.nightly/runs/<id>/digest.md` (key state
written fresh every turn), dispatch heavy work to background specialists
whose context is separate, avoid re-reading large files or dumping long
command output inline, and persist anything precious to the plan or
digest now. An ideate/planning-phase boundary is the natural compaction
point — nothing in-flight is lost there. Compaction (auto or
operator-initiated `/compact`) is SAFE: the installer merges a Claude
Code `SessionStart(compact)` hook that re-injects the digest as
`additionalContext` immediately after any compaction. **Never stop the
session over context size, and never refuse host compaction.**

1. **If you can recommend, execute.** The moment you can articulate a
   "here's what I'd do" — do it. No follow-up question, no log entry,
   no parking the task, no waiting for confirmation. Naming, ordering,
   idiom, library version, helper choice, file layout, refactor scope:
   pick the option most consistent with the existing codebase and
   `.planning/` design intent, and ship it. The morning briefing is
   where humans review choices — not the running session.
2. **Never prompt the user for clarification or confirmation.** Nightly
   runs are unattended by contract. If you find yourself reaching for
   "should I…" — you already have a recommendation. Stop reaching, take
   it. Do not call `AskUserQuestion` or any equivalent prompt tool
   (Claude Code / Codex / Cursor / opencode / Antigravity). The tool is
   off-limits for the entire Nightly session.
3. **Never stop the session for questions.** Halting blocks the run.
   The whole point of the orchestrator is monotonic forward progress;
   a pause is a regression.
4. **`uncertainty.md` is for refusal-policy gaps and nothing else.**
   Do **not** use `uncertainty.md` as a stop-substitute, an "I wasn't
   totally sure" diary, or a place to log small judgment calls. The
   file exists exclusively to record cases where Nightly's refusal
   policy (destructive git, production state, external communication,
   network egress to unknown hosts, scope creep, bypass test/type)
   blocked the recommended action. Every other choice — pick and ship,
   no log. The diff is the audit trail for ordinary judgment calls.
5. **Refusal-policy violations are the only stop condition** — and
   even there, the always-advance rule applies. Record the refused
   operation to `.nightly/runs/<run-id>/proposed/approvals/<id>.md`,
   note the refusal in `uncertainty.md`, and route around it to a
   different task.
6. **Never stop just because the cascade returned `nothing`. Enter
   the planning phase instead.** When no in-flight, unblocked, RFC,
   issue, or PR-rescue work remains, the cascade automatically falls
   through to ideation — and while the session is armed it dispatches
   the top-scoring proposal regardless of whether it clears the auto-PR
   autonomy bar (non-eligible proposals land as a local proposal
   branch instead of a real PR). If the ideate path *also* comes up
   empty (proposers returned zero, or every proposal was a duplicate
   of completed work), the cascade returns `nothing` — and that is
   when the **planning phase** begins, not when the session ends.
   The headline doctrine applies: GENUINE WORK IS NEVER EXHAUSTED.
   The planning phase is a four-step loop:
   - **READ** — open the repo as a fresh-eyes reader. Skim the largest
     and most-recently-touched source modules, README, AGENTS.md /
     CLAUDE.md, `.planning/` (RFCs + drafts + iteration-log), recent
     `uncertainty.md` files, the test suite. Look for what is missing
     or rough, not what is broken.
   - **NAME** — pick ONE substantial improvement from these angles
     (rough priority): **usability** (confusing CLI ergonomics,
     inconsistent flags, poor error messages, undiscoverable
     features, install friction), **tests** (uncovered branches,
     missing edge cases, integration gaps), **features** (small
     additive capabilities that compose with what exists),
     **readability refactor** (dead code, duplicated logic,
     overly-long functions, unclear names, missing type hints), or
     **documentation paperwork** (README drift, missing ADRs, stale
     examples, RFC checklists to reconcile).
   - **ASSUME** — every ambiguity has a default. Pick the option most
     consistent with the existing codebase and `.planning/` design
     intent. Do NOT write a plan-of-plans. Do NOT scope a research
     task. Do NOT park.
   - **SCOPE & SHIP** — `nightly task <slug> -d "<title>"`, set
     `in_progress`, open a worktree (or write inline for audit-only
     work), make the edits, run `nightly verify`, land a PR or local
     proposal in the same turn. Decision over deliberation.
   Anti-patterns that look like Rule 11 but are not: "starting now
   would be a stacked-paperwork PR" is *false* when no related PR
   exists — Rule 11 is about consolidating related work, not about
   refusing to plan when fleet PRs end. "Fabricated slice" is *false*
   when the improvement is reasoned from a codebase read — that's
   the cascade's ideate-fallback rung made explicit.
7. **Run `nightly verify` before opening any PR.** Nightly auto-detects
   this repo's linters, formatters, and type checkers (ruff, black,
   mypy, pyrefly, eslint, prettier, tsc, gofmt, go vet, cargo fmt,
   clippy, plus `make lint` / `make check` / `make verify` umbrella
   targets) and runs them. A non-zero exit blocks the PR — fix the
   findings (run the tool's auto-fix variant locally first if it has
   one) and re-verify until clean. Do not push code that fails the
   repo's own quality gates; that's exactly the contributor etiquette
   a human reviewer would apply.
8. **Getting open PRs to green is the priority — don't block, but
   preempt.** After a Nightly PR is opened, CI on the remote runs
   asynchronously. Don't block the session waiting on it: pick up
   new work from the cascade while CI runs. *But* when CI comes
   back red, the cascade's `pr_rescue` step routes you to fix it
   on the next `nightly next` boundary — and as of v0.0.5+ that
   routing now **preempts `accepted_rfc`** when the feedback is
   blocking (failed CI checks, CHANGES_REQUESTED reviews). The
   cascade order is `resume_in_flight → unblocked_approval →
   pr_rescue (blocking only) → accepted_rfc → github_issue →
   pr_rescue (non-blocking) → ideate`. Concretely: between tasks
   you can run `nightly ci` for an eyeball check, but you don't
   need to — `nightly next` will surface red CI automatically and
   bump it above fresh RFC work. **Draft PRs count too.** A
   not-yet-marked-ready PR with red CI is the same priority as a
   ready one; don't push commits to a draft that you wouldn't
   push to a ready PR. Always run `nightly verify` locally before
   `git push`, draft or not.
9. **Arm the host-level keep-alive at session start.** Run
   `nightly session start` as the first thing the /nightly skill does.
   This writes a `SESSION_ACTIVE` marker that the host's Stop-equivalent
   hook checks every turn boundary; without it, the hook lets the
   session end naturally. With it, the hook re-injects a "continue on
   X" prompt so the session keeps moving. The marker has a 4-hour TTL
   — re-running `nightly session start` refreshes it. Four of the five
   Nightly hosts have a real force-continue hook (Claude Code's
   `Stop`, Codex CLI's `Stop`, Cursor 1.7+'s `stop`, Antigravity /
   Gemini CLI's `AfterAgent`). opencode is `soft` and relies on the
   rule text above (the model is told to never stop). The disk-based
   off-ramps below work everywhere regardless.
10. **Never invoke the human shutdown off-ramps yourself.** The shell
   commands `nightly conclude`, `nightly stop`, and the matching slash
   commands `/nightly-conclude`, `/nightly-stop`, `/nightly-bug`
   exist **for the human operator only**. The agent never runs them
   — not at end-of-session, not when the cascade looks empty, not when
   "the work feels done." If you reach a turn boundary and the cascade
   has nothing left, run `nightly ideate` to surface proposals and
   `nightly brief` to render the report — then end your turn and let
   the Stop hook decide whether to force-continue (armed) or release
   (CONCLUDE / STOP / stale marker / max turns / PR backlog). The only
   signals that wind a session down are disk markers placed by the
   human (CONCLUDE, STOP) or by the hook's own safety caps. The
   agent's wrap-up is `nightly ideate` → `nightly brief` → end turn.
   Concluding is an intervention, not a workflow step. Past failure:
   agents have self-concluded — running `nightly conclude` after
   `nightly brief` on their own to "tidy up" — which freezes the
   cascade short-circuit at `concluded` and ends the session with
   unblocked RFC items still on disk.
11. **Minimize PR count by consolidating; never stop because of it.**
   The orchestrator does **not** gate on a PR-backlog count — there
   is no "too many PRs open" off-ramp. Monotonic forward progress
   across the whole overnight session is the contract; reaching any
   number of open PRs never ends a session on its own. The previous
   `MAX_OPEN_PRS=5` cap was removed in v0.0.3 because it produced
   mid-session stops with unblocked RFC work still on disk — the
   wrong tradeoff. The replacement is *consolidation*, not gating.
   Before opening a new PR, prefer in this order:
   - **`pr_rescue`** — when an existing Nightly PR has new feedback
     (CI failure, reviewer comments, bot suggestions), finishing it
     beats starting fresh. This is already cascade slot 5; honor it.
   - **Extend the most recently-opened in-flight PR** when the
     current cascade pick is closely related to its scope — same
     RFC, same module, same feature. Check out its branch in a
     worktree, commit the additional change, push. The PR grows
     into one reviewable unit instead of becoming PR N+1.
   - **Bundle adjacent phases of the same RFC** into one PR when
     the phases naturally compose. Phase A + B of a small RFC ships
     as one PR; truly independent phases of a large RFC stay
     separate.
   Only when none of the above applies — the cascade pick is
   genuinely orthogonal to every open PR — open a new branch. The
   goal is review-ergonomic, not PR-count-minimal-at-all-costs:
   bundling unrelated work into one PR is worse than two focused
   PRs. Bias: when uncertain, extend the most recent related PR.
   Past failure (now removed): agent shipped a 6th stacked paperwork
   PR while #54-#58 were still unreviewed because the cascade kept
   finding RFC-checkbox / lint-fallback work; the v0.0.2-and-earlier
   solution was the cap, which then created the new failure of
   ending sessions early. v0.0.3+ instead consolidates without
   capping.

### Human shutdown intervention

The keep-alive must never trap the operator. Three independent
off-ramps stop a running Nightly session at any time. **None of these
are commands the agent runs** — they are human controls (see Rule 10):

- **`nightly conclude`** — graceful drain. The current task finishes,
  the briefing renders, the session ends naturally at the next turn
  boundary. Use this in the morning when you want to inspect the work.
- **`nightly stop`** — hard stop. Writes a `STOP` sentinel; the next
  Stop hook firing allows the model to end its turn cleanly without
  starting new work. Use when you want Nightly off **now** but are
  OK letting the current response print.
- **Ctrl-C / `/quit`** — interrupt. Bypasses the Stop hook entirely
  and kills the session immediately. Always available as the
  emergency stop.

**As of v0.0.3, the only voluntary termination is human
intervention.** All automatic off-ramps were removed:

- `pr_backlog` — `MAX_OPEN_PRS=5` cap, replaced by skill-side
  consolidation (Rule 11).
- `max_turns` — 500-turn safety cap on force-continues, removed
  outright. The turn counter is still incremented for telemetry.
- `stale` — 4-hour `SESSION_ACTIVE` freshness check, removed. A
  marker that survived from earlier today still force-continues.
- `cascade_loop` — repeated-pick guard, removed as a *release*
  condition in v0.0.3; **restored as a reroute in v0.0.11**. When
  the same `github_issue` or `accepted_rfc` pick repeats ≥3
  consecutive turn boundaries, the hook injects the planning-phase
  prompt instead of "Continue on: X". Sources that legitimately
  repeat (`resume_in_flight`, `unblocked_approval`, `pr_rescue`)
  never reroute. The session is not released — the v0.0.3 "only
  human intervention terminates" contract holds. The history file
  is still written; it now drives the detector.

Two structural preconditions remain (these are "nothing to keep
alive" not "voluntarily released"): `no_run` (no active run) and
`inactive` (`SESSION_ACTIVE` marker absent — non-Nightly sessions
are untouched). One host-level override also remains: Claude Code's
8-consecutive-blocks-without-progress cap. Unlike the old misread
`stop_hook_active` yield, this cap is a real Python-opaque limit —
no Stop hook can intercept it. The installer mitigates it by merging
`"env": {"CLAUDE_CODE_STOP_HOOK_BLOCK_CAP": "5000"}` into
`.claude/settings.local.json`, effectively lifting the cap for
overnight sessions while keeping a finite runaway backstop.

**v0.0.10 fix (bug reports #19 / #25 — stop_hook_active misread).**
Earlier versions misread Claude Code's `stop_hook_active: true` stdin
flag as "the host cap is about to override us" and yielded immediately
(logged as `host_cap`), writing a RESPAWN_REQUESTED marker. In
reality, `stop_hook_active: true` is set on *every* Stop event that
follows a hook-forced continuation — it simply marks "we are
continuing because the hook blocked," not "override imminent." Result:
sessions surrendered after exactly one force-continue (~minutes into
an overnight run). The fix: the hook now rides through forced-
continuation chains indefinitely; the only stop conditions are human
disk markers (CONCLUDE, STOP) and the structural preconditions above.
The `host_cap` voluntary-yield branch is gone. While blocking inside
a forced-continuation chain the hook **preemptively** writes/refreshes
the RESPAWN_REQUESTED marker — so if Claude Code's without-progress
cap (or a crash) silently kills the session, the marker is already on
disk. A fresh (non-chain) turn boundary clears the stale marker;
`nightly status` and `nightly session start` surface it prominently
so the operator's "re-invoke `/nightly`" resumes cleanly. A new per-
run `keepalive.blocks` counter records chain length for telemetry.
RFC 010 (planned) is the daemon-driven follow-up: a supervisor that
re-invokes the host on an involuntary kill so the operator never has
to.

**v0.0.11 fix (issue #27 — cascade livelock on PR-covered issues).**
The `github_issue` ranker now skips an issue when (a) any open PR
claims it with a closing keyword (existing guard from issue #10), OR
(b) any open **Nightly-authored** PR (`nightly/*` branch) merely
*mentions* `#N` in its body — a bare mention in an orchestrator-owned
PR means the issue is in flight, even without a closing keyword. Skip
reason: "open Nightly PR references this issue (in-flight)." The
keepalive livelock backstop (restored `cascade_loop` reroute — see
above) fires if a `github_issue` or `accepted_rfc` pick repeats ≥3
consecutive boundaries; the hook then injects the planning-phase
prompt so the agent enters ideation rather than holding.

**v0.0.12 — context-compaction feature.** The keepalive hook now
estimates live context size each turn boundary (`ctx=` in heartbeat
log), persists it to `keepalive.context`, and when the estimate
exceeds the `context.budget_tokens` soft ceiling (default 256K),
prepends a context-diet block to the injected prompt. A compact
session digest is written to `digest.md` every N turns (default 1)
and always before any planning-phase reroute. A new
`SessionStart(compact)` hook re-injects the digest after any
compaction so key state is never lost. See `context:` block in
`.nightly/config.yml` and RFC 011.

### Filing a bug against Nightly itself

When Nightly's own behavior looks wrong (self-concluding, ignoring the
cascade, hook misfires, runaway loops), the operator runs
`nightly bug` (or `/nightly-bug`). This bundles the current run's
`keepalive.log`, plan statuses, briefing, and on-disk markers into a
markdown report under `.nightly/bugs/` and — if `gh` is available —
opens an issue on the Nightly repo. **The agent never invokes
`nightly bug` itself**; it is a debugging tool for the human, and
self-filing would mask whatever the agent was about to do wrong.

If you find yourself about to ask the user something: pick the better
default and ship it. Decision is cheaper than deliberation; deliberation
is cheaper than asking; asking is forbidden.
<!-- nightly:rules:end -->
