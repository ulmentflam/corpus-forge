"""Unit tests for daemon module."""

from unittest.mock import MagicMock, patch

import pytest

from corpus_forge.daemon import main, setup_signal_handlers

pytestmark = pytest.mark.integration


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
        with patch("corpus_forge.daemon.signal.signal"):
            setup_signal_handlers()
            # Get the handler from the signal module
            import signal
            handler = signal.getsignal(signal.SIGINT)

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
            handler = signal.getsignal(signal.SIGINT)

        with patch("corpus_forge.daemon.sys.exit") as mock_exit:
            with patch("corpus_forge.daemon.logging.info"):
                handler(signal.SIGINT, None)
                mock_exit.assert_called_once_with(0)

    def test_sigterm_calls_exit_zero(self):
        """Test SIGTERM triggers clean exit."""
        with patch("corpus_forge.daemon.signal.signal") as mock_signal:
            setup_signal_handlers()
            handler = signal.getsignal(signal.SIGTERM)

        with patch("corpus_forge.daemon.sys.exit") as mock_exit:
            with patch("corpus_forge.daemon.logging.info"):
                handler(signal.SIGTERM, None)
                mock_exit.assert_called_once_with(0)
