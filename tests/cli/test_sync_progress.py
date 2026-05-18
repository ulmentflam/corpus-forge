"""Phase L Wave 4 — sync pull/push progress + bookend loggers.

Pull is bounded (we know ``len(pending)`` ahead of time) so its loop is
wrapped in the shared progress factory. Push is observer-driven (no
bounded loop), so we ship lifecycle bookends on ``start()`` and
``stop()`` instead of a bar.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _stub_revision(idx: int, *, content_hash: str = "x" * 64, parent: str | None = None):
    """Lightweight ``pending_remote_revisions`` row stand-in."""

    return {
        "id": idx,
        "source_uri": f"r-{idx}.md",
        "content_hash": content_hash,
        "parent_content_hash": parent,
        "text": f"rev-{idx}",
        "is_tombstone": False,
        "author_host": "host-other",
        "document_id": idx,
    }


def test_pull_tick_emits_bookend_when_pending(tmp_path, caplog):
    """``PullPipeline.tick`` wraps the apply loop in the progress factory."""

    from corpus_forge.sync.echo import EchoSuppressor
    from corpus_forge.sync.pull import PullPipeline

    backend = MagicMock()
    backend.resolve_self_source.return_value = 7
    backend.pending_remote_revisions.return_value = [
        _stub_revision(1),
        _stub_revision(2),
        _stub_revision(3),
    ]
    backend.mark_revision_pulled = MagicMock()
    backend.set_tombstone = MagicMock()

    echo = EchoSuppressor()
    pipeline = PullPipeline(
        backend=backend,
        dataset_id=1,
        source_root=tmp_path,
        echo_suppressor=echo,
        host_id="host-self",
    )

    # All three pending revisions land on disk with no existing local copy,
    # so the parent-hash branch fires (``local_hash`` resolves to ``None``).
    # We patch ``file_content_hash`` to return ``None`` so the conflict
    # branch isn't accidentally exercised either.
    with (
        patch("corpus_forge.sync.pull.file_content_hash", return_value=None),
        patch("corpus_forge.sync.pull.atomic_write_text"),
        caplog.at_level(logging.INFO, logger="corpus_forge.sync.pull"),
    ):
        count = pipeline.tick()

    assert count == 3
    messages = [r.message for r in caplog.records]
    assert any("Pulling revisions started: 3 items" in m for m in messages), messages
    assert any("Pulling revisions complete" in m for m in messages), messages


def test_pull_tick_no_pending_emits_no_bookend(tmp_path, caplog):
    """No pending revisions → no bar, no bookend records (cheap path)."""

    from corpus_forge.sync.echo import EchoSuppressor
    from corpus_forge.sync.pull import PullPipeline

    backend = MagicMock()
    backend.resolve_self_source.return_value = 7
    backend.pending_remote_revisions.return_value = []

    pipeline = PullPipeline(
        backend=backend,
        dataset_id=1,
        source_root=tmp_path,
        echo_suppressor=EchoSuppressor(),
        host_id="host-self",
    )

    with caplog.at_level(logging.INFO, logger="corpus_forge.sync.pull"):
        count = pipeline.tick()

    assert count == 0
    bookends = [r.message for r in caplog.records if "Pulling revisions" in r.message]
    assert bookends == []


def test_push_start_logs_bookend(tmp_path, caplog):
    """``PushPipeline.start`` writes the "Push start" bookend."""

    from corpus_forge.sync.echo import EchoSuppressor
    from corpus_forge.sync.push import PushPipeline

    backend = MagicMock()
    pipeline = PushPipeline(
        backend=backend,
        dataset_id=42,
        echo_suppressor=EchoSuppressor(),
        host_id="host-self",
    )

    # Use a real Path under tmp_path so watchdog accepts the schedule call.
    source_root: Path = tmp_path

    with caplog.at_level(logging.INFO, logger="corpus_forge.sync.push"):
        pipeline.start(source_root, exclude_globs=(), debounce_seconds=0.5)
        try:
            assert any("Push start: dataset_id=42" in r.message for r in caplog.records), [
                r.message for r in caplog.records
            ]
        finally:
            pipeline.stop()


def test_push_stop_logs_bookend(tmp_path, caplog):
    """``PushPipeline.stop`` writes the "Push stop" bookend after the observer joins."""

    from corpus_forge.sync.echo import EchoSuppressor
    from corpus_forge.sync.push import PushPipeline

    backend = MagicMock()
    pipeline = PushPipeline(
        backend=backend,
        dataset_id=42,
        echo_suppressor=EchoSuppressor(),
        host_id="host-self",
    )
    pipeline.start(tmp_path, exclude_globs=(), debounce_seconds=0.5)

    with caplog.at_level(logging.INFO, logger="corpus_forge.sync.push"):
        pipeline.stop()

    messages = [r.message for r in caplog.records]
    assert any("Push stop: dataset_id=42" in m for m in messages), messages


@pytest.fixture(autouse=True)
def _no_op_fixture():
    """Caplog propagation behaves nicely under the conftest's NO_COLOR shim."""

    return
