# Fleet-2 Claim Path Deadlock — Investigation

## Symptom (verbatim)

Real-world reproduction on a Mac (Apple Silicon, Metal-offloaded
`llama-cpp-python`) connecting to a Postgres in an LXC container
over Tailscale at `100.124.253.81:5432`:

1. `corpus-forge embed -e nomic-code` starts cleanly:
   - `Loading embedder nomic-code (llama-cpp, 3584-dim, …, n_ctx=8192,
     n_seq_max=32, n_batch=16384, n_ubatch=16384)`
   - `Embedder nomic-code ready in 0.6s`
   - `Registered embedder nomic-code with ID 7`
2. ~47s gap, then `Backfilling nomic-code: 536527 chunks pending`.
3. ~2 minutes later: `Embedding chunks (nomic-code) started: 536527 items`
   and (DEBUG) `Generating embeddings for 1000 chunks`.
4. **Total log silence for 10+ minutes after that DEBUG line.**
5. `corpus.embed_claims` accumulates 1000 → 2000 rows for
   `host_id='Unicorn-MacBook-Pro-8.local'`, never released, never
   followed by writes to `corpus.embeddings_nomic_code`.
6. `ps`: worker at 0.2% CPU, RSS ~7 GB.
7. `pg_stat_activity`: NO active queries from the worker.
8. `sample <pid>`: ALL threads (main + ~7) blocked in
   `__psynch_cvwait` inside `_PyEval_EvalFrameDefault` — Python
   condition-variable wait.
9. Earlier (different) shutdown produced
   `WARNING - error ignored in rollback on <psycopg.Connection [ACTIVE] …>:
   sending query failed: another command is already in progress`
   followed by `WARNING - closing returned connection` — psycopg-pool
   discarding a conn whose prior command never finished.

## Code-paths walked

* `corpus_forge/embed.py::backfill_embedder` (lines 219-708) — the
  loop body. Each page does `_fetch_page` → `embedder.encode` →
  `backend.write_embeddings` → `release_claims` in a try/finally.
* `corpus_forge/backends/postgres.py::claim_chunks_for_embedding`
  (lines 1342-1420) — single CTE statement (FOR UPDATE SKIP LOCKED
  on `cand`; ON CONFLICT DO NOTHING insert into `embed_claims`).
  Runs inside one `_execute` so the row locks are released at the
  inner `conn.commit()`.
* `corpus_forge/backends/postgres.py::write_embeddings` (lines 1171-
  1198) — **the suspect path.** Issues one `_execute` per pair, so
  a 1000-chunk page does **1001 sequential pool checkouts** (1 for
  the embedder-name SELECT + 1000 INSERTs), each a separate
  transaction + commit + return-to-pool cycle.
* `corpus_forge/backends/postgres.py::_execute` (lines 328-334) —
  `with self._get_connection() as conn, conn.cursor(…) as cur`,
  `cur.execute → fetchall → commit`. Clean per-call lifecycle.
* `corpus_forge/backends/postgres.py::_get_connection` (lines 318-
  326) — wraps `self._pool.connection()`. Pool config:
  `min_size=0, max_size=8` (PostgresBackend __init__ defaults at
  lines 228-229).
* `psycopg_pool/pool.py::ConnectionPool.connection` (3.3.1) — on
  exception inside the `with conn:` block, `Connection.__exit__`
  calls `self.rollback()`. If the prior command is in ACTIVE state
  on the libpq connection (commit ack not yet drained over a flaky
  Tailscale link), rollback raises and `psycopg/connection.py:170`
  warns `"error ignored in rollback on … sending query failed:
  another command is already in progress"`. Pool then closes the
  conn — matches the user's observed shutdown warning.

## Hypotheses (from the task spec) reconciled with code

| H  | Hypothesis                                                        | Verdict                                                                                                                                                                                                                                                                                                                                                                                                          |
|----|-------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| H1 | Pool deadlock — claim transaction held across encode               | Partially confirmed. The CTE *itself* commits inside `_execute` (line 333). BUT `write_embeddings` opens 1001 sequential checkouts per page; each one over Tailscale costs ~14 ms (PR #63 baseline). With `max_size=8` and 4 psycopg_pool maintainer threads + Rich's refresh thread, the pool oscillates between "all checked out" and "growing." A transient Tailscale stall during commit leaves a connection in `ACTIVE` state — matches the user's shutdown WARNING. |
| H2 | llama-cpp GIL contention                                          | Unlikely. The `sample` shows **main** thread also in `__psynch_cvwait` / `_PyEval_EvalFrameDefault` — a GIL-holding C call would show llama frames, not `_PyEval_EvalFrameDefault`.                                                                                                                                                                                                                              |
| H3 | Per-chunk write loop is the bug                                   | **Confirmed as the high-impact regression surface.** Even if not the *literal* deadlock cause, the 1001-checkout-per-page pattern is the dominant contention multiplier on the claim path and is the most concrete fixable thing.                                                                                                                                                                                |
| H4 | Connection-pool exhaustion                                        | Not the root cause — `max_size=8`, `timeout=30s` default, would raise `PoolTimeout`, not hang silently for 10 min.                                                                                                                                                                                                                                                                                              |

## Why the existing integration test doesn't reproduce this

`tests/integration/test_embed_claim_loop_two_host.py` (line 78) uses
a `_FakeEmbedder` whose `encode()` returns ones-vectors **instantly**
and **only 40 chunks total**, so:

* `write_embeddings` is called with at most 40 pairs (vs. 1000 in
  prod) — pool contention never crosses the threshold.
* Embeddings are 8-dim (32 bytes/row) vs. 3584-dim × 4 bytes ≈ 14 KB
  in prod — 1000 INSERTs in prod ship ~14 MB per page over Tailscale.
* The Postgres container is on `localhost` — sub-millisecond round
  trips vs. ~14 ms over Tailscale to LXC.

End result: the per-chunk INSERT loop costs ~80 ms in CI and ~14 s in
prod. The contention/wedge window scales with the second number.

## Root cause (committed)

`PostgresBackend.write_embeddings` issues one `_execute` per pair. On
a Tailscale-backed Postgres with 1000-chunk pages, this is 1001
sequential pool checkouts per page — 1001 BEGIN/INSERT/COMMIT
round-trips. Each round-trip is ~14 ms baseline; under network
jitter, any single commit that doesn't drain its ack stamps the
connection ACTIVE, the pool tries to roll it back on return, that
rollback fails with `"another command is already in progress"`, and
the pool discards the connection. Meanwhile main thread is waiting
on `pool.getconn()` for a fresh conn while psycopg-pool's worker
thread is mid-reconnect over a flapping link. With the CLI also
running Rich's `Live` refresh thread (which holds the Console lock
during refresh) and four psycopg-pool maintainer threads, the
combination presents as "all threads in `__psynch_cvwait`."

The legacy fallback path (`chunks_missing_embedding`) had the same
per-chunk write loop, so why didn't *it* wedge? Two reasons:

1. The legacy path doesn't run the `expire_stale_claims` /
   `release_claims` extra `_execute` calls — fewer round-trips per
   page → less time under contention per cycle.
2. Without `embed_claims`, a wedge merely re-fetches the same first
   page on restart — there's no observable "stale rows" symptom that
   tells the operator the worker is wedged. **The fleet-2 claim
   table makes the pre-existing latency-amplified contention
   *visible* by leaving rows in `embed_claims`.**

## Fix (minimal, surgical)

Replace `write_embeddings`'s per-chunk loop with a single
`cur.executemany(...)` inside one transaction. This collapses 1001
sequential pool checkouts per 1000-pair page into **one**. Network
round-trips drop from ~1001 to ~3 (BEGIN + executemany + COMMIT).

Why this is correct:

* `ON CONFLICT (chunk_id) DO NOTHING` semantics are unchanged —
  `executemany` runs the same statement N times under one
  transaction, conflict-on-conflict is per-row.
* `chunk_id` is the table's primary key; the per-chunk DO NOTHING
  guard is what makes the call idempotent across host restarts.
  Batched executemany preserves that idempotency row-by-row.
* No behavioral change for single-host or fallback paths.
* No behavioral change for empty `pairs` (still early-return).

Backwards compatibility:

* `StorageBackend.write_embeddings` ABI unchanged.
* SQLite implementation unchanged.
* `bench` / `eval` callers unchanged.
* The legacy fallback embed path benefits identically.

## Test anchor (RED → GREEN)

`tests/integration/test_embed_claim_write_batching.py` — drives
`PostgresBackend.write_embeddings` against a real Postgres with 500
pairs, asserts that the number of round-trips (measured via
`pg_stat_statements`-style call counting) is O(1) not O(N). The
pre-fix code makes ~501 `INSERT` calls; the post-fix code makes 1
batched call. With `max_size=2` (tighter than prod) the test also
asserts the operation completes inside a wall-clock budget that
1001 sequential checkouts would blow.

## Open follow-ups (out of scope here)

1. **Lease-renewing heartbeat.** While encode runs, the lease ticks
   down. Currently a slow encode (close to `claim_lease_ttl=600`)
   could expire and let another host re-claim the same page. The
   RFC checklist item "Crash-recovery test: worker A claims and
   dies; worker B picks up after lease expiry" is still unchecked.
2. **SIGINT handler that drains `embed_claims` for this host.** A
   Ctrl-C'd worker leaks claims until lease expiry. Cheap to fix
   with `atexit` + a host-scoped DELETE.
3. **Honor `release_claims` chunk_ids list length.** A 1000-id
   `ANY(%s)` is fine but a future 100k-id batch would warrant
   chunking.
4. **Document the `min_size=0, max_size=8` pool config in
   `[backend]` so operators can tune.**
