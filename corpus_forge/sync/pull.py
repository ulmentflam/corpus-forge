"""Pull pipeline — fast-forward remote revisions to local filesystem."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

from datetime import UTC

from corpus_forge.identity import file_content_hash
from corpus_forge.sync.conflicts import conflict_filename
from corpus_forge.sync.fs import atomic_write_text, move_to_trash


class PullPipeline:
    def __init__(
        self,
        backend,
        dataset_id: int,
        source_root: Path,
        echo_suppressor,
        host_id: str,
        trash_root: Path | None = None,
    ) -> None:
        self._backend = backend
        self._dataset_id = dataset_id
        self._source_root = source_root
        self._echo_suppressor = echo_suppressor
        self._host_id = host_id
        self._trash_root = trash_root or Path("~/.local/share/corpus-forge/trash").expanduser()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self, source_root: Path, poll_interval_s: float = 5.0) -> None:
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("PullPipeline already started")
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, args=(poll_interval_s,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            return
        self._stop_event.set()
        self._thread.join(timeout=10)
        if self._thread.is_alive():
            logger.warning("PullPipeline thread did not stop within timeout")
        self._thread = None

    def _run_loop(self, poll_interval_s: float) -> None:
        while not self._stop_event.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("Error in pull tick")
            self._stop_event.wait(poll_interval_s)

    def tick(self) -> int:
        pending = self._backend.pending_remote_revisions(
            self._dataset_id,
            last_pulled_revision_id=None,
            self_host=self._host_id,
        )
        count = 0
        for rev in pending:
            path = self._source_root / rev["source_uri"]
            local_hash = file_content_hash(path)
            parent_hash = rev.get("parent_content_hash")

            if rev.get("is_tombstone"):
                self._handle_tombstone(rev, path)
                count += 1
            elif local_hash == rev["content_hash"]:
                self._handle_already_in_sync(rev, path)
                count += 1
            elif local_hash == parent_hash:
                atomic_write_text(path, rev["text"])
                self._echo_suppressor.register(path, rev["content_hash"])
                self._backend.mark_revision_pulled(
                    source_id=rev["source_id"], revision_id=rev["id"]
                )
                count += 1
            else:
                self._handle_conflict(rev, path, local_hash)
                count += 1

        return count

    def _handle_already_in_sync(self, revision: dict, path: Path) -> None:
        self._echo_suppressor.register(path, revision["content_hash"])
        self._backend.mark_revision_pulled(
            source_id=revision["source_id"], revision_id=revision["id"]
        )

    def _handle_conflict(self, revision: dict, path: Path, local_hash: str | None) -> None:
        from datetime import datetime

        if path.exists():
            ts = datetime.now(UTC)
            conflict_path = conflict_filename(path, host=self._host_id, ts=ts)
            path.rename(conflict_path)
        atomic_write_text(path, revision["text"])
        self._echo_suppressor.register(path, revision["content_hash"])

    def _handle_tombstone(self, revision: dict, path: Path) -> None:
        if path.exists():
            rel = (
                path.relative_to(self._source_root)
                if path.is_relative_to(self._source_root)
                else None
            )
            move_to_trash(
                path,
                self._trash_root,
                f"dataset_{self._dataset_id}",
                self._host_id,
                rel,
            )
        self._backend.set_tombstone(revision["document_id"])
