"""Tests for the new global Typer flags wired in Phase L Wave 1.

The ``@app.callback`` accepts the following new options:

  - ``--verbose``/``-v`` (count int, 0..2)
  - ``--quiet``/``-q``
  - ``--no-color``
  - ``--light``
  - ``--background``/``-b``
  - ``--agent <auto|off|claude-code|...>`` (default ``auto``)

The callback must:

  1. install logging via ``init_logging('cli', verbose=verbose>=1,
     quiet=quiet)``,
  2. stash a context-state dataclass into ``ctx.obj`` for downstream
     command bodies to read.

Existing commands (``version``, etc.) must still execute without
crashing under the new flag surface.
"""

from __future__ import annotations

import typer
from typer.testing import CliRunner


def _runner() -> CliRunner:
    return CliRunner()


def test_version_command_works_with_no_global_flags() -> None:
    from corpus_forge.cli import app

    runner = _runner()
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0, result.output


def test_global_verbose_flag_accepted() -> None:
    from corpus_forge.cli import app

    runner = _runner()
    result = runner.invoke(app, ["--verbose", "version"])
    assert result.exit_code == 0, result.output


def test_global_short_v_flag_accepted() -> None:
    from corpus_forge.cli import app

    runner = _runner()
    result = runner.invoke(app, ["-v", "version"])
    assert result.exit_code == 0, result.output


def test_global_quiet_flag_accepted() -> None:
    from corpus_forge.cli import app

    runner = _runner()
    result = runner.invoke(app, ["--quiet", "version"])
    assert result.exit_code == 0, result.output


def test_global_no_color_flag_accepted() -> None:
    from corpus_forge.cli import app

    runner = _runner()
    result = runner.invoke(app, ["--no-color", "version"])
    assert result.exit_code == 0, result.output


def test_global_light_flag_accepted() -> None:
    from corpus_forge.cli import app

    runner = _runner()
    result = runner.invoke(app, ["--light", "version"])
    assert result.exit_code == 0, result.output


def test_global_background_flag_accepted() -> None:
    from corpus_forge.cli import app

    runner = _runner()
    result = runner.invoke(app, ["--background", "version"])
    assert result.exit_code == 0, result.output


def test_global_agent_flag_accepts_string() -> None:
    from corpus_forge.cli import app

    runner = _runner()
    result = runner.invoke(app, ["--agent", "off", "version"])
    assert result.exit_code == 0, result.output


def test_global_state_stashed_in_context_obj(monkeypatch) -> None:
    """A spying command should see ``ctx.obj`` populated with our state."""

    from corpus_forge import cli as cli_mod

    captured: dict[str, object] = {}

    @cli_mod.app.command("_probe_global_state")
    def _probe(ctx: typer.Context) -> None:
        captured["state"] = ctx.obj

    runner = _runner()
    result = runner.invoke(
        cli_mod.app,
        [
            "--verbose",
            "--quiet",
            "--no-color",
            "--light",
            "--background",
            "--agent",
            "auto",
            "_probe_global_state",
        ],
    )
    assert result.exit_code == 0, result.output
    state = captured.get("state")
    assert state is not None, "global callback did not stash ctx.obj"
    # Field surface — accessed as attributes on a dataclass-like object.
    assert state.verbose >= 1
    assert state.quiet is True
    assert state.no_color is True
    assert state.light is True
    assert state.background is True
    assert state.agent == "auto"


def test_global_callback_calls_init_logging(monkeypatch) -> None:
    """The callback must invoke ``init_logging('cli', ...)`` so commands
    get a configured logger before they run."""

    from corpus_forge import logging_config
    from corpus_forge.cli import app

    calls: list[dict[str, object]] = []

    original = logging_config.init_logging

    def _spy(component: str, **kwargs: object) -> None:
        calls.append({"component": component, **kwargs})
        return original(component, **kwargs)

    monkeypatch.setattr(logging_config, "init_logging", _spy)

    # Also patch the symbol re-imported inside corpus_forge.cli.
    import corpus_forge.cli as cli_mod

    if hasattr(cli_mod, "init_logging"):
        monkeypatch.setattr(cli_mod, "init_logging", _spy)

    runner = _runner()
    result = runner.invoke(app, ["--verbose", "version"])
    assert result.exit_code == 0, result.output

    assert any(c["component"] == "cli" for c in calls), calls
    # When --verbose is on, verbose=True must be forwarded.
    matching = [c for c in calls if c["component"] == "cli"]
    assert any(c.get("verbose") is True for c in matching), matching
