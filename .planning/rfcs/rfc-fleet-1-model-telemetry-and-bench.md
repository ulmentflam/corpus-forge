# RFC: Fleet 1/4 — model registry, speed telemetry, and `bench embed`

status: accepted
**Owner**: nightly (open for any agent to claim)
**Priority**: P0 — operator-requested 2026-06-04
**Depends on**: none (first of the fleet series; rfc-fleet-2..4 build on its tables)

## Context

The operator runs corpus-forge on multiple machines (Mac + Linux/GB10,
all on one tailnet) against one shared Postgres. Nothing records
**which models each host can run or how fast** — embedder lane choice
(which machine should run `qwen3-4096`?) is operator folklore. With a
3.29 M-chunk corpus and embedding coverage at 73 % / 1.9 % / 0.2 %
across the three configured embedders, "how many days will this lane
take on which box" is the question that decides everything else in
the fleet series — distributed claiming (fleet-2), config federation
(fleet-3), and Tailscale ergonomics (fleet-4) all consume the tables
this RFC creates.

Existing assets: per-host `host_id` (`~/.config/corpus-forge/host_id`),
the `corpus_forge.acceleration` hardware probe (PR #87), embedder
fingerprints, and `EmbedderConfig.base_url` which already distinguishes
local vs API-shaped embedders.

## Goals

- Three new DB tables — `corpus.hosts`, `corpus.models`,
  `corpus.model_benchmarks` — recording every host, every model it has
  run or has available, and measured throughput per
  (host, model, device, transport, batch).
- **Passive telemetry**: every real `embed` backfill run records its
  observed rate. Data accumulates without anyone asking for it.
- **Active sampling**: `corpus-forge bench embed` runs a *very small*
  sample (default ≈ 64 chunks) through configured embedders — local
  and API — and writes a benchmark row per embedder, printing a
  comparison table.
- `corpus-forge models list` / `corpus-forge hosts list` read verbs
  (Rich table + `--json` for agents).

## Non-goals

- No automatic lane assignment or scheduling decisions (fleet-2 task
  `hosts plan` makes a *recommendation*; assignment stays manual).
- No LLM / VLM / Whisper benchmarking in v1 — the `models.kind`
  column reserves room; embedders only for now.
- No remote-host benchmarking — `bench` measures the machine it runs
  on (API embedders measure the round-trip from this machine, which
  is the number that matters for lane planning anyway).

## Approach

### Schema (one alembic revision)

```
corpus.hosts
  host_id        text primary key       -- from ~/.config/corpus-forge/host_id
  hostname       text
  os             text
  accelerator    jsonb                  -- corpus_forge.acceleration probe output
  tailscale_name text null              -- best-effort; fleet-4 fills it properly
  last_seen      timestamptz
corpus.models
  model_key      text primary key       -- "<provider>:<model_id>"
  kind           text                   -- embedder | llm | vlm | whisper
  provider       text
  model_id       text
  dimension      int null
  first_seen     timestamptz
corpus.model_benchmarks
  id             bigserial primary key
  host_id        text references corpus.hosts
  model_key      text references corpus.models
  source         text                   -- "bench" | "embed-run"
  transport      text                   -- "local" | "api"
  device         text                   -- cuda | mps | cpu | remote
  batch_size     int
  sample_chunks  int
  chunks_per_s   numeric
  tokens_per_s   numeric null
  latency_p50_ms numeric null
  latency_p95_ms numeric null
  measured_at    timestamptz
```

Index `model_benchmarks(host_id, model_key, measured_at desc)` for the
"latest per host+model" reads.

Host upsert + heartbeat at daemon startup and at the top of
`embed`/`bench` runs. "Available" models = union of configured
embedders, best-effort `ollama list`, and anything with a benchmark
row. SQLite backend: tables exist (same alembic path) but multi-host
rows are not expected; no special casing needed here.

### `corpus-forge bench embed`

New `corpus_forge/admin/bench.py`. For each target embedder (`--all`
configured, or `-e name…`):

- Sample `--sample N` (default 64) chunks: prefer **real pending**
  chunks for that embedder (the work counts — vectors are persisted),
  fall back to the deterministic synthetic corpus from
  rfc-benchmark-corpus-and-media-fixtures when the lane has no
  backlog. Synthetic vectors are **never** persisted.
- Time per-batch wall clock; compute chunks/s, tokens/s where a
  tokenizer is available, p50/p95 per-request latency for API
  transports. Tag `transport=api` when the embedder resolves to a
  remote `base_url`, else `local`.
- Write one `model_benchmarks` row per embedder (`source="bench"`);
  print a Rich table sorted by chunks/s; `--json` emits one clean
  object.

### Passive telemetry

`embed.py::backfill_embedder` already tracks processed counts for its
progress bar — at end of run (and per ~10k-chunk checkpoint so a
crashed run still reports), write a `model_benchmarks` row with
`source="embed-run"` and the aggregate rate.

### Read verbs

- `corpus-forge models list` — registry + latest benchmark per
  (host, model) + staleness hint (measured_at age).
- `corpus-forge hosts list` — hosts, accelerator summary, last_seen,
  latest aggregate rate. Both `--json`.

### Install / configure touch points

- `corpus-forge migrate` picks up the revision (documented in
  CLAUDE.md §6 sequence — no new step; `install.sh` / `install.ps1`
  already run migrate).
- `corpus-forge setup` (both wizard and `CF_NON_INTERACTIVE` path)
  ends by printing a "next steps" hint that now includes
  `corpus-forge bench embed --all` as the post-setup calibration step.
- `corpus-forge doctor` gains an informational `model_telemetry`
  check: OK with "n benchmarks, freshest X ago" or "no benchmarks yet
  — run corpus-forge bench embed".

**Coverage note:** every task below adds Python; the tester
specialist must cover new code to ≥ 90 % line coverage
(`make test-unit` gate) as part of "done."

## Tasks

- [ ] Alembic revision: `corpus.hosts` / `corpus.models` /
      `corpus.model_benchmarks` + indexes; idempotent re-run test.
- [ ] Host upsert/heartbeat backend helper wired into daemon startup
      and `embed` entry; `accelerator` from the acceleration probe.
- [ ] Model registry upsert from configured embedders + best-effort
      `ollama list`.
- [ ] `corpus_forge/admin/bench.py`: `bench embed` with `--sample`
      (default 64), `-e`/`--all`, real-pending-first sampling with
      synthetic fallback, persisted-vs-not vector rules pinned by
      tests, Rich table + `--json`, writes `source="bench"` rows.
- [ ] Passive telemetry rows from `backfill_embedder`
      (`source="embed-run"`, end-of-run + periodic checkpoint).
- [ ] `corpus-forge models list` and `corpus-forge hosts list` verbs
      (Rich + `--json`).
- [ ] Doctor `model_telemetry` informational check.
- [ ] Setup wizard / non-interactive "next steps" hint includes
      `bench embed --all`; docs touch in CLAUDE.md §6.

## Verification

- `make test-unit` ≥ 90 % coverage including `admin/bench.py` and the
  new backend helpers.
- Integration (testcontainers Postgres): `bench embed` against a
  seeded corpus writes a benchmark row; `models list --json` returns
  it; re-running migrate is a no-op.
- Manual fleet acceptance: run `bench embed --all` on two tailnet
  hosts; `models list` from either machine shows both hosts' rows.

## References

- `corpus_forge/acceleration.py` — hardware probe.
- `corpus_forge/embed.py` — backfill loop that gains passive telemetry.
- `corpus_forge/admin/` — verb-module pattern to follow (`prune.py`).
- `.planning/rfcs/rfc-benchmark-corpus-and-media-fixtures.md` —
  synthetic sample corpus.
- `.planning/rfcs/rfc-best-embedding-models-and-evaluation.md` — the
  ranking harness this telemetry feeds.
