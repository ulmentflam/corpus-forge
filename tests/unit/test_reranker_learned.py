"""Phase P Wave 2 (P2-T2) — Unit tests for corpus_forge.retrieval.rerank.learned.

Pins the public shape of:
  - ``train_reranker(conn, out_path, *, source_filter=None) -> dict``
  - ``LearnedReranker(model_path)`` — conforms to the ``Reranker`` protocol.

Contract source: task P2-T2 brief.

RED state: ``from corpus_forge.retrieval.rerank.learned import ...`` fails
with ``ModuleNotFoundError: No module named
'corpus_forge.retrieval.rerank.learned'`` because ``learned.py`` does not
yet exist.

Key design decisions captured in tests
---------------------------------------
Training (``train_reranker``):
- Reads from ``search_result_events`` joined with ``search_sessions``.
- Feature set (pinned): ``[chunk_score, lexical_score, query_len]`` where
  ``chunk_score`` is ``search_result_events.value`` (defaults to 0.5 when
  NULL), ``lexical_score`` is derived from the ``source`` field presence
  (1.0 if source == "lexical", 0.0 otherwise), and ``query_len`` is
  ``len(session.query)``.
- Labels: ``signal == "thumbs_up"`` or ``value > 0.5`` → positive (1);
  ``signal == "thumbs_down"`` or ``value < 0.5`` → negative (0); neutral
  (``value == 0.5`` without an explicit signal) → skip.
- Model: ``sklearn.linear_model.LogisticRegression``, lazy-imported.
- Persists via ``joblib.dump`` at ``out_path``.
- Returns ``{"n_train": int, "n_pos": int, "n_neg": int, "auc": float,
  "model_path": str}``.
- Empty events table → raises ``ValueError`` with a descriptive message
  (no silent phantom models).
- Imbalanced data (all-positive / all-negative) → handled gracefully
  (single-class LogisticRegression, ``auc`` may be ``None`` or 0.5).

Inference (``LearnedReranker``):
- Constructor stores ``model_path``; does NOT load joblib on construction.
- ``rerank(query, hits, *, top_n)`` lazy-loads the joblib model on first
  call, scores each hit, returns hits sorted by predicted score descending,
  truncated to ``top_n`` (or full list if ``top_n is None``).
- ``rerank(query, [], top_n=5)`` → ``[]`` WITHOUT loading the model.
- Every output hit has ``source == "reranked"``.
- All other ``Hit`` fields (chunk_id, text, document_id, …) preserved.
- Idempotent: same model + same input → same output (deterministic).
- Satisfies ``isinstance(obj, Reranker)`` protocol check at runtime.

Lazy-import discipline:
- ``import corpus_forge.retrieval.rerank.learned`` at module top must NOT
  load ``sklearn`` or ``joblib`` into ``sys.modules``.

Hypothesis property:
- For any synthetic Hit with arbitrary (query, chunk) features the
  predicted score is in ``[0.0, 1.0]``.
"""

from __future__ import annotations

import importlib
import math
import sqlite3
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from corpus_forge.retrieval.types import Hit

# ---------------------------------------------------------------------------
# Helpers shared across the module
# ---------------------------------------------------------------------------


def _hit(
    chunk_id: int,
    *,
    score: float = 0.5,
    text: str | None = None,
    source: str = "fused",
    document_id: int | None = None,
    source_uri: str | None = None,
    title: str | None = None,
    dataset_id: int = 1,
    metadata: dict[str, Any] | None = None,
) -> Hit:
    """Build a minimal Hit suitable for reranker input."""
    return Hit(
        chunk_id=chunk_id,
        score=score,
        text=text or f"chunk-text-{chunk_id}",
        document_id=document_id,
        source_uri=source_uri or f"test://{chunk_id}",
        title=title,
        dataset_id=dataset_id,
        metadata=metadata or {},
        source=source,  # type: ignore[arg-type]
    )


def _sqlite_conn_with_events(
    events: list[dict[str, Any]],
) -> sqlite3.Connection:
    """Create an in-memory SQLite DB with the search_sessions /
    search_result_events schema seeded with the provided event rows.

    Each event dict may contain:
      - ``query``       (str, default "test query")
      - ``signal``      (str, required)
      - ``value``       (float | None)
      - ``source``      (str, default "dense")
      - ``chunk_id``    (int, default 1)
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE search_sessions (
            id         INTEGER PRIMARY KEY,
            query      TEXT NOT NULL,
            dataset_id INTEGER NOT NULL DEFAULT 1,
            started_at TEXT NOT NULL DEFAULT (datetime('now')),
            client     TEXT,
            host       TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE search_result_events (
            id                   INTEGER PRIMARY KEY,
            session_id           INTEGER NOT NULL,
            chunk_id             INTEGER NOT NULL DEFAULT 1,
            signal               TEXT NOT NULL,
            value                REAL,
            source               TEXT NOT NULL DEFAULT 'dense',
            created_at           TEXT NOT NULL DEFAULT (datetime('now')),
            replacement_chunk_id INTEGER
        )
    """)
    conn.commit()

    # Insert a session per unique query; reuse sessions for same query.
    session_cache: dict[str, int] = {}
    for ev in events:
        q = ev.get("query", "test query")
        if q not in session_cache:
            cur = conn.execute(
                "INSERT INTO search_sessions (query, dataset_id) VALUES (?, 1)",
                (q,),
            )
            session_cache[q] = cur.lastrowid  # type: ignore[assignment]
        sid = session_cache[q]
        conn.execute(
            """INSERT INTO search_result_events
               (session_id, chunk_id, signal, value, source)
               VALUES (?, ?, ?, ?, ?)""",
            (
                sid,
                ev.get("chunk_id", 1),
                ev["signal"],
                ev.get("value"),
                ev.get("source", "dense"),
            ),
        )
    conn.commit()
    return conn


def _minimal_trained_model_path(tmp_path: Path) -> Path:
    """Train and persist a minimal LearnedReranker model to tmp_path.

    Uses a tiny synthetic dataset (3 pos + 3 neg) so the produced joblib
    file is a real sklearn artifact the production code can load.
    """
    import joblib
    from sklearn.linear_model import LogisticRegression

    X = [
        [0.9, 0.0, 10],
        [0.8, 1.0, 12],
        [0.7, 0.0, 8],
        [0.2, 0.0, 10],
        [0.1, 0.0, 5],
        [0.15, 1.0, 7],
    ]
    y = [1, 1, 1, 0, 0, 0]
    clf = LogisticRegression(random_state=42, max_iter=500)
    clf.fit(X, y)

    artifact = {
        "model": clf,
        "feature_spec": ["chunk_score", "lexical_score", "query_len"],
    }
    out = tmp_path / "learned_reranker.joblib"
    joblib.dump(artifact, out)
    return out


# ---------------------------------------------------------------------------
# 1. Import smoke
# ---------------------------------------------------------------------------


class TestImportSmoke:
    def test_train_reranker_importable(self) -> None:
        """``train_reranker`` is importable from the learned module."""
        from corpus_forge.retrieval.rerank.learned import train_reranker  # noqa: F401

    def test_learned_reranker_importable(self) -> None:
        """``LearnedReranker`` is importable from the learned module."""
        from corpus_forge.retrieval.rerank.learned import LearnedReranker  # noqa: F401

    def test_module_has_correct_dotted_path(self) -> None:
        """The module lives at the expected dotted path."""
        import corpus_forge.retrieval.rerank.learned as mod

        assert mod.__name__ == "corpus_forge.retrieval.rerank.learned"


# ---------------------------------------------------------------------------
# 2. Lazy-import discipline
# ---------------------------------------------------------------------------


class TestLazyImportGuard:
    """Importing learned.py must NOT pull sklearn or joblib into sys.modules."""

    def test_module_import_does_not_load_sklearn(self) -> None:
        """``import corpus_forge.retrieval.rerank.learned`` must not pull sklearn."""
        to_evict = [
            k
            for k in list(sys.modules)
            if k.startswith("sklearn") or k == "corpus_forge.retrieval.rerank.learned"
        ]
        snapshot = {k: sys.modules.pop(k) for k in to_evict}
        try:
            before = set(sys.modules.keys())
            importlib.import_module("corpus_forge.retrieval.rerank.learned")
            after = set(sys.modules.keys())
            new_mods = after - before
            sklearn_mods = [m for m in new_mods if m.startswith("sklearn")]
            assert sklearn_mods == [], (
                f"importing learned.py greedily loaded sklearn submodules: {sklearn_mods}"
            )
        finally:
            for k, v in snapshot.items():
                sys.modules.setdefault(k, v)

    def test_module_import_does_not_load_joblib(self) -> None:
        """``import corpus_forge.retrieval.rerank.learned`` must not pull joblib."""
        to_evict = [
            k
            for k in list(sys.modules)
            if k == "joblib"
            or k.startswith("joblib.")
            or k == "corpus_forge.retrieval.rerank.learned"
        ]
        snapshot = {k: sys.modules.pop(k) for k in to_evict}
        try:
            before = set(sys.modules.keys())
            importlib.import_module("corpus_forge.retrieval.rerank.learned")
            after = set(sys.modules.keys())
            new_mods = after - before
            joblib_mods = [m for m in new_mods if m == "joblib" or m.startswith("joblib.")]
            assert joblib_mods == [], (
                f"importing learned.py greedily loaded joblib modules: {joblib_mods}"
            )
        finally:
            for k, v in snapshot.items():
                sys.modules.setdefault(k, v)


# ---------------------------------------------------------------------------
# 3. train_reranker — empty events table
# ---------------------------------------------------------------------------


class TestTrainRerankerEmptyTable:
    def test_empty_events_raises_value_error(self, tmp_path: Path) -> None:
        """Empty ``search_result_events`` table must raise ``ValueError``.

        We pin this behavior so callers get a clear error message rather than
        a phantom zero-sample model.
        """
        from corpus_forge.retrieval.rerank.learned import train_reranker

        conn = _sqlite_conn_with_events([])
        out = tmp_path / "model.joblib"
        with pytest.raises(ValueError, match=r"[Nn]o (training|labeled)"):
            train_reranker(conn, out)

    def test_empty_events_does_not_write_file(self, tmp_path: Path) -> None:
        """No joblib file written when training raises."""
        import contextlib

        from corpus_forge.retrieval.rerank.learned import train_reranker

        conn = _sqlite_conn_with_events([])
        out = tmp_path / "model.joblib"
        with contextlib.suppress(ValueError):
            train_reranker(conn, out)
        assert not out.exists(), "model file must not be written on empty events"


# ---------------------------------------------------------------------------
# 4. train_reranker — imbalanced data (all positive, no negative)
# ---------------------------------------------------------------------------


class TestTrainRerankerImbalanced:
    def test_all_positive_handled_gracefully(self, tmp_path: Path) -> None:
        """5 positive, 0 negative events — must not raise; model written."""
        from corpus_forge.retrieval.rerank.learned import train_reranker

        events = [
            {"signal": "thumbs_up", "value": 0.9, "query": "query A", "chunk_id": i}
            for i in range(1, 6)
        ]
        conn = _sqlite_conn_with_events(events)
        out = tmp_path / "model.joblib"
        # Single-class sklearn LR may succeed or raise with a clear error.
        # We pin: either returns a dict with n_pos=5, n_neg=0, or raises ValueError.
        try:
            result = train_reranker(conn, out)
            assert result["n_pos"] == 5
            assert result["n_neg"] == 0
            # auc may be None or 0.5 when only one class present
            assert result["auc"] is None or isinstance(result["auc"], float)
        except ValueError:
            # Acceptable: single-class training is explicitly disallowed.
            pass

    def test_all_negative_handled_gracefully(self, tmp_path: Path) -> None:
        """5 negative, 0 positive events — same contract as all-positive."""
        from corpus_forge.retrieval.rerank.learned import train_reranker

        events = [
            {"signal": "thumbs_down", "value": 0.1, "query": "query B", "chunk_id": i}
            for i in range(1, 6)
        ]
        conn = _sqlite_conn_with_events(events)
        out = tmp_path / "model.joblib"
        try:
            result = train_reranker(conn, out)
            assert result["n_neg"] == 5
            assert result["n_pos"] == 0
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# 5. train_reranker — writes joblib file
# ---------------------------------------------------------------------------


class TestTrainRerankerWritesFile:
    def _balanced_events(self) -> list[dict[str, Any]]:
        """Minimal balanced event list (3 pos + 3 neg)."""
        return [
            {"signal": "thumbs_up", "value": 0.9, "query": "find documents", "chunk_id": 1},
            {"signal": "thumbs_up", "value": 0.8, "query": "find documents", "chunk_id": 2},
            {"signal": "thumbs_up", "value": 0.7, "query": "search notes", "chunk_id": 3},
            {"signal": "thumbs_down", "value": 0.1, "query": "find documents", "chunk_id": 4},
            {"signal": "thumbs_down", "value": 0.2, "query": "search notes", "chunk_id": 5},
            {"signal": "thumbs_down", "value": 0.15, "query": "find papers", "chunk_id": 6},
        ]

    def test_writes_joblib_file_at_out_path(self, tmp_path: Path) -> None:
        """``train_reranker`` writes a file at the given ``out_path``."""
        from corpus_forge.retrieval.rerank.learned import train_reranker

        conn = _sqlite_conn_with_events(self._balanced_events())
        out = tmp_path / "reranker.joblib"
        train_reranker(conn, out)
        assert out.exists(), f"expected joblib file at {out}"
        assert out.stat().st_size > 0, "joblib file must not be empty"

    def test_return_value_model_path_matches_out_path(self, tmp_path: Path) -> None:
        """Returned ``model_path`` matches the string representation of ``out_path``."""
        from corpus_forge.retrieval.rerank.learned import train_reranker

        conn = _sqlite_conn_with_events(self._balanced_events())
        out = tmp_path / "reranker.joblib"
        result = train_reranker(conn, out)
        assert result["model_path"] == str(out)

    def test_return_value_n_train_is_sum_pos_neg(self, tmp_path: Path) -> None:
        """``n_train == n_pos + n_neg``."""
        from corpus_forge.retrieval.rerank.learned import train_reranker

        conn = _sqlite_conn_with_events(self._balanced_events())
        out = tmp_path / "reranker.joblib"
        result = train_reranker(conn, out)
        assert result["n_train"] == result["n_pos"] + result["n_neg"]

    def test_return_value_counts_are_integers(self, tmp_path: Path) -> None:
        """``n_train``, ``n_pos``, ``n_neg`` are all plain ints."""
        from corpus_forge.retrieval.rerank.learned import train_reranker

        conn = _sqlite_conn_with_events(self._balanced_events())
        out = tmp_path / "reranker.joblib"
        result = train_reranker(conn, out)
        assert isinstance(result["n_train"], int)
        assert isinstance(result["n_pos"], int)
        assert isinstance(result["n_neg"], int)

    def test_return_value_auc_in_zero_one(self, tmp_path: Path) -> None:
        """Returned ``auc`` is a float in ``[0.0, 1.0]``."""
        from corpus_forge.retrieval.rerank.learned import train_reranker

        conn = _sqlite_conn_with_events(self._balanced_events())
        out = tmp_path / "reranker.joblib"
        result = train_reranker(conn, out)
        auc = result["auc"]
        assert isinstance(auc, float), f"auc must be float, got {type(auc)}"
        assert 0.0 <= auc <= 1.0, f"auc={auc} not in [0, 1]"

    def test_neutral_events_skipped(self, tmp_path: Path) -> None:
        """Events with ``value == 0.5`` and no explicit thumbs signal are skipped."""
        from corpus_forge.retrieval.rerank.learned import train_reranker

        events = [
            # 2 positive
            {"signal": "thumbs_up", "value": 0.9, "query": "q", "chunk_id": 1},
            {"signal": "thumbs_up", "value": 0.8, "query": "q", "chunk_id": 2},
            # 2 negative
            {"signal": "thumbs_down", "value": 0.1, "query": "q", "chunk_id": 3},
            {"signal": "thumbs_down", "value": 0.2, "query": "q", "chunk_id": 4},
            # 3 neutral — value == 0.5 with a generic signal
            {"signal": "relevance", "value": 0.5, "query": "q", "chunk_id": 5},
            {"signal": "relevance", "value": 0.5, "query": "q", "chunk_id": 6},
            {"signal": "relevance", "value": 0.5, "query": "q", "chunk_id": 7},
        ]
        conn = _sqlite_conn_with_events(events)
        out = tmp_path / "reranker.joblib"
        result = train_reranker(conn, out)
        # The 3 neutral events must NOT inflate n_train.
        assert result["n_train"] == 4, (
            f"expected 4 training rows (2 pos + 2 neg), got {result['n_train']}"
        )

    def test_source_filter_limits_training_data(self, tmp_path: Path) -> None:
        """``source_filter`` restricts which ``source`` values contribute rows."""
        from corpus_forge.retrieval.rerank.learned import train_reranker

        events = [
            # 2 rows from source "mcp" — should be included
            {"signal": "thumbs_up", "value": 0.9, "query": "q", "chunk_id": 1, "source": "mcp"},
            {"signal": "thumbs_down", "value": 0.1, "query": "q", "chunk_id": 2, "source": "mcp"},
            # 2 rows from source "cli" — should be excluded by filter
            {"signal": "thumbs_up", "value": 0.8, "query": "q", "chunk_id": 3, "source": "cli"},
            {"signal": "thumbs_down", "value": 0.2, "query": "q", "chunk_id": 4, "source": "cli"},
        ]
        conn = _sqlite_conn_with_events(events)
        out = tmp_path / "reranker.joblib"
        result = train_reranker(conn, out, source_filter=["mcp"])
        # Only 2 rows (1 pos + 1 neg from "mcp").
        assert result["n_train"] == 2, (
            f"source_filter=['mcp'] should yield 2 training rows, got {result['n_train']}"
        )


# ---------------------------------------------------------------------------
# 6. LearnedReranker — rerank behavior
# ---------------------------------------------------------------------------


class TestLearnedRerankerRerank:
    def test_empty_hits_returns_empty_list(self, tmp_path: Path) -> None:
        """``rerank(query, [], top_n=5)`` must return ``[]`` without loading the model."""
        from corpus_forge.retrieval.rerank.learned import LearnedReranker

        model_path = tmp_path / "dummy.joblib"
        # File does NOT need to exist for this test — model must not be loaded.
        reranker = LearnedReranker(model_path)

        with patch.object(LearnedReranker, "_load_model") as mock_load:
            result = reranker.rerank("any query", [], top_n=5)
            assert result == []
            assert mock_load.call_count == 0, (
                "empty-input rerank must not trigger model load; "
                f"_load_model called {mock_load.call_count}x"
            )

    def test_rerank_returns_list_of_hits(self, tmp_path: Path) -> None:
        """``rerank`` returns a ``list[Hit]``."""
        from corpus_forge.retrieval.rerank.learned import LearnedReranker

        model_path = _minimal_trained_model_path(tmp_path)
        reranker = LearnedReranker(model_path)
        hits = [_hit(i, score=0.5) for i in range(1, 4)]
        result = reranker.rerank("find documents about ML", hits)
        assert isinstance(result, list)
        assert all(isinstance(h, Hit) for h in result)

    def test_rerank_sorted_descending_by_predicted_score(self, tmp_path: Path) -> None:
        """Output hits are sorted by predicted (reranker) score descending."""
        from corpus_forge.retrieval.rerank.learned import LearnedReranker

        model_path = _minimal_trained_model_path(tmp_path)
        reranker = LearnedReranker(model_path)
        # Give hits varied input scores so we can confirm re-ordering.
        hits = [_hit(i, score=float(i) / 10) for i in range(1, 6)]
        result = reranker.rerank("find documents", hits)
        scores = [h.score for h in result]
        assert scores == sorted(scores, reverse=True), (
            f"output not sorted descending; scores={scores}"
        )

    def test_rerank_source_is_reranked(self, tmp_path: Path) -> None:
        """Every output Hit must have ``source == "reranked"``."""
        from corpus_forge.retrieval.rerank.learned import LearnedReranker

        model_path = _minimal_trained_model_path(tmp_path)
        reranker = LearnedReranker(model_path)
        hits = [_hit(i, score=0.5) for i in range(1, 4)]
        result = reranker.rerank("query", hits)
        for h in result:
            assert h.source == "reranked", (
                f"hit {h.chunk_id} source={h.source!r}, expected 'reranked'"
            )

    def test_rerank_preserves_hit_fields(self, tmp_path: Path) -> None:
        """Non-score fields (chunk_id, text, document_id, etc.) are preserved."""
        from corpus_forge.retrieval.rerank.learned import LearnedReranker

        model_path = _minimal_trained_model_path(tmp_path)
        reranker = LearnedReranker(model_path)
        meta = {"language": "en", "year": "2024"}
        in_hit = Hit(
            chunk_id=42,
            score=0.6,
            text="A document about machine learning and retrieval.",
            document_id=99,
            source_uri="vault://notes/ml.md",
            title="ML Notes",
            dataset_id=7,
            metadata=meta,
            source="fused",
        )
        result = reranker.rerank("machine learning", [in_hit])
        assert len(result) == 1
        out = result[0]
        assert out.chunk_id == 42
        assert out.text == "A document about machine learning and retrieval."
        assert out.document_id == 99
        assert out.source_uri == "vault://notes/ml.md"
        assert out.title == "ML Notes"
        assert out.dataset_id == 7
        assert out.metadata == meta

    def test_rerank_top_n_clips_output(self, tmp_path: Path) -> None:
        """``top_n=2`` returns at most 2 hits from a 5-hit input."""
        from corpus_forge.retrieval.rerank.learned import LearnedReranker

        model_path = _minimal_trained_model_path(tmp_path)
        reranker = LearnedReranker(model_path)
        hits = [_hit(i, score=float(i) / 10) for i in range(1, 6)]
        result = reranker.rerank("query", hits, top_n=2)
        assert len(result) == 2

    def test_rerank_top_n_none_returns_all(self, tmp_path: Path) -> None:
        """``top_n=None`` returns all hits (re-ordered)."""
        from corpus_forge.retrieval.rerank.learned import LearnedReranker

        model_path = _minimal_trained_model_path(tmp_path)
        reranker = LearnedReranker(model_path)
        hits = [_hit(i, score=0.5) for i in range(1, 6)]
        result = reranker.rerank("query", hits, top_n=None)
        assert len(result) == 5

    def test_rerank_idempotent(self, tmp_path: Path) -> None:
        """Same model + same input → same output on repeated calls."""
        from corpus_forge.retrieval.rerank.learned import LearnedReranker

        model_path = _minimal_trained_model_path(tmp_path)
        reranker = LearnedReranker(model_path)
        hits = [_hit(i, score=float(i) / 10) for i in range(1, 5)]
        result1 = reranker.rerank("consistent query", hits)
        result2 = reranker.rerank("consistent query", hits)
        chunk_ids1 = [h.chunk_id for h in result1]
        chunk_ids2 = [h.chunk_id for h in result2]
        assert chunk_ids1 == chunk_ids2, (
            f"idempotency failure: first={chunk_ids1}, second={chunk_ids2}"
        )
        scores1 = [h.score for h in result1]
        scores2 = [h.score for h in result2]
        for s1, s2 in zip(scores1, scores2, strict=True):
            assert math.isclose(s1, s2, rel_tol=1e-9), f"score mismatch: {s1} vs {s2}"


# ---------------------------------------------------------------------------
# 7. LearnedReranker — lazy model loading
# ---------------------------------------------------------------------------


class TestLearnedRerankerLazyLoad:
    def test_constructor_does_not_load_model(self, tmp_path: Path) -> None:
        """``LearnedReranker(path)`` must NOT call ``_load_model``."""
        from corpus_forge.retrieval.rerank.learned import LearnedReranker

        with patch.object(LearnedReranker, "_load_model") as mock_load:
            LearnedReranker(tmp_path / "nonexistent.joblib")
            assert mock_load.call_count == 0, (
                f"constructor must not call _load_model; got {mock_load.call_count} calls"
            )

    def test_first_rerank_loads_model_once(self, tmp_path: Path) -> None:
        """First ``rerank`` call triggers exactly one ``_load_model``."""
        from corpus_forge.retrieval.rerank.learned import LearnedReranker

        model_path = _minimal_trained_model_path(tmp_path)
        reranker = LearnedReranker(model_path)
        call_count = {"n": 0}
        original_load = LearnedReranker._load_model

        def counting_load(self):
            call_count["n"] += 1
            return original_load(self)

        with patch.object(LearnedReranker, "_load_model", counting_load):
            reranker.rerank("q", [_hit(1, score=0.5)])
            reranker.rerank("q", [_hit(2, score=0.5)])
            reranker.rerank("q", [_hit(3, score=0.5)])

        assert call_count["n"] == 1, f"expected exactly one _load_model call; got {call_count['n']}"


# ---------------------------------------------------------------------------
# 8. LearnedReranker — protocol conformance
# ---------------------------------------------------------------------------


class TestLearnedRerankerProtocol:
    def test_satisfies_reranker_protocol(self, tmp_path: Path) -> None:
        """``LearnedReranker`` satisfies ``isinstance(obj, Reranker)``."""
        from corpus_forge.retrieval.rerank.learned import LearnedReranker

        from corpus_forge.retrieval.rerank import Reranker

        reranker = LearnedReranker(tmp_path / "dummy.joblib")
        assert isinstance(reranker, Reranker), "LearnedReranker must satisfy the Reranker Protocol"

    def test_has_name_attribute(self, tmp_path: Path) -> None:
        """``LearnedReranker`` exposes a ``name: str`` attribute."""
        from corpus_forge.retrieval.rerank.learned import LearnedReranker

        reranker = LearnedReranker(tmp_path / "dummy.joblib")
        assert isinstance(reranker.name, str)
        assert reranker.name  # non-empty

    def test_has_model_id_attribute(self, tmp_path: Path) -> None:
        """``LearnedReranker`` exposes a ``model_id: str`` attribute."""
        from corpus_forge.retrieval.rerank.learned import LearnedReranker

        reranker = LearnedReranker(tmp_path / "dummy.joblib")
        assert isinstance(reranker.model_id, str)

    def test_has_warmup_method(self, tmp_path: Path) -> None:
        """``LearnedReranker`` exposes a ``warmup()`` method."""
        from corpus_forge.retrieval.rerank.learned import LearnedReranker

        reranker = LearnedReranker(tmp_path / "dummy.joblib")
        assert callable(reranker.warmup)


# ---------------------------------------------------------------------------
# 9. Hypothesis property — predicted scores are in [0, 1]
# ---------------------------------------------------------------------------

# Feature strategy: generate hit-like inputs (chunk_score, query_len).
_score_st = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
_query_len_st = st.integers(min_value=1, max_value=200)
_chunk_id_st = st.integers(min_value=1, max_value=10_000)


@given(
    chunk_score=_score_st,
    query_len=_query_len_st,
    chunk_id=_chunk_id_st,
)
@settings(max_examples=50, deadline=5000)
def test_property_predicted_score_in_zero_one(
    tmp_path_factory,
    chunk_score: float,
    query_len: int,
    chunk_id: int,
) -> None:
    """For any Hit input the predicted score is in [0.0, 1.0].

    Uses the trained LogisticRegression's ``predict_proba`` output which
    is inherently in [0, 1].  This property test guards against any
    clamping regression introduced during feature extraction.
    """
    from corpus_forge.retrieval.rerank.learned import LearnedReranker

    # Build a shared tmp dir for the Hypothesis runs (re-train once).
    # We use a module-scope-ish trick: cache on the module to avoid
    # re-training 50x.
    cache_attr = "_hyp_model_path"
    if not hasattr(test_property_predicted_score_in_zero_one, cache_attr):
        tmp_dir = tmp_path_factory.mktemp("hyp_model")
        mp = _minimal_trained_model_path(tmp_dir)
        setattr(test_property_predicted_score_in_zero_one, cache_attr, mp)
    model_path: Path = getattr(test_property_predicted_score_in_zero_one, cache_attr)

    query = "q" * query_len
    hit = _hit(chunk_id, score=chunk_score)
    reranker = LearnedReranker(model_path)
    result = reranker.rerank(query, [hit])
    assert len(result) == 1
    score = result[0].score
    assert isinstance(score, float), f"score must be float, got {type(score)}"
    assert 0.0 <= score <= 1.0, f"score={score} not in [0, 1]"
    assert math.isfinite(score), f"score={score} is not finite"
