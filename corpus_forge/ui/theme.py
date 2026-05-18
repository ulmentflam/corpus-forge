"""Locked brand palette + semantic-state style table for the CLI UI.

The brand palette comes from ``assets/logo.svg`` (ember orange
``#ff8a3d`` + deep ember ``#b83205``).  State colors are ANSI named so
they adapt to whichever terminal theme the user runs.

See ``.planning/tdd/phase_l_cli_ux.md`` § 1 for the design.
"""

from __future__ import annotations

import os

from rich.theme import Theme

# Brand truecolor anchors.  Kept module-level constants so call sites
# (banner, panel, progress) can refer to them by name without round-
# tripping through a Rich Style.
BRAND_EMBER = "#ff8a3d"
BRAND_FORGE = "#b83205"


def _style_table(*, light: bool) -> dict[str, str]:
    """Build the raw ``Rich`` style-table for the active palette.

    ``light=True`` swaps ``brand.ember`` ↔ ``brand.forge`` so the
    high-contrast deep ember is the primary on light terminals.
    """

    ember = BRAND_FORGE if light else BRAND_EMBER
    forge = BRAND_EMBER if light else BRAND_FORGE
    return {
        "brand.ember": f"bold {ember}",
        "brand.forge": f"bold {forge}",
        "h1": f"bold {ember}",
        "h2": "bold",
        "info": "cyan",
        "success": "green",
        "warn": "yellow",
        "error": "bold red",
        "muted": "dim",
        "accent.path": "cyan",
        "accent.number": "bold cyan",
        "prompt.glyph": f"bold {ember}",
    }


def build_theme(*, light: bool = False) -> Theme:
    """Return a fresh ``rich.theme.Theme`` keyed off the locked palette.

    Call sites should prefer this factory over a module-level constant
    so the ``--light`` flag is honored without re-import gymnastics.
    """

    return Theme(_style_table(light=light))


# A default theme convenient for cases that don't need light-mode swap.
THEME: Theme = build_theme()


def style_for(name: str, *, light: bool = False) -> str:
    """Return the raw style string for a registered style name."""

    table = _style_table(light=light)
    if name not in table:
        raise KeyError(f"unknown theme style: {name!r}")
    return table[name]


# Glyph table — Unicode for color terminals, ASCII fallback when
# ``NO_COLOR`` is set / the console is non-TTY / ``--no-color`` flag is
# active.  ``glyph_for`` consumers should pass ``plain=True`` whenever
# any of those signals are present.
_GLYPHS: dict[str, dict[str, str]] = {
    "info": {"unicode": "→", "ascii": "->"},
    "success": {"unicode": "✓", "ascii": "[OK]"},
    "warn": {"unicode": "⚠", "ascii": "[WARN]"},
    "error": {"unicode": "✗", "ascii": "[ERR]"},
    "prompt": {"unicode": "❯", "ascii": ">"},  # noqa: RUF001 — brand chevron is intentional
}


def glyph_for(level: str, *, plain: bool | None = None) -> str:
    """Return the glyph to emit for a status ``level``.

    ``plain=None`` (default) consults ``NO_COLOR`` from the environment.
    Pass ``plain=True`` / ``plain=False`` explicitly when the caller has
    a richer signal (e.g. a non-TTY ``rich.console.Console``).
    """

    if level not in _GLYPHS:
        raise KeyError(f"unknown glyph level: {level!r}")
    if plain is None:
        plain = "NO_COLOR" in os.environ
    bucket = _GLYPHS[level]
    return bucket["ascii"] if plain else bucket["unicode"]


__all__ = [
    "BRAND_EMBER",
    "BRAND_FORGE",
    "THEME",
    "build_theme",
    "glyph_for",
    "style_for",
]
