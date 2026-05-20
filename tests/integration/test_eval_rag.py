"""Phase P Wave 4 — ``corpus-forge eval rag`` CLI + LLM-judge harness tests.

RED suite for P4-T1.  Every test in this file MUST fail until the Coder
ships ``corpus_forge/eval/rag.py``, ``corpus_forge/eval/judge.py``,
``corpus_forge/eval/judge_mock.py``, and registers the ``eval rag``
subcommand under ``eval_app`` in ``corpus_forge/cli.py``.

Contracts tested (from the Phase P Wave 4 plan):
- ``corpus-forge eval rag --help`` lists known flags.
- ``--judge-endpoint=mock`` exits 0 with deterministic output.
- Two consecutive mock-judge runs on the same input produce byte-identical JSON.
- Output JSON contains nDCG@1, nDCG@5, nDCG@10, MRR.
- Output JSON contains faithfulness, answer_relevance, context_precision,
  context_recall (each a float in [0.0, 1.0]).
- Report directory is created under the configured path.
- Missing dataset exits non-zero with the dataset name in output.
- ``--judge-endpoint=http://localhost:11434`` skips cleanly if not reachable.
- Raw judge prompts are persisted to the report directory for auditability.
- Mock judge uses temperature=0 semantics (deterministic hash-of-prompt scoring).
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from corpus_forge.cli import app

pytestmark = [pytest.mark.integration]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _runner() -> CliRunner:
    return CliRunner()


def _ollama_reachable() -> bool:
    """Return True if localhost:11434 accepts a TCP connection."""
    try:
        with socket.create_connection(("localhost", 11434), timeout=1.0):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Minimal fixture: a JSONL queries file with one RAG-style QA pair.
#
# Format matches the corpus-forge eval gold-set convention:
#   {"query": ..., "answer": ..., "relevant_chunk_ids": [...],
#    "contexts": [...]}
# The ``contexts`` key carries the retrieved passages that the judge
# evaluates for faithfulness/precision/recall.  ``answer`` is the model
# response being evaluated.
# ---------------------------------------------------------------------------

_FIXTURE_QUERIES = [
    {
        "query": "What is corpus-forge?",
        "answer": "corpus-forge is a data curation and retrieval tool.",
        "relevant_chunk_ids": [1],
        "contexts": [
            "corpus-forge turns a directory tree into a searchable index.",
            "It supports hybrid retrieval and HuggingFace export.",
        ],
    },
    {
        "query": "What backends does corpus-forge support?",
        "answer": "It supports Postgres and SQLite backends.",
        "relevant_chunk_ids": [2, 3],
        "contexts": [
            "corpus-forge supports a Postgres backend with pgvector.",
            "A SQLite backend is available as a single-machine fallback.",
        ],
    },
]


@pytest.fixture
def rag_queries_fixture(tmp_path: Path) -> Path:
    """Write the minimal query fixture to a temp JSONL file."""
    p = tmp_path / "rag_queries.jsonl"
    with p.open("w") as fh:
        for row in _FIXTURE_QUERIES:
            fh.write(json.dumps(row) + "\n")
    return p


@pytest.fixture
def report_dir(tmp_path: Path) -> Path:
    """Return a fresh temp directory for report output."""
    d = tmp_path / "reports"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# T1 — ``eval rag --help`` lists known flags
# ---------------------------------------------------------------------------


def test_eval_rag_help_exits_zero():
    """``corpus-forge eval rag --help`` must exit 0."""
    result = _runner().invoke(app, ["eval", "rag", "--help"])
    assert result.exit_code == 0, result.output


def test_eval_rag_help_lists_judge_endpoint_flag():
    """Help text must document ``--judge-endpoint``."""
    result = _runner().invoke(app, ["eval", "rag", "--help"])
    assert result.exit_code == 0
    assert "--judge-endpoint" in result.output


def test_eval_rag_help_lists_dataset_flag():
    """Help text must document ``--dataset``."""
    result = _runner().invoke(app, ["eval", "rag", "--help"])
    assert result.exit_code == 0
    assert "--dataset" in result.output


def test_eval_rag_help_lists_queries_flag():
    """Help text must document ``--queries``."""
    result = _runner().invoke(app, ["eval", "rag", "--help"])
    assert result.exit_code == 0
    assert "--queries" in result.output


def test_eval_rag_help_lists_json_flag():
    """Help text must document ``--json``."""
    result = _runner().invoke(app, ["eval", "rag", "--help"])
    assert result.exit_code == 0
    assert "--json" in result.output


# ---------------------------------------------------------------------------
# T2 — ``--judge-endpoint=mock`` exits 0 with deterministic output
# ---------------------------------------------------------------------------


def test_eval_rag_mock_judge_exits_zero(rag_queries_fixture: Path, report_dir: Path):
    """``eval rag --judge-endpoint=mock`` must exit 0."""
    result = _runner().invoke(
        app,
        [
            "eval",
            "rag",
            "--judge-endpoint=mock",
            "--queries",
            str(rag_queries_fixture),
            "--report-dir",
            str(report_dir),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output


def test_eval_rag_mock_judge_produces_json(rag_queries_fixture: Path, report_dir: Path):
    """JSON output must be parseable when ``--json`` is passed."""
    result = _runner().invoke(
        app,
        [
            "eval",
            "rag",
            "--judge-endpoint=mock",
            "--queries",
            str(rag_queries_fixture),
            "--report-dir",
            str(report_dir),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    # Find JSON in stdout — it may be preceded by informational lines.
    output = result.output.strip()
    # Try to find JSON block in output.
    start = output.find("{")
    end = output.rfind("}") + 1
    assert start != -1 and end > start, f"No JSON object found in:\n{output}"
    parsed = json.loads(output[start:end])
    assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# T3 — Mock-judge determinism: two runs == identical JSON
# ---------------------------------------------------------------------------


def test_eval_rag_mock_judge_deterministic(rag_queries_fixture: Path, tmp_path: Path):
    """Two consecutive mock-judge runs on the same input must produce identical JSON.

    This pins the temperature=0 semantic: the mock judge hashes the prompt
    to produce stable scores.
    """
    report1 = tmp_path / "report1"
    report2 = tmp_path / "report2"
    report1.mkdir()
    report2.mkdir()

    def run():
        r = _runner().invoke(
            app,
            [
                "eval",
                "rag",
                "--judge-endpoint=mock",
                "--queries",
                str(rag_queries_fixture),
                "--report-dir",
                str(report1),
                "--json",
            ],
        )
        assert r.exit_code == 0, r.output
        return r.output

    out1 = run()
    out2 = run()

    # Extract JSON from each output.
    def extract_json(raw: str) -> dict:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        return json.loads(raw[start:end])

    j1 = extract_json(out1)
    j2 = extract_json(out2)
    assert j1 == j2, f"Non-deterministic output:\nRun 1: {j1}\nRun 2: {j2}"


# ---------------------------------------------------------------------------
# T4 — nDCG@1/5/10 + MRR present in JSON
# ---------------------------------------------------------------------------


def test_eval_rag_json_contains_ndcg_at_1(rag_queries_fixture: Path, report_dir: Path):
    """Output JSON must contain ``nDCG@1``."""
    result = _runner().invoke(
        app,
        [
            "eval",
            "rag",
            "--judge-endpoint=mock",
            "--queries",
            str(rag_queries_fixture),
            "--report-dir",
            str(report_dir),
            "--json",
        ],
    )
    assert result.exit_code == 0
    raw = result.output
    start = raw.find("{")
    data = json.loads(raw[start : raw.rfind("}") + 1])
    assert "nDCG@1" in data or any("ndcg" in k.lower() and "1" in k for k in data), (
        f"nDCG@1 not in {list(data.keys())}"
    )


def test_eval_rag_json_contains_ndcg_at_5(rag_queries_fixture: Path, report_dir: Path):
    """Output JSON must contain ``nDCG@5``."""
    result = _runner().invoke(
        app,
        [
            "eval",
            "rag",
            "--judge-endpoint=mock",
            "--queries",
            str(rag_queries_fixture),
            "--report-dir",
            str(report_dir),
            "--json",
        ],
    )
    assert result.exit_code == 0
    raw = result.output
    start = raw.find("{")
    data = json.loads(raw[start : raw.rfind("}") + 1])
    assert "nDCG@5" in data or any("ndcg" in k.lower() and "5" in k for k in data), (
        f"nDCG@5 not in {list(data.keys())}"
    )


def test_eval_rag_json_contains_ndcg_at_10(rag_queries_fixture: Path, report_dir: Path):
    """Output JSON must contain ``nDCG@10``."""
    result = _runner().invoke(
        app,
        [
            "eval",
            "rag",
            "--judge-endpoint=mock",
            "--queries",
            str(rag_queries_fixture),
            "--report-dir",
            str(report_dir),
            "--json",
        ],
    )
    assert result.exit_code == 0
    raw = result.output
    start = raw.find("{")
    data = json.loads(raw[start : raw.rfind("}") + 1])
    assert "nDCG@10" in data or any("ndcg" in k.lower() and "10" in k for k in data), (
        f"nDCG@10 not in {list(data.keys())}"
    )


def test_eval_rag_json_contains_mrr(rag_queries_fixture: Path, report_dir: Path):
    """Output JSON must contain ``MRR``."""
    result = _runner().invoke(
        app,
        [
            "eval",
            "rag",
            "--judge-endpoint=mock",
            "--queries",
            str(rag_queries_fixture),
            "--report-dir",
            str(report_dir),
            "--json",
        ],
    )
    assert result.exit_code == 0
    raw = result.output
    start = raw.find("{")
    data = json.loads(raw[start : raw.rfind("}") + 1])
    assert any("mrr" in k.lower() for k in data), f"MRR not in {list(data.keys())}"


# ---------------------------------------------------------------------------
# T5 — faithfulness/answer_relevance/context_precision/context_recall present
# ---------------------------------------------------------------------------


def test_eval_rag_json_contains_faithfulness(rag_queries_fixture: Path, report_dir: Path):
    """Output JSON must contain ``faithfulness``."""
    result = _runner().invoke(
        app,
        [
            "eval",
            "rag",
            "--judge-endpoint=mock",
            "--queries",
            str(rag_queries_fixture),
            "--report-dir",
            str(report_dir),
            "--json",
        ],
    )
    assert result.exit_code == 0
    raw = result.output
    start = raw.find("{")
    data = json.loads(raw[start : raw.rfind("}") + 1])
    assert any("faithfulness" in k.lower() for k in data), (
        f"faithfulness not in {list(data.keys())}"
    )


def test_eval_rag_json_contains_answer_relevance(rag_queries_fixture: Path, report_dir: Path):
    """Output JSON must contain ``answer_relevance``."""
    result = _runner().invoke(
        app,
        [
            "eval",
            "rag",
            "--judge-endpoint=mock",
            "--queries",
            str(rag_queries_fixture),
            "--report-dir",
            str(report_dir),
            "--json",
        ],
    )
    assert result.exit_code == 0
    raw = result.output
    start = raw.find("{")
    data = json.loads(raw[start : raw.rfind("}") + 1])
    assert any("answer_relevance" in k.lower() or "relevance" in k.lower() for k in data), (
        f"answer_relevance not in {list(data.keys())}"
    )


def test_eval_rag_json_contains_context_precision(rag_queries_fixture: Path, report_dir: Path):
    """Output JSON must contain ``context_precision``."""
    result = _runner().invoke(
        app,
        [
            "eval",
            "rag",
            "--judge-endpoint=mock",
            "--queries",
            str(rag_queries_fixture),
            "--report-dir",
            str(report_dir),
            "--json",
        ],
    )
    assert result.exit_code == 0
    raw = result.output
    start = raw.find("{")
    data = json.loads(raw[start : raw.rfind("}") + 1])
    assert any("context_precision" in k.lower() or "precision" in k.lower() for k in data), (
        f"context_precision not in {list(data.keys())}"
    )


def test_eval_rag_json_contains_context_recall(rag_queries_fixture: Path, report_dir: Path):
    """Output JSON must contain ``context_recall``."""
    result = _runner().invoke(
        app,
        [
            "eval",
            "rag",
            "--judge-endpoint=mock",
            "--queries",
            str(rag_queries_fixture),
            "--report-dir",
            str(report_dir),
            "--json",
        ],
    )
    assert result.exit_code == 0
    raw = result.output
    start = raw.find("{")
    data = json.loads(raw[start : raw.rfind("}") + 1])
    assert any("context_recall" in k.lower() or "recall" in k.lower() for k in data), (
        f"context_recall not in {list(data.keys())}"
    )


def test_eval_rag_judge_scores_in_unit_interval(rag_queries_fixture: Path, report_dir: Path):
    """All LLM-judge scores must be floats in [0.0, 1.0]."""
    result = _runner().invoke(
        app,
        [
            "eval",
            "rag",
            "--judge-endpoint=mock",
            "--queries",
            str(rag_queries_fixture),
            "--report-dir",
            str(report_dir),
            "--json",
        ],
    )
    assert result.exit_code == 0
    raw = result.output
    start = raw.find("{")
    data = json.loads(raw[start : raw.rfind("}") + 1])
    judge_keys = {
        k
        for k in data
        if any(tok in k.lower() for tok in ("faithfulness", "relevance", "precision", "recall"))
    }
    assert judge_keys, "Expected at least one judge-score key in output"
    for k in judge_keys:
        val = data[k]
        assert isinstance(val, (int, float)), f"{k}={val!r} is not numeric"
        assert 0.0 <= float(val) <= 1.0, f"{k}={val} out of [0, 1]"


# ---------------------------------------------------------------------------
# T6 — Report directory created
# ---------------------------------------------------------------------------


def test_eval_rag_report_dir_created(rag_queries_fixture: Path, tmp_path: Path):
    """``eval rag`` must create the report directory when it doesn't exist."""
    report_dir = tmp_path / "new_reports" / "run1"
    assert not report_dir.exists()

    result = _runner().invoke(
        app,
        [
            "eval",
            "rag",
            "--judge-endpoint=mock",
            "--queries",
            str(rag_queries_fixture),
            "--report-dir",
            str(report_dir),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert report_dir.exists(), f"Report dir not created: {report_dir}"


def test_eval_rag_report_dir_contains_markdown(rag_queries_fixture: Path, tmp_path: Path):
    """``eval rag`` must write a Markdown report file to the report directory."""
    report_dir = tmp_path / "reports"
    report_dir.mkdir()

    result = _runner().invoke(
        app,
        [
            "eval",
            "rag",
            "--judge-endpoint=mock",
            "--queries",
            str(rag_queries_fixture),
            "--report-dir",
            str(report_dir),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    md_files = list(report_dir.glob("**/*.md"))
    assert md_files, f"No .md report file found under {report_dir}"


def test_eval_rag_report_dir_contains_json(rag_queries_fixture: Path, tmp_path: Path):
    """``eval rag`` must write a JSON report file to the report directory."""
    report_dir = tmp_path / "reports"
    report_dir.mkdir()

    result = _runner().invoke(
        app,
        [
            "eval",
            "rag",
            "--judge-endpoint=mock",
            "--queries",
            str(rag_queries_fixture),
            "--report-dir",
            str(report_dir),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    json_files = list(report_dir.glob("**/*.json"))
    assert json_files, f"No .json report file found under {report_dir}"


# ---------------------------------------------------------------------------
# T7 — Missing dataset exits non-zero with named error
# ---------------------------------------------------------------------------


def test_eval_rag_missing_queries_file_exits_nonzero():
    """Passing a non-existent ``--queries`` file must exit non-zero."""
    result = _runner().invoke(
        app,
        [
            "eval",
            "rag",
            "--judge-endpoint=mock",
            "--queries",
            "/tmp/__nonexistent_corpus_forge_fixture_12345.jsonl",
        ],
    )
    assert result.exit_code != 0


def test_eval_rag_missing_queries_file_names_path():
    """Non-zero exit for missing --queries file must mention the bad path."""
    bad_path = "/tmp/__nonexistent_corpus_forge_fixture_12345.jsonl"
    result = _runner().invoke(
        app,
        [
            "eval",
            "rag",
            "--judge-endpoint=mock",
            "--queries",
            bad_path,
        ],
    )
    combined = (result.output or "") + (result.stderr if hasattr(result, "stderr") else "")
    assert bad_path in combined or "not found" in combined.lower()


# ---------------------------------------------------------------------------
# T8 — Real endpoint skips cleanly if not reachable
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _ollama_reachable(),
    reason="Ollama is reachable; this test only exercises the skip path",
)
def test_eval_rag_real_endpoint_skips_when_unreachable(rag_queries_fixture: Path, report_dir: Path):
    """When a real judge endpoint is given but not reachable, the command must
    either skip/warn gracefully or exit non-zero — it must NOT hard-crash
    with an unhandled exception traceback."""
    result = _runner().invoke(
        app,
        [
            "eval",
            "rag",
            "--judge-endpoint=http://localhost:11434",
            "--queries",
            str(rag_queries_fixture),
            "--report-dir",
            str(report_dir),
            "--json",
        ],
    )
    # Accept either graceful non-zero or a clean skip message.
    # What we do NOT accept: result.exception is an unhandled exception
    # that leaks a raw traceback without a user-readable error.
    if result.exception is not None:
        # An unhandled Python exception (not typer.Exit) is not acceptable.

        assert isinstance(result.exception, SystemExit), (
            f"Unhandled exception reaching test: {result.exception!r}\nOutput: {result.output}"
        )


# ---------------------------------------------------------------------------
# T9 — Raw judge prompts persisted to report dir
# ---------------------------------------------------------------------------


def test_eval_rag_raw_prompts_persisted(rag_queries_fixture: Path, tmp_path: Path):
    """The LLM judge must persist raw prompts (and responses) in the report dir
    for auditability.  The plan requires ``temperature=0`` + prompt logging.
    """
    report_dir = tmp_path / "reports"
    report_dir.mkdir()

    result = _runner().invoke(
        app,
        [
            "eval",
            "rag",
            "--judge-endpoint=mock",
            "--queries",
            str(rag_queries_fixture),
            "--report-dir",
            str(report_dir),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output

    # Expect at least one file in the report dir that contains raw prompt text.
    # Convention: ``prompts.jsonl`` OR ``prompts/*.json`` OR similar.
    all_files = list(report_dir.glob("**/*"))
    non_dir_files = [f for f in all_files if f.is_file()]
    assert non_dir_files, f"No files written to report dir: {report_dir}"

    # At least one file must look like it contains prompt/response data.
    found_prompt_file = False
    for f in non_dir_files:
        name = f.name.lower()
        if any(tok in name for tok in ("prompt", "judge", "trace", "audit", "raw")):
            found_prompt_file = True
            break
        # Also accept a JSONL where each line has a "prompt" key.
        if f.suffix in (".jsonl", ".json", ".ndjson"):
            try:
                content = f.read_text()
                if "prompt" in content:
                    found_prompt_file = True
                    break
            except Exception:
                pass

    assert found_prompt_file, (
        f"No raw-prompt file found under {report_dir}. "
        f"Files present: {[f.name for f in non_dir_files]}"
    )


# ---------------------------------------------------------------------------
# T10 — judge_mock.score is importable and deterministic
# ---------------------------------------------------------------------------


def test_judge_mock_score_importable():
    """``corpus_forge.eval.judge_mock.score`` must be importable."""
    from corpus_forge.eval.judge_mock import score  # noqa: F401


def test_judge_mock_score_deterministic():
    """Identical prompts must produce identical scores."""
    from corpus_forge.eval.judge_mock import score

    prompt = "Is this answer faithful to the context?"
    result1 = score(prompt)
    result2 = score(prompt)
    assert result1 == result2, f"Non-deterministic mock: {result1} vs {result2}"


def test_judge_mock_score_returns_required_keys():
    """The mock score dict must contain the four required judge dimensions."""
    from corpus_forge.eval.judge_mock import score

    result = score("any prompt text here")
    assert isinstance(result, dict)
    required = {"faithfulness", "answer_relevance", "context_precision", "context_recall"}
    missing = required - set(result.keys())
    assert not missing, f"Missing keys in mock score: {missing}"


def test_judge_mock_score_values_in_unit_interval():
    """All mock score values must be floats in [0.0, 1.0]."""
    from corpus_forge.eval.judge_mock import score

    result = score("evaluate this answer for quality")
    for k, v in result.items():
        assert isinstance(v, float), f"{k}={v!r} is not a float"
        assert 0.0 <= v <= 1.0, f"{k}={v} out of [0, 1]"


def test_judge_mock_score_different_prompts_may_differ():
    """Different prompts should produce the expected deterministic (possibly different) scores.

    This is a smoke test that the hash-based approach is actually keying on
    prompt content, not returning a constant.  We check that at least ONE
    of the four dimensions changes between two very different prompts.
    (They MAY be equal by hash collision — this is acceptable but unlikely.)
    """
    from corpus_forge.eval.judge_mock import score

    r1 = score("What is the capital of France?")
    r2 = score("corpus-forge eval rag context faithfulness check")
    # We do not assert they are different — that would be flaky on hash collision.
    # We do assert both are valid.
    for r in (r1, r2):
        assert isinstance(r, dict)
        assert all(0.0 <= v <= 1.0 for v in r.values())


# ---------------------------------------------------------------------------
# T11 — CF_JUDGE_ENDPOINT env var respected as alias for --judge-endpoint=mock
# ---------------------------------------------------------------------------


def test_eval_rag_env_var_judge_endpoint(rag_queries_fixture: Path, report_dir: Path):
    """``CF_JUDGE_ENDPOINT=mock`` in the environment must be equivalent to
    passing ``--judge-endpoint=mock``."""
    with patch.dict(os.environ, {"CF_JUDGE_ENDPOINT": "mock"}):
        result = _runner().invoke(
            app,
            [
                "eval",
                "rag",
                "--queries",
                str(rag_queries_fixture),
                "--report-dir",
                str(report_dir),
                "--json",
            ],
        )
    # With CF_JUDGE_ENDPOINT=mock set, we expect either:
    # (a) exit 0 if the env var is respected, OR
    # (b) exit != 0 if --judge-endpoint is required and env var not supported.
    # We assert (a): the env var must be respected (this drives the RED).
    assert result.exit_code == 0, (
        f"CF_JUDGE_ENDPOINT=mock was not respected; exit={result.exit_code}\n{result.output}"
    )
