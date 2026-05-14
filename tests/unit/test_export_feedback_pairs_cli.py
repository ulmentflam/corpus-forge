"""H-04 RED — Unit tests for ``corpus-forge export feedback-pairs`` CLI subcommand.

Tests use Typer's CliRunner (via ``typer.testing.CliRunner``) to invoke the
CLI without spawning subprocesses.

All tests FAIL RED because:
  1. ``corpus_forge.export.export_feedback_pairs`` does not exist yet.
  2. The ``export feedback-pairs`` subcommand is not wired in ``corpus_forge/cli.py``.

Run command:
    .venv/bin/python -m pytest tests/unit/test_export_feedback_pairs_cli.py -v

pytestmark: pytest.mark.unit
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from corpus_forge.cli import app

pytestmark = pytest.mark.unit

runner = CliRunner()


class TestExportFeedbackPairsSubcommandDispatch:
    def test_export_feedback_pairs_subcommand_dispatches(self) -> None:
        """Valid invocation monkeypatches export_feedback_pairs; stub called with right kwargs."""
        stub = MagicMock(return_value=None)
        with patch("corpus_forge.export.export_feedback_pairs", stub):
            result = runner.invoke(
                app,
                [
                    "export",
                    "feedback-pairs",
                    "--dataset",
                    "cf-self-docs",
                    "--template",
                    "chatml",
                    "--out",
                    "./feedback-pairs.jsonl",
                ],
            )
        assert result.exit_code == 0, (
            f"Expected exit 0 with valid args; got {result.exit_code}. Output: {result.output!r}"
        )
        stub.assert_called_once()
        call_str = str(stub.call_args)
        assert "cf-self-docs" in call_str, (
            f"Expected dataset='cf-self-docs' in call; got {stub.call_args}"
        )
        assert "chatml" in call_str, f"Expected template='chatml' in call; got {stub.call_args}"

    def test_export_feedback_pairs_format_defaults_to_jsonl(self) -> None:
        """When --format is omitted, the stub sees format='jsonl'."""
        stub = MagicMock(return_value=None)
        with patch("corpus_forge.export.export_feedback_pairs", stub):
            result = runner.invoke(
                app,
                [
                    "export",
                    "feedback-pairs",
                    "--dataset",
                    "cf-self-docs",
                    "--out",
                    "./out.jsonl",
                ],
            )
        assert result.exit_code == 0, (
            f"Expected exit 0; got {result.exit_code}. Output: {result.output!r}"
        )
        stub.assert_called_once()
        assert "jsonl" in str(stub.call_args), (
            f"Expected format='jsonl' (default) to reach export_feedback_pairs; "
            f"got {stub.call_args}"
        )

    def test_export_feedback_pairs_format_parquet_passed_through(self) -> None:
        """--format parquet reaches the stub as format='parquet'."""
        stub = MagicMock(return_value=None)
        with patch("corpus_forge.export.export_feedback_pairs", stub):
            result = runner.invoke(
                app,
                [
                    "export",
                    "feedback-pairs",
                    "--dataset",
                    "cf-self-docs",
                    "--out",
                    "./out.parquet",
                    "--format",
                    "parquet",
                ],
            )
        assert result.exit_code == 0, (
            f"Expected exit 0; got {result.exit_code}. Output: {result.output!r}"
        )
        stub.assert_called_once()
        assert "parquet" in str(stub.call_args), (
            f"Expected format='parquet' to reach export_feedback_pairs; got {stub.call_args}"
        )


class TestExportFeedbackPairsHelp:
    def test_export_feedback_pairs_help_lists_args(self) -> None:
        """export feedback-pairs --help mentions --dataset, --out, --template, --format."""
        result = runner.invoke(app, ["export", "feedback-pairs", "--help"])
        assert result.exit_code == 0, (
            f"--help should exit 0; got {result.exit_code}. Output: {result.output!r}"
        )
        output = result.output
        assert "--dataset" in output, f"--help missing --dataset. Got:\n{output}"
        assert "--out" in output, f"--help missing --out. Got:\n{output}"
        assert "--template" in output, f"--help missing --template. Got:\n{output}"
        assert "--format" in output, f"--help missing --format. Got:\n{output}"

    def test_export_help_lists_feedback_pairs_subcommand(self) -> None:
        """corpus-forge export --help shows 'feedback-pairs' as a subcommand."""
        result = runner.invoke(app, ["export", "--help"])
        assert result.exit_code == 0, (
            f"export --help should exit 0; got {result.exit_code}. Output: {result.output!r}"
        )
        assert "feedback-pairs" in result.output.lower() or "feedback" in result.output.lower(), (
            f"Expected 'feedback-pairs' subcommand in export --help output. Got:\n{result.output}"
        )

    def test_export_feedback_pairs_no_such_command_not_raised(self) -> None:
        """Invoking 'export feedback-pairs' without args must NOT give 'No such command'."""
        result = runner.invoke(app, ["export", "feedback-pairs"])
        output_lower = result.output.lower()
        assert "no such command" not in output_lower, (
            f"'export feedback-pairs' subcommand is not registered "
            f"(got 'No such command'). Output: {result.output!r}"
        )

    def test_export_feedback_pairs_requires_dataset_and_out(self) -> None:
        """Invoking without --dataset or --out yields non-zero exit mentioning missing option."""
        result = runner.invoke(app, ["export", "feedback-pairs"])
        assert result.exit_code != 0, (
            f"Expected non-zero exit when --dataset and --out are omitted; "
            f"got {result.exit_code}. Output: {result.output!r}"
        )
        output_lower = result.output.lower()
        assert "no such command" not in output_lower, (
            f"'export feedback-pairs' subcommand is not registered. Output: {result.output!r}"
        )
        assert (
            "missing" in output_lower or "--dataset" in output_lower or "--out" in output_lower
        ), f"Expected a helpful error about missing required options; got: {result.output!r}"
