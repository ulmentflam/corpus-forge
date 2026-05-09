"""Failing tests for PullPipeline.tick (P1-22)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from corpus_forge.sync.pull import PullPipeline

# ── helpers ──────────────────────────────────────────────────────────────


def _make_pipeline(
    backend=None,
    dataset_id: int = 1,
    source_root: Path = Path("/vault"),
    echo_suppressor=None,
    host_id: str = "test-host",
) -> PullPipeline:
    backend = backend or MagicMock()
    echo_suppressor = echo_suppressor or MagicMock()
    return PullPipeline(
        backend=backend,
        dataset_id=dataset_id,
        source_root=source_root,
        echo_suppressor=echo_suppressor,
        host_id=host_id,
    )


def _make_revision(
    *,
    revision_id: int = 101,
    document_id: int = 42,
    source_id: int = 7,
    source_uri: str = "vault://Claude Wiki/foo.md",
    content_hash: str = "abc123",
    text: str = "file content",
    parent_revision_id: int | None = 100,
    parent_content_hash: str | None = "def456",
    revision_number: int = 3,
    author_host: str = "remote-host",
    is_tombstone: bool = False,
) -> dict:
    return {
        "id": revision_id,
        "document_id": document_id,
        "source_id": source_id,
        "source_uri": source_uri,
        "content_hash": content_hash,
        "text": text,
        "parent_revision_id": parent_revision_id,
        "parent_content_hash": parent_content_hash,
        "revision_number": revision_number,
        "author_host": author_host,
        "is_tombstone": is_tombstone,
    }


# ── No pending revisions ────────────────────────────────────────────────


class TestTickNoPending:
    """No pending revisions → returns 0."""

    def test_no_pending_revisions_returns_zero(self):
        """tick returns 0 when there are no pending revisions."""
        pipeline = _make_pipeline()
        pipeline._backend.pending_remote_revisions.return_value = []

        result = pipeline.tick()

        assert result == 0
        pipeline._backend.mark_revision_pulled.assert_not_called()


# ── Fast-forward: local hash matches parent ─────────────────────────────


class TestTickFastForward:
    """local hash == parent's content_hash → fast-forward."""

    def test_fast_forwards_when_local_hash_matches_parent(self):
        """Local file hash matches parent content_hash → write + register + mark pulled."""
        pipeline = _make_pipeline()
        rev = _make_revision(parent_content_hash="parent_hash_123")
        pipeline._backend.pending_remote_revisions.return_value = [rev]

        with (
            _patch_file_content_hash(return_value="parent_hash_123"),
            _patch_atomic_write_text() as mock_write,
        ):
            result = pipeline.tick()

        assert result == 1
        mock_write.assert_called_once()
        pipeline._echo_suppressor.register.assert_called_once()
        pipeline._backend.mark_revision_pulled.assert_called_once()

    def test_creates_file_when_local_missing_and_parent_null(self):
        """Local file missing and parent_revision_id is None → creates file."""
        pipeline = _make_pipeline()
        rev = _make_revision(
            parent_revision_id=None,
            parent_content_hash=None,
            content_hash="first_hash",
        )
        pipeline._backend.pending_remote_revisions.return_value = [rev]

        with (
            _patch_file_content_hash(return_value=None),
            _patch_atomic_write_text() as mock_write,
        ):
            result = pipeline.tick()

        assert result == 1
        mock_write.assert_called_once()
        pipeline._echo_suppressor.register.assert_called_once()
        pipeline._backend.mark_revision_pulled.assert_called_once()


# ── Multiple pending revisions ──────────────────────────────────────────


class TestTickMultiple:
    """Multiple pending revisions → tick returns applied count."""

    def test_multiple_pending_returns_count(self):
        """Tick returns 3 when 3 fast-forward revisions are applied."""
        pipeline = _make_pipeline()
        revs = [
            _make_revision(
                revision_id=1,
                document_id=42,
                source_id=7,
                parent_revision_id=None,
                parent_content_hash=None,
                content_hash="h1",
            ),
            _make_revision(
                revision_id=2,
                document_id=42,
                source_id=7,
                parent_revision_id=1,
                parent_content_hash="h1",
                content_hash="h2",
            ),
            _make_revision(
                revision_id=3,
                document_id=43,
                source_id=8,
                parent_revision_id=2,
                parent_content_hash="h2",
                content_hash="h3",
            ),
        ]
        pipeline._backend.pending_remote_revisions.return_value = revs

        with (
            _patch_file_content_hash(side_effect=[None, "h1", "h2"]),
            _patch_atomic_write_text() as mock_write,
        ):
            result = pipeline.tick()

        assert result == 3
        assert mock_write.call_count == 3
        assert pipeline._echo_suppressor.register.call_count == 3
        assert pipeline._backend.mark_revision_pulled.call_count == 3


# ── P1-23 — Already in sync ─────────────────────────────────────────────


class TestTickAlreadyInSync:
    """local hash == revision content_hash → skip write, register echo, mark pulled."""

    def test_already_in_sync_skips_write_and_registers_echo(self):
        """Local hash matches content_hash → _handle_already_in_sync (NotImplementedError)."""
        pipeline = _make_pipeline()
        rev = _make_revision(content_hash="same_hash")
        pipeline._backend.pending_remote_revisions.return_value = [rev]

        with _patch_file_content_hash(return_value="same_hash"), _patch_atomic_write_text():
            pipeline.tick()  # raises NotImplementedError until implemented

        # Expected when implemented:
        # assert result == 1
        # mock_write.assert_not_called()
        # pipeline._echo_suppressor.register.assert_called_once()
        # pipeline._backend.mark_revision_pulled.assert_called_once()


# ── P1-24 — Conflict ────────────────────────────────────────────────────


class TestTickConflict:
    """local matches neither parent nor incoming → conflict branch."""

    def test_conflict_renames_local_and_writes_incoming(self):
        """Local hash != parent and != incoming → _handle_conflict (NotImplementedError)."""
        pipeline = _make_pipeline()
        rev = _make_revision(
            content_hash="remote_hash",
            parent_content_hash="parent_hash",
        )
        pipeline._backend.pending_remote_revisions.return_value = [rev]

        with (
            _patch_file_content_hash(return_value="local_only_hash"),
            _patch_atomic_write_text(),
        ):
            pipeline.tick()  # raises NotImplementedError until implemented

        # Expected when implemented:
        # assert result == 1
        # atomic_write_text called with incoming text
        # echo_suppressor.register called for canonical path
        # mark_revision_pulled called


# ── P1-25 — Tombstone ───────────────────────────────────────────────────


class TestTickTombstone:
    """tombstone revision → move to trash, set tombstone."""

    def test_tombstone_moves_to_trash_and_sets_tombstone(self):
        """is_tombstone=True → _handle_tombstone (NotImplementedError before assertions)."""
        pipeline = _make_pipeline()
        rev = _make_revision(
            is_tombstone=True,
            document_id=42,
            source_uri="vault://Claude Wiki/foo.md",
            author_host="remote-host",
        )
        pipeline._backend.pending_remote_revisions.return_value = [rev]

        with _patch_file_content_hash(return_value="whatever"):
            pipeline.tick()  # raises NotImplementedError until implemented

        # Expected when implemented:
        # assert result == 1
        # move_to_trash called with path, trash_root, dataset_name, author_host, rel_path
        # backend.set_tombstone(42) called


# ── P1-26 — Poll loop / lifecycle ───────────────────────────────────────


class TestPullLifecycle:
    """start/stop lifecycle for PullPipeline."""

    def test_start_creates_thread_and_calls_tick(self, tmp_path):
        pipeline = _make_pipeline(source_root=tmp_path)
        pipeline.tick = MagicMock(return_value=0)
        pipeline.start(tmp_path, poll_interval_s=0.01)
        import time

        time.sleep(0.05)
        pipeline.stop()
        pipeline.tick.assert_called()

    def test_stop_signals_exit_and_joins(self, tmp_path):
        pipeline = _make_pipeline(source_root=tmp_path)
        pipeline.tick = MagicMock(return_value=0)
        pipeline.start(tmp_path, poll_interval_s=0.5)
        thread = pipeline._thread
        assert thread is not None and thread.is_alive()
        pipeline.stop()
        assert not thread.is_alive()

    def test_double_start_raises_runtime_error(self, tmp_path):
        pipeline = _make_pipeline(source_root=tmp_path)
        pipeline.tick = MagicMock(return_value=0)
        pipeline.start(tmp_path)

        with pytest.raises(RuntimeError):
            pipeline.start(tmp_path)
        pipeline.stop()

    def test_stop_without_start_is_noop(self, tmp_path):
        pipeline = _make_pipeline(source_root=tmp_path)
        pipeline.stop()

    def test_thread_is_daemon(self, tmp_path):
        pipeline = _make_pipeline(source_root=tmp_path)
        pipeline.tick = MagicMock(return_value=0)
        pipeline.start(tmp_path)
        assert pipeline._thread is not None
        assert pipeline._thread.daemon is True
        pipeline.stop()


# ── helpers (private) ───────────────────────────────────────────────────


def _patch_file_content_hash(*, return_value=None, side_effect=None):
    import corpus_forge.sync.pull as pull_mod

    return patch.object(
        pull_mod,
        "file_content_hash",
        return_value=return_value,
        side_effect=side_effect,
        create=True,
    )


def _patch_atomic_write_text():
    import corpus_forge.sync.pull as pull_mod

    return patch.object(pull_mod, "atomic_write_text", create=True)
