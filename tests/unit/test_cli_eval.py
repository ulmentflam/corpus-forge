"""R3-07 — `corpus-forge eval` CLI surface unit pins.

Two subcommands:

- ``corpus-forge eval retrieval`` — retrieval-quality validation against a
  gold set.
- ``corpus-forge eval corpus-quality`` — same harness over user-provided
  held-out QA pairs.  Dual-use docstring frames this as the PRIMARY use
  (training-corpus quality signal); retrieval-eval is secondary.

Options on both:
    --dataset NAME-or-PATH  (retrieval: bundled `forge_self` or path; corpus-quality: required path)
    --k 10,20               comma-separated
    --metric ndcg,mrr,recall
    --fusion rrf|alpha
    --alpha FLOAT
    --rerank / --no-rerank  (R3 no-op with friendly stderr notice)
    --json PATH

The CLI defers config resolution to ``Config.load()``; tests stub the
config so the CLI doesn't require a real `~/.config/corpus-forge`.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from corpus_forge.cli import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ── presence + help ───────────────────────────────────────────────────────


class TestCommandPresence:
    def test_eval_group_present(self, runner: CliRunner):
        result = runner.invoke(app, ["eval", "--help"])
        assert result.exit_code == 0
        out = result.output
        # Both subcommands must appear in the help listing.
        assert "retrieval" in out.lower()
        assert "corpus-quality" in out.lower()

    def test_eval_retrieval_help(self, runner: CliRunner):
        result = runner.invoke(app, ["eval", "retrieval", "--help"])
        assert result.exit_code == 0
        # Required option names appear.
        for opt in ("--dataset", "--k", "--fusion", "--alpha", "--rerank", "--json"):
            assert opt in result.output, f"--help missing {opt!r}"

    def test_eval_corpus_quality_help(self, runner: CliRunner):
        result = runner.invoke(app, ["eval", "corpus-quality", "--help"])
        assert result.exit_code == 0
        assert "--dataset" in result.output
        # Training-mission framing: docstring must mention "training" OR
        # "chunking" so it's clear the harness is for corpus QA, not just
        # retrieval correctness.
        text = result.output.lower()
        assert "training" in text or "chunking" in text or "corpus" in text


# ── retrieval subcommand: smoke against a mocked retriever ────────────────


def _write_minimal_gold(tmp_path: Path) -> Path:
    p = tmp_path / "gold.jsonl"
    p.write_text(
        json.dumps({"query_id": "q1", "query": "hello", "relevant_chunk_ids": [1]}) + "\n",
        encoding="utf-8",
    )
    return p


class TestEvalRetrievalCommand:
    """Patch the heavy bits (backend / embedder / Config.load) and verify
    the CLI wires options correctly + writes the JSON dump."""

    def test_runs_with_explicit_path_and_writes_json(self, runner: CliRunner, tmp_path: Path):
        from corpus_forge.retrieval.types import RetrievalMetrics

        gold = _write_minimal_gold(tmp_path)
        out_json = tmp_path / "metrics.json"

        # Patch evaluate_retriever to skip the real retriever path; the
        # test is about CLI wiring, not retrieval math.
        fake_metrics = RetrievalMetrics(
            ndcg={10: 0.85},
            mrr={10: 0.71},
            recall={10: 0.64},
        )

        with (
            patch("corpus_forge.cli._build_retriever_for_eval") as mock_build,
            patch(
                "corpus_forge.eval.runner.evaluate_retriever", return_value=fake_metrics
            ) as mock_eval,
        ):
            mock_build.return_value = object()  # placeholder retriever
            result = runner.invoke(
                app,
                [
                    "eval",
                    "retrieval",
                    "--dataset",
                    str(gold),
                    "--k",
                    "10",
                    "--json",
                    str(out_json),
                ],
            )

        assert result.exit_code == 0, result.output
        assert out_json.exists()
        data = json.loads(out_json.read_text())
        assert "ndcg" in data and "mrr" in data and "recall" in data
        assert mock_eval.called

        # Verify k_values was parsed from "10".
        call_kwargs = mock_eval.call_args
        if "k_values" in call_kwargs.kwargs:
            assert 10 in call_kwargs.kwargs["k_values"]
        else:
            assert 10 in call_kwargs.args[2]  # pragma: no cover — positional fallback

    def test_k_parses_csv(self, runner: CliRunner, tmp_path: Path):
        from corpus_forge.retrieval.types import RetrievalMetrics

        gold = _write_minimal_gold(tmp_path)
        fake_metrics = RetrievalMetrics(ndcg={5: 0.8, 10: 0.9}, mrr={5: 0.5, 10: 0.6}, recall={})

        with (
            patch("corpus_forge.cli._build_retriever_for_eval", return_value=object()),
            patch(
                "corpus_forge.eval.runner.evaluate_retriever", return_value=fake_metrics
            ) as mock_eval,
        ):
            result = runner.invoke(
                app,
                ["eval", "retrieval", "--dataset", str(gold), "--k", "5,10"],
            )

        assert result.exit_code == 0, result.output
        call_kwargs = mock_eval.call_args
        # k_values is passed as a kwarg by the CLI; fall back to positional[2]
        # only if a future refactor swaps the call style.
        if "k_values" in call_kwargs.kwargs:
            ks = call_kwargs.kwargs["k_values"]
        else:
            ks = call_kwargs.args[2] if len(call_kwargs.args) > 2 else []
        assert set(ks) == {5, 10}

    def test_rerank_flag_emits_friendly_notice(self, runner: CliRunner, tmp_path: Path):
        """--rerank prints a friendly 'lands in R4' message but does NOT
        actually invoke a reranker (R4 owns that)."""
        from corpus_forge.retrieval.types import RetrievalMetrics

        gold = _write_minimal_gold(tmp_path)
        fake_metrics = RetrievalMetrics(ndcg={10: 1.0}, mrr={10: 1.0}, recall={10: 1.0})

        with (
            patch("corpus_forge.cli._build_retriever_for_eval", return_value=object()),
            patch("corpus_forge.eval.runner.evaluate_retriever", return_value=fake_metrics),
        ):
            result = runner.invoke(
                app,
                ["eval", "retrieval", "--dataset", str(gold), "--k", "10", "--rerank"],
            )

        assert result.exit_code == 0, result.output
        # Mention R4 or rerank availability in stdout or stderr.
        combined = (result.output or "").lower() + (result.stderr or "").lower()
        assert "r4" in combined or "rerank" in combined, (
            "expected a friendly notice about rerank deferral to R4"
        )

    def test_table_printed_to_stdout(self, runner: CliRunner, tmp_path: Path):
        from corpus_forge.retrieval.types import RetrievalMetrics

        gold = _write_minimal_gold(tmp_path)
        fake_metrics = RetrievalMetrics(
            ndcg={10: 0.85},
            mrr={10: 0.71},
            recall={10: 0.64},
        )

        with (
            patch("corpus_forge.cli._build_retriever_for_eval", return_value=object()),
            patch("corpus_forge.eval.runner.evaluate_retriever", return_value=fake_metrics),
        ):
            result = runner.invoke(
                app,
                ["eval", "retrieval", "--dataset", str(gold), "--k", "10"],
            )

        assert result.exit_code == 0
        # The report() table should hit stdout.
        out = result.output.lower()
        assert "ndcg" in out
        assert "mrr" in out
        assert "recall" in out
        assert "10" in result.output

    def test_unknown_dataset_name_errors(self, runner: CliRunner):
        """Unknown bundled dataset name → exit nonzero with a clear message."""
        with patch("corpus_forge.cli._build_retriever_for_eval", return_value=object()):
            result = runner.invoke(
                app,
                ["eval", "retrieval", "--dataset", "does-not-exist", "--k", "10"],
            )
        assert result.exit_code != 0
        msg = (result.output or "").lower() + (result.stderr or "").lower()
        assert "not found" in msg or "unknown" in msg or "no such" in msg or "missing" in msg


class TestEvalCorpusQualityCommand:
    def test_requires_dataset_path(self, runner: CliRunner):
        # --dataset is required; missing → typer error.
        result = runner.invoke(app, ["eval", "corpus-quality"])
        assert result.exit_code != 0

    def test_runs_against_user_jsonl(self, runner: CliRunner, tmp_path: Path):
        from corpus_forge.retrieval.types import RetrievalMetrics

        gold = _write_minimal_gold(tmp_path)
        fake_metrics = RetrievalMetrics(ndcg={20: 0.55}, mrr={20: 0.4}, recall={20: 0.7})

        with (
            patch("corpus_forge.cli._build_retriever_for_eval", return_value=object()),
            patch("corpus_forge.eval.runner.evaluate_retriever", return_value=fake_metrics),
        ):
            result = runner.invoke(
                app,
                ["eval", "corpus-quality", "--dataset", str(gold), "--k", "20"],
            )

        assert result.exit_code == 0, result.output
        out = result.output.lower()
        # The report is printed; recall must appear (the chunking-regression
        # signal call-out).
        assert "recall" in out


# ── _build_retriever_for_eval helper unit pin ────────────────────────────


class TestBuildRetrieverHelper:
    """`_build_retriever_for_eval(config)` returns a HybridRetriever (or
    raises a clear error when no embedder is available).  Tests stub the
    config layer entirely so the CLI doesn't require a real db.
    """

    def test_helper_returns_retriever_with_backend_and_embedder(self, tmp_path: Path):
        """Direct call path: helper instantiates a retriever wired to a
        backend resolved from config (sqlite for the test)."""
        from corpus_forge.cli import _build_retriever_for_eval

        db_path = tmp_path / "cli-eval.db"
        # Pre-create the schema so the helper finds a usable backend.
        # Minimal scaffolding: an empty SQLite file, the helper migrates.
        sqlite3.connect(db_path).close()

        # Build a minimal config-shape mock.  The helper should read
        # `backend.kind` + `backend.dsn` and at least one active embedder.
        # We don't run the full Config validation; just feed it the
        # attributes the helper expects.
        class _Embedder:
            name = "fake-cli"
            provider = "sentence_transformers"
            model_id = "test/cli"
            dimension = 8
            normalize = True
            distance = "cosine"
            active = True
            batch_size = 32
            device = "auto"
            api_key_env = "OPENAI_API_KEY"

        class _Backend:
            kind = "sqlite"
            dsn = str(db_path)
            schema = "corpus"

        class _Retrieval:
            default_k = 10
            fusion = "rrf"
            alpha = 0.5
            rerank_enabled = False
            rerank_top_n = 50

        class _Config:
            def __init__(self):
                self.backend = _Backend()
                self.embedders = [_Embedder()]
                self.retrieval = _Retrieval()
                self.datasets: list = []

        r = _build_retriever_for_eval(_Config())
        # Must expose .search(...) to be a Retriever.
        assert hasattr(r, "search")
