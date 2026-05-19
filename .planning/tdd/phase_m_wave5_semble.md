# Phase M Wave 5 — semble investigation spike (decision doc)

> **Hard limits.** This spike does NOT wire `semble` into corpus-forge.
> No MCP tools were added. Nothing changed under `corpus_forge/`,
> `corpus_forge/embedders/`, or `corpus_forge/retrieval/`. No new
> top-level deps in `pyproject.toml` — `semble` is installed manually
> into the bench venv. All semble-touching code lives under
> top-level `experiments/`, which is excluded from the wheel + Docker
> via the build target's `packages` list and `.dockerignore`. The bench
> entry point (`tests/perf/test_semble_bench.py`) is gated by
> `CF_SEMBLE_BENCH=1` so CI never runs it. The only production-config
> change is the `experiments/`-exclusion line in `pyproject.toml` (and
> the `.dockerignore` rule).

---

## TL;DR

**Recommendation: extract techniques.**

`semble` (MinishLab, `a5a233428aa10b59dbdcdbb05d5cd3b748d585c7`, v0.1.9 on
PyPI) builds in **~1 second** and answers in **~1.4 ms p50** on this
repo's 1.5 MiB code+docs corpus. corpus-forge's `HybridRetriever` with
reranker enabled (the production default) answers in **~1.2 s p50** —
**~880× slower**. Quality is split: `HybridRetriever` wins overall MRR@10
(0.46 vs 0.37) and Recall@5 (0.64 vs 0.48), but `semble` **crushes** it
on identifier searches (MRR 0.85 vs 0.40 — the category corpus-forge
agents actually issue most often via Claude Code's grep replacement
path).

We do **not** swap retrieval engines. But three semble techniques —
adaptive lexical/semantic weighting, definition boosts, and the static
Model2Vec embedding — are worth porting into a future Phase R-N as a
**code-aware shortcut tier** that runs *in front of* the existing hybrid
retriever for identifier-shaped queries. The cross-encoder reranker
budget would not pay for itself on a query like `IgnoreStack.directory_pruned`
when a 1.4 ms BM25+static-embed pass already hits MRR=1.0.

---

## Methodology

### Bench corpus

- **Repository:** corpus-forge itself, pinned to commit
  `5a8a26c979c4c705c569cd6477ea052c5b9fdf06` (Phase M Wave 4 head).
- **Surface:** `corpus_forge/**` (excluding `schema/`), plus
  `config.example.toml` and `README.md`.
- **Size:** 180 files, ~1.54 MiB total, ~1353 tree-sitter chunks
  (semble's count).
- **Excluded:** `tests/fixtures/multi_format_corpus` (thousands of
  trivial throwaway code files in 19 languages that would inflate the
  index without bearing on our query set).

Files are staged into a tmpdir via `_stage_corpus()` so semble's gitignore-
honoring walker can't drift into the fixture tree even if a future
`.gitignore` revision lets it.

### Query set

`tests/perf/data/semble_queries.jsonl` — **25 hand-authored queries**,
**5 per category**:

| Category    | Examples                                                            |
|-------------|---------------------------------------------------------------------|
| identifier  | `HybridRetriever.search`, `IgnoreStack.directory_pruned`            |
| callsite    | "where do we call pymupdf4llm to_markdown", "zotero source plugin branch" |
| concept     | "how does the watch debounce work", "splice managed block"          |
| error       | "managed block sentinels not found exception", "k_values must be non-empty" |
| config      | "scan workers default", "whisper transcription backend config"      |

Ground truth is keyed by `(file, byte_start, byte_end)` and authored
by hand by navigating the repo at the pinned commit and recording the
canonical answer span. A hit is "relevant" iff its byte span overlaps
any ground-truth span by ≥ 32 bytes (see
`tests/perf/metrics.py:hit_matches_ground_truth`). The overlap primitive
is necessary because `semble` chunks by tree-sitter constructs (one
class, one function) while corpus-forge chunks by `MarkdownChunker`'s
paragraph-bounded slices with overlap — the two produce different
absolute spans even when "answering the same question".

The query authoring did NOT consult either retriever's output during
ground-truth labeling.

### Retriever configurations

| Retriever | Construction                                                              |
|-----------|---------------------------------------------------------------------------|
| `SembleRetriever` | `semble==0.1.9`, `SembleIndex.from_path(staging, include_text_files=True)`, defaults (HYBRID mode, code-aware reranker on, potion-code-16M Model2Vec). |
| `HybridRetriever` | `SQLiteBackend(":memory:")` + `SentenceTransformersEmbedder("all-MiniLM-L6-v2", 384d)` + `MarkdownChunker(max_chars=1500, overlap=200)` + `CrossEncoderReranker("BAAI/bge-reranker-v2-m3", device="cpu")`. `SearchOptions(k=10, fusion="rrf", rerank=True, rerank_top_n=50)`. |

The HybridRetriever config mirrors `RetrievalConfig`'s shipped defaults
with the production-recommended reranker enabled.

### Bench harness

`tests/perf/test_semble_bench.py` — gated by `CF_SEMBLE_BENCH=1`. Runs
every query against both retrievers, times each `search` call, joins
hits back to byte spans (semble's line-keyed → byte via per-file line-
start index; HybridRetriever's chunk-keyed → byte via a `bytes.find`
join captured at index-build time), then scores with
`tests/perf/metrics.py:compute_metrics`. Dumps the full per-query
breakdown to `tests/perf/out/semble_bench_<ISO>.json`.

Reproduction:

```bash
uv pip install semble                  # installs into the existing corpus-forge venv
CF_SEMBLE_BENCH=1 uv run python -m pytest tests/perf/test_semble_bench.py -v
```

### Raw bench output

Two JSON runs are preserved under `tests/perf/out/`:

- `semble_bench_20260519T150059Z.json` — HybridRetriever **without**
  rerank (initial sanity pass).
- `semble_bench_20260519T151703Z.json` — HybridRetriever **with**
  rerank=True (the production-equivalent comparison; all numbers below
  cite this run unless noted).

---

## Results

### Headline table

| Metric              | semble | HybridRetriever (rerank=on) | Ratio (hybrid / semble) |
|---------------------|--------|-----------------------------|--------------------------|
| MRR@10              | 0.374  | **0.460**                   | 1.23×                    |
| Recall@5            | 0.480  | **0.640**                   | 1.33×                    |
| p50 latency         | **1.37 ms** | 1210.41 ms             | 884×                     |
| p95 latency         | **9.16 ms** | 1269.84 ms             | 139×                     |
| Index build         | **0.98 s**¹ | 10.62 s (no rerank) / 500.42 s (with first-time BGE download) | — |
| Chunks indexed      | 1353   | ~1100²                      | —                        |

¹ Cold build, includes `potion-code-16M` model download cache miss on
the very first run (~50 ms additional).
² `HybridRetriever`'s chunk count is approximate — `MarkdownChunker`'s
output count varies by ±overlap; we observed ~1100 across runs.

### By query category

| Category    | semble MRR@10 | hybrid MRR@10 | semble R@5 | hybrid R@5 |
|-------------|--------------:|--------------:|-----------:|-----------:|
| identifier  | **0.850**     | 0.400         | **1.00**   | 0.60       |
| callsite    | 0.200         | **0.380**     | 0.20       | **0.60**   |
| concept     | 0.420         | **0.750**     | 0.60       | **1.00**   |
| error       | 0.300         | **0.550**     | 0.40       | **0.80**   |
| config      | 0.100         | **0.230**     | 0.20       | 0.20       |

p50 latency by category is uniformly ~1.0–6.7 ms (semble) vs.
~1130–1245 ms (hybrid) — the ratio is consistent across categories so
we omit the per-category latency table.

---

## Qualitative notes

**semble wins identifier searches.** This is the headline. `IgnoreStack.directory_pruned`,
`HybridRetriever.search`, `class ManagedBlockCorrupted`, `ScanConfig`
all came back at rank 1 from semble; HybridRetriever ranked them at
rank 3 / rank 9 / rank 1 / rank 3 respectively. semble's adaptive
lexical-weight bump on symbol-shaped queries does exactly what we'd
want a coding agent's first-pass tool to do.

**HybridRetriever wins concept and error searches.** Natural-language
queries like "how does the watch debounce work" and "managed block
sentinels not found exception" benefit from the cross-encoder
reranker's deep query↔passage scoring. semble's static Model2Vec
embeddings can't capture the same semantic relationships at the
sentence level.

**Both struggle on config-block searches.** MRR < 0.25 across the
board. The `config.example.toml` queries are about a *region of a TOML
document* — neither tree-sitter (which doesn't have a TOML grammar in
the default semble extension set; the file is treated as text) nor
`MarkdownChunker` (which doesn't know about TOML section semantics)
chunks it on the right boundaries. This is a chunking gap, not a
retriever gap; fixing it would require a TOML-aware chunker
upstream of either retriever.

**Latency disparity is real.** semble's p50 is 884× faster than the
production hybrid configuration. The cross-encoder reranker dominates
the hybrid latency — `search_dense` + `search_lexical` + RRF fuse
together cost ~5 ms (we measured this in the no-rerank pass);
`CrossEncoderReranker.rerank(..., top_n=10)` over 50 fused candidates
costs ~1200 ms on CPU on this M-series Mac. semble achieves equivalent-
or-better quality on identifier queries with no reranker at all.

**Apple Silicon install works.** `uv pip install semble` resolved
cleanly on `aarch64-darwin` with Python 3.13. The `tree-sitter-language-pack`
1.6.2 wheel for aarch64 exists. The only collateral was that
corpus-forge's pinned `tree-sitter-language-pack>=0.7,<1.7` (which we
expressed in the `[code]` extra) had to be downgraded from 1.8.0 to
1.6.2 to satisfy semble's `tree-sitter-language-pack>=1.0,<1.8.0,!=1.6.3`
constraint. **This is the one production-level coupling concern the
spike surfaced.**

---

## Recommendation: extract techniques

We do **not** wire semble in. Three specific reasons make a full swap
unattractive even given the latency win:

1. **Coverage gap.** semble doesn't know about chat-style sources
   (Claude Code conversations, Slack exports), PDFs after extraction,
   audio transcripts, image-VLM captions, or any of the non-code
   surfaces corpus-forge is positioned around. semble is a *code*
   retriever; corpus-forge is a *corpus* retriever. The marketing
   framings agree on this — semble's README leads with "code search"
   in its title.
2. **Dependency conflict.** Tree-sitter-language-pack pin collision
   (semble forbids 1.8.0, corpus-forge has been using 1.8.0 since
   Phase D Wave 1). Either project can move, but the coordination cost
   is real and ongoing.
3. **No async future.** semble's `SembleIndex` is purely in-memory and
   per-process; it has no notion of a multi-host backend. corpus-forge's
   Postgres+pgvector path is the entire point of the `[postgres]` extra.
   Wiring semble in would require either (a) accepting that it only
   works on the SQLite single-host path, or (b) re-implementing semble's
   pipeline against `StorageBackend` — at which point we'd just be
   rewriting semble.

Instead, the **techniques** to extract into a future Phase R-N effort:

- **Adaptive lexical weight on symbol-shaped queries.** semble detects
  `Foo::bar` / `_private` / `getUserById` query shapes and boosts BM25
  in the RRF fusion. Our RRF is currently scale-free + symmetric. A
  query-shape classifier inside `HybridRetriever` (probably ~30 LOC)
  that biases `rerank_top_n` candidate seeding toward lexical hits when
  the query looks like a Python qualified name would likely close the
  identifier-search gap without adding new dependencies. (Source:
  semble's `ranking/` directory.)
- **Definition boosts.** semble re-ranks chunks that *define* (vs.
  reference) the queried identifier above ones that merely cite it.
  This requires the chunker to carry "is this chunk a definition?"
  metadata — already true for tree-sitter chunks via the `Definition`
  query in `tree-sitter-python.scm`. Wave 5 of Phase D's
  `corpus_forge/chunkers/code.py` already extracts named-symbol nodes;
  surfacing `is_definition: bool` on chunk metadata would unlock this
  with no new deps. (Source: semble's
  `ranking/code_aware.py::definition_boost`.)
- **Static-embedding fast tier.** `potion-code-16M` is 16 MB and runs
  on CPU at sub-millisecond per query. Adding it as an *optional first
  pass* before the cross-encoder rerank — and short-circuiting the
  rerank entirely when the static-tier already returns a high-confidence
  identifier match — would shrink the production p50 latency on the
  common-case "find this symbol" queries from ~1.2 s to ~5 ms with no
  quality regression. (Source: semble's `index/dense.py` +
  `model2vec` integration.)

The first two are pure ranking-layer changes (no new deps). The third
introduces `model2vec` as an optional `[code]`-tier dep — a 16 MB MIT
library that already plays nicely on Apple Silicon (we just verified).

A Phase R-N task to land "adaptive weight + definition boost" first,
measure against this bench's gold set, and gate "static-tier" on
whether the first two close the gap, would be the right shape.

---

## Open follow-ups (not in scope for this spike)

- `tree-sitter-language-pack` pin negotiation: semble caps `<1.8.0`;
  corpus-forge currently pulls 1.8.0 via the `[code]` extra. Pinning
  corpus-forge to `<1.8.0` or coordinating with the semble authors on
  loosening their cap is a precondition for any future "ship semble
  alongside" path (which we are not recommending — but it's worth
  documenting for context).
- The config-block category was the weakest for *both* retrievers
  (MRR 0.10 / 0.23). A TOML-aware chunker would help both — orthogonal
  to the semble question.
- The cross-encoder reranker's p50 of ~1.2 s is the single biggest
  retrieval-side latency cost in production. A static-embedding shortcut
  tier (extracted from semble) is the cheapest path to bring it down
  for identifier-shaped queries. Independent of the semble decision,
  this is a real-money win worth its own follow-up plan.

---

## Files touched by this spike

| Path                                                  | Notes                                                          |
|-------------------------------------------------------|----------------------------------------------------------------|
| `experiments/semble_adapter.py`                       | Research `SembleRetriever` over `SembleIndex`.                 |
| `experiments/README.md`                               | "Not shipped" notice; spike venv recipe.                       |
| `tests/perf/metrics.py`                               | MRR@10 / Recall@5 / p50 / p95 helpers (ungated; reusable).     |
| `tests/perf/test_metrics.py`                          | 36 unit tests over the metric helpers (ungated, passing).      |
| `tests/perf/data/semble_queries.jsonl`                | 25 hand-authored queries with `(file, byte_start, byte_end)` ground truth. |
| `tests/perf/test_semble_bench.py`                     | Gated bench harness (`CF_SEMBLE_BENCH=1`).                     |
| `tests/perf/out/semble_bench_20260519T15{00,17}*.json`| Raw bench output (no rerank + with rerank).                    |
| `.planning/tdd/phase_m_wave5_semble.md`               | This decision doc.                                             |
| `pyproject.toml`                                      | Single allowed prod-config touch: explicit `experiments/` exclusion comment on wheel build target. |
| `.dockerignore`                                       | `experiments/` line.                                           |

**No changes under `corpus_forge/`.** That's the spike's hard scope
contract.
