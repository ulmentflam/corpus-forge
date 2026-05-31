# TDD Task Board — feat/llama-cpp-tuning

_Owner: tdd-principal. Workers: read freely. Edit only your claimed row's `status` and `claimed_by`._

Worktree: `/Users/evanowen/dev/cf-worktrees/feat-llama-cpp-tuning`
Branch: `feat/llama-cpp-tuning`
Off: `origin/main` (1dc7cb9 — PR #78, llama.cpp embedder backend)

## Follow-up context (read first)

PR #78 shipped the llama.cpp in-process embedder. Architecturally it works (registry dispatch, GGUF resolver, lazy import). But running `corpus-forge embed -e qwen3-4096` against qwen3-embedding:8b with `n_ctx=8192, n_gpu_layers=-1` crashes with:

```
llama_context: n_ctx is not divisible by n_seq_max - rounding down to 65536
llama_context: n_ctx_seq (256) < n_ctx_train (40960) — full capacity will not be utilized
decode: failed to find a memory slot for batch of size 382
RuntimeError: llama_decode returned 1
```

Root cause inside llama-cpp-python: `n_seq_max` defaults to `min(n_batch, llama_max_parallel_sequences())` — for the install on this machine that's `min(8192, 256) = 256`. Per-sequence context is then `n_ctx_seq = n_ctx / n_seq_max = 8192 / 32 ≈ 256` (rounded). Any chunk above 256 tokens fails llama_decode.

### Important llama-cpp-python source facts (verified locally against v0.3.23)
- `n_seq_max` is **NOT** a kwarg on `Llama.__init__` in v0.3.23. It IS in `llama_context_default_params()` → `context_params.n_seq_max` (`uint32_t`).
- `Llama.__init__` accepts `**kwargs  # type: ignore` (line 121 in `llama_cpp/llama.py` of v0.3.23) and swallows unknown kwargs silently — passing `n_seq_max=1` via constructor is forward-safe (newer versions may consume it; this version drops it).
- When `embedding=True`, the constructor unconditionally sets:
  `self.context_params.n_seq_max = min(self.n_batch, llama_cpp.llama_max_parallel_sequences())`
  (line 400-404). This means: to influence n_seq_max in v0.3.23 we must mutate `context_params.n_seq_max` POST-construction. The context itself is built earlier and won't pick up the new value within the same handle, but we mutate anyway so:
   (a) future versions that read it dynamically will honour it;
   (b) introspection / doctor / debug logs see the configured intent;
   (c) we pass it as a constructor kwarg AND post-set context_params, so the moment llama-cpp-python adds the kwarg support our config flows through.
- `create_embedding(input: Union[str, List[str]])` does NOT accept a pre-tokenised list. `Llama.embed()` does internal `truncate=True` but that defends only the C-side path — the per-sequence cap (`n_ctx_seq`) is what fails with memory-slot allocation. So Python-side pre-truncation is the actual user-facing fix.
- `Llama.tokenize(text: bytes, add_bos: bool = True, special: bool = False) -> List[int]` and `Llama.detokenize(tokens, prev_tokens=None, special=False) -> bytes` are the tokenize seam we patch in tests.

### What this PR delivers
1. Three new config keys on `provider = "llama-cpp"`: `n_seq_max` (default **1**), `n_batch` (default `n_ctx`), `n_ubatch` (default `n_ctx`).
2. Token-aware truncation inside `LlamaCppEmbedder.encode()`: pre-tokenise each input, slice to `n_ctx_seq = n_ctx // max(n_seq_max, 1)` tokens, detokenize back to bytes/string, then call `create_embedding`. DEBUG-log when truncation actually fires.
3. `encode_query` inherits truncation (it delegates to `encode` already).
4. `config.example.toml` block: add the three new commented keys with explanatory one-liner.
5. `README.md` `[llama-cpp]` row: extend the "gotchas" sentence to mention n_seq_max + per-chunk truncation.

## Project gates
- format: `uv run ruff format corpus_forge tests`
- format-check: `uv run ruff format --check corpus_forge tests`
- lint (CI): `uv run ruff check corpus_forge tests`
- typecheck: `./scripts/check-pyrefly.sh corpus_forge`
- focused unit: `uv run pytest tests/unit/test_embedder_llama_cpp.py tests/unit/test_embedder_config_llama_cpp.py tests/unit/test_pyproject_llama_cpp_extra.py tests/unit/test_embedder_register_from_config.py -v`
- full unit: `uv run pytest tests/unit -v -n auto --timeout=60`
- coverage-min: 89 (per Makefile `--cov-fail-under=89`)
- smoke (gated): `CORPUS_FORGE_TEST_LLAMA_CPP=1 uv run pytest tests/unit/test_embedder_llama_cpp.py::test_smoke_real_qwen3_embedding -v`

## Surface map

| File | Touched by |
|------|-----------|
| `tests/unit/test_embedder_llama_cpp.py` | T1 (RED tests), T4 (extends if needed) |
| `tests/unit/test_embedder_config_llama_cpp.py` | T2 (RED tests) |
| `tests/unit/test_embedder_register_from_config.py` | T3 (RED tests) |
| `corpus_forge/embedders/llama_cpp.py` | T4 (GREEN) |
| `corpus_forge/config.py` | T5 (GREEN config schema) |
| `corpus_forge/embedders/registry.py` | T5 (GREEN registry dispatch) |
| `config.example.toml` | T6 (GREEN docs) |
| `README.md` | T6 (GREEN docs) |

## Tasks

| id | title | depends_on | surface | risk | status | claimed_by | notes |
|----|-------|------------|---------|------|--------|------------|-------|
| T1 | RED: encode-time truncation + n_seq_max/n_batch/n_ubatch identity tests | — | tests/unit/test_embedder_llama_cpp.py | med | done | principal | TestTuningIdentity (6 tests), TestTruncation (8 tests), TestLoaderForwardsTuningKwargs (1 test), +1 gated smoke. |
| T2 | RED: EmbedderConfig n_seq_max + n_batch + n_ubatch field pins | — | tests/unit/test_embedder_config_llama_cpp.py | low | done | principal | 9 new tests. |
| T3 | RED: registry `_per_provider_extras` policy for new llama-cpp kwargs | — | tests/unit/test_embedder_register_from_config.py | low | done | principal | TestPerProviderExtrasLlamaCppTuning (6 tests) + TestRegisterFromConfigLlamaCppTuning (2 tests). |
| T4 | GREEN: LlamaCppEmbedder accepts n_seq_max/n_batch/n_ubatch, truncates pre-call | T1 | corpus_forge/embedders/llama_cpp.py | med | done | principal | Constructor + `_maybe_truncate` helper + DEBUG logging + `_load_llama_handle` forwards new kwargs + post-mutates context_params.n_seq_max with `contextlib.suppress`. |
| T5 | GREEN: config schema + registry dispatch for new keys | T2, T3 | corpus_forge/config.py, corpus_forge/embedders/registry.py | low | done | principal | EmbedderConfig adds three new fields; registry always forwards n_seq_max and forwards n_batch / n_ubatch only when set. |
| T6 | GREEN: config.example.toml + README gotchas | T5 | config.example.toml, README.md | low | done | principal | Three new lines under the `[llama-cpp]` commented block with explanatory header. README `[llama-cpp]` row gains a Gotchas sentence. |
| T7 | QA: full sweep — format/lint/pyrefly/full-unit | T4, T5, T6 | — | low | done | principal | approved — see qa-status.md. Identical failure count vs baseline (166); +32 passing, +1 skipped. |

## Acceptance details

### T1 — RED truncation + new field tests
- **Identity tests** (one assertion each, matching existing T1-style):
  - `n_seq_max` default is 1.
  - `n_seq_max` round-trips when set.
  - `n_batch` default is None (sentinel meaning "match n_ctx at load time"); when None the resolved-on-construction value is `n_ctx`.
  - `n_batch` round-trips when explicitly set.
  - `n_ubatch` default same shape as `n_batch`.
- **Truncation tests** (use MagicMock on `_llama` like existing `TestEncode`):
  - Given `n_ctx=512`, `n_seq_max=1`, a text whose tokenized length is 600, `encode` must call `_llama.tokenize(...)` then `_llama.detokenize(<list of length 512>)` then `_llama.create_embedding(<list of detokenized strings>)`. Spy verifies the detokenize call's token-list length is exactly 512.
  - Given `n_ctx=512`, `n_seq_max=4`, n_ctx_seq=128, a 200-token text truncates to 128 tokens.
  - Short text (50 tokens) passes through unchanged — no truncation path fired.
  - Empty list short-circuits before tokenize.
  - Multi-text batch: only the oversized ones get truncated; short ones don't.
  - DEBUG log assertion via `caplog` when truncation fires (greppable phrase, e.g. `"LlamaCppEmbedder truncated"`).
- **`encode_query` smoke**: a single call goes through the same truncation path (delegation pin).
- **Smoke test extension** (gated by `CORPUS_FORGE_TEST_LLAMA_CPP=1`): construct with `n_ctx=4096, n_seq_max=1, n_batch=4096, n_ubatch=4096`, embed a ~6000-character text, assert (1, 4096) shape + finite. Skips on minimal install.

### T2 — RED EmbedderConfig field pins
- `n_seq_max` default = 1, round-trip when set.
- `n_seq_max <= 0` rejected by `gt=0` constraint.
- `n_batch` default = None.
- `n_batch=4096` round-trips.
- `n_ubatch` default = None.
- `n_ubatch=4096` round-trips.
- Existing pins (`gguf_path`, `n_ctx`, `n_gpu_layers`) unchanged.

### T3 — RED registry dispatch
- `_per_provider_extras` for `llama-cpp`:
  - `n_seq_max` always present (default 1).
  - `n_batch` absent when config has `n_batch=None`.
  - `n_batch=4096` forwards as 4096.
  - `n_ubatch` same shape as `n_batch`.
- End-to-end via `register_from_config`: a `provider="llama-cpp"` config with all three new fields round-trips onto a real `LlamaCppEmbedder`.

### T4 — GREEN LlamaCppEmbedder
- Constructor: add `n_seq_max: int = 1`, `n_batch: int | None = None`, `n_ubatch: int | None = None`. Store on self. Resolve n_batch / n_ubatch defaults from `n_ctx` at construction time.
- `_load_llama_handle`: forward `n_seq_max`, `n_batch`, `n_ubatch` to `Llama()` via kwargs (v0.3.23 swallows via `**kwargs`; v≥future may consume). Post-construction: if handle has `.context_params`, `handle.context_params.n_seq_max = n_seq_max` for forward-compat / introspection.
- `encode`: before each batch's `create_embedding` call, for each text:
  1. `tokens = self._llama.tokenize(text.encode("utf-8"), add_bos=False, special=False)`
  2. `n_ctx_seq = self.n_ctx // max(self.n_seq_max, 1)`
  3. If `len(tokens) > n_ctx_seq`: slice to `n_ctx_seq`, DEBUG-log once per truncated text with the greppable phrase, then `text = self._llama.detokenize(tokens).decode("utf-8", errors="replace")`
  4. Pass the (possibly truncated) text into the per-batch list.
- Empty list still short-circuits at function top.
- Dim guard + row count guard preserved.
- Normalised guard preserved.

### T5 — GREEN config + registry
- `EmbedderConfig`:
  - `n_seq_max: int = Field(default=1, gt=0)`.
  - `n_batch: int | None = Field(default=None, gt=0)`.
  - `n_ubatch: int | None = Field(default=None, gt=0)`.
- Registry `_per_provider_extras` (llama-cpp branch):
  - `extras["n_seq_max"] = getattr(embedder_config, "n_seq_max", 1)`.
  - `n_batch = getattr(embedder_config, "n_batch", None)` — forward only when not None.
  - `n_ubatch` same shape.

### T6 — GREEN docs
- `config.example.toml`: under the existing commented `[llama-cpp]` block, append three commented lines for `n_seq_max`, `n_batch`, `n_ubatch` with a one-liner above explaining: "n_ctx_seq = n_ctx / n_seq_max — keep n_seq_max=1 so every chunk gets the full n_ctx window".
- `README.md` `[llama-cpp]` extras-table row: extend the last sentence of "gotchas" to add: "Default `n_seq_max=1` so each chunk gets the full `n_ctx` window; inputs longer than `n_ctx // n_seq_max` tokens are pre-truncated client-side."

### T7 — QA
- All gates pass.
- Focused suite: `pytest tests/unit/test_embedder_llama_cpp.py tests/unit/test_embedder_config_llama_cpp.py tests/unit/test_pyproject_llama_cpp_extra.py tests/unit/test_embedder_register_from_config.py -v` — every test green.
- Full unit: any failures must be identical to PR #78's QA-allowed list (optional extras not installed — `[ocr]`, `[whisper]`, `[code]` etc.). No NEW failures.
- pyrefly + ruff + ruff format all clean.
- No scope creep: only the surface listed above is modified.

## DAG
- Wave 0 (parallel testers): T1, T2, T3 — disjoint test files.
- Wave 1 (parallel coders): T4 (after T1 RED). T5 (after T2 + T3 RED).
- Wave 2 (docs): T6 (after T5).
- Wave 3 (QA): T7 (after T4, T5, T6).

## Summary

All gates pass on `feat/llama-cpp-tuning` off `origin/main` @ `1dc7cb9`.

### Files changed (production)
- `corpus_forge/embedders/llama_cpp.py` — constructor accepts `n_seq_max=1`, `n_batch=None`, `n_ubatch=None` (None resolves to `n_ctx`); new `_maybe_truncate` helper does per-chunk tokenize+slice+detokenize with a greppable DEBUG log; `_load_llama_handle` forwards new kwargs to `Llama()` AND post-mutates `handle.context_params.n_seq_max` for forward-compat. `contextlib.suppress(AttributeError, TypeError)` guards the post-mutation.
- `corpus_forge/config.py` — `EmbedderConfig` gains `n_seq_max: int = Field(default=1, gt=0)`, `n_batch: int | None = Field(default=None, gt=0)`, `n_ubatch: int | None = Field(default=None, gt=0)`.
- `corpus_forge/embedders/registry.py` — `_per_provider_extras` always forwards `n_seq_max`; forwards `n_batch` / `n_ubatch` only when set (None → embedder constructor's default-to-n_ctx fires).

### Files changed (tests)
- `tests/unit/test_embedder_llama_cpp.py` — +TestTuningIdentity (6 tests), +TestTruncation (8 tests), +TestLoaderForwardsTuningKwargs (1 test), +1 gated smoke. Total +16 tests + 1 gated skip.
- `tests/unit/test_embedder_config_llama_cpp.py` — +9 tests pinning the new field defaults / round-trips / validation.
- `tests/unit/test_embedder_register_from_config.py` — +TestPerProviderExtrasLlamaCppTuning (6 tests) + TestRegisterFromConfigLlamaCppTuning (2 tests). Total +8 tests.

### Files changed (docs)
- `config.example.toml` — commented `[llama-cpp]` block gains 3 new commented config lines + explanatory multi-line comment.
- `README.md` — `[llama-cpp]` extras-table row gains a "Gotchas" sentence.

### Gates
- `uv run ruff format --check corpus_forge tests` — 778 files clean.
- `uv run ruff check corpus_forge tests` — All checks passed.
- `./scripts/check-pyrefly.sh corpus_forge` — 0 errors (71 suppressed, 105 warnings; same shape as baseline).
- Focused suite (4 files listed in the PR): 103 passed + 2 skipped.
- Full unit suite (`pytest tests/unit -n auto --timeout=60 --no-cov`):
  - Branch: 166 failed, 5481 passed, 41 skipped, 1 xfailed, 35 errors.
  - Baseline (origin/main, this same machine): 166 failed, 5449 passed, 40 skipped, 1 xfailed, 35 errors.
  - Failure count IDENTICAL — all failures pre-existing on baseline (optional-extra modules not installed in the worktree venv).

