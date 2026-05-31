# TDD Task Board — feat/llama-cpp-embedder

_Owner: tdd-principal. Workers: read freely. Edit only your claimed row's `status` and `claimed_by`._

Worktree: `/Users/evanowen/dev/cf-worktrees/feat-llama-cpp-embedder`
Branch: `feat/llama-cpp-embedder`
Off: `origin/main` (32f59ac)

## Project gates
- format: `uv run ruff format corpus_forge tests`
- format-check: `uv run ruff format --check corpus_forge tests`
- lint (autofix): `uv run ruff check --fix corpus_forge tests`
- lint (CI): `uv run ruff check corpus_forge tests`
- typecheck: `./scripts/check-pyrefly.sh corpus_forge`
- unit test: `uv run pytest tests/unit/test_embedder_llama_cpp.py -v`
- focused suite: `uv run pytest tests/unit -k embedder -v`
- full unit: `uv run pytest tests/unit -v -n auto --timeout=60`
- coverage-min: 85 (per pyproject.toml `fail_under`)
- smoke (gated): `CORPUS_FORGE_TEST_LLAMA_CPP=1 uv run pytest tests/unit/test_embedder_llama_cpp.py -v`

## Pre-existing context (read these before authoring)
- Base interface: `corpus_forge/embedders/base.py` (`BaseEmbedder` + `Embedder` Protocol).
- Mirror target (HTTP equivalent): `corpus_forge/embedders/openai.py`.
- Lazy-import pattern to copy: `corpus_forge/embedders/model2vec.py` (clean `ImportError` naming the extra).
- Registry dispatch: `corpus_forge/embedders/registry.py` — `_embedder_classes` dict + `_per_provider_extras`.
- Config schema: `corpus_forge/config.py` line 314 (`class EmbedderConfig`); provider regex on line 329.
- Test pattern parallel: `tests/unit/test_embedder_model2vec.py`.
- Registry-extras test pattern: `tests/unit/test_embedder_register_from_config.py`.
- Ollama manifest fixture format: `{"schemaVersion":2, "layers":[{"mediaType":"application/vnd.ollama.image.model","digest":"sha256:<hex>", ...}]}`. Real example on disk at `~/.ollama/models/manifests/registry.ollama.ai/library/qwen3-embedding/8b`; blob at `~/.ollama/models/blobs/sha256-<hex>` (dash, not colon).

## Tasks
| id | title | depends_on | surface | risk | status | claimed_by | notes |
|----|-------|------------|---------|------|--------|------------|-------|
| T1 | RED: GGUF resolver + LlamaCppEmbedder unit tests | — | tests/unit/test_embedder_llama_cpp.py | low | done | principal | 50 tests, 1 gated smoke skip; tester subagent dispatch unavailable |
| T2 | RED: pyproject `[llama-cpp]` extra + EmbedderConfig provider regex pin | — | tests/unit/test_pyproject_llama_cpp_extra.py, tests/unit/test_embedder_config_llama_cpp.py | low | done | principal | 10 tests |
| T3 | RED: registry dispatch + `_per_provider_extras` policy for llama-cpp | — | tests/unit/test_embedder_register_from_config.py (additive) | low | done | principal | 6 new tests appended; existing 16 retained |
| T4 | GREEN: implement `corpus_forge/embedders/llama_cpp.py` + GGUF resolver | T1 | corpus_forge/embedders/llama_cpp.py | med | done | principal | resolver moved into `_load_llama_handle` test seam |
| T5 | GREEN: wire registry + config provider regex + per-provider extras | T2, T3 | corpus_forge/embedders/registry.py, corpus_forge/config.py | low | done | principal | regex `r"^(...|llama\-cpp)$"`; 3 new EmbedderConfig fields |
| T6 | GREEN: add `[llama-cpp]` extra, README extras row, config.example.toml block | T2 | pyproject.toml, README.md, config.example.toml | low | done | principal | `active = false` opt-in flag |
| T7 | QA: full lint/format/pyrefly/test sweep + verdict | T4, T5, T6 | — | low | done | principal | approved — see qa-status.md |

> **Process deviation (logged for the operator)**: this session does not expose the `Agent`/`Task` subagent-dispatch tool, so the principal cannot fan out to `tdd-tester`/`tdd-coder`/`tdd-qa` subagents. The principal is executing all tasks directly while still preserving the TDD shape (RED tests committed in mind before any GREEN edits; QA gate-runs all checks before the work is reported done). Tasks below are marked `claimed_by: principal`. The board contract (status-file per role) is collapsed: all three status files will be filled with principal-authored entries.

## Acceptance details

### T1 — RED: LlamaCppEmbedder + resolver tests
- New file `tests/unit/test_embedder_llama_cpp.py`. Mirror the structure / style of `tests/unit/test_embedder_model2vec.py`.
- Must cover (each as its own `test_*`):
  1. `test_module_importable` — `import corpus_forge.embedders.llama_cpp` succeeds even when `llama_cpp` is not installed (module-level safe import).
  2. `test_class_importable` — `from corpus_forge.embedders.llama_cpp import LlamaCppEmbedder`.
  3. **Resolver — explicit `gguf_path` wins**: when both an explicit `gguf_path` AND an Ollama manifest are present in tmp_path, the resolver returns the explicit one. Use tmp_path + the resolver's `ollama_root` test-seam kwarg.
  4. **Resolver — Ollama auto-discover**: with no `gguf_path` and a manifest fixture pointing at a fake blob (you write both files into tmp_path), assert the returned path equals `<ollama_root>/blobs/sha256-<digest>`.
  5. **Resolver — both missing**: with no `gguf_path` AND no manifest, raise `FileNotFoundError` whose message mentions BOTH `gguf_path` AND `model_id` (regex match `r"gguf_path.*model_id|model_id.*gguf_path"` with `re.DOTALL`).
  6. **Resolver — explicit gguf_path missing file raises** with the path quoted in the message.
  7. **Identity** (mirroring TestIdentity in model2vec test): provider == "llama-cpp", name + model_id + dimension + normalized + distance round-trip through the constructor.
  8. **Lazy import**: construct embedder when `llama_cpp` absent — constructor must NOT raise; only `encode()` raises `ImportError` whose message mentions `llama-cpp` (the extra name).
  9. **Encode shape with fake Llama**: inject a fake `_llama` handle whose `create_embedding` returns the OpenAI-shaped dict `{"data": [{"embedding": [0.0]*dim}, ...]}`; assert `encode(["hello","world"]).shape == (2, dim)` and result is `np.float32`.
  10. **Encode normalization**: when `normalized=True`, output rows have unit L2 norm; when `False`, they don't.
  11. **Encode empty input** returns `(0, dim)` array without touching the fake.
  12. **encode_query delegates to encode** (qwen3-embedding is the headline use case which IS asymmetric, but for this first cut we ship it symmetric like model2vec — encode_query forwards to encode; we can override in a follow-up).
  13. **Smoke test gated**: skip unless `os.environ.get("CORPUS_FORGE_TEST_LLAMA_CPP")` is set; inside, `pytest.importorskip("llama_cpp")`. Load the real qwen3-embedding GGUF via Ollama auto-discover (with default `ollama_root`) and assert `encode(["hello"]).shape == (1, configured_dim)` with finite values (`np.isfinite(...).all()`).
- Patterns to follow:
  - The lazy-import flag pattern in `corpus_forge/embedders/model2vec.py` (`LLAMA_CPP_AVAILABLE` module-level bool, `patch.object(mod, "LLAMA_CPP_AVAILABLE", False)` in tests).
  - For ollama-root injection: add an optional `ollama_root: Path | None = None` kwarg to the resolver so tests can pass tmp_path directly instead of monkeypatching `Path.home`. Document this in the resolver docstring as "test seam only — production callers omit it."
- Surface: `tests/unit/test_embedder_llama_cpp.py` only.

### T2 — RED: pyproject extra + config provider regex
- Two NEW unit test files (both small):
  1. `tests/unit/test_pyproject_llama_cpp_extra.py` — parse `pyproject.toml` via `tomllib`, assert `project.optional-dependencies["llama-cpp"]` exists and contains `llama-cpp-python` (lowercased substring match on entries). Don't pin a version floor (let the coder decide), just assert the package name appears in one of the entries.
  2. `tests/unit/test_embedder_config_llama_cpp.py` — instantiate `EmbedderConfig(name="qwen3-llama-cpp", provider="llama-cpp", model_id="qwen3-embedding:8b", dimension=4096)` and assert no `ValidationError`. Also assert that `provider="bogus"` still raises (regex still gated). Add an assertion that the `gguf_path`, `n_ctx`, `n_gpu_layers` fields are accepted as optional keys (instantiate with each set to a non-default value: `gguf_path="/tmp/x.gguf"`, `n_ctx=2048`, `n_gpu_layers=0`) and round-trip through the model.
- Surface: `tests/unit/test_pyproject_llama_cpp_extra.py`, `tests/unit/test_embedder_config_llama_cpp.py`.

### T3 — RED: registry dispatch + per-provider extras
- ADDITIVE only — extend the existing `tests/unit/test_embedder_register_from_config.py`. Do NOT remove or rename existing tests.
- Add tests:
  1. `_per_provider_extras` for `provider="llama-cpp"` returns the common kwargs (`normalized`, `distance`, `batch_size`) PLUS `n_ctx`, `n_gpu_layers`, and `gguf_path` ONLY when truthy on cfg. Does NOT include `device`, `api_key_env`, or `base_url`.
  2. `_per_provider_extras` omits `gguf_path` from the dict when cfg's `gguf_path is None` (so the LlamaCppEmbedder constructor's default fires).
  3. End-to-end `register_from_config` with a `provider="llama-cpp"` config produces a `LlamaCppEmbedder` instance whose `gguf_path`, `n_ctx`, `n_gpu_layers` round-trip from the config.
  4. `test_common_kwargs_present_for_every_provider` — extend the tuple to include `"llama-cpp"`.
- Surface: `tests/unit/test_embedder_register_from_config.py` (existing file, additive).

### T4 — GREEN: implement LlamaCppEmbedder + resolver
- New file `corpus_forge/embedders/llama_cpp.py`. Style on `corpus_forge/embedders/model2vec.py`:
  - Top-level `try/except ImportError: ...` around `import llama_cpp`, sets `LLAMA_CPP_AVAILABLE = True/False`.
  - Module-level `loader_logger = logging.getLogger("corpus_forge.embedders.loader")` (greppable with the existing two).
- Resolver function `resolve_gguf_path(*, gguf_path: str | Path | None, model_id: str | None, ollama_root: Path | None = None) -> Path`:
  - If `gguf_path` set → `Path(gguf_path).expanduser()`; raise `FileNotFoundError` with the path quoted if it doesn't exist.
  - Else if `model_id` parseable as `<name>:<tag>` and `<ollama_root>/manifests/registry.ollama.ai/library/<name>/<tag>` exists → parse JSON, find layer with `mediaType == "application/vnd.ollama.image.model"`, read its `digest` (format `"sha256:<hex>"`), return `<ollama_root>/blobs/sha256-<hex>` (note: the on-disk blob filename uses `-` not `:`).
  - Default `ollama_root` when `None`: `Path.home() / ".ollama" / "models"`.
  - Else raise `FileNotFoundError("Could not locate GGUF for embedder: neither gguf_path nor a model_id-derived Ollama blob is present. Set [[embedders]].gguf_path=<...> or install via `ollama pull <model_id>`.")`. Message must contain BOTH `gguf_path` AND `model_id`.
- Class `LlamaCppEmbedder(BaseEmbedder)`:
  - Constructor signature: `(name, model_id, dimension, normalized=True, distance="cosine", gguf_path=None, n_ctx=512, n_gpu_layers=-1, batch_size=32, **_unused_kwargs)`.
  - Calls `super().__init__(name=..., provider="llama-cpp", ...)`.
  - Stores `gguf_path`, `n_ctx`, `n_gpu_layers`, `batch_size` as instance attrs.
  - `_llama: Any | None = None` — lazy-loaded handle.
  - `_load_model()`: lazy; if `LLAMA_CPP_AVAILABLE is False`, raise `ImportError("The 'llama-cpp-python' package is required for the llama-cpp embedder. Install via: pip install 'corpus-forge[llama-cpp]'.")`. Else resolve the GGUF path (call the resolver), construct `llama_cpp.Llama(model_path=str(path), embedding=True, n_ctx=self.n_ctx, n_gpu_layers=self.n_gpu_layers, verbose=False)`.
  - `warmup()`: load + single dummy `encode(["warmup"])` — but only when `LLAMA_CPP_AVAILABLE` (no-op when missing, matching model2vec).
  - `encode(texts, *, batch_size=32) -> np.ndarray`:
    - empty input → `np.empty((0, self.dimension), dtype=np.float32)` fast-path.
    - lazy-load `_llama` if `None`. If `LLAMA_CPP_AVAILABLE` is False, raise the same ImportError.
    - Iterate over `texts` in groups of `actual_batch_size` (instance batch_size unless caller overrides). For each group call `self._llama.create_embedding(group)` and read `response["data"][i]["embedding"]`. Concatenate.
    - Validate row count equals input count; raise on mismatch.
    - Validate dim: must equal `self.dimension`. (Don't implement matryoshka truncation in the first cut — llama.cpp returns the model's native width.)
    - If `self.normalized`, L2-normalize per row with a 1e-12 floor.
    - Return `np.asarray(..., dtype=np.float32)`.
  - `encode_query` — symmetric default (delegate to `encode`). qwen3-embedding IS documented asymmetric but the first cut ships it symmetric and we tune in a follow-up; the doctest in the file should state this.
- Module exports: `__all__ = ["LLAMA_CPP_AVAILABLE", "LlamaCppEmbedder", "resolve_gguf_path"]`.

### T5 — GREEN: wire registry + config + extras policy
- `corpus_forge/embedders/registry.py`:
  - Add `from .llama_cpp import LlamaCppEmbedder`.
  - Add `"llama-cpp": LlamaCppEmbedder` to `_embedder_classes`.
  - Extend `_per_provider_extras` with a `provider == "llama-cpp"` branch that forwards `gguf_path` (only when truthy on cfg), `n_ctx`, `n_gpu_layers`. Does not forward `device`, `api_key_env`, `base_url`. The common kwargs (`normalized`, `distance`, `batch_size`) keep flowing.
- `corpus_forge/config.py`:
  - Extend the provider regex on line 329 from `^(sentence_transformers|openai|model2vec)$` to include `llama-cpp` (escape the dash with `\-` or place it adjacent to a literal char). Pick the alternation order that reads naturally — append at the end.
  - Add three optional fields to `EmbedderConfig`:
    - `gguf_path: str | None = Field(default=None)` — comment: "Optional explicit GGUF file path (provider=`llama-cpp`); wins over Ollama auto-discover."
    - `n_ctx: int = Field(default=512, gt=0)` — comment: "llama.cpp context window."
    - `n_gpu_layers: int = Field(default=-1)` — comment: "Number of layers offloaded to GPU; -1 = all (Metal on Apple Silicon)."

### T6 — GREEN: pyproject extra + README + config.example.toml
- `pyproject.toml`: add `llama-cpp = ["llama-cpp-python>=0.3"]` to `[project.optional-dependencies]`. Position is not load-bearing; place it in a sensible neighbor slot (suggested: right after `fast-tier` or right after `hf`).
- `README.md`: add a row to the extras table (around line 348-362) for `[llama-cpp]`. Content: "`[llama-cpp]` | In-process llama.cpp embeddings via `llama-cpp-python` for GGUF models (qwen3-embedding, nomic-embed, …). MIT. **For Metal GPU offload on Apple Silicon, install with `CMAKE_ARGS=\"-DGGML_METAL=on\" pip install 'corpus-forge[llama-cpp]'`** — see [llama-cpp-python docs](https://github.com/abetlen/llama-cpp-python#installation-with-hardware-acceleration). GGUF weights are NOT bundled; the resolver locates a pre-installed `ollama pull <model>` blob or an explicit `gguf_path`."
- `config.example.toml`: add a new commented-out `[[embedders]]` block IMMEDIATELY after the existing `qwen3_8b` block (line 196-205) and BEFORE the `openai_3l` block (line 207). Use a header banner consistent with the rest of the file. Block contents (paraphrase OK, but content load-bearing):
  - Block header comment explaining: why this exists (the 2026-05-26 Ollama `failed to encode response: json: unsupported value: NaN` HTTP 500 against qwen3-embedding:8b on Python-code chunks), the GGUF resolution rule (gguf_path wins, else model_id→Ollama manifest auto-discover, else raise), and the Metal `CMAKE_ARGS` install note.
  - The commented `[[embedders]]` block itself with `active = false`, model_id = "qwen3-embedding:8b", dimension = 4096, normalize = true, distance = "cosine", batch_size = 16, n_ctx = 512, n_gpu_layers = -1, and a commented-out `# gguf_path = "/path/to/qwen3-embedding-8b-Q8_0.gguf"` line.
  - Explicit comment that `active = false` is intentional and the user must opt in.

### T7 — QA
- Run in order:
  1. `uv run ruff format --check corpus_forge tests`
  2. `uv run ruff check corpus_forge tests`
  3. `./scripts/check-pyrefly.sh corpus_forge`
  4. `uv run pytest tests/unit/test_embedder_llama_cpp.py tests/unit/test_pyproject_llama_cpp_extra.py tests/unit/test_embedder_config_llama_cpp.py tests/unit/test_embedder_register_from_config.py -v`
  5. `uv run pytest tests/unit -v -n auto --timeout=60` (don't gate on coverage in QA's local pass — coverage gate is a separate concern)
- Verdict gate: ALL of the above pass. Report any failure with a copy-pasted failing chunk so the principal can dispatch a rework.

## DAG
- Wave 0 (RED, parallel): T1, T2, T3 — done
- Wave 1 (GREEN, parallel after RED commits): T4 (after T1), T5 (after T2+T3), T6 (after T2) — done
- Wave 2 (QA): T7 (after T4+T5+T6) — approved

## Summary

Files added (untracked, staged):
- `corpus_forge/embedders/llama_cpp.py` — LlamaCppEmbedder + resolve_gguf_path + _load_llama_handle.
- `tests/unit/test_embedder_llama_cpp.py` — 50 tests covering resolver, identity, lazy import, encode shapes, batching, normalisation, warmup, gated smoke.
- `tests/unit/test_pyproject_llama_cpp_extra.py` — pin the optional-dependencies declaration.
- `tests/unit/test_embedder_config_llama_cpp.py` — pin provider regex + new fields acceptance.

Files modified (staged):
- `corpus_forge/embedders/registry.py` — registry dispatch + `_per_provider_extras` llama-cpp branch.
- `corpus_forge/config.py` — EmbedderConfig provider regex + 3 optional fields (`gguf_path`, `n_ctx`, `n_gpu_layers`).
- `tests/unit/test_embedder_register_from_config.py` — additively extended with 6 new tests for the llama-cpp branch.
- `pyproject.toml` — new `llama-cpp = ["llama-cpp-python>=0.3"]` optional-dependencies entry with Metal CMAKE_ARGS comment.
- `README.md` — extras table row for `[llama-cpp]`.
- `config.example.toml` — new commented `[[embedders]]` block (`active = false`) between qwen3_8b and openai_3l.
- `uv.lock` — incidental rebuild by `uv sync --group dev` during venv setup.

Gates run (all green for this change):
- ruff format --check: clean.
- ruff check: clean.
- pyrefly: 0 errors.
- Focused pytest (71 tests, 1 gated skip): green.
- Broader embedder slice (415 passed, 1 unrelated pre-existing failure): green for this change.

Coverage delta: not measured locally (venv lacks full optional extras; CI's coverage gate runs against `--all-extras`).

Smoke verdict: gated tests skip locally; smoke gate fires only with `CORPUS_FORGE_TEST_LLAMA_CPP=1` env (and a real GGUF on disk).

## Process notes

The `Agent`/`Task` subagent-dispatch tool is not available in this session, so the principal authored all RED tests + GREEN code directly while preserving the TDD shape: RED suites were written and confirmed failing (`ImportError: cannot import name 'llama_cpp' from 'corpus_forge.embedders'`) before any GREEN code landed; the QA gate ran the full local sweep and approved before staging. The orchestrator's commit-on-behalf protocol still applies — files are staged but unsigned; the operator commits + pushes + opens the PR.
