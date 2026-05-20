"""Q1-T3 RED — Unit tests for SDFT capture hooks in commit_curation and rate_search_result.

Verifies that the SDFT-format demonstration capture hooks fire at the right
times and produce correctly-shaped rows.  These are *unit* tests: they wire
the MCP dispatch layer in-process against a real SQLiteBackend(:memory:) so
the assertions can inspect actual DB rows without needing Docker or the full
test-containers setup.

Hook contracts under test
--------------------------
1. ``commit_curation`` SDFT hook (``source="curation_commit"``):
   - When ``set_description`` is provided AND differs from the current chunk
     description, one ``sdft_demonstrations`` row is written with:
       - ``source = "curation_commit"``
       - ``query`` derived from the chunk text (first 200 chars).
       - ``student_messages = [{role: "assistant", content: <prior_description>}]``
       - ``teacher_messages = [{role: "user", content: <curation_prompt>}]``
         where ``curation_prompt`` is the feedback text supplied in the commit
         (or a reasonable sentinel when feedback is absent).
       - ``target = <new_description>``
   - When ``set_description`` is NOT provided or is the same as the current
     description, no SDFT row is written.
   - Pure label/metadata-only commits (no description change) do NOT write an
     SDFT row.

2. ``rate_search_result`` SDFT hook (``source="rate_search_result"``):
   - When ``signal="thumbs_down"`` AND ``replacement_chunk_id`` is not None,
     one ``sdft_demonstrations`` row is written with:
       - ``source = "rate_search_result"``
       - ``target`` = the text of the replacement chunk.
   - When ``signal="thumbs_down"`` without ``replacement_chunk_id``, no SDFT
     row is written.
   - When ``signal="thumbs_up"`` (any signal other than thumbs_down) with
     ``replacement_chunk_id``, no SDFT row is written.

RED state
---------
``corpus_forge.sdft`` package does not yet exist.  Every test that touches
the SDFT hook is expected to fail with:

  - ``ModuleNotFoundError: No module named 'corpus_forge.sdft'``  (import-time)
  - OR ``ImportError`` on ``corpus_forge.sdft.sources.SDFTSource``
  - OR zero rows in ``sdft_demonstrations`` (the hook simply doesn't fire yet)

Run command::

    uv run pytest tests/unit/test_sdft_capture_hooks.py -x 2>&1 | tail -30
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from corpus_forge.backends.sqlite import SQLiteBackend
from corpus_forge.mcp.server import build_server

mcp = pytest.importorskip("mcp")
from mcp import types as mcp_types  # noqa: E402

# ---------------------------------------------------------------------------
# In-process MCP harness (mirrors test_mcp_rate_search_result.py)
# ---------------------------------------------------------------------------


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _call_raw(server: Any, name: str, arguments: dict[str, Any]) -> Any:
    """Return the raw CallToolResult root (do NOT raise on isError)."""
    handler = server.request_handlers[mcp_types.CallToolRequest]
    request = mcp_types.CallToolRequest(
        method="tools/call",
        params=mcp_types.CallToolRequestParams(name=name, arguments=arguments),
    )
    result = _run(handler(request))
    return result.root if hasattr(result, "root") else result


def _payload(root: Any) -> dict:
    content = getattr(root, "content", [])
    return json.loads(content[0].text)


def _is_error(root: Any) -> bool:
    return bool(getattr(root, "isError", False))


def _error_text(root: Any) -> str:
    content = getattr(root, "content", [])
    return "".join(getattr(b, "text", "") for b in content)


# ---------------------------------------------------------------------------
# Backend setup + fixtures
# ---------------------------------------------------------------------------


def _fresh_backend() -> SQLiteBackend:
    b = SQLiteBackend(path=":memory:")
    b.migrate()
    return b


class _BackedRetriever:
    def __init__(self, backend: SQLiteBackend) -> None:
        self.backend = backend

    def search(self, query: str, options: Any) -> list[Any]:
        return []


def _build_server(backend: SQLiteBackend) -> Any:
    retriever = _BackedRetriever(backend)
    return build_server(retriever_builder=lambda: retriever, writes_enabled=True)


def _seed_corpus(backend: SQLiteBackend) -> dict[str, int]:
    """Insert dataset, document, two chunks; return ids."""
    with backend._get_connection() as conn:
        dataset_id: int = conn.execute(
            "INSERT INTO datasets (name, kind, description) VALUES (?, ?, ?) RETURNING id",
            ("sdft-hook-test-ds", "text", "SDFT hook test dataset"),
        ).fetchone()[0]

        document_id: int = conn.execute(
            "INSERT INTO documents"
            " (dataset_id, source_uri, content_hash, title, text, metadata)"
            " VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
            (
                dataset_id,
                "test://sdft-hook/doc.md",
                "doc-hash-sdft-001",
                "SDFT Hook Doc",
                "Quantum electrodynamics is the relativistic quantum field theory "
                "of electrodynamics. It describes how light and matter interact and is the "
                "first theory where full agreement between quantum mechanics and special "
                "relativity is achieved.",
                "{}",
            ),
        ).fetchone()[0]

        # chunk_a has a description that can be improved.
        chunk_a_id: int = conn.execute(
            "INSERT INTO chunks (document_id, chunk_index, text, description, metadata)"
            " VALUES (?, ?, ?, ?, ?) RETURNING id",
            (
                document_id,
                0,
                "Feynman diagrams are pictorial representations of mathematical expressions "
                "describing the behavior and interaction of subatomic particles.",
                "A diagram type used in physics.",
                "{}",
            ),
        ).fetchone()[0]

        # chunk_b will serve as the replacement chunk in rate_search_result tests.
        chunk_b_id: int = conn.execute(
            "INSERT INTO chunks (document_id, chunk_index, text, description, metadata)"
            " VALUES (?, ?, ?, ?, ?) RETURNING id",
            (
                document_id,
                1,
                "The fine structure constant alpha ≈ 1/137 characterises the strength of the "
                "electromagnetic interaction between elementary charged particles.",
                "Fine structure constant description.",
                "{}",
            ),
        ).fetchone()[0]

        conn.commit()

    return {
        "dataset_id": dataset_id,
        "document_id": document_id,
        "chunk_a_id": chunk_a_id,
        "chunk_b_id": chunk_b_id,
    }


def _count_sdft_rows(backend: SQLiteBackend) -> int:
    rows = backend._execute("SELECT COUNT(*) AS n FROM sdft_demonstrations")
    return int(rows[0]["n"])


def _get_sdft_rows(backend: SQLiteBackend) -> list[dict]:
    return backend._execute("SELECT * FROM sdft_demonstrations ORDER BY id")


def _seed_search_session(backend: SQLiteBackend, query: str, dataset_id: int) -> int:
    """Insert a search_sessions row for rate_search_result tests; return id."""
    rows = backend._execute(
        "INSERT INTO search_sessions (query, dataset_id) VALUES (?, ?) RETURNING id",
        (query, dataset_id),
    )
    return int(rows[0]["id"])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def backend() -> SQLiteBackend:
    return _fresh_backend()


@pytest.fixture
def corpus(backend: SQLiteBackend) -> dict[str, int]:
    return _seed_corpus(backend)


@pytest.fixture
def server(backend: SQLiteBackend, corpus: dict) -> Any:
    return _build_server(backend)


# ===========================================================================
# 1. commit_curation SDFT hook
# ===========================================================================


class TestCommitCurationSdftHook:
    """SDFT row is written when commit_curation changes the chunk description."""

    def test_description_change_writes_sdft_row(
        self, backend: SQLiteBackend, corpus: dict, server: Any
    ) -> None:
        """commit_curation with a new set_description writes one SDFT row."""
        before = _count_sdft_rows(backend)

        root = _call_raw(
            server,
            "commit_curation",
            {
                "chunk_id": corpus["chunk_a_id"],
                "set_description": "Feynman diagrams provide a perturbative expansion "
                "of the S-matrix in quantum field theory, encoding particle "
                "interactions as topological graphs with propagators and vertices.",
                "feedback": {
                    "kind": "curation",
                    "text": "Improve the description to be more technically precise.",
                },
            },
        )

        assert not _is_error(root), f"commit_curation failed: {_error_text(root)}"
        after = _count_sdft_rows(backend)
        assert after == before + 1, (
            f"Expected 1 new SDFT row after description-changing commit_curation; "
            f"got {after - before} new rows"
        )

    def test_description_change_sdft_source_is_curation_commit(
        self, backend: SQLiteBackend, corpus: dict, server: Any
    ) -> None:
        """The written SDFT row must have source='curation_commit'."""
        _call_raw(
            server,
            "commit_curation",
            {
                "chunk_id": corpus["chunk_a_id"],
                "set_description": "Improved Feynman diagram description for QED.",
            },
        )

        rows = _get_sdft_rows(backend)
        assert rows, "Expected at least one SDFT row"
        sdft_row = rows[-1]
        assert sdft_row["source"] == "curation_commit", (
            f"Expected source='curation_commit'; got {sdft_row['source']!r}"
        )

    def test_description_change_sdft_target_is_new_description(
        self, backend: SQLiteBackend, corpus: dict, server: Any
    ) -> None:
        """The SDFT row's target must be the new description text."""
        new_desc = "Feynman diagrams encode QED perturbation theory as Wick contractions."
        _call_raw(
            server,
            "commit_curation",
            {
                "chunk_id": corpus["chunk_a_id"],
                "set_description": new_desc,
            },
        )

        rows = _get_sdft_rows(backend)
        assert rows, "Expected SDFT row"
        assert rows[-1]["target"] == new_desc, (
            f"Expected target={new_desc!r}; got {rows[-1]['target']!r}"
        )

    def test_description_change_sdft_student_messages_has_prior_description(
        self, backend: SQLiteBackend, corpus: dict, server: Any
    ) -> None:
        """student_messages must contain the prior chunk description as assistant content."""
        _call_raw(
            server,
            "commit_curation",
            {
                "chunk_id": corpus["chunk_a_id"],
                "set_description": "New and improved Feynman diagram description.",
            },
        )

        rows = _get_sdft_rows(backend)
        assert rows, "Expected SDFT row"
        student_msgs = json.loads(rows[-1]["student_messages"])
        assert isinstance(student_msgs, list) and len(student_msgs) >= 1, (
            "student_messages must be a non-empty list"
        )
        roles = [m.get("role") for m in student_msgs]
        assert "assistant" in roles, (
            f"Expected at least one 'assistant' message in student_messages; got roles={roles}"
        )
        # The prior description ("A diagram type used in physics.") must appear
        # somewhere in the assistant message content.
        contents = " ".join(
            m.get("content", "") for m in student_msgs if m.get("role") == "assistant"
        )
        assert "diagram" in contents.lower(), (
            f"Expected prior description text in student_messages assistant content; "
            f"got: {contents!r}"
        )

    def test_description_change_sdft_query_derived_from_chunk_text(
        self, backend: SQLiteBackend, corpus: dict, server: Any
    ) -> None:
        """SDFT row query must be derived from the chunk text (first 200 chars)."""
        _call_raw(
            server,
            "commit_curation",
            {
                "chunk_id": corpus["chunk_a_id"],
                "set_description": "Detailed QED Feynman diagram description.",
            },
        )

        rows = _get_sdft_rows(backend)
        assert rows, "Expected SDFT row"
        query = rows[-1]["query"]
        chunk_text = (
            "Feynman diagrams are pictorial representations of mathematical expressions "
            "describing the behavior and interaction of subatomic particles."
        )
        # Query should be derived from chunk text (first 200 chars).
        assert query and len(query) > 0, "SDFT query must be non-empty"
        assert query in chunk_text or chunk_text[:200].startswith(query[:50]), (
            f"SDFT query does not appear to be derived from chunk text.\n"
            f"  query    : {query!r}\n"
            f"  chunk[:200]: {chunk_text[:200]!r}"
        )

    def test_label_only_commit_does_not_write_sdft_row(
        self, backend: SQLiteBackend, corpus: dict, server: Any
    ) -> None:
        """Pure label-only commit (no description change) must NOT write SDFT row."""
        before = _count_sdft_rows(backend)

        _call_raw(
            server,
            "commit_curation",
            {
                "chunk_id": corpus["chunk_a_id"],
                "add_labels": [{"namespace": "topic", "value": "quantum-physics"}],
                # No set_description — this is a metadata-only commit.
            },
        )

        after = _count_sdft_rows(backend)
        assert after == before, (
            f"Expected NO new SDFT row for label-only commit_curation; "
            f"got {after - before} new rows"
        )

    def test_metadata_only_commit_does_not_write_sdft_row(
        self, backend: SQLiteBackend, corpus: dict, server: Any
    ) -> None:
        """Pure metadata-only commit (no description change) must NOT write SDFT row."""
        before = _count_sdft_rows(backend)

        _call_raw(
            server,
            "commit_curation",
            {
                "chunk_id": corpus["chunk_a_id"],
                "set_metadata": {"difficulty": "advanced", "domain": "physics"},
                # No set_description.
            },
        )

        after = _count_sdft_rows(backend)
        assert after == before, (
            f"Expected NO new SDFT row for metadata-only commit_curation; "
            f"got {after - before} new rows"
        )


# ===========================================================================
# 2. rate_search_result SDFT hook
# ===========================================================================


class TestRateSearchResultSdftHook:
    """SDFT row is written when rate_search_result records thumbs_down + replacement."""

    def test_thumbs_down_with_replacement_writes_sdft_row(
        self, backend: SQLiteBackend, corpus: dict, server: Any
    ) -> None:
        """thumbs_down + replacement_chunk_id writes one SDFT row with source='rate_search_result'."""  # noqa: E501
        before = _count_sdft_rows(backend)
        _seed_search_session(backend, "q-sdft-replace", corpus["dataset_id"])

        root = _call_raw(
            server,
            "rate_search_result",
            {
                "query_id": "q-sdft-replace",
                "chunk_id": corpus["chunk_a_id"],
                "signal": "thumbs_down",
                "value": None,
                "source": "human",
                "replacement_chunk_id": corpus["chunk_b_id"],
            },
        )

        assert not _is_error(root), f"rate_search_result failed: {_error_text(root)}"
        after = _count_sdft_rows(backend)
        assert after == before + 1, (
            f"Expected 1 new SDFT row for thumbs_down + replacement; got {after - before} new rows"
        )

    def test_thumbs_down_with_replacement_sdft_source(
        self, backend: SQLiteBackend, corpus: dict, server: Any
    ) -> None:
        """SDFT row source must be 'rate_search_result'."""
        _seed_search_session(backend, "q-sdft-src", corpus["dataset_id"])
        _call_raw(
            server,
            "rate_search_result",
            {
                "query_id": "q-sdft-src",
                "chunk_id": corpus["chunk_a_id"],
                "signal": "thumbs_down",
                "value": None,
                "source": "human",
                "replacement_chunk_id": corpus["chunk_b_id"],
            },
        )
        rows = _get_sdft_rows(backend)
        assert rows, "Expected SDFT row"
        assert rows[-1]["source"] == "rate_search_result", (
            f"Expected source='rate_search_result'; got {rows[-1]['source']!r}"
        )

    def test_thumbs_down_with_replacement_sdft_target_is_replacement_text(
        self, backend: SQLiteBackend, corpus: dict, server: Any
    ) -> None:
        """SDFT row target must be the text of the replacement chunk."""
        _seed_search_session(backend, "q-sdft-target", corpus["dataset_id"])
        _call_raw(
            server,
            "rate_search_result",
            {
                "query_id": "q-sdft-target",
                "chunk_id": corpus["chunk_a_id"],
                "signal": "thumbs_down",
                "value": None,
                "source": "human",
                "replacement_chunk_id": corpus["chunk_b_id"],
            },
        )
        rows = _get_sdft_rows(backend)
        assert rows, "Expected SDFT row"
        expected_target = (
            "The fine structure constant alpha ≈ 1/137 characterises the strength of the "
            "electromagnetic interaction between elementary charged particles."
        )
        assert rows[-1]["target"] == expected_target, (
            f"Expected target to be replacement chunk text.\n"
            f"  expected: {expected_target!r}\n"
            f"  actual  : {rows[-1]['target']!r}"
        )

    def test_thumbs_down_without_replacement_does_not_write_sdft_row(
        self, backend: SQLiteBackend, corpus: dict, server: Any
    ) -> None:
        """thumbs_down without replacement_chunk_id must NOT write SDFT row."""
        before = _count_sdft_rows(backend)
        _seed_search_session(backend, "q-sdft-no-replace", corpus["dataset_id"])

        _call_raw(
            server,
            "rate_search_result",
            {
                "query_id": "q-sdft-no-replace",
                "chunk_id": corpus["chunk_a_id"],
                "signal": "thumbs_down",
                "value": None,
                "source": "human",
                # No replacement_chunk_id.
            },
        )

        after = _count_sdft_rows(backend)
        assert after == before, (
            f"Expected NO SDFT row for thumbs_down without replacement; "
            f"got {after - before} new rows"
        )

    def test_thumbs_up_with_replacement_does_not_write_sdft_row(
        self, backend: SQLiteBackend, corpus: dict, server: Any
    ) -> None:
        """thumbs_up signal (even with replacement_chunk_id) must NOT write SDFT row.

        Only thumbs_down triggers the SDFT capture hook.
        """
        before = _count_sdft_rows(backend)
        _seed_search_session(backend, "q-sdft-thumbs-up", corpus["dataset_id"])

        _call_raw(
            server,
            "rate_search_result",
            {
                "query_id": "q-sdft-thumbs-up",
                "chunk_id": corpus["chunk_a_id"],
                "signal": "thumbs_up",
                "value": 1.0,
                "source": "human",
                "replacement_chunk_id": corpus["chunk_b_id"],
            },
        )

        after = _count_sdft_rows(backend)
        assert after == before, (
            f"Expected NO SDFT row for thumbs_up signal; got {after - before} new rows"
        )
