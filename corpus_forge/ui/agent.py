"""Phase L Wave 9 — agent-mode detection + JSONL emission.

When corpus-forge runs under an AI coding agent (Claude Code, OpenCode,
Gemini CLI, GitHub Copilot CLI, OpenAI Codex, Sourcegraph Amp, or any
``AI_AGENT=*``-aware tool), human chrome (banners, progress bars, log
prefixes) is pure token cost.  Agent mode flips every surface to a
single JSONL contract on stdout: one terminal ``result`` / ``error``
event per command, plus optional ``status``, ``progress``, and ``log``
events along the way.

Detection mirrors the canonical list maintained in cli/cli's
``internal/agents/detect.go``.  See ``.planning/tdd/phase_l_cli_ux.md``
§12 and ``docs/agent-mode.md`` for the contract.

This module has zero side effects on import — the singleton ``Detection``
slot starts at ``HUMAN`` until the CLI global callback calls
:func:`set_current`.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import TracebackType
from typing import Final


class AgentClient(StrEnum):
    """Identifiers for every known agent runtime + the catch-all values."""

    CLAUDE_CODE = "claude-code"
    OPENCODE = "opencode"
    GEMINI_CLI = "gemini-cli"
    COPILOT_CLI = "copilot-cli"
    CODEX = "codex"
    AMP = "amp"
    AI_GENERIC = "ai-generic"
    GENERIC = "generic"
    HUMAN = "human"


@dataclass(frozen=True)
class Detection:
    """The outcome of one detection pass.

    Attributes
    ----------
    client:
        Which :class:`AgentClient` we resolved.
    signal:
        Which env var (or ``"--agent"`` / ``""``) triggered the
        resolution.  ``""`` for the default human fallback.
    raw_value:
        The literal value of ``signal`` (or ``""``).
    """

    client: AgentClient
    signal: str
    raw_value: str


# ── singleton + accessor ─────────────────────────────────────────────


_HUMAN: Final[Detection] = Detection(client=AgentClient.HUMAN, signal="", raw_value="")
_DETECTION: Detection = _HUMAN


def current_detection() -> Detection:
    """Return the most recent ``Detection`` (or the human default)."""

    return _DETECTION


def set_current(detection: Detection) -> None:
    """Replace the module-level :class:`Detection` slot."""

    global _DETECTION  # noqa: PLW0603 — module-singleton handle by design
    _DETECTION = detection


def is_agent_mode(detection: Detection | None = None) -> bool:
    """Return True iff ``detection`` (or :func:`current_detection`) is not HUMAN."""

    target = detection if detection is not None else current_detection()
    return target.client != AgentClient.HUMAN


# ── detection ────────────────────────────────────────────────────────


# Per cli/cli/internal/agents/detect.go the validation is roughly
# ``[a-zA-Z0-9_-]+`` on the prefix; we widen to include ``.`` because
# real-world values carry a semver-ish version (e.g.
# ``claude-code_2.1.133_agent``).
_AI_AGENT_RE = re.compile(r"^[a-zA-Z0-9._-]+$")
_VALID_CLIENT_VALUES: frozenset[str] = frozenset(c.value for c in AgentClient)


def _resolve_explicit(explicit: str | None) -> Detection | None:
    """Translate the ``--agent <type>`` flag into a Detection.

    Returns ``None`` when no resolution is possible from the flag
    (``"auto"`` or unset).  ``"off"`` short-circuits to HUMAN.
    """

    if explicit is None:
        return None
    normalised = explicit.strip().lower()
    if not normalised or normalised == "auto":
        return None
    if normalised == "off":
        return Detection(client=AgentClient.HUMAN, signal="--agent", raw_value=explicit)
    if normalised in _VALID_CLIENT_VALUES:
        return Detection(
            client=AgentClient(normalised),
            signal="--agent",
            raw_value=explicit,
        )
    # Unknown explicit values fall through to AI_GENERIC so the user
    # gets agent mode without us silently snapping to HUMAN.
    return Detection(client=AgentClient.AI_GENERIC, signal="--agent", raw_value=explicit)


def _resolve_cf_agent(env: Mapping[str, str]) -> Detection | None:
    """Detection from the ``CF_AGENT`` env var (same vocabulary as ``--agent``)."""

    raw = env.get("CF_AGENT")
    if not raw:
        return None
    normalised = raw.strip().lower()
    if normalised == "off":
        return Detection(client=AgentClient.HUMAN, signal="CF_AGENT", raw_value=raw)
    if normalised in _VALID_CLIENT_VALUES:
        return Detection(
            client=AgentClient(normalised),
            signal="CF_AGENT",
            raw_value=raw,
        )
    return Detection(client=AgentClient.AI_GENERIC, signal="CF_AGENT", raw_value=raw)


def _resolve_ai_agent(env: Mapping[str, str]) -> Detection | None:
    """Detection from the generic ``AI_AGENT=<name>`` convention.

    The value's prefix (before the first ``_``) is matched against the
    enum literals.  Unknown prefixes resolve to AI_GENERIC so an agent
    that ships its own value still triggers structured output.
    """

    raw = env.get("AI_AGENT")
    if not raw:
        return None
    if not _AI_AGENT_RE.match(raw):
        return None
    prefix = raw.split("_", 1)[0].lower()
    if prefix in _VALID_CLIENT_VALUES and prefix not in {"generic", "human"}:
        return Detection(
            client=AgentClient(prefix),
            signal="AI_AGENT",
            raw_value=raw,
        )
    return Detection(client=AgentClient.AI_GENERIC, signal="AI_AGENT", raw_value=raw)


def _mcp_stdio_active(env: Mapping[str, str], argv: list[str] | None) -> Detection | None:
    """MCP stdio carve-out: force agent mode when the wire is JSON-RPC."""

    if env.get("CF_MCP_TRANSPORT", "").lower() == "stdio":
        return Detection(
            client=AgentClient.GENERIC,
            signal="CF_MCP_TRANSPORT",
            raw_value=env.get("CF_MCP_TRANSPORT", ""),
        )
    if argv is None:
        argv = list(sys.argv)
    # Look for ``mcp serve ... --transport stdio`` anywhere in argv.
    if "mcp" in argv and "serve" in argv:
        # ``--transport=stdio`` or ``--transport stdio``.
        for i, token in enumerate(argv):
            if token.startswith("--transport"):
                value = ""
                if "=" in token:
                    value = token.split("=", 1)[1]
                elif i + 1 < len(argv):
                    value = argv[i + 1]
                if value.lower() == "stdio":
                    return Detection(
                        client=AgentClient.GENERIC,
                        signal="argv",
                        raw_value="mcp serve --transport stdio",
                    )
    return None


def detect(
    *,
    explicit: str | None = None,
    env: Mapping[str, str] | None = None,
    stdin_tty: bool | None = None,  # noqa: ARG001 — kept for API stability + future heuristics
    stdout_tty: bool | None = None,  # noqa: ARG001
    argv: list[str] | None = None,
) -> Detection:
    """Resolve a :class:`Detection` per the §12 precedence ladder.

    Parameters
    ----------
    explicit:
        Value of the ``--agent`` global flag.  ``None`` / ``"auto"`` /
        empty string fall through; ``"off"`` forces HUMAN.
    env:
        Mapping to inspect (defaults to :data:`os.environ`).
    stdin_tty / stdout_tty:
        Override TTY detection (mostly for tests).
    argv:
        Override ``sys.argv`` (mostly for tests / MCP carve-out).
    """

    if env is None:
        env = os.environ

    # 1. Explicit --agent flag (off → HUMAN, auto → fall through).
    if (det := _resolve_explicit(explicit)) is not None:
        return det

    # 2. CF_AGENT env var.
    if (det := _resolve_cf_agent(env)) is not None:
        return det

    # 3. Generic AI_AGENT convention.
    if (det := _resolve_ai_agent(env)) is not None:
        return det

    # 4. AGENT=amp — checked BEFORE Claude Code (Amp also sets CLAUDECODE).
    if env.get("AGENT", "").strip().lower() == "amp":
        return Detection(client=AgentClient.AMP, signal="AGENT", raw_value=env.get("AGENT", ""))

    # 5. Codex (any of three env vars).
    for codex_var in ("CODEX_SANDBOX", "CODEX_CI", "CODEX_THREAD_ID"):
        if env.get(codex_var):
            return Detection(
                client=AgentClient.CODEX,
                signal=codex_var,
                raw_value=env.get(codex_var, ""),
            )

    # 6. Gemini CLI.
    if env.get("GEMINI_CLI"):
        return Detection(
            client=AgentClient.GEMINI_CLI,
            signal="GEMINI_CLI",
            raw_value=env.get("GEMINI_CLI", ""),
        )

    # 7. GitHub Copilot CLI.
    if env.get("COPILOT_CLI"):
        return Detection(
            client=AgentClient.COPILOT_CLI,
            signal="COPILOT_CLI",
            raw_value=env.get("COPILOT_CLI", ""),
        )

    # 8. OpenCode.
    if env.get("OPENCODE"):
        return Detection(
            client=AgentClient.OPENCODE,
            signal="OPENCODE",
            raw_value=env.get("OPENCODE", ""),
        )

    # 9. Claude Code (last in the agent block — Amp wants precedence).
    if env.get("CLAUDECODE"):
        return Detection(
            client=AgentClient.CLAUDE_CODE,
            signal="CLAUDECODE",
            raw_value=env.get("CLAUDECODE", ""),
        )

    # 10. MCP stdio carve-out (always agent mode).
    if (det := _mcp_stdio_active(env, argv)) is not None:
        return det

    # 11. Default: HUMAN. (The earlier plan included a `CI=true && no-TTY` heuristic
    # but it broke the user's own pytest runs in CI by silently flipping every
    # CliRunner-invoked command into JSONL mode — `--json` test fixtures and
    # human-substring assertions both regressed. Agent mode now requires an
    # explicit signal: `--agent`, `CF_AGENT`, one of the recognized agent env
    # vars above, or the MCP stdio carve-out.)
    return _HUMAN


# ── JSONL emission ───────────────────────────────────────────────────


def _iso_now() -> str:
    """UTC ISO 8601 with millisecond precision (``Z`` suffix)."""

    # ``isoformat(timespec='milliseconds')`` gives ``+00:00`` — swap to
    # ``Z`` per the agent-mode contract.
    raw = datetime.now(UTC).isoformat(timespec="milliseconds")
    if raw.endswith("+00:00"):
        return raw[:-6] + "Z"
    return raw


def _sanitize(value: object) -> object:
    """Best-effort make ``value`` JSON-serializable."""

    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Mapping):
        return {str(k): _sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_sanitize(v) for v in value]
    return str(value)


def emit(event_type: str, **fields: object) -> None:
    """Write one JSONL line to stdout, then flush.

    Field order is stable (``event``, ``ts``, …) so transcripts diff
    sanely.  Every value is run through :func:`_sanitize` so non-JSON
    inputs (Path, Enum, dataclass instances passed via ``data=``) don't
    explode at the wire.
    """

    payload: dict[str, object] = {"event": event_type, "ts": _iso_now()}
    for k, v in fields.items():
        payload[k] = _sanitize(v)
    line = json.dumps(payload, ensure_ascii=False, default=str)
    # No embedded newlines: dumps already escapes them inside strings,
    # so a single ``\n`` ends the record.
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def result(cmd: str, *, status: str = "ok", data: dict[str, object] | None = None) -> int:
    """Emit a terminal ``result`` event and return a process exit code."""

    emit("result", cmd=cmd, status=status, data=data or {})
    return 0 if status == "ok" else 1


def error(cmd: str, *, kind: str, msg: str, exit_code: int = 1) -> int:
    """Emit a terminal ``error`` event and return ``exit_code``."""

    emit("error", cmd=cmd, kind=kind, msg=msg)
    return exit_code


# ── progress emitter ─────────────────────────────────────────────────


_DEFAULT_TIME_INTERVAL_S: Final[float] = 10.0


class ProgressEmitter:
    """Context-manager replacement for ``rich.progress.Progress`` in agent mode.

    The contract:
      - ``__enter__`` returns ``self``.
      - :meth:`add_task` / :meth:`update` / :meth:`advance` mirror Rich's
        names so existing call sites don't branch.
      - Sparse ``progress`` events are emitted at every 25% milestone of
        ``total`` (for bounded ops) OR every ``_DEFAULT_TIME_INTERVAL_S``
        seconds (whichever happens first).
      - ``__exit__`` emits a final ``progress`` event at 100% for
        bounded ops.
    """

    def __init__(self, op: str, *, total: int | None = None) -> None:
        self._op = op
        self._total = total
        self._completed: int = 0
        self._last_milestone_pct: float = 0.0
        self._last_emit_at: float = time.monotonic()
        self._started_at: float = time.monotonic()
        self._task_ids: list[int] = []

    # ── Rich-compatible task surface ──────────────────────────────

    def add_task(self, description: str, *, total: int | None = None, **_kwargs: object) -> int:
        """Mimic Rich's ``Progress.add_task`` — we honour ``total``.

        ``description`` is accepted (and ignored) so existing call sites
        that mix Rich and the agent emitter keep working.
        """

        del description  # unused — kept for Rich API compatibility.
        task_id = len(self._task_ids)
        self._task_ids.append(task_id)
        if total is not None and self._total is None:
            self._total = total
        return task_id

    def update(
        self,
        task_id: int,  # noqa: ARG002 — Rich-compat
        *,
        advance: int | None = None,
        completed: int | None = None,
        **_kwargs: object,
    ) -> None:
        if completed is not None:
            self._completed = int(completed)
        elif advance is not None:
            self._completed += int(advance)
        self._maybe_emit()

    def advance(self, task_id_or_n: int = 1, *, n: int | None = None) -> None:
        """Increment progress.

        Two call shapes are supported:
          - ``emitter.advance()`` / ``emitter.advance(3)`` — old style.
          - ``emitter.advance(task_id, n=3)`` — Rich style (task_id is
            ignored because we have no per-task state).
        """

        delta = n if n is not None else task_id_or_n
        if not isinstance(delta, int):
            delta = 1
        self._completed += delta
        self._maybe_emit()

    def remove_task(self, task_id: int) -> None:
        """No-op — ``ProgressEmitter`` doesn't track per-task state.

        Rich's :meth:`Progress.remove_task` is called by ``ingest_once``
        in PR #46 so each per-source bar disappears beneath the
        persistent global bar after that source finishes. Agent mode
        emits discrete JSON progress events instead of a live TTY
        render, so there is nothing to clear — but the method must
        exist for Rich-API parity with :class:`rich.progress.Progress`,
        otherwise the call site crashes with ``AttributeError``.
        """

    # ── milestone math ────────────────────────────────────────────

    def _maybe_emit(self) -> None:
        now = time.monotonic()
        bounded = self._total is not None and self._total > 0
        emit_now = False
        pct: float | None = None
        if bounded:
            assert self._total is not None
            pct = (self._completed / self._total) if self._total else 0.0
            # Milestone every 25% — emit when we cross the next quarter.
            next_milestone = self._last_milestone_pct + 0.25
            if pct >= next_milestone:
                emit_now = True
                # Snap to the highest quarter we crossed.
                while self._last_milestone_pct + 0.25 <= pct:
                    self._last_milestone_pct += 0.25
        if not emit_now and (now - self._last_emit_at) >= _DEFAULT_TIME_INTERVAL_S:
            emit_now = True
        if emit_now:
            self._last_emit_at = now
            self._emit_progress(pct)

    def _emit_progress(self, pct: float | None) -> None:
        fields: dict[str, object] = {
            "op": self._op,
            "done": self._completed,
        }
        if self._total is not None:
            fields["total"] = self._total
            if pct is not None:
                fields["pct"] = round(pct, 4)
        elapsed = max(1e-9, time.monotonic() - self._started_at)
        fields["rate_per_s"] = round(self._completed / elapsed, 2)
        emit("progress", **fields)

    # ── context manager ───────────────────────────────────────────

    def __enter__(self) -> ProgressEmitter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        # Final 100% emission for bounded ops, regardless of how we got
        # there.  Suppress on exception so a partial failure still
        # surfaces via the surrounding error event.
        if exc is not None:
            return
        if self._total is not None and self._total > 0:
            self._completed = max(self._completed, self._total)
            self._last_milestone_pct = 1.0
            self._emit_progress(1.0)


# ── command decorator ────────────────────────────────────────────────


class RequiresInteractiveError(Exception):
    """Raised when an interactive prompt is needed under agent mode.

    The CLI global error handler catches this and emits a structured
    ``error`` event with ``kind="requires_interactive"`` + exit code 2.
    """

    def __init__(self, *, cmd: str | None = None, prompt: str | None = None) -> None:
        self.cmd = cmd or "<unknown>"
        self.prompt = prompt or ""
        super().__init__(
            f"corpus-forge requires an interactive prompt ({self.prompt!r}) but "
            "is running in agent mode — pass the value via flags or env."
        )


def cmd_wrap(name: str):
    """Decorator that wraps a command body with agent-mode emissions.

    Zero overhead when not in agent mode.  When agent mode is on:

      - ``command.start`` is emitted before the body runs.
      - On success: nothing (the body is expected to call :func:`result`
        itself when it has a structured payload, or simply return — in
        which case we emit a default ``{"status":"ok"}`` result).
      - On :class:`RequiresInteractiveError`: a structured ``error`` event
        with ``kind="requires_interactive"`` is emitted and the wrapper
        raises ``typer.Exit(code=2)``.
      - On any other exception: a structured ``error`` event with the
        exception's class as ``kind`` is emitted and the exception
        re-raises.
    """

    def _decorate(fn):
        def _wrapped(*args: object, **kwargs: object) -> object:
            if not is_agent_mode():
                return fn(*args, **kwargs)
            # Strip Typer's ctx from args so the emitted payload is JSON-safe.
            visible_kwargs = {k: v for k, v in kwargs.items() if not _is_context_like(v)}
            try:
                from corpus_forge import __version__ as _cf_version  # noqa: PLC0415
            except Exception:  # pragma: no cover — defensive
                _cf_version = "unknown"
            detection = current_detection()
            emit(
                "command.start",
                cmd=name,
                args=visible_kwargs,
                version=_cf_version,
                agent=detection.client.value,
            )
            try:
                out = fn(*args, **kwargs)
            except RequiresInteractiveError as exc:
                emit("error", cmd=name, kind="requires_interactive", msg=str(exc))
                # Re-raise as a typer.Exit-equivalent.  Importing typer
                # lazily so this module stays import-light.
                import typer  # noqa: PLC0415

                raise typer.Exit(code=2) from exc
            except SystemExit:
                # typer.Exit / sys.exit — propagate untouched.  The body
                # owns its own exit code semantics.
                raise
            except Exception as exc:
                emit("error", cmd=name, kind=type(exc).__name__, msg=str(exc))
                raise
            return out

        _wrapped.__name__ = fn.__name__
        _wrapped.__doc__ = fn.__doc__
        _wrapped.__wrapped__ = fn  # type: ignore[attr-defined]
        return _wrapped

    return _decorate


def _is_context_like(value: object) -> bool:
    """Return True iff ``value`` is a Click / Typer ``Context``.

    Lazy import so this module stays import-light.  Failures return
    False — the worst case is a Context object getting str()'d into the
    JSONL payload, which is ugly but not breaking.
    """

    try:  # pragma: no cover — pure import edge
        import click  # noqa: PLC0415

        return isinstance(value, click.Context)
    except Exception:
        return False


__all__ = [
    "AgentClient",
    "Detection",
    "ProgressEmitter",
    "RequiresInteractiveError",
    "cmd_wrap",
    "current_detection",
    "detect",
    "emit",
    "error",
    "is_agent_mode",
    "result",
    "set_current",
]
