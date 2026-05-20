# tests/fixtures/external — vendored OSS code corpora

Self-contained snapshots of upstream open-source repos, used by
the Phase N retrieval-quality bench (`tests/perf/test_phase_n_bench.py`)
to broaden the signal beyond corpus-forge's own source tree.

Snapshots are produced by `build_snapshots.py` and committed as bytes.
**Do not edit files under `*-snapshot/` directories by hand** — re-run
the script to refresh.

## Current vendored corpora

### `flask-snapshot/`

- Upstream: <https://github.com/pallets/flask>
- License: BSD-3-Clause
- Pinned commit: `954f5684e4841aad84a8eec7ace7b81a0d3f6831` (committed 2026-05-18)
- Snapshot footprint: 213 files / 1,150,893 bytes
- Suffix filter: `.cfg, .html, .in, .ini, .json, .md, .py, .rst, .toml, .txt, .yaml, .yml`

To refresh:

```bash
uv run python tests/fixtures/external/build_snapshots.py
```

After refresh, re-author any ground-truth queries in
`tests/perf/data/semble_queries.jsonl` whose byte offsets have shifted
(the `tests/perf/test_semble_queries.py` rot-detector will flag
out-of-bounds offsets).
