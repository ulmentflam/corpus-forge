"""Phase P Wave 4 — ``corpus-forge eval cag`` CLI + LLM-judge harness tests.

RED suite for P4-T2.  Every test in this file MUST fail until the Coder
ships ``corpus_forge/eval/cag.py`` and registers the ``eval cag``
subcommand under ``eval_app`` in ``corpus_forge/cli.py``.

Contracts tested (from the Phase P Wave 4 plan):
- ``corpus-forge eval cag --help`` lists known flags.
- ``--judge-endpoint=mock`` exits 0 with deterministic output.
- Mock judge output is stable across two consecutive runs (byte-identical JSON).
- Output JSON contains: ``cache_hit_count``, ``rag_count``,
  ``cache_quality_score``, ``rag_quality_score``, ``cache_vs_rag_delta``.
- ``cache_quality_score`` and ``rag_quality_score`` are floats in [0.0, 1.0].
- ``cache_vs_rag_delta`` is a float (may be negative).
- CAG selector cache-hit branch is exercised (fixture seeds a matching cache file).
- RAG miss branch is exercised (fixture includes a query with no cache file).
- Missing --queries file exits non-zero with path in output.
"""

from __future__ import annotations

import hashlib
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
# CAG cache-key derivation helper (mirrors corpus_forge.cag.selector logic)
# ---------------------------------------------------------------------------

_DEFAULT_TEMPLATE = "default"


def _derive_cache_key(query: str, dataset: str, template: str = _DEFAULT_TEMPLATE) -> str:
    """Derive the cache key the way corpus_forge.cag.selector does."""
    raw = json.dumps(
        {"dataset": dataset, "template": template, "query": query},
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Minimal fixture: queries split into cache-hit and cache-miss sets.
#
# For the cache-hit queries we pre-seed a matching JSON cache file.
# For the cache-miss queries the selector falls through to the retriever.
# ---------------------------------------------------------------------------

_DATASET = "demo"

_CACHE_HIT_QUERIES = [
    {
        "query": "What is corpus-forge?",
        "answer": "corpus-forge is a data curation and retrieval tool.",
        "relevant_chunk_ids": [1],
        "contexts": [
            "corpus-forge turns a directory tree into a searchable index.",
        ],
    },
]

_CACHE_MISS_QUERIES = [
    {
        "query": "How do I install corpus-forge on Windows?",
        "answer": "Use the Scoop bucket or the PowerShell one-liner.",
        "relevant_chunk_ids": [5],
        "contexts": [
            "Windows installation: scoop bucket add corpus-forge ...",
        ],
    },
]

_ALL_QUERIES = _CACHE_HIT_QUERIES + _CACHE_MISS_QUERIES


@pytest.fixture
def cag_cache_root(tmp_path: Path) -> Path:
    """Pre-seed a cache directory with one entry for the cache-hit query.

    Returns the cache root path (to be passed via ``--cache-root``).
    """
    cache_root = tmp_path / "cag_cache"
    dataset_dir = cache_root / _DATASET
    dataset_dir.mkdir(parents=True)

    for q_row in _CACHE_HIT_QUERIES:
        key = _derive_cache_key(q_row["query"], _DATASET)
        cache_file = dataset_dir / f"{key}.json"
        cache_payload = {
            "query": q_row["query"],
            "dataset": _DATASET,
            "template": _DEFAULT_TEMPLATE,
            "cache_key": key,
            "cached_answer": q_row["answer"],
            "contexts": q_row["contexts"],
            "built_at": "2026-05-20T00:00:00Z",
        }
        cache_file.write_text(json.dumps(cache_payload))

    return cache_root


@pytest.fixture
def cag_queries_fixture(tmp_path: Path) -> Path:
    """Write the combined query fixture (cache-hit + cache-miss) to JSONL."""
    p = tmp_path / "cag_queries.jsonl"
    with p.open("w") as fh:
        for row in _ALL_QUERIES:
            fh.write(json.dumps(row) + "\n")
    return p


@pytest.fixture
def report_dir(tmp_path: Path) -> Path:
    """Return a fresh temp directory for report output."""
    d = tmp_path / "reports"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# T1 — ``eval cag --help`` lists known flags
# ---------------------------------------------------------------------------


def test_eval_cag_help_exits_zero():
    """``corpus-forge eval cag --help`` must exit 0."""
    result = _runner().invoke(app, ["eval", "cag", "--help"])
    assert result.exit_code == 0, result.output


def test_eval_cag_help_lists_judge_endpoint_flag():
    """Help text must document ``--judge-endpoint``."""
    result = _runner().invoke(app, ["eval", "cag", "--help"])
    assert result.exit_code == 0
    assert "--judge-endpoint" in result.output


def test_eval_cag_help_lists_queries_flag():
    """Help text must document ``--queries``."""
    result = _runner().invoke(app, ["eval", "cag", "--help"])
    assert result.exit_code == 0
    assert "--queries" in result.output


def test_eval_cag_help_lists_json_flag():
    """Help text must document ``--json``."""
    result = _runner().invoke(app, ["eval", "cag", "--help"])
    assert result.exit_code == 0
    assert "--json" in result.output


# ---------------------------------------------------------------------------
# T2 — ``--judge-endpoint=mock`` exits 0 with parseable output
# ---------------------------------------------------------------------------


def test_eval_cag_mock_judge_exits_zero(
    cag_queries_fixture: Path, cag_cache_root: Path, report_dir: Path
):
    """``eval cag --judge-endpoint=mock`` must exit 0."""
    result = _runner().invoke(
        app,
        [
            "eval",
            "cag",
            "--judge-endpoint=mock",
            "--queries",
            str(cag_queries_fixture),
            "--cache-root",
            str(cag_cache_root),
            "--dataset",
            _DATASET,
            "--report-dir",
            str(report_dir),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output


def test_eval_cag_mock_judge_produces_json(
    cag_queries_fixture: Path, cag_cache_root: Path, report_dir: Path
):
    """JSON output must be parseable when ``--json`` is passed."""
    result = _runner().invoke(
        app,
        [
            "eval",
            "cag",
            "--judge-endpoint=mock",
            "--queries",
            str(cag_queries_fixture),
            "--cache-root",
            str(cag_cache_root),
            "--dataset",
            _DATASET,
            "--report-dir",
            str(report_dir),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    raw = result.output.strip()
    start = raw.find("{")
    end = raw.rfind("}") + 1
    assert start != -1 and end > start, f"No JSON object found in:\n{raw}"
    parsed = json.loads(raw[start:end])
    assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# T3 — Mock-judge determinism across two runs
# ---------------------------------------------------------------------------


def test_eval_cag_mock_judge_deterministic(
    cag_queries_fixture: Path, cag_cache_root: Path, tmp_path: Path
):
    """Two consecutive mock-judge CAG runs on the same input must produce identical JSON."""
    report1 = tmp_path / "r1"
    report1.mkdir()

    def run():
        r = _runner().invoke(
            app,
            [
                "eval",
                "cag",
                "--judge-endpoint=mock",
                "--queries",
                str(cag_queries_fixture),
                "--cache-root",
                str(cag_cache_root),
                "--dataset",
                _DATASET,
                "--report-dir",
                str(report1),
                "--json",
            ],
        )
        assert r.exit_code == 0, r.output
        return r.output

    out1 = run()
    out2 = run()

    def extract_json(raw: str) -> dict:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        return json.loads(raw[start:end])

    j1 = extract_json(out1)
    j2 = extract_json(out2)
    assert j1 == j2, f"Non-deterministic CAG output:\nRun 1: {j1}\nRun 2: {j2}"


# ---------------------------------------------------------------------------
# T4 — Output JSON contains required CAG comparison keys
# ---------------------------------------------------------------------------


def _run_cag_and_parse(cag_queries_fixture: Path, cag_cache_root: Path, report_dir: Path) -> dict:
    """Helper: run eval cag with mock judge and return parsed JSON."""
    result = _runner().invoke(
        app,
        [
            "eval",
            "cag",
            "--judge-endpoint=mock",
            "--queries",
            str(cag_queries_fixture),
            "--cache-root",
            str(cag_cache_root),
            "--dataset",
            _DATASET,
            "--report-dir",
            str(report_dir),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    raw = result.output
    start = raw.find("{")
    end = raw.rfind("}") + 1
    return json.loads(raw[start:end])


def test_eval_cag_json_contains_cache_hit_count(
    cag_queries_fixture: Path, cag_cache_root: Path, report_dir: Path
):
    """Output JSON must contain ``cache_hit_count``."""
    data = _run_cag_and_parse(cag_queries_fixture, cag_cache_root, report_dir)
    assert "cache_hit_count" in data, f"cache_hit_count not in {list(data.keys())}"


def test_eval_cag_json_contains_rag_count(
    cag_queries_fixture: Path, cag_cache_root: Path, report_dir: Path
):
    """Output JSON must contain ``rag_count``."""
    data = _run_cag_and_parse(cag_queries_fixture, cag_cache_root, report_dir)
    assert "rag_count" in data, f"rag_count not in {list(data.keys())}"


def test_eval_cag_json_contains_cache_quality_score(
    cag_queries_fixture: Path, cag_cache_root: Path, report_dir: Path
):
    """Output JSON must contain ``cache_quality_score``."""
    data = _run_cag_and_parse(cag_queries_fixture, cag_cache_root, report_dir)
    assert "cache_quality_score" in data, f"cache_quality_score not in {list(data.keys())}"


def test_eval_cag_json_contains_rag_quality_score(
    cag_queries_fixture: Path, cag_cache_root: Path, report_dir: Path
):
    """Output JSON must contain ``rag_quality_score``."""
    data = _run_cag_and_parse(cag_queries_fixture, cag_cache_root, report_dir)
    assert "rag_quality_score" in data, f"rag_quality_score not in {list(data.keys())}"


def test_eval_cag_json_contains_cache_vs_rag_delta(
    cag_queries_fixture: Path, cag_cache_root: Path, report_dir: Path
):
    """Output JSON must contain ``cache_vs_rag_delta``."""
    data = _run_cag_and_parse(cag_queries_fixture, cag_cache_root, report_dir)
    assert "cache_vs_rag_delta" in data, f"cache_vs_rag_delta not in {list(data.keys())}"


# ---------------------------------------------------------------------------
# T5 — Quality scores are numeric and delta is a float
# ---------------------------------------------------------------------------


def test_eval_cag_quality_scores_are_numeric(
    cag_queries_fixture: Path, cag_cache_root: Path, report_dir: Path
):
    """``cache_quality_score`` and ``rag_quality_score`` must be floats in [0,1]."""
    data = _run_cag_and_parse(cag_queries_fixture, cag_cache_root, report_dir)
    for key in ("cache_quality_score", "rag_quality_score"):
        val = data.get(key)
        if val is not None:
            assert isinstance(val, (int, float)), f"{key}={val!r} not numeric"
            assert 0.0 <= float(val) <= 1.0, f"{key}={val} out of [0, 1]"


def test_eval_cag_delta_is_numeric(
    cag_queries_fixture: Path, cag_cache_root: Path, report_dir: Path
):
    """``cache_vs_rag_delta`` must be a float (may be negative)."""
    data = _run_cag_and_parse(cag_queries_fixture, cag_cache_root, report_dir)
    delta = data.get("cache_vs_rag_delta")
    if delta is not None:
        assert isinstance(delta, (int, float)), f"cache_vs_rag_delta={delta!r} not numeric"


# ---------------------------------------------------------------------------
# T6 — Cache-hit count reflects the seeded fixture
# ---------------------------------------------------------------------------


def test_eval_cag_cache_hit_count_reflects_fixture(
    cag_queries_fixture: Path, cag_cache_root: Path, report_dir: Path
):
    """With one cache-hit query and one cache-miss query, ``cache_hit_count``
    must be >= 1 (the seeded entry was exercised).

    We allow >= 1 (not strictly == 1) because the retriever mock for the
    cache-miss branch may or may not be wired in the RED state.
    """
    data = _run_cag_and_parse(cag_queries_fixture, cag_cache_root, report_dir)
    hit_count = data.get("cache_hit_count", 0)
    assert hit_count >= 1, f"Expected cache_hit_count >= 1 (one seeded cache file), got {hit_count}"


# ---------------------------------------------------------------------------
# T7 — Missing queries file exits non-zero
# ---------------------------------------------------------------------------


def test_eval_cag_missing_queries_file_exits_nonzero(cag_cache_root: Path, report_dir: Path):
    """Passing a non-existent ``--queries`` file must exit non-zero."""
    result = _runner().invoke(
        app,
        [
            "eval",
            "cag",
            "--judge-endpoint=mock",
            "--queries",
            "/tmp/__nonexistent_corpus_forge_cag_fixture_99999.jsonl",
            "--cache-root",
            str(cag_cache_root),
            "--dataset",
            _DATASET,
            "--report-dir",
            str(report_dir),
            "--json",
        ],
    )
    assert result.exit_code != 0


def test_eval_cag_missing_queries_file_names_path(cag_cache_root: Path, report_dir: Path):
    """Error output for missing queries file must mention the bad path."""
    bad_path = "/tmp/__nonexistent_corpus_forge_cag_fixture_99999.jsonl"
    result = _runner().invoke(
        app,
        [
            "eval",
            "cag",
            "--judge-endpoint=mock",
            "--queries",
            bad_path,
            "--cache-root",
            str(cag_cache_root),
            "--dataset",
            _DATASET,
            "--report-dir",
            str(report_dir),
            "--json",
        ],
    )
    combined = (result.output or "") + (result.stderr if hasattr(result, "stderr") else "")
    assert bad_path in combined or "not found" in combined.lower()


# ---------------------------------------------------------------------------
# T8 — Real judge endpoint skips cleanly when Ollama is not reachable
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _ollama_reachable(),
    reason="Ollama is reachable; this test only exercises the skip path",
)
def test_eval_cag_real_endpoint_skips_when_unreachable(
    cag_queries_fixture: Path, cag_cache_root: Path, report_dir: Path
):
    """When a real judge endpoint is given but not reachable, the command must
    not crash with an unhandled traceback."""
    result = _runner().invoke(
        app,
        [
            "eval",
            "cag",
            "--judge-endpoint=http://localhost:11434",
            "--queries",
            str(cag_queries_fixture),
            "--cache-root",
            str(cag_cache_root),
            "--dataset",
            _DATASET,
            "--report-dir",
            str(report_dir),
            "--json",
        ],
    )
    if result.exception is not None:
        assert isinstance(result.exception, SystemExit), (
            f"Unhandled exception: {result.exception!r}\nOutput: {result.output}"
        )


# ---------------------------------------------------------------------------
# T9 — CF_JUDGE_ENDPOINT env var respected as alias for --judge-endpoint=mock
# ---------------------------------------------------------------------------


def test_eval_cag_env_var_judge_endpoint(
    cag_queries_fixture: Path, cag_cache_root: Path, report_dir: Path
):
    """``CF_JUDGE_ENDPOINT=mock`` in environment must be respected."""
    with patch.dict(os.environ, {"CF_JUDGE_ENDPOINT": "mock"}):
        result = _runner().invoke(
            app,
            [
                "eval",
                "cag",
                "--queries",
                str(cag_queries_fixture),
                "--cache-root",
                str(cag_cache_root),
                "--dataset",
                _DATASET,
                "--report-dir",
                str(report_dir),
                "--json",
            ],
        )
    assert result.exit_code == 0, (
        f"CF_JUDGE_ENDPOINT=mock not respected; exit={result.exit_code}\n{result.output}"
    )
