"""Daemon runner for corpus-forge."""

import logging
import signal
import sys
import time
from typing import NoReturn

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
    """Main entry point for daemon mode.

    Loads the active ``Config``, hands it to ``run_daemon`` (which
    spawns one PushPipeline/PullPipeline pair per ``sync_enabled``
    source and registers SIGINT/SIGTERM handlers), then blocks
    forever.  The signal handler raises ``SystemExit`` which
    propagates up through ``time.sleep`` and out of ``main``.
    """
    from corpus_forge.config import Config  # noqa: PLC0415
    from corpus_forge.logging_config import init_logging  # noqa: PLC0415

    # Route ``corpus_forge.*`` logging to the rotating ``daemon.log``
    # file under ``$CACHE/corpus-forge/logs/``.  Without this the
    # daemon's records would land on stderr — which is redirected to
    # ``/dev/null`` under the LaunchAgent / systemd unit — making the
    # process invisible to ``corpus-forge service status`` and to
    # operators tailing the log.
    init_logging("daemon", verbose=False, quiet=False)

    logger.info("Starting corpus-forge daemon...")

    # Phase L Wave 5 — fire a WARNING for any embedder drift the daemon
    # detects on startup.  Daemons run unattended and don't prompt;
    # surfacing the drift in the rotating log gives operators a
    # greppable signal.
    _log_embedder_drift_warning()

    config = Config.load()
    run_daemon(config)

    # Block until ``run_daemon``'s signal handler exits the process.
    while True:
        time.sleep(3600)

    # Unreachable — kept for type-checker satisfaction (``NoReturn``).
    logger.info("Daemon stopped")
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
