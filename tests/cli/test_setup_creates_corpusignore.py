"""Phase M Wave 1 — end-to-end CLI assertion: ``corpus-forge setup``
under ``CF_NON_INTERACTIVE=1 CF_CREATE_CORPUSIGNORE=yes CF_SCAN_ROOT=<tmp>``
writes a ``.corpusignore`` at the scan root that parses cleanly via
``CorpusIgnore.from_file``.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from corpus_forge.cli import app
from corpus_forge.ignore import CorpusIgnore
from corpus_forge.ignore_defaults import MANAGED_END, MANAGED_START


def _runner() -> CliRunner:
    return CliRunner()


def test_setup_creates_corpusignore_at_scan_root(tmp_path: Path, monkeypatch) -> None:
    scan_root = tmp_path / "vault"
    scan_root.mkdir()
    config_dir = tmp_path / "cf"

    # Drive answers via env vars (non-interactive path).
    monkeypatch.setenv("CF_BACKEND", "sqlite")
    monkeypatch.setenv("CF_MULTI_FORMAT", "no")
    monkeypatch.setenv("CF_CODE", "no")
    monkeypatch.setenv("CF_OCR", "no")
    monkeypatch.setenv("CF_WHISPER", "no")
    monkeypatch.setenv("CF_TOKENS", "no")
    monkeypatch.setenv("CF_RETRIEVAL", "no")
    monkeypatch.setenv("CF_RERANKER", "no")
    monkeypatch.setenv("CF_EMBEDDER", "st")
    monkeypatch.setenv("CF_CLASSIFIER", "rule")
    monkeypatch.setenv("CF_MCP", "no")
    monkeypatch.setenv("CF_HF", "no")
    monkeypatch.setenv("CF_SUPERVISOR", "no")
    monkeypatch.setenv("CF_CREATE_CORPUSIGNORE", "yes")
    monkeypatch.setenv("CF_SCAN_ROOT", str(scan_root))

    result = _runner().invoke(
        app,
        ["setup", "--non-interactive", "--config-dir", str(config_dir)],
    )
    assert result.exit_code == 0, result.output

    ignore_path = scan_root / ".corpusignore"
    assert ignore_path.exists(), f"missing {ignore_path}; CLI output: {result.output}"
    text = ignore_path.read_text(encoding="utf-8")
    assert MANAGED_START in text
    assert MANAGED_END in text

    # Parses cleanly via the existing matcher.
    ig = CorpusIgnore.from_file(ignore_path, root=scan_root)
    assert len(ig.patterns) > 0
    # The always-on preamble contains the macOS metadata file.
    assert ig.matches(scan_root / ".DS_Store", is_dir=False) is True
