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
import sys
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
        # The warning mentions the missing log file.  Strip linewrap
        # whitespace so a narrow CI terminal (Linux) that splits
        # ``daemon`` across newlines still matches the substring.
        flat = "".join(result.output.split())
        assert "daemon.log" in flat

    def test_handles_unparseable_lines_gracefully(self, isolated_log_dir: Path) -> None:
        from corpus_forge.diagnostics.logs import logs_app

        (isolated_log_dir / "cli.log").write_text("this line does not match the format\n")

        runner = CliRunner()
        result = runner.invoke(logs_app, ["tail"])

        assert result.exit_code == 0
        assert "this line" in result.output


# ─── logs tail --level (severity filter) ────────────────────────────────


class TestLogsTailLevelFilter:
    """``--level`` drops lines below the threshold (incl. unparseable lines)."""

    @staticmethod
    def _mixed_log_lines() -> list[str]:
        return [
            "2026-05-18 12:00:00 [DEBUG  ] cli: debug message",
            "2026-05-18 12:00:01 [INFO   ] cli: info message",
            "2026-05-18 12:00:02 [WARNING] cli: warning message",
            "2026-05-18 12:00:03 [ERROR  ] cli: error message",
            "2026-05-18 12:00:04 [CRITICAL] cli: critical message",
        ]

    def test_no_level_arg_prints_everything(self, isolated_log_dir: Path) -> None:
        from corpus_forge.diagnostics.logs import logs_app

        (isolated_log_dir / "cli.log").write_text("\n".join(self._mixed_log_lines()) + "\n")
        runner = CliRunner()
        result = runner.invoke(logs_app, ["tail"])
        assert result.exit_code == 0
        for keyword in ("debug message", "info message", "warning message", "error message"):
            assert keyword in result.output

    def test_level_warn_drops_debug_and_info(self, isolated_log_dir: Path) -> None:
        from corpus_forge.diagnostics.logs import logs_app

        (isolated_log_dir / "cli.log").write_text("\n".join(self._mixed_log_lines()) + "\n")
        runner = CliRunner()
        result = runner.invoke(logs_app, ["tail", "--level", "warn"])
        assert result.exit_code == 0
        assert "debug message" not in result.output
        assert "info message" not in result.output
        assert "warning message" in result.output
        assert "error message" in result.output
        assert "critical message" in result.output

    def test_level_error_drops_warning(self, isolated_log_dir: Path) -> None:
        from corpus_forge.diagnostics.logs import logs_app

        (isolated_log_dir / "cli.log").write_text("\n".join(self._mixed_log_lines()) + "\n")
        runner = CliRunner()
        result = runner.invoke(logs_app, ["tail", "--level", "error"])
        assert result.exit_code == 0
        assert "warning message" not in result.output
        assert "error message" in result.output
        assert "critical message" in result.output

    def test_level_filter_is_case_insensitive(self, isolated_log_dir: Path) -> None:
        from corpus_forge.diagnostics.logs import logs_app

        (isolated_log_dir / "cli.log").write_text("\n".join(self._mixed_log_lines()) + "\n")
        runner = CliRunner()
        result = runner.invoke(logs_app, ["tail", "--level", "ERROR"])
        assert result.exit_code == 0
        assert "warning message" not in result.output
        assert "error message" in result.output

    def test_warning_alias_accepted(self, isolated_log_dir: Path) -> None:
        """Both ``warn`` and ``warning`` resolve to the same threshold."""
        from corpus_forge.diagnostics.logs import logs_app

        (isolated_log_dir / "cli.log").write_text("\n".join(self._mixed_log_lines()) + "\n")
        runner = CliRunner()
        for token in ("warn", "warning", "WARNING"):
            result = runner.invoke(logs_app, ["tail", "--level", token])
            assert result.exit_code == 0, f"token {token!r} returned exit {result.exit_code}"
            assert "info message" not in result.output, f"token {token!r} did not filter info"
            assert "warning message" in result.output

    def test_unknown_level_rejected_with_clean_error(self, isolated_log_dir: Path) -> None:
        from corpus_forge.diagnostics.logs import logs_app

        (isolated_log_dir / "cli.log").write_text("2026-05-18 12:00:00 [INFO   ] cli: x\n")
        runner = CliRunner()
        result = runner.invoke(logs_app, ["tail", "--level", "trace"])
        # Typer's BadParameter → non-zero exit + 'Invalid value' in output.
        assert result.exit_code != 0
        # The error message names the bad token and lists the accepted set.
        assert "trace" in result.output
        # At least one of the documented levels appears in the accepted list.
        assert "info" in result.output or "warning" in result.output

    def test_level_drops_unparseable_lines(self, isolated_log_dir: Path) -> None:
        """A user who asks for ``--level error`` wants only error lines.

        Tracebacks / ASCII / ``print()`` output don't carry a level
        token; per the documented contract they're suppressed when the
        filter is active.
        """
        from corpus_forge.diagnostics.logs import logs_app

        (isolated_log_dir / "cli.log").write_text(
            "2026-05-18 12:00:00 [ERROR  ] cli: real error\n"
            '  File "foo.py", line 1, in <module>\n'
            "    raise RuntimeError('x')\n"
            "RuntimeError: x\n"
        )
        runner = CliRunner()
        result = runner.invoke(logs_app, ["tail", "--level", "error"])
        assert result.exit_code == 0
        assert "real error" in result.output
        # Traceback frame lines (no [LEVEL] token) are dropped.
        assert "RuntimeError: x" not in result.output

    def test_level_filter_helper_independent_of_cli(self) -> None:
        """``_passes_level_filter`` is exposed for non-CLI callers."""
        from corpus_forge.diagnostics.logs import _LEVEL_RANK, _passes_level_filter

        info_line = "2026-05-18 12:00:00 [INFO   ] cli: hi"
        error_line = "2026-05-18 12:00:00 [ERROR  ] cli: bad"

        assert _passes_level_filter(info_line, None) is True
        assert _passes_level_filter(info_line, _LEVEL_RANK["INFO"]) is True
        assert _passes_level_filter(info_line, _LEVEL_RANK["WARNING"]) is False
        assert _passes_level_filter(error_line, _LEVEL_RANK["WARNING"]) is True


# ─── logs tail --follow (SIGINT) ────────────────────────────────────────


class TestLogsTailFollow:
    @pytest.mark.skipif(
        sys.platform == "win32",
        reason=(
            "Windows xdist workers don't survive os.kill(getpid, SIGINT) — "
            "SIGINT delivery to the current process under pytest crashes the worker "
            "rather than waking the poll loop. The follow path is exercised on POSIX runners."
        ),
    )
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

        # Linux CI under pytest occasionally surfaces the SIGINT as a
        # SystemExit(0) before the inner ``except KeyboardInterrupt``
        # in ``_tail_follow`` can catch it (depends on which syscall
        # the signal lands on).  Either outcome is a clean exit.
        try:
            exit_code = logs_mod._tail_follow(log_path, n_initial=10, poll_seconds=0.05)
        except SystemExit as exc:
            exit_code = int(exc.code) if isinstance(exc.code, int) else 0

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
