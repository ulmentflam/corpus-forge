"""Coverage targeting :mod:`corpus_forge.ui.agent` — the ``cmd_wrap``
decorator + edge cases for ProgressEmitter not covered by
``test_agent_detect.py`` / ``test_agent_emit.py``.
"""

from __future__ import annotations

import json

import pytest

from corpus_forge.ui import agent as agent_mod


def _lines(text: str) -> list[str]:
    return [line for line in text.split("\n") if line]


@pytest.fixture(autouse=True)
def restore_detection():
    """Restore the module-level detection slot after each test."""

    original = agent_mod.current_detection()
    yield
    agent_mod.set_current(original)


# ── cmd_wrap: zero overhead when human ─────────────────────────────────


def test_cmd_wrap_passthrough_when_human(capsys: pytest.CaptureFixture) -> None:
    """In HUMAN mode, the decorator just delegates without emitting JSONL."""

    agent_mod.set_current(
        agent_mod.Detection(client=agent_mod.AgentClient.HUMAN, signal="", raw_value="")
    )

    @agent_mod.cmd_wrap("test-cmd")
    def _body(x: int) -> int:
        return x + 1

    assert _body(2) == 3
    captured = capsys.readouterr()
    # No JSONL emitted in HUMAN mode.
    assert captured.out == ""


# ── cmd_wrap: emits start + result when agent ──────────────────────────


def test_cmd_wrap_emits_command_start(capsys: pytest.CaptureFixture) -> None:
    agent_mod.set_current(
        agent_mod.Detection(
            client=agent_mod.AgentClient.CLAUDE_CODE,
            signal="CLAUDECODE",
            raw_value="1",
        )
    )

    @agent_mod.cmd_wrap("greet")
    def _body(name: str = "world") -> str:
        return f"hi {name}"

    out = _body(name="claude")
    assert out == "hi claude"
    captured = capsys.readouterr()
    lines = [json.loads(line) for line in _lines(captured.out)]
    # First event must be command.start with the cmd name.
    starts = [ev for ev in lines if ev["event"] == "command.start"]
    assert len(starts) == 1
    assert starts[0]["cmd"] == "greet"
    assert starts[0]["agent"] == "claude-code"


def test_cmd_wrap_passes_args_through(capsys: pytest.CaptureFixture) -> None:
    agent_mod.set_current(
        agent_mod.Detection(
            client=agent_mod.AgentClient.GENERIC,
            signal="test",
            raw_value="1",
        )
    )

    @agent_mod.cmd_wrap("compute")
    def _body(*, x: int, y: int) -> int:
        return x + y

    assert _body(x=3, y=4) == 7


# ── cmd_wrap: RequiresInteractiveError → exit code 2 ───────────────────


def test_cmd_wrap_requires_interactive_emits_error_and_raises(
    capsys: pytest.CaptureFixture,
) -> None:
    import typer

    agent_mod.set_current(
        agent_mod.Detection(
            client=agent_mod.AgentClient.CLAUDE_CODE,
            signal="CLAUDECODE",
            raw_value="1",
        )
    )

    @agent_mod.cmd_wrap("setup")
    def _body() -> None:
        raise agent_mod.RequiresInteractiveError(cmd="setup", prompt="username")

    with pytest.raises(typer.Exit) as exc_info:
        _body()
    assert exc_info.value.exit_code == 2

    captured = capsys.readouterr()
    events = [json.loads(line) for line in _lines(captured.out)]
    error_events = [e for e in events if e["event"] == "error"]
    assert len(error_events) == 1
    assert error_events[0]["kind"] == "requires_interactive"


# ── cmd_wrap: arbitrary exception → emit + re-raise ────────────────────


def test_cmd_wrap_generic_exception_emits_error_event(
    capsys: pytest.CaptureFixture,
) -> None:
    agent_mod.set_current(
        agent_mod.Detection(
            client=agent_mod.AgentClient.GENERIC,
            signal="test",
            raw_value="x",
        )
    )

    @agent_mod.cmd_wrap("crash")
    def _body() -> None:
        raise ValueError("kaboom")

    with pytest.raises(ValueError):
        _body()

    captured = capsys.readouterr()
    events = [json.loads(line) for line in _lines(captured.out)]
    error_events = [e for e in events if e["event"] == "error"]
    assert len(error_events) == 1
    assert error_events[0]["kind"] == "ValueError"
    assert "kaboom" in error_events[0]["msg"]


# ── cmd_wrap: SystemExit propagates untouched ──────────────────────────


def test_cmd_wrap_systemexit_propagates(capsys: pytest.CaptureFixture) -> None:
    agent_mod.set_current(
        agent_mod.Detection(
            client=agent_mod.AgentClient.GENERIC,
            signal="test",
            raw_value="x",
        )
    )

    @agent_mod.cmd_wrap("exit")
    def _body() -> None:
        raise SystemExit(3)

    with pytest.raises(SystemExit) as exc_info:
        _body()
    assert exc_info.value.code == 3

    captured = capsys.readouterr()
    events = [json.loads(line) for line in _lines(captured.out)]
    # No error event should be emitted for SystemExit.
    error_events = [e for e in events if e["event"] == "error"]
    assert error_events == []


# ── cmd_wrap: typer Context stripped from args ─────────────────────────


def test_cmd_wrap_filters_context_args(capsys: pytest.CaptureFixture) -> None:
    """A Click/Typer Context kwarg must not show up in command.start args."""

    import click

    agent_mod.set_current(
        agent_mod.Detection(
            client=agent_mod.AgentClient.GENERIC,
            signal="test",
            raw_value="x",
        )
    )

    @agent_mod.cmd_wrap("ctx-cmd")
    def _body(*, ctx: click.Context, name: str = "x") -> None:
        return None

    # Make a synthetic click Context.
    @click.command()
    def _dummy():
        pass

    with click.Context(_dummy) as ctx:
        _body(ctx=ctx, name="test")

    captured = capsys.readouterr()
    events = [json.loads(line) for line in _lines(captured.out)]
    start = next(e for e in events if e["event"] == "command.start")
    # Context kwarg should not be present.
    assert "ctx" not in start["args"]
    assert start["args"].get("name") == "test"


# ── RequiresInteractiveError: defaults ─────────────────────────────────


def test_requires_interactive_error_defaults() -> None:
    """Default constructor fills cmd/prompt with sensible placeholders."""

    exc = agent_mod.RequiresInteractiveError()
    assert exc.cmd == "<unknown>"
    assert exc.prompt == ""
    assert "agent mode" in str(exc)


def test_requires_interactive_error_explicit() -> None:
    exc = agent_mod.RequiresInteractiveError(cmd="setup", prompt="api_key")
    assert exc.cmd == "setup"
    assert exc.prompt == "api_key"


# ── ProgressEmitter: unbounded advance with time interval ─────────────


def test_progress_emitter_unbounded_emits_after_time(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unbounded progress emits once enough wall-clock time elapses."""

    fake_now = 1000.0

    def _fake_monotonic() -> float:
        return fake_now

    monkeypatch.setattr(agent_mod.time, "monotonic", _fake_monotonic)

    p = agent_mod.ProgressEmitter("scan", total=None)
    p.__enter__()
    p.advance(1)
    # Move clock past the 10s threshold.
    fake_now += 20.0
    p.advance(1)
    p.__exit__(None, None, None)

    captured = capsys.readouterr()
    events = [json.loads(line) for line in _lines(captured.out)]
    progress_events = [e for e in events if e["event"] == "progress"]
    # We expect at least one emission after the time threshold was crossed.
    assert len(progress_events) >= 1


# ── ProgressEmitter: update() with completed ──────────────────────────


def test_progress_emitter_update_with_completed(capsys: pytest.CaptureFixture) -> None:
    with agent_mod.ProgressEmitter("op", total=100) as p:
        task = p.add_task("desc", total=100)
        p.update(task, completed=50)
        p.update(task, completed=100)

    captured = capsys.readouterr()
    events = [json.loads(line) for line in _lines(captured.out)]
    progress_events = [e for e in events if e["event"] == "progress"]
    assert progress_events
    # Final should be at 100%.
    assert progress_events[-1]["done"] == 100


# ── ProgressEmitter: advance non-int input ────────────────────────────


def test_progress_emitter_advance_non_int(capsys: pytest.CaptureFixture) -> None:
    """Non-int input to advance() falls back to 1."""

    with agent_mod.ProgressEmitter("op", total=10) as p:
        p.advance("not-a-number")  # type: ignore[arg-type]


# ── ProgressEmitter: exception in body skips final 100% emission ───────


def test_progress_emitter_exception_in_body_no_final_emission(
    capsys: pytest.CaptureFixture,
) -> None:
    p = agent_mod.ProgressEmitter("op", total=100)
    try:
        with p:
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    captured = capsys.readouterr()
    events = [json.loads(line) for line in _lines(captured.out)]
    progress_events = [e for e in events if e["event"] == "progress"]
    # On exception, no final 100% is emitted (regardless of bounded).
    assert all(e.get("pct", 0.0) < 1.0 for e in progress_events)


# ── _is_context_like: import edge ──────────────────────────────────────


def test_is_context_like_returns_false_for_plain_object() -> None:
    assert agent_mod._is_context_like(42) is False
    assert agent_mod._is_context_like("not a context") is False


def test_is_context_like_returns_true_for_click_context() -> None:
    import click

    @click.command()
    def _dummy():
        pass

    with click.Context(_dummy) as ctx:
        assert agent_mod._is_context_like(ctx) is True


# ── current_detection / set_current round-trip ─────────────────────────


def test_current_detection_set_and_get() -> None:
    new = agent_mod.Detection(
        client=agent_mod.AgentClient.OPENCODE,
        signal="OPENCODE",
        raw_value="1",
    )
    agent_mod.set_current(new)
    assert agent_mod.current_detection() is new


# ── _sanitize / _iso_now indirectly ────────────────────────────────────


def test_emit_sanitizes_set(capsys: pytest.CaptureFixture) -> None:
    """Sets are coerced to lists for JSON-safety."""

    agent_mod.emit("status", items={1, 2, 3})
    captured = capsys.readouterr()
    parsed = json.loads(_lines(captured.out)[0])
    assert isinstance(parsed["items"], list)
    assert set(parsed["items"]) == {1, 2, 3}


def test_emit_sanitizes_unknown_object_to_str(capsys: pytest.CaptureFixture) -> None:
    class _Custom:
        def __str__(self) -> str:
            return "custom-rendered"

    agent_mod.emit("status", obj=_Custom())
    captured = capsys.readouterr()
    parsed = json.loads(_lines(captured.out)[0])
    assert parsed["obj"] == "custom-rendered"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
