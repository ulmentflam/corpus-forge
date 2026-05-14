# Multi-host deployment (satellite topology)

corpus-forge supports running many ingester daemons across multiple
machines, all writing into and querying from a single central Postgres
instance. This guide walks through standing up a new satellite host
pointing at an existing central database.

## Prerequisites

- A central Postgres 14+ instance reachable from the satellite over the
  network. The `vector` extension must be installable (`CREATE EXTENSION
  IF NOT EXISTS vector` is run automatically during migration).
- corpus-forge installed on the satellite — see [README.md](../README.md).
- Network reach and credentials for the central DSN.
- The `[mcp]` extra if you want to expose the corpus via MCP:
  `pip install 'corpus-forge[mcp]'`.

## Bootstrap Postgres

On a fresh central database, run the schema migrations once from any
host. Alembic stamps to `head` and the operation is idempotent — re-running
on an already-migrated DB is a no-op:

```bash
DATABASE_URL="postgresql://corpus:secret@central.local:5432/corpus" \
  corpus-forge migrate
```

This creates the `corpus` schema, the pgvector extension, all core tables
(`documents`, `chunks`, `document_revisions`, `sources`) and the
full-text-search index. Subsequent satellites connecting to the same
central database skip this step — they share the already-migrated schema.

## Configure host_id

Each satellite needs a stable, unique host identifier so the sync engine
can attribute revisions and avoid pulling its own writes back from the
central database. On first daemon start, corpus-forge derives the host ID
from `socket.gethostname()` and persists the result to
`~/.config/corpus-forge/host_id`.

Override the default by editing `~/.config/corpus-forge/config.toml`:

```toml
[daemon]
host_id = "mac-studio-living-room"
```

Or set the environment variable `CORPUS_FORGE_HOST_ID` before starting
the daemon. The explicit `daemon.host_id` field takes precedence over the
persisted file, which in turn takes precedence over `socket.gethostname()`.

If you are cloning a machine image (e.g. a VM snapshot), delete or
overwrite `~/.config/corpus-forge/host_id` before the first daemon start
so each clone gets its own unique identifier.

## Enable sync

In `~/.config/corpus-forge/config.toml`, set `sync_enabled = true` on
any dataset you want to participate in cross-host sync:

```toml
[[datasets]]
name         = "notes"
kind         = "text"
sync_enabled = true

  [[datasets.sources]]
  plugin     = "markdown_vault"
  vault_root = "~/Documents/notes"
  chunker    = "markdown"
```

The daemon will push local edits as `document_revisions` rows and poll
for remote revisions from other hosts every `sync_poll_interval_s` seconds
(default: `5.0`). Sync is gated to Postgres backends only — the config
loader rejects `sync_enabled = true` when `backend.kind = "sqlite"`:

> Cross-host sync requires the postgres backend; SQLite is single-host.

When a conflict is detected (two hosts edited the same document between
sync polls), the revision is written to `conflict_dir`
(`~/.local/share/corpus-forge/trash` by default) and the remote version
is accepted as the canonical head.

## Verify

Confirm the satellite is healthy and syncing:

```bash
# Daemon connects, sources register, and sync poll runs:
corpus-forge sync status
```

`corpus-forge sync status` reports per-dataset last-pulled revision ID,
the `sync_enabled` flag, and pending push/pull counts. A successful
initial sync leaves `pending_remote_revisions` at zero for each remote
host.

To confirm the new host's datasets are visible alongside other hosts',
start the MCP server and call `list_datasets`:

```bash
# In one shell:
corpus-forge mcp serve

# From any MCP client (e.g. Claude Code with corpus-forge configured):
# mcp__corpus-forge__list_datasets
```

The response includes `chunk_count` and `document_count` aggregated
across all contributing hosts, confirming that cross-host data is
reachable through the shared central Postgres.
