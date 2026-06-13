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
| `check_update` | Reports whether a newer corpus-forge release exists (24h-cached PyPI check) plus the channel-appropriate upgrade command. Read-only; recommend, never run. |
| `next_curation_target` / `next_curation_batch` | Ranker-driven "what entry most needs my help right now?" |
| `commit_curation` | Atomic multi-write covering label adds/removes, metadata, description, feedback. |
| `create_enhancement_chunk` | Mint a NEW chunk (conversation + recommended enhancement) into a per-dataset synthetic curation document, linked to its source via `metadata.derived_from_chunk_id`. Curation that *creates* data, not just edits it. |
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
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force; iwr -useb https://raw.githubusercontent.com/ulmentflam/corpus-forge/main/install.ps1 -OutFile $env:TEMP\install.ps1; & $env:TEMP\install.ps1
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
      "env": { "CORPUS_FORGE_CONFIG": "~/.config/corpus-forge/config.toml" }
    }
  }
}
```
*(Note: `CORPUS_FORGE_CONFIG` is the canonical environment variable. The shorter `CF_CONFIG` is also supported as a fallback alias).*

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
.claude/skills/corpus-forge-update/SKILL.md
.opencode/command/corpus-forge-search.md
.opencode/command/corpus-curate.md
.opencode/command/corpus-forge-update.md
.gemini/agents/corpus-forge-search.md
.gemini/agents/corpus-curate.md
.gemini/agents/corpus-forge-update.md
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

### Add a second machine

When the user wants to onboard another host onto an existing fleet (a
new GPU box, a laptop, a spare Mac mini), the installer takes a
`--join <dsn>` / `-Join <dsn>` pass-through that handles it in one
command — no question tree, no `migrate`, no per-host config drift.

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

Do **not** use `iwr | iex` for install.ps1 — `Invoke-Expression`
doesn't reliably handle scripts with a top-level `param()` block
(its parser stumbles on the preceding `<# #>` comment-based-help
block). The `-OutFile + &` form routes through PowerShell's normal
`.ps1` loader, which handles `param()` correctly.

A `ts://host/db` DSN is resolved against Tailscale's API and stays
portable across the tailnet.

Mechanics: the installer **skips its question tree** (the fleet's
primary owns the shared scope — embedders, retrieval tuning,
classifier chains, even dataset names — and the joiner pulls all of it
via `corpus-forge setup --non-interactive --join <dsn>`) and
**explicitly does NOT run `corpus-forge migrate`** (the primary owns
the schema lifecycle; joiners never migrate). After the command
finishes:

- `doctor` already ran as a smoke check; re-run if it warned.
- `corpus-forge bench embed --all` — record this host's per-lane
  embedder throughput so the fleet's claim loop knows what it's best at.
- `corpus-forge service install` — install the daemon as a managed
  service so the new host drains backlog continuously.

## 6. Curation loop (vendor-neutral playbook)

When the user wants to improve corpus quality, follow this prompt skeleton — it works for any MCP-speaking assistant:

1. **Pick** — call `next_curation_target(dataset=<name>)` (or `next_curation_batch(limit=N)` if the user said "let's batch"). The response includes `chunk_id`, `text`, `current_labels`, `current_metadata`, `missing_fields`, and a `score_breakdown` showing why this entry was selected.
2. **Present** — show the user a tight summary (≤ 6 lines): preview of the text, current labels, and the one most-improvable dimension (missing description? wrong label? thin metadata?). Don't list every gap.
3. **Ask** — at most three focused questions targeting the highest-value gap.
4. **Commit** — call `commit_curation(chunk_id=..., add_labels=[...], remove_labels=[...], set_metadata={...}, set_description="...", feedback="...")`. The server applies the writes atomically.
5. **Loop** — offer "next one?" — yes → step 1, no → session summary.

When the conversation yields *new* material worth keeping (a worked correction, a clarified explanation, a recommended enhancement), call `create_enhancement_chunk(dataset=<name>, text=..., derived_from_chunk_id=<source>)` to mint it as its own chunk under the synthetic `corpus-forge://curation/<dataset>` document. `commit_curation` edits the existing entry; `create_enhancement_chunk` creates a new one. The minted chunk carries `metadata.kind = "curation_enhancement"` — filter on it to keep synthetic rows out of retrieval/eval.

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
| Add a second machine | `corpus-forge setup --join postgresql://user@pg-host/corpus` — registers the host and pulls shared config in one command (postgres only; SQLite is single-machine). Then `config pull --apply` for later updates. |
| `TailscaleUnavailable` / `ts://` won't resolve | `corpus-forge doctor` names the failing endpoint. "daemon" reason → install/start Tailscale; "name" reason → peer not in this tailnet. `ts://` in config while `[tailscale] enabled = false` fails at load. |

Full docs live under `docs/` in this repo. The `corpus-forge doctor --json` output is the quickest way to ship a diagnosis to the user.

---

## See also

- `CLAUDE.md` — Claude Code / Claude Desktop setup.
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
