# MCP config examples — corpus-forge

Drop-in JSON snippets that wire **corpus-forge** into a Claude client over
the Model Context Protocol (MCP).  Both files in this directory declare an
identical `mcpServers.corpus-forge` block; only the install location differs
per surface.

## Prerequisites

```bash
# Install the MCP extra alongside whichever backend you use.
pip install 'corpus-forge[sqlite,mcp]'

# One-time warm-up against your config.toml:
export CORPUS_FORGE_CONFIG="$HOME/.config/corpus-forge/config.toml"
corpus-forge migrate
corpus-forge ingest --once
```

The `[mcp]` extra pulls in the official `mcp` Python SDK.  `[sqlite]` is the
recommended single-user backend; swap in `[pgvector]` if you have Postgres.

## Claude Code

Claude Code reads MCP servers from a project-local `.mcp.json` (preferred) or
the user-global `~/.claude.json`.  Copy `claude-code.mcp.json` to either path
and reload Claude Code.

```bash
cp claude-code.mcp.json /path/to/your/project/.mcp.json
```

Inside Claude Code, the tools surface under the prefix
`mcp__corpus-forge__<tool>` — for example `mcp__corpus-forge__search`.

## Claude Desktop

Claude Desktop reads MCP servers from a single per-user config file:

- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

Merge the `mcpServers.corpus-forge` block from `claude-desktop.json` into your
existing `claude_desktop_config.json` (or copy the file outright if you don't
have other MCP servers).  Restart Claude Desktop to pick up the change.

## Environment variables

| Variable                | Purpose                                                |
|-------------------------|--------------------------------------------------------|
| `CORPUS_FORGE_CONFIG`   | Path to the TOML the MCP subprocess loads at startup (canonical). |
| `CF_CONFIG`             | Fallback alias for `CORPUS_FORGE_CONFIG`.               |
| `HF_HUB_OFFLINE=1`      | Optional — skip Hugging Face network calls at boot.    |
| `TRANSFORMERS_OFFLINE=1`| Optional — pin the embedder / reranker to local cache. |

## Verifying the install

Once the JSON is in place and the client is restarted, ask the model to
"list corpus-forge datasets" — it should call
`mcp__corpus-forge__list_datasets` and answer from the live corpus rather
than fabricating an answer.

For an offline sanity check, run the server by hand:

```bash
corpus-forge mcp serve
```

It should block on stdio without printing errors.  Send `Ctrl-C` to stop.
