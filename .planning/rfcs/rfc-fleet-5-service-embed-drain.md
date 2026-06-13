# RFC: Fleet 5 — the managed service drains the embed backlog after `--join`

status: accepted
**Owner**: nightly (open for any agent to claim)
**Priority**: P0 — operator-requested 2026-06-08
**Depends on**: rfc-fleet-2-distributed-embedding (claim loop, `[embed] lanes`), rfc-fleet-3-federated-config-and-setup (`setup --join`)

## Context

The fleet onboarding story (CLAUDE.md §"Add a second machine") ends
with the operator running:

```
corpus-forge bench embed --all      # record this host's per-lane throughput
corpus-forge service install        # install the daemon as a managed service
```

The promise of that second line is "the host drains backlog
continuously." **It does not.** Today the daemon
(`corpus_forge/daemon.py`) only embeds chunks *it just ingested* —
embedding is a side effect of the watch/ingest discovery callback
(`daemon.py:138-148`, embedders warmed lazily and applied to freshly
upserted chunks). A machine joined purely to *drain an existing
backlog* — the canonical reason to add a GPU box to the fleet — ingests
nothing, so its daemon embeds nothing. The 3.29 M-chunk backlog sits
there while the new host idles, exactly the waste fleet-2 set out to
kill.

The only way to drain backlog today is a foreground
`corpus-forge embed -e <name>` / `--all`, or the ad-hoc detached
embed-worker spawned by the drift-prompt path
(`cli.py:1056 _detach_reembed_worker`, pid file
`state/embed-worker.pid`). Neither is supervised, neither survives a
reboot, and neither is what `service install` gives you. So the
operator's mental model ("install the service, walk away, the backlog
drains") is broken on precisely the host where it matters most.

fleet-2 gives us the safe primitive — `claim_chunks_for_embedding`
with `FOR UPDATE SKIP LOCKED` + lease expiry — so N hosts can drain one
lane with zero duplicate compute. This RFC is the missing wiring: the
**managed service** runs a continuous claim-based drain loop, honoring
`[embed] lanes`, alongside (or instead of) the ingest watcher.

## Goals

- After `service install` on a joined host, the daemon **continuously
  drains the embed backlog** via fleet-2 claims — no foreground
  command, no operator babysitting, survives reboot.
- The drain loop honors `[embed] lanes` (a CUDA box pinned to
  `qwen3-4096` drains only that lane) and `[embed] claim_lease_ttl`.
- A drain-only host (joined, ingests nothing) is a first-class mode:
  the service can run the embed drain **without** an active ingest
  watcher, configured via one obvious knob.
- Idle behavior is cheap and correct: when the claim call returns an
  empty batch (backlog drained, or every remaining chunk live-claimed
  by peers), the loop backs off instead of hot-spinning, and resumes
  when new work appears.
- `service status` / `doctor` truthfully report the drain loop:
  running/idle/backing-off, current lane, chunks/s, last claim time.

## Non-goals

- **No new scheduler.** This is fleet-2's greedy claim loop hosted
  inside the existing daemon supervisor — not a cross-host rebalancer.
- **No SQLite federation.** Drain-loop claims inherit fleet-2's
  `FederationUnsupported` on SQLite; on a single-machine SQLite setup
  the daemon keeps today's ingest-time embedding behavior unchanged.
- **No change to the ingest-time embed path.** New chunks still get
  embedded as they're ingested on hosts that ingest; the drain loop is
  *additive* and complementary (it mops up the standing backlog and
  anything ingest-time embedding skipped or deferred).
- No deprecation of foreground `embed -e <name>` — it stays the manual
  override (and, per fleet-2, an explicit `-e` ignores lane pinning).

## Approach

### Drain loop hosted in the daemon

Add an embed-drain coroutine/thread to the daemon's lifecycle
(`daemon.py`), started after embedder warm-up. Each iteration:

1. `expire_stale_claims` (cheap; also done inside the claim call).
2. For each lane this host owns (`[embed] lanes`, else all active
   embedders): `claim_chunks_for_embedding(embedder_id, host_id,
   batch, lease_ttl)`.
3. Embed the claimed batch, write embeddings, `release_claims`.
4. Record the observed rate (fleet-1 passive telemetry,
   `source="embed-run"`).
5. If **every** lane returned an empty batch, sleep with bounded
   exponential backoff (`[embed] drain_idle_min`..`drain_idle_max`,
   defaults ~5 s → ~5 min); any non-empty batch resets backoff to min.

Reuses fleet-2's claim/release/expire methods verbatim — this loop is
just a long-lived caller of them inside the supervised process.

### Drain-only mode (the join case)

New local-config knob under `[service]` (final name TBD in
implementation; sketch):

```toml
[service]
embed_drain = true        # run the backlog drain loop in the daemon
ingest_watch = true       # run the source watcher (default on)
# a pure-drain GPU box sets ingest_watch = false
```

- `setup --join` (fleet-3) seeds `embed_drain = true` by default for a
  joined host, and — when the accelerator probe finds a capable GPU —
  offers `ingest_watch = false` (pure drain box). `CF_NON_INTERACTIVE`
  honors `--embed-drain/--no-embed-drain` and
  `--ingest-watch/--no-ingest-watch`.
- A plain local (non-fleet) setup is unchanged: `embed_drain` defaults
  to off there, because ingest-time embedding already covers a
  single-machine corpus and we don't want a surprise background GPU
  loop on a laptop. (Backcompat bar mirrors fleet-3's: local-only
  setups behave exactly as before.)

### Coexistence with the drift-prompt worker

The ad-hoc detached re-embed worker (`cli.py:_detach_reembed_worker`,
`state/embed-worker.pid`) and the daemon drain loop must not double up
on the same lane. With fleet-2 claims in place they're *safe* (claims
dedupe), but we still want truthful reporting and no two supervisors
fighting. Resolution: the daemon drain loop is the durable owner; the
detached worker becomes a no-op (logs "drain loop active in service")
when the managed service is running and owns that lane. `service
status` shows one drain owner, not two.

### Status / doctor surface

- `service status` (`admin/service.py:render_status`) gains a
  **drain** row: state (`running`/`idle-backoff`/`disabled`), lanes,
  last-claim age, recent chunks/s.
- `doctor` gains a drain sanity check: on a host where `embed_drain` is
  on but the service isn't installed/running, WARN with the
  `service install` / `service start` fix; on a multi-host SQLite
  corpus, the fleet-2 `FederationUnsupported` WARN already fires.

### Docs

Update CLAUDE.md §"Add a second machine": `service install` now
*actually* drains; document `embed_drain` / `ingest_watch` and the
pure-drain GPU-box recipe. Update the troubleshooting table with the
"joined a fleet, backlog isn't draining" → "is the drain loop on? is
the service running? `service status`" row.

**Coverage note:** ≥ 89 % line coverage (current `make test-unit`
floor — see Makefile) on all new code is part of "done."

## Tasks

- [x] Daemon embed-drain loop: per-lane fleet-2 claim → embed →
      release, bounded exponential backoff on empty, passive rate
      telemetry per batch. — DONE on main: `daemon.py`
      `run_embed_drain_loop` / `_drain_lane_batch` / `_build_drain_lanes`
      (~:235-469), `idle_min`/`idle_max` backoff, `_write_embed_run_telemetry`.
      (Checkbox reconciled 2026-06-13 after the cascade re-surfaced this
      already-implemented item.)
- [x] `[service] embed_drain` / `ingest_watch` config (+ defaults:
      off/on locally, on/on for a joined host); daemon lifecycle wires
      drain loop and makes the ingest watcher optional. — DONE: config
      (`config.py` `ServiceConfig`) + daemon lifecycle wiring
      (`daemon.py` ~:519-614, off/on locally) were already on main; the
      **on/on default for a joined host** lands with task 3 below.
- [x] `setup --join` seeds `embed_drain=true`; GPU-probe offers
      `ingest_watch=false`; `CF_NON_INTERACTIVE` flags
      `--embed-drain` / `--ingest-watch` and their negations. — DONE
      (branch fleet5-join-seed-drain): `setup` gains
      `--embed-drain/--no-embed-drain` + `--ingest-watch/--no-ingest-watch`
      (env `CF_EMBED_DRAIN` / `CF_INGEST_WATCH`); `run_join` resolves them
      (embed_drain defaults true for a joined host; ingest_watch defaults
      off on a capable GPU via `_join_default_ingest_watch`, else on;
      explicit flags win) and `_render_skeleton_join_config` writes the
      `[service]` block. Plain local setup unchanged. Tests:
      `tests/unit/test_setup_join_service_drain.py`.
- [ ] Detached drift-prompt worker yields to the managed drain loop
      when the service owns the lane (no double-embedding, one status
      owner).
- [ ] `service status` drain row (state / lanes / last-claim age /
      chunks-per-s); `--json` carries the same fields.
- [ ] `doctor` drain check: `embed_drain` on but service not
      running → WARN with fix.
- [ ] Integration test (testcontainers Postgres): a drain-only daemon
      (ingest watcher off) drains a pre-seeded backlog to zero;
      concurrent with a second drain daemon → disjoint work, no chunk
      embedded twice (rides fleet-2's claim guarantees).
- [x] Idle test: empty backlog → loop backs off, does not hot-spin
      (assert claim-call frequency under backoff); new work resets to
      min interval. — DONE: `tests/unit/test_embed_drain_loop.py`
      (`test_backoff_capped_at_max`, `test_does_not_hot_spin`,
      `test_work_resets_backoff_to_min`).
- [x] CLAUDE.md "Add a second machine" + troubleshooting updates. — DONE
      (PR #148): documents the `[service] embed_drain`/`ingest_watch`
      block, the `--embed-drain`/`--ingest-watch` flags + `CF_*` env, the
      pure-drain GPU-box recipe, and the local-unchanged backcompat; plus
      a "joined a fleet but the backlog isn't draining" troubleshooting
      row pointing at `service status`.

## Verification

- **Acceptance bar:** on a real joined host with no local sources, a
  freshly `service install`ed daemon drains a standing backlog to zero
  with no foreground command — `service status` shows the drain row
  ticking, `count_chunks_missing_embedding` trends to 0.
- Duplicate-work probe (fleet-2's): after a two-host concurrent drain
  (one via service, one foreground), `select chunk_id, count(*) …
  group by 1 having count(*) > 1` returns zero rows.
- Backoff probe: with an empty backlog, the daemon issues claim calls
  at the configured idle cadence, not in a tight loop (log-rate or
  metric assertion).
- Local-only regression: a single-machine SQLite/Postgres setup with
  `embed_drain` defaulted off behaves byte-for-byte as before.
- `make test-unit` ≥ floor coverage on the drain loop + config.

## References

- `corpus_forge/daemon.py` — ingest-time embedding (`:138-148`), the
  lifecycle this loop joins; drift WARNING (`:394 _log_embedder_drift_warning`).
- `corpus_forge/admin/service.py` — `render_status` (drain row),
  `service_install_cmd`, foreground/background supervision.
- `corpus_forge/cli.py` — `_detach_reembed_worker` (`:1056`),
  `_describe_embed_worker` (`:1218`), `state/embed-worker.pid`.
- `.planning/rfcs/rfc-fleet-2-distributed-embedding.md` — the claim
  primitive + `[embed] lanes` this loop calls.
- `.planning/rfcs/rfc-fleet-3-federated-config-and-setup.md` —
  `setup --join` onboarding this hooks into.
- `.planning/rfcs/rfc-fleet-6-model-identity-aliases.md` — so a joined
  host's differently-named-but-same model claims the *same* lane (else
  the drain loop drains into a phantom second embedder).
- CLAUDE.md §"Add a second machine (one-command fleet onboarding)".
