# RFC: Developer UX verbs — logs tail, stats, debug, config edit

status: accepted
**Owner**: nightly (open for any agent to claim)
**Priority**: P3
**Depends on**: `rfc-source-provenance-git-and-lines.md` (for the
`get_source_file_context` MCP tool)

## Context

corpus-forge's CLI is fairly complete for the core workflows
(`ingest`, `embed`, `search`, `eval`, `prune` — once the growth RFC
lands). The day-to-day developer UX has visible gaps:

- **No live log tail.** Logs rotate into `<cache>/corpus-forge/logs/`
  but the user has to `tail -f` themselves with `platformdirs` paths.
- **No quick stats.** `corpus-forge estimate` predicts future cost
  but doesn't say "you currently have 12k chunks across 3 datasets,
  using 280 MB."
- **No `debug <chunk_id>`** to dump everything we know about a
  chunk — content, embeddings, neighbours, feedback, provenance — in
  one place.
- **No `config edit`** that round-trips comments and key order. We
  pinned `tomlkit>=0.13` (`pyproject.toml:67`) for exactly this
  purpose; the verb was never wired.
- **No MCP `get_source_file_context`** — covered by the provenance
  RFC's dep, called out here so it's discoverable from the
  developer-UX list.

These are small, ship-as-you-go pieces. Bundled into one RFC so
Nightly can pick them off one task at a time without spawning ten
single-line PRs.

## Goals

Five small developer-facing verbs, each with crisp output and
predictable JSON-on-stdout for scripting:

- `corpus-forge logs tail [--follow] [--level info|warn|error]`
- `corpus-forge stats [--dataset <name>] [--json]`
- `corpus-forge debug <chunk_id> [--json]`
- `corpus-forge config edit [--editor $EDITOR]`
- MCP `get_source_file_context(chunk_id)` (lives in the provenance
  RFC's surface; named here so the UX-completeness checklist tracks
  it)

## Non-goals

- No web UI, no TUI. CLI-only.
- No interactive REPL.
- No log shipping / external observability backends.

## Approach

### `logs tail`

`corpus_forge/admin/logs.py` (or extend
`corpus_forge/diagnostics/logs.py`). Resolve log file via the
existing `corpus_forge/logging_config.py` location (rotating-file
handler). Implement `tail` (last N lines) and `follow` (inotify on
POSIX, polling fallback on Windows). Level filter is a simple
substring match on the level token.

```
corpus-forge logs tail            # last 100 lines, exit
corpus-forge logs tail -f         # follow
corpus-forge logs tail -f --level error
```

### `stats`

`corpus_forge/admin/stats.py`. Queries the active backend (SQLite or
Postgres) for:

- Per-dataset: `rows_documents`, `rows_chunks`, `rows_embeddings`,
  `latest_ingest_ts`, `disk_bytes_estimate`.
- Aggregates across all datasets.
- Optional `--dataset <name>` filters.

Default output: a Rich table to stderr + machine summary to stdout
when piped. `--json` skips the Rich rendering entirely.

### `debug <chunk_id>`

`corpus_forge/admin/debug.py`. Dumps for a chunk:

- Text content (first N chars, ellipsis the rest).
- All metadata fields (incl. provenance + quality signals from the
  other RFCs).
- Embedding vector summary (dim, L2 norm, first 8 values).
- Top-5 nearest neighbours by cosine similarity.
- All `recent_feedback` rows.
- If `rfc-source-provenance-git-and-lines.md` shipped: the
  `get_source_file_context` data and whether the file still exists
  on disk at the captured commit.

Default output: a Rich panel-by-panel layout. `--json` for scripting.

### `config edit`

`corpus_forge/admin/config_edit.py`. Round-trips via `tomlkit`:

1. Load `~/.config/corpus-forge/config.toml` as a `tomlkit.Document`
   (preserves comments + key order).
2. Spawn `$EDITOR` against a tempfile copy.
3. On editor exit: re-parse via `tomlkit`, validate via the existing
   `Config.model_validate`, atomic-rename on success, refuse and
   report the validation error on failure.
4. Diff-style summary of what changed.

### MCP `get_source_file_context`

Lives in `rfc-source-provenance-git-and-lines.md` — listed here for
completeness of the developer-UX surface. When that RFC ships, the
MCP tool becomes available; no extra work in this RFC.

## Tasks

- [x] `corpus_forge/admin/logs.py` — `tail` / `follow` / level filter
      against the rotating-file log path from
      `corpus_forge/logging_config.py`. **Lives at
      `corpus_forge/diagnostics/logs.py`** (not the RFC's
      `admin/` path) — that's where the existing `logs path` / `tail`
      / `clear` verbs were already wired in Phase L Wave 6. This task
      finished the trio by adding the `--level <name>` filter
      (case-insensitive, supports `warn` / `warning` aliases) that
      drops lines below the named severity in both single-shot and
      `--follow` modes. Unparseable lines (tracebacks, `print()`)
      are dropped when a level filter is active — the user who asks
      for `--level error` does not want the unstructured noise.
      8 new tests in `tests/diagnostics/test_logs_subcommand.py`.
- [ ] `corpus_forge/admin/stats.py` — per-dataset + aggregate row
      counts and on-disk size estimate (reuse
      `corpus_forge/estimate.py` sizing model).
- [ ] `corpus_forge/admin/debug.py` — chunk content + metadata +
      embedding-summary + neighbours + feedback.
- [ ] `corpus_forge/admin/config_edit.py` — `tomlkit`-based
      round-trip edit with Pydantic validation gate.
- [ ] CLI registration in `corpus_forge/cli.py`:
      `logs tail`, `stats`, `debug`, `config edit`.
- [ ] Tests:
  - [ ] `tests/unit/test_admin_logs.py` — tail an existing fixture
        log; follow against a writer fixture (use a poller, cap the
        test to 2s).
  - [ ] `tests/unit/test_admin_stats.py` — fixture backend with
        known row counts → expected stats output (both human + JSON
        modes).
  - [ ] `tests/unit/test_admin_debug.py` — fixture chunk → expected
        sections present in output.
  - [ ] `tests/unit/test_admin_config_edit.py` — bad TOML rejected,
        good TOML accepted, comments preserved through round-trip.
- [ ] CHANGELOG entry per verb (or one combined entry under
      `### Added — developer UX`).

## Verification

- `corpus-forge logs tail --follow` against a live ingest run prints
  events as they happen; `Ctrl-C` exits cleanly.
- `corpus-forge stats` against a real corpus prints a table; piping
  to `jq` works (`corpus-forge stats --json | jq '.datasets[].rows_chunks'`).
- `corpus-forge debug <chunk_id>` prints all expected sections;
  `--json` round-trips through `jq`.
- `corpus-forge config edit` opens `$EDITOR`; editing in a syntax
  error refuses to save and reports the Pydantic validation message;
  editing in a valid change persists with comments intact.

## References

- Existing log infrastructure: `corpus_forge/logging_config.py`
  (rotating-file handler, platformdirs path),
  `corpus_forge/diagnostics/logs.py` (ring buffer).
- Estimate sizing model: `corpus_forge/estimate.py`.
- Existing admin verb pattern: `corpus_forge/admin/ignore.py`,
  `corpus_forge/admin/embedder.py`.
- Config: `corpus_forge/config.py::Config.model_validate`,
  `tomlkit>=0.13` in `pyproject.toml:67`.
- MCP tool registry pattern: `corpus_forge/mcp/server.py`.
