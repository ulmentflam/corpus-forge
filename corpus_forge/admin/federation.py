"""``corpus-forge config publish / pull / diff`` — fleet-3 federation verbs.

RFC ``rfc-fleet-3-federated-config-and-setup`` item 3. These three
verbs ride the *existing* ``config`` sub-app (defined in
:mod:`corpus_forge.admin.config`) so the operator types
``corpus-forge config publish`` alongside ``config get`` / ``config
set``. Registration happens at import time via the
``@config_app.command(...)`` decorators below; :mod:`corpus_forge.cli`
imports this module once so the verbs attach.

The shared-scope extraction + comment-preserving merge live in
:mod:`corpus_forge.config_scope` (PR #101). This module is the *verb*
layer: state-file bookkeeping, the optimistic-concurrency surfacing,
the dry-run-default UX (mirroring :mod:`corpus_forge.admin.prune`),
and the Rich / ``--json`` / agent-mode rendering (mirroring
:mod:`corpus_forge.admin.bench`).

Verb semantics:

- **``publish``** — extract the shared scope, *re-scan* it against the
  deny-list regex (defense in depth — PR #101's structural test makes
  a hit impossible, hence "re-scan"), then publish under optimistic
  concurrency using the locally-recorded ``last_pulled_version`` as
  ``expected_version``. A :class:`SharedConfigVersionConflict` becomes
  a clean "pull first" operator error. On success the state file
  records the new version.
- **``pull``** — fetch ``(version, body)``, render the unified diff
  between the local ``config.toml`` text and
  :func:`~corpus_forge.config_scope.merge_shared_scope` applied to it.
  **Dry-run by default** (prune precedent); ``--apply`` backs up the
  config to ``config.toml.bak``, writes the merged text, and records
  the pulled version.
- **``diff``** — the same computation as ``pull``'s dry-run, framed as
  inspection (no apply hint) and showing local-vs-published version
  numbers.

Every verb surfaces :class:`FederationUnsupported` (SQLite backend) as
a clean "federation requires the postgres backend" error with a
non-zero exit and no traceback.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.panel import Panel
from rich.syntax import Syntax

from corpus_forge.admin.config import config_app, resolve_config_path
from corpus_forge.backends.base import (
    FederationUnsupported,
    SharedConfigVersionConflict,
)
from corpus_forge.config_scope import merge_shared_scope, shared_scope_dict
from corpus_forge.ui.console import console as ui_console
from corpus_forge.ui.console import error as ui_error
from corpus_forge.ui.console import info as ui_info
from corpus_forge.ui.console import ok as ui_ok

logger = logging.getLogger(__name__)


# ── Deny-list re-scan (defense in depth) ────────────────────────────────

# Mirrors the field-name deny-list pinned by
# ``tests/unit/test_config_scope.py``. PR #101's structural test makes
# a leak impossible at the annotation layer; this regex is the runtime
# re-scan over the *extracted* body so ``publish`` refuses to ship a
# path-shaped or secret-shaped key even if a future field slips past
# review.
_DENY_RE = re.compile(
    r"(root|path|dir|dsn|file|url|device|key|token|secret|password|env|lanes)",
    re.IGNORECASE,
)


def _scan_for_denied_key(body: object, path: str = "$") -> str | None:
    """Return the first deny-shaped dotted key path in ``body``, or ``None``.

    Walks the nested dict / list structure the extraction emits and
    matches each *key* name against :data:`_DENY_RE`. Used by
    ``publish`` to refuse shipping a body that names a machine path,
    endpoint, or key-material field.
    """

    if isinstance(body, dict):
        for key, value in body.items():
            here = f"{path}.{key}"
            if _DENY_RE.search(str(key)):
                return here
            found = _scan_for_denied_key(value, here)
            if found is not None:
                return found
    elif isinstance(body, list):
        for i, value in enumerate(body):
            found = _scan_for_denied_key(value, f"{path}[{i}]")
            if found is not None:
                return found
    return None


# ── Local version-bookkeeping state file ────────────────────────────────

_STATE_FILENAME = "federation-state.json"
_LAST_PULLED_KEY = "last_pulled_version"


def state_path() -> Path:
    """Path to the local federation state file, next to ``config.toml``.

    Honours ``CORPUS_FORGE_CONFIG``'s directory when set (via
    :func:`corpus_forge.admin.config.resolve_config_path`), so a test
    or an alternate-profile install keeps its bookkeeping beside the
    config it pertains to rather than in the user default location.
    """

    return resolve_config_path().parent / _STATE_FILENAME


def read_last_pulled_version() -> int:
    """Return the locally-recorded last-pulled version (``0`` if unknown).

    A missing or unparseable state file means "never pulled" → ``0``,
    which is exactly the ``expected_version`` a fresh host passes to
    :meth:`StorageBackend.put_shared_config` for the first publish.
    """

    path = state_path()
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.debug("federation state %s unreadable (%r); treating as version 0", path, exc)
        return 0
    raw = data.get(_LAST_PULLED_KEY) if isinstance(data, dict) else None
    try:
        return int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        return 0


def write_last_pulled_version(version: int) -> None:
    """Record ``version`` as the locally-known last-pulled version."""

    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({_LAST_PULLED_KEY: int(version)}, indent=2) + "\n",
        encoding="utf-8",
    )


# ── Backend / config plumbing ────────────────────────────────────────────


def _load_config() -> Any:
    """Load the active config, mapping a missing file to exit code 2."""

    from corpus_forge.config import Config

    try:
        return Config.load()
    except FileNotFoundError:
        ui_error("No configuration found; run `corpus-forge setup` to create one.")
        raise typer.Exit(code=2) from None


def _build_backend(config: Any) -> Any:
    """Construct + migrate the configured backend (sqlite|postgres)."""

    kind = getattr(config.backend, "kind", "postgres")
    if kind == "sqlite":
        from corpus_forge.backends.sqlite import SQLiteBackend

        backend = SQLiteBackend(path=config.backend.dsn, schema=config.backend.schema)
    else:
        from corpus_forge.backends.postgres import PostgresBackend

        backend = PostgresBackend(dsn=config.backend.dsn, schema=config.backend.schema)
    backend.migrate()
    return backend


def _close_backend(backend: Any) -> None:
    """Best-effort backend close (mirrors the other verbs)."""

    import contextlib

    closer = getattr(backend, "close", None)
    if callable(closer):
        with contextlib.suppress(Exception):  # pragma: no cover — defensive
            closer()


def _agent_mode(json_out: bool) -> bool:
    """``--json`` forces agent mode; otherwise honour ambient detection."""

    from corpus_forge.ui import agent as ui_agent

    return json_out or ui_agent.is_agent_mode()


# ── Shared diff computation (pull dry-run + diff) ─────────────────────────


@dataclass(frozen=True)
class PullPlan:
    """The computed delta a ``pull`` would apply (also drives ``diff``).

    ``published_version`` is the corpus's current shared-config version;
    ``local_version`` is what this host last pulled. ``changed`` is
    ``True`` when the merge would alter the local config text.
    """

    published_version: int
    local_version: int
    local_text: str
    merged_text: str
    config_path: Path

    @property
    def changed(self) -> bool:
        return self.merged_text != self.local_text

    def diff_lines(self) -> list[str]:
        """Unified diff (local → merged) as a list of lines (no trailing \\n)."""

        return list(
            difflib.unified_diff(
                self.local_text.splitlines(),
                self.merged_text.splitlines(),
                fromfile=f"{self.config_path} (local)",
                tofile=f"{self.config_path} (after pull, v{self.published_version})",
                lineterm="",
            )
        )


def _read_local_text(config_path: Path) -> str:
    """Return the local config text (empty string when the file is absent)."""

    if not config_path.exists():
        return ""
    return config_path.read_text(encoding="utf-8")


def compute_pull_plan(backend: Any, *, local_version: int) -> PullPlan | None:
    """Fetch the published config and compute the merge delta.

    Returns ``None`` when nothing has been published yet (the
    ``get_shared_config`` read returned ``None``) — callers render a
    friendly "nothing published" message and exit 0.
    """

    fetched = backend.get_shared_config()
    if fetched is None:
        return None
    published_version, body = fetched
    config_path = resolve_config_path()
    local_text = _read_local_text(config_path)
    merged_text = merge_shared_scope(local_text, body)
    return PullPlan(
        published_version=int(published_version),
        local_version=local_version,
        local_text=local_text,
        merged_text=merged_text,
        config_path=config_path,
    )


# ── Verb: publish ─────────────────────────────────────────────────────────


@config_app.command("publish")
def cmd_publish(
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit one JSON object instead of Rich output (agent mode)."),
    ] = False,
) -> None:
    """Publish this host's shared-scope config to the corpus.

    Extracts the fleet-shared subset, re-scans it against the deny-list
    (defense in depth), then writes it as the next version under
    optimistic concurrency. If the corpus already has a newer version
    than this host last pulled, the publish is refused with a "pull
    first" message — never a blind clobber.
    """

    agent_mode = _agent_mode(json_out)
    config = _load_config()

    body = shared_scope_dict(config)

    # Defense in depth: re-scan the extracted body for any deny-shaped
    # key before it leaves the host. Structurally impossible given
    # PR #101's test, but a publish must never ship a path/secret.
    denied = _scan_for_denied_key(body)
    if denied is not None:
        msg = (
            f"refusing to publish: shared-scope key {denied} is path-shaped or "
            "secret-shaped — fleet-shared config must never carry machine paths, "
            "endpoints, or key material"
        )
        if agent_mode:
            print(json.dumps({"status": "denied", "offending_key": denied, "error": msg}))
        else:
            ui_error(msg)
        raise typer.Exit(code=2)

    expected_version = read_last_pulled_version()

    backend = _build_backend(config)
    try:
        new_version = backend.put_shared_config(
            body,
            expected_version=expected_version,
            published_by=config.host_id(),
        )
    except FederationUnsupported:
        _federation_unsupported(agent_mode)
        raise typer.Exit(code=1) from None
    except SharedConfigVersionConflict:
        # Surface the optimistic-concurrency loss as a clean operator
        # action — name both versions so the fix is obvious.
        fetched = None
        try:
            fetched = backend.get_shared_config()
        except Exception as exc:  # pragma: no cover — defensive
            logger.debug("post-conflict get_shared_config failed (%r)", exc)
        current = int(fetched[0]) if fetched is not None else expected_version + 1
        msg = (
            f"the corpus has a newer shared config (v{current}) than you last "
            f"pulled (v{expected_version}) — run `corpus-forge config pull` first"
        )
        if agent_mode:
            print(
                json.dumps(
                    {
                        "status": "conflict",
                        "published_version": current,
                        "last_pulled_version": expected_version,
                        "error": msg,
                    }
                )
            )
        else:
            ui_error(msg)
        raise typer.Exit(code=1) from None
    finally:
        _close_backend(backend)

    # Success: the published version is now also our last-pulled version.
    write_last_pulled_version(new_version)

    if agent_mode:
        print(
            json.dumps(
                {
                    "status": "published",
                    "version": new_version,
                    "published_by": config.host_id(),
                }
            )
        )
    else:
        ui_ok(f"published shared config as v{new_version} (by {config.host_id()})")


# ── Verb: pull ─────────────────────────────────────────────────────────────


@config_app.command("pull")
def cmd_pull(
    apply: Annotated[
        bool,
        typer.Option(
            "--apply/--no-apply",
            help=(
                "When set, write the merged config to disk (backing up to "
                "config.toml.bak first). Default is dry-run — the diff is "
                "shown but nothing is written."
            ),
        ),
    ] = False,
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit one JSON object instead of Rich output (agent mode)."),
    ] = False,
) -> None:
    """Pull the corpus's shared config into the local ``config.toml``.

    Dry-run by default (mirrors ``prune``): renders the unified diff
    between the local config and the merge result, then prints "re-run
    with --apply to write". With ``--apply`` it backs up the config to
    ``config.toml.bak``, writes the merged text, and records the pulled
    version locally. Only shared-scope keys change; comments and local
    values survive.
    """

    agent_mode = _agent_mode(json_out)
    config = _load_config()
    local_version = read_last_pulled_version()

    backend = _build_backend(config)
    try:
        try:
            plan = compute_pull_plan(backend, local_version=local_version)
        except FederationUnsupported:
            _federation_unsupported(agent_mode)
            raise typer.Exit(code=1) from None
    finally:
        _close_backend(backend)

    if plan is None:
        _nothing_published(agent_mode)
        return

    if not apply:
        _render_diff(plan, agent_mode, with_apply_hint=True)
        return

    # Validate the merge BEFORE touching disk — never clobber a working
    # config.toml with one that won't load. This mirrors how Config.load
    # validates (``Config(**tomllib.loads(text))``), so the guard rejects
    # exactly what the app would reject at startup. A merge can be valid
    # TOML yet fail Config validation (e.g. a shared value the local
    # config's other fields make invalid).
    import tomllib

    from corpus_forge.config import Config

    try:
        Config(**tomllib.loads(plan.merged_text))
    except Exception as exc:
        if agent_mode:
            print(json.dumps({"status": "invalid_merge", "error": str(exc)}))
        else:
            ui_error(
                f"refusing to apply: the merged config does not validate "
                f"({exc}); {plan.config_path} is unchanged"
            )
        raise typer.Exit(code=3) from None

    # Apply: back up then write the merged text, record the version.
    backup_path = plan.config_path.with_name(plan.config_path.name + ".bak")
    if plan.config_path.exists():
        backup_path.write_text(plan.local_text, encoding="utf-8")
    plan.config_path.parent.mkdir(parents=True, exist_ok=True)
    plan.config_path.write_text(plan.merged_text, encoding="utf-8")
    write_last_pulled_version(plan.published_version)

    if agent_mode:
        print(
            json.dumps(
                {
                    "status": "applied",
                    "version": plan.published_version,
                    "changed": plan.changed,
                    "backup": str(backup_path) if plan.config_path.exists() else None,
                    "config_path": str(plan.config_path),
                }
            )
        )
    else:
        ui_ok(
            f"pulled shared config v{plan.published_version} → {plan.config_path} "
            f"(backup at {backup_path})"
        )


# ── Verb: diff ─────────────────────────────────────────────────────────────


@config_app.command("diff")
def cmd_diff(
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit one JSON object instead of Rich output (agent mode)."),
    ] = False,
) -> None:
    """Show the delta a ``config pull`` would apply (inspection only).

    Same computation as ``pull``'s dry-run, framed as inspection: it
    prints the local-vs-published version numbers and the unified diff,
    but offers no ``--apply`` hint.
    """

    agent_mode = _agent_mode(json_out)
    config = _load_config()
    local_version = read_last_pulled_version()

    backend = _build_backend(config)
    try:
        try:
            plan = compute_pull_plan(backend, local_version=local_version)
        except FederationUnsupported:
            _federation_unsupported(agent_mode)
            raise typer.Exit(code=1) from None
    finally:
        _close_backend(backend)

    if plan is None:
        _nothing_published(agent_mode)
        return

    _render_diff(plan, agent_mode, with_apply_hint=False)


# ── Rendering helpers ────────────────────────────────────────────────────


def _federation_unsupported(agent_mode: bool) -> None:
    """Emit the clean SQLite-backend error (caller raises Exit(1))."""

    msg = "federation requires the postgres backend"
    if agent_mode:
        print(json.dumps({"status": "unsupported", "error": msg}))
    else:
        ui_error(msg)


def _nothing_published(agent_mode: bool) -> None:
    """Friendly "no shared config yet" message; the caller has exited 0."""

    msg = "nothing published yet — no host has run `corpus-forge config publish`"
    if agent_mode:
        print(json.dumps({"status": "nothing_published", "message": msg}))
    else:
        ui_info(msg)


def _render_diff(plan: PullPlan, agent_mode: bool, *, with_apply_hint: bool) -> None:
    """Render a :class:`PullPlan` — JSON in agent mode, Rich otherwise."""

    diff_lines = plan.diff_lines()

    if agent_mode:
        print(
            json.dumps(
                {
                    "status": "diff",
                    "published_version": plan.published_version,
                    "local_version": plan.local_version,
                    "changed": plan.changed,
                    "diff": diff_lines,
                    "config_path": str(plan.config_path),
                }
            )
        )
        return

    ui_console.print(
        Panel.fit(
            f"published v{plan.published_version}  |  local last-pulled v{plan.local_version}",
            title="shared config",
        )
    )
    if not plan.changed:
        ui_info("local config already matches the published shared scope — nothing to pull.")
        return

    ui_console.print(Syntax("\n".join(diff_lines), "diff", theme="ansi_dark"))
    if with_apply_hint:
        ui_info("re-run with --apply to write the merged config.")


__all__ = [
    "PullPlan",
    "compute_pull_plan",
    "read_last_pulled_version",
    "state_path",
    "write_last_pulled_version",
]
