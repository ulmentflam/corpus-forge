# Gemini CLI integration walkthrough

End-to-end recipe for wiring **corpus-forge** into the Gemini CLI, asking the
first grounded question, and delegating research to a dedicated subagent.

If you just want the JSON: see [`examples/mcp-config/`](../examples/mcp-config/)
and [`examples/gemini-extension/`](../examples/gemini-extension/).

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
   corpus-forge dataset list   # confirms non-zero documents (corpus ingested)
   ```

3. A recent [Gemini CLI](https://github.com/google-gemini/gemini-cli) install:

   ```bash
   npm install -g @google/gemini-cli
   gemini --version
   ```

The MCP transport in v1 is **stdio only** — there is no HTTP server to
expose, no port to open. The Gemini CLI launches `corpus-forge` as a
subprocess and talks to it over its stdio.

## Wire-up

There are two ways to wire corpus-forge into the Gemini CLI. Use whichever
fits your workflow.

### Option A — Add to `~/.gemini/settings.json`

Merge the `mcpServers` block from
[`examples/mcp-config/gemini-cli.mcp.json`](../examples/mcp-config/gemini-cli.mcp.json)
into your `~/.gemini/settings.json`:

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

If `~/.gemini/settings.json` doesn't exist yet, create it with the above
content.

### Option B — Install as a Gemini CLI Extension

Copy the extension directory into the Gemini CLI extensions folder:

```bash
mkdir -p ~/.gemini/extensions/corpus-forge-search
cp examples/gemini-extension/gemini-extension.json \
   ~/.gemini/extensions/corpus-forge-search/
cp examples/gemini-extension/GEMINI.md \
   ~/.gemini/extensions/corpus-forge-search/
```

The extension manifest (`gemini-extension.json`) declares the `mcpServers`
block and points the CLI at `GEMINI.md` via the `contextFileName` field.
The Gemini CLI loads the context file as a system-level instruction,
teaching the model when and how to invoke the corpus-forge tools.

Tools surface inside Gemini under the prefix `corpus-forge__<tool>` —
for example `corpus-forge__search`.

## Verify

Two checks to confirm the wire-up is live.

**Offline subprocess sanity check**:

```bash
corpus-forge mcp serve
```

The process should block on stdio without printing errors. `Ctrl-C` to stop.

**Inside the client**: ask Gemini to *"list corpus-forge datasets"* (or any
phrasing that invokes the context-file playbook). It should call
`corpus-forge__list_datasets` and answer from the live corpus rather than
fabricating an answer.

If the call doesn't fire, double-check:

- The drop-in JSON landed at the right path for your chosen option.
- `corpus-forge` is on the Gemini CLI's `PATH` (use an absolute path in the
  `command` field if the shell's `PATH` isn't inherited).
- `CORPUS_FORGE_CONFIG` resolves to a real TOML file from the client's
  environment.

## First search

Once the client is connected, asking the right kind of question is enough to
trigger the playbook (see
[`examples/gemini-extension/GEMINI.md`](../examples/gemini-extension/GEMINI.md)).

Try one of:

- *"What does the corpus-forge daemon log on startup? Show me where in the
  code."*
- *"Find past discussions about pgvector tuning in our team conversations."*
- *"Where in our docs do we explain the chunker's markdown handling?"*

Gemini will call `corpus-forge__search`, inspect the hits, and answer with
citations of the form:

```
From {title} ({source_uri}): {quote}
```

If a preview is too short, Gemini can pull the full chunk via
`corpus-forge__get_chunk(chunk_id)`.

## Subagent

For longer-running research tasks you can configure Gemini CLI's agent mode
to restrict the tool surface to corpus-forge only. Create a focused
`gemini-agent.md` system instruction that mirrors the persona in
[`examples/gemini-extension/GEMINI.md`](../examples/gemini-extension/GEMINI.md):

- A focused persona ("research librarian, citation-disciplined, no code").
- Access only to `corpus-forge__search`, `corpus-forge__get_chunk`,
  `corpus-forge__list_datasets` — no shell, no file edits.
- A built-in `rerank=true`-only-when-high-stakes rule.
- A fixed output template (`**Answer**` + `**Citations**`).

Run it with:

```bash
gemini --system-instruction gemini-agent.md "What does the corpus say about pgvector tuning?"
```

## Troubleshooting

| Symptom | Probable cause | Fix |
|---------|---------------|-----|
| Gemini says "tool not found" | Extension not installed or `settings.json` not reloaded. | Re-copy the snippet and restart the Gemini CLI. |
| `corpus-forge: command not found` in logs | The CLI's shell `PATH` doesn't include the `corpus-forge` console script. | Use an absolute `command` path, e.g. `/Users/…/.venv/bin/corpus-forge`. |
| `search` returns empty `hits` | Corpus not yet ingested, or query is off-topic. | Run `corpus-forge ingest --once` and confirm `corpus-forge dataset list` shows non-zero documents. |
| First call with `rerank=true` hangs ~30s | Cross-encoder weights are downloading (one-time **600 MB**). | Let it finish; subsequent calls reuse the cached weights. Stay `rerank=false` if you don't need precision. |
| `contextFileName` not loaded | Gemini CLI version too old (pre-extension support). | Upgrade to the latest Gemini CLI release. |
| Tools appear but GEMINI.md guidance is ignored | The `contextFileName` path in the manifest is wrong. | Confirm `GEMINI.md` sits next to `gemini-extension.json` in the extension directory. |

When in doubt, run `corpus-forge mcp serve` by hand and replay the failing
tool call with the MCP inspector — the stdio session reproduces 1:1 what the
client sees.
