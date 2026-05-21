"""Unit tests for the type-aware Claude Code source parser.

Covers the gaps the previous implementation had:

- ``permission-mode``, ``ai-title``, ``last-prompt``, ``pr-link``, and
  ``file-history-snapshot`` lines are folded into conversation metadata
  instead of being treated as empty assistant turns.
- ``message.content`` blocks of type ``tool_use`` / ``tool_result`` are
  surfaced as structured ``tool_calls`` / ``tool_results`` on the
  ``RawMessage``.
- Sub-agent / synthetic / permission-mode flags on user / assistant turns
  land on ``RawMessage.metadata``.
- Optional ``~/.claude/history.jsonl`` ingestion yields one extra
  ``RawConversation`` per ``sessionId`` with a distinct
  ``claude-code-history://`` URI scheme.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from corpus_forge.sources.base import RawConversation
from corpus_forge.sources.claude_code import ClaudeCodeSource

pytestmark = pytest.mark.unit


def _write_session(project_dir: Path, name: str, lines: list[dict]) -> Path:
    """Write a JSONL session file under ``project_dir`` with one row per line."""
    project_dir.mkdir(parents=True, exist_ok=True)
    session_file = project_dir / f"{name}.jsonl"
    session_file.write_text("\n".join(json.dumps(line) for line in lines))
    return session_file


def test_drops_non_message_event_types(tmp_path: Path) -> None:
    """Non-message types (permission-mode, file-history-snapshot, ai-title)
    must not produce ``RawMessage`` rows.
    """
    projects = tmp_path / "projects"
    project = projects / "p1"
    _write_session(
        project,
        "s1",
        [
            {"type": "permission-mode", "permissionMode": "bypassPermissions", "sessionId": "s1"},
            {"type": "file-history-snapshot", "messageId": "m0", "isSnapshotUpdate": False},
            {"type": "ai-title", "aiTitle": "Refactor the daemon", "sessionId": "s1"},
            {
                "type": "user",
                "uuid": "u1",
                "sessionId": "s1",
                "message": {"role": "user", "content": "hi"},
            },
            {
                "type": "assistant",
                "uuid": "a1",
                "parentUuid": "u1",
                "sessionId": "s1",
                "message": {"role": "assistant", "content": "hello"},
            },
        ],
    )

    source = ClaudeCodeSource(projects_root=projects)
    conv = source.parse(project / "s1.jsonl")
    assert isinstance(conv, RawConversation)
    assert len(conv.messages) == 2
    assert conv.messages[0].role == "user"
    assert conv.messages[1].role == "assistant"
    assert conv.title == "Refactor the daemon"
    assert conv.metadata["session_id"] == "s1"
    # permission_modes get folded onto conversation metadata
    assert conv.metadata["permission_modes"][0]["mode"] == "bypassPermissions"


def test_extracts_tool_use_and_tool_result(tmp_path: Path) -> None:
    """``tool_use`` and ``tool_result`` content blocks must populate the
    ``tool_calls`` / ``tool_results`` fields, not be dropped.
    """
    projects = tmp_path / "projects"
    project = projects / "p1"
    _write_session(
        project,
        "s2",
        [
            {
                "type": "assistant",
                "uuid": "a1",
                "sessionId": "s2",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "running..."},
                        {
                            "type": "tool_use",
                            "id": "call-1",
                            "name": "bash",
                            "input": {"cmd": "ls"},
                        },
                    ],
                },
            },
            {
                "type": "user",
                "uuid": "u1",
                "parentUuid": "a1",
                "sessionId": "s2",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call-1",
                            "content": "file1\nfile2",
                            "is_error": False,
                        }
                    ],
                },
            },
        ],
    )

    source = ClaudeCodeSource(projects_root=projects)
    conv = source.parse(project / "s2.jsonl")
    assert isinstance(conv, RawConversation)

    assistant_msg = conv.messages[0]
    assert assistant_msg.tool_calls is not None
    assert assistant_msg.tool_calls[0]["id"] == "call-1"
    assert assistant_msg.tool_calls[0]["name"] == "bash"
    assert assistant_msg.content == "running..."

    user_msg = conv.messages[1]
    assert user_msg.tool_results is not None
    assert user_msg.tool_results[0]["tool_use_id"] == "call-1"
    assert user_msg.tool_results[0]["content"] == "file1\nfile2"


def test_captures_sidechain_and_subtype_on_message(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    project = projects / "p1"
    _write_session(
        project,
        "s3",
        [
            {
                "type": "user",
                "uuid": "u1",
                "isSidechain": True,
                "isMeta": True,
                "subtype": "init",
                "permissionMode": "bypassPermissions",
                "sessionId": "s3",
                "message": {"role": "user", "content": "start"},
            }
        ],
    )

    source = ClaudeCodeSource(projects_root=projects)
    conv = source.parse(project / "s3.jsonl")
    msg = conv.messages[0]
    assert msg.metadata["sidechain"] is True
    assert msg.metadata["meta"] is True
    assert msg.metadata["subtype"] == "init"
    assert msg.metadata["permission_mode"] == "bypassPermissions"


def test_attachment_event_becomes_user_message(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    project = projects / "p1"
    _write_session(
        project,
        "s4",
        [
            {
                "type": "attachment",
                "uuid": "att-1",
                "sessionId": "s4",
                "attachment": {"type": "file", "path": "/tmp/foo.txt"},
            }
        ],
    )

    source = ClaudeCodeSource(projects_root=projects)
    conv = source.parse(project / "s4.jsonl")
    assert len(conv.messages) == 1
    assert conv.messages[0].role == "user"
    assert "foo.txt" in conv.messages[0].content


def test_history_jsonl_yields_extra_conversations(tmp_path: Path) -> None:
    """When ``history_path`` is configured, ``scan`` yields one conversation
    per ``sessionId`` from history.jsonl, in addition to project sessions.
    """
    projects = tmp_path / "projects"
    project = projects / "p1"
    _write_session(
        project,
        "abc",
        [
            {
                "type": "user",
                "uuid": "u1",
                "sessionId": "abc",
                "message": {"role": "user", "content": "main session"},
            }
        ],
    )

    history = tmp_path / "history.jsonl"
    history.write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                {
                    "display": "first prompt",
                    "pastedContents": {},
                    "timestamp": 1779292572403,
                    "project": "/work/proj",
                    "sessionId": "abc",
                },
                {
                    "display": "second prompt",
                    "pastedContents": {
                        "p1": {"content": "pasted code here"},
                    },
                    "timestamp": 1779292672403,
                    "project": "/work/proj",
                    "sessionId": "abc",
                },
                {
                    "display": "different-session prompt",
                    "pastedContents": {},
                    "timestamp": 1779292772403,
                    "project": "/work/proj",
                    "sessionId": "xyz",
                },
            ]
        )
    )

    source = ClaudeCodeSource(projects_root=projects, history_path=history)
    convs = list(source.scan())

    project_convs = [c for c in convs if c.source_uri.startswith("claude-code://")]
    history_convs = [c for c in convs if c.source_uri.startswith("claude-code-history://")]

    assert len(project_convs) == 1
    assert len(history_convs) == 2

    abc_history = next(c for c in history_convs if c.external_id == "abc-prompts")
    assert len(abc_history.messages) == 2
    assert "pasted code here" in abc_history.messages[1].content
    assert abc_history.metadata["session_id"] == "abc"
    assert abc_history.metadata["source_kind"] == "history"


def test_history_jsonl_missing_is_silent(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    projects.mkdir()
    missing_history = tmp_path / "does-not-exist.jsonl"
    source = ClaudeCodeSource(projects_root=projects, history_path=missing_history)
    # scan() should be empty (no project files, history missing) and must
    # not raise.
    assert list(source.scan()) == []
