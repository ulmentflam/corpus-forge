"""Daemon runner for corpus-forge."""

import logging
import signal
import sys
from typing import NoReturn

from .ingest import main as ingest_main
from .sync.engine import SyncEngine

logger = logging.getLogger(__name__)


def run_daemon(config) -> None:
    """Run daemon with sync engine orchestration.

    For each dataset with sync_enabled=True, constructs a SyncEngine
    and starts it. Registers SIGINT/SIGTERM handlers that stop all
    engines before exiting.
    """
    engines: list[SyncEngine] = []

    for dataset in config.datasets:
        if not dataset.sync_enabled:
            continue

        for source_config in dataset.sources:
            engine = SyncEngine(
                dataset_config=dataset,
                source=source_config,
                backend=config.backend,
                embedders=[],
                host_id=config.host_id(),
                daemon_config=config.daemon,
            )
            engine.start()
            engines.append(engine)
            logger.info(f"Started sync engine for {dataset.name}/{source_config.plugin}")

    def _shutdown(signum, frame):
        logger.info(f"Received signal {signum}, stopping {len(engines)} engine(s)")
        for engine in engines:
            engine.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)


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
