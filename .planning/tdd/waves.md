# Execution Waves — Active Directory Sync

Each wave is a batch of tasks safe to dispatch in parallel (disjoint surface, all dependencies satisfied). A wave must finish (all tasks `done`, QA approved) before the next wave starts.

Conflict-detection rule applied: two tasks share a wave only if their `surface` file lists are disjoint. Where two tasks both touch `corpus_forge/backends/postgres.py` they are serialized across waves; where they both touch `corpus_forge/sync/<distinct file>.py` they parallelize.

## Wave 0 — Foundation (10 tasks, fully parallel)

| id | surface | why this wave |
|----|---------|---------------|
| P0-01 | `identity.py` | Pure helper, no deps. |
| P0-02 | `schema/002_chunk_content_hash.sql` (new file) | DDL only, no code touch. |
| P1-01 | `schema/003_sync.sql` (new file) | DDL only; numerically ordered after 002 but does not require its application. |
| P1-03 | `config.py` | Independent pydantic additions; existing tests must stay green. |
| P1-05 | `config.example.toml` | Doc-shaped; no test code. |
| P1-06 | `sync/echo.py` (new) + `sync/__init__.py` (new) | New module, no upstream deps. |
| P1-07 | `sync/cloud.py` (new) | New module, pure. |
| P1-09 | `sync/conflicts.py` (new, naming portion) | Pure path-string function; `is_cloud_duplicate` (P1-08) deferred to Wave 1 because it consumes the cloud-provider taxonomy from P1-07. |
| P1-10 | `sync/fs.py` (new, atomic_write_text only) | Pure FS primitive. |
| P1-12 | `sync/fs.py` (placeholder/dataless guards) | **Same file as P1-10** — *moved to Wave 1* to avoid concurrent edits. |

**Correction:** P1-10 and P1-12 both touch `sync/fs.py`; they cannot run in the same wave. P1-12 moves to Wave 1.

### Wave 0 (corrected)

P0-01, P0-02, P1-01, P1-03, P1-05, P1-06, P1-07, P1-09, P1-10.

## Wave 1 — Migrations applied + cloud + config polish

| id | depends on | surface | why now |
|----|------------|---------|---------|
| P0-03 | P0-01, P0-02 | `schema/migrate.py`, `tests/integration/test_migrate_002.py` | Backfill needs the hash helper + the migration file present. |
| P0-04 | P0-01, P0-02 | `backends/postgres.py` (chunk insert) | Schema column must exist; uses helper. |
| P1-02 | P1-01 | `schema/migrate.py` (idempotency check) | **Same file as P0-03** — serialized to Wave 2. |
| P1-04 | P1-03 | `config.py` (host_id resolution) | **Same file as P1-03** — but P1-03 finished in Wave 0. Safe here. |
| P1-08 | P1-07 | `sync/conflicts.py` | **Same file as P1-09** — P1-09 finished Wave 0. Safe here. |
| P1-11 | P1-03 (trash_dir) | `sync/fs.py` (move_to_trash) | **Same file as P1-10** — P1-10 finished Wave 0. Safe here. |
| P1-12 | — | `sync/fs.py` (placeholder guards) | **Conflicts with P1-11** in this wave. *Moved to Wave 2*. |

### Wave 1 (corrected)

P0-03, P0-04, P1-04, P1-08, P1-11.

## Wave 2 — Migrations idempotency + fs guards + reuse helper + revision API surface

| id | depends on | surface | why now |
|----|------------|---------|---------|
| P1-02 | P1-01, (sequential after P0-03) | `schema/migrate.py` | Touches same file as P0-03 — must serialize. |
| P1-12 | P1-10 (file existed), now safe | `sync/fs.py` | Sequential after P1-11. |
| P0-05 | P0-04 | `backends/postgres.py` (new helper, separate region) | **Touches same file as P0-04** — sequential. |
| P1-13 | P1-02 | `backends/postgres.py` (new method) | **Same file as P0-05** — *split into Wave 3*. |
| P1-14 | P1-02 | `backends/postgres.py` | Same conflict — *Wave 3*. |
| P1-15 | P1-02 | `backends/postgres.py` | *Wave 3*. |
| P1-16 | P1-02 | `backends/postgres.py` | *Wave 3*. |
| P1-17 | P1-02 | `backends/postgres.py` | *Wave 3*. |

### Wave 2 (corrected)

P1-02, P1-12, P0-05.

(Each touches a distinct file: `schema/migrate.py`, `sync/fs.py`, `backends/postgres.py`.)

## Wave 3 — Backend revision API (serialized on `postgres.py`)

`backends/postgres.py` has only one writer at a time; we batch the revision API as one task surface in `tasks.md` but list them as a single coherent block here. The principal will dispatch P1-13 through P1-17 sequentially, *not* in parallel — because they all touch the same file.

| id | order | rationale |
|----|-------|-----------|
| P1-13 | first | Allocates revision under lock; foundational for the others. |
| P1-14 | second | Read-side helper. |
| P1-15 | third | Read-side helper, depends on P1-14 logic shape. |
| P1-16 | fourth | Tracks pull-side pointer. |
| P1-17 | fifth | Tombstone state mutation. |

**Wave 3 is intentionally serial.** The principal MAY dispatch them as one tester+coder cycle if the worker can keep the change small — alternative: split into 5 micro-tasks each ≤30 LOC. Recommended grouping: **one** TDD cycle covering all five revision-related methods, since they share fixtures and test surface. Mark this in tasks.md notes if collapsed.

## Wave 4 — `upsert_document(embedder_ids=...)`

P0-06 only. Sequential because it touches `backends/postgres.py` and `backends/base.py` after Wave 3 settles.

## Wave 5 — `ingest_one` reuse wiring

P0-07 only. Touches `corpus_forge/ingest.py`. (Could parallelize with anything not on `ingest.py`; no other Wave-5 candidates exist.)

## Wave 6 — P0 E2E gate

P0-08 only. **Hard gate.** No P1 push/pull task starts until this passes — that is the explicit P0-then-P1 ordering from the plan.

## Wave 7 — Push/Pull primary handlers (parallel)

| id | surface |
|----|---------|
| P1-18 | `sync/push.py` (handler core) |
| P1-22 | `sync/pull.py` (tick + fast-forward) |

Two distinct new files, fully parallel.

## Wave 8 — Push observer + pull branches (parallel)

| id | surface |
|----|---------|
| P1-19 | `sync/push.py` (observer wiring) — sequential after P1-18 (same file) |
| P1-23 | `sync/pull.py` (already-in-sync) — sequential after P1-22 |
| P1-24 | `sync/pull.py` (conflict) — sequential after P1-22 |
| P1-25 | `sync/pull.py` (tombstone) — sequential after P1-22 |

`push.py` and `pull.py` proceed in their own lanes:
- Lane A (push): P1-19 standalone in this wave.
- Lane B (pull): P1-23, P1-24, P1-25 are all *same file*; serialize as a single TDD micro-cycle if the worker can keep it ≤200 LOC, otherwise split across micro-waves 8a / 8b / 8c.

The principal may dispatch P1-19 and the (P1-23 || P1-24 || P1-25) chain in parallel — one tester per file at a time.

## Wave 9 — Push extras + pull lifecycle (parallel)

| id | surface |
|----|---------|
| P1-20 | `sync/push.py` (cloud-duplicate cleanup) |
| P1-21 | `sync/push.py` (tombstone-on-delete) |
| P1-26 | `sync/pull.py` (poll-loop / lifecycle) |

P1-20 and P1-21 serialize on `push.py`. P1-26 parallel.

## Wave 10 — Engine

P1-27 only. New file `sync/engine.py`. Could parallelize with nothing else productive at this stage.

## Wave 11 — Daemon + CLI (parallel)

| id | surface |
|----|---------|
| P1-28 | `corpus_forge/daemon.py` |
| P1-29 | `corpus_forge/cli.py` |

Disjoint files; fully parallel.

## Wave 12 — Integration smoke (parallel)

| id | surface |
|----|---------|
| P1-30 | `tests/integration/test_sync_push_pull.py` (new file) |
| P1-31 | `tests/integration/test_sync_tombstone.py` (new file) |
| P1-32 | `tests/integration/test_sync_icloud_dupe.py` (new file) |

Three new test files, fully parallel.

## Hard ordering constraints (across waves)

1. **P0 ordering:** Migration (P0-02, P0-03) must precede any code that writes `chunks.content_hash` (P0-04). The reuse helper (P0-05) must precede `upsert_document(embedder_ids)` (P0-06), which must precede the ingest wire-up (P0-07), which must precede the E2E reuse test (P0-08).
2. **P0 → P1 gate:** The plan is explicit ("P1 builds on P0"). P0-08 is the gate. No P1 push/pull task starts before P0-08 is approved.
3. **Schema before backend method:** P1-02 must apply before any of P1-13..P1-17 run integration tests.
4. **Revision API before push/pull:** P1-13..P1-17 are required before P1-18 and P1-22.
5. **Engine after both halves:** P1-27 (engine) requires both P1-19 (push observer up) and P1-26 (pull lifecycle up).
6. **Daemon after engine:** P1-28 wires `SyncEngine` into `daemon.py`.
7. **CLI does not depend on engine** but does depend on revision API (P1-13..P1-17) for `sync history` / `sync status`.
8. **Integration smoke last:** P1-30..P1-32 require everything.

## Parallelism summary

| Wave | Parallel tasks |
|------|----------------|
| 0 | 9 (P0-01, P0-02, P1-01, P1-03, P1-05, P1-06, P1-07, P1-09, P1-10) |
| 1 | 5 (P0-03, P0-04, P1-04, P1-08, P1-11) |
| 2 | 3 (P1-02, P1-12, P0-05) |
| 3 | 1 (P1-13..P1-17 collapsed; principal dispatches as one cycle or splits manually) |
| 4 | 1 (P0-06) |
| 5 | 1 (P0-07) |
| 6 | 1 (P0-08 — gate) |
| 7 | 2 (P1-18, P1-22) |
| 8 | up to 2 lanes (P1-19; P1-23..P1-25 chain) |
| 9 | up to 2 (P1-20→P1-21 chain on push; P1-26 on pull) |
| 10 | 1 (P1-27) |
| 11 | 2 (P1-28, P1-29) |
| 12 | 3 (P1-30, P1-31, P1-32) |

Maximum concurrent dispatch: 9 (Wave 0). Typical wave size: 2–3.
