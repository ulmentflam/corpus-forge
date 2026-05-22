# RFC: Propagate git + file/line provenance onto chunks

status: accepted
**Owner**: nightly (open for any agent to claim)
**Priority**: P0
**Depends on**: none

## Context

Today a chunk knows its `source_uri` and `content_hash`, but not:

- **Where on disk** it came from (file path + line range)
- **Which commit** of the repo it came from (commit SHA, branch)
- **Which subdirectory** within the source root, for grouping

This blocks two big downstream wins:

1. **Self-distillation feedback**: when an agent uses a retrieved
   chunk and the result is good/bad, we want to attach feedback to
   *the file at that commit* — so the same chunk regenerated at a
   later commit doesn't inherit stale signal.
2. **Live source navigation**: an agent reading a chunk should be one
   MCP call away from `Read(file_path, line_start..line_end)` against
   the actual repo, instead of operating on a chunk excerpt that's
   already drifted.

The `ClaudeCodeSource` (PR #29) already captures `gitBranch`, `cwd`,
`version` into `RawConversation.metadata`, but the values stop at the
conversation-level dict — they don't propagate into the per-chunk
schema, and the `FilesystemSource` doesn't capture git context at all.

## Goals

- Every chunk produced from a code/text file carries `file_path`
  (relative to source root), `line_start`, `line_end`.
- Every chunk produced from a git-tracked source carries `git_commit`
  (resolved at scan time) and `git_branch`.
- A new MCP tool `get_source_file_context(chunk_id)` returns
  `{file_path, line_start, line_end, git_commit, git_branch,
  abs_path_hint}` so an agent can open the live file at the captured
  commit.
- Schema migration is forward-only and the new columns are nullable so
  existing rows survive.

## Non-goals

- Storing the file *contents* at the captured commit (the user already
  has git; we just point at it).
- Auto-checking-out the captured commit when serving a chunk — the MCP
  tool returns the SHA, the agent decides whether to `git
  checkout`.
- Capturing git context for non-git source roots (e.g., a Zotero
  library); those simply leave the git fields null.

## Approach

### Schema

Add a new alembic revision under `corpus_forge/alembic/versions/`
adding nullable columns on the chunk table:

- `file_path TEXT NULL`
- `line_start INTEGER NULL`
- `line_end INTEGER NULL`
- `git_commit TEXT NULL`
- `git_branch TEXT NULL`

Update `corpus_forge/schema/per_embedder.sql.tmpl` (if chunks live
there in the embedder-specific path) accordingly. Pyrefly will catch
the inevitable backend method signature drift.

### Capture path

- **`corpus_forge/sources/filesystem.py`**: at source construction,
  resolve `git_commit` (via `git rev-parse HEAD` if the root is inside
  a git repo, else null) and `git_branch` (`git rev-parse
  --abbrev-ref HEAD`). Cache for the duration of the scan.
- **`corpus_forge/sources/claude_code.py`**: copy the existing
  `git_branch` from `RawConversation.metadata` down onto each
  `RawMessage.metadata` so the per-message chunker sees it.
- **All chunkers** (`corpus_forge/chunkers/{markdown,code,cdc}.py`):
  when emitting `TextChunk`, populate `metadata["file_path"]`,
  `metadata["line_start"]`, `metadata["line_end"]`. The chunker
  already tracks character offsets — line numbers fall out of a
  `text.count("\n", 0, offset) + 1` walk done once at chunk-emit
  time.
- **Backend write paths**
  (`corpus_forge/backends/{sqlite,postgres}.py`): extend
  `upsert_document`'s chunk insert to read the new metadata keys and
  populate the new columns.

### Read path / MCP

Add `get_source_file_context` to `corpus_forge/mcp/server.py`:

- Input: `chunk_id: int`.
- Output: `{file_path, line_start, line_end, git_commit, git_branch,
  source_root_hint, exists_on_disk: bool}`.
- `source_root_hint` is the source's `root` from config so the agent
  can construct an absolute path: `f"{source_root_hint}/{file_path}"`.
- `exists_on_disk` is a cheap `Path(source_root_hint /
  file_path).is_file()` check so the caller knows whether to fall back
  to the chunk text.

Wire into the `@server.list_tools()` registration table alongside
`get_chunk`.

## Tasks

- [x] Alembic migration adding the five nullable columns. Revision
      `0016_chunk_provenance` adds `file_path`, `line_start`,
      `line_end`, `git_commit`, `git_branch` to `corpus.chunks` /
      `chunks`. Postgres uses `ADD COLUMN IF NOT EXISTS`; SQLite uses
      a `PRAGMA table_info` probe — both fully idempotent. Tests in
      `tests/integration/test_alembic_0016_chunk_provenance.py`.
- [ ] Update `corpus_forge/schema/per_embedder.sql.tmpl` if chunks
      live there.
- [ ] Backend write helpers: `corpus_forge/backends/sqlite.py` and
      `postgres.py` insert/upsert paths populate the new columns from
      `TextChunk.metadata`.
- [x] Add a `git_context()` helper in `corpus_forge/sources/_git.py`
      that returns `(commit, branch)` for a given path, with a clean
      `(None, None)` fallback when `.git` is absent or `git` isn't on
      PATH. Includes detached-HEAD handling (branch=None,
      commit=SHA), file-path resolution via parent dir, `~`
      expansion, 2-second subprocess timeout.
- [ ] Wire `git_context()` into `FilesystemSource.__init__` so
      ingest captures one commit per scan, not per file.
- [ ] Chunkers populate `file_path` / `line_start` / `line_end`:
      `corpus_forge/chunkers/markdown.py`, `code.py`, `cdc.py`,
      `passthrough.py`.
- [x] `ClaudeCodeSource` copies `git_branch` from conversation
      metadata onto each `RawMessage.metadata`. Post-process fan-out
      after the parse loop; uses `setdefault` so any future per-turn
      branch override on a message survives. 4 unit tests in
      `tests/unit/test_claude_code_typed_events.py`.
- [ ] New MCP tool `get_source_file_context(chunk_id)` in
      `corpus_forge/mcp/server.py`. Register in the tool list and add
      its dispatch.
- [ ] Tests:
  - [x] `tests/unit/test_git_context.py` — resolves SHA inside a
        synthetic temp git repo; returns `(None, None)` for a non-git
        directory. 12 tests covering happy path, detached HEAD, and
        every fallback (non-git dir, missing path, missing `git`
        binary, OSError, TimeoutExpired, non-zero exit).
  - [ ] `tests/unit/test_chunker_line_numbers.py` — chunker output's
        line numbers match the source text.
  - [ ] `tests/integration/test_provenance_e2e.py` — ingest a temp
        git repo, query a chunk, assert all five columns populated.
  - [ ] `tests/unit/test_mcp_get_source_file_context.py` — round-trip
        the new MCP tool.
- [ ] CHANGELOG entry.

## Verification

- `corpus-forge migrate` applies the new revision cleanly on both
  SQLite and Postgres.
- After ingesting a small git repo, a SQL query against `chunks`
  returns non-null `file_path` / `line_start` / `line_end` /
  `git_commit` / `git_branch`.
- An MCP client calling `get_source_file_context(chunk_id)` on any
  retrieved chunk gets back the file + line range, and `Read`-ing that
  file produces text containing the chunk's content.

## References

- Schema: `corpus_forge/schema/`, `corpus_forge/alembic/versions/`.
- Sources: `corpus_forge/sources/{filesystem,claude_code}.py`.
- Chunkers: `corpus_forge/chunkers/{markdown,code,cdc,base}.py`.
- Backends: `corpus_forge/backends/{sqlite,postgres}.py::upsert_document`.
- MCP: `corpus_forge/mcp/server.py` — existing `get_chunk` tool at
  line ~760 is the shape to mirror.
- Existing per-conversation git capture: PR #29's
  `claude_code.py` lifts `gitBranch` from the first JSONL line.
