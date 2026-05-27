"""Unit tests for ``corpus_forge.mcp._dispatch_analyze``.

The dispatch closures are also exercised by ``tests/integration/test_mcp_
analyze_tools.py`` end-to-end, but those tests live in ``tests/integration``
and don't count toward the unit-suite coverage gate.

These unit tests use a fake backend with the right interface so the REAL
``_fetch_chunks_for_dataset`` / ``_fetch_chunks_by_ids`` / ``_persist_quality_
signals`` helpers do the work — avoiding monkeypatch-on-module-level issues
that surface under ``pytest -n auto --cov``.
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


class _FakeBackend:
    """Backend whose ``list_chunks`` / ``get_chunk`` return canned data.

    Defining real methods (not MagicMocks) keeps `_fetch_chunks_for_dataset`
    + `_fetch_chunks_by_ids` honest under xdist + coverage instrumentation.
    """

    def __init__(
        self,
        *,
        chunks_by_dataset: dict[str, list[dict] | None] | None = None,
        chunks_by_id: dict[int, dict] | None = None,
    ) -> None:
        self._chunks_by_dataset = chunks_by_dataset or {}
        self._chunks_by_id = chunks_by_id or {}
        self.persist_calls: list[tuple[list[int], list[float], str]] = []

    def list_chunks(self, *, dataset: str) -> list[dict] | None:
        return self._chunks_by_dataset.get(dataset)

    def get_chunk(self, cid: int) -> dict | None:
        return self._chunks_by_id.get(cid)


# ─────────────────────────────────────────────────────────────────────────────
# _json_safe / _json_safe_dict helpers
# ─────────────────────────────────────────────────────────────────────────────


def test_json_safe_passes_through_primitives() -> None:
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
    backend = _FakeBackend(
        chunks_by_dataset={"demo": [{"id": 1, "text": "x"}, {"id": 2, "text": "y"}]}
    )
    out = _fetch_chunks_for_dataset(backend, "demo")
    assert out == [{"id": 1, "text": "x"}, {"id": 2, "text": "y"}]


def test_fetch_chunks_for_dataset_missing_method_raises() -> None:
    backend = object()  # no list_chunks
    with pytest.raises(ValueError, match="does not expose list_chunks"):
        _fetch_chunks_for_dataset(backend, "demo")


def test_fetch_chunks_for_dataset_none_result_raises_not_found() -> None:
    backend = _FakeBackend(chunks_by_dataset={"demo": None})
    with pytest.raises(ValueError, match="not found"):
        _fetch_chunks_for_dataset(backend, "demo")


# ─────────────────────────────────────────────────────────────────────────────
# _fetch_chunks_by_ids
# ─────────────────────────────────────────────────────────────────────────────


def test_fetch_chunks_by_ids_empty_input_returns_empty() -> None:
    assert _fetch_chunks_by_ids(_FakeBackend(), []) == []


def test_fetch_chunks_by_ids_missing_method_returns_empty() -> None:
    backend = object()
    assert _fetch_chunks_by_ids(backend, [1, 2]) == []


def test_fetch_chunks_by_ids_calls_get_chunk_per_id() -> None:
    backend = _FakeBackend(
        chunks_by_id={cid: {"id": cid, "text": f"chunk-{cid}"} for cid in (10, 20, 30)}
    )
    out = _fetch_chunks_by_ids(backend, [10, 20, 30])
    assert [c["id"] for c in out] == [10, 20, 30]


def test_fetch_chunks_by_ids_skips_none_results() -> None:
    backend = _FakeBackend(chunks_by_id={2: {"id": 2}, 4: {"id": 4}})
    # Ids 1 and 3 are absent → get_chunk returns None → skipped.
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


def test_dispatch_analyze_corpus_happy_path() -> None:
    backend = _FakeBackend(
        chunks_by_dataset={
            "demo": [
                {"id": 1, "text": "alpha", "token_count": 10, "document_id": 100},
                {"id": 2, "text": "beta", "token_count": 12, "document_id": 100},
                {"id": 3, "text": "gamma", "token_count": 8, "document_id": 200},
            ]
        }
    )
    out = _run(
        _dispatch_analyze_corpus(
            {"dataset": "demo"},
            backend=backend,
            writes_enabled=False,
        )
    )
    assert out["n_chunks"] == 3
    assert out["n_documents"] == 2
    assert "token_stats" in out


def test_dispatch_analyze_corpus_missing_dataset_returns_error() -> None:
    # chunks_by_dataset returns None for "demo" → _fetch_chunks raises ValueError.
    backend = _FakeBackend(chunks_by_dataset={"demo": None})
    out = _run(
        _dispatch_analyze_corpus(
            {"dataset": "demo"},
            backend=backend,
            writes_enabled=False,
        )
    )
    assert _is_error_result(out)
    assert "analyze_corpus" in _error_text(out)


def test_dispatch_analyze_corpus_unknown_dataset_returns_error() -> None:
    # No "missing" key at all → list_chunks returns None.
    backend = _FakeBackend(chunks_by_dataset={})
    out = _run(
        _dispatch_analyze_corpus(
            {"dataset": "missing"},
            backend=backend,
            writes_enabled=False,
        )
    )
    assert _is_error_result(out)


# ─────────────────────────────────────────────────────────────────────────────
# _dispatch_find_duplicates
# ─────────────────────────────────────────────────────────────────────────────


def test_dispatch_find_duplicates_happy_path() -> None:
    backend = _FakeBackend(
        chunks_by_dataset={
            "demo": [
                {"id": 1, "text": "exact dup", "content_hash": "h1"},
                {"id": 2, "text": "exact dup", "content_hash": "h1"},
                {"id": 3, "text": "unique chunk text", "content_hash": "h2"},
            ]
        }
    )
    out = _run(
        _dispatch_find_duplicates(
            {"dataset": "demo", "threshold": 0.85},
            backend=backend,
            writes_enabled=False,
        )
    )
    assert "exact_duplicates" in out
    assert "near_duplicates" in out
    assert "h1" in out["exact_duplicates"]
    assert sorted(out["exact_duplicates"]["h1"]) == [1, 2]


def test_dispatch_find_duplicates_missing_dataset_returns_error() -> None:
    backend = _FakeBackend(chunks_by_dataset={"demo": None})
    out = _run(
        _dispatch_find_duplicates(
            {"dataset": "demo"},
            backend=backend,
            writes_enabled=False,
        )
    )
    assert _is_error_result(out)


# ─────────────────────────────────────────────────────────────────────────────
# _dispatch_cluster_topics
# ─────────────────────────────────────────────────────────────────────────────


def test_dispatch_cluster_topics_empty_returns_empty_clusters() -> None:
    backend = _FakeBackend(chunks_by_dataset={"demo": []})
    out = _run(
        _dispatch_cluster_topics(
            {"dataset": "demo"},
            backend=backend,
            writes_enabled=False,
        )
    )
    assert out == {"clusters": []}


def test_dispatch_cluster_topics_too_few_embeddings_returns_empty() -> None:
    backend = _FakeBackend(chunks_by_dataset={"demo": [{"id": 1, "text": "x"}]})
    out = _run(
        _dispatch_cluster_topics(
            {"dataset": "demo"},
            backend=backend,
            writes_enabled=False,
        )
    )
    assert out == {"clusters": []}


def test_dispatch_cluster_topics_missing_dataset_returns_error() -> None:
    backend = _FakeBackend(chunks_by_dataset={"demo": None})
    out = _run(
        _dispatch_cluster_topics(
            {"dataset": "demo"},
            backend=backend,
            writes_enabled=False,
        )
    )
    assert _is_error_result(out)


def test_dispatch_cluster_topics_runs_on_embedded_chunks() -> None:
    """Exercises the real cluster_topics + top_terms_per_cluster pipeline."""
    # Two visually-separable groups in 4-D space — small but enough to drive
    # the HDBSCAN/c-TF-IDF path with min_cluster_size=2.
    chunks = [
        {"id": 1, "text": "apple banana orange fruit", "embedding": [1.0, 0.0, 0.0, 0.0]},
        {"id": 2, "text": "apple banana grape", "embedding": [1.0, 0.05, 0.0, 0.0]},
        {"id": 3, "text": "apple orange pear", "embedding": [1.0, 0.0, 0.05, 0.0]},
        {"id": 4, "text": "car truck bus vehicle", "embedding": [0.0, 0.0, 0.0, 1.0]},
        {"id": 5, "text": "car truck van", "embedding": [0.0, 0.05, 0.0, 1.0]},
        {"id": 6, "text": "car van auto", "embedding": [0.0, 0.0, 0.05, 1.0]},
    ]
    backend = _FakeBackend(chunks_by_dataset={"demo": chunks})
    out = _run(
        _dispatch_cluster_topics(
            {"dataset": "demo", "min_cluster_size": 2},
            backend=backend,
            writes_enabled=False,
        )
    )
    assert "clusters" in out
    # Each cluster has the three documented keys.
    for c in out["clusters"]:
        assert set(c) >= {"cluster_id", "chunk_ids", "top_terms"}
        assert isinstance(c["cluster_id"], str)
        assert isinstance(c["chunk_ids"], list)


def test_dispatch_cluster_topics_filters_chunks_without_embedding() -> None:
    """Chunks whose embedding is missing/None are skipped before clustering."""
    chunks = [
        {"id": 1, "text": "alpha", "embedding": None},
        {"id": 2, "text": "beta"},  # no embedding key at all
        {"id": 3, "text": "gamma", "embedding": [1.0, 0.0]},
    ]
    backend = _FakeBackend(chunks_by_dataset={"demo": chunks})
    out = _run(
        _dispatch_cluster_topics(
            {"dataset": "demo", "min_cluster_size": 2},
            backend=backend,
            writes_enabled=False,
        )
    )
    # Only 1 embedded chunk → short-circuit to no clusters.
    assert out == {"clusters": []}


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


def test_dispatch_score_quality_by_chunk_ids() -> None:
    backend = _FakeBackend(
        chunks_by_id={
            cid: {"id": cid, "text": f"chunk text {cid}", "token_count": 50 + cid}
            for cid in (10, 20)
        }
    )
    out = _run(
        _dispatch_score_quality(
            {"chunk_ids": [10, 20]},
            backend=backend,
            writes_enabled=False,
        )
    )
    assert "scores" in out
    assert set(out["scores"].keys()) == {"10", "20"}
    for s in out["scores"].values():
        assert 0.0 <= s <= 1.0


def test_dispatch_score_quality_chunk_ids_lookup_failure_returns_error() -> None:
    """A backend that raises while fetching chunk_ids → graceful error result.

    Drives the `_fetch_chunks_by_ids` try/except in the chunk_ids branch
    (the symmetric guard to the dataset-path failure already pinned below).
    """

    class _BoomBackend(_FakeBackend):
        def get_chunk(self, cid: int):
            raise RuntimeError("backend exploded")

    out = _run(
        _dispatch_score_quality(
            {"chunk_ids": [1, 2]},
            backend=_BoomBackend(),
            writes_enabled=False,
        )
    )
    assert "backend exploded" in _error_text(out)


def test_dispatch_score_quality_by_dataset() -> None:
    backend = _FakeBackend(
        chunks_by_dataset={
            "demo": [
                {"id": 1, "text": "alpha beta gamma delta", "token_count": 10},
                {"id": 2, "text": "another reasonable chunk", "token_count": 7},
            ]
        }
    )
    out = _run(
        _dispatch_score_quality(
            {"dataset": "demo"},
            backend=backend,
            writes_enabled=False,
        )
    )
    assert set(out["scores"].keys()) == {"1", "2"}


def test_dispatch_score_quality_empty_chunk_ids_returns_empty_map() -> None:
    backend = _FakeBackend()
    out = _run(
        _dispatch_score_quality(
            {"chunk_ids": []},
            backend=backend,
            writes_enabled=False,
        )
    )
    assert out == {"scores": {}}


def test_dispatch_score_quality_dataset_lookup_failure_returns_error() -> None:
    backend = _FakeBackend(chunks_by_dataset={"missing": None})
    out = _run(
        _dispatch_score_quality(
            {"dataset": "missing"},
            backend=backend,
            writes_enabled=False,
        )
    )
    assert _is_error_result(out)


class _PersistRecordingBackend(_FakeBackend):
    """FakeBackend whose .conn attribute drives the real _persist_quality_signals.

    A `conn` attribute lets `_persist_quality_signals` find a connection;
    we point it at an in-memory SQLite so persist actually writes rows.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        import sqlite3 as _sqlite3

        self.conn = _sqlite3.connect(":memory:")
        self.conn.execute(
            """
            CREATE TABLE chunk_quality_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chunk_id INTEGER NOT NULL,
                signal_name TEXT NOT NULL,
                signal_value REAL,
                source TEXT NOT NULL,
                computed_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        self.conn.commit()


def test_dispatch_score_quality_persist_writes_real_rows() -> None:
    """When persist=True + writes_enabled=True, rows land in chunk_quality_signals."""
    backend = _PersistRecordingBackend(
        chunks_by_id={1: {"id": 1, "text": "alpha", "token_count": 8}}
    )
    out = _run(
        _dispatch_score_quality(
            {"chunk_ids": [1], "persist": True},
            backend=backend,
            writes_enabled=True,
        )
    )
    assert set(out["scores"].keys()) == {"1"}
    # Real row written via persist_quality_signals.
    count = backend.conn.execute("SELECT COUNT(*) FROM chunk_quality_signals").fetchone()[0]
    assert count == 1


def test_dispatch_score_quality_dataset_with_persist_aligns_resolved_ids() -> None:
    """Persisting by-dataset writes one row per resolved chunk."""
    backend = _PersistRecordingBackend(
        chunks_by_dataset={
            "demo": [
                {"id": 11, "text": "a", "token_count": 8},
                {"id": 22, "text": "b", "token_count": 9},
            ]
        }
    )
    out = _run(
        _dispatch_score_quality(
            {"dataset": "demo", "persist": True},
            backend=backend,
            writes_enabled=True,
        )
    )
    assert set(out["scores"].keys()) == {"11", "22"}
    chunk_ids = sorted(
        r[0] for r in backend.conn.execute("SELECT chunk_id FROM chunk_quality_signals")
    )
    assert chunk_ids == [11, 22]
