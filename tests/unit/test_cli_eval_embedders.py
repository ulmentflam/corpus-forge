"""`corpus-forge eval embedders` CLI surface — body + error paths.

A real invocation needs models + a populated backend (the evaluator embeds the
corpus and scores retrieval), so every heavy seam is mocked:

- ``_build_backend_for_eval`` → a fake backend exposing ``register_embedder`` /
  ``count_chunks_missing_embedding`` / ``chunks_missing_embedding``.
- ``_load_eval_config`` → a minimal config with one active embedder.
- ``register_from_config`` → a stub probe embedder.
- ``make_default_evaluator`` → a no-op stand-in (the pure ``rank_embedders`` core
  is exercised with a stub ``evaluate_fn`` in ``test_eval_embedder_ranking.py``).

These pin the command's own glue (gold/manifest resolution, the empty-corpus
guard, the no-embedders guard, JSON output) without a GPU or a live DB.

NOTE: ``corpus_forge/cli.py`` is omitted from coverage measurement
(``[tool.coverage.run] omit`` in pyproject.toml), so these tests assert
behaviour, not coverage — they still guard the command from regressions.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from corpus_forge.cli import app
from corpus_forge.eval.embedder_ranking import EmbedderPerf
from corpus_forge.retrieval.types import RetrievalMetrics


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _write_manifest(tmp_path: Path) -> Path:
    p = tmp_path / "candidates.toml"
    p.write_text(
        "[[candidates]]\n"
        'name = "cand-a"\n'
        'provider = "model2vec"\n'
        'model_id = "fake/a"\n'
        "dimension = 256\n",
        encoding="utf-8",
    )
    return p


def _write_gold(tmp_path: Path) -> Path:
    p = tmp_path / "gold.jsonl"
    p.write_text(
        json.dumps({"query_id": "q1", "query": "hello", "relevant_chunk_ids": [1]}) + "\n",
        encoding="utf-8",
    )
    return p


class _Embedder:
    name = "probe"
    provider = "model2vec"
    model_id = "fake/probe"
    dimension = 256
    normalize = True
    distance = "cosine"
    active = True
    batch_size = 32
    device = "auto"


def _fake_config():
    return SimpleNamespace(embedders=[_Embedder()])


def _fake_backend(*, chunk_count: int):
    backend = MagicMock(name="backend")
    backend.register_embedder.return_value = 1
    backend.count_chunks_missing_embedding.return_value = chunk_count
    # PR #81 — (chunk_id, text, source_uri) 3-tuple shape.
    backend.chunks_missing_embedding.return_value = [
        (i, f"chunk {i}", "") for i in range(1, chunk_count + 1)
    ]
    return backend


def test_eval_embedders_happy_path_writes_leaderboard(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    manifest = _write_manifest(tmp_path)
    gold = _write_gold(tmp_path)
    out_json = tmp_path / "leaderboard.json"

    backend = _fake_backend(chunk_count=2)

    monkeypatch.setattr("corpus_forge.cli._load_eval_config", _fake_config)
    monkeypatch.setattr("corpus_forge.cli._build_backend_for_eval", lambda config: backend)
    # The probe embedder is built via register_from_config in the registry module.
    monkeypatch.setattr(
        "corpus_forge.embedders.registry.register_from_config",
        lambda registry, cfg: MagicMock(name="probe-embedder"),
    )
    # Stub the real-wiring evaluator factory: rank_embedders will call the
    # returned evaluate_fn per candidate.
    fake_metrics = RetrievalMetrics(
        ndcg={1: 0.9, 5: 0.9, 10: 0.9},
        mrr={1: 0.8, 5: 0.8, 10: 0.8},
        recall={1: 0.7, 5: 0.7, 10: 0.7},
    )
    fake_perf = EmbedderPerf(embed_seconds=1.0, chunks_per_sec=2.0, peak_gpu_mb=None, device="cpu")
    monkeypatch.setattr(
        "corpus_forge.eval.embedder_ranking.make_default_evaluator",
        lambda *a, **k: lambda candidate: (fake_metrics, fake_perf),
    )

    result = runner.invoke(
        app,
        [
            "eval",
            "embedders",
            "--candidates",
            str(manifest),
            "--gold",
            str(gold),
            "--k",
            "1,5,10",
            "--out",
            str(out_json),
        ],
    )

    assert result.exit_code == 0, result.output
    assert out_json.exists()
    envelope = json.loads(out_json.read_text())
    assert envelope["eval_kind"] == "embedder_ranking"
    ranking = envelope["metrics"]["ranking"]
    assert [r["name"] for r in ranking] == ["cand-a"]
    backend.count_chunks_missing_embedding.assert_called_once()


def test_eval_embedders_k_without_10_still_honors_primary_cutoff(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """`-k 1,5` omits 10, but the leaderboard ranks on ndcg@10 — the command
    must append the primary-metric cutoff so ranking stays computable."""
    manifest = _write_manifest(tmp_path)
    gold = _write_gold(tmp_path)
    out_json = tmp_path / "leaderboard.json"

    backend = _fake_backend(chunk_count=2)

    monkeypatch.setattr("corpus_forge.cli._load_eval_config", _fake_config)
    monkeypatch.setattr("corpus_forge.cli._build_backend_for_eval", lambda config: backend)
    monkeypatch.setattr(
        "corpus_forge.embedders.registry.register_from_config",
        lambda registry, cfg: MagicMock(name="probe-embedder"),
    )

    fake_metrics = RetrievalMetrics(
        ndcg={1: 0.9, 5: 0.9, 10: 0.9},
        mrr={1: 0.8, 5: 0.8, 10: 0.8},
        recall={1: 0.7, 5: 0.7, 10: 0.7},
    )
    fake_perf = EmbedderPerf(embed_seconds=1.0, chunks_per_sec=2.0, peak_gpu_mb=None, device="cpu")
    captured: dict[str, object] = {}

    def _factory(*a, **k):
        captured["k_values"] = k.get("k_values")
        return lambda candidate: (fake_metrics, fake_perf)

    monkeypatch.setattr(
        "corpus_forge.eval.embedder_ranking.make_default_evaluator",
        _factory,
    )

    result = runner.invoke(
        app,
        [
            "eval",
            "embedders",
            "--candidates",
            str(manifest),
            "--gold",
            str(gold),
            "--k",
            "1,5",
            "--out",
            str(out_json),
        ],
    )

    assert result.exit_code == 0, result.output
    # The primary-metric cutoff (10 from "ndcg@10") was appended to k_values.
    assert 10 in (captured["k_values"] or [])
    assert out_json.exists()
    envelope = json.loads(out_json.read_text())
    assert [r["name"] for r in envelope["metrics"]["ranking"]] == ["cand-a"]


def test_eval_embedders_empty_corpus_exits_2(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    manifest = _write_manifest(tmp_path)
    gold = _write_gold(tmp_path)

    backend = _fake_backend(chunk_count=0)  # no chunks → guard fires

    monkeypatch.setattr("corpus_forge.cli._load_eval_config", _fake_config)
    monkeypatch.setattr("corpus_forge.cli._build_backend_for_eval", lambda config: backend)
    monkeypatch.setattr(
        "corpus_forge.embedders.registry.register_from_config",
        lambda registry, cfg: MagicMock(name="probe-embedder"),
    )

    result = runner.invoke(
        app,
        ["eval", "embedders", "--candidates", str(manifest), "--gold", str(gold)],
    )

    assert result.exit_code == 2, result.output
    combined = (result.output or "").lower() + (result.stderr or "").lower()
    assert "no chunks" in combined


def test_eval_embedders_no_configured_embedders_errors(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    manifest = _write_manifest(tmp_path)
    gold = _write_gold(tmp_path)

    backend = _fake_backend(chunk_count=2)
    monkeypatch.setattr("corpus_forge.cli._load_eval_config", lambda: SimpleNamespace(embedders=[]))
    monkeypatch.setattr("corpus_forge.cli._build_backend_for_eval", lambda config: backend)

    result = runner.invoke(
        app,
        ["eval", "embedders", "--candidates", str(manifest), "--gold", str(gold)],
    )

    assert result.exit_code != 0
    combined = (result.output or "").lower() + (result.stderr or "").lower()
    assert "no embedders configured" in combined


def test_eval_embedders_bad_manifest_exits_2(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    gold = _write_gold(tmp_path)
    missing = tmp_path / "does-not-exist.toml"

    # Backend/config are mocked but should never be reached — the manifest
    # load fails first.
    monkeypatch.setattr("corpus_forge.cli._load_eval_config", _fake_config)
    monkeypatch.setattr(
        "corpus_forge.cli._build_backend_for_eval",
        lambda config: pytest.fail("backend built before manifest validation"),
    )

    result = runner.invoke(
        app,
        ["eval", "embedders", "--candidates", str(missing), "--gold", str(gold)],
    )
    assert result.exit_code == 2, result.output


def test_eval_embedders_unknown_gold_exits_2(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    manifest = _write_manifest(tmp_path)

    monkeypatch.setattr("corpus_forge.cli._load_eval_config", _fake_config)
    monkeypatch.setattr(
        "corpus_forge.cli._build_backend_for_eval",
        lambda config: pytest.fail("backend built before gold resolution"),
    )

    result = runner.invoke(
        app,
        [
            "eval",
            "embedders",
            "--candidates",
            str(manifest),
            "--gold",
            "no-such-bundled-gold",
        ],
    )
    assert result.exit_code == 2, result.output


# ── _build_backend_for_eval + _current_git_commit helpers ─────────────────────


def test_build_backend_for_eval_sqlite(tmp_path: Path):
    import sqlite3

    from corpus_forge.cli import _build_backend_for_eval

    db = tmp_path / "eval.db"
    sqlite3.connect(db).close()
    config = SimpleNamespace(backend=SimpleNamespace(kind="sqlite", dsn=str(db), schema="corpus"))
    backend = _build_backend_for_eval(config)
    # A real SQLiteBackend exposes register_embedder; we just confirm a usable
    # backend object came back (migrate() ran without error).
    assert hasattr(backend, "register_embedder")


def test_current_git_commit_returns_str_or_none():
    from corpus_forge.cli import _current_git_commit

    commit = _current_git_commit()
    assert commit is None or isinstance(commit, str)


def test_current_git_commit_none_when_git_missing(monkeypatch: pytest.MonkeyPatch):
    import subprocess

    from corpus_forge.cli import _current_git_commit

    def _boom(*a, **k):
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(subprocess, "run", _boom)
    assert _current_git_commit() is None
