"""H-03 RED — end-to-end test: claude_code source plugin links session to conversation.

Flow
----
1. Spin up a migrated in-memory SQLiteBackend.
2. Pre-populate ``feedback_sessions`` for a known session_id.
3. Write a fake ``.jsonl`` session file on disk.
4. Run the ingest pipeline (``ingest_one`` with the claude_code source's
   parsed output) and assert:
   a. A conversations row was created.
   b. feedback_sessions.conversation_id now points at it.

The H-03 production change must:
  - Add ``corpus_forge.sources._session_link.link_session_to_conversation``.
  - Add ``SQLiteBackend.link_feedback_session_to_conversation``.
  - Call the helper from inside the ingest pipeline (in ``ingest_one``) AFTER
    ``backend.upsert_conversation`` returns the conversation_id.

Run command:
    .venv/bin/python -m pytest tests/integration/test_claude_code_session_link_e2e.py -v

pytestmark: pytest.mark.integration
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from corpus_forge.backends.sqlite import SQLiteBackend
from corpus_forge.chunkers.conversation import ConversationChunker
from corpus_forge.ingest import ingest_one

# This import will fail until H-03 production code lands — RED trip-wire.
from corpus_forge.sources._session_link import link_session_to_conversation
from corpus_forge.sources.claude_code import ClaudeCodeSource

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SESSION_ID = "deadbeef-1234"
_CLIENT = "claude-code"
_HOST = "test-host"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _make_backend() -> SQLiteBackend:
    b = SQLiteBackend(path=":memory:")
    b.migrate()
    return b


def _write_fake_session(tmp_path: Path, session_id: str, n_messages: int = 3) -> Path:
    """Write a fake Claude Code .jsonl session file with *n_messages* entries.

    The file is placed at ``<tmp_path>/<session_id>.jsonl`` under a fake
    project directory, matching the ClaudeCodeSource.discover() layout:
    ``<projects_root>/<project_dir>/<session_id>.jsonl``.
    """
    project_dir = tmp_path / "my-project"
    project_dir.mkdir(parents=True, exist_ok=True)

    session_file = project_dir / f"{session_id}.jsonl"
    lines = []
    for i in range(n_messages):
        role = "user" if i % 2 == 0 else "assistant"
        entry = {
            "uuid": f"uuid-{i:04d}",
            "parentUuid": f"uuid-{i - 1:04d}" if i > 0 else None,
            "timestamp": _now_iso(),
            "message": {
                "role": role,
                "content": f"Message {i} from the fake session.",
            },
        }
        lines.append(json.dumps(entry))
    session_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return session_file


def _seed_dataset(backend: SQLiteBackend, name: str = "test-dataset") -> int:
    return backend.get_or_create_dataset(name, "chat", "h03 e2e test dataset")


def _count_conversations(backend: SQLiteBackend) -> int:
    with backend._get_connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]


def _count_feedback_sessions(backend: SQLiteBackend) -> int:
    with backend._get_connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM feedback_sessions").fetchone()[0]


def _get_feedback_session(backend: SQLiteBackend, client: str, session_id: str) -> dict | None:
    return backend.get_feedback_session_by_key(client, session_id)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_ingest_links_existing_feedback_session(tmp_path: Path) -> None:
    """Ingest a claude_code session file; the matching feedback_sessions row gets linked.

    Setup:
    1. feedback_sessions row with client='claude-code', session_id='deadbeef-1234',
       conversation_id=NULL.
    2. Fake .jsonl file at <tmp_path>/my-project/deadbeef-1234.jsonl with 3 messages.
    3. Run ingest_one against the parsed RawConversation.

    Assertions:
    - conversations table has 1 row.
    - feedback_sessions.conversation_id == that conversations.id.
    """
    backend = _make_backend()
    dataset_id = _seed_dataset(backend)

    # Pre-populate feedback_sessions with NULL conversation_id.
    backend.upsert_feedback_session(
        client=_CLIENT,
        session_id=_SESSION_ID,
        host=_HOST,
        started_at=_now_iso(),
    )
    fs_before = _get_feedback_session(backend, _CLIENT, _SESSION_ID)
    assert fs_before is not None
    assert fs_before["conversation_id"] is None

    # Write the fake session file.
    session_file = _write_fake_session(tmp_path, _SESSION_ID, n_messages=3)

    # Parse via ClaudeCodeSource (pure — no backend needed).
    source = ClaudeCodeSource(projects_root=tmp_path)
    raw_conv = source.parse(session_file)

    # Confirm external_id matches the session_id (filename stem).
    assert raw_conv.external_id == _SESSION_ID

    # Run ingest — H-03 wires the link call INSIDE ingest_one (after upsert_conversation).
    chunker = ConversationChunker()
    ingest_one(backend, raw_conv, chunker, [], dataset_id)

    # Assert conversation was ingested.
    assert _count_conversations(backend) == 1
    with backend._get_connection() as conn:
        conv_row = conn.execute("SELECT id FROM conversations").fetchone()
    conv_id = conv_row["id"]

    # Assert feedback_sessions.conversation_id was linked.
    fs_after = _get_feedback_session(backend, _CLIENT, _SESSION_ID)
    assert fs_after is not None, "feedback_sessions row must still exist"
    assert fs_after["conversation_id"] == conv_id, (
        f"Expected conversation_id={conv_id}, got {fs_after['conversation_id']!r}"
    )


def test_ingest_session_without_feedback_session_row_does_not_create_one(
    tmp_path: Path,
) -> None:
    """Run ingest with NO pre-existing feedback_sessions row.

    The source plugin must NOT create a feedback_sessions row.
    Only writes.py / register_session creates those rows.
    feedback_sessions must remain empty after ingest.
    """
    backend = _make_backend()
    dataset_id = _seed_dataset(backend)

    # No feedback_sessions seeded.
    assert _count_feedback_sessions(backend) == 0

    session_file = _write_fake_session(tmp_path, _SESSION_ID, n_messages=2)

    source = ClaudeCodeSource(projects_root=tmp_path)
    raw_conv = source.parse(session_file)

    chunker = ConversationChunker()
    ingest_one(backend, raw_conv, chunker, [], dataset_id)

    # Conversation was ingested.
    assert _count_conversations(backend) == 1

    # feedback_sessions must remain empty — ingest does NOT create rows there.
    assert _count_feedback_sessions(backend) == 0, (
        "ingest_one must not insert rows into feedback_sessions; "
        "only writes.py / register_session is authorised to do so"
    )


def test_link_session_to_conversation_helper_directly(tmp_path: Path) -> None:
    """Directly exercise link_session_to_conversation in an integration context.

    Uses a real migrated SQLiteBackend (not just :memory: unit test) to confirm
    the helper correctly routes through the backend method.
    """
    backend = _make_backend()
    dataset_id = _seed_dataset(backend)

    # Seed a conversations row via raw SQL (we're not ingesting — just linking).
    with backend._get_connection() as conn:
        conv_row = conn.execute(
            """
            INSERT INTO conversations
              (dataset_id, source_uri, content_hash, title, message_count, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (dataset_id, "claude-code://proj/direct-link", "hash-e2e", "Direct Link", 0, "{}"),
        ).fetchone()
        conn.commit()
    conv_id = int(conv_row["id"])

    backend.upsert_feedback_session(
        client=_CLIENT,
        session_id="direct-link-session",
        host=_HOST,
        started_at=_now_iso(),
    )

    result = link_session_to_conversation(
        backend,
        client=_CLIENT,
        session_id="direct-link-session",
        conversation_id=conv_id,
    )

    assert result is True
    row = _get_feedback_session(backend, _CLIENT, "direct-link-session")
    assert row is not None
    assert row["conversation_id"] == conv_id
