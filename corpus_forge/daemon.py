"""Daemon runner for corpus-forge."""

import logging
import os
import signal
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from .backends.postgres import PostgresBackend
from .sync.engine import SyncEngine

# Never-patched class reference so the embed-drain loop's claim-path gate
# (``isinstance(backend, _RealPostgresBackend)``) isn't fooled by tests that
# patch the ``PostgresBackend`` constructor — mirrors
# ``corpus_forge.embed._RealPostgresBackend``.
_RealPostgresBackend = PostgresBackend

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
                "Discovery callback warmed up for dataset_id=%d / %s (embedders=%d)",
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
            logger.debug("Discovery: source.parse returned None for %s; skipping", path)
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


@dataclass
class _DrainLane:
    """One embedder lane the daemon embed-drain loop owns (RFC fleet-5)."""

    embedder: object
    embedder_id: int
    embedder_config: object
    ext_filter: "list[str] | None"
    transport: str
    device: str
    after_id: "int | None" = None


def _interruptible_sleep(stop_event, seconds: float, sleep) -> None:
    """Sleep ``seconds``, waking early if ``stop_event`` is set.

    Uses ``stop_event.wait`` when present (a real ``threading.Event``);
    falls back to the injected ``sleep`` for the cadence tests, whose
    stop-event exposes no ``wait``.
    """
    wait = getattr(stop_event, "wait", None)
    if callable(wait):
        wait(seconds)
    else:
        sleep(seconds)


def _build_drain_lanes(config, backend):
    """Resolve this host's owned embedder lanes for the drain loop.

    Returns ``(lanes, embedders)``: a ``_DrainLane`` per active embedder
    this host owns (``[embed] lanes`` pin, else all active), plus the
    parallel embedder list used for per-chunk routing. Each embedder is
    warmed + registered (its backend id is needed for claims). Mirrors
    ``embed.py``'s backfill setup.
    """
    from .admin.bench import resolve_device, resolve_transport  # noqa: PLC0415
    from .embedders.registry import register_from_config, registry  # noqa: PLC0415

    active = [ec for ec in config.embedders if ec.active]
    lanes_pin = list(getattr(config.embed, "lanes", []) or [])
    if lanes_pin:
        active = [ec for ec in active if ec.name in lanes_pin]

    lanes: list[_DrainLane] = []
    embedders: list = []
    for ec in active:
        try:
            embedder = register_from_config(registry, ec)
            embedder.warmup()
            embedder_id = backend.register_embedder(embedder)
        except Exception as exc:
            logger.warning("drain: embedder %r failed to load: %r", ec.name, exc)
            continue
        transport = resolve_transport(ec)
        ext_filter = list(embedder.extensions) if getattr(embedder, "extensions", None) else None
        lanes.append(
            _DrainLane(
                embedder=embedder,
                embedder_id=embedder_id,
                embedder_config=ec,
                ext_filter=ext_filter,
                transport=transport,
                device=resolve_device(transport),
            )
        )
        embedders.append(embedder)
    return lanes, embedders


def _drain_lane_batch(backend, lane, embedders, host_id, lease_ttl, _config):
    """Claim → embed → write → release one batch for ``lane`` (RFC fleet-5).

    Returns ``(claimed: bool, embedded: int)``. Reuses fleet-2's claim
    primitives verbatim: a ``FederationUnsupported`` backend (SQLite) is a
    no-op; an empty page resets the lane cursor; only chunks that ROUTE to
    this lane's embedder are embedded, but EVERY claimed id is released (in
    a ``finally``) — even when embedding raises (then re-raised) or the
    release itself raises ``FederationUnsupported`` (swallowed).
    """
    import contextlib  # noqa: PLC0415

    from .backends.base import FederationUnsupported  # noqa: PLC0415
    from .embedders import routing  # noqa: PLC0415

    try:
        page = backend.claim_chunks_for_embedding(
            lane.embedder_id,
            host_id,
            batch=1000,
            lease_ttl=lease_ttl,
            extensions=lane.ext_filter,
            after_id=lane.after_id,
        )
    except FederationUnsupported:
        return (False, 0)

    if not page:
        lane.after_id = None
        return (False, 0)

    claimed_ids = [cid for cid, _text, _uri in page]
    try:
        routed_ids: list[int] = []
        routed_texts: list[str] = []
        for cid, text, uri in page:
            if routing.route_for(uri, embedders) is lane.embedder:
                routed_ids.append(cid)
                routed_texts.append(text)
        embedded = 0
        if routed_texts:
            vectors = lane.embedder.encode(routed_texts)
            backend.write_embeddings(lane.embedder_id, list(zip(routed_ids, vectors, strict=True)))
            embedded = len(routed_ids)
    finally:
        # Release ALL claimed ids (routed-in or not) so a chunk another lane
        # owns is freed immediately; release-in-finally frees the page even
        # when encode raised. A FederationUnsupported release is swallowed.
        with contextlib.suppress(FederationUnsupported):
            backend.release_claims(lane.embedder_id, host_id, claimed_ids)

    lane.after_id = max(claimed_ids)
    return (True, embedded)


def run_embed_drain_loop(config, backend, *, stop_event, sleep=time.sleep) -> None:
    """Continuously drain the embed backlog via fleet-2 claims (RFC fleet-5).

    Hosted in the supervised daemon when ``[service] embed_drain = true``.
    A no-op on a non-Postgres backend (SQLite raises ``FederationUnsupported``
    on the claim primitive — today's ingest-time embedding is untouched).
    Sweeps every owned lane each iteration; when EVERY lane returns an empty
    batch, backs off with bounded exponential cadence
    (``[embed] drain_idle_min`` → ``drain_idle_max``); any non-empty batch
    resets the backoff to the minimum. Runs until ``stop_event`` is set.
    """
    if not isinstance(backend, _RealPostgresBackend):
        return

    lanes, embedders = _build_drain_lanes(config, backend)
    if not lanes:
        return

    host_id = config.host_id()
    lease_ttl = config.embed.claim_lease_ttl
    idle_min = float(config.embed.drain_idle_min)
    idle_max = float(config.embed.drain_idle_max)
    backoff = idle_min

    while not stop_event.is_set():
        did_work = False
        for lane in lanes:
            claimed, _embedded = _drain_lane_batch(
                backend, lane, embedders, host_id, lease_ttl, config
            )
            if claimed:
                did_work = True
        if did_work:
            backoff = idle_min
            continue
        _interruptible_sleep(stop_event, backoff, sleep)
        backoff = min(backoff * 2, idle_max)


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
    # Fleet telemetry (rfc-fleet-1): record this host + its available
    # models once at daemon startup.  Failure-isolated inside the helper,
    # and a no-op when the backend is unreachable.
    from corpus_forge.telemetry_registry import heartbeat as _telemetry_heartbeat  # noqa: PLC0415

    _telemetry_heartbeat(backend, config)
    if backend is None:
        logger.warning("No reachable backend at daemon startup; skipping all sync engines")
    elif not config.service.ingest_watch:
        # RFC fleet-5 — a pure-drain host ([service] ingest_watch=false)
        # runs only the embed-drain loop below; no source watchers.
        logger.info(
            "[service] ingest_watch=false — not starting source watchers "
            "(this host runs the embed-drain loop only)."
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
                        "Skipping sync engine for %s/%s — plugin has no watchable filesystem root",
                        dataset.name,
                        source_config.plugin,
                    )
                    continue

                discovery_cb = _build_discovery_callback(config, backend, dataset_id, source_config)
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

    # RFC fleet-5 — embed-drain loop in a daemon thread, gated on
    # ``[service] embed_drain``. ``is True`` (not truthiness) so a MagicMock
    # service in tests doesn't spuriously start it; needs a live backend.
    drain_stop = threading.Event()
    drain_thread: threading.Thread | None = None
    if config.service.embed_drain is True and backend is not None:
        drain_thread = threading.Thread(
            target=run_embed_drain_loop,
            kwargs={"config": config, "backend": backend, "stop_event": drain_stop},
            daemon=True,
        )
        drain_thread.start()
        logger.info("Started embed-drain loop ([service] embed_drain=true)")

    def _shutdown(signum, _frame):
        # Signal the drain loop to stop first so it stops claiming while the
        # engines wind down; joined below after the engines are stopped.
        drain_stop.set()
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

        # Join the embed-drain thread (RFC fleet-5) — it was signalled to
        # stop at the top of this handler; give it a bounded wait so a
        # mid-batch claim can release before exit.
        if drain_thread is not None:
            try:
                drain_thread.join(timeout=10.0)
            except Exception:
                logger.exception("daemon: embed-drain thread join raised during shutdown")

        # Use ``os._exit`` instead of ``sys.exit`` so the daemon
        # actually terminates promptly.  ``sys.exit`` raises
        # ``SystemExit`` which unwinds through Python interpreter
        # finalisation — including the wait on every non-daemon thread.
        # Lazy-loaded llama-cpp embedders + watchdog Observer
        # internals spawn native + Python threads we don't own, and
        # waiting on them blew past the 30 s SIGTERM→SIGKILL grace
        # window in ``corpus-forge service stop`` (12 sources x 10 s
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
        import contextlib  # noqa: PLC0415

        for handler in logging.getLogger("corpus_forge").handlers:
            with contextlib.suppress(Exception):
                handler.flush()
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

    # RFC fleet-3 item 4 — federation drift detection. Construct the
    # checker ONCE, and only when ``[federation] enabled = true`` AND the
    # backend is postgres. With the default (``enabled=False``) this
    # returns ``None`` and the blocking loop below never touches shared
    # config — the hard backcompat bar.
    drift_checker = _make_federation_drift_checker(config)

    # Block until ``run_daemon``'s signal handler exits the process. When
    # a drift checker exists, wake on its check interval and run the
    # throttled, best-effort check each wakeup; otherwise sleep long.
    sleep_interval = (
        config.federation.drift_check_interval_s if drift_checker is not None else 3600.0
    )
    while True:
        time.sleep(sleep_interval)
        if drift_checker is not None:
            drift_checker()

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


def _make_federation_drift_checker(config) -> Callable[[], None] | None:
    """Return a throttled, best-effort federation drift checker, or ``None``.

    RFC ``rfc-fleet-3-federated-config-and-setup`` item 4. The returned
    callable is meant to be invoked once per daemon loop wakeup. It
    compares the corpus's *published* shared-config version against this
    host's *last-pulled* version and logs ONE WARNING when the corpus is
    ahead — pointing the operator at ``corpus-forge config pull``. It
    NEVER applies anything (RFC non-goal: "No auto-apply ... no
    background config mutation, ever").

    Returns ``None`` — so no checker runs at all — unless BOTH:

    - ``config.federation.enabled`` is True, AND
    - the configured backend kind is ``postgres`` (federation requires
      the shared Postgres; SQLite is single-host).

    With the default (``enabled=False``) this is ``None`` and the daemon
    reads no shared config — the hard backcompat bar.

    Throttling: the returned closure holds a ``last_checked`` timestamp
    and skips the DB read until ``drift_check_interval_s`` has elapsed
    since the previous *successful or attempted* check. The first call
    always checks (``last_checked`` starts at ``-inf``).

    Failure isolation: EVERY failure mode — :class:`FederationUnsupported`
    (backend somehow can't federate), an unreachable backend, a
    state-file read problem, anything — is swallowed at DEBUG. The check
    must never crash or slow the daemon.
    """

    if not getattr(config.federation, "enabled", False):
        return None
    if getattr(config.backend, "kind", "postgres") != "postgres":
        # Federation requires the shared Postgres backend (RFC non-goal:
        # no SQLite support). Don't even build the checker.
        logger.debug(
            "federation.enabled=true but backend.kind=%r; drift check disabled "
            "(federation requires the postgres backend)",
            getattr(config.backend, "kind", None),
        )
        return None

    interval = float(config.federation.drift_check_interval_s)
    # Mutable cell for the last-checked timestamp; ``-inf`` forces the
    # first invocation to actually check.
    state = {"last_checked": float("-inf")}

    def _check() -> None:
        now = time.monotonic()
        if now - state["last_checked"] < interval:
            return
        state["last_checked"] = now
        # Lazy imports: keep daemon startup light and avoid the
        # cli -> daemon -> cli import cycle. ``federation`` is the admin
        # helper module (NOT this daemon) — reuse its state bookkeeping.
        from contextlib import suppress  # noqa: PLC0415

        with suppress(Exception):
            from corpus_forge.admin.federation import (  # noqa: PLC0415
                read_last_pulled_version,
            )

            backend = _get_any_backend(config)
            if backend is None:
                logger.debug("federation drift check: no reachable backend; skipping")
                return
            try:
                fetched = backend.get_shared_config()
            except Exception as exc:  # FederationUnsupported, conn errors, ...
                logger.debug("federation drift check: get_shared_config failed (%r)", exc)
                return
            if fetched is None:
                # Nothing published yet — no drift possible.
                return
            published_version = int(fetched[0])
            last_pulled = read_last_pulled_version()
            if published_version > last_pulled:
                logger.warning(
                    "shared config v%s is published but this host last pulled v%s "
                    "— run `corpus-forge config pull` to review (then --apply)",
                    published_version,
                    last_pulled,
                )

    return _check


if __name__ == "__main__":
    main()
