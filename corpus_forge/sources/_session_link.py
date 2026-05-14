"""Shared helper: link a freshly-ingested conversation to its feedback_sessions row.

When a chat client (Claude Code, OpenCode, Gemini CLI) persists its session
to a file that corpus-forge then ingests, this helper bridges the in-process
MCP writes (which created feedback_sessions rows) to the on-disk session
file (which created the conversation row).

Returns True if linked, False if no matching feedback_sessions row OR the
row was already linked. Always idempotent.
"""

from __future__ import annotations


def link_session_to_conversation(
    backend,
    *,
    client: str,
    session_id: str,
    conversation_id: int,
) -> bool:
    return backend.link_feedback_session_to_conversation(
        client=client,
        session_id=session_id,
        conversation_id=conversation_id,
    )
