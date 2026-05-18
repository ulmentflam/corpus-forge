"""Rounded-box banner rendered at the top of ``setup`` / ``doctor``.

In Wave 1 the banner is opt-in: only the call sites that explicitly
invoke ``render_banner`` see it.  Wave 9 will suppress the banner when
agent mode is active (the placeholder hook is already wired below).
"""

from __future__ import annotations

from rich import box
from rich.console import Console
from rich.panel import Panel

from .console import _agent_mode_active
from .console import console as _default_console


def build_banner_panel(title: str, *, subtitle: str | None = None) -> Panel:
    """Return the Rich ``Panel`` that ``render_banner`` would print."""

    body = f"[h1]{title}[/h1]\n[muted]{subtitle}[/muted]" if subtitle else f"[h1]{title}[/h1]"
    return Panel(
        body,
        box=box.ROUNDED,
        border_style="brand.ember",
        padding=(0, 2),
    )


def render_banner(
    title: str,
    subtitle: str | None = None,
    *,
    console: Console | None = None,
) -> None:
    """Print the corpus-forge banner.

    Suppressed under the Wave 9 agent-mode placeholder so agent-driven
    invocations don't pay tokens for ASCII art.
    """

    if _agent_mode_active():
        return
    target = console if console is not None else _default_console
    target.print(build_banner_panel(title, subtitle=subtitle))


__all__ = ["build_banner_panel", "render_banner"]
