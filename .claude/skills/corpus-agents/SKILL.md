---
name: corpus-agents
description: Synthesize a project's AGENTS.md by combining local inspection with corpus-forge cross-corpus retrieval. Use when the user asks to "init agents", "generate AGENTS.md", "synthesize project conventions", or starts a brand-new repo that should ship with grounded agent guidance.
allowed-tools:
  - Bash
  - Read
  - Edit
  - Write
---

## What this skill does

Drives the `corpus-forge agents init` CLI command. The command:

1. **Inspects** the current project: languages, package managers, test
   framework, license, existing AGENTS.md / CLAUDE.md / README.md.
2. **Samples** representative files locally to extract import style,
   docstring style, error-handling examples, and test-naming patterns.
3. **Queries the indexed corpus** for cross-project idioms in the same
   language (pytest fixtures, logging conventions, dataclass usage, etc.)
   so the generated guidance reflects patterns the user has actually
   adopted across their other projects.
4. **Synthesizes TWO artifacts** via the configured LLM
   (the `[code_enricher]` block in `~/.config/corpus-forge/config.toml`):
   - `.corpus-agents/AGENTS.md` — **private**, corpus-grounded, with
     `chunk_id` citations to the user's other projects. Gitignored.
   - `.corpus-agents/shareable.md` — **sanitized** subset. Citation-free.
     References only THIS project's own facts and language/tool
     defaults.
5. **Maybe seeds the project root** with the shareable body: if
   `<root>/AGENTS.md` is absent, the CLI copies `shareable.md` to that
   path so the user's collaborators immediately see safe conventions.
   If `<root>/AGENTS.md` already exists, it is **never** overwritten —
   not even with `--force`.
6. **Appends `.corpus-agents/` to `.gitignore`** (idempotent).

Every claim in the **private** synthesis is traceable to either local
sampling or a corpus citation. The shareable variant is gated to carry
no citations at all — that file is for commit, not for grounded
debugging.

## When to invoke

- User explicitly says "init agents", "create AGENTS.md", "set up agent
  conventions", "synthesize project guide", or similar.
- New repo or project skeleton where AGENTS.md is missing.
- User wants to refresh the private debugging notes after the project
  drifted (the `.corpus-agents/*` files are the principal's
  scratchpad).

## When NOT to invoke

- The user is asking about the corpus-forge project itself rather than
  *their* project's conventions — that's documentation, not synthesis.
- corpus-forge is not configured (`code_enricher.backend == "none"`)
  and the user has no local LLM — the command will exit 2; redirect
  them to `corpus-forge setup` first.
- The user wants to overwrite an existing root `AGENTS.md`. The CLI
  intentionally never does that. If the user truly wants a fresh root
  file, ask them to move/delete the existing one first.

## Two outputs — when to use each

| File | Audience | Citations | Auto-committed? |
|------|----------|-----------|----------------|
| `.corpus-agents/AGENTS.md` | The principal agent (you, the user) | Yes — chunk_ids + source URIs | No — gitignored |
| `.corpus-agents/shareable.md` | Reference for manual review before committing | None — gated empty | No — gitignored |
| `<root>/AGENTS.md` | The user's teammates and AI agents working in the repo | None | Yes — meant for commit. Only created when absent. |

The flow is:

1. Read `.corpus-agents/shareable.md` to confirm the sanitized
   conventions are accurate for THIS project.
2. Diff it against the auto-created `<root>/AGENTS.md` (they should
   match on a fresh run).
3. If the user already had a root `AGENTS.md`, manually merge the
   `.corpus-agents/shareable.md` highlights into it. The CLI did NOT
   touch the user's file — that's the safety contract.

## Workflow

1. **Confirm scope**: ask which directory should be treated as the
   project root (default: current working directory).
2. **Invoke the CLI**:
   ```bash
   corpus-forge agents init --project-root <PATH> --json
   ```
   Add `--no-ingest` if the user has already pointed corpus-forge at
   the project. Add `--no-root-write` if the user does NOT want the
   automatic root AGENTS.md seeding behavior. Add `--no-gitignore` if
   the user manages `.gitignore` by hand.
3. **Read the outputs**: the JSON result's `paths_written` lists every
   file the command produced. Walk the user through:
   - The private `.corpus-agents/AGENTS.md` (with citations) for
     debugging.
   - The shareable `.corpus-agents/shareable.md` they should review
     before committing.
   - Whether a root `AGENTS.md` was auto-created (it will be unless
     one already existed or `--no-root-write` was passed).
4. **Prompt edits**: ask the user to tighten any section that doesn't
   match their actual conventions. Apply edits with the `Edit` tool to
   the relevant file (root `AGENTS.md` for the shareable surface,
   `.corpus-agents/AGENTS.md` for the private notes).
5. **Do not commit**: leave staging to the user. The `.corpus-agents/`
   directory should stay gitignored.

## Exit codes

- `0` — clean write.
- `1` — user-input error (bad `--project-root`).
- `2` — corpus not ready or no LLM endpoint configured.
- `3` — LLM synthesis failure (empty / malformed response upstream).

## CLI flag summary

```
corpus-forge agents init
  [--project-root PATH]      default: cwd
  [--output-dir PATH]        default: <root>/.corpus-agents/
  [--no-root-write]          disable auto-write to <root>/AGENTS.md when absent
  [--gitignore/--no-gitignore]
  [--no-ingest]              error if project not in corpus
  [--force]                  overwrite .corpus-agents/* only — NEVER root AGENTS.md
  [--diff]                   print a diff vs existing .corpus-agents/AGENTS.md
  [--json]                   machine-readable result event
```

## Gotchas

- **`--force` is bounded**: it only overwrites the four files in
  `.corpus-agents/`. The root `AGENTS.md` is the user's commitment
  surface; the CLI never touches it once it exists. Test this
  expectation when explaining the command.
- This skill does NOT use the corpus-forge MCP server — it drives the
  CLI directly so it can be run pre-MCP-registration on a brand-new
  setup.
- The synthesizer rejects an LLM response that drops a required
  section heading. Two passes (private + shareable) each have their
  own header set — the private pass additionally requires
  `## Cross-Corpus Patterns`.
- The shareable response is gated: even if the LLM tries to sneak in a
  `chunk_id=...` citation, the synthesizer's `citations` list will
  still be empty for the shareable result. That said, audit the
  shareable markdown body before recommending the user commit it.
