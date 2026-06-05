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
- Status line sits near the top of each RFC, on its own line, as
  `status: accepted` (or `in-progress` / `done`). Important: the
  whole line MUST be unbolded and use lowercase `status:` — Nightly's
  cascade matches the literal lowercase substring `status: accepted`
  to decide if an RFC is claimable, and any markdown bold inside the
  line (e.g. `**Status**:` or `Status: **accepted**`) breaks that
  match. The matcher applies `.lower()` to the file body so the
  `accepted` token itself can be any case, but keep the whole line
  lowercase for consistency with the contract.
- Other field labels (`**Owner**`, `**Priority**`, `**Depends on**`,
  …) stay bold as before — only the `status:` line is special.
- Tasks must use Markdown checkboxes (`- [ ] foo`) because the
  cascade scans for unchecked items.

## Backlog

| Priority | RFC | Depends on | Why it's here |
|---|---|---|---|
| P0 (top) | [rfc-fleet-1-model-telemetry-and-bench](rfc-fleet-1-model-telemetry-and-bench.md) | — | DB registry of hosts/models + measured embed rates (passive from real runs, active via the small-sample `bench embed` verb). Foundation for the fleet series. **Operator-requested 2026-06-04, accepted same day.** |
| P0 (top) | [rfc-fleet-2-distributed-embedding](rfc-fleet-2-distributed-embedding.md) | rfc-fleet-1 | Claim-based backfill (`FOR UPDATE SKIP LOCKED` + lease expiry) so N tailnet machines drain one embedder lane with zero duplicate compute; `[embed] lanes` per-host pinning. **Operator-requested 2026-06-04.** |
| P0 (top) | [rfc-fleet-3-federated-config-and-setup](rfc-fleet-3-federated-config-and-setup.md) | rfc-fleet-1 | Shared-vs-local config scopes, `config publish/pull`, `setup --join <dsn>` one-command machine onboarding, installer `--join` flags. Hard backcompat bar: local-only setups unchanged. **Operator-requested 2026-06-04.** |
| P1 | [rfc-fleet-4-tailscale-integration](rfc-fleet-4-tailscale-integration.md) | rfc-fleet-1, rfc-fleet-3 | `ts://<magicdns-name>` accepted in every URL/DSN field, doctor tailnet-reachability check, live-peer picker in setup/join. Read-only Tailscale integration. **Operator-requested 2026-06-04.** |
| P0 (top) | [rfc-benchmark-corpus-and-media-fixtures](rfc-benchmark-corpus-and-media-fixtures.md) | — | Smoke/integration tests + embedder ranking need realistic, license-clean (PD/CC0) text, code, image, audio, video fixtures. Unblocks the embedding-eval multilingual + multimodal lanes. **Operator-requested 2026-05-26.** |
| P0 (top) | [rfc-best-embedding-models-and-evaluation](rfc-best-embedding-models-and-evaluation.md) | rfc-benchmark-corpus-and-media-fixtures | Deep research on best embedders per lane (english/code/multilingual/multimodal) + an on-machine ranking harness + a README recommendations section. **Operator-requested 2026-05-26.** |
| P0 (top) | [rfc-codebase-cleanup-and-readability](rfc-codebase-cleanup-and-readability.md) | — (sequence after the two above) | Bounded, behavior-preserving LOC reduction / dedup / readability pass + a copy-paste "install with Claude" prompt in the README. **Operator-requested 2026-05-26.** |
| P0 | [rfc-claude-code-self-ingest-e2e](rfc-claude-code-self-ingest-e2e.md) | — | Zero end-to-end coverage of the new Claude Code parser against this repo's own JSONL files. Unblocks H-03 RED. |
| P0 | [rfc-source-provenance-git-and-lines](rfc-source-provenance-git-and-lines.md) | — | Chunks don't carry git commit / file path / line range — blocks self-distillation, live source nav, profile attach. |
| P1 | [rfc-corpus-growth-controls](rfc-corpus-growth-controls.md) | — | No pruning, no per-source caps, no pre-ingest cost gate. Counters the user's hard "no exponential growth" constraint. |
| P1 | [rfc-nlp-data-quality-signals](rfc-nlp-data-quality-signals.md) | — | Curation selector only has meta-signals. Add lang / dedup / quality / boilerplate enrichers to feed pruning + retrieval. |
| P1 | [rfc-eval-framework-expansion](rfc-eval-framework-expansion.md) | — | Retrieval eval is complete; classifier-accuracy and chunk-quality have no harness. Adds drift-regression mode. |
| P2 | [rfc-runtime-feedback-exec-and-profile](rfc-runtime-feedback-exec-and-profile.md) | rfc-source-provenance-git-and-lines | Sandboxed code execution + cProfile + stack traces as feedback. Includes threat model. |
| P2 | [rfc-self-distillation-export](rfc-self-distillation-export.md) | (composes with rfc-runtime-feedback) | Finish H-04 RED `export_feedback_pairs` + new SDFT preprocessing pipeline. |
| P2 | [rfc-version-update-awareness](rfc-version-update-awareness.md) | — | The CLI already detects + recommends updates, but MCP agents (claude/opencode/gemini/cursor/…) never see it. Add a `check_update` MCP tool, a passive server-instructions notice, and a `corpus-forge-update` skill. |
| P3 | [rfc-hf-dataset-inbound-source](rfc-hf-dataset-inbound-source.md) | rfc-corpus-growth-controls | Pull external HF datasets into corpus-forge with per-import row caps. |
| P3 | [rfc-profiling-trace-source](rfc-profiling-trace-source.md) | rfc-source-provenance-git-and-lines | Ingest cProfile / pyinstrument artefacts; attach hot-path metadata to code chunks. |
| P3 | [rfc-developer-ux-verbs](rfc-developer-ux-verbs.md) | rfc-source-provenance-git-and-lines (one verb only) | `logs tail`, `stats`, `debug`, `config edit` — small dev-friendly verbs. |

## Sequencing notes

- **Fleet series (2026-06-04, accepted).** Four RFCs prefixed
  `rfc-fleet-<n>-` so they sort together and in execution order under
  the cascade's alphabetical pick. All four accepted by the operator
  2026-06-04. Pick order: fleet-1 (telemetry tables everything else reads) → fleet-2
  (distributed claiming, the wall-clock win) → fleet-3 (federated
  config + join flow, touches setup wizard and install.{sh,ps1}) →
  fleet-4 (Tailscale ergonomics layered over 1+3). Fleet-2 and
  fleet-3 are independent of each other and can be armed in either
  order once fleet-1 lands.
- **Operator-requested top-of-queue (2026-05-26).** Three new P0s were
  added by the operator and named so they sort ahead of the rest in the
  cascade's alphabetical pick order (the cascade picks the first accepted
  RFC with an unchecked `- [ ]`, by `sorted(filename)` — the `Priority`
  field is documentation, not the tiebreak). Resulting pick order:
  `rfc-benchmark-corpus-and-media-fixtures` →
  `rfc-best-embedding-models-and-evaluation` →
  `rfc-codebase-cleanup-and-readability`. Fixtures go first because the
  embedding eval's multilingual + multimodal lanes depend on them;
  cleanup goes last so the refactor doesn't churn code the first two add.
- **P0s are independent** — Nightly can pick either first.
- **P1s are independent**, but `rfc-nlp-data-quality-signals` makes
  `rfc-corpus-growth-controls`'s pruning smarter, and
  `rfc-eval-framework-expansion` can borrow the new signals as a
  quality rubric. Land all three before P2 work for maximum leverage.
- **P2s compose**: `rfc-runtime-feedback-exec-and-profile` produces
  the rich feedback that `rfc-self-distillation-export` then turns
  into training pairs. `rfc-version-update-awareness` is independent
  of both — a leaf-node UX plumbing RFC that builds only on the
  already-shipped `corpus_forge/update/` machinery.
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
