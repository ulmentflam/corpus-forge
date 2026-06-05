"""Failing tests for CLI sync subcommands (P1-29).

Notes
-----
These tests run without a populated config — they only exercise the
no-config exit path (a graceful ``typer.Exit(code=2)`` with the
"run `corpus-forge setup`" hint). They do NOT assert success against a
real backend; that's covered by integration tests.
"""

import pytest
from typer.testing import CliRunner

from corpus_forge.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def no_real_config(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Force the missing-config path regardless of the dev machine.

    ``Config.load()`` resolves ``CORPUS_FORGE_CONFIG`` first, then
    ``~/.config/corpus-forge/config.toml`` — so on any machine with a
    real config these tests used to exercise the *found*-config path
    and fail (CI was green only because runners have no config).
    Pointing the env var at a path that doesn't exist pins the
    behavior under test on every machine.
    """
    monkeypatch.setenv("CORPUS_FORGE_CONFIG", str(tmp_path / "nonexistent-config.toml"))


def _assert_no_config_exit(result) -> None:
    assert result.exit_code == 2, (
        f"missing-config path must exit 2 (matches other missing-config "
        f"branches in the CLI); got {result.exit_code}\n{result.output}"
    )
    assert "no configuration found" in result.output.lower(), (
        f"missing-config message must name the fix; got:\n{result.output}"
    )


class TestSyncStatus:
    def test_sync_status_calls_backend(self):
        result = runner.invoke(app, ["sync", "status"])
        _assert_no_config_exit(result)


class TestSyncPull:
    def test_sync_pull_once_calls_pipeline(self):
        result = runner.invoke(app, ["sync", "pull", "--once", "-d", "test-ds"])
        _assert_no_config_exit(result)

    def test_sync_pull_continuous_flag_accepted(self):
        result = runner.invoke(app, ["sync", "pull", "--continuous", "-d", "test-ds"])
        _assert_no_config_exit(result)

    def test_sync_pull_missing_dataset_rejected(self):
        result = runner.invoke(app, ["sync", "pull", "--once"])
        assert result.exit_code != 0


class TestSyncPush:
    def test_sync_push_calls_pipeline(self):
        result = runner.invoke(app, ["sync", "push", "-d", "test-ds"])
        _assert_no_config_exit(result)


class TestSyncResolve:
    def test_sync_resolve_merge_strategy_raises_friendly_error(self):
        result = runner.invoke(app, ["sync", "resolve", "conflict.md", "--strategy", "merge"])
        assert result.exit_code != 0
        # Phase L Wave 2: warning routed through ui.warn → stderr. CliRunner
        # default mixes both streams into result.output; assert there.
        assert "not yet implemented" in result.output.lower()

    def test_sync_resolve_ours_succeeds(self):
        result = runner.invoke(app, ["sync", "resolve", "conflict.md", "--strategy", "ours"])
        assert result.exit_code == 0, (
            f"sync resolve --strategy ours should succeed, got {result.output}"
        )


class TestSyncHistory:
    def test_sync_history_shows_revisions(self):
        result = runner.invoke(app, ["sync", "history", "source://test"])
        _assert_no_config_exit(result)

    def test_sync_history_limit_option(self):
        result = runner.invoke(app, ["sync", "history", "source://test", "--limit", "5"])
        _assert_no_config_exit(result)
