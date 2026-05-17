"""Unit tests for `corpus-forge estimate` CLI.

Phase J / J1.

Pure pure-function plumbing tests — we exercise the flag surface
(human vs JSON output, --compression-ratio override, --embedder filter,
error paths) without touching a real backend. ``Config.load()`` is
driven via a temp TOML file + ``CORPUS_FORGE_CONFIG`` env var.
"""

from __future__ import annotations

import json as _json
import textwrap
from pathlib import Path

from typer.testing import CliRunner

from corpus_forge.cli import app


def _build_test_config(tmp_path: Path, *, compression_ratio: float | None = None) -> Path:
    """Write a minimal SQLite-backed config with one active embedder."""
    db_path = tmp_path / "corpus.db"
    estimate_block = ""
    if compression_ratio is not None:
        estimate_block = f"\n[estimate]\ncompression_ratio = {compression_ratio}\n"
    cfg = (
        textwrap.dedent(
            f"""
        [backend]
        kind = "sqlite"
        dsn  = "{db_path.as_posix()}"

        [daemon]

        [[datasets]]
        name = "demo"
        kind = "text"
        sources = [{{plugin = "filesystem", root = "/tmp", chunker = "markdown"}}]

        [[embedders]]
        name      = "fake"
        provider  = "sentence_transformers"
        model_id  = "fake-1"
        dimension = 384
        """
        )
        + estimate_block
    )
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(cfg, encoding="utf-8")
    return cfg_path


def _scan_dir(tmp_path: Path) -> Path:
    """Create a tiny mixed tree under tmp_path/scan and return that dir."""
    scan = tmp_path / "scan"
    (scan).mkdir(parents=True, exist_ok=True)
    (scan / "a.md").write_bytes(b"x" * 4096)
    (scan / "b.pdf").write_bytes(b"y" * 8192)
    (scan / "c.py").write_bytes(b"z" * 4096)
    return scan


# ─────────────────────────────────────────────────────────────────────────
# Human output
# ─────────────────────────────────────────────────────────────────────────


def test_estimate_human_output_contains_scan_summary(tmp_path: Path) -> None:
    cfg_path = _build_test_config(tmp_path)
    scan = _scan_dir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["estimate", str(scan)],
        env={"CORPUS_FORGE_CONFIG": str(cfg_path)},
    )
    assert result.exit_code == 0, result.output
    assert "Scanned" in result.output
    assert "files" in result.output


def test_estimate_human_output_contains_total(tmp_path: Path) -> None:
    cfg_path = _build_test_config(tmp_path)
    scan = _scan_dir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["estimate", str(scan)],
        env={"CORPUS_FORGE_CONFIG": str(cfg_path)},
    )
    assert result.exit_code == 0, result.output
    assert "Total" in result.output


def test_estimate_human_output_lists_by_extractor(tmp_path: Path) -> None:
    cfg_path = _build_test_config(tmp_path)
    scan = _scan_dir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["estimate", str(scan)],
        env={"CORPUS_FORGE_CONFIG": str(cfg_path)},
    )
    assert result.exit_code == 0, result.output
    # All three extractor classes touched by the scan tree.
    assert "markdown" in result.output
    assert "pdf" in result.output
    assert "code" in result.output


# ─────────────────────────────────────────────────────────────────────────
# JSON output
# ─────────────────────────────────────────────────────────────────────────


def test_estimate_json_output_is_parseable_and_has_schema_version(tmp_path: Path) -> None:
    cfg_path = _build_test_config(tmp_path)
    scan = _scan_dir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["estimate", str(scan), "--json"],
        env={"CORPUS_FORGE_CONFIG": str(cfg_path)},
    )
    assert result.exit_code == 0, result.output
    payload = _json.loads(result.output)
    assert payload["schema_version"] == 1
    assert "file_count" in payload
    assert "embeddings" in payload


def test_estimate_json_output_total_matches_parts(tmp_path: Path) -> None:
    cfg_path = _build_test_config(tmp_path)
    scan = _scan_dir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["estimate", str(scan), "--json"],
        env={"CORPUS_FORGE_CONFIG": str(cfg_path)},
    )
    assert result.exit_code == 0
    payload = _json.loads(result.output)
    embedding_total = sum(e["total_bytes"] for e in payload["embeddings"])
    assert payload["total_bytes"] == (
        payload["documents_bytes"]
        + payload["chunks_bytes"]
        + embedding_total
        + payload["btree_index_bytes"]
    )


# ─────────────────────────────────────────────────────────────────────────
# Error paths
# ─────────────────────────────────────────────────────────────────────────


def test_estimate_missing_path_exits_with_code_2_and_stderr(tmp_path: Path) -> None:
    cfg_path = _build_test_config(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["estimate", str(tmp_path / "does-not-exist")],
        env={"CORPUS_FORGE_CONFIG": str(cfg_path)},
    )
    assert result.exit_code == 2
    # Output (stderr is mixed into output by default in current Typer).
    text = (result.output or "").lower()
    assert (
        "does-not-exist" in (result.output or "")
        or "not found" in text
        or "does not exist" in text
        or "exist" in text
    )


def test_estimate_missing_config_exits_with_code_2(tmp_path: Path) -> None:
    scan = _scan_dir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["estimate", str(scan)],
        env={"CORPUS_FORGE_CONFIG": str(tmp_path / "no-such-config.toml")},
    )
    assert result.exit_code == 2


def test_estimate_unknown_embedder_exits_with_clear_error(tmp_path: Path) -> None:
    cfg_path = _build_test_config(tmp_path)
    scan = _scan_dir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["estimate", str(scan), "--embedder", "nope"],
        env={"CORPUS_FORGE_CONFIG": str(cfg_path)},
    )
    assert result.exit_code == 2
    assert "nope" in (result.output or "")


# ─────────────────────────────────────────────────────────────────────────
# Flag passthrough
# ─────────────────────────────────────────────────────────────────────────


def test_estimate_compression_ratio_flag_overrides_config(tmp_path: Path) -> None:
    cfg_path = _build_test_config(tmp_path, compression_ratio=1.0)
    scan = _scan_dir(tmp_path)
    runner = CliRunner()
    full = runner.invoke(
        app,
        ["estimate", str(scan), "--json"],
        env={"CORPUS_FORGE_CONFIG": str(cfg_path)},
    )
    half = runner.invoke(
        app,
        ["estimate", str(scan), "--json", "--compression-ratio", "0.5"],
        env={"CORPUS_FORGE_CONFIG": str(cfg_path)},
    )
    assert full.exit_code == 0
    assert half.exit_code == 0
    f = _json.loads(full.output)
    h = _json.loads(half.output)
    assert f["compression_ratio"] == 1.0
    assert h["compression_ratio"] == 0.5
    assert h["chunks_bytes"] < f["chunks_bytes"]


def test_estimate_embedder_filter_passthrough(tmp_path: Path) -> None:
    cfg_path = _build_test_config(tmp_path)
    scan = _scan_dir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["estimate", str(scan), "--json", "--embedder", "fake"],
        env={"CORPUS_FORGE_CONFIG": str(cfg_path)},
    )
    assert result.exit_code == 0
    payload = _json.loads(result.output)
    assert [e["name"] for e in payload["embeddings"]] == ["fake"]
    assert payload["embedders_active"] == ["fake"]


def test_estimate_dataset_flag_unknown_does_not_crash(tmp_path: Path) -> None:
    """Per current contract the dataset filter is permissive (a no-op for
    embedder selection in J1 — kept as a forward-compat hook). Passing
    an unknown name must not crash; the estimate falls back to all
    active embedders."""
    cfg_path = _build_test_config(tmp_path)
    scan = _scan_dir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["estimate", str(scan), "--dataset", "no-such-dataset"],
        env={"CORPUS_FORGE_CONFIG": str(cfg_path)},
    )
    assert result.exit_code == 0


# ─────────────────────────────────────────────────────────────────────────
# Help surface
# ─────────────────────────────────────────────────────────────────────────


def test_estimate_help_lists_all_flags(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["estimate", "--help"])
    assert result.exit_code == 0
    text = result.output
    for flag in ("--json", "--compression-ratio", "--embedder", "--dataset", "--verbose"):
        assert flag in text, f"missing flag: {flag}"


def test_estimate_verbose_flag_does_not_crash(tmp_path: Path) -> None:
    cfg_path = _build_test_config(tmp_path)
    scan = _scan_dir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["estimate", str(scan), "--verbose"],
        env={"CORPUS_FORGE_CONFIG": str(cfg_path)},
    )
    assert result.exit_code == 0
