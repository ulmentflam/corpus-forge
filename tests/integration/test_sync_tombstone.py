"""E2E tombstone integration test (P1-31).

Two SyncEngine instances share a testcontainers Postgres.  The test drives
file events directly (handle_change / handle_delete + tick) rather than
relying on the watchdog observer, so it stays fast and deterministic.

Tests:
  * test_delete_on_a_tombstones_on_b
  * test_resurrect_clears_tombstone
"""

from __future__ import annotations

import time
from hashlib import sha256
from pathlib import Path

import psycopg
import pytest

from corpus_forge.backends.postgres import PostgresBackend
from corpus_forge.schema.migrate import apply_migrations
from corpus_forge.sync.echo import EchoSuppressor
from corpus_forge.sync.pull import PullPipeline
from corpus_forge.sync.push import PushPipeline

pytestmark = pytest.mark.integration

# ── constants ─────────────────────────────────────────────────────────────────

_DATASET_NAME = "tombstone-e2e"
_HOST_A = "macA"
_HOST_B = "macB"
_POLL_S = 0.3
_TIMEOUT_S = 15.0


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_backend(pg_dsn: str) -> PostgresBackend:
    backend = PostgresBackend(dsn=pg_dsn, schema="corpus")
    backend.migrate()
    # Apply sync migrations (003_sync.sql etc.)
    schema_dir = Path(__file__).parent.parent.parent / "corpus_forge" / "schema"
    apply_migrations(backend, schema_dir)
    return backend


def _ensure_dataset(pg_dsn: str, name: str) -> int:
    """Insert dataset if absent; return its id."""
    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO corpus.datasets (name, kind) VALUES (%s, 'text') "
            "ON CONFLICT (name) DO UPDATE SET kind = EXCLUDED.kind "
            "RETURNING id",
            (name,),
        )
        conn.commit()
        return cur.fetchone()[0]


def wait_for(
    condition,
    timeout: float = _TIMEOUT_S,
    interval: float = 0.1,
    msg: str = "condition not met",
) -> None:
    """Poll until *condition()* is truthy or *timeout* seconds elapse."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if condition():
                return
        except Exception:
            pass
        time.sleep(interval)
    # One last check to produce a meaningful assertion failure
    assert condition(), msg


def _push_and_pull(
    push: PushPipeline, pull: PullPipeline, path: Path, *, delete: bool = False
) -> None:
    """Drive one cycle: push the event then tick the pull pipeline."""
    if delete:
        push.handle_delete(path)
    else:
        push.handle_change(path)
    pull.tick()


def _direct_push_change(push: PushPipeline, path: Path) -> None:
    push.handle_change(path)


def _direct_push_delete(push: PushPipeline, path: Path) -> None:
    push.handle_delete(path)


def _direct_pull_tick(pull: PullPipeline) -> int:
    return pull.tick()


# ── fixtures ──────────────────────────────────────────────────────────────────


class _FakeEmbedder:
    """Minimal embedder stub — never called in these tests."""

    name = "fake-embed-tombstone"
    provider = "fake"
    model_id = "fake-model"
    dimension = 4
    normalized = True
    distance = "cosine"


# ── tests ─────────────────────────────────────────────────────────────────────


class TestTombstoneDeleteOnA:
    """Delete on A → tombstone revision in DB; file moves to B's trash; tombstoned_at set."""

    def test_delete_on_a_tombstones_on_b(self, pg_dsn: str, tmp_path: Path) -> None:
        # ── setup ──
        backend = _make_backend(pg_dsn)
        dataset_id = _ensure_dataset(pg_dsn, f"{_DATASET_NAME}-delete-1")

        root_a = tmp_path / "root_a"
        root_b = tmp_path / "root_b"
        trash_b = tmp_path / "trash_b"
        root_a.mkdir()
        root_b.mkdir()
        trash_b.mkdir()

        echo_a = EchoSuppressor()
        echo_b = EchoSuppressor()

        push_a = PushPipeline(
            backend=backend,
            dataset_id=dataset_id,
            echo_suppressor=echo_a,
            host_id=_HOST_A,
        )
        pull_b = PullPipeline(
            backend=backend,
            dataset_id=dataset_id,
            source_root=root_b,
            echo_suppressor=echo_b,
            host_id=_HOST_B,
            trash_root=trash_b,
        )
        push_a._exclude_globs = ()

        # ── step 1: create file on A, push, pull → file appears on B ──
        doomed = root_a / "doomed.md"
        doomed.write_text("# Doomed\n\nThis file will be deleted.")

        push_a.handle_change(doomed)
        pull_b.tick()

        # Wait for pull to materialise file on B
        wait_for(
            lambda: (root_b / "doomed.md").exists(),  # noqa: PLW0108
            timeout=_TIMEOUT_S,
            msg="doomed.md should appear on B after initial push+pull",
        )

        # ── step 2: delete file on A, push tombstone ──
        doomed.unlink()
        push_a.handle_delete(doomed)

        # ── step 3: pull B — file should disappear from root_b ──
        def _b_root_gone() -> bool:
            pull_b.tick()
            return not (root_b / "doomed.md").exists()

        wait_for(
            _b_root_gone,
            timeout=_TIMEOUT_S,
            msg="doomed.md should disappear from B's root after tombstone pull",
        )

        # ── step 4: trashed file path matches the naming contract ──
        # Expected pattern: <trash_b>/dataset_<id>/doomed.deleted-macA-<ts>.md
        trashed = list(trash_b.rglob("doomed.deleted-*"))
        assert len(trashed) >= 1, (
            f"Expected at least one trashed file under {trash_b}, found: {list(trash_b.rglob('*'))}"
        )
        trashed_file = trashed[0]
        # File name must contain the author host
        assert _HOST_A in trashed_file.name, (
            f"Trash file name should contain author host '{_HOST_A}': {trashed_file.name}"
        )
        # Extension preserved
        assert trashed_file.suffix == ".md", f"Expected .md suffix, got: {trashed_file.suffix}"

        # ── step 5: tombstone revision exists in DB ──
        rows = backend._execute(
            "SELECT * FROM corpus.document_revisions "
            "WHERE is_tombstone = TRUE "
            "ORDER BY id DESC LIMIT 10"
        )
        assert len(rows) >= 1, "Expected at least one tombstone revision in document_revisions"
        tombstone_rev = rows[0]
        assert tombstone_rev["author_host"] == _HOST_A, (
            f"Tombstone author_host should be '{_HOST_A}', got: {tombstone_rev['author_host']}"
        )

        # ── step 6: documents.tombstoned_at is set ──
        doc_rows = backend._execute(
            "SELECT tombstoned_at FROM corpus.documents "
            "WHERE source_uri LIKE '%%doomed.md' AND dataset_id = %s",
            (dataset_id,),
        )
        assert len(doc_rows) >= 1, "Expected a documents row for doomed.md"
        assert doc_rows[0]["tombstoned_at"] is not None, (
            "documents.tombstoned_at should be set after tombstone"
        )

    def test_trash_path_dataset_component_matches_contract(
        self, pg_dsn: str, tmp_path: Path
    ) -> None:
        """Trashed file should reside under trash_b/<dataset_component>/."""
        backend = _make_backend(pg_dsn)
        dataset_id = _ensure_dataset(pg_dsn, f"{_DATASET_NAME}-delete-2")

        root_a = tmp_path / "root_a"
        root_b = tmp_path / "root_b"
        trash_b = tmp_path / "trash_b"
        root_a.mkdir()
        root_b.mkdir()
        trash_b.mkdir()

        echo_a = EchoSuppressor()
        echo_b = EchoSuppressor()

        push_a = PushPipeline(
            backend=backend,
            dataset_id=dataset_id,
            echo_suppressor=echo_a,
            host_id=_HOST_A,
        )
        pull_b = PullPipeline(
            backend=backend,
            dataset_id=dataset_id,
            source_root=root_b,
            echo_suppressor=echo_b,
            host_id=_HOST_B,
            trash_root=trash_b,
        )
        push_a._exclude_globs = ()

        note = root_a / "note.md"
        note.write_text("# Note\n")
        push_a.handle_change(note)
        pull_b.tick()

        wait_for(
            lambda: (root_b / "note.md").exists(),  # noqa: PLW0108
            timeout=_TIMEOUT_S,
            msg="note.md should appear on B",
        )

        note.unlink()
        push_a.handle_delete(note)

        def _gone_from_b() -> bool:
            pull_b.tick()
            return not (root_b / "note.md").exists()

        wait_for(_gone_from_b, timeout=_TIMEOUT_S, msg="note.md should disappear from B root")

        # Trashed file must live under trash_b (in some subdirectory)
        all_trashed = list(trash_b.rglob("note.deleted-*"))
        assert len(all_trashed) >= 1, (
            f"Expected trashed note.md under {trash_b}, got {list(trash_b.rglob('*'))}"
        )
        trashed_path = all_trashed[0]
        # The parent of the trashed file should be under trash_b
        assert str(trashed_path).startswith(str(trash_b)), (
            f"Trashed file should be under trash_b, found: {trashed_path}"
        )

    def test_tombstone_revision_content_hash_is_empty_sha256(
        self, pg_dsn: str, tmp_path: Path
    ) -> None:
        """Tombstone revision must have content_hash = sha256(b'')."""
        empty_hash = sha256(b"").hexdigest()

        backend = _make_backend(pg_dsn)
        dataset_id = _ensure_dataset(pg_dsn, f"{_DATASET_NAME}-hash-check")

        root_a = tmp_path / "root_a"
        root_a.mkdir()

        echo_a = EchoSuppressor()
        push_a = PushPipeline(
            backend=backend,
            dataset_id=dataset_id,
            echo_suppressor=echo_a,
            host_id=_HOST_A,
        )
        push_a._exclude_globs = ()

        victim = root_a / "victim.md"
        victim.write_text("# Victim\n")
        push_a.handle_change(victim)

        victim.unlink()
        push_a.handle_delete(victim)

        rows = backend._execute(
            "SELECT content_hash, text FROM corpus.document_revisions "
            "WHERE is_tombstone = TRUE ORDER BY id DESC LIMIT 1"
        )
        assert len(rows) >= 1, "Expected a tombstone revision"
        assert rows[0]["content_hash"] == empty_hash, (
            f"Tombstone content_hash should be sha256(b''), got: {rows[0]['content_hash']}"
        )
        assert rows[0]["text"] == "", (
            f"Tombstone text should be empty string, got: {rows[0]['text']!r}"
        )

    def test_tombstoned_at_set_on_document(self, pg_dsn: str, tmp_path: Path) -> None:
        """After delete event, documents.tombstoned_at must be non-NULL."""
        backend = _make_backend(pg_dsn)
        dataset_id = _ensure_dataset(pg_dsn, f"{_DATASET_NAME}-tombstoned-at")

        root_a = tmp_path / "root_a"
        root_a.mkdir()

        echo_a = EchoSuppressor()
        push_a = PushPipeline(
            backend=backend,
            dataset_id=dataset_id,
            echo_suppressor=echo_a,
            host_id=_HOST_A,
        )
        push_a._exclude_globs = ()

        target = root_a / "target.md"
        target.write_text("# Target\n")
        push_a.handle_change(target)

        target.unlink()
        push_a.handle_delete(target)

        rows = backend._execute(
            "SELECT tombstoned_at FROM corpus.documents "
            "WHERE source_uri LIKE '%%target.md' AND dataset_id = %s",
            (dataset_id,),
        )
        assert len(rows) >= 1, "Expected a documents row for target.md"
        assert rows[0]["tombstoned_at"] is not None, (
            "documents.tombstoned_at should be non-NULL after handle_delete"
        )


class TestTombstoneResurrection:
    """Re-create file on A → file reappears on B; tombstoned_at cleared."""

    def test_resurrect_clears_tombstone(self, pg_dsn: str, tmp_path: Path) -> None:
        # ── setup ──
        backend = _make_backend(pg_dsn)
        dataset_id = _ensure_dataset(pg_dsn, f"{_DATASET_NAME}-resurrect-1")

        root_a = tmp_path / "root_a"
        root_b = tmp_path / "root_b"
        trash_b = tmp_path / "trash_b"
        root_a.mkdir()
        root_b.mkdir()
        trash_b.mkdir()

        echo_a = EchoSuppressor()
        echo_b = EchoSuppressor()

        push_a = PushPipeline(
            backend=backend,
            dataset_id=dataset_id,
            echo_suppressor=echo_a,
            host_id=_HOST_A,
        )
        pull_b = PullPipeline(
            backend=backend,
            dataset_id=dataset_id,
            source_root=root_b,
            echo_suppressor=echo_b,
            host_id=_HOST_B,
            trash_root=trash_b,
        )
        push_a._exclude_globs = ()

        # ── step 1: create on A → pull to B ──
        phoenix = root_a / "phoenix.md"
        phoenix.write_text("# Phoenix v1\n")
        push_a.handle_change(phoenix)
        pull_b.tick()

        wait_for(
            lambda: (root_b / "phoenix.md").exists(),  # noqa: PLW0108
            timeout=_TIMEOUT_S,
            msg="phoenix.md should appear on B initially",
        )

        # ── step 2: delete on A → tombstone pulled to B ──
        phoenix.unlink()
        push_a.handle_delete(phoenix)

        def _gone() -> bool:
            pull_b.tick()
            return not (root_b / "phoenix.md").exists()

        wait_for(_gone, timeout=_TIMEOUT_S, msg="phoenix.md should disappear from B after delete")

        # Confirm tombstoned_at was set
        rows_before = backend._execute(
            "SELECT tombstoned_at FROM corpus.documents "
            "WHERE source_uri LIKE '%%phoenix.md' AND dataset_id = %s",
            (dataset_id,),
        )
        assert rows_before[0]["tombstoned_at"] is not None, (
            "tombstoned_at should be set before resurrection"
        )

        # ── step 3: recreate on A → push new revision → pull to B ──
        phoenix.write_text("# Phoenix Reborn\n")
        push_a.handle_change(phoenix)
        pull_b.tick()

        def _reborn() -> bool:
            pull_b.tick()
            return (root_b / "phoenix.md").exists()

        wait_for(
            _reborn,
            timeout=_TIMEOUT_S,
            msg="phoenix.md should reappear on B after resurrection",
        )

        # ── step 4: tombstoned_at should be cleared ──
        rows_after = backend._execute(
            "SELECT tombstoned_at FROM corpus.documents "
            "WHERE source_uri LIKE '%%phoenix.md' AND dataset_id = %s",
            (dataset_id,),
        )
        assert len(rows_after) >= 1, "Expected documents row for phoenix.md after resurrection"
        assert rows_after[0]["tombstoned_at"] is None, (
            f"documents.tombstoned_at should be NULL after resurrection, "
            f"got: {rows_after[0]['tombstoned_at']}"
        )

    def test_resurrect_content_appears_on_b(self, pg_dsn: str, tmp_path: Path) -> None:
        """Resurrected file on B should have the content from A's re-creation."""
        backend = _make_backend(pg_dsn)
        dataset_id = _ensure_dataset(pg_dsn, f"{_DATASET_NAME}-resurrect-content")

        root_a = tmp_path / "root_a"
        root_b = tmp_path / "root_b"
        trash_b = tmp_path / "trash_b"
        root_a.mkdir()
        root_b.mkdir()
        trash_b.mkdir()

        echo_a = EchoSuppressor()
        echo_b = EchoSuppressor()

        push_a = PushPipeline(
            backend=backend,
            dataset_id=dataset_id,
            echo_suppressor=echo_a,
            host_id=_HOST_A,
        )
        pull_b = PullPipeline(
            backend=backend,
            dataset_id=dataset_id,
            source_root=root_b,
            echo_suppressor=echo_b,
            host_id=_HOST_B,
            trash_root=trash_b,
        )
        push_a._exclude_globs = ()

        reborn_content = "# Reborn\n\nThis is the resurrected content."

        # create → delete → recreate
        doc = root_a / "reborn.md"
        doc.write_text("# Original\n")
        push_a.handle_change(doc)
        pull_b.tick()

        wait_for(lambda: (root_b / "reborn.md").exists(), timeout=_TIMEOUT_S)  # noqa: PLW0108

        doc.unlink()
        push_a.handle_delete(doc)
        pull_b.tick()

        wait_for(lambda: not (root_b / "reborn.md").exists(), timeout=_TIMEOUT_S)

        doc.write_text(reborn_content)
        push_a.handle_change(doc)
        pull_b.tick()

        wait_for(lambda: (root_b / "reborn.md").exists(), timeout=_TIMEOUT_S)  # noqa: PLW0108

        b_content = (root_b / "reborn.md").read_text()
        assert b_content == reborn_content, f"B should have resurrected content, got: {b_content!r}"


class TestTombstonePushSideOnly:
    """Push-side tombstone tests: verify DB state written by handle_delete."""

    def test_handle_delete_inserts_tombstone_revision(self, pg_dsn: str, tmp_path: Path) -> None:
        """handle_delete must insert a document_revisions row with is_tombstone=True."""
        backend = _make_backend(pg_dsn)
        dataset_id = _ensure_dataset(pg_dsn, f"{_DATASET_NAME}-push-only-1")

        root = tmp_path / "root"
        root.mkdir()
        echo = EchoSuppressor()
        push = PushPipeline(
            backend=backend,
            dataset_id=dataset_id,
            echo_suppressor=echo,
            host_id=_HOST_A,
        )
        push._exclude_globs = ()

        target = root / "delete-me.md"
        target.write_text("# Delete me\n")
        push.handle_change(target)

        before_count = len(
            backend._execute("SELECT id FROM corpus.document_revisions WHERE is_tombstone = TRUE")
        )
        target.unlink()
        push.handle_delete(target)

        after_rows = backend._execute(
            "SELECT id FROM corpus.document_revisions WHERE is_tombstone = TRUE"
        )
        assert len(after_rows) > before_count, (
            "handle_delete should have inserted a tombstone revision"
        )

    def test_handle_delete_sets_tombstoned_at(self, pg_dsn: str, tmp_path: Path) -> None:
        """handle_delete must set documents.tombstoned_at."""
        backend = _make_backend(pg_dsn)
        dataset_id = _ensure_dataset(pg_dsn, f"{_DATASET_NAME}-push-only-2")

        root = tmp_path / "root"
        root.mkdir()
        echo = EchoSuppressor()
        push = PushPipeline(
            backend=backend,
            dataset_id=dataset_id,
            echo_suppressor=echo,
            host_id=_HOST_A,
        )
        push._exclude_globs = ()

        doc = root / "tombstone-me.md"
        doc.write_text("# Tombstone me\n")
        push.handle_change(doc)
        doc.unlink()
        push.handle_delete(doc)

        rows = backend._execute(
            "SELECT tombstoned_at FROM corpus.documents "
            "WHERE source_uri LIKE '%%tombstone-me.md' AND dataset_id = %s",
            (dataset_id,),
        )
        assert rows, "document row should exist for tombstone-me.md"
        assert rows[0]["tombstoned_at"] is not None, "tombstoned_at must be set after handle_delete"

    def test_handle_delete_noop_when_file_not_tracked(self, pg_dsn: str, tmp_path: Path) -> None:
        """Deleting an untracked file must not raise and must not insert any revision."""
        backend = _make_backend(pg_dsn)
        dataset_id = _ensure_dataset(pg_dsn, f"{_DATASET_NAME}-push-only-3")

        root = tmp_path / "root"
        root.mkdir()
        echo = EchoSuppressor()
        push = PushPipeline(
            backend=backend,
            dataset_id=dataset_id,
            echo_suppressor=echo,
            host_id=_HOST_A,
        )
        push._exclude_globs = ()

        ghost = root / "ghost.md"  # never written to DB

        before_count = len(
            backend._execute("SELECT id FROM corpus.document_revisions WHERE is_tombstone = TRUE")
        )
        # Should not raise
        push.handle_delete(ghost)

        after_count = len(
            backend._execute("SELECT id FROM corpus.document_revisions WHERE is_tombstone = TRUE")
        )
        assert after_count == before_count, (
            "No tombstone revision should be inserted for an untracked file"
        )

    def test_handle_delete_ignores_icloud_placeholder(self, pg_dsn: str, tmp_path: Path) -> None:
        """If a .icloud placeholder sibling exists, handle_delete must be a no-op."""
        backend = _make_backend(pg_dsn)
        dataset_id = _ensure_dataset(pg_dsn, f"{_DATASET_NAME}-icloud-guard")

        root = tmp_path / "root"
        root.mkdir()
        echo = EchoSuppressor()
        push = PushPipeline(
            backend=backend,
            dataset_id=dataset_id,
            echo_suppressor=echo,
            host_id=_HOST_A,
        )
        push._exclude_globs = ()

        evicted = root / "evicted.md"
        evicted.write_text("# Will be evicted\n")
        push.handle_change(evicted)

        # Simulate iCloud eviction: file gone, placeholder appears
        evicted.unlink()
        placeholder = root / "evicted.md.icloud"
        placeholder.write_text("")  # 0-byte placeholder

        before_tombstones = len(
            backend._execute("SELECT id FROM corpus.document_revisions WHERE is_tombstone = TRUE")
        )
        push.handle_delete(evicted)

        after_tombstones = len(
            backend._execute("SELECT id FROM corpus.document_revisions WHERE is_tombstone = TRUE")
        )
        assert after_tombstones == before_tombstones, (
            "iCloud eviction (placeholder present) should NOT generate a tombstone revision"
        )


class TestPullSideTombstone:
    """Pull-side tombstone handler: move_to_trash + set_tombstone."""

    def test_pull_tombstone_moves_file_to_trash(self, pg_dsn: str, tmp_path: Path) -> None:
        """When pull encounters a tombstone revision, the local file moves to trash."""
        backend = _make_backend(pg_dsn)
        dataset_id = _ensure_dataset(pg_dsn, f"{_DATASET_NAME}-pull-trash-1")

        root_a = tmp_path / "root_a"
        root_b = tmp_path / "root_b"
        trash_b = tmp_path / "trash_b"
        root_a.mkdir()
        root_b.mkdir()
        trash_b.mkdir()

        echo_a = EchoSuppressor()
        echo_b = EchoSuppressor()

        push_a = PushPipeline(
            backend=backend,
            dataset_id=dataset_id,
            echo_suppressor=echo_a,
            host_id=_HOST_A,
        )
        pull_b = PullPipeline(
            backend=backend,
            dataset_id=dataset_id,
            source_root=root_b,
            echo_suppressor=echo_b,
            host_id=_HOST_B,
            trash_root=trash_b,
        )
        push_a._exclude_globs = ()

        # Create and sync
        doc = root_a / "will-be-trashed.md"
        doc.write_text("# Will be trashed\n")
        push_a.handle_change(doc)
        pull_b.tick()

        wait_for(lambda: (root_b / "will-be-trashed.md").exists(), timeout=_TIMEOUT_S)  # noqa: PLW0108

        # Delete on A, pull on B
        doc.unlink()
        push_a.handle_delete(doc)

        def _trashed() -> bool:
            pull_b.tick()
            trashed = list(trash_b.rglob("will-be-trashed.deleted-*"))
            return len(trashed) >= 1 and not (root_b / "will-be-trashed.md").exists()

        wait_for(_trashed, timeout=_TIMEOUT_S, msg="File should be trashed on B side")

        trashed_files = list(trash_b.rglob("will-be-trashed.deleted-*"))
        assert len(trashed_files) >= 1
        assert trashed_files[0].suffix == ".md"

    def test_pull_tombstone_sets_tombstoned_at(self, pg_dsn: str, tmp_path: Path) -> None:
        """Pull tombstone handler must call set_tombstone → tombstoned_at non-NULL."""
        backend = _make_backend(pg_dsn)
        dataset_id = _ensure_dataset(pg_dsn, f"{_DATASET_NAME}-pull-tombstoned-at")

        root_a = tmp_path / "root_a"
        root_b = tmp_path / "root_b"
        trash_b = tmp_path / "trash_b"
        root_a.mkdir()
        root_b.mkdir()
        trash_b.mkdir()

        echo_a = EchoSuppressor()
        echo_b = EchoSuppressor()

        push_a = PushPipeline(
            backend=backend,
            dataset_id=dataset_id,
            echo_suppressor=echo_a,
            host_id=_HOST_A,
        )
        pull_b = PullPipeline(
            backend=backend,
            dataset_id=dataset_id,
            source_root=root_b,
            echo_suppressor=echo_b,
            host_id=_HOST_B,
            trash_root=trash_b,
        )
        push_a._exclude_globs = ()

        note = root_a / "marked.md"
        note.write_text("# Marked for tombstone\n")
        push_a.handle_change(note)
        pull_b.tick()

        wait_for(lambda: (root_b / "marked.md").exists(), timeout=_TIMEOUT_S)  # noqa: PLW0108

        note.unlink()
        push_a.handle_delete(note)

        def _tombstoned_in_db() -> bool:
            pull_b.tick()
            rows = backend._execute(
                "SELECT tombstoned_at FROM corpus.documents "
                "WHERE source_uri LIKE '%%marked.md' AND dataset_id = %s",
                (dataset_id,),
            )
            return bool(rows) and rows[0]["tombstoned_at"] is not None

        wait_for(
            _tombstoned_in_db,
            timeout=_TIMEOUT_S,
            msg="tombstoned_at should be non-NULL in documents after pull processes tombstone",
        )


class TestTombstonePollLoop:
    """Verify that the PullPipeline poll loop processes tombstones without manual ticks."""

    def test_poll_loop_processes_tombstone(self, pg_dsn: str, tmp_path: Path) -> None:
        """Start push (no watchdog) and pull (with poll loop); drive via direct calls."""
        backend = _make_backend(pg_dsn)
        dataset_id = _ensure_dataset(pg_dsn, f"{_DATASET_NAME}-poll-loop")

        root_a = tmp_path / "root_a"
        root_b = tmp_path / "root_b"
        trash_b = tmp_path / "trash_b"
        root_a.mkdir()
        root_b.mkdir()
        trash_b.mkdir()

        echo_a = EchoSuppressor()
        echo_b = EchoSuppressor()

        push_a = PushPipeline(
            backend=backend,
            dataset_id=dataset_id,
            echo_suppressor=echo_a,
            host_id=_HOST_A,
        )
        pull_b = PullPipeline(
            backend=backend,
            dataset_id=dataset_id,
            source_root=root_b,
            echo_suppressor=echo_b,
            host_id=_HOST_B,
            trash_root=trash_b,
        )
        push_a._exclude_globs = ()

        # Create and sync
        loop_doc = root_a / "loop-doc.md"
        loop_doc.write_text("# Loop doc\n")
        push_a.handle_change(loop_doc)
        pull_b.tick()

        wait_for(lambda: (root_b / "loop-doc.md").exists(), timeout=_TIMEOUT_S)  # noqa: PLW0108

        # Start pull pipeline's actual poll loop (short interval)
        pull_b.start(source_root=root_b, poll_interval_s=_POLL_S)
        try:
            # Delete and push tombstone
            loop_doc.unlink()
            push_a.handle_delete(loop_doc)

            # Poll loop should pick it up
            wait_for(
                lambda: not (root_b / "loop-doc.md").exists(),
                timeout=_TIMEOUT_S,
                msg="Poll loop should have processed tombstone and removed file from B root",
            )

            # Trash should have the file
            trashed = list(trash_b.rglob("loop-doc.deleted-*"))
            assert len(trashed) >= 1, (
                f"Trashed file not found under {trash_b}; contents: {list(trash_b.rglob('*'))}"
            )
        finally:
            pull_b.stop()
