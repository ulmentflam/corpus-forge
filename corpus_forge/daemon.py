"""Daemon runner for corpus-forge."""

import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Callable, NoReturn

from .sync.engine import SyncEngine


# Module-level alias for the hard-exit primitive used by ``_shutdown``.
# Tests patch ``corpus_forge.daemon._exit_hard`` to avoid terminating
# the pytest process when they invoke the signal handler directly.
_exit_hard = os._exit

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


# Per-plugin URI scheme used by ``Source.parse`` when it writes a
# document into ``corpus.documents``.  Mirrors
# ``FilesystemSource.parse`` (``filesystem://``) and
# ``MarkdownVaultSource.parse`` (``vault://``).  When a new
# filesystem-watchable source plugin lands, its scheme goes here so
# ``PushPipeline`` keeps round-tripping the right URI.
_PUSH_SOURCE_URI_SCHEMES: dict[str, str] = {
    "filesystem": "filesystem",
    "markdown_vault": "vault",
}


def _source_uri_prefix_for_push(source_config, root: Path) -> str:
    """Build the prefix ``PushPipeline._compute_source_uri`` prepends.

    Returns e.g. ``"filesystem://Workspace/"`` so the URI for a path
    like ``<root>/notes/foo.md`` becomes
    ``"filesystem://Workspace/notes/foo.md"`` — exactly what
    ``FilesystemSource.parse`` writes into ``corpus.documents``.
    Empty string for unrecognised plugins (rare; the watchable-plugin
    allow-list in ``ignore_lifecycle._source_root`` would have already
    skipped the source).
    """
    scheme = _PUSH_SOURCE_URI_SCHEMES.get(source_config.plugin, "")
    if not scheme:
        return ""
    return f"{scheme}://{root.name}/"


def _build_discovery_callback(
    config, backend, dataset_id: int, source_config
) -> Callable[[Path], None]:
    """Return a per-source callback that ingests one brand-new file.

    The callback is wired into ``PushPipeline`` via ``SyncEngine``.
    When the watchdog handler sees a path with no row in
    ``corpus.documents``, it hands the path here; we extract it
    through the source plugin, chunk it, upsert it, and embed it —
    the same path ``corpus-forge ingest --once`` walks, but for a
    single file.

    The expensive bits (source instantiation, chunker dispatcher,
    embedder loading — qwen3-4096 is ~4 GB resident) are deferred
    until the first new file actually appears so an idle daemon
    stays light.  Re-init is guarded by a lock so concurrent
    watchdog events don't race.
    """

    state: dict = {}
    state_lock = threading.Lock()

    def _ensure_pipeline_state() -> None:
        if "source" in state:
            return
        with state_lock:
            if "source" in state:  # double-checked under lock
                return
            # Lazy imports: ``corpus_forge.ingest`` pulls in a lot
            # of optional plugins; we don't want to pay that cost
            # at daemon startup if no new files appear.
            from corpus_forge.ingest import (  # noqa: PLC0415
                _instantiate_source,
                get_active_embedders,
                get_chunker_for_source,
            )

            source = _instantiate_source(source_config, config=config)
            state["source"] = source
            state["chunker"] = get_chunker_for_source(source, config)
            state["embedders"] = get_active_embedders(config)
            logger.info(
                "Discovery callback warmed up for dataset_id=%d / %s "
                "(embedders=%d)",
                dataset_id,
                getattr(source_config, "plugin", "?"),
                len(state["embedders"]),
            )

    def _on_new_file(path: Path) -> None:
        _ensure_pipeline_state()
        from corpus_forge.backends.base import IngestRunInProgressError  # noqa: PLC0415
        from corpus_forge.ingest import ingest_one  # noqa: PLC0415

        source = state["source"]
        chunker = state["chunker"]
        embedders = state["embedders"]
        raw = source.parse(Path(path))
        if raw is None:
            logger.debug(
                "Discovery: source.parse returned None for %s; skipping", path
            )
            return
        try:
            ingest_one(
                backend=backend,
                raw=raw,
                chunker=chunker,
                embedders=embedders,
                dataset_id=dataset_id,
                source=source,
            )
        except IngestRunInProgressError:
            # Per-source lock contention — another in-flight discovery
            # callback (or ``corpus-forge ingest --once``) is already
            # processing this file.  Benign for actively-edited files
            # (Obsidian's autosave fires watchdog events rapidly); the
            # next debounced event will succeed.  Log at DEBUG so the
            # rotating log doesn't spam ERROR-level noise on every
            # save burst.
            logger.debug(
                "Discovery: lock_source contention on %s — another "
                "ingest run holds the lock; next event will retry",
                path,
            )
            return
        logger.info(
            "Discovery: ingested new file %s into dataset_id=%d",
            path,
            dataset_id,
        )

    return _on_new_file


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

                discovery_cb = _build_discovery_callback(
                    config, backend, dataset_id, source_config
                )
                # URI prefix MUST match what ``Source.parse`` writes
                # into ``corpus.documents.source_uri`` so PushPipeline's
                # ``find_document`` lookup hits.  Without this, every
                # file modification falls through to the discovery
                # callback again and re-runs the (~2 min) embedder
                # pipeline instead of taking the cheap revision-insert
                # replication path.  Mapping is per-plugin and mirrors
                # the constants in ``corpus_forge.sources.filesystem``
                # / ``corpus_forge.sources.markdown_vault``.
                source_uri_prefix = _source_uri_prefix_for_push(source_config, root)
                engine = SyncEngine(
                    dataset_id=dataset_id,
                    dataset_config=dataset,
                    source=source_config,
                    source_root=root,
                    backend=backend,
                    embedders=[],
                    host_id=config.host_id(),
                    daemon_config=config.daemon,
                    discovery_callback=discovery_cb,
                    source_uri_prefix=source_uri_prefix,
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
        # Parallelise ``engine.stop()`` so the total time is bounded
        # by the slowest engine, not the sum.  Each PullPipeline.stop
        # can block up to ~10s on its thread.join; with 12 engines a
        # serial loop hits ~120s and easily blows past the 30s
        # SIGTERM→SIGKILL grace window in ``service stop``.
        import concurrent.futures  # noqa: PLC0415

        from corpus_forge.admin.foreground import clear_pid  # noqa: PLC0415

        logger.info(
            "Received signal %s, stopping %d engine(s) in parallel",
            signum,
            len(engines),
        )
        if engines:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=len(engines), thread_name_prefix="cf-shutdown"
            ) as pool:
                # ``map`` blocks on context-manager exit until every
                # task finishes (or raises).  Wrap each stop in a
                # try/except so one slow engine can't mask others —
                # we want all of them to TRY to stop even if some hit
                # an error.
                def _stop_one(engine):
                    try:
                        engine.stop()
                    except Exception:
                        logger.exception("engine.stop() raised during shutdown")

                list(pool.map(_stop_one, engines))

        # Use ``os._exit`` instead of ``sys.exit`` so the daemon
        # actually terminates promptly.  ``sys.exit`` raises
        # ``SystemExit`` which unwinds through Python interpreter
        # finalisation — including the wait on every non-daemon thread.
        # Lazy-loaded llama-cpp embedders + watchdog Observer
        # internals spawn native + Python threads we don't own, and
        # waiting on them blew past the 30 s SIGTERM→SIGKILL grace
        # window in ``corpus-forge service stop`` (12 sources × 10 s
        # PullPipeline.join).  The daemon's authoritative state lives
        # in Postgres + the rotating log file (both already durable);
        # there is nothing to flush, so a hard exit is appropriate.
        # Clear the pid file first so ``service status`` doesn't
        # report a phantom process after shutdown — that's the one
        # bit of state ``start_daemon_foreground``'s ``finally``
        # block would normally cover.
        try:
            clear_pid("daemon")
        except Exception:
            logger.exception("daemon: failed to clear pid file on shutdown")
        logger.info("daemon: shutdown complete, exiting hard")
        # Force-flush logs so the operator's last view in daemon.log
        # is the shutdown ack, not whatever was buffered when the
        # OS started tearing down.
        for handler in logging.getLogger("corpus_forge").handlers:
            try:
                handler.flush()
            except Exception:
                pass
        _exit_hard(0)

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
