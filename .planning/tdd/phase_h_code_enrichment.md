# Phase H — Qwen3.6-35B-A3B Code Enrichment

_Owner: tdd-principal. Workers: read freely. Edit only your claimed row's `status` and `claimed_by`._

## Status

**Phase G** complete. **Phase H** (this file): **COMPLETE** (2026-05-15).

## Goal

For every chunk in a `class=code` document, attach an LLM-generated **enrichment** record carrying:
- A **synthesized docstring** (when the function/class lacks one)
- A **semantic summary** (1-2 sentences explaining what the chunk does, in domain language)
- **Symbol references** (functions/types this chunk depends on; useful for cross-chunk linking)

The enrichment lives alongside the existing `chunks.metadata.{kind, name, language, byte_range}` from Phase D's CodeChunker. Downstream retrievers can boost on enrichment text, do natural-language code search, and surface dependency edges.

Gated to `class=code` only — Phase E's labels make this cheap.

## Architecture

**CodeEnricher protocol** (`corpus_forge/enrichers/base.py`):
```python
@dataclass(frozen=True)
class CodeChunkEnrichment:
    docstring: str | None
    summary: str
    symbols: list[str]      # referenced symbol names
    model: str              # model tag used
    confidence: float

class CodeEnricher(Protocol):
    name: str
    def enrich(self, chunk: TextChunk, *, language: str) -> CodeChunkEnrichment: ...
    def warmup(self) -> None: ...
```

**Backends**:
- `corpus_forge/enrichers/qwen_local.py::QwenCoderLocal` — Ollama-hosted `qwen3.6:35b-a3b-instruct` (MoE; 35B total / ~3B active; ~22 GB on disk). Local `llm_url` defaults to `http://localhost:11434`.
- `corpus_forge/enrichers/qwen_remote.py::QwenCoderRemote` — speaks the same Ollama API at any remote URL OR an OpenAI-compatible chat-completions endpoint (configurable via `api_shape = "ollama" | "openai"`).

**Storage**: chunk enrichments live in `chunks.metadata.enrichment.{docstring, summary, symbols, model, confidence}` (existing JSON column from Phase D HK-2). No schema change needed for P0.

**CLI**: `corpus-forge enrich [OPTIONS]` — walks `class=code` documents' chunks, runs the enricher, writes results to `chunks.metadata.enrichment`. Idempotency check: skip chunks with `metadata.enrichment.model == <current model>` (re-enrich on model change).

## Local-or-remote requirement

Per `project_model_local_or_remote.md`: `[code_enricher]` config block with `backend = "local" | "remote"`, explicit `local_url` AND `remote_url` fields, rich `config.example.toml` docs showing both, README + `docs/architecture.md` "Model endpoints" subsection updated.

## Task table

| id | title | depends_on | surface | risk | status |
|----|-------|------------|---------|------|--------|
| H-01 | `CodeEnricher` protocol + `CodeChunkEnrichment` + `EnricherRegistry` | — | `corpus_forge/enrichers/{__init__,base,registry}.py` (new), `tests/unit/test_enricher_registry.py` | low | **done** |
| H-02 | `QwenCoderLocal` (Ollama HTTP) | H-01 | `corpus_forge/enrichers/qwen_local.py`, `tests/unit/test_qwen_local.py` (mocked HTTP) | med | **done** |
| H-03 | `QwenCoderRemote` (configurable URL + shape) | H-01 | `corpus_forge/enrichers/qwen_remote.py`, `tests/unit/test_qwen_remote.py` (mocked HTTP) | med | **done** |
| H-04 | `EnricherConfig` pydantic + `Config.code_enricher` | — | `corpus_forge/config.py`, `tests/unit/test_config_enricher.py` | low | **done** |
| H-05 | Backend helpers: `iter_code_chunks_for_enrichment`, `update_chunk_enrichment` | — | `corpus_forge/backends/{base,postgres,sqlite}.py`, `tests/unit/test_backend_enrichment_helpers.py` | med | **done** |
| H-06 | `corpus-forge enrich` CLI command | H-02, H-04, H-05 | `corpus_forge/cli.py`, `tests/unit/test_cli_enrich.py` | med | **done** |
| H-07 | `config.example.toml` `[code_enricher]` rich-docs block | H-04 | `config.example.toml` | low | **done** |
| H-08 | README + `docs/architecture.md` "Code enrichment" sections | H-06 | `README.md`, `docs/architecture.md` | low | **done** |
| H-09 | E2E integration test (requires_qwen_coder marker) | H-06 | `tests/integration/test_enrich_e2e.py`, `tests/integration/conftest.py` (probe) | med | **done** |
| H-10 | **P0 gate** — `make ci` green + manual cross-model smoke | H-09 | — | gate | **done** |

## Definition of Done

- [x] `corpus_forge/enrichers/` package with protocol + 2 backends + registry
- [x] `[code_enricher]` config block parses; defaults to `backend = "none"` (Phase H is opt-in)
- [x] `corpus-forge enrich` walks `class=code` chunks, populates `chunks.metadata.enrichment`, is idempotent on model-tag match
- [x] Cost-guard preflight estimates LLM calls and per-chunk latency
- [x] Live e2e against local Ollama (skip when model not pulled) classifies ≥ 5 fixture-corpus code chunks successfully
- [x] Manual smoke: enrich a small Python module from `corpus_forge/`; review the synthesized docstrings + summaries for coherence
- [x] `make ci` exit 0 at ≥ 90% coverage (achieved: 90.09%)

## Out of scope (P2)

- Cross-chunk dependency-graph storage (for now, `symbols` is a flat list)
- Embedding the enrichment text itself (would need a separate pass; future milestone)
- Multi-language enrichment beyond what the model already supports (works automatically — the model handles Python/Rust/Go/JS/TS/etc. natively)
- Re-enrichment trigger on chunk content change (currently only model-tag change forces re-enrich)

## Reuse (do not reinvent)

- `OllamaVLM` HTTP shape (`corpus_forge/vlm/ollama.py`) for the Ollama backend
- `LLMClassifier` HTTP shape (`corpus_forge/classifiers/llm.py`) — closest existing template
- `apply_label` + chunk metadata write paths from Phase D HK-2
- `ClassifierRegistry` shape (`corpus_forge/classifiers/registry.py`) — adapt for `EnricherRegistry`
- `requires_ollama_text` marker plumbing — clone for `requires_qwen_coder`
