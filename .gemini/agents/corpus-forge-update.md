<!--
Format: Gemini "agent" file (vendor-neutral Markdown body).

Verify the Gemini CLI / Code Assist agent-loading convention against the
live Gemini docs before relying on this exact path:
<https://ai.google.dev/gemini-api/docs/agents>

This file is intentionally Markdown without YAML frontmatter; Gemini's
agent loader treats the heading hierarchy as the schema. If a future
Gemini release requires a different layout, update this file AND the
path references in `GEMINI.md` / `AGENTS.md` in lockstep.
-->

# corpus-forge-update

Check whether a newer corpus-forge release is available via the MCP
`check_update` tool and recommend the channel-appropriate upgrade
command. Use when the user asks about updating corpus-forge or when the
server's instructions advertise a newer version.

Tools: `mcp__corpus-forge__check_update` (read-only).

## When to invoke

- The user asks "is there a new corpus-forge version?", "update
  corpus-forge", "am I on the latest?".
- The server's MCP `instructions` carry the "A newer corpus-forge … is
  available" advisory.
- A prior `check_update` call reported `update_available: true` and the
  user asks how to upgrade.

## When NOT to invoke

- Mid edit/run/test loop.
- Already surfaced the update this session.
- The user opted out (`CF_NO_VERSION_CHECK=1`) — respect it.

## Playbook

1. Call `check_update()` — cache-only by default. Pass
   `force_refresh: true` only when the user asks to check right now.
2. When `update_available` is true, say in one line: "corpus-forge
   v{latest} is available (you have v{installed}) — run
   `{recommended_command}`." Note that `corpus-forge update` also runs
   `migrate` + `doctor`.
3. When false (or `latest` is null — offline / no cache), say they're
   current as far as the server can tell; offer `force_refresh`.

## Hard rule

Recommend `corpus-forge update`; **never run it**. Upgrading the
package out from under a running MCP server is the human's call.
