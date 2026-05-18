"""Phase L Wave 6 — ``corpus-forge logs`` subcommand (W6-04).

Three verbs:

- ``logs path`` → print the platformdirs log directory.
- ``logs tail`` → cat the last N lines (default 200), optionally
  follow with a 250 ms poll loop until SIGINT.
- ``logs clear`` → truncate matched component logs, with
  :class:`Confirm.ask` gating unless ``--yes``.

The module exports the Typer sub-app plus the underlying helpers so
tests can exercise the non-CLI surface directly when convenient.
"""

from __future__ import annotations

import os
import signal
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner


@pytest.fixture
def isolated_log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``CF_LOG_DIR`` at a fresh tmp dir for the duration of the test."""

    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True)
    monkeypatch.setenv("CF_LOG_DIR", str(log_dir))
    # Re-initialize logging so the new directory is picked up.
    from corpus_forge.logging_config import init_logging

    init_logging("cli")
    return log_dir


# ─── logs path ──────────────────────────────────────────────────────────


class TestLogsPath:
    def test_prints_log_dir(self, isolated_log_dir: Path) -> None:
        from corpus_forge.diagnostics.logs import logs_app

        runner = CliRunner()
        result = runner.invoke(logs_app, ["path"])

        assert result.exit_code == 0
        assert str(isolated_log_dir) in result.output


# ─── logs tail (one-shot) ───────────────────────────────────────────────


class TestLogsTail:
    def test_reads_last_n_lines(self, isolated_log_dir: Path) -> None:
        from corpus_forge.diagnostics.logs import logs_app

        log_path = isolated_log_dir / "cli.log"
        lines = [f"2026-05-18 12:00:0{i} [INFO   ] cli: message {i}" for i in range(10)]
        log_path.write_text("\n".join(lines) + "\n")

        runner = CliRunner()
        result = runner.invoke(logs_app, ["tail", "--component", "cli", "-n", "3"])

        assert result.exit_code == 0
        # Last 3 lines visible; the 4th-to-last (message 6) is not.
        assert "message 9" in result.output
        assert "message 8" in result.output
        assert "message 7" in result.output
        assert "message 6" not in result.output

    def test_default_component_is_cli(self, isolated_log_dir: Path) -> None:
        from corpus_forge.diagnostics.logs import logs_app

        (isolated_log_dir / "cli.log").write_text(
            "2026-05-18 12:00:00 [INFO   ] cli: hello from cli\n"
        )

        runner = CliRunner()
        result = runner.invoke(logs_app, ["tail"])

        assert result.exit_code == 0
        assert "hello from cli" in result.output

    def test_warns_when_log_missing(self, isolated_log_dir: Path) -> None:
        from corpus_forge.diagnostics.logs import logs_app

        runner = CliRunner()
        result = runner.invoke(logs_app, ["tail", "--component", "daemon"])

        # Missing file → exit 0 (not an error) with a warning line.
        assert result.exit_code == 0
        # The warning mentions the missing file.
        assert "daemon" in result.output

    def test_handles_unparseable_lines_gracefully(self, isolated_log_dir: Path) -> None:
        from corpus_forge.diagnostics.logs import logs_app

        (isolated_log_dir / "cli.log").write_text("this line does not match the format\n")

        runner = CliRunner()
        result = runner.invoke(logs_app, ["tail"])

        assert result.exit_code == 0
        assert "this line" in result.output


# ─── logs tail --follow (SIGINT) ────────────────────────────────────────


class TestLogsTailFollow:
    def test_follow_exits_cleanly_on_sigint(self, isolated_log_dir: Path) -> None:
        from corpus_forge.diagnostics import logs as logs_mod

        log_path = isolated_log_dir / "cli.log"
        log_path.write_text("seed line\n")

        # Background thread that fires SIGINT after a short delay so the
        # blocking poll loop exits via KeyboardInterrupt.
        def _interrupt_soon() -> None:
            time.sleep(0.3)
            os.kill(os.getpid(), signal.SIGINT)

        interrupter = threading.Thread(target=_interrupt_soon, daemon=True)
        interrupter.start()

        exit_code = logs_mod._tail_follow(log_path, n_initial=10, poll_seconds=0.05)

        assert exit_code == 0


# ─── logs clear ────────────────────────────────────────────────────────


class TestLogsClear:
    def test_clear_with_yes_truncates(self, isolated_log_dir: Path) -> None:
        from corpus_forge.diagnostics.logs import logs_app

        log_path = isolated_log_dir / "cli.log"
        log_path.write_text("lots of content\n")
        assert log_path.stat().st_size > 0

        runner = CliRunner()
        result = runner.invoke(logs_app, ["clear", "--component", "cli", "--yes"])

        assert result.exit_code == 0
        assert log_path.read_text() == ""

    def test_clear_without_yes_prompts(self, isolated_log_dir: Path) -> None:
        from corpus_forge.diagnostics.logs import logs_app

        log_path = isolated_log_dir / "cli.log"
        log_path.write_text("data\n")

        runner = CliRunner()
        # Patch the Confirm.ask used by the module so we don't actually
        # block on stdin.
        with patch("corpus_forge.diagnostics.logs.Confirm") as MockConfirm:
            MockConfirm.ask.return_value = False
            result = runner.invoke(logs_app, ["clear", "--component", "cli"])

        assert result.exit_code == 0
        # Declined → file not truncated.
        assert log_path.read_text() == "data\n"
        MockConfirm.ask.assert_called_once()

    def test_clear_unknown_component_warns(self, isolated_log_dir: Path) -> None:
        from corpus_forge.diagnostics.logs import logs_app

        runner = CliRunner()
        result = runner.invoke(logs_app, ["clear", "--component", "ghost", "--yes"])

        assert result.exit_code == 0
        # Friendly skip — not a hard error.


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
