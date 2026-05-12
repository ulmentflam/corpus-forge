"""Unit tests for daemon module."""

from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from corpus_forge.config import (
    BackendConfig,
    Config,
    DaemonConfig,
    DatasetConfig,
    DatasetSourceConfig,
    EmbedderConfig,
)
from corpus_forge.daemon import main, run_daemon, setup_signal_handlers


class TestSetupSignalHandlers:
    """Tests for setup_signal_handlers function."""

    def test_setup_handlers_registers_sigint(self):
        """Test that SIGINT handler is registered."""
        with patch("corpus_forge.daemon.signal.signal") as mock_signal:
            setup_signal_handlers()
            assert mock_signal.call_count == 2
            calls = mock_signal.call_args_list
            assert calls[0][0][0] == 2  # SIGINT = 2

    def test_setup_handlers_registers_sigterm(self):
        """Test that SIGTERM handler is registered."""
        with patch("corpus_forge.daemon.signal.signal") as mock_signal:
            setup_signal_handlers()
            calls = mock_signal.call_args_list
            assert calls[1][0][0] == 15  # SIGTERM = 15

    def test_signal_handler_exits_gracefully(self):
        """Test that the signal handler calls sys.exit(0)."""
        with patch("corpus_forge.daemon.signal.signal") as mock_signal:
            setup_signal_handlers()
            handler = mock_signal.call_args_list[0][0][1]  # SIGINT handler

        # Call the handler directly
        with (
            patch("corpus_forge.daemon.sys.exit") as mock_exit,
            patch("corpus_forge.daemon.logging.info"),
        ):
            handler(2, None)
            mock_exit.assert_called_once_with(0)


class TestDaemonMain:
    """Tests for daemon main function."""

    def test_main_sets_up_signal_handlers(self):
        """Test that main calls setup_signal_handlers."""
        with (
            patch("corpus_forge.daemon.setup_signal_handlers") as mock_setup,
            patch("corpus_forge.daemon.ingest_main") as mock_ingest,
            patch("corpus_forge.daemon.logging.info"),
            patch("corpus_forge.daemon.sys.exit"),
        ):
            main()
            mock_setup.assert_called_once()
            mock_ingest.assert_called_once_with(once=False)

    def test_main_logs_start(self):
        """Test that main logs startup message."""
        with (
            patch("corpus_forge.daemon.setup_signal_handlers"),
            patch("corpus_forge.daemon.ingest_main") as mock_ingest,
            patch("corpus_forge.daemon.logging.info") as mock_info,
            patch("corpus_forge.daemon.sys.exit"),
        ):
            main()
            mock_info.assert_any_call("Starting corpus-forge daemon...")
            mock_ingest.assert_called_once()

    def test_main_exits_after_ingest(self):
        """Test that main exits after ingest_main returns."""
        with (
            patch("corpus_forge.daemon.setup_signal_handlers"),
            patch("corpus_forge.daemon.ingest_main"),
            patch("corpus_forge.daemon.logging.info"),
            patch("corpus_forge.daemon.sys.exit") as mock_exit,
        ):
            main()
            # Should exit at the end
            assert mock_exit.called


class TestDaemonSignalHandling:
    """Tests for signal handling in daemon mode."""

    def test_sigint_calls_exit_zero(self):
        """Test SIGINT triggers clean exit."""
        with patch("corpus_forge.daemon.signal.signal") as mock_signal:
            setup_signal_handlers()
            handler = mock_signal.call_args_list[0][0][1]  # SIGINT handler

        with (
            patch("corpus_forge.daemon.sys.exit") as mock_exit,
            patch("corpus_forge.daemon.logging.info"),
        ):
            handler(2, None)
            mock_exit.assert_called_once_with(0)

    def test_sigterm_calls_exit_zero(self):
        """Test SIGTERM triggers clean exit."""
        with patch("corpus_forge.daemon.signal.signal") as mock_signal:
            setup_signal_handlers()
            handler = mock_signal.call_args_list[1][0][1]  # SIGTERM handler

        with (
            patch("corpus_forge.daemon.sys.exit") as mock_exit,
            patch("corpus_forge.daemon.logging.info"),
        ):
            handler(15, None)
            mock_exit.assert_called_once_with(0)


class TestDaemonOrchestrator:
    """Tests for daemon sync engine orchestration via run_daemon."""

    def test_sync_enabled_starts_engine(self):
        """sync_enabled dataset — SyncEngine constructed and started."""
        config = MagicMock()
        dataset = MagicMock(sync_enabled=True)
        dataset.sources = [MagicMock()]
        config.datasets = [dataset]

        with patch("corpus_forge.daemon.SyncEngine") as mock_cls:
            run_daemon(config)
            mock_cls.assert_called_once()
            mock_cls.return_value.start.assert_called_once()

    def test_sync_disabled_skips_engine(self):
        """sync_enabled=False — no SyncEngine created."""
        config = MagicMock()
        dataset = MagicMock(sync_enabled=False)
        dataset.sources = [MagicMock()]
        config.datasets = [dataset]

        with patch("corpus_forge.daemon.SyncEngine") as mock_cls:
            run_daemon(config)
            mock_cls.assert_not_called()

    def test_multiple_sync_datasets_all_started(self):
        """Multiple sync-enabled datasets — all get engines."""
        config = MagicMock()
        d1 = MagicMock(sync_enabled=True, sources=[MagicMock()])
        d2 = MagicMock(sync_enabled=True, sources=[MagicMock()])
        config.datasets = [d1, d2]

        with patch("corpus_forge.daemon.SyncEngine") as mock_cls:
            run_daemon(config)
            assert mock_cls.call_count == 2
            assert mock_cls.return_value.start.call_count == 2

    def test_signal_stops_all_engines(self):
        """SIGINT/SIGTERM handler calls stop() on every engine."""
        config = MagicMock()
        d1 = MagicMock(sync_enabled=True, sources=[MagicMock()])
        config.datasets = [d1]

        engine = MagicMock()

        with (
            patch("corpus_forge.daemon.SyncEngine", return_value=engine),
            patch("corpus_forge.daemon.signal.signal") as mock_signal,
            patch("corpus_forge.daemon.sys.exit"),
        ):
            run_daemon(config)
            handler = mock_signal.call_args_list[0][0][1]
            handler(None, None)
            engine.stop.assert_called_once()

    def test_signal_does_not_stop_disabled_engines(self):
        """Only running engines are stopped on signal (disabled skipped)."""
        config = MagicMock()
        enabled = MagicMock(sync_enabled=True, sources=[MagicMock()])
        disabled = MagicMock(sync_enabled=False, sources=[MagicMock()])
        config.datasets = [enabled, disabled]

        engine = MagicMock()

        with (
            patch("corpus_forge.daemon.SyncEngine", return_value=engine),
            patch("corpus_forge.daemon.signal.signal") as mock_signal,
            patch("corpus_forge.daemon.sys.exit"),
        ):
            run_daemon(config)
            handler = mock_signal.call_args_list[0][0][1]
            handler(None, None)
            engine.stop.assert_called_once()


class TestDaemonRespectsConfigValidator:
    """B-14 regression: daemon path is gated by Config validator.

    The contract is: the validator runs at Config construction time.
    If a caller builds a Config with the offending sqlite+sync_enabled combo,
    the ValidationError fires before run_daemon is ever called — so run_daemon
    itself doesn't need a duplicate check.

    This class pins that contract. If the validator is ever moved to run_daemon
    instead of Config, these tests will catch the regression.
    """

    _SQLITE_DSN = "~/corpus.db"
    _PG_DSN = "postgresql://user:pass@localhost/db"
    _EXACT_ERROR_MSG = (
        "Cross-host sync requires the postgres backend; SQLite is single-host. "
        "Set sync_enabled = false or switch backend.kind to 'postgres'."
    )

    @staticmethod
    def _make_text_source() -> DatasetSourceConfig:
        return DatasetSourceConfig(
            plugin="markdown_vault",
            vault_root="/tmp/vault",
            chunker="markdown",
        )

    @staticmethod
    def _make_embedder() -> EmbedderConfig:
        return EmbedderConfig(
            name="test-embedder",
            provider="sentence_transformers",
            model_id="test/model",
            dimension=384,
        )

    @staticmethod
    def _minimal_daemon() -> DaemonConfig:
        return DaemonConfig(debounce_seconds=2.0, log_level="INFO", log_format="text")

    def test_config_construction_raises_before_run_daemon(self):
        """sqlite + sync_enabled=True raises ValidationError at Config() call, not in run_daemon.

        The test verifies that:
        1. Config(**bad_kwargs) raises ValidationError.
        2. run_daemon is never reached with a bad Config object.
        """
        bad_kwargs = {
            "backend": BackendConfig(kind="sqlite", dsn=self._SQLITE_DSN),
            "daemon": self._minimal_daemon(),
            "datasets": [
                DatasetConfig(
                    name="vault",
                    kind="text",
                    sync_enabled=True,
                    sources=[self._make_text_source()],
                )
            ],
            "embedders": [self._make_embedder()],
        }

        # The validator must fire during Config construction — before run_daemon.
        with pytest.raises(ValidationError) as exc_info:
            Config(**bad_kwargs)

        assert self._EXACT_ERROR_MSG in str(exc_info.value)

    def test_run_daemon_never_called_with_offending_config(self):
        """run_daemon body is never entered when Config construction raises.

        Confirms ValidationError propagates to the caller, not to run_daemon.
        """
        bad_kwargs = {
            "backend": BackendConfig(kind="sqlite", dsn=self._SQLITE_DSN),
            "daemon": self._minimal_daemon(),
            "datasets": [
                DatasetConfig(
                    name="vault",
                    kind="text",
                    sync_enabled=True,
                    sources=[self._make_text_source()],
                )
            ],
            "embedders": [self._make_embedder()],
        }

        with pytest.raises(ValidationError):
            Config(**bad_kwargs)

        # If we reach here, Config raised — run_daemon was never called.
        # This assertion is a static proof: the ValidationError above is the only
        # path out of Config(**bad_kwargs), so run_daemon can never receive that config.

    def test_valid_sqlite_config_reaches_run_daemon(self):
        """sqlite + sync_enabled=False does NOT raise — run_daemon can be reached.

        Confirms the validator is not over-restrictive: a valid sqlite config
        with sync_enabled=False should construct without error, allowing run_daemon
        to be called normally.
        """
        valid_config = Config(
            backend=BackendConfig(kind="sqlite", dsn=self._SQLITE_DSN),
            daemon=self._minimal_daemon(),
            datasets=[
                DatasetConfig(
                    name="vault",
                    kind="text",
                    sync_enabled=False,
                    sources=[self._make_text_source()],
                )
            ],
            embedders=[self._make_embedder()],
        )
        # Config constructed OK — validator did not fire
        assert valid_config.backend.kind == "sqlite"
        assert valid_config.datasets[0].sync_enabled is False

        # run_daemon should be callable without raising (sync_disabled → no engines started)
        with (
            patch("corpus_forge.daemon.SyncEngine") as mock_cls,
            patch("corpus_forge.daemon.signal.signal"),
        ):
            run_daemon(valid_config)
            mock_cls.assert_not_called()
