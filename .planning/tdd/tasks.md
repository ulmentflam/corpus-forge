# TDD Task Board — feat/embedder-routing (PR #81)

_Owner: tdd-principal. Workers: read freely. Edit only your claimed row's `status` and `claimed_by`._

Worktree: `/Users/evanowen/dev/cf-worktrees/feat-embedder-routing`
Branch: `feat/embedder-routing`
Off: `main` (99bdfb0 — PR #79, llama.cpp n_seq_max tuning landed)

## Follow-up context (read first)

PRs #78/#79/#80 landed the in-process llama.cpp embedder. The user now wants a *dual-tower* setup: `nomic-embed-text-v1.5` for text + `nomic-embed-code` (Qwen2.5-Coder-7B, Apache-2.0, 3584-d) for code. Both run as parallel dense lanes. This PR introduces **extension-based routing** so each chunk is embedded by *exactly one* of the active embedders.

### The routing rule (locked — don't bikeshed)

1. New optional `extensions: list[str]` on `[[embedders]]`. Values are lowercase, leading-dot extensions: `[".py", ".ts", ".go"]`.
   - Empty / absent → **catchall** semantics.
   - Non-empty → **specialist**: only claims chunks whose `documents.source_uri` ends with one of these (case-insensitive).
2. Routing per chunk (deterministic):
   - Iterate **active** embedders in **config declaration order**.
   - First *specialist* whose allow-list matches wins.
   - Else: first *catchall* claims it.
   - No catchall + any specialist with no fallback for some chunk → `EmbedderRoutingError` at config-validation time when the catchall is missing.
3. `corpus-forge embed -e <name>` runs only chunks claimed by `<name>` under the rule. "Pending" is now filtered by the route.
4. **Backwards-compat**: when *no* embedder declares `extensions`, every active embedder still embeds every chunk (today's behaviour — no routing rule fires because there are no specialists).

### Where routing must apply

- `corpus_forge/config.py` — new `extensions` field on `EmbedderConfig`; validation (leading dot, lowercase normalise, reject bare names) + `Config`-level invariant (specialist with no catchall → error).
- `corpus_forge/embedders/registry.py` — surface `extensions` on the loaded `Embedder` (so downstream code can read it without going back to config).
- `corpus_forge/embed.py` — filter the `chunks_missing_embedding` stream by the routing rule before encoding.
- `corpus_forge/ingest.py` — `_write_embeddings_for_chunks` runs once per active embedder per flush; apply the same per-embedder filter there.
- `config.example.toml` — append a commented dual-tower block (nomic catchall + nomic-code specialist).
- `README.md` — short subsection on dual-tower retrieval.

### Routing seam (decision — single source of truth)

Centralise the rule in **one** helper:

```python
# corpus_forge/embedders/routing.py
class EmbedderRoutingError(ValueError): ...

def extension_for(source_uri: str) -> str: ...
    """'.../foo.PY' → '.py'; URIs with no extension → ''."""

def route_for(extension: str, active_embedders: Sequence[Embedder]) -> Embedder | None: ...
    """First specialist match (declaration order), else first catchall, else None."""

def claims(embedder: Embedder, source_uri: str) -> bool: ...
    """True when `embedder` is the one `route_for` picks for `source_uri`."""

def validate_routing_invariant(embedder_configs: Sequence[EmbedderConfig]) -> None: ...
    """Raise EmbedderRoutingError if active specialists exist but no active catchall."""
```

`embed.py` and `ingest.py` filter pending chunks with `[(cid, text) for (cid, text, uri) in rows if claims(embedder, uri)]` after extending the backend query to surface `documents.source_uri` alongside `chunk_id` + `text`.

### Backend signature evolution (smallest possible change)

`backend.chunks_missing_embedding(embedder_id, limit)` currently yields `(chunk_id, text)`. Two options were considered:
- **(A)** Extend it to yield `(chunk_id, text, source_uri)` and update both backends + all call sites.
- **(B)** Add a sibling `chunks_missing_embedding_with_uri(embedder_id, limit)` and keep the old method untouched.

**Pick (A)**: chunks have at most one source_uri (the parent document's), the join is cheap, and we don't want two near-duplicate methods drifting. The Protocol in `corpus_forge/backends/base.py` gains the new tuple shape; existing call sites unpack two-tuples today, they'll unpack three-tuples after — explicit, mechanical update.

## Project gates
- format: `uv run ruff format corpus_forge tests`
- format-check: `uv run ruff format --check corpus_forge tests`
- lint (CI): `uv run ruff check corpus_forge tests`
- typecheck: `./scripts/check-pyrefly.sh corpus_forge`
- focused unit: `uv run pytest tests/unit/test_embedder_config_routing.py tests/unit/test_embedder_routing.py tests/unit/test_embed_routing_filter.py tests/unit/test_ingest_routing_filter.py -v`
- regression unit: `uv run pytest tests/unit/test_embedder_config_llama_cpp.py tests/unit/test_embedder_register_from_config.py tests/unit/test_embed.py tests/unit/test_embed_backfill.py tests/unit/test_ingest_embedders.py -v`
- full unit: `uv run pytest tests/unit -v -n auto --timeout=60`
- coverage-min: 89 (per Makefile `--cov-fail-under=89`)

## Surface map

| File | Touched by |
|------|-----------|
| `tests/unit/test_embedder_config_routing.py` | T1 (RED) |
| `tests/unit/test_embedder_routing.py` | T2 (RED) |
| `tests/unit/test_embed_routing_filter.py` | T3 (RED) |
| `tests/unit/test_ingest_routing_filter.py` | T3 (RED) |
| `corpus_forge/config.py` | T4 (GREEN config schema + validator) |
| `corpus_forge/embedders/routing.py` | T5 (GREEN routing module — NEW file) |
| `corpus_forge/embedders/registry.py` | T5 (GREEN — propagate `extensions` to Embedder instances) |
| `corpus_forge/embedders/base.py` | T5 (GREEN — add `extensions` attr default) |
| `corpus_forge/backends/base.py` | T6 (GREEN — Protocol tuple shape) |
| `corpus_forge/backends/postgres.py` | T6 (GREEN — JOIN documents, yield source_uri) |
| `corpus_forge/backends/sqlite.py` | T6 (GREEN — same) |
| `corpus_forge/embed.py` | T7 (GREEN — backfill filter) |
| `corpus_forge/ingest.py` | T7 (GREEN — per-flush filter) |
| `config.example.toml` | T8 (GREEN docs) |
| `README.md` | T8 (GREEN docs) |

## Tasks

| id | title | depends_on | surface | risk | status | claimed_by | notes |
|----|-------|------------|---------|------|--------|------------|-------|
| T1 | RED: `EmbedderConfig.extensions` field validation + `Config` invariant | — | tests/unit/test_embedder_config_routing.py | low | done | principal | 11 tests; RED verified. |
| T2 | RED: routing helpers (`extension_for`, `route_for`, `claims`, `EmbedderRoutingError`) | — | tests/unit/test_embedder_routing.py | low | done | principal | 19 tests; RED via missing module. |
| T3 | RED: backfill + ingest filter pending chunks by route | — | tests/unit/test_embed_routing_filter.py, tests/unit/test_ingest_routing_filter.py | med | done | principal | 12 tests across two files; RED. |
| T4 | GREEN: add `extensions` field to `EmbedderConfig` + `Config`-level invariant validator | T1 | corpus_forge/config.py | low | done | principal | 11/11 tests pass. |
| T5 | GREEN: new `corpus_forge/embedders/routing.py` + propagate `extensions` to Embedder instances | T2, T4 | corpus_forge/embedders/routing.py, corpus_forge/embedders/registry.py, corpus_forge/embedders/base.py | med | done | principal | 24/24 tests pass. |
| T6 | GREEN: backends emit `source_uri` alongside chunks_missing_embedding | T3 | corpus_forge/backends/base.py, corpus_forge/backends/postgres.py, corpus_forge/backends/sqlite.py | med | done | principal | JOIN documents + conversations, COALESCE the source_uri. |
| T7 | GREEN: filter the pending-chunks stream in embed.py + ingest.py using `claims()` | T5, T6 | corpus_forge/embed.py, corpus_forge/ingest.py | med | done | principal | 13/13 filter tests pass. |
| T8 | GREEN: config.example.toml dual-tower example + README dual-tower subsection | T4 | config.example.toml, README.md | low | done | principal | Annotated dual-tower block + README subsection. |
| T9 | QA: format + lint + pyrefly + focused suite + full unit shape | T4..T8 | — | low | done | principal | approved — see qa-status.md. 166 baseline failures unchanged. |

## Acceptance details

### T1 — RED: `EmbedderConfig.extensions` field validation + `Config` invariant

In a new file `tests/unit/test_embedder_config_routing.py`, write failing tests against the current code (the field does not exist yet):

- `extensions` default is an empty list (`EmbedderConfig(..., name="x", provider="sentence_transformers", model_id="m", dimension=64).extensions == []`).
- `extensions=[".py", ".ts"]` round-trips unchanged.
- `extensions=[".PY", ".Ts"]` is normalised to `[".py", ".ts"]` at validation time.
- `extensions=["py"]` (no leading dot) raises `ValidationError` and the error message contains the offending string `"py"`.
- `extensions=[""]` rejected (must start with a dot).
- `extensions=[".tar.gz"]` ACCEPTED — multi-dot is fine; the matching uses suffix-comparison (a `.tar.gz` ext claims `foo.tar.gz`, not `foo.gz`).
- `extensions=[".py", ".PY"]` — after normalisation produces a single `[".py"]` (de-dupe) OR keeps both — pin whichever the implementer chooses by writing the test to whatever the implementation does. **DEFAULT**: accept both into the normalised list (no de-dupe needed — routing is short-circuit).
- `Config`-level invariant: when a `Config` has two `[[embedders]]` both with `active=True`, one specialist (`extensions=[".py"]`) and **no catchall**, `Config.model_validate(...)` raises `ValidationError` whose message is greppable (contains `"EmbedderRoutingError"` or `"catchall"`). Use the existing `Config` test pattern — see `corpus_forge.config.Config` and the `_check_fast_tier_embedder` validator for the shape.
- `Config`-level invariant: same setup but with one catchall declared first → passes validation.
- `Config`-level invariant: when *all* embedders have empty `extensions` (today's single-tower setup) → passes validation (no routing rule applies).
- Inactive specialists don't trigger the invariant: `active=False` specialist + no active catchall → passes (only active embedders gate routing).

### T2 — RED: routing helpers

New file `tests/unit/test_embedder_routing.py`. The module under test is `corpus_forge.embedders.routing` — does not exist yet, so every import fails RED.

Spec:

- `extension_for("filesystem://vault/foo/bar.py")` → `".py"`.
- `extension_for("filesystem://vault/foo/bar.tar.gz")` → `".gz"` (single-suffix). Document this — multi-extension `extensions=[".tar.gz"]` will NOT match through `extension_for`; matching for multi-suffix happens via `claims()` doing a `source_uri.lower().endswith(ext)` check. Phrased differently: `extension_for` is the **single-suffix** lookup; `claims()` does the **endswith** check against the embedder's allow-list, so `.tar.gz` works through the matcher.
- `extension_for("filesystem://vault/foo/README")` → `""` (no dot).
- `extension_for("filesystem://vault/foo/.envrc")` → `""` (dotfile, not a suffix).
- `extension_for("FILE.PY")` → `".py"` (case-folded).

- `claims(embedder, "x.py")` where `embedder.extensions == []` → `True` (catchall claims everything when consulted in isolation).
- `claims(embedder, "x.py")` where `embedder.extensions == [".py"]` → `True`.
- `claims(embedder, "x.md")` where `embedder.extensions == [".py"]` → `False`.
- `claims(embedder, "X.PY")` where `embedder.extensions == [".py"]` → `True` (case-insensitive).
- `claims(embedder, "foo.tar.gz")` where `embedder.extensions == [".tar.gz"]` → `True` (endswith).

- `route_for(".py", [text_catchall, code_specialist])` where text has `extensions=[]` and code has `[".py"]` → returns `code_specialist` (specialist beats catchall regardless of order).
- `route_for(".md", [text_catchall, code_specialist])` → returns `text_catchall`.
- `route_for(".py", [code_specialist_A, code_specialist_B])` where both `extensions=[".py"]` → returns the FIRST (declaration order).
- `route_for(".py", [code_specialist])` (no catchall, `.py` matches the specialist) → returns `code_specialist`.
- `route_for(".md", [code_specialist])` (no catchall, no match) → returns `None`.
- `route_for("", [text_catchall, code_specialist])` (no extension) → returns `text_catchall`.

- `validate_routing_invariant([code_specialist_cfg])` (only an active specialist, no catchall) → raises `EmbedderRoutingError` with a message that names the missing catchall.
- `validate_routing_invariant([text_catchall_cfg, code_specialist_cfg])` → does not raise.
- `validate_routing_invariant([code_specialist_cfg_inactive])` (specialist `active=False`) → does not raise.
- `validate_routing_invariant([])` → does not raise (no embedders, nothing to route — config-load-time other validators handle that).

- `EmbedderRoutingError` is a subclass of `ValueError` (so pydantic's validator wrapping is clean).

Use a tiny stand-in for `Embedder`/`EmbedderConfig` in the tests — `types.SimpleNamespace(extensions=[".py"], active=True, name="code")` is fine; the routing helpers should access `.extensions` only.

### T3 — RED: backfill + ingest filter

Two new files:

**`tests/unit/test_embed_routing_filter.py`**: monkeypatches `Config.load` to return a config with a text catchall + code specialist, monkeypatches the backend with a stub whose `chunks_missing_embedding` yields a mix of `(chunk_id, text, source_uri)` rows (e.g. `(1, "py text", "filesystem://a/foo.py")`, `(2, "md text", "filesystem://a/foo.md")`). Calls `backfill_embedder("nomic-code")` and asserts:
- Only chunk_id 1 was sent into `embedder.encode`.
- The chunk-id-2 `.md` chunk was not seen by the code embedder.
- Conversely, `backfill_embedder("nomic")` (catchall) sees only chunk 2 (because chunk 1 is claimed by `nomic-code`).
- When no embedder declares `extensions`, both embedders see every chunk (back-compat: today's behaviour preserved).
- A `MagicMock` embedder via `register_from_config`-style returns: assert `encode.call_args[0][0]` is the filtered text list.
- `backend.write_embeddings` was called with only the routed pairs.

**`tests/unit/test_ingest_routing_filter.py`**: targets `corpus_forge.ingest._write_embeddings_for_chunks`. Stubs a backend whose `chunks_missing_embedding` returns the same mixed `(cid, text, source_uri)` triples. Asserts:
- For `embedder.extensions=[".py"]` only chunk 1 reaches `embedder.encode`; `write_embeddings` writes (chunk_id=1, …).
- For catchall embedder, only chunk 2 reaches `encode` (because chunk 1 is claimed by the specialist registered alongside it).
- The returned int count (return value of `_write_embeddings_for_chunks`) matches the number of pairs actually written.
- When `active_embedders` contains only a single catchall (no specialists active), every chunk reaches every active embedder (back-compat).

For both files, the active-embedders list MUST be threaded into the filter — either via an argument the production code now accepts, OR via the registry. Pick the simplest seam: in `embed.backfill_embedder`, read `config.embedders` directly to build the `active_embedders` list, then call `claims(embedder, source_uri, active_embedders)`. In `ingest._write_embeddings_for_chunks`, pass `active_embedders` (already in scope as `embedders`) into the filter.

### T4 — GREEN: `EmbedderConfig.extensions` + `Config` invariant

- Add `extensions: list[str] = Field(default_factory=list)` on `EmbedderConfig`.
- Add a `@field_validator("extensions")` that lowercases every entry, rejects empty strings + entries without a leading dot. Error message names the offending value.
- Add a `@model_validator(mode="after")` on `Config` (alongside `_check_fast_tier_embedder`) named `_check_routing_invariant` that calls `validate_routing_invariant(self.embedders)` — re-raises with a clear message.
- All T1 tests pass.

### T5 — GREEN: routing module + propagate `extensions`

- New file `corpus_forge/embedders/routing.py` implementing the spec from T2.
- `BaseEmbedder.__init__` accepts an optional `extensions: list[str] | None = None` kwarg, defaults to `[]`.
- `EmbedderRegistry.register` forwards the kwarg.
- `_per_provider_extras` in `embedders/registry.py` always includes `extensions` (from `embedder_config.extensions`).
- Every concrete embedder class (`SentenceTransformersEmbedder`, `OpenAIEmbedder`, `Model2VecEmbedder`, `LlamaCppEmbedder`) accepts the new kwarg without breaking existing constructors — pass through via `**kwargs` if needed, or add the param explicitly. **Prefer explicit** — extend each `__init__` to accept `extensions: list[str] | None = None` and forward to super.
- All T2 tests pass.

### T6 — GREEN: backends yield `source_uri`

- `StorageBackend.chunks_missing_embedding` Protocol updated to `Iterator[tuple[int, str, str]]` (`(chunk_id, text, source_uri)`).
- `PostgresBackend.chunks_missing_embedding`: JOIN `corpus.documents` so the SELECT yields `c.id, c.text, d.source_uri` — and `JOIN corpus.documents d ON d.id = c.document_id`. Order by `c.id` preserved.
- `SQLiteBackend.chunks_missing_embedding`: same JOIN against `documents`.
- Image embeddings unchanged (`image_chunks_missing_embedding` retains its `(chunk_id, metadata_dict)` shape — text-embedding routing only touches the text path).
- Existing tests that consume `chunks_missing_embedding` MUST be updated to unpack three-tuples. Grep for callers (the ingest + embed paths above + any test stubs) and tighten. Treat this as part of the GREEN — if it breaks an existing test, fix the call site.

### T7 — GREEN: filter the stream in embed.py + ingest.py

- `corpus_forge.embed.backfill_embedder` builds `active_embedders` via `register_from_config` over `config.embedders` where `active=True`. After fetching `chunks_needing` it filters to only triples where `claims(embedder, source_uri, active_embedders)` (route returns `embedder`).
- `corpus_forge.ingest._write_embeddings_for_chunks` accepts the `embedders` list in its caller (already in scope via `_flush_all_pending_embeddings`) and uses the same filter.
- `count_chunks_missing_embedding` is left untouched for now — the progress-bar total may slightly over-count when routing is on; document in the docstring + log a one-time INFO if the routed count differs at first iter. (Not strictly required for this PR.)
- All T3 tests pass.

### T8 — GREEN: docs

- `config.example.toml`: append a commented `# ── Dual-tower retrieval (nomic + nomic-code) ──` block under the existing embedders section. Use `provider = "llama-cpp"` for both (since that's the user's setup). Include:
  - `nomic-embed-text-v1.5` as catchall (no `extensions` field shown; comment notes "absent = catchall").
  - `nomic-embed-code` with `extensions = [".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".kt", ".swift", ".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".rb", ".php", ".scala", ".sh", ".sql", ".lua", ".pl", ".pm", ".r", ".m", ".mm", ".dart", ".ex", ".exs", ".clj", ".cljs", ".hs", ".ml", ".fs", ".jl", ".nim", ".zig", ".v"]` (a reasonable code-extension list — pull a sensible default; the user can prune).
- `README.md`: short "Dual-tower retrieval" subsection (3-6 lines + the routing-rule bullet, no marketing copy). Link to `config.example.toml`.

### T9 — QA: gate sweep

- `make format-check` clean
- `make lint` clean
- `make typecheck` clean
- focused unit (see Project gates) all green
- regression unit (see Project gates) all green
- full unit suite: assert no NEW failures vs the PR-#80 baseline (any pre-existing failures inherited from main are OK and must be enumerated in `qa-status.md`).
- Coverage must remain ≥89 (Makefile gate).

Verdict format: `approved` / `rework` with one-paragraph rationale in `qa-status.md`.

## DAG
- Wave 0 (parallel RED): T1, T2, T3 (three testers in one dispatch).
- Wave 1 (parallel GREEN): T4 (config), T8 (docs only — does not depend on code).
- Wave 2 (parallel GREEN): T5 (routing module + Embedder.extensions), T6 (backend triples).
- Wave 3: T7 (filter wired into embed + ingest — needs both T5 and T6).
- Wave 4: T9 (QA — needs T4..T8).

## Summary

**Branch**: `feat/embedder-routing` (worktree `/Users/evanowen/dev/cf-worktrees/feat-embedder-routing`), off `main` @ `99bdfb0`.

**Files changed**:
- Production
  - `corpus_forge/config.py` — `EmbedderConfig.extensions` field + validator + `Config._check_routing_invariant` model validator.
  - `corpus_forge/embedders/routing.py` — NEW. `EmbedderRoutingError`, `extension_for`, `claims`, `route_for`, `validate_routing_invariant`.
  - `corpus_forge/embedders/base.py` — `BaseEmbedder.extensions` attr.
  - `corpus_forge/embedders/registry.py` — `_per_provider_extras` forwards `extensions` for every provider.
  - `corpus_forge/embedders/{sentence_transformers,openai,model2vec,llama_cpp}.py` — accept `extensions` kwarg.
  - `corpus_forge/backends/base.py` — Protocol widened to `Iterator[tuple[int, str, str]]`.
  - `corpus_forge/backends/postgres.py` — `chunks_missing_embedding` JOINs `documents` + `conversations`, yields `source_uri`.
  - `corpus_forge/backends/sqlite.py` — same.
  - `corpus_forge/embed.py` — backfill filters per-batch via `route_for`; builds the full active-embedder list.
  - `corpus_forge/ingest.py` — `_write_embeddings_for_chunks(active_embedders=...)` filter; `_flush_all_pending_embeddings` threads the list through.
  - `corpus_forge/cli.py` — `corpus-forge eval embedders` strips source_uri from `chunks_missing_embedding` output to keep the evaluator signature unchanged.
- Docs
  - `config.example.toml` — annotated dual-tower block.
  - `README.md` — "Dual-tower retrieval (extension-based routing)" subsection.
- Tests added (RED → GREEN)
  - `tests/unit/test_embedder_config_routing.py` (11)
  - `tests/unit/test_embedder_routing.py` (24)
  - `tests/unit/test_embed_routing_filter.py` (4)
  - `tests/unit/test_ingest_routing_filter.py` (9)
- Tests updated for 3-tuple shape (back-compat sweep)
  - `tests/unit/test_embed_backfill.py`, `test_ingest_embedders.py`, `test_remaining.py`, `test_ingest_extended.py`, `test_embed_extended.py`, `test_cli_eval_embedders.py`, `test_postgres_backend.py`, `test_sqlite_backend.py`, `test_eval_runner.py`, `tests/cli/test_embed_progress.py`.

**Gates run** (worktree-local):
- `ruff format --check corpus_forge tests` — clean (783 files).
- `ruff check corpus_forge tests` — clean.
- `./scripts/check-pyrefly.sh corpus_forge` — 0 errors.
- Focused suite (`test_embedder_config_routing`, `test_embedder_routing`, `test_embed_routing_filter`, `test_ingest_routing_filter`, plus regression on embed_backfill / ingest_embedders / embed / embedder_register_from_config / embedder_config_llama_cpp): **127/127 green**.
- Full `tests/unit` suite: 166 failed, 5529 passed, 35 errors — **identical failure set to baseline (main @ 99bdfb0)**, +48 net new passing tests.

**Smoke** (Postgres dual-tower integration) — not run locally (requires running Postgres + GGUF models). Logic-verified via unit-level routing-filter tests covering specialist/catchall split, declaration-order tiebreaker, single-tower back-compat, no-claim short-circuit.

**Override**: none. QA approved.
