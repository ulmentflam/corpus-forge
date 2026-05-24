"""Phase J / J4 — Integration test for the curation chat loop.

Drives the full MCP curation surface against an in-memory SQLite
backend:

1. Seed a tiny corpus (1 dataset, 1 document, 3 chunks of varying
   metadata completeness + classifier confidence).
2. Build the MCP server with ``writes_enabled=True``.
3. Call ``next_curation_target`` (no seed_query) — the worst-scored
   chunk wins.
4. Call ``commit_curation`` with a multi-write payload (labels +
   metadata + description + feedback).
5. Re-query ``next_curation_target`` — the fortified chunk no longer
   wins; a different chunk surfaces.
6. Call ``next_curation_batch(limit=2)`` — the response carries a
   ``cohesion_score`` in ``[0, 1]`` and ≤ 2 targets.

No Docker required. The test stands up an in-process MCP server +
SQLiteBackend(":memory:") and drives the registered request handlers.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

mcp = pytest.importorskip("mcp")
from mcp import types as mcp_types  # noqa: E402
from mcp.server import Server  # noqa: E402

from corpus_forge.backends.sqlite import SQLiteBackend  # noqa: E402
from corpus_forge.chunkers.base import TextChunk  # noqa: E402
from corpus_forge.identity import chunk_content_hash  # noqa: E402
from corpus_forge.mcp.server import build_server  # noqa: E402
from corpus_forge.retrieval.types import SearchOptions, SearchResponse  # noqa: E402
from corpus_forge.sources.base import RawDocument  # noqa: E402

if TYPE_CHECKING:
    from corpus_forge.retrieval.types import Hit

pytestmark = pytest.mark.integration


# ─────────────────────────────────────────────────────────────────────────
# Helpers — drive the MCP server in-process
# ─────────────────────────────────────────────────────────────────────────


def _call(server: Server[object], name: str, arguments: dict) -> dict | None:
    async def _run() -> dict | None:
        handler = server.request_handlers.get(mcp_types.CallToolRequest)
        assert handler is not None
        request = mcp_types.CallToolRequest(
            method="tools/call",
            params=mcp_types.CallToolRequestParams(name=name, arguments=arguments),
        )
        wrapper = await handler(request)
        root = wrapper.root if hasattr(wrapper, "root") else wrapper
        if getattr(root, "isError", False):
            text = "".join(getattr(b, "text", "") for b in getattr(root, "content", []))
            raise AssertionError(f"MCP tool {name!r} returned isError=True: {text}")
        structured = getattr(root, "structuredContent", None)
        if structured is not None:
            return dict(structured)
        text_blocks = [getattr(b, "text", "") for b in getattr(root, "content", [])]
        return json.loads("".join(text_blocks)) if text_blocks else None

    return asyncio.run(_run())


def _seed_corpus(backend: SQLiteBackend) -> dict[str, int]:
    """Insert a tiny corpus: 1 dataset, 1 doc, 3 chunks with varying
    metadata completeness and classifier-label confidence."""
    dataset_id = backend.get_or_create_dataset(
        name="curation_demo",
        kind="text",
        description="J4 e2e fixture",
    )

    chunks = [
        TextChunk(
            text="alpha — well-tagged chunk",
            heading="Alpha",
            metadata={"language": "en"},
        ),
        TextChunk(
            text="beta — partially tagged chunk",
            heading=None,
            metadata={},
        ),
        TextChunk(
            text="gamma — barely tagged chunk needing curation",
            heading=None,
            metadata={},
        ),
    ]
    doc = RawDocument(
        source_uri="vault://fixtures/curation_e2e.md",
        content_hash=chunk_content_hash("curation_e2e_doc"),
        text="alpha\n\nbeta\n\ngamma",
        title="Curation Fixture",
        modified_at=0.0,
        metadata={},
        labels=[],
    )
    document_id = backend.upsert_document(dataset_id, doc, chunks)

    # Find chunk_ids in deterministic order.
    rows = backend._execute(
        "SELECT id FROM chunks WHERE document_id = ? ORDER BY chunk_index",
        (document_id,),
    )
    chunk_ids = [int(r["id"]) for r in rows]
    assert len(chunk_ids) == 3, f"expected 3 chunks, got {len(chunk_ids)}"

    # Classifier labels — one strong, one weak, one missing.
    # apply_label expects (entity_type, entity_id, namespace, value, *, confidence, source).
    backend.apply_label(
        "chunk",
        chunk_ids[0],
        "class",
        "topic_a",
        confidence=0.95,
        source="classifier:fake",
    )
    backend.apply_label(
        "chunk",
        chunk_ids[1],
        "class",
        "topic_b",
        confidence=0.55,
        source="classifier:fake",
    )
    # chunk_ids[2] has NO classifier label → confidence_deficit = 1.0.

    return {
        "dataset_id": dataset_id,
        "document_id": document_id,
        "chunk_0": chunk_ids[0],
        "chunk_1": chunk_ids[1],
        "chunk_2": chunk_ids[2],
    }


class _Retriever:
    def __init__(self, backend: SQLiteBackend) -> None:
        self.backend = backend

    def search(self, query: str, options: SearchOptions) -> SearchResponse:
        results: list[Hit] = []
        return SearchResponse(
            query_id="",
            results=results,
            query=query,
            dataset_id=None,
            started_at=datetime.now(UTC),
        )


# ─────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────


def test_curation_e2e_picks_worst_chunk() -> None:
    backend = SQLiteBackend(path=":memory:")
    backend.migrate()
    seeded = _seed_corpus(backend)
    server = build_server(retriever_builder=lambda: _Retriever(backend), writes_enabled=True)

    payload = _call(server, "next_curation_target", {"dataset": "curation_demo"})
    assert payload is not None
    target = payload["target"]
    assert target is not None, "selector must return a target for the seeded corpus"
    # The unlabelled chunk has classifier_confidence=None →
    # confidence_deficit=1.0 (highest weight). It should win.
    assert target["chunk_id"] == seeded["chunk_2"], (
        f"expected chunk_2 to score highest; got chunk_id={target['chunk_id']}"
    )
    assert target["score_breakdown"]["confidence_deficit"] == 1.0


def test_curation_e2e_commit_changes_next_pick() -> None:
    backend = SQLiteBackend(path=":memory:")
    backend.migrate()
    seeded = _seed_corpus(backend)
    server = build_server(retriever_builder=lambda: _Retriever(backend), writes_enabled=True)

    # First call surfaces chunk_2.
    first = _call(server, "next_curation_target", {"dataset": "curation_demo"})
    assert first is not None and first["target"]["chunk_id"] == seeded["chunk_2"]

    # Fortify chunk_2 with labels + metadata + description + feedback.
    commit = _call(
        server,
        "commit_curation",
        {
            "chunk_id": seeded["chunk_2"],
            "add_labels": [
                {"namespace": "class", "value": "topic_c", "confidence": 0.95},
                {"namespace": "topic", "value": "ml"},
            ],
            "set_metadata": {"language": "en"},
            "set_description": "gamma chunk, manually fortified",
            "feedback": {"kind": "rating", "rating": 5},
        },
    )
    assert commit is not None
    assert commit["writes"]["add_label"] == 2
    assert commit["writes"]["set_metadata"] == 1
    assert commit["writes"]["set_description"] == 1
    assert commit["writes"]["add_feedback"] == 1

    # Re-query: chunk_2 has classifier_confidence=0.95 now → its
    # confidence_deficit drops dramatically. The next worst chunk
    # (chunk_1, confidence 0.55) should surface instead.
    second = _call(server, "next_curation_target", {"dataset": "curation_demo"})
    assert second is not None
    assert second["target"]["chunk_id"] != seeded["chunk_2"], (
        "chunk_2 was fortified; it should no longer be the worst-scoring chunk"
    )


def test_curation_e2e_batch_shape() -> None:
    backend = SQLiteBackend(path=":memory:")
    backend.migrate()
    _seed_corpus(backend)
    server = build_server(retriever_builder=lambda: _Retriever(backend), writes_enabled=True)

    payload = _call(server, "next_curation_batch", {"dataset": "curation_demo", "limit": 2})
    assert payload is not None
    batch = payload["batch"]
    assert batch is not None
    assert 0.0 <= batch["cohesion_score"] <= 1.0
    assert len(batch["targets"]) <= 2
    # grouping_key is a [stem, label] pair.
    assert isinstance(batch["grouping_key"], (list, tuple))
    assert len(batch["grouping_key"]) == 2
