# TDD Task Board — Phase L / Wave 4 (estimate stats + progress bars + logger discipline)

_Owner: tdd-principal. Workers: read freely. Edit only your claimed row's
`status` and `claimed_by`._

Source plan: `/Users/evanowen/Library/Mobile Documents/com~apple~CloudDocs/Workspace/playground/corpus-forge/.planning/tdd/phase_l_cli_ux.md` (§6 Estimate upgrades + §7 Progress bars on long ops).
Dispatch input: orchestrator brief, Phase L / Wave 4 kickoff after Wave 3 landed in commit `bbb213e`.

> Previous slice (Wave 3) record archived in git history at `bbb213e`.

## Project gates
- lint: `uv run ruff check`
- format: `uv run ruff format --check`
- test: `uv run python -m pytest tests/ -x`
- coverage-min: keep current baseline (no regression)

## Hard constraints (from dispatch)
1. **DO NOT COMMIT, DO NOT PUSH.** Workers stage only. Orchestrator commits.
2. **NO `typer.echo/secho/prompt/confirm`** outside `corpus_forge/ui/` — the
   `tests/cli/test_no_typer_echo.py` regression will fail you.
3. **`uv run python -m pytest`**, never bare `pytest`.
4. Daemon mode does NOT show progress bars — gate on `once=True` for
   `ingest`. Sync push `start()` is observer-driven (never one-shot, never
   bounded total), so its bookend is a one-shot info pair + N=0 progress
   (or skipped); see W4-04 acceptance for the exact shape.
5. Existing `SyncEstimate` dataclass shape is wire-protocol stable for the
   MCP `estimate_sync_size` tool (schema_version=1). DO NOT add new
   *required* fields to `SyncEstimate` — put scan stats on a NEW dataclass
   `ScanStats` exposed via the CLI command, not on `SyncEstimate`.
   `--json` mode must preserve the existing top-level shape; add scan
   stats as a NEW sibling key (e.g. nested under a top-level `"scan"`).
6. `backend.chunks_missing_embedding(embedder_id)` exists today. A
   *count* helper does NOT — W4-02 adds a thin `count_chunks_missing_embedding`
   method on both `PostgresBackend` + `SQLiteBackend` (one-line
   `SELECT COUNT(*)` companion).
7. Pending-docs query: there is no `documents.state` column. Define
   "documents not yet chunked" as documents with zero chunks rows
   (`NOT EXISTS (SELECT 1 FROM chunks WHERE document_id = documents.id)`).
   The schema also has no top-level `dataset_id` on chunks — chunks
   live under documents which carry dataset_id. Add new backend method
   `pending_documents(dataset_id=None, limit=5)` returning
   `(count: int, sample_uris: list[str])` on both backends.
8. CLI estimate today renders human output via bare `print(...)` for the
   data lines (intentional — see existing comments). Keep that idiom for
   the new tables (use `rich.table.Table` rendered through `console` for
   the styled path BUT also write a plain fallback when running under
   `NO_COLOR=1`/`TERM=dumb` conftest so existing assertions stay
   readable). Pragmatic: rely on `ui_console.print(table)` — Rich already
   degrades to plain text under `NO_COLOR=1`.

## Decomposition notes (orchestrator)

- **Surface-disjoint matrix:**
  - W4-01 owns `corpus_forge/estimate.py` + `cli.py` `estimate` function
    body (lines ~1851-2087). Adds new `ScanStats` dataclass and threads
    `pending_documents` + `chunks_missing_embedding` (read-only) into the
    CLI render.
  - W4-02 owns `corpus_forge/embed.py` (`backfill_embedder` +
    `backfill_image_embedder`), `corpus_forge/embedders/sentence_transformers.py`
    (loader INFO), AND the new backend method
    `count_chunks_missing_embedding` on `corpus_forge/backends/postgres.py`
    + `corpus_forge/backends/sqlite.py`. W4-01 also reads from
    `chunks_missing_embedding` but the count helper is owned by W4-02 —
    serialize: tester W4-02 produces the helper first; W4-01 reads it
    via a stub when fingerprint testing.
  - W4-03 owns `corpus_forge/ingest.py` (the `ingest_once` function +
    audit of `scan/extract/chunk` logger taxonomy).
  - W4-04 owns `corpus_forge/sync/pull.py` + `corpus_forge/sync/push.py`
    plus the sync CLI command bodies (`pull`/`push` in `cli.py`).
  - All four tasks touch `cli.py` but on disjoint command bodies
    (estimate vs embed vs ingest vs sync pull/push). Workers stage; the
    orchestrator commits separately per task.

- **W4-02 is the bottleneck for W4-01:** W4-01's pending-files-render
  needs `count_chunks_missing_embedding` to exist on both backends.
  Resolution: testers produce their RED suites in parallel, but W4-01's
  coder waits for W4-02's coder to land the helper. Two-wave shape:
  - Wave A (parallel): all four testers RED; W4-02 coder GREEN; W4-03 coder GREEN; W4-04 coder GREEN.
  - Wave B (after W4-02 coder): W4-01 coder GREEN.
  - Wave C (after all coders): QA in parallel.

- **Documents-pending-count:** add new `backend.pending_documents(dataset_id=None,
  limit=5) -> tuple[int, list[str]]` on both backends. Logic:
  `WHERE NOT EXISTS (SELECT 1 FROM chunks WHERE document_id = documents.id)`.
  Owned by W4-01 (since W4-01 is the only consumer).

- **Embedder selection for the "Pending files" pending-embedding count:**
  estimate already resolves the active embedder list via
  `Config.load().embedders` filtered on `active=True`. For the chunks-
  missing-embedding count, sum across all active embedders OR pick the
  first (cheapest). Pick FIRST active embedder (simplest, matches the
  user mental model of "the embedder that would run next"). Document
  the choice in a code comment + a CLI footnote.

- **Sync push is observer-driven:** `push.start()` schedules a watchdog
  observer and returns immediately — there's no bounded loop to wrap.
  W4-04 ships a single `logger.info` bookend pair around `start()` (and
  a separate one around the optional `handle_change(path)` per-event
  trigger). No progress bar for the push start path. (The brief notes
  "Pending push count" — there is no such count in the current push
  pipeline because push is event-driven, not pending-queue-driven; we
  document this discrepancy in the W4-04 acceptance details and ship
  bookends only, no bar.)

- **Logger taxonomy audit:**
  - `corpus_forge.embedders.loader` — owned by W4-02. Add INFO at model
    load start/finish in `embedders/sentence_transformers.py`
    `_load_model()`.
  - `corpus_forge.embedders.batch` — owned by W4-02. DEBUG per batch
    (already present via `logger.info`-as-debug at embed.py:175 — demote
    to DEBUG). INFO milestone via the progress factory is free.
  - `corpus_forge.ingest.scan` — owned by W4-03. INFO at start/end of
    each source-root scan in `ingest_once()`.
  - `corpus_forge.ingest.extract` — owned by W4-03. INFO on extractor
    failure (the existing `logger.error` at ingest.py:481 is on the
    correct taxonomy logger; rename if not).
  - `corpus_forge.ingest.chunk` — owned by W4-03. INFO on aggregate
    every 100 docs (use a local counter in the loop).
  - `corpus_forge.sync.scan` / `corpus_forge.sync.push` /
    `corpus_forge.sync.pull` — owned by W4-04. INFO bookends on
    `PullPipeline.start()` + `PushPipeline.start()`. The existing
    `corpus_forge.sync.pull` logger is on a per-module basis (already
    correctly namespaced as `corpus_forge.sync.pull` via
    `logger = logging.getLogger(__name__)` at `pull.py:14`); the push
    module is missing its module-level logger entirely (uses `logging`
    inline). Add a module-level
    `logger = logging.getLogger(__name__)` to `push.py`.

- **Config-loading in tests:** for the embed-progress test, stub
  `Config.load()` to return a minimal config with one embedder. The
  backend can be a `unittest.mock.MagicMock` with
  `chunks_missing_embedding` returning `[]` on the second call to
  exit the loop, and `count_chunks_missing_embedding` returning a
  known integer. Drive via direct call to `backfill_embedder`, not
  through the Typer CLI runner — the CLI wrapper is a 2-line shim.

- **Sample-of-5-paths in estimate "Pending files":** call
  `backend.pending_documents(dataset_id=None, limit=5)` once and render
  count + the sample list. Skip the section entirely if both pending
  docs == 0 AND pending chunks == 0 (no "Pending: 0 files" noise).

## Tasks

| id | title | depends_on | surface | risk | status | claimed_by | notes |
|----|-------|------------|---------|------|--------|------------|-------|
| W4-01 | Estimate scan stats + pending files | W4-02 (coder) | `corpus_forge/estimate.py`, `corpus_forge/cli.py` (estimate command), `corpus_forge/backends/postgres.py` (+pending_documents), `corpus_forge/backends/sqlite.py` (+pending_documents), `tests/test_estimate.py` (extend), `tests/cli/test_estimate_progress.py` (new) | med | pending | — | New `ScanStats` dataclass; wrap `_walk` in `time.perf_counter()` + `make_progress` unbounded with logger; CLI renders new `rich.table.Table` for Scan stats + Pending files. `--json` mode preserves existing `SyncEstimate` shape; adds new sibling key `"scan"` under the JSON document. |
| W4-02 | Embed progress + count helper + loader INFO | — | `corpus_forge/embed.py`, `corpus_forge/embedders/sentence_transformers.py`, `corpus_forge/backends/postgres.py` (+count_chunks_missing_embedding), `corpus_forge/backends/sqlite.py` (+count_chunks_missing_embedding), `tests/cli/test_embed_progress.py` (new) | med | pending | — | Wrap `backfill_embedder` main loop in `make_progress("Embedding chunks", total=n, logger=logger)`. Add count helper to both backends. Add INFO at load start/finish in `_load_model`. Demote per-batch INFO to DEBUG. |
| W4-03 | Ingest --once progress + logger taxonomy | — | `corpus_forge/ingest.py`, `tests/cli/test_ingest_progress.py` (new) | med | pending | — | Wrap the `for raw in raw_items:` loop in `make_progress("Ingest", total=None, logger=...)` (unbounded; sources don't pre-count). Add INFO bookends "Scanning <source>" + "Scan complete" via `corpus_forge.ingest.scan` logger. Audit + add the taxonomy loggers listed in the brief. |
| W4-04 | Sync pull/push progress + logger bookends | — | `corpus_forge/sync/pull.py`, `corpus_forge/sync/push.py`, `corpus_forge/cli.py` (sync pull / sync push command bodies), `tests/cli/test_sync_progress.py` (new) | low | pending | — | Pull `--once` wraps the `for rev in pending` loop in `make_progress("Pulling revisions", total=len(pending), logger=...)`. Push start logs INFO bookend; per-change handler stays log-only (no bar). Add module-level logger to `push.py`. |

## Acceptance details

### W4-01 — Estimate scan stats + pending files

**`corpus_forge/estimate.py` changes:**

1. New frozen dataclass `ScanStats`:
   ```python
   @dataclass(frozen=True)
   class ScanStats:
       elapsed_s: float
       scan_rate: float   # files / second
       file_count: int
       dir_count: int
   ```
2. Refactor `_walk()` (lines 364-442) to return a `(buckets, file_count,
   dir_count, total_raw_bytes, ScanStats)` 5-tuple. Existing callers
   (just `estimate_sync`) updated to unpack the 5-tuple; the extra
   element is ignored by `estimate_sync` itself (the CLI will reach in
   for it separately via a new helper).
3. New module-level `scan_logger = logging.getLogger("corpus_forge.estimate.scan")`.
4. Wrap the existing iterative walk's `while stack:` loop in:
   ```python
   from corpus_forge.ui.progress import make_progress
   started = time.perf_counter()
   with make_progress("Scanning", total=None, logger=scan_logger) as progress:
       task = progress.add_task("Scanning", total=None)
       while stack:
           ...
           # at the end of each file processed:
           progress.update(task, advance=1)
   elapsed_s = time.perf_counter() - started
   scan_rate = (file_count / elapsed_s) if elapsed_s > 0 else 0.0
   stats = ScanStats(elapsed_s=elapsed_s, scan_rate=scan_rate, file_count=file_count, dir_count=dir_count)
   ```
5. New public function `walk_with_stats(root, *, ignore=None) -> tuple[..., ScanStats]`
   that exposes the 5-tuple. `estimate_sync` calls the internal `_walk`
   (which now also returns the stats) and uses them when rendering.
6. Export `ScanStats` + `walk_with_stats` in `__all__`.

**Backend additions (W4-01 owns these because pending_documents is read-
only for estimate; W4-02 owns the count_chunks_missing_embedding):**

In `corpus_forge/backends/postgres.py` (new method near
`chunks_missing_embedding`):
```python
def pending_documents(
    self, *, dataset_id: int | None = None, limit: int = 5
) -> tuple[int, list[str]]:
    """Return (count, sample_source_uris) of documents not yet chunked.

    Defined as documents with zero rows in the ``chunks`` table.
    """
    where_clause = "" if dataset_id is None else " AND d.dataset_id = %s"
    params: tuple = () if dataset_id is None else (dataset_id,)
    count_rows = self._execute(
        f"SELECT COUNT(*) AS n FROM corpus.documents d "
        f"WHERE NOT EXISTS (SELECT 1 FROM corpus.chunks c WHERE c.document_id = d.id)"
        f"{where_clause}",
        params,
    )
    count = int(count_rows[0]["n"]) if count_rows else 0
    sample_rows = self._execute(
        f"SELECT d.source_uri FROM corpus.documents d "
        f"WHERE NOT EXISTS (SELECT 1 FROM corpus.chunks c WHERE c.document_id = d.id)"
        f"{where_clause} ORDER BY d.id LIMIT %s",
        (*params, limit),
    )
    return count, [r["source_uri"] for r in sample_rows]
```

Mirror for `corpus_forge/backends/sqlite.py` (no `corpus.` schema prefix,
`?` placeholders).

**CLI changes (`corpus_forge/cli.py` `estimate` command body):**

After the existing `estimate_sync` call:
1. Compute `scan_stats = estimate_result.scan_stats` (new attribute, see
   below — or call `walk_with_stats` separately to avoid breaking the
   `SyncEstimate` ABI).
   
   **DECISION:** keep `SyncEstimate` ABI clean. Run the walk twice? No —
   call `walk_with_stats` ONCE before `estimate_sync` and pass the
   pre-walked buckets into `estimate_sync` via a new kwarg `_prewalked:
   tuple | None = None` (private/escape-hatch). Or simpler: change
   `estimate_sync` to internally use the new walk and stash `ScanStats`
   on a NEW non-frozen sibling result the CLI assembles. Implementor's
   choice — pick the path with smallest diff and document.
2. Build a `rich.table.Table` for "Scan stats":
   ```
   Scan stats
   ──────────────────────────
   Elapsed       3.2s
   Rate          385 files/s
   Files seen    1,234
   Dirs visited  87
   ```
   Use `rich.box.SIMPLE` for clean look; render via `ui_console.print(table)`.
3. Open the backend ONLY if `--json` is False AND the user did not
   pass a flag like `--no-pending` (no need to add a flag; just degrade
   gracefully if the backend doesn't open — wrap in try/except OSError,
   ConnectionError, RuntimeError, ImportError; on failure skip the
   "Pending files" section silently). Pseudocode:
   ```python
   backend = None
   try:
       backend = _get_backend(config)  # existing helper
   except Exception as exc:
       logger.debug("estimate: backend unreachable for pending counts (%s)", exc)
   ```
4. If `backend is not None`, compute:
   - `pending_doc_count, pending_doc_samples = backend.pending_documents(limit=5)`
   - For the first active embedder (or None if none configured), call
     `embedder_id = backend.find_embedder_id_by_name(<first active.name>)`
     (if that helper doesn't exist, fall back to skipping). Then
     `pending_chunk_count = backend.count_chunks_missing_embedding(embedder_id)`.
     Catch and log-debug on any exception.
5. Render "Pending files" table:
   ```
   Pending files
   ──────────────────────────────
   Documents not chunked   12
   Chunks missing embedding 481
   
   Sample paths (top 5):
     - /path/to/foo.md
     - ...
   ```
   Skip the entire section if BOTH counters are zero.
6. `--json` mode: the existing `print(_json.dumps(asdict(estimate_result), ensure_ascii=False))`
   stays UNCHANGED. The new `scan` key is added as a sibling — wrap the
   final json object in `{"sync_estimate": asdict(...), "scan": asdict(scan_stats), "pending": {"documents": N, "chunks_missing_embedding": N, "sample_paths": [...]}}`.

   **Wire compat:** this IS a JSON schema bump. The MCP `estimate_sync_size`
   tool consumes `asdict(SyncEstimate)` directly (per the J1 brief +
   `tests/unit/test_mcp_estimate.py`). DO NOT change that tool's shape.
   The CLI `--json` mode gets the wrapped shape (sibling keys). Add a
   `--legacy-json` flag for the old shape if needed, OR put the new
   data UNDER a top-level `scan` + `pending` while keeping the existing
   keys at the top level (additive — safer). **Pick the additive shape:**
   `{...existing SyncEstimate fields..., "scan": {...}, "pending": {...}}`.
   Add the new keys via `{**asdict(estimate_result), "scan": ..., "pending": ...}`.

**Tests:**

`tests/test_estimate.py` extend (or add new file `tests/estimate/test_scan_stats.py`):
1. `test_walk_with_stats_returns_scanstats` — synthetic tmp_path with
   3 files; assert `stats.file_count == 3`, `stats.elapsed_s > 0`,
   `stats.scan_rate > 0`.
2. `test_scan_logger_emits_bookends` — use `caplog` at INFO level on
   `corpus_forge.estimate.scan`; assert "Scanning started" + "Scanning
   complete" in the captured records after one walk.

`tests/cli/test_estimate_progress.py` new:
1. `test_estimate_renders_scan_stats_table` — `runner.invoke(app, ["estimate", str(tmp_path)])`;
   assert "Scan stats" header appears in stdout AND a regex matching
   `\d+\.\d+s` (elapsed) appears.
2. `test_estimate_renders_pending_files_when_backend_available` —
   patch `_get_backend` to return a `MagicMock` with
   `pending_documents.return_value = (3, ["/a.md", "/b.md"])` and
   `count_chunks_missing_embedding.return_value = 17`; assert
   "Pending files" + "3" + "17" in stdout.
3. `test_estimate_skips_pending_section_when_backend_unreachable` —
   patch `_get_backend` to raise; assert "Pending files" NOT in stdout.
4. `test_estimate_json_includes_scan_and_pending` — `runner.invoke(app,
   ["estimate", str(tmp_path), "--json"])`; parse stdout as JSON;
   assert `"scan"` key present with `"elapsed_s"`, `"scan_rate"`,
   `"file_count"`, `"dir_count"`; assert top-level SyncEstimate fields
   like `"schema_version"`, `"file_count"`, `"total_raw_bytes"` are STILL
   present (additive shape).

### W4-02 — Embed progress + count helper + loader INFO

**Backend additions (BOTH `corpus_forge/backends/postgres.py` and
`corpus_forge/backends/sqlite.py`):**

```python
def count_chunks_missing_embedding(self, embedder_id: int) -> int:
    """Return the total number of chunks missing an embedding for ``embedder_id``.

    Companion to ``chunks_missing_embedding``; no limit, no row payload.
    """
    embedder_info = self._execute(
        "SELECT name FROM corpus.embedders WHERE id = %s", (embedder_id,)
    )
    if not embedder_info:
        return 0
    name = embedder_info[0]["name"]
    table_name = f"embeddings_{name.replace('-', '_')}"
    rows = self._execute(
        f"SELECT COUNT(*) AS n FROM corpus.chunks c "
        f"LEFT JOIN corpus.{table_name} e ON e.chunk_id = c.id "
        f"WHERE e.chunk_id IS NULL"
    )
    return int(rows[0]["n"]) if rows else 0
```

Mirror in sqlite (no `corpus.` prefix, `?` placeholder for embedder_id
lookup; the embedder name lookup also reads `embedders.table_name`
column directly in sqlite per the existing `chunks_missing_embedding`
implementation — match that pattern). For unknown embedder_id, return 0.

**`corpus_forge/embed.py` changes:**

1. Add `from corpus_forge.ui.progress import make_progress` at module top.
2. Inside `backfill_embedder` (line 73), after `embedder_id = backend.register_embedder(embedder)`:
   ```python
   total = backend.count_chunks_missing_embedding(embedder_id)
   logger.info(f"Backfilling {embedder_name}: {total} chunks pending")
   ```
3. Wrap the `while True:` loop (line 140) in:
   ```python
   with make_progress(
       f"Embedding chunks ({embedder_name})",
       total=total,
       logger=logger,
   ) as progress:
       task = progress.add_task("Embedding chunks", total=total)
       while True:
           ...
           processed += len(pairs)
           progress.update(task, completed=processed)
           if limit is not None and processed >= limit:
               break
   ```
4. Demote per-batch `logger.info(f"Generating embeddings for ...")` (line
   175) and `logger.info(f"Processed {processed} embeddings so far")`
   (line 183) to `logger.debug`. The progress factory's bookends +
   milestone INFO lines replace them in the default INFO stream.
5. Mirror in `backfill_image_embedder` (line 192). For the image path,
   `total` falls back to `None` (unbounded) since
   `image_chunks_missing_embedding` doesn't have a count helper today —
   document the gap in a code comment; Wave 5 can backfill the helper.

**`corpus_forge/embedders/sentence_transformers.py` changes:**

1. Add module-level `loader_logger = logging.getLogger("corpus_forge.embedders.loader")`
   (NOT `logger = ...` to avoid clashing with the `import logging`
   namespace).
2. In `_load_model()` (line 60):
   ```python
   def _load_model(self):
       if self._model is None and SENTENCE_TRANSFORMERS_AVAILABLE:
           from corpus_forge._ml_device import resolve_device
           import time
           started = time.perf_counter()
           loader_logger.info(
               "Loading embedder %s (sentence-transformers, %d-dim, device=%s)",
               self.name, self.dimension, self.device,
           )
           self._model = SentenceTransformer(self.model_id, device=resolve_device(self.device))
           loader_logger.info(
               "Embedder %s ready in %.1fs", self.name, time.perf_counter() - started,
           )
   ```

**Tests:**

`tests/cli/test_embed_progress.py` new:
1. `test_backfill_embedder_emits_bookends` — stub `Config.load`, stub
   the backend (`MagicMock`) with:
   - `register_embedder.return_value = 1`
   - `chunks_missing_embedding.side_effect = [
       [(1, "text-a"), (2, "text-b")],
       [],  # exit loop
     ]`
   - `count_chunks_missing_embedding.return_value = 2`
   Stub the embedder registry to return a `MagicMock` embedder whose
   `encode` returns a 2x4 numpy array. Call `backfill_embedder("e1")`.
   Use `caplog` at INFO; assert "Embedding chunks" appears in at least
   one bookend record (the progress factory's auto-emitted "started"
   + "complete" lines).
2. `test_count_chunks_missing_embedding_postgres` — *NEW BACKEND TEST*
   skip if no Docker / Postgres unavailable (use existing pg_backend
   fixture pattern); insert known docs+chunks (no embeddings); assert
   count matches.
3. `test_count_chunks_missing_embedding_sqlite` — same against
   sqlite_backend.
4. `test_loader_logs_on_model_load` — instantiate
   `SentenceTransformersEmbedder` and call `_load_model()` with the
   sentence-transformers package patched to a stub. `caplog` on
   `corpus_forge.embedders.loader` shows "Loading embedder" + "ready in".

### W4-03 — Ingest --once progress + logger taxonomy

**`corpus_forge/ingest.py` changes:**

1. Add new module-level loggers (in addition to the existing
   `logger = logging.getLogger(__name__)`):
   ```python
   scan_logger = logging.getLogger("corpus_forge.ingest.scan")
   extract_logger = logging.getLogger("corpus_forge.ingest.extract")
   chunk_logger = logging.getLogger("corpus_forge.ingest.chunk")
   ```
2. Inside `ingest_once()` (line 422), after `source = _instantiate_source(...)`:
   ```python
   scan_logger.info("Scanning %s (%s source)…", source_config.plugin, source.name)
   ```
3. Wrap the `for raw in raw_items:` loop in `make_progress("Ingest",
   total=None, logger=scan_logger)`:
   ```python
   from corpus_forge.ui.progress import make_progress
   raw_items = source.scan()
   with make_progress(
       f"Ingest ({source.name})",
       total=None,
       logger=scan_logger,
   ) as progress:
       task = progress.add_task("Ingest", total=None)
       docs_chunked = 0
       for raw in raw_items:
           try:
               ingest_one(backend, raw, chunker, embedders, dataset_id)
               docs_chunked += 1
               if docs_chunked % 100 == 0:
                   chunk_logger.info("Chunked %d documents so far", docs_chunked)
           except Exception as e:
               extract_logger.info(
                   "Extractor failed on %s: %s",
                   getattr(raw, "source_uri", "unknown"),
                   e,
               )
               continue
           progress.update(task, advance=1)
       scan_logger.info("Scan complete: %d documents", docs_chunked)
   ```
   Note: the existing `logger.error(...)` at line 481 stays as a fallback
   but the user-facing log line moves to `extract_logger` (downgrade
   level from ERROR to INFO since the extractor failure is recoverable
   per-file).
4. **Daemon mode gating:** `main()` at line 613 already branches on
   `once`. The progress wrapping lives inside `ingest_once`, which is
   only called when `once=True`. The daemon-mode branch (line 629) is
   stub today — no progress changes needed.

**Tests:**

`tests/cli/test_ingest_progress.py` new:
1. `test_ingest_once_emits_bookend_logs` — stub `Config.load` returning
   a minimal config with one filesystem source pointed at `tmp_path`
   containing 2 markdown files. Stub the backend (`MagicMock` covering
   `migrate`, `get_or_create_dataset`, `register_source`, `upsert_document`,
   `register_embedder`, etc.). Call `corpus_forge.ingest.main(once=True)`.
   `caplog` at INFO on `corpus_forge.ingest.scan` and on the root
   `corpus_forge.ui.progress`-bookend records — assert "Ingest started"
   AND "Ingest complete" appear AND "Scan complete:" appears.
2. `test_extract_failure_logs_at_info` — same stub but `ingest_one`
   patched to raise on one of the two files; `caplog` on
   `corpus_forge.ingest.extract` shows "Extractor failed".
3. `test_chunk_milestone_emitted_every_100_docs` — patch source to
   yield 250 raw items; assert at least 2 `chunk_logger` INFO records
   ("Chunked 100 …", "Chunked 200 …"). Patch `ingest_one` to a no-op
   so the loop is fast.

### W4-04 — Sync pull/push progress + logger bookends

**`corpus_forge/sync/pull.py` changes:**

1. Inside `PullPipeline.tick()` (line 81), wrap the `for rev in pending`
   loop in progress (after computing `pending` so `len(pending)` is known):
   ```python
   from corpus_forge.ui.progress import make_progress
   pending_list = list(pending)
   if not pending_list:
       return 0
   with make_progress(
       "Pulling revisions",
       total=len(pending_list),
       logger=logger,
   ) as progress:
       task = progress.add_task("Pulling revisions", total=len(pending_list))
       count = 0
       for rev in pending_list:
           ...  # existing loop body
           progress.update(task, advance=1)
   return count
   ```
   Note: `pending` from `pending_remote_revisions` is already a list in
   the existing tests — confirm via spot-read; the conversion is a
   defensive no-op for iterators.

**`corpus_forge/sync/push.py` changes:**

1. Add module-level `logger = logging.getLogger(__name__)` at top.
2. In `PushPipeline.start()` (line 142): add INFO bookend BEFORE
   `self._observer.start()`:
   ```python
   logger.info(
       "Push start: dataset_id=%d root=%s debounce=%.1fs",
       self._dataset_id, source_root, debounce_seconds,
   )
   ```
3. In `PushPipeline.stop()`: add INFO bookend after the observer joins:
   ```python
   logger.info("Push stop: dataset_id=%d", self._dataset_id)
   ```

**CLI changes (`corpus_forge/cli.py`):**

1. `sync pull --once` body (`cli.py:401`-ish): after the `count = pl.tick()`
   line, the existing `ui_ok(f"Pulled {count} revision(s)")` stays.
   Progress bar lives inside `tick()` — CLI is unchanged.
2. `sync push` body (`cli.py:446`-ish): no change. Bookends are inside
   `PushPipeline.start`/`stop`.

**Tests:**

`tests/cli/test_sync_progress.py` new:
1. `test_pull_tick_emits_bookend_when_pending` — instantiate
   `PullPipeline` with a `MagicMock` backend whose
   `pending_remote_revisions.return_value = [<3 fake revs>]`. Call
   `pipeline.tick()`. `caplog` on `corpus_forge.sync.pull` shows
   "Pulling revisions started: 3 items" + "complete: 3 items".
2. `test_pull_tick_no_pending_skips_bookend` — same with empty pending;
   no bookend records emitted (early-return guards the wrap).
3. `test_push_start_logs_bookend` — instantiate `PushPipeline` with a
   mock backend + a real `EchoSuppressor`; call `start(tmp_path)`;
   `caplog` on `corpus_forge.sync.push` shows "Push start:".
4. `test_push_stop_logs_bookend` — same, call `stop()` after `start()`;
   assert "Push stop:" recorded.

## DAG

- **Wave A** (5 parallel): testers W4-01, W4-02, W4-03, W4-04; coder W4-02; coder W4-03; coder W4-04.
- **Wave B** (1 sequential): coder W4-01 (after W4-02 lands `count_chunks_missing_embedding`).
- **Wave C** (4 parallel): QA W4-01, W4-02, W4-03, W4-04.
