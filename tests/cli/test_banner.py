"""Phase L Wave 3 — banner rendering on ``setup`` and ``doctor``.

The rounded-box banner (``render_banner("corpus-forge", subtitle="Chat
with your data.")``) is shown:
- At the top of ``corpus-forge doctor`` (always, in human-render mode).
- At the top of ``corpus-forge setup`` (any mode EXCEPT
  ``--non-interactive``, which is meant to be silent and machine-driven).

Other commands stay banner-free so scripts that pipe their output don't
have to filter ASCII art.

The conftest sets ``NO_COLOR=1`` and ``TERM=dumb`` and strips ANSI from
``CliRunner.Result.output``. Under that env Rich's ``box.ROUNDED`` still
emits box-drawing chars *but* the legacy-Windows / dumb-term fallback
may degrade them to ASCII. The reliable signal in either case is the
banner's text content (``"corpus-forge"`` + the subtitle string), so we
assert on the substring.
"""

from __future__ import annotations

import io
from pathlib import Path

from typer.testing import CliRunner

from corpus_forge.cli import app

_BANNER_SUBTITLE = "Chat with your data."


def _runner() -> CliRunner:
    return CliRunner()


def _combined(result) -> str:
    """Return stdout + stderr (newer Click separates them)."""
    parts: list[str] = []
    if result.stdout:
        parts.append(result.stdout)
    try:
        if result.stderr:
            parts.append(result.stderr)
    except (AttributeError, ValueError):
        pass
    return "".join(parts) or result.output


# ── doctor renders the banner by default ──────────────────────────────


def test_doctor_renders_banner_by_default() -> None:
    """``corpus-forge doctor`` (no ``--json``) renders the banner."""

    result = _runner().invoke(app, ["doctor"])
    # Exit code may be 0 or 1 depending on local system deps; only the
    # banner content matters here.
    out = _combined(result)
    assert _BANNER_SUBTITLE in out, f"banner subtitle missing from doctor output:\n{out}"
    assert "corpus-forge" in out


# ── setup --non-interactive is banner-free ────────────────────────────


def test_setup_non_interactive_does_not_render_banner(tmp_path: Path) -> None:
    """The silent non-interactive path must NOT render the banner."""

    config_dir = tmp_path / "cf"
    result = _runner().invoke(
        app,
        ["setup", "--non-interactive", "--config-dir", str(config_dir)],
    )
    assert result.exit_code == 0, result.output
    out = _combined(result)
    assert _BANNER_SUBTITLE not in out, f"banner leaked into --non-interactive setup:\n{out}"


def test_setup_quick_non_interactive_does_not_render_banner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """``--quick --non-interactive`` is also silent — no banner."""

    monkeypatch.setenv("CF_BACKEND", "sqlite")
    config_dir = tmp_path / "cf"
    result = _runner().invoke(
        app,
        ["setup", "--quick", "--non-interactive", "--config-dir", str(config_dir)],
    )
    assert result.exit_code == 0, result.output
    out = _combined(result)
    assert _BANNER_SUBTITLE not in out, (
        f"banner leaked into --quick --non-interactive setup:\n{out}"
    )


# ── setup --quick interactive renders the banner ──────────────────────


def test_setup_quick_interactive_renders_banner(tmp_path: Path) -> None:
    """Interactive ``--quick`` shows the banner at the top.

    Drive via the CLI runner with stdin fed with default-accepting
    blank lines. The banner is rendered by the CLI wrapper (not by
    ``run_quick`` itself), so we go through Typer.
    """

    config_dir = tmp_path / "cf"
    # Feed enough blank lines to default through every quick prompt.
    result = _runner().invoke(
        app,
        ["setup", "--quick", "--config-dir", str(config_dir)],
        input="\n" * 20,
    )
    # Banner subtitle must appear regardless of whether prompts completed.
    out = _combined(result)
    assert _BANNER_SUBTITLE in out, f"banner missing from --quick interactive setup:\n{out}"


# ── direct call to render_banner is the building block ───────────────


def test_render_banner_writes_subtitle_text() -> None:
    """``ui.render_banner`` prints the title + subtitle into the given
    console. Smoke test the helper itself so the banner-on-CLI tests
    above can rely on it producing the subtitle substring.
    """

    from rich.console import Console

    from corpus_forge.ui.banner import render_banner
    from corpus_forge.ui.theme import build_theme

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=80, no_color=True, theme=build_theme())
    render_banner("corpus-forge", subtitle=_BANNER_SUBTITLE, console=console)
    output = buf.getvalue()
    assert "corpus-forge" in output
    assert _BANNER_SUBTITLE in output
