# Code Status — owned by tdd-coder (feat/agent-chunk-explorer)
_Append-only per task._

## Schema per entry
```
### <task_id> @ <ISO timestamp>
verdict: green | failed
files_touched:
  - path
gates:
  format: pass|fail
  lint: pass|fail
  typecheck: pass|fail
  tests: pass|fail (n=...)
notes: |
  ...
```

### T1 @ 2026-06-01T00:00Z
verdict: green
files_touched:
  - corpus_forge/sources/path_resolve.py (new)
gates:
  tests: pass (17/17)
notes: |
  Pure function. ~95 LOC. Resolves filesystem:// + vault://; non-fs
  schemes → None; never raises.

### T2 @ 2026-06-01T00:00Z
verdict: green
files_touched:
  - corpus_forge/backends/base.py
  - corpus_forge/backends/sqlite.py
  - corpus_forge/backends/postgres.py
gates:
  tests: pass (17/17)
notes: |
  Additive: get_chunk now carries prev_chunk_id/next_chunk_id.
  New: get_chunk_neighbors, get_document_chunks on both backends.
  Conversation chunks ordered by (message_id, chunk_index); document
  chunks by chunk_index. before=0 / after=0 supported; missing anchor
  returns [].

### T4 @ 2026-06-01T00:00Z
verdict: green
files_touched:
  - corpus_forge/cli.py (search function only)
gates:
  tests: pass (4 new + 11 existing search tests)
notes: |
  --json now accepts a string: PATH (file, back-compat with k + took_ms
  fields added) or "-" (stdout, single JSON object, no log chatter).
  Stdout mode short-circuits before agent-mode event emission.

### T3 @ 2026-06-01T00:00Z
verdict: green
files_touched:
  - corpus_forge/cli.py (added chunk_app + 3 subcommands)
gates:
  format: pass
  lint: pass
  typecheck: pass
  tests: pass (10/10)
notes: |
  `chunk show/neighbors/doc` Typer subapp mounted at `chunk`. Reuses
  the T1 resolver to surface abs_path. --json paths suppress agent-mode
  events and clamp library loggers to WARNING.

### T5 @ 2026-06-01T00:00Z
verdict: green
files_touched:
  - corpus_forge/mcp/server.py
  - tests/unit/test_mcp_server.py (tool-set assertion updated)
  - tests/unit/test_mcp_server_enrichment.py (tool-set assertions updated)
  - tests/smoke/test_mcp_writes_disabled_by_default.py (tool-set updated)
gates:
  format: pass
  lint: pass
  typecheck: pass
  tests: pass (7 new + 19 existing mcp tests + 3 enrichment)
notes: |
  Two new always-on read tools: chunk_neighbors, get_document.
  get_chunk dispatcher enhanced: prev_chunk_id, next_chunk_id, abs_path
  (additive — no existing key renamed). abs_path lookup loads Config
  lazily and never raises.

### T6 @ 2026-06-01T00:00Z
verdict: green
files_touched:
  - tests/smoke/test_chunk_doc_reassemble.py (new)
gates:
  tests: pass (2/2)
notes: |
  Smoke: builds a real 4-chunk doc in temp SQLite, invokes
  `corpus-forge chunk doc <id> --reassemble --json`, asserts text ==
  concat of chunk texts.

### post-Wave 1 hardening @ 2026-06-01T00:00Z
verdict: green
files_touched:
  - corpus_forge/backends/sqlite.py
  - corpus_forge/backends/postgres.py
notes: |
  Regression fix: `_chunk_prev_next_ids` was raising KeyError when given
  a partial mock row missing `chunk_index` (test_postgres_backend_helpers
  ::TestSearchHelpers::test_get_chunk_returns_row). Made it defensive —
  returns (None, None) on missing chunk_index. Verified: wider unit suite
  count matches `main` baseline exactly (5447 passed / 121 failed; all
  121 failures pre-existing on `main` due to missing optional extras
  in this venv).
