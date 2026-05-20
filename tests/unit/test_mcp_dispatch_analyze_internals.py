"""Unit tests for ``corpus_forge.mcp._dispatch_analyze``.

The dispatch closures are also exercised by ``tests/integration/test_mcp_
analyze_tools.py`` end-to-end, but those tests live in ``tests/integration``
and don't count toward the unit-suite coverage gate.  These unit tests pin
each dispatch's pure-function behavior with a MagicMock backend so coverage
of ``mcp/_dispatch_analyze`` exceeds the threshold.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from corpus_forge.mcp._dispatch_analyze import (
    _dispatch_analyze_corpus,
    _dispatch_cluster_topics,
    _dispatch_find_duplicates,
    _dispatch_score_quality,
    _fetch_chunks_by_ids,
    _fetch_chunks_for_dataset,
    _json_safe,
    _json_safe_dict,
    _persist_quality_signals,
)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _is_error_result(result: Any) -> bool:
    return bool(getattr(result, "isError", False))


def _error_text(result: Any) -> str:
    content = getattr(result, "content", [])
    return "".join(getattr(b, "text", "") for b in content)


# ─────────────────────────────────────────────────────────────────────────────
# _json_safe / _json_safe_dict helpers
# ─────────────────────────────────────────────────────────────────────────────


def test_json_safe_passes_through_primitives() -> None:
    # _json_safe is intentionally narrow — only coerces numeric types
    # (incl. numpy scalars). Strings / None / containers pass through
    # untouched.
    assert _json_safe(1) == 1
    assert _json_safe(1.5) == 1.5
    assert _json_safe("x") == "x"
    assert _json_safe(None) is None


def test_json_safe_passes_through_lists_and_dicts() -> None:
    payload = {"a": [1, 2, {"b": "c"}]}
    assert _json_safe(payload) == payload


def test_json_safe_returns_value_unchanged_for_unknown_types() -> None:
    class Custom:
        pass

    obj = Custom()
    # Falls through the type checks and returns the value as-is.
    assert _json_safe(obj) is obj


def test_json_safe_handles_numpy_scalar_if_numpy_imported() -> None:
    import sys

    if "numpy" not in sys.modules:
        pytest.skip("numpy not imported; coercion path not exercised here")
    import numpy as np

    out = _json_safe(np.int64(7))
    assert isinstance(out, int)
    assert out == 7


def test_json_safe_dict_returns_plain_dict() -> None:
    out = _json_safe_dict({"cluster_id": 1, "chunk_ids": [2, 3]})
    assert isinstance(out, dict)
    assert out["cluster_id"] == 1
    assert out["chunk_ids"] == [2, 3]


# ─────────────────────────────────────────────────────────────────────────────
# _fetch_chunks_for_dataset
# ─────────────────────────────────────────────────────────────────────────────


def test_fetch_chunks_for_dataset_returns_list() -> None:
    backend = MagicMock()
    backend.list_chunks.return_value = [{"id": 1, "text": "x"}, {"id": 2, "text": "y"}]
    out = _fetch_chunks_for_dataset(backend, "demo")
    assert out == [{"id": 1, "text": "x"}, {"id": 2, "text": "y"}]
    backend.list_chunks.assert_called_once_with(dataset="demo")


def test_fetch_chunks_for_dataset_missing_method_raises() -> None:
    backend = object()  # no list_chunks
    with pytest.raises(ValueError, match="does not expose list_chunks"):
        _fetch_chunks_for_dataset(backend, "demo")


def test_fetch_chunks_for_dataset_none_result_raises_not_found() -> None:
    backend = MagicMock()
    backend.list_chunks.return_value = None
    with pytest.raises(ValueError, match="not found"):
        _fetch_chunks_for_dataset(backend, "missing")


# ─────────────────────────────────────────────────────────────────────────────
# _fetch_chunks_by_ids
# ─────────────────────────────────────────────────────────────────────────────


def test_fetch_chunks_by_ids_empty_input_returns_empty() -> None:
    assert _fetch_chunks_by_ids(MagicMock(), []) == []


def test_fetch_chunks_by_ids_missing_method_returns_empty() -> None:
    backend = object()
    assert _fetch_chunks_by_ids(backend, [1, 2]) == []


def test_fetch_chunks_by_ids_calls_get_chunk_per_id() -> None:
    backend = MagicMock()
    backend.get_chunk.side_effect = lambda cid: {"id": cid, "text": f"chunk-{cid}"}
    out = _fetch_chunks_by_ids(backend, [10, 20, 30])
    assert [c["id"] for c in out] == [10, 20, 30]


def test_fetch_chunks_by_ids_skips_none_results() -> None:
    backend = MagicMock()
    backend.get_chunk.side_effect = [None, {"id": 2}, None, {"id": 4}]
    out = _fetch_chunks_by_ids(backend, [1, 2, 3, 4])
    assert [c["id"] for c in out] == [2, 4]


# ─────────────────────────────────────────────────────────────────────────────
# _persist_quality_signals
# ─────────────────────────────────────────────────────────────────────────────


def test_persist_quality_signals_no_conn_returns_zero() -> None:
    backend = object()  # neither .conn nor .connection
    assert _persist_quality_signals(backend, [1, 2], [0.5, 0.7]) == 0


# ─────────────────────────────────────────────────────────────────────────────
# _dispatch_analyze_corpus
# ─────────────────────────────────────────────────────────────────────────────


def test_dispatch_analyze_corpus_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "corpus_forge.mcp._dispatch_analyze._fetch_chunks_for_dataset",
        lambda backend, dataset: [
            {"id": 1, "text": "alpha", "token_count": 10, "document_id": 100},
            {"id": 2, "text": "beta", "token_count": 12, "document_id": 100},
            {"id": 3, "text": "gamma", "token_count": 8, "document_id": 200},
        ],
    )
    out = _run(
        _dispatch_analyze_corpus(
            {"dataset": "demo"},
            backend=MagicMock(),
            writes_enabled=False,
        )
    )
    assert out["n_chunks"] == 3
    assert out["n_documents"] == 2
    assert "token_stats" in out


def test_dispatch_analyze_corpus_missing_dataset_returns_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*_a: Any, **_kw: Any) -> Any:
        raise ValueError("Dataset 'demo' not found")

    monkeypatch.setattr(
        "corpus_forge.mcp._dispatch_analyze._fetch_chunks_for_dataset",
        _raise,
    )
    out = _run(
        _dispatch_analyze_corpus(
            {"dataset": "demo"},
            backend=MagicMock(),
            writes_enabled=False,
        )
    )
    assert _is_error_result(out)
    assert "analyze_corpus" in _error_text(out)


# ─────────────────────────────────────────────────────────────────────────────
# _dispatch_find_duplicates
# ─────────────────────────────────────────────────────────────────────────────


def test_dispatch_find_duplicates_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "corpus_forge.mcp._dispatch_analyze._fetch_chunks_for_dataset",
        lambda backend, dataset: [
            {"id": 1, "text": "exact dup", "content_hash": "h1"},
            {"id": 2, "text": "exact dup", "content_hash": "h1"},
            {"id": 3, "text": "unique chunk text", "content_hash": "h2"},
        ],
    )
    out = _run(
        _dispatch_find_duplicates(
            {"dataset": "demo", "threshold": 0.85},
            backend=MagicMock(),
            writes_enabled=False,
        )
    )
    assert "exact_duplicates" in out
    assert "near_duplicates" in out
    # Exact dup contains hash 'h1' with chunk_ids [1, 2].
    assert "h1" in out["exact_duplicates"]
    assert sorted(out["exact_duplicates"]["h1"]) == [1, 2]


def test_dispatch_find_duplicates_missing_dataset_returns_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*_a: Any, **_kw: Any) -> Any:
        raise ValueError("missing dataset")

    monkeypatch.setattr(
        "corpus_forge.mcp._dispatch_analyze._fetch_chunks_for_dataset",
        _raise,
    )
    out = _run(
        _dispatch_find_duplicates(
            {"dataset": "demo"},
            backend=MagicMock(),
            writes_enabled=False,
        )
    )
    assert _is_error_result(out)


# ─────────────────────────────────────────────────────────────────────────────
# _dispatch_cluster_topics
# ─────────────────────────────────────────────────────────────────────────────


def test_dispatch_cluster_topics_empty_returns_empty_clusters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "corpus_forge.mcp._dispatch_analyze._fetch_chunks_for_dataset",
        lambda backend, dataset: [],
    )
    out = _run(
        _dispatch_cluster_topics(
            {"dataset": "demo"},
            backend=MagicMock(),
            writes_enabled=False,
        )
    )
    assert out == {"clusters": []}


def test_dispatch_cluster_topics_too_few_embeddings_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "corpus_forge.mcp._dispatch_analyze._fetch_chunks_for_dataset",
        lambda backend, dataset: [{"id": 1, "text": "x"}],  # no embedding
    )
    out = _run(
        _dispatch_cluster_topics(
            {"dataset": "demo"},
            backend=MagicMock(),
            writes_enabled=False,
        )
    )
    assert out == {"clusters": []}


def test_dispatch_cluster_topics_missing_dataset_returns_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*_a: Any, **_kw: Any) -> Any:
        raise ValueError("no dataset")

    monkeypatch.setattr(
        "corpus_forge.mcp._dispatch_analyze._fetch_chunks_for_dataset",
        _raise,
    )
    out = _run(
        _dispatch_cluster_topics(
            {"dataset": "demo"},
            backend=MagicMock(),
            writes_enabled=False,
        )
    )
    assert _is_error_result(out)


# ─────────────────────────────────────────────────────────────────────────────
# _dispatch_score_quality
# ─────────────────────────────────────────────────────────────────────────────


def test_dispatch_score_quality_persist_without_writes_returns_error() -> None:
    out = _run(
        _dispatch_score_quality(
            {"chunk_ids": [1, 2], "persist": True},
            backend=MagicMock(),
            writes_enabled=False,
        )
    )
    assert _is_error_result(out)
    assert "writes_enabled" in _error_text(out)


def test_dispatch_score_quality_no_scope_returns_error() -> None:
    out = _run(
        _dispatch_score_quality(
            {},
            backend=MagicMock(),
            writes_enabled=False,
        )
    )
    assert _is_error_result(out)
    assert "chunk_ids or dataset" in _error_text(out)


def test_dispatch_score_quality_by_chunk_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "corpus_forge.mcp._dispatch_analyze._fetch_chunks_by_ids",
        lambda backend, ids: [
            {"id": cid, "text": f"chunk text {cid}", "token_count": 50 + cid} for cid in ids
        ],
    )
    out = _run(
        _dispatch_score_quality(
            {"chunk_ids": [10, 20]},
            backend=MagicMock(),
            writes_enabled=False,
        )
    )
    assert "scores" in out
    assert set(out["scores"].keys()) == {"10", "20"}
    for s in out["scores"].values():
        assert 0.0 <= s <= 1.0


def test_dispatch_score_quality_by_dataset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "corpus_forge.mcp._dispatch_analyze._fetch_chunks_for_dataset",
        lambda backend, dataset: [
            {"id": 1, "text": "alpha beta gamma delta", "token_count": 10},
            {"id": 2, "text": "another reasonable chunk", "token_count": 7},
        ],
    )
    out = _run(
        _dispatch_score_quality(
            {"dataset": "demo"},
            backend=MagicMock(),
            writes_enabled=False,
        )
    )
    assert set(out["scores"].keys()) == {"1", "2"}


def test_dispatch_score_quality_empty_chunk_ids_returns_empty_map(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "corpus_forge.mcp._dispatch_analyze._fetch_chunks_by_ids",
        lambda backend, ids: [],
    )
    out = _run(
        _dispatch_score_quality(
            {"chunk_ids": []},
            backend=MagicMock(),
            writes_enabled=False,
        )
    )
    assert out == {"scores": {}}


def test_dispatch_score_quality_persist_writes_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def _fake_persist(backend: Any, chunk_ids: list[int], scores: list[float]) -> int:
        captured["chunk_ids"] = chunk_ids
        captured["scores"] = scores
        return len(chunk_ids)

    monkeypatch.setattr(
        "corpus_forge.mcp._dispatch_analyze._fetch_chunks_by_ids",
        lambda backend, ids: [{"id": cid, "text": f"text {cid}", "token_count": 5} for cid in ids],
    )
    monkeypatch.setattr(
        "corpus_forge.mcp._dispatch_analyze._persist_quality_signals",
        _fake_persist,
    )

    out = _run(
        _dispatch_score_quality(
            {"chunk_ids": [7, 9], "persist": True},
            backend=MagicMock(),
            writes_enabled=True,
        )
    )
    assert set(out["scores"].keys()) == {"7", "9"}
    assert captured["chunk_ids"] == [7, 9]
    assert len(captured["scores"]) == 2


def test_dispatch_score_quality_dataset_lookup_failure_returns_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*_a: Any, **_kw: Any) -> Any:
        raise ValueError("no such dataset")

    monkeypatch.setattr(
        "corpus_forge.mcp._dispatch_analyze._fetch_chunks_for_dataset",
        _raise,
    )
    out = _run(
        _dispatch_score_quality(
            {"dataset": "missing"},
            backend=MagicMock(),
            writes_enabled=False,
        )
    )
    assert _is_error_result(out)
