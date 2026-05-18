"""Phase L Wave 4 — ``corpus-forge estimate`` scan stats + pending files.

Validates that the upgraded estimate command renders the new "Scan stats"
table, surfaces the "Pending files" section when a backend is reachable,
and threads scan + pending data into the ``--json`` output as additive
sibling keys (no break to ``SyncEstimate`` wire shape).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from corpus_forge.cli import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def estimate_tmp_dir(tmp_path):
    """Build a tiny directory tree the walker will traverse cleanly."""

    (tmp_path / "a.md").write_text("# alpha\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("# beta\n", encoding="utf-8")
    subdir = tmp_path / "sub"
    subdir.mkdir()
    (subdir / "c.md").write_text("# gamma\n", encoding="utf-8")
    return tmp_path


def _stub_config_for_estimate():
    """Pydantic-flavoured stub avoiding ``Config.load`` filesystem reads."""

    backend = MagicMock()
    backend.kind = "sqlite"
    backend.dsn = ":memory:"
    backend.schema = "corpus"

    embedder = MagicMock()
    embedder.name = "qwen3_8b"
    embedder.dimension = 1024
    embedder.active = True

    cfg = MagicMock()
    cfg.backend = backend
    cfg.embedders = [embedder]
    cfg.datasets = []
    cfg.estimate = None
    return cfg


def test_estimate_renders_scan_stats_table(runner, estimate_tmp_dir):
    """Human output contains the new Scan stats block + an elapsed string."""

    cfg = _stub_config_for_estimate()
    with (
        patch("corpus_forge.config.Config.load", return_value=cfg),
        patch(
            "corpus_forge.cli._estimate_pending_files",
            return_value={
                "documents_not_chunked": 0,
                "chunks_missing_embedding": 0,
                "sample_paths": [],
                "embedder": None,
            },
        ),
    ):
        result = runner.invoke(app, ["estimate", str(estimate_tmp_dir)])

    assert result.exit_code == 0, result.stdout + (result.stderr or "")
    assert "Scan stats:" in result.stdout
    # Elapsed + rate substrings — Rich-degraded under NO_COLOR keeps them plain.
    assert "Elapsed" in result.stdout
    assert "files/s" in result.stdout
    assert "Files seen" in result.stdout


def test_estimate_renders_pending_section_when_backend_available(runner, estimate_tmp_dir):
    """Pending files section appears when the backend reports non-zero pending."""

    cfg = _stub_config_for_estimate()
    fake_payload = {
        "documents_not_chunked": 3,
        "chunks_missing_embedding": 17,
        "sample_paths": ["/tmp/a.md", "/tmp/b.md"],
        "embedder": "qwen3_8b",
    }
    with (
        patch("corpus_forge.config.Config.load", return_value=cfg),
        patch("corpus_forge.cli._estimate_pending_files", return_value=fake_payload),
    ):
        result = runner.invoke(app, ["estimate", str(estimate_tmp_dir)])

    assert result.exit_code == 0, result.stdout + (result.stderr or "")
    assert "Pending files:" in result.stdout
    assert "Documents not chunked" in result.stdout
    assert "Chunks missing embedding" in result.stdout
    assert "3" in result.stdout
    assert "17" in result.stdout
    assert "/tmp/a.md" in result.stdout


def test_estimate_skips_pending_section_when_zero(runner, estimate_tmp_dir):
    """Zero-pending payload silently drops the Pending section."""

    cfg = _stub_config_for_estimate()
    with (
        patch("corpus_forge.config.Config.load", return_value=cfg),
        patch(
            "corpus_forge.cli._estimate_pending_files",
            return_value={
                "documents_not_chunked": 0,
                "chunks_missing_embedding": 0,
                "sample_paths": [],
                "embedder": "qwen3_8b",
            },
        ),
    ):
        result = runner.invoke(app, ["estimate", str(estimate_tmp_dir)])

    assert result.exit_code == 0
    assert "Pending files:" not in result.stdout


def test_estimate_json_includes_scan_and_pending(runner, estimate_tmp_dir):
    """``--json`` payload carries new sibling keys without losing SyncEstimate fields."""

    cfg = _stub_config_for_estimate()
    fake_payload = {
        "documents_not_chunked": 2,
        "chunks_missing_embedding": 5,
        "sample_paths": ["/tmp/a.md"],
        "embedder": "qwen3_8b",
    }
    with (
        patch("corpus_forge.config.Config.load", return_value=cfg),
        patch("corpus_forge.cli._estimate_pending_files", return_value=fake_payload),
    ):
        result = runner.invoke(app, ["estimate", str(estimate_tmp_dir), "--json"])

    assert result.exit_code == 0, result.stdout + (result.stderr or "")
    doc = json.loads(result.stdout.strip())
    # SyncEstimate fields still present at top level.
    assert doc["schema_version"] == 1
    assert doc["file_count"] >= 3  # 3 markdown files in the tree
    assert "by_extractor" in doc
    # New Wave 4 sibling keys.
    assert "scan" in doc
    assert {"elapsed_s", "scan_rate", "file_count", "dir_count"} <= doc["scan"].keys()
    assert isinstance(doc["scan"]["elapsed_s"], (int, float))
    assert doc["scan"]["elapsed_s"] >= 0.0
    assert "pending" in doc
    assert doc["pending"] == fake_payload


def test_walk_with_stats_returns_scan_stats(estimate_tmp_dir):
    """``walk_with_stats`` exposes :class:`ScanStats` with positive timings."""

    from corpus_forge.estimate import ScanStats, walk_with_stats

    _, file_count, dir_count, _, stats = walk_with_stats(estimate_tmp_dir)
    assert isinstance(stats, ScanStats)
    assert file_count == 3
    # 1 subdir under tmp_path (the ``sub`` we created).
    assert dir_count == 1
    assert stats.file_count == 3
    assert stats.dir_count == 1
    assert stats.elapsed_s >= 0.0
    # Rate is non-negative; for trivial trees it can round to zero if the
    # walk completes faster than the perf_counter resolution.
    assert stats.scan_rate >= 0.0


def test_scan_logger_emits_bookends(estimate_tmp_dir, caplog):
    """The scan logger captures the make_progress bookend pair."""

    import logging

    from corpus_forge.estimate import walk_with_stats

    with caplog.at_level(logging.INFO, logger="corpus_forge.estimate.scan"):
        walk_with_stats(estimate_tmp_dir)

    messages = [r.message for r in caplog.records]
    assert any("Scanning started" in m for m in messages), messages
    assert any("Scanning complete" in m for m in messages), messages
