"""H-04 RED — Integration test: full session → writes → export round-trip.

Simulates a complete self-distillation workflow:
1. Create a conversation via in-process MCP (append_conversation).
2. Add a label and feedback via MCP (add_label, add_feedback) with CORPUS_FORGE_SESSION_ID set.
3. Manually link the feedback_session to the conversation (simulating source-plugin link step).
4. Run export_feedback_pairs.
5. Assert output file has rows with proper prompts.

All tests FAIL RED because ``corpus_forge.export.export_feedback_pairs`` does not exist.

Run command:
    .venv/bin/python -m pytest tests/integration/test_self_distillation_export.py -v

pytestmark: pytest.mark.integration
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from corpus_forge.backends.sqlite import SQLiteBackend
from corpus_forge.mcp import writes
from corpus_forge.mcp.writes import WriteContext

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Import target — will fail RED: export_feedback_pairs not implemented
# ---------------------------------------------------------------------------

from corpus_forge.export import export_feedback_pairs  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_backend() -> SQLiteBackend:
    b = SQLiteBackend(path=":memory:")
    b.migrate()
    return b


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


def _seed_dataset(backend: SQLiteBackend) -> str:
    """Ensure the dataset 'cf-self-docs' exists; return its name."""
    backend.get_or_create_dataset("cf-self-docs", "chat", "self-distillation test dataset")
    return "cf-self-docs"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFullRoundTripSessionWritesExport:
    def test_full_round_trip_session_writes_export(self, tmp_path: Path) -> None:
        """Full round-trip: append_conversation + add_label + add_feedback → export_feedback_pairs.

        Steps:
        1. Create a WriteContext with session_id='test-session-123'.
        2. append_conversation — creates conversation X in the dataset.
        3. add_label — on a chunk in conversation X; emits audit + feedback_event.
        4. add_feedback — on the conversation; emits audit + feedback_event with both ids.
        5. Link feedback_session.conversation_id = X.id (simulating source-plugin).
        6. export_feedback_pairs.
        7. Assert: file has >=2 rows; each has a non-empty prompt; kinds include 'audit'
           and 'feedback'.
        """
        backend = _make_backend()
        dataset_name = _seed_dataset(backend)

        ctx = WriteContext(
            host="test-host",
            client="claude-code",
            session_id="test-session-123",
        )

        # Step 2: append_conversation — creates conversation X
        conv_result = writes.append_conversation(
            backend,
            ctx,
            dataset=dataset_name,
            title="Self-distillation round-trip",
            messages=[
                {"role": "user", "content": "What is corpus-forge?"},
                {"role": "assistant", "content": "It is a personal corpus manager."},
                {"role": "user", "content": "How do I export?"},
                {"role": "assistant", "content": "Use corpus-forge export chat."},
            ],
        )
        conv_id = conv_result["conversation_id"]
        assert conv_id is not None, "append_conversation must return a conversation_id"

        # Step 3: add_label on the conversation
        label_result = writes.add_label(
            backend,
            ctx,
            entity_type="conversation",
            entity_id=conv_id,
            namespace="topic",
            value="corpus-forge",
        )
        assert "audit_id" in label_result

        # Step 4: add_feedback on the conversation
        feedback_result = writes.add_feedback(
            backend,
            ctx,
            entity_type="conversation",
            entity_id=conv_id,
            kind="thumbs",
            rating=1,
            text="Good explanation",
        )
        assert "feedback_id" in feedback_result
        assert "audit_id" in feedback_result

        # Step 5: link feedback_session → conversation (simulates source-plugin link)
        linked = backend.link_feedback_session_to_conversation(
            "claude-code", "test-session-123", conv_id
        )
        assert linked, "link_feedback_session_to_conversation must return True"

        # Step 6: export
        out_path = tmp_path / "round_trip.jsonl"
        export_feedback_pairs(
            dataset=dataset_name,
            template="chatml",
            out_path=out_path,
            format="jsonl",
            backend=backend,
        )

        # Step 7: assertions
        assert out_path.exists(), "output file must exist after export"
        rows = _read_jsonl(out_path)

        # We created events from append_conversation + add_label + add_feedback.
        # At minimum, add_label and add_feedback created events (append_conversation
        # may also generate one). Must have >=2 rows.
        assert len(rows) >= 2, (
            f"expected >=2 rows (at least one per write with session_id); got {len(rows)}"
        )

        # Every row must have a non-empty prompt
        for row in rows:
            assert row.get("prompt"), f"every row must have a non-empty prompt; got row: {row}"

        # Kinds present must be a subset of {'audit', 'feedback'}
        kinds = {row["kind"] for row in rows}
        assert kinds <= {"audit", "feedback"}, f"unexpected kind values in output: {kinds}"

        # At least one row of each kind must be present
        assert "audit" in kinds, "expected at least one 'audit' kind row"
        assert "feedback" in kinds, "expected at least one 'feedback' kind row"

        # Each row must have the required keys
        required = {
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
        for row in rows:
            missing = required - set(row.keys())
            assert not missing, f"row missing keys {missing}: {row}"

    def test_export_skips_events_when_session_not_linked(self, tmp_path: Path) -> None:
        """Events under a session with conversation_id IS NULL are skipped without error."""
        backend = _make_backend()
        dataset_name = _seed_dataset(backend)

        ctx = WriteContext(
            host="test-host",
            client="claude-code",
            session_id="unlinked-session-456",
        )

        # Write some events but do NOT link the session to a conversation
        conv_result = writes.append_conversation(
            backend,
            ctx,
            dataset=dataset_name,
            title="Unlinked conversation",
            messages=[{"role": "user", "content": "Hello"}],
        )
        conv_id = conv_result["conversation_id"]

        writes.add_label(
            backend,
            ctx,
            entity_type="conversation",
            entity_id=conv_id,
            namespace="ns",
            value="val",
        )
        # Deliberately skip: backend.link_feedback_session_to_conversation(...)

        out_path = tmp_path / "unlinked.jsonl"
        # Must not raise even though no session is linked
        export_feedback_pairs(
            dataset=dataset_name,
            template="chatml",
            out_path=out_path,
            format="jsonl",
            backend=backend,
        )

        assert out_path.exists(), "output file must exist"
        rows = _read_jsonl(out_path)
        assert rows == [], (
            f"expected 0 rows when no session is linked to a conversation; got {len(rows)}"
        )
