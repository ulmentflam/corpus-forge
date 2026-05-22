# RFCs — Nightly accepted-task queue

This directory is the documented entry point for Nightly's
`accepted_rfc` tier in the priority cascade (see
`.claude/skills/nightly/SKILL.md`). Each RFC is a self-contained
proposal with an unchecked task checklist (`- [ ] …`) that Nightly
ticks off as it ships work.

**Conventions** (mirrors `.planning/tdd/` — no frontmatter, plain
Markdown):

- One RFC per file, filename `rfc-<slug>.md`.
- Required sections: Status, Owner, Priority, Depends on, Context,
  Goals, Non-goals, Approach, Tasks, Verification, References.
- Status is informal: `accepted` once it's in this directory and
  ready to claim; flip to `in progress` / `done` as Nightly works.
- Tasks must use Markdown checkboxes (`- [ ] foo`) because the
  cascade scans for unchecked items.

## Backlog

| Priority | RFC | Depends on | Why it's here |
|---|---|---|---|
| P0 | [rfc-claude-code-self-ingest-e2e](rfc-claude-code-self-ingest-e2e.md) | — | Zero end-to-end coverage of the new Claude Code parser against this repo's own JSONL files. Unblocks H-03 RED. |
| P0 | [rfc-source-provenance-git-and-lines](rfc-source-provenance-git-and-lines.md) | — | Chunks don't carry git commit / file path / line range — blocks self-distillation, live source nav, profile attach. |
| P1 | [rfc-corpus-growth-controls](rfc-corpus-growth-controls.md) | — | No pruning, no per-source caps, no pre-ingest cost gate. Counters the user's hard "no exponential growth" constraint. |
| P1 | [rfc-nlp-data-quality-signals](rfc-nlp-data-quality-signals.md) | — | Curation selector only has meta-signals. Add lang / dedup / quality / boilerplate enrichers to feed pruning + retrieval. |
| P1 | [rfc-eval-framework-expansion](rfc-eval-framework-expansion.md) | — | Retrieval eval is complete; classifier-accuracy and chunk-quality have no harness. Adds drift-regression mode. |
| P2 | [rfc-runtime-feedback-exec-and-profile](rfc-runtime-feedback-exec-and-profile.md) | rfc-source-provenance-git-and-lines | Sandboxed code execution + cProfile + stack traces as feedback. Includes threat model. |
| P2 | [rfc-self-distillation-export](rfc-self-distillation-export.md) | (composes with rfc-runtime-feedback) | Finish H-04 RED `export_feedback_pairs` + new SDFT preprocessing pipeline. |
| P3 | [rfc-hf-dataset-inbound-source](rfc-hf-dataset-inbound-source.md) | rfc-corpus-growth-controls | Pull external HF datasets into corpus-forge with per-import row caps. |
| P3 | [rfc-profiling-trace-source](rfc-profiling-trace-source.md) | rfc-source-provenance-git-and-lines | Ingest cProfile / pyinstrument artefacts; attach hot-path metadata to code chunks. |
| P3 | [rfc-developer-ux-verbs](rfc-developer-ux-verbs.md) | rfc-source-provenance-git-and-lines (one verb only) | `logs tail`, `stats`, `debug`, `config edit` — small dev-friendly verbs. |

## Sequencing notes

- **P0s are independent** — Nightly can pick either first.
- **P1s are independent**, but `rfc-nlp-data-quality-signals` makes
  `rfc-corpus-growth-controls`'s pruning smarter, and
  `rfc-eval-framework-expansion` can borrow the new signals as a
  quality rubric. Land all three before P2 work for maximum leverage.
- **P2s compose**: `rfc-runtime-feedback-exec-and-profile` produces
  the rich feedback that `rfc-self-distillation-export` then turns
  into training pairs.
- **P3s are leaf nodes** — none of the higher-priority RFCs depend
  on them. Each can ship at any time after its declared `Depends on`.

## How Nightly picks the next one

Per `.claude/skills/nightly/SKILL.md`, the cascade walks:

1. `resume_in_flight` — any plan with `status: in_progress`
2. `unblocked_approval` — `status: parked` whose approval landed
3. **`accepted_rfc`** — *this directory* (`.planning/rfcs/`)
4. `github_issue`
5. `ideate`
6. `nothing`

Nightly scans `.planning/rfcs/*.md` for `- [ ]` items and picks the
first RFC with at least one unchecked task. Closing all the boxes in
an RFC marks it effectively done (you can also delete it or move it
to a `.planning/rfcs/done/` subdirectory once shipped).
