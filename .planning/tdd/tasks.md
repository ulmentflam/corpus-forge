# TDD Task Board — feat/llama-cpp-runtime-n-ctx-seq

_Owner: tdd-principal. Workers: read freely. Edit only your claimed row's `status` and `claimed_by`._

Worktree: `/Users/evanowen/dev/cf-worktrees/feat-llama-cpp-runtime-n-ctx-seq`
Branch: `feat/llama-cpp-runtime-n-ctx-seq` (off `main` @ 99bdfb0)

## Project gates
- format: `uv run ruff format --check corpus_forge tests`
- lint: `uv run ruff check corpus_forge tests`
- typecheck: `uv run pyrefly check`
- test (focused): `uv run pytest tests/unit/test_embedder_llama_cpp.py tests/unit/test_embedder_config_llama_cpp.py tests/unit/test_embedder_register_from_config.py -v`
- test (full unit, regression gate): `uv run pytest tests/unit -q` (same shape as PR #79 baseline — only pre-existing optional-extra ModuleNotFoundError failures permitted)
- coverage-min: existing baseline (no new gate)
- smoke: n/a (test ingest already verified by user — 50 chunks nomic-embed-code → 50 vectors)

## Background

PR #79 (commit 99bdfb0) added per-sequence truncation via `n_ctx_seq = self.n_ctx // max(self.n_seq_max, 1)`. Problem: llama-cpp-python's `embedding=True` initialiser overrides `n_seq_max` post-construction to ~32, so the configured value is a lie. Decoder accepted ~256 tokens; truncation sliced to 8192 → `decode: failed to find a memory slot for batch of size N`. Fix: introspect the *actual* runtime `n_ctx` / `n_seq_max` via `llama_cpp.llama_n_ctx(ctx)` / `llama_cpp.llama_n_seq_max(ctx)` against `self._llama._ctx.ctx`.

## Tasks
| id | title | depends_on | surface | risk | status | claimed_by | notes |
|----|-------|------------|---------|------|--------|------------|-------|
| T1 | Runtime n_ctx_seq introspection + once-per-instance log | — | corpus_forge/embedders/llama_cpp.py, tests/unit/test_embedder_llama_cpp.py | med | done | principal-as-worker | OVERRIDE: no Agent tool in session, principal executed loop directly. RED→GREEN→QA all complete. |

## Acceptance details

### T1 — Runtime n_ctx_seq introspection

**Code change** (in `corpus_forge/embedders/llama_cpp.py` `encode()`):

Replace:
```python
n_ctx_seq = self.n_ctx // max(self.n_seq_max, 1)
```

With:
```python
import llama_cpp as _lcpp
try:
    _ctx_ptr = self._llama._ctx.ctx
    _runtime_n_ctx = int(_lcpp.llama_n_ctx(_ctx_ptr))
    _runtime_n_seq_max = int(_lcpp.llama_n_seq_max(_ctx_ptr))
    n_ctx_seq = max(_runtime_n_ctx // max(_runtime_n_seq_max, 1) - 4, 64)
except (AttributeError, TypeError):
    n_ctx_seq = self.n_ctx // max(self.n_seq_max, 1)
```

Plus a **once-per-instance** INFO log line containing the greppable phrase `"LlamaCppEmbedder runtime n_ctx_seq"`, payload includes runtime `(n_ctx, n_seq_max, n_ctx_seq)` AND the configured `(n_ctx, n_seq_max)`. Track "already logged" state on the instance (e.g. `self._runtime_logged: bool` initialised in `__init__`).

**Acceptance bullets**:
- `4` is the safety margin for BOS/EOS/pooling — do not change it.
- `64` is the floor — protects against pathological zero from the bindings.
- Fallback path covers `_ctx is None`, `_ctx.ctx` missing, or `llama_n_ctx`/`llama_n_seq_max` unbound on older bindings.
- Log fires exactly once per `LlamaCppEmbedder` instance — encoding twice on the same instance produces exactly one `"LlamaCppEmbedder runtime n_ctx_seq"` line.
- Existing PR #79 config keys (`n_seq_max`, `n_batch`, `n_ubatch`) remain. They feed the fallback path and stay valid knobs for older binding versions.

**Tests** (new, in `tests/unit/test_embedder_llama_cpp.py`):
1. **Runtime lookup happy path**: patch `llama_cpp.llama_n_ctx` + `llama_cpp.llama_n_seq_max` to return `(8192, 32)`; mock `self._llama._ctx.ctx` to a sentinel. Assert `_maybe_truncate` is called with `n_ctx_seq == 252` (= `8192 // 32 - 4`). Spy via `patch.object(LlamaCppEmbedder, "_maybe_truncate", ...)` to capture the arg.
2. **Fallback path (no `_ctx.ctx`)**: fake `self._llama` whose `_ctx` is `None` (or lacks `.ctx`) → assert truncation uses `self.n_ctx // max(self.n_seq_max, 1)`. Use config values `n_ctx=512, n_seq_max=1` → fallback yields `512`.
3. **Floor guard**: bindings return `(0, 32)` → `n_ctx_seq == 64`, not `-4`.
4. **Single-log-per-instance**: capture logger (`caplog` on `corpus_forge.embedders.loader` at INFO) — call `encode()` twice on the same instance → exactly one `"LlamaCppEmbedder runtime n_ctx_seq"` line.
5. **No regression**: existing PR #79 tests for `n_seq_max` / `n_batch` / `n_ubatch` config keys still pass unchanged.

**Don'ts** (from user brief):
- Don't remove `n_seq_max` / `n_batch` / `n_ubatch` config keys.
- Don't change the GGUF resolver, warmup path, or `encode_query` delegation.
- Don't add `pytest.importorskip("llama_cpp")` to existing non-smoke tests — new tests mock bindings.
- Don't bump the safety margin from 4.
- Don't touch the user's `~/.config/corpus-forge/config.toml`.

## DAG
- Wave 0: T1 (tester → coder → qa, serial)

## Summary

- **Files changed**:
  - `corpus_forge/embedders/llama_cpp.py` — added `_runtime_logged` latch in `__init__`; replaced `n_ctx_seq = self.n_ctx // max(self.n_seq_max, 1)` in `encode()` with runtime C-bindings introspection (`llama_cpp.llama_n_ctx` / `llama_n_seq_max` against `self._llama._ctx.ctx`); safety margin `-4`, floor `64`; once-per-instance INFO log line `"LlamaCppEmbedder runtime n_ctx_seq"`.
  - `tests/unit/test_embedder_llama_cpp.py` — appended `TestRuntimeNCtxSeqIntrospection` (4 cases: happy path, no-_ctx fallback, zero-runtime floor, once-per-instance log).
- **Spec deviation (documented)**: widened exception tuple from `(AttributeError, TypeError)` to `(AttributeError, TypeError, ImportError)`. `llama_cpp` is an optional extra; existing tests inject a fake `self._llama` without the import being available. Without `ImportError` in the catch, the new `import llama_cpp as _lcpp` inside `encode()` crashes every test that pre-attaches a fake handle (the entire `TestEncode`, `TestTruncation`, `TestWarmup`, `TestEncodeQuery` classes). The widened catch preserves the user-spec intent — fall back to the configured-value math when the runtime path isn't available — and is the only honest way to keep the existing 87+ tests green on a minimal install.
- **Gates run**:
  - format: `ruff format --check` → 778 files already formatted.
  - lint: `ruff check` → All checks passed.
  - typecheck: `pyrefly check` → 40 errors, all `missing-import` on optional extras (`mcp`, etc.) — the Makefile gate explicitly tolerates this class.
  - focused: 105 passed, 2 smoke skipped.
  - full unit regression: 5485 passed / 166 failed / 41 skipped / 1 xfailed / 35 errors. Identical shape to PR #79 baseline (5481 passed / 166 failed); +4 passes = the new tests. Failures are all pre-existing optional-extra ModuleNotFoundError.
- **Smoke**: n/a in TDD loop — user already verified live (50 chunks nomic-embed-code → 50 vectors, no crashes).
- **Worktree**: `/Users/evanowen/dev/cf-worktrees/feat-llama-cpp-runtime-n-ctx-seq`
- **Branch**: `feat/llama-cpp-runtime-n-ctx-seq` (off `main` @ 99bdfb0)
- **Commit status**: changes staged on worktree branch, not committed (orchestrator policy: principal hands back to user for commit/PR; iCloud / SSH-signing concerns documented in memory).
