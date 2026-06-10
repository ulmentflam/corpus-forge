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

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from corpus_forge.eval.embedder_ranking import (
    EmbedderCandidate,
    EmbedderPerf,
    _read_peak_gpu_mb,
    _reset_peak_gpu_mem,
    build_envelope,
    load_candidates,
    make_default_evaluator,
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


def test_load_candidates_malformed_toml_raises_valueerror(tmp_path: Path):
    """A syntactically-broken manifest surfaces as the documented
    ValueError (not a raw tomllib.TOMLDecodeError)."""
    p = tmp_path / "broken.toml"
    p.write_text("this is = = not valid toml\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid TOML"):
        load_candidates(p)


def test_load_candidates_rejects_bad_dimension(tmp_path: Path):
    p = tmp_path / "bad.toml"
    p.write_text(
        '[[candidates]]\nname = "x"\nprovider = "model2vec"\nmodel_id = "fake/x"\ndimension = 0\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_candidates(p)


def test_load_candidates_rejects_non_table_row(tmp_path: Path):
    """A `[[candidates]]` entry that isn't a table → ValueError (line 302).

    TOML can't put a scalar directly into an array-of-tables, so we build the
    manifest dict shape `load_candidates` parses (`candidates` -> list) with a
    bare string row to drive the `not isinstance(row, dict)` guard.
    """
    p = tmp_path / "scalar_row.toml"
    # `candidates = ["nope"]` parses to {"candidates": ["nope"]} — a non-empty
    # list whose single element is a str, not a table.
    p.write_text('candidates = ["nope"]\n', encoding="utf-8")
    with pytest.raises(ValueError, match="must be a table"):
        load_candidates(p)


def test_load_candidates_rejects_missing_string_field(tmp_path: Path):
    """A row missing a required string field → ValueError (line 313).

    `dimension` is valid here so the int guard passes and the failure is
    specifically the `_require_str` branch (missing/empty `model_id`).
    """
    p = tmp_path / "missing_model.toml"
    p.write_text(
        '[[candidates]]\nname = "x"\nprovider = "model2vec"\ndimension = 256\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing/empty string `model_id`"):
        load_candidates(p)


def test_load_candidates_rejects_empty_string_field(tmp_path: Path):
    """An empty-string required field also trips `_require_str` (line 313)."""
    p = tmp_path / "empty_name.toml"
    p.write_text(
        '[[candidates]]\nname = ""\nprovider = "model2vec"\nmodel_id = "fake/x"\ndimension = 256\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing/empty string `name`"):
        load_candidates(p)


_BASE_FIELDS = 'name = "x"\nprovider = "model2vec"\nmodel_id = "fake/x"\ndimension = 256\n'


def test_load_candidates_rejects_bad_normalize(tmp_path: Path):
    """`normalize` is strictly a bool — a non-bool trips the new guard."""
    p = tmp_path / "bad_normalize.toml"
    p.write_text(f"[[candidates]]\n{_BASE_FIELDS}normalize = 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="`normalize` must be a bool"):
        load_candidates(p)


def test_load_candidates_rejects_bad_distance(tmp_path: Path):
    """`distance` is strictly a string — a non-string trips the new guard."""
    p = tmp_path / "bad_distance.toml"
    p.write_text(f"[[candidates]]\n{_BASE_FIELDS}distance = 5\n", encoding="utf-8")
    with pytest.raises(ValueError, match="`distance` must be a string"):
        load_candidates(p)


def test_load_candidates_rejects_zero_batch_size(tmp_path: Path):
    """`batch_size` must be a positive int — zero trips the new guard."""
    p = tmp_path / "zero_batch.toml"
    p.write_text(f"[[candidates]]\n{_BASE_FIELDS}batch_size = 0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="`batch_size` must be a positive int"):
        load_candidates(p)


def test_load_candidates_rejects_bad_batch_size(tmp_path: Path):
    """A bool is not a valid `batch_size` (bool is a subtype of int)."""
    p = tmp_path / "bool_batch.toml"
    p.write_text(f"[[candidates]]\n{_BASE_FIELDS}batch_size = true\n", encoding="utf-8")
    with pytest.raises(ValueError, match="`batch_size` must be a positive int"):
        load_candidates(p)


def test_load_candidates_rejects_bad_device(tmp_path: Path):
    """`device` is strictly a string — a non-string trips the new guard."""
    p = tmp_path / "bad_device.toml"
    p.write_text(f"[[candidates]]\n{_BASE_FIELDS}device = 0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="`device` must be a string"):
        load_candidates(p)


# ── GPU-mem helpers: non-CUDA + import-guard early returns ────────────────────


def test_read_peak_gpu_mb_returns_none_off_cuda():
    """`_read_peak_gpu_mb` short-circuits to None on a non-CUDA device."""
    assert _read_peak_gpu_mb("cpu") is None
    assert _read_peak_gpu_mb("mps") is None


def test_reset_peak_gpu_mem_noop_off_cuda():
    """`_reset_peak_gpu_mem` is a no-op (returns None) off CUDA."""
    assert _reset_peak_gpu_mem("cpu") is None
    assert _reset_peak_gpu_mem("mps") is None


def test_read_peak_gpu_mb_returns_none_when_torch_missing(monkeypatch: pytest.MonkeyPatch):
    """When torch can't be imported, `_read_peak_gpu_mb('cuda')` returns None.

    Simulate the import failure by inserting a sentinel into ``sys.modules``
    that raises ``ImportError`` on attribute access — the lazy ``import torch``
    inside the helper then fails and the guard returns None.
    """

    def _boom(name, *args, **kwargs):
        if name == "torch":
            raise ImportError("no torch")
        return _real_import(name, *args, **kwargs)

    import builtins

    _real_import = builtins.__import__
    monkeypatch.setattr(builtins, "__import__", _boom)
    monkeypatch.delitem(sys.modules, "torch", raising=False)
    assert _read_peak_gpu_mb("cuda") is None


def test_reset_peak_gpu_mem_returns_none_when_torch_missing(monkeypatch: pytest.MonkeyPatch):
    """`_reset_peak_gpu_mem('cuda')` swallows a torch ImportError as a no-op."""
    import builtins

    _real_import = builtins.__import__

    def _boom(name, *args, **kwargs):
        if name == "torch":
            raise ImportError("no torch")
        return _real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _boom)
    monkeypatch.delitem(sys.modules, "torch", raising=False)
    assert _reset_peak_gpu_mem("cuda") is None


def test_read_peak_gpu_mb_returns_none_when_cuda_unavailable(monkeypatch: pytest.MonkeyPatch):
    """With torch present but no CUDA device, the helper returns None.

    A fake ``torch`` module whose ``cuda.is_available()`` is False drives the
    `not torch.cuda.is_available()` branch without needing a real GPU.
    """
    fake_torch = types.ModuleType("torch")
    fake_torch.cuda = types.SimpleNamespace(is_available=lambda: False)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    assert _read_peak_gpu_mb("cuda") is None


def test_read_peak_gpu_mb_reads_allocated_when_cuda_available(monkeypatch: pytest.MonkeyPatch):
    """With a fake CUDA-available torch, the helper converts bytes → MiB.

    ``max_memory_allocated`` returns 2 MiB worth of bytes; the helper must
    divide by 1024*1024 and return 2.0.  This exercises the final return
    statement without a real GPU.
    """
    fake_torch = types.ModuleType("torch")
    fake_torch.cuda = types.SimpleNamespace(
        is_available=lambda: True,
        max_memory_allocated=lambda: 2 * 1024 * 1024,
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    assert _read_peak_gpu_mb("cuda") == pytest.approx(2.0)


def test_reset_peak_gpu_mem_calls_torch_when_cuda_available(monkeypatch: pytest.MonkeyPatch):
    """`_reset_peak_gpu_mem('cuda')` calls reset_peak_memory_stats once."""
    reset = MagicMock()
    fake_torch = types.ModuleType("torch")
    fake_torch.cuda = types.SimpleNamespace(
        is_available=lambda: True,
        reset_peak_memory_stats=reset,
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    assert _reset_peak_gpu_mem("cuda") is None
    reset.assert_called_once_with()


def test_reset_peak_gpu_mem_skips_when_cuda_unavailable(monkeypatch: pytest.MonkeyPatch):
    """torch present but CUDA unavailable → reset is NOT called."""
    reset = MagicMock()
    fake_torch = types.ModuleType("torch")
    fake_torch.cuda = types.SimpleNamespace(
        is_available=lambda: False,
        reset_peak_memory_stats=reset,
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    assert _reset_peak_gpu_mem("cuda") is None
    reset.assert_not_called()


# ── make_default_evaluator: real-wiring closure, fully mocked ─────────────────


def _patch_evaluator_deps(
    monkeypatch: pytest.MonkeyPatch,
    *,
    embedder,
    fake_metrics: RetrievalMetrics,
    device: str = "cpu",
):
    """Patch the lazy imports `make_default_evaluator` reaches for.

    The factory imports `register_from_config`, `resolve_device`,
    `evaluate_retriever`, and `HybridRetriever` *inside* the function body, so
    they must be patched at their source modules (not at the embedder_ranking
    namespace).  Returns the MagicMock standing in for `HybridRetriever` so the
    caller can assert it was constructed.
    """
    monkeypatch.setattr(
        "corpus_forge.embedders.registry.register_from_config",
        lambda registry, candidate: embedder,
    )
    monkeypatch.setattr("corpus_forge._ml_device.resolve_device", lambda dev: device)
    monkeypatch.setattr(
        "corpus_forge.eval.runner.evaluate_retriever",
        lambda retriever, gold, k_values: fake_metrics,
    )
    fake_retriever_cls = MagicMock(name="HybridRetriever")
    monkeypatch.setattr("corpus_forge.retrieval.HybridRetriever", fake_retriever_cls)
    return fake_retriever_cls


def test_make_default_evaluator_runs_full_closure(monkeypatch: pytest.MonkeyPatch):
    """`make_default_evaluator(...)` returns a working `_evaluate` closure.

    Every real dependency (embedder build, device resolution, backend writes,
    retriever construction, retrieval scoring) is mocked, so this exercises the
    closure body (lines 415-443) end to end without models or a live backend.
    """
    corpus = [(1, "alpha text"), (2, "beta text")]

    embedder = MagicMock(name="embedder")
    embedder.encode.return_value = [[0.1, 0.2], [0.3, 0.4]]

    backend = MagicMock(name="backend")
    backend.register_embedder.return_value = 7  # embedder_id

    registry = MagicMock(name="registry")

    fake_metrics = RetrievalMetrics(
        ndcg={1: 0.9, 5: 0.9, 10: 0.9},
        mrr={1: 0.8, 5: 0.8, 10: 0.8},
        recall={1: 0.7, 5: 0.7, 10: 0.7},
    )

    fake_retriever_cls = _patch_evaluator_deps(
        monkeypatch, embedder=embedder, fake_metrics=fake_metrics, device="cpu"
    )

    evaluate_fn = make_default_evaluator(
        corpus, "forge_self", backend, registry, k_values=(1, 5, 10)
    )

    candidate = _candidate("cand")
    metrics, perf = evaluate_fn(candidate)

    # Retrieval metrics flow straight through from the mocked evaluate_retriever.
    assert metrics is fake_metrics
    # Perf record: device echoed, chunks_per_sec computed from 2 texts / elapsed.
    assert perf.device == "cpu"
    assert perf.peak_gpu_mb is None  # cpu → no GPU reading
    assert perf.chunks_per_sec > 0.0
    assert perf.embed_seconds >= 0.0

    # The closure registered + warmed the embedder, registered it with the
    # backend, encoded the corpus texts, wrote embeddings, and built a retriever.
    embedder.warmup.assert_called_once_with()
    backend.register_embedder.assert_called_once_with(embedder)
    embedder.encode.assert_called_once_with(
        ["alpha text", "beta text"], batch_size=candidate.batch_size
    )
    assert backend.write_embeddings.call_count == 1
    write_args = backend.write_embeddings.call_args
    assert write_args.args[0] == 7  # embedder_id threaded through
    fake_retriever_cls.assert_called_once()


def test_make_default_evaluator_zero_elapsed_guards_div_by_zero(monkeypatch: pytest.MonkeyPatch):
    """When the embed wall-clock is 0, chunks_per_sec falls back to 0.0.

    Freeze `time.perf_counter` so `elapsed == 0` and confirm the closure takes
    the `else 0.0` branch instead of dividing by zero (line 433 false arm).
    """
    corpus = [(1, "only text")]
    embedder = MagicMock(name="embedder")
    embedder.encode.return_value = [[0.5, 0.5]]
    backend = MagicMock(name="backend")
    backend.register_embedder.return_value = 1
    registry = MagicMock(name="registry")
    fake_metrics = RetrievalMetrics(ndcg={10: 1.0}, mrr={10: 1.0}, recall={10: 1.0})

    _patch_evaluator_deps(monkeypatch, embedder=embedder, fake_metrics=fake_metrics, device="cpu")
    # A constant clock makes elapsed == 0.
    monkeypatch.setattr("corpus_forge.eval.embedder_ranking.time.perf_counter", lambda: 123.0)

    evaluate_fn = make_default_evaluator(corpus, "forge_self", backend, registry)
    _metrics, perf = evaluate_fn(_candidate("c"))
    assert perf.embed_seconds == 0.0
    assert perf.chunks_per_sec == 0.0
