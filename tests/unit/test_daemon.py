"""Unit tests for daemon module."""

from unittest.mock import MagicMock, patch

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
        with patch("corpus_forge.daemon.sys.exit") as mock_exit:
            with patch("corpus_forge.daemon.logging.info"):
                handler(2, None)
                mock_exit.assert_called_once_with(0)


class TestDaemonMain:
    """Tests for daemon main function."""

    def test_main_sets_up_signal_handlers(self):
        """Test that main calls setup_signal_handlers."""
        with patch("corpus_forge.daemon.setup_signal_handlers") as mock_setup:
            with patch("corpus_forge.daemon.ingest_main") as mock_ingest:
                with patch("corpus_forge.daemon.logging.info"):
                    with patch("corpus_forge.daemon.sys.exit"):
                        main()
                        mock_setup.assert_called_once()
                        mock_ingest.assert_called_once_with(once=False)

    def test_main_logs_start(self):
        """Test that main logs startup message."""
        with patch("corpus_forge.daemon.setup_signal_handlers"):
            with patch("corpus_forge.daemon.ingest_main") as mock_ingest:
                with patch("corpus_forge.daemon.logging.info") as mock_info:
                    with patch("corpus_forge.daemon.sys.exit"):
                        main()
                        mock_info.assert_any_call("Starting corpus-forge daemon...")
                        mock_ingest.assert_called_once()

    def test_main_exits_after_ingest(self):
        """Test that main exits after ingest_main returns."""
        with patch("corpus_forge.daemon.setup_signal_handlers"):
            with patch("corpus_forge.daemon.ingest_main"):
                with patch("corpus_forge.daemon.logging.info"):
                    with patch("corpus_forge.daemon.sys.exit") as mock_exit:
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

        with patch("corpus_forge.daemon.sys.exit") as mock_exit:
            with patch("corpus_forge.daemon.logging.info"):
                handler(2, None)
                mock_exit.assert_called_once_with(0)

    def test_sigterm_calls_exit_zero(self):
        """Test SIGTERM triggers clean exit."""
        with patch("corpus_forge.daemon.signal.signal") as mock_signal:
            setup_signal_handlers()
            handler = mock_signal.call_args_list[1][0][1]  # SIGTERM handler

        with patch("corpus_forge.daemon.sys.exit") as mock_exit:
            with patch("corpus_forge.daemon.logging.info"):
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

        with patch("corpus_forge.daemon.SyncEngine", return_value=engine):
            with patch("corpus_forge.daemon.signal.signal") as mock_signal:
                with patch("corpus_forge.daemon.sys.exit"):
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

        with patch("corpus_forge.daemon.SyncEngine", return_value=engine):
            with patch("corpus_forge.daemon.signal.signal") as mock_signal:
                with patch("corpus_forge.daemon.sys.exit"):
                    run_daemon(config)
                    handler = mock_signal.call_args_list[0][0][1]
                    handler(None, None)
                    engine.stop.assert_called_once()
