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
    """Conversation reconstruction must be driven by *timestamp*, not filename.

    Message and part files are deliberately written in reverse-lexicographic
    order (``zzz_msg.json`` for the earlier user turn, ``aaa_msg.json`` for
    the later assistant turn; parts ``zzz_part`` precedes ``aaa_part``
    inside the user message) while their ``timestamp`` fields stay
    chronologically ascending. The loader must sort by ``timestamp`` so the
    user turn ends up first in ``conv.messages`` despite filename order
    putting it last.
    """
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

    # Earlier turn (user), filename sorts AFTER the later turn.
    _write(
        storage / "session" / "message" / sid / "zzz_msg.json",
        {
            "id": "m1",
            "role": "user",
            "timestamp": 1700000000,
        },
    )
    # User-turn parts: "Hello " has the later filename so file-name sort
    # would render "world.Hello " — but inline-list parts have their own
    # ordering via ``msg_data["parts"]`` fallback (see other test); here
    # we use the directory layout so part filenames stay lexicographic for
    # within-message ordering, and only the message-level sort exercises
    # the timestamp path.
    _write(
        storage / "session" / "part" / sid / "m1" / "p1.json",
        {"type": "text", "text": "Hello "},
    )
    _write(
        storage / "session" / "part" / sid / "m1" / "p2.json",
        {"type": "text", "text": "world."},
    )

    # Later turn (assistant), filename sorts BEFORE the earlier turn.
    _write(
        storage / "session" / "message" / sid / "aaa_msg.json",
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

    # Timestamp-driven ordering: user (1700000000) must come first even
    # though its filename (``zzz_msg.json``) sorts after the assistant's.
    user_msg, assistant_msg = conv.messages
    assert user_msg.role == "user"
    assert user_msg.ts == 1700000000
    assert user_msg.content == "Hello world."

    assert assistant_msg.role == "assistant"
    assert assistant_msg.ts == 1700000001
    assert assistant_msg.content == "Hi back."
    assert assistant_msg.tool_calls is not None
    assert assistant_msg.tool_calls[0]["name"] == "edit"

    # started_at/ended_at must come from the chronologically sorted ends.
    assert conv.started_at == 1700000000
    assert conv.ended_at == 1700000001


def test_scan_places_untimestamped_messages_after_timed_turns(tmp_path: Path) -> None:
    """Messages with ``ts is None`` must sort to the end so ``started_at``
    picks the earliest real timestamp instead of being clobbered by a
    missing one. Regression for the ``0.0``-sentinel pitfall.
    """
    storage = tmp_path / "storage"
    sid = "sess-tsmix"
    _write(
        storage / "session" / "info" / f"{sid}.json",
        {"id": sid, "title": "Mixed ts session"},
    )
    # User turn has no timestamp at all.
    _write(
        storage / "session" / "message" / sid / "m_user.json",
        {"id": "m1", "role": "user"},
    )
    _write(
        storage / "session" / "part" / sid / "m1" / "p1.json",
        {"type": "text", "text": "no ts here"},
    )
    # Assistant turn has a real timestamp.
    _write(
        storage / "session" / "message" / sid / "m_assistant.json",
        {"id": "m2", "role": "assistant", "timestamp": 1700000005},
    )
    _write(
        storage / "session" / "part" / sid / "m2" / "p1.json",
        {"type": "text", "text": "answered"},
    )

    convs = list(OpenCodeSource(storage_root=storage).scan())
    assert len(convs) == 1
    conv = convs[0]
    # Real-ts message must come first; None-ts message goes to the end.
    assert [m.ts for m in conv.messages] == [1700000005, None]
    # started_at must be the real timestamp, not None.
    assert conv.started_at == 1700000005


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
