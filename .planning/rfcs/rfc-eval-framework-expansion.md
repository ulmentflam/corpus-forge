# RFC: Evaluation framework expansion (classifier + chunk quality)

status: accepted
**Owner**: nightly (open for any agent to claim)
**Priority**: P1
**Depends on**: none (composes well with `rfc-nlp-data-quality-signals.md`)

## Context

`corpus_forge/eval/{metrics,dataset,runner}.py` is a complete
retrieval-eval harness (recall@k, MRR, nDCG, gold-set JSONL loader,
runner CLI: `corpus-forge eval retrieval` and
`eval corpus-quality`). It tells us "how well does retrieval rank a
known-relevant chunk." It does not tell us:

- **Are our classifiers actually right?** `corpus_forge/classifiers/`
  has rule-based + LLM classifiers. There is no harness measuring
  per-class precision/recall/F1 against a ground-truth set.
- **Is the chunk's *content* good?** Retrieval can rank a low-quality
  chunk highly if it lexically matches; we have no separate quality
  rubric scoring chunks against a held-out human-graded set.
- **Does ingest behaviour regress?** No drift detector that re-runs
  the same eval over time and alerts on metric movement.

Without these, we're flying blind on the two biggest data-quality
levers (classification accuracy + chunk quality).

## Goals

- `corpus-forge eval classifier --dataset <name> --gold
  <gold.jsonl>` — per-class precision/recall/F1, confusion matrix,
  macro-/micro-averages, written to JSON for downstream tooling.
- `corpus-forge eval quality --dataset <name> --rubric <rubric.jsonl>`
  — scores chunks against a held-out rubric (loaded from JSONL like
  the retrieval gold set); reports per-rubric-dimension means + a
  blended score.
- A regression mode: `corpus-forge eval regression --baseline
  <prior_results.json>` compares the current run to a prior baseline
  and flags movements outside a tolerance band.
- Shared output schema across all three so a downstream dashboard can
  plot them on one timeline.

## Non-goals

- No new ML training — we evaluate existing classifiers/scorers.
- No web dashboard in this RFC; the JSON output is the API for
  whatever the user wants to plot.
- No automatic relabelling on classifier-eval failures. Failures are
  surfaced; the curator decides.

## Approach

### `eval classifier`

New file `corpus_forge/eval/classifier_accuracy.py`. Gold-set format
mirrors the retrieval gold loader:

```jsonl
{"item_id": "doc-42", "true_label": "code", "predicted_label": null}
```

Or, if comparing against the existing classified rows, the
`predicted_label` is looked up from the chunk's `class_hint` /
`labels` table at runtime.

Metrics: per-class precision/recall/F1, macro/micro averages,
confusion matrix as 2D dict. Reuse the same `LoadGoldErrors`-style
schema validation pattern from `corpus_forge/eval/dataset.py`.

### `eval quality`

New file `corpus_forge/eval/chunk_quality.py`. Gold rubric format:

```jsonl
{"chunk_id_hint": "<hash-or-source-uri>",
 "scores": {"clarity": 4, "completeness": 5, "is_boilerplate": 0}}
```

The runner resolves `chunk_id_hint` via content-hash (same drift
tolerance pattern from R3-05's
`corpus_forge/eval/runner.py::resolve_chunk_id_by_hash`), pulls the
predicted score from the chunk metadata (where
`rfc-nlp-data-quality-signals.md` lands the values), and reports
per-rubric-dimension MAE / Spearman correlation against the human
labels.

### `eval regression`

New file `corpus_forge/eval/regression.py`. Reads two JSON output
files (current run + baseline), computes per-metric deltas, prints a
table, exits non-zero if any metric outside the tolerance band
(configurable per metric in `[eval.regression.tolerance]`).

### CLI

Extend `corpus_forge/cli.py`'s `eval` typer group with three new
subcommands. All emit JSON to stdout (or `--out`) and a human-readable
summary to stderr — mirrors the existing
`eval retrieval` shape so the user's mental model is consistent.

### Shared output schema

```json
{
  "eval_kind": "classifier" | "quality" | "retrieval" | "regression",
  "dataset": "<name>",
  "git_commit": "<sha>",
  "ts": "<iso8601>",
  "metrics": { ... },
  "config": { ... }
}
```

Lives in `corpus_forge/eval/_schema.py` so all three new evaluators
share it.

## Tasks

- [x] `corpus_forge/eval/_schema.py` — shared output envelope. — PR #38 (already open from prior Nightly run)
- [x] `corpus_forge/eval/classifier_accuracy.py` — local proposal (branch `nightly/eval-classifier-192805Z`, commit `f39b303`)
       — original RFC text:
      precision/recall/F1, confusion matrix, gold loader.
- [x] `corpus_forge/eval/chunk_quality.py` — rubric loader, scoring, — local proposal (branch `nightly/eval-chunk-quality-193342Z`, commit `1769c57`)
       — original RFC text:
      MAE/Spearman.
- [x] `corpus_forge/eval/regression.py` — diff two result envelopes, — local proposal (branch `nightly/eval-regression-193929Z`, commit `367effb`)
       — original RFC text:
      tolerance gating.
- [x] Extend `corpus_forge/cli.py` with `eval classifier`,
      `eval quality`, `eval regression`. — local proposal (branch `nightly/eval-cli-194335Z`, commit `6481f76`)
- [x] Pydantic `EvalRegressionConfig` block in
      `corpus_forge/config.py` (tolerances). — PR #41 (already open from prior Nightly run)
- [x] Tests:
  - [x] `tests/unit/test_eval_classifier_accuracy.py` — task 0007 local proposal (11 tests)
  - [x] `tests/unit/test_eval_chunk_quality.py` — task 0008 local proposal (21 tests)
  - [x] `tests/unit/test_eval_regression.py` — task 0009 local proposal (14 tests)
  - [x] `tests/unit/test_eval_schema.py` — PR #38 (already open from prior Nightly run)
  - [ ] `tests/integration/test_eval_cli_e2e.py` — (Deferred: needs PR #38, tasks 0007/0008/0009/0010 all merged for the e2e round-trip)
- [x] Sample gold files under `tests/fixtures/eval/` for both
      classifier and quality. — `classifier_gold.jsonl` (task 0007), `chunk_quality_gold.jsonl` (task 0008)
- [x] CHANGELOG entry. — bullets in each task's local proposal (0007/0008/0009/0010).

## Verification

- `corpus-forge eval classifier --dataset <name> --gold
  classifier_gold.jsonl --out out.json` writes JSON with per-class
  metrics; the schema validates against `_schema.py`.
- Same for `eval quality` and `eval regression`.
- `eval regression --baseline old.json` exits 0 when metrics are
  within tolerance, non-zero when they aren't — confirmed by
  fixture-driven test.
- The existing `corpus-forge eval retrieval` CLI still works (no
  regression on it).

## References

- Retrieval eval (the existing shape to mirror): `corpus_forge/eval/{metrics,dataset,runner}.py`.
- Classifiers under test: `corpus_forge/classifiers/{base,llm,rule_based}.py`.
- Drift-tolerant chunk-id resolution:
  `corpus_forge/eval/runner.py` (R3-05).
- CLI surface pin tests: `tests/unit/test_cli_eval.py`.
