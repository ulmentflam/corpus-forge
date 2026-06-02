from __future__ import annotations

import fnmatch
import logging
import threading
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from watchdog import observers
from watchdog.events import FileSystemEventHandler

from corpus_forge.identity import chunk_content_hash
from corpus_forge.sync.cloud import detect_cloud_provider  # noqa: F401
from corpus_forge.sync.conflicts import conflict_filename, is_cloud_duplicate

# Phase L Wave 4 — module-level logger so the push pipeline writes to
# the documented ``corpus_forge.sync.push`` taxonomy bucket. Push is
# observer-driven (no bounded loop) so we ship lifecycle bookends here
# instead of a progress bar.
logger = logging.getLogger(__name__)


class _DebouncedHandler(FileSystemEventHandler):
    def __init__(self, pipeline: PushPipeline, debounce_seconds: float) -> None:
        super().__init__()
        self.pipeline = pipeline
        self.debounce_seconds = debounce_seconds
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def on_modified(self, event) -> None:
        if event.is_directory:
            return
        if self.pipeline._should_ignore(Path(event.src_path)):
            return
        with self._lock:
            timer = self._timers.get(event.src_path)
            if timer:
                timer.cancel()
            timer = threading.Timer(
                self.debounce_seconds,
                self.pipeline.handle_change,
                args=[Path(event.src_path)],
            )
            timer.daemon = True
            timer.start()
            self._timers[event.src_path] = timer

    def on_created(self, event) -> None:
        self.on_modified(event)

    def shutdown(self) -> None:
        with self._lock:
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()


class PushPipeline:
    def __init__(
        self,
        backend,
        dataset_id: int,
        echo_suppressor,
        host_id: str,
        discovery_callback=None,
    ) -> None:
        self._backend = backend
        self._dataset_id = dataset_id
        self._echo_suppressor = echo_suppressor
        self._host_id = host_id
        # Brand-new files (no row in ``corpus.documents``) take the
        # discovery path: ``handle_change`` invokes this callable with
        # the path so the caller (typically ``run_daemon``) can route
        # it through the per-file ingest pipeline.  ``None`` keeps
        # the legacy replication-only behavior — new files are
        # silently dropped, as they were before this hook existed.
        self._discovery_callback = discovery_callback
        self._mtime_cache: dict[str, float] = {}
        self._observer: observers.Observer | None = None  # pyrefly: ignore[unsupported-operation]  # watchdog's Observer is a callable factory; runtime annotation works
        self._handler: _DebouncedHandler | None = None
        # Set by start(); None when pipeline is used without watchdog (direct calls)
        self._source_root: Path | None = None
        self._exclude_globs: tuple[str, ...] = ()

    def _compute_source_uri(self, path: Path) -> str:
        """Return source_uri relative to source_root if known, else absolute."""
        if self._source_root is not None:
            try:
                return str(path.resolve().relative_to(self._source_root.resolve()))
            except ValueError:
                pass
        return str(path.resolve())

    def handle_change(self, path: Path) -> None:
        # Wrap the whole body so any exception surfaces in the rotating
        # log file.  Without this, the ``threading.Timer`` in the
        # debounce handler swallows tracebacks to stderr — which the
        # LaunchAgent / systemd unit redirects to ``/dev/null``, making
        # watchdog-stage crashes invisible to operators.
        try:
            self._handle_change_inner(path)
        except Exception:  # noqa: BLE001 — defensive in a worker thread
            logger.exception("handle_change raised for %s", path)

    def _handle_change_inner(self, path: Path) -> None:
        logger.debug("handle_change: %s", path)

        # Cloud-duplicate early exit (BUG-7 fix: wire in before main logic)
        if self._handle_cloud_duplicate(path):
            return

        resolved = str(path.resolve())

        # 1. mtime pre-filter
        try:
            current_mtime = path.stat().st_mtime
        except OSError:
            return
        cached_mtime = self._mtime_cache.get(resolved)
        if cached_mtime is not None and current_mtime <= cached_mtime:
            return
        self._mtime_cache[resolved] = current_mtime

        # 2. Read text, compute hash, echo check
        text = path.read_text(encoding="utf-8")
        content_hash = chunk_content_hash(text)
        if self._echo_suppressor.was_just_written(path, content_hash):
            return

        # 3. Discovery branch — fire the callback BEFORE the replication
        # path's ``resolve_document`` call.  ``resolve_document`` is a
        # lookup-OR-CREATE (see ``PostgresBackend.resolve_document``)
        # which silently inserts an empty stub for any unknown
        # ``source_uri`` and returns the stub.  That makes a "doc is
        # None" check downstream unreachable for brand-new files.
        # We instead use ``find_document`` (lookup-only) up front: a
        # ``None`` here means there is no row at all, so hand the path
        # to the discovery callback (the daemon-supplied ingest hook)
        # and skip the replication path entirely.  ingest_one's own
        # ``upsert_document`` will create the row with chunks + the
        # right content_hash; the next push event will see it via
        # ``find_document`` and take the replication path.
        source_uri = self._compute_source_uri(path)
        if self._discovery_callback is not None:
            existing = self._backend.find_document(self._dataset_id, source_uri)
            if existing is None:
                logger.info(
                    "Discovery: new file %s — routing to ingest callback",
                    path,
                )
                try:
                    self._discovery_callback(path)
                except Exception:  # noqa: BLE001 — defensive in a worker thread
                    logger.exception(
                        "Discovery callback raised for %s; new file not ingested",
                        path,
                    )
                return

        # 4. Lock + revision logic (replication path)
        with self._backend.lock_source(source_uri):
            doc = self._backend.resolve_document(self._dataset_id, source_uri)
            if doc is None:
                # ``resolve_document`` only returns None for empty
                # ``source_uri`` — keep the guard as a safety net.
                return
            latest = self._backend.latest_revision(doc["id"])

            if latest is not None:
                if latest["content_hash"] == content_hash:
                    return
                parent_id = latest["id"]
            else:
                if doc.get("content_hash") == content_hash:
                    return
                parent_id = None

            self._backend.insert_revision(
                document_id=doc["id"],
                source_uri=source_uri,
                content_hash=content_hash,
                text=text,
                parent_revision_id=parent_id,
                author_host=self._host_id,
                is_tombstone=False,
            )
            # Update the documents row to reflect the new content
            self._backend._execute(
                """
                UPDATE corpus.documents
                SET content_hash = %s, text = %s, modified_at = NOW()
                WHERE id = %s
                """,
                (content_hash, text, doc["id"]),
            )
            # Clear tombstone if this document was previously deleted
            self._backend.clear_tombstone(doc["id"])

    def start(
        self,
        source_root: Path,
        *,
        exclude_globs: tuple[str, ...] | list[str] | None = None,
        debounce_seconds: float = 1.0,
    ) -> None:
        self._source_root = source_root
        self._exclude_globs = tuple(exclude_globs) if exclude_globs is not None else ()
        self._debounce_seconds = debounce_seconds
        self._handler = _DebouncedHandler(self, debounce_seconds)
        self._observer = observers.Observer()
        self._observer.schedule(self._handler, str(source_root), recursive=True)
        self._observer.start()
        logger.info(
            "Push start: dataset_id=%d root=%s debounce=%.1fs",
            self._dataset_id,
            source_root,
            debounce_seconds,
        )

    def stop(self) -> None:
        if self._observer is None:
            return
        if self._handler is not None:
            self._handler.shutdown()
        self._observer.stop()
        self._observer.join()
        self._observer = None
        logger.info("Push stop: dataset_id=%d", self._dataset_id)

    def _should_ignore(self, path: Path) -> bool:
        name = path.name
        if path.is_dir():
            return True
        if name.startswith("."):
            return True
        if name.endswith(".icloud"):
            return True
        for pattern in self._exclude_globs:
            if fnmatch.fnmatch(name, pattern):
                return True
        try:
            if path.stat().st_blocks == 0:
                return True
        except OSError:
            pass
        return False

    def _handle_cloud_duplicate(self, path: Path) -> bool:
        matched, provider, canonical_path = is_cloud_duplicate(path)
        if not matched or canonical_path is None or not canonical_path.exists():
            return False

        local_hash = chunk_content_hash(path.read_text(encoding="utf-8"))
        canonical_hash = chunk_content_hash(canonical_path.read_text(encoding="utf-8"))

        if local_hash == canonical_hash:
            path.unlink()
            return True

        text = path.read_text(encoding="utf-8")
        ts = datetime.now(UTC)
        conflict = conflict_filename(canonical_path, host=self._host_id, ts=ts, provider=provider)
        path.rename(conflict)
        source_uri = self._compute_source_uri(conflict)
        with self._backend.lock_source(source_uri):
            doc = self._backend.resolve_document(self._dataset_id, source_uri)
            if doc is None:
                return True
            latest = self._backend.latest_revision(doc["id"])
            self._backend.insert_revision(
                document_id=doc["id"],
                source_uri=source_uri,
                content_hash=local_hash,
                text=text,
                parent_revision_id=latest["id"] if latest else None,
                author_host=self._host_id,
                is_tombstone=False,
            )
            self._backend._execute(
                """
                UPDATE corpus.documents
                SET content_hash = %s, text = %s, modified_at = NOW()
                WHERE id = %s
                """,
                (local_hash, text, doc["id"]),
            )
        return True

    def handle_delete(self, path: Path) -> None:
        source_uri = self._compute_source_uri(path)
        with self._backend.lock_source(source_uri):
            icloud_sibling = path.with_name(path.name + ".icloud")
            if icloud_sibling.exists():
                return

            # Only tombstone documents that are actually tracked.  Use
            # find_document (find-only, no stub creation) so that ghost files
            # (never ingested) are cleanly skipped.
            doc = self._backend.find_document(self._dataset_id, source_uri)
            if doc is None:
                return
            latest = self._backend.latest_revision(doc["id"])
            self._backend.insert_revision(
                document_id=doc["id"],
                source_uri=source_uri,
                content_hash=sha256(b"").hexdigest(),
                text="",
                parent_revision_id=latest["id"] if latest else None,
                author_host=self._host_id,
                is_tombstone=True,
            )
            self._backend.set_tombstone(doc["id"])
