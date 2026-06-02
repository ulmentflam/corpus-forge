# TDD Task Board — feat/agent-chunk-explorer

_Owner: tdd-principal. Workers: read freely. Edit only your claimed row's `status` and `claimed_by`._

Feature: Make corpus-forge faster/easier for an AI agent to explore chunks.
Four CLI commands + matching MCP tools + abs-path resolver.

## Worktree
- Path: `/Users/evanowen/dev/cf-worktrees/feat-agent-chunk-explorer`
- Branch: `feat/agent-chunk-explorer` (off `main` @ 837ed27)
- Active dev tree (for cross-reference): `/Users/evanowen/dev/corpus-forge`

## Project gates
- format: `cd /Users/evanowen/dev/cf-worktrees/feat-agent-chunk-explorer && ruff format --check .`
- lint: `cd /Users/evanowen/dev/cf-worktrees/feat-agent-chunk-explorer && ruff check .`
- typecheck: `cd /Users/evanowen/dev/cf-worktrees/feat-agent-chunk-explorer && ./scripts/check-pyrefly.sh corpus_forge`
- test (per-task scope): use `/Users/evanowen/dev/corpus-forge/.venv/bin/pytest` with `PYTHONPATH=/Users/evanowen/dev/cf-worktrees/feat-agent-chunk-explorer`
- smoke: `tests/smoke/test_chunk_doc_reassemble.py`

## Tasks
| id | title | depends_on | surface | risk | status | claimed_by | notes |
|----|-------|------------|---------|------|--------|------------|-------|
| T1 | abs-path resolver (`source_uri` -> Path) | — | corpus_forge/sources/path_resolve.py, tests/unit/test_sources_path_resolve.py | low | done | principal | 17/17 green |
| T2 | Backend chunk navigation APIs (get_chunk enrich, get_chunk_neighbors, get_document_chunks) | — | corpus_forge/backends/base.py, corpus_forge/backends/sqlite.py, corpus_forge/backends/postgres.py, tests/unit/test_backend_chunk_navigation.py | med | done | principal | 17/17 green + 1 regression fix |
| T3 | CLI `chunk show/neighbors/doc` subcommands | T1, T2 | corpus_forge/cli.py, tests/unit/test_cli_chunk_commands.py | med | done | principal | 10/10 green |
| T4 | CLI `search --json` single-object on stdout | — | corpus_forge/cli.py (search fn only), tests/unit/test_cli_search_json.py | low | done | principal | 4 new + 11 existing green |
| T5 | MCP: chunk_neighbors + get_document + get_chunk enrich | T1, T2 | corpus_forge/mcp/server.py, tests/unit/test_mcp_chunk_navigation.py | med | done | principal | 7 new + 22 touched green |
| T6 | Smoke: `chunk doc --reassemble` multi-chunk fixture | T2, T3 | tests/smoke/test_chunk_doc_reassemble.py | low | done | principal | 2/2 green |

## Acceptance details

### T1 — abs-path resolver
- New module `corpus_forge/sources/path_resolve.py` with `resolve_abs_path(source_uri: str, config: Config) -> Path | None`.
- For `filesystem://<root_name>/<rel>` URIs (the actual emitted shape — see `corpus_forge/sources/filesystem.py:359` `source_uri=f"filesystem://{self.root.name}/{path.relative_to(self.root)}"`): walk `config.datasets[*].sources[*]` where `plugin == "filesystem"`, find the source whose `root` (an `ExpandedPath` -> str) has a `Path(root).name` equal to `<root_name>`, return `(Path(source.root) / rel).resolve()` (do NOT require existence on disk).
- Also handle the legacy `vault://<root_name>/<rel>` URI shape from `markdown_vault.py:67` — same resolution logic, but match against `vault_root` instead of `root`.
- Non-filesystem schemes (`claude-code://`, `http(s)://`, `chatgpt-export://`, `codex-cli://`, `gemini-cli://`, `jsonl-chat://`, `opencode://`, `zotero://`, `claude-code-history://`) → return `None`.
- Malformed URI, unknown root name, no matching source → return `None` (do not raise).
- Unit tests cover: classic filesystem root match, `vault://` root match, unknown root → None, non-filesystem scheme → None, URI with empty rel part → returns the root itself, multi-source disambiguation (two filesystem sources with different `root.name`s; resolver picks correctly).

### T2 — Backend chunk navigation APIs
- Add to `StorageBackend` (base ABC):
  - `get_chunk_neighbors(chunk_id: int, *, before: int = 1, after: int = 1) -> list[dict]` — returns neighbor chunks (NOT including the anchor), same row shape as `get_chunk`, ordered by `chunk_index ASC`.
    - Document chunks: same `document_id`, `chunk_index` in `[anchor-before, anchor+after]` excluding anchor.
    - Conversation chunks: same `conversation_id`, ordered by `(message_id, chunk_index)`, window of N before/after the anchor's flat position.
    - Anchor missing → `[]`. `before=0` or `after=0` valid.
  - `get_document_chunks(document_id: int) -> list[dict]` — all chunks of a document ordered by `chunk_index`. Empty list if none.
- Enhance `get_chunk(chunk_id)` to ALSO include `prev_chunk_id: int | None` and `next_chunk_id: int | None`. Cheap lookup of adjacent `chunk_index`. DO NOT remove or rename existing keys (additive only).
- Implement on `PostgresBackend` AND `SQLiteBackend`.
- Tests in `tests/unit/test_backend_chunk_navigation.py`. Match the SQLite-fixture pattern of `tests/unit/test_sqlite_backend.py` (likely an in-memory `SQLiteBackend(path=":memory:")`). For Postgres, mirror `tests/unit/test_postgres_backend.py` patterns. Cover:
  - 5-chunk document: `get_chunk_neighbors(c2, before=1, after=2)` returns chunks at indices 1, 3, 4 in order.
  - 3-chunk document: `get_chunk(c1)` → prev=None, next=c2.id; `get_chunk(c3)` → prev=c2.id, next=None.
  - Conversation: 2 messages × 2 chunks each → cross-message neighbor ordering.
  - Non-existent chunk_id → `get_chunk` returns None; `get_chunk_neighbors` returns [].
  - `get_document_chunks` ordering for a 3-chunk doc.

### T3 — CLI `chunk` subcommands
- Add a `chunk_app = typer.Typer()` and mount via `app.add_typer(chunk_app, name="chunk")`. (Follow the same pattern the existing `sync_app`, `export_app`, `eval_app`, `mcp_app`, `migrate_app` use — `grep -n "app.add_typer\b" corpus_forge/cli.py` to find the existing call sites.)
- `chunk show <id> [--json] [--neighbors-hint/--no-neighbors-hint]`
  - Default `--neighbors-hint`: ON for `--json`, OFF for human.
  - Human: prints text + header block with `chunk_id`, `dataset`, `source_uri`, `abs_path` (if resolvable), `document_id`, `chunk_index`, `role`, `token_count`, `heading`, `line_start`/`line_end` (from chunk.metadata).
  - `--json`: ONE JSON object on stdout. NO log chatter. Includes all the above + `prev_chunk_id` + `next_chunk_id` when hint on.
- `chunk neighbors <id> [--before N] [--after N] [--json]`
  - Defaults: `--before 1 --after 1`.
  - Human: each chunk separated by `---` with one-line header.
  - `--json`: `{"anchor_chunk_id": <id>, "before": [...], "after": [...]}`. Each entry includes `chunk_id`, `chunk_index`, `text`, `source_uri`, `abs_path`, `role`, `heading`, `token_count`.
- `chunk doc <doc_id> [--json] [--reassemble]`
  - Default human: chunks separated by `---`, header `[chunk_index=N chunk_id=M heading="..."]`.
  - `--reassemble`: concatenated chunk texts. A leading `NOTE:` line goes to **stderr** (not stdout) about overlap caveat.
  - `--json`: `{"document": {id, source_uri, abs_path, dataset, title}, "chunks": [...]}` or with `--reassemble` `{"document": {...}, "text": "..."}`.
- For `--json`, suppress agent-mode `command.start` / `result` events and any alembic/plugin INFO logging on stdout. Reference pattern: search for `doctor --json` in `corpus_forge/cli.py` (the doctor command emits a single JSON object cleanly).
- Errors: chunk_id / doc_id not found → exit code 2; `--json` emits `{"error": "...", "code": "NOT_FOUND"}` and nothing else on stdout.
- Tests in `tests/unit/test_cli_chunk_commands.py` using a mock backend (look at how `tests/unit/test_cli_search.py` builds its CliRunner harness). Assert JSON schema, ordering, exit codes; assert clean stdout in `--json` mode (no log noise).

### T4 — `search --json` clean output
- Current behavior: `--json <PATH>` writes to a file. The user observed that bare `--json` (and the agent-mode emission) mix logs with the JSON event.
- New contract: keep `--json <PATH>` working for back-compat (file output). Add a new mode where `--json -` writes the single JSON object to **stdout** with zero log chatter. Both forms emit the **same** schema:
  - `{"query": "...", "k": <int>, "took_ms": <int>, "hits": [<hit>, ...]}`
- When `--json -` is used:
  - Suppress agent-mode `command.start` / `result` events.
  - Suppress alembic INFO / plugin chatter on stdout — escalate logging to WARNING or route to stderr for the duration of the command. Reference: see how `doctor --json` keeps stdout clean.
  - `took_ms` = wall-clock of the `retriever.search(query, options)` call.
  - Exit 0 on success.
- Without `--json`: existing rank-list behavior unchanged.
- Tests in `tests/unit/test_cli_search_json.py`: invoke via CliRunner with `--json -`, parse stdout as JSON, assert no leading non-JSON lines, assert `len(hits) == k` for a mock retriever, assert exit 0.

### T5 — MCP tools
- In `corpus_forge/mcp/server.py`:
  1. Add JSON schemas `_CHUNK_NEIGHBORS_INPUT_SCHEMA` and `_GET_DOCUMENT_INPUT_SCHEMA`.
  2. Register both new tools in `_list_tools` (read-only, always-on — next to `list_datasets`).
  3. Add dispatch branches: `name == "chunk_neighbors"` → `_dispatch_chunk_neighbors`; `name == "get_document"` → `_dispatch_get_document`.
  4. Enhance the existing `_dispatch_get_chunk` to ALSO include `prev_chunk_id`, `next_chunk_id`, and `abs_path` in the returned dict. **Additive — do NOT rename existing keys.**
- Tool shapes:
  - `chunk_neighbors(chunk_id, before=1, after=1)` → `{"anchor_chunk_id": int, "before": [chunk_dict, ...], "after": [chunk_dict, ...]}`. Each chunk_dict: `chunk_id`, `chunk_index`, `text`, `source_uri`, `abs_path`, `role`, `heading`, `token_count`.
  - `get_document(document_id, reassemble=False)` → `{"document": {id, source_uri, abs_path, dataset_id, title}, "chunks": [...]}` or with `reassemble=True`, `{"document": {...}, "text": "..."}`.
- `abs_path` uses the T1 resolver. Loading `Config` should use the same pattern as `_dispatch_estimate_sync_size` (`Config.load()` with FileNotFoundError fallback to `None` for `abs_path`).
- Tests in `tests/unit/test_mcp_chunk_navigation.py`. Follow `tests/unit/test_mcp_server.py` / `test_mcp_server_enrichment.py` patterns — fake retriever + fake backend exposing `get_chunk`, `get_chunk_neighbors`, `get_document_chunks`.

### T6 — Smoke test
- `tests/smoke/test_chunk_doc_reassemble.py`.
- Build a tiny multi-chunk markdown doc in-memory (or a temp SQLite backend), pre-seeded directly with `backend.upsert_document(...)` — look at `tests/smoke/` for the fixture pattern.
- Invoke `corpus-forge chunk doc <doc_id> --reassemble --json` via CliRunner.
- Assert `text` is the concatenation of all chunk texts in `chunk_index` order.

## DAG
- Wave 0: T1, T2, T4 (disjoint surfaces, no deps)
- Wave 1: T3, T5 (need T1 + T2 done)
- Wave 2: T6 (needs T2 + T3 done)

## Summary

All 6 tasks done.

**Files staged (new + modified):**

Source:
- `corpus_forge/sources/path_resolve.py` (new — 95 LOC)
- `corpus_forge/backends/base.py` (Protocol: 2 new methods + extended get_chunk docstring)
- `corpus_forge/backends/sqlite.py` (+ get_chunk_neighbors, get_document_chunks, _chunk_prev_next_ids; get_chunk extended)
- `corpus_forge/backends/postgres.py` (same surface as sqlite)
- `corpus_forge/cli.py` (new `chunk_app` Typer subapp with show/neighbors/doc; search --json - stdout mode)
- `corpus_forge/mcp/server.py` (new chunk_neighbors + get_document tools; get_chunk dispatcher enriched)

Tests:
- `tests/unit/test_sources_path_resolve.py` (new, 17 tests)
- `tests/unit/test_backend_chunk_navigation.py` (new, 17 tests)
- `tests/unit/test_cli_chunk_commands.py` (new, 10 tests)
- `tests/unit/test_cli_search_json.py` (new, 4 tests)
- `tests/unit/test_mcp_chunk_navigation.py` (new, 7 tests)
- `tests/smoke/test_chunk_doc_reassemble.py` (new, 2 tests)
- `tests/unit/test_mcp_server.py` (tool-set assertion updated)
- `tests/unit/test_mcp_server_enrichment.py` (two tool-set assertions updated)
- `tests/smoke/test_mcp_writes_disabled_by_default.py` (tool-set updated)

Planning:
- `.planning/tdd/tasks.md` (this file)
- `.planning/tdd/test-status.md`
- `.planning/tdd/code-status.md`
- `.planning/tdd/qa-status.md`

**Gates:**
- `ruff format --check .`: clean
- `ruff check corpus_forge tests`: clean
- `./scripts/check-pyrefly.sh corpus_forge`: 0 errors
- Combined new+touched test suite: 349 passing
- Wider regression: matches main baseline (5447 passing, same 121 pre-existing failures from missing optional extras)

**Back-compat preserved:** existing `search --json <PATH>` writes a file; existing `get_chunk` MCP keeps every key; existing tests for both still green.

**Smoke verdict:** `corpus-forge chunk doc <id> --reassemble --json` round-trips through a real SQLite backend (no mocks at the storage layer).

## Notes for workers
- Worktree: `/Users/evanowen/dev/cf-worktrees/feat-agent-chunk-explorer`. Absolute paths only.
- Stage with `git add` but DO NOT commit (orchestrator commits on workers' behalf — 1Password SSH signing needs TTY).
- Don't touch `~/.config/corpus-forge/config.toml`.
- Don't break existing `search` / `get_chunk` behavior — both must remain back-compat without flags.
- The repo `.venv` lives at `/Users/evanowen/dev/corpus-forge/.venv` (Python 3.11). For tests, run from the worktree dir with PYTHONPATH set so `import corpus_forge` resolves to the worktree's source:
  `cd /Users/evanowen/dev/cf-worktrees/feat-agent-chunk-explorer && PYTHONPATH=/Users/evanowen/dev/cf-worktrees/feat-agent-chunk-explorer /Users/evanowen/dev/corpus-forge/.venv/bin/pytest tests/unit/<file> -x -q`
