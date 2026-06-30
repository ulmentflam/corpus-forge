# RFC: Code-intel 1/2 — incremental Merkle diff-sync for scans

status: accepted
**Owner**: nightly (open for any agent to claim)
**Priority**: P0 — operator-requested 2026-06-30, top of queue
**Depends on**: — (foundational; `codeintel-2` depends on this)

## Context

Today every scan re-walks the **entire** source tree and re-reads /
re-hashes **every** file to decide what changed. Cost is O(all files),
not O(changed files), which is the dominant wall-clock tax on a large
corpus and the thing the operator wants gone.

Where the cost lives today:

- `corpus_forge/sources/filesystem.py::FilesystemSource.discover()`
  (~lines 179–243) is **stateless** — it composes the `IgnoreStack`
  and re-runs `scanner.walker.walk()` over the whole root on every
  call. No directory or file fingerprint is persisted between runs.
- `corpus_forge/scanner/walker.py::walk()` (lines 133–223) yields
  `WalkEntry(path, stat, is_dir)` but **computes no content hash** and
  keeps no memory of prior scans. It already prunes ignored
  directories at descent time (lines 155–160) — the exact hook a
  Merkle prune extends.
- `corpus_forge/ingest.py::ingest_once()` (~lines 510–514) detects
  change per-document by reading the file, extracting, hashing, then
  comparing `RawDocument.content_hash` against `backend.get_hash()`.
  The file is fully read + extracted *before* we can know it was
  unchanged.
- Deletions are **not** cleanly detected on a full re-scan — there is
  no "seen-set minus this-run" bookkeeping, even though the sink
  already supports tombstones.

What we already have (the cache half is done):

- `documents.content_hash` and `chunks.content_hash` (migration
  `0002_chunk_content_hash`) mean re-ingest already preserves
  `chunk_id` + the embedding row when a chunk's text is unchanged
  (the BUG-3 fix). Embeddings are effectively content-addressed.
- `document_revisions` (migration `0004_sync`) is an append-only chain
  with `parent_revision_id`, `is_tombstone`, and `author_host` — a
  per-document version history already exists.
- `sync/push.py::PushPipeline` already does the cheap-check-first
  pattern (mtime pre-filter → content hash) for the watchdog path
  (lines 205–341).

The missing half is **scan-time skip**: a persisted Merkle fingerprint
of the tree so an unchanged subtree is pruned without reading a single
file in it. This mirrors Cursor's secure-indexing design (SHA-256 per
file, directory hash derived from children, walk only the branches
whose hashes diverge — `cursor.com/blog/secure-codebase-indexing`).

## Goals

- Re-scan cost is **O(changed paths)**, not O(all files): an unchanged
  directory subtree is skipped without `os.scandir`-ing into it or
  reading any file, by comparing a stored `subtree_hash`.
- A per-(dataset, root) **Merkle root**: when it matches the last
  scan, the whole tree is provably unchanged → near-zero-cost sync.
- **Deletions are detected for free** — anything in the manifest not
  seen this run is tombstoned via the existing `is_tombstone` path.
- **mtime/size pre-check before hashing** — a file is only re-hashed
  when its `(size, mtime_ns)` differ from the manifest, matching the
  PushPipeline pattern. Content hash stays the source of truth (mtime
  is only a hint; a mtime-bump with identical content re-confirms the
  hash and does no downstream work).
- **Single-machine behavior unchanged in effect**; first scan after
  upgrade transparently backfills the manifest (cold-cache = today's
  full walk, exactly once).
- Correctness over cleverness: a corrupt / missing manifest degrades
  to a full re-scan, never to a missed change.

## Non-goals

- **No remote content-proof / path-obfuscation / shared-index trust
  model.** Cursor's "server can't read your source" machinery solves
  an untrusted-remote-server threat; corpus-forge is local-first /
  self-hosted Postgres the operator owns. Out of scope here (parked
  for a future cross-trust-boundary shared index — see References).
- **No sub-file byte-diffing of document text.** Granularity is
  file → chunk (we already re-chunk a changed file and keep unchanged
  chunks by `content_hash`). True intra-file delta patching is not in
  scope.
- **No change to the chunker, embedder, or retrieval.** This is a
  scan/discover-layer optimization; downstream stages are untouched.
- **No watchdog rewrite.** `PushPipeline` keeps its event path; it may
  *read* the manifest as a warm cache but its design is unchanged.

## Approach

### Manifest table (next alembic revision after `0019`)

```
corpus.fs_manifest
  id            bigserial primary key
  dataset_id    bigint not null references corpus.datasets(id) on delete cascade
  path          text   not null          -- absolute, normalized
  parent_dir    text   not null          -- absolute path of containing dir
  kind          text   not null          -- 'file' | 'dir'
  size          bigint                    -- null for dirs
  mtime_ns      bigint                    -- file: stat mtime; dir: max(child)
  content_hash  text                      -- file: sha256(bytes); dir: null
  subtree_hash  text                      -- dir: merkle of children; file: = content_hash
  last_seen_run bigint not null references corpus.ingest_runs(run_id)
  unique (dataset_id, path)
  index (dataset_id, parent_dir)          -- child lookup during prune
  index (dataset_id, last_seen_run)       -- deletion sweep
```

`subtree_hash(dir)` = SHA-256 over the sorted list of
`(child_name, child.subtree_hash)` pairs (Cursor's "directory hash
derives from its children's hashes"). `subtree_hash(file)` =
`content_hash`. The dataset-root row's `subtree_hash` **is** the
Merkle root.

### Merkle-pruned walk

Add a `verify=` / manifest-aware mode to `scanner.walker.walk()` (or a
thin wrapper `walk_incremental()` so the hot path stays clean):

1. Load the prior manifest for the dataset into an in-memory dict
   keyed by `path` (one indexed query).
2. Descend as today, but **before recursing into a directory**, cheap-
   check whether it can be pruned: if every immediate child's
   `(size, mtime_ns)` matches the manifest, the subtree is *candidate-
   unchanged*. Confirm by recomputing the dir's `subtree_hash` from the
   manifest's child hashes (no file reads) and comparing to the stored
   `subtree_hash`. Match → **skip the whole subtree**, reusing the
   existing descent-time prune hook (walker.py:155–160).
3. For a file whose `(size, mtime_ns)` matches → reuse the stored
   `content_hash`, no read. Mismatch → read + hash, emit a changed
   `WalkEntry` carrying the fresh hash.
4. Bottom-up, recompute `subtree_hash` for every touched directory and
   the root.

### Wiring into ingest

- `ingest_once()` consumes the walk's per-file `content_hash` directly,
  dropping the read-then-compare round-trip for unchanged files
  (the `backend.get_hash()` compare becomes a fast-path confirmation).
- **Deletion sweep**: after the walk, any `fs_manifest` row for the
  dataset with `last_seen_run < this_run` (and not re-seen) is a
  delete → insert a tombstone revision (`is_tombstone=True`,
  `author_host=this host`) via the existing `document_revisions` path,
  and drop the manifest row.
- The manifest write is part of the run's transaction so a crashed run
  doesn't half-update it; `resume` semantics mirror `ingest_runs`.

### Fleet & doctor touch points

- **Per-dataset Merkle root surfaced** so a host can publish its root
  and peers compare before doing work (cheap fleet-wide "are we in
  sync?" probe). Read-only here; federation of the manifest itself is
  a follow-up.
- `corpus-forge doctor`: an `fs_manifest` row — reports manifest
  coverage (datasets with vs without a manifest), stale-root drift,
  and a WARN if the manifest is newer than the schema (downgrade
  guard).
- `corpus-forge estimate` reuses the manifest when present to report
  *incremental* projected cost ("12 changed files / 480 MB" vs the
  full-tree number).

### Stretch — fleet index reuse via simhash

Cursor's index-reuse trick (a new client computes a codebase simhash
and clones a matching existing index instead of re-embedding) maps onto
`setup --join`: a joining host computes its tree simhash; if it matches
an existing host's published root within a threshold, it can clone the
embedding set rather than draining the whole `embed_claims` backlog.
Print-only recommendation in this RFC; actual clone path is a follow-up.

**Coverage note:** ≥ 90 % line coverage on all new code is part of
"done" (`make test-unit` gate).

## Tasks

- [ ] Alembic revision: `corpus.fs_manifest` (schema above) + indexes;
      idempotent re-run test; SQLite + Postgres parity.
- [ ] `subtree_hash` helper + property tests: order-independence of
      child set, file==content_hash, empty-dir hash stable, rename
      detected as delete+add.
- [ ] `walk_incremental()` (or `walk(verify=…)`): mtime/size pre-check,
      manifest-confirmed subtree prune via the descent hook, fresh-hash
      on mismatch; falls back to full walk on missing/corrupt manifest.
- [ ] `ingest_once()` consumes per-file `content_hash`; drops the
      read-then-`get_hash` round-trip for unchanged files; manifest
      written in the run transaction.
- [ ] Deletion sweep: `last_seen_run`-based tombstone insertion through
      `document_revisions`; manifest row pruned.
- [ ] Per-dataset Merkle root accessor + `doctor` `fs_manifest` check
      (coverage, drift, downgrade-guard WARN).
- [ ] `estimate` incremental mode (changed-only projection when a
      manifest exists).
- [ ] Perf assertion (integration): on a fixture tree of N files with
      K changed, re-scan reads ≤ K files + ancestors and prunes the
      rest; cold-cache run equals today's full walk.
- [ ] Crash-safety test: run dies mid-walk → manifest unchanged →
      next run still detects all real changes (no missed delta).
- [ ] (Stretch) simhash root + `setup --join` print-only index-reuse
      recommendation.

## Verification

- **Prune correctness**: golden fixture tree; mutate one leaf; assert
  exactly the changed file + its ancestor dirs are re-hashed and every
  sibling subtree is skipped (instrument read counts).
- **No-missed-change invariant**: randomized add/modify/delete/rename
  fuzz over a tree, each round compared against a from-scratch full
  scan — the incremental result must equal the full-scan result.
- **Deletion → tombstone**: remove a file, re-scan, assert a tombstone
  revision exists and the chunk is gone from retrieval.
- **Wall-clock**: re-scan of an unchanged 50k-file fixture completes in
  a small constant multiple of one Merkle-root comparison, not a full
  walk.
- `make test-unit` ≥ 90 % coverage on manifest + walk + sweep code.

## References

- `corpus_forge/scanner/walker.py:133` — `walk()`; descent-time prune
  hook at lines 155–160 (extended here).
- `corpus_forge/sources/filesystem.py:179` — stateless `discover()`.
- `corpus_forge/ingest.py:510` — current read-then-hash change check.
- `corpus_forge/alembic/versions/0002_chunk_content_hash.py` — chunk
  content-addressing (the cache half already shipped).
- `corpus_forge/alembic/versions/0004_sync.py` — `document_revisions`,
  `is_tombstone`, `author_host` (the tombstone sink).
- `corpus_forge/sync/push.py:205` — PushPipeline mtime→hash pattern to
  mirror.
- `cursor.com/blog/secure-codebase-indexing` — Merkle tree of file
  hashes, child-derived directory hashes, walk-divergent-branches,
  simhash index reuse. The remote content-proof / obfuscated-path
  model is explicitly **out of scope** (see Non-goals).
- `.planning/rfcs/000-codeintel-2-code-knowledge-subgraphs.md` —
  consumes this RFC's change-set for incremental graph rebuild
  (`detect_changes`).
