"""E2E integration test — Claude Code self-ingest of this repo's own sessions.

Backs RFC `rfc-claude-code-self-ingest-e2e` (P0). The existing
`test_claude_code_session_link_e2e.py` exercises the *link* mechanic with a
synthesised 3-message JSONL; this test instead drives the full pipeline
(parser → conversation chunker → in-memory SQLite backend → fake embedder →
HybridRetriever round-trip) against a real (anonymised) Claude Code session
file checked in under ``tests/fixtures/claude_code_self_ingest/``.

The fixture exercises every event type the parser knows about: ``user``,
``assistant``, ``attachment``, ``ai-title``, ``last-prompt``, ``pr-link``,
``permission-mode``, ``file-history-snapshot``, plus nested
``tool_use`` / ``tool_result`` blocks inside ``message.content``. See
``tests/fixtures/claude_code_self_ingest/README.md`` for the
anonymisation transformations.

Run:
    .venv/bin/python -m pytest \
        tests/integration/test_claude_code_self_ingest_e2e.py -v

pytestmark: pytest.mark.integration
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from corpus_forge.backends.sqlite import SQLiteBackend
from corpus_forge.chunkers.conversation import ConversationChunker
from corpus_forge.ingest import ingest_one
from corpus_forge.sources.claude_code import ClaudeCodeSource

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FIXTURE_ROOT = Path(__file__).parent.parent / "fixtures" / "claude_code_self_ingest"
PROJECTS_ROOT = FIXTURE_ROOT / "projects"
PROJECT_SLUG = "-home-test-user-workspace-corpus-forge"
FIXTURE_SESSION_ID = "fed1bafe-0001-4000-8000-000000000001"
FIXTURE_SESSION_FILE = PROJECTS_ROOT / PROJECT_SLUG / f"{FIXTURE_SESSION_ID}.jsonl"

FAKE_EMBEDDER_NAME = "fake_self_ingest"
FAKE_DIMENSION = 8

_CLIENT = "claude-code"
_HOST = "test-host"


# ---------------------------------------------------------------------------
# Fake embedder — deterministic, mirrors the FakeEmbedder pattern from
# `tests/integration/test_chunk_reuse_e2e.py` but tailored to the
# 8-dim/fake_self_ingest contract this file uses.
# ---------------------------------------------------------------------------


class _FakeEmbedder:
    name: str = FAKE_EMBEDDER_NAME
    provider: str = "fake"
    model_id: str = "fake-self-ingest-v1"
    dimension: int = FAKE_DIMENSION
    normalized: bool = True
    distance: str = "cosine"

    def _vec(self, text: str) -> np.ndarray:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        vec = np.frombuffer(digest[:FAKE_DIMENSION], dtype=np.uint8).astype(np.float32)
        vec = (vec + 1.0) / 256.0
        norm = np.linalg.norm(vec)
        return vec / norm

    def encode(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        return np.stack([self._vec(t) for t in texts])

    def encode_query(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        # Symmetric model — query and document share the same encoder.
        return self.encode(texts, batch_size=batch_size)

    def warmup(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _make_backend() -> SQLiteBackend:
    b = SQLiteBackend(path=":memory:")
    b.migrate()
    return b


def _seed_dataset(backend: SQLiteBackend, name: str) -> int:
    return backend.get_or_create_dataset(name, "chat", "self-ingest e2e test dataset")


def _conversations(backend: SQLiteBackend) -> list[dict]:
    with backend._get_connection() as conn:
        rows = conn.execute(
            "SELECT id, source_uri, content_hash, title, message_count, metadata FROM conversations"
        ).fetchall()
    return [dict(r) for r in rows]


def _messages_for_conversation(backend: SQLiteBackend, conversation_id: int) -> list[dict]:
    with backend._get_connection() as conn:
        rows = conn.execute(
            "SELECT id, role, content, tool_calls, tool_results, metadata "
            "FROM messages WHERE conversation_id = ? ORDER BY id",
            (conversation_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFixtureShape:
    """Sanity-check that the fixture is well-formed before exercising the parser.

    These tests run in milliseconds and fail fast if the on-disk fixture
    has rotted (e.g. someone deletes the JSONL or strips its event types).
    """

    def test_fixture_file_exists(self) -> None:
        assert FIXTURE_SESSION_FILE.is_file(), (
            f"Fixture missing at {FIXTURE_SESSION_FILE}. See "
            f"{FIXTURE_ROOT / 'README.md'} for what should be there."
        )

    def test_fixture_covers_full_parser_surface(self) -> None:
        """Every event type the parser distinguishes must be represented once."""
        required_top_level_types = {
            "user",
            "assistant",
            "attachment",
            "ai-title",
            "last-prompt",
            "pr-link",
            "permission-mode",
            "file-history-snapshot",
        }
        seen_top: set[str] = set()
        seen_tool_use = False
        seen_tool_result = False
        for raw_line in FIXTURE_SESSION_FILE.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            entry = json.loads(line)
            event_type = entry.get("type")
            if isinstance(event_type, str):
                seen_top.add(event_type)
            msg = entry.get("message") if isinstance(entry.get("message"), dict) else None
            content = msg.get("content") if msg else None
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "tool_use":
                            seen_tool_use = True
                        if block.get("type") == "tool_result":
                            seen_tool_result = True
        missing = required_top_level_types - seen_top
        assert not missing, f"Fixture missing top-level event types: {missing}"
        assert seen_tool_use, "Fixture must contain at least one tool_use content block"
        assert seen_tool_result, "Fixture must contain at least one tool_result content block"


class TestSelfIngestE2E:
    """Full-pipeline ingest of the anonymised real session."""

    def test_metadata_fields_present(self) -> None:
        """RawConversation.metadata folds the parser's session-level fields.

        Hard-pins the contract that `session_id`, `cwd`, `git_branch`, and
        `client_version` survive the parse — these are the four fields the
        rest of the system (curation, retrieval filters, exports) reads.
        """
        source = ClaudeCodeSource(projects_root=PROJECTS_ROOT)
        raw = source.parse(FIXTURE_SESSION_FILE)

        meta = raw.metadata
        assert meta.get("session_id"), f"session_id empty: {meta!r}"
        assert meta.get("cwd"), f"cwd empty: {meta!r}"
        assert meta.get("git_branch"), f"git_branch empty: {meta!r}"
        assert meta.get("client_version"), f"client_version empty: {meta!r}"

        # Fixture-stable values — also verifies the anonymisation map.
        assert meta["session_id"] == FIXTURE_SESSION_ID
        assert meta["cwd"] == "/home/test-user/workspace/corpus-forge"
        assert meta["git_branch"] == "main"

    def test_ingest_happy_path(self) -> None:
        """`ingest_one` writes a conversations row + messages with tool blocks.

        Asserts the regression for the bug PR #29 fixed: ``permission-mode``
        events must NOT produce empty-content message rows.
        """
        backend = _make_backend()
        dataset_id = _seed_dataset(backend, "self-ingest-happy")

        source = ClaudeCodeSource(projects_root=PROJECTS_ROOT)
        raw = source.parse(FIXTURE_SESSION_FILE)
        chunker = ConversationChunker()

        ingest_one(backend, raw, chunker, [], dataset_id)

        # Conversations table — exactly one row, source_uri scheme intact.
        convs = _conversations(backend)
        assert len(convs) == 1, f"expected 1 conversation row, got {len(convs)}"
        conv = convs[0]
        assert conv["source_uri"].startswith("claude-code://"), conv["source_uri"]

        conv_meta = json.loads(conv["metadata"]) if conv["metadata"] else {}
        assert conv_meta.get("session_id") == FIXTURE_SESSION_ID
        assert conv_meta.get("cwd") == "/home/test-user/workspace/corpus-forge"
        assert conv_meta.get("git_branch") == "main"
        assert conv_meta.get("client_version"), f"client_version missing: {conv_meta!r}"
        # pr-link + permission-mode events are folded into metadata, not messages.
        assert isinstance(conv_meta.get("pr_links"), list) and conv_meta["pr_links"], (
            "pr_links should be populated from the appended pr-link event"
        )
        assert (
            isinstance(conv_meta.get("permission_modes"), list) and conv_meta["permission_modes"]
        ), "permission_modes should be populated from permission-mode events"

        # Messages — at least one with tool_calls and at least one with tool_results.
        msgs = _messages_for_conversation(backend, conv["id"])
        assert len(msgs) > 0, "no messages persisted"

        # Regression for the PR #29 bug: `permission-mode` (and any other
        # non-message event type) must NOT produce message rows. The fixture
        # has 11 user + 12 assistant + 5 attachment = 28 message-eligible
        # events; any deviation upward means a metadata event leaked through
        # as a message.
        expected_msg_count = 28
        assert len(msgs) == expected_msg_count, (
            f"expected {expected_msg_count} message rows "
            f"(11 user + 12 assistant + 5 attachment), got {len(msgs)} — "
            "a metadata event type (permission-mode / ai-title / pr-link / "
            "file-history-snapshot / last-prompt / queue-operation / system) "
            "leaked through as a message row"
        )

        # Messages that are pure tool_use have empty `content` by design —
        # the text part is the tool call, surfaced via `tool_calls`. The
        # legitimate check is: any empty-content row must carry a
        # non-empty tool_calls or tool_results payload. SQLite stores
        # these as JSON-serialised TEXT, so we decode before checking —
        # `"[]"` is a truthy string but an empty payload, and we want to
        # reject that shape as a regression.
        def _decoded(val: object) -> list:
            if val is None:
                return []
            if isinstance(val, str):
                parsed = json.loads(val)
                assert isinstance(parsed, list), (
                    f"expected JSON-encoded list for tool field, got "
                    f"{type(parsed).__name__} from {val!r}"
                )
                return parsed
            assert isinstance(val, list), (
                f"expected JSON string or list for tool field, got {type(val).__name__}"
            )
            return val

        for m in msgs:
            if not m["content"]:
                tc = _decoded(m["tool_calls"])
                tr = _decoded(m["tool_results"])
                assert tc or tr, (
                    f"empty-content message {m['id']} has neither tool_calls "
                    f"nor tool_results — would be a permission-mode leak: {m!r}"
                )

        has_tool_calls = any(_decoded(m["tool_calls"]) for m in msgs)
        has_tool_results = any(_decoded(m["tool_results"]) for m in msgs)
        assert has_tool_calls, "no message landed with non-empty tool_calls"
        assert has_tool_results, "no message landed with non-empty tool_results"

    def test_session_link_lands_during_full_ingest(self) -> None:
        """Pre-populating feedback_sessions then ingesting links them."""
        backend = _make_backend()
        dataset_id = _seed_dataset(backend, "self-ingest-link")

        backend.upsert_feedback_session(
            client=_CLIENT,
            session_id=FIXTURE_SESSION_ID,
            host=_HOST,
            started_at=_now_iso(),
        )
        before = backend.get_feedback_session_by_key(_CLIENT, FIXTURE_SESSION_ID)
        assert before is not None
        assert before["conversation_id"] is None

        source = ClaudeCodeSource(projects_root=PROJECTS_ROOT)
        raw = source.parse(FIXTURE_SESSION_FILE)
        chunker = ConversationChunker()

        ingest_one(backend, raw, chunker, [], dataset_id, source=source)

        convs = _conversations(backend)
        assert len(convs) == 1
        conv_id = convs[0]["id"]

        after = backend.get_feedback_session_by_key(_CLIENT, FIXTURE_SESSION_ID)
        assert after is not None
        assert after["conversation_id"] == conv_id, (
            f"expected conversation_id={conv_id}, got {after['conversation_id']!r}"
        )

    def test_retrieval_round_trip(self) -> None:
        """Ingest with a real embedder; HybridRetriever can round-trip.

        We can't assert *which* chunk wins (the fake embedder + RRF fusion
        depend on full corpus layout), only that retrieval returns at least
        one Hit whose `conversation_id` matches the ingested conversation —
        i.e. the dense+lexical lookup path is wired through to the
        ingested data without errors.
        """
        from corpus_forge.retrieval import HybridRetriever
        from corpus_forge.retrieval.types import SearchOptions

        backend = _make_backend()
        dataset_id = _seed_dataset(backend, "self-ingest-retrieve")
        embedder = _FakeEmbedder()
        embedder_id = backend.register_embedder(embedder)

        source = ClaudeCodeSource(projects_root=PROJECTS_ROOT)
        raw = source.parse(FIXTURE_SESSION_FILE)
        chunker = ConversationChunker()
        ingest_one(backend, raw, chunker, [embedder], dataset_id, source=source)

        convs = _conversations(backend)
        assert len(convs) == 1
        conv_id = convs[0]["id"]

        retriever = HybridRetriever(
            backend=backend,
            embedder=embedder,
            embedder_id=embedder_id,
        )
        # Query for a token that the fixture definitely contains (the
        # session is a tmux troubleshooting transcript; "tmux" is dense
        # in the user/assistant content).
        out = retriever.search(
            "tmux",
            SearchOptions(k=5, dataset="self-ingest-retrieve", fusion="rrf"),
        )
        assert isinstance(out, list)
        assert len(out) >= 1, "HybridRetriever returned 0 hits over the self-ingest corpus"

        with backend._get_connection() as conn:
            rows = conn.execute(
                "SELECT id FROM messages WHERE conversation_id = ?", (conv_id,)
            ).fetchall()
        message_ids = {int(r["id"]) for r in rows}

        with backend._get_connection() as conn:
            chunk_rows = conn.execute(
                "SELECT id, message_id FROM chunks WHERE message_id IN "
                "(SELECT id FROM messages WHERE conversation_id = ?)",
                (conv_id,),
            ).fetchall()
        chunk_ids_for_conv = {int(r["id"]) for r in chunk_rows}

        hit_ids = {int(h.chunk_id) for h in out}
        assert hit_ids & chunk_ids_for_conv, (
            f"none of the retrieved chunks belong to the ingested conversation; "
            f"hits={hit_ids}, conv_chunks={chunk_ids_for_conv}, "
            f"conv_messages={message_ids}"
        )


class TestFixtureAnonymisation:
    """Pin the anonymisation contract — fail if real PII slips into the fixture."""

    def test_no_real_home_paths(self) -> None:
        """Only `/home/test-user` is allowed under the fixture tree."""
        import re

        # Match `/home/<word>` where <word> is anything except `test-user`.
        bad = re.compile(r"/home/(?!test-user[/\"\s])[A-Za-z0-9._-]+")
        for path in FIXTURE_ROOT.rglob("*"):
            if not path.is_file() or path.suffix == ".md":
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            hits = bad.findall(text)
            assert not hits, f"{path}: real home paths leaked through scrub: {hits[:5]}"

    def test_only_fixture_uuids_present(self) -> None:
        """Every UUID in the fixture must be in the `fed1bafe-` namespace."""
        import re

        uuid_re = re.compile(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            re.IGNORECASE,
        )
        for path in FIXTURE_ROOT.rglob("*.jsonl"):
            text = path.read_text(encoding="utf-8")
            for found in uuid_re.findall(text):
                assert found.lower().startswith("fed1bafe-"), (
                    f"{path}: unscrubbed UUID {found!r} — must live in the "
                    f"fed1bafe-* fixture namespace per the README"
                )
