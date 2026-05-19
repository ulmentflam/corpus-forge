# experiments/

This directory is **research-only**. Nothing under `experiments/` ships
in the corpus-forge wheel or Docker image — the wheel build target's
`packages` list excludes it, and `.dockerignore` strips the tree before
the image is built.

## What lives here

| File                  | Purpose                                                       |
|-----------------------|---------------------------------------------------------------|
| `semble_adapter.py`   | Phase M Wave 5 — research `SembleRetriever` over `SembleIndex`. |

## Hard rules

- **Do NOT** import from `experiments/` anywhere in `corpus_forge/`.
  Production code paths are forbidden from depending on spike artifacts.
  CI enforces this implicitly because `corpus_forge` is the only package
  installed by the wheel build target.
- **Do NOT** add the experiment's third-party deps to `pyproject.toml`'s
  core, `[project.optional-dependencies]`, or `[dependency-groups]`. The
  semble bench installs `semble` manually into a dedicated venv via
  `uv pip install semble` (or a pinned commit). This file is the audit
  trail for that choice.
- Test gates: any bench that needs an experimental dep is environment-
  variable-gated (e.g. `CF_SEMBLE_BENCH=1`) and skips by default in CI.

## Spike venv recipe (semble)

```bash
uv venv /tmp/semble-bench-venv --python 3.11
VIRTUAL_ENV=/tmp/semble-bench-venv uv pip install -e '.[retrieval,eval,sqlite,rerank]'
VIRTUAL_ENV=/tmp/semble-bench-venv uv pip install semble
CF_SEMBLE_BENCH=1 VIRTUAL_ENV=/tmp/semble-bench-venv \
    /tmp/semble-bench-venv/bin/python -m pytest tests/perf/test_semble_bench.py -v
```

The `corpus-forge` editable install is needed only because the bench
harness imports `corpus_forge.retrieval.HybridRetriever` for side-by-side
comparison. The `SembleRetriever` adapter itself imports `semble` lazily
inside method bodies, so this module is importable in a vanilla
corpus-forge venv (the import errors only fire if you actually try to
construct a `SembleRetriever`).

## See also

- `.planning/tdd/phase_m_wave5_semble.md` — decision doc (methodology,
  numbers, recommendation).
- `tests/perf/test_semble_bench.py` — pytest entry point.
- `tests/perf/metrics.py` — MRR@10 / Recall@5 / latency helpers (these
  ARE shipped as part of the tests tree, not under `experiments/`,
  because they are reusable beyond the spike).
