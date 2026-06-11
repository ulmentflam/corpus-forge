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

## Resource sizing — before you add a host

Claim-based distributed embedding (RFC fleet-2) is *greedy*: every
embed-worker races to claim the next batch of un-embedded chunks and
writes the vectors straight back to the central Postgres. There is no
cluster scheduler holding the fleet back — a host that can claim, claims.
That's the right default for throughput, but it means **the central
Postgres host, not the GPUs, is the shared bottleneck**, and adding
workers past what it can serve degrades the whole fleet rather than
speeding it up.

A real failure seen in the field: a two-host fleet (a Mac + a Windows
5090) pointed at a 4-vCPU / 16 GiB Postgres LXC drove the database host
to ~98% CPU, ~98% RAM, and a fully-exhausted swap. The symptoms cascade
and look like a *network* problem rather than a capacity one:

- intermittent connection timeouts to `:5432` from every client,
- embed-workers wedged mid-batch while still holding live `embed_claims`,
- even read-only commands like `corpus-forge hosts list` failing with
  `could not receive data from server`.

Size the central host up front to avoid it.

### Rule of thumb

A Postgres host comfortably serves roughly **2–3 active embed-workers per
vCPU**. The 4-vCPU / 16 GiB host above is comfortable with ~2–3 concurrent
embedders; a third or fourth worker is where it starts to swap. CPU and
RAM on the database host — not GPU count — set the ceiling, because every
worker's writes, the HNSW index maintenance, and the claim/release
bookkeeping all land there.

### Connection-pool footprint

Each host opens its own psycopg pool, up to `max_size` connections
(**default `8`**). N hosts therefore consume up to `N × 8` of the
server's `max_connections` (typically `100`), *plus* the operator's own
ad-hoc `doctor` / `hosts list` / `psql` connections. Keep the fleet's
steady-state usage under ~half the limit so those interactive commands
never get starved:

```text
per_host_max_size  ≈  floor(max_connections / N / 2)
```

For `max_connections = 100` and a 4-host fleet that is `floor(100/4/2) =
12` — comfortably above the default `8`, so the default is fine up to ~6
hosts. Past that, either lower each host's pool ceiling or raise
`max_connections` on the server (and its `shared_buffers` / `work_mem` to
match — see [deployment/postgres.md](deployment/postgres.md#tuning)).

### Pre-flight check

Before you start a worker on a new host, run `doctor` against the central
database from any existing host:

```bash
corpus-forge doctor
```

The `embed_claims` check reads the server's live connection count
(`pg_stat_activity`) against `max_connections` and **WARNs when the host
is already at ≥ 80% of its connection limit** — the "look before you add
another embed-worker" guard. If it warns, the central Postgres is hot:
add capacity (vCPU / RAM, or a higher `max_connections`) before adding a
worker, rather than after the fleet wedges. `corpus-forge estimate <path>`
(no network, no model calls) tells you the storage footprint a new root
will add before you sync it.

During the first backfill on a freshly-added host, watch the database
host's CPU / RAM / swap (Proxmox, `htop`, or `pg_stat_activity`). Steady
swap usage is the early-warning sign that you have one worker too many for
the current database size.
