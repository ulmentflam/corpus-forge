# Proxmox LXC deployment

A Proxmox LXC is the recommended home for a long-running corpus-forge
Postgres in a homelab or small-team setup: lighter than a VM,
snapshottable with `vzdump`, and (with the unprivileged + Tailscale
combo) reasonably isolated from the host.

This guide is specifically about hosting the Postgres database in an
LXC; the corpus-forge ingester daemon can run anywhere with network
access to it.

## Proxmox LXC create

### Template choice

Pick a Debian or Ubuntu LTS template from the `Templates` page of the
Proxmox storage view:

- `debian-12-standard` — recommended, most-tested with the PGDG repo.
- `ubuntu-24.04-standard` — fine, slightly larger base image.

Avoid `*-cloudimg-amd64` variants for LXC — those are pre-configured
for cloud-init, which collides with Proxmox's container provisioning
in surprising ways.

### Sizing

Start point that comfortably handles the maintainer's reference 2.25M-
chunk corpus (~49 GB total PG footprint at Qwen3-Embedding-8B 2048-dim):

| Resource | Value | Why |
|---|---|---|
| RAM | 16 GB | shared_buffers=4 GB + effective_cache_size=12 GB matches the tuning drop-in |
| Cores | 4 | HNSW backfill is the only CPU-heavy phase; 4 vCPUs is enough |
| Rootfs | 150 GB | 49 GB working set + WAL + 1–2 pg_dump snapshots + HNSW build headroom |
| Swap | 0 GB | Postgres performs poorly when paged out — avoid swap on the rootfs |

For smaller corpora, scale RAM down using the table in
[`postgres.md#tuning`](postgres.md#tuning). The 150 GB rootfs is the
one number you should not undersize: HNSW index builds can transiently
double the on-disk footprint of the embedding table.

### Privileged vs unprivileged

**Unprivileged** is the right default. The Phase R bootstrap script
runs cleanly inside an unprivileged container — none of its steps
require host kernel capabilities.

Pick `Unprivileged container: Yes` in the Proxmox create wizard, or
add `unprivileged: 1` to the `pct` line if you're scripting it:

```bash
pct create 200 local:vztmpl/debian-12-standard_12.0-1_amd64.tar.zst \
  --hostname corpus-forge-db \
  --unprivileged 1 \
  --memory 16384 --swap 0 \
  --cores 4 \
  --rootfs local-zfs:150 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp \
  --features nesting=0 \
  --onboot 1
```

Use **privileged** containers (`--unprivileged 0`) only if you
absolutely need a host-bind-mount with strict uid passthrough — almost
nothing about corpus-forge needs that.

## Inside the LXC

Once the container is up:

```bash
# 1. Get a root shell.
pct enter 200

# 2. Install corpus-forge's scripts (clone the repo or copy
#    scripts/postgres-bootstrap.sh + scripts/postgres-tune.sh into /root).
apt-get update && apt-get install -y git curl
git clone https://github.com/ulmentflam/corpus-forge.git /root/corpus-forge

# 3. Bootstrap Postgres + pgvector.
cd /root/corpus-forge
CF_PG_DB=corpus_forge \
CF_PG_USER=corpus_forge \
CF_PG_PASSWORD="$(openssl rand -base64 32)" \
CF_PG_CIDR=192.168.1.0/24 \
  bash scripts/postgres-bootstrap.sh

# 4. Apply the tuning drop-in.
bash scripts/postgres-tune.sh --ram 16
systemctl reload postgresql

# 5. Smoke-test from outside the container.
psql -h <LXC IP> -U corpus_forge -d corpus_forge -c '\dx vector'
```

The script's idempotency means you can re-run step 3 after a tweak
without losing anything — the role and database are skip-if-exists,
the `pg_hba.conf` append is grep-guarded, and the
`listen_addresses` edit is a sed in-place that converges on the same
value.

## Pitfalls

### Unprivileged container UID mapping

Proxmox unprivileged containers map UIDs through a shift, typically
**+100000** — root inside the container is UID 100000 on the host. The
Postgres `postgres` user is UID 999 inside the container, which maps
to UID **100999** on the host. This matters in two situations:

1. **Host bind-mounts.** If you bind-mount a host directory into the
   container for Postgres data (instead of using the container's own
   rootfs), the host directory must be `chown -R 100999:100999`. The
   recommended pattern is the opposite: keep PG data on the container
   rootfs (or a Proxmox storage-managed mount point), not a host
   bind-mount.
2. **Restoring a `vzdump` archive onto a different Proxmox host.** UID
   mapping shifts can differ between hosts; use Proxmox's `pct
   restore` rather than untarring by hand.

### `pg_hba.conf` ordering

The bootstrap script *appends* to `pg_hba.conf`. PG processes the file
top-down and the first matching line wins. If the default
`local all postgres peer` line comes before your `host` line, that's
fine — `local` and `host` are distinct connection types. The trap is a
broad `host all all 0.0.0.0/0 reject` line above your CIDR allow; that
will reject the corpus-forge client before your line is reached.

Check ordering:

```bash
sudo grep -nE '^(host|local)' /etc/postgresql/17/main/pg_hba.conf
```

Move the corpus-forge `host` line up if needed, then
`sudo systemctl reload postgresql`.

### `nesting=1` only if you need Docker too

If you plan to run *Docker* inside the LXC (e.g. to host the
corpus-forge daemon alongside Postgres), Proxmox needs
`features: nesting=1`. For a pure Postgres host (this guide's main
case) leave nesting off — fewer capability surfaces exposed.

```bash
# Toggle nesting on an existing container.
pct set 200 -features nesting=1
pct reboot 200
```

### `listen_addresses = '*'` and a public IP

`listen_addresses = '*'` makes Postgres listen on every interface,
including any public IP the container has. Combine with a tight
`pg_hba.conf` (private CIDR or single tailnet address), and ideally
firewall the LXC at the Proxmox host level too. The script's default
is `'*'`; if you want the database to only accept connections on the
tailnet interface, use `--no-listen` and set `listen_addresses`
manually to the specific IP.

## Tailscale

If you already run Tailscale on every machine that talks to
corpus-forge, **using the tailnet hostname as the database address** is
the simplest way to bind the entire mesh by a stable name.

```bash
# Inside the LXC.
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up --auth-key=tskey-... --hostname=corpus-forge-db

# Confirm the tailnet IP.
tailscale ip -4
# 100.x.y.z

# Use the tailnet hostname in your corpus-forge config:
#   dsn = "postgresql://corpus_forge:...@corpus-forge-db:5432/corpus_forge"
# MagicDNS resolves corpus-forge-db on every joined machine.
```

When you run the bootstrap script, pass the tailnet CIDR
(`100.64.0.0/10` covers the whole tailnet) or a tighter range that
matches your own tags / ACL setup:

```bash
CF_PG_CIDR=100.64.0.0/10 ... bash scripts/postgres-bootstrap.sh
```

This is one option among several — bare LAN IPs, a private VPC, a
WireGuard mesh you manage yourself — pick whatever you already have.
The point is *something* that gives a stable hostname for every
client.

## Backups combo

Run two backup mechanisms in parallel — each protects against a
failure mode the other doesn't.

| Layer | Tool | Schedule | Restores |
|---|---|---|---|
| Whole-LXC snapshot | Proxmox `vzdump` | nightly | LXC bit-identical, including config, hostname, network |
| Logical DB dump | `pg_dump -Fc` | nightly | Database into any compatible Postgres (including a different LXC) |

```bash
# On the Proxmox host — nightly vzdump.
vzdump 200 --compress zstd --mode snapshot --storage local
# /var/lib/vz/dump/vzdump-lxc-200-YYYY_MM_DD-HH_MM_SS.tar.zst

# Inside the LXC — nightly pg_dump shipped off-host.
sudo -u postgres pg_dump -Fc -d corpus_forge \
  | ssh nas-host "cat > /backup/corpus-forge/$(date +%Y%m%d).dump"
```

The `vzdump` archive is the answer to "the LXC is gone"; the
`pg_dump` is the answer to "corpus-forge wrote bad data" (you can
restore the dump into a fresh container without reverting *system*
configuration to last night's state).

For a single-disk Proxmox host without ZFS, also rsync the
`vzdump` archives off-host weekly. For a ZFS host, `zfs send` of the
storage pool covers the same ground at lower cost.
