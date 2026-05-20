# PostgreSQL deployment (bare-metal Debian/Ubuntu)

This guide takes a fresh Debian 12 or Ubuntu 22.04/24.04 host from
"nothing installed" to "`corpus-forge migrate` succeeded against a
pgvector-enabled database listening on the LAN." Two paths are
documented:

- **Quick start** — the `scripts/postgres-bootstrap.sh` helper. One
  command, idempotent, dual-mode (interactive TTY or env-var driven for
  CI / Ansible / cloud-init).
- **Manual procedure** — the same steps spelled out verbatim, for
  operators who want to understand every line before letting a script
  edit `pg_hba.conf`.

Both paths arrive at the same end state.

## Prerequisites

- Debian 12 (bookworm) or Ubuntu 22.04 / 24.04. RHEL family and Arch are
  out of scope for the bootstrap script — adapt the manual procedure if
  you need them.
- Root or `sudo` access on the target host.
- Outbound HTTPS to `apt.postgresql.org` (the PGDG repo) and the
  distribution's normal apt mirrors.
- A CIDR range for the corpus-forge clients (e.g. your LAN
  `192.168.1.0/24`, your tailnet `100.64.0.0/10`, or a single
  `10.0.0.5/32` for a one-machine setup).

## Quick start

Run the bootstrap script. The TTY path will prompt for the four
required values; the env-var path is ideal for unattended setups
(cloud-init, Ansible, Proxmox helper scripts).

```bash
# Interactive — prompts for db / user / password / cidr.
sudo bash scripts/postgres-bootstrap.sh

# Unattended — CF_PG_* env vars set the four required values.
sudo -E CF_PG_DB=corpus_forge \
        CF_PG_USER=corpus_forge \
        CF_PG_PASSWORD="$(openssl rand -base64 32)" \
        CF_PG_CIDR=192.168.1.0/24 \
        bash scripts/postgres-bootstrap.sh

# See what would happen, change nothing.
CF_PG_DB=corpus_forge CF_PG_USER=corpus_forge \
  CF_PG_PASSWORD=placeholder CF_PG_CIDR=192.168.1.0/24 \
  bash scripts/postgres-bootstrap.sh --dry-run
```

When the script exits cleanly it prints the suggested DSN. Plug it into
`~/.config/corpus-forge/config.toml`:

```toml
[backend]
kind = "postgres"
dsn  = "postgresql://corpus_forge:PASSWORD@HOST:5432/corpus_forge"
schema = "corpus"
```

Then on the *client* host (which may be the same machine):

```bash
corpus-forge migrate
corpus-forge doctor
```

The bootstrap script is idempotent end-to-end — re-running it with the
same inputs is a no-op (PGDG repo file check, `DO $$ BEGIN IF NOT
EXISTS … END $$` for the role, skip-if-exists for the database,
`CREATE EXTENSION IF NOT EXISTS vector`, grep-before-append for the
`pg_hba.conf` line).

### Flag reference

| Flag | Env var | What it does |
|---|---|---|
| `--db NAME` | `CF_PG_DB` | Database name. Required. |
| `--user NAME` | `CF_PG_USER` | Role name. Required. |
| `--password STR` | `CF_PG_PASSWORD` | Role password. Required. |
| `--cidr CIDR` | `CF_PG_CIDR` | `pg_hba.conf` source CIDR. Required. |
| `--pg-version N` | `CF_PG_VERSION` | Major version (default 17). |
| `--no-listen` | — | Skip the `listen_addresses` edit. |
| `--dry-run` | — | Print the plan; execute nothing. |
| `--quiet` | — | Suppress progress logs. |
| `--help` | — | Show the canonical flag list. |

Missing required inputs on a non-TTY invocation cause exit code 2 with
a clear error naming the missing variable.

## Manual procedure

If you'd rather lay your hands on the keyboard for every step:

```bash
# 1. Add the PGDG apt repo (Debian / Ubuntu both work; lsb_release picks
#    the codename).
sudo apt-get install -y curl ca-certificates gnupg lsb-release
sudo install -d /usr/share/postgresql-common/pgdg
curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | \
  sudo gpg --dearmor -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg
echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg] \
https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" | \
  sudo tee /etc/apt/sources.list.d/pgdg.list

# 2. Install Postgres + pgvector.
sudo apt-get update
sudo apt-get install -y postgresql-17 postgresql-17-pgvector
sudo systemctl enable --now postgresql

# 3. Create the role (idempotent — wrapped in a DO $$ … $$ block).
sudo -u postgres psql -v ON_ERROR_STOP=1 <<'SQL'
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'corpus_forge') THEN
    CREATE ROLE corpus_forge LOGIN PASSWORD 'CHANGEME';
  END IF;
END
$$;
SQL

# 4. Create the database (skip-if-exists).
sudo -u postgres psql -tAc \
  "SELECT 1 FROM pg_database WHERE datname = 'corpus_forge'" | grep -q 1 || \
  sudo -u postgres psql -v ON_ERROR_STOP=1 \
    -c "CREATE DATABASE corpus_forge OWNER corpus_forge"

# 5. Enable pgvector inside the corpus database.
sudo -u postgres psql -v ON_ERROR_STOP=1 -d corpus_forge \
  -c "CREATE EXTENSION IF NOT EXISTS vector"

# 6. Allow LAN connections.
sudo sed -i "s/^#\?listen_addresses *=.*/listen_addresses = '*'/" \
  /etc/postgresql/17/main/postgresql.conf
echo "host    corpus_forge    corpus_forge    192.168.1.0/24    scram-sha-256" | \
  sudo tee -a /etc/postgresql/17/main/pg_hba.conf

# 7. Reload.
sudo systemctl reload postgresql
```

After step 7 you can run `corpus-forge migrate` from a client host on
the configured CIDR.

## Tuning

`shared_buffers`, `effective_cache_size`, and the work-mem knobs all
benefit from being sized to the host. Don't edit `postgresql.conf`
directly — ship a drop-in via `scripts/postgres-tune.sh`:

```bash
# See what would be written.
bash scripts/postgres-tune.sh --ram 16 --dry-run

# Apply it.
sudo bash scripts/postgres-tune.sh --ram 16
sudo systemctl reload postgresql
```

The script writes `/etc/postgresql/<version>/main/conf.d/corpus-forge.conf`.
PostgreSQL concatenates `conf.d/*.conf` after the main configuration, so
the drop-in overrides any matching key. Deleting the file reverts the
tuning to PostgreSQL defaults.

### Sizing example — the maintainer's 9-root corpus

A real estimate against a curated 9-root corpus (2.25M chunks at
`Qwen3-Embedding-8B`, 2048-dim vectors) yielded:

| Component | Footprint |
|---|---|
| Docs + chunks + btree indexes | 12.7 GB |
| Embed table + HNSW index (2048-dim) | 36 GB |
| **Total PG footprint** | **49 GB** |

The corresponding tunings (computed by `postgres-tune.sh`):

| Host RAM | shared_buffers | effective_cache_size | work_mem | maintenance_work_mem |
|---|---|---|---|---|
| 8 GB | 2 GB | 6 GB | 64 MB | 512 MB |
| **16 GB** (recommended) | **4 GB** | **12 GB** | **128 MB** | **1 GB** |
| 32 GB | 8 GB | 24 GB | 256 MB | 2 GB |
| 64 GB | 16 GB | 48 GB | 512 MB | 4 GB |

For a Proxmox host with 32 GB total, a 16 GB / 150 GB / 4 vCPU
LXC is a comfortable starting point. The 150 GB rootfs leaves room
for the 49 GB working set plus WAL + 1-2 pg_dump snapshots and
~30 GB of vacuum/HNSW headroom.

### Tuning formulae

| Knob | Formula | Floor |
|---|---|---|
| `shared_buffers` | RAM × 25% | 1 GB |
| `effective_cache_size` | RAM × 75% | 1 GB |
| `work_mem` | RAM_GB × 8 MB | 64 MB |
| `maintenance_work_mem` | RAM_GB × 64 MB | 512 MB |
| `wal_compression` | on | — |

`wal_compression = on` is a small CPU cost for a meaningful WAL-volume
reduction on the heavy HNSW backfill passes.

## Backups

Use both `pg_dump` and ZFS snapshots — they protect against different
failure modes.

```bash
# 1. Logical dump (portable; restores into any compatible PG instance).
sudo -u postgres pg_dump -Fc -d corpus_forge \
  > /var/backups/corpus_forge-$(date +%Y%m%d).dump

# 2. ZFS snapshot of the data directory (atomic; instant; cheap on
#    copy-on-write filesystems; restore by clone / rollback).
zfs snapshot rpool/var/lib/postgresql@nightly-$(date +%Y%m%d)
```

A nightly cron that does both, plus a weekly `pg_dump` shipped off-host
to NAS or object storage, covers all the common loss scenarios:

- **Catastrophic disk loss** — ZFS snapshot on the same disk is gone;
  the off-host `pg_dump` survives.
- **Logical corruption** ("I just `DELETE FROM chunks`") — ZFS
  rollback is faster, but `pg_dump` works too.
- **Postgres major-version upgrade** — `pg_dump | pg_restore` is the
  documented path. ZFS snapshots don't carry across major versions.

## Troubleshooting

### `FATAL: no pg_hba.conf entry for host ...`

The client's IP isn't in the CIDR you configured. Check what range the
script appended to `pg_hba.conf`:

```bash
sudo grep corpus_forge /etc/postgresql/17/main/pg_hba.conf
```

`pg_hba.conf` is order-sensitive — the first matching line wins. If the
default `local all postgres peer` line appears above your `host`
line, that's fine. If a `host all all 127.0.0.1/32 reject` line is
above yours, that wins; reorder by hand and `sudo systemctl reload
postgresql`.

### `could not connect to server: Connection refused`

`listen_addresses` is probably still on the default `'localhost'`.
Confirm:

```bash
sudo grep '^listen_addresses' /etc/postgresql/17/main/postgresql.conf
```

If it shows `'localhost'`, re-run the bootstrap script without
`--no-listen`, or edit the line by hand to `'*'` and reload.

### Role vs database ownership confusion

The bootstrap script creates the role and the database both named
`corpus_forge`, with the role owning the database. If you ran the
manual procedure with mismatched names, you'll see permission errors
when `corpus-forge migrate` tries to create the schema. Fix:

```bash
sudo -u postgres psql -c "ALTER DATABASE corpus_forge OWNER TO corpus_forge"
```

### `extension "vector" is not available`

The `postgresql-N-pgvector` apt package wasn't installed. Re-run the
install step:

```bash
sudo apt-get install -y postgresql-17-pgvector
sudo -u postgres psql -d corpus_forge -c "CREATE EXTENSION IF NOT EXISTS vector"
```

The package name varies by major version (`postgresql-15-pgvector`,
`postgresql-16-pgvector`, `postgresql-17-pgvector`). The bootstrap
script picks the right one from `--pg-version`.

### Disk fills up during the embedding backfill

The HNSW index build can roughly double the on-disk footprint of the
embedding table while it's running, then settle back. Run
`corpus-forge estimate <root>` before syncing to model this — see the
sizing table above and the `[estimate]` block in `config.example.toml`.
