"""Tests for ``corpus_forge.ui.console`` wrappers.

The wrappers (``info``/``ok``/``warn``/``error``/``title``/``panel``) are
the surface the rest of the CLI calls. We assert:

  - each writes its expected glyph + text to a captured Rich console,
  - color/glyph fallback degrades to ASCII when ``NO_COLOR`` is set or
    the console is non-TTY,
  - the agent-mode placeholder returns ``False`` in Wave 1 (Wave 9 will
    swap its body in).
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console


def _make_capture_console(*, no_color: bool = True, force_terminal: bool = False) -> Console:
    """Build a Rich console wired to an ``io.StringIO`` sink for capture."""

    from corpus_forge.ui import theme

    return Console(
        file=io.StringIO(),
        theme=theme.build_theme(),
        force_terminal=force_terminal,
        no_color=no_color,
        width=120,
    )


def test_agent_mode_placeholder_returns_false() -> None:
    import corpus_forge.ui.console as console_mod

    assert console_mod._agent_mode_active() is False


def test_singleton_console_is_rich_console() -> None:
    from rich.console import Console as RichConsole

    import corpus_forge.ui.console as console_mod

    assert isinstance(console_mod.console, RichConsole)


def test_ok_writes_success_glyph_and_text() -> None:
    import corpus_forge.ui.console as console_mod

    sink = _make_capture_console()
    console_mod.ok("ready", console=sink)
    out = sink.file.getvalue()
    # Plain-mode glyph is [OK]; the message must follow.
    assert "[OK]" in out or "✓" in out
    assert "ready" in out


def test_warn_writes_warn_glyph_and_text() -> None:
    import corpus_forge.ui.console as console_mod

    sink = _make_capture_console()
    console_mod.warn("careful", console=sink)
    out = sink.file.getvalue()
    assert "[WARN]" in out or "⚠" in out
    assert "careful" in out


def test_error_writes_error_glyph_and_text() -> None:
    import corpus_forge.ui.console as console_mod

    sink = _make_capture_console()
    console_mod.error("broken", console=sink)
    out = sink.file.getvalue()
    assert "[ERR]" in out or "✗" in out
    assert "broken" in out


def test_info_writes_info_glyph_and_text() -> None:
    import corpus_forge.ui.console as console_mod

    sink = _make_capture_console()
    console_mod.info("loading", console=sink)
    out = sink.file.getvalue()
    assert "->" in out or "→" in out
    assert "loading" in out


def test_title_writes_text_in_h1_style() -> None:
    import corpus_forge.ui.console as console_mod

    sink = _make_capture_console()
    console_mod.title("Setup", console=sink)
    assert "Setup" in sink.file.getvalue()


def test_panel_writes_message_and_optional_title() -> None:
    import corpus_forge.ui.console as console_mod

    sink = _make_capture_console()
    console_mod.panel("hello", title="greet", console=sink)
    out = sink.file.getvalue()
    assert "hello" in out
    assert "greet" in out


def test_no_color_env_forces_ascii_glyphs(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ``NO_COLOR=1`` the helpers must emit ASCII glyphs."""

    monkeypatch.setenv("NO_COLOR", "1")
    import corpus_forge.ui.console as console_mod

    sink = _make_capture_console(no_color=True, force_terminal=False)
    console_mod.ok("done", console=sink)
    out = sink.file.getvalue()
    assert "[OK]" in out
    assert "✓" not in out


def test_non_tty_falls_back_to_plain_glyphs() -> None:
    """Piping output (``console.is_terminal == False``) must drop the
    fancy Unicode glyphs even when ``NO_COLOR`` isn't set."""

    import corpus_forge.ui.console as console_mod

    sink = _make_capture_console(no_color=False, force_terminal=False)
    assert sink.is_terminal is False
    console_mod.warn("hmm", console=sink)
    out = sink.file.getvalue()
    assert "[WARN]" in out


def test_wrappers_accept_string_safely() -> None:
    """Smoke: each helper accepts a plain string and writes something."""

    import corpus_forge.ui.console as console_mod

    sink = _make_capture_console()
    for fn in (console_mod.info, console_mod.ok, console_mod.warn, console_mod.error):
        fn("msg", console=sink)
    assert "msg" in sink.file.getvalue()
