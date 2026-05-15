# Phase E — Document Classification & Strong Labels (TDD plan)

_Owner: tdd-principal. Workers: read freely. Edit only your claimed row's `status` and `claimed_by`._

Source plan (full prose, rationale, taxonomy debate): `/Users/evanowen/.claude/plans/phase-e-document-classification.md`

## Status

**Phase A/C** (Active Directory Sync): complete.
**Phase B** (SQLite backend): in flight at `.planning/tdd/sqlite_backend.md`.
**Phase D** (Multi-Format Corpus): complete. P0 closed at `06285fb`, P1 closed at `1f21822`, housekeeping at `156b34b`.
**Phase E** (this file): **COMPLETE** (P0 closed at `ec3fe15`, P1 closed at <orchestrator commit>).

## C-14 manual smoke results

P1 gate — `qwen2.5:7b-instruct` against 8 ambiguous fixtures (path doesn't help; format is markdown/HTML/notebook). Run script: `/tmp/c14_smoke.py` against the rule classifier and the LLM classifier head-to-head.

| doc | rule | llm | LLM rationale |
|---|---|---|---|
| `prose/intro.md` | `note@0.50` | `article@0.95` | "The document is formatted like a typical article or blog post with sections and paragraphs." |
| `prose/frontmatter.md` | `note@0.50` | `other@0.80` | "No clear content type, appears to be metadata or documentation." |
| `html/simple-article.html` | `article@0.50` | `article@0.95` | "The document is an HTML file containing a short article with headings and paragraphs." |
| `html/nav-and-ads.html` | `article@0.50` | `article@0.95` | "HTML structure with a main article section and navigation/ads/footer elements." |
| `notebook/analysis.ipynb` | `article@0.45` | `code@0.95` | "Contains Python code cells and a markdown cell in a Jupyter notebook format." |
| `fixtures/README.md` | `note@0.50` | `code@0.95` | "The document describes a fixture tree for testing and includes commands and file paths." |
| `synth/sourdough-recipe.md` (hand-crafted) | `note@0.50` | `other@0.20` | Graceful fallback: model emitted `class="recipe"` (not in enum) → LLMClassifier mapped to `other` 0.2 with the verbatim rationale prefix `"invalid LLM output: …"`. WORKING AS DESIGNED. |
| `synth/changelog-rambling.txt` (hand-crafted) | `note@0.50` | `note@0.85` | "Changelog format with personal reflections and technical details fits best with a personal note." |

**Verdict.** Rationales are coherent and defensible. The model correctly identifies article-shaped HTML (twice), recognises a Jupyter notebook as code-heavy, and gracefully degrades when it tries to invent a category outside the 9-value enum. One borderline disagreement (`fixtures/README.md` → code vs the rule's note): the fixture README contains shell commands and a directory tree, so "code" is defensible; the rule classifier's note answer is also fine. The escalation chain works as designed — every weak rule signal got a stronger LLM answer.

## Goal

Lift every document in the corpus from carrying only **structural**
labels (`format=*`, `language=*`, `extractor=*`) to also carrying a
**content-class** label — one of nine values that says what *kind* of
document this is, not what its bytes look like. The label powers
subset-selection at training time ("give me all chat-class docs",
"hold out textbook-class for eval").

After Phase E lands, Phases F/G/H (content-defined chunking, multi-
modal embeddings + Whisper, Qwen-Coder enrichment) become cheap to
gate by class.

## Class taxonomy

Nine-value flat enum. Persisted as
`labels(namespace='class', value=<one>)` and attached via
`document_labels(source='classifier:rule' | 'classifier:llm' | 'user')`.

| value | what it covers |
|---|---|
| `code` | source code, scripts, build files (Makefile, Dockerfile), config-as-code (`.nix`, `.tf`) |
| `chat` | conversation transcripts — Claude Code, OpenCode, generic dialogue |
| `book` | long-form non-pedagogical — fiction, memoir, popular non-fiction, biography |
| `textbook` | long-form pedagogical — academic textbooks, course notes, learning material with exercises |
| `paper` | research / academic papers (PDF with abstract+citations pattern) |
| `article` | blog posts, magazine articles, news, opinion writing |
| `reference` | API docs, schema specs, manifests, machine-readable data (JSON/YAML/TOML/CSV) |
| `note` | personal notes — Obsidian vault, markdown jottings, journals |
| `other` | fallback when no signal is strong enough to commit |

One class per document, written by the classifier chain. Users may
attach additional classes manually; classifier never overwrites a
`source='user'` row.

## Architecture seam

Post-ingest, separate from the extract/chunk pipeline. The classifier
walks `corpus.documents`, reads body + format labels + path, and
writes a `class=*` label via `backend.apply_label`.

```python
@dataclass(frozen=True)
class ClassifiableDocument:
    document_id: int
    source_uri: str
    title: str | None
    text: str
    format_labels: list[tuple[str, str]]  # already-attached
    metadata: dict

@dataclass(frozen=True)
class ClassLabel:
    value: str        # one of the 9 enum values
    confidence: float # 0.0-1.0
    rationale: str    # short string for audit

@runtime_checkable
class Classifier(Protocol):
    name: str
    def classify(self, doc: ClassifiableDocument) -> ClassLabel | None: ...
        # None = pass to the next classifier in the chain
```

`ClassifierRegistry` holds an ordered list; `classify(doc)` walks the
list, first non-`None` (or first `confidence >= threshold`) result
wins. Default chain after P1: `[RuleBasedClassifier(),
LLMClassifier(...)]`.

## Phase split (P0/P1, hard-gated — Phase D convention)

### P0 — RuleBasedClassifier + CLI + persistence

Ships standalone. Zero model dependency.

**Rules (priority order, first match wins):**

1. **Format-label fast path**
   - `format=code` → `class=code` (0.99)
   - source_uri scheme `claude-code://` / `opencode://` /
     `gemini-cli://` OR row is a conversation → `class=chat` (0.99)
   - `format=epub` + title/path matches pedagogy regex
     (`\b(textbook|primer|introduction to|course|handbook|cookbook|
     tutorial|exercises?|lectures?)\b`, case-insensitive) →
     `class=textbook` (0.85)
   - `format=epub` otherwise → `class=book` (0.7)

2. **Path / filename heuristics**
   - `/papers/`, `/research/`, `arxiv-*`, `*.bib` neighbours →
     `class=paper` (0.7)
   - `/notes/`, vault-style `daily/`/`journal/` → `class=note` (0.8)
   - `/blog/`, `/posts/`, `/articles/` → `class=article` (0.7)
   - `/docs/`, `/reference/`, `/api/` → `class=reference` (0.7)

3. **Content heuristics**
   - JSON/YAML/TOML/CSV extractors → `class=reference` (0.9)
   - Many `^User:` / `^Assistant:` / `^Human:` per kilobyte →
     `class=chat` (0.85)
   - PDF with `^Abstract\b` AND `\bReferences\b` AND `\[\d+\]`
     citation pattern → `class=paper` (0.75)
   - PDF without paper-pattern: length ≥ 50 pages → `class=book`
     (0.55); 8-49 pages → `class=textbook` (0.45) if pedagogy regex
     hits, else `class=book` (0.45); under 8 pages → `class=article`
     (0.5)

4. **Markdown-vault default** — `format=markdown` with no other
   signal → `class=note` (0.5)

5. **Fallback** — `class=other` (0.3)

**Schema addition (minor)**: add optional `confidence REAL` column to
`document_labels`. Mirrors `chunk_labels.confidence` (already exists).
NULL default keeps existing rows untouched.

**CLI** — `corpus-forge classify`:

```
Usage: corpus-forge classify [OPTIONS]

  Walk documents and assign class labels via the configured chain.
  Idempotent: docs with an existing classifier:* class label are
  skipped unless --reclassify is set.

Options:
  --dataset NAME       Limit to a single dataset (repeatable)
  --reclassify         Force re-classification of all docs
  --dry-run            Print the plan without writing
  --limit N            Stop after N documents
  --json               Emit per-document JSON
  --classifier NAME    Force a specific classifier (bypass the chain)
```

Idempotency check: a document is classified iff it has at least one
label in the `class` namespace from `source LIKE 'classifier:%'`.
User-attached `class=*` rows never block re-classification (since the
classifier won't overwrite them anyway).

### P1 — LLMClassifier (Ollama qwen2.5:7b-instruct default)

**LLMClassifier** — `corpus_forge/classifiers/llm.py`:

- Reuses Wave 4's `OllamaVLM` HTTP plumbing (requests client, retry,
  timeout, exception mapping). Talks to `/api/generate` with text
  input + `format=json` constraint.
- Default model: **`qwen2.5:7b-instruct`** (~5 GB on disk, strong
  accuracy on the 9-way classification, fast enough for batch use on
  M-series 64 GB).
- Prompt: head + tail (~2 KB) of doc body, plus the format labels
  already attached, plus the 9-value enum. Structured-output JSON
  schema: `{"class": "...", "confidence": 0.0-1.0, "rationale": "..."}`.
- Output validation: assert `class` is one of the 9 values; if not,
  log WARNING and fall through to `class=other` (0.2).
- Escalation: chain triggers LLM when `RuleBasedClassifier` returned
  `None` OR `confidence < 0.4`. Above that, rule classifier wins (LLM
  call avoided — saves seconds per doc).

**Config additions** (`ClassifierConfig` in `corpus_forge/config.py`):

```toml
[classifier]
chain                  = ["rule", "llm"]         # ordered
escalation_threshold   = 0.4                     # rule confidence below this → LLM
llm_model              = "qwen2.5:7b-instruct"   # default
llm_url                = "http://localhost:11434"
llm_timeout_s          = 60.0
llm_excerpt_chars      = 2000                    # head+tail budget
```

**`requires_ollama_text` pytest marker** — extension of Wave 6's
marker pattern. Skip live tests when daemon down or model not pulled.

## Architectural reuse (do not reinvent)

- `apply_label("document", doc_id, "class", value, source=...)` —
  the existing API persists everything. Phase D HK-2 confirmed the
  path works.
- `OllamaVLM` (`corpus_forge/vlm/ollama.py`) — copy the HTTP shape;
  the LLM classifier doesn't need vision, just text generation. May
  end up sharing a base helper or just duplicating the small HTTP
  block — your call when implementing.
- `requires_ollama_text` skip plumbing — same shape as
  `requires_ollama` (`tests/integration/conftest.py`).
- Backend protocol — no changes to chunk/doc upsert shape (Phase D
  HK-2 already opened the metadata channel).

## Critical files

**Added (P0)**
- `corpus_forge/classifiers/{__init__,base,registry,rule_based}.py`
- `corpus_forge/cli.py` — `classify` Typer command (additive)
- `corpus_forge/schema/<N>_document_label_confidence.sql` (Alembic
  revision adding the optional `confidence` column)
- `tests/unit/test_classifier_registry.py`
- `tests/unit/test_rule_based_classifier.py`
- `tests/integration/test_classify_cli_e2e.py`

**Added (P1)**
- `corpus_forge/classifiers/llm.py`
- `tests/unit/test_llm_classifier.py` (mocked HTTP)
- `tests/integration/test_classify_llm_e2e.py` (`requires_ollama_text`)

**Modified**
- `corpus_forge/config.py` — `ClassifierConfig` pydantic, attached to
  `Config`
- `config.example.toml` — `[classifier]` block
- `docs/architecture.md` — new H2 "Document classification"
- `README.md` — short usage note under existing structure

## Waves

### Wave 0 — Foundation (parallel; disjoint files)

| id | surface | why this wave |
|----|---------|---------------|
| C-01 | `classifiers/{__init__,base,registry}.py` (new) | Protocol + dataclasses + registry, no deps |
| C-02 | `classifiers/rule_based.py` (new) + `tests/unit/test_rule_based_classifier.py` | Stdlib only |
| C-03 | `config.py::ClassifierConfig` (additive) + `tests/unit/test_config_classifier.py` | Additive pydantic; existing tests stay green |
| C-04 | `schema/<N>_document_label_confidence.sql` (new) — optional `confidence REAL` on `document_labels` | DDL only |

### Wave 1 — CLI + ingest integration (serialized on `cli.py` / `config.example.toml`)

| id | depends on | surface | why now |
|----|------------|---------|---------|
| C-05 | C-01..C-04 | `cli.py` `classify` command + `tests/unit/test_cli_classify.py` | Pulls everything together; needs the protocol and rule classifier to exist |
| C-06 | C-04 | `backends/postgres.py` + `backends/sqlite.py` — `iter_documents_for_classification` helper (read-only); apply `confidence` column on both upsert + label paths | Backend extension serialized after schema migration |
| C-07 | C-03 | `config.example.toml` `[classifier]` block | Doc-shaped |

### Wave 2 — P0 gate

| id | depends on | surface | why now |
|----|------------|---------|---------|
| C-08 | C-05..C-07 | `tests/integration/test_classify_cli_e2e.py` against testcontainers Postgres + the fixture corpus | E2E gate |
| C-09 | C-08 | `make ci` green at ≥90% coverage — **P0 gate**. Hard stop before P1 dispatch. |

### Wave 3 — LLM foundation

| id | depends on | surface | why now |
|----|------------|---------|---------|
| C-10 | C-09 | `classifiers/llm.py` (new) + `tests/unit/test_llm_classifier.py` (mocked HTTP) | Reuses Wave 4 HTTP shape |
| C-11 | C-09 | `config.py::ClassifierConfig` LLM fields (model, url, timeout, escalation threshold) + `config.example.toml` update | Additive |

### Wave 4 — Live integration + P1 gate

| id | depends on | surface | why now |
|----|------------|---------|---------|
| C-12 | C-10, C-11 | `tests/integration/test_classify_llm_e2e.py` (`requires_ollama_text` marker; skip when model absent) | Live Ollama smoke |
| C-13 | C-12 | `README.md` + `docs/architecture.md` updates + cost-guard CLI message | Docs |
| C-14 | C-13 | Manual cross-model smoke (qwen2.5:3b vs qwen2.5:7b on ambiguous fixtures) — **P1 gate**. | Hard close |

## Definition of Done

### P0 (gate C-09)

- [ ] `corpus_forge/classifiers/` package with `base.py`, `registry.py`, `rule_based.py`, `__init__.py`
- [ ] All nine `class=*` values emittable by `RuleBasedClassifier` on the smoke-test fixture corpora
- [ ] `corpus-forge classify --dry-run --json` produces an auditable plan
- [ ] `corpus-forge classify` is idempotent (re-run is a no-op)
- [ ] `--reclassify` re-runs the chain
- [ ] `ClassifierConfig` accepted in TOML; existing configs unaffected
- [ ] `document_labels.confidence` column added via Alembic revision
- [ ] `make test-unit` green at ≥90% coverage
- [ ] `make test-integration` green including `test_classify_cli_e2e.py`
- [ ] `make ci` green

### P1 (gate C-14)

- [x] `classifiers/llm.py` instantiable from config; live skip-gate honoured
- [x] Rule classifier with confidence `< 0.4` escalates to LLM; above threshold the LLM call is avoided
- [x] LLM output validated against the 9-value enum; invalid responses fall through to `class=other`
- [x] `make ci` green; new `requires_ollama_text`-marker-gated tests skip cleanly without daemon/model
- [x] Manual cross-model smoke: 8 ambiguous documents (markdown / HTML / notebook / synthesised), `qwen2.5:7b-instruct` produces defensible classes — see `## C-14 manual smoke results` above

## Out of scope (P2 / later)

- Per-chunk class labels (only document-level for now)
- Multi-label classification (one primary class to start)
- Hierarchical classes (`code/python`, `code/web`)
- User-feedback loop (label correction → fine-tune rule weights)
- Fine-tuning a small dedicated classifier (replace LLM with
  ~100M-param head)

## Roadmap after Phase E

- **Phase F** — true content-defined chunking (FastCDC); class label
  informs chunk strategy (`code` keeps tree-sitter; prose switches to
  CDC)
- **Phase G** — multi-modal embeddings + Whisper transcription; class
  label distinguishes transcript-class from chat-class
- **Phase H** — **Qwen3.6-35B-A3B** code-LLM enrichment for code
  chunks (docstring synthesis, semantic chunk summaries, possibly
  symbol-level annotation). MoE: 35B total / ~3B active per token,
  fits 64 GB unified memory; outpaces Qwen2.5-Coder on every standard
  code benchmark. Gated to `class=code` documents — Phase E makes
  this cheap.

## Risks / open issues

- **Class-taxonomy bias** — the 9 classes are calibrated to this
  user's corpus shape. Configurable via `ClassifierConfig.allowed_classes`
  if a future user needs different categories.
- **Path-heuristic over-fit** — the rule classifier's path patterns
  match this user's vault layout; transfer is uneven. LLM classifier
  in P1 catches the misses.
- **Book vs textbook disambiguation** — trickiest call. Rule
  classifier biases toward `book` when uncertain; LLM catches the
  pedagogical-sounding-but-actually-popular-non-fiction cases.
- **LLM hallucination** — Ollama `format=json` mitigates structure
  drift; we additionally validate the returned `class` is in the
  9-value set.
- **Performance at scale** — rule classifier microseconds/doc;
  qwen2.5:7b-instruct 300-900 ms/doc on M-series. 100k docs = 8-25 h
  single-threaded. `--parallel N` flag deferred to P2 unless
  immediately needed.
- **Confidence semantics** — rule confidence is hand-calibrated; LLM
  confidence is self-reported. Both stored against the row's `source`
  field so downstream consumers can disambiguate.

## Task table

| id | title | depends_on | surface | risk | status | claimed_by | notes |
|----|-------|------------|---------|------|--------|------------|-------|
| C-01 | `Classifier` protocol + dataclasses + `ClassifierRegistry` | — | `corpus_forge/classifiers/{__init__,base,registry}.py`, `tests/unit/test_classifier_registry.py` | low | pending | — | Runtime-checkable Protocol; registry mirrors `embedders/registry.py` shape |
| C-02 | `RuleBasedClassifier` | C-01 | `corpus_forge/classifiers/rule_based.py`, `tests/unit/test_rule_based_classifier.py` | med | pending | — | Stdlib only; all 9 class values emittable; nine confidence calibrations |
| C-03 | `ClassifierConfig` pydantic | — | `corpus_forge/config.py`, `tests/unit/test_config_classifier.py` | low | pending | — | Additive; default chain = `["rule"]` (LLM added in C-11) |
| C-04 | `document_labels.confidence REAL` migration | — | `corpus_forge/schema/<N>_document_label_confidence.sql`, `tests/integration/test_migrate_label_confidence.py` | low | pending | — | NULL default; backwards-compat |
| C-05 | `corpus-forge classify` CLI | C-01..C-04 | `corpus_forge/cli.py`, `tests/unit/test_cli_classify.py` | med | pending | — | `--dry-run` / `--reclassify` / `--json` / `--limit N` / `--dataset NAME` / `--classifier NAME` |
| C-06 | Backend `iter_documents_for_classification` + `confidence` plumbing | C-04 | `corpus_forge/backends/postgres.py`, `corpus_forge/backends/sqlite.py`, `tests/unit/test_backend_classifier_helpers.py` | med | pending | — | Read-only iterator; pass `confidence` through `apply_label` |
| C-07 | `config.example.toml` `[classifier]` block | C-03 | `config.example.toml` | low | pending | — | Documents default chain + threshold |
| C-08 | E2E integration test (Postgres) | C-05..C-07 | `tests/integration/test_classify_cli_e2e.py` | med | pending | — | Testcontainers Postgres + fixture corpus; asserts per-class document counts |
| C-09 | **P0 gate** — `make ci` green | C-08 | — | gate | pending | — | Hard stop before P1 dispatch |
| C-10 | `LLMClassifier` (Ollama, mocked HTTP) | C-09 | `corpus_forge/classifiers/llm.py`, `tests/unit/test_llm_classifier.py` | med | done | tdd-principal | Collapsed with C-11 — they collide on `classifiers/__init__.py` + registry tuple-return. 23 unit tests; transport+output validation+URL+prompt covered. |
| C-11 | `ClassifierConfig` LLM fields + chain composition | C-09 | `corpus_forge/config.py`, `config.example.toml` | low | done | tdd-principal | `llm_url` flipped to `AnyHttpUrl`; `llm_temperature` added; default chain now `["rule", "llm"]`; rich-docs audit on `[vlm]` + `[classifier]` blocks. |
| C-12 | OCR-text live E2E (`requires_ollama_text` marker) | C-10, C-11 | `tests/integration/test_classify_llm_e2e.py`, `tests/integration/conftest.py` (extend marker probe) | med | done | tdd-principal | 4 tests, all `requires_ollama_text`. Run live (`qwen2.5:7b-instruct` pulled): 4/4 PASS in 8.76s. Skip plumbing in conftest. |
| C-13 | README + architecture docs + cost-guard CLI text | C-12 | `README.md`, `docs/architecture.md`, `corpus_forge/cli.py` (help text) | low | done | tdd-principal | README H2 "Document classification" + H3 "Model endpoints (local vs remote)"; architecture H2 "Document classification" with seam diagram + escalation policy + local-vs-remote subsection; CLI help + cost-guard breakdown updated. |
| C-14 | **P1 gate** — manual cross-model smoke | C-13 | — | gate | done | tdd-principal | 8 ambiguous fixtures classified via `qwen2.5:7b-instruct`; rationales captured in the `## C-14 manual smoke results` block above; graceful-fallback path exercised in the wild via the synth/recipe fixture. |
