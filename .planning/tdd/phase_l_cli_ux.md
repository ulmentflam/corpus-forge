# corpus-forge CLI Beautification & Diagnostics

## Context

corpus-forge already exposes ~20 Typer commands (`setup`, `doctor`, `ingest`, `embed`, `estimate`, `search`, `sync*`, `eval*`, `mcp serve`, `export*`, `bug-report` — TBD). The current UX is functional but undecorated: every command emits plain `typer.echo()`, there are zero progress bars, no centralized color/theme, no rotating log file, no way to detect that the embedder config drifted from what produced existing vectors, and no easy way for a user (or an agent triaging an issue) to attach reproducible diagnostics to a bug report.

This phase brings the CLI to feature-parity with peer tools like Nous's `hermes-agent` — boxed banners, semantic-color prefixes (`✓ → ⚠ ✗`), real progress for long ops, useful background logging, and a one-command bug-report bundle — while adding the operational glue (`--quick` setup, embedder-change detection, `--json` doctor) that the README/CLAUDE.md already promise but haven't shipped.

**Inspiration**: `NousResearch/hermes-agent` — boxed `┌─…─┐` banner around the command name, magenta banner text, cyan `→` info / green `✓` success / yellow `⚠` warn / red `✗` error, dedicated `doctor` for self-diagnosis, multi-path installer that ends in "run `hermes`".

---

## What changes (at a glance)

| # | Change | Files touched (primary) |
|---|---|---|
| 1 | New `corpus_forge/ui/` package — theme, console, banner, progress, prompts | new package |
| 2 | New `corpus_forge/logging_config.py` — rotating file + stderr mirror | new file |
| 3 | Rewrite all CLI command output to route through `ui.console` | `corpus_forge/cli.py` (most touch points) |
| 4 | `setup --quick` flag + ASCII banner on first run | `corpus_forge/setup/wizard.py`, `cli.py:156` |
| 5 | `doctor --json` flag + colored render | `corpus_forge/doctor/checks.py`, `cli.py:258` |
| 6 | `estimate` adds wall-clock time, scan-rate, pending-files (DB-known but un-embedded) | `corpus_forge/estimate.py`, `cli.py:1757` |
| 7 | Progress bars on `ingest`, `embed`, `sync pull/push`, `estimate` walk | each command + `ui/progress.py` |
| 8 | Embedder-fingerprint comparison + interactive rerun prompt | new `corpus_forge/embedders/fingerprint.py`, hooks in `setup/wizard.py`, `cli.py` `ingest`/`embed`, `daemon.py:14` |
| 9 | New `corpus-forge bug-report` command | new `corpus_forge/diagnostics/bug_report.py`, `cli.py` |
| 10 | New `corpus-forge logs tail|path` subcommand | new `corpus_forge/diagnostics/logs.py`, `cli.py` |
| 11 | CRUD command groups: `config`, `embedder`, `ollama`, `dataset`, `source`, `service` | new `corpus_forge/admin/` package, `cli.py` |
| 12 | Agent-mode detection + JSONL output for Claude Code / OpenCode / Gemini | new `corpus_forge/ui/agent.py`, hooks in `ui/console.py`, `ui/banner.py`, `ui/progress.py`, `logging_config.py` |

---

## Design

### 1. UI package — `corpus_forge/ui/`

New package, no behavioral side effects on import. Modules:

- **`theme.py`** — single `THEME` (`rich.theme.Theme`) and string constants for semantic styles.

  **Palette derivation.** The brand palette is taken directly from `assets/logo.svg` (the anvil-and-ember mark): ember orange `#ff8a3d`, deep ember `#b83205`, anvil steel `#2a2f3a` / `#0e1117`, warm cream `#fff7e8` / `#e7dec8`. We use the two ember shades as the *only* brand color in the CLI — anvil steel and cream are background tones in the logo and the terminal's own background fills that role.

  **Two-track palette.** Brand decoration uses fixed truecolor / 256-color values so corpus-forge looks the same across themes. State messaging (info / success / warn / error) uses ANSI *named* colors so it adapts to whichever terminal theme the user runs (Solarized, Dracula, Tomorrow, Apple defaults — all render their own green/yellow/red).

  | Role | Rich style | Glyph | Notes |
  |---|---|---|---|
  | `brand.ember` (primary) | `bold #ff8a3d` (256-color fallback: `color(208)`) | — | banner border, h1 titles, progress-bar fill, prompt `❯` |
  | `brand.forge` (deep accent) | `bold #b83205` (fallback: `color(166)`) | — | section rules, "active job" indicator, drift-panel border |
  | `h1` | `bold #ff8a3d` | — | command titles ("Setup", "Doctor") |
  | `h2` | `bold` | — | sub-sections (theme-color, just bold) |
  | `info` | `cyan` (ANSI) | `→` | "Scanning…", "Loading model…" |
  | `success` | `green` (ANSI) | `✓` | end-of-step confirmations |
  | `warn` | `yellow` (ANSI) | `⚠` | embedder drift, deprecations |
  | `error` | `bold red` (ANSI) | `✗` | exits ≠ 0 |
  | `muted` | `dim` | — | timestamps, paths after the primary fact |
  | `accent.path` | `cyan` | — | file paths, URLs |
  | `accent.number` | `bold cyan` | — | counts, byte sizes, percentages |
  | `prompt.glyph` | `bold #ff8a3d` | `❯` | replaces typer's default `?` |

  **Readability check.** `#ff8a3d` on pure-black terminal bg = contrast ratio 5.2 : 1 (passes WCAG AA for normal text); on pure-white bg = 2.9 : 1 (passes the AA large-text rule, which applies to our usage: 16pt+ equivalent for the banner / 14pt+ bold for headings). `#b83205` is the inverse: 2.7 : 1 on black, 7.9 : 1 on white — strong on light terminals, weaker on dark, so we restrict it to bold short strings (rules, borders, single words) where the eye doesn't fatigue. ANSI named colors are theme-deferred so they inherit whatever contrast the user's terminal already provides. The brand palette intentionally **does not** include cream `#fff7e8` — on a dark terminal it's near-white and indistinguishable from default text; we let `dim` handle "muted" instead.

  **Light-vs-dark detection.** Rich's `Console` already exposes `color_system` and respects `COLORFGBG`. We add a `--light` global flag (and `CF_LIGHT=1` env) that swaps `brand.ember` ↔ `brand.forge` for users whose terminals are light-themed and find `#ff8a3d` washed out. Default behavior matches dark terminals (the majority).

  **Fallbacks for no-color terminals** (`NO_COLOR` env, non-TTY, `--no-color`): glyphs degrade to ASCII (`[OK]`, `[WARN]`, `[ERR]`, `->`) — re-using the pattern from `cli.py:181`. Box-drawing characters in the banner degrade to `+--+` / `|` on terminals reporting < 256 colors (Rich does this automatically via `legacy_windows` / `force_terminal`).

- **`console.py`** — singleton `console = Console(theme=THEME, ...)` plus thin wrappers `info()`, `ok()`, `warn()`, `error()`, `title()`, `panel()`. `console.is_terminal` drives color/glyph fallback. Honors `NO_COLOR` env and a global `--no-color` flag wired at `cli.py:34`.

- **`banner.py`** — `render_banner(title: str, subtitle: str | None = None) -> None`. Box-drawing style modeled on hermes:
  ```
  ┌────────────────────────────────────────────────────────┐
  │  corpus-forge  ·  v0.1.0b3                             │
  │  Chat with your data.                                  │
  └────────────────────────────────────────────────────────┘
  ```
  Shown on `setup` (always), `doctor` (always), and once per `daemon` start. Other commands stay banner-free to keep them scriptable. Rendered via `rich.panel.Panel(..., box=box.ROUNDED, border_style="magenta")`.

- **`progress.py`** — factory `make_progress(description: str, *, total: int | None) -> rich.progress.Progress` returning a preconfigured `Progress` with columns `[SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn, TimeElapsedColumn, TimeRemainingColumn]`. For unbounded ops, the bar column is replaced by `TextColumn("[dim]rate[/dim]")`. Used by ingest, embed, sync, estimate scan. Single context manager, single style.

- **`prompts.py`** — wrappers around `rich.prompt.Prompt` / `Confirm` that share the theme. Replaces `typer.prompt` calls in `setup/wizard.py` so the wizard inherits the palette.

`rich` is already a transitive dep (via Typer); promote it to a direct dep in `pyproject.toml`.

### 2. Logging — `corpus_forge/logging_config.py`

Logging is the load-bearing surface for "what is the daemon doing right now," "did the model load," and "what did the user see before it broke." It complements (not duplicates) the UI: **progress bars show in-terminal motion, loggers record the durable narrative the bug-report ships**. The two never compete — bars are stderr-TTY-only and stop on completion; logger lines persist in the rotating file regardless.

Replaces the lone `logging.basicConfig` call at `corpus_forge/ingest.py:619`.

#### `init_logging(component, *, verbose, quiet)` — entry point

Called once at every CLI command entry, every daemon start, and the MCP `serve` entrypoint. The `component` argument names the rotating-file destination:

| Component | Written by | File |
|---|---|---|
| `cli` | every foreground `corpus-forge <cmd>` invocation | `cli.log` |
| `daemon` | `corpus-forge daemon` long-running process | `daemon.log` |
| `mcp` | `corpus-forge mcp serve` | `mcp.log` |
| `embed-worker` | the detached subprocess spawned by the embedder-drift rerun | `embed-worker.log` |

Three handlers attached to the root `corpus_forge` logger (every existing `logging.getLogger(__name__)` call site is automatically scoped underneath):

1. **Rotating file** at `{platformdirs.user_cache_dir('corpus-forge')}/logs/{component}.log`, 10 MB × 5 files, level **DEBUG** (always-on; the file is the diagnostic substrate). Formatter:
   ```
   %(asctime)s.%(msecs)03d [%(levelname)-7s] %(name)s: %(message)s
   ```
2. **Stderr `RichHandler`** with `console=ui.console`, `show_time=False`, `show_path=False`, `markup=True`. Level: `INFO` by default, `DEBUG` with `--verbose`/`-v`, `WARNING` with `--quiet`/`-q`. Logger names render in the `muted` style so noise visually deprioritizes itself.
3. **In-memory ring buffer** (`logging.handlers.MemoryHandler` capacity 200, target=NullHandler) holding the most recent INFO+ records. `bug-report` flushes this into `recent_events.txt` even if the rotating file has been truncated. Cheap (≈40 KB resident).

Env-var overrides honored at init: `CF_LOG_LEVEL` (overrides verbose/quiet), `CF_LOG_DIR` (overrides platformdirs path — useful for ephemeral containers), `NO_COLOR` (RichHandler degrades to plain).

#### Logger taxonomy (the named loggers everything else uses)

The user's brief called out specific moments that must be visible — "model loading, performing embeddings, syncing a dir." We standardize on these logger names so the rotating file is greppable and the bug-report reader knows the vocabulary:

| Logger | Emits at | Example line |
|---|---|---|
| `corpus_forge.embedders.loader` | INFO when loading begins / finishes; DEBUG on cache hit | `Loading embedder qwen3_8b (sentence-transformers, 1024-dim, device=mps)…` → `Embedder qwen3_8b ready in 4.1s` |
| `corpus_forge.embedders.batch` | DEBUG per batch; INFO every Nth (rate-limited) | `Embedded 320/12481 chunks (rate 71/s, ETA 02:51)` |
| `corpus_forge.embedders.fingerprint` | INFO on detection; WARNING on drift | `Embedder drift: qwen3_8b -> bge-m3 (12,481 chunks affected)` |
| `corpus_forge.ingest.scan` | INFO at start/end of each source root | `Scanning ~/Notes (filesystem source)…` → `Scan complete: 1,284 files, 3.2 GB in 4.7s` |
| `corpus_forge.ingest.extract` | DEBUG per file; INFO on extractor failure | `Extractor pdf_digital failed on file.pdf — escalating to vlm` |
| `corpus_forge.ingest.chunk` | DEBUG per doc; INFO on aggregate every 100 docs | `Chunked 100 docs (avg 7.2 chunks/doc)` |
| `corpus_forge.sync.scan` | INFO when scan starts / completes per dataset | `Sync scan starting for dataset=default plugin=filesystem` |
| `corpus_forge.sync.push` | INFO on debounced file change → ingest trigger | `Push triggered by 3 changed files in ~/Notes` |
| `corpus_forge.sync.pull` | INFO per poll cycle outcome | `Pull cycle: 0 conflicts, 14 new docs ingested` |
| `corpus_forge.backend.postgres` / `.sqlite` | DEBUG on every query; INFO on schema migration | `Migration head: 0017_curation_score` |
| `corpus_forge.mcp.request` | DEBUG on every tool call; INFO on errors | `MCP search(k=5, dataset=default) → 5 hits in 142ms` |
| `corpus_forge.classifier.run` / `corpus_forge.vlm.run` / `corpus_forge.whisper.run` | INFO at start / end of a run | `Whisper transcribe queue: 4 audio files…` |
| `corpus_forge.daemon.lifecycle` | INFO on start / stop / signal | `Daemon started (pid=42138, datasets=[default])` |

**Noise discipline.** The rule of thumb: INFO is "anyone reading after the fact should care," DEBUG is "I'd want this when a bug is in this exact subsystem." Heavy per-item events (per-file extract, per-batch embedding progress, per-query MCP) are DEBUG. Rate-limited INFO summaries (every 100 docs, every Nth embedding batch) replace them in the default stream. Result: an idle daemon writes < 10 lines/min at INFO; a busy daemon writes ~30 lines/min — readable, scrollable, attachable.

#### Progress bars and loggers complement each other

Each long op gets **both**:
- A `rich.progress` bar via `ui.progress.make_progress(...)` for the live TTY — disappears when the op ends.
- A bookending `logger.info(...)` pair: `"<op> started: <N> items"` at top, `"<op> complete: <N> items in <wall>s (rate <r>/s)"` at bottom.
- Plus rate-limited INFO every 10% of progress for ops > 30s, so the rotating log captures progress milestones even when the user wasn't watching.

The progress factory accepts a `logger` argument and emits these bookends automatically — call sites get both surfaces from one line of code:
```python
with ui.progress.make_progress("Embedding chunks", total=n, logger=logger) as p:
    ...
```

#### Daemon: log file is the canonical "is it doing anything?" surface

The daemon prints **nothing** to stdout/stderr after startup banner (it's a long-running service). Everything goes to `daemon.log`. `corpus-forge logs tail --follow` is the documented way to watch it. `corpus-forge sync status` (`cli.py:370`) is upgraded to read the last N lines of `daemon.log` and surface the most recent INFO event ("Last activity: 12s ago — embedding batch 7/40") so a user who didn't tail can still answer "is it stuck?".

#### MCP servers don't break stdio

Critical: `corpus-forge mcp serve --transport stdio` MUST NOT write to stdout (the MCP protocol owns it) and MUST NOT write to stderr in default INFO mode (Claude Code captures it and pollutes the user's transcript). For stdio MCP, `init_logging('mcp', ...)` skips the RichHandler entirely — file-only. The MCP transport is the only command with this carve-out.

#### Existing logger sites stay as-is, but get a one-line audit

All 37+ existing `logger = logging.getLogger(__name__)` declarations stay (they're already correctly scoped). Wave 1 includes a sweep to:
- Rename any logger that doesn't fit the taxonomy (e.g. if `embed.py` uses `logger = logging.getLogger("corpus_forge.embed")` but emits a "loading model" line, that line moves to `logging.getLogger("corpus_forge.embedders.loader")`).
- Add the missing INFO lines the taxonomy promises (model-load start/end, scan start/end, sync cycle start/end). Audit checklist lives at the bottom of Wave 1's commit.
- Demote any existing `print(...)` or `typer.echo` that was acting as a log to `logger.info(...)`.

**Why rotating file (not jsonl):** the bug-report bundler needs human-skimmable logs and Python's `RotatingFileHandler` is one import. JSONL adds parse overhead with no win since `bug-report` already snapshots structured doctor results separately. If we ever need JSONL for an external aggregator we can add a third handler — the logger taxonomy above is already structured-friendly (logger names are stable, message templates are consistent).

### 3. CLI output retrofit

The big mechanical pass. `corpus_forge/cli.py` has ~99 `typer.echo(...)` calls. Replace with:
- `console.print(...)` for plain output
- `ui.ok("…")` / `ui.warn(…)` / `ui.error(…)` / `ui.info(…)` for status lines
- `ui.title("Setup")` etc. for section headers
- existing `[OK]` markers in `setup` (`cli.py:181`) replaced by `ui.ok()` which auto-handles no-color

The yellow `typer.secho` at `cli.py:493` becomes `ui.warn(...)`.

Done command-by-command; commit per command so reverts are surgical. Functional behavior identical — only output style changes.

### 4. `setup --quick` + banner

`cli.py:156` adds a `--quick` boolean flag. `corpus_forge/setup/wizard.py` already drives an interactive wizard reading from `questions.toml`; add a `quick: bool` parameter and a small `QUICK_QUESTIONS` subset hard-coded in `wizard.py`:

| Question | Default | Notes |
|---|---|---|
| Backend kind | `sqlite` | Skips DSN if sqlite |
| Postgres DSN | only if user picked postgres | Reuses existing prompt |
| Ollama URL | `http://localhost:11434` | Probe at end (`GET /api/tags`) — if 200, default-pick a model from the response |
| Embedder model | `qwen3:8b` (or first model returned by Ollama probe) | |
| First dataset name | `default` | |
| Scan root | empty → skip | If empty, prints `→ Run "corpus-forge ingest --once" after editing config to add a source root` |

Quick path always uses defaults for retrieval, classifier, VLM, whisper, code-enricher (all stay disabled / safe). Generates the same `config.toml` shape `Config.load()` already validates.

Banner is rendered at the top of `setup` (both quick and full) and at the top of `doctor`. The render function lives in `ui/banner.py` and is opt-in everywhere else.

### 5. `doctor --json` + colored render

`corpus_forge/doctor/checks.py:41–47` `DoctorReport.render()` becomes two methods: `render_human(console)` (rich-formatted table with green/yellow/red status pills) and `to_json()` (already mostly there — checks return `CheckResult` dataclasses). `cli.py:258` adds `--json` flag; JSON path skips banner.

The JSON shape is exactly what `bug-report` serializes, so this is the same code path.

### 6. `estimate` upgrades

`corpus_forge/estimate.py:363–428` `_walk_directory` (iterative stack walk):
- Wrap in `time.perf_counter()` around the scan.
- Wrap the inner loop in `ui.progress.make_progress("Scanning", total=None)` — unbounded progress, ticks `files_seen` and shows rate (`files/s`).
- Return a richer `ScanResult` namedtuple that includes `elapsed_s` and `scan_rate`.

`cli.py:1757` `estimate` command output gets two new sections rendered as `rich.table.Table`:
1. **Scan stats**: wall-clock time, files/s, dirs visited
2. **Pending files**: query `backend.chunks_missing_embedding(active_embedder_id)` (already exists at `corpus_forge/backends/postgres.py:776`) plus an analogous documents-not-yet-chunked check. Count + a sample of 5 file paths.

The existing Postgres-footprint section stays; just rendered as a `rich.table.Table` with right-aligned byte columns and color-coded growth.

### 7. Progress bars on long ops

| Command | Where to wrap | Total source |
|---|---|---|
| `ingest --once` | `corpus_forge/ingest.py` main loop (after `cli.py:94`) | source roots' planned-file count from a quick prepass (cap at 10k) or unbounded |
| `embed` | `corpus_forge/embed.py:73-117` `backfill_embedder` main loop | `backend.count_chunks_missing_embedding(embedder_id)` |
| `sync pull --once` / `sync push` | `corpus_forge/sync/pull.py` and `push.py` | pending document count |
| `estimate` | (covered in §6) | unbounded |

All use the single `ui.progress.make_progress` factory. Daemon mode (`corpus-forge daemon`) does **not** show bars — daemon stays log-only — but every state transition gets a `logger.info(...)` so `corpus-forge logs tail` is informative.

### 8. Embedder-fingerprint detection — `corpus_forge/embedders/fingerprint.py`

New module:
- `embedder_fingerprint(cfg: EmbedderConfig) -> str` — stable hash of `(provider, model_id, dimension, normalize, distance)`.
- `compare_active(config: Config, backend) -> EmbedderDrift | None` — for each `EmbedderConfig` marked `active=True`, look up the row in `corpus.embedders` (already keyed by name per `alembic/versions/0001_core.py:143-156`), recompute fingerprint, return drift info: `EmbedderDrift(name, was_model_id, now_model_id, chunks_to_rerun, est_seconds)`.

The DB already stores embedder config as JSONB (`embedders.config`) per the audit, so no schema change. We just compare config-hash to stored-config-hash.

**Where the check fires:**
- End of `setup` wizard (any mode) — prints the panel, prompts.
- Start of `ingest` and `embed` commands — same panel, same prompt.
- Daemon startup (`daemon.py:14`) — logs a `WARNING` but does **not** auto-run (daemon already mid-task).

**Prompt UX** (panel rendered via `ui.banner` helpers):
```
┌─ Embedder changed ───────────────────────────┐
│ Was:  qwen3:8b (1024-dim)                    │
│ Now:  bge-m3 (1024-dim)                      │
│ 12,481 chunks need re-embedding (~7 min)     │
└──────────────────────────────────────────────┘
? Rerun now in background, later, or skip? [Now/later/skip]
```
- **Now** → spawn `corpus-forge embed -e <name>` as a detached subprocess. The subprocess calls `init_logging('embed-worker', ...)` so its output lands in `embed-worker.log` (separate from the parent `cli.log`). stdout/stderr of the subprocess are redirected to `/dev/null` because the file handler is the durable record; the foreground stays quiet. Print `→ Running in background — watch with: corpus-forge logs tail --component embed-worker --follow`. A pid file at `~/.cache/corpus-forge/state/embed-worker.pid` lets `sync status` and `doctor` report "rerun in progress."
- **Later** → write a marker file `~/.cache/corpus-forge/state/pending_rerun.json` listing the affected embedder. Next foreground command sees the marker, reprompts.
- **Skip** → suppress the prompt for that fingerprint pair for 7 days (record in same state file).

`corpus-forge sync status` (`cli.py:370`) gets a new row showing whether a background rerun is in progress (by reading the log tail + presence of the pid file).

### 9. `corpus-forge bug-report` — `corpus_forge/diagnostics/bug_report.py`

New top-level command. Single goal: produce a single `.zip` a user (or an issue-triaging agent) can read top-to-bottom to reproduce/diagnose without back-and-forth.

**Bundle contents** (everything redacted before zipping):
- `manifest.json` — corpus-forge version, OS, Python version, architecture, time, hostname (hashed, not literal), tool install path (`uv tool list` lookup), redaction log.
- `doctor.json` — `DoctorReport.to_json()` output (reuses §5).
- `config.redacted.toml` — `config.toml` with all `*_api_key`, `dsn`, `base_url` paths under `*_api_key_env`, and `password=…` substrings replaced by `«redacted»`. The redactor lives in `corpus_forge/diagnostics/redact.py` (new) and is unit-testable.
- `logs/cli.log.txt`, `logs/daemon.log.txt`, `logs/mcp.log.txt`, `logs/embed-worker.log.txt` — last 2 MB of each that exists (tail), with the redactor run over them. Filenames preserve the component prefix so the agent reading the zip knows which surface produced which line.
- `logs/recent_events.txt` — flushed contents of the in-memory ring buffer (§2). Captures the last 200 INFO+ events even if the file logs rotated.
- `env.txt` — `os.environ` filtered to `CF_*` and `OLLAMA_*` keys, values redacted.
- `deps.txt` — `pip list --format=freeze` (best-effort; falls back to importlib.metadata).
- `db_summary.json` — counts only (datasets, documents, chunks, embedders by name+dim), no row content.
- `recent_events.txt` — last 50 `INFO+` log lines from each rotating log, decorated with component prefix.
- `README.txt` — top-of-zip greeting that tells the recipient (human or agent) "Start with `manifest.json`, then `doctor.json`, then `recent_events.txt`. Logs are at `logs/`."

**CLI surface**:
```
corpus-forge bug-report [--out <path>] [--no-logs] [--no-db] [--no-zip]
```
- `--out` defaults to `./corpus-forge-bugreport-<ISO date>-<short-hash>.zip` in the CWD (NOT `/tmp`, so it's near the project).
- `--no-logs` / `--no-db` for users worried about even redacted leakage.
- `--no-zip` writes the staging directory uncompressed (handy when an agent will read files individually).
- After writing, prints:
  ```
  ✓ Wrote corpus-forge-bugreport-2026-05-17-a3f9.zip (412 KB)
  ✓ 7 secrets redacted

  Attach this file to a new issue at:
    https://github.com/ulmentflam/corpus-forge/issues/new?template=bug.yml&title=…
  ```
  The URL pre-fills the title with `[bug-report a3f9]` so we can correlate without exposing PII.

**Why this matters for the user's ask** ("easy for someone to open up an issue with the log and for an agent or a person to debug"): one command, redacted-by-default, the zip is the single source of truth, and the issue template (also added: `.github/ISSUE_TEMPLATE/bug.yml`) explicitly asks the user to attach the `corpus-forge-bugreport-*.zip` and nothing else. An agent picking up the issue can `unzip` and start at `README.txt`.

### 10. CRUD admin commands — `corpus_forge/admin/`

A consistent **noun-verb** layer (kubectl / gh style) for inspecting and editing a deployment without hand-editing `config.toml` or remembering which env var controls what. Six command groups, each with a small, predictable verb set.

#### Project-wide convention: "stay attached, unless `--background`"

Every command that triggers a long-running side effect (rerunning embeddings, restarting the daemon, pulling an Ollama model, ingesting after a config change) defaults to **foreground**: the CLI stays attached, renders the live progress bar and log tail, forwards SIGINT/SIGTERM to the child, and exits with the child's exit code. Adding `--background` (and `-b`) detaches the child via `subprocess.Popen(stdin=DEVNULL, start_new_session=True)`, writes a pid file under `~/.cache/corpus-forge/state/`, and returns immediately with `✓ <op> running (pid=N). Watch: corpus-forge logs tail --component <c> --follow`.

The foreground wrapper lives in `corpus_forge/admin/foreground.py` as `run_attached(argv, *, component, on_progress=None) -> int` and is reused by `service start`, `embedder set-active`, `ollama pull`, and the embedder-drift rerun in §8 (which today already detaches — it now respects the same flag so the user can pick).

#### `config` — generic key/value access into `config.toml`

| Verb | Synopsis | Notes |
|---|---|---|
| `get` | `corpus-forge config get <dotted.key>` | Print scalar/JSON. Supports `embedders[0].model_id`, `datasets.default.sources[0].plugin`. |
| `set` | `corpus-forge config set <key> <value>` | Parses value into the target type (Pydantic field info). Round-trips through `Config.load()` to validate before writing. Fails if invalid (atomic temp-file swap). |
| `unset` | `corpus-forge config unset <key>` | Resets to Pydantic default or removes optional field. |
| `show` | `corpus-forge config show [--diff] [--secrets]` | Default redacts secrets; `--secrets` requires `--yes`. `--diff` shows delta from defaults. |
| `path` | `corpus-forge config path` | Prints absolute path (XDG / `%APPDATA%`). |
| `validate` | `corpus-forge config validate [--file <path>]` | Round-trips through `Config.load()` without writing. |
| `edit` | `corpus-forge config edit` | Opens `$EDITOR` on the config; validates on save; rolls back if invalid. |

Side-effect rule: `config set` on an embedder field, an `ollama.base_url`, or a source root prompts "Apply now — restart daemon / rerun embed?" (defaults Yes). Foreground unless `--background`.

Implementation: `corpus_forge/admin/config.py` uses `tomlkit` (preserves comments/order) plus a dotted-path resolver built on Pydantic's `model_fields` introspection so the type coercion is centralized.

#### `embedder` — manage configured embedders

| Verb | Synopsis | Notes |
|---|---|---|
| `list` | `corpus-forge embedder list` | Table: name, provider, model_id, dim, active, fingerprint (last 8 chars), chunk coverage. |
| `get <name>` | full record + DB fingerprint match + last-used timestamp from `embed-worker.log` | |
| `add <name>` | wizard (reuses `setup` prompt module) | |
| `remove <name>` | confirm prompt; flags: `--drop-vectors` (default off — keeps `embeddings_<name>` table) | |
| `set-active <name>` | toggles `active=true`, others to `false` | Triggers the §8 fingerprint flow. Foreground unless `--background`. |
| `test <name>` | runs a sample embedding round-trip (model load + 1-batch + dim check + cosine self-similarity) and reports timing | |

#### `ollama` — local model endpoint helpers

The user explicitly called out "local models/urls." Ollama is the dominant local provider; for OpenAI / sentence-transformers users `config set` is the escape hatch.

| Verb | Synopsis | Notes |
|---|---|---|
| `list` | `corpus-forge ollama list` | Hits `GET <base_url>/api/tags`; prints name, size, modified, family. |
| `get <model>` | `GET /api/show` for a single model | |
| `pull <model>` | `POST /api/pull` with streaming JSON; renders a `ui.progress` bar driven by the stream's `completed`/`total` fields | Foreground unless `--background`. |
| `set-url <url>` | shortcut for `config set ollama.base_url <url>` | Reprobes immediately; warns if unreachable. |
| `test` | embeds "hello world" via the configured Ollama embedder | Reports latency + dim. |

#### `dataset` — datasets configured in `config.toml`

| Verb | Synopsis |
|---|---|
| `list` | name, kind, source count, document count, last-sync timestamp |
| `get <name>` | full record |
| `add <name>` | wizard |
| `remove <name>` | confirm; `--drop-vectors` like embedder |

#### `source` — sources nested under a dataset

All require `-d <dataset>`.

| Verb | Synopsis |
|---|---|
| `list -d <dataset>` | plugin, chunker, root path, last scan |
| `add -d <dataset>` | wizard (filesystem / markdown_vault / claude_code / opencode / etc.) |
| `remove -d <dataset> <name>` | confirm |

Adding a new source prompts "Ingest now?" (defaults Yes). Foreground unless `--background`.

#### `service` — daemon process & system integration

This replaces the bare `corpus-forge daemon` (`cli.py:135`) with a real lifecycle group, while keeping `daemon` as a deprecated alias for one release.

| Verb | Synopsis | Notes |
|---|---|---|
| `status` | Pid alive? Where's the log? Last INFO line? Uptime? Memory (psutil)? Active datasets? | Reads `~/.cache/corpus-forge/state/daemon.pid` + tails `daemon.log`. Always exits 0; status is in the body. |
| `start` | `corpus-forge service start [--background]` | Foreground default: child process + live log tail + SIGINT forwarding. `--background`: detach, write pid file, return. |
| `stop` | sends SIGTERM, waits up to 30s, escalates to SIGKILL | |
| `restart` | stop + start (preserves foreground/background mode) | |
| `logs` | alias for `corpus-forge logs tail --component daemon` | |
| `install` | `corpus-forge service install [--systemd|--launchd|--auto]` | Generates and (with `--apply`) installs a systemd `.service` unit on Linux or launchd `.plist` on macOS. Prints the unit content first; `--apply` writes to `~/.config/systemd/user/corpus-forge.service` or `~/Library/LaunchAgents/com.corpus-forge.plist` and runs `systemctl --user enable --now` / `launchctl load`. Refuses to write a system-wide unit (requires sudo + explicit `--system` flag). |
| `uninstall` | reverse of `install` | |

Generated systemd unit (template at `corpus_forge/admin/templates/corpus-forge.service.j2`):
```ini
[Unit]
Description=corpus-forge daemon
After=network-online.target

[Service]
ExecStart=%h/.local/bin/corpus-forge service start
Restart=on-failure
RestartSec=5
Environment=CF_CONFIG=%h/.config/corpus-forge/config.toml

[Install]
WantedBy=default.target
```

`status` and `install`/`uninstall` are platform-aware: on Windows, `install` falls back to a Scheduled Task (`schtasks /create`) and `status` reads the Windows Service Manager. Same JSON output shape for all three platforms so scripts are portable.

#### Implementation layout

```
corpus_forge/admin/
├── __init__.py
├── foreground.py          # run_attached() wrapper, pid-file helpers
├── config.py              # tomlkit-based CRUD + dotted-path resolver
├── embedder.py            # wraps backends/{postgres,sqlite}.py embedder ops
├── ollama.py              # HTTP client + streamed pull
├── dataset.py
├── source.py
├── service.py             # daemon lifecycle, pid file, signal forwarding
├── service_install.py     # systemd/launchd/schtasks unit generators
└── templates/
    ├── corpus-forge.service.j2
    └── com.corpus-forge.plist.j2
```

#### Tests

Each verb gets a unit test with a tmp config and tmp pid dir:
- `tests/admin/test_config_crud.py` — dotted-path set/get round-trip; invalid value rolls back; secrets redacted in `show` by default.
- `tests/admin/test_embedder_crud.py` — `set-active` flips the active flag and emits the drift event; `remove --drop-vectors` truncates the per-embedder table.
- `tests/admin/test_ollama.py` — list/test/pull against a `responses`-mocked Ollama; pull progress increments.
- `tests/admin/test_service.py` — start (background) writes pid; stop terminates; status reads last log line; install writes the right unit file content for each platform.
- `tests/admin/test_foreground.py` — `run_attached` forwards SIGINT, returns child exit code; `--background` returns immediately and creates a pid file.

#### Cross-cuts back to other waves

- The new flags `--background` / `--quiet` are added globally at `cli.py:34` so any long-op command picks them up (not just the admin group).
- `service status`, `embedder list`, and `config show` are also surfaced in `bug-report` (§9) as `service_status.txt`, `embedders.txt`, `config.redacted.toml` respectively so triage has the same view the user would see locally.
- The daemon-drift detection (§8) now uses `service status` under the hood and shares the pid-file/state-dir conventions.

### 12. Agent mode — `corpus_forge/ui/agent.py`

Agents calling the CLI (Claude Code, OpenCode, Gemini Code Assist, custom Anthropic-API agents) pay per-token for everything we print. Banners, progress chrome, colored log lines, and chatty INFO summaries are pure cost. Agent mode is the switch that says "I'm an agent — give me terse, structured, parseable output and nothing else."

#### Detection precedence

`is_agent_mode()` returns an `AgentClient` enum value:
```python
class AgentClient(StrEnum):
    CLAUDE_CODE = "claude-code"
    OPENCODE    = "opencode"
    GEMINI_CLI  = "gemini-cli"
    COPILOT_CLI = "copilot-cli"
    CODEX       = "codex"           # OpenAI Codex CLI
    AMP         = "amp"             # Sourcegraph Amp
    AI_GENERIC  = "ai-generic"      # AI_AGENT=<unknown> set by some other agent
    GENERIC     = "generic"         # CI / piped-non-interactive heuristic
    HUMAN       = "human"
```

The detection logic mirrors **the canonical implementation in `cli/cli`'s `internal/agents/detect.go`** ([source](https://github.com/cli/cli/blob/trunk/internal/agents/detect.go)), which is the reference list every coding-agent ecosystem now coordinates on. Reproducing it is intentional — when a new agent ships, we update the same way GitHub CLI updates and stay compatible. Order:

1. Explicit `--agent <type>` global flag (`--agent off` forces human mode even under env signals).
2. `CF_AGENT` env var (any `AgentClient` value).
3. **`AI_AGENT=<name>`** — the *generic convention*. Checked first because it's the most specific signal. Validated against `^[a-zA-Z0-9_-]+$`. The value's `<prefix>` (before the first `_`) is matched against the enum names (`claude-code`, `opencode`, `gemini-cli`, `copilot-cli`, `codex`, `amp`). Unknown names → `AI_GENERIC`. (Claude Code today sets `AI_AGENT=claude-code_<version>_agent`, verified live in `claude` 2.1.133.)
4. **`AGENT=amp`** — Sourcegraph Amp. Checked **before** Claude Code because Amp also sets `CLAUDECODE=1`.
5. **OpenAI Codex** — any of `CODEX_SANDBOX`, `CODEX_CI`, `CODEX_THREAD_ID` ([codex-rs/core/src/spawn.rs](https://github.com/openai/codex/blob/main/codex-rs/core/src/spawn.rs)).
6. **Google Gemini CLI** — `GEMINI_CLI` ([gemini-cli/docs/tools/shell.md](https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/shell.md#L96-L97), confirmed set during shell tool invocations).
7. **GitHub Copilot CLI** — `COPILOT_CLI` (no first-party docs; `cli/cli` is the reference).
8. **OpenCode** — `OPENCODE` ([packages/opencode/src/index.ts:78-80](https://github.com/anomalyco/opencode/blob/main/packages/opencode/src/index.ts#L78-L80)).
9. **Anthropic Claude Code** — `CLAUDECODE=1`. Checked **last** in the agent block (Amp sets it too; we want Amp to take precedence). Confirmed live: `CLAUDECODE=1`, `CLAUDE_CODE_ENTRYPOINT=cli`, `CLAUDE_CODE_EXECPATH`, `CLAUDE_CODE_SESSION_ID`. We rely only on the documented `CLAUDECODE` flag — the others stay as breadcrumbs in the bug-report but we don't gate behavior on them.
10. MCP carve-out: if launched via `corpus-forge mcp serve --transport stdio`, agent mode is already implicit because JSON-RPC owns the wire. We never write loose stdout in this mode regardless of the flag.
11. Heuristic fallback: stdin not-a-tty AND stdout not-a-tty AND `CI=true` → `GENERIC`. (Plain piping like `corpus-forge search "q" | grep` does **not** trigger — it requires the CI signal too, because pipe-to-grep is a human pattern we mustn't break.)
12. Otherwise: `HUMAN`.

The detected client is logged once at INFO (`corpus_forge.cli: agent_mode=claude-code (signal=CLAUDECODE=1)`) so bug-reports show which path the detector took. The detection module exposes a `Detection(name, signal, raw_value)` namedtuple so the log line and the `bug-report` `manifest.json` can both record `"signal":"AI_AGENT"`, `"raw":"claude-code_2.1.133_agent"`.

#### Coverage commitment

Agent mode is on for **every command**. The plumbing is in three load-bearing modules (`ui/console.py`, `ui/progress.py`, `ui/prompts.py`) plus `logging_config.py`'s handler swap — call sites do not branch. A test (`tests/cli/test_agent_mode_smoke.py`) iterates the full Typer app registry, invokes each non-destructive command with `CF_AGENT=generic`, and asserts: (a) zero ANSI bytes on stdout, (b) every emitted line parses as JSON, (c) exactly one `{"event":"command.start",...}` and one `{"event":"result"|"error",...}` per invocation. New commands cannot accidentally skip the contract — the smoke test parametrizes over `app.registered_commands`.

#### What changes when agent mode is active

| Surface | Human mode | Agent mode |
|---|---|---|
| Banner (`ui/banner.py`) | rounded box, ember border | suppressed |
| Progress bar (`ui/progress.py`) | live `rich.progress` with bar + rate + ETA | replaced by sparse JSONL `{"event":"progress",...}` events (every 25 % or every 10 s, whichever is rarer; never per-item) |
| Log → stderr (`logging_config.py`) | `RichHandler` colored INFO+ | suppressed by default. Optional `--agent-logs warning` raises threshold for stderr JSONL log events; rotating file unchanged. |
| Status lines (`ui.ok/warn/error/info`) | colored prefixed lines | each becomes a JSONL `{"event":"status","level":"ok|warn|error|info","msg":...}` |
| Prompts (`ui/prompts.py`) | interactive | **hard fail** with `{"event":"error","kind":"requires_interactive","msg":...}` and exit 2. Agents must pass values via flags or env. |
| Tables (`config show`, `embedder list`, `doctor` human render) | rich `Table` | JSON document, single emission |
| Command result | scattered echoes | one terminal `{"event":"result","cmd":"<name>","status":"ok|error","data":{...}}` per command |
| Color / glyph fallback | by terminal | always plain — JSONL never carries ANSI |
| Exit codes | already standard | unchanged; agents rely on these so we keep them stable: `0` ok, `2` invalid input, `3` config error, `4` backend error, `5` agent-interactive-required, `64`+ command-specific |

#### Event schema (JSONL, one event per line on stdout)

```jsonl
{"event":"command.start","ts":"2026-05-17T14:22:01.482Z","cmd":"embed","args":{"embedder":"qwen3_8b","dataset":"default","limit":null},"version":"0.1.0b3","agent":"claude-code"}
{"event":"status","ts":"...","level":"info","msg":"Loading embedder qwen3_8b"}
{"event":"progress","ts":"...","op":"embed","done":3120,"total":12481,"rate_per_s":74.2,"eta_s":126,"pct":0.25}
{"event":"status","ts":"...","level":"warn","msg":"Embedder drift: qwen3_8b -> bge-m3","data":{"affected":12481}}
{"event":"result","ts":"...","cmd":"embed","status":"ok","data":{"embedded":12481,"skipped":0,"elapsed_s":167.4,"embedder":"qwen3_8b"}}
```

Schema documented at `docs/agent-mode.md` with TypeScript types so MCP clients and external agents can validate. Versioned via the top-level `"event"` discriminator — adding fields is non-breaking; renaming requires `"event":"command.start.v2"`.

Per-command result payloads:
- `search` → `{"hits":[{"chunk_id":...,"score":...,"text":...,"doc":...}]}` (terse — no surrounding prose)
- `estimate` → `{"files":N,"bytes":N,"scan_elapsed_s":N,"pending_chunks":N,"postgres_estimate":{...}}`
- `doctor` → `{"checks":[{"name":...,"status":"ok|warn|fail|skip","detail":...}],"summary":"ok|warn|fail"}` (same JSON as `--json` human-explicit path)
- `config get` → `{"key":...,"value":...,"type":...}`
- `config show` → `{"config":{...},"redacted_keys":[...]}`
- `embedder list` → `{"embedders":[{...}]}`
- `bug-report` → `{"zip":"/abs/path.zip","bytes":N,"redacted_count":N,"issue_url":"..."}`

#### How agents discover what's available

`corpus-forge --agent <type> capabilities` (new sub-verb) emits a one-shot JSON document listing every command, its flags, its result schema, and the agent-mode contract. Agents call this once at startup to learn what corpus-forge can do — equivalent to the MCP `list_tools` call but for the CLI surface.

#### Token economics — why this matters

Human mode for `corpus-forge embed -e qwen3_8b` over a 12k-chunk corpus prints roughly: banner (8 lines) + ~3 INFO lines + a continuously-updating progress bar (re-rendered ~10×/sec, ANSI-erased and replaced) + a final summary (4 lines). In a Claude Code transcript this materializes as **hundreds of tokens of progress chrome** the agent has to parse and (mostly) discard.

Agent mode for the same op: ~5 JSONL events (start, 4 progress milestones at 25/50/75/100, result). Roughly **~120 tokens total**, all directly parseable.

For an agent that calls corpus-forge ~10 times per session (search + curate loop), that's the difference between ~50 KB of transcript noise and a clean ~6 KB of structured events.

#### Implementation

```
corpus_forge/ui/agent.py
├── AgentClient (enum)
├── detect() -> AgentClient
├── is_agent_mode() -> bool
├── emit(event_type: str, **fields) -> None   # writes one JSONL line to stdout, flushes
├── result(cmd: str, *, status: str, data: dict | None = None) -> int   # convenience
├── error(cmd: str, *, kind: str, msg: str, exit_code: int = 1) -> int
└── progress_emitter(op: str, total: int | None) -> ProgressEmitter   # context-manager replacement for rich.progress
```

`ui.console.print/ok/warn/error/info` all branch on `is_agent_mode()` and either render via Rich or call `agent.emit("status", ...)`. Call sites stay unchanged — one helper handles both modes. Same with `ui.progress.make_progress()` — it returns a Rich `Progress` OR an `agent.ProgressEmitter` with the same context-manager interface.

`logging_config.py` adds a third handler shape: when agent mode is on, the stderr handler is swapped for `AgentLogHandler` that emits log records as `{"event":"log",...}` on stdout, gated by `--agent-logs` level (default: WARNING — so a quiet run is truly quiet).

#### Tests

- `tests/ui/test_agent_detection.py` — table-driven over every signal the canonical list covers: `AI_AGENT=claude-code_*`, `AI_AGENT=opencode_*`, `AI_AGENT=<unknown>` → `AI_GENERIC`, `AGENT=amp` precedence over `CLAUDECODE=1`, `CODEX_SANDBOX`, `CODEX_CI`, `CODEX_THREAD_ID`, `GEMINI_CLI`, `COPILOT_CLI`, `OPENCODE`, plain `CLAUDECODE=1`. Explicit `--agent` flag overrides any env signal. MCP stdio carve-out always agent-mode regardless of flag.
- `tests/ui/test_agent_emission.py` — `emit()` produces valid JSONL one line per call, ts is UTC ISO 8601 with ms, no embedded newlines.
- `tests/cli/test_agent_mode_smoke.py` — runs `embed`, `search`, `doctor`, `estimate`, `config get`, `config show`, `embedder list`, `bug-report` under `CF_AGENT=generic` and asserts: zero ANSI bytes on stdout, every line parses as JSON, the final line is `{"event":"result",...}`.
- `tests/cli/test_agent_prompts.py` — interactive prompts under agent mode return exit 2 with `kind:"requires_interactive"`.

#### Cross-cuts back to other waves

- `bug-report` (§9) auto-enables agent mode if it detects it's being run from one — the resulting bundle includes a `mode.txt` so a triaging agent knows which surface produced the logs.
- The `--json` flag I added to `doctor` (§5) becomes equivalent to `--agent generic` scoped to that one command. We keep the flag for human-explicit use (`doctor --json | jq`).
- MCP server (`mcp serve`) already speaks JSON-RPC; agent mode here is a no-op (the protocol is the structure).

### 11. `corpus-forge logs` subcommand — `corpus_forge/diagnostics/logs.py`

Small sibling of `bug-report`:
- `corpus-forge logs path` → prints `~/.cache/corpus-forge/logs/`.
- `corpus-forge logs tail [--follow] [--component daemon|cli|mcp] [-n 200]` → like `tail -f` but theme-aware (colors levels). Uses `console.print` so it inherits `NO_COLOR`.
- `corpus-forge logs clear` → with confirm prompt; rotates current and truncates.

Daemon log is the canonical "is it doing anything?" surface, so this gets featured prominently in `doctor` output ("Last daemon activity: 12 s ago — embedding (batch 7/40)").

---

## Critical files to modify

| File | Change |
|---|---|
| `pyproject.toml` | Promote `rich` to direct dep; add `platformdirs` (already a Pydantic transitive but make direct) |
| `corpus_forge/cli.py:1968` | Wire `init_logging` in global callback; add `--verbose`/`--quiet`/`--no-color` flags; rewrite ~99 `typer.echo` calls; register `bug-report`, `logs` subcommands; route prompts through `ui.prompts` |
| `corpus_forge/cli.py:156` (`setup`) | Add `--quick`; render banner |
| `corpus_forge/cli.py:258` (`doctor`) | Add `--json`; banner; rich render |
| `corpus_forge/cli.py:1757` (`estimate`) | Wall-clock time, pending files, progress on scan |
| `corpus_forge/setup/wizard.py` | `quick=True` path; use `ui.prompts` |
| `corpus_forge/doctor/checks.py:41` | Split into `render_human` + `to_json` |
| `corpus_forge/estimate.py:363` | Wrap walk in `time.perf_counter()` + progress; return `ScanResult` |
| `corpus_forge/ingest.py:619` | Remove `logging.basicConfig`; let `init_logging` own it; add progress wrapping |
| `corpus_forge/embed.py:73` | Progress wrap; pre-flight fingerprint check |
| `corpus_forge/sync/pull.py`, `push.py` | Progress on `--once` mode (daemon mode stays log-only) |
| `corpus_forge/daemon.py:14` | Banner-once; fingerprint warn; `init_logging('daemon', …)` |

## New files

```
corpus_forge/ui/__init__.py            # re-exports: console, ok, warn, error, info, title, panel, progress
corpus_forge/ui/theme.py
corpus_forge/ui/console.py
corpus_forge/ui/banner.py
corpus_forge/ui/progress.py
corpus_forge/ui/prompts.py
corpus_forge/logging_config.py
corpus_forge/embedders/fingerprint.py
corpus_forge/diagnostics/__init__.py
corpus_forge/diagnostics/bug_report.py
corpus_forge/diagnostics/redact.py
corpus_forge/diagnostics/logs.py
corpus_forge/admin/__init__.py
corpus_forge/admin/foreground.py       # run_attached(); pid-file helpers; -b flag plumbing
corpus_forge/admin/config.py           # tomlkit + dotted-path CRUD
corpus_forge/admin/embedder.py
corpus_forge/admin/ollama.py
corpus_forge/admin/dataset.py
corpus_forge/admin/source.py
corpus_forge/admin/service.py          # daemon lifecycle: start/stop/restart/status
corpus_forge/admin/service_install.py  # systemd / launchd / schtasks unit generators
corpus_forge/admin/templates/corpus-forge.service.j2
corpus_forge/admin/templates/com.corpus-forge.plist.j2
corpus_forge/ui/agent.py               # agent detection + JSONL emit + ProgressEmitter
docs/agent-mode.md                     # schema spec for the JSONL contract
.github/ISSUE_TEMPLATE/bug.yml         # asks for the bug-report zip attachment
```

## Tests (TDD per project convention, `.planning/tdd/`)

Per `MEMORY.md`, this project follows the TDD workflow with RED→GREEN→wave-gate phases. Test files mirror new modules under `tests/`:
- `tests/ui/test_theme.py` — palette resolves; no-color fallback substitutes `[OK]`/`[WARN]`/`[ERR]`.
- `tests/ui/test_progress.py` — factory returns the expected columns; rate column kicks in for unbounded.
- `tests/ui/test_prompts.py` — quick wizard path round-trips a config; full wizard unchanged.
- `tests/test_logging_config.py` — rotating file at expected path; redactor leaves no `dsn=` substrings; `--verbose` widens stderr level.
- `tests/embedders/test_fingerprint.py` — identical configs → equal fingerprint; changing `dimension` flips it; drift detection counts correct chunks against a sqlite fixture.
- `tests/diagnostics/test_redact.py` — covers DSN, API keys, base_urls, embedded `password=` in log lines; round-trip safety (idempotent).
- `tests/diagnostics/test_bug_report.py` — produces valid zip; `manifest.json` matches schema; all known secret patterns redacted; `--no-zip` writes directory.
- `tests/diagnostics/test_logs.py` — `path` prints platformdirs path; `tail` reads from rotating file; `clear` requires confirm.
- `tests/test_estimate.py` — wall-clock field present; pending-files count matches a synthetic backend with N missing chunks.
- `tests/cli/test_setup_quick.py` — `--quick` non-interactive (via `CF_NON_INTERACTIVE=1`) writes minimal viable config; default sqlite, default Ollama URL.
- `tests/cli/test_doctor_json.py` — `--json` emits parseable JSON matching `DoctorReport.to_json()`.

## Sequencing (suggested wave structure)

1. **Wave 1 — Foundation** (no behavior change): add `rich` to deps, write `ui/` package + `logging_config.py`, route global flags. Tests for `ui/` and logging.
2. **Wave 2 — Output retrofit**: rewrite `cli.py` echo sites to use `ui.*`. Per-command commits. No new commands.
3. **Wave 3 — Setup/doctor polish**: `--quick`, `--json`, banner.
4. **Wave 4 — Estimate + progress**: wall-clock + pending + progress bars on all long ops.
5. **Wave 5 — Embedder fingerprint**: detection, prompt, background rerun.
6. **Wave 6 — Diagnostics**: `bug-report`, `logs`, redactor, GitHub issue template.
7. **Wave 7 — Admin CRUD**: `config`, `embedder`, `ollama`, `dataset`, `source` groups + `run_attached` foreground wrapper.
8. **Wave 8 — Service lifecycle**: `service` group, systemd / launchd / schtasks unit generators, deprecate bare `daemon` command in favor of `service start`.
9. **Wave 9 — Agent mode**: `ui/agent.py` detection + emission, route `ui.console.*` / `ui.progress` / `logging_config` through the agent-mode branch, JSONL schema doc, `capabilities` subcommand, agent-mode smoke tests across every command.

Each wave is independently shippable. Wave 1 unblocks all others; Waves 2–9 can be reordered (with the small exceptions that Wave 7's `--background` global flag should be wired before Wave 8 reuses it, and Wave 9's agent-mode JSONL contract is easier to design once the human-mode UI has stabilized — Waves 2–6 first, then 7/8/9 in any order).

---

## Verification

End-to-end manual smoke (run after Wave 6 lands):

```bash
# 1. Banner + quick setup happy path
CF_NON_INTERACTIVE=1 corpus-forge setup --quick --backend sqlite
# Expect: boxed banner, ✓ at each step, exits 0, writes ~/.config/corpus-forge/config.toml

# 2. Doctor (both modes)
corpus-forge doctor
corpus-forge doctor --json | jq .checks[0]
# Expect: human form is colored table; JSON validates and lists all probes

# 3. Estimate with progress + new sections
corpus-forge estimate ~/Notes
# Expect: live progress while scanning, then table with "Scan: 1234 files in 3.2s (385 f/s)"
# and "Pending: 81 files queued, 0 chunks missing embeddings"

# 4. Long-op progress visible
corpus-forge ingest --once
corpus-forge embed -e qwen3_8b
# Expect: live bar with M of N + rate + ETA; ✓ summary at end

# 5. Embedder fingerprint flow
# Edit config to swap model_id to bge-m3, then:
corpus-forge ingest --once
# Expect: drift panel + 3-way prompt; "Now" backgrounds the rerun;
# corpus-forge logs tail --follow shows the worker

# 6. Bug-report end-to-end
corpus-forge bug-report
# Expect: zip in CWD, prints GitHub URL, prints redaction count.
# Open the zip — confirm no DSN/API-key strings present (grep -r '«redacted»' shows hits, no raw secrets).

# 7. Log surfaces work — taxonomy is greppable
corpus-forge logs path
corpus-forge logs tail -n 20
corpus-forge logs tail --component daemon --follow &
ls ~/.cache/corpus-forge/logs/
# Expect: daemon.log, cli.log present; tail is colored; clear requires confirm.

# Verify the logger taxonomy actually fires:
grep "corpus_forge.embedders.loader" ~/.cache/corpus-forge/logs/cli.log     # model load events
grep "corpus_forge.ingest.scan"      ~/.cache/corpus-forge/logs/daemon.log  # dir-scan events
grep "corpus_forge.embedders.batch"  ~/.cache/corpus-forge/logs/cli.log     # embedding progress milestones
# Each should have at least one INFO line per run.

# 8. Color fallback
NO_COLOR=1 corpus-forge doctor
corpus-forge --no-color doctor
# Expect: [OK]/[WARN] glyphs, no ANSI codes in piped output.

# 9. MCP/agent integration unaffected
corpus-forge mcp serve --transport stdio &
# Expect: still serves; banner suppressed for stdio transport (don't pollute MCP stream).

# 10. Admin CRUD round-trips
corpus-forge config get backend.kind
corpus-forge config set retrieval.alpha 0.6 && corpus-forge config get retrieval.alpha
corpus-forge config show --diff          # should show the alpha change
corpus-forge embedder list
corpus-forge embedder test qwen3_8b
corpus-forge ollama list
corpus-forge ollama set-url http://localhost:11434
corpus-forge dataset list

# 11. Foreground vs background side effects
corpus-forge service start                 # foreground: terminal stays attached, log tail visible, Ctrl+C exits cleanly
corpus-forge service start -b              # detaches; pid file written; prompt returns
corpus-forge service status                # reports pid, uptime, last log line, active datasets
corpus-forge service stop
# Same -b semantics on the side-effecting verbs:
corpus-forge embedder set-active bge-m3    # foreground rerun, attached progress bar
corpus-forge embedder set-active qwen3_8b -b   # detached rerun, returns immediately
corpus-forge ollama pull nomic-embed-text  # foreground streaming progress
corpus-forge ollama pull llama3.1 -b       # detached

# 12. Service install (Linux/macOS)
corpus-forge service install --auto        # prints the generated unit
corpus-forge service install --auto --apply  # writes + enables (user-scope only)
systemctl --user status corpus-forge       # Linux: should be active
corpus-forge service uninstall

# 13. Agent mode — structured output, no chrome
CF_AGENT=generic corpus-forge doctor | jq '.event,.cmd' | head
# Expect: every line parseable JSON; final event is "result"; zero ANSI codes:
CF_AGENT=generic corpus-forge embed -e qwen3_8b 2>/dev/null | grep -c $'\x1b\\['
# Expect: 0

# Auto-detect for each supported agent:
AI_AGENT=claude-code_2.1.133_agent corpus-forge search "x" | head -1 | jq .agent
# Expect: "claude-code"
CLAUDECODE=1 corpus-forge search "x" | head -1 | jq .agent           # claude-code
AGENT=amp CLAUDECODE=1 corpus-forge search "x" | head -1 | jq .agent  # amp (precedence)
GEMINI_CLI=1 corpus-forge search "x" | head -1 | jq .agent           # gemini-cli
OPENCODE=1 corpus-forge search "x" | head -1 | jq .agent             # opencode
COPILOT_CLI=1 corpus-forge search "x" | head -1 | jq .agent          # copilot-cli
CODEX_SANDBOX=1 corpus-forge search "x" | head -1 | jq .agent        # codex

# Capabilities self-description for agents:
corpus-forge --agent generic capabilities | jq '.commands | length'
# Expect: >= 20 (one per subcommand)

# Interactive prompts under agent mode hard-fail with kind:"requires_interactive":
CF_AGENT=generic corpus-forge setup 2>&1 | jq 'select(.event=="error")'
# Expect: {"event":"error","kind":"requires_interactive",...}; exit code 2.
```

Tests:
```bash
uv run pytest tests/ui tests/diagnostics tests/admin \
              tests/embedders/test_fingerprint.py \
              tests/test_logging_config.py tests/test_estimate.py \
              tests/cli/test_setup_quick.py tests/cli/test_doctor_json.py \
              tests/cli/test_agent_mode_smoke.py tests/cli/test_agent_prompts.py -v
```
