# Docker Compose deployment

A self-contained Postgres + pgvector stack for corpus-forge in a single
`docker-compose.postgres.yml`. Useful for:

- Bringing up a throwaway database on your laptop while you evaluate
  corpus-forge.
- Running on a NAS / homelab box where you already manage everything
  via Compose.
- Spinning a Postgres next to a corpus-forge container in the same
  network.

For long-running production deployments where you control the host OS
(e.g. a Proxmox LXC), prefer the bare-metal procedure in
[`postgres.md`](postgres.md) — fewer moving parts, simpler backups.

## Quick start

```bash
# 1. Copy the env-file template and replace the placeholder password.
cp scripts/.env.postgres.example scripts/.env.postgres
# CHANGEME_LONG_RANDOM must be replaced before `docker compose up`.
# Recommended: openssl rand -base64 32
${EDITOR:-vi} scripts/.env.postgres

# 2. Bring the stack up.
docker compose -f scripts/docker-compose.postgres.yml up -d

# 3. Watch it become healthy.
docker compose -f scripts/docker-compose.postgres.yml ps
# corpus-forge-postgres   ...   Up 7s (healthy)
```

The `pgvector/pgvector:pg17` image runs `postgres-initdb.sql` on first
container start, which executes
`CREATE EXTENSION IF NOT EXISTS vector;` so corpus-forge's migration
sees a vector-aware database from the first connection.

After the container is healthy, point a client at it:

```toml
# ~/.config/corpus-forge/config.toml
[backend]
kind = "postgres"
dsn  = "postgresql://corpus_forge:PASSWORD_FROM_ENV_FILE@localhost:5432/corpus_forge"
schema = "corpus"
```

```bash
corpus-forge migrate
corpus-forge doctor
```

## Health verification

```bash
# Compose's own view.
docker compose -f scripts/docker-compose.postgres.yml ps

# pg_isready end-to-end (same probe Compose uses).
docker exec corpus-forge-postgres pg_isready -U corpus_forge -d corpus_forge

# Confirm the extension landed.
docker exec -it corpus-forge-postgres \
  psql -U corpus_forge -d corpus_forge -c '\dx vector'
```

The healthcheck runs every 10 seconds; the `start_period` (30 s) gives
the database time to initialise on first boot without the container
flapping into unhealthy state.

## Production caveats

The stack ships defaults that are convenient, not safe. Read these
before exposing the container to anything beyond `localhost`.

1. **Default password.** `.env.postgres.example` ships with the literal
   string `CHANGEME_LONG_RANDOM`. The bootstrap and Compose paths both
   shout about this — replace it before `docker compose up`, ideally
   with `$(openssl rand -base64 32)`. A short, guessable password on a
   public-facing Postgres is the most common way these stacks get
   pwned.
2. **No TLS by default.** The image listens on plaintext 5432. Acceptable
   over a private tailnet or a private VPC, never over the open
   internet. If you must expose it, terminate TLS at a reverse proxy
   (Caddy / nginx / Traefik / Cloudflared) and bind 5432 to
   `127.0.0.1:5432` inside Compose.
3. **No backups.** Compose itself doesn't take backups. Wire a
   sidecar that runs `pg_dump` on a schedule (see the Backups
   section below) and ship the dumps off the host — a volume snapshot
   on the same physical disk doesn't survive that disk dying.
4. **Single-node only.** This compose file does not configure
   streaming replication. For HA, use a dedicated Postgres
   orchestrator (Patroni, CrunchyData PGO, etc.) rather than extending
   this file.
5. **Restart loops mask config errors.** `restart: unless-stopped` is
   convenient but can hide config typos in `postgres-initdb.sql` —
   check `docker logs` on first boot.

## Backups

```bash
# 1. Logical dump from outside the container — portable, restorable
#    into any compatible PG. Run as a cron or systemd timer.
docker exec corpus-forge-postgres \
  pg_dump -Fc -U corpus_forge -d corpus_forge \
  > corpus_forge-$(date +%Y%m%d).dump

# 2. Restore.
docker exec -i corpus-forge-postgres \
  pg_restore -U corpus_forge -d corpus_forge --clean --if-exists \
  < corpus_forge-YYYYMMDD.dump
```

Volume snapshots (e.g. `docker run --rm -v
corpus-forge-postgres-data:/data alpine tar czf /backup/data.tgz /data`)
are cheap but only safe when Postgres is *stopped* — otherwise you
capture an inconsistent file-level snapshot of a database that's mid-
transaction. Prefer `pg_dump` for live snapshots.

For a small homelab the recommended cadence is:

| Job | Frequency | Destination |
|---|---|---|
| `pg_dump` (logical, full) | nightly | local + off-host (NAS / S3) |
| Volume tarball (stopped) | weekly | off-host |
| Compose YAML + env-file (no password) | per-change | git / config-management |

The Compose file and (sanitised) env-file under version control is the
recovery story for the *configuration* — the dumps cover the data.

## Tearing down

```bash
# Stop + remove the container, keep the volume (and therefore the data).
docker compose -f scripts/docker-compose.postgres.yml down

# Stop + remove EVERYTHING including the volume.
docker compose -f scripts/docker-compose.postgres.yml down -v
```

The named volume `corpus-forge-postgres-data` persists across `down`
unless you pass `-v`. Restore by `docker compose up -d` after a clean
shutdown — the database starts back up against the same data
directory.
