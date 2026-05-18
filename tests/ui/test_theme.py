"""Tests for ``corpus_forge.ui.theme``.

The theme module owns the locked brand palette + semantic-state style
table for the entire CLI. These tests pin the user-visible contract:

  - palette resolves to the documented hex values,
  - ``NO_COLOR=1`` (or ``--no-color``) collapses glyphs to ASCII
    (``[OK]`` / ``[WARN]`` / ``[ERR]`` / ``->``), AND
  - ``--light`` swaps ``brand.ember`` and ``brand.forge`` so the deep
    accent is the primary on light terminals.
"""

from __future__ import annotations

import pytest


def test_theme_module_importable() -> None:
    from corpus_forge.ui import theme  # noqa: F401


def test_theme_returns_rich_theme_instance() -> None:
    from rich.theme import Theme

    from corpus_forge.ui import theme

    built = theme.build_theme()
    assert isinstance(built, Theme)


def test_brand_ember_hex_value() -> None:
    """The primary brand color is locked to ember orange ``#ff8a3d``."""

    from corpus_forge.ui import theme

    # Use the canonical accessor so the fixture survives a future
    # refactor that splits the theme into smaller modules.
    style = theme.style_for("brand.ember")
    assert "#ff8a3d" in style.lower()


def test_brand_forge_hex_value() -> None:
    """The deep accent is locked to ``#b83205``."""

    from corpus_forge.ui import theme

    style = theme.style_for("brand.forge")
    assert "#b83205" in style.lower()


def test_state_colors_are_ansi_named() -> None:
    """Semantic state colors are theme-deferred (ANSI named) so they
    inherit the user's terminal palette."""

    from corpus_forge.ui import theme

    assert "cyan" in theme.style_for("info")
    assert "green" in theme.style_for("success")
    assert "yellow" in theme.style_for("warn")
    # error is bold red — assert both tokens land in the style string.
    err = theme.style_for("error")
    assert "red" in err and "bold" in err


@pytest.mark.parametrize(
    ("level", "ascii_glyph"),
    [
        ("info", "->"),
        ("success", "[OK]"),
        ("warn", "[WARN]"),
        ("error", "[ERR]"),
    ],
)
def test_no_color_returns_ascii_glyphs(
    monkeypatch: pytest.MonkeyPatch,
    level: str,
    ascii_glyph: str,
) -> None:
    """When ``NO_COLOR`` is set we must NOT emit non-ASCII glyphs."""

    monkeypatch.setenv("NO_COLOR", "1")
    from corpus_forge.ui import theme

    assert theme.glyph_for(level, plain=True) == ascii_glyph


@pytest.mark.parametrize(
    ("level", "unicode_glyph"),
    [
        ("info", "→"),
        ("success", "✓"),
        ("warn", "⚠"),
        ("error", "✗"),
    ],
)
def test_color_terminal_returns_unicode_glyphs(level: str, unicode_glyph: str) -> None:
    """In color mode we render the polished Unicode marks."""

    from corpus_forge.ui import theme

    assert theme.glyph_for(level, plain=False) == unicode_glyph


def test_light_swap_inverts_ember_and_forge() -> None:
    """``--light`` swaps ``brand.ember`` ↔ ``brand.forge`` so the high-
    contrast deep ember is the primary on a light terminal."""

    from corpus_forge.ui import theme

    dark = theme.build_theme(light=False)
    light = theme.build_theme(light=True)

    # Pull the rendered style strings from the underlying registry.
    dark_ember = str(dark.styles["brand.ember"])
    dark_forge = str(dark.styles["brand.forge"])
    light_ember = str(light.styles["brand.ember"])
    light_forge = str(light.styles["brand.forge"])

    assert dark_ember != light_ember
    assert light_ember == dark_forge
    assert light_forge == dark_ember


def test_theme_includes_all_required_style_keys() -> None:
    """Every key the UI module + log handler reference must be present."""

    from corpus_forge.ui import theme

    built = theme.build_theme()
    expected = {
        "brand.ember",
        "brand.forge",
        "h1",
        "h2",
        "info",
        "success",
        "warn",
        "error",
        "muted",
        "accent.path",
        "accent.number",
        "prompt.glyph",
    }
    missing = expected - set(built.styles.keys())
    assert not missing, f"missing theme keys: {sorted(missing)}"
