# Task 0005 — MCP server smoke

## What I exercised

Drove `corpus-forge mcp serve --transport stdio` end-to-end via JSON-RPC
from a small Python harness (no real MCP client). Sequence:

1. `initialize` (protocolVersion 2024-11-05) → server replies.
2. `notifications/initialized`.
3. `tools/list` → server returns the tool catalog.
4. `tools/call name=list_datasets` → server replies.

## Results

**Init negotiation works.** Server identifies as `corpus-forge` v1.27.1
(MCP SDK version), advertises `tools.listChanged=false`, no error.

**Tool surface is correct.** `tools/list` returned 10 tools, including
all five CLAUDE.md §1-documented ones:

```
search, get_chunk, list_datasets, estimate_sync_size,
next_curation_target  (+ 5 more)
```

**`list_datasets` works at the protocol level** — well-formed JSON-RPC
response with `content` array.

## Findings worth filing

1. **MCP server requires the `[mcp]` extra.** Without it,
   `corpus-forge mcp serve` exits with
   `ModuleNotFoundError: No module named 'mcp'` and a full traceback.
   The error is technically accurate but the CLI should ideally:
   - check at startup whether `mcp` is importable, AND
   - print a one-line "install with: `uv tool install
     'corpus-forge[mcp]'`" hint instead of the traceback.

   `setup-corpus-forge.sh` / `install.sh` don't install the mcp extra
   by default (it's optional), so any first-run user following the
   CLAUDE.md quickstart hits this. Worth a follow-up issue.

2. **`list_datasets` with no config returns `isError: true` and an
   empty `content` text.** A real MCP client (Claude Desktop / Code)
   would see `Error: ` with no message. The server should populate
   the content with the same `"Configuration file not found: ..."`
   that the bug-report bundle gracefully includes for `db_summary.json`.

   This is the same root cause (no config) — but the bug-report bundle
   handles it gracefully and the MCP server doesn't. Worth a separate
   issue: "MCP server should surface configuration errors in tool-call
   responses, not return empty `isError`."

3. **The schema-shadow UserWarning leaks to stderr.** Same as task 0002
   notes — this branch doesn't yet have PR #18's filter in config.py.
   Will clear once main's filter propagates to whatever branch you run
   from.

## Verdict

Smoke passes: protocol negotiation, tools/list, and tools/call all
work. Two UX follow-ups worth filing as issues (extra-not-installed
error message + empty MCP error responses), neither blocking.

## Caveats

- Did NOT exercise `search` against a populated DB. Item #10 called for
  poking `search / list_datasets from a real client.` `list_datasets`
  was exercised; `search` over a real index needs the user's actual
  setup.
