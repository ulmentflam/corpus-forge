"""Phase L Wave 9 — JSONL emission contract for ``ui.agent.emit``."""

from __future__ import annotations

import json
import re

import pytest

from corpus_forge.ui import agent as agent_mod


def _lines(text: str) -> list[str]:
    return [line for line in text.split("\n") if line]


def test_emit_writes_one_jsonl_line(capsys: pytest.CaptureFixture) -> None:
    agent_mod.emit("status", level="info", msg="hello")
    captured = capsys.readouterr()
    lines = _lines(captured.out)
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["event"] == "status"
    assert parsed["level"] == "info"
    assert parsed["msg"] == "hello"


def test_emit_ts_is_utc_iso_with_ms(capsys: pytest.CaptureFixture) -> None:
    agent_mod.emit("status", level="ok", msg="hi")
    captured = capsys.readouterr()
    parsed = json.loads(_lines(captured.out)[0])
    ts = parsed["ts"]
    # Z-suffixed UTC ISO 8601 with millisecond precision.
    assert ts.endswith("Z")
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$", ts), ts


def test_emit_has_no_embedded_newlines(capsys: pytest.CaptureFixture) -> None:
    agent_mod.emit("status", level="ok", msg="line1\nline2")
    captured = capsys.readouterr()
    raw = captured.out
    # Exactly one newline (the line terminator); embedded ``\n`` inside
    # the JSON string is escaped as ``\\n`` so the line stays single.
    assert raw.count("\n") == 1


def test_emit_serializes_non_json_values(capsys: pytest.CaptureFixture) -> None:
    """Pathlike / enum / nested dict values must not raise."""

    from pathlib import Path

    agent_mod.emit(
        "result",
        cmd="test",
        status="ok",
        data={"path": Path("/tmp/x"), "n": 3, "items": [1, 2, 3]},
    )
    captured = capsys.readouterr()
    parsed = json.loads(_lines(captured.out)[0])
    assert parsed["data"]["n"] == 3
    assert parsed["data"]["items"] == [1, 2, 3]


def test_result_helper_returns_zero_on_ok(capsys: pytest.CaptureFixture) -> None:
    rc = agent_mod.result("doctor", status="ok", data={"summary": "ok"})
    captured = capsys.readouterr()
    assert rc == 0
    parsed = json.loads(_lines(captured.out)[0])
    assert parsed["event"] == "result"
    assert parsed["status"] == "ok"


def test_result_helper_returns_one_on_error(capsys: pytest.CaptureFixture) -> None:
    rc = agent_mod.result("doctor", status="error", data={})
    capsys.readouterr()
    assert rc == 1


def test_error_helper_emits_error_event(capsys: pytest.CaptureFixture) -> None:
    rc = agent_mod.error("search", kind="config", msg="nope", exit_code=3)
    captured = capsys.readouterr()
    assert rc == 3
    parsed = json.loads(_lines(captured.out)[0])
    assert parsed["event"] == "error"
    assert parsed["kind"] == "config"


def test_progress_emitter_emits_at_milestones(capsys: pytest.CaptureFixture) -> None:
    """Bounded progress fires at 25/50/75/100% boundaries."""

    with agent_mod.ProgressEmitter("embed", total=100) as p:
        for _ in range(25):
            p.advance(1)
        for _ in range(25):
            p.advance(1)
        for _ in range(25):
            p.advance(1)
        for _ in range(25):
            p.advance(1)

    captured = capsys.readouterr()
    events = [json.loads(line) for line in _lines(captured.out)]
    progress_events = [e for e in events if e["event"] == "progress"]
    # Expect at least one per quarter (4 incremental milestones) plus a
    # final 100% snapshot on __exit__.
    assert len(progress_events) >= 4
    final = progress_events[-1]
    assert final["done"] == 100
    assert final.get("pct") == 1.0


def test_progress_emitter_unbounded_uses_time(capsys: pytest.CaptureFixture) -> None:
    """Unbounded progress doesn't emit on every advance; only on time interval."""

    with agent_mod.ProgressEmitter("scan", total=None) as p:
        # Fast bursts shouldn't flood the wire.
        for _ in range(50):
            p.advance(1)

    captured = capsys.readouterr()
    events = [json.loads(line) for line in _lines(captured.out)]
    progress_events = [e for e in events if e["event"] == "progress"]
    # No 100% emission for unbounded; bursts under the 10s interval skip.
    assert len(progress_events) <= 1


def test_progress_emitter_rich_compatible_advance(capsys: pytest.CaptureFixture) -> None:
    """``advance(task_id, n=5)`` (Rich style) must work."""

    with agent_mod.ProgressEmitter("test", total=10) as p:
        task = p.add_task("test", total=10)
        p.advance(task, n=5)
        p.advance(task, n=5)

    captured = capsys.readouterr()
    events = [json.loads(line) for line in _lines(captured.out)]
    progress_events = [e for e in events if e["event"] == "progress"]
    assert progress_events[-1]["done"] == 10


def test_set_current_and_is_agent_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """The singleton survives across calls; is_agent_mode reflects it."""

    original = agent_mod.current_detection()
    try:
        agent_mod.set_current(
            agent_mod.Detection(
                client=agent_mod.AgentClient.CLAUDE_CODE,
                signal="test",
                raw_value="x",
            )
        )
        assert agent_mod.is_agent_mode() is True
        assert agent_mod.current_detection().client is agent_mod.AgentClient.CLAUDE_CODE
    finally:
        agent_mod.set_current(original)
