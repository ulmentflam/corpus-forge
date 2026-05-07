"""Daemon runner for corpus-forge."""

import logging
import signal
import sys
from typing import NoReturn

from .ingest import main as ingest_main


def setup_signal_handlers() -> None:
    """Setup signal handlers for graceful shutdown."""

    def signal_handler(signum, _frame):
        logging.info(f"Received signal {signum}, shutting down gracefully...")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)


def main() -> NoReturn:
    """Main entry point for daemon mode."""
    setup_signal_handlers()
    logging.info("Starting corpus-forge daemon...")

    # In daemon mode, we run continuous ingestion
    # For now, we'll just run one-shot and exit
    # In a real implementation, we'd set up watchdog observers and run indefinitely
    ingest_main(once=False)

    # This point should never be reached in a real daemon
    logging.info("Daemon stopped")
    sys.exit(0)


if __name__ == "__main__":
    main()
