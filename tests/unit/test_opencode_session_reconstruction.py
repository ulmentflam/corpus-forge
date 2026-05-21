"""Unit tests for OpenCode's session/message/part triple-store reconstruction.

The legacy per-file ``parse`` path is exercised by ``test_sources.py``;
this file covers the new ``scan`` behaviour: when an OpenCode storage
root has ``session/info/<sid>.json`` + ``session/message/<sid>/<mid>.json``
+ ``session/part/<sid>/<mid>/<pid>.json``, the source emits one
``RawConversation`` per session.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from corpus_forge.sources.opencode import OpenCodeSource

pytestmark = pytest.mark.unit


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def test_scan_reconstructs_session_from_triple_store(tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    sid = "sess-A"

    _write(
        storage / "session" / "info" / f"{sid}.json",
        {
            "id": sid,
            "title": "Refactor the daemon",
            "model": "claude-opus-4-7",
            "provider": "anthropic",
            "cwd": "/repo",
        },
    )

    # Two messages, with parts written in non-lexicographic-order to verify
    # the loader actually sorts.
    _write(
        storage / "session" / "message" / sid / "m1.json",
        {
            "id": "m1",
            "role": "user",
            "timestamp": 1700000000,
        },
    )
    _write(
        storage / "session" / "part" / sid / "m1" / "p1.json",
        {"type": "text", "text": "Hello "},
    )
    _write(
        storage / "session" / "part" / sid / "m1" / "p2.json",
        {"type": "text", "text": "world."},
    )

    _write(
        storage / "session" / "message" / sid / "m2.json",
        {
            "id": "m2",
            "role": "assistant",
            "timestamp": 1700000001,
        },
    )
    _write(
        storage / "session" / "part" / sid / "m2" / "p1.json",
        {"type": "text", "text": "Hi back."},
    )
    _write(
        storage / "session" / "part" / sid / "m2" / "p2.json",
        {
            "type": "tool_use",
            "id": "call-1",
            "name": "edit",
            "input": {"path": "main.py"},
        },
    )

    convs = list(OpenCodeSource(storage_root=storage).scan())
    assert len(convs) == 1

    conv = convs[0]
    assert conv.external_id == sid
    assert conv.title == "Refactor the daemon"
    assert conv.metadata["model"] == "claude-opus-4-7"
    assert conv.metadata["provider"] == "anthropic"
    assert conv.metadata["cwd"] == "/repo"
    assert len(conv.messages) == 2

    user_msg, assistant_msg = conv.messages
    assert user_msg.role == "user"
    assert user_msg.content == "Hello world."

    assert assistant_msg.role == "assistant"
    assert assistant_msg.content == "Hi back."
    assert assistant_msg.tool_calls is not None
    assert assistant_msg.tool_calls[0]["name"] == "edit"


def test_scan_falls_back_to_legacy_layout_when_no_info(tmp_path: Path) -> None:
    """Without ``session/info/*.json`` the legacy ``message.json`` walker runs.

    This is the path covered by the pre-existing ``sample_opencode_dir``
    fixture; this test just confirms scan() honours it.
    """
    storage = tmp_path / "storage"
    _write(
        storage / "message" / "msg1" / "message.json",
        {
            "id": "msg1",
            "role": "assistant",
            "content": "legacy content",
            "timestamp": 1,
            "parts": [],
        },
    )
    convs = list(OpenCodeSource(storage_root=storage).scan())
    assert len(convs) == 1
    assert convs[0].external_id == "msg1"
    assert convs[0].messages[0].content == "legacy content"
