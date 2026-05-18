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

    def _shutdown(signum, _frame):
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

    # Phase L Wave 5 — fire a WARNING for any embedder drift the daemon
    # detects on startup.  Daemons run unattended and don't prompt;
    # surfacing the drift in the rotating log gives operators a
    # greppable signal.
    _log_embedder_drift_warning()

    # In daemon mode, we run continuous ingestion
    # For now, we'll just run one-shot and exit
    # In a real implementation, we'd set up watchdog observers and run indefinitely
    ingest_main(once=False)

    # This point should never be reached in a real daemon
    logging.info("Daemon stopped")
    sys.exit(0)


def _log_embedder_drift_warning() -> None:
    """Best-effort WARNING log on embedder fingerprint drift (Wave 5).

    Lazy imports avoid a startup-time circular import with ``cli.py``
    (which already imports ``daemon`` via the Typer registration path).
    """

    from contextlib import suppress  # noqa: PLC0415

    drift_logger = logging.getLogger("corpus_forge.embedders.fingerprint")
    try:
        from corpus_forge.cli import _get_any_backend  # noqa: PLC0415
        from corpus_forge.config import Config  # noqa: PLC0415
        from corpus_forge.embedders.fingerprint import compare_active  # noqa: PLC0415
    except ImportError:
        return

    with suppress(Exception):
        config = Config.load()
        backend = _get_any_backend(config)
        if backend is None:
            return
        drifts = compare_active(config, backend)
        for d in drifts:
            drift_logger.warning(
                "Embedder drift detected: %s -> %s (%d chunks affected)",
                d.was_model_id,
                d.now_model_id,
                d.chunks_to_rerun,
            )


if __name__ == "__main__":
    main()
