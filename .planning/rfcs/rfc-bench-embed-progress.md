# RFC: Progress feedback for `bench embed` — phases + cold-start accounting

status: accepted
**Owner**: nightly (open for any agent to claim)
**Priority**: P2 — operator-requested 2026-06-08
**Depends on**: rfc-fleet-1-model-telemetry-and-bench (the `bench embed`
verb + `corpus.model_benchmarks` table this enhances; merged via #97)

## Context

`corpus-forge bench embed -e nomic-code --sample 64` runs a tiny
sample through an embedder and prints a throughput row. Today the
human watching it sees **nothing** until the run finishes — and for a
local model like `nomic-code` the longest part is the **cold start**:
`register_from_config(...)` + `embedder.warmup()` load (and on a fresh
box, download) the model before any timing begins. During those
seconds-to-tens-of-seconds the terminal is frozen with no signal that
the process is alive vs. wedged.

The current shape (`corpus_forge/admin/bench.py::bench_one`):

1. **Load + warmup** — `register_from_config` then `embedder.warmup()`.
   This is the cold start. It happens *before* `t0`, so it is
   correctly **excluded** from the measured `chunks_per_s` — but it is
   also completely invisible.
2. **Sample fetch** — `_sample_pending` (real pending chunks) or the
   deterministic `synthetic_sample` fallback.
3. **Encode (timed)** — for `transport == "api"` a per-text loop
   (latency distribution); for local transports a **single batched**
   `embedder.encode(texts)` call — one opaque call, no sub-steps.
4. **Write** — `register_embedder` + the `model_benchmarks` row, then
   the Rich comparison table (or `--json`).

The repo already ships the right tool: `corpus_forge/ui/progress.py`'s
`make_progress` factory, with a **bounded** mode (spinner + elapsed +
remaining/ETA) and an **unbounded** mode (spinner + elapsed), plus
agent-mode awareness so it never pollutes `--json` on stdout. `embed.py`
already uses it for the backfill bar.

## Goals

- The human running `bench embed` always sees what phase it's in —
  **especially the cold-start load**, rendered as a live "still
  working" indicator rather than a frozen prompt.
- Cold start is **accounted for**, two ways:
  - **UI**: shown as its own phase (indeterminate spinner + elapsed —
    you cannot ETA a model download), distinct from the encode phase.
  - **Measurement**: the reported `chunks_per_s` stays load-excluded
    (it already is); make that guarantee explicit and **pinned by a
    test**, and surface the cold-start duration so a "first run on this
    box" is distinguishable from steady-state.
- Zero change to the measured numbers a warm `bench embed` produces
  today (the bar must not alter what's timed) and zero change to
  `--json` / agent-mode output shape beyond an additive field.

## Non-goals

- No new heavy dependency — `make_progress` (Rich) is already present.
- No change to the sampling logic, synthetic fallback, or the
  benchmark row's existing columns' meaning.
- Not a general per-batch streaming-encode rework. If a determinate
  encode bar requires sub-batching the local `encode()`, that is
  optional (see Approach) and must preserve the single-batch timing
  semantics.

## Approach

### Phase-aware progress

Wrap `bench_one` (or the `cmd_bench_embed` loop that calls it) in a
small phase sequence driven by `make_progress`, each phase a labeled
task:

| Phase | Mode | Why |
|---|---|---|
| `load model (cold start)` | unbounded spinner + elapsed | duration unknowable (download/load); the human just needs "alive" |
| `warmup` | unbounded spinner + elapsed | first forward pass; also cold-ish |
| `sample` | unbounded (tiny) or skipped if instant | fetch real-pending / synthesise |
| `encode N chunks` | bounded (ETA) for `api`; bounded-by-sub-batch or unbounded for local | the actual measured work |
| `write benchmark row` | unbounded (instant) | closes the loop |

- **api transport** already loops per-text → a bounded bar over
  `sample` is natural; advance once per text. This also gives a live
  ETA from the latency distribution.
- **local transport** is one batched `encode(texts)`. Two options,
  implementer picks and documents:
  - **(a) Indeterminate spinner** over the whole encode — simplest,
    zero timing risk, honest ("encoding 64 chunks…").
  - **(b) Sub-batch** the local encode into k chunks of size
    `ceil(sample/k)`, advance the bar per sub-batch, and compute
    `chunks_per_s = n / sum(per-sub-batch elapsed)`. Equivalent rate
    to one batch only if sub-batching doesn't change throughput —
    which it can (batch size affects GPU utilisation). **If (b),** the
    benchmark must keep timing a single full-size batch for the
    recorded rate and use sub-batching for the bar *only*, or the
    recorded number drifts from production. Given that risk, **(a) is
    the recommended default**; (b) only if a determinate local bar is
    deemed worth the care.

### Cold-start accounting

- The cold-start phases (load + warmup) are timed with
  `time.perf_counter()` around them and the total recorded as
  `cold_start_s`.
- **`chunks_per_s` is unchanged** — it is `n / encode_elapsed` with
  `t0` taken *after* warmup, exactly as today. Add a test that pins
  this: a fake embedder whose `warmup()` sleeps must not change
  `chunks_per_s` (only `cold_start_s`).
- Surface `cold_start_s`:
  - **Always** in the Rich table (a "cold start" column) and in
    `--json` (additive field — agents reading the payload get it for
    free; existing keys unchanged).
  - **Optionally (stretch)** persist it to `corpus.model_benchmarks`
    (new nullable `cold_start_s` column via an alembic revision) so
    `models list` / `hosts plan` can distinguish a cold first-run
    sample from a warm one. If the schema touch is out of scope for a
    first pass, print-only is acceptable and the column lands later.

### Output / agent discipline

- Progress renders on stderr via `make_progress` (Rich) and is
  suppressed/replaced by milestone log lines under agent mode, like
  `embed.py` — it must never interleave into the `--json` object on
  stdout. The self-emitting-verb pattern in `bench.py` already gates
  stdout; the bar sits alongside it.
- `--json` payload gains `cold_start_s` (float, nullable) per result;
  no existing field changes.

## Tasks

- [ ] Phase-aware `make_progress` wrapping in `bench_one` /
      `cmd_bench_embed`: cold-start + warmup as unbounded spinners with
      elapsed; encode phase bounded for `api`, indeterminate (option a)
      for local; write phase. Renders on stderr, agent-mode aware,
      never pollutes `--json`.
- [ ] Time the load+warmup as `cold_start_s` (perf_counter around the
      pre-`t0` block); confirm `chunks_per_s` semantics are unchanged.
- [ ] Surface `cold_start_s` in the Rich table (new column) and the
      `--json` payload (additive field).
- [ ] Tests:
  - [ ] `chunks_per_s` is unaffected by a slow `warmup()` (fake
        embedder with a sleeping warmup → `cold_start_s` rises,
        `chunks_per_s` constant).
  - [ ] progress renders on stderr / is suppressed under agent mode;
        `--json` stdout stays a clean parseable object (no Rich escape
        codes), with the new `cold_start_s` key present.
  - [ ] api per-text path advances the bar `sample` times; local path
        shows the indeterminate encode phase.
  - [ ] ≥90% line coverage on the new code (`make test-unit` gate).
- [ ] (Stretch) alembic revision adding nullable
      `model_benchmarks.cold_start_s`; `bench` writes it; `models list`
      surfaces it. Idempotent re-run test, head-pin transition like the
      0018→0019 precedent.

## Verification

- `corpus-forge bench embed -e nomic-code --sample 64` shows a live
  cold-start spinner during model load (no frozen terminal), then an
  encode phase, then the table — with a `cold_start_s` cell.
- A warm second run reports the same `chunks_per_s` band as today
  (the bar changed nothing that's timed) and a much smaller
  `cold_start_s`.
- `corpus-forge bench embed -e nomic-code --sample 64 --json` emits a
  single clean JSON object carrying `cold_start_s`; no progress
  artifacts on stdout.
- `make test-unit` ≥ 90% on the new code; full suite green.

## References

- `corpus_forge/admin/bench.py` — `bench_one` (load/warmup/encode/write
  phases), `cmd_bench_embed`, the `--json` self-emit path.
- `corpus_forge/ui/progress.py` — `make_progress` bounded/unbounded
  modes + agent-mode awareness; `embed.py` is the usage precedent.
- `.planning/rfcs/rfc-fleet-1-model-telemetry-and-bench.md` — the
  `bench embed` verb + `model_benchmarks` table this enhances.
