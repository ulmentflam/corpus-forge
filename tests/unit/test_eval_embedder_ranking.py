"""Unit pins for the embedder-ranking eval harness.

Surface under test: ``corpus_forge.eval.embedder_ranking``.

These tests exercise the **pure, injectable core** only — no model
download, no DB.  ``rank_embedders`` is driven with a stub ``evaluate_fn``
that returns canned ``(RetrievalMetrics, EmbedderPerf)`` per candidate, so
the ranking + envelope logic is verified without the real-wiring evaluator
(``make_default_evaluator``), which needs models + a live backend and runs
in a separate on-machine task.

Covered:
- ranking is sorted by the primary metric, descending;
- the envelope has the right top-level keys, ``eval_kind ==
  "embedder_ranking"``, and one ranking entry per candidate;
- ``load_candidates`` parses the bundled sample manifest into the expected
  count + fields.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from corpus_forge.eval.embedder_ranking import (
    EmbedderCandidate,
    EmbedderPerf,
    build_envelope,
    load_candidates,
    rank_embedders,
)
from corpus_forge.retrieval.types import RetrievalMetrics

pytestmark = pytest.mark.unit

_MANIFEST = Path(__file__).resolve().parents[1] / "fixtures" / "eval" / "embedder_candidates.toml"


# ── stub evaluate_fn ──────────────────────────────────────────────────────────


def _make_stub_evaluate_fn(scores: dict[str, float]):
    """Return an ``evaluate_fn`` mapping candidate name → canned ndcg@10.

    Every metric bucket (ndcg / mrr / recall) at every k is set to the
    candidate's score so the flattened ``ndcg@10`` key (the default primary
    metric) is well-defined and the ranking is unambiguous.
    """

    def _evaluate(candidate: EmbedderCandidate) -> tuple[RetrievalMetrics, EmbedderPerf]:
        score = scores[candidate.name]
        metrics = RetrievalMetrics(
            ndcg={1: score, 5: score, 10: score},
            mrr={1: score, 5: score, 10: score},
            recall={1: score, 5: score, 10: score},
        )
        perf = EmbedderPerf(
            embed_seconds=1.0,
            chunks_per_sec=100.0,
            peak_gpu_mb=None,
            device="cpu",
        )
        return metrics, perf

    return _evaluate


def _candidate(name: str, dimension: int = 256) -> EmbedderCandidate:
    return EmbedderCandidate(
        name=name,
        provider="model2vec",
        model_id=f"fake/{name}",
        dimension=dimension,
    )


# ── rank_embedders: ordering ──────────────────────────────────────────────────


def test_ranking_sorted_by_primary_metric_desc():
    candidates = [_candidate("low"), _candidate("high"), _candidate("mid")]
    evaluate_fn = _make_stub_evaluate_fn({"low": 0.10, "high": 0.90, "mid": 0.50})

    envelope = rank_embedders(candidates, evaluate_fn=evaluate_fn)

    ranking = envelope["metrics"]["ranking"]
    names = [entry["name"] for entry in ranking]
    assert names == ["high", "mid", "low"]

    # The recorded primary-metric values are themselves descending.
    primary = envelope["metrics"]["primary_metric"]
    values = [entry["metrics"][primary] for entry in ranking]
    assert values == sorted(values, reverse=True)


def test_custom_primary_metric_drives_sort():
    # Make ndcg@10 agree across all (so it can't be the tiebreak) and let
    # the chosen primary (recall@5) decide the order instead.
    def _evaluate(candidate: EmbedderCandidate) -> tuple[RetrievalMetrics, EmbedderPerf]:
        recall5 = {"a": 0.2, "b": 0.8}[candidate.name]
        metrics = RetrievalMetrics(
            ndcg={10: 0.5},
            mrr={5: 0.5},
            recall={5: recall5},
        )
        perf = EmbedderPerf(embed_seconds=1.0, chunks_per_sec=1.0, peak_gpu_mb=None, device="cpu")
        return metrics, perf

    envelope = rank_embedders(
        [_candidate("a"), _candidate("b")],
        evaluate_fn=_evaluate,
        primary_metric="recall@5",
    )
    names = [entry["name"] for entry in envelope["metrics"]["ranking"]]
    assert names == ["b", "a"]


def test_empty_candidates_raises():
    with pytest.raises(ValueError):
        rank_embedders([], evaluate_fn=_make_stub_evaluate_fn({}))


# ── rank_embedders: envelope shape ────────────────────────────────────────────


def test_envelope_top_level_shape():
    candidates = [_candidate("alpha"), _candidate("beta")]
    evaluate_fn = _make_stub_evaluate_fn({"alpha": 0.7, "beta": 0.3})

    envelope = rank_embedders(
        candidates,
        evaluate_fn=evaluate_fn,
        dataset="gold-toy",
        git_commit="deadbeef",
        config={"k_values": [1, 5, 10]},
    )

    assert set(envelope.keys()) == {"eval_kind", "dataset", "git_commit", "ts", "metrics", "config"}
    assert envelope["eval_kind"] == "embedder_ranking"
    assert envelope["dataset"] == "gold-toy"
    assert envelope["git_commit"] == "deadbeef"
    assert isinstance(envelope["ts"], str) and envelope["ts"]
    assert envelope["config"] == {"k_values": [1, 5, 10]}

    metrics_block = envelope["metrics"]
    assert set(metrics_block.keys()) == {"ranking", "primary_metric"}
    assert metrics_block["primary_metric"] == "ndcg@10"

    # One ranking entry per candidate.
    ranking = metrics_block["ranking"]
    assert len(ranking) == len(candidates)
    for entry in ranking:
        assert set(entry.keys()) == {"name", "recall", "mrr", "ndcg", "metrics", "perf"}
        assert "ndcg@10" in entry["metrics"]
        assert set(entry["perf"].keys()) == {
            "embed_seconds",
            "chunks_per_sec",
            "peak_gpu_mb",
            "device",
        }


def test_git_commit_defaults_to_none():
    envelope = rank_embedders(
        [_candidate("solo")], evaluate_fn=_make_stub_evaluate_fn({"solo": 1.0})
    )
    assert envelope["git_commit"] is None


def test_build_envelope_honours_ts_override():
    envelope = build_envelope(
        [],
        dataset="d",
        git_commit=None,
        primary_metric="ndcg@10",
        ts="2026-01-01T00:00:00+00:00",
    )
    assert envelope["ts"] == "2026-01-01T00:00:00+00:00"
    assert envelope["metrics"]["ranking"] == []


# ── load_candidates: manifest parsing ─────────────────────────────────────────


def test_load_candidates_parses_sample_manifest():
    candidates = load_candidates(_MANIFEST)
    assert len(candidates) == 3

    by_name = {c.name: c for c in candidates}
    assert set(by_name) == {"qwen3-0.6b", "bge-large-en", "potion-code-16m"}

    qwen = by_name["qwen3-0.6b"]
    assert qwen.provider == "sentence_transformers"
    assert qwen.model_id == "Qwen/Qwen3-Embedding-0.6B"
    assert qwen.dimension == 1024

    potion = by_name["potion-code-16m"]
    assert potion.provider == "model2vec"
    assert potion.dimension == 256

    # Defaults applied for keys absent in the manifest.
    assert qwen.normalize is True
    assert qwen.distance == "cosine"
    assert qwen.batch_size == 32
    assert qwen.device == "auto"


def test_load_candidates_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_candidates(tmp_path / "nope.toml")


def test_load_candidates_empty_array_raises(tmp_path: Path):
    p = tmp_path / "empty.toml"
    p.write_text("# no candidates here\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_candidates(p)


def test_load_candidates_rejects_bad_dimension(tmp_path: Path):
    p = tmp_path / "bad.toml"
    p.write_text(
        '[[candidates]]\nname = "x"\nprovider = "model2vec"\nmodel_id = "fake/x"\ndimension = 0\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_candidates(p)
