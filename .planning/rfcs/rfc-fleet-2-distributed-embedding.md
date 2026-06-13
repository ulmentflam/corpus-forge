# RFC: Fleet 2/4 — distributed embedding via claim-based backfill

status: accepted
**Owner**: nightly (open for any agent to claim)
**Priority**: P0 — operator-requested 2026-06-04
**Depends on**: rfc-fleet-1-model-telemetry-and-bench (corpus.hosts table, host heartbeat)

## Context

The corpus has 3.29 M chunks; `nomic-code` is 1.9 % embedded and
`qwen3-4096` is 0.2 %. At single-machine rates the backlog is days-to-
weeks of wall clock, while other tailnet machines sit idle — because
only one machine can safely run a backfill today:

- `corpus_forge/embed.py::backfill_embedder` fetches
  `chunks_missing_embedding(embedder_id, limit=1000)` with **no
  claiming**. Two hosts running the same lane fetch overlapping
  batches; the `(content_hash, embedder_id)` reuse path in
  `backends/postgres.py` keeps the *data* correct but the duplicate
  GPU compute is pure waste.
- There is no per-host control over *which* embedder lanes a machine
  works (the operator currently keeps embedding off a machine by
  simply not starting the embed-worker — all-or-nothing).

All coordination state can live in the shared Postgres the fleet
already points at — no broker, no new service.

## Goals

- N machines can drain the **same** embedder's backlog concurrently
  with zero duplicated compute, crash-safe via lease expiry, with no
  operator babysitting.
- Different machines can be pinned to **different** lanes via local
  config (`[embed] lanes`), so a CUDA box takes `qwen3-4096` while a
  weaker box takes `nomic`.
- Single-host behavior is unchanged in effect and within 5 % in
  throughput (measured, not assumed).
- Progress reporting stays truthful when multiple hosts work one lane
  (no two progress bars both claiming the full backlog).

## Non-goals

- **No general cluster scheduler / rebalancer.** Greedy batch-claiming
  only; a host that dies simply stops claiming.
- **No SQLite federation.** Claim methods on the SQLite backend raise
  `FederationUnsupported`; doctor WARNs if multiple hosts have
  heartbeated against a SQLite corpus.
- **No automatic lane assignment.** `hosts plan` (here, stretch)
  prints a recommendation from fleet-1 benchmarks; the operator
  writes the config.
- No changes to chunking/ingest distribution — ingest is already
  per-host by source locality; only embedding is distributed.

## Approach

### Claim primitive

```
corpus.embed_claims
  claim_id    bigserial primary key
  embedder_id int
  chunk_id    bigint
  host_id     text references corpus.hosts
  claimed_at  timestamptz
  lease_until timestamptz        -- claimed_at + lease_ttl
  unique (embedder_id, chunk_id)
```

`claim_chunks_for_embedding(embedder_id, host_id, batch, lease_ttl)`
selects from the missing-embeddings set, excluding live claims,
`FOR UPDATE SKIP LOCKED`, inserts claim rows, returns the batch.
Completion deletes the claims (the embedding row is the durable
record). `expire_stale_claims` deletes rows past `lease_until` —
called opportunistically at the top of each claim call, so abandoned
work self-heals with zero operator action.

### Backfill loop

`embed.py` switches to claim/release **unconditionally** — a single
host claiming everything is behaviorally identical to today, modulo
one extra round-trip per batch (the < 5 % perf gate below keeps us
honest). Lease TTL configurable via `[embed] claim_lease_ttl`
(default 600 s; must comfortably exceed worst-case batch wall clock —
the doctor check warns when a host's observed rate × batch size
approaches the TTL).

Progress totals: `count_chunks_missing_embedding` minus other hosts'
live claims, so concurrent workers each report their share of a
shrinking pool.

### Lane pinning (`[embed] lanes`)

New local-config block:

```toml
[embed]
lanes = ["qwen3-4096", "nomic-code"]   # this host only works these
# claim_lease_ttl = 600
```

Absent block → all active embedders (today's behavior). The
embed-worker and `corpus-forge embed --all` respect it; explicit
`corpus-forge embed -e <name>` overrides (operator said so).

### Install / configure touch points

- `corpus-forge setup` wizard: when more than one host has
  heartbeated (fleet-1 `corpus.hosts`), offer a lane-pinning prompt
  seeded from the accelerator probe ("this host has CUDA ≥ 8 GB —
  suggest lanes: qwen3-4096"). `CF_NON_INTERACTIVE` accepts
  `--embed-lanes a,b`.
- `corpus-forge doctor`: `embed_claims` check — stale-claim count,
  lease-TTL-vs-observed-rate sanity, FederationUnsupported WARN on
  multi-host SQLite.
- `corpus-forge migrate` carries the revision (installers already run
  it).

**Coverage note:** ≥ 90 % line coverage on all new code is part of
"done" per task (`make test-unit` gate).

## Tasks

- [x] Alembic revision: `corpus.embed_claims` + unique
      `(embedder_id, chunk_id)` + lease index; idempotent re-run test.
- [x] Backend methods `claim_chunks_for_embedding` /
      `release_claims` / `expire_stale_claims`
      (`FOR UPDATE SKIP LOCKED`); SQLite raises
      `FederationUnsupported`.
- [x] `embed.py` backfill loop on claims; `[embed] claim_lease_ttl`
      config; truthful multi-host progress totals. — claim/release loop in
      `embed.py:backfill_embedder` (`claim_chunks_for_embedding` page fetch,
      `count_live_claims` progress adjust); `config.embed.claim_lease_ttl`.
- [x] Claim-path latch self-heal (live bug 2026-06-08): a first-claim FK
      violation (missing `corpus.hosts` row from a silently-failed
      heartbeat) no longer PERMANENTLY demotes the worker to the un-deduped
      `chunks_missing_embedding` fallback. `_fetch_page` now re-heartbeats
      once and retries the claim; permanent `use_claims=False` is reserved
      for `FederationUnsupported` (SQLite) only. Regression test:
      heartbeat-fails → first claim 23503 → re-heartbeat → retry succeeds →
      worker stays coordinated.
- [ ] `[embed] lanes` local-config block; embed-worker + `--all`
      respect it; explicit `-e` overrides; setup wizard lane prompt +
      `--embed-lanes` non-interactive flag.
- [ ] Crash-recovery test: worker A claims and dies; worker B picks
      up after lease expiry; no chunk embedded twice, none lost.
- [ ] Two-worker integration test (testcontainers Postgres): same
      lane, concurrent backfills → disjoint work, full coverage; perf
      assertion: single-host claim overhead < 5 % vs old fetch path.
- [ ] Doctor `embed_claims` check (stale claims, TTL sanity,
      multi-host-SQLite WARN).
- [ ] `hosts plan` (stretch): benchmarks + per-lane backlog →
      recommended host→lane assignment + projected drain time;
      print-only.

## Verification

- The two-worker and crash-recovery integration tests are the
  acceptance bar — Phase C of the fleet is not "done" without them.
- Duplicate-work probe on the real fleet:
  `select chunk_id, count(*) from corpus.embeddings_nomic_code
  group by 1 having count(*) > 1` returns zero rows after a two-host
  concurrent drain session.
- `make test-unit` ≥ 90 % coverage on claims + lane-pinning code.

## References

- `corpus_forge/embed.py` — the claim-free loop being replaced.
- `corpus_forge/backends/postgres.py` — `chunks_missing_embedding`,
  embedding-reuse path.
- `corpus_forge/identity.py` — `advisory_lock_key` precedent for
  Postgres-coordinated mutual exclusion.
- `.planning/rfcs/rfc-fleet-1-model-telemetry-and-bench.md` — hosts
  table + benchmarks consumed by `hosts plan`.
