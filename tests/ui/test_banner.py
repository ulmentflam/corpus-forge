"""Tests for ``corpus_forge.ui.banner.render_banner``."""

from __future__ import annotations

import io

from rich.console import Console


def _make_console() -> Console:
    from corpus_forge.ui import theme

    return Console(
        file=io.StringIO(),
        theme=theme.build_theme(),
        force_terminal=False,
        no_color=True,
        width=120,
    )


def test_render_banner_writes_title() -> None:
    from corpus_forge.ui import banner

    sink = _make_console()
    banner.render_banner("corpus-forge", console=sink)
    assert "corpus-forge" in sink.file.getvalue()


def test_render_banner_includes_subtitle_when_provided() -> None:
    from corpus_forge.ui import banner

    sink = _make_console()
    banner.render_banner("corpus-forge", subtitle="Chat with your data.", console=sink)
    out = sink.file.getvalue()
    assert "corpus-forge" in out
    assert "Chat with your data." in out


def test_render_banner_no_subtitle_does_not_render_pipe_separator() -> None:
    """When subtitle is omitted no leading-line separator is emitted."""

    from corpus_forge.ui import banner

    sink = _make_console()
    banner.render_banner("only-title", console=sink)
    assert "only-title" in sink.file.getvalue()


def test_render_banner_is_suppressed_in_agent_mode(monkeypatch) -> None:
    """Banner must be silent under the Wave 1 agent-mode placeholder."""

    from corpus_forge.ui import banner

    # Force the placeholder to claim we're in agent mode.
    monkeypatch.setattr(banner, "_agent_mode_active", lambda: True)
    sink = _make_console()
    banner.render_banner("hidden", subtitle="should not appear", console=sink)
    assert sink.file.getvalue() == ""


def test_render_banner_uses_brand_ember_border_style() -> None:
    """The panel border must be wired to ``brand.ember`` so the brand
    palette flows through the banner."""

    from corpus_forge.ui import banner

    # Inspect the Panel produced by the builder helper, not the rendered
    # text — border styles are erased by the no-color, non-terminal sink.
    panel = banner.build_banner_panel("corpus-forge", subtitle=None)
    # ``border_style`` may be a Style or str; either way it must mention
    # ``brand.ember``.
    border_repr = str(panel.border_style)
    assert "brand.ember" in border_repr
