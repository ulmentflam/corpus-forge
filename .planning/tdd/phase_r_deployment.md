# Phase R — Postgres deployment helpers + docs

**Motivation:** a fresh user (or a maintainer setting up a new
Proxmox LXC) currently has to assemble a 15-step bare-metal Postgres
+ pgvector procedure by hand — PGDG repo, role/db SQL, `listen_addresses`,
`pg_hba.conf`, listen-via-LAN verification, sensible tuning. Phase R
ships turnkey helpers so the path from "I have a Linux host" to
"`corpus-forge migrate` succeeded" is one well-documented script
plus three deployment-guide pages.

**Target release:** `0.1.0b7`.

**Status:** planning → execution. Workflow: tdd-principal owns Wave 1;
orchestrator (this session) commits on workers' behalf.

## Decisions locked with the user

- **Bare-metal target: Debian/Ubuntu only.** Covers the most common
  Proxmox LXC templates (Debian 12, Ubuntu 22.04/24.04). RHEL family,
  Arch, macOS bare-metal Postgres are out of scope for this phase.
- **Docker path: compose + brief README section.** A single
  self-contained `scripts/docker-compose.postgres.yml` using
  `pgvector/pgvector:pg17`, named volume, healthcheck, env-file.
- **Scripts are dual-mode**: interactive prompts when run from a TTY,
  env-var driven (`CF_PG_*`) when piped or in CI. `--dry-run` and
  `--help` mandatory. `set -euo pipefail`, shellcheck-clean, idempotent.
- **Docs layout**: three pages under a new `docs/deployment/`
  directory — `postgres.md`, `docker.md`, `lxc.md`. Mirrors the
  existing `docs/sources/zotero.md` pattern.

## Wave overview

Single wave — scripts + docs + smoke tests all together. No sequenced
sub-waves; the surface is too small to benefit from internal gating.

### Critical files

| File | Purpose |
|---|---|
| `scripts/postgres-bootstrap.sh` | Bare-metal Debian/Ubuntu installer + DB/user/pg_hba/listen_addresses config |
| `scripts/postgres-tune.sh` | Applies sized `postgresql.conf` tuning for a target RAM via a drop-in conf.d file |
| `scripts/docker-compose.postgres.yml` | Self-contained pgvector compose stack |
| `scripts/.env.postgres.example` | Env-var template for the compose stack |
| `scripts/postgres-initdb.sql` | `CREATE EXTENSION vector` + DB owner setup; mounted by the compose stack |
| `docs/deployment/postgres.md` | Bare-metal Debian/Ubuntu guide |
| `docs/deployment/docker.md` | Docker Compose guide |
| `docs/deployment/lxc.md` | Proxmox LXC specifics (unprivileged container caveats, uid mapping, Tailscale, backup combo) |
| `README.md` | Cross-link to deployment docs from the `[backend]` section |
| `tests/scripts/test_postgres_bootstrap.py` | `--help` smoke, `--dry-run` command-list assertions, idempotency check |
| `tests/scripts/test_postgres_tune.py` | `--help`, `--ram` sizing math, `--dry-run` diff |
| `tests/scripts/test_docker_compose.py` | YAML parses; service name + image tag stable; init-sql is mounted |
| `tests/scripts/test_deployment_docs.py` | Rot-detector: each docs page exists, has the expected H2 sections; README links resolve |

## Wave 1 — Helpers + docs

### Red (failing tests first)

- `tests/scripts/test_postgres_bootstrap.py`:
  - `--help` exits 0 and prints usage including all flag names
  - `--dry-run` with required env vars set prints the expected sequence
    (apt repo add → install → role create → db create → CREATE EXTENSION
    → conf edit → reload) WITHOUT executing
  - Missing required env var on non-TTY → exit 2 with a clear error
    naming the missing var
  - Second `--dry-run` run with the same inputs produces byte-identical
    command list (idempotency proof)
  - Unsupported distro (mocked `/etc/os-release` cat to RHEL) → exit 3
    with a message pointing at `docs/deployment/postgres.md`
- `tests/scripts/test_postgres_tune.py`:
  - `--help` exits 0
  - `--ram 16 --dry-run` emits `shared_buffers = 4GB`,
    `effective_cache_size = 12GB`, `work_mem = 128MB`,
    `maintenance_work_mem = 1GB`
  - `--ram 32 --dry-run` scales linearly: `shared_buffers = 8GB`, etc.
  - Output is a drop-in `conf.d/corpus-forge.conf` file, not edits to
    the main `postgresql.conf`
- `tests/scripts/test_docker_compose.py`:
  - YAML parses
  - Has exactly one service named `postgres`
  - Image is `pgvector/pgvector:pg17`
  - Volume mount mounts `postgres-initdb.sql` to
    `/docker-entrypoint-initdb.d/`
  - Healthcheck uses `pg_isready`
  - Env file reference matches `.env.postgres.example`
- `tests/scripts/test_deployment_docs.py`:
  - `docs/deployment/postgres.md`, `docker.md`, `lxc.md` all exist
  - Each has the expected H2 sections (e.g. `## Prerequisites`,
    `## Quick start`, `## Manual procedure`, `## Tuning`, `## Backups`)
  - `README.md` contains a working markdown link to each page

### Green

- `scripts/postgres-bootstrap.sh`:
  - Flags: `--help`, `--dry-run`, `--db NAME`, `--user NAME`,
    `--password STR`, `--cidr CIDR`, `--pg-version N`,
    `--no-listen`, `--quiet`
  - Env vars (consulted when flag not supplied): `CF_PG_DB`,
    `CF_PG_USER`, `CF_PG_PASSWORD`, `CF_PG_CIDR`, `CF_PG_VERSION`
  - Interactive prompts only when stdin is a TTY and the value isn't
    provided via flag or env var
  - Steps: detect Debian/Ubuntu via `/etc/os-release`; add PGDG apt
    repo (idempotent — check signed-by file exists); `apt update`;
    `apt install postgresql-N postgresql-N-pgvector`;
    `systemctl enable --now postgresql`; create role (idempotent via
    `DO $$ BEGIN IF NOT EXISTS … END $$`); create db (skip if
    `SELECT 1 FROM pg_database WHERE datname=...` returns row);
    `CREATE EXTENSION IF NOT EXISTS vector`; sed-edit
    `listen_addresses` (unless `--no-listen`); append `pg_hba.conf`
    entry (grep before append); `systemctl reload postgresql`; print
    DSN
- `scripts/postgres-tune.sh`:
  - Flags: `--help`, `--ram GB`, `--dry-run`, `--pg-version N`,
    `--config-dir PATH`
  - Writes a drop-in `conf.d/corpus-forge.conf` rather than editing
    `postgresql.conf` directly — easier to revert, harder to break
  - Tuning formulae (locked in this phase, document in script comment):
    - `shared_buffers` = 25% of RAM
    - `effective_cache_size` = 75% of RAM
    - `work_mem` = max(64MB, RAM_GB * 8 MB)
    - `maintenance_work_mem` = max(512MB, RAM_GB * 64 MB)
    - `wal_compression` = on
- `scripts/docker-compose.postgres.yml`:
  - Single `postgres` service; `pgvector/pgvector:pg17`
  - `volumes: postgres-data:/var/lib/postgresql/data` (named volume)
  - `./postgres-initdb.sql:/docker-entrypoint-initdb.d/00-init.sql:ro`
  - `env_file: .env.postgres`
  - Healthcheck: `pg_isready -U $POSTGRES_USER -d $POSTGRES_DB`
  - Restart policy `unless-stopped`
- `scripts/.env.postgres.example`:
  - `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, plus a
    commented `# rename to .env.postgres before docker compose up`
    note
- `scripts/postgres-initdb.sql`:
  - `CREATE EXTENSION IF NOT EXISTS vector;` — runs on first container
    start before corpus-forge connects
- `docs/deployment/postgres.md` — bare-metal Debian/Ubuntu guide:
  - `## Prerequisites` (root or sudo, modern Debian/Ubuntu)
  - `## Quick start` (one `curl | sudo bash` style invocation of the
    bootstrap script — or `wget` for the script-paranoid path)
  - `## Manual procedure` (verbatim apt commands for ops folks who
    want to understand each step)
  - `## Tuning` (sizing tables carried over from the LXC chat — RAM
    vs chunks vs latency)
  - `## Backups` (pg_dump + ZFS snapshots, both, why)
  - `## Troubleshooting` (common pg_hba mistakes, listen_addresses,
    role-vs-db-owner confusion)
- `docs/deployment/docker.md`:
  - `## Quick start` (`cp .env.postgres.example .env.postgres` →
    edit → `docker compose -f scripts/docker-compose.postgres.yml up -d`)
  - `## Health verification`
  - `## Production caveats` (default password = bad; no TLS by default;
    where to add a reverse proxy)
  - `## Backups` (volume snapshots vs pg_dump from outside the container)
- `docs/deployment/lxc.md`:
  - `## Proxmox LXC create` (template choice, sizing, unprivileged vs
    privileged)
  - `## Inside the LXC` (run the bootstrap script via apt)
  - `## Pitfalls` (unprivileged container uid mapping for
    `/var/lib/postgresql` bind-mounts; pg_hba.conf ordering; missing
    `nesting=1` ONLY if Docker is also wanted)
  - `## Tailscale` (recommended: bind LXC to tailnet hostname so
    every machine reaches it by the same name)
  - `## Backups combo` (Proxmox `vzdump` + scheduled `pg_dump` to NAS)
- `README.md` updates:
  - Add a short paragraph in the existing backend section pointing at
    `docs/deployment/postgres.md` (bare metal) and
    `docs/deployment/docker.md` (Docker) and
    `docs/deployment/lxc.md` (Proxmox LXC).

### Verification

- `bash -n scripts/*.sh` clean
- `shellcheck scripts/*.sh` clean (add to CI if not already there)
- `uv run python -m pytest tests/scripts -x` green
- Markdown lint (if any) clean
- README rendered cross-links resolve (rot-detector test)

### Risks / open questions

- **shellcheck not on every dev machine.** If the project's CI doesn't
  already run shellcheck, we don't add a CI dependency in this phase
  — just lint locally during dev. Document the recommendation in the
  script header.
- **`set -euo pipefail` + idempotent SQL.** Postgres role-create is
  awkward to make idempotent without using `DO $$ ... $$ EXECUTE`
  blocks; we use those rather than the simpler `CREATE ROLE` + accept
  failure pattern, because the latter masks real errors.
- **Tailscale recommendation in `lxc.md`** assumes user has Tailscale.
  Frame it as one option among LAN-IP, not the only path.
- **Docker default password.** The `.env.postgres.example` ships with
  a placeholder like `CHANGEME_LONG_RANDOM` — the docs and bootstrap
  script must both yell about this.

## Release shape

- `0.1.0b7` — additive only. No existing user-facing flow changes.
  No new top-level deps. CHANGELOG entry describes the new
  `scripts/` artifacts and `docs/deployment/` pages.
