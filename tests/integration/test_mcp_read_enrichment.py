"""F-04 RED — MCP read-side enrichment integration tests.

End-to-end via build_server() using a SQLite-backed LexicalRetriever
(no ML embedder, no Docker).  Every test asserts on search/get_chunk
response fields that F-04 has not yet wired in — all should fail RED.

Pinned parent-rollup shape (what the F-04 Coder must produce for chunk hits):
    {
        "chunk_id": <int>,
        "score": <float>,
        "text": <str>,
        ...existing fields...,
        "labels": [{"namespace": str, "value": str, "source": str,
                    "confidence": float | None}, ...],
        "description": <str | None>,
        "recent_feedback": [{"kind": str, "rating": int | None,
                             "text": str | None, "ts": str | None}, ...],
        "parent": {
            "labels": [...],
            "description": <str | None>,
            "recent_feedback": [...],
        } | None,
    }

include_labels / include_description / include_feedback toggles:
    False → the corresponding key is ABSENT from each hit dict (KeyError).
    True  → key is present (default).

recent_feedback is sorted ts DESC, bounded to 5 entries.

pytestmark: pytest.mark.integration
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
from typing import Any
from unittest.mock import patch

import pytest

from corpus_forge.backends.sqlite import SQLiteBackend
from corpus_forge.mcp.server import build_server

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CHUNK_TEXT = "The eigenvalue of the Hamiltonian operator is quantized."
_CHUNK_TEXT_2 = "Reciprocal lattice vectors span the Brillouin zone boundary."
_DOC_TEXT = "Quantum mechanics foundational text body with band structure theory."


# ---------------------------------------------------------------------------
# Helpers — SQLite in-process backend
# ---------------------------------------------------------------------------


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _make_backend() -> SQLiteBackend:
    """Fresh migrated in-memory SQLiteBackend."""
    b = SQLiteBackend(path=":memory:")
    b.migrate()
    return b


def _seed_db(backend: SQLiteBackend) -> dict[str, int]:
    """Insert one dataset, document, and chunk.  Returns ids."""
    with backend._get_connection() as conn:
        dataset_id = conn.execute(
            "INSERT INTO datasets (name, kind, description) VALUES (?, ?, ?) RETURNING id",
            ("enrichment-test", "text", "F-04 test dataset"),
        ).fetchone()[0]

        document_id = conn.execute(
            "INSERT INTO documents (dataset_id, source_uri, content_hash, title, text, metadata)"
            " VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
            (
                dataset_id,
                "vault://test/doc.md",
                _content_hash(_DOC_TEXT),
                "Quantum Mechanics",
                _DOC_TEXT,
                "{}",
            ),
        ).fetchone()[0]

        chunk_id = conn.execute(
            "INSERT INTO chunks (document_id, chunk_index, text, content_hash, metadata)"
            " VALUES (?, ?, ?, ?, ?) RETURNING id",
            (document_id, 0, _CHUNK_TEXT, _content_hash(_CHUNK_TEXT), "{}"),
        ).fetchone()[0]

        # FTS5 virtual table is kept in sync via trigger, but insert directly as safety:
        with contextlib.suppress(Exception):
            conn.execute(
                "INSERT INTO chunks_fts (rowid, text) VALUES (?, ?)",
                (chunk_id, _CHUNK_TEXT),
            )

        conn.commit()

    return {
        "dataset_id": dataset_id,
        "document_id": document_id,
        "chunk_id": chunk_id,
    }


# ---------------------------------------------------------------------------
# Lexical-only retriever stub (no ML)
# ---------------------------------------------------------------------------


class _LexicalRetriever:
    """Minimal retriever backed by backend.search_lexical — no embedder."""

    def __init__(self, backend: SQLiteBackend) -> None:
        self.backend = backend

    def search(self, query: str, options: Any) -> list[Any]:
        k = getattr(options, "k", 10)
        return self.backend.search_lexical(query, k=k)


# ---------------------------------------------------------------------------
# MCP call helper (same pattern as test_two_ingester_one_mcp.py)
# ---------------------------------------------------------------------------


def _call_tool(server: Any, name: str, arguments: dict) -> dict:
    """Drive an MCP tool call synchronously and return its structured payload."""

    async def _run() -> dict:
        from mcp.types import CallToolRequest, CallToolRequestParams

        handler = server.request_handlers.get(CallToolRequest)
        assert handler is not None, "No CallToolRequest handler on server"

        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name=name, arguments=arguments),
        )
        wrapper = await handler(request)
        root = wrapper.root
        if getattr(root, "isError", False):
            text = "".join(getattr(b, "text", "") for b in getattr(root, "content", []))
            raise AssertionError(f"MCP tool {name!r} isError=True: {text}")
        if root.structuredContent is not None:
            return dict(root.structuredContent)
        text_blocks = [getattr(b, "text", "") for b in getattr(root, "content", [])]
        return json.loads("".join(text_blocks))

    return asyncio.run(_run())


def _build_server(backend: SQLiteBackend, writes_enabled: bool = True) -> Any:
    retriever = _LexicalRetriever(backend)
    return build_server(
        retriever_builder=lambda: retriever,
        writes_enabled=writes_enabled,
    )


def _call_write(server: Any, tool: str, **kwargs: Any) -> dict:
    return _call_tool(server, tool, kwargs)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def backend() -> SQLiteBackend:
    return _make_backend()


@pytest.fixture
def seeded(backend: SQLiteBackend) -> dict[str, int]:
    return _seed_db(backend)


@pytest.fixture
def server(backend: SQLiteBackend) -> Any:
    return _build_server(backend)


# ---------------------------------------------------------------------------
# Helper: search and return first hit (asserts at least one hit exists)
# ---------------------------------------------------------------------------


def _search_first_hit(server: Any, query: str, **extra: Any) -> dict:
    args: dict = {"query": query, "k": 5}
    args.update(extra)
    result = _call_tool(server, "search", args)
    hits = result.get("hits", [])
    assert hits, f"Expected at least one search hit for {query!r}; got 0"
    return hits[0]


# ---------------------------------------------------------------------------
# 1. test_search_hit_includes_labels
# ---------------------------------------------------------------------------


def test_search_hit_includes_labels(backend: SQLiteBackend, seeded: dict, server: Any) -> None:
    """search() hit must have a `labels` list with the seeded label dict."""
    # Seed: apply a label to the chunk via the write tool.
    _call_write(
        server,
        "add_label",
        entity_type="chunk",
        entity_id=seeded["chunk_id"],
        namespace="topic",
        value="quantum",
    )

    hit = _search_first_hit(server, "eigenvalue Hamiltonian")

    # F-04 not yet wired: `labels` key missing or empty → RED
    assert "labels" in hit, f"Expected 'labels' key in search hit; got keys: {list(hit.keys())}"
    labels = hit["labels"]
    assert len(labels) > 0, f"Expected non-empty labels on hit; got: {labels}"

    label = labels[0]
    assert isinstance(label, dict), f"Expected label to be a dict; got: {type(label)}"
    assert label.get("namespace") == "topic", f"Wrong namespace: {label}"
    assert label.get("value") == "quantum", f"Wrong value: {label}"
    # source and confidence are part of the pinned shape
    assert "source" in label, f"Expected 'source' key in label dict; got: {label}"
    assert "confidence" in label, f"Expected 'confidence' key in label dict; got: {label}"


# ---------------------------------------------------------------------------
# 2. test_search_hit_includes_description
# ---------------------------------------------------------------------------


def test_search_hit_includes_description(backend: SQLiteBackend, seeded: dict, server: Any) -> None:
    """search() hit must have a `description` field matching what was set."""
    _call_write(
        server,
        "set_description",
        entity_type="chunk",
        entity_id=seeded["chunk_id"],
        text="Key chunk about quantization of energy levels.",
    )

    hit = _search_first_hit(server, "eigenvalue Hamiltonian")

    # F-04 not yet wired: `description` key missing → RED
    assert "description" in hit, (
        f"Expected 'description' key in search hit; got keys: {list(hit.keys())}"
    )
    assert hit["description"] == "Key chunk about quantization of energy levels.", (
        f"Wrong description: {hit['description']!r}"
    )


# ---------------------------------------------------------------------------
# 3. test_search_hit_includes_recent_feedback
# ---------------------------------------------------------------------------


def test_search_hit_includes_recent_feedback(
    backend: SQLiteBackend, seeded: dict, server: Any
) -> None:
    """search() hit must have `recent_feedback` with 2 entries (kind-sorted ts DESC)."""
    _call_write(
        server,
        "add_feedback",
        entity_type="chunk",
        entity_id=seeded["chunk_id"],
        kind="thumbs",
        rating=1,
        text="Very helpful chunk.",
    )
    _call_write(
        server,
        "add_feedback",
        entity_type="chunk",
        entity_id=seeded["chunk_id"],
        kind="comment",
        rating=None,
        text="Could be clearer.",
    )

    hit = _search_first_hit(server, "eigenvalue Hamiltonian")

    # F-04 not yet wired → RED
    assert "recent_feedback" in hit, (
        f"Expected 'recent_feedback' key in search hit; got keys: {list(hit.keys())}"
    )
    fb = hit["recent_feedback"]
    assert len(fb) == 2, f"Expected 2 feedback entries; got {len(fb)}: {fb}"

    kinds = {entry["kind"] for entry in fb}
    assert kinds == {"thumbs", "comment"}, f"Wrong feedback kinds: {kinds}"


# ---------------------------------------------------------------------------
# 4. test_search_hit_recent_feedback_bounded_to_5
# ---------------------------------------------------------------------------


def test_search_hit_recent_feedback_bounded_to_5(
    backend: SQLiteBackend, seeded: dict, server: Any
) -> None:
    """recent_feedback length must be <= 5 even when 7 feedback rows exist."""
    for i in range(7):
        _call_write(
            server,
            "add_feedback",
            entity_type="chunk",
            entity_id=seeded["chunk_id"],
            kind="thumbs",
            rating=i % 2,
            text=f"Feedback entry {i}",
        )

    hit = _search_first_hit(server, "eigenvalue Hamiltonian")

    # F-04 not yet wired → RED
    assert "recent_feedback" in hit, (
        f"Expected 'recent_feedback' key in search hit; got keys: {list(hit.keys())}"
    )
    fb = hit["recent_feedback"]
    assert len(fb) <= 5, f"recent_feedback must be bounded to 5; got {len(fb)}"
    assert len(fb) > 0, "recent_feedback must not be empty when feedback rows exist"


# ---------------------------------------------------------------------------
# 5. test_get_chunk_includes_enrichment
# ---------------------------------------------------------------------------


def test_get_chunk_includes_enrichment(backend: SQLiteBackend, seeded: dict, server: Any) -> None:
    """get_chunk(chunk_id) response must include labels, description, recent_feedback."""
    _call_write(
        server,
        "add_label",
        entity_type="chunk",
        entity_id=seeded["chunk_id"],
        namespace="quality",
        value="high",
    )
    _call_write(
        server,
        "set_description",
        entity_type="chunk",
        entity_id=seeded["chunk_id"],
        text="This chunk discusses quantization.",
    )
    _call_write(
        server,
        "add_feedback",
        entity_type="chunk",
        entity_id=seeded["chunk_id"],
        kind="thumbs",
        rating=1,
    )

    result = _call_tool(server, "get_chunk", {"chunk_id": seeded["chunk_id"]})

    # F-04 not yet wired → RED (KeyError or empty fields)
    assert "labels" in result, (
        f"Expected 'labels' in get_chunk result; got keys: {list(result.keys())}"
    )
    labels = result["labels"]
    assert len(labels) > 0, f"Expected non-empty labels in get_chunk; got: {labels}"
    assert labels[0].get("namespace") == "quality", f"Wrong label namespace: {labels[0]}"

    assert "description" in result, (
        f"Expected 'description' in get_chunk result; got keys: {list(result.keys())}"
    )
    assert result["description"] == "This chunk discusses quantization.", (
        f"Wrong description in get_chunk: {result['description']!r}"
    )

    assert "recent_feedback" in result, (
        f"Expected 'recent_feedback' in get_chunk result; got keys: {list(result.keys())}"
    )
    assert len(result["recent_feedback"]) > 0, "Expected at least 1 feedback in get_chunk"


# ---------------------------------------------------------------------------
# 6. test_search_include_labels_false_omits_labels
# ---------------------------------------------------------------------------


def test_search_include_labels_false_omits_labels(
    backend: SQLiteBackend, seeded: dict, server: Any
) -> None:
    """search(include_labels=False) → `labels` key must be ABSENT from each hit."""
    _call_write(
        server,
        "add_label",
        entity_type="chunk",
        entity_id=seeded["chunk_id"],
        namespace="topic",
        value="quantum",
    )

    hit = _search_first_hit(server, "eigenvalue Hamiltonian", include_labels=False)

    # F-04 not yet wired (toggle doesn't exist in schema) → RED
    # Either KeyError because the key was never present, or we assert it's absent.
    assert "labels" not in hit, (
        f"Expected 'labels' to be absent when include_labels=False; "
        f"hit has keys: {list(hit.keys())}"
    )


# ---------------------------------------------------------------------------
# 7. test_search_include_description_false_omits_description
# ---------------------------------------------------------------------------


def test_search_include_description_false_omits_description(
    backend: SQLiteBackend, seeded: dict, server: Any
) -> None:
    """search(include_description=False) → `description` key must be ABSENT."""
    _call_write(
        server,
        "set_description",
        entity_type="chunk",
        entity_id=seeded["chunk_id"],
        text="Some description text.",
    )

    hit = _search_first_hit(server, "eigenvalue Hamiltonian", include_description=False)

    # F-04 not yet wired → RED
    assert "description" not in hit, (
        f"Expected 'description' absent when include_description=False; "
        f"hit has keys: {list(hit.keys())}"
    )


# ---------------------------------------------------------------------------
# 8. test_search_include_feedback_false_omits_recent_feedback
# ---------------------------------------------------------------------------


def test_search_include_feedback_false_omits_recent_feedback(
    backend: SQLiteBackend, seeded: dict, server: Any
) -> None:
    """search(include_feedback=False) → `recent_feedback` key must be ABSENT."""
    _call_write(
        server,
        "add_feedback",
        entity_type="chunk",
        entity_id=seeded["chunk_id"],
        kind="thumbs",
        rating=1,
    )

    hit = _search_first_hit(server, "eigenvalue Hamiltonian", include_feedback=False)

    # F-04 not yet wired → RED
    assert "recent_feedback" not in hit, (
        f"Expected 'recent_feedback' absent when include_feedback=False; "
        f"hit has keys: {list(hit.keys())}"
    )


# ---------------------------------------------------------------------------
# 9. test_search_chunk_hit_includes_parent_rollup
# ---------------------------------------------------------------------------


def test_search_chunk_hit_includes_parent_rollup(
    backend: SQLiteBackend, seeded: dict, server: Any
) -> None:
    """Chunk hit must include a `parent` key with the document's enrichment.

    Pinned parent rollup shape:
        hit["parent"] == {
            "labels": [...],
            "description": <str | None>,
            "recent_feedback": [...],
        }

    Seed: document has a description; chunk has no description of its own.
    Assert: hit["parent"]["description"] == <doc description>.
    """
    # Set description on the DOCUMENT (not the chunk)
    _call_write(
        server,
        "set_description",
        entity_type="document",
        entity_id=seeded["document_id"],
        text="Parent document describing quantum band structure.",
    )
    # Apply a label to the document
    _call_write(
        server,
        "add_label",
        entity_type="document",
        entity_id=seeded["document_id"],
        namespace="domain",
        value="physics",
    )

    hit = _search_first_hit(server, "eigenvalue Hamiltonian")

    # F-04 not yet wired → RED (`parent` key missing)
    assert "parent" in hit, (
        f"Expected 'parent' rollup key in chunk search hit; got keys: {list(hit.keys())}"
    )
    parent = hit["parent"]
    assert isinstance(parent, dict), f"Expected 'parent' to be a dict; got: {type(parent)}"

    # parent.description must reflect the document's description
    assert "description" in parent, (
        f"Expected 'description' in parent rollup; got parent keys: {list(parent.keys())}"
    )
    assert parent["description"] == "Parent document describing quantum band structure.", (
        f"Wrong parent description: {parent['description']!r}"
    )

    # parent.labels must include the document label
    assert "labels" in parent, (
        f"Expected 'labels' in parent rollup; got parent keys: {list(parent.keys())}"
    )
    parent_labels = parent["labels"]
    assert len(parent_labels) > 0, f"Expected non-empty parent labels; got: {parent_labels}"
    assert any(
        lbl.get("namespace") == "domain" and lbl.get("value") == "physics" for lbl in parent_labels
    ), f"Expected domain/physics label in parent; got: {parent_labels}"

    # parent.recent_feedback must be present (even if empty list)
    assert "recent_feedback" in parent, (
        f"Expected 'recent_feedback' in parent rollup; got parent keys: {list(parent.keys())}"
    )


# ---------------------------------------------------------------------------
# 10. test_search_no_n_plus_one
# ---------------------------------------------------------------------------


def test_search_no_n_plus_one(backend: SQLiteBackend) -> None:
    """hydrate_hit_metadata called at most 2x per search (chunks + parents). Not per-hit.

    Seed 20 chunks across 5 documents.  Patch backend.hydrate_hit_metadata to
    count invocations.  Assert call_count <= 2 after one search.

    F-04 currently never calls hydrate_hit_metadata → call_count == 0 → RED.
    """
    # Seed 5 documents, 4 chunks each = 20 chunks total
    with backend._get_connection() as conn:
        dataset_id = conn.execute(
            "INSERT INTO datasets (name, kind, description) VALUES (?, ?, ?) RETURNING id",
            ("n+1-test", "text", "no N+1 test"),
        ).fetchone()[0]

        for doc_idx in range(5):
            doc_text = f"Document {doc_idx} about neutron scattering diffraction patterns"
            document_id = conn.execute(
                "INSERT INTO documents"
                " (dataset_id, source_uri, content_hash, title, text, metadata)"
                " VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
                (
                    dataset_id,
                    f"vault://doc{doc_idx}.md",
                    _content_hash(doc_text + str(doc_idx)),
                    f"Doc {doc_idx}",
                    doc_text,
                    "{}",
                ),
            ).fetchone()[0]

            for chunk_idx in range(4):
                chunk_text = f"neutron scattering diffraction pattern doc{doc_idx} chunk{chunk_idx}"
                chunk_id = conn.execute(
                    "INSERT INTO chunks (document_id, chunk_index, text, content_hash, metadata)"
                    " VALUES (?, ?, ?, ?, ?) RETURNING id",
                    (
                        document_id,
                        chunk_idx,
                        chunk_text,
                        _content_hash(chunk_text),
                        "{}",
                    ),
                ).fetchone()[0]

                with contextlib.suppress(Exception):
                    conn.execute(
                        "INSERT INTO chunks_fts (rowid, text) VALUES (?, ?)",
                        (chunk_id, chunk_text),
                    )

        conn.commit()

    retriever = _LexicalRetriever(backend)
    server = build_server(
        retriever_builder=lambda: retriever,
        writes_enabled=False,
    )

    call_count = 0
    real_hydrate = backend.hydrate_hit_metadata

    def _counting_hydrate(hits: list) -> list:
        nonlocal call_count
        call_count += 1
        return real_hydrate(hits)

    with patch.object(backend, "hydrate_hit_metadata", side_effect=_counting_hydrate):
        _call_tool(server, "search", {"query": "neutron scattering diffraction", "k": 20})

    # F-04 not wired → hydrate never called → call_count == 0 → RED
    assert call_count >= 1, (
        f"Expected hydrate_hit_metadata to be called at least once during search; "
        f"was called {call_count} times. F-04 has not yet wired hydrate_hit_metadata into search."
    )
    assert call_count <= 2, (
        f"Expected hydrate_hit_metadata called at most 2 times (chunk batch + parent batch); "
        f"was called {call_count} times — N+1 violation."
    )
