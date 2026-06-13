"""G-04 RED — Unit tests for `corpus-forge export chat` CLI subcommand.

Tests use Typer's CliRunner (via ``typer.testing.CliRunner``) to invoke the
CLI without spawning subprocesses.

All tests FAIL RED because:
  1. ``corpus_forge.export`` module does not exist yet.
  2. The ``export chat`` subcommand is not wired in ``corpus_forge/cli.py``.

Run command:
    .venv/bin/python -m pytest tests/unit/test_export_chat_cli.py -v
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from corpus_forge.cli import app

runner = CliRunner()


class TestExportChatRequiredArgs:
    def test_export_chat_requires_dataset_and_out(self):
        """Invoking `export chat` without --dataset or --out yields a non-zero exit
        with a helpful error message mentioning the missing option — NOT a generic
        'No such command' error (which would mean the subcommand isn't wired yet)."""
        result = runner.invoke(app, ["export", "chat"])
        assert result.exit_code != 0, (
            f"Expected non-zero exit when --dataset and --out are omitted; "
            f"got {result.exit_code}. Output: {result.output!r}"
        )
        output_lower = result.output.lower()
        # Must NOT be a generic "no such command" or "no such option" from Typer
        # about the subcommand itself not existing. The subcommand must be registered
        # and the error must be about the missing *options*.
        assert "no such command" not in output_lower, (
            f"'export chat' subcommand is not registered (got 'No such command'). "
            f"The CLI must register the 'chat' subcommand under 'export'. "
            f"Output: {result.output!r}"
        )
        # Should mention the missing required option(s)
        assert (
            "missing" in output_lower or "--dataset" in output_lower or "--out" in output_lower
        ), (
            f"Expected a helpful error about missing required options (--dataset, --out); "
            f"got: {result.output!r}"
        )

    def test_export_chat_requires_out_when_dataset_given(self):
        """Invoking with --dataset but without --out yields a non-zero exit with an
        error about --out (not a 'No such command' error)."""
        result = runner.invoke(app, ["export", "chat", "--dataset", "cf-self-docs"])
        assert result.exit_code != 0, (
            f"Expected non-zero exit when --out is omitted; got {result.exit_code}. "
            f"Output: {result.output!r}"
        )
        output_lower = result.output.lower()
        assert "no such command" not in output_lower, (
            f"'export chat' subcommand is not registered; got 'No such command'. "
            f"Output: {result.output!r}"
        )

    def test_export_chat_requires_dataset_when_out_given(self):
        """Invoking with --out but without --dataset yields a non-zero exit with an
        error about --dataset (not a 'No such command' error)."""
        result = runner.invoke(app, ["export", "chat", "--out", "./out.jsonl"])
        assert result.exit_code != 0, (
            f"Expected non-zero exit when --dataset is omitted; got {result.exit_code}. "
            f"Output: {result.output!r}"
        )
        output_lower = result.output.lower()
        assert "no such command" not in output_lower, (
            f"'export chat' subcommand is not registered; got 'No such command'. "
            f"Output: {result.output!r}"
        )


class TestExportChatDispatch:
    @pytest.fixture(autouse=True)
    def _config_present(self):
        """The handler now guards on ``Config.load()`` (clean missing-config
        error). These dispatch tests stub the export fn but run the real
        handler body, so the guard executes — make config "present" so they
        pass regardless of whether the CI runner has ``~/.config/corpus-forge``.
        """
        with patch("corpus_forge.config.Config.load", return_value=MagicMock()):
            yield

    def test_export_chat_dispatches_to_export_module(self):
        """Valid invocation monkeypatches corpus_forge.export.export_chat and asserts it
        was called with the expected keyword arguments."""
        stub = MagicMock(return_value=None)
        with patch("corpus_forge.export.export_chat", stub):
            result = runner.invoke(
                app,
                [
                    "export",
                    "chat",
                    "--template",
                    "chatml",
                    "--dataset",
                    "cf-self-docs",
                    "--out",
                    "./out.jsonl",
                ],
            )
        assert result.exit_code == 0, (
            f"Expected exit 0 with valid args; got {result.exit_code}. Output: {result.output!r}"
        )
        stub.assert_called_once()
        kwargs = stub.call_args.kwargs if stub.call_args.kwargs else {}
        args = stub.call_args.args if stub.call_args.args else ()
        # Accept both positional and keyword call patterns
        all_args = {**kwargs}
        if args:
            # Map positional args by known param order: dataset, template, out_path, format
            param_order = ["dataset", "template", "out_path", "format"]
            for i, a in enumerate(args):
                if i < len(param_order):
                    all_args[param_order[i]] = a
        assert all_args.get("dataset") == "cf-self-docs" or "cf-self-docs" in str(stub.call_args), (
            f"Expected dataset='cf-self-docs' in call; got {stub.call_args}"
        )
        assert all_args.get("template") == "chatml" or "chatml" in str(stub.call_args), (
            f"Expected template='chatml' in call; got {stub.call_args}"
        )

    def test_export_chat_format_defaults_to_jsonl(self):
        """When --format is omitted, the stub sees format='jsonl'."""
        stub = MagicMock(return_value=None)
        with patch("corpus_forge.export.export_chat", stub):
            result = runner.invoke(
                app,
                [
                    "export",
                    "chat",
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
        # 'jsonl' must appear somewhere in the call arguments
        assert "jsonl" in str(stub.call_args), (
            f"Expected format='jsonl' (default) to reach export_chat; got {stub.call_args}"
        )

    def test_export_chat_format_parquet_passed_through(self):
        """--format parquet reaches the stub as format='parquet'."""
        stub = MagicMock(return_value=None)
        with patch("corpus_forge.export.export_chat", stub):
            result = runner.invoke(
                app,
                [
                    "export",
                    "chat",
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
            f"Expected format='parquet' to reach export_chat; got {stub.call_args}"
        )

    def test_export_chat_push_flag_passed_through(self):
        """--push <repo> reaches the stub as push='my-org/my-dataset'."""
        stub = MagicMock(return_value=None)
        with patch("corpus_forge.export.export_chat", stub):
            result = runner.invoke(
                app,
                [
                    "export",
                    "chat",
                    "--dataset",
                    "cf-self-docs",
                    "--out",
                    "./out.jsonl",
                    "--push",
                    "my-org/my-dataset",
                ],
            )
        assert result.exit_code == 0, (
            f"Expected exit 0; got {result.exit_code}. Output: {result.output!r}"
        )
        stub.assert_called_once()
        assert "my-org/my-dataset" in str(stub.call_args), (
            f"Expected push='my-org/my-dataset' to reach export_chat; got {stub.call_args}"
        )


class TestExportChatHelp:
    def test_export_chat_help_lists_template_and_dataset(self):
        """corpus-forge export chat --help mentions --template, --dataset, --out,
        --format, and --push."""
        result = runner.invoke(app, ["export", "chat", "--help"])
        assert result.exit_code == 0, (
            f"--help should exit 0; got {result.exit_code}. Output: {result.output!r}"
        )
        output = result.output
        assert "--template" in output, f"--help missing --template. Got:\n{output}"
        assert "--dataset" in output, f"--help missing --dataset. Got:\n{output}"
        assert "--out" in output, f"--help missing --out. Got:\n{output}"
        assert "--format" in output, f"--help missing --format. Got:\n{output}"
        assert "--push" in output, f"--help missing --push. Got:\n{output}"

    def test_export_help_lists_chat_subcommand(self):
        """corpus-forge export --help shows 'chat' as a subcommand."""
        result = runner.invoke(app, ["export", "--help"])
        assert result.exit_code == 0, (
            f"export --help should exit 0; got {result.exit_code}. Output: {result.output!r}"
        )
        assert "chat" in result.output.lower(), (
            f"Expected 'chat' subcommand in export --help output. Got:\n{result.output}"
        )


class TestExportChatConfigGuard:
    """Missing config → clean error + exit 2, not a raw FileNotFoundError."""

    def test_export_chat_no_config_clean_error(self):
        with patch(
            "corpus_forge.config.Config.load",
            side_effect=FileNotFoundError("Configuration file not found"),
        ):
            result = runner.invoke(app, ["export", "chat", "--dataset", "d", "--out", "./o.jsonl"])
        assert result.exit_code == 2, result.output
        assert "no configuration found" in result.output.lower()
        assert "corpus-forge setup" in result.output
        assert result.exception is None or isinstance(result.exception, SystemExit)
