# Skill Packs — Installation Guide

corpus-forge ships a **corpus-curate skill pack** for each of the four
supported AI coding clients. Each pack teaches the client how to run the
curation loop and how to capture generalizable edits as SDFT demonstrations
via MCP.

---

## Claude Code

**File:** `.claude/skills/corpus-curate/SKILL.md`

This skill is auto-discovered when the corpus-forge repo is on Claude
Code's skill search path. To load it in a different project, copy or
symlink `.claude/skills/corpus-curate/` into the target repo's `.claude/`
directory.

```bash
# From a project that should pick up the skill:
ln -s /path/to/corpus-forge/.claude/skills/corpus-curate \
      .claude/skills/corpus-curate
```

The pack instructs Claude Code to run the five-step curation loop, record
generalizable edits via `record_demonstration` with `source="claude_code"`,
and call `commit_curation`, `rate_search_result`, and `add_feedback` as
appropriate.

---

## Gemini CLI

**Files:**
- `.gemini/extensions/corpus-curate.toml` — extension manifest
- `.gemini/extensions/corpus-curate/PROMPT.md` — prompt body

Load the extension with:

```bash
gemini extension load .gemini/extensions/corpus-curate.toml
```

Or reference the `.toml` path in your Gemini CLI config. The pack enables
the same four MCP tools and sets `source="gemini"` for all SDFT
demonstrations captured during Gemini CLI sessions.

---

## OpenCode

**File:** `opencode/commands/corpus-curate.md`

OpenCode slash-commands live under `opencode/commands/`. The file is
auto-loaded when OpenCode starts from the repo root. Invoke the command
with:

```
/corpus-curate [dataset=<name>]
```

The pack covers the five-step curation loop and captures demonstrations
via `record_demonstration` with `source="opencode"`.

---

## Codex

**File:** `codex/agents/corpus-curate.md`

Codex agent definitions live under `codex/agents/`. Register the agent
by pointing your Codex configuration at this file, or place it in the
directory Codex scans for agent definitions on startup.

The pack defines the agent's role, the curation loop steps, and the SDFT
capture contract (`source="codex"`). It references all four MCP tools:
`record_demonstration`, `commit_curation`, `rate_search_result`, and
`add_feedback`.

---

## Common notes

All four packs require the corpus-forge MCP server running with
`writes_enabled=True` to use `commit_curation` and `record_demonstration`.
If writes are disabled, the packs instruct the client to surface that
limitation to the user rather than silently failing.

See [CLAUDE.md](../CLAUDE.md) for MCP server wiring instructions.
