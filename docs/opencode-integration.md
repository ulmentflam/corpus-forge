# OpenCode integration walkthrough

End-to-end recipe for wiring **corpus-forge** into OpenCode, asking the first
grounded question, and delegating research to a dedicated subagent.

If you just want the JSON: see [`examples/mcp-config/`](../examples/mcp-config/).

---

## Prerequisites

1. A working corpus-forge install with the MCP extra:

   ```bash
   pip install 'corpus-forge[sqlite,mcp]'
   ```

2. At least one ingested dataset. From a fresh checkout:

   ```bash
   export CORPUS_FORGE_CONFIG="$HOME/.config/corpus-forge/config.toml"
   corpus-forge migrate
   corpus-forge ingest --once
   corpus-forge stats     # confirms non-zero chunks/embeddings
   ```

3. A recent OpenCode install (`sst/opencode` — the CLI agent, not the
   corpus-forge OpenCode source plugin):

   ```bash
   # Install via npm (check the sst/opencode repo for the latest method)
   npm install -g opencode-ai
   ```

The MCP transport in v1 is **stdio only** — there is no HTTP server to
expose, no port to open. The client launches `corpus-forge` as a
subprocess and talks to it over its stdio.

## Wire-up

The drop-in JSON snippet in [`examples/mcp-config/opencode-client.mcp.json`](../examples/mcp-config/opencode-client.mcp.json)
declares a single `mcpServers.corpus-forge` entry:

```json
{
  "mcpServers": {
    "corpus-forge": {
      "command": "corpus-forge",
      "args": ["mcp", "serve"],
      "env": { "CORPUS_FORGE_CONFIG": "~/.config/corpus-forge/config.toml" }
    }
  }
}
```

**OpenCode** reads its MCP server config from `~/.opencode/config.json` or a
project-local `.opencode/config.json`. Merge the `mcpServers.corpus-forge`
block from `opencode-client.mcp.json` into your existing config and restart
OpenCode.

```bash
# Example: copy into a project-local opencode config
cp examples/mcp-config/opencode-client.mcp.json /path/to/your/project/.opencode/config.json
```

Tools surface inside OpenCode under the prefix `mcp__corpus-forge__<tool>` —
for example `mcp__corpus-forge__search`.

The agent and command files ship in `.opencode/`:

- **Agent**: `.opencode/agent/corpus-forge-researcher.md` — research librarian
  delegate with access to the five read tools.
- **Command**: `.opencode/command/corpus-forge-search.md` — search command
  (OpenCode's equivalent of a Claude skill).

## Verify

Two checks to confirm the wire-up is live.

**Offline subprocess sanity check**:

```bash
corpus-forge mcp serve
```

The process should block on stdio without printing errors. `Ctrl-C` to
stop.

**Inside the client**: ask OpenCode to *"list corpus-forge datasets"* (or any
phrasing that invokes the `corpus-forge-search` command). It should call
`mcp__corpus-forge__list_datasets` and answer from the live corpus rather
than fabricating an answer.

If the call doesn't fire, double-check:

- The drop-in JSON landed at the right path for your client.
- `corpus-forge` is on the client's `PATH` (use an absolute path in the
  `command` field if the shell's `PATH` isn't inherited).
- `CORPUS_FORGE_CONFIG` resolves to a real TOML file from the client's
  environment.

## First search

Once the client is connected, asking the right kind of question is enough
to trigger the command (see
[`.opencode/command/corpus-forge-search.md`](../.opencode/command/corpus-forge-search.md)).

Try one of:

- *"What does the corpus-forge daemon log on startup? Show me where in the
  code."*
- *"Find past discussions about pgvector tuning in our team conversations."*
- *"Where in our docs do we explain the chunker's markdown handling?"*

OpenCode will call `mcp__corpus-forge__search`, inspect the hits, and answer
with citations of the form:

```
From {title} ({source_uri}): {quote}
```

If a preview is too short, OpenCode can pull the full chunk via
`mcp__corpus-forge__get_chunk(chunk_id)`.

## Subagent

For longer-running research tasks, delegate to the dedicated subagent at
[`.opencode/agent/corpus-forge-researcher.md`](../.opencode/agent/corpus-forge-researcher.md).

The subagent has:

- A focused persona ("research librarian, citation-disciplined, no code").
- Access only to the five `mcp__corpus-forge__` read tools — no shell, no
  file edits.
- A built-in rerank-only-when-high-stakes rule (see below).
- A fixed output template (`**Answer**` + `**Citations**`).

Inside OpenCode you can spawn it via the agent mechanism and ask it a single
research question.

## Troubleshooting

| Symptom                                              | Probable cause                                                 | Fix                                                                                              |
|------------------------------------------------------|----------------------------------------------------------------|--------------------------------------------------------------------------------------------------|
| Client says "MCP server `corpus-forge` not found"    | Drop-in JSON not at the expected path / not reloaded.          | Re-copy the snippet from `examples/mcp-config/` and restart the client.                          |
| `corpus-forge: command not found` in the client logs | The client's shell `PATH` doesn't include the `corpus-forge` console script. | Use an absolute `command` path in the JSON, e.g. `/Users/…/.venv/bin/corpus-forge`.              |
| `search` returns empty `hits`                        | Corpus not yet ingested, or query is off-topic.                | Run `corpus-forge ingest --once` and confirm `corpus-forge stats` shows non-zero chunks.         |
| First call with `rerank=true` hangs ~30s             | Cross-encoder weights are downloading (one-time **600 MB**).   | Let it finish; subsequent calls reuse the cached weights. Stay `rerank=false` if you don't need precision. |
| `tools/list` shows tools you didn't ask for          | A different MCP server is also registered.                     | Inspect the client's MCP config; corpus-forge always advertises exactly `search` / `get_chunk` / `list_datasets`. |
| `schema validation` errors from the client           | Argument type mismatch (e.g. `k` passed as a string).          | The `search` schema requires `k: int`, `dataset: str \| null`, `rerank: bool`. Confirm types.    |

When in doubt, run `corpus-forge mcp serve` by hand and replay the failing
tool call with the MCP inspector — the stdio session reproduces 1:1 what
the client sees.
