# TDD Task Board — Phase L / Wave 6 (Diagnostics — bug-report, logs, redactor)

_Owner: tdd-principal. Workers: read freely. Edit only your claimed row's
`status` and `claimed_by`._

Source plan:
`/Users/evanowen/Library/Mobile Documents/com~apple~CloudDocs/Workspace/playground/corpus-forge/.planning/tdd/phase_l_cli_ux.md`
§9 (bug-report), §11 (logs subcommand), plus the §8 carry-over (Wave 5
embedder helpers that landed in `corpus_forge.embedders.fingerprint` but
never got the concrete backend methods).

Dispatch input: orchestrator brief, Phase L / Wave 6 kickoff after Wave
5 landed (`8625d0b`).

> Previous slice (Wave 5) summary archived in git history at `8625d0b`.

## Project gates
- lint: `uv run ruff check`
- format: `uv run ruff format --check`
- test (Wave 6 surface):
  `uv run python -m pytest tests/backends tests/diagnostics tests/cli/test_bug_report.py tests/cli/test_logs_subcommand.py -x`
- regression:
  `uv run python -m pytest tests/unit tests/cli tests/embedders tests/backends -x`
  (no new failures vs Wave 5 baseline)
- coverage-min: keep current baseline (no regression)

## Hard constraints (from dispatch + project)
1. **DO NOT COMMIT, DO NOT PUSH.** Workers stage only. Orchestrator commits.
2. **NO `typer.echo/secho/prompt/confirm`** outside `corpus_forge/ui/` —
   the `tests/cli/test_no_typer_echo.py` regression will fail you.
3. **`uv run python -m pytest`**, never bare `pytest`.
4. Themed output only via `corpus_forge.ui.console` (`info`, `ok`,
   `warn`, `error`, `console.print`). `Confirm.ask` / `Prompt.ask` come
   from `corpus_forge.ui.prompts`.
5. `corpus-forge bug-report` zip filename pattern is exactly
   `corpus-forge-bugreport-<ISO date>-<short-hash>.zip` written in CWD
   by default; `<short-hash>` is the 8-char prefix of the manifest
   content hash (deterministic from manifest contents → so a
   re-generation with identical manifest data produces the same hash).
6. The bug-report URL is
   `https://github.com/ulmentflam/corpus-forge/issues/new?template=bug.yml&title=...`
   with `title=[bug-report <short-hash>]` URL-encoded.
7. Redactor patterns (compile once at module top): DSN/connection
   string, OpenAI-style `sk-`, xAI-style `xai-`, Anthropic-style
   `claude-`, generic `api_key=`/`password=`/`secret=`, Bearer tokens,
   `base_url` values containing `@`.
8. `redact_string(s) -> tuple[str, int]` returns the redacted text +
   count of replacements made. Idempotent — running twice over the same
   input yields the same string with **0** new redactions.
9. `redact_toml_dict(doc) -> tuple[doc, int]` walks a tomlkit
   `TOMLDocument` and replaces string values at keys matching `*dsn*`,
   `*password*`, `*_api_key`, `*api_key`, `*secret*`, `*token*` (case
   insensitive) with the literal `«redacted»` (Unicode guillemets).
10. `logs tail --follow` polls 250 ms; clean SIGINT exit (no
    `inotify`). Default component is `cli`, default `-n` is 200.
11. `logs clear` prompts via `Confirm.ask` unless `--yes` is passed.
12. Backend helper SQL must use the same `_execute` patterns the
    existing methods use — no new connection plumbing.
13. The Wave 5 `getattr` / try-except shims in
    `corpus_forge/embedders/fingerprint.py` for the three new helpers
    SHOULD STAY (defensive against third-party backends) but the
    real `PostgresBackend` / `SQLiteBackend` paths must now activate
    them.
14. iCloud sync race: keep the working tree clean per file; never
    commit until orchestrator has read `git status` + `git diff
    --stat`.

## Decomposition notes (orchestrator)

### Surface-disjoint matrix

| Task | Owns (writes) | Reads (depends on) |
|------|---------------|--------------------|
| W6-01 (backend helpers) | `corpus_forge/backends/postgres.py`, `corpus_forge/backends/sqlite.py`, `corpus_forge/backends/base.py` (Protocol additions), `tests/backends/__init__.py`, `tests/backends/test_postgres_embedder_helpers.py`, `tests/backends/test_sqlite_embedder_helpers.py` | existing embedder rows shape from `alembic/versions/0001_core.py:143-156` |
| W6-02 (redactor) | `corpus_forge/diagnostics/__init__.py`, `corpus_forge/diagnostics/redact.py`, `tests/diagnostics/__init__.py`, `tests/diagnostics/test_redact.py` | none |
| W6-03 (bug-report) | `corpus_forge/diagnostics/bug_report.py`, `tests/diagnostics/test_bug_report.py`, `.github/ISSUE_TEMPLATE/bug.yml` | W6-02 (`redact_string`, `redact_toml_dict`, `redact_file`); W6-01 (for `db_summary.json` via backend) |
| W6-04 (logs subcommand) | `corpus_forge/diagnostics/logs.py`, `tests/diagnostics/test_logs_subcommand.py` | `corpus_forge.logging_config.get_log_dir` (already shipped); `ui.console` |
| W6-05 (CLI wiring + doctor daemon line) | `corpus_forge/cli.py`, `corpus_forge/doctor/checks.py` | W6-03, W6-04 |

### Wave shape

- **Wave A (parallel testers)**: W6-01 + W6-02 + W6-04 testers fire in
  one Agent batch. Surfaces are fully disjoint. W6-03 tester also
  fires in this wave with the understanding that the real
  `redact_string` API is contract-defined in the brief (tests use
  imports that will be RED until W6-02 lands).
- **Wave B (parallel coders)**: W6-01 coder + W6-02 coder + W6-04
  coder fire in one Agent batch. All three module surfaces are
  independent.
- **Wave C (single coder)**: W6-03 (bug-report) coder runs after Wave
  B because it imports from `redact` + the new backend helpers.
- **Wave D (single coder)**: W6-05 (CLI wiring + doctor daemon-line)
  runs last because it imports both `bug_report` and `logs` modules.
- **Wave E (parallel QA)**: one QA per task (W6-01 → W6-05). Each gets
  the green commit hash, the task brief, and runs `ruff check` +
  `ruff format --check` + the task's pytest selection.

### Implementation hints

#### W6-01 — backend helpers

Add to both backends (postgres + sqlite). The Protocol in
`backends/base.py` should declare the three new methods so the
fingerprint module's `try/except AttributeError` falls through only
for third-party / mock backends:

```python
def find_embedder_row_by_name(self, name: str) -> dict | None: ...
def count_existing_embeddings(self, embedder_id: int) -> int: ...
def update_embedder_config_blob(
    self, embedder_id_or_name: int | str, config_blob: dict
) -> None: ...
```

NOTE: `fingerprint.save_active_fingerprint` calls
`backend.update_embedder_config_blob(row["id"], new_blob)` — i.e. by
**id**. The brief's signature example shows `(name, config_blob)` but
the actual consumer uses id. Accept **either** (overload on type):

```python
def update_embedder_config_blob(
    self, embedder: int | str, config_blob: dict
) -> None:
    """Update the embedder row's ``config`` JSONB.

    ``embedder`` may be an integer row id or a string name — both
    paths are kept for ergonomic callers.
    """
```

Postgres SQL shape:
```sql
SELECT id, name, provider, model_id, dimension, normalized, distance,
       active, table_name, config
  FROM corpus.embedders WHERE name = %s
```

SQLite mirror (drop the schema prefix; JSON-decode the `config` blob
before returning so callers see a dict — the fingerprint module
already tolerates string-shape but a dict is friendlier):

```python
row = ...
row["config"] = json.loads(row["config"]) if isinstance(row["config"], str) else row["config"]
row["normalized"] = bool(row["normalized"])
row["active"] = bool(row["active"])
return row
```

`count_existing_embeddings`: fetch the row first to get `table_name`,
then `SELECT COUNT(*) FROM <table_name> WHERE embedder_id = ?`. On
postgres the table lives under `corpus.<table_name>`; on sqlite it's
bare. If the embedder id doesn't resolve, return 0 (don't raise).

`update_embedder_config_blob(embedder, blob)`:
- If `embedder` is `int`: `UPDATE embedders SET config = ? WHERE id = ?`.
- If `embedder` is `str`: `UPDATE embedders SET config = ? WHERE name = ?`.
- Postgres uses `psycopg.types.json.Json(blob)`; SQLite serializes
  with `json.dumps(blob)`.

#### W6-02 — redactor

Module-level pre-compiled regexes (don't recompile per call):

```python
_PATTERNS = [
    re.compile(r"(postgres(?:ql)?|mysql|mongodb|redis)(\+\w+)?://[^/\s]+@[\S]+", re.I),
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"xai-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"claude-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)(api[_-]?key|password|secret)\s*[=:]\s*[\"']?[^\s\"']+[\"']?"),
    re.compile(r"Bearer\s+[A-Za-z0-9_.\-]+"),
]
```

`redact_string` runs each pattern in turn and counts matches. Empty
input → `("", 0)`. After replacement, the literal string in the result
is `«redacted»` (Unicode 0xab/0xbb) so a single `grep '«redacted»'`
finds every site.

`redact_toml_dict` walks `doc` keys recursively. The matcher is the
**key name**, not the value; this is so `dsn = "..."` is redacted even
when the value doesn't pattern-match (Postgres DSNs with no embedded
credentials still leak hostname/port). Use
`tomlkit.items.{Table,InlineTable,AoT}` `.items()` for traversal.
Preserve comments/order — that's the reason for tomlkit over
`tomllib`.

`redact_file(path: Path) -> int`: read text (utf-8, errors=replace),
call `redact_string`, write back atomically (temp file + rename), return
count.

Idempotency: tests must verify a second `redact_string(redact_string(s)[0])`
returns the same string with 0 added redactions.

#### W6-03 — bug-report

Module surface:

```python
def collect(
    *,
    out: Path | None = None,
    include_logs: bool = True,
    include_db: bool = True,
    zip_bundle: bool = True,
) -> BugReport:  # NamedTuple(path, redacted_count, short_hash)
```

Bundle the staging directory under a tempdir; build files in this
order so the manifest hash captures everything: `doctor.json`,
`config.redacted.toml`, `logs/*`, `env.txt`, `deps.txt`,
`db_summary.json`, **THEN** `manifest.json` (which lists the
files), **THEN** compute short hash of `manifest.json` content,
**THEN** write `README.txt` referencing the hash. Rename the zip
last.

`manifest.json` keys (per brief):

```python
{
    "corpus_forge_version": str,
    "os": str,                       # platform.system()
    "os_version": str,               # platform.release() or platform.mac_ver/win32_ver
    "python_version": str,           # platform.python_version()
    "arch": str,                     # platform.machine()
    "ts_utc": str,                   # iso ms
    "hostname_hash": str,            # sha256(socket.gethostname())[:16]
    "tool_path": str,                # shutil.which('corpus-forge') or sys.argv[0]
    "redaction_log": list[str],      # category names with redactions
    "agent_mode_at_time_of_capture": str,  # 'human' for Wave 6 (Wave 9 fills)
}
```

`env.txt` filtering: keep keys whose prefix matches one of `CF_`,
`OLLAMA_`, `CLAUDECODE`, `AI_AGENT`, `OPENCODE`, `GEMINI_CLI`,
`COPILOT_CLI`, `CODEX_`. Run values through `redact_string`.

`deps.txt`: try `subprocess.run([sys.executable, '-m', 'pip', 'list',
'--format=freeze'], capture_output=True, text=True, timeout=10)`. On
failure, fall back to `importlib.metadata.distributions()` →
`name==version` lines. On both failure, write a 1-line note.

`db_summary.json`:

```python
try:
    config = Config.load()
    backend = _get_any_backend(config)  # reuse cli helper or inline
    summary = {
        "datasets": backend._execute("SELECT COUNT(*) AS n FROM datasets")[0]["n"],
        "documents": ...,
        "chunks": ...,
        "embedders": [
            {"name": r["name"], "dimension": r["dimension"], "count": ...}
            for r in backend._execute("SELECT name, dimension, table_name FROM embedders")
        ],
    }
except Exception as exc:
    summary = {"unavailable": str(exc)}
```

`recent_events.txt`: flush the `MemoryHandler` ring buffer (Wave 1
exposes `logging_config.get_ring_buffer()`). Iterate
`ring.buffer` list; format `<ts> [<level>] <logger>: <msg>` per record,
trim to 200 lines.

Console output after writing:

```python
ui_ok(f"Wrote {zip_path.name} ({_human_bytes(zip_path.stat().st_size)})")
ui_ok(f"{redacted_count} secrets redacted")
console.print("")
console.print("Attach this file to a new issue at:")
console.print(f"  [accent.path]{issue_url}[/accent.path]")
```

URL build: use `urllib.parse.quote("[bug-report a3f9]")` for the title
value.

`.github/ISSUE_TEMPLATE/bug.yml` is a standard GitHub form YAML:
```yaml
name: Bug report
description: Report a problem with corpus-forge
title: "[bug-report <short-hash>]"
labels: ["bug"]
body:
  - type: markdown
    attributes:
      value: |
        Thanks for the report. Please attach the
        `corpus-forge-bugreport-*.zip` produced by
        `corpus-forge bug-report` so a triager can reproduce.
  - type: textarea
    id: what-happened
    attributes:
      label: What happened?
    validations:
      required: true
  - type: textarea
    id: steps
    attributes:
      label: Steps to reproduce
    validations:
      required: true
  - type: textarea
    id: expected
    attributes:
      label: Expected behavior
  - type: textarea
    id: actual
    attributes:
      label: Actual behavior
  - type: checkboxes
    id: preflight
    attributes:
      label: Pre-flight
      options:
        - label: I ran `corpus-forge doctor`
        - label: I attached the `corpus-forge-bugreport-*.zip`
```

#### W6-04 — logs subcommand

`typer.Typer` sub-app with `path`, `tail`, `clear`. Module exports the
sub-app + the underlying helpers (so tests can unit-test the
non-CLI surface without spawning subprocesses).

`tail`:
- Resolve the file: `logging_config.get_log_dir() / f"{component}.log"`.
- If missing: `ui_warn(f"{file} does not exist yet")`, return.
- `--follow`: open + seek to end of file (or `-n` lines back if
  asked), then loop `time.sleep(0.25)` and read new bytes. Handle
  `KeyboardInterrupt` by returning 0 cleanly. Yes, this is a
  blocking implementation — that's fine; the only tests we need are
  "happy path read" and "SIGINT clean exit" (we'll fire a thread
  that calls `os.kill(os.getpid(), signal.SIGINT)` after ~150 ms).

`clear`:
- `--all` or `--component <name>` required (mutually exclusive).
- Without `--yes`, call `Confirm.ask("Clear logs?")`. Tests must
  patch this.
- For each target, `path.write_text("")` after `path.rename(path.with_suffix(path.suffix + ".rotated"))` (or simply truncate; rotation is optional — keep it simple, truncate is fine).

Theme log levels via the existing UI colors:
```python
_LEVEL_STYLE = {
    "DEBUG":   "muted",
    "INFO":    "info",
    "WARNING": "warn",
    "WARN":    "warn",
    "ERROR":   "error",
    "CRITICAL":"error",
}
```

Parse the standard log format `YYYY-MM-DD HH:MM:SS.ms [LEVEL  ] logger: msg`
using a regex; on parse miss, print the line as `muted`.

#### W6-05 — CLI wiring + doctor daemon line

- `corpus_forge/cli.py`: register `bug-report` as `@app.command("bug-report")`
  + a `logs_app = typer.Typer(...)` with `app.add_typer(logs_app, name="logs")`.
- `corpus_forge/doctor/checks.py`: add `_check_last_daemon_activity()`
  that reads the tail of `<log_dir>/daemon.log`, finds the most
  recent INFO line, and returns a `CheckResult` with the human-friendly
  "12s ago — <msg>" detail (or `SKIP` when the file doesn't exist).
  Wire into `_CHECKS`.

The doctor surface is rendered via `DoctorReport.render_styled` already
(Wave 3); the new check just appends to `_CHECKS`.

### Acceptance

- W6-01: `find_embedder_row_by_name` returns dict for known embedder /
  `None` for unknown. `count_existing_embeddings` matches a seeded
  embedding count. `update_embedder_config_blob` round-trips and the
  fingerprint compare sees the new blob next call.
- W6-02: `redact_string` redacts every pattern, idempotent, 0-count on
  innocuous strings. `redact_toml_dict` preserves comments/order. The
  full Wave 6 zip's grep finds 0 raw secret patterns from the
  redactor's pattern set.
- W6-03: `corpus-forge bug-report` exits 0; zip in CWD; `manifest.json`
  has all 10 keys; `--no-zip` writes the staging directory;
  `--no-logs` / `--no-db` omit those sections; short hash is
  deterministic from manifest content (i.e. two runs with identical
  contents produce identical hashes — tested by injecting a frozen
  clock + frozen hostname).
- W6-04: `logs path` prints the platformdirs path; `logs tail -n 5`
  shows the last 5 lines; `logs tail --follow` exits 0 on SIGINT;
  `logs clear --component cli --yes` truncates the file.
- W6-05: `bug-report` + `logs` appear in `corpus-forge --help`;
  `doctor` lists a "Last daemon activity" check; existing tests
  unaffected (run `test_no_typer_echo.py` + the global flags suite).

### Definition of done

1. New tests pass under
   `uv run python -m pytest tests/backends tests/diagnostics
   tests/cli/test_bug_report.py tests/cli/test_logs_subcommand.py -x`.
2. Regression: `uv run python -m pytest tests/unit tests/cli
   tests/embedders tests/backends -x` is green (no new failures vs
   Wave 5 baseline).
3. `uv run ruff check` clean on touched files.
4. `uv run ruff format --check` clean on touched files.
5. Manual smoke: `uv run corpus-forge bug-report --no-db` produces a
   zip; `unzip -l` shows expected files; `unzip -p <zip>
   config.redacted.toml | grep -i dsn` shows `«redacted»`.
6. `uv run corpus-forge logs path` prints the cache log directory.

## Tasks

| id | title | depends_on | surface | risk | status | claimed_by | notes |
|----|-------|------------|---------|------|--------|------------|-------|
| W6-01 | Backend embedder helpers (postgres + sqlite + protocol) | — | corpus_forge/backends/postgres.py, corpus_forge/backends/sqlite.py, corpus_forge/backends/base.py, tests/backends/test_postgres_embedder_helpers.py, tests/backends/test_sqlite_embedder_helpers.py | low | done | tdd-principal | 20 tests green; activates Wave 5 drift path on real backends |
| W6-02 | Redactor module | — | corpus_forge/diagnostics/redact.py, corpus_forge/diagnostics/__init__.py, tests/diagnostics/test_redact.py | low | done | tdd-principal | 24 tests green; tomlkit promoted to direct dep |
| W6-03 | bug-report command | W6-01, W6-02 | corpus_forge/diagnostics/bug_report.py, tests/diagnostics/test_bug_report.py, .github/ISSUE_TEMPLATE/bug.yml | med | done | tdd-principal | 10 tests green; deterministic short hash; issue template added |
| W6-04 | logs subcommand | — | corpus_forge/diagnostics/logs.py, tests/diagnostics/test_logs_subcommand.py | low | done | tdd-principal | 9 tests green incl. SIGINT clean-exit |
| W6-05 | CLI wiring + doctor daemon-activity check | W6-03, W6-04 | corpus_forge/cli.py, corpus_forge/doctor/checks.py | low | done | tdd-principal | bug-report + logs in --help; daemon_activity check ships SKIP/OK |

## Summary

- Files changed: 7 modified, 8 new (+ 2 new test dirs).
- Modified: `corpus_forge/backends/{base,postgres,sqlite}.py`, `corpus_forge/cli.py`, `corpus_forge/doctor/checks.py`, `pyproject.toml`, `uv.lock`.
- New: `corpus_forge/diagnostics/{__init__,redact,bug_report,logs}.py`, `.github/ISSUE_TEMPLATE/bug.yml`, `tests/backends/{__init__,test_postgres_embedder_helpers,test_sqlite_embedder_helpers}.py`, `tests/diagnostics/{__init__,test_redact,test_bug_report,test_logs_subcommand}.py`, `tests/cli/{test_bug_report,test_logs_subcommand}.py`, `tests/unit/test_doctor_daemon_activity.py`.
- Gates: 237 W6+neighbor tests passing, 0 new regressions vs Wave 5 baseline (164 pre-existing missing-dep failures unchanged).
- Lint clean, format clean on every touched file.
- Manual smoke: `corpus-forge bug-report --no-db` writes a valid zip with 8 deterministic files; `corpus-forge logs path` prints the platformdirs dir; `corpus-forge doctor --json` includes the new `daemon_activity` check.

## DAG

- Wave A (3 RED testers in parallel): W6-01, W6-02, W6-04
- Wave B (3 GREEN coders in parallel): W6-01, W6-02, W6-04
- Wave C (1 RED tester): W6-03 (after W6-02 + W6-01)
- Wave D (1 GREEN coder): W6-03
- Wave E (1 RED tester): W6-05 (after W6-03 + W6-04)
- Wave F (1 GREEN coder): W6-05
- Wave G (5 QAs in parallel): W6-01 .. W6-05
