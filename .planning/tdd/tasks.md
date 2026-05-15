# TDD Task Board — Phase E P1

_Owner: tdd-principal. Workers: read freely. Edit only your claimed row's `status` and `claimed_by`._

Source plan: `/Users/evanowen/Library/Mobile Documents/com~apple~CloudDocs/Workspace/playground/corpus-forge/.planning/tdd/phase_e_classification.md`
Dispatch input: orchestrator brief (Phase E P1, post-`ec3fe15`).

## Project gates
- lint: `make lint` (ruff)
- format: `make format-check`
- typecheck: `make typecheck` (pyrefly strict)
- test-unit: `make test-unit` (≥90% coverage post-P0 baseline 91.83%)
- test-integration: `make test-integration` (testcontainers Postgres + `requires_ollama_text` skip-gated)
- test-fuzz / test-smoke: `make test-fuzz` / `make test-smoke`
- ci: `make ci`

## Hard constraints (from dispatch)
1. **DO NOT COMMIT, DO NOT PUSH.** Stage only. Orchestrator commits.
2. Reuse `OllamaVLM` HTTP shape; do not reinvent.
3. `make ci` must remain green; coverage ≥90%.
4. P0 surfaces unchanged in behaviour (rule classifier output identical).
5. Cross-cutting: every model client must support local-or-remote URL via config.

## Tasks

| id | title | depends_on | surface | risk | status | claimed_by | notes |
|----|-------|------------|---------|------|--------|------------|-------|
| C-10/11 | `LLMClassifier` + classifier exception hierarchy + `ClassifierConfig` LLM wiring + registry tuple-return + CLI source attribution + `config.example.toml` rich-doc audit | — | `corpus_forge/classifiers/{base,registry,llm,__init__}.py`, `corpus_forge/config.py`, `corpus_forge/cli.py`, `config.example.toml`, `tests/unit/test_llm_classifier.py` (new), `tests/unit/test_classifier_registry.py` (update), `tests/unit/test_cli_classify.py` (update), `tests/unit/test_config_classifier.py` (update) | high | done | tdd-principal | 23 new unit tests + updated registry/config/cli tests; lint+pyrefly+coverage gate passes; `make test-unit` 2831 passed @ 91.78%. |
| C-12 | Live OCR-text E2E + `requires_ollama_text` marker + conftest probe | C-10/11 | `tests/integration/conftest.py`, `tests/integration/test_classify_llm_e2e.py` (new), `pyproject.toml` (marker registration) | med | done | tdd-principal | 4/4 PASS in 8.76s on qwen2.5:7b-instruct. Skip plumbing landed. |
| C-13 | README + architecture docs + CLI help expansion + cost-guard breakdown | C-10/11 | `README.md`, `docs/architecture.md`, `corpus_forge/cli.py` (help string + cost-guard text only) | low | done | tdd-principal | README: H2 "Document classification" + H3 "Model endpoints (local vs remote)". docs/architecture: H2 with seam diagram + escalation policy + local-vs-remote subsection. CLI `classify --help` + cost-guard rewritten. |
| C-14 | **P1 gate** — manual cross-model smoke + plan + active_tasks bookkeeping + `make ci` re-run | C-12, C-13 | `.planning/tdd/phase_e_classification.md`, `.planning/active_tasks.md` | gate | done | tdd-principal | 8 ambiguous fixtures classified via `qwen2.5:7b-instruct`; verdict recorded; plan flipped to COMPLETE; `make ci` re-run. |

## Acceptance details

### C-10/11 — LLMClassifier + exception hierarchy + config wiring + source-attribution fix

**LLMClassifier** (`corpus_forge/classifiers/llm.py`, new):
- `name = "llm"`.
- Constructor: `__init__(self, *, model="qwen2.5:7b-instruct", llm_url="http://localhost:11434", timeout_s=60.0, temperature=0.0, excerpt_chars=2000)`.
- Endpoint: `POST {llm_url.rstrip('/')}/api/generate` with payload `{"model": <model>, "prompt": <prompt>, "stream": false, "format": "json", "options": {"temperature": <temperature>, "num_ctx": 8192}}`.
- Prompt template: head (`excerpt_chars // 2`) + `\n…\n` separator + tail (`excerpt_chars // 2`) of `doc.text`, plus the format-labels list and the 9-value enum. When `len(doc.text) <= excerpt_chars`, pass `doc.text` whole.
- Response parsing: load Ollama's `{"response": "<json-string>"}`, then `json.loads` the inner string to extract `{"class": ..., "confidence": ..., "rationale": ...}`.
- Output validation: assert `class` is in `ALLOWED_CLASS_VALUES`; clamp `confidence` into `[0.0, 1.0]`. If `class` is invalid OR inner JSON is malformed, log a WARNING and return `ClassLabel(value="other", confidence=0.2, rationale="invalid LLM output: <raw-snippet>")`. Never raise from invalid model output (graceful fallback to "other").
- Lazy `import requests` inside `classify`. Exception mapping (mirrors `OllamaVLM` but at classifier-specific names from `base.py`):
  - `requests.Timeout` → `ClassifierTimeoutError`.
  - `requests.ConnectionError` → `ClassifierUnavailableError`.
  - 4xx/5xx → `ClassifierResponseError(f"HTTP {status}: {body[:200]}")`.
  - `requests.RequestException` (other) → `ClassifierUnavailableError`.
  - Outer JSON malformed (resp.json() fails) → `ClassifierResponseError`. Inner JSON malformed (the model's own output) → graceful fallback to `class=other` (per validation policy above).

**Exception hierarchy** (extend `corpus_forge/classifiers/base.py`):
- `ClassifierError(Exception)` base.
- `ClassifierUnavailableError(ClassifierError)`.
- `ClassifierTimeoutError(ClassifierError)`.
- `ClassifierResponseError(ClassifierError)`.

**Source-attribution fix** (`corpus_forge/classifiers/registry.py` + `corpus_forge/cli.py`):
- `ClassifierRegistry.classify` returns `tuple[str, ClassLabel] | None` — `(winner_name, label)`.
- `winner_name` is the classifier whose label was returned (the first to clear threshold, OR — when fallback fires — the classifier that produced the `last_seen` label).
- CLI uses `winner_name` directly: `source = f"classifier:{winner_name}"`. Drop the `chain_names[-1]` hack.
- Update every existing call site + every test that touches the surface (`test_classifier_registry.py`, `test_cli_classify.py`, and `test_classify_cli_e2e.py` only if its assertions break).

**ClassifierConfig** (`corpus_forge/config.py`):
- Switch `llm_url` from `str` to `AnyHttpUrl` with explicit-instance default `AnyHttpUrl("http://localhost:11434")` — pydantic v2 quirk; copy `VLMConfig.ollama_url` pattern exactly.
- Add `llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)`.
- Change `chain` default to `["rule", "llm"]` (was `["rule"]`).
- Keep `model_config = ConfigDict(extra="forbid")`.
- Preserve all P0 fields (`llm_model`, `llm_timeout_s`, `llm_excerpt_chars`, `escalation_threshold`).

**Wire LLMClassifier into `classifiers/__init__.py::_load_classifier`**:
- The current `_load_classifier(name) -> Classifier` calls the class with no args; change to accept the config object so the `llm` branch can forward kwargs (`model`, `llm_url`, `timeout_s`, `excerpt_chars`, `temperature`).
- Suggested shape: `_load_classifier(name, config) -> Classifier`, where `config` is the `ClassifierConfig` instance (or `None` for default `RuleBasedClassifier()`).
- `register_default_classifiers(config)` already receives the config — just pass it through.

**`config.example.toml`** — RICH DOCS (every field gets a one-line comment + a paragraph header):
- `[vlm]` block: replace existing sparse comments with a paragraph header + one comment per field + a commented-out remote-Ollama example (`# ollama_url = "https://ollama.internal.example.com"`) with a one-line rationale (VRAM / latency / container resource constraint).
- `[classifier]` block: replace existing sparse comments with the SAME pattern. Every field gets a comment explaining what it controls, valid values, and (where relevant) the trade-off. Include BOTH `llm_url = "http://localhost:11434"` AND a commented-out remote example.
- Style baseline: the current `[vlm]` block is your style FLOOR — exceed it.

**Tests** (`tests/unit/test_llm_classifier.py`, new — use `unittest.mock.patch`):
- Happy path: mock `requests.post` to return a 200 with `{"response": "{\"class\": \"book\", \"confidence\": 0.82, \"rationale\": \"long-form fiction\"}"}`. Assert the returned `ClassLabel` round-trips.
- Malformed outer JSON: outer 200 returns non-JSON body → `ClassifierResponseError`.
- Inner JSON has invalid `class`: returns `ClassLabel("other", 0.2, "invalid LLM output: …")` + logs WARNING (assert via `caplog`).
- Inner JSON has out-of-range `confidence` (e.g. 1.5): clamp to 1.0.
- Inner JSON is unparseable (model emitted prose): graceful fallback to `class=other`.
- Timeout: `requests.Timeout` → `ClassifierTimeoutError`.
- Connection error: `requests.ConnectionError` → `ClassifierUnavailableError`.
- 5xx: 500 status → `ClassifierResponseError`.
- 4xx: 404 status → `ClassifierResponseError`.
- Non-default URL: instantiate `LLMClassifier(llm_url="https://hosted.example.com")`, assert `requests.post.call_args[0][0] == "https://hosted.example.com/api/generate"`.
- Excerpt budget: when `len(doc.text) > excerpt_chars`, assert the prompt has head + tail but NOT the middle (check a specific middle substring is absent and head/tail substrings are present).
- Format labels in prompt: assert all `(k, v)` pairs from `doc.format_labels` show up in the assembled prompt.
- All 9 enum values appear in the prompt (sanity check on prompt construction).

**Registry tests** (`tests/unit/test_classifier_registry.py` — update):
- `ClassifierRegistry.classify` now returns `tuple[str, ClassLabel] | None`. Update every existing assertion. Add a new test specifically asserting the winner name matches the classifier that produced it (including the low-confidence fallback case).

**CLI tests** (`tests/unit/test_cli_classify.py` — update):
- If the existing test asserts `source = 'classifier:rule'`, that's still correct when the rule classifier wins (most fixtures). Add an assertion for the dry-run JSON output containing the `classifier` field reflecting the winner.

**Config tests** (`tests/unit/test_config_classifier.py` — update):
- `test_default_chain_is_rule_only` → rename to `test_default_chain_is_rule_then_llm` and assert `["rule", "llm"]`.
- New test for `llm_temperature` default + bounds.
- New test that `llm_url` accepts a non-default URL: `ClassifierConfig(llm_url="https://hosted.example.com")` succeeds; the resulting `str(c.llm_url)` contains "hosted.example.com".
- Existing TOML-loader tests asserting `cfg.classifier.chain == ["rule"]` from a no-classifier-block config need updating to `["rule", "llm"]`.

### C-12 — Live OCR-text E2E

**`pyproject.toml` markers** — register `requires_ollama_text: requires a running ollama daemon with qwen2.5:*-instruct pulled` alongside the existing `requires_ollama`.

**`tests/integration/conftest.py`** — add `_probe_ollama_text()` mirroring `_probe_ollama()`:
- Same `GET /api/tags` probe.
- Accept any tag matching `qwen2.5:*-instruct` (the project default is `qwen2.5:7b-instruct`).
- Add `ollama_text_ready` session-scoped fixture + a marker-driven skip in `pytest_collection_modifyitems` analogous to the existing `requires_ollama` plumbing.

**`tests/integration/test_classify_llm_e2e.py`** — 4 tests, all `pytestmark = [pytest.mark.integration, pytest.mark.requires_ollama_text]`:
1. **`test_llm_classifier_real_document`**: instantiate `LLMClassifier(model="qwen2.5:7b-instruct", llm_url="http://localhost:11434")`. Build a `ClassifiableDocument` from an ambiguous markdown excerpt. Assert returned `ClassLabel.value in ALLOWED_CLASS_VALUES` and `0.0 <= confidence <= 1.0`.
2. **`test_escalation_chain_fires_llm`**: build `[RuleBasedClassifier(), LLMClassifier(...)]` with `escalation_threshold=0.4`. Feed a doc where the rule classifier returns `class=other` (no format-label, no path hint, plain markdown body). Assert the FINAL result is from the LLM (winner_name == "llm") and is NOT the rule's `class=other`.
3. **`test_high_confidence_rule_skips_llm`**: cost guard. Feed a doc with `format_labels=[("format", "code")]` (rule returns `code` at 0.99). Mock-spy `requests.post` (use `unittest.mock.patch` to wrap `requests.post` and count calls). Assert: zero HTTP calls to Ollama happened.
4. **`test_cli_end_to_end_postgres`**: testcontainers Postgres fixture (`pg_dsn`) + chain `["rule", "llm"]`. Use the `tests/fixtures/multi_format_corpus/` tree, ingest, then `corpus-forge classify`. Assert at least one `document_labels.source = 'classifier:llm'` AND at least one `'classifier:rule'`. (Don't pin specific docs.)

Time budgets per dispatch: each test < 60s, full file < 120s.

### C-13 — Docs + CLI help

**`README.md`**:
- New H2 between "Distribution / licensing" and "Architecture" titled "Document classification":
  - One paragraph summary of the chain (rule → LLM with escalation).
  - The 9-value taxonomy table.
  - 3-line usage example (`corpus-forge classify --dry-run --json`).
  - One short paragraph calling out the local-vs-remote principle: every model client takes a URL, default local, swap to remote by changing the URL.
- Under existing "Distribution / licensing": add a new H3 "Model endpoints (local vs remote)" with one short paragraph stating the principle, with one-line callouts for VLM (`vlm.ollama_url`) and classifier (`classifier.llm_url`).

**`docs/architecture.md`**:
- New H2 "Document classification" placed AFTER "Multi-format extractor layer":
  - Seam diagram (ASCII similar to the multi-format diagram) showing `documents → ClassifierRegistry → classify → apply_label`.
  - Classifier chain composition (rule, then llm).
  - Escalation policy (threshold + first-clears-bar dispatch + fallback to last-seen).
  - 9-value taxonomy table (copy from plan).
  - Sub-section "Local vs remote model endpoints" applicable to all model clients.

**`corpus_forge/cli.py` — `classify` help + cost-guard preflight**:
- Expand the docstring on the `classify` command to mention chain composition, escalation threshold, cost guard.
- Cost-guard preflight message: replace the single-line "Classifying N document(s) via chain=[...]. Rule classifier is microseconds/doc." with a breakdown:
  - "Classifying N document(s) via chain=[rule, llm]."
  - If `"llm"` in chain: "Worst-case LLM cost: up to N LLM calls (~5-10 s/doc on qwen2.5:7b-instruct, M-series). Rule classifier short-circuits high-confidence docs (≥ <threshold>)."
  - If `"llm"` not in chain: "Rule classifier only — microseconds per document."

### C-14 — P1 gate

1. Run `LLMClassifier` against 5-10 ambiguous documents from `tests/fixtures/multi_format_corpus/` (e.g. plain prose markdowns, the HTML article fixtures, the notebook — anything the rule classifier returned `other` or `< 0.4 confidence` for).
2. Capture results: doc path, rule's class+confidence, LLM's class+confidence+rationale.
3. Write the capture as a `## C-14 manual smoke results` block in `.planning/tdd/phase_e_classification.md` UNDER the `## Status` section.
4. Mark C-10..C-14 done in `phase_e_classification.md`'s Task table.
5. Flip the Status block: "Phase E: P0 complete; P1 pending kickoff" → "Phase E: **COMPLETE** (P0 closed at `ec3fe15`, P1 closed at <orchestrator commit>)".
6. Tick the P1 checkboxes in `.planning/active_tasks.md`.
7. Run `make ci` one final time; confirm exit 0; include the gate output in the wave report.

## DAG

- **Wave 3** (now): C-10/11 (single coherent task — they collide on `classifiers/__init__.py` + the registry tuple-return).
- **Wave 4** (after C-10/11 green): C-12 (live integration) and C-13 (docs + CLI help) IN PARALLEL — disjoint surfaces.
- **Wave 5** (after C-12 + C-13 green): C-14 P1 gate (manual smoke + bookkeeping + `make ci`).
