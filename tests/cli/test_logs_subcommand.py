"""Phase L Wave 6 — CLI smoke for ``corpus-forge logs ...`` (W6-05).

Verifies the Typer sub-app is mounted via ``add_typer`` so the user
can run ``corpus-forge logs path``.  Underlying behavior is covered in
``tests/diagnostics/test_logs_subcommand.py``.
"""

from __future__ import annotations

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
    return log_dir


def test_logs_subapp_mounted() -> None:
    from corpus_forge.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["logs", "--help"])
    assert result.exit_code == 0
    # The three verbs must be listed in the sub-app's help.
    assert "path" in result.output
    assert "tail" in result.output
    assert "clear" in result.output


def test_logs_path_prints_dir(isolated_log_dir: Path) -> None:
    from corpus_forge.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["logs", "path"])
    assert result.exit_code == 0
    assert str(isolated_log_dir) in result.output


def test_logs_tail_reads_last_n(isolated_log_dir: Path) -> None:
    from corpus_forge.cli import app

    log_path = isolated_log_dir / "cli.log"
    log_path.write_text(
        "\n".join(f"2026-05-18 12:00:0{i} [INFO   ] cli: message {i}" for i in range(8)) + "\n"
    )
    runner = CliRunner()
    result = runner.invoke(app, ["logs", "tail", "-n", "3"])

    assert result.exit_code == 0
    assert "message 7" in result.output
    # ``-n 3`` keeps the last three lines (5, 6, 7); message 4 is dropped.
    assert "message 4" not in result.output


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
