---
name: corpus-forge-update
description: Check whether a newer corpus-forge release is available via the MCP check_update tool and recommend the channel-appropriate upgrade command. Use when the user asks about updating corpus-forge or when the server's instructions advertise a newer version.
allowed-tools:
  - mcp__corpus-forge__check_update
---

## What this does

Asks the connected corpus-forge MCP server whether a newer release exists
and, when one does, surfaces the one-line upgrade recommendation. The server
wraps its existing 24h-cached PyPI version check — calling the tool is cheap
by default and only touches the network when you explicitly ask it to.

## When to invoke

- The user asks "is there a new corpus-forge version?", "update
  corpus-forge", "am I on the latest?", or similar.
- The server's `instructions` (delivered at MCP initialize) carry the
  "A newer corpus-forge … is available" advisory and the user's current task
  would benefit from the newer release (bug fix, new tool).
- A prior `check_update` call this session reported `update_available: true`
  and the user now asks how to upgrade.

## When NOT to invoke

- Mid edit/run/test loop — an update recommendation is an interruption, not
  help.
- You already surfaced the update this session; don't nag.
- The user opted out (`CF_NO_VERSION_CHECK=1`) — the tool will say checks are
  disabled; respect that and drop the subject.

## Tool playbook

1. Call `check_update()` with no arguments. The common path is served from
   the 24h cache and never blocks on the network.
2. Only pass `force_refresh: true` when the user explicitly asks to "check
   right now" — it performs a live PyPI ping (2s budget).
3. Read the payload:

```json
{
  "installed": "0.1.0b7",
  "latest": "0.1.0",
  "update_available": true,
  "served_from_cache": true,
  "channel": "uv-tool",
  "recommended_command": "corpus-forge update"
}
```

4. When `update_available` is `true`, tell the user in one line:
   "corpus-forge v{latest} is available (you have v{installed}) — run
   `{recommended_command}`." Mention that `corpus-forge update` also runs
   `migrate` + `doctor` afterwards.
5. When `false` (or `latest` is `null` — offline / no cache), say they're
   current as far as the server can tell, and offer `force_refresh` if they
   want a live check.

## Hard rule

**Recommend `corpus-forge update`; never run it.** Upgrading the package out
from under a running MCP server is the human's call, made in their own
terminal. This skill's entire write surface is one read-only MCP tool.
