"""O4-T2 RED — MCP analyze tools integration tests.

Tests the four new Phase O read-only MCP tools:
  - ``analyze_corpus``  — token / length stats over a dataset.
  - ``find_duplicates`` — exact + near-duplicate clusters.
  - ``cluster_topics``  — topic discovery via BERTopic / HDBSCAN.
  - ``score_quality``   — per-chunk quality scoring (optional persist).

All four tools are read-only — they must be callable with
``writes_enabled=False``.

Pattern mirrors ``tests/unit/test_mcp_curation_tools.py`` and
``tests/integration/test_mcp_read_enrichment.py``: in-process server built
with ``build_server()``, request handlers driven directly (no subprocess).

RED state: the dispatch module ``corpus_forge/mcp/_dispatch_analyze.py``
and the tool registrations in ``corpus_forge/mcp/server.py`` do not yet
exist — every test fails with a KeyError or ``unknown tool`` error payload.

pytestmark: pytest.mark.integration
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import MagicMock

import pytest

# Guard: skip the whole file if the mcp package is not installed.
mcp = pytest.importorskip("mcp")
from mcp import types as mcp_types  # noqa: E402

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Tool names (constants to avoid typo drift)
# ---------------------------------------------------------------------------

_ANALYZE_CORPUS = "analyze_corpus"
_FIND_DUPLICATES = "find_duplicates"
_CLUSTER_TOPICS = "cluster_topics"
_SCORE_QUALITY = "score_quality"

_ALL_ANALYZE_TOOLS = {_ANALYZE_CORPUS, _FIND_DUPLICATES, _CLUSTER_TOPICS, _SCORE_QUALITY}

# ---------------------------------------------------------------------------
# In-process MCP harness (mirrors test_mcp_curation_tools.py)
# ---------------------------------------------------------------------------


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _list_tools(server: Any) -> set[str]:
    handler = server.request_handlers[mcp_types.ListToolsRequest]
    request = mcp_types.ListToolsRequest(method="tools/list")
    result = _run(handler(request))
    root = result.root if hasattr(result, "root") else result
    return {t.name for t in root.tools}


def _get_tool(server: Any, name: str) -> mcp_types.Tool:
    handler = server.request_handlers[mcp_types.ListToolsRequest]
    request = mcp_types.ListToolsRequest(method="tools/list")
    result = _run(handler(request))
    root = result.root if hasattr(result, "root") else result
    for t in root.tools:
        if t.name == name:
            return t
    raise KeyError(f"Tool {name!r} not registered on this server")


def _call(server: Any, name: str, arguments: dict[str, Any]) -> Any:
    handler = server.request_handlers[mcp_types.CallToolRequest]
    request = mcp_types.CallToolRequest(
        method="tools/call",
        params=mcp_types.CallToolRequestParams(name=name, arguments=arguments),
    )
    result = _run(handler(request))
    return result.root if hasattr(result, "root") else result


def _payload(result: Any) -> dict:
    """Extract the structured dict payload from a CallToolResult."""
    sc = getattr(result, "structuredContent", None)
    if sc is not None:
        return dict(sc)
    content = getattr(result, "content", [])
    return json.loads(content[0].text)


# ---------------------------------------------------------------------------
# Fake retriever + backend
# ---------------------------------------------------------------------------


class _FakeRetriever:
    """Minimal retriever with a MagicMock backend that carries demo data."""

    def __init__(self) -> None:
        self.backend = MagicMock()
        self.backend.list_datasets.return_value = [
            {
                "name": "demo",
                "kind": "text",
                "description": "O4 test dataset",
                "document_count": 3,
                "chunk_count": 5,
            }
        ]


def _build_server(*, writes_enabled: bool = False) -> Any:
    from corpus_forge.mcp.server import build_server

    retriever = _FakeRetriever()
    return build_server(
        retriever_builder=lambda: retriever,
        writes_enabled=writes_enabled,
    )


# ---------------------------------------------------------------------------
# Demo corpus fixture — 5 chunks used across multiple tests.
# ---------------------------------------------------------------------------

_DEMO_CHUNKS = [
    {
        "id": 1,
        "text": (
            "The eigenvalue of the Hamiltonian operator is quantized. "
            "This fundamental result underlies quantum mechanics and band structure theory."
        ),
        "token_count": 28,
        "content_hash": "hash_a",
        "dataset_id": 1,
        "classifier_label": "physics",
        "metadata": {"language": "en", "source": "textbook", "year": "2020"},
    },
    {
        "id": 2,
        "text": (
            "Reciprocal lattice vectors span the Brillouin zone boundary. "
            "Understanding this is key to solid-state physics and material science."
        ),
        "token_count": 24,
        "content_hash": "hash_b",
        "dataset_id": 1,
        "classifier_label": "physics",
        "metadata": {"language": "en", "source": "lecture", "year": "2021"},
    },
    {
        "id": 3,
        "text": (
            "The eigenvalue of the Hamiltonian operator is quantized. "
            "This fundamental result underlies quantum mechanics and band structure theory."
        ),
        "token_count": 28,
        "content_hash": "hash_a",  # exact dup of chunk 1
        "dataset_id": 1,
        "classifier_label": "physics",
        "metadata": {"language": "en", "source": "textbook", "year": "2020"},
    },
    {
        "id": 4,
        "text": "Short.",
        "token_count": 1,
        "content_hash": "hash_c",
        "dataset_id": 1,
        "classifier_label": None,
        "metadata": {},
    },
    {
        "id": 5,
        "text": (
            "Machine learning models generalise from training data to unseen examples. "
            "Overfitting occurs when the model memorises noise rather than signal."
        ),
        "token_count": 22,
        "content_hash": "hash_d",
        "dataset_id": 1,
        "classifier_label": "ml",
        "metadata": {"language": "en", "source": "paper"},
    },
]

_DEMO_CHUNK_IDS = [c["id"] for c in _DEMO_CHUNKS]


# ===========================================================================
# 1. Tool registration tests
# ===========================================================================


class TestToolRegistration:
    """All four analyze tools appear in list_tools() regardless of writes_enabled."""

    def test_analyze_corpus_registered_writes_false(self) -> None:
        names = _list_tools(_build_server(writes_enabled=False))
        assert _ANALYZE_CORPUS in names, (
            f"Expected {_ANALYZE_CORPUS!r} in registered tools; got {names}"
        )

    def test_find_duplicates_registered_writes_false(self) -> None:
        names = _list_tools(_build_server(writes_enabled=False))
        assert _FIND_DUPLICATES in names, (
            f"Expected {_FIND_DUPLICATES!r} in registered tools; got {names}"
        )

    def test_cluster_topics_registered_writes_false(self) -> None:
        names = _list_tools(_build_server(writes_enabled=False))
        assert _CLUSTER_TOPICS in names, (
            f"Expected {_CLUSTER_TOPICS!r} in registered tools; got {names}"
        )

    def test_score_quality_registered_writes_false(self) -> None:
        names = _list_tools(_build_server(writes_enabled=False))
        assert _SCORE_QUALITY in names, (
            f"Expected {_SCORE_QUALITY!r} in registered tools; got {names}"
        )

    def test_all_four_analyze_tools_registered_writes_true(self) -> None:
        """All four are also present when writes_enabled=True (they're read-only)."""
        names = _list_tools(_build_server(writes_enabled=True))
        missing = _ALL_ANALYZE_TOOLS - names
        assert not missing, (
            f"Expected all analyze tools registered with writes_enabled=True; missing: {missing}"
        )

    def test_analyze_corpus_input_schema_has_dataset(self) -> None:
        tool = _get_tool(_build_server(), _ANALYZE_CORPUS)
        schema = tool.inputSchema
        assert "dataset" in schema.get("properties", {}), (
            f"Expected 'dataset' property in {_ANALYZE_CORPUS} inputSchema; "
            f"got properties: {list(schema.get('properties', {}).keys())}"
        )

    def test_find_duplicates_input_schema_has_threshold(self) -> None:
        tool = _get_tool(_build_server(), _FIND_DUPLICATES)
        schema = tool.inputSchema
        props = schema.get("properties", {})
        assert "threshold" in props, (
            f"Expected 'threshold' property in {_FIND_DUPLICATES} inputSchema; "
            f"got properties: {list(props.keys())}"
        )

    def test_cluster_topics_input_schema_has_min_cluster_size(self) -> None:
        tool = _get_tool(_build_server(), _CLUSTER_TOPICS)
        schema = tool.inputSchema
        props = schema.get("properties", {})
        assert "min_cluster_size" in props, (
            f"Expected 'min_cluster_size' property in {_CLUSTER_TOPICS} inputSchema; "
            f"got properties: {list(props.keys())}"
        )

    def test_score_quality_input_schema_has_dataset_or_chunk_ids(self) -> None:
        tool = _get_tool(_build_server(), _SCORE_QUALITY)
        schema = tool.inputSchema
        props = schema.get("properties", {})
        has_dataset = "dataset" in props
        has_chunk_ids = "chunk_ids" in props
        assert has_dataset or has_chunk_ids, (
            f"Expected 'dataset' or 'chunk_ids' property in {_SCORE_QUALITY} inputSchema; "
            f"got properties: {list(props.keys())}"
        )

    def test_score_quality_input_schema_has_persist(self) -> None:
        tool = _get_tool(_build_server(), _SCORE_QUALITY)
        schema = tool.inputSchema
        props = schema.get("properties", {})
        assert "persist" in props, (
            f"Expected 'persist' property in {_SCORE_QUALITY} inputSchema; "
            f"got properties: {list(props.keys())}"
        )


# ===========================================================================
# 2. analyze_corpus dispatch
# ===========================================================================


class TestAnalyzeCorpusDispatch:
    """analyze_corpus returns a well-shaped stats summary."""

    def _dispatch(
        self,
        monkeypatch: pytest.MonkeyPatch,
        dataset: str = "demo",
        chunks: list[dict] | None = None,
    ) -> dict:
        if chunks is None:
            chunks = _DEMO_CHUNKS

        import corpus_forge.mcp._dispatch_analyze as _da

        monkeypatch.setattr(
            _da,
            "_fetch_chunks_for_dataset",
            lambda backend, ds: chunks,
        )
        server = _build_server(writes_enabled=False)
        result = _call(server, _ANALYZE_CORPUS, {"dataset": dataset})
        assert not getattr(result, "isError", False), (
            f"{_ANALYZE_CORPUS} returned isError=True: "
            + "".join(getattr(b, "text", "") for b in getattr(result, "content", []))
        )
        return _payload(result)

    def test_returns_n_chunks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = self._dispatch(monkeypatch)
        assert "n_chunks" in payload, f"Expected 'n_chunks' key; got: {list(payload.keys())}"
        assert payload["n_chunks"] == len(_DEMO_CHUNKS)

    def test_returns_token_stats(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = self._dispatch(monkeypatch)
        assert "token_stats" in payload, f"Expected 'token_stats' key; got: {list(payload.keys())}"
        ts = payload["token_stats"]
        for key in ("p50", "p95", "mean", "min", "max", "token_total", "n"):
            assert key in ts, (
                f"Expected 'token_stats.{key}'; got token_stats keys: {list(ts.keys())}"
            )

    def test_returns_n_documents(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = self._dispatch(monkeypatch)
        assert "n_documents" in payload, f"Expected 'n_documents' key; got: {list(payload.keys())}"

    def test_output_is_json_serializable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = self._dispatch(monkeypatch)
        # Raises TypeError if numpy arrays or non-serializable objects are present.
        json.dumps(payload)

    def test_missing_dataset_returns_error_payload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A dataset name that doesn't exist returns a clear error payload, not a crash."""
        import corpus_forge.mcp._dispatch_analyze as _da

        def _raise(backend: Any, ds: str) -> list:
            raise ValueError(f"Dataset {ds!r} not found")

        monkeypatch.setattr(_da, "_fetch_chunks_for_dataset", _raise)
        server = _build_server(writes_enabled=False)
        result = _call(server, _ANALYZE_CORPUS, {"dataset": "nonexistent_dataset"})
        # The tool must surface an error, not crash the MCP server.
        is_error = getattr(result, "isError", False)
        assert is_error, (
            "Expected isError=True for a missing dataset; "
            f"got isError=False with payload: {_payload(result)}"
        )

    def test_empty_dataset_returns_zero_counts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = self._dispatch(monkeypatch, chunks=[])
        assert payload.get("n_chunks", -1) == 0, (
            f"Expected n_chunks=0 for empty dataset; got: {payload.get('n_chunks')}"
        )


# ===========================================================================
# 3. find_duplicates dispatch
# ===========================================================================


class TestFindDuplicatesDispatch:
    """find_duplicates returns exact_duplicates + near_duplicates."""

    def _dispatch(
        self,
        monkeypatch: pytest.MonkeyPatch,
        dataset: str = "demo",
        threshold: float = 0.85,
        chunks: list[dict] | None = None,
    ) -> dict:
        if chunks is None:
            chunks = _DEMO_CHUNKS

        import corpus_forge.mcp._dispatch_analyze as _da

        monkeypatch.setattr(
            _da,
            "_fetch_chunks_for_dataset",
            lambda backend, ds: chunks,
        )
        server = _build_server(writes_enabled=False)
        result = _call(
            server,
            _FIND_DUPLICATES,
            {"dataset": dataset, "threshold": threshold},
        )
        assert not getattr(result, "isError", False), (
            f"{_FIND_DUPLICATES} returned isError=True: "
            + "".join(getattr(b, "text", "") for b in getattr(result, "content", []))
        )
        return _payload(result)

    def test_returns_exact_duplicates_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = self._dispatch(monkeypatch)
        assert "exact_duplicates" in payload, (
            f"Expected 'exact_duplicates' key; got: {list(payload.keys())}"
        )

    def test_returns_near_duplicates_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = self._dispatch(monkeypatch)
        assert "near_duplicates" in payload, (
            f"Expected 'near_duplicates' key; got: {list(payload.keys())}"
        )

    def test_exact_duplicates_detects_shared_content_hash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Chunks 1 and 3 share hash_a — they must appear as an exact-dup group."""
        payload = self._dispatch(monkeypatch)
        exact = payload["exact_duplicates"]
        # exact_duplicates is a dict of {hash: [chunk_ids]} or a list; either shape works.
        found = False
        if isinstance(exact, dict):
            for ids in exact.values():
                if 1 in ids and 3 in ids:
                    found = True
                    break
        elif isinstance(exact, list):
            for group in exact:
                ids = group.get("chunk_ids", [])
                if 1 in ids and 3 in ids:
                    found = True
                    break
        assert found, f"Expected chunks 1 and 3 (sharing hash_a) in exact_duplicates; got: {exact}"

    def test_output_is_json_serializable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = self._dispatch(monkeypatch)
        json.dumps(payload)

    def test_works_with_writes_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Core read-only contract: callable without writes_enabled=True."""
        import corpus_forge.mcp._dispatch_analyze as _da

        monkeypatch.setattr(
            _da,
            "_fetch_chunks_for_dataset",
            lambda backend, ds: _DEMO_CHUNKS,
        )
        server = _build_server(writes_enabled=False)
        result = _call(server, _FIND_DUPLICATES, {"dataset": "demo"})
        assert not getattr(result, "isError", False), (
            "Expected find_duplicates to succeed with writes_enabled=False; "
            "got isError=True: "
            + "".join(getattr(b, "text", "") for b in getattr(result, "content", []))
        )

    def test_missing_dataset_returns_error_payload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import corpus_forge.mcp._dispatch_analyze as _da

        monkeypatch.setattr(
            _da,
            "_fetch_chunks_for_dataset",
            lambda backend, ds: (_ for _ in ()).throw(ValueError(f"Dataset {ds!r} not found")),
        )
        server = _build_server(writes_enabled=False)
        result = _call(server, _FIND_DUPLICATES, {"dataset": "ghost"})
        assert getattr(result, "isError", False), (
            "Expected isError=True for a missing dataset in find_duplicates"
        )


# ===========================================================================
# 4. cluster_topics dispatch
# ===========================================================================


class TestClusterTopicsDispatch:
    """cluster_topics returns topic clusters with cluster_id, chunk_ids, top_terms."""

    def _dispatch(
        self,
        monkeypatch: pytest.MonkeyPatch,
        dataset: str = "demo",
        min_cluster_size: int = 2,
        chunks: list[dict] | None = None,
    ) -> dict:
        if chunks is None:
            chunks = _DEMO_CHUNKS

        import corpus_forge.mcp._dispatch_analyze as _da

        monkeypatch.setattr(
            _da,
            "_fetch_chunks_for_dataset",
            lambda backend, ds: chunks,
        )
        server = _build_server(writes_enabled=False)
        result = _call(
            server,
            _CLUSTER_TOPICS,
            {"dataset": dataset, "min_cluster_size": min_cluster_size},
        )
        assert not getattr(result, "isError", False), (
            f"{_CLUSTER_TOPICS} returned isError=True: "
            + "".join(getattr(b, "text", "") for b in getattr(result, "content", []))
        )
        return _payload(result)

    def test_returns_clusters_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = self._dispatch(monkeypatch)
        assert "clusters" in payload, f"Expected 'clusters' key; got: {list(payload.keys())}"

    def test_each_cluster_has_cluster_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = self._dispatch(monkeypatch)
        clusters = payload["clusters"]
        if isinstance(clusters, list) and clusters:
            for c in clusters:
                assert "cluster_id" in c, (
                    f"Expected 'cluster_id' in cluster entry; got keys: {list(c.keys())}"
                )
        elif isinstance(clusters, dict) and clusters:
            # dict-keyed form: cluster_id is the key itself — acceptable
            pass

    def test_each_cluster_has_chunk_ids(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = self._dispatch(monkeypatch)
        clusters = payload["clusters"]
        if isinstance(clusters, list) and clusters:
            for c in clusters:
                assert "chunk_ids" in c, (
                    f"Expected 'chunk_ids' in cluster entry; got keys: {list(c.keys())}"
                )

    def test_each_cluster_has_top_terms(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = self._dispatch(monkeypatch)
        clusters = payload["clusters"]
        if isinstance(clusters, list) and clusters:
            for c in clusters:
                assert "top_terms" in c, (
                    f"Expected 'top_terms' in cluster entry; got keys: {list(c.keys())}"
                )

    def test_output_is_json_serializable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = self._dispatch(monkeypatch)
        json.dumps(payload)

    def test_works_with_writes_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import corpus_forge.mcp._dispatch_analyze as _da

        monkeypatch.setattr(
            _da,
            "_fetch_chunks_for_dataset",
            lambda backend, ds: _DEMO_CHUNKS,
        )
        server = _build_server(writes_enabled=False)
        result = _call(server, _CLUSTER_TOPICS, {"dataset": "demo"})
        assert not getattr(result, "isError", False), (
            "Expected cluster_topics to succeed with writes_enabled=False"
        )

    def test_missing_dataset_returns_error_payload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import corpus_forge.mcp._dispatch_analyze as _da

        def _raise(backend: Any, ds: str) -> list:
            raise ValueError(f"Dataset {ds!r} not found")

        monkeypatch.setattr(_da, "_fetch_chunks_for_dataset", _raise)
        server = _build_server(writes_enabled=False)
        result = _call(server, _CLUSTER_TOPICS, {"dataset": "no_such_ds"})
        assert getattr(result, "isError", False), (
            "Expected isError=True for a missing dataset in cluster_topics"
        )


# ===========================================================================
# 5. score_quality dispatch
# ===========================================================================


class TestScoreQualityDispatch:
    """score_quality returns quality scores in [0, 1]; persist gated on writes_enabled."""

    def _dispatch_chunk_ids(
        self,
        monkeypatch: pytest.MonkeyPatch,
        chunk_ids: list[int] | None = None,
        persist: bool = False,
        writes_enabled: bool = False,
        chunks: list[dict] | None = None,
    ) -> Any:
        if chunk_ids is None:
            chunk_ids = _DEMO_CHUNK_IDS
        if chunks is None:
            chunks = _DEMO_CHUNKS

        import corpus_forge.mcp._dispatch_analyze as _da

        monkeypatch.setattr(
            _da,
            "_fetch_chunks_by_ids",
            lambda backend, ids: [c for c in chunks if c["id"] in ids],
        )
        server = _build_server(writes_enabled=writes_enabled)
        return _call(
            server,
            _SCORE_QUALITY,
            {"chunk_ids": chunk_ids, "persist": persist},
        )

    def test_returns_scores_for_each_chunk_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = self._dispatch_chunk_ids(monkeypatch, chunk_ids=[1, 2, 5])
        assert not getattr(result, "isError", False), (
            f"{_SCORE_QUALITY} returned isError=True: "
            + "".join(getattr(b, "text", "") for b in getattr(result, "content", []))
        )
        payload = _payload(result)
        # Payload must contain quality scores keyed by chunk_id (int or str key).
        scores_map = payload.get("scores", payload)
        assert len(scores_map) == 3, (
            f"Expected 3 score entries for chunk_ids=[1,2,5]; got: {scores_map}"
        )

    def test_scores_are_in_zero_one_range(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = self._dispatch_chunk_ids(monkeypatch, chunk_ids=_DEMO_CHUNK_IDS)
        payload = _payload(result)
        scores_map = payload.get("scores", payload)
        for cid, score in scores_map.items():
            assert 0.0 <= float(score) <= 1.0, (
                f"Score for chunk_id={cid} is {score!r}, outside [0, 1]"
            )

    def test_output_is_json_serializable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = self._dispatch_chunk_ids(monkeypatch)
        payload = _payload(result)
        json.dumps(payload)

    def test_persist_false_does_not_write_audit_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """persist=False must NOT invoke any write on the backend."""
        import corpus_forge.mcp._dispatch_analyze as _da

        monkeypatch.setattr(
            _da,
            "_fetch_chunks_by_ids",
            lambda backend, ids: [c for c in _DEMO_CHUNKS if c["id"] in ids],
        )
        server = _build_server(writes_enabled=False)
        # Spy on persist_quality_signals via monkeypatch on the module.
        persist_calls: list[Any] = []

        def _fake_persist(*args: Any, **kwargs: Any) -> int:
            persist_calls.append((args, kwargs))
            return 0

        monkeypatch.setattr(_da, "_persist_quality_signals", _fake_persist)
        _call(server, _SCORE_QUALITY, {"chunk_ids": [1, 2], "persist": False})
        assert persist_calls == [], (
            f"Expected no persist calls with persist=False; got: {persist_calls}"
        )

    def test_persist_true_requires_writes_enabled_true(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """persist=True without writes_enabled=True must return a clear error payload."""
        result = self._dispatch_chunk_ids(
            monkeypatch,
            chunk_ids=[1, 2],
            persist=True,
            writes_enabled=False,
        )
        is_error = getattr(result, "isError", False)
        if not is_error:
            # Some implementations return a structured error payload instead of isError.
            payload = _payload(result)
            has_error_message = (
                "error" in payload
                or "persist" in str(payload).lower()
                or "writes_enabled" in str(payload).lower()
                or "permission" in str(payload).lower()
            )
            assert has_error_message, (
                "Expected a clear error for persist=True without writes_enabled; "
                f"got payload: {payload}"
            )
        # If isError=True that already satisfies the assertion.

    def test_persist_true_with_writes_enabled_calls_persist(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """persist=True + writes_enabled=True must invoke the persistence helper."""
        import corpus_forge.mcp._dispatch_analyze as _da

        monkeypatch.setattr(
            _da,
            "_fetch_chunks_by_ids",
            lambda backend, ids: [c for c in _DEMO_CHUNKS if c["id"] in ids],
        )
        persist_calls: list[Any] = []

        def _fake_persist(*args: Any, **kwargs: Any) -> int:
            persist_calls.append((args, kwargs))
            return len(args[1]) if len(args) > 1 else 0

        monkeypatch.setattr(_da, "_persist_quality_signals", _fake_persist)
        server = _build_server(writes_enabled=True)
        result = _call(server, _SCORE_QUALITY, {"chunk_ids": [1, 2], "persist": True})
        assert not getattr(result, "isError", False), (
            "Expected no error for persist=True with writes_enabled=True"
        )
        assert len(persist_calls) >= 1, (
            "Expected _persist_quality_signals to be called when persist=True + writes_enabled=True"
        )

    def test_empty_chunk_ids_returns_empty_scores(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import corpus_forge.mcp._dispatch_analyze as _da

        monkeypatch.setattr(_da, "_fetch_chunks_by_ids", lambda backend, ids: [])
        server = _build_server(writes_enabled=False)
        result = _call(server, _SCORE_QUALITY, {"chunk_ids": []})
        assert not getattr(result, "isError", False), "Expected no error for empty chunk_ids list"
        payload = _payload(result)
        scores_map = payload.get("scores", payload)
        assert len(scores_map) == 0, (
            f"Expected empty scores map for empty chunk_ids; got: {scores_map}"
        )

    def test_dataset_scope_also_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """score_quality can be called with dataset= instead of chunk_ids=."""
        import corpus_forge.mcp._dispatch_analyze as _da

        monkeypatch.setattr(
            _da,
            "_fetch_chunks_for_dataset",
            lambda backend, ds: _DEMO_CHUNKS,
        )
        server = _build_server(writes_enabled=False)
        result = _call(server, _SCORE_QUALITY, {"dataset": "demo"})
        assert not getattr(result, "isError", False), (
            "Expected no error when calling score_quality with dataset= argument; "
            "got isError=True: "
            + "".join(getattr(b, "text", "") for b in getattr(result, "content", []))
        )
        payload = _payload(result)
        scores_map = payload.get("scores", payload)
        assert len(scores_map) == len(_DEMO_CHUNKS), (
            f"Expected {len(_DEMO_CHUNKS)} scores; got {len(scores_map)}"
        )


# ===========================================================================
# 6. Read-only contract — all four tools work with writes_enabled=False
# ===========================================================================


class TestReadOnlyContract:
    """Explicit gate: all four analyze tools callable without granting writes."""

    def _assert_tool_works_without_writes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tool_name: str,
        arguments: dict,
    ) -> None:
        import corpus_forge.mcp._dispatch_analyze as _da

        monkeypatch.setattr(
            _da,
            "_fetch_chunks_for_dataset",
            lambda backend, ds: _DEMO_CHUNKS,
        )
        monkeypatch.setattr(
            _da,
            "_fetch_chunks_by_ids",
            lambda backend, ids: [c for c in _DEMO_CHUNKS if c["id"] in ids],
        )
        server = _build_server(writes_enabled=False)
        result = _call(server, tool_name, arguments)
        assert not getattr(result, "isError", False), (
            f"Expected {tool_name!r} to succeed with writes_enabled=False; "
            f"got isError=True: "
            + "".join(getattr(b, "text", "") for b in getattr(result, "content", []))
        )

    def test_analyze_corpus_read_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._assert_tool_works_without_writes(monkeypatch, _ANALYZE_CORPUS, {"dataset": "demo"})

    def test_find_duplicates_read_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._assert_tool_works_without_writes(monkeypatch, _FIND_DUPLICATES, {"dataset": "demo"})

    def test_cluster_topics_read_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._assert_tool_works_without_writes(monkeypatch, _CLUSTER_TOPICS, {"dataset": "demo"})

    def test_score_quality_read_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._assert_tool_works_without_writes(
            monkeypatch,
            _SCORE_QUALITY,
            {"chunk_ids": [1, 2], "persist": False},
        )


# ===========================================================================
# 7. Idempotency — re-running read tools yields same result
# ===========================================================================


class TestIdempotency:
    """Running a read tool twice with the same arguments yields the same result."""

    def test_analyze_corpus_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import corpus_forge.mcp._dispatch_analyze as _da

        monkeypatch.setattr(
            _da,
            "_fetch_chunks_for_dataset",
            lambda backend, ds: _DEMO_CHUNKS,
        )
        server = _build_server(writes_enabled=False)
        r1 = _payload(_call(server, _ANALYZE_CORPUS, {"dataset": "demo"}))
        r2 = _payload(_call(server, _ANALYZE_CORPUS, {"dataset": "demo"}))
        assert r1 == r2, f"Expected idempotent analyze_corpus; first={r1}, second={r2}"

    def test_score_quality_idempotent_no_persist(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import corpus_forge.mcp._dispatch_analyze as _da

        monkeypatch.setattr(
            _da,
            "_fetch_chunks_by_ids",
            lambda backend, ids: [c for c in _DEMO_CHUNKS if c["id"] in ids],
        )
        server = _build_server(writes_enabled=False)
        args = {"chunk_ids": [1, 2, 5], "persist": False}
        r1 = _payload(_call(server, _SCORE_QUALITY, args))
        r2 = _payload(_call(server, _SCORE_QUALITY, args))
        assert r1 == r2, (
            f"Expected idempotent score_quality (persist=False); first={r1}, second={r2}"
        )
