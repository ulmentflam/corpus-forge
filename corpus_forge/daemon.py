"""Daemon runner for corpus-forge."""

import logging
import signal
import sys
import time
from typing import NoReturn

from .sync.engine import SyncEngine

logger = logging.getLogger(__name__)


def _get_any_backend(config):
    """Module-level shim around ``cli._get_any_backend``.

    Defined here as a wrapper (rather than imported at module load)
    to break the ``cli -> daemon -> cli`` circular import that the
    Typer command registration path sets up.  Tests patch
    ``corpus_forge.daemon._get_any_backend`` to stand in for the real
    backend factory; keeping a module-level definition makes that
    attribute resolvable at patch time.
    """

    from corpus_forge.cli import _get_any_backend as _impl  # noqa: PLC0415

    return _impl(config)


def _source_root(source):
    """Module-level shim around ``ignore_lifecycle._source_root``.

    Plugin-aware on-disk root resolution: returns ``Path`` for
    ``filesystem`` / ``markdown_vault`` sources, ``None`` for
    sources without a watchable filesystem root (zotero, chat
    plugins, etc.).  Defined here as a wrapper so tests can patch
    ``corpus_forge.daemon._source_root`` directly.
    """

    from corpus_forge.ignore_lifecycle import _source_root as _impl  # noqa: PLC0415

    return _impl(source)


def run_daemon(config) -> None:
    """Run daemon with sync engine orchestration.

    For each dataset with ``sync_enabled=True``:

    1. Resolve the backend row id via
       ``backend.find_dataset_id_by_name(name)``.  Skip with a WARNING
       if the name has never been ingested (no row → no id).
    2. Construct one ``SyncEngine`` per source with the resolved id.

    Registers SIGINT/SIGTERM handlers that stop all engines before
    exiting.  No-ops cleanly if the backend can't be reached at
    startup.
    """
    engines: list[SyncEngine] = []

    backend = _get_any_backend(config)
    if backend is None:
        logger.warning(
            "No reachable backend at daemon startup; skipping all sync engines"
        )
    else:
        for dataset in config.datasets:
            if not dataset.sync_enabled:
                continue

            dataset_id = backend.find_dataset_id_by_name(dataset.name)
            if dataset_id is None:
                logger.warning(
                    "Dataset %r is sync_enabled but not yet present in the "
                    "backend; run `corpus-forge ingest --once` first to "
                    "register it.  Skipping its sync engine.",
                    dataset.name,
                )
                continue

            for source_config in dataset.sources:
                # Resolve the per-plugin on-disk root.  ``filesystem``
                # uses ``source.root``; ``markdown_vault`` uses
                # ``source.vault_root``.  Skip sources that don't expose
                # a watchable FS root (zotero, chat plugins, etc.) —
                # SyncEngine watches files, not API-backed sources.
                root = _source_root(source_config)
                if root is None:
                    logger.info(
                        "Skipping sync engine for %s/%s — plugin has no "
                        "watchable filesystem root",
                        dataset.name,
                        source_config.plugin,
                    )
                    continue

                engine = SyncEngine(
                    dataset_id=dataset_id,
                    dataset_config=dataset,
                    source=source_config,
                    source_root=root,
                    backend=backend,
                    embedders=[],
                    host_id=config.host_id(),
                    daemon_config=config.daemon,
                )
                engine.start()
                engines.append(engine)
                logger.info(
                    "Started sync engine for %s/%s (dataset_id=%d, root=%s)",
                    dataset.name,
                    source_config.plugin,
                    dataset_id,
                    root,
                )

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
