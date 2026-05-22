# RFC: Self-distillation export — feedback pairs + SDFT preprocessing

Status: accepted
**Owner**: nightly (open for any agent to claim)
**Priority**: P2
**Depends on**: `rfc-runtime-feedback-exec-and-profile.md` is *not*
a hard dep but composes well — once runtime feedback lands, this
export gets richer signal.

## Context

`corpus_forge/export.py::export_chat()` works (JSONL/Parquet,
templated rows, optional Hub push). The H-04 RED test at
`tests/integration/test_self_distillation_export.py` requires an
`export_feedback_pairs()` function that does NOT exist. That blocks
the entire self-distillation training loop the user wants:

1. User chats with Claude Code / Codex / Gemini.
2. corpus-forge ingests + links feedback (good/bad ratings, accepted
   vs rejected diffs, stack traces from `rfc-runtime-feedback-...`).
3. **GAP**: nothing exports those `(prompt, chosen, rejected)`
   triples into a DPO/SDFT-ready format.
4. (Future) user fine-tunes a smaller model on the export.

We also lack any SDFT (Synthetic Data Fine-Tuning) preprocessing
module — formatting per tokenizer, template-aware splitting,
deduplication of training examples.

## Goals

- `export_feedback_pairs(dataset, out, format='dpo'|'kto'|'orpo')` —
  emits training rows in standard preference-learning shapes from
  feedback-linked conversations.
- `corpus_forge/exports/sdft.py` — preprocessing pipeline:
  per-tokenizer chat-template rendering (reuse
  `corpus_forge/templates/` Jinja), max-length truncation, dup
  filtering, optional packing.
- `corpus-forge export feedback-pairs` and
  `corpus-forge export sdft` CLI verbs.
- HF Datasets export path (push the result via the existing
  `corpus_forge/exports/huggingface.py:push_to_hub`).
- Make H-04's RED test go GREEN.

## Non-goals

- No training. We *export*; the user picks a trainer.
- No RLHF reward model. We emit preference *pairs*; reward modelling
  is downstream.
- No new tokenizer integration; we use whatever `templates/` already
  pulls in (HF tokenizers via `transformers` chat templates).

## Approach

### `export_feedback_pairs`

New file: extend `corpus_forge/export.py` with
`export_feedback_pairs(...)`. Algorithm:

1. Query `feedback_sessions` rows joined to `conversations` (the
   linker built by `_session_link`).
2. For each session, walk messages chronologically. Build prompt =
   user turn(s) up to the assistant turn under evaluation. The
   assistant turn becomes either `chosen` or `rejected` based on
   its feedback row (`rating > 0` → chosen, `rating < 0` →
   rejected, or `kind in {"accepted","rejected"}` for binary
   labels).
3. Pair them: within a session, group `chosen` and `rejected`
   responses that share the same prompt prefix. If only one side
   exists, skip (DPO needs both).
4. Emit JSONL / Parquet rows per the requested format:
   - `dpo`: `{prompt, chosen, rejected}`
   - `kto`: `{prompt, completion, label: bool, weight: float}`
   - `orpo`: `{prompt, chosen, rejected, ...}`
5. Include provenance metadata: `source_uri`, `session_id`,
   `git_commit` (from `rfc-source-provenance-git-and-lines.md`).

### SDFT preprocessing

New file: `corpus_forge/exports/sdft.py`. Pipeline:

```
raw conversations
  → chat-template render (per tokenizer; reuse templates/)
  → length truncation (model-aware; tokenizer.encode + slice)
  → dedup (MinHash → drop near-dupes; reuse the enricher from
            rfc-nlp-data-quality-signals.md if present, else exact dedup)
  → optional packing (group short examples to a max seq-len budget)
  → JSONL or HF Dataset
```

Each stage is a small composable function so users can call them
piecemeal (`sdft.render_template(...)`, `sdft.dedup_minhash(...)`,
`sdft.pack_examples(...)`).

### Schema for emitted rows

Shared envelope `corpus_forge/exports/_sdft_schema.py`:

```python
@dataclass
class SDFTRow:
    prompt: str
    completion: str | None       # for SFT
    chosen: str | None           # for DPO/KTO/ORPO
    rejected: str | None
    label: bool | None           # for KTO
    weight: float | None
    metadata: dict               # source_uri, session_id, git_commit, etc.
```

### CLI

Extend `corpus_forge/cli.py`'s `export` typer group:

```
corpus-forge export feedback-pairs --dataset <name> --format dpo \
    [--out out.jsonl] [--push-to-hub <repo>]
corpus-forge export sdft --dataset <name> --tokenizer <hf_id> \
    --max-len 4096 [--pack] [--out out.jsonl] [--push-to-hub <repo>]
```

## Tasks

- [ ] `corpus_forge/exports/_sdft_schema.py`: `SDFTRow` dataclass.
- [ ] `export_feedback_pairs()` in `corpus_forge/export.py` (or new
      module `corpus_forge/exports/feedback_pairs.py` — keep
      `export.py` lean).
- [ ] `corpus_forge/exports/sdft.py`: `render_template`,
      `truncate_to_tokens`, `dedup_minhash` (or exact), `pack_examples`,
      top-level `preprocess_for_sdft()`.
- [ ] CLI verbs `export feedback-pairs` and `export sdft`.
- [ ] HF Hub push integration (reuse
      `corpus_forge/exports/huggingface.py:push_to_hub`).
- [ ] Make `tests/integration/test_self_distillation_export.py` (H-04
      RED) go GREEN.
- [ ] New tests:
  - [ ] `tests/unit/test_export_feedback_pairs.py` — known fixture
        with chosen+rejected pairs → expected DPO rows.
  - [ ] `tests/unit/test_sdft_pipeline.py` — render → truncate →
        dedup → pack on a tiny corpus.
  - [ ] `tests/integration/test_export_feedback_pairs_e2e.py` —
        ingest a fixture, link feedback, export → assert shape.
  - [ ] `tests/integration/test_sdft_e2e.py` — full pipeline against
        a tiny tokenizer (e.g., `hf-internal-testing/tiny-random-Llama`).
- [ ] CHANGELOG entry.

## Verification

- H-04 RED test passes (
  `pytest tests/integration/test_self_distillation_export.py`).
- `corpus-forge export feedback-pairs --dataset claude-code --format
  dpo --out pairs.jsonl` produces a file whose first row parses as
  valid DPO JSON with non-empty `prompt`/`chosen`/`rejected`.
- `corpus-forge export sdft --tokenizer hf-internal-testing/tiny-random-Llama
  --max-len 256` writes JSONL where every row's tokenised length ≤
  256.
- Pushing either output to a private HF Hub repo round-trips via
  `datasets.load_dataset`.

## References

- Existing export: `corpus_forge/export.py::export_chat`.
- HF Hub integration:
  `corpus_forge/exports/huggingface.py::push_to_hub`,
  `export_to_hf_dataset`.
- Templates / chat formatting: `corpus_forge/templates/`.
- Session-feedback link: `corpus_forge/sources/_session_link.py`.
- Pending RED test: `tests/integration/test_self_distillation_export.py`.
