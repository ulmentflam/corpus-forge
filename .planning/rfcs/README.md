# RFCs — Nightly accepted-task queue

This directory is the documented entry point for Nightly's
`accepted_rfc` tier in the priority cascade (see
`.claude/skills/nightly/SKILL.md`). Each RFC is a self-contained
proposal with an unchecked task checklist (`- [ ] …`) that Nightly
ticks off as it ships work.

**Conventions** (mirrors `.planning/tdd/` — no frontmatter, plain
Markdown):

- One RFC per file, filename `rfc-<slug>.md` (numeric `NNN-` prefixes
  are also used when an RFC must sort to a specific cascade position —
  `000-` is reserved for the current top-priority initiative).
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

The cascade picks the first **accepted** RFC with an unchecked
`- [ ]`, by `sorted(filename)` — the `Priority` column is
documentation, the filename is the tiebreak. The `000-codeintel-*`
RFCs are named to sort ahead of everything else so they're worked
first.

| Priority | RFC | Depends on | Why it's here |
|---|---|---|---|
| **P0 (top)** | [000-codeintel-1-incremental-merkle-sync](000-codeintel-1-incremental-merkle-sync.md) | — | **Operator-requested 2026-06-30.** Merkle-tree diff-sync for scans (Cursor secure-indexing model): persisted per-(dataset,path) fingerprint so an unchanged subtree is pruned without reading any file. Turns re-scan cost from O(all files) → O(changed). Foundational — the change-set feeds codeintel-2. |
| **P0 (top)** | [000-codeintel-2-code-knowledge-subgraphs](000-codeintel-2-code-knowledge-subgraphs.md) | 000-codeintel-1 | **Operator-requested 2026-06-30.** Code knowledge graph (GitNexus model) over Postgres: resolved `CALLS`/`IMPORTS`/`EXTENDS`/`IMPLEMENTS` edges with confidence, Leiden communities, GraphRAG retrieval expansion, `code_context`/`code_impact`/`code_neighbors` MCP tools. Cross-repo edges via `qualified_name` are the differentiator. Fulfills the `CodeChunkEnrichment.symbols` "P2 graph storage" reservation. |
| P1 | [001-interactive-curation-debug-trajectory](001-interactive-curation-debug-trajectory.md) | — | Phase 1 shipped (MCP enhancement chunks, commit f2d73e5); later phases of the interactive-curation debug-trajectory flow remain. 19 open tasks. |
| P1 | [rfc-fleet-2-distributed-embedding](rfc-fleet-2-distributed-embedding.md) | rfc-fleet-1 (done) | Claim-based distributed backfill mostly shipped; `[embed] lanes` pinning, crash-recovery + two-worker integration tests, doctor check, `hosts plan` still open. 5 open tasks. |
| P1 | [rfc-fleet-7-llama-cpp-accelerated-install](rfc-fleet-7-llama-cpp-accelerated-install.md) | — | Accelerated llama-cpp wheel auto-select on install; 3 open tasks remain. |
| P2 | [rfc-bench-embed-progress](rfc-bench-embed-progress.md) | — | `bench embed` progress reporting polish; 4 open tasks remain. |

Everything else has all its task boxes checked and is archived under
[`done/`](done/) with `status: done` (13 RFCs as of 2026-06-30:
fleet-1/3/4, source-provenance, corpus-growth-controls, nlp-data-
quality-signals, eval-framework-expansion, developer-ux-verbs,
runtime-feedback, self-distillation-export, hf-dataset-inbound,
profiling-trace-source, claude-code-self-ingest-e2e). They are kept,
not deleted, for provenance.

## Sequencing notes

- **Top of queue (2026-06-30, operator-requested).** The two
  `000-codeintel-*` RFCs are the active priority and sort ahead of
  every remaining todo. **codeintel-1 (Merkle diff-sync) goes first**
  — it's pure scan-layer infra that speeds every sync, gives clean
  deletion detection, and produces the change-set that codeintel-2's
  incremental graph rebuild (`detect_changes`) consumes. **codeintel-2
  (code knowledge subgraphs) layers on top** — start with the
  `code_symbols`/`code_edges` schema + resolution pass (the existing
  flat `symbols` enrichment is the seed) and lead with the cross-repo
  angle, the corpus-forge-native differentiator.
- **Remaining todos are demoted, not dropped.** The four RFCs below
  the codeintel pair still carry genuine unchecked work; they stay
  `accepted` at lower cascade priority (sorted after `000-*`). Nothing
  was buried — only genuinely-complete RFCs moved to `done/`.
- **`done/` is provenance, not the queue.** The cascade scans the
  top-level `*.md` glob; archived RFCs under `done/` are out of the
  pick by location and by `status: done`.

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
to the `.planning/rfcs/done/` subdirectory once shipped).
