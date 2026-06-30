# RFC: Code-intel 2/2 — code knowledge subgraphs (cross-repo)

status: accepted
**Owner**: nightly (open for any agent to claim)
**Priority**: P0 — operator-requested 2026-06-30
**Depends on**: 000-codeintel-1-incremental-merkle-sync (change-set for
incremental graph rebuild / `detect_changes`)

## Context

corpus-forge indexes code into flat, individually-embedded chunks. It
knows *what* a chunk is (function / class / method) but not how chunks
*relate* — who calls whom, what imports what, which class extends which.
A code corpus is really a graph; we store only the nodes.

The bottom half of a code-graph already exists:

- `corpus_forge/chunkers/code.py` — a tree-sitter AST-aware chunker
  that already descends the grammar and tags each chunk with `kind`
  (function / class / method) and `name`.
- `corpus_forge/enrichers/base.py::CodeChunkEnrichment` (lines 96–150)
  carries `symbols: list[str]` (line 123) — "referenced symbol names …
  this chunk depends on" — with a literal comment (lines 108–111) that
  it is kept flat **"so P2 graph storage can be added without a schema
  change."** This RFC *is* that P2.
- The backend is Postgres; recursive CTEs traverse a graph natively —
  no second datastore needed.

The reference design is GitNexus (`github.com/abhigyanpatwari/GitNexus`):
a deterministic, no-LLM-at-index-time pipeline of
Structure → Parse → **Resolve** (import / call / type-aware receiver /
heritage) → Cluster (Leiden communities) → Process-trace → hybrid
search, exposed over MCP (`impact`, `context`, `detect_changes`,
`cypher`). corpus-forge has Structure + Parse; the **Resolve** layer,
persistent edges, and graph-aware query tools are what's missing.

The corpus-forge-native differentiator GitNexus structurally can't
match: GitNexus indexes one repo into a per-repo `.gitnexus/` store.
corpus-forge keys everything in **one** Postgres corpus, so an edge
whose target `qualified_name` resolves to a symbol defined in a
*different dataset/repo* yields a **cross-repo call/contract graph** —
service-to-service edges across the whole fleet's corpus. That is the
"string together analyzed repos" the operator asked for.

Scope guardrail (memory `project_corpus_forge_scope_no_training`): the
graph is built by **deterministic structural analysis — no LLM at index
time, no training/distillation.** It is a preprocessing + retrieval-
grounding artifact, embedded on the normal lane, nothing more.

## Goals

- Persist a code graph: **symbol nodes** + **resolved edges**
  (`CALLS`, `IMPORTS`, `EXTENDS`, `IMPLEMENTS`, `MEMBER_OF`), each edge
  carrying a **confidence score** (0–1) reflecting resolution
  certainty, layered on existing `chunks` with no chunk-schema change.
- **Cross-repo resolution**: an edge whose target resolves to a symbol
  in another dataset is recorded as such, enabling cross-repo blast-
  radius and contract queries.
- **GraphRAG retrieval expansion**: `search` can optionally expand a
  hit to its N-hop graph neighbors (callers / callees / definitions)
  as additional grounded context — strictly additive to today's
  hybrid retrieval.
- New MCP tools mirroring GitNexus ergonomics: `code_context` (360°
  in/out edges for a symbol), `code_impact` (blast radius),
  `code_neighbors`.
- **Incremental**: rebuild only the subgraph touched by a scan's
  change-set (from `codeintel-1`), not the whole graph.
- Confidence scores feed the **existing curation selector** — low-
  confidence resolutions surface as curation targets.

## Non-goals

- **No new graph database.** GitNexus uses KuzuDB/LadybugDB; we use
  Postgres recursive CTEs. Adding a second backend would fracture the
  fleet's single-backend story for no gain at our scale. Explicitly
  rejected.
- **No LLM at index time.** Resolution is purely structural/AST. (LLM
  use stays where it already is — curation, enrichment summaries — and
  is never on the graph-build path.)
- **No training, sampling, or distillation** from the graph
  (scope memory). Retrieval-grounding + preprocessing only.
- **No raw-Cypher tool** in v1. GitNexus exposes `cypher`; we expose
  curated traversal tools first and defer an arbitrary-query surface
  (injection surface, supportability) to a later RFC if asked.
- **No process-trace / control-flow-graph** in v1 (GitNexus's
  Process nodes + `--pdg`). Nodes + resolved edges + communities
  first; traces are a follow-up.

## Approach

### Graph tables (next alembic revision; after `codeintel-1`'s)

```
corpus.code_symbols
  symbol_id       bigserial primary key
  dataset_id      bigint not null references corpus.datasets(id) on delete cascade
  document_id     bigint not null references corpus.documents(id) on delete cascade
  chunk_id        bigint references corpus.chunks(id) on delete set null
  kind            text not null              -- function|class|method|interface|...
  name            text not null
  qualified_name  text not null              -- module.Class.method — resolution key
  language        text not null
  signature       text
  span            int4range                  -- byte/line span in the document
  index (dataset_id, qualified_name)         -- cross-repo resolution probe
  index (chunk_id)

corpus.code_edges
  edge_id           bigserial primary key
  src_symbol_id     bigint not null references corpus.code_symbols(symbol_id) on delete cascade
  dst_symbol_id     bigint references corpus.code_symbols(symbol_id) on delete cascade
  dst_qualified     text                      -- unresolved target name (dst_symbol_id null until/if resolved)
  edge_type         text not null             -- CALLS|IMPORTS|EXTENDS|IMPLEMENTS|MEMBER_OF
  confidence        real not null             -- 0..1
  resolution_method text not null             -- exact|import-alias|heritage|receiver-infer|heuristic
  cross_repo        boolean not null default false
  index (src_symbol_id), index (dst_symbol_id), index (edge_type)
```

A `dst_qualified` with null `dst_symbol_id` is a dangling reference
(target not yet indexed, or external) — kept so a later scan that adds
the target can resolve it, and so cross-repo edges form as repos land.

### Resolution pass (deterministic)

Extend the code chunker's AST walk to emit **references** alongside
chunks: call sites, imports, base-class/interface clauses. Then resolve
GitNexus-style, in order, each with a `resolution_method` + confidence:

1. **Import resolution** — ES6 / CommonJS / Python / language module
   systems, incl. `import { X as Y }` aliasing and re-export chains.
2. **Call resolution** — match call sites against in-file defs, then
   imported symbols; overloads / dynamic dispatch lower the confidence.
3. **Type-aware receiver inference** — infer `self`/`this` type from
   method-def class, constructor calls, and inheritance chain to
   resolve `recv.method()`.
4. **Heritage** — `EXTENDS` / `IMPLEMENTS` from class/interface clauses.

Resolution keys on `qualified_name`. When the lookup matches a symbol
in a **different `dataset_id`**, set `cross_repo = true`. Unresolved →
keep `dst_qualified`, low confidence.

### Communities (Leiden)

Run Leiden community detection over the `CALLS`/`IMPORTS` subgraph to
group symbols into functional clusters; store membership as
`MEMBER_OF` edges to a synthetic community node (or a
`corpus.code_communities` side table). Feed cluster labels to the
existing `cluster_topics` and `next_curation_batch` grouping. (Pure
Python lib, e.g. `networkx`/`igraph`/`graphology`-equivalent — no new
service.)

### Retrieval + MCP

- **GraphRAG expansion**: `search` gains an opt-in
  `expand_graph=<hops>` that, after the hybrid hit set, pulls N-hop
  neighbors over `code_edges` (recursive CTE) and returns them as
  related context. Off by default — zero change to existing callers.
- **New MCP tools** (alongside `chunk_neighbors` / `get_document` in
  `corpus_forge/mcp/server.py`):
  - `code_context(symbol)` — the symbol's incoming + outgoing edges
    (callers, callees, base/derived, imports) with confidences.
  - `code_impact(symbol, depth)` — transitive blast radius, depth-
    grouped, **cross-repo-aware** (flags edges that cross datasets).
  - `code_neighbors(symbol, edge_type?)` — one-hop typed neighbors.
- **`detect_changes`-style hook**: given `codeintel-1`'s change-set
  for a scan, recompute only the subgraph for touched symbols
  (delete + re-resolve their edges), so the graph stays current
  incrementally.

### Incremental rebuild

On a scan, the changed documents (from the Merkle diff) determine which
`code_symbols` rows to drop + re-extract; edges sourced from those
symbols are re-resolved; dangling `dst_qualified` references elsewhere
get a cheap re-resolution probe in case the change added a definition.
Whole-graph rebuild is the cold-cache / `--rebuild` path only.

**Coverage note:** ≥ 90 % line coverage on all new code is part of
"done" (`make test-unit` gate). Each resolver lands with a language
fixture matrix (Python + TypeScript at minimum; others as the
language-pack supports them, matching the chunker's existing support).

## Tasks

- [ ] Alembic revision: `corpus.code_symbols` + `corpus.code_edges`
      (+ optional `code_communities`); indexes above; Postgres +
      SQLite parity; idempotent re-run test.
- [ ] Reference extraction in the tree-sitter walk: emit call sites,
      imports, heritage clauses with spans; Python + TS fixtures.
- [ ] Symbol persistence: populate `code_symbols` (incl.
      `qualified_name`) from chunks; backfill from existing
      `CodeChunkEnrichment.symbols` where present.
- [ ] Import resolver (alias + re-export chains) → `IMPORTS` edges +
      confidence/method.
- [ ] Call resolver (in-file → imported; overload/dynamic ambiguity
      lowers confidence) → `CALLS` edges.
- [ ] Type-aware receiver inference for `self`/`this` method calls.
- [ ] Heritage resolver → `EXTENDS` / `IMPLEMENTS` edges.
- [ ] Cross-repo resolution: `qualified_name` match across `dataset_id`
      sets `cross_repo=true`; dangling-reference reprobe on new defs.
- [ ] Leiden communities + `MEMBER_OF`; wire labels into
      `cluster_topics` / `next_curation_batch`.
- [ ] MCP tools `code_context`, `code_impact` (cross-repo-aware),
      `code_neighbors`; recursive-CTE traversal; tests.
- [ ] `search` opt-in `expand_graph=<hops>` GraphRAG expansion
      (default off; existing callers unaffected).
- [ ] Incremental subgraph rebuild driven by `codeintel-1`
      change-set; `--rebuild` full path.
- [ ] Confidence → curation: low-confidence edges surface via
      `next_curation_target`.

## Verification

- **Resolution accuracy**: per-language golden fixtures with known
  call/import/heritage graphs; assert edges + confidences match;
  overloaded/dynamic cases assert *lowered* confidence, not silent
  high-confidence wrong edges.
- **Cross-repo**: two fixture datasets where repo B calls a symbol
  defined in repo A; assert the `CALLS` edge is `cross_repo=true` and
  `code_impact` from A's symbol reaches B's caller.
- **Incremental == full**: mutate one function, incrementally rebuild,
  assert the graph equals a from-scratch `--rebuild` of the same tree.
- **GraphRAG**: a query whose answer needs a callee's body returns it
  via `expand_graph` when it was outside the raw hybrid hit set.
- **No-LLM invariant**: graph-build path makes zero model calls
  (asserted by a no-network/​no-embedder test harness).
- `make test-unit` ≥ 90 % coverage on extraction + resolvers + tools.

## References

- `github.com/abhigyanpatwari/GitNexus` — reference pipeline
  (Structure→Parse→Resolve→Cluster→Process), graph schema, confidence
  scoring, MCP `impact`/`context`/`detect_changes`. We adopt the
  resolution + edge model and the query ergonomics; we reject its
  embedded graph DB in favor of Postgres recursive CTEs (Non-goals).
- `corpus_forge/enrichers/base.py:96` — `CodeChunkEnrichment`;
  `symbols` (line 123) + the "P2 graph storage … no schema change"
  comment (lines 108–111) this RFC fulfills.
- `corpus_forge/chunkers/code.py` — tree-sitter AST walk to extend
  with reference extraction.
- `corpus_forge/mcp/server.py` — `chunk_neighbors` / `get_document`
  precedent for the new graph tools.
- `.planning/rfcs/000-codeintel-1-incremental-merkle-sync.md` — the
  change-set this RFC's incremental rebuild consumes.
- Memory `project_corpus_forge_scope_no_training` — deterministic,
  no-training scope guardrail.
