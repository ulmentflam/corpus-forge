"""Phase L Wave 2 — smoke tests for the themed CLI output.

The Wave-1 ``ui`` package + Wave-2 routing pass mean that *every*
user-visible status line in ``corpus_forge/cli.py`` flows through
``ui.ok`` / ``ui.warn`` / ``ui.error`` / ``ui.info`` (stderr-themed) or
plain ``print()`` (stdout, for data lines).  These three smoke tests
pin the contract from the outside:

1. ``corpus-forge version`` still emits the version line cleanly.
2. ``corpus-forge setup --non-interactive`` emits an ``[OK]``-styled
   completion line (the literal ``[OK]`` falls out under ``NO_COLOR=1``
   because the ``ui.ok`` wrapper degrades the green glyph to ASCII —
   see ``ui/console._is_plain``).
3. ``corpus-forge doctor`` outputs at least one status pill with a
   literal status token (``OK``, ``WARN``, ``FAIL`` or ``SKIP``) — the
   styled-render path keeps the existing pill shape so substring
   assertions survive the ANSI-strip applied by ``tests/conftest.py``.

The conftest already sets ``NO_COLOR=1`` and patches
``click.testing.Result.output`` to strip ANSI, so each assertion below
binds against the user-visible text.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from corpus_forge import __version__
from corpus_forge.cli import app


def test_version_command_emits_version_line() -> None:
    """``corpus-forge version`` succeeds and prints the package version.

    The actual route is bare ``print()`` (data line on stdout for
    piping), but the test asserts the *substring* — that contract is
    stable across human / agent modes.
    """
    runner = CliRunner()
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0, result.output
    assert "corpus-forge version" in result.output
    assert __version__ in result.output


def test_setup_non_interactive_emits_ok_pill(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """``setup --non-interactive`` emits an ``[OK]`` line at completion.

    Phase L Wave 2 routes the wizard's status output through
    ``ui.ok`` — under ``NO_COLOR=1`` the green ``✓`` glyph degrades to
    the literal ASCII pill ``[OK]``, so a substring search still binds
    the contract regardless of theme.
    """
    # Belt-and-braces: conftest already sets NO_COLOR=1 globally, but
    # `monkeypatch.setenv` makes the contract explicit at the call site.
    monkeypatch.setenv("NO_COLOR", "1")

    runner = CliRunner()
    config_dir = tmp_path / "cf"
    result = runner.invoke(
        app,
        ["setup", "--non-interactive", "--config-dir", str(config_dir)],
    )
    assert result.exit_code == 0, result.output
    assert "[OK]" in result.output, (
        f"setup completion must emit an [OK]-styled line; got:\n{result.output}"
    )
    assert "Wrote" in result.output, (
        f"setup must announce the rendered config path; got:\n{result.output}"
    )


def test_doctor_emits_styled_status_pills() -> None:
    """``corpus-forge doctor`` prints at least one status-pill token.

    The styled render preserves the existing ``[OK  ]`` / ``[WARN]`` /
    ``[FAIL]`` / ``[SKIP]`` shape so substring assertions still bind
    under the ANSI-strip applied by conftest.  We accept any one of
    the four literal status tokens — the test environment may
    legitimately produce WARN (no system ffmpeg, missing config, etc.).

    Exit code is intentionally not asserted: a healthy lab machine
    yields 0, a stripped-down CI runner may yield 1 because system
    deps are missing.  Either way the pills must be present.
    """
    runner = CliRunner()
    result = runner.invoke(app, ["doctor"])
    output = result.output
    assert any(token in output for token in ("OK", "WARN", "FAIL", "SKIP")), (
        f"doctor output must contain at least one status pill; got:\n{output}"
    )
