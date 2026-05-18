"""Phase L Wave 9 — interactive prompts under agent mode hard-fail.

Per ``.planning/tdd/phase_l_cli_ux.md`` §12, ``ui.prompts.Prompt.ask``
and ``ui.prompts.Confirm.ask`` MUST raise :class:`ui.agent.RequiresInteractiveError`
when called inside agent mode.  The CLI global error handler translates
the exception into a structured ``{"event":"error","kind":"requires_interactive"}``
event and a process exit code of 2.
"""

from __future__ import annotations

import json
import re

import pytest
from typer.testing import CliRunner

from corpus_forge.cli import app
from corpus_forge.ui.agent import (
    AgentClient,
    Detection,
    RequiresInteractiveError,
    current_detection,
    set_current,
)
from corpus_forge.ui.prompts import Confirm, Prompt

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


@pytest.fixture
def agent_mode_on() -> None:
    original = current_detection()
    set_current(Detection(client=AgentClient.GENERIC, signal="test", raw_value="x"))
    try:
        yield
    finally:
        set_current(original)


def test_prompt_ask_raises_under_agent_mode(agent_mode_on: None) -> None:
    with pytest.raises(RequiresInteractiveError):
        Prompt.ask("What's your name?")


def test_confirm_ask_raises_under_agent_mode(agent_mode_on: None) -> None:
    with pytest.raises(RequiresInteractiveError):
        Confirm.ask("Continue?")


def test_embedder_add_under_agent_mode_emits_requires_interactive() -> None:
    """``corpus-forge embedder add`` uses ``Prompt.ask`` — under agent mode
    that raises and the CLI emits a structured error event with exit 2.
    """

    try:
        runner = CliRunner(mix_stderr=False)  # type: ignore[call-arg]
    except TypeError:
        runner = CliRunner()
    result = runner.invoke(
        app,
        ["embedder", "add", "my-embedder"],
        env={"CF_AGENT": "generic", "NO_COLOR": "1"},
    )
    assert result.exit_code == 2, result.stdout
    assert not _ANSI_RE.search(result.stdout)
    events: list[dict] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        events.append(json.loads(line))
    errors = [e for e in events if e["event"] == "error"]
    assert errors, events
    assert any(e.get("kind") == "requires_interactive" for e in errors), errors
