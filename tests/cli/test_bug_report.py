"""Phase L Wave 6 — CLI smoke for the ``bug-report`` + ``logs`` commands (W6-05).

These are *integration* tests around the Typer wiring; the bundler
itself is exhaustively covered in ``tests/diagnostics/test_bug_report.py``.
We invoke through ``CliRunner`` so the Typer registration and the
no-typer-echo contract both stay live.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner


@pytest.fixture
def isolated_log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True)
    monkeypatch.setenv("CF_LOG_DIR", str(log_dir))
    from corpus_forge.logging_config import init_logging

    init_logging("cli")
    (log_dir / "cli.log").write_text("2026-05-18 12:00:00 [INFO   ] cli: ok\n")
    return log_dir


@pytest.fixture
def cwd_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_bug_report_command_in_help() -> None:
    """``bug-report`` is listed in ``corpus-forge --help``."""

    from corpus_forge.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "bug-report" in result.output


def test_bug_report_writes_zip(isolated_log_dir: Path, cwd_tmp: Path) -> None:
    from corpus_forge.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["bug-report", "--no-db"])

    assert result.exit_code == 0, result.output
    zips = list(cwd_tmp.glob("corpus-forge-bugreport-*.zip"))
    assert len(zips) == 1
    with zipfile.ZipFile(zips[0]) as zf:
        names = zf.namelist()
    assert "manifest.json" in names
    assert "README.txt" in names


def test_bug_report_no_zip_writes_directory(isolated_log_dir: Path, cwd_tmp: Path) -> None:
    from corpus_forge.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["bug-report", "--no-db", "--no-zip"])

    assert result.exit_code == 0, result.output
    dirs = list(cwd_tmp.glob("corpus-forge-bugreport-*"))
    assert len(dirs) == 1
    assert dirs[0].is_dir()
    assert (dirs[0] / "manifest.json").exists()


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
