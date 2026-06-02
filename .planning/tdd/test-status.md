# Test Status — owned by tdd-tester (feat/agent-chunk-explorer)
_Append-only per task._

## Schema per entry
```
### <task_id> @ <ISO timestamp>
verdict: red | error
red_tests:
  - path::test_name
notes: |
  ...
```

### T1 @ 2026-06-01T00:00Z
verdict: red
red_tests:
  - tests/unit/test_sources_path_resolve.py::* (16 tests)
notes: |
  Pins resolve_abs_path contract: filesystem:// + vault:// resolution against
  Config; non-fs schemes return None; malformed/unknown roots return None;
  empty-rel returns root.

### T2 @ 2026-06-01T00:00Z
verdict: red
red_tests:
  - tests/unit/test_backend_chunk_navigation.py::* (16 tests)
notes: |
  Pins get_chunk(prev_chunk_id/next_chunk_id), get_chunk_neighbors,
  get_document_chunks on the SQLite backend with in-memory DB; ABC contract
  check that both Postgres and SQLite expose the new methods.

### T4 @ 2026-06-01T00:00Z
verdict: red
red_tests:
  - tests/unit/test_cli_search_json.py::* (4 tests)
notes: |
  Pins `search --json -` stdout-clean contract (single JSON object, no log
  chatter); back-compat for `--json <PATH>` file output.
