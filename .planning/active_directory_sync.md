# Active Directory Sync for corpus-forge

## Context

corpus-forge already has two Macs writing into one shared Postgres, but each daemon is standalone — there is no coordination between hosts and no path back from the database to the local filesystem. Today, when you edit a markdown note on Mac A, Mac B has no way to learn about the change without re-scanning, and even on a single host every edit re-embeds *all* chunks of the file because `chunks` are positional and have no content identity.

This plan adds **active directory sync** for writable text sources (markdown vaults, etc.), modeled after Dropbox / iCloud / Obsidian Sync — Postgres is the source of truth, two halves of the sync engine push local FS events into the DB and pull remote revisions onto local disk, and chunk-level content hashing makes re-embedding incremental. Cloud-synced directories (iCloud, Dropbox, Google Drive) are explicitly supported, including detection and cleanup of cloud-generated duplicate files.

The work ships in two phases:

- **P0 — chunk-level embedding reuse.** Independently valuable: lowers Qwen3-8B cost on every edit, no behavior change visible to users.
- **P1 — cross-host sync engine.** Builds on P0, adds revision history, push/pull pipelines, non-destructive conflict handling, iCloud duplicate cleanup, sync CLI.

Read-only sources (`claude_code`, `opencode`) are explicitly out of scope — those tools own their files and round-tripping would corrupt them.

---

## P0 — Chunk-level embedding reuse

### Goal

When a markdown file is edited, only re-embed the chunks whose text actually changed. Reuse existing embeddings for unchanged chunks via a content-addressed lookup.

### Schema changes

`corpus_forge/schema/migrate.py` and the canonical SQL files gain a new migration:

```sql
-- 002_chunk_content_hash.sql
ALTER TABLE corpus.chunks
  ADD COLUMN IF NOT EXISTS content_hash TEXT;
CREATE INDEX IF NOT EXISTS chunks_content_hash_idx
  ON corpus.chunks(content_hash);
```

Backfill (idempotent, batched) in the migration runner: for any `chunks` row with `content_hash IS NULL`, set `content_hash = sha256(text)`.

### Code changes

- **`corpus_forge/backends/postgres.py`**
  - In `upsert_document` (existing path around `postgres.py:316–348`), when inserting new chunks, set `content_hash = sha256(chunk.text)`.
  - Add helper `_copy_reusable_embeddings(new_chunk_id, content_hash, embedder_ids) -> set[int]`: for each active embedder, look up an existing chunk with the same `content_hash` whose embedding row exists, and `INSERT … SELECT` that vector into the embedder's table for `new_chunk_id`. Returns the set of embedder_ids successfully reused so the caller can skip those during the encode pass.
  - Cache `(content_hash, embedder_id) → chunk_id` lookups for the duration of a single `upsert_document` call to avoid repeat queries when a chunk recurs.

- **`corpus_forge/ingest.py`**
  - `ingest_one` (`ingest.py:67–104`) passes the list of active embedder ids into `upsert_document` so the backend can short-circuit the encode pass when reuse covers an embedder.
  - `_write_embeddings_for_chunks` (`ingest.py:158–184`) is unchanged — `chunks_missing_embedding(embedder_id)` already returns only the chunks that still need encoding, which after reuse will be just the truly-new chunks.

- **`corpus_forge/identity.py`**
  - Add `chunk_content_hash(text: str) -> str` so the chunker reuse path uses the same hashing convention as `file_content_hash`.

### Caveat (worth flagging in the docstring, not fixing)

`MarkdownChunker` is positional with overlap. Edit-in-the-middle still shifts every downstream chunk's bytes, so reuse mainly helps for append-only edits and stable prefixes. This is the common case for note-taking; content-defined chunking would be a separate, much larger refactor.

### Tests

- `tests/unit/test_chunk_reuse.py`: `_copy_reusable_embeddings` copies vectors correctly; cache prevents duplicate lookups; non-matching `content_hash` falls through to encode path.
- `tests/integration/test_chunk_reuse_e2e.py` (testcontainers Postgres): ingest a 10-chunk doc, capture embedding count, append one chunk, assert ≥7 of the original embeddings are reused (allowing some loss to overlap).

---

## P1 — Cross-host sync engine

### Schema changes

```sql
-- 003_sync.sql
CREATE TABLE IF NOT EXISTS corpus.document_revisions (
  id                  BIGSERIAL PRIMARY KEY,
  document_id         BIGINT NOT NULL REFERENCES corpus.documents(id) ON DELETE CASCADE,
  revision_number     INT    NOT NULL,
  content_hash        TEXT   NOT NULL,
  text                TEXT   NOT NULL,                 -- '' for tombstones
  parent_revision_id  BIGINT REFERENCES corpus.document_revisions(id),
  author_host         TEXT   NOT NULL,
  authored_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  is_tombstone        BOOLEAN NOT NULL DEFAULT FALSE,
  metadata            JSONB  NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (document_id, revision_number)
);
CREATE INDEX document_revisions_doc_idx
  ON corpus.document_revisions(document_id, revision_number DESC);
CREATE INDEX document_revisions_authored_idx
  ON corpus.document_revisions(authored_at);

ALTER TABLE corpus.documents
  ADD COLUMN IF NOT EXISTS tombstoned_at TIMESTAMPTZ;

ALTER TABLE corpus.sources
  ADD COLUMN IF NOT EXISTS last_pulled_revision_id BIGINT,
  ADD COLUMN IF NOT EXISTS sync_enabled BOOLEAN NOT NULL DEFAULT FALSE;
```

`revision_number` is allocated under the existing `lock_source(source_uri)` advisory lock (`postgres.py:526-542`) using `SELECT COALESCE(MAX(revision_number), 0) + 1 …`. Full content per revision (Postgres TOAST handles compression); deferred diff-chain compaction is a P2 concern.

### New module: `corpus_forge/sync/`

```
corpus_forge/sync/
  __init__.py         exports SyncEngine
  engine.py           orchestrates push + pull per dataset; lifecycle
  push.py             watchdog observer → revision insert
  pull.py             poll loop → apply remote revisions to FS
  echo.py             EchoSuppressor (drop watchdog event for our own writes)
  conflicts.py        conflict file naming, iCloud duplicate detection
  fs.py               atomic_write_text, trash dir, .icloud / .dataless guards
  cloud.py            cloud-sync-directory detection (iCloud/Dropbox/GDrive paths)
```

### Push pipeline (FS → DB)

`PushPipeline` per sync-enabled dataset:

1. `watchdog.Observer` rooted at each source's `root`. Skip directories, hidden files, items matching `exclude_globs`, `*.icloud` placeholders, `.dataless` entries.
2. **Pre-filter** by `stat().st_mtime` against an in-memory cache before re-hashing — avoids SHA256 on Obsidian "save all" no-op writes.
3. **Echo check**: if `EchoSuppressor.was_just_written(path, current_hash)` matches, drop the event.
4. **iCloud-duplicate guard** (`conflicts.is_cloud_duplicate(path)`): detect the well-known cloud-generated patterns before treating the file as a new document.
5. Inside `lock_source(source_uri)`:
   - Read latest revision for this `source_uri`.
   - If `local_hash == latest.content_hash`: no-op.
   - Otherwise insert a `document_revisions` row (`parent_revision_id = latest.id`, `revision_number = MAX+1`, `author_host = self.host`).
   - `upsert_document` (now reuse-aware from P0).

Debounce stays at the existing `daemon.debounce_seconds` config, applied per-path.

### Pull pipeline (DB → FS)

`PullPipeline` per sync-enabled dataset, polling every `sync_poll_interval_s`:

```
SELECT r.*
FROM corpus.document_revisions r
JOIN corpus.documents d ON d.id = r.document_id
WHERE d.dataset_id = $1
  AND r.id > $last_pulled
  AND r.author_host <> $self_host
ORDER BY r.id ASC
```

For each remote revision, resolve the local path from `documents.source_uri` and:

- **Fast-forward** (local hash matches `parent_revision`'s hash, or local missing and parent NULL) → atomic write, register echo.
- **Already in sync** (local hash already matches new revision — typical when iCloud/Dropbox delivered the same bytes first) → register echo only.
- **Conflict** (local hash matches neither parent nor new) → non-destructive LWW: write incoming as canonical, save local copy as `<stem>.conflict-<host>-<ts><suffix>` in the configured `conflict_dir` (default: same directory). The conflict file is itself ingested as its own document by the next push cycle.
- **Tombstone** → move local file to `<trash_dir>/<dataset>/<rel-path>.deleted-<host>-<ts><suffix>` via `os.replace`. Never hard-delete. Set `documents.tombstoned_at`.

After processing, `UPDATE sources.last_pulled_revision_id = max(processed)`.

### Echo suppression

`EchoSuppressor` keeps an in-memory dict keyed by `str(path.resolve())` mapping to `(content_hash, expires_at)`, TTL 5s, GC'd opportunistically. Both pull (after writing a remote revision) and resolve commands register entries. The push pipeline checks before treating an event as new content.

### iCloud / cloud-sync-directory support

Three concrete pieces:

1. **`sync/cloud.py::detect_cloud_provider(path) -> Literal["icloud","dropbox","gdrive","none"]`** — substring match on resolved absolute path against known prefixes (`Library/Mobile Documents/com~apple~CloudDocs`, `Library/Mobile Documents/iCloud~`, `Dropbox`, `Google Drive`, `GoogleDrive`, `My Drive`). Used to log a startup warning and to enable provider-specific guards.

2. **Placeholder / dataless guards** in the push event handler:
   - `*.icloud` placeholders (0-byte stubs left when iCloud evicts content) — ignored. A delete event on an evicted file does **not** generate a tombstone; we wait for the placeholder to be replaced with real content.
   - `xattr` `com.apple.fileprovider.materialized` / dataless flag — same treatment.

3. **Cloud-duplicate detection and cleanup** — `conflicts.is_cloud_duplicate(path)` recognizes the standard duplicate-name patterns generated by these providers when they cannot merge:
   - iCloud: `<stem> 2<suffix>`, `<stem> 3<suffix>`, `<stem> (<n>)<suffix>`
   - Dropbox: `<stem> (<host>'s conflicted copy <date>)<suffix>`
   - Google Drive: `<stem> (1)<suffix>`, `<stem>-conflict-<date>-<n><suffix>`
   - macOS Finder copy-of pattern: `<stem> copy<suffix>`, `<stem> copy 2<suffix>`

   When a duplicate is detected, the push pipeline:
   - Reads both files, computes hashes.
   - If duplicate hash matches the canonical → log + delete the duplicate (it's pure noise from the cloud provider).
   - If hashes differ → rename the duplicate to corpus-forge's own `<stem>.conflict-<provider>-<host>-<ts><suffix>` naming so it's recognizable downstream, and ingest it as a conflict revision rather than a brand-new document.

   This keeps the DB free of "Foo.md", "Foo 2.md", "Foo 3.md" trios that iCloud sometimes spams under contention.

### Configuration additions (`corpus_forge/config.py`, `config.example.toml`)

```toml
[daemon]
debounce_seconds       = 2.0
host_id                = ""              # blank → socket.gethostname()
trash_dir              = "~/.local/share/corpus-forge/trash"
conflict_dir           = ""              # blank → next to original file
sync_poll_interval_s   = 5.0
sync_use_listen_notify = false           # P2

[[datasets]]
name         = "obsidian-vault"
kind         = "text"
sync_enabled = true                      # NEW: per-dataset opt-in
  [[datasets.sources]]
  plugin        = "markdown_vault"
  vault_root    = "~/Library/Mobile Documents/iCloud~md~obsidian/Documents"
  exclude_globs = [".obsidian/**", ".trash/**", ".*", "*.icloud"]
  ...
```

Pydantic validators:
- `sync_enabled = true` allowed only when `kind == "text"`.
- `host_id` resolves via `Config.host_id() -> str` (config value, else `socket.gethostname()`); first-run writes the chosen value to `~/.config/corpus-forge/host_id` so later hostname changes don't fork revision history.

### CLI additions (`corpus_forge/cli.py`)

```python
sync_app = typer.Typer(help="Cross-host sync operations.")
app.add_typer(sync_app, name="sync")

@sync_app.command("status")           # per-dataset: last pulled, pending, conflicts
@sync_app.command("pull")             # --once (default) | --continuous, -d DATASET
@sync_app.command("push")             # force rescan + push pending changes
@sync_app.command("resolve")          # CONFLICT_FILE --strategy keep-local|keep-remote (P2: merge)
@sync_app.command("history")          # SOURCE_URI [--limit N]
```

The existing `daemon` command becomes the orchestrator: spins up a `SyncEngine` for each sync-enabled dataset (push + pull) and the existing one-shot ingestion path for read-only datasets.

### Daemon wiring (`corpus_forge/daemon.py`)

Replace the current 39-line stub. New responsibilities:

- Construct `Config`, backend, embedders.
- For each dataset:
  - If `sync_enabled` → create a `SyncEngine(dataset, backend, embedders, host_id, …)` and start it (push + pull threads/tasks).
  - Else → fall through to existing `ingest_main(once=False)` path for that dataset (one-shot scan + watchdog-less mode).
- Block on signal handlers (`SIGINT`/`SIGTERM`); on shutdown, call `engine.stop()` for each engine and flush pending revisions.

### Tombstones / deletes

- Watchdog delete event (after iCloud-placeholder filtering) → revision with `is_tombstone=true`, `text=''`, `content_hash=sha256(b'')`.
- `documents.tombstoned_at = NOW()`; chunks/embeddings retained until a P2 retention sweeper.
- Pull side moves local file to trash dir (atomic rename), never hard-deletes.
- Resurrection: a non-tombstone revision after a tombstone clears `tombstoned_at` and resumes normal operation.

### Critical files

**Modified**
- `corpus_forge/daemon.py` — replace stub with sync-aware orchestrator.
- `corpus_forge/backends/postgres.py` — schema additions (revisions, columns), new methods (`insert_revision`, `latest_revision`, `pending_remote_revisions`, `mark_revision_pulled`), reuse-aware chunk insert from P0.
- `corpus_forge/ingest.py` — pass `embedder_ids` into `upsert_document`.
- `corpus_forge/config.py` — `DaemonConfig` and `DatasetConfig` sync fields + validators.
- `corpus_forge/cli.py` — `sync` Typer subgroup.
- `config.example.toml` — sync example block; add `*.icloud` to exclude_globs default.
- `corpus_forge/schema/migrate.py` — register `002_chunk_content_hash.sql` (P0) and `003_sync.sql` (P1).

**Added**
- `corpus_forge/sync/{__init__,engine,push,pull,echo,conflicts,fs,cloud}.py`
- `corpus_forge/schema/002_chunk_content_hash.sql`
- `corpus_forge/schema/003_sync.sql`
- `tests/unit/test_chunk_reuse.py`, `test_sync_echo.py`, `test_sync_conflicts.py`, `test_sync_fs.py`, `test_sync_cloud.py`
- `tests/integration/test_chunk_reuse_e2e.py`, `test_sync_push_pull.py`, `test_sync_tombstone.py`, `test_sync_icloud_dupe.py`

### Reused primitives (don't reinvent)

- `corpus_forge/identity.py::file_content_hash` — reuse for both file and revision hashing.
- `PostgresBackend.lock_source` (`postgres.py:526–542`) — already serializes cross-host writes per source_uri.
- `PostgresBackend.chunks_missing_embedding` — already returns the only chunks needing encoding after P0 reuse.
- `WatchedSource.file_content_hash`, `identity()` — keep contract; sync engine consumes these rather than duplicating logic.
- `documents.content_hash` short-circuit (`postgres.py:294`) — still the cheap "did anything change?" check; revision insertion only runs when content actually changed.

---

## P2 (deferred, do not build now)

- `LISTEN/NOTIFY` channel per dataset to drop pull latency from ~5s to ~50ms (poll fallback retained).
- `corpus-forge sync resolve --strategy merge` opening `$EDITOR` with diff markers.
- `corpus-forge sync history`.
- Section-level 3-way merge for non-overlapping concurrent edits.
- Tombstone retention sweeper.
- Revision compaction (keep latest + last-30-days, coalesce older to checkpoints).
- Content-addressed `chunk_texts` table replacing per-chunk embedding rows.

---

## Verification

End-to-end smoke test for both phases. Postgres via `make test-integration` testcontainers fixtures.

**P0**
1. `make test-unit` → all green including `test_chunk_reuse.py`.
2. `make test-integration` → `test_chunk_reuse_e2e.py` shows ≥70% of original embeddings reused after a small append.
3. Manual: ingest a real ~50-note vault, time second run after appending one chunk to one note — confirm only that doc's tail chunk(s) hit the encoder.

**P1**
1. `make test-unit` → all green for `test_sync_*.py`.
2. `make test-integration` → push/pull, tombstone, iCloud duplicate cleanup pass.
3. Manual cross-Mac:
   - Both Macs configured with `sync_enabled = true` against shared Postgres, pointed at a non-iCloud dir on each. Edit a markdown file on Mac A → appears on Mac B within `sync_poll_interval_s`. Delete on Mac A → moves to trash dir on Mac B. Concurrent edit on both → exactly one canonical winner per side, plus a `.conflict-<host>-<ts>.md` of the loser, also synced to both sides.
4. Manual iCloud:
   - Same as above but vault under iCloud. Confirm `.icloud` placeholder files are not treated as tombstones. Trigger an iCloud-generated `Foo 2.md` (e.g., by editing offline on both Macs while iCloud is paused, then resuming) and confirm corpus-forge either deletes the noise dupe (when content matches) or renames it to its own conflict naming and ingests it (when it doesn't).
5. `corpus-forge sync status` reports sane numbers on each Mac.

## Risks / open issues to watch during implementation

- **iCloud `.icloud` placeholder vs real delete** — must distinguish. A user-driven delete leaves no placeholder; an iCloud evict leaves a `<file>.icloud` stub. Test both.
- **revision_number monotonicity under contention** — guaranteed by holding `lock_source` through MAX-and-INSERT. Do not split the lock.
- **Hostname stability** — persist `host_id` to disk on first run; do not derive from live `socket.gethostname()` on every revision insert.
- **Echo TTL** — 5s default is generous on APFS; expose as config knob in case slow filesystems need more.
- **Embedding reuse and chunker config changes** — switching chunker config invalidates content_hash semantics. Document explicitly: chunker change → expect full re-embed. Optional later: store chunker-config-fingerprint in `chunks.metadata` and gate reuse on it.
