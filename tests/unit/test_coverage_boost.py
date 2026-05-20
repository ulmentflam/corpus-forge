"""Targeted unit tests that push coverage of the new modules above 90%.

Each test class targets a specific small set of uncovered lines reported by
``--cov-report=term-missing``. They are pure-unit (no Docker, no network) so
they always run as part of the test-unit gate.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# analyze/drift.py — compare_distributions edge cases
# ─────────────────────────────────────────────────────────────────────────────


class TestDrift:
    def test_compare_distributions_empty_inputs(self) -> None:
        """KS path skips when either side is empty; result keys still present."""
        from corpus_forge.analyze.drift import compare_distributions

        out = compare_distributions([], [{"token_count": 1}])
        assert out == {
            "ks_token_length": None,
            "js_embedding_centroid": None,
            "n_a": 0,
            "n_b": 1,
        }

    def test_compare_distributions_ks_failure_swallowed(self) -> None:
        """If ks_2samp raises, the result is None rather than propagating."""
        from corpus_forge.analyze import drift as drift_mod

        with patch.object(drift_mod, "ks_token_length", side_effect=RuntimeError("boom")):
            out = drift_mod.compare_distributions(
                [{"token_count": 1}],
                [{"token_count": 2}],
                methods=["ks"],
            )
        assert out["ks_token_length"] is None

    def test_compare_distributions_js_failure_swallowed(self) -> None:
        """If js_embedding_centroid raises, the result is None."""
        from corpus_forge.analyze import drift as drift_mod

        chunks_a = [{"embedding": [1.0, 0.0]}]
        chunks_b = [{"embedding": [0.0, 1.0]}]
        with patch.object(drift_mod, "js_embedding_centroid", side_effect=RuntimeError("boom")):
            out = drift_mod.compare_distributions(chunks_a, chunks_b, methods=["js"])
        assert out["js_embedding_centroid"] is None

    def test_get_token_count_fallback_to_text_length(self) -> None:
        from corpus_forge.analyze.drift import _get_token_count

        # token_count present → used directly.
        assert _get_token_count({"token_count": 7}) == 7
        # token_count absent → len(text)//4, min 1.
        assert _get_token_count({"text": "x" * 16}) == 4
        # empty / missing text → 0.
        assert _get_token_count({}) == 0
        assert _get_token_count({"text": ""}) == 0


# ─────────────────────────────────────────────────────────────────────────────
# analyze/topics.py — c-TF-IDF empty-vocabulary fallback
# ─────────────────────────────────────────────────────────────────────────────


class TestTopicsTopTermsEmptyVocab:
    def test_top_terms_empty_vocabulary_returns_empty_lists(self) -> None:
        """When the c-TF-IDF vectorizer can't build a vocabulary, fall back to
        a {cid: []} map instead of raising."""
        from sklearn.feature_extraction.text import TfidfVectorizer

        from corpus_forge.analyze.topics import top_terms_per_cluster

        # Patch TfidfVectorizer.fit_transform to raise the empty-vocab
        # ValueError that c-TF-IDF emits on pathological input.
        with patch.object(
            TfidfVectorizer, "fit_transform", side_effect=ValueError("empty vocabulary")
        ):
            out = top_terms_per_cluster(
                ["alpha", "beta"],
                cluster_assignments=[0, 1],
            )
        # Each cluster id maps to an empty list (the fallback path).
        assert out == {0: [], 1: []}


# ─────────────────────────────────────────────────────────────────────────────
# cag/cache.py — list_cached_keys empty / invalidate no-op / cache_key prefix
# ─────────────────────────────────────────────────────────────────────────────


class TestCagCacheEdges:
    def test_list_cached_keys_missing_dir(self, tmp_path: Path) -> None:
        from corpus_forge.cag.cache import list_cached_keys

        # Directory doesn't exist → empty list, no exception.
        out = list_cached_keys(tmp_path / "missing-root", "demo")
        assert out == []

    def test_list_cached_keys_ignores_non_json_files(self, tmp_path: Path) -> None:
        from corpus_forge.cag.cache import list_cached_keys

        target = tmp_path / "demo"
        target.mkdir()
        (target / "abc123.json").write_text("{}")
        (target / "stray.txt").write_text("not json")
        out = list_cached_keys(tmp_path, "demo")
        assert out == ["abc123"]

    def test_invalidate_no_dataset_dir_returns_zero(self, tmp_path: Path) -> None:
        from corpus_forge.cag.cache import invalidate

        assert invalidate(tmp_path, "demo", "h1") == 0

    def test_invalidate_no_match_returns_zero(self, tmp_path: Path) -> None:
        from corpus_forge.cag.cache import invalidate

        # Seed one cache file that doesn't reference 'h_missing'.
        target = tmp_path / "demo"
        target.mkdir()
        (target / "key.json").write_text(json.dumps({"content_hashes": ["h1", "h2"]}))
        assert invalidate(tmp_path, "demo", "h_missing") == 0
        assert (target / "key.json").exists()


# ─────────────────────────────────────────────────────────────────────────────
# eval/rag.py — distractor interleave when relevant_ids exhausts first
# ─────────────────────────────────────────────────────────────────────────────


class TestRagDistractorInterleave:
    def test_more_relevant_than_distractors_still_interleaves(self, tmp_path: Path) -> None:
        from corpus_forge.eval.rag import run_rag_eval

        # 3 relevant, 1 distractor → loop must drain both iterators without
        # an infinite spin and produce a 4-item ranked list.
        result = run_rag_eval(
            "ds",
            [
                {
                    "query": "q",
                    "answer": "a",
                    "relevant_chunk_ids": [1, 2, 3],
                    "distractor_chunk_ids": [99],
                    "contexts": ["c"],
                }
            ],
            judge_endpoint="mock",
            report_dir=tmp_path,
        )
        # nDCG@1 may be 0 or 1 depending on which item lands at rank 1,
        # but MRR is bounded in [0, 1].
        assert 0.0 <= result["MRR"] <= 1.0


# ─────────────────────────────────────────────────────────────────────────────
# eval/distill.py — _build_backend dispatch on the SQLite / Postgres kinds
# ─────────────────────────────────────────────────────────────────────────────


class _FakeBackendCfg:
    """Inner-class config shape that distill._build_backend reads from."""

    def __init__(self, *, kind: str, dsn: str, schema: str = "") -> None:
        self.kind = kind
        self.dsn = dsn
        self.schema = schema


class _FakeCfg:
    def __init__(self, *, kind: str, dsn: str, schema: str = "") -> None:
        self.backend = _FakeBackendCfg(kind=kind, dsn=dsn, schema=schema)


class TestDistillBuildBackend:
    def test_build_backend_sqlite_returns_sqlite_backend(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from corpus_forge.config import Config
        from corpus_forge.eval.distill import _build_backend

        monkeypatch.setattr(
            Config, "load", classmethod(lambda cls: _FakeCfg(kind="sqlite", dsn=":memory:"))
        )
        b = _build_backend()
        assert "SQLite" in type(b).__name__

    def test_build_backend_postgres_dispatches_to_postgres_factory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from corpus_forge.config import Config
        from corpus_forge.eval import distill as distill_mod

        monkeypatch.setattr(
            Config,
            "load",
            classmethod(
                lambda cls: _FakeCfg(
                    kind="postgres",
                    dsn="postgresql://user@host/db",
                    schema="corpus",
                )
            ),
        )
        with patch(
            "corpus_forge.backends.postgres.PostgresBackend",
            return_value="postgres-sentinel",
        ) as ctor:
            out = distill_mod._build_backend()
        assert out == "postgres-sentinel"
        ctor.assert_called_once()
        kwargs = ctor.call_args.kwargs
        assert kwargs["dsn"].startswith("postgresql://")
        assert kwargs["schema"] == "corpus"

    def test_get_backend_calls_build_backend(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_get_backend wraps _build_backend()."""
        from corpus_forge.eval import distill as distill_mod

        sentinel = object()
        monkeypatch.setattr(distill_mod, "_build_backend", lambda: sentinel)
        assert distill_mod._get_backend() is sentinel


# ─────────────────────────────────────────────────────────────────────────────
# cli_feedback.py — psycopg path of _get_dataset_id can't be exercised
# without psycopg, but the SQLite branch when datasets table is missing is
# already covered. Add: _save_session creates parent dir; _load_session
# round-trip with absolute path.
# ─────────────────────────────────────────────────────────────────────────────


class TestCliFeedbackPaths:
    def test_save_session_creates_parent_dir(self, tmp_path: Path) -> None:
        from corpus_forge.cli_feedback import _load_session, _save_session

        nested = tmp_path / "a" / "b" / "c"
        # Parent doesn't exist yet; _save_session must mkdir parents=True.
        _save_session(
            nested,
            {
                "session_id": "s",
                "dataset": "demo",
                "started_at": "2026-05-20T00:00:00Z",
                "queue_strategy": "default",
                "position": 0,
                "processed_chunk_ids": [],
                "pending_writes": [],
            },
        )
        loaded = _load_session(nested, "s")
        assert loaded["dataset"] == "demo"
