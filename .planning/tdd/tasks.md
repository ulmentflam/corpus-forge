# TDD Task Board — Phase J / Slice J4 (Data-curation chat skill)

_Owner: tdd-principal. Workers: read freely. Edit only your claimed row's
`status` and `claimed_by`._

Source plan: `/Users/evanowen/Library/Mobile Documents/com~apple~CloudDocs/Workspace/playground/corpus-forge/.planning/tdd/phase_j_living_corpus.md` (§ J4).
Dispatch input: orchestrator brief, Phase J / J4 kickoff.

> Previous slice (J1) record preserved below under `## Archive — J1`.
> J2 shipped without an internal task board (pure docs slice — committed in
> `36bb68f`).

## Project gates
- lint: `make lint` (ruff)
- format: `make format-check`
- typecheck: `make typecheck` (pyrefly strict)
- test-unit: `make test-unit` (≥90% coverage)
- test-integration: `make test-integration` (testcontainers Postgres + skip-gated markers)
- ci: `make ci` (format-check + lint + typecheck + test-unit + test-integration + test-fuzz + test-smoke)

## Hard constraints (from dispatch)
1. **DO NOT COMMIT, DO NOT PUSH.** Workers stage only. Orchestrator commits.
2. `make ci` green; coverage ≥90 % on `corpus_forge/curation/`.
3. Local-or-remote URL invariant: reuse the existing `Reranker` protocol
   (cross_encoder OR ollama); both already accept configurable URLs. Do
   NOT introduce a new model client. Centroid-similarity fallback uses
   pure NumPy / SQL.
4. `commit_curation` REUSES the existing MCP write surface
   (`_dispatch_add_label`, `_dispatch_remove_label`, `_dispatch_set_metadata`,
   `_dispatch_set_description`, `_dispatch_add_feedback`). No new write logic.
5. `writes_enabled` gating: `commit_curation` honors it. The two read
   tools (`next_curation_target`, `next_curation_batch`) are NOT gated.
6. Score weights non-negotiable for this ship:
   - confidence_deficit × 0.35
   - missing_metadata × 0.30
   - ranker_elevation × 0.25
   - freshness × 0.10
   - Final score is clipped to [0, 1].
7. No drive-by refactors. Surfaces bounded to:
   - new `corpus_forge/curation/__init__.py`
   - new `corpus_forge/curation/selector.py`
   - new `corpus_forge/curation/prompts.py`
   - additions to `corpus_forge/mcp/server.py` (three new tools)
   - new `.claude/skills/corpus-curate/SKILL.md`
   - new `.opencode/command/corpus-curate.md`
   - new `.gemini/agents/corpus-curate.md` (greenfield dir)
   - new `tests/unit/test_curation_selector.py`
   - new `tests/unit/test_mcp_curation_tools.py`
   - new `tests/integration/test_curation_e2e.py`
   - rot-detector test updates (`tests/smoke/test_skill_tool_contract.py`,
     `tests/smoke/test_mcp_writes_disabled_by_default.py`,
     `tests/smoke/test_mcp_stdio.py`, and any
     `tests/unit/test_mcp_*.py` pinned-tool-list shims) to include the
     three new tools.
   - `CHANGELOG.md` `[Unreleased]` — single new bullet under the existing
     `#### Phase J — Living Corpus` subhead.

## Tasks

| id | title | depends_on | surface | risk | status | claimed_by | notes |
|----|-------|------------|---------|------|--------|------------|-------|
| J4-01 | Curation core (`selector` + `prompts` + pkg `__init__`) | — | `corpus_forge/curation/__init__.py` (new), `corpus_forge/curation/selector.py` (new), `corpus_forge/curation/prompts.py` (new), `tests/unit/test_curation_selector.py` (new) | med | green | tdd-principal | 47/47 tests pass. Pure-function selector; `iter_curation_candidates` hook path + generic `_execute` fallback; no heavy ML imports at package load. |
| J4-02 | MCP tools — `next_curation_target` / `next_curation_batch` / `commit_curation` | J4-01 | `corpus_forge/mcp/server.py` (3 schemas + 3 tool entries + 3 dispatches; `commit_curation` is internal composition over existing write dispatchers), `tests/unit/test_mcp_curation_tools.py` (new) | med | green | tdd-principal | 24/24 tests pass. `commit_curation` honours `writes_enabled`, sentinel-distinguishes "set_description omitted" vs. "set_description=null", and surfaces inner-write failures as `isError` with the failing `chunk_id`. |
| J4-03 | Skill assets (Claude / OpenCode / Gemini) | — | `.claude/skills/corpus-curate/SKILL.md` (new), `.opencode/command/corpus-curate.md` (new), `.gemini/agents/corpus-curate.md` (new dir) | low | green | tdd-principal | Three files landed with mirrored playbook + citation format; Gemini file carries a docs-URL placeholder per project memory. |
| J4-04 | Rot-detectors + integration test + CHANGELOG | J4-01, J4-02, J4-03 | `tests/smoke/test_skill_tool_contract.py`, `tests/smoke/test_mcp_writes_disabled_by_default.py`, `tests/smoke/test_mcp_stdio.py`, `tests/unit/test_mcp_server.py`, `tests/unit/test_mcp_server_enrichment.py` (rot-detector updates), `tests/integration/test_curation_e2e.py` (new), `CHANGELOG.md` | low | green | tdd-principal | 5 rot-detectors updated; 3 e2e cases pass against in-memory SQLite; CHANGELOG `[Unreleased]` Phase J subhead gains the J4 bullet. |

## Acceptance details

### J4-01 — Curation core

**Module layout:**

```
corpus_forge/curation/
├── __init__.py         # re-exports CurationTarget, next_curation_target, next_curation_batch
├── selector.py         # the score engine + DB walk
└── prompts.py          # shared chat-loop prompt template (vendor-neutral)
```

**`corpus_forge/curation/selector.py` — REQUIRED public API:**

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


_MISSING_METADATA_FIELDS = (
    "title",        # documents.title
    "heading",      # chunks.heading
    "labels",       # chunk_labels rows
    "description",  # chunks.description
    "language",     # chunks.metadata.language
    "source_uri",   # documents.source_uri (must have a known suffix)
)
# total = 6 fields; missing_metadata_score = count_missing / 6

_SCORE_WEIGHTS = {
    "confidence_deficit": 0.35,
    "missing_metadata":   0.30,
    "ranker_elevation":   0.25,
    "freshness":          0.10,
}


@dataclass(frozen=True)
class ScoreBreakdown:
    confidence_deficit: float   # in [0, 1]
    missing_metadata:   float   # in [0, 1]
    ranker_elevation:   float   # in [0, 1]
    freshness:          float   # in [0, 1]


@dataclass(frozen=True)
class CurationTarget:
    chunk_id: int
    document_id: int | None
    text: str
    heading: str | None
    current_labels: list[tuple[str, str]]   # (namespace, value) pairs
    current_metadata: dict[str, Any]
    missing_fields: list[str]                # subset of _MISSING_METADATA_FIELDS
    classifier_confidence: float | None
    score: float
    score_breakdown: ScoreBreakdown
    selection_reason: str                    # human one-liner naming the top contributor


@dataclass(frozen=True)
class CurationBatch:
    cohesion_score: float                    # in [0, 1]
    grouping_key: tuple[str, str]            # (source_uri_stem, classifier_label)
    targets: list[CurationTarget]


def next_curation_target(
    *,
    backend: Any,
    dataset: str | None = None,
    embedder: str | None = None,
    seed_query: str | None = None,
    reranker: Any | None = None,
    candidate_pool: int = 200,
) -> CurationTarget | None: ...


def next_curation_batch(
    *,
    backend: Any,
    dataset: str | None = None,
    embedder: str | None = None,
    seed_query: str | None = None,
    reranker: Any | None = None,
    candidate_pool: int = 200,
    limit: int = 10,
) -> CurationBatch | None: ...
```

**Selector behavior contract:**

1. Pull a candidate pool of `candidate_pool` chunks from the backend.
   The MVP uses a single SQL query that returns: `chunk_id, document_id,
   text, heading, description, metadata, created_at, source_uri,
   document_title`. Workers MAY add a new backend method
   `iter_curation_candidates(dataset, limit) -> Iterable[dict]` if a
   clean abstraction is helpful (allowed surface for J4-01 — it's
   adjacent to the curation core); otherwise, do the SQL inline via
   `backend._execute(...)` like other `corpus_forge/curation/` modules
   do not yet exist so this is greenfield.

   Discover what's available by reading
   `corpus_forge/backends/base.py` + the Postgres / SQLite implementations.
   If you need a new backend method, add it to BOTH `postgres.py` AND
   `sqlite.py` and to the base protocol — keeping the two backends in
   lockstep is a hard project rule.

2. For each candidate, compute the 4-tuple of sub-scores:
   - `confidence_deficit`: pull the chunk's classifier label confidence
     from `chunk_labels.confidence` where the joined `labels` row has
     `namespace='class'`. If none exists, treat as `classifier_confidence
     = None` and `confidence_deficit = 1.0` (the chunk has not even been
     classified yet — maximally interesting).
   - `missing_metadata`: count empty / null across the six fields listed
     in `_MISSING_METADATA_FIELDS`. The `source_uri` field counts as
     missing when its suffix isn't in the extractor heuristic table
     OR when the value is None. Score = `count_missing / 6`.
   - `ranker_elevation`:
     - If `seed_query` is provided AND `reranker` is provided, call
       `reranker.rerank(seed_query, hits=[fake_hit_for_chunk], top_n=1)`
       and use the returned score (normalised to [0, 1] by dividing
       max-observed-this-batch).
     - If `seed_query` is None: compute the chunk's cosine distance
       from the **dataset centroid** via pure NumPy (load the
       per-embedder vectors for the candidate set + pre-computed
       centroid). Larger distance ⇒ more anomalous ⇒ higher
       elevation potential. Normalise to [0, 1] across the candidate
       pool.
     - If neither path is workable (no reranker AND no embeddings):
       fall back to `0.5` (neutral) and emit a DEBUG log line.
   - `freshness`: chunks created in the last 7 days get 1.0; older
     decays linearly to 0.0 at 180 days; clamp.
3. Apply the weights from `_SCORE_WEIGHTS`. Final score is clipped to
   `[0, 1]` (the weighted sum is already in that range when each
   component is, but defend in code).
4. `selection_reason` is a one-line string naming the SINGLE highest
   weighted contributor: e.g. `"missing 4 of 6 metadata fields"` or
   `"classifier confidence 0.18"` or `"seed-query reranker score 0.91"`
   or `"chunk is anomalous vs dataset centroid"` or `"newly ingested
   (<7d)"`.
5. `next_curation_target` returns the single highest-scoring `CurationTarget`,
   or `None` when the candidate pool is empty.
6. `next_curation_batch`:
   - Compute scores for the whole candidate pool.
   - Group by `(source_uri_stem, classifier_label)` where
     `source_uri_stem` = `Path(source_uri).stem` (falls back to
     `"<unknown>"` when source_uri is None) and `classifier_label` is
     the assigned `class` namespace label value (or `"<unclassified>"`).
   - Pick the group with the highest **mean score** that has at least 1
     target and at most `limit` members. Pad/truncate to `limit`.
   - `cohesion_score` is the inverse of the score variance within the
     group, normalised to [0, 1] via `1.0 / (1.0 + variance)`. Empty /
     single-element groups get `cohesion_score = 1.0`.
   - Returns `CurationBatch` or `None`.

**`corpus_forge/curation/prompts.py`:**

A single module-level string constant `CURATION_CHAT_TEMPLATE` that
encodes the vendor-neutral chat-loop prompt. Shape:

```
You are helping the user fortify a single corpus entry. Use the
five-step loop:

1. Call next_curation_target (or next_curation_batch if the user said
   "let's batch") — pass dataset= if known.
2. Present the entry: text, current labels, missing fields, and the
   one-line selection_reason that explains why this entry was picked.
3. Ask AT MOST 3 focused questions about what to fix:
   - Should we add or remove labels?
   - Does the heading or description need correcting?
   - Is there factual feedback worth recording?
4. On user confirm, call commit_curation with the change set in one
   atomic call.
5. Loop: ask "next one?" — yes -> step 1, no -> short summary of
   changes this session.

Citations: when you show text from a chunk, attribute it as
"From {title} ({source_uri}): {quote}". Keep quotes terse.
```

The string MUST be the SAME content the four skill assets reference
(workers in J4-03 will copy or link to this same template — keep the
phrasing consistent). The module exports this constant and nothing else.

**`corpus_forge/curation/__init__.py`:**

Re-export `CurationTarget`, `CurationBatch`, `ScoreBreakdown`,
`next_curation_target`, `next_curation_batch`, `CURATION_CHAT_TEMPLATE`.

**Tests `tests/unit/test_curation_selector.py` (≥30 cases) — REQUIRED scenarios:**

Use a FAKE backend (in-memory dict). Mock nothing else. Pure-function
tests over the score math + grouping.

1. `test_confidence_deficit_no_classifier_label_is_one` — chunk with no
   `namespace='class'` label → `confidence_deficit=1.0`.
2. `test_confidence_deficit_high_confidence_is_low` — chunk with
   classifier confidence 0.9 → `confidence_deficit ≈ 0.1`.
3. `test_confidence_deficit_clamped_below_zero` — confidence=1.0 → 0.0.
4. `test_confidence_deficit_clamped_above_one` — confidence=None handled.
5. `test_missing_metadata_all_six_missing` → `missing_metadata=1.0`.
6. `test_missing_metadata_none_missing` → `missing_metadata=0.0`.
7. `test_missing_metadata_partial` — 3 of 6 missing → `0.5`.
8. `test_missing_metadata_unknown_source_uri_suffix_counts_missing` —
   `.xyz` extension → source_uri counts as missing.
9. `test_missing_metadata_no_labels_counts_missing`.
10. `test_freshness_today_is_one` — created_at = now → freshness=1.0.
11. `test_freshness_seven_days_old_still_one` (boundary; ≤7d).
12. `test_freshness_180_days_old_is_zero`.
13. `test_freshness_one_year_old_clamped_zero`.
14. `test_ranker_elevation_with_seed_query_calls_reranker` — fake
    reranker records call args; assert query + hits.
15. `test_ranker_elevation_without_seed_query_uses_centroid_distance` —
    backend supplies vectors; selector computes cosine distance.
16. `test_ranker_elevation_no_reranker_no_vectors_is_neutral_half`.
17. `test_total_score_weights_match_spec` — given controlled sub-scores
    of (1, 1, 1, 1) the total = 1.0 exactly; (0,0,0,0) = 0.0.
18. `test_total_score_clamped_to_unit_interval`.
19. `test_selection_reason_names_top_contributor_confidence`.
20. `test_selection_reason_names_top_contributor_missing_metadata`.
21. `test_selection_reason_names_top_contributor_ranker`.
22. `test_selection_reason_names_top_contributor_freshness`.
23. `test_next_curation_target_picks_highest_score`.
24. `test_next_curation_target_returns_none_on_empty_pool`.
25. `test_next_curation_target_passes_dataset_filter_to_backend`.
26. `test_next_curation_batch_groups_by_source_stem_and_class_label`.
27. `test_next_curation_batch_respects_limit`.
28. `test_next_curation_batch_cohesion_high_when_scores_close`.
29. `test_next_curation_batch_cohesion_lower_when_scores_spread`.
30. `test_next_curation_batch_returns_none_on_empty_pool`.
31. `test_next_curation_batch_single_target_cohesion_is_one`.
32. `test_curation_target_dataclass_frozen` — mutation raises.
33. `test_score_breakdown_dataclass_frozen`.
34. `test_curation_batch_dataclass_frozen`.
35. `test_curation_chat_template_constant_is_nonempty_string` (lives
    in `prompts.py` but the unit test file covers it for proximity).
36. `test_selector_does_not_run_classifier_or_embedder` — patches
    classifier registry and embedder factory to raise on call; selector
    still works (proves selector reads stored state only).

If the implementation chooses to add a backend method
(`iter_curation_candidates`), also add at minimum 2 tests:
- `test_iter_curation_candidates_sqlite_smoke` — in-memory SQLite, 3
  chunks, returns expected fields.
- `test_iter_curation_candidates_filters_by_dataset`.

(Coverage gate: ≥90 % on `corpus_forge/curation/`.)

### J4-02 — MCP tools

In `corpus_forge/mcp/server.py`, mirror the `estimate_sync_size` pattern:

1. Add three schema constants near `_ESTIMATE_SYNC_SIZE_INPUT_SCHEMA`:

   ```python
   _NEXT_CURATION_TARGET_INPUT_SCHEMA: dict[str, Any] = {
       "type": "object",
       "properties": {
           "dataset": {"type": "string"},
           "embedder": {"type": "string"},
           "seed_query": {"type": "string"},
       },
       "additionalProperties": False,
   }

   _NEXT_CURATION_BATCH_INPUT_SCHEMA: dict[str, Any] = {
       "type": "object",
       "properties": {
           "dataset": {"type": "string"},
           "embedder": {"type": "string"},
           "seed_query": {"type": "string"},
           "limit": {"type": "integer", "minimum": 1},
       },
       "additionalProperties": False,
   }

   _COMMIT_CURATION_INPUT_SCHEMA: dict[str, Any] = {
       "type": "object",
       "properties": {
           "chunk_id":  {"type": "integer"},
           "chunk_ids": {"type": "array", "items": {"type": "integer"}},
           "add_labels":     {"type": "array", "items": {"type": "object",
               "properties": {"namespace": {"type": "string"},
                              "value":     {"type": "string"},
                              "confidence":{"type": "number"}},
               "required": ["namespace", "value"], "additionalProperties": False}},
           "remove_labels":  {"type": "array", "items": {"type": "object",
               "properties": {"namespace": {"type": "string"},
                              "value":     {"type": "string"}},
               "required": ["namespace", "value"], "additionalProperties": False}},
           "set_metadata":   {"type": "object"},
           "set_description":{"type": ["string", "null"]},
           "feedback":       {"type": "object",
               "properties": {"kind":   {"type": "string"},
                              "rating": {"type": "integer"},
                              "text":   {"type": "string"}},
               "required": ["kind"], "additionalProperties": False},
           "dry_run":        {"type": "boolean"},
       },
       "additionalProperties": False,
   }
   ```

   Argument exclusivity: exactly one of `chunk_id` or `chunk_ids` must
   be present — validate inside the dispatcher (the JSON Schema oneOf
   would work but inflate complexity). Return `_error_result(...)` when
   both or neither are present.

2. Register the three tools in `_list_tools`:
   - `next_curation_target` and `next_curation_batch` go in the
     **always-available read tool block** (between `estimate_sync_size`
     and `render_conversation`).
   - `commit_curation` goes inside the `if writes_enabled:` block,
     **adjacent to the other write tools** (after `add_feedback`,
     before `register_template`).

3. Wire `_call_tool` dispatch:
   - read block: `if name == "next_curation_target": return await _dispatch_next_curation_target(arguments)`
   - read block: `if name == "next_curation_batch":  return await _dispatch_next_curation_batch(arguments)`
   - write block: `if name == "commit_curation":    return await _dispatch_commit_curation(arguments)`

4. Implement `_dispatch_next_curation_target` and
   `_dispatch_next_curation_batch`:
   - Lazy-import the selector module.
   - Acquire backend via `_get_write_backend()` (read tools that need
     SQL access — same pattern as `_dispatch_list_chat_templates`).
   - Resolve a reranker iff the call has `seed_query` set:
     - Reuse `_build_reranker_from_config(Config.load())` from
       `corpus_forge.cli` (lazy-import). Local-or-remote URL is already
       resolved inside that factory.
     - Wrap the call in try/except — if the reranker can't be built
       (no eval extras installed), pass `reranker=None` and let the
       selector fall back to centroid distance.
   - Return `{"target": asdict(target)}` or `{"target": None}` for the
     single-item tool, and `{"batch": asdict(batch)}` or
     `{"batch": None}` for the batch tool. On `ValueError` /
     `FileNotFoundError` -> `_error_result(str(exc))`.

5. Implement `_dispatch_commit_curation` (only reachable when
   `writes_enabled=True`):
   - Validate `chunk_id` xor `chunk_ids` — error if both/neither.
   - Normalise to `chunk_ids: list[int]`.
   - For each chunk_id, iterate the provided write groups and call the
     existing dispatcher helpers DIRECTLY (NOT via the MCP wire):
     - For each `add_labels` entry: call `_dispatch_add_label(...)` with
       `entity_type="chunk"`, `entity_id=chunk_id`.
     - For each `remove_labels` entry: call `_dispatch_remove_label(...)`.
     - For each `set_metadata` key/value pair: call
       `_dispatch_set_metadata(...)`.
     - For `set_description`: call `_dispatch_set_description(...)`.
     - For `feedback`: call `_dispatch_add_feedback(...)`.
   - Aggregate counts; return:
     ```python
     {"writes": {
         "add_label":       <int>,
         "remove_label":    <int>,
         "set_metadata":    <int>,
         "set_description": <int>,
         "add_feedback":    <int>,
       },
       "chunk_ids_processed": [...],
       "dry_run": <bool>,
       "audit_ids": [...],  # all audit ids emitted, in order
     }
     ```
   - Any failure in an inner write -> return `_error_result(...)`
     containing the failed chunk_id and write kind. NOTE: this is a
     best-effort multi-write, NOT a transactional atomic. Document the
     non-transactional behavior in the dispatcher docstring; the brief
     says "atomic multi-write" but the existing write surface is per-
     call. Workers should follow up with the orchestrator if a stricter
     atomicity is required — for J4 ship the best-effort version.

**Tests `tests/unit/test_mcp_curation_tools.py`:**

Pattern matches `tests/unit/test_mcp_estimate.py` — in-process
`build_server` + a fake retriever / backend. Required cases:

1. `test_next_curation_target_in_list_tools_always` — appears with
   `writes_enabled=False`.
2. `test_next_curation_batch_in_list_tools_always`.
3. `test_commit_curation_only_when_writes_enabled` — absent when
   `writes_enabled=False`; present when `True`.
4. `test_next_curation_target_dispatch_calls_selector` —
   monkeypatch `corpus_forge.curation.selector.next_curation_target`.
5. `test_next_curation_target_returns_target_under_key` — payload
   shape matches `{"target": {...}}`.
6. `test_next_curation_target_handles_none_result`.
7. `test_next_curation_target_passes_seed_query_through_to_reranker_builder`.
8. `test_next_curation_target_no_seed_query_skips_reranker_build` — assert
   the reranker builder is NOT called when `seed_query` is absent.
9. `test_next_curation_batch_dispatch_calls_selector`.
10. `test_next_curation_batch_default_limit_is_10`.
11. `test_commit_curation_requires_xor_chunk_id` — both/neither -> error.
12. `test_commit_curation_single_chunk_routes_through_existing_writes` —
    monkeypatch the five existing dispatchers; assert each is called
    with the right `entity_type='chunk'` + `entity_id`.
13. `test_commit_curation_bulk_routes_each_chunk_id`.
14. `test_commit_curation_returns_count_per_kind`.
15. `test_commit_curation_dry_run_propagates_to_inner_dispatchers`.
16. `test_commit_curation_error_in_inner_write_surfaces_clean_error`.
17. `test_next_curation_target_schema_rejects_extra_args`.
18. `test_commit_curation_schema_rejects_extra_args`.
19. `test_commit_curation_when_writes_disabled_returns_unknown_tool` —
    the dispatch path is unreachable; calling the tool over the wire
    yields `isError=True` or an unknown-tool message.

(Coverage gate: keep `corpus_forge/mcp/server.py` overall coverage
green; the new branch coverage is checked via the dispatcher tests
above.)

### J4-03 — Skill assets

Three new files. Tone + format MUST mirror
`.claude/skills/corpus-forge-search/SKILL.md` and
`.opencode/command/corpus-forge-search.md` (terse, citation-disciplined,
decisive — see the SKILL.md frontmatter for the schema).

**`.claude/skills/corpus-curate/SKILL.md`:**

Frontmatter:
```yaml
---
name: corpus-curate
description: Run the corpus-forge data-improvement chat loop. Use when the user wants to fortify low-confidence or metadata-poor entries — find the weakest, talk through the fix, and commit edits via MCP.
allowed-tools:
  - mcp__corpus-forge__next_curation_target
  - mcp__corpus-forge__next_curation_batch
  - mcp__corpus-forge__commit_curation
  - mcp__corpus-forge__list_datasets
  - mcp__corpus-forge__get_chunk
  - mcp__corpus-forge__search
---
```

Body sections (mirror the search SKILL's section order):
- **What corpus-forge curation is** — three sentences.
- **When to invoke** — bullets covering "let's curate", "improve my
  data", "fix labels", "this looks under-tagged", and the
  implicit-trigger case (many recently ingested entries with low
  confidence / sparse metadata).
- **When NOT to invoke** — bullets covering "user asked a question
  needing a citation (use corpus-forge-search instead)", "user is in an
  edit/run loop", "writes_enabled is off on the server" (the curate
  loop is read-only in that case — `commit_curation` is unavailable).
- **Tool playbook** — exactly the five-step loop from
  `CURATION_CHAT_TEMPLATE`. Each step calls out the tool with its
  args and what the response looks like.
- **Response handling** — show the `CurationTarget` JSON shape so the
  assistant knows what fields to surface to the user.
- **Citation format** — same `From {title} ({source_uri}): {quote}`
  pattern as corpus-forge-search.

**`.opencode/command/corpus-curate.md`:**

Same frontmatter shape as `.opencode/command/corpus-forge-search.md`
(slash-command schema). Body content identical to the Claude SKILL.md
above, **with one paragraph swap**: the "Both skills are auto-
discovered…" type wording should refer to OpenCode commands instead.

**`.gemini/agents/corpus-curate.md`:**

This is the first file in a brand-new `.gemini/` tree. Format mirrors
the Claude SKILL.md but uses vendor-neutral phrasing. Top of the file
MUST cite a placeholder note like:

```
<!-- Format: Gemini "agent" file. Verify the Gemini CLI / Code Assist
agent-loading docs at <https://ai.google.dev/gemini-api/docs/...>.
This file is intentionally Markdown without YAML frontmatter; Gemini's
agent loader treats the heading hierarchy as the schema. -->
```

(The repo memory + Phase J brief note that the Gemini convention may
shift between now and J5; the placeholder is acceptable for the first
ship. AGENTS.md already names this path so the file MUST land at
exactly `.gemini/agents/corpus-curate.md`.)

Body content identical to the Claude SKILL.md, minus the YAML
frontmatter, minus any Claude-specific install instructions.

### J4-04 — Rot-detectors + integration test + CHANGELOG

**Rot-detector updates** — extend the pinned tool sets in three smoke
tests so the new tools are first-class:

1. `tests/smoke/test_skill_tool_contract.py`:
   - `_READ_TOOLS` already has 6 entries. ADD `next_curation_target`
     and `next_curation_batch` -> 8.
   - `_WRITE_TOOLS` already has 10 entries. ADD `commit_curation` -> 11.
   - `_ALL_15_TOOLS` constant renamed to `_ALL_19_TOOLS` (or just bump
     the cardinality and rename the assertion-error string).
   - `test_server_exposes_15_tools_when_writes_enabled` rename to
     `_19_tools` (or generalise the test name to not pin the count in
     the function name).
2. `tests/smoke/test_mcp_writes_disabled_by_default.py`:
   - `_READ_TOOL_NAMES`: add both read tools.
   - `_WRITE_TOOL_NAMES`: add `commit_curation`.
   - All three tests should pass; no behavior change beyond the
     constant update.
3. `tests/smoke/test_mcp_stdio.py`:
   - `_expected_read_tools` (around line 155) — add the two new
     curation read tools.

If unit-level `tests/unit/test_mcp_server.py` or
`tests/unit/test_mcp_server_enrichment.py` pin tool-list counts, update
them in lockstep (apply the J1 pattern documented in the J1-04 row of
the archive below).

**Integration test `tests/integration/test_curation_e2e.py` (NEW):**

End-to-end test against an SQLite in-memory backend (NO Docker
required — pattern matches the rest of `tests/integration/` that don't
need Postgres testcontainers). Skip-gate behind `@pytest.mark.integration`.

Test steps:
1. Build an in-memory SQLite backend, `backend.migrate()`.
2. Insert a tiny fixture corpus: 1 dataset, 1 document, 3 chunks with
   varying confidence / metadata completeness.
3. Spin up `build_server(retriever_builder=lambda: _StubRetriever(backend),
   writes_enabled=True)`.
4. Call `next_curation_target` (no seed_query); assert the returned
   target is the chunk with the worst combined score.
5. Call `commit_curation(chunk_id=..., add_labels=[...],
   set_description="...", set_metadata={"language": "en"},
   feedback={"kind": "rating", "rating": 4})`.
6. Re-query `next_curation_target`; assert the previously-worst chunk
   no longer scores highest (the labels + metadata fixed the
   missing_metadata sub-score).
7. Call `next_curation_batch(limit=2)`; assert `len(batch.targets) <= 2`
   and `0 <= cohesion_score <= 1`.

**CHANGELOG `[Unreleased] / ### Added / #### Phase J — Living Corpus`:**

Append (after the existing two Phase J bullets) a single new bullet:

```
- Data-curation chat skill (Claude / Gemini / OpenCode / AGENTS.md
  generic recipe) — pulls low-confidence or metadata-poor entries,
  facilitates a chat to improve them, and commits changes via MCP.
  New module `corpus_forge/curation/` (selector + shared chat-loop
  prompt). New MCP tools: `next_curation_target` /
  `next_curation_batch` (read-only) and `commit_curation` (gated by
  `writes_enabled`; reuses the existing
  `add_label`/`remove_label`/`set_metadata`/`set_description`/`add_feedback`
  write surface internally). New skill assets under
  `.claude/skills/corpus-curate/`, `.opencode/command/corpus-curate.md`,
  and the greenfield `.gemini/agents/corpus-curate.md`.
```

## DAG

- **Wave 0** (RED→GREEN, parallel): J4-01 + J4-03.
  Disjoint surfaces (Python module vs. three Markdown files).
  Fire two testers in one message (J4-03 tester writes
  presence/structure tests against the three new asset paths); fire two
  coders in one message.
- **Wave 1** (RED→GREEN): J4-02 alone — depends on J4-01's module
  symbols.
- **Wave 2** (RED→GREEN): J4-04 — rot-detectors + integration test +
  CHANGELOG. Last so it exercises the whole stack.
- **QA gate** at end of Wave 2: independent re-run of full `make ci` +
  coverage delta check + regression sweep on adjacent surfaces
  (`mcp/server.py`, smoke tests, `.claude/` / `.opencode/` / `.gemini/`
  asset paths).

## Summary — J4 closed

All four J4 tasks `done`. Slice ready for orchestrator commit.

### Files added
- `corpus_forge/curation/__init__.py` (pkg re-exports).
- `corpus_forge/curation/selector.py` (pure-function ranker; ~700 lines).
- `corpus_forge/curation/prompts.py` (shared chat-loop template).
- `.claude/skills/corpus-curate/SKILL.md` (Claude skill asset).
- `.opencode/command/corpus-curate.md` (OpenCode command asset).
- `.gemini/agents/corpus-curate.md` (Gemini agent file in greenfield `.gemini/`).
- `tests/unit/test_curation_selector.py` (47 unit cases, mock-free).
- `tests/unit/test_mcp_curation_tools.py` (24 MCP-dispatch cases).
- `tests/integration/test_curation_e2e.py` (3 e2e cases, in-memory SQLite).

### Files modified
- `corpus_forge/mcp/server.py` (3 schemas, 3 tool registrations, 3
  dispatchers, `_SENTINEL_UNSET` for "set_description omitted" vs.
  "set_description = null").
- `CHANGELOG.md` (Phase J — Living Corpus subhead gains the J4 bullet).
- `tests/smoke/test_skill_tool_contract.py` — pinned tool sets bumped
  to include the three new tools; renamed tests off the pinned count.
- `tests/smoke/test_mcp_writes_disabled_by_default.py` — read + write
  tool sets bumped.
- `tests/smoke/test_mcp_stdio.py` — expected read-tools set bumped.
- `tests/unit/test_mcp_server.py` — `test_three_tools_registered` set
  bumped (8 read tools).
- `tests/unit/test_mcp_server_enrichment.py` — both tool-count pins
  bumped (8 read, 11 write, 19 total when writes_enabled=True).
- `.planning/tdd/{tasks,code-status,test-status,qa-status}.md` updated.

### Gates run
| gate | result |
|---|---|
| `make format-check` | All formatted |
| `make lint` | All checks passed |
| `make typecheck` | 0 errors (33 suppressed, 50 warnings not shown) |
| `make test-unit` | 3534 passed / 2 skipped / 1 xfailed @ 90.24 % coverage |
| `make test-integration` | 413 passed / 3 skipped (env-gated MISTRAL) |
| `make test-smoke` | 30 passed |
| `make test-fuzz` | 15 passed |

### Deviation from brief
- **`commit_curation` atomicity:** the brief calls the tool "atomic
  multi-write." The implementation is *best-effort serial*, not a
  single transaction (each inner dispatcher emits its own audit row).
  This is documented in the dispatcher docstring and the CHANGELOG;
  stricter transactional atomicity is left for a Phase J+ follow-up.
- **Selector backend hook:** the brief allowed adding a backend method
  if helpful. The selector calls a *duck-typed*
  `iter_curation_candidates` hook when the backend exposes one;
  otherwise it falls back to a generic `_execute` walk. No backend
  protocol methods were added — the surface stays the bounded set in
  the brief.
- **Freshness for SQLite chunks without `created_at`:** the brief said
  "freshness — newer chunks ranked higher." SQLite's `chunks` table
  has no `created_at` column (only Postgres documents do). The
  implementation uses `documents.modified_at` as the freshness proxy,
  which is present on both backends.
- **`selection_reason` skips neutral ranker_elevation:** when no
  `seed_query` AND no embeddings are available, ranker_elevation is
  the neutral 0.5 fallback. The reason picker excludes that case so
  users get an informative one-liner (confidence / metadata /
  freshness) instead of a generic placeholder. Documented inside the
  selector module.

## Archive — J1 (closed; preserved for reference)

J1 shipped in commit `986ac25`. All four J1-01 .. J1-04 rows reached
`status: done`; gates green at 93.30 % coverage. The historical
breakdown is preserved in git (`git show 77768fb:.planning/tdd/tasks.md`)
and elided from this file to keep J4 above the fold.
