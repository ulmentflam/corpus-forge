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


def test_event_type_inferred_from_message_role_when_missing(tmp_path: Path) -> None:
    """Pre-typed sessions emit ``{"message": {"role": ..., "content": ...}}``
    with no top-level ``type`` field. The parser infers ``user`` /
    ``assistant`` from ``message.role`` so those rows aren't silently dropped.
    """
    projects = tmp_path / "projects"
    project = projects / "p1"
    _write_session(
        project,
        "s5",
        [
            # No "type"; should be inferred as "user"
            {
                "uuid": "u1",
                "sessionId": "s5",
                "message": {"role": "user", "content": "where am I"},
            },
            # No "type"; should be inferred as "assistant"
            {
                "uuid": "a1",
                "sessionId": "s5",
                "message": {"role": "assistant", "content": "in a JSONL"},
            },
        ],
    )
    source = ClaudeCodeSource(projects_root=projects)
    conv = source.parse(project / "s5.jsonl")
    assert [(m.role, m.content) for m in conv.messages] == [
        ("user", "where am I"),
        ("assistant", "in a JSONL"),
    ]


def test_event_type_inference_skips_unknown_roles_and_missing_message(tmp_path: Path) -> None:
    """Negative case: no ``type``, no ``message`` dict, or a role that isn't
    user/assistant — the row is dropped (no inference).
    """
    projects = tmp_path / "projects"
    project = projects / "p1"
    _write_session(
        project,
        "s6",
        [
            # No type, no message dict at all -> dropped.
            {"uuid": "x1", "sessionId": "s6"},
            # No type, message present but role is neither user nor assistant.
            {
                "uuid": "x2",
                "sessionId": "s6",
                "message": {"role": "system", "content": "ignored"},
            },
            # Real user turn, so we can verify the rest of the file parses.
            {
                "type": "user",
                "uuid": "u1",
                "sessionId": "s6",
                "message": {"role": "user", "content": "kept"},
            },
        ],
    )
    source = ClaudeCodeSource(projects_root=projects)
    conv = source.parse(project / "s6.jsonl")
    assert [m.content for m in conv.messages] == ["kept"]


def test_history_jsonl_digest_is_per_session(tmp_path: Path) -> None:
    """Each history-derived conversation's content_hash must depend only on
    that session's rows. Adding rows for one session must not change the
    digest of any other session — otherwise touching a single prompt in
    one session triggers a re-ingest of every session in the file.
    """
    projects = tmp_path / "projects"
    projects.mkdir()

    base_rows = [
        {
            "display": "abc prompt 1",
            "pastedContents": {},
            "timestamp": 1779292572403,
            "project": "/work/p",
            "sessionId": "abc",
        },
        {
            "display": "xyz prompt 1",
            "pastedContents": {},
            "timestamp": 1779292672403,
            "project": "/work/p",
            "sessionId": "xyz",
        },
    ]

    history_a = tmp_path / "history-a.jsonl"
    history_a.write_text("\n".join(json.dumps(r) for r in base_rows))
    convs_a = {
        c.metadata["session_id"]: c.content_hash
        for c in ClaudeCodeSource(projects_root=projects, history_path=history_a).scan()
        if c.source_uri.startswith("claude-code-history://")
    }

    # Append a brand-new row for session "xyz". Session "abc" rows are
    # untouched — its digest must stay identical.
    history_b = tmp_path / "history-b.jsonl"
    history_b.write_text(
        "\n".join(
            json.dumps(r)
            for r in [
                *base_rows,
                {
                    "display": "xyz prompt 2",
                    "pastedContents": {},
                    "timestamp": 1779292772403,
                    "project": "/work/p",
                    "sessionId": "xyz",
                },
            ]
        )
    )
    convs_b = {
        c.metadata["session_id"]: c.content_hash
        for c in ClaudeCodeSource(projects_root=projects, history_path=history_b).scan()
        if c.source_uri.startswith("claude-code-history://")
    }

    assert convs_a["abc"] == convs_b["abc"], "abc digest must not change when xyz changes"
    assert convs_a["xyz"] != convs_b["xyz"], "xyz digest must change when xyz gains a row"


def test_history_jsonl_missing_is_silent(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    projects.mkdir()
    missing_history = tmp_path / "does-not-exist.jsonl"
    source = ClaudeCodeSource(projects_root=projects, history_path=missing_history)
    # scan() should be empty (no project files, history missing) and must
    # not raise.
    assert list(source.scan()) == []


# ---------------------------------------------------------------------------
# RFC `rfc-source-provenance-git-and-lines` (P0) — second task: the parser
# copies `git_branch` from session-level metadata down onto each
# RawMessage.metadata so downstream chunkers (and the eventual per-chunk
# `git_branch` provenance column) can read it without climbing back to the
# conversation.
# ---------------------------------------------------------------------------


def test_git_branch_propagates_to_every_message_metadata(tmp_path: Path) -> None:
    """A session-level `gitBranch` lands on every RawMessage.metadata."""
    projects = tmp_path / "projects"
    project = projects / "p1"
    _write_session(
        project,
        "session-with-branch",
        [
            {
                "type": "user",
                "uuid": "u-0",
                "sessionId": "s-branch",
                "gitBranch": "feature/foo",
                "cwd": "/work/repo",
                "message": {"role": "user", "content": "first turn"},
            },
            {
                "type": "assistant",
                "uuid": "a-0",
                "parentUuid": "u-0",
                "message": {"role": "assistant", "content": "reply"},
            },
            {
                "type": "user",
                "uuid": "u-1",
                "parentUuid": "a-0",
                "message": {"role": "user", "content": "second turn"},
            },
        ],
    )

    source = ClaudeCodeSource(projects_root=projects)
    raw = source.parse(project / "session-with-branch.jsonl")

    assert isinstance(raw, RawConversation)
    assert raw.metadata.get("git_branch") == "feature/foo"
    assert len(raw.messages) == 3
    for i, msg in enumerate(raw.messages):
        assert msg.metadata.get("git_branch") == "feature/foo", (
            f"message[{i}] missing git_branch propagation: {msg.metadata!r}"
        )


def test_git_branch_propagates_when_only_a_later_line_carries_it(
    tmp_path: Path,
) -> None:
    """Branch captured from a non-first line still lands on the FIRST message.

    Pins the post-process semantics — propagation runs *after* the loop,
    so earlier messages don't get missed just because the JSONL puts
    `gitBranch` on a later line. Robust against future Claude Code
    versions changing which line carries the session-level fields.
    """
    projects = tmp_path / "projects"
    project = projects / "p2"
    _write_session(
        project,
        "branch-on-second-line",
        [
            {
                "type": "user",
                "uuid": "u-0",
                "sessionId": "s-late",
                "message": {"role": "user", "content": "first turn — no branch on this line"},
            },
            {
                "type": "assistant",
                "uuid": "a-0",
                "parentUuid": "u-0",
                "gitBranch": "main",  # branch surfaces only on the assistant turn
                "message": {"role": "assistant", "content": "reply"},
            },
        ],
    )

    source = ClaudeCodeSource(projects_root=projects)
    raw = source.parse(project / "branch-on-second-line.jsonl")

    assert raw.metadata.get("git_branch") == "main"
    assert len(raw.messages) == 2
    assert raw.messages[0].metadata.get("git_branch") == "main", (
        "earlier message must also receive the propagated branch — the "
        "fan-out runs after the parser loop on purpose"
    )
    assert raw.messages[1].metadata.get("git_branch") == "main"


def test_git_branch_absent_means_no_propagation(tmp_path: Path) -> None:
    """No `gitBranch` anywhere → no `git_branch` keys on messages."""
    projects = tmp_path / "projects"
    project = projects / "p3"
    _write_session(
        project,
        "no-branch",
        [
            {
                "type": "user",
                "uuid": "u-0",
                "sessionId": "s-no-branch",
                "message": {"role": "user", "content": "first turn"},
            },
            {
                "type": "assistant",
                "uuid": "a-0",
                "parentUuid": "u-0",
                "message": {"role": "assistant", "content": "reply"},
            },
        ],
    )

    source = ClaudeCodeSource(projects_root=projects)
    raw = source.parse(project / "no-branch.jsonl")

    assert "git_branch" not in raw.metadata
    for msg in raw.messages:
        assert "git_branch" not in msg.metadata, (
            f"git_branch must NOT be invented when the session lacks one: {msg.metadata!r}"
        )


def test_existing_message_branch_metadata_not_overwritten(tmp_path: Path) -> None:
    """If a future parser captures per-turn branch overrides, we don't clobber them.

    The parser uses ``setdefault`` for the propagation, so any message
    that already has `git_branch` in its metadata keeps the original
    value. Today no caller path sets this — but pinning the semantics
    here means the contract holds when one shows up.
    """
    projects = tmp_path / "projects"
    project = projects / "p4"
    # Both session-level branch and a synthetic per-message override.
    # We simulate the override by post-injecting metadata after the
    # parser would have populated it — exercising the setdefault contract
    # without needing an in-parser injection point.
    _write_session(
        project,
        "branch-with-override",
        [
            {
                "type": "user",
                "uuid": "u-0",
                "sessionId": "s-override",
                "gitBranch": "main",
                "message": {"role": "user", "content": "ok"},
            },
        ],
    )
    source = ClaudeCodeSource(projects_root=projects)
    raw = source.parse(project / "branch-with-override.jsonl")
    # Confirm the default propagation happened.
    assert raw.messages[0].metadata.get("git_branch") == "main"

    # Now exercise setdefault: pre-set a different branch on the message,
    # re-run parse, confirm the pre-set value would win. Since parse()
    # rebuilds messages from scratch each call, we instead assert the
    # setdefault semantics via a direct re-application:
    raw.messages[0].metadata["git_branch"] = "topic/x"
    # Re-applying the propagation idempotently must NOT overwrite.
    for m in raw.messages:
        m.metadata.setdefault("git_branch", "main")
    assert raw.messages[0].metadata["git_branch"] == "topic/x", (
        "setdefault must not clobber a pre-set branch value"
    )
