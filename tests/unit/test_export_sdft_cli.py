"""Unit tests for ``corpus-forge export sdft`` — config-missing guard.

The ``export sdft`` subcommand had no dedicated CLI test file; this covers
the clean missing-config error (rfc — CLI clean-errors hardening), matching
the ``export chat`` / ``export feedback-pairs`` guard tests.
"""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from corpus_forge.cli import app

runner = CliRunner()


class TestExportSdftConfigGuard:
    """Missing config → clean error + exit 2, not a raw FileNotFoundError."""

    def test_export_sdft_no_config_clean_error(self) -> None:
        with patch(
            "corpus_forge.config.Config.load",
            side_effect=FileNotFoundError("Configuration file not found"),
        ):
            result = runner.invoke(app, ["export", "sdft", "--dataset", "d", "--out", "./o.jsonl"])
        assert result.exit_code == 2, result.output
        assert "no configuration found" in result.output.lower()
        assert "corpus-forge setup" in result.output
