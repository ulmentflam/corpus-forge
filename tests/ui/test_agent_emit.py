"""Phase L Wave 9 — JSONL emission contract for ``ui.agent.emit``."""

from __future__ import annotations

import errno
import json
import logging
import re

import pytest

from corpus_forge.ui import agent as agent_mod


def _lines(text: str) -> list[str]:
    return [line for line in text.split("\n") if line]


class _StubStdout:
    """Minimal stdout stand-in that counts ``write`` calls.

    ``raise_on`` selects an exception class to raise from ``write`` so a
    closed-pipe consumer can be simulated without an actual pipe.
    """

    def __init__(self, *, raise_exc: BaseException | None = None) -> None:
        self.write_calls = 0
        self.flush_calls = 0
        self._raise_exc = raise_exc

    def write(self, _data: str) -> int:
        self.write_calls += 1
        if self._raise_exc is not None:
            raise self._raise_exc
        return len(_data)

    def flush(self) -> None:
        self.flush_calls += 1


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


# ── broken-pipe latch (issue #93) ─────────────────────────────────────
#
# The latch is a module-level global; under pytest-randomly any test that
# touches it must reset it via monkeypatch so teardown restores the
# original value and no leak reaches the capsys-based emit tests above.


def test_emit_latches_on_broken_pipe_and_logs_once(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A ``BrokenPipeError`` from stdout doesn't propagate, latches, logs once."""

    monkeypatch.setattr(agent_mod._SINK, "dead", False)
    stub = _StubStdout(raise_exc=BrokenPipeError(errno.EPIPE, "broken pipe"))
    monkeypatch.setattr(agent_mod.sys, "stdout", stub)

    with caplog.at_level(logging.WARNING, logger=agent_mod.__name__):
        # Must not raise even though the underlying write blows up.
        agent_mod.emit("status", level="info", msg="first")

    assert agent_mod._SINK.dead is True
    assert stub.write_calls == 1
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "stdout closed" in warnings[0].getMessage()


def test_emit_is_noop_after_latch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Once latched, ``emit`` returns without even attempting a stdout write."""

    monkeypatch.setattr(agent_mod._SINK, "dead", True)
    stub = _StubStdout()
    monkeypatch.setattr(agent_mod.sys, "stdout", stub)

    agent_mod.emit("status", level="info", msg="ignored")

    assert stub.write_calls == 0
    assert stub.flush_calls == 0


def test_emit_reraises_non_epipe_oserror_without_latching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-EPIPE ``OSError`` (e.g. EBADF) propagates and leaves the latch off."""

    monkeypatch.setattr(agent_mod._SINK, "dead", False)
    stub = _StubStdout(raise_exc=OSError(errno.EBADF, "bad file descriptor"))
    monkeypatch.setattr(agent_mod.sys, "stdout", stub)

    with pytest.raises(OSError) as excinfo:
        agent_mod.emit("status", level="info", msg="boom")

    assert excinfo.value.errno == errno.EBADF
    assert agent_mod._SINK.dead is False
    assert stub.write_calls == 1


def test_emit_latch_set_before_log_blocks_reentrant_emit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The latch must flip *before* ``_LOG.warning`` so the re-entrant
    ``AgentLogHandler.emit`` short-circuits instead of writing again.

    In agent mode the daemon attaches :class:`AgentLogHandler` to the
    ``corpus_forge`` root logger; its ``emit`` calls ``agent.emit("log",
    ...)`` re-entrantly.  When stdout is a broken pipe, the first
    ``emit`` write raises EPIPE → ``emit`` sets ``_SINK.dead = True`` and
    *then* logs a WARNING.  Because the latch is already set, the
    handler-driven re-entrant ``emit`` returns immediately and never
    touches the dead pipe a second time.
    """

    from corpus_forge.logging_config import AgentLogHandler

    monkeypatch.setattr(agent_mod._SINK, "dead", False)
    stub = _StubStdout(raise_exc=BrokenPipeError(errno.EPIPE, "broken pipe"))
    monkeypatch.setattr(agent_mod.sys, "stdout", stub)

    handler = AgentLogHandler()
    handler.setLevel(logging.WARNING)
    logger = logging.getLogger(agent_mod.__name__)
    logger.addHandler(handler)
    try:
        # Must not raise even though the underlying write blows up and a
        # WARNING fires while stdout is still broken.
        agent_mod.emit("status", level="info", msg="first")
    finally:
        logger.removeHandler(handler)

    assert agent_mod._SINK.dead is True
    # Exactly one write attempt: the original ``emit``.  The re-entrant
    # log emit from the handler must have short-circuited on the latch.
    assert stub.write_calls == 1


def test_progress_emitter_silent_after_latch(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the latch ON, a ``ProgressEmitter`` run writes nothing (issue #93).

    The original symptom: after the agent-mode consumer closed the pipe,
    a long ingest kept driving the progress bar and re-raised EPIPE on
    every milestone.  Once latched, every milestone crossing and the
    final ``__exit__`` snapshot must be a no-op on stdout.
    """

    monkeypatch.setattr(agent_mod._SINK, "dead", True)
    stub = _StubStdout()
    monkeypatch.setattr(agent_mod.sys, "stdout", stub)

    with agent_mod.ProgressEmitter("embed", total=100) as p:
        task = p.add_task("embed", total=100)
        for _ in range(100):
            p.advance(task, n=1)

    assert stub.write_calls == 0
    assert stub.flush_calls == 0
