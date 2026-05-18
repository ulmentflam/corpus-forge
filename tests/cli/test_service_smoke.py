"""CLI smoke for the Wave 8 ``service`` group (Phase L Wave 8).

Verifies that every verb under ``corpus-forge service`` is wired through
the root Typer app and that ``--help`` returns 0 for the group and for
each individual verb.  Verb behaviour itself is covered in
``tests/admin/test_service_*.py``.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_service_group_help_succeeds(runner: CliRunner) -> None:
    from corpus_forge.cli import app

    result = runner.invoke(app, ["service", "--help"])
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize(
    "verb",
    ["status", "start", "stop", "restart", "logs", "install", "uninstall"],
)
def test_service_verb_help(runner: CliRunner, verb: str) -> None:
    from corpus_forge.cli import app

    result = runner.invoke(app, ["service", verb, "--help"])
    assert result.exit_code == 0, result.output


def test_root_help_lists_service(runner: CliRunner) -> None:
    from corpus_forge.cli import app

    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "service" in result.output


def test_service_help_lists_every_verb(runner: CliRunner) -> None:
    from corpus_forge.cli import app

    result = runner.invoke(app, ["service", "--help"])
    assert result.exit_code == 0
    for verb in ("status", "start", "stop", "restart", "logs", "install", "uninstall"):
        assert verb in result.output, f"missing verb {verb!r}"


def test_daemon_command_still_registered(runner: CliRunner) -> None:
    """Backwards-compat: bare ``daemon`` must still be reachable for one release."""

    from corpus_forge.cli import app

    # Typer leaves ``name=None`` when ``@app.command()`` is used without an
    # explicit name and infers the verb from the callback's ``__name__``.
    callback_names = {
        cmd.callback.__name__ for cmd in app.registered_commands if cmd.callback is not None
    }
    assert "daemon" in callback_names


def test_daemon_invocation_succeeds_with_deprecation(runner: CliRunner) -> None:
    """Invoking ``corpus-forge daemon --help`` still works through the alias."""

    from corpus_forge.cli import app

    result = runner.invoke(app, ["daemon", "--help"])
    assert result.exit_code == 0, result.output
