"""Failing tests for CLI sync subcommands (P1-29)."""

from typer.testing import CliRunner

from corpus_forge.cli import app

runner = CliRunner()


class TestSyncStatus:
    def test_sync_status_calls_backend(self):
        result = runner.invoke(app, ["sync", "status"])
        assert result.exit_code == 0, f"sync status should succeed, got {result.output}"


class TestSyncPull:
    def test_sync_pull_once_calls_pipeline(self):
        result = runner.invoke(app, ["sync", "pull", "--once", "-d", "test-ds"])
        assert result.exit_code == 0, f"sync pull --once should succeed, got {result.output}"

    def test_sync_pull_continuous_flag_accepted(self):
        result = runner.invoke(app, ["sync", "pull", "--continuous", "-d", "test-ds"])
        assert result.exit_code == 0, f"sync pull --continuous should succeed, got {result.output}"

    def test_sync_pull_missing_dataset_rejected(self):
        result = runner.invoke(app, ["sync", "pull", "--once"])
        assert result.exit_code != 0


class TestSyncPush:
    def test_sync_push_calls_pipeline(self):
        result = runner.invoke(app, ["sync", "push", "-d", "test-ds"])
        assert result.exit_code == 0, f"sync push should succeed, got {result.output}"


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
        assert result.exit_code == 0, f"sync history should succeed, got {result.output}"

    def test_sync_history_limit_option(self):
        result = runner.invoke(app, ["sync", "history", "source://test", "--limit", "5"])
        assert result.exit_code == 0, (
            f"sync history --limit should be accepted, got {result.output}"
        )
