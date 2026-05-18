# TDD Task Board — Phase L / Wave 5 (Embedder-fingerprint detection)

_Owner: tdd-principal. Workers: read freely. Edit only your claimed row's
`status` and `claimed_by`._

Source plan: `/Users/evanowen/Library/Mobile Documents/com~apple~CloudDocs/Workspace/playground/corpus-forge/.planning/tdd/phase_l_cli_ux.md` §8 (Embedder-fingerprint detection).
Dispatch input: orchestrator brief, Phase L / Wave 5 kickoff after Wave 4 landed (`ebe4273`).

> Previous slice (Wave 4) summary archived in git history at `ebe4273`.

## Project gates
- lint: `uv run ruff check`
- format: `uv run ruff format --check`
- test (Wave 5 surface): `uv run python -m pytest tests/embedders tests/cli/test_drift_flow.py -x`
- regression: `uv run python -m pytest tests/unit tests/cli tests/embedders -x` (no new failures vs Wave 4 baseline)
- coverage-min: keep current baseline (no regression)

## Hard constraints (from dispatch + project)
1. **DO NOT COMMIT, DO NOT PUSH.** Workers stage only. Orchestrator commits.
2. **NO `typer.echo/secho/prompt/confirm`** outside `corpus_forge/ui/` — the
   `tests/cli/test_no_typer_echo.py` regression will fail you.
3. **`uv run python -m pytest`**, never bare `pytest`.
4. **Daemon mode does NOT prompt.** WARNING-level log only on detected drift.
5. `Prompt.ask` / `Confirm.ask` come from `corpus_forge.ui.prompts` (Wave 1);
   they're thin wrappers around `rich.prompt`.
6. `EmbedderConfig.distance` is part of the fingerprint tuple per the brief.
   Stored config JSONB today only carries `provider` + `model_id` (see
   `corpus_forge/backends/postgres.py:307` / sqlite mirror). On the FIRST
   run after Wave 5 lands, drift comparison must NOT explode on the
   legacy 2-key shape — when the stored config is missing any of the
   five fingerprint fields, fall back to the row's top-level columns
   (`provider`, `model_id`, `dimension`, `normalized`, `distance`) which
   the migration 0001 already provides.
7. The "WAS" fingerprint comes from the stored row; the "NOW" fingerprint
   comes from the live `EmbedderConfig`. Identical → no drift. Differing
   → drift.
8. Background subprocess MUST NOT inherit the parent's stdio:
   `subprocess.Popen(..., stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL, start_new_session=True)`.
   The detached worker calls `init_logging('embed-worker', ...)` so its
   logs land in `embed-worker.log`.
9. Marker file lives under
   `~/.cache/corpus-forge/state/pending_rerun.json` and the embed-worker
   pid under `~/.cache/corpus-forge/state/embed-worker.pid`. Use
   `platformdirs.user_cache_dir('corpus-forge')` so the path matches the
   Wave 1 logging convention. Atomic write via tempfile + rename.

## Decomposition notes (orchestrator)

- **Surface-disjoint matrix:**
  - W5-01 owns `corpus_forge/embedders/fingerprint.py` (NEW) — the
    `embedder_fingerprint`, `EmbedderDrift`, `compare_active`, and
    `save_active_fingerprint` API surface. No CLI / no setup wizard
    edits.
  - W5-02 owns `corpus_forge/embedders/_marker.py` (NEW) +
    `corpus_forge/embedders/drift_prompt.py` (NEW; the panel +
    `prompt_for_drift` helper). Pure module — no CLI plumbing.
  - W5-03 owns CLI hook points: `corpus_forge/cli.py` (the `ingest` +
    `embed` command bodies + `sync status` row), `corpus_forge/setup/wizard.py`
    (end-of-wizard hook), `corpus_forge/daemon.py` (WARNING log only).
    Depends on W5-01 + W5-02 landing first (consumer).

- **Wave shape:**
  - Wave A (3 parallel testers): RED for W5-01, W5-02, W5-03.
  - Wave B (parallel coders): GREEN for W5-01 + W5-02 (independent surface).
  - Wave C (sequential coder): GREEN for W5-03 (after W5-01 + W5-02).
  - Wave D (3 parallel QAs): verify each.

- **Embed-worker subprocess invocation.** The dispatch brief says:
  ```python
  subprocess.Popen(
      [sys.executable, "-m", "corpus_forge", "embed", "-e", "<name>"],
      stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
      stderr=subprocess.DEVNULL, start_new_session=True,
  )
  ```
  `python -m corpus_forge` works because `corpus_forge/__main__.py`
  re-exports the Typer app. The subprocess gets `embed` CLI args
  (`-e <name>` + optional `-d <dataset>`).

- **Pid-file liveness check** (sync status row): `os.kill(pid, 0)`
  returns without raising iff the process exists; `ProcessLookupError`
  → dead. Wrap in try/except so we degrade gracefully.

- **Marker JSON shape** (per brief):
  ```json
  {
    "<embedder_name>": {
      "state": "pending|skipped",
      "fp_was": "...",
      "fp_now": "...",
      "detected_at": "iso",
      "suppressed_until": "iso?"
    }
  }
  ```
  `mark_skipped` writes `suppressed_until = now + 7 days`. The check
  helper returns `"skipped"` while now < suppressed_until, else `"none"`
  (the suppression has expired — caller re-prompts).

- **Re-prompt sticky on "later".** The marker file is the bridge between
  invocations. Next foreground command's `compare_active` returns the
  same drift; the prompt UI reads the marker and either:
  - "skipped" + not expired → no prompt (skip silently)
  - "pending" → re-prompt (the user said "later")
  - "none" (no entry / expired) → fresh prompt

- **Fingerprint canonicalization.** The brief asks for stable hashing.
  Concatenate the five fields with a `|` separator after `.strip()` on
  each string field (`provider`, `model_id`, `distance`) and
  `repr(bool)` / `repr(int)` for the others. Hash with `hashlib.sha256`.
  Return a NamedTuple `Fingerprint(short, full)` where `short = full[:16]`.

- **`compare_active` signature.** Returns `list[EmbedderDrift]` (empty
  list, NEVER None, per brief). Iterates `[e for e in config.embedders
  if e.active]`. For each, looks up the stored row (via
  `backend.find_embedder_row_by_name(name)`); on miss (new embedder
  never registered), treats as "no drift" (the embedder hasn't been
  used yet — nothing to migrate). On hit, computes both fingerprints
  and compares.

- **Helper `backend.find_embedder_row_by_name(name)`.** Does NOT exist
  today. Add a thin one on both backends (postgres + sqlite) that runs
  `SELECT id, name, provider, model_id, dimension, normalized,
  distance, config FROM corpus.embedders WHERE name = %s` (postgres) /
  `... FROM embedders WHERE name = ?` (sqlite) and returns the row
  dict (or `None` on miss). Centralizes the lookup; W5-01 owns this
  helper because W5-01 is the only consumer.

- **chunks_to_rerun calculation.**
  - "chunks already embedded by the OLD fingerprint that need
    re-embedding" = `SELECT COUNT(*) FROM corpus.embeddings_<table_name>`
    (no missing-embedding gate; ALL existing rows must be
    re-embedded because the model changed). Use the row's
    `table_name` column to pick the right table.
  - PLUS `backend.count_chunks_missing_embedding(embedder_id)` (chunks
    that never got embedded by the old model either).
  - Sum the two.

- **`est_seconds` constant.** Use module-level
  `_DEFAULT_SECONDS_PER_CHUNK = 0.034` with env override
  `CF_REEMBED_SECONDS_PER_CHUNK`. Document the constant in the
  module docstring.

- **`prompt_for_drift` decision matrix.**
  | non_interactive | background | result |
  |---|---|---|
  | True | True | "now" |
  | True | False | "later" |
  | False | * | Render panel + `Prompt.ask(choices=["now", "later", "skip"], default="now")` |

- **Panel render.** Use `ui.panel(message, title="Embedder changed")` or
  the lower-level `Panel(..., border_style="brand.forge")` for one-off
  styling. The text body is multi-line; render each drift as a small
  block separated by a blank line. Example (single drift):
  ```
  Was:  qwen3_8b (1024-dim)   fp=abc123def4567890…
  Now:  bge_m3   (1024-dim)   fp=def456abc7890123…
  12,481 chunks need re-embedding (~7 min)
  ```
  The 7-min estimate is `est_seconds // 60` (integer minutes).

- **Setup wizard hook.** After `run_wizard` / `run_quick` /
  `run_non_interactive` returns in `setup()` (cli.py:280), if the
  freshly-written config can be loaded AND a backend is reachable, call
  `compare_active` and act on the result. Skip this entirely under
  `--non-interactive` unless `CF_BACKGROUND=1` is set — then run "now"
  background.

- **Pyrefly / mypy.** The `Literal["now", "later", "skip"]` return type
  on `prompt_for_drift` should use `typing.Literal`. The brief asks
  for a small `namedtuple` for the fingerprint — use
  `NamedTuple` from `typing` (subscriptable, type-stable).

## Tasks

| id | title | depends_on | surface | risk | status | claimed_by | notes |
|----|-------|------------|---------|------|--------|------------|-------|
| W5-01 | Fingerprint module + backend drift compare | — | `corpus_forge/embedders/fingerprint.py` (NEW), `corpus_forge/backends/postgres.py` (+find_embedder_row_by_name), `corpus_forge/backends/sqlite.py` (+find_embedder_row_by_name), `tests/embedders/test_fingerprint.py` (NEW), `tests/embedders/test_drift_detect.py` (NEW) | med | pending | — | — |
| W5-02 | Marker file + drift prompt helper | — | `corpus_forge/embedders/_marker.py` (NEW), `corpus_forge/embedders/drift_prompt.py` (NEW), `tests/embedders/test_marker.py` (NEW), `tests/embedders/test_drift_prompt.py` (NEW) | low | pending | — | — |
| W5-03 | CLI/setup/daemon hook points + sync status row | W5-01, W5-02 | `corpus_forge/cli.py` (ingest/embed/sync status), `corpus_forge/setup/wizard.py` OR `corpus_forge/cli.py:setup` (post-wizard hook), `corpus_forge/daemon.py` (WARNING log), `tests/cli/test_drift_flow.py` (NEW) | high | pending | — | — |

## Acceptance details

### W5-01 — Fingerprint module + backend drift compare

**`corpus_forge/embedders/fingerprint.py` (new):**

```python
"""Embedder-fingerprint detection (Phase L Wave 5).

Computes a stable hash over the five embedder identity fields
(``provider``, ``model_id``, ``dimension``, ``normalize``, ``distance``)
and compares it to the fingerprint stored in ``corpus.embedders.config``
to detect when the user has swapped a model and the existing embeddings
need a re-encode pass.

Public API:
- ``embedder_fingerprint(cfg: EmbedderConfig) -> Fingerprint``
- ``compare_active(config: Config, backend) -> list[EmbedderDrift]``
- ``save_active_fingerprint(config: Config, backend) -> None``

The per-chunk re-embed time estimate (``est_seconds``) defaults to
``0.034 s / chunk`` and can be tuned via ``CF_REEMBED_SECONDS_PER_CHUNK``.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from typing import NamedTuple

from corpus_forge.config import Config, EmbedderConfig

logger = logging.getLogger(__name__)

_DEFAULT_SECONDS_PER_CHUNK = 0.034


class Fingerprint(NamedTuple):
    short: str  # 16-char hex prefix
    full: str   # full sha256 hex


@dataclass(frozen=True)
class EmbedderDrift:
    name: str
    was_model_id: str
    was_dimension: int
    now_model_id: str
    now_dimension: int
    chunks_to_rerun: int
    est_seconds: float
    fingerprint_was: str  # short form
    fingerprint_now: str  # short form


def _seconds_per_chunk() -> float:
    raw = os.environ.get("CF_REEMBED_SECONDS_PER_CHUNK")
    if not raw:
        return _DEFAULT_SECONDS_PER_CHUNK
    try:
        return float(raw)
    except ValueError:
        return _DEFAULT_SECONDS_PER_CHUNK


def embedder_fingerprint(cfg: EmbedderConfig) -> Fingerprint:
    """Stable SHA-256 over (provider, model_id, dimension, normalize, distance).

    String fields are stripped; bool/int fields are repr()'d. Returns
    both the short (16-char) and full hex forms.
    """
    canonical = "|".join([
        cfg.provider.strip(),
        cfg.model_id.strip(),
        repr(int(cfg.dimension)),
        repr(bool(cfg.normalize)),
        cfg.distance.strip(),
    ])
    full = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return Fingerprint(short=full[:16], full=full)


def _stored_fingerprint(row: dict) -> Fingerprint:
    """Recompute the fingerprint from a stored embedder row.

    Falls back to top-level columns when ``config`` JSONB is missing
    fields (the legacy 2-key shape pre-Wave-5).
    """
    cfg_blob = row.get("config") or {}
    # Stored config may be a JSON string in sqlite — coerce.
    if isinstance(cfg_blob, str):
        import json
        try:
            cfg_blob = json.loads(cfg_blob)
        except (json.JSONDecodeError, ValueError):
            cfg_blob = {}
    provider = cfg_blob.get("provider") or row["provider"]
    model_id = cfg_blob.get("model_id") or row["model_id"]
    dimension = cfg_blob.get("dimension", row["dimension"])
    normalize = cfg_blob.get("normalize", row.get("normalized", True))
    distance = cfg_blob.get("distance", row.get("distance", "cosine"))
    canonical = "|".join([
        provider.strip(),
        model_id.strip(),
        repr(int(dimension)),
        repr(bool(normalize)),
        distance.strip(),
    ])
    full = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return Fingerprint(short=full[:16], full=full)


def compare_active(config: Config, backend) -> list[EmbedderDrift]:
    """For each active EmbedderConfig, return drift info iff fingerprint diverges."""
    drifts: list[EmbedderDrift] = []
    for cfg in config.embedders:
        if not getattr(cfg, "active", True):
            continue
        row = backend.find_embedder_row_by_name(cfg.name)
        if row is None:
            # Never registered — no drift to report.
            continue
        fp_was = _stored_fingerprint(row)
        fp_now = embedder_fingerprint(cfg)
        if fp_was.full == fp_now.full:
            continue
        # Count chunks needing rerun: existing embeddings table + missing chunks.
        embedder_id = row["id"]
        existing = 0
        try:
            existing = backend.count_existing_embeddings(embedder_id)
        except AttributeError:
            # Fallback: query via embeddings table name directly.
            existing = 0
        try:
            missing = backend.count_chunks_missing_embedding(embedder_id)
        except (AttributeError, TypeError):
            missing = 0
        chunks_to_rerun = int(existing) + int(missing)
        est_seconds = chunks_to_rerun * _seconds_per_chunk()
        drifts.append(EmbedderDrift(
            name=cfg.name,
            was_model_id=row["model_id"],
            was_dimension=int(row["dimension"]),
            now_model_id=cfg.model_id,
            now_dimension=int(cfg.dimension),
            chunks_to_rerun=chunks_to_rerun,
            est_seconds=est_seconds,
            fingerprint_was=fp_was.short,
            fingerprint_now=fp_now.short,
        ))
    return drifts


def save_active_fingerprint(config: Config, backend) -> None:
    """After a successful re-embed, persist the new fingerprint."""
    import json as _json
    for cfg in config.embedders:
        if not getattr(cfg, "active", True):
            continue
        row = backend.find_embedder_row_by_name(cfg.name)
        if row is None:
            continue
        fp = embedder_fingerprint(cfg)
        new_config_blob = {
            "provider": cfg.provider,
            "model_id": cfg.model_id,
            "dimension": int(cfg.dimension),
            "normalize": bool(cfg.normalize),
            "distance": cfg.distance,
            "fingerprint": fp.full,
        }
        # Both backends accept the raw dict via _execute parameter binding
        # (postgres adapts via psycopg.types.json.Json; sqlite needs a
        # JSON string).
        backend.update_embedder_config_blob(row["id"], new_config_blob)


__all__ = [
    "EmbedderDrift",
    "Fingerprint",
    "compare_active",
    "embedder_fingerprint",
    "save_active_fingerprint",
]
```

**Backend additions (BOTH `corpus_forge/backends/postgres.py` and
`corpus_forge/backends/sqlite.py`):**

```python
def find_embedder_row_by_name(self, name: str) -> dict | None:
    """Return the embedders row for ``name`` (None if not registered)."""
    rows = self._execute(
        "SELECT id, name, provider, model_id, dimension, normalized,"
        " distance, table_name, config FROM corpus.embedders WHERE name = %s",
        (name,),
    )
    return dict(rows[0]) if rows else None

def count_existing_embeddings(self, embedder_id: int) -> int:
    """Return total rows in the per-embedder embeddings table."""
    info = self._execute(
        "SELECT table_name FROM corpus.embedders WHERE id = %s", (embedder_id,)
    )
    if not info:
        return 0
    table_name = info[0]["table_name"]
    rows = self._execute(f"SELECT COUNT(*) AS n FROM corpus.{table_name}")
    return int(rows[0]["n"]) if rows else 0

def update_embedder_config_blob(self, embedder_id: int, blob: dict) -> None:
    """Replace the config JSONB for ``embedder_id``."""
    self._execute(
        "UPDATE corpus.embedders SET config = %s WHERE id = %s",
        (psycopg.types.json.Json(blob), embedder_id),
    )
```

SQLite mirror: drop `corpus.` prefix, use `?` placeholders, store
`json.dumps(blob)` instead of `Json(blob)`. Be careful with table_name
column existence — sqlite mirror schema also carries it (see existing
`register_embedder` which writes to `table_name` per chunks_missing_embedding
implementation).

**Tests** (`tests/embedders/test_fingerprint.py` + `test_drift_detect.py`):

1. `test_fingerprint_identical_configs` — two identical `EmbedderConfig`s →
   same `.full` and same `.short`.
2. `test_fingerprint_changes_with_dimension` — flip `dimension=1024` →
   `dimension=768`, fingerprint differs.
3. `test_fingerprint_changes_with_model_id` — flip `model_id` value,
   fingerprint differs.
4. `test_fingerprint_whitespace_stable` — leading/trailing whitespace
   on `model_id` does NOT change the fingerprint (we strip).
5. `test_fingerprint_short_is_prefix_of_full` — `.short == .full[:16]`.
6. `test_compare_active_no_stored_row_returns_empty` — backend returns
   None for the lookup → no drift.
7. `test_compare_active_matching_fingerprint_returns_empty` — stored row
   matches → empty list.
8. `test_compare_active_diverging_fingerprint_returns_drift` — stored
   row differs → one `EmbedderDrift` with correct was/now ids + sum of
   existing + missing chunks.
9. `test_compare_active_handles_multiple_actives` — two active embedders
   with mixed drift state → only the diverging ones returned.
10. `test_compare_active_inactive_skipped` — `active=False` → skipped.
11. `test_save_active_fingerprint_writes_config_blob` — after call,
    backend.update_embedder_config_blob was invoked with the new
    fingerprint embedded.

Use `MagicMock` for the backend; no real Postgres / SQLite needed for
the W5-01 unit suite. (The integration QA pass can re-verify on a
real sqlite backend if desired.)

### W5-02 — Marker file + drift prompt helper

**`corpus_forge/embedders/_marker.py` (new):**

```python
"""Pending / skipped re-embed marker (~/.cache/corpus-forge/state/pending_rerun.json).

Atomic-write via tempfile + rename. JSON shape:

    {
      "<embedder_name>": {
        "state": "pending|skipped",
        "fp_was": "...", "fp_now": "...",
        "detected_at": "iso",
        "suppressed_until": "iso?"
      }
    }
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import platformdirs

_SUPPRESSION_DAYS = 7


def _state_dir() -> Path:
    base = Path(platformdirs.user_cache_dir("corpus-forge")) / "state"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _marker_path() -> Path:
    return _state_dir() / "pending_rerun.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _read() -> dict:
    p = _marker_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _atomic_write(payload: dict) -> None:
    p = _marker_path()
    fd, tmp = tempfile.mkstemp(prefix=".pending_rerun.", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def mark_pending(name: str, *, fp_was: str, fp_now: str) -> None:
    payload = _read()
    payload[name] = {
        "state": "pending",
        "fp_was": fp_was,
        "fp_now": fp_now,
        "detected_at": _now_iso(),
    }
    _atomic_write(payload)


def mark_skipped(name: str, *, fp_was: str, fp_now: str) -> None:
    payload = _read()
    suppress_until = (datetime.now(timezone.utc) + timedelta(days=_SUPPRESSION_DAYS))
    payload[name] = {
        "state": "skipped",
        "fp_was": fp_was,
        "fp_now": fp_now,
        "detected_at": _now_iso(),
        "suppressed_until": suppress_until.replace(microsecond=0).isoformat(),
    }
    _atomic_write(payload)


def check_pending_or_skipped(
    name: str, fp_now: str
) -> Literal["pending", "skipped", "none"]:
    payload = _read()
    entry = payload.get(name)
    if entry is None:
        return "none"
    # If the user re-changed fingerprints, the marker is stale.
    if entry.get("fp_now") and entry["fp_now"] != fp_now:
        return "none"
    state = entry.get("state")
    if state == "skipped":
        suppressed = entry.get("suppressed_until")
        if not suppressed:
            return "none"
        try:
            until = datetime.fromisoformat(suppressed)
        except ValueError:
            return "none"
        if datetime.now(timezone.utc) >= until:
            return "none"
        return "skipped"
    if state == "pending":
        return "pending"
    return "none"


def clear_marker(name: str) -> None:
    payload = _read()
    if name in payload:
        del payload[name]
        _atomic_write(payload)


__all__ = [
    "check_pending_or_skipped",
    "clear_marker",
    "mark_pending",
    "mark_skipped",
]
```

**`corpus_forge/embedders/drift_prompt.py` (new):**

```python
"""Render the embedder-drift panel and prompt the user for action."""

from __future__ import annotations

from typing import Literal

from rich.panel import Panel

from corpus_forge.embedders.fingerprint import EmbedderDrift
from corpus_forge.ui.console import console as _console
from corpus_forge.ui.prompts import Prompt


def _format_drift_line(d: EmbedderDrift) -> str:
    minutes = max(1, int(d.est_seconds // 60))
    return (
        f"Was:  {d.name} ({d.was_dimension}-dim, model={d.was_model_id})  fp={d.fingerprint_was}…\n"
        f"Now:  {d.name} ({d.now_dimension}-dim, model={d.now_model_id})  fp={d.fingerprint_now}…\n"
        f"{d.chunks_to_rerun:,} chunks need re-embedding (~{minutes} min)"
    )


def prompt_for_drift(
    drifts: list[EmbedderDrift],
    *,
    background: bool,
    non_interactive: bool,
    console=None,
) -> Literal["now", "later", "skip"]:
    """Render the drift panel and prompt the user (or auto-resolve)."""
    if not drifts:
        return "skip"
    if non_interactive and background:
        return "now"
    if non_interactive and not background:
        return "later"
    body = "\n\n".join(_format_drift_line(d) for d in drifts)
    target = console if console is not None else _console
    panel = Panel(body, title="Embedder changed", border_style="brand.forge")
    target.print(panel)
    answer = Prompt.ask(
        "Rerun now, later, or skip?",
        choices=["now", "later", "skip"],
        default="now",
        console=target,
    )
    return answer  # type: ignore[return-value]


__all__ = ["prompt_for_drift"]
```

**Tests** (`tests/embedders/test_marker.py` + `test_drift_prompt.py`):

1. `test_mark_pending_writes_json` — `mark_pending("e1", fp_was="a",
   fp_now="b")`; `check_pending_or_skipped("e1", "b") == "pending"`.
2. `test_mark_skipped_with_ttl` — `mark_skipped("e1", fp_was="a",
   fp_now="b")` → returns "skipped" now; monkeypatch the clock to
   8 days ahead → returns "none".
3. `test_check_returns_none_on_unknown` — fresh state dir; check returns
   "none".
4. `test_marker_re-change_fingerprint_invalidates_skip` — mark_skipped
   for fp_now=b, then check with fp_now=c → "none" (user changed
   fingerprints again; the suppression no longer applies).
5. `test_clear_marker_removes_entry` — mark + clear; check returns "none".
6. `test_atomic_write_doesnt_race` — open the file in read mode in
   parallel via `Path.read_text()` while `mark_pending` is in flight;
   no exception, no torn write. (Simulate with two threads + 100
   iterations.)
7. `test_prompt_for_drift_non_interactive_background_returns_now` —
   `prompt_for_drift([drift], background=True, non_interactive=True)`
   → "now"; no panel rendered.
8. `test_prompt_for_drift_non_interactive_foreground_returns_later` —
   `prompt_for_drift([drift], background=False, non_interactive=True)`
   → "later".
9. `test_prompt_for_drift_interactive_renders_panel_and_returns_choice`
   — monkeypatch `Prompt.ask` to return "skip"; check that the
   render captured the drift body AND the return is "skip".
10. `test_prompt_for_drift_empty_drifts_returns_skip` — drift list
    empty → "skip" (no prompt).

Use `tmp_path` + monkeypatching of `platformdirs.user_cache_dir` to
isolate the marker file under the test temp dir (`CF_LOG_DIR` style
override doesn't exist for state — patch the function instead).

### W5-03 — CLI / setup / daemon hook points

**Action dispatcher.** Create a small private helper in
`corpus_forge/cli.py` (NOT a new module — keep the CLI's wiring local
to the CLI surface):

```python
def _handle_drift(
    config,
    backend,
    *,
    background: bool,
    non_interactive: bool,
) -> None:
    """End-to-end drift detection + prompt + action dispatch."""
    from corpus_forge.embedders.fingerprint import (
        compare_active, save_active_fingerprint,
    )
    from corpus_forge.embedders.drift_prompt import prompt_for_drift
    from corpus_forge.embedders._marker import (
        check_pending_or_skipped, mark_pending, mark_skipped, clear_marker,
    )
    drifts = compare_active(config, backend)
    if not drifts:
        return
    # Filter out suppressed entries.
    actionable: list = []
    for d in drifts:
        state = check_pending_or_skipped(d.name, d.fingerprint_now)
        if state == "skipped":
            continue
        actionable.append(d)
    if not actionable:
        return
    decision = prompt_for_drift(
        actionable, background=background, non_interactive=non_interactive,
    )
    if decision == "now":
        if background:
            _spawn_background_embed(actionable)
        else:
            _run_foreground_embed(config, backend, actionable)
            save_active_fingerprint(config, backend)
            for d in actionable:
                clear_marker(d.name)
    elif decision == "later":
        for d in actionable:
            mark_pending(d.name, fp_was=d.fingerprint_was, fp_now=d.fingerprint_now)
    elif decision == "skip":
        for d in actionable:
            mark_skipped(d.name, fp_was=d.fingerprint_was, fp_now=d.fingerprint_now)


def _spawn_background_embed(drifts):
    import os
    import subprocess
    import sys
    from pathlib import Path
    import platformdirs

    state_dir = Path(platformdirs.user_cache_dir("corpus-forge")) / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    pid_file = state_dir / "embed-worker.pid"
    # Spawn one worker per drifting embedder (sequential within the worker
    # is acceptable — the user only cares that the foreground returns
    # immediately).
    for d in drifts:
        proc = subprocess.Popen(  # noqa: S603
            [sys.executable, "-m", "corpus_forge", "embed", "-e", d.name],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        pid_file.write_text(str(proc.pid), encoding="utf-8")
    ui_info(
        "Running in background — watch with: "
        "corpus-forge logs tail --component embed-worker --follow"
    )


def _run_foreground_embed(config, backend, drifts):
    from corpus_forge.embed import backfill_embedder
    for d in drifts:
        backfill_embedder(d.name)
```

**Hook points:**

1. **`corpus_forge/cli.py` `ingest` command body (cli.py:182)** — before
   the `main(once=once)` call:
   ```python
   from corpus_forge.config import Config
   from contextlib import suppress
   try:
       config = Config.load()
   except FileNotFoundError:
       config = None
   if config is not None:
       with suppress(Exception):
           backend = _get_backend(config) if config.backend.kind == "postgres" else _get_sqlite_backend(config)
           ctx = typer.Context  # — we'll need actual ctx access
           _handle_drift(config, backend, background=False, non_interactive=False)
   ```
   Implementor: thread the typer context's `ctx.obj.background` into the
   call. Use `ctx: typer.Context` param if not already present.

2. **`corpus_forge/cli.py` `embed` command body (cli.py:200)** — same
   pattern as ingest.

3. **Setup wizard end-hook (`cli.py:setup` after `run_wizard` /
   `run_quick` / `run_non_interactive`):** load the just-written
   config and run `_handle_drift`. Skip if non_interactive AND not
   CF_BACKGROUND.

4. **Daemon (`corpus_forge/daemon.py`):** in `main()` (line 61), after
   `setup_signal_handlers()`, add:
   ```python
   from contextlib import suppress
   from corpus_forge.config import Config
   from corpus_forge.embedders.fingerprint import compare_active
   drift_logger = logging.getLogger("corpus_forge.embedders.fingerprint")
   with suppress(Exception):
       config = Config.load()
       # Daemon may not have a foreground backend; reuse the helper:
       from corpus_forge.cli import _get_backend  # imported lazy to avoid cycles
       backend = _get_backend(config)
       drifts = compare_active(config, backend)
       for d in drifts:
           drift_logger.warning(
               "Embedder drift detected: %s -> %s (%d chunks affected)",
               d.was_model_id, d.now_model_id, d.chunks_to_rerun,
           )
   ```
   No prompt. WARNING level.

5. **`sync status` command body (`cli.py:534`)** — at the end of the
   per-dataset loop, append one line:
   ```python
   import os
   import platformdirs
   from pathlib import Path
   pid_path = Path(platformdirs.user_cache_dir("corpus-forge")) / "state" / "embed-worker.pid"
   worker = "none"
   log_path = ""
   if pid_path.exists():
       try:
           pid = int(pid_path.read_text(encoding="utf-8").strip())
           os.kill(pid, 0)
           log_path = str(Path(platformdirs.user_cache_dir("corpus-forge")) / "logs" / "embed-worker.log")
           worker = f"pid={pid}, log={log_path}"
       except (ProcessLookupError, ValueError, PermissionError):
           worker = "none"
   print(f"Background embed-worker: {worker}")
   ```

**Tests** (`tests/cli/test_drift_flow.py`):

1. `test_ingest_drift_prompts_now_runs_in_foreground` — invoke `ingest
   --once` against a stubbed `compare_active` returning one drift +
   `Prompt.ask` patched to return `"now"`; assert `backfill_embedder`
   was called AND `save_active_fingerprint` was called.
2. `test_ingest_drift_later_writes_marker` — `Prompt.ask` returns
   `"later"`; assert the marker file under
   `~/.cache/corpus-forge/state/pending_rerun.json` (redirected via
   patched `platformdirs.user_cache_dir`) contains `state=pending`.
3. `test_ingest_drift_skip_writes_suppression` — `Prompt.ask` returns
   `"skip"`; assert marker has `state=skipped` + `suppressed_until` ≥
   today + 7d.
4. `test_ingest_background_flag_spawns_subprocess` — with global
   `--background` flag set, drift handler runs `decision=="now"` AND
   `subprocess.Popen` is called (mocked) AND the worker pid file is
   written.
5. `test_setup_quick_non_interactive_with_background_env_runs_now` —
   `setup --quick --non-interactive` + `CF_BACKGROUND=1` env: drift
   handler returns `"now"` background, `subprocess.Popen` invoked.
6. `test_daemon_emits_warning_log_on_drift` — call `daemon.main()`
   (patched to no-op the ingest loop) with a `compare_active` returning
   one drift; assert a WARNING record on
   `corpus_forge.embedders.fingerprint` containing
   "Embedder drift detected".
7. `test_daemon_does_not_prompt` — monkeypatch `Prompt.ask` to fail
   loudly; daemon path must NOT invoke it.
8. `test_sync_status_reports_running_worker_when_pid_alive` — write a
   pid file with `str(os.getpid())` (definitely alive); invoke
   `corpus-forge sync status`; stdout contains "pid=" + the current
   pid.
9. `test_sync_status_reports_none_when_pid_dead` — write a pid file
   with `99999999` (unlikely to exist); stdout contains
   "Background embed-worker: none".
10. `test_no_drift_no_panel` — `compare_active` returns empty list;
    drift panel is NOT rendered.

Use `CliRunner(mix_stderr=False)` so we can check stdout vs stderr
separately. Patch `corpus_forge.embedders.fingerprint.compare_active`
to control the drift state. Patch `platformdirs.user_cache_dir` to
point at `tmp_path`.

## DAG

- Wave A (3 parallel testers): W5-01, W5-02, W5-03 testers RED in
  parallel — all three test surfaces are disjoint.
- Wave B (2 parallel coders): W5-01 + W5-02 coders GREEN in parallel
  (independent module surface).
- Wave C (1 sequential coder): W5-03 coder GREEN (consumes W5-01 +
  W5-02 APIs).
- Wave D (3 parallel QAs).
