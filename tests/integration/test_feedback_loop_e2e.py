"""H-05 — End-to-end self-distillation smoke tests.

Pins all four sub-systems built in H-01..H-04 working together:
  H-01: feedback_sessions + feedback_events tables
  H-02: writes.py session-link hook + register_session
  H-03: claude_code source links session→conversation
  H-04: export_feedback_pairs CLI + writers

Three tests:

1. test_full_self_distillation_loop
   Claude Code session env vars set → 3 MCP writes (append_conversation,
   add_label, add_feedback) → feedback_sessions row with conversation_id=NULL
   → manually link → export_feedback_pairs → JSONL with ≥2 rows → each row
   has <|im_start|> in prompt.

2. test_session_writes_create_feedback_events
   Set session_id in WriteContext → 3 writes → confirm feedback_events has
   exactly 3 rows in the database.

3. test_export_skips_unlinked_session_events
   Events from a session with conversation_id IS NULL are absent from export.

Run command:
    .venv/bin/python -m pytest tests/integration/test_feedback_loop_e2e.py -v

pytestmark: pytest.mark.integration
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from corpus_forge.backends.sqlite import SQLiteBackend
from corpus_forge.export import export_feedback_pairs
from corpus_forge.mcp import writes
from corpus_forge.mcp.writes import WriteContext

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CLIENT = "claude-code"
_SESSION_ID = "h05-e2e-session-deadbeef"
_HOST = "test-host-h05"
_DATASET = "h05-self-distillation"
_CHATML_TOKEN = "<|im_start|>"

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_backend() -> SQLiteBackend:
    b = SQLiteBackend(path=":memory:")
    b.migrate()
    return b


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


def _seed_dataset(backend: SQLiteBackend, name: str = _DATASET) -> str:
    backend.get_or_create_dataset(name, "chat", "H-05 self-distillation test dataset")
    return name


def _count_feedback_events(backend: SQLiteBackend) -> int:
    with backend._get_connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM feedback_events").fetchone()[0]


def _get_feedback_session_row(backend: SQLiteBackend, client: str, session_id: str) -> dict | None:
    return backend.get_feedback_session_by_key(client, session_id)


# ---------------------------------------------------------------------------
# Test 1 — Full self-distillation loop
# ---------------------------------------------------------------------------


def test_full_self_distillation_loop(tmp_path: Path) -> None:
    """Simulate a Claude Code feedback session end-to-end.

    Steps
    -----
    1. Build in-process SQLiteBackend with migrations applied.
    2. Create WriteContext with client='claude-code' and a session_id.
    3. Via writes module:
       - append_conversation: 3-message context (sets up the chunk to label/feedback).
       - add_label: namespace='topic', value='important' on the conversation.
       - add_feedback: kind='thumbs', rating=1, text='great answer' on the conversation.
    4. Confirm feedback_sessions row exists with conversation_id=NULL (not yet linked).
    5. Call backend.link_feedback_session_to_conversation directly (simulates source ingest).
    6. Confirm feedback_sessions.conversation_id is now populated.
    7. Call export_feedback_pairs(dataset, 'chatml', tmp.jsonl).
    8. Assert file has ≥ 2 rows (add_label audit + add_feedback feedback).
    9. Each row has prompt containing <|im_start|> (chatml-templated context).
    10. Round-trip: verify each row has the required schema fields.
    """
    backend = _make_backend()
    dataset_name = _seed_dataset(backend)

    ctx = WriteContext(
        host=_HOST,
        client=_CLIENT,
        session_id=_SESSION_ID,
    )

    # --- Step 3a: append_conversation (3 messages) ---
    conv_result = writes.append_conversation(
        backend,
        ctx,
        dataset=dataset_name,
        title="H-05 E2E smoke conversation",
        messages=[
            {"role": "user", "content": "What is corpus-forge?"},
            {
                "role": "assistant",
                "content": "Corpus-forge is a personal corpus manager for LLM training.",
            },
            {"role": "user", "content": "How do I export feedback pairs?"},
        ],
    )
    conv_id = conv_result["conversation_id"]
    assert isinstance(conv_id, int), (
        f"append_conversation must return an integer conversation_id; got {conv_id!r}"
    )

    # --- Step 3b: add_label ---
    label_result = writes.add_label(
        backend,
        ctx,
        entity_type="conversation",
        entity_id=conv_id,
        namespace="topic",
        value="important",
    )
    assert "audit_id" in label_result, "add_label must return audit_id"

    # --- Step 3c: add_feedback ---
    feedback_result = writes.add_feedback(
        backend,
        ctx,
        entity_type="conversation",
        entity_id=conv_id,
        kind="thumbs",
        rating=1,
        text="great answer",
    )
    assert "feedback_id" in feedback_result, "add_feedback must return feedback_id"
    assert "audit_id" in feedback_result, "add_feedback must return audit_id"

    # --- Step 4: confirm feedback_sessions row exists with conversation_id=NULL ---
    fs_before_link = _get_feedback_session_row(backend, _CLIENT, _SESSION_ID)
    assert fs_before_link is not None, (
        "feedback_sessions row must exist after MCP writes with session_id"
    )
    assert fs_before_link["conversation_id"] is None, (
        "feedback_sessions.conversation_id must be NULL before the source-plugin link step; "
        f"got {fs_before_link['conversation_id']!r}"
    )

    # --- Step 5: simulate source-plugin link (H-03 ingest step) ---
    linked = backend.link_feedback_session_to_conversation(_CLIENT, _SESSION_ID, conv_id)
    assert linked, "link_feedback_session_to_conversation must return True for a valid link"

    # --- Step 6: confirm conversation_id is now populated ---
    fs_after_link = _get_feedback_session_row(backend, _CLIENT, _SESSION_ID)
    assert fs_after_link is not None, "feedback_sessions row must still exist after link"
    assert fs_after_link["conversation_id"] == conv_id, (
        f"feedback_sessions.conversation_id must equal {conv_id}; "
        f"got {fs_after_link['conversation_id']!r}"
    )

    # --- Step 7: export_feedback_pairs ---
    out_path = tmp_path / "h05_e2e.jsonl"
    export_feedback_pairs(
        dataset=dataset_name,
        template="chatml",
        out_path=out_path,
        format="jsonl",
        backend=backend,
    )

    # --- Step 8: file has ≥ 2 rows ---
    assert out_path.exists(), "export must create the output file"
    rows = _read_jsonl(out_path)
    assert len(rows) >= 2, (
        f"expected ≥ 2 rows (at least add_label audit + add_feedback feedback); got {len(rows)}"
    )

    # --- Step 9: each row has <|im_start|> in prompt (chatml template) ---
    for i, row in enumerate(rows):
        prompt = row.get("prompt", "")
        assert _CHATML_TOKEN in prompt, (
            f"row[{i}] prompt missing chatml token {_CHATML_TOKEN!r}; prompt={prompt!r}"
        )

    # --- Step 10: round-trip schema check ---
    required_keys = {
        "feedback_event_id",
        "feedback_session_id",
        "client",
        "session_id",
        "host",
        "prompt",
        "response",
        "after",
        "kind",
        "ts",
    }
    for i, row in enumerate(rows):
        missing = required_keys - set(row.keys())
        assert not missing, f"row[{i}] missing required keys {sorted(missing)}: {row}"

    # kinds must be a subset of valid values
    kinds = {row["kind"] for row in rows}
    assert kinds <= {"audit", "feedback"}, (
        f"unexpected kind values in export output: {kinds - {'audit', 'feedback'}}"
    )

    # At least one feedback kind must be present (from add_feedback)
    assert "feedback" in kinds, "expected at least one 'feedback' kind row from add_feedback"

    # Optional round-trip via datasets.load_dataset
    try:
        import datasets as _ds  # type: ignore[import]

        loaded = _ds.load_dataset("json", data_files=str(out_path), split="train")
        assert len(loaded) >= 2, "datasets.load_dataset round-trip must recover ≥ 2 rows"
    except ImportError:
        pass  # datasets not installed — skip silently


# ---------------------------------------------------------------------------
# Test 2 — Session writes create feedback_events rows
# ---------------------------------------------------------------------------


def test_session_writes_create_feedback_events() -> None:
    """Set session env in WriteContext, perform 3 writes, assert 3 feedback_events rows.

    The three writes are:
      1. append_conversation  → 1 feedback_event (audit)
      2. add_label            → 1 feedback_event (audit)
      3. add_feedback         → 1 feedback_event (audit + feedback, counts as 1 row)

    Total: exactly 3 feedback_events rows.
    """
    backend = _make_backend()
    dataset_name = _seed_dataset(backend, name="h05-events-test")

    ctx = WriteContext(
        host=_HOST,
        client=_CLIENT,
        session_id="h05-events-session-abc123",
    )

    # Confirm clean slate
    assert _count_feedback_events(backend) == 0, "feedback_events must be empty before any writes"

    # Write 1: append_conversation
    conv_result = writes.append_conversation(
        backend,
        ctx,
        dataset=dataset_name,
        title="Events count smoke",
        messages=[
            {"role": "user", "content": "Message 1"},
            {"role": "assistant", "content": "Response 1"},
            {"role": "user", "content": "Message 2"},
        ],
    )
    conv_id = conv_result["conversation_id"]
    assert _count_feedback_events(backend) == 1, (
        "append_conversation must create exactly 1 feedback_event"
    )

    # Write 2: add_label
    writes.add_label(
        backend,
        ctx,
        entity_type="conversation",
        entity_id=conv_id,
        namespace="quality",
        value="high",
    )
    assert _count_feedback_events(backend) == 2, (
        "add_label must create exactly 1 more feedback_event (total 2)"
    )

    # Write 3: add_feedback
    writes.add_feedback(
        backend,
        ctx,
        entity_type="conversation",
        entity_id=conv_id,
        kind="thumbs",
        rating=1,
        text="great answer",
    )
    assert _count_feedback_events(backend) == 3, (
        "add_feedback must create exactly 1 more feedback_event (total 3)"
    )


# ---------------------------------------------------------------------------
# Test 3 — Export skips unlinked session events
# ---------------------------------------------------------------------------


def test_export_skips_unlinked_session_events(tmp_path: Path) -> None:
    """Events from an unlinked session (conversation_id IS NULL) are absent from export.

    This is the H-05 version of the H-04 skip-unlinked test.  It explicitly
    verifies the data path: writes go in, session remains unlinked, export
    produces 0 rows.

    Differs from H-04 in that we also assert the feedback_sessions row itself
    exists (with conversation_id=NULL) to prove the writes landed — they are
    just filtered out at export time.
    """
    backend = _make_backend()
    dataset_name = _seed_dataset(backend, name="h05-unlinked-test")

    unlinked_session_id = "h05-unlinked-session-xyz789"
    ctx = WriteContext(
        host=_HOST,
        client=_CLIENT,
        session_id=unlinked_session_id,
    )

    # Write events — but do NOT link the session to a conversation
    conv_result = writes.append_conversation(
        backend,
        ctx,
        dataset=dataset_name,
        title="Unlinked H-05 conversation",
        messages=[
            {"role": "user", "content": "This session will not be linked."},
            {"role": "assistant", "content": "Understood."},
        ],
    )
    conv_id = conv_result["conversation_id"]

    writes.add_label(
        backend,
        ctx,
        entity_type="conversation",
        entity_id=conv_id,
        namespace="status",
        value="unlinked",
    )

    writes.add_feedback(
        backend,
        ctx,
        entity_type="conversation",
        entity_id=conv_id,
        kind="thumbs",
        rating=0,
        text="should be skipped",
    )

    # Confirm the session row exists with conversation_id=NULL
    fs_row = _get_feedback_session_row(backend, _CLIENT, unlinked_session_id)
    assert fs_row is not None, "feedback_sessions row must exist after writes even when not linked"
    assert fs_row["conversation_id"] is None, (
        "feedback_sessions.conversation_id must remain NULL — no link was requested; "
        f"got {fs_row['conversation_id']!r}"
    )

    # Confirm 3 feedback_events rows exist (writes landed)
    assert _count_feedback_events(backend) == 3, (
        f"expected 3 feedback_events rows from 3 writes; got {_count_feedback_events(backend)}"
    )

    # Export must produce 0 rows (all events belong to unlinked session)
    out_path = tmp_path / "unlinked_h05.jsonl"
    export_feedback_pairs(
        dataset=dataset_name,
        template="chatml",
        out_path=out_path,
        format="jsonl",
        backend=backend,
    )

    assert out_path.exists(), "export must create the output file even when empty"
    rows = _read_jsonl(out_path)
    assert rows == [], (
        f"export must produce 0 rows when all events belong to an unlinked session; "
        f"got {len(rows)} rows"
    )
