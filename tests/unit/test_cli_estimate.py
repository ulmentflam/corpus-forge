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
    payload = _json.loads(result.stdout)
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
    payload = _json.loads(result.stdout)
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
    f = _json.loads(full.stdout)
    h = _json.loads(half.stdout)
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
    payload = _json.loads(result.stdout)
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


# ─────────────────────────────────────────────────────────────────────────
# Wall-clock time estimate (new)
# ─────────────────────────────────────────────────────────────────────────


def test_estimate_human_output_contains_wall_clock_section(tmp_path: Path) -> None:
    cfg_path = _build_test_config(tmp_path)
    scan = _scan_dir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["estimate", str(scan)],
        env={"CORPUS_FORGE_CONFIG": str(cfg_path), "CF_RUNTIME_PROFILE": str(tmp_path / "rp.json")},
    )
    assert result.exit_code == 0, result.output
    assert "Estimated wall-clock" in result.output
    # Isolate the wall-clock subsection so the phase-name search can't be
    # satisfied by the earlier "Scan stats" / "embeddings" sections (which
    # also contain the words "scan" and "embed").
    lower = result.output.lower()
    start = lower.index("estimated wall-clock")
    # The subsection ends at the calibration footer; that footer always
    # begins with "calibration" or "calibrated". Fall back to EOF if we
    # somehow can't find it.
    end_candidates = [lower.find(needle, start + 1) for needle in ("calibration", "calibrated")]
    end_candidates = [idx for idx in end_candidates if idx != -1]
    end = min(end_candidates) if end_candidates else len(lower)
    section = lower[start:end]
    for phase in ("scan", "extract", "chunk", "embed", "db_write"):
        assert phase in section, f"missing phase {phase} in wall-clock section"
    # Calibration footer must mention one of the three labels.
    assert "heuristic" in lower or "calibrat" in lower or "hybrid" in lower


def test_estimate_json_output_includes_time_block(tmp_path: Path) -> None:
    cfg_path = _build_test_config(tmp_path)
    scan = _scan_dir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["estimate", str(scan), "--json"],
        env={"CORPUS_FORGE_CONFIG": str(cfg_path), "CF_RUNTIME_PROFILE": str(tmp_path / "rp.json")},
    )
    assert result.exit_code == 0, result.output
    payload = _json.loads(result.stdout)
    assert "time" in payload
    time_block = payload["time"]
    assert time_block["schema_version"] == 1
    assert time_block["total_seconds"] >= 0
    assert {p["name"] for p in time_block["phases"]} == {
        "scan",
        "extract",
        "chunk",
        "embed",
        "db_write",
    }


# ─────────────────────────────────────────────────────────────────────────
# K1 — .corpusignore flag coverage
# ─────────────────────────────────────────────────────────────────────────


def _count_files(out: str) -> int:
    """Extract the file_count from the `--json` output."""
    payload = _json.loads(out)
    return int(payload["file_count"])


def test_estimate_honors_ignore_file_flag(tmp_path: Path) -> None:
    cfg_path = _build_test_config(tmp_path)
    scan = _scan_dir(tmp_path)
    # Drop a custom ignore file OUTSIDE the scan dir so auto-detect
    # doesn't kick in. (`.corpusignore` at the scan root would be
    # auto-detected; we want to prove the flag works on its own.)
    ignore_path = tmp_path / "custom-ignore"
    ignore_path.write_text("*.md\n", encoding="utf-8")
    runner = CliRunner()

    no_ignore = runner.invoke(
        app,
        ["estimate", str(scan), "--json", "--no-ignore-file", "--no-global-ignore"],
        env={"CORPUS_FORGE_CONFIG": str(cfg_path)},
    )
    assert no_ignore.exit_code == 0
    baseline = _count_files(no_ignore.stdout)

    with_ignore = runner.invoke(
        app,
        [
            "estimate",
            str(scan),
            "--json",
            "--ignore-file",
            str(ignore_path),
            "--no-global-ignore",
        ],
        env={"CORPUS_FORGE_CONFIG": str(cfg_path)},
    )
    assert with_ignore.exit_code == 0
    assert _count_files(with_ignore.stdout) < baseline


def test_estimate_honors_no_ignore_file_flag(tmp_path: Path) -> None:
    """`--no-ignore-file` must skip an auto-detected `.corpusignore`."""
    cfg_path = _build_test_config(tmp_path)
    scan = _scan_dir(tmp_path)
    # Auto-detect file at the scan root — would normally be honored.
    (scan / ".corpusignore").write_text("*.md\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["estimate", str(scan), "--json", "--no-ignore-file", "--no-global-ignore"],
        env={"CORPUS_FORGE_CONFIG": str(cfg_path)},
    )
    assert result.exit_code == 0
    # We don't know the exact count without running a sibling no-ignore
    # baseline, but if `--no-ignore-file` is honored, `*.md` files are
    # still counted. Probe by comparing to an auto-detect run.
    counted_with_disable = _count_files(result.stdout)

    auto = runner.invoke(
        app,
        ["estimate", str(scan), "--json", "--no-global-ignore"],
        env={"CORPUS_FORGE_CONFIG": str(cfg_path)},
    )
    assert auto.exit_code == 0
    counted_with_auto = _count_files(auto.stdout)
    # When auto-detect is active and `*.md` is ignored, fewer files counted.
    assert counted_with_disable > counted_with_auto


def test_estimate_auto_detects_corpusignore_at_root(tmp_path: Path) -> None:
    cfg_path = _build_test_config(tmp_path)
    scan = _scan_dir(tmp_path)
    # Pre-create the auto-detect file in the scan dir so the file set is
    # identical in both invocations — the ONLY difference is whether the
    # ignore is honored.
    (scan / ".corpusignore").write_text("*.md\n", encoding="utf-8")
    runner = CliRunner()

    # Baseline: explicitly disable both ignore legs.
    baseline = runner.invoke(
        app,
        ["estimate", str(scan), "--json", "--no-ignore-file", "--no-global-ignore"],
        env={"CORPUS_FORGE_CONFIG": str(cfg_path)},
    )
    baseline_count = _count_files(baseline.stdout)

    # Auto-detect: no flags, the `.corpusignore` at the scan root should
    # kick in and prune the `.md` file.
    auto = runner.invoke(
        app,
        ["estimate", str(scan), "--json", "--no-global-ignore"],
        env={"CORPUS_FORGE_CONFIG": str(cfg_path)},
    )
    assert auto.exit_code == 0
    assert _count_files(auto.stdout) < baseline_count


def test_estimate_ignore_file_missing_path_errors(tmp_path: Path) -> None:
    cfg_path = _build_test_config(tmp_path)
    scan = _scan_dir(tmp_path)
    missing = tmp_path / "no-such-file"
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["estimate", str(scan), "--ignore-file", str(missing)],
        env={"CORPUS_FORGE_CONFIG": str(cfg_path)},
    )
    assert result.exit_code == 2
    # The error path message should mention the missing file or be
    # actionable for the user.
    combined = (result.output or "") + (result.stderr or "")
    assert "not found" in combined.lower() or "no such" in combined.lower()


def test_estimate_ignore_file_and_no_ignore_file_mutex(tmp_path: Path) -> None:
    cfg_path = _build_test_config(tmp_path)
    scan = _scan_dir(tmp_path)
    placeholder = tmp_path / "any.ignore"
    placeholder.write_text("*.tmp\n", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "estimate",
            str(scan),
            "--ignore-file",
            str(placeholder),
            "--no-ignore-file",
        ],
        env={"CORPUS_FORGE_CONFIG": str(cfg_path)},
    )
    assert result.exit_code == 2
    combined = (result.output or "") + (result.stderr or "")
    assert "mutually exclusive" in combined.lower()
