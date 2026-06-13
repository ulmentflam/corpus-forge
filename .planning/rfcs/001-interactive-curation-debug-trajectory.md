---
status: accepted
sized: true
title: Interactive curation & debug-trajectory capture for RL
created: 2026-06-13
accepted_on: 2026-06-13
author: nightly-seed
source: interactive_seed
---

# RFC 001 — Interactive curation & debug-trajectory capture for RL

## Status

`accepted` — operator seed (2026-06-13). The curation loop today can
only *edit* existing chunks; runtime execution today is single-shot
(`run_chunk_and_capture` → one feedback row). This RFC closes the gap
between those two: let a curation conversation **mint new chunks**, and
capture the **full error → fix → success trajectory** of an interactive
debugging session as first-class, exportable corpus data — the
highest-signal rows for the operator's RL/SDFT dataset.

The filename is intentionally `001-…` so it sorts ahead of the
`rfc-fleet-*` family under the cascade's alphabetical pick: this is the
operator's current top-of-queue seed.

## Context

The operator's seed names four capabilities the corpus loop is missing:

1. **Curation should create data, not just edit it.** After the
   curation loop asks its labeling questions, the model should be able
   to write a *new* chunk that captures the conversation + the
   recommended enhancement, persisted to the DB via MCP. Today
   `commit_curation` only mutates labels / metadata / description /
   feedback on an *existing* chunk (`corpus_forge/mcp/writes.py`).
   `append_conversation` / `append_message` exist but are generic — there
   is no "turn this curation exchange into a linked enhancement chunk"
   primitive. The Explore pass confirmed: *no existing API for synthetic
   chunk creation.*

2. **Executable snippets should be run and debugged interactively.**
   `rfc-runtime-feedback-exec-and-profile` (shipped) gave us
   `corpus_forge/execfeedback/` — a `Sandbox` ABC, `SubprocessSandbox`
   (rlimit + tempdir + scrubbed env), `LocalVenvSandbox`,
   `ProfileCapture`, `snippet.py`, `runner.py`, the `run_chunk_and_capture`
   MCP tool, and the `corpus-forge exec-chunk` CLI. That is **single
   shot**: run once, attach one `kind: "execution"` feedback row. It does
   *not* model the iterative loop the operator describes — run, hit a
   traceback, discuss/edit, re-run, converge.

3. **An interactive mode that collects live debugging sessions** — via
   MCP or by reading terminal history — so tracebacks and their fixes
   become corpus rows.

4. **The RL-dataset thesis.** The most valuable corpus pieces are *runs*
   tied to code that errored, plus the traceback, plus the conversation
   or edit that corrected it to a working final state. Immediate
   successes are useful but lower-signal; a recovered failure carries the
   reasoning. This RFC's trajectory record is shaped to make that
   chosen/rejected structure fall out for free in
   `rfc-self-distillation-export`'s `export_feedback_pairs`.

### What already exists (do not rebuild)

- **Sandbox + single-shot exec**: `corpus_forge/execfeedback/`
  (`sandbox.py`, `profile.py`, `snippet.py`, `runner.py`) and the
  `run_chunk_and_capture` MCP tool + `exec-chunk` CLI. Phase 2 *composes*
  these into a multi-step loop; it does not reimplement the sandbox.
- **Write surface**: `corpus_forge/mcp/writes.py` —
  `append_conversation`, `append_message`, `add_feedback`,
  `commit_curation`, `record_demonstration`, plus the
  `audit_event` + `_link_to_session` plumbing every write goes through.
- **Curation selector**: `corpus_forge/curation/selector.py`
  (`next_curation_target`, `next_curation_batch`, `CurationTarget`).
- **Chunk schema**: `corpus.chunks` (alembic `0001_core.py`) —
  `document_id` XOR `conversation_id`, `chunk_index`, `text`, `heading`,
  `role`, `token_count`, `metadata` JSON. Latest revision is `0020`;
  new tables land at `0021_*` and onward (Postgres + SQLite dialect
  split, per the established pattern).
- **Export**: `corpus_forge/exports/feedback_pairs.py` +
  `export_feedback_pairs` (DPO/KTO/ORPO), `corpus_forge/exports/sdft.py`.

## Non-goals

- **No new sandbox tech.** Phase 2 uses the existing `SubprocessSandbox`
  / `LocalVenvSandbox`. No Docker/MicroVM (that stays a follow-up to the
  runtime-feedback RFC).
- **No autonomous code patching of the user's source files.** The
  trajectory captures proposed edits as data; it never writes back to
  the user's repo. (Consistent with runtime-feedback's "captured, never
  patched".)
- **No new embedder / retrieval work.** New chunks ride the existing
  embed backfill lane; they get embeddings the same way ingested chunks
  do.
- **No training.** We *capture* and *export*; the trainer is downstream
  (per `rfc-self-distillation-export`).
- **No always-on terminal keylogger.** Terminal-history capture (Phase 4)
  is opt-in, explicit-invocation only, and redacts via the existing
  `corpus_forge/diagnostics/redact.py`.

## Proposed direction

Four phases, each independently shippable and each landing as its own PR
(or local proposal). Phase 1 delivers the operator's headline ask and
depends on nothing new. Phases 2–4 build the debugging-trajectory and
interactive-mode story on top of the already-shipped sandbox.

**A. Enhancement chunks (Phase 1).** A new MCP write tool,
`create_enhancement_chunk`, that takes a source `chunk_id`, the curation
conversation turns, and the recommended enhancement, and persists a new
chunk into the same dataset — linked back to the source via metadata
(`{"derived_from_chunk_id": N, "kind": "curation_enhancement"}`) so
retrieval and export can find the lineage. Backed by a new
`backend.append_enhancement_chunk(...)` method. Routes through the same
`audit_event` + `_link_to_session` plumbing as every other write, gated
by `writes_enabled`. The `corpus-curate` skill gains a step-6 ("mint an
enhancement chunk") and the curation MCP playbook documents it.

**B. Debug-trajectory record (Phases 2–3).** A `run_trajectories` table
(one row per debugging session) + `trajectory_steps` (one row per
run/edit/observation). A new MCP tool family
(`begin_trajectory` / `record_trajectory_step` / `finalize_trajectory`)
lets an assistant capture: initial snippet, each sandbox run + its
stdout/stderr/traceback (reusing `execfeedback.runner`), each proposed
edit + rationale, and the terminal state (`resolved` / `gave_up` /
`already_passing`). `finalize_trajectory` derives an export-ready
`(prompt, chosen, rejected)` shape: the failing step is `rejected`, the
converged step is `chosen`. The `corpus-forge interactive` shell (Phase 3)
drives the loop against a chunk or a free-form snippet.

**C. Terminal-history source (Phase 4).** `corpus-forge interactive
--capture-shell` and a `corpus-forge ingest-history` verb that reads a
provided shell-history window (commands + exit codes + captured output),
redacts it, and ingests failure→fix windows as trajectory rows. Opt-in,
never a daemon.

Alternatives considered: (1) overload `add_feedback` with a `kind:
"trajectory"` JSON blob — rejected: a multi-step trajectory needs its own
queryable rows for export, not one opaque JSON column. (2) Reuse
`append_conversation` for enhancement chunks — viable but loses the
explicit source-chunk lineage and the curation-specific metadata; a thin
dedicated tool is clearer and keeps the skill playbook honest.

## Resolved technical decisions

1. **Enhancement chunks attach to the dataset's curation document, not a
   conversation.** A new chunk needs `document_id` XOR `conversation_id`.
   We create (once per dataset, lazily) a synthetic document
   `source_uri = "corpus-forge://curation/<dataset>"`,
   `kind = "curation_enhancement"`, and append enhancement chunks to it.
   Rationale: keeps them queryable as a cohesive synthetic source,
   embeddable on the normal lane, and out of the way of file-ingested
   docs.
2. **Lineage lives in chunk `metadata`, not a new FK column.** Phase 1
   ships with zero schema migration by storing
   `{"derived_from_chunk_id", "kind", "curation_session_id"}` in the
   existing `metadata` JSON. Rationale: smallest first cut; a typed FK
   can come later if querying lineage by index becomes hot.
3. **Trajectory tables are new (Phase 2), dialect-split at `0021_*`.**
   `run_trajectories` + `trajectory_steps`, Postgres (JSONB/TIMESTAMPTZ/
   BIGSERIAL) + SQLite (TEXT/ISO-8601/INTEGER) per the `0017–0020`
   pattern, idempotent up/down.
4. **Trajectory steps reuse `execfeedback.runner`** for the actual run;
   the trajectory layer only orchestrates and records. No second runner.
5. **Every write goes through `audit_event` + `_link_to_session`** and
   honors `dry_run` + `writes_enabled`, identical to the existing tools.
6. **Export shape is derived, not stored twice.** `finalize_trajectory`
   computes the `(prompt, chosen, rejected)` projection on read in
   `export_feedback_pairs`; the table stores raw steps.
7. **Redaction is mandatory on terminal capture** —
   `corpus_forge/diagnostics/redact.py` scrubs absolute paths and
   secret-shaped tokens before any row is written.

## Risks

- **Synthetic chunks polluting retrieval/eval.** Mitigation: the
  `kind: "curation_enhancement"` metadata + synthetic source_uri let
  search filters and the eval harness exclude or weight them explicitly;
  document the filter in the curate skill.
- **Embedding cost of new chunks.** Mitigation: they ride the existing
  claim-based backfill lane; no special path. Volume is human-paced
  (one per curation commit), negligible against ingest.
- **Trajectory tables drift from the export reader.** Mitigation: the
  derived-projection decision (#6) keeps one source of truth; an
  integration test pins the round-trip.
- **Terminal capture leaking secrets.** Mitigation: decision #7 +
  opt-in-only; default off; no daemon.
- **Cascade double-counts phases.** Mitigation: each phase is its own PR;
  bundle adjacent phases only when they naturally compose (per Rule 11).

## Implementation phases

- **Phase 1 — Enhancement chunks via MCP (~4h).** Backend method + MCP
  tool + skill update + tests. Merge gate: `create_enhancement_chunk`
  dry-run and real-write both round-trip; a `get_chunk` on the new id
  surfaces the lineage metadata; `nightly verify` green. **Ships first.**
- **Phase 2 — Trajectory schema + record tools (~6h).** Alembic `0021`,
  backend CRUD, `begin/record_step/finalize` MCP tools, unit tests.
  Merge gate: a synthetic 3-step trajectory (fail → edit → pass)
  persists and reads back; dialect parity test (Postgres + SQLite).
- **Phase 3 — `corpus-forge interactive` shell (~6h).** Typer command
  that drives curate + exec + trajectory with MCP write-through; uses
  `execfeedback.runner` for runs. Merge gate: scripted session against a
  failing fixture chunk produces a finalized `resolved` trajectory.
- **Phase 4 — Terminal-history capture + export wiring (~5h).**
  `ingest-history` verb + `--capture-shell`, redaction, and
  `export_feedback_pairs` learning to read trajectory rows. Merge gate:
  a failure→fix history window exports as a valid DPO row.

## Sized checklist

### Phase 1 — Enhancement chunks via MCP

- [ ] `backend.append_enhancement_chunk(dataset_id, text, *, heading, role, derived_from_chunk_id, metadata)` on `backends/base.py` (ABC) + `backends/sqlite.py` and the Postgres backend; lazily creates the `corpus-forge://curation/<dataset>` synthetic document.
- [ ] `create_enhancement_chunk(...)` dispatch in `corpus_forge/mcp/writes.py` — resolves dataset, writes chunk, stamps lineage metadata, emits `audit_event` + `_link_to_session`, honors `dry_run`.
- [ ] Register the tool in `corpus_forge/mcp/server.py` (schema in the tool-defs block + dispatch in `@server.call_tool()`), gated by `writes_enabled` like `commit_curation`.
- [ ] Unit tests: dry-run returns `chunk_id: None` + audit row; real write returns a new `chunk_id`; lineage metadata present; `writes_enabled=False` hides the tool.
- [ ] Update `.claude/skills/corpus-curate/SKILL.md` — add a "mint an enhancement chunk" step + the tool to `allowed-tools`; note the `kind: "curation_enhancement"` retrieval filter.
- [ ] Update `CLAUDE.md` / `AGENTS.md` curation-loop quickstart to mention `create_enhancement_chunk`.
- [ ] CHANGELOG entry.

### Phase 2 — Debug-trajectory schema + record tools

- [ ] Alembic `0021_run_trajectories.py` — `run_trajectories` + `trajectory_steps`, Postgres + SQLite dialect split, idempotent up/down.
- [ ] Backend CRUD for trajectories + steps on `backends/base.py` + both impls.
- [ ] MCP tools `begin_trajectory` / `record_trajectory_step` / `finalize_trajectory` in `writes.py` + `server.py`, `writes_enabled`-gated, audited.
- [ ] `record_trajectory_step` reuses `corpus_forge/execfeedback/runner.py` for the sandboxed run; no second runner.
- [ ] Unit tests: 3-step fail→edit→pass round-trips; dialect parity; `finalize_trajectory` sets terminal state.

### Phase 3 — `corpus-forge interactive` shell

- [ ] `corpus-forge interactive [--chunk <id>] [--mode sandbox|local]` Typer command driving the curate+exec+trajectory loop with MCP write-through.
- [ ] Drives `execfeedback.runner`; records each run/edit as a trajectory step; finalizes on exit.
- [ ] Tests: scripted session against a failing fixture chunk yields a finalized `resolved` trajectory.

### Phase 4 — Terminal-history capture + export

- [ ] `corpus-forge ingest-history` verb + `interactive --capture-shell`; reads a history window (cmd + exit code + output), redacts via `diagnostics/redact.py`, ingests failure→fix windows as trajectory rows.
- [ ] Teach `export_feedback_pairs` to read trajectory rows → `(prompt, chosen=converged, rejected=failing)` DPO/KTO/ORPO rows.
- [ ] Integration test: a failure→fix window exports as a valid DPO row.
- [ ] CHANGELOG entry.

## Verification

- `create_enhancement_chunk(chunk_id=<weak>, ...)` (dry-run then real)
  produces a new chunk under `corpus-forge://curation/<dataset>` whose
  metadata carries `derived_from_chunk_id`; `get_chunk` surfaces it.
- A scripted 3-step trajectory (`1/0` → fix → pass) persists and
  `finalize_trajectory` reports `resolved`.
- `export_feedback_pairs --dataset <name> --format dpo` emits a row whose
  `rejected` is the failing snippet and `chosen` is the fixed one.
- `nightly verify` green at every phase boundary.

## References

- Write surface: `corpus_forge/mcp/writes.py`
  (`append_conversation`, `commit_curation`, `add_feedback`,
  `record_demonstration`, `audit_event`, `_link_to_session`).
- MCP registration: `corpus_forge/mcp/server.py`
  (`build_server`, `@server.list_tools()`, `@server.call_tool()`).
- Sandbox + single-shot exec: `corpus_forge/execfeedback/`,
  `rfc-runtime-feedback-exec-and-profile.md`.
- Export: `corpus_forge/exports/feedback_pairs.py`,
  `corpus_forge/exports/sdft.py`, `rfc-self-distillation-export.md`.
- Chunk schema: `corpus_forge/alembic/versions/0001_core.py`; migration
  pattern: `0017`–`0020`.
- Selector: `corpus_forge/curation/selector.py`.
- Redaction: `corpus_forge/diagnostics/redact.py`.
- Curate skill: `.claude/skills/corpus-curate/SKILL.md`.
</content>
</invoke>
