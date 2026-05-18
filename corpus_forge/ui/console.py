"""Singleton ``rich.console.Console`` + thin status-line wrappers.

The wrappers (``info``/``ok``/``warn``/``error``/``title``/``panel``)
are what the rest of the CLI calls — they own the glyph fallback rule
and the (Wave 9-pending) agent-mode placeholder.
"""

from __future__ import annotations

import os

from rich.console import Console
from rich.panel import Panel

from . import theme as _theme


def _agent_mode_active() -> bool:
    """Placeholder for the Wave 9 agent-mode detector.

    Wave 9 replaces the body to consult ``ui/agent.py``.  Wave 1 keeps
    it as a stable hook so the wrappers don't change shape later.
    """

    return False


# The singleton console.  We pin ``stderr=True`` so status lines and
# Rich tracebacks never collide with command stdout (search hits, JSON
# emission, etc.).  Commands that want stdout output should call
# ``console.print(...)`` with their own ``file=`` if needed.
console: Console = Console(
    theme=_theme.build_theme(),
    stderr=True,
    highlight=False,
)


def _is_plain(target: Console) -> bool:
    """Should we emit ASCII glyphs / suppress style for ``target``?"""

    if "NO_COLOR" in os.environ:
        return True
    if not target.is_terminal:
        return True
    return bool(getattr(target, "no_color", False))


def _resolve(target: Console | None) -> Console:
    return target if target is not None else console


def info(message: str, *, console: Console | None = None) -> None:
    """Print an informational status line (cyan ``→``)."""

    if _agent_mode_active():
        return
    target = _resolve(console)
    glyph = _theme.glyph_for("info", plain=_is_plain(target))
    target.print(f"[info]{glyph}[/info] {message}")


def ok(message: str, *, console: Console | None = None) -> None:
    """Print a success status line (green ``✓``)."""

    if _agent_mode_active():
        return
    target = _resolve(console)
    glyph = _theme.glyph_for("success", plain=_is_plain(target))
    target.print(f"[success]{glyph}[/success] {message}")


def warn(message: str, *, console: Console | None = None) -> None:
    """Print a warning status line (yellow ``⚠``)."""

    if _agent_mode_active():
        return
    target = _resolve(console)
    glyph = _theme.glyph_for("warn", plain=_is_plain(target))
    target.print(f"[warn]{glyph}[/warn] {message}")


def error(message: str, *, console: Console | None = None) -> None:
    """Print an error status line (red ``✗``)."""

    if _agent_mode_active():
        return
    target = _resolve(console)
    glyph = _theme.glyph_for("error", plain=_is_plain(target))
    target.print(f"[error]{glyph}[/error] {message}")


def title(text: str, *, console: Console | None = None) -> None:
    """Print a top-level section title rendered in the ``h1`` style."""

    if _agent_mode_active():
        return
    target = _resolve(console)
    target.print(f"[h1]{text}[/h1]")


def panel(message: str, *, title: str | None = None, console: Console | None = None) -> None:
    """Render ``message`` inside a Rich panel with the brand border."""

    if _agent_mode_active():
        return
    target = _resolve(console)
    target.print(Panel(message, title=title, border_style="brand.ember"))


__all__ = [
    "console",
    "error",
    "info",
    "ok",
    "panel",
    "title",
    "warn",
]
