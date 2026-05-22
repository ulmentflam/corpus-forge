"""``corpus-forge logs`` Typer sub-app (Phase L Wave 6).

Three verbs:

- ``path`` — print the rotating-log directory.
- ``tail`` — read the last N lines of a component log, optionally
  ``--follow`` with a 250 ms polling loop (no ``inotify`` / ``kqueue``
  hooks — that's a future Wave 8 deliverable if anyone needs it).
- ``clear`` — truncate a component's log file (with confirmation).

The themed renderer levels-up log lines so DEBUG looks muted, INFO is
cyan, WARNING yellow, ERROR red.  Lines that don't match the standard
``YYYY-MM-DD HH:MM:SS.ms [LEVEL  ] logger: msg`` shape render in the
``muted`` style — this is the catch-all for tracebacks, ASCII art,
and anything else that ended up in the log file via ``print``.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import typer

from corpus_forge.logging_config import get_log_dir
from corpus_forge.ui import Confirm
from corpus_forge.ui import info as ui_info
from corpus_forge.ui import ok as ui_ok
from corpus_forge.ui import warn as ui_warn
from corpus_forge.ui.console import console as ui_console

logs_app = typer.Typer(
    name="logs",
    help="Inspect and manage corpus-forge rotating log files.",
    add_completion=False,
    no_args_is_help=True,
)


# ── Level → theme-style table ───────────────────────────────────────


_LEVEL_STYLE: dict[str, str] = {
    "DEBUG": "muted",
    "INFO": "info",
    "WARNING": "warn",
    "WARN": "warn",
    "ERROR": "error",
    "CRITICAL": "error",
}

# Severity ordering for ``--level`` filtering. Mirrors stdlib
# ``logging`` numeric levels — higher value = more severe.
# ``WARN`` is an alias for ``WARNING`` (stdlib accepts both spellings
# from older code; the log format emits ``WARNING``).
_LEVEL_RANK: dict[str, int] = {
    "DEBUG": 10,
    "INFO": 20,
    "WARN": 30,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}

# Default Wave-1 log-format prefix shape:
#   2026-05-18 12:34:56.789 [INFO   ] corpus_forge.ingest.scan: msg
_LOG_LINE_RE = re.compile(
    r"^(?P<ts>\S+ \S+) \[(?P<level>[A-Z]+)\s*\]\s+(?P<logger>\S+): (?P<msg>.*)$"
)


def _passes_level_filter(line: str, min_level: int | None) -> bool:
    """Return True if *line* clears the configured minimum level.

    Three cases:

    * ``min_level is None`` — no filter, everything passes.
    * Line matches ``_LOG_LINE_RE`` — compare the parsed level's rank
      against ``min_level``. Unknown levels (a hypothetical
      ``[CUSTOM]`` token) get rank 0 so they pass through when the
      user filters at INFO or above.
    * Line is unparseable (tracebacks, ASCII art, ``print()`` output)
      — drop it. A user who asked for ``--level error`` does not want
      the unstructured noise that doesn't carry a level token; if they
      did, they wouldn't be filtering.
    """
    if min_level is None:
        return True
    m = _LOG_LINE_RE.match(line.rstrip("\n"))
    if not m:
        return False
    level = m.group("level").upper()
    return _LEVEL_RANK.get(level, 0) >= min_level


def _resolve_level_arg(level: str | None) -> int | None:
    """Map a user-supplied ``--level`` string to a stdlib-rank int.

    Returns ``None`` when no level was passed. Raises
    ``typer.BadParameter`` when the user supplied an unknown token —
    surfaces as a clean CLI error rather than a silent no-op.
    """
    if level is None:
        return None
    rank = _LEVEL_RANK.get(level.strip().upper())
    if rank is None:
        accepted = ", ".join(sorted({k.lower() for k in _LEVEL_RANK}))
        raise typer.BadParameter(
            f"unknown log level {level!r}; accepted: {accepted}",
            param_hint="--level",
        )
    return rank


# ── Helpers — pure, testable without spawning the CLI ────────────────


def _component_log_path(component: str) -> Path:
    """Resolve ``<log_dir>/<component>.log`` for the running process."""

    return get_log_dir() / f"{component}.log"


def _format_line(line: str) -> str:
    """Return a Rich-marked-up rendering of ``line``.

    Unparseable lines fall back to ``muted`` so they're visually
    deprioritized but still readable.
    """

    m = _LOG_LINE_RE.match(line.rstrip("\n"))
    if not m:
        return f"[muted]{_escape(line.rstrip())}[/muted]"
    level = m.group("level").upper()
    style = _LEVEL_STYLE.get(level, "muted")
    ts = _escape(m.group("ts"))
    logger_name = _escape(m.group("logger"))
    msg = _escape(m.group("msg"))
    return f"[muted]{ts}[/muted] [{style}][{level:7}][/{style}] [muted]{logger_name}[/muted]: {msg}"


def _escape(s: str) -> str:
    """Escape Rich markup-square-brackets so user content can't inject styles."""

    return s.replace("[", r"\[")


def _tail_lines(path: Path, n: int) -> list[str]:
    """Return the last ``n`` lines of ``path``."""

    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    return lines[-n:] if n > 0 else lines


def _tail_follow(
    path: Path,
    *,
    n_initial: int = 200,
    poll_seconds: float = 0.25,
    min_level: int | None = None,
) -> int:
    """Print the last ``n_initial`` lines then poll for new bytes.

    Exits cleanly (``return 0``) on :class:`KeyboardInterrupt` so SIGINT
    from a user (or a test thread) is the documented "stop watching"
    gesture.  Returns the suggested CLI exit code.

    When *min_level* is set, lines below that severity are dropped —
    both from the initial tail and from the streaming poll. See
    :func:`_passes_level_filter` for the unparseable-line semantics.
    """

    if not path.exists():
        ui_warn(f"{path} does not exist yet")
        return 0

    # Print the existing tail first.
    for line in _tail_lines(path, n_initial):
        if _passes_level_filter(line, min_level):
            ui_console.print(_format_line(line))

    # Open the file fresh and seek to the end so we only stream new bytes.
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            fh.seek(0, 2)  # SEEK_END
            buffer = ""
            while True:
                chunk = fh.read()
                if chunk:
                    buffer += chunk
                    # Split out complete lines (the trailing fragment
                    # without a newline stays in the buffer until the
                    # next write).
                    while "\n" in buffer:
                        line, _, buffer = buffer.partition("\n")
                        if _passes_level_filter(line, min_level):
                            ui_console.print(_format_line(line))
                else:
                    time.sleep(poll_seconds)
    except KeyboardInterrupt:
        return 0


# ── Verb: path ──────────────────────────────────────────────────────


@logs_app.command("path")
def logs_path_cmd() -> None:
    """Print the rotating-log directory (platformdirs cache)."""

    # Data line — go to stdout for piping (``corpus-forge logs path | cd``).
    print(str(get_log_dir()))


# ── Verb: tail ──────────────────────────────────────────────────────


@logs_app.command("tail")
def logs_tail_cmd(
    component: str = typer.Option(
        "cli",
        "--component",
        "-c",
        help="Log component to tail: cli, daemon, mcp, embed-worker.",
    ),
    n: int = typer.Option(
        200,
        "-n",
        "--lines",
        help="Number of trailing lines to print (default 200).",
    ),
    follow: bool = typer.Option(
        False,
        "--follow",
        "-f",
        help="Keep printing new lines as they arrive (250 ms poll).",
    ),
    level: str = typer.Option(
        None,
        "--level",
        help=(
            "Minimum severity to print: debug, info, warn, warning, "
            "error, critical (case-insensitive). Lines without a "
            "parseable level (tracebacks, prints) are suppressed when "
            "this is set."
        ),
    ),
) -> None:
    """Print the last N lines of ``<component>.log``.

    With ``--follow``, the command stays attached and polls every
    250 ms for new bytes.  Send SIGINT (Ctrl+C) to exit cleanly.

    With ``--level <name>``, only lines at or above the named severity
    are printed (in both single-shot and follow modes).
    """

    log_path = _component_log_path(component)
    if not log_path.exists():
        ui_warn(f"{log_path} does not exist yet")
        return

    min_level = _resolve_level_arg(level)

    if follow:
        # Returns 0 on KeyboardInterrupt.
        _tail_follow(log_path, n_initial=n, min_level=min_level)
        return

    for line in _tail_lines(log_path, n):
        if _passes_level_filter(line, min_level):
            ui_console.print(_format_line(line))


# ── Verb: clear ─────────────────────────────────────────────────────


@logs_app.command("clear")
def logs_clear_cmd(
    component: str = typer.Option(
        "cli",
        "--component",
        "-c",
        help="Log component to clear: cli, daemon, mcp, embed-worker.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip the confirmation prompt.",
    ),
) -> None:
    """Truncate the chosen component's rotating log file.

    Without ``--yes`` you'll be asked to confirm.  Truncation is
    in-place — there's no backup; if you wanted history, the
    RotatingFileHandler's numbered ``.1`` / ``.2`` siblings already
    have it.
    """

    log_path = _component_log_path(component)
    if not log_path.exists():
        ui_warn(f"{log_path} does not exist yet — nothing to clear")
        return

    if not yes and not Confirm.ask(f"Clear {log_path.name}?", default=False):
        ui_info("Skipped.")
        return

    log_path.write_text("")
    ui_ok(f"Cleared {log_path}")


__all__ = [
    "_format_line",
    "_tail_follow",
    "_tail_lines",
    "logs_app",
]
