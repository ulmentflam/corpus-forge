"""Unit tests for the modern Codex CLI event-stream format.

Recent ``codex`` releases write rollouts under
``sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl`` with typed event
payloads — ``session_meta``, ``event_msg`` (user/agent), and
``response_item`` (function_call/function_call_output). These tests
pin the parser's coverage of that shape end-to-end.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from corpus_forge.sources.codex_cli import CodexCLISource

pytestmark = pytest.mark.unit


def _write_rollout(root: Path, lines: list[dict]) -> Path:
    """Write a rollout at the real-world date-tree path."""
    leaf = root / "2026" / "05" / "21"
    leaf.mkdir(parents=True)
    path = leaf / "rollout-2026-05-21T17-00-00-deadbeef.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines))
    return path


def test_discover_walks_date_tree(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    _write_rollout(
        root,
        [
            {"type": "session_meta", "payload": {"id": "sess-1"}},
            {
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "hi"},
            },
        ],
    )
    found = list(CodexCLISource(sessions_root=root).discover())
    assert len(found) == 1
    assert "rollout-" in found[0].name


def test_parse_modern_event_stream(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    path = _write_rollout(
        root,
        [
            {
                "type": "session_meta",
                "payload": {
                    "id": "sess-42",
                    "cwd": "/repo",
                    "cli_version": "0.42.0",
                    "originator": "codex_cli_rs",
                    "model": "gpt-5-codex",
                },
            },
            {
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "Run tests"},
                "ts": "2026-05-21T17:00:00",
            },
            {
                "type": "event_msg",
                "payload": {"type": "agent_message", "message": "On it."},
                "ts": "2026-05-21T17:00:02",
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "shell",
                    "arguments": "pytest -q",
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call-1",
                    "output": "1 passed",
                },
            },
        ],
    )
    conv = CodexCLISource(sessions_root=root).parse(path)
    assert conv is not None
    assert conv.external_id == "sess-42"
    assert conv.metadata["cli_version"] == "0.42.0"
    assert conv.metadata["model"] == "gpt-5-codex"

    # user, agent, function_call (attached to agent), function_call_output
    assert [m.role for m in conv.messages] == ["user", "assistant", "tool"]
    agent = conv.messages[1]
    assert agent.tool_calls is not None
    assert agent.tool_calls[0]["name"] == "shell"
    tool_msg = conv.messages[2]
    assert tool_msg.tool_results is not None
    assert tool_msg.tool_results[0]["tool_use_id"] == "call-1"
    assert tool_msg.content == "1 passed"


def test_legacy_format_still_works(tmp_path: Path) -> None:
    """The pre-existing ``{role, content, ts}`` shape must continue to parse.

    The legacy unit-test fixture lives at ``sessions/abc.jsonl`` flat at
    the root; this test pins that path so the rglob change doesn't
    accidentally regress the simple case.
    """
    root = tmp_path / "sessions"
    root.mkdir()
    flat = root / "abc.jsonl"
    flat.write_text(
        json.dumps({"role": "user", "content": "hi", "ts": 1700000000})
        + "\n"
        + json.dumps({"role": "assistant", "content": "yo", "ts": 1700000001})
    )
    conv = CodexCLISource(sessions_root=root).parse(flat)
    assert conv is not None
    assert [m.role for m in conv.messages] == ["user", "assistant"]
