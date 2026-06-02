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


def _build_ignore_stack(source_root: Path, exclude_globs: tuple[str, ...]):
    """Compose the same three-layer ``IgnoreStack`` the scanner uses.

    Mirrors ``FilesystemSource.discover`` (corpus_forge/sources/filesystem.py)
    so the daemon's watchdog gate and the batch scanner agree on which
    files belong to the corpus.  Without this, files the managed
    ``.corpusignore`` block excludes at scan time (audio/video when
    Whisper is off, RAW images when image_extractor is off, archives,
    build artifacts) would still trigger the watchdog's ``read_text``
    path and crash on ``UnicodeDecodeError``.

    Returns ``None`` when none of the three layers contribute patterns
    — callers fall back to the existing ``fnmatch(exclude_globs)``
    path on a per-name basis.
    """
    from corpus_forge.ignore import IgnoreStack, load_global_ignore, load_local_ignore  # noqa: PLC0415
    from corpus_forge.sources.filesystem import _ignore_from_globs  # noqa: PLC0415

    sets = []
    try:
        global_set = load_global_ignore()
        if global_set.patterns:
            sets.append(global_set)
    except (OSError, ValueError) as exc:  # pragma: no cover — defensive
        logger.debug("PushPipeline: global ignore failed (%s) — skipping layer", exc)
    try:
        local_set = load_local_ignore(source_root)
        if local_set.patterns:
            sets.append(local_set)
    except (OSError, ValueError) as exc:  # pragma: no cover — defensive
        logger.debug("PushPipeline: local ignore failed (%s) — skipping layer", exc)

    glob_stack = _ignore_from_globs(list(exclude_globs), root=source_root)
    if glob_stack is not None:
        sets.extend(glob_stack.sets)

    return IgnoreStack(sets=tuple(sets)) if sets else None


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
        # ``.corpusignore`` matcher built at ``start()`` time from the
        # same three-layer composition the scanner uses (global +
        # local + ``exclude_globs``).  Keeps the watchdog's view of
        # "what's ingestible" in sync with ``ingest --once`` — without
        # this, audio/video/RAW-image patterns the managed
        # ``.corpusignore`` block excludes at scan time would still
        # trigger ``read_text`` here and crash on ``UnicodeDecodeError``.
        self._ignore_stack = None
        # Prefix that ``_compute_source_uri`` prepends to the
        # relative path so the URI matches what the source plugin
        # writes into ``corpus.documents`` (e.g.
        # ``filesystem://Workspace/`` for ``FilesystemSource``,
        # ``vault://Workspace/`` for ``MarkdownVaultSource``).  Empty
        # string preserves the legacy bare-relpath behaviour used by
        # the cross-host integration tests, which seed the documents
        # row with bare relpaths and never round-trip through
        # ``Source.parse``.
        self._source_uri_prefix: str = ""

    def _invoke_discovery_callback(self, path: Path) -> None:
        """Call ``discovery_callback`` and swallow + log any error.

        Centralised so both the new-file and content-change branches
        of ``_handle_change_inner`` share identical
        exception-handling behaviour.  A buggy callback must NEVER
        kill the watchdog Timer thread it runs on; the rotating log
        captures the traceback for triage instead.
        """
        try:
            self._discovery_callback(path)
        except Exception:  # noqa: BLE001 — defensive in a worker thread
            logger.exception(
                "Discovery callback raised for %s; new file not ingested",
                path,
            )

    def _compute_source_uri(self, path: Path) -> str:
        """Return source_uri matching what ``Source.parse`` writes.

        Production callers (run_daemon) set ``_source_uri_prefix`` to
        the plugin's URI scheme + root.name (e.g.
        ``filesystem://Workspace/``).  We append the relative path
        below to round-trip exactly the value
        ``FilesystemSource.parse`` / ``MarkdownVaultSource.parse``
        wrote into ``corpus.documents`` — that's what
        ``find_document`` looks up.  Without the prefix,
        ``find_document`` never matches and every modification fires
        the discovery callback (re-runs embedders) instead of the
        cheap revision-insert replication path.
        """
        if self._source_root is not None:
            try:
                rel = path.resolve().relative_to(self._source_root.resolve())
            except ValueError:
                pass
            else:
                rel_posix = rel.as_posix()
                if self._source_uri_prefix:
                    return f"{self._source_uri_prefix}{rel_posix}"
                return rel_posix
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

        # 2. Read text, compute hash, echo check.  Binary files that
        # slipped past ``_should_ignore`` (no extension match, no
        # ``.corpusignore`` entry) land here — ``read_text`` raises
        # ``UnicodeDecodeError``.  Treat as "not text-replicable" and
        # bail without surfacing a stack trace; the corresponding
        # binary-aware ingest path (extractors registry inside
        # ``ingest_one``) is reached only via the discovery branch,
        # which is taken below ONLY when no row exists yet — so a
        # known binary file modification is a genuine no-op here.
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            logger.debug(
                "handle_change: %s is not UTF-8 text; skipping replication path. "
                "Binary-aware ingest happens via the discovery callback on "
                "first sighting.",
                path,
            )
            return
        content_hash = chunk_content_hash(text)
        if self._echo_suppressor.was_just_written(path, content_hash):
            return

        # 3. Discovery branch — fire the callback for any *content
        # change*, whether the file is brand-new (no row in
        # ``corpus.documents``) OR an existing file whose stored
        # ``content_hash`` no longer matches what's on disk.
        #
        # ``find_document`` (lookup-only — *not* ``resolve_document``,
        # which auto-creates an empty stub for unknown URIs) tells us
        # which case we're in: ``None`` → new file; row with a
        # different content_hash → modification.  Either way, the
        # canonical update path is the discovery callback (which runs
        # ``ingest_one`` → ``backend.upsert_document``, the same path
        # ``corpus-forge ingest --once`` uses).  ``upsert_document``
        # carries the BUG-3 fix that preserves chunks + embeddings
        # whose content_hash didn't change, so the cost is bounded by
        # the actually-changed chunks.
        #
        # If the URIs already line up (``_source_uri_prefix`` set to
        # match ``Source.parse``'s output) AND content_hash equals
        # the stored value, we no-op cheaply — that's the common
        # case for mtime-touched-but-unmodified events (saves on
        # disk, IDE format-on-save).
        source_uri = self._compute_source_uri(path)
        if self._discovery_callback is not None:
            existing = self._backend.find_document(self._dataset_id, source_uri)
            if existing is None:
                logger.info(
                    "Discovery: new file %s — routing to ingest callback",
                    path,
                )
                self._invoke_discovery_callback(path)
                return
            if existing.get("content_hash") != content_hash:
                logger.info(
                    "Discovery: content changed for %s (was=%s now=%s) — "
                    "routing to ingest callback",
                    path,
                    str(existing.get("content_hash"))[:8],
                    content_hash[:8],
                )
                self._invoke_discovery_callback(path)
                # Replication trail (insert_revision) is preserved
                # below so multi-machine setups get the per-revision
                # cross-host history.  Callers without a multi-host
                # setup can ignore the rows.
            else:
                # No content change — nothing to do, regardless of
                # whether mtime moved.  Skip the lock + revision
                # query path entirely.
                return

        # 4. Lock + revision logic (replication path — cross-host
        # revision history).  Runs ONLY when ``discovery_callback``
        # is unset (legacy / integration-test path) OR content has
        # changed and we've already triggered the discovery
        # callback above.  Keeping the revision insert here makes the
        # PullPipeline's fast-forward replication available without
        # forcing every modification through the slow path.
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
        source_uri_prefix: str = "",
    ) -> None:
        self._source_root = source_root
        self._exclude_globs = tuple(exclude_globs) if exclude_globs is not None else ()
        self._source_uri_prefix = source_uri_prefix
        self._debounce_seconds = debounce_seconds
        self._ignore_stack = _build_ignore_stack(source_root, self._exclude_globs)
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
        # Consult the same ``IgnoreStack`` the scanner uses, so the
        # daemon's watchdog filters out audio/video, RAW images,
        # archives, build artifacts, etc. exactly the way
        # ``ingest --once`` does.  Skipped here means we never reach
        # ``read_text`` on these files — which is critical for binary
        # formats that would raise ``UnicodeDecodeError``.
        if self._ignore_stack is not None and self._source_root is not None:
            try:
                if self._ignore_stack.matches(
                    path, is_dir=False, scan_root=self._source_root
                ):
                    return True
            except ValueError:
                # Path outside the source root — let the rest of the
                # pipeline decide.  Should not happen under watchdog,
                # which only fires within the scheduled tree.
                pass
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
