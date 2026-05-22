# E2E + UX flows — coverage plan

Seeded by Nightly run `2026-05-21T02-14-07Z`, task 0006. The goal is a
prioritized backlog of end-user flows that warrant E2E tests
*specifically* checking that the flow is **easy to navigate** — not just
that the happy path returns exit 0.

Current state:

- 15 `*_e2e.py` files under `tests/integration/`, mostly **internal
  contract** tests (chunk reuse, embedder dispatch, feedback DB writes,
  classifier output, etc.). They prove the machine works; they don't
  prove the human-facing surface is friendly.
- Almost no test asserts on the *shape* of CLI output, error
  messages, or recovery prompts.
- No test simulates the curl-pipe-bash → `setup --quick` → `migrate`
  → `ingest` → `embed` → `search` journey end-to-end as a single
  scripted scenario.

## What "human-friendly" means as a test assertion

Five testable properties. A flow passes if it satisfies all five:

| # | Property | How a test checks it |
|---|----------|----------------------|
| 1 | **Exit code matches the user's mental model.** Success ⇒ 0; missing config ⇒ specific non-zero; bad arg ⇒ different specific non-zero. | Assert exact exit codes; reject `exit 1` as a catch-all for everything that went wrong. |
| 2 | **Error messages name the broken thing AND the fix.** "Could not load config" alone is not enough; "Could not load config at /path/X — run `corpus-forge setup` to create one" is. | Substring assertion: the error message MUST contain a verb-form recovery command (`run`, `pass`, `set`, `install`). |
| 3 | **Output is parseable in BOTH modes.** Human mode renders a Rich table; `--json` mode emits one JSON line per event with stable keys. | Assert `--json` output decodes; assert key set is a stable subset across versions. |
| 4 | **Recovery from a partial failure leaves no half-state.** If `ingest` is killed mid-stream, the next `ingest --once` resumes cleanly without losing or duplicating chunks. | Assert idempotency: run twice, check chunk count is identical. |
| 5 | **The "what should I do next?" hint exists at every dead end.** Empty corpus → "ingest your first file with X"; empty search → "no results; try a broader query or check `dataset` filter." | Substring assertion for a `next-step` hint in the no-results / empty-state output. |

Tests that assert these properties read like contract specs, not
implementation tests. They survive refactors of the internals.

## Priority backlog (high → low value, by user-impact)

### P0 — First-run journey (the make-or-break flow)

**One scripted scenario, broken into 6 stages, each with its own
property assertion.** Tests live in
`tests/e2e/test_first_run_journey.py` (new directory; the test imports
`tests.integration.conftest`'s docker fixtures for Postgres).

1. `install.sh --non-interactive` against a Docker container with no
   Python yet → exit 0, `corpus-forge --version` on PATH, migrate ran.
   (Already covered for the script *layer* in
   `tests/scripts/test_install_sh.py`; the missing piece is the
   container-level full install. Punt unless we add a Docker-based
   integration lane.)
2. `corpus-forge doctor --json` immediately after install → exit 1 or 2
   (some checks WARN/FAIL, that's expected on a bare host), JSON
   decodes, no traceback on stderr. Assertions: properties 1, 2, 3.
3. `corpus-forge setup --quick` non-interactively → config.toml written,
   no secrets in plaintext, the resulting config validates. Assertion:
   property 2 — if a required answer is missing, the error names it.
4. `corpus-forge ingest --once --dataset notes` against a 3-file
   fixture → all 3 documents land, chunk count > 0, no UserWarning on
   stderr (regression for the schema-shadow fix in PR #18).
   Assertions: properties 1, 4.
5. `corpus-forge embed -e qwen3_8b` against a stubbed embedder
   endpoint → all chunks embedded, idempotent (re-run = no-op).
   Assertion: property 4.
6. `corpus-forge search "<term>" --k 5 --json` → JSON envelope with
   `results` array; if the term doesn't match, the "no results" branch
   carries a `hint` field. Assertions: properties 3, 5.

### P1 — Single-command UX checks (cheap, high signal)

Six small unit tests in `tests/unit/test_cli_human_friendly.py`. Each
runs one CLI verb against a known-broken state and asserts the
recovery hint exists.

| Test | Broken state | Property | Hint expected |
|------|--------------|----------|---------------|
| `test_doctor_with_no_config_names_setup_command` | no `~/.config/corpus-forge/config.toml` | 2 | "run `corpus-forge setup`" |
| `test_ingest_with_no_config_names_setup_command` | same | 2 | same |
| `test_embed_with_no_active_embedder_names_admin_command` | config exists, no `active: true` embedder | 2 | "use `corpus-forge embedder set-active`" |
| `test_search_with_empty_index_carries_ingest_hint` | DB up, 0 chunks | 5 | "ingest your data first" |
| `test_search_with_zero_results_carries_broaden_hint` | DB has chunks, query matches none | 5 | "try a broader query" |
| `test_migrate_against_unreachable_db_names_dsn` | DSN points at a closed port | 2 | the DSN host:port in the error |

These are five-minute tests against `corpus_forge.cli` via Typer's
`CliRunner` — no real DB, no real network. Should land before P0.

### P2 — Multi-command journey checks (curation + admin)

| Flow | Assertion |
|------|-----------|
| `embedder add` → `embedder list` shows the new row | property 1 + the new name appears in stdout |
| `embedder list` with two configured embedders (one orphaned in DB) | the table renders both rows; orphaned ones carry an `(orphan)` marker. **Needs a follow-up to actually surface orphans — see task 0003 notes.** |
| `corpus-curate` skill loop: pick → commit → re-pick | the second pick differs from the first (the selector advances). Already partially covered in `test_curation_e2e.py`; add an assertion that the chat-loop response includes a clearly-labelled diff between current and proposed metadata. |
| `bug-report` with a doctor WARN → bundle JSON shape | already smoke-tested in task 0002 notes; promote that into a real test that diffs the bundle filenames against a manifest. |
| `dataset add` then `dataset remove` | the `remove` verb's "are you sure?" prompt actually prompts under TTY and accepts `--yes` for CI. |

### P3 — Recovery / chaos flows

These are the highest-confidence "is this product trustworthy?" tests.
They take effort to write but each one prevents a Phase-M-class
incident.

1. **Mid-ingest crash** — kill `ingest --once` after N documents; the
   next `ingest --once` resumes from where it stopped. Assert chunk
   count after two runs equals chunk count after one uninterrupted
   run. Property 4.
2. **Embedder rename mid-DB** — change `[[embedders]].name` in
   config; assert `embedder list --include-orphans` (see P2) surfaces
   the old name as `(orphan)` and that re-running `embed` does NOT
   delete the orphan's vectors. Property 4 + property 5 (hint:
   "use `embedder remove --vectors` to clean up orphans").
3. **Concurrent writers** — two `ingest` processes racing against
   the same dataset. Backend's chunk-reuse logic should produce no
   duplicate chunks. Property 4.
4. **Postgres dropped mid-run** — `pkill -9 postgres` mid-embed. Next
   `embed` resumes from the last committed chunk; no orphaned
   transaction rows. Property 4.
5. **Disk full** — fill the DB volume to 100%; `ingest` should produce
   a clear "no space left on device — see corpus-forge estimate" error,
   not a tangled Postgres internal error. Property 2.

### P4 — Out-of-scope (for now)

- True browser-based UX flows (no UI yet).
- Network-degradation tests (slow Ollama, flaky GitHub) — would need
  toxiproxy or similar.
- Multi-OS install matrix beyond the existing `install.sh` /
  `install.ps1` script tests.

## Suggested rollout order

1. P1 (cheap, 1 day).
2. P0 first-run journey, *without* the install-container piece (use the
   already-installed test interpreter; assert from `migrate` forward).
3. P2 curation + admin journey assertions.
4. P3 — pick the highest-fear scenarios first (mid-ingest crash,
   concurrent writers). The Postgres-dropped + disk-full scenarios are
   really platform tests; defer.

## Open questions

- Where does the `[e2e]` marker live in `pyproject.toml`? Currently
  the `*_e2e.py` files don't carry an explicit pytest marker; they're
  selected by filename pattern only. If we add a true E2E lane in CI,
  it'd be worth promoting that to a marker so `pytest -m e2e` works.
- How do we want to model the "no config" path in fixtures? Each P1
  test recreates the broken state from scratch — could be a shared
  `no_config` fixture in `tests/conftest.py`.
- Should the "next-step hint" be a structured field in JSON output
  (`{"event": "no_results", "hint": "..."}`) or just a string substring
  in human output? The structured form is more testable and lets us
  build a "tip-of-the-day" surface later. Recommend: structured.

## Acceptance signal

This plan is "done" when:

- A `tests/e2e/` directory exists with at least the P0 journey scaffolded.
- `tests/unit/test_cli_human_friendly.py` exists with the six P1 tests.
- The five testable properties at the top of this doc are referenced by
  at least one test each.
- A `make e2e` / `uv run pytest tests/e2e/` target works locally
  against the existing testcontainers fixtures.

Until then, this doc is the source of truth for "what would a
human-friendly corpus-forge look like, as code we can run in CI."
